"""Offline only. Use real transaction objects with mocked database responses."""

import asyncio
import ssl
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core import native_account_control_observation_0056_v1 as account56
from app.core import native_startup_control_observation_0056_v1 as subject
from app.database.models.demo_control import DemoAccountControl
from app.trade_qualification.demo_control import stopped_genesis

_START = datetime(2026, 1, 1, tzinfo=UTC)


class _Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def one_or_none(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def one(self):
        return self.value

    def all(self):
        return self.value


@pytest.fixture
async def scenario(monkeypatch):
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    names = ("ctcc_writer", "ctcc_custody", "ctcc_frame")
    engines = tuple(
        create_async_engine(
            f"postgresql+asyncpg://{name}@127.0.0.1:5432/synthetic",
            poolclass=NullPool,
            connect_args={"ssl": tls},
        )
        for name in names
    )
    selected = subject.ConfiguredNativeDemoFactoriesV2(
        *(
            async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
            for engine in engines
        ),
        engines[1],
        engines[2],
    )
    state = SimpleNamespace(
        selected=selected,
        tls=tls,
        writer=engines[0],
        sql=[],
        sessions=[],
        acl_checked=False,
        fail_acl=False,
        head=[("0056", True)],
        actor=[names[0], names[0], "none", 201, 93, _START, 170011, "127.0.0.1", 5432],
        snapshot=[
            32,
            _START + timedelta(seconds=1),
            "synthetic",
            True,
            True,
            True,
            True,
            True,
        ],
        control={
            "control_revision": 7,
            "armed": True,
            "emergency_stop": False,
            "locked": False,
            "restart_latch_required": True,
            "lock_reasons": [],
            "active_trades": {"private-synthetic": {}},
            "symbol_cooldowns": {},
            "realized_pnl_events": [],
        },
        soak=None,
        drift=False,
        transaction_changed=False,
        error=None,
        fitted=[
            (name, routine, False)
            for name in names
            for routine in subject.roles56._FITTED_FUNCTION_NAMES
        ],
    )

    async def execute(session, statement, parameters=None):
        assert session.bind is state.writer
        state.sessions.append(session)
        state.sql.append(str(statement))
        if statement is subject.roles56.current55._IDENTITY_SQL:
            return _Result(state.actor)
        if statement is subject.roles56._HEAD:
            return _Result(state.head)
        if statement is subject._SNAPSHOT_SQL:
            row = state.snapshot.copy()
            if state.drift and str(subject._RUNNING_SOAK_SQL) in state.sql:
                row[0] += 1
            return _Result(row)
        if statement is subject.roles56._FITTED_DEMO_ACCESS:
            assert parameters == {"roles": list(names)}
            return _Result(state.fitted)
        if statement is subject._CONTROL_SQL:
            if state.error is not None:
                raise state.error
            if state.transaction_changed:
                await session.rollback()
                await session.begin()
            return _Result(state.control)
        if statement is subject._RUNNING_SOAK_SQL:
            return _Result(state.soak)
        assert str(statement) in subject._STARTUP_SETTINGS
        return _Result(None)

    async def connection(session):
        assert session.bind is state.writer
        return session

    async def exact_acl(session, roles, oid):
        assert session.bind is state.writer and oid == 201
        assert roles == names
        if state.fail_acl:
            raise ValueError("private invalid ACL")
        state.acl_checked = True

    monkeypatch.setattr(AsyncSession, "execute", execute)
    monkeypatch.setattr(AsyncSession, "connection", connection)
    monkeypatch.setattr(subject, "_exact_existing_acl", exact_acl)
    try:
        yield state
    finally:
        for engine in engines:
            await engine.dispose()


@pytest.mark.asyncio
async def test_observes_control_and_soak_once_in_same_restricted_snapshot(scenario):
    result = await subject.observe_startup_control_0056_v1(scenario.selected)
    assert result.control == subject.ExistingControl(7, True, False, False, True)
    assert result.database_head == "0056"
    assert result.startup_admission is result.execution_authority is False
    assert "private-synthetic" not in repr(result)
    assert len({id(session) for session in scenario.sessions}) == 1
    assert scenario.acl_checked is True
    assert (
        scenario.sql[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    )
    assert scenario.sql[-5:] == [
        str(statement)
        for statement in (
            subject._CONTROL_SQL,
            subject._RUNNING_SOAK_SQL,
            subject.roles56.current55._IDENTITY_SQL,
            subject.roles56._HEAD,
            subject._SNAPSHOT_SQL,
        )
    ]


@pytest.mark.asyncio
async def test_control_runtime_witness_hashes_full_original_row(scenario):
    first = await subject.observe_startup_control_0056_witness_v1(scenario.selected)
    assert first.observation.control.revision == 7
    assert first.startup_admission is first.execution_authority is False
    assert subject.roles56._is_digest(first.control_snapshot_sha256)
    assert subject.roles56._is_digest(first.server_incarnation_sha256)
    assert subject.roles56._is_digest(first.role_route_sha256)
    scenario.control["active_trades"]["private-synthetic"]["new"] = 1
    changed = await subject.observe_startup_control_0056_witness_v1(scenario.selected)
    assert first.observation == changed.observation
    assert first.control_snapshot_sha256 != changed.control_snapshot_sha256
    assert "private-synthetic" not in repr(first)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "wrong-head",
        "duplicate-head",
        "no-tls",
        "no-peer-check",
        "wrong-role",
        "changed-role",
        "acl",
        "fitted",
        "rw",
        "wrong-isolation",
        "rls-active",
        "replica",
        "missing-control",
        "running-soak",
        "readback-drift",
        "transaction-drift",
        "error",
    ],
)
async def test_denies_wrong_scope_unsafe_state_and_changed_snapshot(scenario, fault):
    if fault == "wrong-head":
        scenario.head[:] = [("0055", True)]
    elif fault == "duplicate-head":
        scenario.head.append(("0056", True))
    elif fault == "no-tls":
        scenario.head[:] = [("0056", False)]
    elif fault == "no-peer-check":
        scenario.tls.check_hostname = False
    elif fault == "wrong-role":
        scenario.actor[:2] = ["ctcc_owner", "ctcc_owner"]
    elif fault == "changed-role":
        scenario.actor[2] = "ctcc_writer"
    elif fault == "acl":
        scenario.fail_acl = True
    elif fault == "fitted":
        name, routine, _ = scenario.fitted[0]
        scenario.fitted[0] = (name, routine, True)
    elif fault == "rw":
        scenario.snapshot[4] = False
    elif fault == "wrong-isolation":
        scenario.snapshot[3] = False
    elif fault == "rls-active":
        scenario.snapshot[6] = False
    elif fault == "replica":
        scenario.snapshot[7] = False
    elif fault == "missing-control":
        scenario.control = None
    elif fault == "running-soak":
        scenario.soak = 1
    elif fault == "readback-drift":
        scenario.drift = True
    elif fault == "transaction-drift":
        scenario.transaction_changed = True
    else:
        scenario.error = RuntimeError("private fixture detail")
    with pytest.raises(
        subject.NativeStartupControl0056Error,
        match="^native_startup_control_0056_unavailable$",
    ):
        await subject.observe_startup_control_0056_v1(scenario.selected)


@pytest.mark.asyncio
async def test_cancellation_does_not_produce_observation(scenario):
    scenario.error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await subject.observe_startup_control_0056_v1(scenario.selected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", [None, "different-scope", "clock-regressed", "replay-denied"]
)
async def test_account_control_uses_exact_scope_and_replays_inside_same_snapshot(
    scenario, monkeypatch, fault
):
    scope = account56.ControlScope("demo", "000123")
    seen = []
    row = DemoAccountControl()
    state = stopped_genesis(scope, _START + timedelta(seconds=2))

    async def get(session, model, key):
        assert model is DemoAccountControl
        assert key == ("demo", "000123")
        seen.append(session)
        return row

    async def replay(session, actual):
        assert actual is row
        seen.append(session)
        if fault == "replay-denied":
            raise ValueError("private journal payload")
        if fault == "different-scope":
            return replace(
                state, scope=account56.ControlScope("demo", "000124")
            ), "a" * 64
        return state, "a" * 64

    async def clock(session, statement):
        assert statement is account56._CLOCK_SQL
        seen.append(session)
        return _START if fault == "clock-regressed" else _START + timedelta(seconds=3)

    monkeypatch.setattr(AsyncSession, "get", get)
    monkeypatch.setattr(AsyncSession, "scalar", clock)
    monkeypatch.setattr(account56.DemoControlRepositoryV2, "_snapshot", replay)

    if fault is None:
        result = await account56.observe_account_control_0056_v1(
            scenario.selected, scope
        )
        assert result.control.state == state
        assert result.control.event_sha256 == "a" * 64
        assert result.control.observed_at == _START + timedelta(seconds=3)
        assert result.execution_authority is result.startup_admission is False
        assert result.control.execution_authority is False
    else:
        with pytest.raises(
            subject.NativeStartupControl0056Error,
            match="^native_startup_control_0056_unavailable$",
        ):
            await account56.observe_account_control_0056_v1(scenario.selected, scope)
    assert len(seen) >= 2
    assert all(session is scenario.sessions[0] for session in seen)
    assert not any("FOR UPDATE" in statement for statement in scenario.sql)
