"""Focused checks for the DB0056 ledger preflight's unresolved-intent counts."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core import native_ledger_preflight_0056_v1 as preflight
from app.database.models.qualification_ledger import (
    QualificationAccountScope,
    QualificationReservation,
    QualificationReservationTransition,
)
from app.domain.source_primitives import sha
from app.trade_qualification.control_bound_ledger import (
    CONSUMED_REASON,
    RESERVED_REASON,
)
from app.trade_qualification.reservations import LedgerScope, canonical
from tests.unit.qualification_execution_binding_fixtures import consumed_receipt
from tests.unit.test_control_bound_ledger import records as records  # noqa: PLC0414

_SCOPE = LedgerScope(account_id="123", settlement_currency="USDT")
_HASH = "4" * 64
_LEGACY_CONSUMED = ("a", "consumed", 2, 1, 2, "reserved", "consumed", None, _HASH)
_DURABLE_CONSUMED = (
    "b",
    "uncertain",
    3,
    2,
    2,
    "reserved",
    CONSUMED_REASON,
    "3" * 64,
    _HASH,
)


def _session(rows):
    return SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: rows))
    )


@pytest.mark.asyncio
async def test_reservations_and_consumed_intents_remain_distinct():
    reserved = ("c", "reserved", 1, None, None, None, None, None, _HASH)
    uncertain = ("d", "uncertain", 2, None, None, None, None, None, _HASH)
    session = _session([reserved, _LEGACY_CONSUMED, _DURABLE_CONSUMED, uncertain])
    held = Mock()
    unresolved, consumed = await preflight._unresolved_under_uid_lock(
        session, _SCOPE, held
    )
    assert unresolved == (reserved, _LEGACY_CONSUMED, _DURABLE_CONSUMED, uncertain)
    assert consumed == 1
    held.assert_called_once_with()
    query = session.execute.call_args.args[0]
    assert query.get_final_froms()[0].isouter is True
    assert _SCOPE.settlement_currency not in query.compile().params.values()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [_DURABLE_CONSUMED, _DURABLE_CONSUMED],
        [("c", "consumed", 2, None, None, None, None, None, _HASH)],
        [("c", "uncertain", 3, None, None, None, None, None, _HASH)],
        [("c", "uncertain", 2, 1, 2, "reserved", CONSUMED_REASON, _HASH, _HASH)],
        [("c", "consumed", 2, 1, 2, "reserved", CONSUMED_REASON, None, _HASH)],
        [("c", "consumed", 2, 1, 2, "uncertain", CONSUMED_REASON, _HASH, _HASH)],
        [("c", "consumed", 2, 1, 3, "reserved", CONSUMED_REASON, _HASH, _HASH)],
        [("c", "consumed", 2, 1, 2, "reserved", "consumed", _HASH, _HASH)],
        [
            (
                "c",
                "reconciled_flat",
                4,
                1,
                2,
                "reserved",
                CONSUMED_REASON,
                _HASH,
                _HASH,
            )
        ],
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


@pytest.mark.asyncio
async def test_uid_fingerprint_binds_second_currency_and_exact_hold_not_just_count(
    monkeypatch,
):
    now = datetime.now(UTC)
    selected = SimpleNamespace(
        account_revision=0, ledger_revision=0, claims_sha256=None, updated_at=now
    )
    requesting = (_SCOPE.settlement_currency, 0, 0, None, now)
    secondary = ("USDC", 1, 1, _HASH, now)
    holds = AsyncMock(return_value=((_DURABLE_CONSUMED,), 1))
    monkeypatch.setattr(preflight, "_unresolved_under_uid_lock", holds)
    replay = AsyncMock(return_value=[[_DURABLE_CONSUMED[0], None, []]])
    monkeypatch.setattr(preflight, "_replay_consumed_under_uid_lock", replay)
    held = Mock()

    async def digest(currencies):
        reservations, intents, summary = await preflight._uid_ledger_checkpoint(
            _session(currencies), _SCOPE, held, selected
        )
        assert reservations == intents == 1
        return summary

    baseline = await digest((secondary, requesting))
    changed_second_currency = await digest((("USDC", 1, 2, _HASH, now), requesting))
    holds.return_value = (((*_DURABLE_CONSUMED[:-2], "5" * 64, _HASH),), 1)
    changed_first_hold = await digest((secondary, requesting))
    assert len({baseline, changed_second_currency, changed_first_hold}) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("control_bound", [False, True])
async def test_held_replay_checks_committed_bytes_and_currency_revisions(
    records, monkeypatch, control_bound
):
    fixture, state, expectation, _, reserve_raw, inner, envelope = records
    request = fixture.request
    consumed = consumed_receipt(fixture)
    scope = consumed.scope
    row = QualificationReservation(
        environment=scope.environment,
        account_id=scope.account_id,
        settlement_currency=scope.settlement_currency,
        reservation_id=consumed.reservation_id,
        state=consumed.state,
        state_revision=consumed.state_revision,
        request_json=canonical(request),
        coverage_json=canonical(consumed.coverage),
        risk_amount=consumed.coverage.risk_amount,
        margin_amount=consumed.coverage.margin_amount,
        notional_amount=consumed.coverage.notional_amount,
        **{
            field: getattr(consumed, field)
            for field in (
                "original_event_key",
                "report_id",
                "instrument_id",
                "direction",
                "correlation_group",
                "request_sha256",
                "deadline",
                "created_at",
                "updated_at",
            )
        },
    )
    intent = envelope if control_bound else inner
    reserved = QualificationReservationTransition(
        id=4,
        from_state=None,
        to_state="reserved",
        state_revision=1,
        reason_code=RESERVED_REASON if control_bound else "risk_reserved",
        evidence_json=reserve_raw if control_bound else None,
        occurred_at=consumed.created_at,
    )
    transition = QualificationReservationTransition(
        id=5,
        from_state="reserved",
        to_state="consumed",
        state_revision=2,
        reason_code=CONSUMED_REASON if control_bound else "consumed_with_submit_intent",
        evidence_json=intent.canonical_json,
        occurred_at=consumed.updated_at,
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=row),
        scalars=AsyncMock(
            return_value=SimpleNamespace(all=lambda: [reserved, transition])
        ),
    )
    check_control = AsyncMock()
    monkeypatch.setattr(
        preflight.locked.DemoControlRepositoryV2,
        "verify_historical_binding",
        check_control,
    )
    currency = (
        scope.settlement_currency,
        consumed.account_revision,
        consumed.ledger_revision,
        _HASH,
        fixture.now + timedelta(seconds=10),
    )
    metadata = (
        consumed.reservation_id,
        row.state,
        row.state_revision,
        transition.id,
        transition.state_revision,
        transition.from_state,
        transition.reason_code,
        intent.sha256,
        consumed.request_sha256,
    )
    result = await preflight._replay_consumed_under_uid_lock(
        session, scope, Mock(), [currency], [metadata]
    )
    assert [item[0] for item in result] == [consumed.reservation_id]
    assert result[0][2][1][-1] == intent.sha256
    if control_bound:
        check_control.assert_awaited_once_with(session, state, expectation.event_sha256)
    else:
        check_control.assert_not_awaited()

    with pytest.raises(preflight.NativeLedgerPreflight0056Error):
        await preflight._replay_consumed_under_uid_lock(
            session,
            scope,
            Mock(),
            [(currency[0], currency[1], currency[2] - 1, *currency[3:])],
            [metadata],
        )
    with pytest.raises(preflight.NativeLedgerPreflight0056Error):
        await preflight._replay_consumed_under_uid_lock(
            session,
            scope,
            Mock(),
            [currency],
            [(*metadata[:-2], sha(b"changed"), metadata[-1])],
        )
    reserved.evidence_json = "{}"
    with pytest.raises(preflight.NativeLedgerPreflight0056Error):
        await preflight._replay_consumed_under_uid_lock(
            session, scope, Mock(), [currency], [metadata]
        )
