"""DB0040 protects a separate typed B3 epoch without rewriting old evidence."""

import importlib.util
from pathlib import Path

from app.database.repositories import account_observation_index_typed as persisted
from app.trade_qualification import account_observation_index as observed


def _migration():
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0040_account_observation_typed.py"
    )
    spec = importlib.util.spec_from_file_location("ctcc_db0040", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordedOp:
    def __init__(self):
        self.commands = []

    def execute(self, sql):
        self.commands.append(str(sql).strip())


def test_typed_migration_pins_separate_policies_and_source_time_domains():
    migration = _migration()
    assert migration.revision == "0040" and migration.down_revision == "0039"
    assert migration._TYPED_POLICY_SHA256 == (
        "959fb552875d01b06fdd0545dc914a4fa36a880320c7c6ce8bc796e6ffdcdb21"
    )
    assert migration._PERSISTENCE_POLICY_SHA256 == (
        "d4e9a8e4fea2de6d4b534c7be4d3ab5e3713409dd22640f6adbca5e2e483cd8e"
    )
    assert migration._TYPED_POLICY_SHA256 == observed.TYPED_POLICY_SHA256
    assert migration._PERSISTENCE_POLICY_SHA256 == persisted.PERSISTENCE_POLICY_SHA256
    guard = migration._batch_guard()
    assert "__PERSISTENCE_POLICY_SHA__" not in guard
    assert "__TYPED_POLICY_SHA__" not in guard
    assert "ctcc.account_observation_typed_batch.v1" in guard
    assert "ctcc.observed_account_execution_window.v3" in guard
    assert "ctcc.account_observation_typed_continuation.v1" in guard
    assert "j->>'policy_sha256' IS DISTINCT FROM" in guard
    assert "j->>'typed_policy_sha256' IS DISTINCT FROM" in guard
    assert "r->>'policy_sha256' IS DISTINCT FROM" in guard
    assert "prior.document_json::jsonb->>'policy_sha256'" in guard
    assert (
        "src.event_json::jsonb->>'outcome' IS DISTINCT FROM 'complete_recorded'"
        in guard
    )
    assert "FOR KEY SHARE" in guard
    assert "pg_advisory_xact_lock(lock_key)" in guard
    assert "NEW.db_recorded_at := clock_timestamp()" in guard
    assert "NEW.db_recorded_at < src.db_recorded_at" in guard
    assert "j->'source_authenticity_verified' IS DISTINCT FROM 'false'::jsonb" in guard
    assert "NEW.previous_sha256 IS DISTINCT FROM prior.event_sha256" in guard
    assert "^[a-f0-9]{64}$" in guard
    member = migration._member_guard()
    assert "j ? 'generation_at'" in member
    assert (
        "j->>'source_ts_semantics' IS DISTINCT FROM NEW.source_ts_semantics" in member
    )
    assert "j->>'source_ts_at'" in member
    assert "j->>'fill_at'" in member
    assert "NEW.family='bills' AND NEW.fill_at IS NOT NULL" in member
    assert "'time_domain',NEW.time_domain" in member


def test_typed_migration_requires_contiguous_complete_members_and_is_append_only():
    migration = _migration()
    recorder = _RecordedOp()
    migration.op = recorder
    migration.upgrade()
    sql = "\n".join(recorder.commands)
    for table in migration._TABLES:
        assert f"CREATE TABLE public.{table}" in sql
        assert (
            f"public.{table} FOR EACH ROW EXECUTE FUNCTION public.qualification_ledger_immutable()"
            in sql
        )
        assert (
            f"public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.qualification_ledger_immutable()"
            in sql
        )
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "actual.n IS DISTINCT FROM expected" in sql
    assert "actual.first IS DISTINCT FROM 1::bigint" in sql
    assert "actual.digest IS DISTINCT FROM" in sql
    assert "account_observation_typed_prefix_count_denied" in sql
    assert "source_ts_at TIMESTAMPTZ NOT NULL" in sql
    assert "source_ts_semantics VARCHAR(40) NOT NULL" in sql
    assert "fill_record_generated_at" in sql
    assert "balance_update_completed_at" in sql
    assert (
        "REFERENCES public.demo_account_capture_events(capture_id,sequence) ON DELETE RESTRICT"
        in sql
    )
    assert "CREATE OR REPLACE" not in sql
    assert "UPDATE public.demo_account_observation_batches" not in sql
    assert "DELETE FROM public.demo_account_observation_batches" not in sql


def test_typed_downgrade_checks_empty_under_nowait_lock_before_drop():
    migration = _migration()
    recorder = _RecordedOp()
    migration.op = recorder
    migration.downgrade()
    assert recorder.commands[0].startswith(
        "LOCK TABLE public.demo_account_capture_events,"
    )
    assert "ACCESS EXCLUSIVE MODE NOWAIT" in recorder.commands[0]
    for table in migration._TABLES:
        assert f"public.{table}" in recorder.commands[0]
        assert f"EXISTS(SELECT 1 FROM public.{table})" in recorder.commands[1]
    assert "account_observation_typed_downgrade_requires_empty" in recorder.commands[1]
    assert recorder.commands[2].startswith("DROP TABLE public.")
    assert not any(
        "DROP TABLE public.demo_account_observation_batches" in command
        for command in recorder.commands
    )
