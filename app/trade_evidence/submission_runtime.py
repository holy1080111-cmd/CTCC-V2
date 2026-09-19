"""Independent token-free DB→file reporting worker, default off.

The dedicated thread owns its database pool and native file operations. It has
no order client, Arm operation, HTTP transport or trading callback.
"""

import asyncio
import logging
import threading
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.repositories.submission_reporting import SubmissionReportingRepository
from app.trade_evidence import outbox, storage
from app.trade_evidence.submission_reporting import queue

logger = logging.getLogger(__name__)


class SubmissionOutboxRuntime:
    def __init__(self, settings, *, clock=outbox.actual_utc, session_factory=None):
        self.settings, self.clock = settings, clock
        self._sessions = session_factory
        self._thread = self._loop = self._task = None
        self._stop = threading.Event()
        self._after = ""
        self.status, self.last_code = "disabled", "submission_outbox_disabled"

    async def start(self):
        if not self.settings.submission_outbox_enabled or (
            self._thread and self._thread.is_alive()
        ):
            return
        self._stop.clear()
        self.status, self.last_code = "starting", "submission_outbox_starting"
        self._thread = threading.Thread(
            target=self._thread_main, name="ctcc-submission-outbox", daemon=True
        )
        try:
            self._thread.start()
        except Exception:  # noqa: BLE001 -- Reporting cannot abort API startup.
            self._thread = None
            self._pending("submission_outbox_start_failed")

    def _pending(self, code):
        self.status, self.last_code = "pending", code
        logger.warning("submission_outbox_pending code=%s", code)

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 -- No database URL/exception text in logs.
            self._pending("submission_outbox_worker_failed")
        finally:
            self._loop = self._task = None
            if self._stop.is_set():
                self.status, self.last_code = "stopped", "submission_outbox_stopped"

    async def stop(self):
        self._stop.set()
        loop, task, thread = self._loop, self._task, self._thread
        if loop and task:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        if thread:
            await asyncio.to_thread(thread.join, 10)
            if thread.is_alive():
                self._pending("submission_outbox_shutdown_pending")
                return
            self._thread = None
        self.status, self.last_code = "stopped", "submission_outbox_stopped"

    async def tick(self):
        if not self.settings.submission_outbox_enabled:
            return
        if not self.settings.submission_outbox_root or self._sessions is None:
            self._pending("submission_outbox_configuration_missing")
            return
        try:
            root = storage._root_path(Path(self.settings.submission_outbox_root))
            namespace = queue(self.settings.submission_outbox_queue_namespace)
            repository = SubmissionReportingRepository(self._sessions, clock=self.clock)
            pending = await repository.pending_ids(
                namespace,
                limit=self.settings.submission_outbox_batch_size,
                after=self._after,
            )
            failed = False
            for spool_id in pending:
                self._after = spool_id
                try:
                    await repository.project_one(
                        spool_id, root=root, queue_namespace=namespace
                    )
                except Exception:  # noqa: BLE001 -- Retain pending rows; continue fair batch.
                    failed = True
            if failed:
                self._pending("submission_outbox_projection_pending")
            else:
                self.status, self.last_code = "idle", "submission_outbox_pass_complete"
        except Exception:  # noqa: BLE001 -- No private SQL/driver diagnostics in logs.
            self._pending("submission_outbox_pass_pending")

    async def _run(self):
        self._loop, self._task = asyncio.get_running_loop(), asyncio.current_task()
        engine = None
        try:
            if self._sessions is None:
                engine = create_async_engine(
                    self.settings.database_url,
                    pool_size=1,
                    max_overflow=0,
                    pool_timeout=5,
                    pool_pre_ping=True,
                    connect_args={"timeout": 5, "command_timeout": 10},
                )
                self._sessions = async_sessionmaker(
                    engine, expire_on_commit=False, autoflush=False
                )
            while not self._stop.is_set():
                try:
                    async with asyncio.timeout(
                        self.settings.submission_outbox_pass_timeout_seconds
                    ):
                        await self.tick()
                except TimeoutError:
                    self._pending("submission_outbox_pass_timeout")
                await asyncio.sleep(self.settings.submission_outbox_poll_seconds)
        finally:
            if engine is not None:
                self._sessions = None
                await engine.dispose()
