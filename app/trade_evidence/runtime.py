"""Independent, default-off durable reporting worker; no trading operations.

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

from app.config.settings import Settings, get_settings
from app.trade_evidence import (
    notion_adapter,
    outbox,
    outbox_worker,
    storage,
)
from app.trade_evidence.notion_binding import (
    NotionBindingError,
    load_destination,
    load_private_token,
)

logger = logging.getLogger(__name__)
SAFE_CODES = frozenset(
    {
        "notion_outbox_configuration_missing",
        "notion_secret_inside_outbox",
        "notion_outbox_directory_limit",
        "notion_outbox_case_alias",
        "notion_outbox_job_pair_incomplete",
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
        "notion_outbox_submission_source_authority_unavailable",
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
        reports, journals = set(), set()
        for name in names:
            if name.endswith(".json"):
                report_id = name[:-5]
                outbox._name(report_id)
                reports.add(report_id)
            elif name.endswith(".state"):
                report_id = name[:-6]
                outbox._name(report_id)
                journals.add(report_id)
        if reports != journals:
            # A late publisher failure may have left a durable first event
            # without its envelope. Keep that evidence and stop this queue;
            # another complete report is not permission to skip the orphan.
            # Recheck later so source-owned spool recovery can complete it;
            # no HTTP is possible while the pair remains incomplete.
            raise NotionBindingError("notion_outbox_job_pair_incomplete")
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

    def _configuration_presence(self):
        """Existing missing-config reason before source, token or filesystem IO."""
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

    async def tick(self) -> int | None:
        """Genuine native source preparation around the shared lower DATA cycle."""
        delay = self.settings.notion_outbox_poll_seconds
        source_permit = None
        cycle_progress = None
        if not self.settings.notion_outbox_enabled:
            return delay
        try:
            self._configuration_presence()
            if self.settings is not get_settings():
                raise NotionBindingError(
                    "notion_outbox_submission_source_authority_unavailable",
                    retry_after_seconds=None,
                )
            from app.trade_evidence import (
                native_notion_delivery_source_v55 as native_source,
            )

            root = storage._root_path(Path(self.settings.notion_outbox_root))
            destination = token = None

            async def prepare_batch(batch):
                nonlocal source_permit, destination, token
                for report_id in batch:
                    view = outbox.validate_outbox_view(
                        outbox.read_job(root, report_id, clock=outbox.actual_utc)
                    )
                    if (
                        view.status not in {"queued", "retry_wait"}
                        or view.head.next_attempt_at > view.verified_at
                    ):
                        continue
                    try:
                        source_permit = await native_source.acquire_native_notion_delivery_source_v55(
                            root=root,
                            report_id=report_id,
                            expected_envelope_sha256=view.envelope.envelope_sha256,
                        )
                    except native_source.NativeNotionSourceUnavailable:
                        raise outbox_worker.ReportingPreparationUnavailable(
                            report_id
                        ) from None
                    actual_root, destination, token = self._configuration()
                    if actual_root != root:
                        raise NotionBindingError(
                            "notion_outbox_submission_source_authority_unavailable"
                        )
                    native_source.require_prepared_native_notion_source_v55(
                        source_permit, root=root, report_ids=(report_id,)
                    )
                    return (report_id,)
                return None

            async def verify_binding(client):
                await native_source.verify_native_notion_binding_v55(
                    source_permit, client=client, token=token, destination=destination
                )

            def make_adapter(client, check_pairs):
                adapter = notion_adapter.NotionDeliveryAdapter(
                    client=client,
                    token=token,
                    destination=destination,
                    clock=self._clock,
                    pre_create_check=check_pairs,
                )
                return native_source.make_native_notion_adapter_v55(
                    source_permit, adapter=adapter
                ), adapter

            cycle_progress = outbox_worker.ReportingCycleProgress(
                self._after_id, self.last_result, self._failures
            )
            data = await outbox_worker.run_reporting_delivery_cycle(
                root,
                worker_id=self._worker_id,
                clock=self._clock,
                poll_seconds=delay,
                batch_size=self.settings.notion_outbox_batch_size,
                after_id=self._after_id,
                client_factory=self._client_factory,
                discover=discover_jobs,
                prepare_batch=prepare_batch,
                verify_binding=verify_binding,
                make_adapter=make_adapter,
                stop_requested=self._stop.is_set,
                timeout_seconds=self.settings.notion_outbox_pass_timeout_seconds,
                progress=cycle_progress,
            )
            self.status, self.last_code, delay = data.status, data.code, data.delay
            self._after_id, self.last_result, self._failures = (
                data.after_id,
                data.last_result,
                data.failures,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 -- no private error text or trade consequence.
            if cycle_progress is not None:
                self._after_id, self.last_result = (
                    cycle_progress.after_id,
                    cycle_progress.last_result,
                )
            data = outbox_worker.reporting_error_data(
                error,
                poll_seconds=delay,
                failures=self._failures,
                after_id=self._after_id,
                safe_codes=SAFE_CODES,
            )
            self.status, self.last_code, delay = data.status, data.code, data.delay
            self._failures = data.failures
            logger.warning("notion_outbox_pending code=%s", self.last_code)
        finally:
            if source_permit is not None:
                try:
                    source_permit.close()
                except BaseException:  # noqa: BLE001 -- Preserve the enclosing operation's result and mark custody pending.
                    self.status, self.last_code = (
                        "pending",
                        "notion_outbox_submission_source_authority_unavailable",
                    )
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
