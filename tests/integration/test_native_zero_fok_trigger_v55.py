"""Real TEMP PostgreSQL trigger over synthetic DATA; no native owner minted.

Only fixed public table references/creation target are mapped into pg_temp.
The original0027 restore must reject the same witness. These cases cannot
replace full native acquisition→submit→readback→restart integration.
"""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.database.repositories import qualification_ledger as ledger
from app.domain import native_zero_fok_closure_schema_v55 as schema
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import native_reconciliation_facts_v1 as facts
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- preserve the actual shared fixture registration.
)
from tests.unit.test_native_ledger_contracts_v2 import (
    synthetic as synthetic,  # noqa: PLC0414 -- preserve the actual shared fixture registration.
)
from tests.unit.test_native_reconciliation_facts_v1 import data
from tests.unit.test_native_reconciliation_facts_v1 import (
    target as target,  # noqa: PLC0414 -- preserve the actual shared fixture registration.
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_TABLES = {
    "demo_account_controls": "environment text,account_id text",
    "qualification_account_scopes": "environment text,account_id text,settlement_currency text,ledger_revision bigint,updated_at timestamptz",
    "qualification_reservations": "reservation_id text,environment text,account_id text,settlement_currency text,request_json text,request_sha256 text,state text,state_revision bigint,updated_at timestamptz",
    "qualification_reservation_transitions": "id bigint,reservation_id text,state_revision bigint,from_state text,to_state text,reason_code text,evidence_json text,occurred_at timestamptz",
    "qualification_submission_outcomes": "outcome_id text,reservation_id text,intent_transition_id bigint,sequence bigint,previous_sha256 text,observation_kind text,status text,intent_sha256 text,exchange_request_sha256 text,capture_json text,capture_sha256 text,outcome_json text,ledger_revision bigint,observed_at timestamptz,recorded_at timestamptz",
    "demo_collector_issued_account_revisions": "issuer_sha256 text,environment text,account_id text,settlement_currency text,previous_account_revision bigint,previous_ledger_revision bigint,previous_claims_sha256 text,account_revision bigint,ledger_revision bigint,claims_sha256 text,claims_json text,control_revision bigint,control_event_sha256 text,control_state_sha256 text,source_lineage_sha256 text,hwm_custody_id text,hwm_bundle_sha256 text,proof_json text,original_expires_at timestamptz,db_recorded_at timestamptz",
    "demo_collector_current_revision_lineages": "lineage_sha256 text,environment text,account_id text,settlement_currency text,current_capture_id text,current_terminal_sequence bigint,current_head_sha256 text,lineage_json text",
    "demo_account_capture_events": "capture_id text,sequence bigint,environment text,account_id text,settlement_currency text,previous_sha256 text,chain_bytes bigint,event_sha256 text,event_json text,raw_body bytea,packet_payload bytea",
}


async def insert(connection, table, row):
    assert table in _TABLES
    await connection.execute(
        text(
            f"INSERT INTO pg_temp.{table} SELECT * FROM jsonb_populate_record("
            f"NULL::pg_temp.{table},CAST(:row AS jsonb))"
        ),
        {"row": canonical(row).decode()},
    )


def sample(target, mutation):
    observations = list(data(target))
    assessment = decode(
        facts.assess_native_reconciliation_v1(target, tuple(observations)).receipt_json
    )
    for index, (role, page) in enumerate(observations):
        payload = decode(page.raw_body)
        if (
            mutation == "future_order"
            and role in {"order_before", "order_after"}
            or mutation == "future_balance"
            and role == "balance"
        ):
            payload["data"][0]["uTime"] = "9999999999999"
        elif mutation == "future_detail" and role == "balance":
            payload["data"][0]["details"][0]["uTime"] = "9999999999999"
        elif mutation == "future_risk" and role == "account_risk":
            payload["data"][0]["ts"] = "9999999999999"
        elif mutation == "missing_order_clock" and role == "order_before":
            del payload["data"][0]["cTime"]
        elif mutation == "nan_equity" and role == "balance":
            payload["data"][0]["details"][0]["eq"] = "NaN"
        elif mutation == "infinity_risk" and role == "account_risk":
            payload["data"][0]["balData"][0]["eq"] = "Infinity"
        elif mutation == "numeric_json_equity" and role == "balance":
            payload["data"][0]["details"][0]["eq"] = 10000
        elif mutation == "nonzero_inventory" and role == "positions":
            payload["data"] = [{"pos": "1", "cTime": "1", "uTime": "1"}]
        elif mutation == "unexpected_fill" and role == "fills_recent":
            payload["data"] = [{"billId": "99"}]
        elif mutation == "partial_order" and role in {"order_before", "order_after"}:
            payload["data"][0]["accFillSz"] = "0.5"
        elif mutation == "unexpected_protection" and role == "algo_oco":
            payload["data"] = [{"algoId": "99", "cTime": "1"}]
        raw = canonical(payload)
        if mutation == "duplicate_raw_data" and role == "positions":
            raw = b'{"code":"0","data":[{"pos":"1"}],"data":[]}'
        observations[index] = (role, replace(page, raw_body=raw))
    return observations, assessment


async def prepare(connection, target, mutation, *, restore=False):
    for table, columns in _TABLES.items():
        await connection.execute(
            text(f"CREATE TEMP TABLE {table} ({columns}) ON COMMIT DROP")
        )
    sql = schema.restore_statements()[0] if restore else schema.statements()[0]
    if not restore:
        for table in _TABLES:
            sql = sql.replace("public." + table, "pg_temp." + table)
    sql = sql.replace(
        "FUNCTION public.qualification_reservation_update()",
        "FUNCTION pg_temp.native_zero_fok_test()",
        1,
    )
    await connection.execute(text(sql))
    await connection.execute(
        text(
            "CREATE TRIGGER native_flat BEFORE UPDATE ON pg_temp.qualification_reservations "
            "FOR EACH ROW EXECUTE FUNCTION pg_temp.native_zero_fok_test()"
        )
    )
    scope = target.request.scope.model_dump(
        mode="json", include={"environment", "account_id", "settlement_currency"}
    )
    resid = target.reservation_id
    observations, assessment = sample(target, mutation)
    final_time = observations[-1][1].eof_at + timedelta(milliseconds=1)
    initial_time = observations[0][1].started_at - timedelta(milliseconds=1)
    header_data = {
        "plan": {
            "expected_uid": scope["account_id"],
            "environment": "demo",
            "settlement_currency": "USDT",
            "expected_main_uid": target.main_uid,
            "origin": target.origin,
            "session_binding_id": "original-session",
        },
        "plan_sha256": "b" * 64,
        "session_binding_sha256": sha(b"original-session"),
    }
    previous, chain_bytes, packet_sha = None, 0, sha(b"{}")
    for sequence, kind in enumerate(
        ("capture_start", "packet_recorded", "terminal"), 1
    ):
        packet = b"{}" if sequence == 2 else None
        event = {
            "version": "ctcc.demo_account_ingestion_event.v1",
            "capture_id": "c" * 32,
            "sequence": sequence,
            "previous_sha256": previous,
            "kind": kind,
            "outcome": "complete_recorded" if sequence == 3 else "in_progress",
            "data": header_data if sequence == 1 else {},
            "raw_sha256": None,
            "packet_sha256": packet_sha if packet else None,
            "execution_authority": False,
            "account_complete": False,
            "admission": "DENY",
        }
        raw = canonical(event)
        chain_bytes += len(raw) + len(packet or b"")
        await insert(
            connection,
            "demo_account_capture_events",
            {
                **scope,
                "capture_id": "c" * 32,
                "sequence": sequence,
                "previous_sha256": previous,
                "chain_bytes": chain_bytes,
                "event_sha256": sha(raw),
                "event_json": raw.decode(),
                "raw_body": None,
                "packet_payload": "\\x" + packet.hex() if packet else None,
            },
        )
        previous = sha(raw)
    lineage_json = canonical(
        {
            "source_reference": {
                "capture_id": "c" * 32,
                "head_sha256": previous,
                "packet_sha256": packet_sha,
                "plan_sha256": "b" * 64,
                "session_binding_sha256": sha(b"original-session"),
            }
        }
    ).decode()
    lineage_sha = sha(lineage_json.encode())
    await insert(
        connection,
        "demo_collector_current_revision_lineages",
        {
            **scope,
            "lineage_sha256": lineage_sha,
            "current_capture_id": "c" * 32,
            "current_terminal_sequence": 3,
            "current_head_sha256": previous,
            "lineage_json": lineage_json,
        },
    )
    proof = canonical({"original": 'raw Unicode台灣\\\n\t\b\f\r"'}).decode()
    issued = {
        **scope,
        "issuer_sha256": sha(proof.encode()),
        "previous_account_revision": 2,
        "previous_ledger_revision": 4,
        "previous_claims_sha256": None,
        "account_revision": target.request.expected_account_revision,
        "ledger_revision": target.request.expected_ledger_revision,
        "claims_sha256": sha(b"{}"),
        "claims_json": "{}",
        "control_revision": 2,
        "control_event_sha256": "d" * 64,
        "control_state_sha256": "e" * 64,
        "source_lineage_sha256": lineage_sha,
        "hwm_custody_id": "f" * 32,
        "hwm_bundle_sha256": "1" * 64,
        "proof_json": proof,
        "original_expires_at": target.request.origin.deadline.replace(
            microsecond=0 if mutation == "iso_zero" else 123456
        ).isoformat(),
        "db_recorded_at": initial_time.replace(
            microsecond=0 if mutation == "iso_zero" else 123456
        ).isoformat(),
    }
    await insert(connection, "demo_collector_issued_account_revisions", issued)
    request = decode(target.request_json.encode())
    request.update(
        issuer_sha256=issued["issuer_sha256"],
        issued_row_sha256=sha(canonical(issued)),
        credential_session_sha256=sha(b"original-session"),
    )
    if mutation == "issued_row_hash":
        request["issued_row_sha256"] = "8" * 64
    outer = decode(target.intent_json.encode())
    inner = decode(outer["intent_json"].encode())
    request_raw = canonical(request).decode()
    inner["request_sha256"] = sha(request_raw.encode())
    outer["intent_json"] = canonical(inner).decode()
    outer_raw = canonical(outer).decode()
    await insert(
        connection,
        "demo_account_controls",
        {"environment": "demo", "account_id": scope["account_id"]},
    )
    await insert(
        connection,
        "qualification_reservations",
        {
            **scope,
            "reservation_id": resid,
            "request_json": request_raw,
            "request_sha256": sha(request_raw.encode()),
            "state": "uncertain",
            "state_revision": 3,
            "updated_at": initial_time.isoformat(),
        },
    )
    for revision, before, after, reason, evidence, at in (
        (
            1,
            None,
            "reserved",
            "risk_reserved_control_bound_v1",
            outer["reservation_binding_json"],
            initial_time,
        ),
        (
            2,
            "reserved",
            "consumed",
            "consumed_with_control_bound_intent_v1",
            outer_raw,
            initial_time,
        ),
        (
            3,
            "consumed",
            "uncertain",
            "post_submit_native_uncertain_v1",
            "{}",
            initial_time,
        ),
    ):
        await insert(
            connection,
            "qualification_reservation_transitions",
            {
                "id": revision,
                "reservation_id": resid,
                "state_revision": revision,
                "from_state": before,
                "to_state": after,
                "reason_code": reason,
                "evidence_json": evidence,
                "occurred_at": at.isoformat(),
            },
        )
    outcomes = []

    async def outcome(capture, *, kind="reconciliation", status="uncertain"):
        sequence = len(outcomes) + 1
        raw = canonical(capture).decode()
        document = {
            "version": "ctcc.native_submission_outcome.v1",
            "reservation_id": resid,
            "intent_transition_id": 2,
            "intent_sha256": sha(outer_raw.encode()),
            "exchange_request_sha256": sha(canonical(inner["exact_order"])),
            "request_sha256": sha(request_raw.encode()),
            "sequence": sequence,
            "previous_sha256": outcomes[-1] if outcomes else None,
            "observation_kind": kind,
            "status": status,
            "capture_sha256": sha(raw.encode()),
            "ledger_revision": sequence + 10,
            "observed_at": final_time.isoformat(),
            "recorded_at": final_time.isoformat(),
            "execution_authority": False,
            "order_retry_authority": False,
        }
        encoded = canonical(document).decode()
        identity = sha(encoded.encode())
        await insert(
            connection,
            "qualification_submission_outcomes",
            {
                **{
                    key: document[key]
                    for key in (
                        "reservation_id",
                        "intent_transition_id",
                        "sequence",
                        "previous_sha256",
                        "observation_kind",
                        "status",
                        "intent_sha256",
                        "exchange_request_sha256",
                        "capture_sha256",
                        "ledger_revision",
                        "observed_at",
                        "recorded_at",
                    )
                },
                "outcome_id": identity,
                "outcome_json": encoded,
                "capture_json": raw,
            },
        )
        outcomes.append(identity)
        return identity

    await outcome({}, kind="initial")
    pages = []
    for index, (role, page) in enumerate(observations):
        metadata = {
            "version": "ctcc.native_read_page_metadata.v1",
            "capture_id": "a" * 64,
            "page_index": index,
            "role": role,
            "path": page.path,
            "parameters": list(page.parameters),
            "request_url": page.request_url,
            "request_signature_sha256": page.request_signature_sha256,
            "read_session_id": "2" * 64,
            "credential_session_sha256": "3" * 64,
            "original_credential_session_sha256": sha(b"original-session"),
            "started_at": page.started_at.isoformat(),
            "started_monotonic_ns": page.started_monotonic_ns,
            "headers_at": page.headers_at.isoformat(),
            "headers_monotonic_ns": page.headers_monotonic_ns,
            "eof_at": page.eof_at.isoformat(),
            "eof_monotonic_ns": page.eof_monotonic_ns,
            "status": 200,
            "certificate_sha256": page.certificate_sha256,
            "tls_hostname": page.tls_hostname,
            "body_bytes": len(page.raw_body),
            "body_sha256": sha(page.raw_body),
        }
        metadata_id = await outcome(metadata)
        part_id = await outcome(
            {
                "version": "ctcc.native_read_raw_part.v1",
                "capture_id": "a" * 64,
                "page_index": index,
                "metadata_outcome_id": metadata_id,
                "offset": 0,
                "raw_hex": page.raw_body.hex(),
                "raw_sha256": sha(page.raw_body),
            }
        )
        pages.append(
            {
                "role": role,
                "metadata": metadata_id,
                "parts": [part_id],
                "body_sha256": sha(page.raw_body),
            }
        )
    assessment.update(
        request_sha256=sha(request_raw.encode()), intent_sha256=sha(outer_raw.encode())
    )
    assessment_json = canonical(assessment).decode()
    manifest_id = await outcome(
        {
            "version": "ctcc.native_read_manifest.v1",
            "capture_id": "a" * 64,
            "reservation_id": resid,
            "pages": pages,
            "computation": "not_evaluated" if mutation == "unassessed" else "completed",
            "assessment_sha256": sha(assessment_json.encode()),
        }
    )
    await outcome(
        {
            "version": "ctcc.native_zero_fok_flat_closure.v1",
            "reservation_id": resid,
            "request_sha256": sha(request_raw.encode()),
            "intent_sha256": sha(outer_raw.encode()),
            "issued_row_sha256": request["issued_row_sha256"],
            "original_credential_session_sha256": sha(b"original-session"),
            "original_main_uid": "other"
            if mutation == "original_identity"
            else target.main_uid,
            "original_origin": target.origin,
            "manifest_outcome_id": manifest_id,
            "exact_order_json": canonical(inner["exact_order"]).decode(),
            "assessment_json": assessment_json,
            "assessment_sha256": sha(assessment_json.encode()),
            "execution_authority": False,
            "order_retry_authority": False,
        },
        status="rejected",
    )
    await insert(
        connection,
        "qualification_account_scopes",
        {
            **scope,
            "ledger_revision": len(outcomes) + 10,
            "updated_at": final_time.isoformat(),
        },
    )
    if mutation != "no_uid_lock":
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": ledger._account_uid_lock_key(target.request.scope)},
        )
    if mutation == "no_control":
        await connection.execute(text("DELETE FROM pg_temp.demo_account_controls"))
    return final_time


async def exercise(database, target, mutation=None, *, restore=False):
    engine, _ = database
    async with engine.connect() as connection, connection.begin():
        at = await prepare(connection, target, mutation, restore=restore)
        statement = text(
            "UPDATE pg_temp.qualification_reservations SET state='reconciled_flat',state_revision=4,updated_at=:at"
        )
        if mutation in (None, "iso_zero") and not restore:
            await connection.execute(statement, {"at": at})
            assert (
                await connection.scalar(
                    text("SELECT state FROM pg_temp.qualification_reservations")
                )
                == "reconciled_flat"
            )
        else:
            async with connection.begin_nested() as savepoint:
                with pytest.raises(
                    DBAPIError, match="qualification_reservation_update_denied"
                ):
                    await connection.execute(statement, {"at": at})
                await savepoint.rollback()


async def test_exact_zero_fok_body_recomputes_and_original0027_still_denies(
    database, target
):
    await exercise(database, target)
    await exercise(database, target, "iso_zero")
    await exercise(database, target, restore=True)


@pytest.mark.parametrize(
    "mutation",
    (
        "future_order",
        "future_balance",
        "future_detail",
        "future_risk",
        "missing_order_clock",
        "nan_equity",
        "infinity_risk",
        "numeric_json_equity",
        "nonzero_inventory",
        "unexpected_fill",
        "partial_order",
        "unexpected_protection",
        "unassessed",
        "original_identity",
        "no_uid_lock",
        "no_control",
        "duplicate_raw_data",
        "issued_row_hash",
    ),
)
async def test_recomputed_digests_cannot_hide_invalid_zero_witness(
    database, target, mutation
):
    await exercise(database, target, mutation)
