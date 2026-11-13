"""The native issuer checks the actual head and holds one transaction."""

import ssl
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.database.repositories import native_issuer_transaction as issuer
from app.domain.source_primitives import canonical, sha

_NOW = datetime(2026, 11, 14, tzinfo=UTC)
_METADATA = ("writer", "writer", "none", 55, False, False, False, 96, "11", _NOW)
_IDENTITY = (
    ["0056"],
    "writer",
    "writer",
    "none",
    "isolated",
    55,
    43,
    96,
    _NOW - timedelta(seconds=2),
    _NOW - timedelta(seconds=1),
    "off",
    True,
)


@pytest.fixture
async def session():
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    engine = create_async_engine(
        "postgresql+asyncpg://writer@localhost:5432/isolated",
        connect_args={"ssl": tls},
    )
    try:
        async with AsyncSession(engine) as active, active.begin():
            yield active
    finally:
        await engine.dispose()


@pytest.fixture
def database(monkeypatch):
    head = AsyncMock(return_value=SimpleNamespace(all=lambda: ["0056"]))
    identity = AsyncMock(return_value=_IDENTITY)
    role = issuer.writer56.NativeWriter0056ObservationV1("a" * 64, "b" * 64)
    writer = AsyncMock(return_value=role)
    legacy = AsyncMock(
        return_value={
            "database_head": "0055",
            "native_baseline_admission": True,
            "role_oid": 55,
            "catalog_sha256": "c" * 64,
        }
    )
    metadata = AsyncMock(return_value=_METADATA)
    monkeypatch.setattr(AsyncSession, "scalars", head)
    monkeypatch.setattr(issuer.writer56, "_session_identity", identity)
    monkeypatch.setattr(
        issuer.writer56, "observe_restricted_readwrite_writer_0056_v1", writer
    )
    monkeypatch.setattr(issuer.catalog55, "verify_actual_issuer_catalog_v2", legacy)
    monkeypatch.setattr(issuer.lineage, "_metadata", metadata)
    return SimpleNamespace(
        head=head, identity=identity, writer=writer, legacy=legacy, metadata=metadata
    )


async def test_db56_proves_actual_writer_and_transaction_coordinates(session, database):
    catalog, metadata = await issuer.read_actual_native_issuer_transaction(session)
    assert catalog == issuer.NativeIssuerCatalogIdentity(
        "0056", 55, sha(canonical(["a" * 64, "b" * 64]))
    )
    assert metadata == _METADATA
    assert database.identity.await_count == 2
    database.writer.assert_awaited_once_with(session)
    database.legacy.assert_not_awaited()


async def test_db55_retains_original_strict_writer_verifier(session, database):
    database.head.return_value = SimpleNamespace(all=lambda: ["0055"])
    catalog, metadata = await issuer.read_actual_native_issuer_transaction(session)
    assert catalog == issuer.NativeIssuerCatalogIdentity("0055", 55, "c" * 64)
    assert metadata == _METADATA
    database.legacy.assert_awaited_once_with(session)
    database.writer.assert_not_awaited()


@pytest.mark.parametrize(
    "fault", ["empty_head", "mixed_heads", "changed_backend", "role_oid"]
)
async def test_wrong_head_or_transaction_identity_refuses(session, database, fault):
    if fault == "empty_head":
        database.head.return_value = SimpleNamespace(all=list)
    elif fault == "mixed_heads":
        database.head.return_value = SimpleNamespace(all=lambda: ["0055", "0056"])
    elif fault == "changed_backend":
        database.identity.side_effect = [
            _IDENTITY,
            (*_IDENTITY[:7], 97, *_IDENTITY[8:]),
        ]
    else:
        database.metadata.return_value = (*_METADATA[:3], 56, *_METADATA[4:])
    with pytest.raises(
        issuer.NativeIssuerTransactionError, match="^native_issuer_database_refused$"
    ):
        await issuer.read_actual_native_issuer_transaction(session)
    database.legacy.assert_not_awaited()


async def test_replacing_the_transaction_inside_catalog_read_refuses(session, database):
    async def replace_transaction(_):
        await session.rollback()
        return issuer.writer56.NativeWriter0056ObservationV1("a" * 64, "b" * 64)

    database.writer.side_effect = replace_transaction
    with pytest.raises(issuer.NativeIssuerTransactionError):
        await issuer.read_actual_native_issuer_transaction(session)
    database.metadata.assert_not_awaited()
