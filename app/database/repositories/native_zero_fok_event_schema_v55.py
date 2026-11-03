"""Exact live55 metadata admission with the unchanged historical zero branch.

This grants no source, Arm, submission, closure or cleanup capability. The
unchanged legacy map remains the default. A genuine new native55 catalog seal
must include this exact body, trigger, ownership and ACL on the SAME connection.
"""

from sqlalchemy import text

from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as catalog,
)
from app.domain import native_filled_flat_closure_schema_v55 as live_schema
from app.domain import native_zero_fok_closure_schema_v55 as schema


class NativeZeroFokSchemaError(ValueError):
    pass


def _need(condition):
    if condition is not True:
        raise NativeZeroFokSchemaError(
            "native_zero_fok_actual55_schema_unavailable"
        ) from None


async def require_actual_zero_fok_event_schema_v55(session):
    """Read actual metadata; never project an old head/role/baseline to55."""
    _need(session.get_bind().dialect.name == "postgresql" and session.in_transaction())
    _need(
        tuple(
            (
                await session.scalars(
                    text(
                        "SELECT version_num FROM public.alembic_version ORDER BY version_num"
                    )
                )
            ).all()
        )
        == ("0055",)
    )
    # This requires the NEW real native digest. A missing baseline or R4 seal
    # cannot satisfy the closure body marker and is never made permissive here.
    observed = await catalog.verify_actual_issuer_catalog_v2(session)
    _need(
        type(observed) is dict
        and observed.get("database_head") == "0055"
        and observed.get("native_baseline_admission") is True
        and observed.get("native_closure_body_sha256")
        == live_schema.CURRENT_BODY_SHA256
        and observed.get("native_zero_closure_body_sha256")
        == schema.CURRENT_BODY_SHA256
        and observed.get("native_filled_flat_body_sha256")
        == live_schema.FILLED_BODY_SHA256
    )
    rows = (
        await session.execute(
            text(
                "SELECT p.prosrc,l.lanname,p.provolatile::text,p.prosecdef,p.proconfig,"
                "p.prokind::text,p.prorettype=to_regtype('pg_catalog.trigger'),p.pronargs,"
                "p.proparallel::text,p.proisstrict,p.proleakproof,p.proowner::bigint,"
                "(SELECT c.relowner::bigint FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname='qualification_reservations'),"
                "CASE WHEN p.proacl IS NULL OR cardinality(p.proacl)=0 THEN true "
                "WHEN array_ndims(p.proacl)<>1 OR array_lower(p.proacl,1)<>1 THEN false "
                "ELSE array_position(p.proacl,NULL) IS NULL END,"
                "(SELECT jsonb_agg(jsonb_build_array(x.grantee::bigint,x.grantor::bigint,"
                "x.privilege_type,x.is_grantable) ORDER BY x.grantee,x.privilege_type) "
                "FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x) "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "JOIN pg_language l ON l.oid=p.prolang WHERE n.nspname='public' "
                "AND p.proname='qualification_reservation_update'"
            )
        )
    ).all()
    _need(len(rows) == 1)
    (
        body,
        language,
        volatility,
        security_definer,
        settings,
        kind,
        returns_trigger,
        arity,
        parallel,
        strict,
        leakproof,
        owner,
        table_owner,
        valid_acl,
        acl,
    ) = tuple(rows[0])
    _need(
        body == live_schema.CURRENT_SQL.split("$$")[1]
        and language == "plpgsql"
        and volatility == "v"
        and security_definer is False
        and tuple(settings or ()) == ("search_path=pg_catalog, pg_temp",)
        and kind == "f"
        and returns_trigger is True
        and arity == 0
        and parallel == "u"
        and strict is False
        and leakproof is False
        and type(owner) is int
        and owner > 0
        and table_owner == owner
        and valid_acl is True
        and type(acl) is list
        and sorted(acl)
        == [[0, owner, "EXECUTE", False], [owner, owner, "EXECUTE", False]]
    )
    return schema.CURRENT_BODY_SHA256
