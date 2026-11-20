"""Synthetic report/cold-ledger dispatch; no real database or order authority."""

from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import native_ledger_preflight_0056_v1 as ledger
from app.database.repositories import native_closed_reporting_0056_v1 as report
from app.domain import native_database_profile_v1 as profiles
from app.trade_qualification import demo_control as control
from app.trade_qualification.reservations import LedgerScope
from tests.native_role_profile_fixtures_v1 import restricted_roles_fixture
from tests.unit.test_native_startup_control_observation_0056_v1 import (
    scenario,  # noqa: F401
)


def identity(profile):
    roles = restricted_roles_fixture(profile)
    return report.NativeRoleIdentity0056V2(
        91,
        92,
        ("writer", "custody", "frame"),
        (1, 2, 3),
        "8" * 64,
        roles.server_incarnation_sha256,
        roles.role_route_sha256,
        roles.observation.inherited_native_definitions_sha256,
        roles.observation.granted_inherited_acl_shape_sha256,
        schema=profiles.schema(
            profile, "ctcc.native_only.restricted_role_identity0056.v2"
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("head", ["0056", "0057", None, "0058", "duplicate"])
async def test_readout_probe_uses_actual_bounded_row_and_always_rolls_back(
    scenario,  # noqa: F811
    monkeypatch,
    head,
):
    sql = []
    sessions = []

    async def execute(session, statement):
        assert session.in_transaction()
        sessions.append(session)
        sql.append(str(statement))
        return SimpleNamespace(
            all=lambda: [("0056",), ("0057",)] if head == "duplicate" else [(head,)]
        )

    monkeypatch.setattr(AsyncSession, "execute", execute)
    if head in ("0056", "0057"):
        profile = await report._profile_from_selected_head(scenario.selected)
        assert profiles.head(profile) == head
    else:
        with pytest.raises(report.NativeTerminalReport0056Error):
            await report._profile_from_selected_head(scenario.selected)
    assert sql[-1] == "SELECT version_num FROM public.alembic_version LIMIT 2"
    assert "READ ONLY" in sql[0]
    assert all(not value.in_transaction() for value in sessions)


@pytest.mark.asyncio
async def test_readout_foreign_factory_bind_refuses_before_sql(scenario, monkeypatch):  # noqa: F811
    call = AsyncMock()
    monkeypatch.setattr(AsyncSession, "execute", call)
    monkeypatch.setattr(
        AsyncSession,
        "get_bind",
        lambda *_, **__: scenario.selected.custody_engine.sync_engine,
    )
    with pytest.raises(report.NativeTerminalReport0056Error):
        await report._profile_from_selected_head(scenario.selected)
    call.assert_not_awaited()


@pytest.mark.asyncio
async def test_expected_successor_requires_real_successor_role_replay(monkeypatch):
    probe = AsyncMock(return_value=restricted_roles_fixture())
    monkeypatch.setattr(
        report.witness56, "observe_restricted_native_roles_0056_witness_v1", probe
    )
    with pytest.raises(report.NativeTerminalReport0056Error):
        await report._require_matching_roles(object(), identity(profiles.DB0057))
    assert probe.await_args.kwargs["profile"] is profiles.DB0057
    probe.reset_mock()
    with pytest.raises(report.NativeTerminalReport0056Error):
        await report._require_matching_roles(
            object(), replace(identity(profiles.DB0057), schema="0057")
        )
    probe.assert_not_awaited()


def test_default_report_wire_remains_exact_previous_head():
    value = report.NativeTerminalLineage0056V1(*("1" * 64 for _ in range(6)))
    data = asdict(value)
    assert data["schema"] == "ctcc.db0056.native_terminal_lineage_observation.v1"
    assert data["database_head"] == "0056"
    assert data["execution_authority"] is data["delivery_authority"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [profiles.DB0056, profiles.DB0057])
@pytest.mark.parametrize("wrong_head", [False, True])
async def test_cold_ledger_replays_explicit_profile_without_inventing_completeness(
    scenario,  # noqa: F811
    monkeypatch,
    profile,
    wrong_head,
):
    at = datetime(2026, 11, 20, tzinfo=UTC)
    scope = LedgerScope(account_id="730074", settlement_currency="USDT")
    state = control.stopped_genesis(control.ControlScope("demo", scope.account_id), at)
    roles = restricted_roles_fixture(profile)

    def catalog(selected):
        return ledger.locked.writer56.NativeWriter0056ObservationV1(
            roles.observation.inherited_native_definitions_sha256,
            "7" * 64,
            schema=profiles.schema(
                selected, "ctcc.current0056.restricted_readwrite_writer_observation.v1"
            ),
            database_head=profiles.head(selected),
        )

    valid = catalog(profile)
    repeated = (
        catalog(profiles.DB0057 if profile is profiles.DB0056 else profiles.DB0056)
        if wrong_head
        else valid
    )

    @asynccontextmanager
    async def verified(selected, session, actual_scope, **kwargs):
        assert selected is scenario.selected and actual_scope == state.scope
        assert kwargs == profiles.arguments(profile)
        yield (
            object(),
            valid,
            (roles.server_incarnation_sha256, roles.role_route_sha256),
            at,
            lambda: None,
        )

    monkeypatch.setattr(ledger.locked, "_verified_locked_demo_control_0056", verified)
    monkeypatch.setattr(
        ledger.locked.DemoControlRepositoryV2,
        "_snapshot",
        AsyncMock(return_value=(state, "8" * 64)),
    )
    monkeypatch.setattr(
        ledger.locked.DemoControlRepositoryV2, "verify_historical_binding", AsyncMock()
    )
    monkeypatch.setattr(
        ledger.ledger.QualificationLedgerRepository,
        "_locked",
        AsyncMock(
            return_value=SimpleNamespace(
                updated_at=at, account_revision=0, ledger_revision=0
            )
        ),
    )
    monkeypatch.setattr(
        ledger.locked.writer56,
        "observe_restricted_readwrite_writer_0056_v1",
        AsyncMock(return_value=repeated),
    )
    monkeypatch.setattr(ledger.ledger, "_require_event_journal_session", AsyncMock())
    monkeypatch.setattr(
        ledger.ledger, "_read_event_journal_schema_rows", AsyncMock(return_value=())
    )
    monkeypatch.setattr(
        ledger.ledger, "_check_event_journal_schema_rows", lambda *_: None
    )
    monkeypatch.setattr(
        ledger, "_replay_historical_scope", AsyncMock(return_value="BOOTSTRAP")
    )
    checkpoint = AsyncMock(return_value=(0, 0, "9" * 64))
    monkeypatch.setattr(ledger, "_uid_ledger_checkpoint", checkpoint)
    monkeypatch.setattr(AsyncSession, "execute", AsyncMock())
    monkeypatch.setattr(
        AsyncSession, "scalar", AsyncMock(return_value=at + timedelta(seconds=1))
    )
    async with scenario.selected.writer_factory() as session, session.begin():
        if wrong_head:
            with pytest.raises(ledger.NativeLedgerPreflight0056Error):
                await ledger.read_locked_native_ledger_preflight_0056_v1(
                    scenario.selected, roles, session, scope
                )
            checkpoint.assert_not_awaited()
        else:
            result = await ledger.read_locked_native_ledger_preflight_0056_v1(
                scenario.selected, roles, session, scope
            )
            assert result.database_head == profiles.head(profile)
            assert result.scope_state == "BOOTSTRAP"
            assert (
                result.account_complete
                is result.execution_authority
                is result.reconciliation_verified
                is False
            )
            assert checkpoint.await_args.kwargs == profiles.arguments(profile)
