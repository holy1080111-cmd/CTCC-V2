"""Run the deferred coverage pair guard under its existing admin owner.

Revision ID: 0049
Revises: 0048

No table/column grants, role creation, source initialization or write authority.
0048 effective DDL remains immutable; only this migration changes pair context.
"""

import copy

from alembic import op
from sqlalchemy import text

from app.domain import controlled_writer_coverage_catalog_v1 as policy

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

_LOCK_SQL = "LOCK TABLE public.demo_account_controls, public.demo_controlled_writer_coverage_scopes, public.demo_controlled_writer_coverage_journal IN ACCESS EXCLUSIVE MODE NOWAIT"
_PAIR_SIGNATURE = "public.controlled_writer_coverage_v1_pair_guard()"
_ACL_SQL = r"""
SELECT jsonb_build_object(
  'table_grants',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) x
    WHERE n.nspname='public' AND c.relname=ANY(ARRAY[__TABLES__]) AND x.grantee<>c.relowner),
  'column_grants',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_attribute a ON a.attrelid=c.oid
    CROSS JOIN LATERAL aclexplode(coalesce(a.attacl,'{}'::aclitem[])) x
    WHERE n.nspname='public' AND c.relname=ANY(ARRAY[__TABLES__])
      AND a.attnum>0 AND NOT a.attisdropped AND x.grantee<>c.relowner),
  'helper_grants',(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x
    WHERE n.nspname='public' AND p.proname=ANY(ARRAY[__HELPERS__])
      AND x.privilege_type='EXECUTE' AND x.grantee<>p.proowner)
)
""".replace("__TABLES__", policy._array(policy.COVERAGE_TABLES)).replace(
    "__HELPERS__",
    policy._array(
        tuple(
            name
            for name in policy.COVERAGE_FUNCTION_SQL
            if not name.endswith(("_append", "_read"))
        )
    ),
)


def _checked_catalog(bind, *, historical):
    catalog = bind.scalar(text(policy._CATALOG_SQL))
    verifier = (
        policy.verify_controlled_writer_coverage_catalog_v48
        if historical
        else policy.verify_controlled_writer_coverage_catalog
    )
    verifier(catalog)
    role = catalog["role"]
    server = catalog["server"]
    owners = {row["owner_oid"] for row in catalog["tables"]}
    if (
        type(role.get("oid")) is not int
        or owners != {role["oid"]}
        or server.get("current_user") != server.get("session_user")
        or role.get("name") != server.get("session_user")
        or server.get("role_setting") != "none"
    ):
        raise RuntimeError("controlled_writer_pair_guard_migration_owner_denied")
    acl = bind.scalar(text(_ACL_SQL))
    if (
        type(acl) is not dict
        or set(acl) != {"table_grants", "column_grants", "helper_grants"}
        or any(type(value) is not int or value != 0 for value in acl.values())
    ):
        raise RuntimeError("controlled_writer_pair_guard_migration_acl_denied")
    return catalog


def _unchanged_except_pair_context(before, after):
    # PostgreSQL's function definition reflects SECURITY DEFINER. The sole
    # verifier already checks source, identity, owner, path and every other
    # function attribute; only that one derived rendering may also change.
    before = copy.deepcopy(before)
    after = copy.deepcopy(after)
    for catalog in (before, after):
        pair = next(
            row
            for row in catalog["functions"]
            if row["name"] == "controlled_writer_coverage_v1_pair_guard"
        )
        pair.pop("definition")
        pair.pop("security_definer")
    if before != after:
        raise RuntimeError("controlled_writer_pair_guard_migration_unexpected_drift")


def upgrade():
    op.execute(_LOCK_SQL)
    bind = op.get_bind()
    before = _checked_catalog(bind, historical=True)
    op.execute(f"ALTER FUNCTION {_PAIR_SIGNATURE} SECURITY DEFINER")
    op.execute(
        f"ALTER FUNCTION {_PAIR_SIGNATURE} SET search_path TO pg_catalog, public, pg_temp"
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_PAIR_SIGNATURE} FROM PUBLIC")
    after = _checked_catalog(bind, historical=False)
    _unchanged_except_pair_context(before, after)


def downgrade():
    op.execute(_LOCK_SQL)
    bind = op.get_bind()
    before = _checked_catalog(bind, historical=False)
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.demo_controlled_writer_coverage_scopes)
          OR EXISTS(SELECT 1 FROM public.demo_controlled_writer_coverage_journal) THEN
          RAISE EXCEPTION 'controlled_writer_pair_guard_definer_downgrade_requires_empty';
        END IF;
      END $$
    """)
    op.execute(f"ALTER FUNCTION {_PAIR_SIGNATURE} SECURITY INVOKER")
    op.execute(
        f"ALTER FUNCTION {_PAIR_SIGNATURE} SET search_path TO pg_catalog, public, pg_temp"
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_PAIR_SIGNATURE} FROM PUBLIC")
    after = _checked_catalog(bind, historical=True)
    _unchanged_except_pair_context(before, after)
