"""Declared synthetic ownership and drift controls; no DB/venue authority."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.database.repositories import qualification_ledger as ledger
from app.database.repositories import qualification_native_checkpoint_v2 as checkpoint
from app.trade_qualification import native_ledger_runtime_v2 as runtime
from app.trade_qualification.reservations import LedgerScope

SCOPE = LedgerScope(environment="demo", account_id="730057", settlement_currency="USDT")


@pytest.mark.parametrize("witness", [None, object(), SimpleNamespace(profile="0056")])
async def test_caller_witness_never_reaches_current_event_write(witness):
    with pytest.raises(
        ledger.QualificationLedgerError, match="original_checkpoint_required"
    ):
        await checkpoint.require_held_event_journal_write_0056_v1(
            object(), object(), SCOPE, witness
        )


@pytest.mark.parametrize("head", ["0055", "0056", "0057", "0058"])
async def test_current_journal_dispatch_preserves_old55_and_checks_original_profile(
    monkeypatch, head
):
    session = SimpleNamespace(execute=AsyncMock())
    record = {"request": SimpleNamespace(scope=SCOPE)}
    legacy = AsyncMock()
    session_guard = AsyncMock()
    # Only dispatch is synthetic. No original native registry is populated;
    # the installed event row checker is deliberately denied by the SQL reader.
    monkeypatch.setattr(runtime, "_actual_session", lambda *_: record)
    monkeypatch.setattr(
        runtime, "_record", lambda *_: (record, SimpleNamespace(database_head=head))
    )
    monkeypatch.setattr(ledger, "_require_event_journal_write_schema", legacy)
    monkeypatch.setattr(ledger, "_require_event_journal_session", session_guard)
    catalog = AsyncMock(side_effect=ValueError("DECLARED_SYNTHETIC_CATALOG_DRIFT"))
    monkeypatch.setattr(runtime, "_require_issued_database_head", catalog)
    if head == "0055":
        await runtime.require_event_journal_write_v2(object(), object(), session, SCOPE)
        legacy.assert_awaited_once_with(session, SCOPE)
        session_guard.assert_not_awaited()
    elif head in {"0056", "0057"}:
        with pytest.raises(ValueError, match="SYNTHETIC_CATALOG_DRIFT"):
            await runtime.require_event_journal_write_v2(
                object(), object(), session, SCOPE
            )
        legacy.assert_not_awaited()
        catalog.assert_awaited_once()
        statements = [str(item.args[0]) for item in session.execute.await_args_list]
        assert len(statements) == 2
        assert "pg_advisory_xact_lock" in statements[0]
        assert "ROW EXCLUSIVE MODE NOWAIT" in statements[1]
    else:
        with pytest.raises(
            ledger.QualificationLedgerError, match="issued_database_changed"
        ):
            await runtime.require_event_journal_write_v2(
                object(), object(), session, SCOPE
            )
        legacy.assert_not_awaited()
        session_guard.assert_not_awaited()


async def test_foreign_scope_cannot_select_a_newer_journal(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "_actual_session",
        lambda *_: {"request": SimpleNamespace(scope=object())},
    )
    monkeypatch.setattr(
        runtime, "_record", lambda *_: (None, SimpleNamespace(database_head="0057"))
    )
    with pytest.raises(
        ledger.QualificationLedgerError, match="original_scope_required"
    ):
        await runtime.require_event_journal_write_v2(
            object(), object(), object(), SCOPE
        )
