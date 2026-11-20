"""Real held restricted DB0056 close fences; no venue calls or source permit."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.native_role_witness_0056_v1 import (
    observe_restricted_native_roles_0056_witness_v1,
)
from app.database.models.demo_automation import DemoAutomationState
from app.database.repositories import native_legacy_control_genesis_0056_v1 as genesis
from app.database.repositories import native_postsubmit_v1 as repository
from app.database.repositories.demo_control import DemoControlRepository
from app.trade_qualification import native_controlled_close_v1 as close
from app.trade_qualification.demo_control import ControlScope, account_lock_key
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _open_actual_0056_role_observation_lab,
)
from tests.unit.native_function_primitive_fixture_v1 import foreign_primitive_v1

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_SOURCE_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def _negative_close0056_lab():
    async with _open_actual_0056_role_observation_lab(grant_demo=True) as lab:
        yield lab


async def test_actual0056_negative_close_holds_control_catalog_and_estop_through_io(
    monkeypatch, _negative_close0056_lab
):
    owner_engine, owner_factory, selected, _ = _negative_close0056_lab
    roles = await observe_restricted_native_roles_0056_witness_v1(
        selected, source=_SOURCE_ROOT
    )
    scope = LedgerScope(
        environment="demo", account_id="730060", settlement_currency="USDT"
    )
    control_scope = ControlScope(scope.environment, scope.account_id)
    stopped = await DemoControlRepository(
        owner_factory, clock=lambda: datetime.now(UTC)
    ).latch_stop(control_scope, command_id="a" * 64)
    original = genesis._stopped_values(datetime.now(UTC), "b" * 64)
    async with owner_factory.begin() as session:
        session.add(DemoAutomationState(**original))

    state, events = {"transport_lock": {}}, []
    binding = object()
    source = SimpleNamespace(scope=scope, session_factory=selected.writer_factory)
    view = SimpleNamespace(binding=binding, source_context=source)
    original_session = None

    def require_physical_state(actual, *, session, key):
        assert actual is state and session is original_session
        assert session.in_transaction() and session in repository._HELD_POSTSUBMIT_0056
        events.append("held_native_session")

    # Extract before replacing synthetic registry seams. The clone cannot
    # satisfy the actual native source guard or send an exchange request.
    shared_fence = foreign_primitive_v1(
        close._lock_shared_owned_native_close_control_v1,
        {"_require_owned_native_close_transport_lock_v1": require_physical_state},
    )
    monkeypatch.setattr(repository, "_close_source", lambda *args, **kwargs: source)
    monkeypatch.setattr(repository, "_source", lambda actual: ({}, source))
    monkeypatch.setattr(
        repository, "_database_profile", lambda actual: (selected, roles)
    )

    async with selected.writer_factory() as session, session.begin():
        original_session = session
        async with repository._held_owned_close_database_v1(
            view, session=session, key=close._DB_KEY_V1
        ):
            actual, event = await repository._read_owned_close_control_v1(
                view, session=session, key=close._DB_KEY_V1
            )
            assert actual == stopped.state and event == stopped.event_sha256
            assert actual.emergency_stop and actual.arm_request_id is None
            await shared_fence(state, session=session, key=object())
            observed = state["transport_lock"]["shared_negative_control"]
            assert observed[2] is False and observed[3] is observed[4] is True
            assert observed[5] is True
            async with owner_engine.begin() as competing:
                assert (
                    await competing.scalar(
                        text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"),
                        {"key": account_lock_key(control_scope)},
                    )
                    is False
                )
                with pytest.raises(DBAPIError) as blocked:
                    await competing.exec_driver_sql(
                        "LOCK TABLE public.demo_automation_state IN ROW EXCLUSIVE MODE NOWAIT"
                    )
                assert blocked.value.orig.sqlstate == "55P03"
            # Stand-in I/O only. The real marker/locks persist across the same
            # await where Root would perform its one controlled physical POST.
            await asyncio.sleep(0)
            assert repository._HELD_POSTSUBMIT_0056[session][0] is binding
            assert state["transport_lock"]["shared_negative_control"] == observed
            meta = tuple((await session.execute(repository._META)).one())
        assert session not in repository._HELD_POSTSUBMIT_0056

    async with selected.writer_factory() as reopened, reopened.begin():
        with pytest.raises(repository.NativePostsubmitRepositoryError):
            await repository._read_owned_close_control_v1(
                view, session=reopened, key=close._DB_KEY_V1
            )
    async with owner_factory() as session:
        singleton = await session.get(DemoAutomationState, 1)
        assert singleton.armed is False
        assert singleton.emergency_stop is singleton.locked is True
        assert singleton.control_revision == original["control_revision"]
        assert singleton.lock_reasons == original["lock_reasons"]
    assert len(events) == 3 and meta[8].isdecimal()
