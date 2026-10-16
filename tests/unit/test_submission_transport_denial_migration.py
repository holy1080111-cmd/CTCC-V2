"""DB0042 cannot turn an unverified private outcome into an outbox job."""

from app.database.models.submission_reporting import (
    QualificationReportProjectionReceipt,
    QualificationReportSpool,
)
from tests.durable_migration_fixtures import load_migration


class Recorder:
    def __init__(self):
        self.commands = []

    def execute(self, sql):
        self.commands.append(str(sql))


def test_db0042_is_persisted_outcome_denial_and_preserves_legacy_rows():
    migration = load_migration("0042")
    assert migration.down_revision == "0041"
    recorded = Recorder()
    migration.op = recorded
    migration.upgrade()
    statements = recorded.commands
    assert "ACCESS EXCLUSIVE MODE NOWAIT" in statements[0]
    assert "qualification_submission_outcomes" in statements[0]
    assert "qualification_report_spool" in statements[0]
    assert "qualification_report_projection_receipts" in statements[0]
    assert "WHERE eligible" in statements[1]
    assert (
        "EXISTS(SELECT 1 FROM public.qualification_report_projection_receipts)"
        in statements[1]
    )
    assert (
        "submission_transport_legacy_rows_require_forensic_resolution" in statements[1]
    )
    source = "\n".join(statements)
    assert "UPDATE public.qualification_report_spool" not in source
    assert "DELETE FROM public.qualification_report_spool" not in source
    assert "DELETE FROM public.qualification_submission_outcomes" not in source
    assert "CHECK (eligible=false)" in source
    assert "CHECK (false)" in source
    spool = migration._spool_guard()
    projection = migration._projection_guard()
    assert "o.outcome_json::jsonb" in spool
    assert "transport_authenticity_verified' IS DISTINCT FROM 'false'::jsonb" in spool
    assert "NEW.eligible IS DISTINCT FROM false" in spool
    assert "RAISE EXCEPTION 'submission_projection_transport_unverified'" in projection
    assert "RETURN NEW" not in projection
    assert "status='acknowledged'" not in spool


def test_db0042_downgrade_is_intentionally_irreversible():
    migration = load_migration("0042")
    recorded = Recorder()
    migration.op = recorded
    migration.downgrade()
    assert len(recorded.commands) == 1
    assert "submission_transport_authenticity_downgrade_denied" in recorded.commands[0]
    assert "CREATE OR REPLACE FUNCTION" not in recorded.commands[0]


def test_db0042_model_constraints_match_hard_denial():
    spool_constraints = {
        item.name for item in QualificationReportSpool.__table__.constraints
    }
    receipt_constraints = {
        item.name for item in QualificationReportProjectionReceipt.__table__.constraints
    }
    assert "ck_qualification_report_spool_transport_unverified" in spool_constraints
    assert (
        "ck_qualification_report_projection_receipts_transport_unverified"
        in receipt_constraints
    )
