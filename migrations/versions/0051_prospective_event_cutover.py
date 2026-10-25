"""Immutable prospective cutover markers linked to existing writer coverage.

Revision ID: 0051
Revises: 0050

Native0050 declarations and seal stay unchanged. The new0051 baseline remains
pending until an independently reviewed genuine PG17 catalog is captured.
"""

from alembic import op
from sqlalchemy import text

from app.domain import account_history_partition_catalog_v1 as parent
from app.domain import prospective_event_cutover_catalog_v2 as policy

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None
_FROZEN_DDL_SHA256 = "dba77c3af835900e4d49ff9aa17f5d47031aeb2c40cdc4f84566e9c08e020924"
_PARENT_LOCK = (
    "LOCK TABLE public.demo_account_controls, public.qualification_account_scopes, "
    "public.qualification_reservations, public.qualification_reservation_transitions, "
    "public.demo_controlled_writer_coverage_scopes, public.demo_controlled_writer_coverage_journal "
    "IN ACCESS EXCLUSIVE MODE NOWAIT"
)
_DOWNGRADE_LOCK = _PARENT_LOCK.replace(
    " IN ACCESS", ", public.demo_controlled_writer_coverage_cutovers IN ACCESS"
)


def _owner(bind):
    result = bind.scalar(
        text("""
      SELECT jsonb_build_object('oid',oid::bigint,'current',current_user,'session',session_user,
       'role',current_setting('role'),'superuser',rolsuper,
       'default_grants',EXISTS(SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL
        aclexplode(nullif(d.defaclacl,'{}'::aclitem[])) x WHERE d.defaclrole=r.oid AND x.grantee<>r.oid),
       'public_create',EXISTS(SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL
        aclexplode(nullif(n.nspacl,'{}'::aclitem[])) x WHERE n.nspname='public'
        AND x.grantee=0 AND x.privilege_type='CREATE'))
      FROM pg_roles r WHERE rolname=current_user
    """)
    )
    if (
        type(result) is not dict
        or result["current"] != result["session"]
        or result["role"] != "none"
        or result["superuser"] is not True
        or result["default_grants"] is not False
        or result["public_create"] is not False
    ):
        raise RuntimeError("prospective_cutover_migration_owner")
    return result["oid"]


def _identity():
    if policy.ddl_identity() != _FROZEN_DDL_SHA256:
        raise RuntimeError("prospective_cutover_frozen_0051_ddl_changed")


def _structural(bind):
    observed = bind.scalar(text(policy.CATALOG_SQL))
    policy.verify_catalog(observed, require_native_baseline=False)
    owner = _owner(bind)
    if any(
        row["owner_oid"] != owner
        for row in [*observed["parents"], *observed["tables"], *observed["functions"]]
    ):
        raise RuntimeError("prospective_cutover_migration_parent_owner")


def upgrade():
    _identity()
    op.execute(_PARENT_LOCK)
    bind = op.get_bind()
    _owner(bind)
    parent.verify_account_history_partition_catalog(
        bind.scalar(text(parent.CATALOG_SQL))
    )
    if (
        bind.scalar(
            text("""
      SELECT (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname='demo_controlled_writer_coverage_cutovers')
       +(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname LIKE 'controlled_writer_coverage_v2_cutover_%')
    """)
        )
        != 0
    ):
        raise RuntimeError("prospective_cutover_existing_objects")
    for statement in (
        policy.TABLE_SQL,
        *policy.FUNCTION_SQL.values(),
        *policy.TRIGGER_SQL,
    ):
        op.execute(statement)
    op.execute(f"REVOKE ALL ON TABLE public.{policy.TABLE} FROM PUBLIC")
    for name, signature in policy.FUNCTION_SIGNATURES.items():
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC")
    _structural(bind)


def downgrade():
    op.execute(_DOWNGRADE_LOCK)
    _identity()
    bind = op.get_bind()
    _structural(bind)
    op.execute("""
      DO $cutover_empty$
      BEGIN
        IF EXISTS(SELECT 1 FROM public.demo_controlled_writer_coverage_cutovers) THEN
          RAISE EXCEPTION 'prospective_cutover_downgrade_requires_empty';
        END IF;
      END
      $cutover_empty$
    """)
    op.execute(f"DROP TABLE public.{policy.TABLE}")
    for name in reversed(tuple(policy.FUNCTION_SIGNATURES)):
        op.execute(f"DROP FUNCTION public.{name}({policy.FUNCTION_SIGNATURES[name]})")
