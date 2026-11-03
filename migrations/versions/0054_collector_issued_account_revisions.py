"""Add immutable native-issuer revisions; install no application role grants.

Revision ID: 0054
Revises: 0053

This source-only proposal preserves every old lineage/source row. New exact
0054 catalog and native admissions are required separately; 0053 cannot admit it.
"""

from alembic import op
from sqlalchemy import text

from app.domain import account_issued_revision_schema_v1 as schema

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None
_FROZEN_DDL_SHA256 = "5f82b31ffe168f803d65e485c8296d1df49d3fb2cda6701b0216fea0d6be24ee"
_PARENTS = (
    "qualification_account_scopes",
    "demo_account_controls",
    "demo_control_journal",
    "demo_account_capture_events",
    "public_runtime_raw_bundles_v1",
    "demo_collector_current_revision_lineages",
)
_LOCK = (
    "LOCK TABLE "
    + ",".join("public." + name for name in _PARENTS)
    + " IN ACCESS EXCLUSIVE MODE NOWAIT"
)


def _owner(bind):
    row = bind.execute(
        text("""
      SELECT r.oid::bigint,current_user=session_user,current_setting('role')='none',r.rolsuper,
        EXISTS(SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) x
          WHERE n.nspname='public' AND x.grantee=0 AND x.privilege_type='CREATE'),
        EXISTS(SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) x
          WHERE d.defaclrole=r.oid AND x.grantee<>r.oid)
      FROM pg_roles r WHERE r.rolname=current_user
    """)
    ).one()
    if (
        type(row[0]) is not int
        or row[0] <= 0
        or tuple(row[1:]) != (True, True, True, False, False)
    ):
        raise RuntimeError("collector_issuer_migration_owner") from None
    owners = bind.execute(
        text("""
      SELECT c.relname,c.relowner::bigint,c.relkind::text,c.relpersistence::text
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relname IN(
        'qualification_account_scopes','demo_account_controls','demo_control_journal',
        'demo_account_capture_events','public_runtime_raw_bundles_v1',
        'demo_collector_current_revision_lineages')
    """)
    ).all()
    if (
        len(owners) != len(_PARENTS)
        or {item[0] for item in owners} != set(_PARENTS)
        or any(item[1:] != (row[0], "r", "p") for item in owners)
    ):
        raise RuntimeError("collector_issuer_migration_parent_owner") from None
    return row[0]


def _identity():
    if (
        schema.DDL_SHA256 != _FROZEN_DDL_SHA256
        or schema.FROZEN_DDL_SHA256 != _FROZEN_DDL_SHA256
    ):
        raise RuntimeError("collector_issuer_frozen_0054_ddl_changed") from None


def upgrade():
    _identity()
    bind = op.get_bind()
    bind.exec_driver_sql(_LOCK)
    _owner(bind)
    if bind.scalar(
        text(
            "SELECT to_regclass('public.demo_collector_issued_account_revisions') IS NOT NULL"
        )
    ) or bind.scalar(
        text("""
      SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND p.proname IN(
        'collector_issued_account_revision_guard_v1','collector_issued_account_revision_immutable_v1'))
    """)
    ):
        raise RuntimeError("collector_issuer_existing_schema") from None
    for statement in schema.statements():
        bind.exec_driver_sql(statement)
    # Deliberately no actor/reader/custody/raw-table/default/inherited ACL grants.


def downgrade():
    _identity()
    bind = op.get_bind()
    bind.exec_driver_sql(_LOCK)
    owner = _owner(bind)
    bind.exec_driver_sql(
        "LOCK TABLE public.demo_collector_issued_account_revisions IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    row = bind.execute(
        text("""
      SELECT c.relowner::bigint,
        EXISTS(SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
          WHERE x.grantee<>c.relowner),
        EXISTS(SELECT 1 FROM public.demo_collector_issued_account_revisions)
      FROM pg_class c WHERE c.oid='public.demo_collector_issued_account_revisions'::regclass
    """)
    ).one()
    if tuple(row) != (owner, False, False):
        raise RuntimeError(
            "collector_issuer_revoke_grants_and_preserve_audit"
        ) from None
    bind.exec_driver_sql("DROP TABLE public.demo_collector_issued_account_revisions")
    bind.exec_driver_sql(
        "DROP FUNCTION public.collector_issued_account_revision_guard_v1()"
    )
    bind.exec_driver_sql(
        "DROP FUNCTION public.collector_issued_account_revision_immutable_v1()"
    )
