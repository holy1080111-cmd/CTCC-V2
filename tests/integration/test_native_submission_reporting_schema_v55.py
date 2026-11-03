"""Exercise the exact new trigger body against PostgreSQL TEMP rows only.

These synthetic rows test DB0018's data binding. They cannot produce an issued
owner, native request, exchange write, or closure witness. No public table or
installed function is changed. Full migration/catalog tests remain separate.
"""

import copy
import hashlib
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain import native_submission_reporting_schema_v55 as schema
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- preserve actual pytest fixture reexport.
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def dump(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def sample():
    scope = {
        "environment": "demo",
        "account_id": "991234567890123456",
        "settlement_currency": "USDT",
    }
    request = {
        "contract_version": "ctcc-reservation-request-native-v4",
        "scope": scope,
        "credential_session_sha256": "a" * 64,
        "expected_account_revision": 11,
        "expected_ledger_revision": 21,
    }
    reserved = {
        "version": "ctcc-control-bound-reservation-v1",
        "request_sha256": sha(dump(request)),
    }
    inner = {
        "version": "ctcc-demo-submit-intent-native-v2",
        "request_sha256": sha(dump(request)),
        "credential_session_sha256": "a" * 64,
        "execution_authority": False,
        "order_retry_authority": False,
        "reservation_receipt": {
            "reservation_id": "b" * 64,
            "request_sha256": sha(dump(request)),
            "scope": scope,
            "state": "consumed",
            "state_revision": 2,
            "account_revision": 11,
            "ledger_revision": 23,
        },
    }
    return {
        "request": request,
        "reserved": reserved,
        "inner": inner,
        "reason": "consumed_with_control_bound_intent_v1",
        "status": "uncertain",
        "kind": "initial",
        "sequence": 1,
        "scope_ledger_revision": 24,
        "outcome_ledger_revision": 24,
        "row_state": "uncertain",
        "bound_reserved": True,
    }


async def install(connection, sql):
    # The BODY is byte-identical; only its creation target is private TEMP.
    assert connection.dialect.name == "postgresql"
    for ddl in (
        (
            "CREATE TEMP TABLE qualification_reservations (reservation_id text, "
            "environment text,account_id text,settlement_currency text,"
            "request_json text,request_sha256 text,created_at timestamptz,state text) ON COMMIT DROP"
        ),
        (
            "CREATE TEMP TABLE qualification_reservation_transitions (id text,"
            "reservation_id text,state_revision bigint,from_state text,to_state text,"
            "reason_code text,evidence_json text,occurred_at timestamptz) ON COMMIT DROP"
        ),
        (
            "CREATE TEMP TABLE qualification_account_scopes (environment text,"
            "account_id text,settlement_currency text,ledger_revision bigint,"
            "updated_at timestamptz) ON COMMIT DROP"
        ),
        (
            "CREATE TEMP TABLE qualification_submission_outcomes (outcome_id text,"
            "reservation_id text,intent_transition_id text,sequence bigint,"
            "previous_sha256 text,intent_sha256 text,ledger_revision bigint,"
            "observed_at timestamptz,recorded_at timestamptz,observation_kind text,"
            "status text,capture_json text) ON COMMIT DROP"
        ),
    ):
        await connection.execute(text(ddl))
    await connection.execute(
        text(
            sql.replace(
                "FUNCTION qualification_submission_binding()",
                "FUNCTION pg_temp.native_submission_binding_test()",
                1,
            )
        )
    )
    await connection.execute(
        text(
            "CREATE TRIGGER native_data_binding BEFORE INSERT ON "
            "qualification_submission_outcomes FOR EACH ROW EXECUTE FUNCTION "
            "pg_temp.native_submission_binding_test()"
        )
    )


async def insert_case(connection, case):
    request, reserved, inner = case["request"], case["reserved"], case["inner"]
    reservation_json = dump(reserved)
    inner_json = dump(inner)
    outer = {
        "version": "ctcc-control-bound-submit-intent-v1",
        "intent_json": inner_json,
        "intent_sha256": sha(inner_json),
        "reservation_binding_json": reservation_json,
        "reservation_binding_sha256": sha(reservation_json),
        "execution_authority": False,
        "order_retry_authority": False,
    }
    outer.update(case.get("outer", {}))
    outer_json = dump(outer)
    if case.get("legacy"):
        outer_json = dump({"version": "ctcc-demo-submit-intent-v2"})
    stamp = datetime(2026, 11, 2, 1, 2, 3, tzinfo=UTC)
    scope = {
        "environment": "demo",
        "account_id": "991234567890123456",
        "settlement_currency": "USDT",
    }
    await connection.execute(
        text(
            "INSERT INTO qualification_account_scopes VALUES "
            "(:environment,:account_id,:settlement_currency,:revision,:stamp)"
        ),
        {**scope, "revision": case["scope_ledger_revision"], "stamp": stamp},
    )
    await connection.execute(
        text(
            "INSERT INTO qualification_reservations VALUES "
            "(:id,:environment,:account_id,:settlement_currency,:raw,:sha,:stamp,:state)"
        ),
        {
            **scope,
            "id": "b" * 64,
            "raw": dump(request),
            "sha": sha(dump(request)),
            "stamp": stamp,
            "state": case["row_state"],
        },
    )
    await connection.execute(
        text(
            "INSERT INTO qualification_reservation_transitions VALUES "
            "('reserved',:id,1,NULL,'reserved','risk_reserved_control_bound_v1',:raw,:stamp),"
            "('consumed',:id,2,'reserved','consumed',:reason,:outer,:stamp)"
        ),
        {
            "id": "b" * 64,
            "raw": reservation_json if case["bound_reserved"] else "{}",
            "stamp": stamp,
            "outer": outer_json,
            "reason": case["reason"],
        },
    )
    if case.get("closed_witness"):
        await connection.execute(
            text(
                "INSERT INTO qualification_submission_outcomes VALUES "
                "(:id,:reservation,'consumed',1,NULL,:intent,24,:at,:at,'reconciliation',:status,:capture)"
            ),
            {
                "id": "d" * 64,
                "reservation": "b" * 64,
                "intent": sha(outer_json),
                "at": stamp,
                "status": case["closed_witness"][0],
                "capture": dump({"version": case["closed_witness"][1]}),
            },
        )
        await connection.execute(
            text(
                "INSERT INTO qualification_reservation_transitions VALUES "
                "('closure',:reservation,4,'uncertain','reconciled_flat',:reason,:evidence,:at)"
            ),
            {
                "reservation": "b" * 64,
                "reason": case["closed_witness"][2],
                "evidence": dump({"outcome_id": "d" * 64}),
                "at": stamp,
            },
        )
    await connection.execute(
        text(
            "INSERT INTO qualification_submission_outcomes VALUES "
            "(:outcome,:id,'consumed',:sequence,:previous,:sha,:revision,:stamp,:stamp,:kind,:status,:capture)"
        ),
        {
            "outcome": "c" * 64,
            "previous": "d" * 64 if case.get("closed_witness") else None,
            "capture": dump(case.get("capture", {})),
            "id": "b" * 64,
            "sequence": case["sequence"],
            "sha": case.get("intent_sha", sha(outer_json)),
            "revision": case["outcome_ledger_revision"],
            "stamp": stamp,
            "kind": case["kind"],
            "status": case["status"],
        },
    )


async def exercise(database, case, *, accepted, restore=False):
    engine, _ = database
    async with engine.connect() as connection, connection.begin():
        sql = schema.restore_statements()[0] if restore else schema.statements()[0]
        await install(connection, sql)
        if accepted:
            await insert_case(connection, case)
            assert await connection.scalar(
                text("SELECT count(*) FROM pg_temp.qualification_submission_outcomes")
            ) == (2 if case.get("closed_witness") else 1)
        else:
            async with connection.begin_nested() as savepoint:
                with pytest.raises(
                    DBAPIError, match="submission_outcome_binding_denied"
                ):
                    await insert_case(connection, case)
                await savepoint.rollback()


async def test_native_exact_pair_is_data_only_and_original_body_rejects_it(database):
    await exercise(database, sample(), accepted=True)
    await exercise(database, sample(), accepted=False, restore=True)


@pytest.mark.parametrize(
    "change",
    (
        "credential_missing",
        "credential_changed",
        "receipt_scope",
        "receipt_account",
        "receipt_ledger",
        "receipt_state_revision",
        "receipt_request",
        "inner_authority",
        "outer_authority",
        "reserved_bytes",
        "initial_ack",
        "outer_digest",
        "inner_digest",
        "reservation_digest",
        "ledger_cas",
        "initial_sequence",
        "native_legacy_masquerade",
        "request_revision_null",
    ),
)
async def test_native_mutation_denies_without_changing_outer_chain_guards(
    database, change
):
    case = copy.deepcopy(sample())
    inner, request = case["inner"], case["request"]
    receipt = inner["reservation_receipt"]
    if change == "credential_missing":
        del inner["credential_session_sha256"]
    elif change == "credential_changed":
        inner["credential_session_sha256"] = "d" * 64
    elif change == "receipt_scope":
        receipt["scope"] = {**receipt["scope"], "environment": "live"}
    elif change == "receipt_account":
        receipt["account_revision"] += 1
    elif change == "receipt_ledger":
        receipt["ledger_revision"] += 1
    elif change == "receipt_state_revision":
        receipt["state_revision"] = 3
    elif change == "receipt_request":
        receipt["request_sha256"] = "d" * 64
    elif change == "inner_authority":
        inner["execution_authority"] = True
    elif change == "outer_authority":
        case["outer"] = {"order_retry_authority": True}
    elif change == "reserved_bytes":
        case["bound_reserved"] = False
    elif change == "initial_ack":
        case["status"] = "acknowledged"
    elif change == "outer_digest":
        case["intent_sha"] = "d" * 64
    elif change == "inner_digest":
        case["outer"] = {"intent_sha256": "d" * 64}
    elif change == "reservation_digest":
        case["outer"] = {"reservation_binding_sha256": "d" * 64}
    elif change == "ledger_cas":
        case["outcome_ledger_revision"] += 1
    elif change == "initial_sequence":
        case["sequence"] = 2
    elif change == "native_legacy_masquerade":
        case.update(legacy=True, reason="consumed_with_submit_intent")
    elif change == "request_revision_null":
        request["expected_ledger_revision"] = None
    await exercise(database, case, accepted=False)


async def test_old_unbound_pair_stays_accepted_and_native_cannot_use_it(database):
    case = sample()
    case["request"]["contract_version"] = "ctcc-reservation-request-v2"
    case.update(legacy=True, reason="consumed_with_submit_intent")
    await exercise(database, case, accepted=True)
    await exercise(database, case, accepted=True, restore=True)


@pytest.mark.parametrize("kind", ("zero", "filled"))
@pytest.mark.parametrize(
    "mutation", (None, "wrong_witness", "authority", "unrelated_version", "wrong_state")
)
async def test_closed_recovery_data_requires_exact_spent_witness(
    database, kind, mutation
):
    zero = kind == "zero"
    case = sample()
    case.update(
        kind="reconciliation",
        sequence=2,
        row_state="reconciled_flat",
        closed_witness=(
            "rejected" if zero else "acknowledged",
            "ctcc.native_zero_fok_flat_closure.v1"
            if zero
            else "ctcc.native_owned_filled_flat_closure.v1",
            "post_submit_native_zero_fok_flat_v1"
            if zero
            else "post_submit_native_filled_flat_v1",
        ),
    )
    case["capture"] = {
        "version": "ctcc.native_read_manifest.v1"
        if zero
        else "ctcc.native_closed_trade_readback.v1",
        "recovery_closed_outcome_id": "e" * 64
        if mutation == "wrong_witness"
        else "d" * 64,
        "execution_authority": mutation == "authority",
        "order_retry_authority": False,
    }
    if mutation == "unrelated_version":
        case["capture"]["version"] = "ctcc.native_submission_outcome.v1"
    if mutation == "wrong_state":
        case["row_state"] = "consumed"
    await exercise(database, case, accepted=mutation is None)
