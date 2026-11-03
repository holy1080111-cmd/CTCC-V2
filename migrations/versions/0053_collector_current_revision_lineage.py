"""Install ungranted owned-CURRENT lineage prerequisites, not complete claims.

Revision ID: 0053
Revises: 0052

No old claims/witness/source row is rewritten. The application role requires a
separately reviewed SELECT/INSERT grant; no custody/raw-read grants are added.
A nonempty audit table cannot be silently dropped by this downgrade.
"""

from alembic import op
from sqlalchemy import text

from app.domain import account_revision_collector_lineage_schema_v1 as schema

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None
_FROZEN_DDL_SHA256 = "edd0896ada9f1e07303f382deccffc7461bf2b23b5ea6ad0ab0b5c3bc5193ed9"
_PARENTS = (
    "qualification_account_scopes",
    "demo_account_controls",
    "demo_control_journal",
    "demo_account_capture_events",
    "public_runtime_raw_bundles_v1",
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
        raise RuntimeError("collector_lineage_migration_owner") from None
    owners = bind.execute(
        text("""
      SELECT c.relname,c.relowner::bigint,c.relkind::text,c.relpersistence::text
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relname IN(
        'qualification_account_scopes','demo_account_controls','demo_control_journal',
        'demo_account_capture_events','public_runtime_raw_bundles_v1')
    """)
    ).all()
    if (
        len(owners) != len(_PARENTS)
        or {item[0] for item in owners} != set(_PARENTS)
        or any(item[1:] != (row[0], "r", "p") for item in owners)
    ):
        raise RuntimeError("collector_lineage_migration_parent_owner") from None
    return row[0]


def _identity():
    if schema.DDL_SHA256 != _FROZEN_DDL_SHA256:
        raise RuntimeError("collector_lineage_frozen_0053_ddl_changed") from None


def upgrade():
    _identity()
    bind = op.get_bind()
    bind.exec_driver_sql(_LOCK)
    _owner(bind)
    if bind.scalar(
        text(
            "SELECT to_regclass('public.demo_collector_current_revision_lineages') IS NOT NULL"
        )
    ) or bind.scalar(
        text("""
              SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
              WHERE n.nspname='public' AND p.proname IN(
                'collector_current_revision_lineage_guard_v1',
                'collector_current_revision_lineage_immutable_v1'))
            """)
    ):
        raise RuntimeError("collector_lineage_existing_schema") from None
    # One fixed top-level statement per driver call. Never split a PL/pgSQL
    # function body; asyncpg prepared execution rejects multi-command strings.
    guard = (
        "CREATE FUNCTION public."
        + schema.GUARD
        + "() RETURNS trigger AS $ctcc$"
        + schema.GUARD_BODY
        + "$ctcc$ LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp;"
    )
    immutable = (
        "CREATE FUNCTION public."
        + schema.IMMUTABLE
        + "() RETURNS trigger AS $ctcc$"
        + schema.IMMUTABLE_BODY
        + "$ctcc$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;"
    )
    if schema.FUNCTIONS_DDL != guard + "\n" + immutable:
        raise RuntimeError(
            "collector_lineage_frozen_function_statement_changed"
        ) from None
    # This frozen tail contains only three flat CREATE TRIGGER and three
    # REVOKE statements, with no quoted semicolon or executable input.
    tail = tuple(item for item in schema.TRIGGERS_DDL.split(";") if item.strip())
    if len(tail) != 6:
        raise RuntimeError(
            "collector_lineage_frozen_trigger_statement_changed"
        ) from None
    for statement in (schema.TABLE_DDL, guard, immutable, *tail):
        bind.exec_driver_sql(statement)
    # Deliberately no application, reader, raw-custody or inherited ACL grant.


def downgrade():
    _identity()
    bind = op.get_bind()
    bind.exec_driver_sql(_LOCK)
    owner = _owner(bind)
    bind.exec_driver_sql(
        "LOCK TABLE public.demo_collector_current_revision_lineages IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    row = bind.execute(
        text("""
      SELECT c.relowner::bigint,
        EXISTS(SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
          WHERE x.grantee<>c.relowner),
        EXISTS(SELECT 1 FROM public.demo_collector_current_revision_lineages)
      FROM pg_class c WHERE c.oid='public.demo_collector_current_revision_lineages'::regclass
    """)
    ).one()
    if tuple(row) != (owner, False, False):
        raise RuntimeError(
            "collector_lineage_revoke_grants_and_preserve_audit"
        ) from None
    bind.exec_driver_sql("DROP TABLE public.demo_collector_current_revision_lineages")
    bind.exec_driver_sql(
        "DROP FUNCTION public.collector_current_revision_lineage_guard_v1()"
    )
    bind.exec_driver_sql(
        "DROP FUNCTION public.collector_current_revision_lineage_immutable_v1()"
    )
