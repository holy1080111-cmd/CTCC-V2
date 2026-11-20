"""Synthetic dispatch only; no native owner, database or execution admission."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.trade_qualification import native_ledger_runtime_v2 as runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("head", ["0055", "0056", "0057"])
async def test_control_read_keeps_original55_56_calls_and_explicit_actual57(
    monkeypatch, head
):
    scope = SimpleNamespace(environment="demo", account_id="123")
    request = SimpleNamespace(scope=scope)
    record = {"request": request, "control_expectation": object()}
    issued = SimpleNamespace(database_head=head)
    session, repository, binding = object(), object(), object()
    state, event = object(), object()
    legacy, current = (
        AsyncMock(return_value=(state, event)),
        AsyncMock(return_value=(state, event)),
    )
    guard = Mock()
    # The real registry and actual-session gates are deliberately absent in
    # this DATA-only branch test. This does not construct a native capability.
    monkeypatch.setattr(runtime, "_actual_session", lambda *_: record)
    monkeypatch.setattr(runtime, "_record", lambda *_: (record, issued))
    monkeypatch.setattr(
        runtime._native_control_repository55.DemoControlRepositoryV2,
        "read_in_transaction",
        legacy,
    )
    monkeypatch.setattr(
        runtime._native_issuer_transaction, "read_actual_issuer_control_0056", current
    )
    monkeypatch.setattr(runtime._native_control, "guard_current_control", guard)
    monkeypatch.setattr(runtime, "_clock", lambda: "DECLARED_SYNTHETIC_CLOCK")
    assert await runtime.read_control_v2(repository, binding, session, scope) == (
        state,
        event,
    )
    control_scope = runtime._native_control.control.ControlScope("demo", "123")
    if head == "0055":
        legacy.assert_awaited_once_with(session, control_scope)
        current.assert_not_awaited()
    elif head == "0056":
        current.assert_awaited_once_with(session, control_scope)
        legacy.assert_not_awaited()
    else:
        current.assert_awaited_once_with(session, control_scope, database_head="0057")
        legacy.assert_not_awaited()
    guard.assert_called_once_with(
        state,
        event,
        record["control_expectation"],
        scope,
        now="DECLARED_SYNTHETIC_CLOCK",
        request=request,
    )
