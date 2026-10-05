"""Load actual durable migrations without importing the Alembic environment."""

import importlib.util
from pathlib import Path

MIGRATIONS = {
    "0017": "0017_qualification_reservations.py",
    "0018": "0018_submission_reporting.py",
    "0019": "0019_demo_control.py",
    "0020": "0020_account_capture_journal.py",
    "0021": "0021_account_observation_index.py",
    "0022": "0022_account_bill_archive_claim.py",
    "0023": "0023_qualification_uid_event.py",
}
TABLES = {
    # 0023 is constraint/trigger-only and has dedicated upgrade/downgrade
    # integration cases starting from the 0022 sandbox.
    "0017": (
        "qualification_account_scopes",
        "qualification_reservations",
        "qualification_reservation_transitions",
    ),
    "0018": (
        "qualification_submission_outcomes",
        "qualification_report_spool",
        "qualification_report_projection_receipts",
    ),
    "0019": ("demo_account_controls", "demo_control_journal"),
    "0020": ("demo_account_capture_events",),
    "0021": (
        "demo_account_observation_batches",
        "demo_account_observation_facts",
        "demo_account_observation_coverage",
        "demo_account_observation_findings",
    ),
    "0022": ("demo_account_bill_archive_claims",),
}
DOWNGRADE_LOCKS = {
    **TABLES,
    "0018": TABLES["0017"] + TABLES["0018"],
    "0020": ("qualification_account_scopes",) + TABLES["0020"],
    "0021": TABLES["0020"] + TABLES["0021"],
    "0022": ("qualification_account_scopes",) + TABLES["0022"],
}


def load_migration(revision):
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    spec = importlib.util.spec_from_file_location(
        "durable_migration_" + revision, path / MIGRATIONS[revision]
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordedDowngrade:
    def __init__(self, revision):
        self.commands = []
        module = load_migration(revision)
        module.op = self
        module.downgrade()

    def execute(self, sql):
        self.commands.append(("execute", str(sql).strip()))

    def drop_table(self, table):
        self.commands.append(("drop", table))
