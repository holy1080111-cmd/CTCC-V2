"""Real PostgreSQL component body over declared synthetic TEMP rows only.

No public table or installed function changes. These verify DATA append binding,
not a genuine protective closure, native owner, or exchange execution. The exact
source function body is retained; only its private TEMP creation target changes.
Full physical0058 profile and full public journal acceptance remain separate.
"""

from copy import deepcopy

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain import native_protective_hold_schema_v58 as hold
from app.domain import native_submission_binding_schema_v58 as schema
from tests.integration import test_native_submission_reporting_schema_v55 as lower
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- explicit disposable DB fixture only.
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _case(version="ctcc.native_read_manifest.v1"):
    case = lower.sample()
    # This fixture's acknowledged closure is DATA supplied to a TEMP trigger;
    # it is never inserted into a public qualification ledger or treated as a
    # successful protective predicate/risk-resolution proof.
    case.update(
        kind="reconciliation",
        sequence=2,
        row_state="reconciled_flat",
        closed_witness=("acknowledged", hold.TERMINAL_VERSION, hold.TRANSITION_REASON),
    )
    outer = {
        "version": "ctcc-control-bound-submit-intent-v1",
        "intent_json": lower.dump(case["inner"]),
        "intent_sha256": lower.sha(lower.dump(case["inner"])),
        "reservation_binding_json": lower.dump(case["reserved"]),
        "reservation_binding_sha256": lower.sha(lower.dump(case["reserved"])),
        "execution_authority": False,
        "order_retry_authority": False,
    }
    case["closed_witness_capture"] = {
        "reservation_id": "b" * 64,
        "request_sha256": lower.sha(lower.dump(case["request"])),
        "intent_sha256": lower.sha(lower.dump(outer)),
        "closure_body_sha256": hold.CURRENT_BODY_SHA256,
        "risk_hold_retained": False,
        "execution_authority": False,
        "order_retry_authority": False,
    }
    case["capture"] = {
        "version": version,
        "recovery_closed_outcome_id": "d" * 64,
        "execution_authority": False,
        "order_retry_authority": False,
    }
    return case


async def _run(database, case, *, accepted, restored=False):
    engine, _ = database
    async with engine.connect() as connection, connection.begin():
        sql = schema.ANCESTOR_SQL if restored else schema.CURRENT_SQL
        await lower.install(connection, sql)
        await connection.execute(
            text(
                "ALTER TABLE pg_temp.qualification_reservations ADD COLUMN state_revision bigint NOT NULL DEFAULT 4"
            )
        )
        body = await connection.scalar(
            text(
                "SELECT prosrc FROM pg_catalog.pg_proc WHERE oid='pg_temp.native_submission_binding_test()'::regprocedure"
            )
        )
        assert body == sql.split("$$")[1]
        if accepted:
            await lower.insert_case(connection, case)
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_temp.qualification_submission_outcomes"
                    )
                )
                == 2
            )
        else:
            async with connection.begin_nested() as savepoint:
                with pytest.raises(
                    DBAPIError, match="submission_outcome_binding_denied"
                ):
                    await lower.insert_case(connection, case)
                await savepoint.rollback()


@pytest.mark.parametrize(
    "version",
    [
        "ctcc.native_read_page_metadata.v1",
        "ctcc.native_read_raw_part.v1",
        "ctcc.native_read_manifest.v1",
    ],
)
async def test_real0058_binding_allows_only_exact_original_oco_postflat_raw_data(
    database, version
):
    case = _case(version)
    await _run(database, case, accepted=True)
    await _run(database, case, accepted=False, restored=True)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_witness",
        "authority",
        "wrong_reason",
        "wrong_version",
        "request_hash",
        "intent_hash",
        "body_hash",
        "risk_retained",
        "ownclose_payload",
        "unrelated_payload",
    ],
)
async def test_real0058_binding_rejects_forged_postflat_lineage_or_unrelated_data(
    database, mutation
):
    case = deepcopy(_case())
    witness = case["closed_witness_capture"]
    if mutation == "wrong_witness":
        case["capture"]["recovery_closed_outcome_id"] = "e" * 64
    elif mutation == "authority":
        case["capture"]["execution_authority"] = True
    elif mutation == "wrong_reason":
        case["closed_witness"] = (
            "acknowledged",
            hold.TERMINAL_VERSION,
            "post_submit_native_filled_flat_v1",
        )
    elif mutation == "wrong_version":
        case["closed_witness"] = (
            "acknowledged",
            "ctcc.native_owned_filled_flat_closure.v1",
            hold.TRANSITION_REASON,
        )
    elif mutation == "request_hash":
        witness["request_sha256"] = "e" * 64
    elif mutation == "intent_hash":
        witness["intent_sha256"] = "e" * 64
    elif mutation == "body_hash":
        witness["closure_body_sha256"] = "e" * 64
    elif mutation == "risk_retained":
        witness["risk_hold_retained"] = True
    elif mutation == "ownclose_payload":
        case["capture"]["version"] = "ctcc.native_closed_trade_readback.v1"
    elif mutation == "unrelated_payload":
        case["capture"]["version"] = "ctcc.native_realized_forensics.v2"
    await _run(database, case, accepted=False)
