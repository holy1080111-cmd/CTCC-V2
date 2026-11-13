"""Focused checks for the DB0056 ledger preflight's unresolved-intent counts."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core import native_ledger_preflight_0056_v1 as preflight
from app.database.models.qualification_ledger import QualificationAccountScope
from app.trade_qualification.control_bound_ledger import CONSUMED_REASON
from app.trade_qualification.reservations import LedgerScope

_SCOPE = LedgerScope(account_id="123", settlement_currency="USDT")
_LEGACY_CONSUMED = ("a", "consumed", 2, 1, 2, "reserved", "consumed", False)
_DURABLE_CONSUMED = ("b", "uncertain", 3, 2, 2, "reserved", CONSUMED_REASON, True)


def _session(rows):
    return SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: rows))
    )


@pytest.mark.asyncio
async def test_reservations_and_consumed_intents_remain_distinct():
    reserved = ("c", "reserved", 1, None, None, None, None, False)
    uncertain = ("d", "uncertain", 2, None, None, None, None, False)
    session = _session([reserved, _LEGACY_CONSUMED, _DURABLE_CONSUMED, uncertain])
    held = Mock()
    assert await preflight._unresolved_under_uid_lock(session, _SCOPE, held) == (4, 1)
    held.assert_called_once_with()
    query = session.execute.call_args.args[0]
    assert query.get_final_froms()[0].isouter is True
    assert _SCOPE.settlement_currency not in query.compile().params.values()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [_DURABLE_CONSUMED, _DURABLE_CONSUMED],
        [("c", "consumed", 2, None, None, None, None, False)],
        [("c", "uncertain", 3, None, None, None, None, False)],
        [("c", "uncertain", 2, 1, 2, "reserved", CONSUMED_REASON, True)],
        [("c", "consumed", 2, 1, 2, "reserved", CONSUMED_REASON, False)],
        [("c", "consumed", 2, 1, 2, "uncertain", CONSUMED_REASON, True)],
        [("c", "consumed", 2, 1, 3, "reserved", CONSUMED_REASON, True)],
        [("c", "consumed", 2, 1, 2, "reserved", "consumed", True)],
        [("c", "reconciled_flat", 4, 1, 2, "reserved", CONSUMED_REASON, True)],
    ],
)
async def test_conflicting_or_partial_intent_lineage_refuses(rows):
    with pytest.raises(
        preflight.NativeLedgerPreflight0056Error,
        match="^native_ledger_0056_preflight_refused$",
    ):
        await preflight._unresolved_under_uid_lock(_session(rows), _SCOPE, Mock())


@pytest.mark.asyncio
async def test_cold_scope_stays_bootstrap_and_refuses_false_claims():
    row = QualificationAccountScope(
        environment="demo",
        account_id=_SCOPE.account_id,
        settlement_currency="USDT",
        account_revision=0,
        ledger_revision=0,
    )
    repository = SimpleNamespace(_now=lambda _: datetime.now(UTC))
    assert (
        await preflight._replay_historical_scope(
            object(), repository, _SCOPE, row, Mock()
        )
        == "BOOTSTRAP"
    )
    row.claims_json = "{}"
    with pytest.raises(preflight.NativeLedgerPreflight0056Error):
        await preflight._replay_historical_scope(
            object(), repository, _SCOPE, row, Mock()
        )
