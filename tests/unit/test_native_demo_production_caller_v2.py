"""Public request/negative-control mechanisms; never native/account evidence.

Inert fake negative handles below test only synchronous revocation, one-use IO
settlement and strong unknown retention. No positive native producer, auth
setting, registry, DB, read session, source checker or submit API is replaced.
"""

import asyncio

import pytest
from pydantic import ValidationError

from app.demo_automation import DemoAutomationSafetyError
from app.demo_automation import native_caller_v2 as caller
from app.demo_automation.native_private_root_v2 import (
    NativeEvidenceRootErrorV2,
    require_private_evidence_base_v2,
)
from app.demo_automation.native_requests_v2 import (
    NativeDemoActionResultV2,
    NativeDemoArmExecuteRequestV2,
)
from app.domain.demo_automation import ARM_PHRASE, EXECUTE_PHRASE
from app.exchange.okx import native_demo_write_v2 as write
from app.trade_qualification import native_continuation_guard_v1 as guard
from app.trade_qualification import native_ledger_runtime_v2 as ledger
from tests.unit.native_function_primitive_fixture_v1 import foreign_primitive_v1


def _request(**changes):
    values = {
        "instrument_id": "BTC-USDT-SWAP",
        "strategy": "trend_pullback",
        "contracts": "1",
        "leverage": 1,
        "arm_confirmation": ARM_PHRASE,
        "execute_confirmation": EXECUTE_PHRASE,
    }
    values.update(changes)
    return NativeDemoArmExecuteRequestV2(**values)


def test_native_write_guard_rejects_a_replaced_shared_stop_check(monkeypatch):
    async def admit_anything(*_args, **_kwargs):
        return None

    monkeypatch.setattr(caller, "_lock_shared_native_write_control_v2", admit_anything)
    with pytest.raises(guard.NativeSourceClosureError):
        write._NATIVE_GUARD()


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ("entry", "close", "leverage"))
async def test_physical_gate_requires_shared_stop_readback_after_tls(
    purpose, monkeypatch
):
    calls = []
    gate, session = object(), object()
    record = {"state": object(), "session": session, "tls_ready": False}
    if purpose == "close":
        record["close_purpose"] = write._CLOSE_INTENT_KEY_V1
    elif purpose == "leverage":
        record["configuration_purpose"] = write._LEVERAGE_CONFIGURATION_KEY_V1

    async def locked_readback(*_args, **_kwargs):
        calls.append("native-readback")

    async def shared_stop(actual, *, action):
        assert actual is session
        assert action == (
            "configure_leverage" if purpose == "leverage" else "arm_execute"
        )
        assert record["tls_ready"] is False
        calls.append("shared-stop")
        raise DemoAutomationSafetyError("synthetic_shared_stop")

    monkeypatch.setattr(caller, "_lock_shared_native_write_control_v2", shared_stop)
    monkeypatch.setattr(ledger, "_lock_native_intent_for_transport_v2", locked_readback)
    monkeypatch.setattr(
        write._native_leverage_configuration, "lock_after_tls_v1", locked_readback
    )
    primitive = foreign_primitive_v1(
        write._PhysicalWriteGateV2.tls_ready_after_locked_readback,
        {
            "_checked_gate": lambda actual: record if actual is gate else None,
            "_close_function_v1": lambda *_args: locked_readback,
        },
    )
    with pytest.raises(DemoAutomationSafetyError, match="synthetic_shared_stop"):
        await primitive(gate)
    assert calls == ["native-readback", "shared-stop"]
    assert record["tls_ready"] is False


def test_explicit_request_contains_no_source_truth():
    value = _request()
    assert set(value.model_dump()) == {
        "instrument_id",
        "strategy",
        "contracts",
        "leverage",
        "arm_confirmation",
        "execute_confirmation",
    }
    result = NativeDemoActionResultV2(
        invocation_id="inert",
        action="arm_execute",
        status="reconciliation_data_hold_retained",
        risk_hold_retained=True,
    )
    assert result.execution_complete is False


@pytest.mark.parametrize(
    "extra",
    ["uid", "main_uid", "claims", "factory", "clock", "roots", "arm_expires_at"],
)
def test_http_cannot_supply_native_authority(extra):
    with pytest.raises(ValidationError):
        _request(**{extra: "untrusted"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("contracts", "0"),
        ("contracts", "NaN"),
        ("contracts", "Infinity"),
        ("contracts", "1e2"),
        ("contracts", "-1"),
        ("contracts", 1),
        ("contracts", "0.000000000000000000001"),
        ("leverage", True),
        ("leverage", 0),
        ("arm_confirmation", ""),
        ("execute_confirmation", ""),
        ("instrument_id", "BTC-USDT"),
        ("strategy", "caller_pass"),
    ],
)
def test_closed_request_rejects_unapproved_values(field, value):
    with pytest.raises(ValidationError):
        _request(**{field: value})


@pytest.mark.parametrize("root", ["", ".", "relative/private", "..", 1, None])
def test_private_root_has_no_default_or_relative_fallback(root):
    with pytest.raises(NativeEvidenceRootErrorV2):
        require_private_evidence_base_v2(root)


def _inert_states(monkeypatch, owners):
    # This closed inert test only reaches negative handle revocation. No positive
    # function consumes these dictionaries or claims them to be genuine owners.
    service = object()
    monkeypatch.setattr(caller, "_ACTIVE", {})
    monkeypatch.setattr(caller, "_HELD", {})
    monkeypatch.setattr(caller, "_service", lambda value: None)
    states = []
    for index, owner in enumerate(owners):
        state = {
            "id": f"inert-{index}",
            "service": service,
            "controller": owner,
            "revoked": False,
        }
        caller._HELD[state["id"]] = state
        states.append(state)
    if states:
        caller._ACTIVE[service] = states[0]
    return service, states


@pytest.mark.asyncio
async def test_negative_control_revokes_once_before_first_await(monkeypatch):
    events = []

    class InertNegativeHandle:
        def disarm(self):
            events.append("local-revoked")

            async def persist():
                events.append("durable-command")

            return persist()

    handle = InertNegativeHandle()
    service, states = _inert_states(monkeypatch, [handle])
    operation = caller.revoke_native_for_service_v2(service, emergency=False)
    assert events == ["local-revoked"]  # ACTIVE/HELD duplicate is one original.
    assert states[0]["revoked"] is True
    assert caller._HELD[states[0]["id"]] is states[0]
    await operation
    assert events == ["local-revoked", "durable-command"]
    await caller.revoke_native_for_service_v2(service, emergency=False)
    assert events == ["local-revoked", "durable-command"]


@pytest.mark.asyncio
async def test_one_sync_failure_does_not_skip_other_local_revocations(monkeypatch):
    events = []

    class FailedNegativeHandle:
        def emergency_stop(self):
            events.append("first-revoked")
            raise RuntimeError("inert")

    class OtherNegativeHandle:
        def emergency_stop(self):
            events.append("second-revoked")

            async def persist():
                events.append("second-persisted")

            return persist()

    service, states = _inert_states(
        monkeypatch, [FailedNegativeHandle(), OtherNegativeHandle()]
    )
    operation = caller.revoke_native_for_service_v2(service, emergency=True)
    assert events == ["first-revoked", "second-revoked"]
    assert all(state["revoked"] is True for state in states)
    with pytest.raises(
        DemoAutomationSafetyError, match="native_demo_stop_persistence_unknown"
    ):
        await operation
    assert events[-1] == "second-persisted"
    assert caller._HELD[states[0]["id"]] is states[0]
    assert states[0]["negative_request"]["status"] == "unknown"
    # The unknown is neither retried nor silently reported as persisted.
    with pytest.raises(
        DemoAutomationSafetyError, match="native_demo_stop_persistence_unknown"
    ):
        await caller.revoke_native_for_service_v2(service, emergency=True)
    assert events == ["first-revoked", "second-revoked", "second-persisted"]


@pytest.mark.asyncio
async def test_negative_persistence_cancel_retains_actual_pending_operation(
    monkeypatch,
):
    started, never = asyncio.Event(), asyncio.Event()

    class InertNegativeHandle:
        def emergency_stop(self):
            async def persist():
                started.set()
                await never.wait()

            return persist()

    handle = InertNegativeHandle()
    service, states = _inert_states(monkeypatch, [handle])
    operation = caller.revoke_native_for_service_v2(service, emergency=True)
    task = asyncio.create_task(operation)
    async with asyncio.timeout(1):
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert caller._HELD[states[0]["id"]] is states[0]
    assert states[0]["controller"] is handle
    assert states[0]["negative_request"]["status"] == "unknown"
    assert "operation" in states[0]["negative_request"]
    with pytest.raises(DemoAutomationSafetyError):
        await caller.revoke_native_for_service_v2(service, emergency=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("arm_confirmation", ""),
        ("execute_confirmation", "wrong"),
        ("contracts", "NaN"),
        ("contracts", "1e2"),
        ("contracts", 1),
        ("contracts", "01"),
        ("contracts", "1."),
        ("contracts", "0"),
        ("contracts", "1.000000000000000000001"),
        ("leverage", True),
        ("leverage", 126),
        ("strategy", "caller_pass"),
        ("instrument_id", "btc-USDT-SWAP"),
        ("instrument_id", "BTC-USDT"),
    ],
)
def test_direct_service_revalidates_model_constructed_fields(field, value):
    raw = _request().model_dump()
    raw[field] = value
    request = NativeDemoArmExecuteRequestV2.model_construct(**raw)
    with pytest.raises(DemoAutomationSafetyError):
        caller._freeze_request_v2(request)


@pytest.mark.parametrize(
    "field,value",
    [
        ("strategy", "breakout_continuation"),
        ("contracts", "2"),
        ("leverage", 2),
        ("instrument_id", "ETH-USDT-SWAP"),
        ("arm_confirmation", ""),
        ("execute_confirmation", ""),
    ],
)
def test_selected_request_cannot_change_after_an_await(field, value):
    request = _request()
    selected = caller._freeze_request_v2(request)
    original = (
        selected.action,
        selected.instrument_id,
        selected.strategy,
        selected.contracts,
        selected.leverage,
        selected.confirmation,
    )
    setattr(request, field, value)
    with pytest.raises(DemoAutomationSafetyError):
        caller._check_request_v2(request, selected)
    assert (
        selected.action,
        selected.instrument_id,
        selected.strategy,
        selected.contracts,
        selected.leverage,
        selected.confirmation,
    ) == original


def test_selected_plan_is_frozen_and_validates_its_own_field_values():
    from dataclasses import FrozenInstanceError

    selected = caller._freeze_request_v2(_request())
    assert selected.action == "arm_execute"
    assert str(selected.contracts) == "1"
    with pytest.raises(FrozenInstanceError):
        selected.leverage = 2
    # Correct explicit confirmations remain valid as pure selection DATA even
    # if model_construct skipped Pydantic. This does not create native authority.
    constructed = NativeDemoArmExecuteRequestV2.model_construct(
        **_request().model_dump()
    )
    assert caller._freeze_request_v2(constructed) == selected


@pytest.mark.asyncio
async def test_invalid_confirmation_rejects_before_first_auth_or_source_await(
    monkeypatch,
):
    calls = []

    async def auth(_token):
        calls.append("unexpected-auth-await")

    monkeypatch.setattr(caller, "require_ctcc_token", auth)
    request = NativeDemoArmExecuteRequestV2.model_construct(
        **{**_request().model_dump(), "arm_confirmation": "unconfirmed"}
    )
    with pytest.raises(
        DemoAutomationSafetyError, match="explicit_confirmation_required"
    ):
        await caller.perform_native_demo_action_v2(
            object(), request, operator_token="inert"
        )
    assert calls == []
