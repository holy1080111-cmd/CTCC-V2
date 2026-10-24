"""Immutable original history members and32 checkpoints, without native authority.

Revision ID: 0050
Revises: 0049

The new native baseline starts pending. Upgrade uses explicit structural-only
DDL diagnostics, so an actual catalog can be captured before the formal denial.
Runtime admission always requires the independent native seal and role gate.
"""

from alembic import op
from sqlalchemy import text

from app.domain import account_history_partition_catalog_v1 as policy
from app.domain import controlled_writer_coverage_catalog_v1 as previous

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None
_FROZEN_DDL_SHA256 = "ea1673b244a9cdda216f49300045ef0f36633b292ac59da853b940291d7576bb"
_PARENTS_LOCK = "LOCK TABLE public.qualification_account_scopes, public.demo_account_controls, public.demo_account_observation_batches, public.demo_account_capture_events IN ACCESS EXCLUSIVE MODE NOWAIT"
_DOWNGRADE_LOCK = "LOCK TABLE public.qualification_account_scopes, public.demo_account_controls, public.demo_account_observation_batches, public.demo_account_capture_events, public.demo_account_history_native_members, public.demo_account_history_partition_checkpoints IN ACCESS EXCLUSIVE MODE NOWAIT"


def _owner(bind, catalog=None):
    value = bind.scalar(
        text("""
      SELECT jsonb_build_object('current_user',current_user,'session_user',session_user,
        'role',current_setting('role'),'oid',CAST(oid AS bigint),'superuser',rolsuper,
        'public_writable',EXISTS(SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL aclexplode(nullif(n.nspacl,'{}'::aclitem[])) x WHERE n.nspname='public' AND x.grantee=0 AND x.privilege_type='CREATE'),
        'default_grants',EXISTS(SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(nullif(d.defaclacl,'{}'::aclitem[])) x WHERE d.defaclrole=r.oid AND x.grantee<>r.oid))
      FROM pg_roles r WHERE rolname=current_user
    """)
    )
    if (
        type(value) is not dict
        or value["current_user"] != value["session_user"]
        or value["role"] != "none"
        or type(value["oid"]) is not int
        or value["superuser"] is not True
        or value["public_writable"] is not False
        or value["default_grants"] is not False
    ):
        raise RuntimeError("account_history_partition_migration_owner_denied")
    if catalog is not None and any(
        row["owner_oid"] != value["oid"]
        for row in [*catalog["tables"], *catalog["functions"], *catalog["acl_rows"]]
    ):
        raise RuntimeError("account_history_partition_migration_acl_denied")
    return value


def _identity():
    if policy.ddl_identity() != _FROZEN_DDL_SHA256:
        raise RuntimeError("account_history_partition_frozen_0050_ddl_changed")


def _checked_catalog(bind):
    """Structural migration-only diagnostic, never the formal runtime gate."""
    observed = bind.scalar(text(policy.CATALOG_SQL))
    policy.verify_account_history_partition_catalog(
        observed, require_native_baseline=False
    )
    _owner(bind, observed)
    return observed


def upgrade():
    _identity()
    op.execute(_PARENTS_LOCK)
    bind = op.get_bind()
    owner = _owner(bind)
    before = bind.scalar(text(previous._CATALOG_SQL))
    previous.verify_controlled_writer_coverage_catalog(before)
    if any(row["owner_oid"] != owner["oid"] for row in before["tables"]):
        raise RuntimeError("account_history_partition_parent_owner_denied")
    existing = bind.scalar(
        text(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname IN ('demo_account_history_native_members','demo_account_history_partition_checkpoints')"
        )
    )
    functions = bind.scalar(
        text(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname LIKE 'account_history_partition_v1_%'"
        )
    )
    if existing != 0 or functions != 0:
        raise RuntimeError("account_history_partition_existing_objects_denied")
    for statement in (
        *policy.TABLE_SQL,
        *policy.INDEX_SQL,
        *policy.FUNCTION_SQL.values(),
        *policy.TRIGGER_SQL,
    ):
        op.execute(statement)
    for table in policy.TABLES:
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC")
    for name, signature in policy.FUNCTION_SIGNATURES.items():
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC")
    _checked_catalog(bind)
    # This structural result is intentionally discarded: it is neither a
    # native baseline, runtime role proof, account observation nor permission.


def downgrade():
    # First operation locks the genuine parent writers and both new tables.
    # Preserve native55P03 and transaction rollback, never translate to warning.
    op.execute(_DOWNGRADE_LOCK)
    _identity()
    bind = op.get_bind()
    _checked_catalog(bind)
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.demo_account_history_native_members)
          OR EXISTS(SELECT 1 FROM public.demo_account_history_partition_checkpoints) THEN
          RAISE EXCEPTION 'account_history_partition_downgrade_requires_empty';
        END IF;
      END $$
    """)
    for table in reversed(policy.TABLES):
        op.execute(f"DROP TABLE public.{table}")
    for name in reversed(tuple(policy.FUNCTION_SIGNATURES)):
        op.execute(f"DROP FUNCTION public.{name}({policy.FUNCTION_SIGNATURES[name]})")
