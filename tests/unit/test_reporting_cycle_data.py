"""Shared lower cycle negatives: real files, no native permit or remote client."""

import pytest

from app.trade_evidence import outbox, outbox_worker, runtime
from tests.unit.test_trade_evidence_outbox import Clock, payload


@pytest.mark.asyncio
async def test_data_preparation_denial_advances_cursor_without_client_or_claim(
    tmp_path,
):
    clock = Clock()
    for report_id in ("a-first", "b-second"):
        outbox.enqueue(tmp_path, payload(report_id=report_id), clock=clock)
    selected = []

    def forbidden(*_args, **_kwargs):
        pytest.fail("DATA preparation denial reached a client or adapter")

    async def deny_preparation(batch):
        selected.append(batch)
        raise outbox_worker.ReportingPreparationUnavailable(batch[0])

    cursor = ""
    for expected in ("a-first", "b-second"):
        result = await outbox_worker.run_reporting_delivery_cycle(
            tmp_path,
            worker_id="synthetic-data-denial",
            clock=clock,
            poll_seconds=30,
            batch_size=1,
            after_id=cursor,
            client_factory=forbidden,
            discover=runtime.discover_jobs,
            prepare_batch=deny_preparation,
            verify_binding=forbidden,
            make_adapter=forbidden,
            stop_requested=lambda: False,
            timeout_seconds=3,
        )
        assert (result.status, result.code, result.delay, result.after_id) == (
            "pending",
            "notion_outbox_submission_source_authority_unavailable",
            30,
            expected,
        )
        assert result.execution_authority is False
        assert result.source_authenticity_verified is False
        assert result.last_result is None
        cursor = result.after_id
    assert selected == [("a-first",), ("b-second",)]
    for report_id in ("a-first", "b-second"):
        view = outbox.read_job(tmp_path, report_id, clock=clock)
        assert view.status == "queued" and view.head.revision == 1
