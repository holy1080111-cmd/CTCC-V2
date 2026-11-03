"""MockTransport-only harness for shared reporting DATA mechanics.

This class never calls or mints a native source permit. It exercises the same
lower queue cycle used by production, with existing synthetic adapter tests'
explicit legacy gate fixture. It proves no genuine historical/remote admission.
"""

import asyncio

import httpx

from app.trade_evidence import notion_adapter, outbox_worker, runtime
from app.trade_evidence.notion_binding import verify_binding


class SyntheticReportingDataRuntime(runtime.NotionOutboxRuntime):
    source_authenticity_verified = False
    remote_acceptance_verified = False
    execution_authority = False

    async def tick(self):
        delay = self.settings.notion_outbox_poll_seconds
        if not self.settings.notion_outbox_enabled:
            return delay
        progress = outbox_worker.ReportingCycleProgress(
            self._after_id, self.last_result, self._failures
        )
        try:
            root, destination, token = self._configuration()

            async def prepare_batch(batch):
                # An empty synthetic batch still tests metadata/cooldown IO.
                return batch

            async def verify_synthetic_binding(client):
                if type(client._transport) is not httpx.MockTransport:
                    raise ValueError("synthetic_reporting_requires_mock_transport")
                await verify_binding(client, token, destination)

            def make_adapter(client, check_pairs):
                adapter = notion_adapter.NotionDeliveryAdapter(
                    client=client,
                    token=token,
                    destination=destination,
                    clock=self._clock,
                    pre_create_check=check_pairs,
                )
                return adapter, adapter

            data = await outbox_worker.run_reporting_delivery_cycle(
                root,
                worker_id=self._worker_id,
                clock=self._clock,
                poll_seconds=delay,
                batch_size=self.settings.notion_outbox_batch_size,
                after_id=self._after_id,
                client_factory=self._client_factory,
                discover=runtime.discover_jobs,
                prepare_batch=prepare_batch,
                verify_binding=verify_synthetic_binding,
                make_adapter=make_adapter,
                stop_requested=self._stop.is_set,
                timeout_seconds=self.settings.notion_outbox_pass_timeout_seconds,
                progress=progress,
            )
            self.status, self.last_code, delay = data.status, data.code, data.delay
            self._after_id, self.last_result = data.after_id, data.last_result
            self._failures = data.failures
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 -- exercise actual fixed-code mapping.
            self._after_id, self.last_result = progress.after_id, progress.last_result
            data = outbox_worker.reporting_error_data(
                error,
                poll_seconds=delay,
                failures=self._failures,
                after_id=self._after_id,
                safe_codes=runtime.SAFE_CODES,
            )
            self.status, self.last_code, delay = data.status, data.code, data.delay
            self._failures = data.failures
            runtime.logger.warning("notion_outbox_pending code=%s", self.last_code)
        return delay
