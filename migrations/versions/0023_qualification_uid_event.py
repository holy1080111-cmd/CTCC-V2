"""Keep one durable qualification event per exact Demo account UID.

Revision ID: 0023
Revises: 0022

The pre-existing scoped unique key includes settlement currency. A second
currency can therefore hold the same original event with another reservation
ID if a writer bypasses the repository's UID lock. Existing collisions require
forensic resolution; this migration never deletes or rewrites ledger history.
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

_TABLES = (
    "qualification_account_scopes",
    "qualification_reservations",
    "qualification_reservation_transitions",
)
_TRIGGERS = (
    ("qualification_account_scopes", "qualification_scope_no_truncate"),
    ("qualification_reservations", "qualification_reservation_no_truncate"),
    ("qualification_reservation_transitions", "qualification_transition_no_truncate"),
)
_UNIQUE = "uq_qualification_reservations_uid_event"


def upgrade():
    # No writer may insert between the duplicate audit and the new constraint.
    # Lock all trigger targets up front; NOWAIT never waits across live writers.
    op.execute(
        "LOCK TABLE qualification_account_scopes, qualification_reservations, "
        "qualification_reservation_transitions IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM qualification_reservations
            GROUP BY environment, account_id, original_event_key
            HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION 'qualification_uid_event_duplicates_preexisting';
          END IF;
        END $$;
    """)
    op.create_unique_constraint(
        _UNIQUE,
        "qualification_reservations",
        ["environment", "account_id", "original_event_key"],
    )
    for table, trigger in _TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION qualification_ledger_immutable()"
        )


def downgrade():
    # Removing this constraint or its retention guards with any ledger record
    # would make a previously rejected duplicate or erasure possible.
    op.execute(
        "LOCK TABLE qualification_account_scopes, qualification_reservations, "
        "qualification_reservation_transitions IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM qualification_account_scopes)
             OR EXISTS (SELECT 1 FROM qualification_reservations)
             OR EXISTS (SELECT 1 FROM qualification_reservation_transitions) THEN
            RAISE EXCEPTION 'qualification_uid_event_downgrade_requires_empty';
          END IF;
        END $$;
    """)
    for table, trigger in reversed(_TRIGGERS):
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
    op.drop_constraint(_UNIQUE, "qualification_reservations", type_="unique")
