"""Pure transaction and actor checks for the DB0056 writer observer."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core import native_writer_observation_0056_v1 as writer
from app.domain import collector_current_0056_inherited_catalog_v1 as inherited
from app.domain.source_primitives import canonical, sha

_START = datetime(2026, 11, 13, tzinfo=UTC)
_IDENTITY = (
    ["0056"],
    "demo_writer",
    "demo_writer",
    "none",
    "test_db",
    201,
    93,
    302,
    _START,
    _START + timedelta(seconds=1),
    "off",
    True,
)


class _Result:
    def __init__(self, value):
        self.value = value

    def one(self):
        return self.value

    def scalar_one(self):
        return self.value

    def all(self):
        return self.value


@pytest.fixture
def scenario(monkeypatch):
    graph = {
        "role": {"name": "demo_writer"},
        "issued": {"server": {"database": "test_db"}},
    }
    observed = {
        "schema": inherited.OBSERVATION_SCHEMA,
        "database_head": "0056",
        "role_oid": 201,
        "native_definitions_sha256": "a" * 64,
        "native_baseline_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }
    state = SimpleNamespace(
        first=list(_IDENTITY),
        last=list(_IDENTITY),
        graph=graph,
        observed=observed,
        fitted=[
            ("demo_writer", name, False)
            for name in writer.diagnostic._FITTED_FUNCTION_NAMES
        ],
        events=[],
        transaction_changed=False,
        internal_error=None,
    )

    def domain_observer(value):
        assert value is graph
        state.events.append("graph-validation")
        if state.internal_error is not None:
            raise state.internal_error
        return observed

    async def execute(session, statement, parameters=None):
        if statement is writer._SESSION_SQL:
            if not state.events:
                state.events.append("identity-first")
                return _Result(state.first)
            state.events.append("identity-last")
            return _Result(state.last)
        if statement is writer._ACTOR_CATALOG_SQL:
            state.events.append("graph-query")
            if state.transaction_changed:
                await session.rollback()
                await session.begin()
            return _Result(graph)
        assert statement is writer.diagnostic._FITTED_DEMO_ACCESS
        assert parameters == {"roles": ["demo_writer"]}
        state.events.append("effective-execute")
        return _Result(state.fitted)

    monkeypatch.setattr(inherited, "observe_readwrite_writer_0056", domain_observer)
    monkeypatch.setattr(AsyncSession, "execute", execute)
    return state


@pytest.fixture
async def session():
    engine = create_async_engine("postgresql+asyncpg://demo_writer@127.0.0.1/test_db")
    try:
        async with AsyncSession(engine) as active:
            await active.begin()
            yield active
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_restricted_writer_observes_exact_live_session_without_authority(
    scenario, session
):
    assert asdict(
        await writer.observe_restricted_readwrite_writer_0056_v1(session)
    ) == {
        "native_definitions_sha256": "a" * 64,
        "actor_catalog_sha256": sha(canonical(scenario.graph)),
        "schema": "ctcc.current0056.restricted_readwrite_writer_observation.v1",
        "database_head": "0056",
        "native_baseline_admission": False,
        "startup_admission": False,
        "account_complete": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    assert scenario.events == [
        "identity-first",
        "graph-query",
        "graph-validation",
        "effective-execute",
        "identity-last",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "head55",
        "mixed_heads",
        "read_only",
        "no_tls",
        "role_switch",
        "owner_login",
        "db_mismatch",
        "changed_pid",
        "transaction_changed",
        "role_oid",
        "graph_database",
        "fake_admission",
        "fitted_execute",
        "fitted_missing",
        "fitted_duplicate",
        "redact",
    ],
)
async def test_restricted_writer_refuses_drift_in_real_query_contract(
    scenario, session, fault
):
    if fault == "head55":
        scenario.first[0] = ["0055"]
    elif fault == "mixed_heads":
        scenario.first[0] = ["0056", "0055"]
    elif fault == "read_only":
        scenario.first[10] = "on"
    elif fault == "no_tls":
        scenario.first[11] = False
    elif fault == "role_switch":
        scenario.first[3] = "demo_writer"
    elif fault == "owner_login":
        scenario.first[1:3] = ["owner", "owner"]
    elif fault == "db_mismatch":
        scenario.first[4] = "other_db"
    elif fault == "changed_pid":
        scenario.last[7] += 1
    elif fault == "transaction_changed":
        scenario.transaction_changed = True
    elif fault == "role_oid":
        scenario.observed["role_oid"] = 101
    elif fault == "graph_database":
        scenario.graph["issued"]["server"]["database"] = "other_db"
    elif fault == "fake_admission":
        scenario.observed["native_baseline_admission"] = True
    elif fault == "fitted_execute":
        role, routine, _ = scenario.fitted[0]
        scenario.fitted[0] = (role, routine, True)
    elif fault == "fitted_missing":
        scenario.fitted.pop()
    elif fault == "fitted_duplicate":
        scenario.fitted[0] = scenario.fitted[-1]
    else:
        scenario.internal_error = ValueError("private synthetic data")
    with pytest.raises(
        writer.NativeWriter0056ObservationError,
        match="^native_writer_0056_observation_unavailable$",
    ):
        await writer.observe_restricted_readwrite_writer_0056_v1(session)


@pytest.mark.asyncio
async def test_wrapper_will_not_open_a_transaction_or_accept_unbound_object(
    scenario, session
):
    await session.rollback()
    for value in (session, SimpleNamespace()):
        with pytest.raises(writer.NativeWriter0056ObservationError):
            await writer.observe_restricted_readwrite_writer_0056_v1(value)
    assert not scenario.events


def test_rw_inherited_observer_never_projects_onto_read_only_or_owner(monkeypatch):
    checked = []

    def observe(value, *, owner_mode, read_only):
        checked.append((value, owner_mode, read_only))
        return value

    monkeypatch.setattr(inherited, "_observe_inherited_graph", observe)
    assert inherited.observe_readwrite_writer_0056("rw") == "rw"
    assert inherited.observe_writer_0056("ro") == "ro"
    assert inherited.observe_owner_installation_0056("owner") == "owner"
    assert checked == [("rw", False, "off"), ("ro", False, "on"), ("owner", True, "on")]
