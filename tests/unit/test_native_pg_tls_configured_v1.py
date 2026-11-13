"""Optional native PostgreSQL TLS wiring, without opening a database."""

from __future__ import annotations

import hashlib
import ssl
from pathlib import Path
from types import SimpleNamespace

import certifi
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import Settings
from app.database import session as db_session
from app.database.native_pg_tls_v1 import (
    NativePgTlsError,
    configured_native_pg_tls_context_v1,
)
from app.database.repositories.public_runtime_bundle_custody import _closure
from app.demo_automation import native_server_selectors_v2 as selectors

_WRITER = "postgresql+asyncpg://writer:private@postgres:5432/ctcc"
_CUSTODY = "postgresql+asyncpg://custody:private@postgres:5432/ctcc"
_FRAME = "postgresql+asyncpg://frame:private@postgres:5432/ctcc"


@pytest.fixture
def reviewed_ca(tmp_path):
    raw = Path(certifi.where()).read_bytes()
    path = tmp_path / "reviewed-ca.pem"
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    "file,sha",
    (("/reviewed/ca.pem", ""), ("", "a" * 64), ("/reviewed/ca.pem", "BAD")),
)
def test_optional_ca_settings_reject_partial_or_malformed_without_private_input(
    file, sha
):
    private_value = "fake_private_dsn_password_987654"
    with pytest.raises(ValidationError) as caught:
        Settings(
            _env_file=None,
            database_url=f"postgresql+asyncpg://writer:{private_value}@postgres/ctcc",
            ctcc_native_pg_tls_ca_file=file,
            ctcc_native_pg_tls_ca_sha256=sha,
        )
    assert "native_pg_tls_configuration_denied" in str(caught.value)
    assert private_value not in str(caught.value)


def test_absent_ca_preserves_legacy_no_tls_and_valid_pair_builds_context(reviewed_ca):
    path, digest = reviewed_ca
    assert (
        configured_native_pg_tls_context_v1(
            ca_file="", ca_sha256="", database_url=_WRITER
        )
        is None
    )
    context = configured_native_pg_tls_context_v1(
        ca_file=str(path), ca_sha256=digest, database_url=_WRITER
    )
    assert type(context) is ssl.SSLContext
    assert context.check_hostname is True and context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize(
    "url",
    (
        "postgresql+asyncpg://writer:private@postgres/ctcc?ssl=disable",
        "postgresql+asyncpg://writer:private@one,two/ctcc",
        "postgresql+asyncpg://writer:private@/ctcc",
        "postgresql+psycopg://writer:private@postgres/ctcc",
    ),
)
def test_tls_route_refuses_query_or_ambiguous_host_before_ca_read(
    reviewed_ca, url, monkeypatch
):
    from app.database import native_pg_tls_v1 as tls_module

    path, digest = reviewed_ca
    monkeypatch.setattr(
        tls_module,
        "verified_native_pg_tls_context_v1",
        lambda *_: pytest.fail("unsafe URL reached CA acquisition"),
    )
    with pytest.raises(NativePgTlsError, match="^native_pg_tls_unavailable$"):
        configured_native_pg_tls_context_v1(
            ca_file=str(path), ca_sha256=digest, database_url=url
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("tls_enabled", (False, True))
async def test_writer_and_restricted_factories_share_exact_context_when_enabled(
    reviewed_ca, monkeypatch, tls_enabled
):
    path, digest = reviewed_ca
    context = (
        configured_native_pg_tls_context_v1(
            ca_file=str(path), ca_sha256=digest, database_url=_WRITER
        )
        if tls_enabled
        else None
    )
    writer = create_async_engine(
        _WRITER,
        poolclass=NullPool,
        connect_args={"ssl": context} if context is not None else {},
    )
    factory = async_sessionmaker(writer, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db_session, "engine", writer)
    monkeypatch.setattr(db_session, "AsyncSessionFactory", factory)
    monkeypatch.setattr(db_session, "native_pg_tls_context", context)
    monkeypatch.setattr(
        selectors,
        "get_settings",
        lambda: SimpleNamespace(
            okx_demo_native_custody_database_url=SecretStr(_CUSTODY),
            okx_demo_native_frame_reader_database_url=SecretStr(_FRAME),
        ),
    )
    selected = None
    try:
        selected = selectors.select_configured_native_demo_factories_v2()
        engines = (writer, selected.custody_engine, selected.frame_reader_engine)
        observed = tuple(
            _closure(engine.sync_engine.pool._creator)["cparams"].get("ssl")
            for engine in engines
        )
        assert observed == (context, context, context)
        if tls_enabled:
            assert all(value is context for value in observed)
    finally:
        if selected is not None:
            await selected.custody_engine.dispose()
            await selected.frame_reader_engine.dispose()
        await writer.dispose()


@pytest.mark.asyncio
async def test_restricted_selector_refuses_different_writer_context(
    reviewed_ca, monkeypatch
):
    path, digest = reviewed_ca
    writer_context = configured_native_pg_tls_context_v1(
        ca_file=str(path), ca_sha256=digest, database_url=_WRITER
    )
    different_context = configured_native_pg_tls_context_v1(
        ca_file=str(path), ca_sha256=digest, database_url=_WRITER
    )
    writer = create_async_engine(
        _WRITER, poolclass=NullPool, connect_args={"ssl": writer_context}
    )
    monkeypatch.setattr(db_session, "engine", writer)
    monkeypatch.setattr(
        db_session,
        "AsyncSessionFactory",
        async_sessionmaker(writer, autoflush=False, expire_on_commit=False),
    )
    monkeypatch.setattr(db_session, "native_pg_tls_context", different_context)
    monkeypatch.setattr(
        selectors,
        "get_settings",
        lambda: SimpleNamespace(
            okx_demo_native_custody_database_url=SecretStr(_CUSTODY),
            okx_demo_native_frame_reader_database_url=SecretStr(_FRAME),
        ),
    )
    try:
        with pytest.raises(
            selectors.NativeDemoCallerReadinessErrorV2,
            match="^native_demo_restricted_tls_context_invalid$",
        ):
            selectors.select_configured_native_demo_factories_v2()
    finally:
        await writer.dispose()


@pytest.mark.asyncio
async def test_restricted_tls_route_rejects_query_before_engine_creation(
    reviewed_ca, monkeypatch
):
    path, digest = reviewed_ca
    context = configured_native_pg_tls_context_v1(
        ca_file=str(path), ca_sha256=digest, database_url=_WRITER
    )
    writer = create_async_engine(
        _WRITER, poolclass=NullPool, connect_args={"ssl": context}
    )
    monkeypatch.setattr(db_session, "engine", writer)
    monkeypatch.setattr(
        db_session,
        "AsyncSessionFactory",
        async_sessionmaker(writer, autoflush=False, expire_on_commit=False),
    )
    monkeypatch.setattr(db_session, "native_pg_tls_context", context)
    monkeypatch.setattr(
        selectors,
        "get_settings",
        lambda: SimpleNamespace(
            okx_demo_native_custody_database_url=SecretStr(_CUSTODY + "?ssl=disable"),
            okx_demo_native_frame_reader_database_url=SecretStr(_FRAME),
        ),
    )
    monkeypatch.setattr(
        selectors,
        "create_async_engine",
        lambda *_args, **_kwargs: pytest.fail("unsafe route created an engine"),
    )
    try:
        with pytest.raises(
            selectors.NativeDemoCallerReadinessErrorV2,
            match="^native_demo_restricted_factory_selection_failed$",
        ):
            selectors.select_configured_native_demo_factories_v2()
    finally:
        await writer.dispose()
