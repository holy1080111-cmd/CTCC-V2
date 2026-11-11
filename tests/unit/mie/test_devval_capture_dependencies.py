"""Offline verification of the native D/V capture dependency readback."""

from __future__ import annotations

import ssl
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.mie.validation import prospective_devval_capture_dependencies_v1 as subject

pytestmark = pytest.mark.asyncio

_SERVER_STARTED = datetime(2026, 11, 12, tzinfo=UTC)
_BEFORE_GUARD = [
    "session-enter",
    "transaction-enter",
    "SET TRANSACTION READ ONLY",
    "SET LOCAL search_path = pg_catalog, pg_temp",
    "SET LOCAL synchronous_commit = on",
    "SET LOCAL statement_timeout = '8s'",
    "SET LOCAL lock_timeout = '2s'",
    "identity",
]


class _Session:
    def __init__(self, row):
        self.row = row
        self.events = []

    async def __aenter__(self):
        self.events.append("session-enter")
        return self

    async def __aexit__(self, *_):
        self.events.append("session-exit")

    @asynccontextmanager
    async def begin(self):
        assert self.events == ["session-enter"]
        self.events.append("transaction-enter")
        try:
            yield
        finally:
            self.events.append("transaction-exit")

    async def execute(self, statement):
        assert self.events[:2] == _BEFORE_GUARD[:2]
        assert "transaction-exit" not in self.events
        if statement is subject._IDENTITY:
            self.events.append("identity")
            return SimpleNamespace(
                mappings=lambda: SimpleNamespace(one=lambda: self.row)
            )
        self.events.append(str(statement))
        return None


@pytest.fixture
def role_factory(monkeypatch):
    def refuse_connect(*_args, **_kwargs):
        pytest.fail("test must never open a PostgreSQL connection")

    monkeypatch.setattr(asyncpg, "connect", refuse_connect)

    def make(
        login="witness", login_oid=101, *, tls_mode="verified", override=None, **facts
    ):
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if tls_mode in {"without_hostname", "without_cert"}:
            tls.check_hostname = False
        if tls_mode == "without_cert":
            tls.verify_mode = ssl.CERT_NONE
        engine = create_async_engine(
            f"postgresql+asyncpg://{login}@fixture.invalid/evidence",
            connect_args={
                "ssl": tls if tls_mode != "absent" else None,
                **(override or {}),
            },
            poolclass=NullPool,
        )
        session = _Session(
            {
                "database_oid": 50,
                "login_oid": login_oid,
                "database_name": "evidence",
                "login_name": login,
                "server_address": "192.0.2.10",
                "server_port": 5432,
                "server_started": _SERVER_STARTED,
                "direct_login": True,
                "read_only": True,
                "durable_primary": True,
                "tls": True,
                **facts,
            }
        )
        factory = Mock(return_value=session)
        factory.kw = {"bind": engine}
        return factory, session

    return make


async def test_reads_native_tls_role_and_guard_in_one_read_only_transaction(
    role_factory,
):
    factory, session = role_factory()

    async def guard(actual):
        assert actual is session
        assert session.events == _BEFORE_GUARD
        session.events.append("guard")

    readback = await subject._read_role(factory, guard)
    factory.assert_called_once_with()
    assert readback == subject._RoleReadback(
        ("192.0.2.10", 5432, _SERVER_STARTED, 50), 101
    )
    assert session.events == [
        *_BEFORE_GUARD,
        "guard",
        "transaction-exit",
        "session-exit",
    ]


@pytest.mark.parametrize(
    ("tls_mode", "override"),
    [
        ("absent", None),
        ("without_hostname", None),
        ("without_cert", None),
        ("verified", {"host": "another.invalid"}),
        ("verified", {"user": "other_login"}),
    ],
)
async def test_rejects_unverified_or_retargeted_tls_before_opening_session(
    role_factory, tls_mode, override
):
    factory, session = role_factory(tls_mode=tls_mode, override=override)
    guard = AsyncMock()
    with pytest.raises(
        subject.DevvalCaptureDependencyError, match="^devval_capture_tls_required$"
    ):
        await subject._read_role(factory, guard)
    factory.assert_not_called()
    guard.assert_not_awaited()
    assert session.events == []


@pytest.mark.parametrize(
    "facts",
    [
        {"tls": False},
        {"direct_login": False},
        {"login_name": "other_login"},
        {"database_name": "other_database"},
        {"read_only": False},
        {"durable_primary": False},
    ],
)
async def test_rejects_untrusted_database_facts_before_role_guard(role_factory, facts):
    factory, session = role_factory(**facts)
    guard = AsyncMock()
    with pytest.raises(
        subject.DevvalCaptureDependencyError,
        match="^devval_capture_role_readback_invalid$",
    ):
        await subject._read_role(factory, guard)
    factory.assert_called_once_with()
    guard.assert_not_awaited()
    assert session.events == [*_BEFORE_GUARD, "transaction-exit", "session-exit"]


@pytest.mark.parametrize(
    ("observer_facts", "custody_facts", "accepted"),
    [
        ({}, {"database_oid": 60}, True),
        ({"database_oid": 51}, {"database_oid": 60}, False),
        (
            {"server_started": _SERVER_STARTED + timedelta(seconds=1)},
            {"database_oid": 60},
            False,
        ),
        ({"login_oid": 101}, {"database_oid": 60}, False),
        ({}, {"login_oid": 101}, False),
        ({}, {"login_oid": 101, "database_oid": 60}, False),
    ],
)
async def test_requires_same_actual_witness_database_and_distinct_roles(
    monkeypatch, role_factory, observer_facts, custody_facts, accepted
):
    witness_factory, witness_session = role_factory()
    observation_factory, observation_session = role_factory(
        "observer", **({"login_oid": 102} | observer_facts)
    )
    custody_factory, custody_session = role_factory(
        "custody", **({"login_oid": 103} | custody_facts)
    )
    witness = SimpleNamespace(_role_guard=AsyncMock())
    observation = SimpleNamespace(_role_guard=AsyncMock())
    custody = SimpleNamespace(_catalog=AsyncMock(), _check_factory=Mock())
    pins = SimpleNamespace(factories=(witness_factory, observation_factory))
    source_pins = Mock(return_value=pins)
    repository = Mock(return_value=custody)
    monkeypatch.setattr(subject, "_source_database_pins", source_pins)
    monkeypatch.setattr(subject, "RuntimeBundleCustodyRepository", repository)

    if accepted:
        await subject.verify_devval_capture_dependencies(
            witness, observation, custody_factory
        )
        assert source_pins.call_count == 2
        source_pins.assert_called_with(witness, observation, pins)
        custody._check_factory.assert_called_once_with()
    else:
        with pytest.raises(
            subject.DevvalCaptureDependencyError,
            match="^devval_capture_role_topology_changed$",
        ):
            await subject.verify_devval_capture_dependencies(
                witness, observation, custody_factory
            )
        source_pins.assert_called_once_with(witness, observation)
        custody._check_factory.assert_not_called()
    repository.assert_called_once_with(custody_factory)
    witness._role_guard.assert_awaited_once_with(witness_session)
    observation._role_guard.assert_awaited_once_with(observation_session)
    custody._catalog.assert_awaited_once_with(custody_session)
