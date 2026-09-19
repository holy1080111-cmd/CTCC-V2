"""Independent, default-off durable reporting worker; no trading imports/callbacks.

The worker owns a thread and event loop, keeping synchronous durable filesystem
work and credential ACL checks off the API/trading event loop. The durable outbox
continues to own leases, retries and uncertainty; restart never resubmits orders.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from pathlib import Path

import httpx

from app.config.settings import Settings
from app.trade_evidence import (
    notion_adapter,
    outbox,
    outbox_worker,
    reporting_control,
    storage,
)
from app.trade_evidence.notion_binding import (
    NotionBindingError,
    load_destination,
    load_private_token,
    verify_binding,
)

logger = logging.getLogger(__name__)
SAFE_CODES = frozenset(
    {
        "notion_outbox_configuration_missing",
        "notion_secret_inside_outbox",
        "notion_outbox_directory_limit",
        "notion_outbox_case_alias",
        "notion_secret_path_invalid",
        "notion_secret_path_reparse",
        "notion_secret_inside_git_source",
        "notion_secret_inside_source_or_workspace",
        "notion_secret_inside_evidence",
        "notion_private_file_permissions_invalid",
        "notion_private_file_identity_invalid",
        "notion_private_file_readback_failed",
        "notion_destination_pin_missing",
        "notion_destination_pin_mismatch",
        "notion_binding_endpoint_invalid",
        "notion_binding_rate_limited",
        "notion_binding_http_rejected",
        "notion_binding_headers_invalid",
        "notion_binding_response_bound",
        "notion_binding_length_mismatch",
        "notion_database_identity_mismatch",
        "notion_database_sources_incomplete",
        "notion_database_sources_invalid",
        "notion_database_source_mismatch",
    }
)


def isolated_client():
    return httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=3)


def discover_jobs(root: Path) -> tuple[str, ...]:
    checked = storage._root_path(root)
    with outbox._root_context(checked) as directory:
        names = directory.names()
        if len(names) > outbox.MAX_ROOT_ENTRIES:
            raise NotionBindingError("notion_outbox_directory_limit")
        if len({name.casefold() for name in names}) != len(names):
            raise NotionBindingError("notion_outbox_case_alias")
        reports = []
        for name in names:
            if name.endswith(".json"):
                report_id = name[:-5]
                outbox._name(report_id)
                reports.append(report_id)
        # Ordering is scheduler fairness only. The existing worker independently
        # verifies every complete journal, lease and envelope before dispatch.
        return tuple(sorted(reports))


class NotionOutboxRuntime:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory=isolated_client,
        clock=outbox.actual_utc,
    ):
        self.settings = settings
        self._client_factory = client_factory
        self._clock = clock
        self._thread = None
        self._loop = None
        self._task = None
        self._stop = threading.Event()
        self._after_id = ""
        self._worker_id = "notion-" + uuid.uuid4().hex
        self.status = "disabled"
        self.last_code = "notion_outbox_disabled"
        self.last_result = None
        self._failures = 0

    async def start(self):
        if not self.settings.notion_outbox_enabled:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.status, self.last_code = "starting", "notion_outbox_starting"
        self._thread = threading.Thread(
            target=self._thread_main, name="ctcc-notion-outbox", daemon=True
        )
        try:
            self._thread.start()
        except Exception:  # noqa: BLE001 -- Reporter startup cannot abort the API.
            self._thread = None
            self.status, self.last_code = "pending", "notion_outbox_worker_start_failed"
            logger.warning(
                "notion_outbox_pending code=notion_outbox_worker_start_failed"
            )

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 -- Never log credential/remote exception text.
            self.status, self.last_code = "pending", "notion_outbox_worker_failed"
            logger.warning("notion_outbox_pending code=notion_outbox_worker_failed")
        finally:
            self._loop = self._task = None
            if self._stop.is_set():
                self.status, self.last_code = "stopped", "notion_outbox_stopped"

    async def stop(self):
        self._stop.set()
        loop, task, thread = self._loop, self._task, self._thread
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # The dedicated loop already finished.
        if thread is not None:
            await asyncio.to_thread(thread.join, 10)
            if thread.is_alive():
                self.status, self.last_code = (
                    "pending",
                    "notion_outbox_shutdown_pending",
                )
                return
            self._thread = None
        self.status, self.last_code = "stopped", "notion_outbox_stopped"

    def _configuration(self):
        values = self.settings
        if not all(
            (
                values.notion_outbox_root,
                values.notion_outbox_destination_file,
                values.notion_outbox_destination_sha256,
                values.notion_outbox_token_file,
            )
        ):
            raise NotionBindingError("notion_outbox_configuration_missing")
        root = storage._root_path(Path(values.notion_outbox_root))
        if Path(values.notion_outbox_token_file).is_relative_to(root):
            raise NotionBindingError("notion_secret_inside_outbox")
        destination = load_destination(
            Path(values.notion_outbox_destination_file),
            values.notion_outbox_destination_sha256,
        )
        token = load_private_token(Path(values.notion_outbox_token_file))
        return root, destination, token

    async def tick(self) -> int | None:
        """One bounded reporting pass. A None delay pauses until explicit restart."""
        delay = self.settings.notion_outbox_poll_seconds
        if not self.settings.notion_outbox_enabled:
            return delay
        try:
            root, destination, token = self._configuration()
            with reporting_control.reporting_lease(root) as control:
                cooldown = reporting_control.retry_delay(control, self._clock())
                if cooldown is None or cooldown > 0:
                    self.status, self.last_code = (
                        "paused",
                        "notion_outbox_rate_limit_pending",
                    )
                    return cooldown
                ids = discover_jobs(root)
                # No HTTP until every durable prior dispatch is resolved. This
                # also preserves global rate/uncertainty pauses across restarts.
                for report_id in ids:
                    view = outbox.recover_job(root, report_id, clock=self._clock)
                    if view.status in {"uncertain", "dispatching", "claimed"}:
                        self.status, self.last_code = (
                            "paused",
                            "notion_outbox_reconciliation_required",
                        )
                        return None
                after = tuple(name for name in ids if name > self._after_id)
                ordered = after + tuple(name for name in ids if name <= self._after_id)
                batch = ordered[: self.settings.notion_outbox_batch_size]
                self.last_result = None
                async with (
                    self._client_factory() as client,
                    asyncio.timeout(self.settings.notion_outbox_pass_timeout_seconds),
                ):
                    try:
                        await verify_binding(client, token, destination)
                    except NotionBindingError as error:
                        if error.code == "notion_binding_rate_limited":
                            reporting_control.record_rate_limit(
                                control, self._clock(), error.retry_after_seconds
                            )
                        raise
                    if self._stop.is_set():
                        raise asyncio.CancelledError
                    adapter = notion_adapter.NotionDeliveryAdapter(
                        client=client,
                        token=token,
                        destination=destination,
                        clock=self._clock,
                    )
                    for report_id in batch:
                        self.last_result = await outbox_worker.run_outbox_pass(
                            root,
                            report_ids=(report_id,),
                            worker_id=self._worker_id,
                            adapter=adapter,
                            policy=outbox_worker.OutboxWorkerPolicy(
                                pass_timeout_seconds=self.settings.notion_outbox_pass_timeout_seconds
                            ),
                            clock=self._clock,
                        )
                        self._after_id = report_id
                        if adapter.rate_limited:
                            reporting_control.record_rate_limit(
                                control,
                                self._clock(),
                                adapter.rate_limit_retry_after_seconds,
                            )
                        if adapter.rate_limited or any(
                            item.outbox_status in {"uncertain", "dispatching"}
                            or item.status == "failed"
                            for item in self.last_result.items
                        ):
                            self.status, self.last_code = (
                                "paused",
                                "notion_outbox_reconciliation_required",
                            )
                            return None
                pending = self.last_result is not None and any(
                    item.outbox_status != "delivered" for item in self.last_result.items
                )
                self.status, self.last_code = (
                    ("pending", "notion_outbox_jobs_pending")
                    if pending
                    else ("running", "notion_outbox_pass_complete")
                )
                self._failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 -- Reporting cannot affect trade state or leak errors.
            self._failures += 1
            self.status, self.last_code = (
                "pending",
                "notion_outbox_configuration_or_delivery_pending",
            )
            delay = min(300, delay * 2 ** min(self._failures - 1, 6))
            if (
                type(error) is NotionBindingError
                and type(error.code) is str
                and error.code in SAFE_CODES
            ):
                self.last_code = error.code
                if error.retry_after_seconds is None:
                    self.status, delay = "paused", None
                elif (
                    type(error.retry_after_seconds) is int
                    and 0 <= error.retry_after_seconds <= 99999
                ):
                    delay = max(delay, error.retry_after_seconds)
            logger.warning("notion_outbox_pending code=%s", self.last_code)
        return delay

    async def _run(self):
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        while not self._stop.is_set():
            delay = await self.tick()
            if delay is None:
                # Shutdown cancels; unknown rate limits are not retried.
                await asyncio.Future()
            else:
                await asyncio.sleep(delay)
