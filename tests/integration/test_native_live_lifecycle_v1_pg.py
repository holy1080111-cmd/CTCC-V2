"""Actual migrated DB0058 HMAC/role/immutability acceptance, synthetic proof DATA.

The main migration must install every object. No test-local SPEC DDL, secret
file, live credentials, venue source, Arm or execution acceptance is claimed.
The published synthetic HMAC key exercises the real SQL verifier only.
"""

import asyncio
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import native_live_lifecycle_v1 as subject
from app.domain import native_live_lifecycle_schema_v1 as spec
from app.domain.native_live_intent_v1 import exact_live_intent_document_v1
from app.domain.source_primitives import canonical, decode, sha
from app.okx_live import lifecycle_key_v1 as keys
from tests.integration import test_native_live_ledger_v1_pg as fixture
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _isolated_tls_database_config,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_live58_lab = fixture._live58_lab


@pytest.fixture
async def lifecycle_lab(_live58_lab):
    owner, writer, value = _live58_lab
    await writer.reserve_and_commit_intent_data_v1(value, owned_source=object())
    _, tls = _isolated_tls_database_config()
    name = "ctcc_lifecycle_" + uuid4().hex[:12]
    engine, created = None, False
    material = bytes(range(32))
    try:
        async with owner.begin() as connection:
            assert (
                await connection.scalar(
                    text("SELECT version_num FROM public.alembic_version")
                )
                == "0058"
            )
            for table in spec.TABLES:
                assert (
                    await connection.scalar(
                        text("SELECT to_regclass(:name)::text"),
                        {"name": "public." + table},
                    )
                    is not None
                )
            await connection.exec_driver_sql(
                f'CREATE ROLE "{name}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
            )
            created = True
            await connection.exec_driver_sql(
                f'GRANT CONNECT ON DATABASE "{owner.url.database}" TO "{name}"'
            )
            for sql in spec.live_lifecycle_reconciler_grant_sql_v1(name):
                await connection.exec_driver_sql(sql)
            actor = await connection.scalar(
                text("SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=:name"),
                {"name": name},
            )
            await connection.execute(
                text(
                    "UPDATE public.native_live_controls_v1 SET armed=false,emergency_stop=true,arm_id=NULL,arm_expires_at=NULL"
                )
            )
            key = {
                "schema": keys.SCHEMA,
                "environment": "live",
                "account_uid": value.account_uid,
                "main_uid": value.main_uid,
                "database_oid": writer.pins.database_oid,
                "owner_oid": writer.pins.owner_oid,
                "reconciler_oid": actor,
                "origin": "https://openapi.okx.com",
                "key_id": "0" * 64,
                "key_hex": material.hex(),
            }
            key["key_id"] = keys.key_identity_v1(key)
            metadata = {
                name: entry
                for name, entry in key.items()
                if name not in ("schema", "key_hex")
            }
            metadata["key_material_sha256"] = sha(material)
            await connection.execute(
                text("""INSERT INTO public.native_live_lifecycle_keys_v1
 (environment,account_uid,main_uid,database_oid,owner_oid,reconciler_oid,origin,key_id,key_material,key_material_sha256)
 VALUES(:environment,:account_uid,:main_uid,:database_oid,:owner_oid,:reconciler_oid,:origin,:key_id,:material,:key_material_sha256)"""),
                {**metadata, "material": material},
            )
        engine = create_async_engine(
            owner.url.set(username=name),
            poolclass=NullPool,
            connect_args={"ssl": tls},
            hide_parameters=True,
        )
        factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        pins = subject.LiveLifecycleRolePinsV1(
            writer.pins.database_oid, writer.pins.owner_oid, actor, name
        )
        yield owner, writer, value, factory, pins, metadata, material
    finally:
        if engine is not None:
            await engine.dispose()
        if created:
            async with owner.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.exec_driver_sql(f'DROP OWNED BY "{name}"')
                await connection.exec_driver_sql(f'DROP ROLE "{name}"')


def _proof(lab, **updates):
    _, _, value, _, _, metadata, material = lab
    rid, intent_json, intent_sha = exact_live_intent_document_v1(value)
    intent_document = decode(intent_json, maximum=65536)
    result = {
        **{
            name: entry
            for name, entry in metadata.items()
            if name != "key_material_sha256"
        },
        "schema": spec.SCHEMA,
        "execution_authority": False,
        "reservation_id": rid,
        "sequence": 1,
        "previous_sha256": None,
        "intent_sha256": intent_sha,
        "event_key": value.event_key,
        "candidate_sha256": value.candidate_sha256,
        "report_sha256": value.report_sha256,
        "policy_sha256": value.policy_sha256,
        "original_credential_session_sha256": value.credential_session_sha256,
        "credential_session_sha256": value.credential_session_sha256,
        "credential_continuity": "same_original_lease",
        "request_sha256": intent_document["request_sha256"],
        "outcome": "UNCERTAIN",
        "observed_at": datetime.now(UTC).isoformat(),
        "fixture_purpose": "synthetic_pg_hmac_only",
    }
    result.update(updates)
    raw = canonical(result)
    return result, raw, hmac.digest(material, spec.DOMAIN + raw, hashlib.sha256).hex()


async def _append(lab, raw, signature):
    async with lab[3]() as session, session.begin():
        return await session.scalar(
            text("SELECT public.native_live_lifecycle_append_v1(:raw,:signature)"),
            {"raw": raw.decode("ascii"), "signature": signature},
        )


async def test_actual58_lifecycle_closed_reconciler_profile_hides_key_and_forbids_entry_writes(
    lifecycle_lab,
):
    lab = lifecycle_lab
    async with lab[3]() as session, session.begin():
        await subject._profile(session, lab[4])
        actual = await session.scalar(
            text("SELECT public.native_live_lifecycle_key_identity_v1(:uid)"),
            {"uid": lab[2].account_uid},
        )
        assert actual == lab[5]
    for sql in (
        "SELECT key_material FROM public.native_live_lifecycle_keys_v1",
        "UPDATE public.native_live_reservations_v1 SET state='reconciled'",
        "DELETE FROM public.native_live_lifecycle_proofs_v1",
        "INSERT INTO public.native_live_transitions_v1 DEFAULT VALUES",
    ):
        async with lab[3]() as session:
            with pytest.raises(DBAPIError):
                async with session.begin():
                    await session.execute(text(sql))
    # An added default preserves body/argtypes yet changes the callable
    # contract. The actual actor must refuse it before any proof operation.
    altered = spec.KEY_IDENTITY_SQL.replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    ).replace("(u text)", "(u text DEFAULT '" + lab[2].account_uid + "')", 1)
    async with lab[0].begin() as connection:
        await connection.exec_driver_sql(altered)
    async with lab[3]() as session, session.begin():
        with pytest.raises(subject.LiveLifecycleRepositoryError):
            await subject._profile(session, lab[4])


@pytest.mark.parametrize(
    "mutation",
    (
        "signature",
        "uid",
        "intent",
        "sequence",
        "previous",
        "future",
        "stale",
        "authority",
    ),
)
async def test_actual58_lifecycle_bad_signature_scope_or_chronology_never_resolves(
    lifecycle_lab, mutation
):
    lab = lifecycle_lab
    updates = {
        "uid": {"account_uid": "999999"},
        "intent": {"intent_sha256": "0" * 64},
        "sequence": {"sequence": 2},
        "previous": {"previous_sha256": "0" * 64},
        "future": {
            "observed_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        },
        "stale": {
            "observed_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        },
        "authority": {"execution_authority": True},
    }
    _, raw, signature = _proof(lab, **updates.get(mutation, {}))
    if mutation == "signature":
        signature = "0" * 64
    with pytest.raises(DBAPIError):
        await _append(lab, raw, signature)
    async with lab[0].connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT state FROM public.native_live_reservations_v1")
            )
            == "reserved"
        )
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.native_live_lifecycle_proofs_v1")
            )
            == 0
        )


async def test_actual58_lifecycle_uncertain_proof_is_durable_and_keeps_stop_and_hold(
    lifecycle_lab,
):
    lab = lifecycle_lab
    document, raw, signature = _proof(lab)
    assert await _append(lab, raw, signature) == sha(raw)
    assert await _append(lab, raw, signature) == sha(raw)
    async with lab[0].connect() as connection:
        assert (
            await connection.execute(
                text(
                    "SELECT state,resolution_sha256 FROM public.native_live_reservations_v1"
                )
            )
        ).one() == ("reserved", None)
        assert (
            await connection.execute(
                text(
                    "SELECT armed,emergency_stop,arm_id,arm_expires_at FROM public.native_live_controls_v1"
                )
            )
        ).one() == (False, True, None, None)
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.native_live_lifecycle_proofs_v1")
            )
            == 1
        )
    _, changed, changed_signature = _proof(lab, sequence=2, previous_sha256=sha(raw))
    assert await _append(lab, changed, changed_signature) == sha(changed)
    async with lab[0].begin() as connection:
        with pytest.raises(DBAPIError):
            async with connection.begin_nested():
                await connection.execute(
                    text(
                        "UPDATE public.native_live_lifecycle_proofs_v1 SET proof_json=:value"
                    ),
                    {"value": canonical(document).decode()},
                )


async def test_actual58_lifecycle_terminal_zero_fill_releases_only_authenticated_original_hold(
    lifecycle_lab,
):
    lab = lifecycle_lab
    _, raw, signature = _proof(
        lab,
        outcome="ZERO_FILL_FLAT",
        full_account_verified=True,
        fills_complete=True,
        flat_verified=True,
        unknown_exposure=False,
        raw_bank_sha256="1" * 64,
        submission_sha256="2" * 64,
        account_config_before_sha256="3" * 64,
        account_config_after_sha256="4" * 64,
        entry_order_id="123456",
        position_count=0,
        pending_order_count=0,
        pending_algo_count=0,
        submission_attempted=True,
        entry_filled_contracts="0",
        entry_order_state="canceled",
    )
    assert await _append(lab, raw, signature) == sha(raw)
    async with lab[0].connect() as connection:
        assert (
            await connection.execute(
                text(
                    "SELECT state,resolution_sha256 FROM public.native_live_reservations_v1"
                )
            )
        ).one() == ("reconciled", sha(raw))
    _, changed, changed_signature = _proof(lab, sequence=2, previous_sha256=sha(raw))
    with pytest.raises(DBAPIError):
        await _append(lab, changed, changed_signature)


async def test_actual58_live_recovery_journal_preserves_db_intent_recorded_time(
    lifecycle_lab,
    monkeypatch,
):
    """Real TLS/role/catalog/query, synthetic key handoff; no venue/Arm credit."""
    lab = lifecycle_lab
    repository = subject.NativeLiveLifecycleRepositoryV1(lab[3], lab[4])
    # Only the private key-file handoff is declared synthetic. The actual
    # role/catalog, original intent decoder and committed query stay intact.
    monkeypatch.setattr(repository, "_key", lambda owner: dict(lab[5]))
    rid, intent_json, intent_sha = exact_live_intent_document_v1(lab[2])
    first = await repository.read_journal_v1(object(), rid)
    second = await repository.read_journal_v1(object(), rid)
    async with lab[0].connect() as connection:
        recorded = await connection.scalar(
            text(
                "SELECT created_at FROM public.native_live_intents_v1 WHERE reservation_id=:id"
            ),
            {"id": rid},
        )
    assert type(recorded) is datetime and recorded.tzinfo is UTC
    assert first.intent_recorded_at == second.intent_recorded_at == recorded
    assert first.intent_json == second.intent_json == intent_json
    assert first.intent_sha256 == second.intent_sha256 == intent_sha
    assert first.reservation_state == second.reservation_state == "reserved"
    assert first.proofs == second.proofs == ()
    assert first.execution_authority is second.execution_authority is False


@pytest.mark.parametrize(
    "source_case", ["complete", "changed_after", "partial_exit", "missing_terminal"]
)
async def test_actual58_original_oco_terminal_source_drives_signed_resolution_or_hold(
    lifecycle_lab,
    monkeypatch,
    source_case,
):
    """Actual SQL/HMAC, explicitly synthetic Live raw DTOs; no venue authority."""
    from dataclasses import replace
    from types import SimpleNamespace

    from app.okx_live import original_oco_recovery_v1 as oco
    from app.okx_live.reconciliation_facts_v1 import LiveReconciliationHistoryV1
    from tests import native_live_postsubmit_fixtures_v1 as raw_fixture
    from tests.unit.test_native_live_original_oco_recovery_v1 import scenario

    lab = lifecycle_lab
    rid, _, _ = exact_live_intent_document_v1(lab[2])
    _, protected, protected_signature = _proof(
        lab,
        outcome="PROTECTED_HOLD",
        entry_order_id="123",
        protection_algo_id="456",
        protection_verified=True,
        full_account_verified=True,
        fills_complete=True,
        fixture_purpose="synthetic_original_protected_id_sql_binding_only",
    )
    assert await _append(lab, protected, protected_signature) == sha(protected)
    async with lab[3]() as session, session.begin():
        retained = (
            await session.execute(
                text(
                    "SELECT proof_json,signature FROM public.native_live_lifecycle_proofs_v1 WHERE reservation_id=:rid ORDER BY sequence"
                ),
                {"rid": rid},
            )
        ).one()
    assert retained[0].encode("ascii") == protected
    assert hmac.compare_digest(
        hmac.digest(lab[6], spec.DOMAIN + protected, hashlib.sha256).hex(), retained[1]
    )
    expected_entry, expected_algo = oco.expected_original_bindings_v1(
        SimpleNamespace(proofs=(protected,))
    )
    async with lab[0].connect() as connection:
        recorded = await connection.scalar(
            text(
                "SELECT created_at FROM public.native_live_intents_v1 WHERE reservation_id=:rid"
            ),
            {"rid": rid},
        )
    delta = recorded - datetime(1970, 1, 1, tzinfo=UTC)
    recorded_ns = (
        delta.days * 86400 + delta.seconds
    ) * 1_000_000_000 + delta.microseconds * 1000
    base = ((recorded_ns + 999999) // 1_000_000) * 1_000_000
    # Declare the NEW synthetic wire's measured-time template before creating
    # any DTO. There is no venue packet, retained receipt or source retagging.
    with monkeypatch.context() as construction:
        construction.setattr(raw_fixture, "BASE", base)
        value, capture, args = scenario(original_intent=lab[2])
    assert value is lab[2]
    args.update(
        history=LiveReconciliationHistoryV1(recorded),
        expected_entry_order_id=expected_entry,
        expected_algo_id=expected_algo,
    )
    if source_case == "changed_after":
        row = decode(capture.protection_after.raw_body)["data"][0]
        row["slTriggerPx"] = "98"
        capture = replace(
            capture,
            protection_after=raw_fixture.rewrite(capture.protection_after, [row]),
        )
    elif source_case == "partial_exit":
        row = decode(capture.exit_order.raw_body)["data"][0]
        row["accFillSz"] = "0.5"
        capture = replace(
            capture,
            exit_order=raw_fixture.rewrite(capture.exit_order, [row]),
            exit_order_after=raw_fixture.rewrite(capture.exit_order_after, [row]),
        )
    elif source_case == "missing_terminal":
        capture = replace(capture, exit_fills=capture.exit_fills[:1])
    assessed = oco.assess_original_live_oco_terminal_v1(value, capture, **args)
    assert assessed.execution_authority is assessed.resolution_authority is False
    # Let the declared synthetic timeline pass; no original intent/Control
    # deadline, lease or native source clock is reset or extended.
    await asyncio.sleep(0.6)
    expected = "RECONCILED_FLAT" if source_case == "complete" else "UNCERTAIN"
    assert assessed.outcome == expected
    complete = expected == "RECONCILED_FLAT"
    _, raw, signature = _proof(
        lab,
        outcome=expected,
        sequence=2,
        previous_sha256=sha(protected),
        credential_continuity="fresh_recovery_lease_uid_reverified",
        full_account_verified=complete,
        fills_complete=complete,
        flat_verified=complete,
        unknown_exposure=not complete,
        raw_bank_sha256=assessed.raw_bank_sha256,
        submission_sha256="2" * 64,
        account_config_before_sha256=capture.config_before.receipt_sha256,
        account_config_after_sha256=capture.config_after.receipt_sha256,
        entry_order_id=assessed.order_id,
        entry_order_state="filled" if complete else None,
        entry_filled_contracts=str(assessed.filled_contracts) if complete else None,
        exit_filled_contracts=str(assessed.exit_filled_contracts) if complete else None,
        exit_kind=assessed.exit_kind,
        exit_lineage_verified=assessed.exit_lineage_verified,
        exit_source_sha256=assessed.exit_source_sha256,
        protection_client_id=value.protection_client_id,
        position_count=0 if complete else None,
        pending_order_count=0 if complete else None,
        pending_algo_count=0 if complete else None,
        submission_started=None,
        submission_attempted=complete,
        fixture_purpose="synthetic_live_raw_and_hmac_sql_only",
    )
    assert await _append(lab, raw, signature) == sha(raw)
    async with lab[0].connect() as connection:
        state = (
            await connection.execute(
                text(
                    "SELECT state,resolution_sha256 FROM public.native_live_reservations_v1 WHERE reservation_id=:rid"
                ),
                {"rid": rid},
            )
        ).one()
        assert tuple(state) == (
            ("reconciled", sha(raw)) if complete else ("reserved", None)
        )
        assert (
            await connection.execute(
                text(
                    "SELECT armed,emergency_stop,arm_id,arm_expires_at FROM public.native_live_controls_v1"
                )
            )
        ).one() == (False, True, None, None)
