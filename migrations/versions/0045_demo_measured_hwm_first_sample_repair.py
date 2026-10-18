"""Repair the first sampled balance-HWM insert without granting authority.

Revision ID: 0045
Revises: 0044

DB0044's first-sample branch never assigned the genesis/peak RECORD variables,
yet the final boolean expression referred to their fields. PostgreSQL must
resolve those fields before it can short-circuit the CASE expression and raised
SQLSTATE 55000. This revision changes only those two comparisons to scalars
assigned in both branches. Every other DB0044 guard clause remains byte-exact.

The function body is patched only from the reviewed DB0044 body; its old and
new SHA256 are pinned. CREATE OR REPLACE preserves the function OID and ACL,
so existing INSERT and immutable triggers continue to refer to the same
function. A nonempty sampled index cannot be downgraded to the broken guard.
"""

import hashlib

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

_OLD_BODY_SHA256 = "f489bd66b20bee39e630b6a6e50643c7c5f962a4c7613070c7e50bab53ce0aaa"
_NEW_BODY_SHA256 = "707f66529bfd183f480d6124e87d11e006a76513c2d8fae983988e11310de8f9"
_FUNCTION = "public.account_measured_hwm_v2_guard()"
_LOCK_SQL = (
    "LOCK TABLE public.demo_account_capture_events, "
    "public.demo_account_observation_batches, "
    "public.demo_account_measured_hwm_v2 IN ACCESS EXCLUSIVE MODE NOWAIT"
)
_CATALOG_SQL = sa.text("""
    SELECT p.oid::bigint, p.prosrc, p.proacl::text, p.proconfig::text,
           p.prosecdef, p.prolang::bigint, p.prorettype::bigint,
           p.proowner::bigint, p.prokind, p.pronargs, p.proargtypes::text,
           p.provolatile, p.proisstrict, p.proparallel, p.proleakproof,
           p.procost, p.prorows, p.prosupport::bigint,
           EXISTS (
             SELECT 1 FROM pg_catalog.aclexplode(
               COALESCE(p.proacl, pg_catalog.acldefault('f',p.proowner))
             ) AS a
             WHERE a.grantee=0 AND a.privilege_type='EXECUTE'
           ) AS public_execute_enabled
    FROM pg_catalog.pg_proc AS p
    WHERE p.oid=pg_catalog.to_regprocedure(:signature)
""")
_REPAIRS = (
    (
        "expected_peak BIGINT; expected_native BOOLEAN;",
        "expected_peak BIGINT; expected_native BOOLEAN;\n"
        + "              expected_genesis_sha256 TEXT; expected_peak_sha256 TEXT;",
    ),
    (
        "IF prior.sequence IS NULL THEN\n"
        + "          g := m; selected := m;\n"
        + "          expected_peak := NEW.sequence;\n"
        + "          expected_native := (m->>'recorded_native_transport')::boolean;",
        "IF prior.sequence IS NULL THEN\n"
        + "          g := m; selected := m;\n"
        + "          expected_peak := NEW.sequence;\n"
        + "          expected_genesis_sha256 := NEW.measurement_sha256;\n"
        + "          expected_peak_sha256 := NEW.measurement_sha256;\n"
        + "          expected_native := (m->>'recorded_native_transport')::boolean;",
    ),
    (
        "          g := genesis.measurement_json::jsonb;\n"
        + "          old_num := (peak.measurement_json::jsonb->'equity'->>'numerator')::numeric;",
        "          g := genesis.measurement_json::jsonb;\n"
        + "          expected_genesis_sha256 := genesis.measurement_sha256;\n"
        + "          old_num := (peak.measurement_json::jsonb->'equity'->>'numerator')::numeric;",
    ),
    (
        "          selected := CASE WHEN expected_peak=NEW.sequence THEN m\n"
        + "                           ELSE peak.measurement_json::jsonb END;\n"
        + "          expected_native := (oldp->>'all_sources_recorded_native')::boolean",
        "          selected := CASE WHEN expected_peak=NEW.sequence THEN m\n"
        + "                           ELSE peak.measurement_json::jsonb END;\n"
        + "          expected_peak_sha256 := CASE WHEN expected_peak=NEW.sequence\n"
        + "            THEN NEW.measurement_sha256 ELSE peak.measurement_sha256 END;\n"
        + "          expected_native := (oldp->>'all_sources_recorded_native')::boolean",
    ),
    (
        "          OR p->>'genesis_measurement_sha256' IS DISTINCT FROM\n"
        + "             (CASE WHEN prior.sequence IS NULL THEN NEW.measurement_sha256 ELSE genesis.measurement_sha256 END)",
        "          OR p->>'genesis_measurement_sha256' IS DISTINCT FROM\n"
        + "             expected_genesis_sha256",
    ),
    (
        "          OR p->>'peak_measurement_sha256' IS DISTINCT FROM\n"
        + "             (CASE WHEN expected_peak=NEW.sequence THEN NEW.measurement_sha256 ELSE peak.measurement_sha256 END)",
        "          OR p->>'peak_measurement_sha256' IS DISTINCT FROM\n"
        + "             expected_peak_sha256",
    ),
)


def _sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _transform(body: str, *, reverse: bool = False) -> str:
    expected = _NEW_BODY_SHA256 if reverse else _OLD_BODY_SHA256
    target = _OLD_BODY_SHA256 if reverse else _NEW_BODY_SHA256
    if _sha256(body) != expected:
        raise RuntimeError("account_measured_hwm_v2_0045_source_mismatch")
    for before, after in _REPAIRS:
        source, replacement = (after, before) if reverse else (before, after)
        if body.count(source) != 1:
            raise RuntimeError("account_measured_hwm_v2_0045_patch_mismatch")
        body = body.replace(source, replacement, 1)
    if _sha256(body) != target or "$ctcc_0045$" in body:
        raise RuntimeError("account_measured_hwm_v2_0045_result_mismatch")
    return body


def _catalog(connection):
    row = connection.execute(_CATALOG_SQL, {"signature": _FUNCTION}).one_or_none()
    if row is None:
        raise RuntimeError("account_measured_hwm_v2_0045_function_missing")
    return row


def _replace(*, reverse: bool):
    connection = op.get_bind()
    connection.exec_driver_sql(_LOCK_SQL)
    if (
        reverse
        and connection.exec_driver_sql(
            "SELECT EXISTS(SELECT 1 FROM public.demo_account_measured_hwm_v2)"
        ).scalar_one()
    ):
        raise RuntimeError("account_measured_hwm_v2_downgrade_requires_empty")
    before = _catalog(connection)
    if before.public_execute_enabled:
        raise RuntimeError("account_measured_hwm_v2_0045_public_execute_denied")
    repaired = _transform(before.prosrc, reverse=reverse)
    # The only interpolated bytes are the exact, pinned prior function body.
    connection.exec_driver_sql(
        "CREATE OR REPLACE FUNCTION public.account_measured_hwm_v2_guard() "
        "RETURNS trigger AS $ctcc_0045$" + repaired + "$ctcc_0045$ LANGUAGE plpgsql "
        "SET search_path=pg_catalog,pg_temp"
    )
    after = _catalog(connection)
    original_attributes = tuple(
        value for key, value in before._mapping.items() if key != "prosrc"
    )
    final_attributes = tuple(
        value for key, value in after._mapping.items() if key != "prosrc"
    )
    if (
        after.public_execute_enabled
        or original_attributes != final_attributes
        or after.prosrc != repaired
    ):
        raise RuntimeError("account_measured_hwm_v2_0045_catalog_changed")


def upgrade():
    _replace(reverse=False)


def downgrade():
    _replace(reverse=True)
