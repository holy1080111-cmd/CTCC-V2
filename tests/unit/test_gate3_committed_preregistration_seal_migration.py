"""Offline shape checks for the independent 0030 seal commitment boundary.

Behavioral privilege, transaction and concurrency acceptance runs separately
against an isolated PostgreSQL database.
"""

import app.database.models  # noqa: F401 - register migration metadata
from app.database.base import Base
from tests.durable_migration_fixtures import RecordedDowngrade, load_migration


class Recorder:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, statement):
        self.sql.append(str(statement))


def test_0030_graph_and_new_tables_match_metadata():
    migration = load_migration("0030")
    assert migration.revision == "0030"
    assert migration.down_revision == "0029"
    recorder = Recorder()
    migration.op = recorder
    migration.upgrade()
    ddl = "\n".join(recorder.sql)
    for name in (
        "gate3_preregistration_seals",
        "gate3_preregistration_seal_acks",
    ):
        assert f"CREATE TABLE public.{name}" in ddl
        for constraint in Base.metadata.tables[name].constraints:
            assert constraint.name in ddl
        assert f"REVOKE ALL ON TABLE public.{name} FROM PUBLIC" in ddl
    # A rejected proposal must not reserve the window. Only the independent
    # committed ACK can own these identities.
    seal_constraints = {
        constraint.name
        for constraint in Base.metadata.tables[
            "gate3_preregistration_seals"
        ].constraints
    }
    ack_constraints = {
        constraint.name
        for constraint in Base.metadata.tables[
            "gate3_preregistration_seal_acks"
        ].constraints
    }
    assert "uq_gate3_preregistration_seals_window_key" not in seal_constraints
    assert "uq_gate3_preregistration_seals_holdout_id" not in seal_constraints
    assert "uq_gate3_preregistration_seal_acks_window_key" in ack_constraints
    assert "uq_gate3_preregistration_seal_acks_holdout_id" in ack_constraints
    assert "uq_gate3_preregistration_seal_acks_preregistration_id" in ack_constraints
    assert "CREATE ROLE" not in ddl
    assert "GRANT EXECUTE" not in ddl
    assert "gate3_prereg_direct_role_allowed('seal_publish')" in ddl
    assert "gate3_prereg_direct_role_allowed('seal_ack')" in ddl
    assert "gate3_prereg_direct_role_allowed('capture_publish')" in ddl
    assert "gate3_prereg_direct_role_allowed('capture_ack')" in ddl
    assert "FOR KEY SHARE NOWAIT" in ddl
    assert "acknowledged.acknowledged_at < pinned.planned_at" in ddl
    assert "CREATE OR REPLACE FUNCTION public.gate3_capture_schedule_claim_read" in ddl
    assert "JOIN public.gate3_preregistration_seal_acks" in ddl
    assert "gate3_prereg_downgrade_requires_empty" not in ddl


def test_0030_downgrade_requires_empty_new_evidence_and_preserves_0029():
    commands = RecordedDowngrade("0030").commands
    assert commands[0][0] == "execute"
    assert commands[0][1].startswith("LOCK TABLE public.gate3_capture_schedule_pins,")
    assert commands[0][1].endswith("IN ACCESS EXCLUSIVE MODE NOWAIT")
    assert "gate3_prereg_downgrade_requires_empty" in commands[1][1]
    assert ("drop", "gate3_preregistration_seal_acks") in commands
    assert ("drop", "gate3_preregistration_seals") in commands
    assert ("drop", "gate3_capture_schedule_key_claims") not in commands
    assert ("drop", "gate3_capture_schedule_pins") not in commands
    assert ("drop", "gate3_capture_schedule_claim_acks") not in commands
