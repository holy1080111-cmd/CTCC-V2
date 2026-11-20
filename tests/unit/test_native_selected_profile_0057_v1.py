"""Closed successor dispatch/refusal mechanics; no native/account acceptance."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core import native_account_restart_recovery_0056_v1 as restart
from app.core import native_manual_demo_lifespan_0056_v1 as manual
from app.database.repositories import demo_control_current56_v1 as repository
from app.demo_automation import native_server_selectors_v2 as selectors
from app.domain import native_database_profile_v1 as profiles
from app.trade_qualification.demo_control import ControlScope
from tests.native_role_profile_fixtures_v1 import restricted_roles_fixture
from tests.unit.test_native_manual_demo_http_0056_v1 import app as http_fixture
from tests.unit.test_native_manual_demo_lifespan_0056_v1 import lab


def test_registered_profile_must_match_exact_typed_witness_and_wire_schema(monkeypatch):
    roles = restricted_roles_fixture(profiles.DB0057)
    record, factories = object(), object()
    entry = {
        "source_roles_0056": roles,
        "database_profile": profiles.DB0057,
        "purpose_0056": selectors._CONTROLLED_0056,
        "record": record,
        "factories": factories,
    }
    monkeypatch.setattr(selectors, "_record", lambda _: entry)
    assert selectors._selection_database_profile_0056(object()) is profiles.DB0057
    assert selectors._runtime_selection_database_head_v2(object()) == "0057"
    assert selectors._controlled_identity_0056(object()) == (record, factories, roles)
    entry["database_profile"] = profiles.DB0056
    with pytest.raises(
        selectors.NativeDemoCallerReadinessErrorV2, match="profile_changed"
    ):
        selectors._controlled_identity_0056(object())
    entry["database_profile"] = profiles.DB0057
    entry["source_roles_0056"] = replace(
        roles, schema="ctcc.native_only.restricted0056_runtime_witness.v1"
    )
    with pytest.raises(
        selectors.NativeDemoCallerReadinessErrorV2, match="profile_refused"
    ):
        selectors._controlled_identity_0056(object())


def test_source_only_and_retained_recovery_successors_cannot_enter_arm_registry(
    monkeypatch,
):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    roles = restricted_roles_fixture(profiles.DB0057)
    record, factories = object(), object()
    entry = {
        "source_roles_0056": roles,
        "database_profile": profiles.DB0057,
        "purpose_0056": selectors._SOURCE_ONLY_0056,
        "record": record,
        "factories": factories,
    }
    monkeypatch.setattr(selectors, "_record", lambda _: entry)
    assert (
        selectors._runtime_selection_database_head_v2(object(), history_only=True)
        == "0057"
    )
    with pytest.raises(selectors.NativeDemoCallerReadinessErrorV2):
        selectors._replay_configured_demo_recovery_identity_v2(
            object(), key=source._SOURCE_KEY_V6
        )
    entry["purpose_0056"] = selectors._RECOVERY_0056
    assert selectors._replay_configured_demo_recovery_identity_v2(
        object(), key=source._SOURCE_KEY_V6
    ) == (record, (factories, roles))
    with pytest.raises(selectors.NativeDemoCallerReadinessErrorV2):
        selectors._controlled_identity_0056(object())
    with pytest.raises(selectors.NativeDemoCallerReadinessErrorV2):
        selectors._runtime_selection_database_head_v2(object())


def test_successor_witness_does_not_select_repository_profile_implicitly():
    writer = AsyncMock()
    factories = selectors.ConfiguredNativeDemoFactoriesV2(
        writer, object(), object(), object(), object()
    )
    roles = restricted_roles_fixture(profiles.DB0057)
    arguments = {
        "roles": roles,
        "scope": ControlScope("demo", "730057"),
        "clock": lambda: datetime.now(UTC),
    }
    with pytest.raises(repository.DemoControlRepository0056Error):
        repository.DemoControlRepository0056V1(factories, **arguments)
    current = repository.DemoControlRepository0056V1(
        factories, **arguments, profile=profiles.DB0057
    )
    assert current.database_profile is profiles.DB0057
    current._profile = profiles.DB0056
    with pytest.raises(repository.DemoControlRepository0056Error):
        current._check_scope(arguments["scope"])
    writer.assert_not_called()


@pytest.mark.asyncio
async def test_manual_startup_replays_successor_and_returns_actual_head(monkeypatch):
    config, factory, chosen, automation, seen, dispose, *_ = lab(
        monkeypatch, profiles.DB0057
    )
    assert await manual.start_manual_demo_0056_v1(config, factory, automation) == "0057"
    assert seen == ["roles", "account_stop", "genuine_recover", "roles"]
    automation.recover.assert_awaited_once_with(
        require_existing=True, expected_revision=7
    )
    dispose.assert_awaited_once_with(chosen)


@pytest.mark.asyncio
async def test_manual_successor_cannot_accept_previous_head_witness(monkeypatch):
    config, factory, _, automation, _, _, roles, _, _ = lab(
        monkeypatch, profiles.DB0057
    )
    monkeypatch.setattr(
        roles,
        "observe_restricted_native_roles_0056_witness_v1",
        AsyncMock(return_value=restricted_roles_fixture()),
    )
    with pytest.raises(manual.NativeManualDemoStartup0056Error):
        await manual.start_manual_demo_0056_v1(config, factory, automation)
    automation.recover.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("head", ["0056", "0057", None, "0058"])
async def test_http_readiness_uses_only_successfully_published_actual_head(
    monkeypatch, head
):
    instance = http_fixture(monkeypatch)
    instance.state.native_manual_demo_database_head = head
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=instance), base_url="http://test"
    ) as client:
        response = await client.get("/readiness")
    if head in ("0056", "0057"):
        assert response.status_code == 200
        assert response.json()["database_head"] == head
        assert response.json()["execution_authority"] is False
    else:
        assert response.status_code == 503


@pytest.mark.asyncio
async def test_selected_successor_stop_passes_profile_to_mutation_and_readback(
    monkeypatch,
):
    from tests.unit.test_native_account_restart_recovery_0056_v1 import operation

    fixture = operation.__wrapped__(monkeypatch)
    monkeypatch.setattr(
        restart.selectors, "_selection_database_profile_0056", lambda _: profiles.DB0057
    )
    result = await restart._recover_selected(fixture.selection, reconcile_only=False)
    assert result.database_head == "0057"
    assert fixture.stage.await_args.kwargs == {"profile": profiles.DB0057}
    assert fixture.observation.await_args.kwargs == {"profile": profiles.DB0057}
    fixture.tx.commit.assert_awaited_once()
