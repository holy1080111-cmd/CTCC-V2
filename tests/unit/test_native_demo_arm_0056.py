"""Arm refusal predicates only; synthetic state never issues native authority."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories import demo_control_current56_v1 as repo
from app.database.repositories.demo_control import ControlObservation
from app.demo_automation import DemoAutomationSafetyError
from app.demo_automation import native_caller_v2 as caller
from app.demo_automation.native_requests_v2 import NativeDemoArmExecuteRequestV2
from app.domain.demo_automation import ARM_PHRASE, EXECUTE_PHRASE
from app.trade_qualification import demo_control
from app.trade_qualification import native_demo_control_owner_v2 as owner
from tests.unit.native_function_primitive_fixture_v1 import foreign_primitive_v1


@pytest.mark.asyncio
async def test_pending_arm_requires_original_exact_confirmation_and_request(
    monkeypatch,
):
    request = NativeDemoArmExecuteRequestV2(
        instrument_id="BTC-USDT-SWAP",
        strategy="trend_pullback",
        contracts="1",
        leverage=3,
        arm_confirmation=ARM_PHRASE,
        execute_confirmation=EXECUTE_PHRASE,
    )
    factory = object()
    cap = object()

    class Service:
        def __init__(self, **fields):
            self.__dict__.update(fields)

    service = Service(
        settings=SimpleNamespace(
            trading_mode="okx_demo",
            okx_demo_enabled=True,
            live_trading=False,
            okx_live_enabled=False,
            okx_live_allow_order_writes=False,
            okx_live_auto_execution=False,
            paper_auto_execution=False,
            environment="production",
            okx_demo_allow_order_writes=True,
            okx_demo_auto_execution=True,
            okx_demo_require_protection=True,
            okx_ws_enabled=True,
            okx_demo_allowed_symbol_list=["BTC-USDT-SWAP"],
            okx_demo_max_order_size_contracts=1,
            okx_demo_max_leverage=3,
        ),
        repository=SimpleNamespace(session_factory=factory),
        _recovered=True,
    )
    monkeypatch.setattr(caller, "_service", lambda value: value.settings)
    plan = caller._freeze_request_v2(request)
    state = {
        "service": service,
        "settings": service.settings,
        "task": asyncio.current_task(),
        "action": "arm_execute",
        "pending_arm_0056": request,
        "arm_plan_0056": plan,
        "controller": cap,
        "selection": object(),
        "factories": SimpleNamespace(writer_factory=factory),
        "submitted_or_unknown": False,
        "shared_control_revision": 2,
        "revoked": False,
    }
    monkeypatch.setattr(caller, "_ACTIVE", {service: state})
    assert caller._check_pending_owned_arm_0056(state, request, plan, cap) == (
        2,
        state["selection"],
    )
    with pytest.raises(DemoAutomationSafetyError, match="owned_arm_0056_required"):
        caller._check_pending_owned_arm_0056(state, request.model_copy(), plan, cap)
    request.arm_confirmation = "WRONG"
    with pytest.raises(
        DemoAutomationSafetyError, match="explicit_confirmation_required"
    ):
        caller._check_pending_owned_arm_0056(state, request, plan, cap)
    request.arm_confirmation = ARM_PHRASE
    state["revoked"] = True
    with pytest.raises(DemoAutomationSafetyError):
        caller._check_pending_owned_arm_0056(state, request, plan, cap)


@pytest.mark.asyncio
async def test_forged_arm_cap_is_denied_before_repository():
    with pytest.raises(
        owner.NativeDemoControlError, match="original_0056_arm_required"
    ):
        await owner._request_owned_arm_0056(
            object.__new__(owner.OwnedDemoControlSessionV2),
            {},
            object(),
            object(),
            expires_at=datetime.now(UTC) + timedelta(seconds=1),
        )


def test_owned_arm_repository_attempt_is_burned(monkeypatch):
    now = datetime.now(UTC)
    scope = demo_control.ControlScope("demo", "730036")
    previous = demo_control.acquire(
        None,
        scope=scope,
        owner_sha256="1" * 64,
        pins=demo_control.ControlPins("2" * 64, "3" * 64, "4" * 64),
        now=now,
    )
    expected = owner._OwnedArmExpectation0056(
        ControlObservation(previous, "5" * 64, now), 2, now + timedelta(seconds=1)
    )
    attempt = object()
    repository = object.__new__(repo.DemoControlRepository0056V1)
    state = {}
    monkeypatch.setitem(owner._ARM_0056, attempt, (state,))
    monkeypatch.setattr(owner, "_replay_owned_0056_arm", lambda *_: expected)
    assert owner._observe_owned_arm_0056(attempt, repository, scope) is expected
    with pytest.raises(owner.NativeDemoControlError, match="attempt_burned"):
        owner._observe_owned_arm_0056(attempt, repository, scope)


@pytest.mark.parametrize(
    "changed",
    ["owner", "handle", "selection", "generation", "arm", "stop", "expiry", "clock"],
)
def test_owned_arm_predicate_rejects_changed_original_state(changed):
    now = datetime.now(UTC)
    scope = demo_control.ControlScope("demo", "730036")
    control = demo_control.acquire(
        None,
        scope=scope,
        owner_sha256="1" * 64,
        pins=demo_control.ControlPins("2" * 64, "3" * 64, "4" * 64),
        now=now,
    )
    previous = ControlObservation(control, "5" * 64, now)
    cap = object.__new__(owner.OwnedDemoControlSessionV2)
    repository = object.__new__(repo.DemoControlRepository0056V1)
    factories, selected, request, plan, attempt = (object() for _ in range(5))
    local = SimpleNamespace(
        _observation=previous, _generation=1, _local_estop=False, _arm_intent=False
    )
    state = {
        "phase": "OWNED_ARM_0056",
        "repository": repository,
        "scope": scope,
        "identity_selection": selected,
        "db0056_factories": factories,
        "observation": previous,
    }
    caller_state = {"factories": factories}
    expected = owner._OwnedArmExpectation0056(previous, 2, now + timedelta(seconds=1))
    owners, handles = {cap: state}, {cap: state}
    stamp = {"utc_ns": int(now.timestamp() * 1_000_000_000) + 1000, "monotonic_ns": 1}
    primitive = foreign_primitive_v1(
        owner._replay_owned_0056_arm,
        {
            "_REQUIRE_KERNEL": lambda: None,
            "_require_context": lambda actual: local if actual is state else None,
            "production": SimpleNamespace(
                _check_pending_owned_arm_0056=lambda *args: (2, selected)
            ),
            "_stamp": lambda: stamp,
            "_OWNERS": owners,
            "_HANDLES": handles,
            "_ARM_0056": {
                attempt: (
                    state,
                    cap,
                    caller_state,
                    request,
                    plan,
                    expected,
                    1,
                    2,
                )
            },
        },
    )
    # A foreign code clone checks only the refusal predicate. The real source
    # namespace guard always rejects it as an authority producer.
    assert primitive(attempt, repository, scope) is expected
    if changed == "owner":
        owners.clear()
    elif changed == "handle":
        handles.clear()
    elif changed == "selection":
        state["identity_selection"] = object()
    elif changed == "generation":
        local._generation += 1
    elif changed == "arm":
        local._arm_intent = True
    elif changed == "stop":
        local._local_estop = True
    elif changed == "expiry":
        stamp["utc_ns"] += 2_000_000_000
    elif changed == "clock":
        stamp["monotonic_ns"] = 2
    with pytest.raises(owner.NativeDemoControlError, match="original_0056_arm_changed"):
        primitive(attempt, repository, scope)
