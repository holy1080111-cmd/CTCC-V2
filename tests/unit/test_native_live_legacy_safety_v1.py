"""Negative-only SQL composition, not a Live session or execution permit."""

import pytest

from app.domain import native_live_financial_epoch_schema_v1 as financial
from app.domain import native_live_ledger_schema_v1 as ledger
from app.domain import native_live_legacy_safety_v1 as subject


@pytest.mark.asyncio
@pytest.mark.parametrize("results", ((False,), (None,), (True, None), (True, False)))
async def test_unknown_busy_or_uncertain_legacy_state_never_means_clear(results):
    class Session:
        async def scalar(self, statement):
            return queue.pop(0)

    queue = list(results)
    with pytest.raises(ValueError):
        await subject.require_legacy_live_negative_fence_v1(Session())


@pytest.mark.asyncio
async def test_known_empty_legacy_domain_produces_no_positive_authority_and_reuses_original_lock():
    calls = []

    class Session:
        async def scalar(self, statement):
            calls.append(str(statement))
            return True

    assert await subject.require_legacy_live_negative_fence_v1(Session()) is None
    assert "pg_try_advisory_xact_lock(1680010)" in calls[0]
    assert all(table in calls[1] for table in subject.TABLES)
    assert "IS NOT TRUE" in calls[1] and "safety_latched" in calls[1]


def test_writer_and_financial_collector_get_only_exact_legacy_selects():
    for grant in (
        ledger.live_ledger_grant_sql_v1("ctcc_live_writer"),
        financial.live_financial_collector_grant_sql_v1("ctcc_live_financial"),
    ):
        for table in subject.TABLES:
            matching = [item for item in grant if "public." + table + " " in item]
            assert len(matching) == 1 and matching[0].startswith(
                "GRANT SELECT ON TABLE "
            )
