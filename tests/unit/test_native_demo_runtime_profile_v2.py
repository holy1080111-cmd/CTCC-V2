"""Synthetic profile/dispatch mechanics; no native or exchange acceptance."""

import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.demo_automation import DemoAutomationSafetyError
from app.demo_automation import native_caller_v2 as caller
from app.demo_automation import native_current_source_0056_v1 as source56
from app.demo_automation import native_server_selectors_v2 as selectors
from app.demo_automation import native_source_only_v1 as source_only
from app.demo_automation.native_requests_v2 import NativeDemoArmExecuteRequestV2
from app.domain import native_database_profile_v1 as profiles
from app.domain.demo_automation import ARM_PHRASE, EXECUTE_PHRASE
from app.exchange.okx import native_demo_write_v2 as write
from app.exchange.okx import private_rest
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import native_controlled_account_source_v6 as controlled
from app.trade_qualification import native_demo_control_owner_v2 as owner
from app.trade_qualification import native_invocation_v2 as invocation
from tests.native_role_profile_fixtures_v1 import restricted_roles_fixture


def test_runtime_profile_keeps_the_real_compiled_source_closure_intact():
    # No mocked dependency or foreign code clone enters this check.
    assert controlled._source_bootstrap_marker_v6() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("head", ["0055", "0056", "0057"])
@pytest.mark.parametrize("purpose", ["entry", "history", "recovery"])
async def test_runtime_role_verification_failure_precedes_signed_get(
    monkeypatch, head, purpose
):
    settings = object()
    client = SimpleNamespace(account_config_selector_v2=AsyncMock())
    factories = SimpleNamespace(
        frame_reader_engine=SimpleNamespace(dispose=AsyncMock()),
        custody_engine=SimpleNamespace(dispose=AsyncMock()),
    )
    monkeypatch.setattr(selectors, "_configured_inputs", lambda *_: settings)
    monkeypatch.setattr(selectors, "_server_settings_pin", lambda *_: "fixed")
    monkeypatch.setattr(selectors, "_route_artifact", lambda: b"route")
    monkeypatch.setattr(
        selectors, "select_configured_native_demo_factories_v2", lambda: factories
    )
    head_read = AsyncMock(return_value=head)
    monkeypatch.setattr(selectors, "_observe_runtime_database_head_v2", head_read)
    refused = AsyncMock(side_effect=ValueError("fixture_role_refusal"))
    if head in ("0056", "0057"):
        monkeypatch.setattr(selectors, "_source_roles_before_get_0056", refused)
    else:
        from app.core import native_role_preflight_v1 as roles55

        monkeypatch.setattr(roles55, "verify_selected_native_roles_v1", refused)
    with pytest.raises(selectors.NativeDemoCallerReadinessErrorV2):
        if purpose == "recovery":
            await selectors._select_configured_demo_runtime_recovery_identity_v2(
                private_client=client,
                read_session=object(),
                instrument_ids=("BTC-USDT-SWAP",),
            )
        else:
            await selectors._select_configured_demo_runtime_identity_v2(
                private_client=client,
                read_session=object(),
                instrument_ids=("BTC-USDT-SWAP",),
                history_only=purpose == "history",
            )
    head_read.assert_awaited_once_with(factories)
    refused.assert_awaited_once_with(
        factories,
        **profiles.arguments(profiles.DB0057 if head == "0057" else profiles.DB0056),
    )
    client.account_config_selector_v2.assert_not_awaited()
    factories.frame_reader_engine.dispose.assert_awaited_once()
    factories.custody_engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], ["0055", "0056"], ["0058"], [None]])
async def test_runtime_head_requires_exactly_one_supported_sql_row(monkeypatch, rows):
    statements = []

    class Session:
        def begin(self):
            return nullcontext()

        async def execute(self, statement):
            statements.append(str(statement))
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    session = Session()
    writer = lambda: nullcontext(session)
    factories = selectors.ConfiguredNativeDemoFactoriesV2(
        writer, object(), object(), object(), object()
    )
    monkeypatch.setattr(selectors.runtime, "_configured_factory", lambda _: True)
    monkeypatch.setattr(selectors.history, "_factory_pin", lambda _: "pin")
    with pytest.raises(
        selectors.NativeDemoCallerReadinessErrorV2, match="database_profile_refused"
    ):
        await selectors._observe_runtime_database_head_v2(factories)
    assert statements == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        "SELECT version_num FROM public.alembic_version",
    ]


@pytest.mark.asyncio
async def test_source_only_registration_cannot_dispatch_control_or_recovery(
    monkeypatch,
):
    roles = restricted_roles_fixture()
    record, factories = object(), object()
    entry = {
        "source_roles_0056": roles,
        "database_profile": profiles.DB0056,
        "purpose_0056": selectors._SOURCE_ONLY_0056,
        "record": record,
        "factories": factories,
    }
    monkeypatch.setattr(selectors, "_record", lambda _: entry)
    assert (
        selectors._runtime_selection_database_head_v2(object(), history_only=True)
        == "0056"
    )
    with pytest.raises(selectors.NativeDemoCallerReadinessErrorV2):
        selectors._runtime_selection_database_head_v2(object())
    with pytest.raises(selectors.NativeDemoCallerReadinessErrorV2):
        selectors._replay_configured_demo_recovery_identity_v2(
            object(), key=controlled._SOURCE_KEY_V6
        )
    entry["purpose_0056"] = selectors._CONTROLLED_0056
    assert selectors._replay_configured_demo_recovery_identity_v2(
        object(), key=controlled._SOURCE_KEY_V6
    ) == (record, (factories, roles))
    entry["purpose_0056"] = selectors._RECOVERY_0056
    assert selectors._replay_configured_demo_recovery_identity_v2(
        object(), key=controlled._SOURCE_KEY_V6
    ) == (record, (factories, roles))
    with pytest.raises(selectors.NativeDemoCallerReadinessErrorV2):
        selectors._runtime_selection_database_head_v2(object())
    with pytest.raises(selectors.NativeDemoCallerReadinessErrorV2):
        selectors._controlled_identity_0056(object())
    with pytest.raises(selectors.NativeDemoCallerReadinessErrorV2):
        selectors._replay_configured_demo_recovery_identity_v2(object(), key=object())


@pytest.mark.asyncio
@pytest.mark.parametrize("stopped", [True, False])
@pytest.mark.parametrize("profile", [profiles.DB0056, profiles.DB0057])
async def test_cold56_bootstrap_requires_stop_and_independent_genesis_readback(
    monkeypatch, tmp_path, stopped, profile
):
    events = []
    factories = SimpleNamespace(writer_factory=object())
    selection = SimpleNamespace(
        factories=factories,
        source_roles_0056=object(),
        scope_config=SimpleNamespace(expected_uid="730036"),
    )
    observation = SimpleNamespace(
        control=SimpleNamespace(state=SimpleNamespace(emergency_stop=stopped))
    )

    async def observe(*_, **kwargs):
        assert kwargs == profiles.arguments(profile)
        events.append("control")
        return observation

    async def collect(**kwargs):
        assert kwargs["selection"] is selection and kwargs["bootstrap_only"] is True
        events.append("collect")
        return "a" * 64

    async def genesis(*_):
        events.append("genesis")
        return SimpleNamespace(source_sha256="b" * 64)

    monkeypatch.setattr(caller, "_ready", lambda *_: None)
    monkeypatch.setattr(
        selectors,
        "_runtime_selection_database_head_v2",
        lambda *_a, **_k: profiles.head(profile),
    )
    monkeypatch.setattr(
        selectors, "_selection_database_profile_0056", lambda _: profile
    )
    monkeypatch.setattr(source56, "_observe_control", observe)
    monkeypatch.setattr(
        source56,
        "_readback_control",
        lambda a, b: (
            events.append("readback") if a is b else pytest.fail("changed control")
        ),
    )
    collect_mock = AsyncMock(side_effect=collect)
    monkeypatch.setattr(
        history, "acquire_configured_history_current_source_0056_v1", collect_mock
    )
    monkeypatch.setattr(owner, "_read_genesis", genesis)
    monkeypatch.setattr(source_only, "_history_bundle_hash", lambda _: "a" * 64)
    lease = SimpleNamespace(verify_current=Mock())
    state = {
        "roots": {
            "cold-history-raw": tmp_path / "raw",
            "cold-history-companion": tmp_path / "companion",
        }
    }
    if not stopped:
        with pytest.raises(DemoAutomationSafetyError):
            await caller._bootstrap_cold_history_0056(
                object(), state, selection, lease, object()
            )
        collect_mock.assert_not_awaited()
        assert events == ["control"]
        return
    await caller._bootstrap_cold_history_0056(
        object(), state, selection, lease, object()
    )
    assert events == [
        "control",
        "collect",
        "control",
        "readback",
        "genesis",
        "control",
        "readback",
    ]
    assert state["cold_genesis_sha256"] == "b" * 64
    lease.verify_current.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("head", ["0055", "0056", "0057"])
@pytest.mark.parametrize("profile_changed", [False, True])
async def test_one_request_dispatches_exact_profile_and_retains_g12_refusal(
    monkeypatch, tmp_path, head, profile_changed
):
    """Mocked boundaries only; G12 refusal prevents any submit in this fixture."""
    events = []
    factory, issued = object(), object()
    settings = SimpleNamespace(
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
    )

    class Service:
        pass

    service = Service()
    service.settings = settings
    service._run_lock = asyncio.Lock()
    service._recovered = True  # Synthetic dispatch precondition, never persisted.
    service.repository = SimpleNamespace(session_factory=factory)
    selection = SimpleNamespace(
        factories=SimpleNamespace(writer_factory=factory),
        current_plan=SimpleNamespace(
            created_at=datetime.now(UTC), max_batch_seconds=30
        ),
    )

    class InertOwner:
        async def request_arm(self, **_):
            events.append("arm55")

        async def acquire_controlled_account(self, **_):
            events.append("account")
            return issued

        def emergency_stop(self):
            events.append("revoke")

            async def stopped():
                events.append("stop")

            return stopped()

    cap = InertOwner()
    request = NativeDemoArmExecuteRequestV2(
        instrument_id="BTC-USDT-SWAP",
        strategy="trend_pullback",
        contracts="1",
        leverage=3,
        arm_confirmation=ARM_PHRASE,
        execute_confirmation=EXECUTE_PHRASE,
    )

    async def cold(actual, state, _):
        assert actual is service
        events.append("cold")
        state["database_head"] = (
            ("0055" if head == "0056" else "0056") if profile_changed else head
        )

    async def select_profile(**_):
        events.append("selector")
        return selection

    async def prepare56(actual):
        assert actual is selection
        events.append("owner56")
        return cap

    async def prepare55(actual):
        assert actual is selection
        events.append("owner55")
        return cap

    async def arm56(actual, state, original_request, plan, **_):
        assert actual is cap and original_request is request
        assert caller._check_pending_owned_arm_0056(state, request, plan, cap) == (
            2,
            selection,
        )
        events.append("arm56")

    async def refuse_g12(actual, **_):
        assert actual is issued
        events.append("g12-refusal")
        raise ValueError("fixture_g12_catalog_refused")

    monkeypatch.setattr(caller, "_ACTIVE", {})
    monkeypatch.setattr(caller, "_HELD", {})
    monkeypatch.setattr(
        caller, "_service", lambda actual: settings if actual is service else None
    )
    monkeypatch.setattr(caller, "require_ctcc_token", AsyncMock())
    monkeypatch.setattr(
        caller, "_read_shared_native_write_control_v2", AsyncMock(return_value=2)
    )
    monkeypatch.setattr(caller, "_cold_history", cold)
    monkeypatch.setattr(
        caller,
        "_roots",
        lambda *_: {name: tmp_path / name for name in caller._ROOT_NAMES},
    )
    monkeypatch.setattr(caller, "_market_policy", lambda _: object())
    monkeypatch.setattr(controlled, "_source_bootstrap_marker_v6", lambda: None)
    monkeypatch.setattr(
        private_rest,
        "OkxDemoPrivateRestClient",
        lambda **_: SimpleNamespace(pinned_read_session=lambda: nullcontext(object())),
    )
    monkeypatch.setattr(
        selectors, "_select_configured_demo_runtime_identity_v2", select_profile
    )
    monkeypatch.setattr(
        selectors, "_runtime_selection_database_head_v2", lambda _: head
    )
    monkeypatch.setattr(owner, "prepare_selected_demo_control_owner_0056", prepare56)
    monkeypatch.setattr(owner, "prepare_selected_demo_control_owner_v3", prepare55)
    monkeypatch.setattr(owner, "_request_owned_arm_0056", arm56)
    monkeypatch.setattr(invocation, "complete_owned_native_current55_v2", refuse_g12)
    submit = AsyncMock()
    monkeypatch.setattr(write, "submit_owned_native_demo_v2", submit)
    if profile_changed:
        with pytest.raises(DemoAutomationSafetyError, match="database_profile_changed"):
            await caller.perform_native_demo_action_v2(
                service, request, operator_token="fixture"
            )
        assert events == ["cold", "selector"]
        submit.assert_not_awaited()
        assert not caller._ACTIVE
        retained = next(iter(caller._HELD.values()))
        assert retained["controller"] is None
        assert retained["submitted_or_unknown"] is False
        return
    with pytest.raises(ValueError, match="fixture_g12_catalog_refused"):
        await caller.perform_native_demo_action_v2(
            service, request, operator_token="fixture"
        )
    assert events == [
        "cold",
        "selector",
        "owner" + ("55" if head == "0055" else "56"),
        "arm" + ("55" if head == "0055" else "56"),
        "account",
        "g12-refusal",
        "revoke",
        "stop",
    ]
    submit.assert_not_awaited()
    assert not caller._ACTIVE
    retained = next(iter(caller._HELD.values()))
    assert retained["controller"] is cap
    assert retained["submitted_or_unknown"] is False
    assert retained["negative_request"]["status"] == "confirmed"
    assert "pending_arm_0056" not in retained
