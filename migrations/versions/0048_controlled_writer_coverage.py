"""Immutable prospective controlled-CTCC Demo coverage epochs.

Revision ID: 0048
Revises: 0047

No source/account initialization, role creation, Arm or execution permission.
The sole catalog declaration is source-pinned in the repository module.
"""

from alembic import op

from app.database.repositories.controlled_writer_coverage import (
    COVERAGE_FUNCTION_SIGNATURES,
    COVERAGE_FUNCTION_SQL,
    COVERAGE_INDEX_SQL,
    COVERAGE_TABLE_SQL,
    COVERAGE_TABLES,
    COVERAGE_TRIGGER_SPECS,
    COVERAGE_TRIGGER_SQL,
)

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    for statement in (*COVERAGE_TABLE_SQL, *COVERAGE_INDEX_SQL):
        op.execute(statement)
    for statement in COVERAGE_FUNCTION_SQL.values():
        op.execute(statement)
    for statement in COVERAGE_TRIGGER_SQL:
        op.execute(statement)
    for name, table, *_ in COVERAGE_TRIGGER_SPECS:
        op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
    for table in COVERAGE_TABLES:
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC")
    for name, arguments in COVERAGE_FUNCTION_SIGNATURES.items():
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}({arguments}) FROM PUBLIC")


def downgrade():
    # Removing the scope FK also removes its triggers on the existing parent.
    # Take that parent lock first so DROP cannot introduce a later waiting lock.
    op.execute(
        "LOCK TABLE public.demo_account_controls, public.demo_controlled_writer_coverage_scopes, public.demo_controlled_writer_coverage_journal IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.demo_controlled_writer_coverage_scopes)
          OR EXISTS(SELECT 1 FROM public.demo_controlled_writer_coverage_journal) THEN
          RAISE EXCEPTION 'controlled_writer_coverage_downgrade_requires_empty';
        END IF;
      END $$
    """)
    op.execute(
        "DROP FUNCTION public.controlled_writer_coverage_v1_read(text,text,bigint,text,text)"
    )
    op.execute(
        "DROP FUNCTION public.controlled_writer_coverage_v1_append(text,text,bigint,text,bytea,bytea,bytea)"
    )
    op.execute("DROP TABLE public.demo_controlled_writer_coverage_journal")
    op.execute("DROP TABLE public.demo_controlled_writer_coverage_scopes")
    for name in ("scope_guard", "journal_guard", "pair_guard", "immutable"):
        op.execute(f"DROP FUNCTION public.controlled_writer_coverage_v1_{name}()")
