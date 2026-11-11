"""Offline verification of the native D/V capture dependency readback."""

from __future__ import annotations

import ssl
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.mie.validation import prospective_devval_capture_dependencies_v1 as subject

pytestmark = pytest.mark.asyncio

_SERVER_STARTED = datetime(2026, 11, 12, tzinfo=UTC)
_WINDOWS_BUILD = (
    "PostgreSQL 17.11 on x86_64-windows, compiled by msvc-19.44.35207, 64-bit"
)
_BEFORE_GUARD = [
    "session-enter",
    "transaction-enter",
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
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

    async def execute(self, statement, parameters=None):
        assert self.events[:2] == _BEFORE_GUARD[:2]
        assert "transaction-exit" not in self.events
        if statement is subject._IDENTITY:
            self.events.append("identity")
            self.relation = parameters["relation"]
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
        login="witness",
        login_oid=101,
        *,
        database="evidence",
        tls_mode="verified",
        override=None,
        **facts,
    ):
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if tls_mode in {"without_hostname", "without_cert"}:
            tls.check_hostname = False
        if tls_mode == "without_cert":
            tls.verify_mode = ssl.CERT_NONE
        engine = create_async_engine(
            f"postgresql+asyncpg://{login}@fixture.invalid/{database}",
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
                "database_name": database,
                "login_name": login,
                "owner_oid": 90,
                "server_address": "192.0.2.10",
                "server_port": 5432,
                "server_started": _SERVER_STARTED,
                "server_version_num": "170011",
                "server_build": _WINDOWS_BUILD,
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

    relation = subject._SOURCE_RELATIONS[0]
    readback = await subject._read_role(factory, guard, relation)
    factory.assert_called_once_with()
    assert "pg_catalog.version() AS server_build" in str(subject._IDENTITY)
    assert session.relation == relation
    assert readback == subject._RoleReadback(
        subject._SourceScope(
            50,
            "evidence",
            "192.0.2.10",
            5432,
            _SERVER_STARTED,
            "170011",
            _WINDOWS_BUILD,
            90,
        ),
        101,
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
        await subject._read_role(factory, guard, subject._SOURCE_RELATIONS[0])
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
        {"owner_oid": 0},
        {"owner_oid": 101},
        {"server_version_num": "170012"},
        {
            "server_build": (
                "PostgreSQL 17.11 on x86_64-pc-linux-musl, "
                "compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit"
            )
        },
        {
            "server_build": _WINDOWS_BUILD.replace(
                "msvc-19.44.35207", "clang-19.44.35207"
            )
        },
        {"server_build": _WINDOWS_BUILD.encode()},
        {"server_started": _SERVER_STARTED.astimezone(timezone(timedelta(hours=8)))},
    ],
)
async def test_rejects_untrusted_database_facts_before_role_guard(role_factory, facts):
    factory, session = role_factory(**facts)
    guard = AsyncMock()
    with pytest.raises(
        subject.DevvalCaptureDependencyError,
        match="^devval_capture_role_readback_invalid$",
    ):
        await subject._read_role(factory, guard, subject._SOURCE_RELATIONS[0])
    factory.assert_called_once_with()
    guard.assert_not_awaited()
    assert session.events == [*_BEFORE_GUARD, "transaction-exit", "session-exit"]


@pytest.mark.parametrize(
    ("observer_facts", "custody_facts", "accepted"),
    [
        ({}, {}, True),
        ({"database_oid": 51}, {}, False),
        (
            {"server_started": _SERVER_STARTED + timedelta(seconds=1)},
            {},
            False,
        ),
        ({"login_oid": 101}, {}, False),
        ({}, {"login_oid": 101}, False),
        ({}, {"login_oid": 102}, False),
        ({}, {"database_oid": 60}, False),
        ({}, {"database": "other_evidence"}, False),
        ({}, {"server_address": "192.0.2.11"}, False),
        ({}, {"server_port": 5433}, False),
        ({}, {"server_started": _SERVER_STARTED + timedelta(seconds=1)}, False),
        ({}, {"server_build": _WINDOWS_BUILD.replace("35207", "35208")}, False),
        ({}, {"owner_oid": 91}, False),
        ({"owner_oid": 91}, {}, False),
    ],
)
async def test_requires_shared_source_database_and_owner_with_distinct_roles(
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
    assert (
        witness_session.relation,
        observation_session.relation,
        custody_session.relation,
    ) == (
        "public.public_receipt_witness_revisions",
        "public.public_receipt_post_read_observations",
        "public.public_runtime_raw_bundles_v1",
    )
