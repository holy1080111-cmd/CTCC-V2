"""Synthetic private-custody catalog contracts; no candidate or order authority."""

import ssl
from dataclasses import fields
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import native_role_preflight_0056_full_diagnostic_v1 as role_data
from app.core import native_role_witness_0056_v1 as witness_data
from app.database.repositories.native_issuer_transaction import (
    NativeIssuerCatalogIdentity,
)
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)
from app.domain import native_database_profile_v1 as profiles
from app.trade_qualification import native_g12_publication_v2 as publisher


@pytest.fixture
async def synthetic_sources():
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    engines = tuple(
        create_async_engine(
            f"postgresql+asyncpg://{name}@localhost:5432/isolated",
            connect_args={"ssl": tls},
        )
        for name in ("writer", "custody", "frame")
    )
    factories = tuple(
        async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        for engine in engines
    )
    selected = ConfiguredNativeDemoFactoriesV2(*factories, engines[1], engines[2])

    def issued(head):
        values = {
            f.name: None
            for f in fields(publisher.candidate.account.IssuedAccountForG11V1)
            if f.init
        }
        values.update(
            session_factory=factories[0], database_head=head, writer_role_oid=55
        )
        if head in {"0056", "0057", "0058"}:
            profile = profiles.from_head(head)
            observation = role_data.RestrictedNativeRoles0056ObservationV1(
                "a" * 64,
                "b" * 64,
                "c" * 64,
                "d" * 64,
                schema=profiles.schema(
                    profile, "ctcc.native_only.restricted0056_role_observation.v1"
                ),
                database_head=head,
            )
            values.update(
                database_factories=selected,
                database_roles=witness_data.RestrictedNativeRoles0056WitnessV1(
                    observation,
                    "e" * 64,
                    "f" * 64,
                    schema=profiles.schema(
                        profile, "ctcc.native_only.restricted0056_runtime_witness.v1"
                    ),
                ),
            )
        return publisher.candidate.account.IssuedAccountForG11V1(**values)

    try:
        async with factories[0]() as session, session.begin():
            yield issued, session
    finally:
        for engine in engines:
            await engine.dispose()


@pytest.mark.parametrize("head", ["0055", "0056", "0057", "0058"])
async def test_private_catalog_dispatch_uses_replayed_issued_head_role_and_exact_acl(
    monkeypatch, synthetic_sources, head
):
    make_issued, session = synthetic_sources
    issued = make_issued(head)
    state = {}
    # Only the private owned-custody handoff is synthetic in this unit test.
    # No public entry or saved receipt is made eligible by these DATA records.
    monkeypatch.setattr(
        publisher, "_custody_context", lambda *_args, **_kwargs: (state, issued)
    )
    legacy = AsyncMock(
        return_value={
            "database_head": "0055",
            "role_oid": 55,
            "native_baseline_admission": True,
            "execution_authority": False,
            "catalog_sha256": "c" * 64,
        }
    )
    current = AsyncMock(
        return_value=(
            NativeIssuerCatalogIdentity(
                head, 55, "c" * 64, "a" * 64 if head in {"0057", "0058"} else None
            ),
            (),
        )
    )
    acl = AsyncMock()
    connection = object()
    monkeypatch.setattr(
        publisher._custody_catalog, "verify_actual_issuer_catalog_v2", legacy
    )
    monkeypatch.setattr(
        publisher._issuer_transaction, "read_actual_native_issuer_transaction", current
    )
    monkeypatch.setattr(publisher._database_profiles, "require_exact_acl", acl)
    monkeypatch.setattr(AsyncSession, "connection", AsyncMock(return_value=connection))
    await publisher._verify_custody_writer_catalog_v2(
        object(), object(), session, key=object()
    )
    assert state["custody_catalog_pin"] == (head, 55, "c" * 64)
    if head == "0055":
        legacy.assert_awaited_once_with(session)
        current.assert_not_awaited()
        acl.assert_not_awaited()
    else:
        legacy.assert_not_awaited()
        current.assert_awaited_once_with(session)
        acl.assert_awaited_once_with(
            connection,
            ("writer", "custody", "frame"),
            55,
            profile=profiles.from_head(head),
            read_only=False,
        )


@pytest.mark.parametrize(
    "fault", ["wrong_head", "wrong_role", "changed_catalog", "changed_reference"]
)
async def test_private_catalog_denies_head_role_schema_or_original_reference_drift(
    monkeypatch, synthetic_sources, fault
):
    make_issued, session = synthetic_sources
    issued = make_issued("0057")
    state = {"custody_catalog_pin": ("0057", 55, "c" * 64)}
    monkeypatch.setattr(
        publisher, "_custody_context", lambda *_args, **_kwargs: (state, issued)
    )
    value = NativeIssuerCatalogIdentity(
        "0056" if fault == "wrong_head" else "0057",
        56 if fault == "wrong_role" else 55,
        "d" * 64 if fault == "changed_catalog" else "c" * 64,
        "b" * 64 if fault == "changed_reference" else "a" * 64,
    )
    monkeypatch.setattr(
        publisher._issuer_transaction,
        "read_actual_native_issuer_transaction",
        AsyncMock(return_value=(value, ())),
    )
    monkeypatch.setattr(publisher._database_profiles, "require_exact_acl", AsyncMock())
    monkeypatch.setattr(AsyncSession, "connection", AsyncMock(return_value=object()))
    with pytest.raises(
        publisher.NativeG12Error, match="native_g12_issued_writer_catalog_changed"
    ):
        await publisher._verify_custody_writer_catalog_v2(
            object(), object(), session, key=object()
        )
    assert state["custody_catalog_pin"] == ("0057", 55, "c" * 64)


async def test_constructed_issued_data_cannot_enter_custody_without_live_binding(
    synthetic_sources,
):
    make_issued, session = synthetic_sources
    issued = make_issued("0057")
    assert issued.execution_authority is False
    repository = publisher.custody.G12InvocationCustodyRepository(
        issued.session_factory
    )
    with pytest.raises(
        publisher.NativeG12Error, match="native_g12_custody_owner_required"
    ):
        await publisher._verify_custody_writer_catalog_v2(
            repository, object(), session, key=publisher.custody._NATIVE_V2_KEY
        )


@pytest.mark.parametrize("head, role", [("0058", 55), ("0057", True), ("0057", 0)])
async def test_unknown_or_untyped_issued_metadata_is_not_a_profile(
    synthetic_sources, head, role
):
    make_issued, _ = synthetic_sources
    value = make_issued("0057")
    object.__setattr__(value, "database_head", head)
    object.__setattr__(value, "writer_role_oid", role)
    with pytest.raises(publisher.NativeG12Error):
        publisher._issued_writer_pin(value)


def _transcript_state(head):
    timestamp = datetime(2026, 11, 20, tzinfo=UTC)
    sessions = []
    for index, stage in enumerate(publisher._CUSTODY_STAGES):
        metadata = (
            "writer",
            "writer",
            "none",
            100 + index,
            str(1000 + index),
            timestamp,
            "off",
            "read committed",
            "pg_catalog, public, pg_temp",
            "UTC",
            "on",
        )
        sessions.append((stage, object(), metadata, metadata))
    return {
        "custody_sessions": sessions,
        "custody_pending_session": None,
        "custody_issued_writer_pin": (head, 55),
        "custody_catalog_pin": (head, 55, "c" * 64),
    }


def test_successor_transcript_binds_physical_head_role_and_actual_catalog():
    state = _transcript_state("0057")
    before = publisher._custody_transcript_sha256(state)
    state["custody_catalog_pin"] = ("0057", 55, "d" * 64)
    assert publisher._custody_transcript_sha256(state) != before
    state["custody_issued_writer_pin"] = ("0057", 56)
    state["custody_catalog_pin"] = ("0057", 56, "c" * 64)
    assert publisher._custody_transcript_sha256(state) != before


def test_physical58_transcript_cannot_reuse_physical57_metadata():
    before = publisher._custody_transcript_sha256(_transcript_state("0057"))
    current = publisher._custody_transcript_sha256(_transcript_state("0058"))
    assert before != current


@pytest.mark.parametrize(
    "fault", ["missing_catalog", "wrong_role", "duplicate_xid", "missing_stage"]
)
def test_transcript_refuses_incomplete_or_changed_custody(fault):
    state = _transcript_state("0057")
    if fault == "missing_catalog":
        state["custody_catalog_pin"] = ("0057", 55)
    elif fault == "wrong_role":
        state["custody_catalog_pin"] = ("0057", 56, "c" * 64)
    elif fault == "duplicate_xid":
        state["custody_sessions"][1] = (
            state["custody_sessions"][1][0],
            object(),
            state["custody_sessions"][0][2],
            state["custody_sessions"][0][3],
        )
    else:
        state["custody_sessions"].pop()
    with pytest.raises(publisher.NativeG12Error):
        publisher._custody_transcript_sha256(state)
