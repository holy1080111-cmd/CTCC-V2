"""Read the exact retained Stop revision-1 snapshot through held native custody."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from scripts import retained0055_negative_stop_reassert_v1 as negative
from scripts import retained0056_bootstrap_reference_pin_v2 as fitted_reference
from scripts import retained_pg_bootstrap_custody_v1 as custody
from scripts.evidence_json import read_evidence_json

REQUEST_SCHEMA = "ctcc.retained_pg.stopped0055_revision1_preflight_request.v1"
RESULT_SCHEMA = "ctcc.retained_pg.stopped0055_revision1_preflight_result.v1"
STATUS = "READ_ONLY_STOPPED_0055_REVISION1_PREFLIGHT_DATA"
INVENTORY_SHA256 = "4efe47970d43c3128a1a5b9722dae4bdc26d85c0d4dd174d61deb53736b5702a"
_ACCEPTANCE_SHA256 = "8124dcde06e63e5bbbd425db976da9c5a9f5e66f701449c096640354000905f1"
_SOURCE_ARCHIVE_SHA256 = (
    "d720d9ee557685890ca871ef1d4c0ee6e38a2c0b0a73480ed8dd1618307851da"
)
_AFTER_SHA256 = "fb68e12a8942b18dd8d2fac4a59ead3ab9e8050391a309fb1aad10cc65433292"
_SYSTEM_SHA256 = "724c2061030a884f9c88682ce000547ffd3b406b84bf2eff70e51081b151b937"
_CONTROL_SHA256 = "82d97d6f73d9cfccd1c69fd82d0eb96c7e3ba59feda3788f4a9b21511b6f8ee9"
_DURABLE_LAUNCH_SHA256 = (
    "851ba1aae51b3258c551170fb402ca1178d384fb4e73abc6d80473ac4e8ac490"
)
_PREDECESSOR_INVENTORY_SHA256 = (
    "5cc81205bb158e022b7eb7a80a495450a7cb39c2f594cbc09e48f3ab685e27f4"
)
_EXPECTED_PRIOR = {
    "physical_inventory_sha256": INVENTORY_SHA256,
    "native_acceptance_sha256": _ACCEPTANCE_SHA256,
    "source_archive_sha256": _SOURCE_ARCHIVE_SHA256,
    "after_sha256": _AFTER_SHA256,
}
_PHASES = ("ready", "claimed", "commit_attempted", "readback_only")
_SESSION = text("""SELECT pg_catalog.current_setting('default_transaction_read_only'),
 pg_catalog.current_setting('transaction_read_only'),
 pg_catalog.current_setting('transaction_isolation'), pg_catalog.pg_backend_pid()""")
_AUDIT = text("""SELECT actor='source_owned_maintenance' AND action=:action
 AND resource_type='demo_automation_state' AND resource_id='1'
 AND "before"->>'before_snapshot_sha256'=:before_sha256
 AND "before"->'control_revision'='0'::pg_catalog.jsonb
 AND "after"->'control_revision'='1'::pg_catalog.jsonb
 AND "before"->'execution_authority'='false'::pg_catalog.jsonb
 AND "after"->'execution_authority'='false'::pg_catalog.jsonb
 AND ip_address IS NULL AND occurred_at IS NOT NULL
 FROM public.audit_logs WHERE id=CAST(:operation_id AS pg_catalog.uuid)""")


def require_request_lineage(request):
    custody._need(
        request["schema"] == REQUEST_SCHEMA
        and request["predecessor"] == _EXPECTED_PRIOR
        and request["route"]["cluster_system_identifier_sha256"] == _SYSTEM_SHA256
    )


def admit_manifest(raw, request):
    """Bind the protected physical seal to the original revision-1 acceptance."""
    need = custody._need
    require_request_lineage(request)
    need(type(raw) is bytes and custody._sha(raw) == INVENTORY_SHA256)
    value = read_evidence_json(raw)
    need(type(value) is dict and custody._canonical(value) == raw)
    expected = {
        "schema": "ctcc.retained_pg.stopped_0055_negative_stop_physical_inventory.v1",
        "database_head": "0055",
        "control_revision": 1,
        "after_snapshot_sha256": _AFTER_SHA256,
        "native_acceptance_sha256": _ACCEPTANCE_SHA256,
        "source_archive_sha256": _SOURCE_ARCHIVE_SHA256,
        "durable_launch_sha256": _DURABLE_LAUNCH_SHA256,
        "predecessor_inventory_sha256": _PREDECESSOR_INVENTORY_SHA256,
        "system_identifier_sha256": _SYSTEM_SHA256,
        "pg_control_sha256": _CONTROL_SHA256,
        "postgres_started": False,
        "execution_authority": False,
        "reopen_authority": False,
    }
    need(
        all(
            type(value[key]) is type(item) and value[key] == item
            for key, item in expected.items()
        )
    )
    need(tuple(row["phase"] for row in value["journal_sha256"]) == _PHASES)
    route = request["route"]
    parent, accepted = value["private_parent_operation"], value["native_result"]
    need(
        parent["origin"] == custody._Origin.STOPPED55_NEGATIVE.value
        and parent["execution_authority"] is False
        and parent["source_archive_sha256"] == _SOURCE_ARCHIVE_SHA256
        and parent["cold_manifest_sha256"] == _PREDECESSOR_INVENTORY_SHA256
        and parent["system_identifier_sha256"]
        == route["cluster_system_identifier_sha256"]
        == _SYSTEM_SHA256
        and parent["owner_oid"] == route["owner_oid"] == 10
        and parent["database_oid"] == route["database_oid"]
        and parent["operation_id"] == accepted["operation_id"].replace("-", "")
        and accepted["status"] == custody._STOPPED55_NEGATIVE_STATUS
        and accepted["after_snapshot_sha256"] == _AFTER_SHA256
        and accepted["control_revision"]
        == accepted["commit_attempts"]
        == accepted["new_journal_rows"]
        == 1
        and accepted["independent_readbacks"] == 2
        and accepted["execution_authority"]
        is accepted["account_complete"]
        is accepted["armed"]
        is False
        and accepted["exchange_flat"] is None
        and accepted["emergency_stop"] is accepted["restart_latch_required"] is True
    )
    parsed = custody._cold_tree_entries_v1(value, origin_sha256=INVENTORY_SHA256)
    need(parsed[0]["global/pg_control"][1] == _CONTROL_SHA256)
    return value, parsed


def require_reference_data():
    return fitted_reference.read_reviewed_bootstrap_fitted_data_v2()


def require_result(result):
    reference = require_reference_data()
    expected = {
        **_data(reference.reference_pair_sha256),
        "stopped_inventory_sha256": INVENTORY_SHA256,
        "native_predecessor_acceptance_sha256": _ACCEPTANCE_SHA256,
        "stopped_source_unchanged_verified": True,
        "distinct_working_copy_verified": True,
        "owned_server_fast_shutdown_verified": True,
        "commit_attempts": 0,
    }
    custody._need(type(result) is dict and set(result) == set(expected))
    custody._need(
        all(
            type(result[key]) is type(value) and result[key] == value
            for key, value in expected.items()
        )
    )


async def _read_one(lease, engine, pins, sealed):
    async with engine.connect() as connection:
        phase = negative._Phase.READ
        transaction = await negative._begin(connection, pins, phase)
        try:
            await custody._replay_owned_connection_v1(lease, connection)
            row = tuple((await connection.execute(_SESSION)).one())
            custody._need(row[:3] == ("on", "on", "repeatable read"))
            custody._need(type(row[3]) is int and row[3] > 0)
            state = await negative._observe(connection, pins, phase)
            negative._control(state["control"], revision=1)
            custody._need(negative._digest(state) == _AFTER_SHA256)
            accepted = sealed["native_result"]
            custody._need(
                await connection.scalar(
                    _AUDIT,
                    {
                        "action": negative.ACTION,
                        "operation_id": accepted["operation_id"],
                        "before_sha256": accepted["before_snapshot_sha256"],
                    },
                )
                is True
            )
            await custody._replay_owned_connection_v1(lease, connection)
            return row[3], state
        finally:
            await transaction.rollback()


async def observe_owned_stop1(lease, engine, pins, source_archive, sealed):
    """Two uncached owner readbacks without transaction write authority."""
    custody._guard()
    record = custody._record(lease)
    custody._need(
        record.origin is custody._Origin.STOPPED55_STOP1_PREFLIGHT
        and record.phase is custody._Phase.READY
        and record.commit_attempts == 0
        and record.preserved_apply_confirmed is False
        and type(engine) is AsyncEngine
        and type(engine.pool) is NullPool
        and record.engine is engine
        and record.pins is pins
        and record.cold_manifest_sha256 == INVENTORY_SHA256
        and record.maintenance_lock_profile_sha256
        == sealed["private_parent_operation"]["maintenance_lock_profile_sha256"]
    )
    reference = require_reference_data()
    negative._source(pins, source_archive)
    first_pid, first = await _read_one(lease, engine, pins, sealed)
    second_pid, second = await _read_one(lease, engine, pins, sealed)
    custody._need(first_pid != second_pid and first == second)
    negative._source(pins, source_archive)
    custody._need(require_reference_data() == reference)
    return _data(reference.reference_pair_sha256)


def _data(reference_pair_sha256):
    return {
        "schema": RESULT_SCHEMA,
        "status": STATUS,
        "database_head": "0055",
        "control_revision": 1,
        "independent_readbacks": 2,
        "snapshot_sha256": _AFTER_SHA256,
        "reference_pair_sha256": reference_pair_sha256,
        "mutation_performed": False,
        "schema_upgrade_authorized": False,
        "startup_admission": False,
        "execution_authority": False,
        "exchange_flat": None,
        "account_complete": False,
    }
