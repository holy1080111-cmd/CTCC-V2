"""Validate a Git archive in new, credential-free, isolated Docker services.

Never reads a host .env or uses the deployment's Compose project or volumes.
The archive's actual Git tree is reconstructed before building. Results identify
this run only; a green result is not trading, OOS or production acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import stat
import subprocess
import tarfile
import threading
import time
from collections.abc import Iterable, Sequence
from copy import deepcopy
from pathlib import Path, PurePosixPath
from uuid import uuid4

from scripts.evidence_json import read_evidence_json
from scripts.verify_linux_shard import POSTGRES_TEST_PATHS, SHARD_COUNT, selected_for
from scripts.verify_native_witness_identity_schema import (
    NATIVE_WITNESS_IDENTITY_SCHEMA,
    NATIVE_WITNESS_IDENTITY_SCHEMA_SQL,  # noqa: F401 -- probe/API re-export
    NATIVE_WITNESS_OBJECT_CUSTODY_SQL,  # noqa: F401 -- probe/API re-export
    NATIVE_WITNESS_RETENTION_SNAPSHOT_SQL,  # noqa: F401 -- probe/API re-export
    expected_legacy_witness_identity_schema,
    require_native_witness_identity_baseline,
)

# The two full PostgreSQL-bearing test runs can exceed 90 minutes together.
# Leave time for bounded cleanup and `if: always()` evidence upload before the
# 180-minute GitHub job cutoff; a budget failure is never a validation pass.
VALIDATION_BUDGET_SECONDS = 165 * 60
CLEANUP_BUDGET_SECONDS = 5 * 60
HEARTBEAT_INTERVAL_SECONDS = 60
DOWNGRADE_DENIAL = "submission_transport_authenticity_downgrade_denied"
NATIVE_IDENTITY_DOWNGRADE_DENIAL = (
    "public_witness_wide_identity_downgrade_requires_empty"
)
COVERAGE_DOWNGRADE_DENIAL = "controlled_writer_coverage_downgrade_requires_empty"
PREDECESSOR_0047_CASES = (
    (
        "tests.integration.test_public_receipt_witness_repository",
        "test_restricted_function_only_role_and_monotonic_cas",
    ),
    (
        "tests.integration.test_public_receipt_witness_repository",
        "test_native_identity_round_trips_without_signed_conversion[full-native-identity-width]",
    ),
)
PRIVATE_ARCHIVE_SUFFIXES = frozenset(
    {".token", ".key", ".pem", ".p12", ".pfx", ".kdbx", ".jks", ".keystore"}
)
PRIVATE_ARCHIVE_NAMES = frozenset(
    {
        ".npmrc",
        ".pypirc",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "credentials.json",
        "secrets.json",
        "secret.json",
        "token.json",
        "api-key.json",
        "apikey.json",
    }
)
PRIVATE_ARCHIVE_DIRECTORIES = frozenset(
    {
        ".git",
        "reports",
        "backups",
        ".venv",
        "private-notion",
        "private",
        "secrets",
        "credentials",
    }
)
PRIVATE_ARCHIVE_BACKUP_SUFFIXES = (".bak", ".backup", ".old", ".orig", ".tmp", "~")
# The exact 0b28282 PostgreSQL shard reached 84% before the 75-minute stage
# deadline killed pytest without a JUnit report. Keep a bounded stage deadline
# inside the 165-minute validation and 180-minute CI job budgets.
POSTGRES_TEST_TIMEOUT_SECONDS = 120 * 60
FULL_TEST_TIMEOUT_SECONDS = 100 * 60

ARCHIVE_REQUIRED_CASES = (
    (
        "tests.integration.test_controlled_writer_coverage_repository",
        "test_function_only_append_read_and_restricted_table_denial",
    ),
    (
        "tests.integration.test_controlled_writer_coverage_repository",
        "test_nonempty_0048_downgrade_refuses_and_retains_exact_catalog",
    ),
    (
        "tests.integration.test_controlled_writer_coverage_repository",
        "test_empty_0048_downgrade_and_reupgrade",
    ),
    (
        "tests.integration.test_controlled_writer_coverage_repository",
        "test_catalog_capture_retains_pending_native_baseline",
    ),
    (
        "tests.integration.test_account_portfolio_runtime_v3_postgres",
        "test_v3_real_postgresql_same_invocation_commits_and_replays_deny_only",
    ),
    (
        "tests.integration.test_account_portfolio_runtime_v3_postgres",
        "test_v3_late_v1_fold_failure_preserves_committed_b1_b3_b5_after_restart",
    ),
    (
        "tests.integration.test_measured_hwm_v2_catch_up_postgres",
        "test_b3_only_scope_catches_up_from_original_genesis_in_bounded_pages",
    ),
    (
        "tests.integration.test_measured_hwm_v2_postgres",
        "test_b5_direct_sql_forged_nonpeak_equity_cannot_enter_chain",
    ),
    (
        "tests.integration.test_measured_hwm_v2_postgres",
        "test_b5_direct_sql_rejects_rehashed_noncanonical_or_extra_fields[whitespace]",
    ),
    (
        "tests.integration.test_measured_hwm_v2_postgres",
        "test_b5_direct_sql_rejects_rehashed_noncanonical_or_extra_fields[projection_extra]",
    ),
    (
        "tests.integration.test_measured_hwm_v2_postgres",
        "test_b5_nonempty_downgrade_denies_and_rolls_back_exact_catalog",
    ),
    (
        "tests.integration.test_account_bill_archive_claim_repository",
        "test_claim_is_one_across_sessions_and_survives_repository_restart",
    ),
    (
        "tests.integration.test_account_bill_archive_claim_repository",
        "test_future_host_clock_denies_readback_but_preserves_one_attempt_claim",
    ),
    (
        "tests.integration.test_durable_migration_downgrade",
        "test_nonempty_downgrade_retains_exact_durable_records[0022-archive_claim]",
    ),
    (
        "tests.integration.test_durable_migration_downgrade",
        "test_uid_event_upgrade_refuses_legacy_cross_currency_collision[0022]",
    ),
    (
        "tests.integration.test_durable_migration_downgrade",
        "test_uid_event_upgrade_downgrade_and_truncate_guards_are_atomic[0022]",
    ),
)

GATE3_PREFLIGHT_CASES = (
    (
        "tests.integration.test_gate3_canonical_schedule_claim",
        "test_poisoned_raw_keys_do_not_block_canonical_retry_or_claim_ack",
    ),
    (
        "tests.integration.test_gate3_canonical_schedule_claim",
        "test_concurrent_canonical_schedules_get_at_most_one_claim",
    ),
    (
        "tests.integration.test_gate3_claim_trigger_alias_repair",
        "test_0032_repairs_existing_guard_without_qualifying_legacy_evidence[noncanonical]",
    ),
    (
        "tests.integration.test_gate3_claim_trigger_alias_repair",
        "test_0032_repaired_guard_accepts_fresh_prereg_acknowledged_claim",
    ),
)

LEDGER_SCHEMA_PREFLIGHT_CASES = (
    (
        "tests.integration.test_complete_consumed_event_ledger_repository",
        "test_committed_event_journal_schema_retention_guard_accepts_current_head",
    ),
)

RUNTIME_CUSTODY_PREFLIGHT_CASES = (
    (
        "tests.integration.test_public_runtime_bundle_custody_repository",
        "test_migrated_restricted_role_commit_then_new_transaction_exact_readback",
    ),
    (
        "tests.integration.test_public_runtime_bundle_custody_repository",
        "test_privileged_migration_login_and_missing_preflight_rejected",
    ),
    (
        "tests.integration.test_public_runtime_bundle_custody_repository",
        "test_committed_append_with_unknown_readback_never_retries_or_issues_receipt",
    ),
    (
        "tests.integration.test_public_runtime_bundle_custody_repository",
        "test_direct_table_read_mutation_and_schema_changes_denied",
    ),
)

ACCOUNT_REVISION_WITNESS_TRIGGER_CATALOG_SQL = """
SELECT tgname, tgenabled::text, tgtype::integer,
       CASE tgname
         WHEN 'account_revision_source_witness_guard'
           THEN tgfoid = pg_catalog.to_regprocedure('public.account_revision_source_witness_guard()')
         WHEN 'account_revision_source_witness_immutable'
           THEN tgfoid = pg_catalog.to_regprocedure('public.qualification_ledger_immutable()')
         WHEN 'account_revision_source_witness_no_truncate'
           THEN tgfoid = pg_catalog.to_regprocedure('public.qualification_ledger_immutable()')
         ELSE false
       END AS function_oid_matches,
       tgqual IS NULL AS when_clause_absent,
       tgattr::text = '' AS column_filter_absent,
       encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex') AS function_body_sha256
FROM pg_catalog.pg_trigger AS t
JOIN pg_catalog.pg_proc AS p ON p.oid = t.tgfoid
WHERE tgrelid=pg_catalog.to_regclass('public.demo_account_revision_source_witnesses')
  AND NOT tgisinternal
ORDER BY tgname
"""

ACCOUNT_REVISION_WITNESS_TRIGGERS = (
    (
        "account_revision_source_witness_guard",
        "O",
        7,
        True,
        True,
        True,
        "6be587260ac8375424aa5a986170664752e3778ab5a84f5309d95c5da99e6312",
    ),
    (
        "account_revision_source_witness_immutable",
        "O",
        27,
        True,
        True,
        True,
        "14fe6a29f274ba10141a2ecdfbc40e4dc6e4014421f338f76a39352670e48649",
    ),
    (
        "account_revision_source_witness_no_truncate",
        "O",
        34,
        True,
        True,
        True,
        "14fe6a29f274ba10141a2ecdfbc40e4dc6e4014421f338f76a39352670e48649",
    ),
)

ACCOUNT_MEASURED_HWM_V2_TRIGGER_CATALOG_SQL = """
SELECT tgname, tgenabled::text, tgtype::integer,
       CASE tgname
         WHEN 'account_measured_hwm_v2_guard'
           THEN tgfoid = pg_catalog.to_regprocedure('public.account_measured_hwm_v2_guard()')
         WHEN 'account_measured_hwm_v2_immutable'
           THEN tgfoid = pg_catalog.to_regprocedure('public.qualification_ledger_immutable()')
         WHEN 'account_measured_hwm_v2_no_truncate'
           THEN tgfoid = pg_catalog.to_regprocedure('public.qualification_ledger_immutable()')
         ELSE false
       END AS function_oid_matches,
       tgqual IS NULL AS when_clause_absent,
       tgattr::text = '' AS column_filter_absent,
       encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex') AS function_body_sha256
FROM pg_catalog.pg_trigger AS t
JOIN pg_catalog.pg_proc AS p ON p.oid = t.tgfoid
WHERE tgrelid=pg_catalog.to_regclass('public.demo_account_measured_hwm_v2')
  AND NOT tgisinternal
ORDER BY tgname
"""

ACCOUNT_MEASURED_HWM_V2_TRIGGERS = (
    (
        "account_measured_hwm_v2_guard",
        "O",
        7,
        True,
        True,
        True,
        "f489bd66b20bee39e630b6a6e50643c7c5f962a4c7613070c7e50bab53ce0aaa",
    ),
    (
        "account_measured_hwm_v2_immutable",
        "O",
        27,
        True,
        True,
        True,
        "14fe6a29f274ba10141a2ecdfbc40e4dc6e4014421f338f76a39352670e48649",
    ),
    (
        "account_measured_hwm_v2_no_truncate",
        "O",
        34,
        True,
        True,
        True,
        "14fe6a29f274ba10141a2ecdfbc40e4dc6e4014421f338f76a39352670e48649",
    ),
)

# DB0045 replaces only the reviewed guard body. DB0044's catalog pin remains
# separate so a downgrade/readback cannot silently reinterpret old history.
ACCOUNT_MEASURED_HWM_V2_TRIGGERS_0045 = (
    (
        "account_measured_hwm_v2_guard",
        "O",
        7,
        True,
        True,
        True,
        "707f66529bfd183f480d6124e87d11e006a76513c2d8fae983988e11310de8f9",
    ),
    *ACCOUNT_MEASURED_HWM_V2_TRIGGERS[1:],
)

PUBLIC_RUNTIME_BUNDLE_SCHEMA_SQL = """
SELECT jsonb_build_object(
 'table_present',pg_catalog.to_regclass('public.public_runtime_raw_bundles_v1') IS NOT NULL,
 'table_shape',COALESCE((SELECT relkind='r' AND relpersistence='p'
   AND NOT relrowsecurity AND NOT relforcerowsecurity FROM pg_catalog.pg_class
   WHERE oid=pg_catalog.to_regclass('public.public_runtime_raw_bundles_v1')),false),
 'columns',COALESCE((SELECT jsonb_agg(jsonb_build_array(attname,
   pg_catalog.format_type(atttypid,atttypmod),attnotnull,
   attidentity='',attgenerated='') ORDER BY attnum)
   FROM pg_catalog.pg_attribute WHERE attrelid=pg_catalog.to_regclass(
   'public.public_runtime_raw_bundles_v1') AND attnum>0 AND NOT attisdropped),'[]'::jsonb),
 'functions',COALESCE((SELECT jsonb_agg(jsonb_build_array(proname,
   pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(prosrc,'UTF8')),'hex'),
   prosecdef,proretset,pg_catalog.oidvectortypes(proargtypes),provolatile::text,
   proconfig=ARRAY['search_path=pg_catalog, public'],
   proowner=(SELECT relowner FROM pg_catalog.pg_class WHERE oid=
     pg_catalog.to_regclass('public.public_runtime_raw_bundles_v1')),
   NOT proisstrict AND proparallel='u' AND NOT proleakproof
     AND prokind='f' AND prosupport=0
     AND (SELECT lanname FROM pg_catalog.pg_language WHERE oid=prolang)=
       CASE WHEN proname='public_runtime_bundle_v1_read' THEN 'sql' ELSE 'plpgsql' END
     AND prorettype=CASE proname
       WHEN 'public_runtime_bundle_v1_append' THEN pg_catalog.to_regtype('void')
       WHEN 'public_runtime_bundle_v1_read' THEN
         pg_catalog.to_regtype('public.public_runtime_raw_bundles_v1')
       ELSE pg_catalog.to_regtype('trigger') END,
   NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(proacl,
     pg_catalog.acldefault('f',proowner))) a
     WHERE a.grantee=0 AND a.privilege_type='EXECUTE')) ORDER BY proname)
   FROM pg_catalog.pg_proc WHERE pronamespace='public'::regnamespace
   AND proname LIKE 'public_runtime_bundle_v1_%'),'[]'::jsonb),
 'triggers',COALESCE((SELECT jsonb_agg(jsonb_build_array(tgname,tgenabled::text,
   tgtype::integer,p.proname,tgqual IS NULL AND tgconstraint=0 AND tgnargs=0
   AND pg_catalog.octet_length(tgargs)=0 AND tgattr::text=''
   AND tgoldtable IS NULL AND tgnewtable IS NULL
   AND p.pronamespace='public'::regnamespace) ORDER BY tgname)
   FROM pg_catalog.pg_trigger g JOIN pg_catalog.pg_proc p ON p.oid=g.tgfoid
   WHERE tgrelid=pg_catalog.to_regclass('public.public_runtime_raw_bundles_v1')
   AND NOT tgisinternal),'[]'::jsonb),
 'constraints',COALESCE((SELECT jsonb_agg(jsonb_build_array(conname,contype::text,
   convalidated,condeferrable,condeferred,
   pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
     pg_catalog.pg_get_constraintdef(oid,true),'UTF8')),'hex')) ORDER BY conname)
   FROM pg_catalog.pg_constraint WHERE conrelid=pg_catalog.to_regclass(
   'public.public_runtime_raw_bundles_v1')),'[]'::jsonb))
"""

# Exact PG17.11 deparser/body hashes from reviewed DB0046 probe run39647583880.
# This is current raw retention only, never historical/PIT or execution proof.
PUBLIC_RUNTIME_BUNDLE_SCHEMA = {
    "table_present": True,
    "table_shape": True,
    "columns": [
        ["bundle_id", "character varying(32)", True, True, True],
        ["bundle_sha256", "character varying(64)", True, True, True],
        ["proof_envelope_sha256", "character varying(64)", True, True, True],
        ["bundle_raw", "bytea", True, True, True],
        ["contract_version", "integer", True, True, True],
        ["recorded_at", "timestamp with time zone", True, True, True],
    ],
    "functions": [
        [
            "public_runtime_bundle_v1_append",
            "d5d1586f2d4e2a5292981179a8c29710cf5f86a39ed2ec5ab3fb12ca8d2179a3",
            True,
            False,
            "text, text, text, bytea",
            "v",
            True,
            True,
            True,
            True,
        ],
        [
            "public_runtime_bundle_v1_guard",
            "13e77635eb0518e5db1da94a077e8930616bd7972be6069a9ddfb5f02a3e82e6",
            False,
            False,
            "",
            "v",
            True,
            True,
            True,
            True,
        ],
        [
            "public_runtime_bundle_v1_immutable",
            "1daea630a98adaed8e61ac18a428189e9d216413e52b6fe5588dd35690872635",
            False,
            False,
            "",
            "v",
            True,
            True,
            True,
            True,
        ],
        [
            "public_runtime_bundle_v1_read",
            "9dbc4008637b6a6092f893c347d60dc8608a6678db860f37daf6d1782de4705f",
            True,
            True,
            "text",
            "s",
            True,
            True,
            True,
            True,
        ],
    ],
    "triggers": [
        [
            "public_runtime_bundle_v1_immutable",
            "A",
            27,
            "public_runtime_bundle_v1_immutable",
            True,
        ],
        [
            "public_runtime_bundle_v1_insert_guard",
            "A",
            7,
            "public_runtime_bundle_v1_guard",
            True,
        ],
        [
            "public_runtime_bundle_v1_no_truncate",
            "A",
            34,
            "public_runtime_bundle_v1_immutable",
            True,
        ],
    ],
    "constraints": [
        [
            "ck_public_runtime_raw_bundles_v1_content",
            "c",
            True,
            False,
            False,
            "e09c32d8d1c64fa7bd867325b89c8fac0f7b050cd71d37649ab45fff2e17a542",
        ],
        [
            "ck_public_runtime_raw_bundles_v1_identity",
            "c",
            True,
            False,
            False,
            "31242ca55a04c56bb824865996239bd8064e4200c740e2e689ae882dd8c679c8",
        ],
        [
            "pk_public_runtime_raw_bundles_v1",
            "p",
            True,
            False,
            False,
            "6149072230c4daef7add37e0bf6fc793f7583e94d95f12627020d214f1c4cb7d",
        ],
        [
            "uq_public_runtime_raw_bundles_v1_bundle_sha256",
            "u",
            True,
            False,
            False,
            "53750f27830362606cde6e5e46ebc11cbb15acbd176c48c3543466bc87fe88e6",
        ],
    ],
}
PUBLIC_RUNTIME_BUNDLE_SCHEMA_ABSENT = {
    "table_present": False,
    "table_shape": False,
    "columns": [],
    "functions": [],
    "triggers": [],
    "constraints": [],
}

MIGRATION_REVISION_READER = """
import asyncio, json, sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from app.config.settings import get_settings
from scripts.verify_final_hermetic import (
    ACCOUNT_REVISION_WITNESS_TRIGGER_CATALOG_SQL,
    ACCOUNT_MEASURED_HWM_V2_TRIGGER_CATALOG_SQL,
    PUBLIC_RUNTIME_BUNDLE_SCHEMA_SQL,
    NATIVE_WITNESS_IDENTITY_SCHEMA_SQL,
    migration_revision_readback,
    native_identity_empty_downgrade_readback,
    native_identity_empty_before_readback,
    NATIVE_WITNESS_OBJECT_CUSTODY_SQL,
    NATIVE_WITNESS_RETENTION_SNAPSHOT_SQL,
    native_identity_nonempty_snapshot,
)
from scripts.verify_migration_identity import database_head
from scripts.verify_controlled_writer_coverage_schema_v1 import read_catalog

async def read_head(*, retain=False, coverage_retain=False):
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="REPEATABLE READ")
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            def read_state(sync_connection):
                head = database_head(sync_connection)
                witness_present = sync_connection.execute(text(
                    "SELECT pg_catalog.to_regclass('public.demo_account_revision_source_witnesses') IS NOT NULL"
                )).scalar_one()
                guard_present = sync_connection.execute(text(
                    "SELECT pg_catalog.to_regprocedure('public.account_revision_source_witness_guard()') IS NOT NULL"
                )).scalar_one()
                trigger_catalog = sync_connection.execute(text(
                    ACCOUNT_REVISION_WITNESS_TRIGGER_CATALOG_SQL
                )).all()
                hwm_present = sync_connection.execute(text(
                    "SELECT pg_catalog.to_regclass('public.demo_account_measured_hwm_v2') IS NOT NULL"
                )).scalar_one()
                hwm_guard_present = sync_connection.execute(text(
                    "SELECT pg_catalog.to_regprocedure('public.account_measured_hwm_v2_guard()') IS NOT NULL"
                )).scalar_one()
                hwm_catalog = sync_connection.execute(text(
                    ACCOUNT_MEASURED_HWM_V2_TRIGGER_CATALOG_SQL
                )).all()
                runtime_catalog = sync_connection.execute(text(
                    PUBLIC_RUNTIME_BUNDLE_SCHEMA_SQL
                )).scalar_one()
                native_catalog = sync_connection.execute(text(
                    NATIVE_WITNESS_IDENTITY_SCHEMA_SQL
                )).scalar_one()
                custody = sync_connection.execute(text(
                    NATIVE_WITNESS_OBJECT_CUSTODY_SQL
                )).scalar_one()
                retention = sync_connection.execute(text(
                    NATIVE_WITNESS_RETENTION_SNAPSHOT_SQL
                )).scalar_one() if retain else None
                return (head, witness_present, guard_present, trigger_catalog,
                        hwm_present, hwm_guard_present, hwm_catalog, runtime_catalog, native_catalog, retention, custody)
            observed = await connection.run_sync(read_state)
            coverage = await read_catalog(connection) if observed[0] == '0048' else None
            if coverage_retain:
                from app.database.repositories.controlled_writer_coverage import read_controlled_writer_coverage_retention
                coverage_rows = await read_controlled_writer_coverage_retention(connection)
            else:
                coverage_rows = None
            return (*observed, coverage, coverage_rows)
    finally:
        await engine.dispose()

"""

MIGRATION_REVISION_PROBE = (
    MIGRATION_REVISION_READER
    + """
observed, witness_present, guard_present, trigger_catalog, hwm_present, hwm_guard_present, hwm_catalog, runtime_catalog, native_catalog, _, custody, coverage, _coverage_rows = asyncio.run(read_head())
expected = sys.argv[1]
result = migration_revision_readback(
    expected, observed, witness_present, guard_present, trigger_catalog,
    hwm_present, hwm_guard_present, hwm_catalog, runtime_catalog, native_catalog,
    coverage_catalog=coverage,
)
if len(sys.argv) == 3:
    if sys.argv[2] == 'native-identity-0047-empty-down' and expected == '0046':
        result = native_identity_empty_downgrade_readback(result, native_catalog, custody)
    elif sys.argv[2] == 'native-identity-0047-empty-before' and expected == '0047':
        result = native_identity_empty_before_readback(result, custody)
    else:
        raise RuntimeError('native_identity_down_probe_scope_invalid')
print(json.dumps(result, sort_keys=True))
"""
)

NATIVE_IDENTITY_RETENTION_PROBE = (
    MIGRATION_REVISION_READER
    + """
observed, witness_present, guard_present, trigger_catalog, hwm_present, hwm_guard_present, hwm_catalog, runtime_catalog, native_catalog, retention, _, _coverage, _coverage_rows = asyncio.run(read_head(retain=True))
revision = migration_revision_readback(
    '0047', observed, witness_present, guard_present, trigger_catalog,
    hwm_present, hwm_guard_present, hwm_catalog, runtime_catalog, native_catalog,
)
result = native_identity_nonempty_snapshot(revision, retention)
print(json.dumps(result, sort_keys=True))
"""
)

# Capture runs before the guarded current-head reader. Pending native pins do
# not prevent preserving the server's actual deparser/catalog observation.
COVERAGE_NATIVE_CATALOG_CAPTURE = """
import asyncio, hashlib, json, os
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from app.config.settings import get_settings
from scripts.verify_controlled_writer_coverage_schema_v1 import read_catalog
from scripts.verify_migration_identity import database_head, source_identity, SOURCE_ROOT
async def capture():
    before = source_identity(SOURCE_ROOT)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            connection = await connection.execution_options(isolation_level='REPEATABLE READ')
            await connection.execute(text('SET TRANSACTION READ ONLY'))
            head = await connection.run_sync(database_head)
            facts = (await connection.execute(text(
                "SELECT current_setting('server_version'), current_setting('server_version_num'), current_user, session_user, current_database(), current_schema()"
            ))).one()
            catalog = await read_catalog(connection)
        after = source_identity(SOURCE_ROOT)
        if before != after or before.head != '0048' or head != '0048':
            raise RuntimeError('coverage_capture_source_or_database_identity_changed')
        result = {'schema':'ctcc.controlled_writer_coverage_native_catalog_observation.v1',
                  'source_head':before.head, 'source_migration_sha256':before.sha256,
                  'database_head':head, 'session_facts':list(facts), 'catalog':catalog,
                  'native_baseline_admission':False, 'execution_authority':False}
        raw = (json.dumps(result,sort_keys=True,allow_nan=False,separators=(',',':'))+'\\n').encode()
        if not 0 < len(raw) <= 2*1024*1024:
            raise RuntimeError('coverage_native_catalog_capture_size_invalid')
        path = Path('/validation-results/coverage-native-catalog.json')
        with path.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if path.read_bytes() != raw:
            raise RuntimeError('coverage_native_catalog_capture_readback_failed')
        print(json.dumps({'artifact':'test-results/coverage-native-catalog.json',
                          'sha256':hashlib.sha256(raw).hexdigest(),
                          'native_baseline_admission':False,'execution_authority':False},sort_keys=True))
    finally:
        await engine.dispose()
asyncio.run(capture())
"""

COVERAGE_ABSENT_PROBE = """
import asyncio,json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from app.config.settings import get_settings
from scripts.verify_migration_identity import database_head
from app.database.repositories.controlled_writer_coverage import COVERAGE_TABLES, COVERAGE_FUNCTION_SQL
async def inspect():
    engine=create_async_engine(get_settings().database_url,poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            head=await connection.run_sync(database_head)
            tables=[await connection.scalar(text('SELECT pg_catalog.to_regclass(:name) IS NOT NULL'),{'name':'public.'+name}) for name in COVERAGE_TABLES]
            functions=(await connection.execute(text("SELECT p.proname FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname LIKE 'controlled_writer_coverage_v1_%' ORDER BY p.proname"))).scalars().all()
        result={'schema':'ctcc.controlled_writer_coverage_absent.v1','database_head':head,'tables_present':tables,'functions_present':functions,'execution_authority':False}
        if head!='0047' or tables != [False,False] or functions:
            raise RuntimeError('coverage_empty_downgrade_absence_unverified')
        print(json.dumps(result,sort_keys=True))
    finally:
        await engine.dispose()
asyncio.run(inspect())
"""

COVERAGE_RETENTION_PROBE = (
    MIGRATION_REVISION_READER
    + """
observed,witness,guard,triggers,hwm,hwm_guard,hwm_triggers,runtime,native,_,_,coverage,retention=asyncio.run(read_head(coverage_retain=True))
revision=migration_revision_readback('0048',observed,witness,guard,triggers,hwm,hwm_guard,hwm_triggers,runtime,native,coverage_catalog=coverage)
from scripts.verify_final_hermetic import coverage_nonempty_snapshot
print(json.dumps(coverage_nonempty_snapshot(revision,retention),sort_keys=True))
"""
)

PREDECESSOR_0047_IDENTITY_PROBE = """
import asyncio,json
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from app.config.settings import get_settings
from scripts.verify_migration_identity import source_identity,database_head,SOURCE_ROOT
async def inspect():
    before=source_identity(SOURCE_ROOT)
    config=Config(str(SOURCE_ROOT/'alembic.ini'))
    config.set_main_option('script_location',str(SOURCE_ROOT/'migrations'))
    graph=ScriptDirectory.from_config(config)
    if before.head!='0048' or graph.get_revision('0048').down_revision!='0047':
        raise RuntimeError('predecessor_source_graph_mismatch')
    engine=create_async_engine(get_settings().database_url,poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            observed=await connection.run_sync(database_head)
        if observed!='0047' or before!=source_identity(SOURCE_ROOT):
            raise RuntimeError('predecessor_database_identity_mismatch')
        print(json.dumps({'schema':'ctcc.migration_predecessor_identity.v1','status':'PASS',
            'source_head':before.head,'database_head':observed,'target_revision':'0047',
            'migration_sha256':before.sha256,'execution_authority':False},sort_keys=True))
    finally:
        await engine.dispose()
asyncio.run(inspect())
"""


def expected_migration_revision_readback(revision: str) -> dict:
    if revision == "0048":
        from app.database.repositories.controlled_writer_coverage import (
            expected_controlled_writer_coverage_schema,
        )

        return {
            **expected_migration_revision_readback("0047"),
            "schema": "ctcc.migration_revision_readback.v5",
            "expected": "0048",
            "observed": "0048",
            "controlled_writer_coverage_schema": expected_controlled_writer_coverage_schema(),
        }
    result = {
        "schema": "ctcc.migration_revision_readback.v4"
        if revision == "0047"
        else "ctcc.migration_revision_readback.v3"
        if revision == "0046"
        else "ctcc.migration_revision_readback.v2",
        "expected": revision,
        "observed": revision,
        "execution_authority": False,
    }
    if revision in ("0001", "0042", "0043", "0044", "0045", "0046", "0047"):
        witness_expected = revision in ("0043", "0044", "0045", "0046", "0047")
        result["account_revision_witness_table_present"] = witness_expected
        result["account_revision_witness_guard_present"] = witness_expected
        result["account_revision_witness_trigger_count"] = 3 if witness_expected else 0
        result["account_revision_witness_trigger_catalog"] = (
            [list(row) for row in ACCOUNT_REVISION_WITNESS_TRIGGERS]
            if witness_expected
            else []
        )
        hwm_expected = revision in ("0044", "0045", "0046", "0047")
        result["account_measured_hwm_v2_table_present"] = hwm_expected
        result["account_measured_hwm_v2_guard_present"] = hwm_expected
        result["account_measured_hwm_v2_trigger_count"] = 3 if hwm_expected else 0
        result["account_measured_hwm_v2_trigger_catalog"] = (
            [
                list(row)
                for row in (
                    ACCOUNT_MEASURED_HWM_V2_TRIGGERS_0045
                    if revision in ("0045", "0046", "0047")
                    else ACCOUNT_MEASURED_HWM_V2_TRIGGERS
                )
            ]
            if hwm_expected
            else []
        )
    if revision in ("0046", "0047"):
        result["public_runtime_bundle_schema"] = deepcopy(PUBLIC_RUNTIME_BUNDLE_SCHEMA)
    if revision == "0047":
        result["public_witness_native_identity_schema"] = deepcopy(
            NATIVE_WITNESS_IDENTITY_SCHEMA
        )
    return result


def _require_native_object_digest(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise RuntimeError("native_identity_object_custody_digest_invalid")


def expected_coverage_absent_readback() -> dict:
    return {
        "schema": "ctcc.controlled_writer_coverage_absent.v1",
        "database_head": "0047",
        "tables_present": [False, False],
        "functions_present": [],
        "execution_authority": False,
    }


def verify_coverage_native_catalog_capture(
    document: dict, *, migration_sha256: str
) -> dict:
    """Require separately retained observed catalog and a sealed native policy."""
    from scripts.verify_controlled_writer_coverage_schema_v1 import verify_catalog

    if (
        type(document) is not dict
        or set(document)
        != {
            "schema",
            "source_head",
            "source_migration_sha256",
            "database_head",
            "session_facts",
            "catalog",
            "native_baseline_admission",
            "execution_authority",
        }
        or document.get("schema")
        != "ctcc.controlled_writer_coverage_native_catalog_observation.v1"
        or document.get("source_head") != "0048"
        or document.get("database_head") != "0048"
        or document.get("source_migration_sha256") != migration_sha256
        or document.get("native_baseline_admission") is not False
        or document.get("execution_authority") is not False
        or type(document.get("session_facts")) is not list
        or len(document["session_facts"]) != 6
        or any(
            type(value) is not str or not value for value in document["session_facts"]
        )
        or not document["session_facts"][1].isdigit()
        or not 170000 <= int(document["session_facts"][1]) < 180000
        or document["session_facts"][-1] != "public"
    ):
        raise ValueError("coverage_native_catalog_observation_invalid")
    return verify_catalog(document["catalog"], require_native_baseline=True)


def coverage_nonempty_snapshot(revision: dict, retention: dict) -> dict:
    from app.database.repositories.controlled_writer_coverage import COVERAGE_TABLES

    if (
        not native_migration_json_equal(
            revision, expected_migration_revision_readback("0048")
        )
        or type(retention) is not dict
        or set(retention) != {"schema_version", "rows", "counts", "rows_sha256"}
        or retention.get("schema_version")
        != "ctcc.controlled_writer_coverage_retention.v1"
        or type(retention.get("rows")) is not dict
        or type(retention.get("counts")) is not dict
        or set(retention["rows"]) != set(COVERAGE_TABLES)
        or set(retention["counts"]) != set(COVERAGE_TABLES)
    ):
        raise RuntimeError("coverage_nonempty_snapshot_invalid")
    for table in COVERAGE_TABLES:
        count, rows = retention["counts"][table], retention["rows"][table]
        if (
            type(count) is not int
            or not 1 <= count <= 8192
            or type(rows) is not list
            or len(rows) != count
            or any(type(row) is not dict for row in rows)
        ):
            raise RuntimeError("coverage_nonempty_snapshot_invalid")
    raw = json.dumps(
        retention["rows"], sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode("ascii")
    if (
        len(raw) > 2 * 1024 * 1024
        or hashlib.sha256(raw).hexdigest() != retention["rows_sha256"]
    ):
        raise RuntimeError("coverage_nonempty_snapshot_invalid")
    return {
        "schema": "ctcc.controlled_writer_coverage_db0048_nonempty_snapshot.v1",
        "revision_readback": deepcopy(revision),
        "retention": deepcopy(retention),
        "execution_authority": False,
    }


def coverage_nonempty_downgrade_readback(before: dict, after: dict) -> dict:
    for observed in (before, after):
        if (
            type(observed) is not dict
            or set(observed)
            != {"schema", "revision_readback", "retention", "execution_authority"}
            or not native_migration_json_equal(
                observed,
                coverage_nonempty_snapshot(
                    observed.get("revision_readback"), observed.get("retention")
                ),
            )
        ):
            raise RuntimeError("coverage_nonempty_downgrade_readback_mismatch")
    if not native_migration_json_equal(before, after):
        raise RuntimeError("coverage_nonempty_downgrade_readback_mismatch")
    return {
        "schema": "ctcc.controlled_writer_coverage_db0048_nonempty_downgrade.v1",
        "source_head": "0048",
        "target": "0047",
        "result": "DENIED",
        "policy_refusal": COVERAGE_DOWNGRADE_DENIAL,
        "before": deepcopy(before),
        "after": deepcopy(after),
        "rollback": "EXACT",
        "execution_authority": False,
    }


def verify_coverage_migration_receipt(
    value: dict, *, manifest_sha256: str, repository_sha256: str, migration_sha256: str
) -> dict:
    from app.database.repositories.controlled_writer_coverage import COVERAGE_TABLES

    if (
        type(value) is not dict
        or set(value)
        != {
            "schema_version",
            "manifest_sha256",
            "repository_sha256",
            "migration_sha256",
            "server_major",
            "before",
            "after",
            "refusal_code",
            "downgrade_target",
            "native_baseline_admission",
            "execution_authority",
        }
        or value.get("schema_version")
        != "ctcc.controlled_writer_coverage_migration_acceptance.v1"
        or value.get("manifest_sha256") != manifest_sha256
        or value.get("repository_sha256") != repository_sha256
        or value.get("migration_sha256") != migration_sha256
        or any(
            type(pin) is not str or re.fullmatch(r"[a-f0-9]{64}", pin) is None
            for pin in (manifest_sha256, repository_sha256, migration_sha256)
        )
        or type(value.get("server_major")) is not int
        or value["server_major"] != 17
        or value.get("refusal_code") != COVERAGE_DOWNGRADE_DENIAL
        or value.get("downgrade_target") != "0047"
        or value.get("native_baseline_admission") is not False
        or value.get("execution_authority") is not False
    ):
        raise ValueError("coverage_migration_receipt_invalid")
    for snapshot in (value["before"], value["after"]):
        if (
            type(snapshot) is not dict
            or set(snapshot) != {"head", "rows_sha256", "counts", "catalog_sha256"}
            or snapshot.get("head") != "0048"
            or type(snapshot.get("counts")) is not dict
            or set(snapshot["counts"]) != set(COVERAGE_TABLES)
            or any(
                type(count) is not int or not 1 <= count <= 8192
                for count in snapshot["counts"].values()
            )
            or any(
                type(snapshot.get(key)) is not str
                or re.fullmatch(r"[a-f0-9]{64}", snapshot[key]) is None
                for key in ("rows_sha256", "catalog_sha256")
            )
        ):
            raise ValueError("coverage_migration_receipt_snapshot_invalid")
    if not native_migration_json_equal(value["before"], value["after"]):
        raise ValueError("coverage_migration_receipt_retention_changed")
    return deepcopy(value)


def native_identity_empty_before_readback(revision: dict, custody_sha256: str) -> dict:
    require_native_witness_identity_baseline()
    _require_native_object_digest(custody_sha256)
    if not native_migration_json_equal(
        revision, expected_migration_revision_readback("0047")
    ):
        raise RuntimeError("native_identity_empty_before_readback_mismatch")
    return {
        "schema": "ctcc.public_witness_db0047_empty_before.v1",
        "revision_readback": deepcopy(revision),
        "object_custody_sha256": custody_sha256,
        "execution_authority": False,
    }


def expected_native_identity_empty_downgrade_readback(custody_sha256: str) -> dict:
    _require_native_object_digest(custody_sha256)
    return {
        "schema": "ctcc.public_witness_db0047_empty_downgrade.v1",
        "source_head": "0047",
        "target": "0046",
        "legacy_revision_readback": expected_migration_revision_readback("0046"),
        "legacy_witness_catalog": expected_legacy_witness_identity_schema(),
        "object_custody_sha256": custody_sha256,
        "execution_authority": False,
    }


def native_migration_json_equal(left: object, right: object) -> bool:
    """Keep bool, int and float distinct in the new evidence contracts."""
    try:
        return json.dumps(
            left, sort_keys=True, allow_nan=False, separators=(",", ":")
        ) == json.dumps(right, sort_keys=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return False


def native_identity_empty_downgrade_readback(
    legacy_revision: dict, catalog: dict, custody_sha256: str
) -> dict:
    result = {
        **expected_native_identity_empty_downgrade_readback(custody_sha256),
        "legacy_revision_readback": legacy_revision,
        "legacy_witness_catalog": catalog,
    }
    if not native_migration_json_equal(
        result, expected_native_identity_empty_downgrade_readback(custody_sha256)
    ):
        raise RuntimeError("native_identity_empty_downgrade_readback_mismatch")
    return result


def native_identity_nonempty_snapshot(revision: dict, snapshot: dict) -> dict:
    require_native_witness_identity_baseline()
    if not native_migration_json_equal(
        revision, expected_migration_revision_readback("0047")
    ) or not isinstance(snapshot, dict):
        raise RuntimeError("native_identity_nonempty_snapshot_invalid")
    if set(snapshot) != {
        "row_count",
        "small_row_count",
        "wide_row_count",
        "records_sha256",
        "catalog_sha256",
    }:
        raise RuntimeError("native_identity_nonempty_snapshot_invalid")
    if any(
        type(snapshot[k]) is not int or not 1 <= snapshot[k] <= 100000
        for k in ("row_count", "small_row_count", "wide_row_count")
    ):
        raise RuntimeError("native_identity_nonempty_snapshot_invalid")
    if snapshot["small_row_count"] + snapshot["wide_row_count"] != snapshot[
        "row_count"
    ] or any(
        not isinstance(snapshot[k], str)
        or not re.fullmatch(r"[0-9a-f]{64}", snapshot[k])
        for k in ("records_sha256", "catalog_sha256")
    ):
        raise RuntimeError("native_identity_nonempty_snapshot_invalid")
    return {
        "schema": "ctcc.public_witness_db0047_nonempty_snapshot.v1",
        "revision_readback": deepcopy(revision),
        "retention": deepcopy(snapshot),
        "execution_authority": False,
    }


def native_identity_nonempty_downgrade_readback(before: dict, after: dict) -> dict:
    for observed in (before, after):
        if (
            not isinstance(observed, dict)
            or set(observed)
            != {"schema", "revision_readback", "retention", "execution_authority"}
            or not native_migration_json_equal(
                observed,
                native_identity_nonempty_snapshot(
                    observed.get("revision_readback"), observed.get("retention")
                ),
            )
        ):
            raise RuntimeError("native_identity_nonempty_downgrade_readback_mismatch")
    if not native_migration_json_equal(before, after):
        raise RuntimeError("native_identity_nonempty_downgrade_readback_mismatch")
    return {
        "schema": "ctcc.public_witness_db0047_nonempty_downgrade.v1",
        "source_head": "0047",
        "target": "0046",
        "result": "DENIED",
        "policy_refusal": NATIVE_IDENTITY_DOWNGRADE_DENIAL,
        "before": deepcopy(before),
        "after": deepcopy(after),
        "rollback": "EXACT",
        "execution_authority": False,
    }


def migration_revision_readback(
    expected: str,
    observed: str,
    witness_present: bool,
    guard_present: bool,
    trigger_catalog: Iterable[Sequence[object]],
    hwm_present: bool = False,
    hwm_guard_present: bool = False,
    hwm_trigger_catalog: Iterable[Sequence[object]] = (),
    runtime_catalog: dict | None = None,
    native_identity_catalog: dict | None = None,
    *,
    coverage_catalog: dict | None = None,
) -> dict:
    """Deny schema drift, including extra or disabled non-internal triggers."""
    if expected == "0048":
        from scripts.verify_controlled_writer_coverage_schema_v1 import verify_catalog

        require_native_witness_identity_baseline()
        # The separately captured catalog is an observation. A pending native
        # baseline must still refuse here, even if structural checks succeed.
        coverage = verify_catalog(coverage_catalog, require_native_baseline=True)
        catalog = [list(row) for row in trigger_catalog]
        hwm_catalog = [list(row) for row in hwm_trigger_catalog]
        result = {
            "schema": "ctcc.migration_revision_readback.v5",
            "expected": expected,
            "observed": observed,
            "execution_authority": False,
            "account_revision_witness_table_present": witness_present,
            "account_revision_witness_guard_present": guard_present,
            "account_revision_witness_trigger_count": len(catalog),
            "account_revision_witness_trigger_catalog": catalog,
            "account_measured_hwm_v2_table_present": hwm_present,
            "account_measured_hwm_v2_guard_present": hwm_guard_present,
            "account_measured_hwm_v2_trigger_count": len(hwm_catalog),
            "account_measured_hwm_v2_trigger_catalog": hwm_catalog,
            "public_runtime_bundle_schema": runtime_catalog,
            "public_witness_native_identity_schema": native_identity_catalog,
            "controlled_writer_coverage_schema": coverage,
        }
        if not native_migration_json_equal(
            result, expected_migration_revision_readback(expected)
        ):
            raise RuntimeError("migration_revision_readback_mismatch")
        return result
    if expected not in ("0001", "0042", "0043", "0044", "0045", "0046", "0047"):
        raise RuntimeError("migration_revision_readback_mismatch")
    if runtime_catalog is not None and runtime_catalog != (
        PUBLIC_RUNTIME_BUNDLE_SCHEMA
        if expected in ("0046", "0047")
        else PUBLIC_RUNTIME_BUNDLE_SCHEMA_ABSENT
    ):
        raise RuntimeError("migration_revision_readback_mismatch")
    catalog = [list(row) for row in trigger_catalog]
    hwm_catalog = [list(row) for row in hwm_trigger_catalog]
    result = {
        "schema": "ctcc.migration_revision_readback.v4"
        if expected == "0047"
        else "ctcc.migration_revision_readback.v3"
        if expected == "0046"
        else "ctcc.migration_revision_readback.v2",
        "expected": expected,
        "observed": observed,
        "execution_authority": False,
        "account_revision_witness_table_present": witness_present,
        "account_revision_witness_guard_present": guard_present,
        "account_revision_witness_trigger_count": len(catalog),
        "account_revision_witness_trigger_catalog": catalog,
        "account_measured_hwm_v2_table_present": hwm_present,
        "account_measured_hwm_v2_guard_present": hwm_guard_present,
        "account_measured_hwm_v2_trigger_count": len(hwm_catalog),
        "account_measured_hwm_v2_trigger_catalog": hwm_catalog,
    }
    if expected in ("0046", "0047"):
        result["public_runtime_bundle_schema"] = runtime_catalog
    if expected == "0047":
        # None is a deliberate unsealed native constraint baseline; even a
        # caller-supplied DTO identical to the pending template cannot qualify.
        require_native_witness_identity_baseline()
        result["public_witness_native_identity_schema"] = native_identity_catalog
    if result != expected_migration_revision_readback(expected):
        raise RuntimeError("migration_revision_readback_mismatch")
    return result


def verify_downgrade_denial_log(path: Path) -> None:
    """Accept only DB0042's refusal after DB0047 through DB0043 downgrade."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("migration_downgrade_denial_log_missing_or_unsafe")
    try:
        output = path.read_bytes().decode("utf-8")
    except UnicodeError as error:
        raise ValueError("migration_downgrade_denial_log_invalid") from error
    if (
        "Running downgrade 0047 -> 0046" not in output
        or "Running downgrade 0046 -> 0045" not in output
        or "Running downgrade 0045 -> 0044" not in output
        or "Running downgrade 0044 -> 0043" not in output
        or "Running downgrade 0043 -> 0042" not in output
        or "Running downgrade 0042 -> 0041" not in output
        or "/app/migrations/versions/0042_submission_transport_authenticity_denial.py"
        not in output
        or len(
            re.findall(
                rf"(?m)^asyncpg\.exceptions\.RaiseError: {DOWNGRADE_DENIAL}\r?$",
                output,
            )
        )
        != 1
        or not re.search(
            rf"(?m)^sqlalchemy\.exc\.DBAPIError: .*: {DOWNGRADE_DENIAL}\r?$",
            output,
        )
    ):
        raise ValueError("migration_downgrade_policy_refusal_unverified")


def verify_native_identity_downgrade_denial_log(path: Path) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("native_identity_downgrade_denial_log_missing_or_unsafe")
    try:
        output = path.read_bytes().decode("utf-8")
    except UnicodeError as error:
        raise ValueError("native_identity_downgrade_denial_log_invalid") from error
    if (
        re.findall(r"Running downgrade (\d{4} -> \d{4})", output) != ["0047 -> 0046"]
        or "/app/migrations/versions/0047_public_witness_native_identity.py"
        not in output
        or len(
            re.findall(
                rf"(?m)^asyncpg\.exceptions\.RaiseError: {NATIVE_IDENTITY_DOWNGRADE_DENIAL}\r?$",
                output,
            )
        )
        != 1
        or not re.search(
            rf"(?m)^sqlalchemy\.exc\.DBAPIError: .*: {NATIVE_IDENTITY_DOWNGRADE_DENIAL}\r?$",
            output,
        )
    ):
        raise ValueError("native_identity_downgrade_policy_refusal_unverified")


def verify_current_downgrade_denial_log(path: Path) -> None:
    """0048 outer transaction, followed by the unchanged DB0042 refusal."""
    verify_downgrade_denial_log(path)
    output = path.read_bytes().decode("utf-8")
    if re.findall(r"Running downgrade (\d{4} -> \d{4})", output) != [
        "0048 -> 0047",
        "0047 -> 0046",
        "0046 -> 0045",
        "0045 -> 0044",
        "0044 -> 0043",
        "0043 -> 0042",
        "0042 -> 0041",
    ]:
        raise ValueError("current_migration_downgrade_policy_refusal_unverified")


def verify_coverage_downgrade_denial_log(path: Path) -> None:
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= 2 * 1024 * 1024
    ):
        raise ValueError("coverage_downgrade_denial_log_missing_or_unsafe")
    try:
        output = path.read_bytes().decode("utf-8")
    except UnicodeError as error:
        raise ValueError("coverage_downgrade_denial_log_invalid") from error
    if (
        re.findall(r"Running downgrade (\d{4} -> \d{4})", output) != ["0048 -> 0047"]
        or "/app/migrations/versions/0048_controlled_writer_coverage.py" not in output
        or len(
            re.findall(
                rf"(?m)^asyncpg\.exceptions\.RaiseError: {COVERAGE_DOWNGRADE_DENIAL}\r?$",
                output,
            )
        )
        != 1
        or not re.search(
            rf"(?m)^sqlalchemy\.exc\.DBAPIError: .*: {COVERAGE_DOWNGRADE_DENIAL}\r?$",
            output,
        )
    ):
        raise ValueError("coverage_downgrade_policy_refusal_unverified")


def git_object(kind: str, raw: bytes) -> bytes:
    return hashlib.sha1(f"{kind} {len(raw)}\0".encode() + raw).digest()


def archive_tree(archive: tarfile.TarFile) -> str:
    """Hash exact archived bytes/modes, not normalized manifest representations."""
    root: dict = {}
    declared_directories: set[tuple[str, ...]] = set()
    file_paths: set[tuple[str, ...]] = set()
    directories_with_files: set[tuple[str, ...]] = set()
    for item in archive.getmembers():
        path = PurePosixPath(item.name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("unsafe_archive_path")
        for index, part in enumerate(path.parts):
            folded = part.casefold()
            if folded in PRIVATE_ARCHIVE_DIRECTORIES:
                raise ValueError("archive_runtime_or_git_entry")
            base_name = folded
            for backup_suffix in PRIVATE_ARCHIVE_BACKUP_SUFFIXES:
                if base_name.endswith(backup_suffix):
                    base_name = base_name[: -len(backup_suffix)]
                    break
            if (
                folded.startswith(".env")
                and not (
                    index == len(path.parts) - 1
                    and item.isfile()
                    and folded == ".env.example"
                )
                or base_name in PRIVATE_ARCHIVE_NAMES
                or base_name in PRIVATE_ARCHIVE_SUFFIXES
                or PurePosixPath(base_name).suffix in PRIVATE_ARCHIVE_SUFFIXES
            ):
                raise ValueError("archive_non_source_entry")
        if item.isdir():
            if path.parts in declared_directories or path.parts in file_paths:
                raise ValueError("archive_duplicate_entry")
            declared_directories.add(path.parts)
            continue
        if not item.isfile():
            raise ValueError("archive_non_source_entry")
        if path.parts in declared_directories:
            raise ValueError("archive_path_conflict")
        file_paths.add(path.parts)
        directories_with_files.update(
            path.parts[:index] for index in range(1, len(path.parts))
        )
        node = root
        for part in path.parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise TypeError("archive_path_conflict")
        if path.name in node:
            raise ValueError("archive_duplicate_entry")
        stream = archive.extractfile(item)
        assert stream is not None
        raw = stream.read()
        node[path.name] = (
            b"100755" if item.mode & 0o111 else b"100644",
            git_object("blob", raw),
        )

    if not declared_directories.issubset(directories_with_files):
        raise ValueError("archive_empty_directory")

    def tree(node):
        records = []
        for name, value in node.items():
            directory = isinstance(value, dict)
            mode, digest = (b"40000", tree(value)) if directory else value
            records.append(
                (
                    name.encode() + (b"/" if directory else b""),
                    mode + b" " + name.encode() + b"\0" + digest,
                )
            )
        return git_object("tree", b"".join(raw for _, raw in sorted(records)))

    return tree(root).hex()


def archive_files(archive: tarfile.TarFile) -> dict:
    """Independent expected COPY bytes; called only after archive admission."""
    archive_tree(archive)
    files = {}
    for item in archive.getmembers():
        if item.isfile():
            stream = archive.extractfile(item)
            assert stream is not None
            files[item.name] = {
                "sha256": hashlib.sha256(stream.read()).hexdigest(),
                "executable": bool(item.mode & 0o111),
            }
    return files


def verify_copied_source(root: Path, manifest: Path, expected_sha256: str) -> int:
    """Check exact archived files inside the image, including executable bits."""
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("source_file_map_digest_mismatch")
    files = read_evidence_json(raw)
    for name, expected in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("source_file_map_unsafe_path")
        path = root.joinpath(*relative.parts)
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError(f"copied_source_symlink:{name}")
        mode = path.stat().st_mode
        if (
            not stat.S_ISREG(mode)
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]
            or bool(mode & 0o111) != expected["executable"]
        ):
            raise ValueError(f"copied_source_mismatch:{name}")
    return len(files)


def verify_pytest_report(
    path: Path, *, required_modules=(), required_cases=(), allow_unrelated_skips=False
):
    """Verify execution, with explicit full-suite platform-skip handling.

    Required modules must have passed cases and no skipped cases. A targeted
    PostgreSQL report still rejects every skip by default. Full-suite callers
    may retain skips outside those modules without claiming they passed.
    """
    import xml.etree.ElementTree as ET

    if path.is_symlink() or not 0 < path.stat().st_size <= 64 * 1024 * 1024:
        raise ValueError("pytest_report_missing_or_unsafe")
    raw = path.read_bytes()
    root = ET.fromstring(raw)
    if root.tag != "testsuites" or not len(root):
        raise ValueError("pytest_report_schema_invalid")
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    required = set(required_modules)
    required_counts = {module: 0 for module in sorted(required)}
    case_counts = {tuple(case): 0 for case in required_cases}
    required_skipped = False
    for suite in root:
        if suite.tag != "testsuite":
            raise ValueError("pytest_report_schema_invalid")
        cases = suite.findall("testcase")
        counts = {"tests": len(cases)}
        for key, child in (
            ("failures", "failure"),
            ("errors", "error"),
            ("skipped", "skipped"),
        ):
            counts[key] = sum(case.find(child) is not None for case in cases)
        if any(str(value) != suite.get(key) for key, value in counts.items()):
            raise ValueError("pytest_report_counts_conflict")
        for key, value in counts.items():
            total[key] += value
        for case in cases:
            module = case.get("classname")
            exact_case = (module, case.get("name"))
            matched_modules = (
                {
                    target
                    for target in required
                    if module == target or module.startswith(target + ".")
                }
                if module
                else set()
            )
            if matched_modules and case.find("skipped") is not None:
                required_skipped = True
            if all(case.find(tag) is None for tag in ("failure", "error", "skipped")):
                for target in matched_modules:
                    required_counts[target] += 1
                if exact_case in case_counts:
                    case_counts[exact_case] += 1
    passed = total["tests"] - total["failures"] - total["errors"] - total["skipped"]
    if (
        passed <= 0
        or total["failures"]
        or total["errors"]
        or required_skipped
        or (required and total["skipped"] and not allow_unrelated_skips)
        or any(count <= 0 for count in required_counts.values())
        or any(count != 1 for count in case_counts.values())
    ):
        raise ValueError("pytest_required_execution_not_verified")
    return {
        **total,
        "passed": passed,
        "required_module_passed": required_counts,
        "required_case_passed": {
            f"{module}::{name}": count
            for (module, name), count in sorted(case_counts.items())
        },
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def verify_linux_collection(path: Path, *, mode: str, index: int | None, report: dict):
    """Read back an executed shard against its complete collected test list."""
    if path.is_symlink() or not 0 < path.stat().st_size <= 16 * 1024 * 1024:
        raise ValueError("linux_collection_missing_or_unsafe")
    raw = path.read_bytes()
    record = read_evidence_json(raw)
    full = record.get("full_nodeids")
    selected = record.get("selected_nodeids")
    observed = record.get("observed_nodeids")
    if (
        record.get("schema") != "ctcc.linux_full_collection_shard.v1"
        or record.get("mode") != mode
        or (index is not None and type(record.get("shard_index")) is not int)
        or record.get("shard_index") != index
        or type(record.get("shard_count")) is not int
        or record.get("shard_count") != SHARD_COUNT
        or type(record.get("pytest_exitstatus")) is not int
        or record.get("pytest_exitstatus") != 0
        or not isinstance(full, list)
        or not full
        or any(type(node) is not str for node in full)
        or len(full) != len(set(full))
        or not isinstance(selected, list)
        or not selected
        or selected != [node for node in full if selected_for(mode, index, node)]
        or observed != selected
        or report["tests"] != len(selected)
    ):
        raise ValueError("linux_collection_execution_incomplete")
    return {
        "full_count": len(full),
        "selected_count": len(selected),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "full_sha256": hashlib.sha256(
            json.dumps(full, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def run_runtime_bundle_custody_preflight(container, *, test_results: Path, mounts):
    """Exercise native DB0046 protocol before any long PostgreSQL test run.

    Use the same credential-free runner and isolated database as the full
    suite. Suppress raw DB tracebacks/capture in both stage log and JUnit; the
    exact four executed cases remain separately verifiable evidence.
    """
    container(
        "runtime-bundle-custody-preflight",
        [
            "python",
            "-m",
            "scripts.hermetic_pytest",
            "-p",
            "no:cacheprovider",
            "--tb=no",
            "--show-capture=no",
            "-o",
            "junit_logging=no",
            "--junitxml=/validation-results/runtime-bundle-custody-preflight.xml",
            *(
                f"{module.replace('.', '/')}.py::{case}"
                for module, case in RUNTIME_CUSTODY_PREFLIGHT_CASES
            ),
        ],
        mounts=mounts,
        timeout=5 * 60,
    )
    return verify_pytest_report(
        test_results / "runtime-bundle-custody-preflight.xml",
        required_modules=tuple(
            sorted({module for module, _ in RUNTIME_CUSTODY_PREFLIGHT_CASES})
        ),
        required_cases=RUNTIME_CUSTODY_PREFLIGHT_CASES,
    )


def run_predecessor_0047_validation(
    run, container, *, postgres, suffix, test_results, mounts
):
    """Own one exact temporary DB; two diagnostic cases are outside the union."""
    name = "ctcc_predecessor_47_" + suffix
    if not re.fullmatch(r"ctcc_predecessor_47_[a-f0-9]{12}", name):
        raise ValueError("predecessor_database_name_invalid")
    run.command(
        "predecessor-0047-parent-identity",
        [
            "docker",
            "exec",
            postgres,
            "psql",
            "-U",
            "ctcc",
            "-d",
            "ctcc",
            "-Atc",
            "SELECT current_database()",
        ],
        timeout=60,
    )
    if (run.output / "predecessor-0047-parent-identity.log").read_bytes() != b"ctcc\n":
        raise RuntimeError("predecessor_parent_database_identity_invalid")
    created = False
    environment = (
        "-e",
        f"DATABASE_URL=postgresql+asyncpg://ctcc@{postgres}:5432/{name}",
    )

    def separate(stage, command, **kwargs):
        return container(stage, command, extra_environment=environment, **kwargs)

    try:
        run.command(
            "predecessor-0047-create",
            [
                "docker",
                "exec",
                postgres,
                "createdb",
                "-U",
                "ctcc",
                "--maintenance-db=ctcc",
                "--owner=ctcc",
                name,
            ],
            timeout=60,
        )
        created = True
        separate(
            "predecessor-0047-upgrade", ["alembic", "upgrade", "0047"], timeout=120
        )
        separate(
            "predecessor-0047-identity",
            ["python", "-c", PREDECESSOR_0047_IDENTITY_PROBE],
            timeout=60,
        )
        report_name = "predecessor-0047-two-cases.xml"
        separate(
            "predecessor-0047-two-cases",
            [
                "python",
                "-m",
                "scripts.hermetic_pytest",
                "-p",
                "no:cacheprovider",
                "--tb=no",
                "--show-capture=no",
                "-o",
                "junit_logging=no",
                "--junitxml=/validation-results/" + report_name,
                *(
                    f"{module.replace('.', '/')}.py::{case}"
                    for module, case in PREDECESSOR_0047_CASES
                ),
            ],
            mounts=mounts,
            timeout=5 * 60,
        )
        report = verify_pytest_report(
            test_results / report_name,
            required_cases=PREDECESSOR_0047_CASES,
            required_modules=(PREDECESSOR_0047_CASES[0][0],),
        )
        if report["tests"] != 2 or report["passed"] != 2 or report["skipped"] != 0:
            raise RuntimeError("predecessor_0047_exact_two_case_inventory_required")
        separate(
            "migration-native-identity-nonempty-before",
            ["python", "-c", NATIVE_IDENTITY_RETENTION_PROBE],
            timeout=60,
        )
        before = json.loads(
            (run.output / "migration-native-identity-nonempty-before.log").read_bytes()
        )
        separate(
            "migration-native-identity-nonempty-denial",
            ["alembic", "downgrade", "0046"],
            expected_refusal=NATIVE_IDENTITY_DOWNGRADE_DENIAL,
            timeout=60,
        )
        separate(
            "migration-native-identity-nonempty-after",
            ["python", "-c", NATIVE_IDENTITY_RETENTION_PROBE],
            timeout=60,
        )
        after = json.loads(
            (run.output / "migration-native-identity-nonempty-after.log").read_bytes()
        )
        return {
            "schema": "ctcc.isolated_0047_predecessor_validation.v1",
            "source_head": "0048",
            "database_target": "0047",
            "separate_from_full_suite_unique_count": True,
            "test_report": report,
            "nonempty_downgrade_policy": native_identity_nonempty_downgrade_readback(
                before, after
            ),
            "execution_authority": False,
        }
    finally:
        if created:
            # No FORCE, termination of unrelated sessions, or arbitrary target.
            run.command(
                "predecessor-0047-drop",
                [
                    "docker",
                    "exec",
                    postgres,
                    "dropdb",
                    "-U",
                    "ctcc",
                    "--maintenance-db=ctcc",
                    name,
                ],
                timeout=60,
            )


class Run:
    def __init__(
        self,
        output: Path,
        *,
        budget_seconds: float = VALIDATION_BUDGET_SECONDS,
        heartbeat_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        clock=time.monotonic,
    ):
        if budget_seconds <= 0 or heartbeat_seconds <= 0:
            raise ValueError("validation_timing_budget_invalid")
        self.output = output
        self.steps = []
        self.clock = clock
        self.deadline = clock() + budget_seconds
        self.heartbeat_seconds = heartbeat_seconds

    def command(
        self, name, args, *, timeout=3600, input_bytes=None, expected_refusal=None
    ):
        start = self.clock()
        record = {"name": name, "exit_code": None}
        print(f"HERMETIC_STAGE_START={name}", flush=True)
        heartbeat_stop = threading.Event()

        def heartbeat():
            while not heartbeat_stop.wait(self.heartbeat_seconds):
                if heartbeat_stop.is_set():
                    return
                print(
                    f"HERMETIC_STAGE_ALIVE={name}:{int(self.clock() - start)}",
                    flush=True,
                )

        heartbeat_thread = threading.Thread(
            target=heartbeat, name="ctcc-hermetic-heartbeat", daemon=True
        )
        heartbeat_started = False
        try:
            with (self.output / f"{name}.log").open("wb") as log:
                remaining = self.deadline - self.clock()
                record["timeout_scope"] = (
                    "validation" if remaining < timeout else "stage"
                )
                if remaining <= 0:
                    raise TimeoutError("validation_deadline_exceeded")
                effective_timeout = min(timeout, remaining)
                heartbeat_thread.start()
                heartbeat_started = True
                result = subprocess.run(
                    args,
                    input=input_bytes,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=effective_timeout,
                    check=False,
                )
                record["exit_code"] = result.returncode
                if expected_refusal is not None:
                    if (
                        expected_refusal
                        not in (
                            DOWNGRADE_DENIAL,
                            NATIVE_IDENTITY_DOWNGRADE_DENIAL,
                            COVERAGE_DOWNGRADE_DENIAL,
                        )
                        or result.returncode == 0
                    ):
                        raise RuntimeError(
                            "migration_downgrade_policy_refusal_unverified"
                        )
                    if expected_refusal == DOWNGRADE_DENIAL:
                        verify_downgrade_denial_log(self.output / f"{name}.log")
                    elif expected_refusal == COVERAGE_DOWNGRADE_DENIAL:
                        verify_coverage_downgrade_denial_log(
                            self.output / f"{name}.log"
                        )
                    else:
                        verify_native_identity_downgrade_denial_log(
                            self.output / f"{name}.log"
                        )
                    record["policy_refusal"] = expected_refusal
        except Exception as error:
            record["failure_type"] = type(error).__name__
            raise
        finally:
            heartbeat_stop.set()
            if heartbeat_started:
                heartbeat_thread.join(timeout=1)
            record["elapsed_seconds"] = self.clock() - start
            self.steps.append(record)
            self.save()
            print(
                f"HERMETIC_STAGE_END={name}:"
                f"{record['exit_code'] if record['exit_code'] is not None else record.get('failure_type', 'unknown')}",
                flush=True,
            )
        if result.returncode and expected_refusal is None:
            raise RuntimeError(f"validation_step_failed:{name}")

    def save(self):
        (self.output / "steps.json").write_text(json.dumps(self.steps, indent=2))


def cleanup_resources(
    resources: list[tuple[str, str]],
    run_id: str,
    *,
    budget_seconds: float = CLEANUP_BUDGET_SECONDS,
    clock=time.monotonic,
) -> list[dict]:
    """Reconcile attempted creates, including timeouts, without touching others."""
    if budget_seconds < 0:
        raise ValueError("cleanup_timing_budget_invalid")
    deadline = clock() + budget_seconds

    def bounded_command(args, *, timeout, check, **kwargs):
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("cleanup_deadline_exceeded")
        return subprocess.run(
            args, timeout=min(timeout, remaining), check=check, **kwargs
        )

    results = []
    for kind, name in reversed(resources):
        result = {"kind": kind, "name": name, "status": "FAIL"}
        try:
            listing = bounded_command(
                [
                    "docker",
                    kind,
                    "ls",
                    *(["-a"] if kind == "container" else []),
                    "--format",
                    "{{.Name}}" if kind == "network" else "{{.Names}}",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            if name not in listing.stdout.splitlines():
                result["status"] = "ABSENT"
            else:
                inspected = bounded_command(
                    ["docker", kind, "inspect", name],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30,
                )
                details = json.loads(inspected.stdout)[0]
                labels = (
                    details.get("Config", {}) if kind == "container" else details
                ).get("Labels") or {}
                if labels.get("org.ctcc.validation.run") != run_id:
                    raise ValueError("cleanup_resource_ownership_mismatch")
                bounded_command(
                    [
                        "docker",
                        kind,
                        "rm",
                        *(["-f", "-v"] if kind == "container" else []),
                        name,
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=60,
                )
                result["status"] = "REMOVED"
        except (
            OSError,
            subprocess.SubprocessError,
            ValueError,
            KeyError,
            IndexError,
        ) as error:
            result["failure_type"] = type(error).__name__
        results.append(result)
    return results


def verify_durable_seed_marker(raw: bytes) -> dict:
    """Shared producer/reader contract for the synthetic durable seed marker.

    This validates the captured protocol envelope, not the database state. The
    separate post-restart process must still replay and verify that state.
    """

    try:
        if type(raw) is not bytes or not 0 < len(raw) <= 32768:
            raise ValueError
        envelope = read_evidence_json(raw)
        if type(envelope) is not dict or set(envelope) != {"body", "sha256"}:
            raise ValueError
        body = envelope["body"]
        if type(body) is not dict:
            raise ValueError
        canonical = json.dumps(
            body, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        if (
            envelope["sha256"] != hashlib.sha256(canonical).hexdigest()
            or body.get("schema") != "ctcc.synthetic.qualification.crash-probe.v3"
            or type(body.get("account_captures")) is not list
            or len(body["account_captures"]) != 2
            or any(type(item) is not dict for item in body["account_captures"])
            or {item.get("scenario") for item in body["account_captures"]}
            != {"safe_failed_capture", "unfinished_ram_prefix"}
            or type(body.get("controls")) is not list
            or len(body["controls"]) != 3
            or any(type(item) is not dict for item in body["controls"])
            or {item.get("scenario") for item in body["controls"]}
            != {"arm_intent", "estop", "cold_estop"}
            or body.get("control_arm_intent_observed_before_publish") is not True
            or body.get("intent_version") != "ctcc-demo-submit-intent-v2"
            or type(body.get("exchange_request_sha256")) is not str
            or re.fullmatch("[a-f0-9]{64}", body["exchange_request_sha256"]) is None
            or body.get("execution_authority") is not False
            or type(body.get("order_writes")) is not int
            or body["order_writes"] != 0
        ):
            raise ValueError
        return body
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        raise RuntimeError("durable_seed_marker_invalid") from None


def wait_for_durable_seed(run: Run, container: str, marker: Path, *, timeout=120):
    """Require a live seed process and a complete durable readback marker."""
    deadline = time.monotonic() + timeout
    while True:
        inspected = subprocess.run(
            ["docker", "inspect", "--format", "{{json .State}}", container],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        state = json.loads(inspected.stdout)
        if state.get("Running") is not True:
            run.command("crash-seed-exited-log", ["docker", "logs", container])
            run.command("crash-seed-exited-state", ["docker", "inspect", container])
            raise RuntimeError("durable_seed_process_exited")
        if marker.is_file():
            verify_durable_seed_marker(marker.read_bytes())
            return
        if time.monotonic() >= deadline:
            run.command("crash-seed-timeout-log", ["docker", "logs", container])
            raise RuntimeError("durable_seed_marker_missing")
        time.sleep(0.25)


API_PROBE = """
import json, time, urllib.request
deadline = time.monotonic() + 60
while True:
    try:
        values = {}
        for path in ('liveness', 'readiness', 'api/version'):
            with urllib.request.urlopen('http://127.0.0.1:8000/' + path, timeout=5) as response:
                values[path] = json.load(response)
        assert values['liveness']['status'] == 'alive'
        ready = values['readiness']
        assert ready['status'] == 'ready' and not ready['blockers']
        assert ready['database']['ok'] is True and ready['redis']['ok'] is True
        version = values['api/version']
        assert version['environment'] == 'test' and version['trading_mode'] == 'analysis_only'
        assert version['auto_trade'] is False and version['live_trading'] is False
        print(json.dumps(values, sort_keys=True))
        break
    except Exception:
        if time.monotonic() >= deadline:
            raise
        time.sleep(1)
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--suite", choices=("full", "postgres", "shard"), default="full"
    )
    parser.add_argument("--shard-index", type=int)
    args = parser.parse_args()
    if (args.suite == "shard") != (args.shard_index is not None) or (
        args.shard_index is not None and not 0 <= args.shard_index < SHARD_COUNT
    ):
        raise ValueError("linux_suite_selection_invalid")
    for identity in (args.commit, args.tree):
        if not re.fullmatch("[a-f0-9]{40}", identity):
            raise ValueError("exact_git_identity_required")
    # Pin one byte buffer for admission and build. A Windows directory context
    # does not retain archived POSIX executable bits, and rereading the archive
    # after admission could submit different bytes to the builder.
    archive_bytes = args.archive.read_bytes()
    if hashlib.sha256(archive_bytes).hexdigest() != args.archive_sha256:
        raise ValueError("archive_digest_mismatch")
    args.output.mkdir(parents=True, exist_ok=False)
    source = args.output / "source"
    source.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as archive:
        if (
            archive.pax_headers.get("comment") != args.commit
            or archive_tree(archive) != args.tree
        ):
            raise ValueError("archive_git_identity_mismatch")
        files = archive_files(archive)
        archive.extractall(source, filter="data")
    file_map = args.output / "source-files.json"
    file_map.write_text(json.dumps(files, sort_keys=True, separators=(",", ":")))
    file_map_sha256 = hashlib.sha256(file_map.read_bytes()).hexdigest()
    identity = {
        "commit": args.commit,
        "tree": args.tree,
        "archive_sha256": args.archive_sha256,
        "build_context": "admitted_archive_bytes_v1",
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_files_sha256": file_map_sha256,
        "manifest_sha256": (
            hashlib.sha256((source / "MANIFEST.sha256").read_bytes()).hexdigest()
            if (source / "MANIFEST.sha256").is_file()
            else None
        ),
        "suite": args.suite,
        "shard_index": args.shard_index,
        "shard_count": SHARD_COUNT,
        "host_credentials": False,
        "host_env": False,
        "external_order_writes": 0,
        "trading_acceptance": False,
    }
    (args.output / "identity.json").write_text(json.dumps(identity, indent=2))
    run = Run(args.output)
    test_results = args.output / "test-results"
    test_results.mkdir()
    # Dedicated synthetic test output only; neither source nor credentials mount.
    test_results.chmod(0o777)
    test_results_mount = (
        "--mount",
        f"type=bind,src={test_results.resolve()},dst=/validation-results",
    )
    suffix = uuid4().hex[:12]
    label = f"org.ctcc.validation.run={suffix}"
    network, postgres, redis = (
        f"ctcc-final-{suffix}-{part}" for part in ("network", "postgres", "redis")
    )
    image = f"ctcc-final-validation:{args.commit[:12]}-{suffix}"
    resources = []
    environment = [
        "-e",
        "ENVIRONMENT=test",
        "-e",
        "TRADING_MODE=analysis_only",
        "-e",
        f"DATABASE_URL=postgresql+asyncpg://ctcc@{postgres}:5432/ctcc",
        "-e",
        f"REDIS_URL=redis://{redis}:6379/0",
    ]

    # No exchange credential or write switch is passed. Settings defaults and the
    # hermetic pytest preflight both deny execution; the network has no egress.
    def container(
        name,
        command,
        *,
        mounts=(),
        extra_environment=(),
        timeout=3600,
        expected_refusal=None,
    ):
        container_name = f"ctcc-final-{suffix}-{name}"
        resources.append(("container", container_name))
        run.command(
            name,
            [
                "docker",
                "run",
                "--rm",
                "--name",
                container_name,
                "--label",
                label,
                "--network",
                network,
                *mounts,
                *environment,
                *extra_environment,
                image,
                *command,
            ],
            timeout=timeout,
            expected_refusal=expected_refusal,
        )

    try:
        run.command(
            "build",
            [
                "docker",
                "build",
                "--no-cache",
                "--build-arg",
                f"CTCC_SOURCE_COMMIT={args.commit}",
                "--build-arg",
                f"CTCC_SOURCE_TREE={args.tree}",
                "-t",
                image,
                "-",
            ],
            input_bytes=archive_bytes,
        )
        run.command(
            "image-identity",
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}} {{json .RepoDigests}} {{json .Config.Labels}}",
                image,
            ],
        )
        resources.append(("network", network))
        run.command(
            "network-create",
            ["docker", "network", "create", "--internal", "--label", label, network],
        )
        resources.append(("container", postgres))
        run.command(
            "postgres-start",
            [
                "docker",
                "run",
                "-d",
                "--name",
                postgres,
                "--label",
                label,
                "--network",
                network,
                "-e",
                "POSTGRES_USER=ctcc",
                "-e",
                "POSTGRES_DB=ctcc",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "postgres:17-alpine@sha256:f02121de6f74d30d8a94cd1d9584125e2178d7e6c377d8130112d4e52d867995",
            ],
        )
        resources.append(("container", redis))
        run.command(
            "redis-start",
            [
                "docker",
                "run",
                "-d",
                "--name",
                redis,
                "--label",
                label,
                "--network",
                network,
                "redis:8-alpine@sha256:bd999b5cfee25fb24b8320a31fddbd69f462df44c8138c66e369582937beebc0",
            ],
        )
        deadline = time.monotonic() + 120
        while subprocess.run(
            ["docker", "exec", postgres, "pg_isready", "-U", "ctcc", "-d", "ctcc"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        ).returncode:
            if time.monotonic() > deadline:
                raise RuntimeError("isolated_postgres_not_ready")
            time.sleep(1)
        container("copied-manifest", ["python", "scripts/manifest.py", "--check"])
        container(
            "copied-source-exact",
            [
                "python",
                "-c",
                "from pathlib import Path; from scripts.verify_final_hermetic import verify_copied_source; import sys; print(verify_copied_source(Path('/app'),Path('/verification/source-files.json'),sys.argv[1]))",
                file_map_sha256,
            ],
            mounts=(
                "--mount",
                f"type=bind,source={file_map.resolve()},target=/verification/source-files.json,readonly",
            ),
        )
        container("dependencies", ["python", "-m", "pip", "freeze", "--all"])
        container(
            "dependency-lock",
            ["python", "-m", "scripts.verify_dependency_lock", "--target", "linux"],
        )
        container("ruff", ["python", "-m", "ruff", "check", "--no-cache", "."])
        container(
            "format",
            ["python", "-m", "ruff", "format", "--check", "--no-cache", "."],
        )
        container("migration-initial-supported", ["alembic", "upgrade", "0001"])
        container("migration-upgrade-supported", ["alembic", "upgrade", "0016"])
        container("migration-downgrade-supported", ["alembic", "downgrade", "0001"])
        container(
            "migration-supported-readback",
            ["python", "-c", MIGRATION_REVISION_PROBE, "0001"],
        )
        identity["migration_supported_roundtrip"] = json.loads(
            (args.output / "migration-supported-readback.log").read_text()
        )
        if identity[
            "migration_supported_roundtrip"
        ] != expected_migration_revision_readback("0001"):
            raise RuntimeError("migration_supported_roundtrip_readback_invalid")
        container("migration-upgrade", ["alembic", "upgrade", "head"])
        container(
            "migration-coverage-native-catalog",
            ["python", "-c", COVERAGE_NATIVE_CATALOG_CAPTURE],
            mounts=test_results_mount,
            timeout=60,
        )
        container(
            "migration-identity", ["python", "-m", "scripts.verify_migration_identity"]
        )
        identity["migration"] = json.loads(
            (args.output / "migration-identity.log").read_text()
        )
        if (
            identity["migration"].get("source_head") != "0048"
            or identity["migration"].get("database_head") != "0048"
            or identity["migration"].get("status") != "PASS"
        ):
            raise RuntimeError("migration_downgrade_policy_head_unreviewed")
        container("migration-drift", ["alembic", "check"])
        container(
            "migration-coverage-empty-before",
            ["python", "-c", MIGRATION_REVISION_PROBE, "0048"],
            timeout=60,
        )
        coverage_empty_before = json.loads(
            (args.output / "migration-coverage-empty-before.log").read_bytes()
        )
        if not native_migration_json_equal(
            coverage_empty_before, expected_migration_revision_readback("0048")
        ):
            raise RuntimeError("coverage_current_head_readback_invalid")
        container(
            "migration-coverage-empty-downgrade",
            ["alembic", "downgrade", "0047"],
            timeout=60,
        )
        container(
            "migration-coverage-empty-readback",
            ["python", "-c", COVERAGE_ABSENT_PROBE],
            timeout=60,
        )
        coverage_empty_down = json.loads(
            (args.output / "migration-coverage-empty-readback.log").read_bytes()
        )
        if not native_migration_json_equal(
            coverage_empty_down, expected_coverage_absent_readback()
        ):
            raise RuntimeError("coverage_empty_downgrade_readback_invalid")
        container(
            "migration-native-identity-empty-before",
            [
                "python",
                "-c",
                MIGRATION_REVISION_PROBE,
                "0047",
                "native-identity-0047-empty-before",
            ],
        )
        native_empty_before = json.loads(
            (args.output / "migration-native-identity-empty-before.log").read_text()
        )
        if not native_migration_json_equal(
            native_empty_before,
            native_identity_empty_before_readback(
                native_empty_before.get("revision_readback"),
                native_empty_before.get("object_custody_sha256"),
            ),
        ):
            raise RuntimeError("native_identity_empty_before_readback_mismatch")
        container(
            "migration-native-identity-empty-downgrade",
            ["alembic", "downgrade", "0046"],
        )
        container(
            "migration-native-identity-empty-readback",
            [
                "python",
                "-c",
                MIGRATION_REVISION_PROBE,
                "0046",
                "native-identity-0047-empty-down",
            ],
        )
        native_empty_readback = json.loads(
            (args.output / "migration-native-identity-empty-readback.log").read_text()
        )
        if not native_migration_json_equal(
            native_empty_readback,
            expected_native_identity_empty_downgrade_readback(
                native_empty_before["object_custody_sha256"]
            ),
        ):
            raise RuntimeError(
                "migration_native_identity_empty_downgrade_readback_invalid"
            )
        container("migration-bundle-empty-downgrade", ["alembic", "downgrade", "0045"])
        container(
            "migration-bundle-empty-readback",
            ["python", "-c", MIGRATION_REVISION_PROBE, "0045"],
        )
        bundle_empty_readback = json.loads(
            (args.output / "migration-bundle-empty-readback.log").read_text()
        )
        if bundle_empty_readback != expected_migration_revision_readback("0045"):
            raise RuntimeError("migration_bundle_empty_downgrade_readback_invalid")
        # Prove the repaired trigger restores the exact DB0044 body only on an
        # empty HWM table before checking the existing empty-table downgrade.
        container("migration-guard-empty-downgrade", ["alembic", "downgrade", "0044"])
        container(
            "migration-guard-empty-readback",
            ["python", "-c", MIGRATION_REVISION_PROBE, "0044"],
        )
        guard_empty_readback = json.loads(
            (args.output / "migration-guard-empty-readback.log").read_text()
        )
        if guard_empty_readback != expected_migration_revision_readback("0044"):
            raise RuntimeError("migration_guard_empty_downgrade_readback_invalid")
        # Prove each append-only diagnostic table can be removed only empty,
        # including a committed DB0043 readback before reaching DB0042.
        container("migration-hwm-empty-downgrade", ["alembic", "downgrade", "0043"])
        container(
            "migration-hwm-empty-readback",
            ["python", "-c", MIGRATION_REVISION_PROBE, "0043"],
        )
        hwm_empty_readback = json.loads(
            (args.output / "migration-hwm-empty-readback.log").read_text()
        )
        if hwm_empty_readback != expected_migration_revision_readback("0043"):
            raise RuntimeError("migration_hwm_empty_downgrade_readback_invalid")
        container("migration-empty-downgrade", ["alembic", "downgrade", "0042"])
        container(
            "migration-empty-readback",
            ["python", "-c", MIGRATION_REVISION_PROBE, "0042"],
        )
        empty_readback = json.loads(
            (args.output / "migration-empty-readback.log").read_text()
        )
        if empty_readback != expected_migration_revision_readback("0042"):
            raise RuntimeError("migration_empty_downgrade_readback_invalid")
        container("migration-reupgrade", ["alembic", "upgrade", "head"])
        container(
            "migration-reupgrade-identity",
            ["python", "-m", "scripts.verify_migration_identity"],
        )
        if identity["migration"] != json.loads(
            (args.output / "migration-reupgrade-identity.log").read_text()
        ):
            raise RuntimeError("migration_reupgrade_identity_changed")
        container(
            "migration-reupgrade-readback",
            ["python", "-c", MIGRATION_REVISION_PROBE, "0048"],
        )
        reupgrade_readback = json.loads(
            (args.output / "migration-reupgrade-readback.log").read_text()
        )
        if reupgrade_readback != expected_migration_revision_readback("0048"):
            raise RuntimeError("migration_reupgrade_readback_invalid")
        container("migration-reupgrade-drift", ["alembic", "check"])
        identity["migration_empty_roundtrip"] = {
            "source_head": "0048",
            "coverage_before_readback": coverage_empty_before,
            "coverage_down_revision": "0047",
            "coverage_down_readback": coverage_empty_down,
            "native_identity_before_readback": native_empty_before,
            "native_identity_down_revision": "0046",
            "native_identity_down_readback": native_empty_readback,
            "bundle_down_revision": "0045",
            "bundle_down_readback": bundle_empty_readback,
            "guard_down_revision": "0044",
            "guard_down_readback": guard_empty_readback,
            "hwm_down_revision": "0043",
            "hwm_down_readback": hwm_empty_readback,
            "down_revision": "0042",
            "down_readback": empty_readback,
            "reupgrade_readback": reupgrade_readback,
            "reupgrade_identity": "PASS",
            "schema_drift": "PASS",
        }
        container(
            "migration-downgrade-denial",
            ["alembic", "downgrade", "0041"],
            expected_refusal=DOWNGRADE_DENIAL,
        )
        verify_current_downgrade_denial_log(
            args.output / "migration-downgrade-denial.log"
        )
        container(
            "migration-reidentity",
            ["python", "-m", "scripts.verify_migration_identity"],
        )
        if identity["migration"] != json.loads(
            (args.output / "migration-reidentity.log").read_text()
        ):
            raise RuntimeError("migration_denial_identity_changed")
        container(
            "migration-denial-readback",
            ["python", "-c", MIGRATION_REVISION_PROBE, "0048"],
        )
        denial_readback = json.loads(
            (args.output / "migration-denial-readback.log").read_text()
        )
        if denial_readback != expected_migration_revision_readback("0048"):
            raise RuntimeError("migration_denial_transaction_rollback_unverified")
        container("migration-redrift", ["alembic", "check"])
        identity["migration_downgrade_policy"] = {
            "source_head": "0048",
            "target": "0041",
            "result": "DENIED",
            "denied_at": "0042",
            "policy_refusal": DOWNGRADE_DENIAL,
            "database_head_after": "0048",
            "rollback_readback": denial_readback,
            "schema_drift": "PASS",
        }
        if args.suite in ("full", "postgres"):
            identity["runtime_bundle_custody_preflight"] = (
                run_runtime_bundle_custody_preflight(
                    container, test_results=test_results, mounts=test_results_mount
                )
            )
            # Check the committed journal retention schema before the long
            # PostgreSQL suite. A failure is a hard denial; its allowlisted
            # mismatch category is retained in the JUnit artifact.
            container(
                "event-journal-schema-preflight",
                [
                    "python",
                    "-m",
                    "scripts.hermetic_pytest",
                    "-p",
                    "no:cacheprovider",
                    "--tb=no",
                    "--show-capture=no",
                    "-o",
                    "junit_logging=no",
                    "--junitxml=/validation-results/event-journal-schema-preflight.xml",
                    *(
                        f"{module.replace('.', '/')}.py::{case}"
                        for module, case in LEDGER_SCHEMA_PREFLIGHT_CASES
                    ),
                ],
                mounts=test_results_mount,
                timeout=5 * 60,
            )
            identity["event_journal_schema_preflight"] = verify_pytest_report(
                test_results / "event-journal-schema-preflight.xml",
                required_cases=LEDGER_SCHEMA_PREFLIGHT_CASES,
            )
            # Fail early on the isolated 0029 claim boundary before the long
            # PostgreSQL suite. Suppress raw DB tracebacks in this diagnostic
            # stage; the test exposes only allowlisted PostgreSQL metadata.
            container(
                "gate3-postgres-preflight",
                [
                    "python",
                    "-m",
                    "scripts.hermetic_pytest",
                    "-p",
                    "no:cacheprovider",
                    "--tb=no",
                    "--show-capture=no",
                    "-o",
                    "junit_logging=no",
                    "--junitxml=/validation-results/gate3-postgres-preflight.xml",
                    *(
                        f"{module.replace('.', '/')}.py::{case}"
                        for module, case in GATE3_PREFLIGHT_CASES
                    ),
                ],
                mounts=test_results_mount,
                timeout=15 * 60,
            )
            identity["gate3_preflight"] = verify_pytest_report(
                test_results / "gate3-postgres-preflight.xml",
                required_cases=GATE3_PREFLIGHT_CASES,
            )
            postgres_command = [
                "python",
                "-m",
                "scripts.hermetic_pytest",
                "-p",
                "no:cacheprovider",
                "-ra",
                "-vv",
                "--junitxml=/validation-results/postgres-intent.xml",
            ]
            if args.suite == "postgres":
                postgres_command += [
                    "-p",
                    "scripts.verify_linux_shard",
                    "--ctcc-linux-suite=postgres",
                    "--ctcc-linux-collection-json=/validation-results/postgres-collection.json",
                    "tests",
                ]
            else:
                postgres_command += sorted(POSTGRES_TEST_PATHS)
            container(
                "postgres-intent",
                postgres_command,
                mounts=test_results_mount,
                extra_environment=(
                    "-e",
                    "CTCC_COVERAGE_RECEIPT_DIR=/validation-results",
                    *(
                        (
                            "-e",
                            "CTCC_RUN_B3_PREFIX_33=1",
                            "-e",
                            "CTCC_B3_SCALE_RECEIPT_DIR=/validation-results",
                        )
                        if args.suite == "postgres"
                        else ()
                    ),
                ),
                timeout=POSTGRES_TEST_TIMEOUT_SECONDS,
            )
            identity["postgres_tests"] = verify_pytest_report(
                test_results / "postgres-intent.xml",
                required_cases=ARCHIVE_REQUIRED_CASES,
                required_modules=tuple(
                    sorted(
                        path.removesuffix(".py").replace("/", ".")
                        for path in POSTGRES_TEST_PATHS
                    )
                ),
            )
            receipt_path = test_results / "controlled-writer-coverage-migration.json"
            if (
                receipt_path.is_symlink()
                or not receipt_path.is_file()
                or not 0 < receipt_path.stat().st_size <= 65536
            ):
                raise RuntimeError("coverage_migration_receipt_missing_or_unsafe")
            identity["coverage_migration_acceptance"] = (
                verify_coverage_migration_receipt(
                    read_evidence_json(receipt_path.read_bytes()),
                    manifest_sha256=identity["manifest_sha256"],
                    repository_sha256=files[
                        "app/database/repositories/controlled_writer_coverage.py"
                    ]["sha256"],
                    migration_sha256=files[
                        "migrations/versions/0048_controlled_writer_coverage.py"
                    ]["sha256"],
                )
            )
            # Current 0048 rows must survive its own refusal. The historical
            # small/wide 0047 experiment owns an independent database below.
            container(
                "migration-coverage-nonempty-before",
                ["python", "-c", COVERAGE_RETENTION_PROBE],
                timeout=60,
            )
            coverage_before = json.loads(
                (args.output / "migration-coverage-nonempty-before.log").read_bytes()
            )
            container(
                "migration-coverage-nonempty-denial",
                ["alembic", "downgrade", "0047"],
                expected_refusal=COVERAGE_DOWNGRADE_DENIAL,
                timeout=60,
            )
            container(
                "migration-coverage-nonempty-after",
                ["python", "-c", COVERAGE_RETENTION_PROBE],
                timeout=60,
            )
            coverage_after = json.loads(
                (args.output / "migration-coverage-nonempty-after.log").read_bytes()
            )
            identity["coverage_nonempty_downgrade_policy"] = (
                coverage_nonempty_downgrade_readback(coverage_before, coverage_after)
            )
            identity["predecessor_0047_validation"] = run_predecessor_0047_validation(
                run,
                container,
                postgres=postgres,
                suffix=suffix,
                test_results=test_results,
                mounts=test_results_mount,
            )
            identity["native_identity_nonempty_downgrade_policy"] = identity[
                "predecessor_0047_validation"
            ]["nonempty_downgrade_policy"]
            if args.suite == "postgres":
                identity["collection"] = verify_linux_collection(
                    test_results / "postgres-collection.json",
                    mode="postgres",
                    index=None,
                    report=identity["postgres_tests"],
                )
        if args.suite == "full":
            container(
                "linux-full",
                [
                    "python",
                    "-m",
                    "scripts.hermetic_pytest",
                    "-p",
                    "no:cacheprovider",
                    "-ra",
                    "--junitxml=/validation-results/linux-full.xml",
                    "tests",
                ],
                mounts=test_results_mount,
                timeout=FULL_TEST_TIMEOUT_SECONDS,
            )
            identity["linux_tests"] = verify_pytest_report(
                test_results / "linux-full.xml",
                required_cases=ARCHIVE_REQUIRED_CASES,
                required_modules=tuple(
                    identity["postgres_tests"]["required_module_passed"]
                ),
                allow_unrelated_skips=True,
            )
        if args.suite == "shard":
            stage = f"linux-shard-{args.shard_index}"
            container(
                stage,
                [
                    "python",
                    "-m",
                    "scripts.hermetic_pytest",
                    "-p",
                    "no:cacheprovider",
                    "-p",
                    "scripts.verify_linux_shard",
                    "-ra",
                    f"--junitxml=/validation-results/{stage}.xml",
                    "--ctcc-linux-suite=shard",
                    f"--ctcc-linux-shard-index={args.shard_index}",
                    f"--ctcc-linux-collection-json=/validation-results/{stage}-collection.json",
                    "tests",
                ],
                mounts=test_results_mount,
                timeout=FULL_TEST_TIMEOUT_SECONDS,
            )
            identity["linux_tests"] = verify_pytest_report(
                test_results / f"{stage}.xml",
                allow_unrelated_skips=True,
            )
            identity["collection"] = verify_linux_collection(
                test_results / f"{stage}-collection.json",
                mode="shard",
                index=args.shard_index,
                report=identity["linux_tests"],
            )
            identity["component_validation"] = "PASS"
            identity["hermetic_regression"] = "PENDING_UNION"
            return
        # Actual process death and server restarts, using explicitly synthetic
        # claims. This does not establish source, Demo or Live acceptance.
        probe_dir = args.output / "crash-probe"
        probe_dir.mkdir()
        probe_dir.chmod(0o777)  # Dedicated Linux validation output; no secrets.
        probe_marker = probe_dir / "durable.json"
        probe_container = f"ctcc-final-{suffix}-crash-seed"
        resources.append(("container", probe_container))
        probe_mount = (
            "--mount",
            f"type=bind,source={probe_dir.resolve()},target=/probe",
        )
        run.command(
            "crash-probe-start",
            [
                "docker",
                "run",
                "-d",
                "--name",
                probe_container,
                "--label",
                label,
                "--network",
                network,
                *probe_mount,
                *environment,
                "-e",
                "CTCC_HERMETIC_DURABILITY=1",
                image,
                "python",
                "-m",
                "scripts.qualification_durability_probe",
                "seed",
                "--marker",
                "/probe/durable.json",
            ],
        )
        wait_for_durable_seed(run, probe_container, probe_marker)
        run.command("crash-seed-readback-log", ["docker", "logs", probe_container])
        run.command(
            "process-sigkill", ["docker", "kill", "--signal=KILL", probe_container]
        )
        run.command("postgres-restart", ["docker", "restart", "--time", "10", postgres])
        run.command("redis-restart", ["docker", "restart", "--time", "10", redis])
        deadline = time.monotonic() + 120
        while subprocess.run(
            ["docker", "exec", postgres, "pg_isready", "-U", "ctcc", "-d", "ctcc"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        ).returncode:
            if time.monotonic() >= deadline:
                raise RuntimeError("restarted_postgres_not_ready")
            time.sleep(0.25)
        container(
            "crash-restart-independent-readback",
            [
                "python",
                "-m",
                "scripts.qualification_durability_probe",
                "verify",
                "--marker",
                "/probe/durable.json",
            ],
            mounts=(probe_mount[0], probe_mount[1] + ",readonly"),
            extra_environment=("-e", "CTCC_HERMETIC_DURABILITY=1"),
        )
        identity["synthetic_process_pg_redis_crash_durability"] = "PASS"
        identity["crash_probe_marker_sha256"] = hashlib.sha256(
            probe_marker.read_bytes()
        ).hexdigest()
        api = f"ctcc-final-{suffix}-api"
        resources.append(("container", api))
        run.command(
            "api-start",
            [
                "docker",
                "run",
                "-d",
                "--name",
                api,
                "--label",
                label,
                "--network",
                network,
                *environment,
                image,
            ],
        )
        run.command(
            "api-health", ["docker", "exec", api, "python", "-c", API_PROBE], timeout=90
        )
        run.command(
            "api-restart", ["docker", "restart", "--time", "10", api], timeout=60
        )
        run.command(
            "api-restart-health",
            ["docker", "exec", api, "python", "-c", API_PROBE],
            timeout=90,
        )
        run.command("api-logs", ["docker", "logs", api], timeout=30)
        identity["api_service_restart"] = "PASS"
        identity["live_restart_acceptance"] = False
        identity["component_validation"] = "PASS"
        identity["hermetic_regression"] = (
            "PASS" if args.suite == "full" else "PENDING_UNION"
        )
    except Exception as error:
        identity["component_validation"] = "FAIL"
        identity["hermetic_regression"] = "FAIL"
        identity["failure_type"] = type(error).__name__
        raise
    finally:
        cleanup = cleanup_resources(resources, suffix)
        (args.output / "cleanup.json").write_text(json.dumps(cleanup, indent=2))
        cleanup_failed = any(item["status"] == "FAIL" for item in cleanup)
        identity["cleanup"] = "FAIL" if cleanup_failed else "PASS"
        if cleanup_failed:
            identity["component_validation"] = "FAIL"
            identity["hermetic_regression"] = "FAIL"
        (args.output / "identity.json").write_text(json.dumps(identity, indent=2))
        evidence_files = (
            *args.output.iterdir(),
            *test_results.glob("*.xml"),
            *test_results.glob("*-collection.json"),
            *test_results.glob("coverage-*.json"),
            *test_results.glob("controlled-writer-coverage-*.json"),
            *(args.output / "crash-probe").glob("*.json"),
        )
        hashes = {
            p.relative_to(args.output).as_posix(): hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
            for p in evidence_files
            if p.is_file() and not p.is_symlink()
        }
        (args.output / "results.sha256.json").write_text(json.dumps(hashes, indent=2))
        if cleanup_failed:
            raise RuntimeError("validation_resource_cleanup_unconfirmed")


if __name__ == "__main__":
    main()
