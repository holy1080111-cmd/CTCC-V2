"""Install an ungranted restricted original-B1 frame read, native seal pending.

Revision ID: 0052
Revises: 0051

This is NEW private raw-data EXECUTE capability. No old metadata reader is
automatically granted it. A separately reviewed collector grant is required;
that grant must be explicitly revoked before this owner-only downgrade.
No old DB0050/0051 function, original row or scope revision is rewritten.
"""

from alembic import op
from sqlalchemy import text

from app.domain import account_history_original_frame_v1 as policy
from app.domain import account_history_original_source_catalog_v1 as source_guard
from app.domain import account_history_partition_catalog_v1 as parent
from app.domain import prospective_event_cutover_catalog_v2 as cutover

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None
_FROZEN_DDL_SHA256 = "abe0ca020c2e6ffc5fd551b043c7d8db02812920cd5f0146c7881fab70d08dbb"
_FROZEN_SOURCE_CONTRACT_SHA256 = (
    "790d159d0cbbce8346f7431f91f8d6fe2fe3ef3d8fcc05c5c322090d154d77a3"
)
_LOCK = (
    "LOCK TABLE public.qualification_account_scopes, public.demo_account_controls, "
    "public.demo_account_observation_batches, public.demo_account_capture_events, "
    "public.demo_account_history_native_members, "
    "public.demo_account_history_partition_checkpoints, "
    "public.demo_account_observation_facts, public.demo_account_observation_coverage, "
    "public.demo_account_observation_findings IN ACCESS EXCLUSIVE MODE NOWAIT"
)


def _owner(bind):
    value = bind.scalar(
        text("""
      SELECT jsonb_build_object('oid',r.oid::bigint,'current',current_user,
        'session',session_user,'role',current_setting('role'),'superuser',r.rolsuper,
        'public_acl_create',EXISTS(SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL
          aclexplode(n.nspacl) x WHERE n.nspname='public' AND x.grantee=0
            AND x.privilege_type='CREATE'),
        'default_grants',EXISTS(SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL
          aclexplode(d.defaclacl) x WHERE d.defaclrole=r.oid AND x.grantee<>r.oid))
      FROM pg_roles r WHERE r.rolname=current_user
    """)
    )
    if (
        type(value) is not dict
        or value["current"] != value["session"]
        or value["role"] != "none"
        or value["superuser"] is not True
        or value["public_acl_create"] is not False
        or value["default_grants"] is not False
        or type(value["oid"]) is not int
    ):
        raise RuntimeError("original_frame_migration_owner")
    return value["oid"]


def _parents(bind, owner):
    captured = bind.scalar(text(parent.CATALOG_SQL))
    parent.verify_account_history_partition_catalog(captured)
    cutover.verify_catalog(bind.scalar(text(cutover.CATALOG_SQL)))
    # A separate original-source structural diagnostic under these actual
    # table locks. It deliberately does not mint a deparser/native baseline.
    source_guard.verify_source_catalog(
        bind.scalar(text(source_guard.CATALOG_SQL)),
        captured,
        require_native_baseline=False,
    )
    if any(
        row["owner_oid"] != owner
        for row in [*captured["parents"], *captured["tables"], *captured["functions"]]
    ):
        raise RuntimeError("original_frame_migration_owner")
    return captured


def _identity():
    if policy.DDL_SHA256 != _FROZEN_DDL_SHA256:
        raise RuntimeError("original_frame_frozen_0052_ddl_changed")
    if source_guard.CONTRACT_SHA256 != _FROZEN_SOURCE_CONTRACT_SHA256:
        raise RuntimeError("original_frame_frozen_source_contract_changed")


def upgrade():
    _identity()
    op.execute(_LOCK)
    bind = op.get_bind()
    captured = _parents(bind, _owner(bind))
    if bind.scalar(text(policy.CATALOG_SQL))["functions"] != []:
        raise RuntimeError("original_frame_existing_function")
    # Preserve frozen JSON-bearing DDL; text() would parse :null as a bind.
    bind.exec_driver_sql(policy.DDL)
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{policy.FUNCTION}({policy.SIGNATURE}) FROM PUBLIC"
    )
    # Deliberately NO reader grant, table grant or automatic old-ACL copying.
    policy.verify_catalog(
        bind.scalar(text(policy.CATALOG_SQL)), captured, require_native_baseline=False
    )


def downgrade():
    _identity()
    op.execute(_LOCK)
    bind = op.get_bind()
    captured = _parents(bind, _owner(bind))
    # A live collector grant is an explicit deployment capability. Refuse an
    # implicit rollback of it; the operator procedure must revoke it first.
    policy.verify_catalog(
        bind.scalar(text(policy.CATALOG_SQL)), captured, require_native_baseline=False
    )
    op.execute(f"DROP FUNCTION public.{policy.FUNCTION}({policy.SIGNATURE})")
