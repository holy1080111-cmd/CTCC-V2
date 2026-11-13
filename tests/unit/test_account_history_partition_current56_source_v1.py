"""Readback boundaries for the DB0056 restricted history partition."""

import ssl
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import (
    account_history_partition_current56_source_v1 as subject,
)
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)

_NOW = datetime(2026, 11, 14, tzinfo=UTC)
_NAMES = ("demo_writer", "demo_custody", "demo_frame")
_ROUTE = ("postgresql+asyncpg", "127.0.0.1", 5432, "test_db")
_ACTOR = (_NAMES[0], _NAMES[0], "none", 11, 21, _NOW, 170011, "127.0.0.1", 5432)
_IDENTITY = (["0056"], *_ACTOR[:3], "test_db", 11, 21, 42, _NOW, _NOW, "off", True)
_CATALOG = subject.writer56.NativeWriter0056ObservationV1("a" * 64, "b" * 64)
_WITNESS = subject.RestrictedNativeRoles0056WitnessV1(
    subject.roles56.RestrictedNativeRoles0056ObservationV1(
        "a" * 64,
        "c" * 64,
        "d" * 64,
        subject.roles56._granted_inherited_acl_shape_sha256(_NAMES, "a" * 64),
    ),
    *subject.runtime_witness_0056(_ACTOR, _NAMES, (_ROUTE,) * 3),
)
_SCOPE = subject.HistoryPartitionScopeV1("demo", "000001", "USDT")


@pytest.fixture
async def scenario(monkeypatch):
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    engines = tuple(
        create_async_engine(
            f"postgresql+asyncpg://{name}@127.0.0.1:5432/test_db",
            poolclass=NullPool,
            connect_args={"ssl": tls},
        )
        for name in _NAMES
    )
    factories = tuple(
        async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        for engine in engines
    )
    selected = ConfiguredNativeDemoFactoriesV2(*factories, *engines[1:])
    state = SimpleNamespace(failure=None, events=[], session=None, tx=None)

    def same(session):
        assert session.bind is engines[0]
        if state.session is None:
            state.session = session
            state.tx = session.sync_session.get_transaction()
        assert session is state.session
        assert session.sync_session.get_transaction() is state.tx

    async def execute(session, statement):
        same(session)
        if statement is subject._DB_TX:
            state.events.append("physical")
            return SimpleNamespace(
                one=lambda: (42, "77" if state.failure == "rpc-transaction" else "99")
            )
        assert str(statement) in subject._SETTINGS
        state.events.append("settings")

    async def scalar(session, statement, params):
        same(session)
        assert statement is subject._READ
        assert params == {
            "environment": "demo",
            "account_id": "000001",
            "currency": "USDT",
        }
        state.events.append("rpc")
        if state.failure == "transaction-replaced":
            await session.rollback()
            await session.begin()
        return b"private original source"

    async def connection(session):
        same(session)
        return state

    async def identity(session, engine):
        same(session)
        assert engine is engines[0]
        return _IDENTITY

    async def actor(session, name):
        same(session)
        assert name == _NAMES[0]
        return _ACTOR

    async def catalog(session):
        same(session)
        state.events.append("catalog")
        if state.failure == "post-rpc-catalog-changed" and "rpc" in state.events:
            return subject.writer56.NativeWriter0056ObservationV1("a" * 64, "e" * 64)
        return _CATALOG

    async def acl(con, names, oid):
        assert con is state and names == _NAMES and oid == 11
        if state.failure == "acl":
            raise ValueError("private DSN")
        state.events.append("acl")

    async def fitted(session, names):
        same(session)
        assert names == _NAMES[1:]

    async def partition_catalog(session):
        same(session)
        return {
            "role": {"oid": 11},
            "server": {"database": "test_db"},
            "acl_rows": [
                {
                    "kind": "function",
                    "object": "account_history_partition_v1_read",
                    "grantee_oid": 99 if state.failure == "wrong-reader-grant" else 11,
                    "owner_oid": 22,
                    "privilege": "EXECUTE",
                }
            ],
            "functions": [
                {"name": "account_history_partition_v1_read", "can_execute": True},
                {
                    "name": "account_history_partition_v1_append",
                    "can_execute": state.failure == "legacy-append",
                },
            ],
        }

    def readback(raw, scope, checked_at):
        assert (
            raw == b"private original source" and scope == _SCOPE and checked_at == _NOW
        )
        return subject.original.HistoryPartitionReadbackV1(
            scope, (), (), (b"first", b"second"), 2, "f" * 64, 1, 0, 0, _NOW, 42, "99"
        )

    monkeypatch.setattr(AsyncSession, "execute", execute)
    monkeypatch.setattr(AsyncSession, "scalar", scalar)
    monkeypatch.setattr(AsyncSession, "connection", connection)
    monkeypatch.setattr(subject.writer56, "_session_identity", identity)
    monkeypatch.setattr(
        subject.writer56, "observe_restricted_readwrite_writer_0056_v1", catalog
    )
    monkeypatch.setattr(subject.roles56.current55, "_identity", actor)
    monkeypatch.setattr(subject.roles56.current55, "_exact_existing_acl", acl)
    monkeypatch.setattr(
        subject.roles56, "_require_no_effective_demo_fitted_execute", fitted
    )
    monkeypatch.setattr(
        subject.original, "read_account_history_partition_catalog", partition_catalog
    )
    monkeypatch.setattr(
        subject.original, "verify_account_history_partition_catalog", lambda _: None
    )
    monkeypatch.setattr(subject, "_verify_current55_runtime_role_v2", lambda _: None)
    monkeypatch.setattr(subject.original, "_readback", readback)
    try:
        yield selected, state
    finally:
        for engine in engines:
            await engine.dispose()


async def test_read_revalidates_same_actual_writer_around_inherited_procedure(scenario):
    selected, state = scenario
    reader = subject.Db0056SourcePartitionReaderV1(
        selected, clock=lambda: _NOW, expected_roles=_WITNESS
    )
    result = await reader.read(_SCOPE)
    assert result.head_sequence == 2
    assert result.b3_documents == (b"first", b"second")
    assert result.account_complete is result.execution_authority is False
    assert not state.session.in_transaction()
    assert not hasattr(reader, "append_member_and_checkpoint")
    assert state.events == ["settings"] * len(subject._SETTINGS) + [
        "catalog",
        "acl",
        "rpc",
        "physical",
        "catalog",
        "acl",
    ]


@pytest.mark.parametrize(
    ("failure", "rpc_started"),
    [
        ("acl", False),
        ("legacy-append", False),
        ("wrong-reader-grant", False),
        ("rpc-transaction", True),
        ("transaction-replaced", True),
        ("post-rpc-catalog-changed", True),
    ],
)
async def test_refuses_catalog_grant_or_transaction_drift(
    scenario, failure, rpc_started
):
    selected, state = scenario
    state.failure = failure
    reader = subject.Db0056SourcePartitionReaderV1(
        selected, clock=lambda: _NOW, expected_roles=_WITNESS
    )
    with pytest.raises(
        subject.Db0056SourcePartitionError,
        match="^db0056_source_partition_unavailable$",
    ):
        await reader.read(_SCOPE)
    assert ("rpc" in state.events) is rpc_started


async def test_refuses_other_incarnation_before_partition_rpc(scenario):
    selected, state = scenario
    witness = subject.RestrictedNativeRoles0056WitnessV1(
        _WITNESS.observation, "f" * 64, _WITNESS.role_route_sha256
    )
    reader = subject.Db0056SourcePartitionReaderV1(
        selected, clock=lambda: _NOW, expected_roles=witness
    )
    with pytest.raises(subject.Db0056SourcePartitionError):
        await reader.read(_SCOPE)
    assert "rpc" not in state.events
