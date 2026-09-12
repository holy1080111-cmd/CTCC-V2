"""Durable Demo qualification reservations, not order authorization.

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "qualification_account_scopes",
        sa.Column("environment", sa.String(8), primary_key=True),
        sa.Column("account_id", sa.String(32), primary_key=True),
        sa.Column("settlement_currency", sa.String(16), primary_key=True),
        sa.Column("account_revision", sa.BigInteger(), nullable=False),
        sa.Column("ledger_revision", sa.BigInteger(), nullable=False),
        sa.Column("claims_json", sa.Text()),
        sa.Column("claims_sha256", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "environment = 'demo'",
            name=op.f("ck_qualification_account_scopes_demo_only"),
        ),
        sa.CheckConstraint(
            "account_id ~ '^[0-9]{1,32}$'",
            name=op.f("ck_qualification_account_scopes_exact_uid"),
        ),
        sa.CheckConstraint(
            "settlement_currency ~ '^[A-Z0-9]{1,16}$'",
            name=op.f("ck_qualification_account_scopes_currency"),
        ),
        sa.CheckConstraint(
            "account_revision >= 0 AND ledger_revision >= account_revision",
            name=op.f("ck_qualification_account_scopes_revisions"),
        ),
        sa.CheckConstraint(
            "(account_revision = 0 AND claims_json IS NULL AND claims_sha256 IS NULL) OR (account_revision > 0 AND claims_json IS NOT NULL AND claims_sha256 ~ '^[a-f0-9]{64}$')",
            name=op.f("ck_qualification_account_scopes_claims_pair"),
        ),
        sa.CheckConstraint(
            "octet_length(claims_json) <= 8388608",
            name=op.f("ck_qualification_account_scopes_claims_bound"),
        ),
    )
    op.create_table(
        "qualification_reservations",
        sa.Column("reservation_id", sa.String(64), primary_key=True),
        sa.Column("environment", sa.String(8), nullable=False),
        sa.Column("account_id", sa.String(32), nullable=False),
        sa.Column("settlement_currency", sa.String(16), nullable=False),
        sa.Column("original_event_key", sa.String(64), nullable=False),
        sa.Column("report_id", sa.String(96), nullable=False),
        sa.Column("instrument_id", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("correlation_group", sa.String(128), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("coverage_json", sa.Text(), nullable=False),
        sa.Column("risk_amount", sa.Numeric(60, 20), nullable=False),
        sa.Column("margin_amount", sa.Numeric(60, 20), nullable=False),
        sa.Column("notional_amount", sa.Numeric(60, 20), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("state_revision", sa.BigInteger(), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency"],
            [
                "qualification_account_scopes.environment",
                "qualification_account_scopes.account_id",
                "qualification_account_scopes.settlement_currency",
            ],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "environment",
            "account_id",
            "settlement_currency",
            "original_event_key",
            name="uq_qualification_reservations_scope_event",
        ),
        sa.CheckConstraint(
            "environment = 'demo'", name=op.f("ck_qualification_reservations_demo_only")
        ),
        sa.CheckConstraint(
            "direction IN ('long','short')",
            name=op.f("ck_qualification_reservations_direction"),
        ),
        sa.CheckConstraint(
            "state IN ('reserved','consumed','uncertain','reconciled_flat')",
            name=op.f("ck_qualification_reservations_state"),
        ),
        sa.CheckConstraint(
            "state_revision >= 1",
            name=op.f("ck_qualification_reservations_state_revision"),
        ),
        sa.CheckConstraint(
            "risk_amount > 0 AND margin_amount > 0 AND notional_amount > 0",
            name=op.f("ck_qualification_reservations_amounts"),
        ),
        sa.CheckConstraint(
            "deadline > created_at AND updated_at >= created_at",
            name=op.f("ck_qualification_reservations_clocks"),
        ),
        sa.CheckConstraint(
            "octet_length(request_json) <= 16777216",
            name=op.f("ck_qualification_reservations_request_bound"),
        ),
        sa.CheckConstraint(
            "octet_length(coverage_json) <= 16384",
            name=op.f("ck_qualification_reservations_coverage_bound"),
        ),
        sa.CheckConstraint(
            "reservation_id ~ '^[a-f0-9]{64}$' AND original_event_key ~ '^[a-f0-9]{64}$' AND request_sha256 ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_qualification_reservations_digests"),
        ),
    )
    op.create_table(
        "qualification_reservation_transitions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("reservation_id", sa.String(64), nullable=False),
        sa.Column("state_revision", sa.BigInteger(), nullable=False),
        sa.Column("from_state", sa.String(24)),
        sa.Column("to_state", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=False),
        sa.Column("evidence_json", sa.Text()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["qualification_reservations.reservation_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("reservation_id", "state_revision"),
        sa.CheckConstraint(
            "state_revision >= 1",
            name=op.f("ck_qualification_reservation_transitions_state_revision"),
        ),
        sa.CheckConstraint(
            "to_state IN ('reserved','consumed','uncertain','reconciled_flat')",
            name=op.f("ck_qualification_reservation_transitions_to_state"),
        ),
        sa.CheckConstraint(
            "octet_length(evidence_json) <= 8388608",
            name=op.f("ck_qualification_reservation_transitions_evidence_bound"),
        ),
    )
    op.execute("""
        CREATE FUNCTION qualification_ledger_immutable() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'qualification_ledger_immutable';
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER qualification_scope_no_delete BEFORE DELETE
          ON qualification_account_scopes FOR EACH ROW EXECUTE FUNCTION qualification_ledger_immutable();
    """)
    op.execute("""
        CREATE TRIGGER qualification_reservation_no_delete BEFORE DELETE
          ON qualification_reservations FOR EACH ROW EXECUTE FUNCTION qualification_ledger_immutable();
    """)
    op.execute("""
        CREATE TRIGGER qualification_transition_immutable BEFORE UPDATE OR DELETE
          ON qualification_reservation_transitions FOR EACH ROW EXECUTE FUNCTION qualification_ledger_immutable();
    """)
    op.execute("""
        CREATE FUNCTION qualification_reservation_update() RETURNS trigger AS $$
        BEGIN
          IF (to_jsonb(NEW) - ARRAY['state','state_revision','updated_at']) IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY['state','state_revision','updated_at'])
             OR NEW.state_revision <> OLD.state_revision + 1
             OR NEW.updated_at < OLD.updated_at
             OR NOT ((OLD.state = 'reserved' AND NEW.state IN ('consumed','uncertain','reconciled_flat'))
                     OR (OLD.state = 'consumed' AND NEW.state IN ('uncertain','reconciled_flat'))
                     OR (OLD.state = 'uncertain' AND NEW.state = 'reconciled_flat')) THEN
            RAISE EXCEPTION 'qualification_reservation_update_denied';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER qualification_reservation_update_guard BEFORE UPDATE
          ON qualification_reservations FOR EACH ROW EXECUTE FUNCTION qualification_reservation_update();
    """)
    op.execute("""
        CREATE FUNCTION qualification_scope_update() RETURNS trigger AS $$
        BEGIN
          IF ROW(NEW.environment,NEW.account_id,NEW.settlement_currency) IS DISTINCT FROM
             ROW(OLD.environment,OLD.account_id,OLD.settlement_currency)
             OR NEW.ledger_revision <> OLD.ledger_revision + 1
             OR NEW.account_revision NOT IN (OLD.account_revision,OLD.account_revision + 1)
             OR (OLD.ledger_revision > 0 AND NEW.updated_at < OLD.updated_at)
             OR ((ROW(NEW.claims_json,NEW.claims_sha256) IS DISTINCT FROM ROW(OLD.claims_json,OLD.claims_sha256))
                 <> (NEW.account_revision = OLD.account_revision + 1)) THEN
            RAISE EXCEPTION 'qualification_scope_update_denied';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER qualification_scope_update_guard BEFORE UPDATE
          ON qualification_account_scopes FOR EACH ROW EXECUTE FUNCTION qualification_scope_update();
    """)


def downgrade():
    # Explicit schema rollback is not a runtime release/expiry operation.
    op.drop_table("qualification_reservation_transitions")
    op.drop_table("qualification_reservations")
    op.drop_table("qualification_account_scopes")
    op.execute("DROP FUNCTION qualification_scope_update()")
    op.execute("DROP FUNCTION qualification_reservation_update()")
    op.execute("DROP FUNCTION qualification_ledger_immutable()")
