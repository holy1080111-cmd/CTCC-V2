"""Offline checks for the restricted DB0056 same-transaction Control read."""

import ssl
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import native_locked_demo_control_0056_v1 as subject
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)
from app.trade_qualification.demo_control import ControlScope, stopped_genesis

_START = datetime(2026, 11, 13, tzinfo=UTC)
_SCOPE = ControlScope("demo", "000001")
_NAMES = ("demo_writer", "demo_custody", "demo_frame")
_ACTOR = (*(_NAMES[0],) * 2, "none", 11, 21, _START, 170011, "127.0.0.1", 5432)
_IDENTITY = (
    ["0056"],
    *_ACTOR[:3],
    "test_db",
    *_ACTOR[3:5],
    42,
    _START,
    _START + timedelta(seconds=1),
    "off",
    True,
)
_CATALOG = subject.writer56.NativeWriter0056ObservationV1("a" * 64, "b" * 64)
_WITNESS = subject.runtime_witness_0056(
    _ACTOR, _NAMES, (("postgresql+asyncpg", "127.0.0.1", 5432, "test_db"),) * 3
)


@pytest.fixture
async def scenario(monkeypatch, request):
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    connect_args = {"ssl": tls}
    if getattr(request, "param", None) == "route-override":
        connect_args["host"] = "different.example"
    engines = tuple(
        create_async_engine(
            f"postgresql+asyncpg://{name}@127.0.0.1:5432/test_db",
            poolclass=NullPool,
            connect_args=connect_args,
        )
        for name in _NAMES
    )
    factories = tuple(
        async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        for engine in engines
    )
    selected = ConfiguredNativeDemoFactoriesV2(*factories, *engines[1:])
    events = []
    state = SimpleNamespace(
        selected=selected,
        events=events,
        factories=factories,
        tls=tls,
        snapshot=stopped_genesis(_SCOPE, _START),
        identity=_IDENTITY,
        observed_at=_START + timedelta(seconds=2),
        fault=None,
    )

    async def execute(session, statement):
        assert session is state.session and session.in_transaction()
        assert str(statement).startswith("SET LOCAL ")
        events.append("setup")

    async def identity(session, engine):
        assert session.bind is engine is engines[0]
        first = "identity" not in events
        events.append("identity")
        if not first and state.fault == "server-drift":
            return (*state.identity[:7], 99, *state.identity[8:])
        return state.identity

    async def actor(session, name):
        assert session is state.session and name == _NAMES[0]
        events.append("actor")
        return _ACTOR

    async def catalog(session):
        assert session is state.session
        events.append("catalog")
        return _CATALOG

    async def connection(session):
        assert session is state.session
        return state

    async def acl(held, names, oid):
        assert held is state and names == _NAMES and oid == _ACTOR[3]
        events.append("acl")
        if state.fault == "acl":
            raise ValueError("private role name")

    async def fitted(session, names):
        assert session is state.session and names == _NAMES[1:]
        events.append("fitted")

    async def lock(session, scope):
        assert session is state.session and scope == _SCOPE
        events.append("lock")
        return state

    async def snapshot(session, row):
        assert session is state.session and row is state
        events.append("snapshot")
        if state.fault == "replacement":
            await session.rollback()
            await session.begin()
        if state.fault == "lineage":
            raise ValueError("private event bytes")
        return state.snapshot, "c" * 64

    async def scalar(session, statement):
        assert session is state.session and statement is subject._CLOCK
        events.append("clock")
        return state.observed_at

    monkeypatch.setattr(AsyncSession, "execute", execute)
    monkeypatch.setattr(AsyncSession, "scalar", scalar)
    monkeypatch.setattr(AsyncSession, "connection", connection)
    monkeypatch.setattr(subject.writer56, "_session_identity", identity)
    monkeypatch.setattr(
        subject.writer56, "observe_restricted_readwrite_writer_0056_v1", catalog
    )
    monkeypatch.setattr(subject.roles56.current55, "_identity", actor)
    monkeypatch.setattr(subject, "_exact_existing_acl", acl)
    monkeypatch.setattr(
        subject.roles56, "_require_no_effective_demo_fitted_execute", fitted
    )
    monkeypatch.setattr(subject.DemoControlRepository, "_locked", lock)
    monkeypatch.setattr(subject.DemoControlRepositoryV2, "_snapshot", snapshot)

    try:
        async with factories[0]() as state.session:
            await state.session.begin()
            yield state
    finally:
        for engine in engines:
            await engine.dispose()


async def test_exact_writer_keeps_the_account_locked_and_returns_no_authority(scenario):
    transaction = scenario.session.sync_session.get_transaction()
    result = await subject.read_locked_demo_control_0056_v1(
        scenario.selected, scenario.session, _SCOPE
    )
    assert result.control.state == scenario.snapshot
    assert result.control.event_sha256 == "c" * 64
    assert result.control.observed_at == scenario.observed_at
    assert result.writer_catalog == _CATALOG
    assert (result.server_incarnation_sha256, result.role_route_sha256) == _WITNESS
    assert result.issuance_authority is result.execution_authority is False
    assert result.control.execution_authority is False
    assert type(result).__repr__ is object.__repr__
    assert scenario.session.sync_session.get_transaction() is transaction
    assert scenario.events == ["setup"] * 5 + [
        "identity",
        "actor",
        "catalog",
        "acl",
        "fitted",
        "setup",
        "lock",
        "snapshot",
        "clock",
        "actor",
        "identity",
    ]


@pytest.mark.parametrize(
    "fault",
    ["no-transaction", "wrong-engine", "no-hostname", "dirty-session"],
)
async def test_refuses_unbound_session_before_queries(scenario, fault):
    session = scenario.session
    if fault == "no-transaction":
        await session.rollback()
    elif fault == "wrong-engine":
        scenario.selected = ConfiguredNativeDemoFactoriesV2(
            scenario.factories[1],
            scenario.factories[0],
            scenario.factories[2],
            scenario.factories[0].kw["bind"],
            scenario.factories[2].kw["bind"],
        )
    elif fault == "no-hostname":
        scenario.tls.check_hostname = False
    else:
        session.add(subject.DemoAccountControl())
    with pytest.raises(subject.NativeLockedDemoControl0056Error):
        await subject.read_locked_demo_control_0056_v1(
            scenario.selected, session, _SCOPE
        )
    assert not scenario.events


@pytest.mark.parametrize("scenario", ["route-override"], indirect=True)
async def test_refuses_common_connect_address_override(scenario):
    with pytest.raises(subject.NativeLockedDemoControl0056Error):
        await subject.read_locked_demo_control_0056_v1(
            scenario.selected, scenario.session, _SCOPE
        )
    assert not scenario.events


@pytest.mark.parametrize(
    "fault",
    ["acl", "lineage", "replacement", "server-drift", "wrong-uid", "future-clock"],
)
async def test_refuses_changed_control_or_transaction_with_fixed_error(scenario, fault):
    scenario.fault = fault
    if fault == "wrong-uid":
        scenario.snapshot = stopped_genesis(ControlScope("demo", "1"), _START)
    elif fault == "future-clock":
        scenario.observed_at = _START + timedelta(microseconds=1)
    with pytest.raises(
        subject.NativeLockedDemoControl0056Error,
        match="^native_locked_demo_control_0056_unavailable$",
    ):
        await subject.read_locked_demo_control_0056_v1(
            scenario.selected, scenario.session, _SCOPE
        )
    if fault == "acl":
        assert "lock" not in scenario.events
    elif fault in ("lineage", "replacement", "wrong-uid"):
        assert "clock" not in scenario.events
