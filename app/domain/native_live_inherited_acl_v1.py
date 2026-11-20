"""Exact inherited PUBLIC DATA helpers; these grant no Demo/Live table access.

Five helpers only transform arguments. The remaining reviewed invoker helpers
read journals and remain unable to read any Demo table as the isolated Live
writer. Unknown public routines, changed bodies/flags/ACL or direct grants fail.
No existing PUBLIC privilege is revoked or silently broadened here.
"""

from sqlalchemy import text

from app.domain import native_closed_report_schema_v55 as reports
from app.domain import native_filled_flat_closure_schema_v55 as filled
from app.domain import native_leverage_configuration_schema_v55 as leverage
from app.domain import native_protective_hold_schema_v58 as protective
from app.domain.source_primitives import canonical, sha


def inherited_live_data_helper_specs_v1():
    filled.statements()
    reports.statements()
    leverage.statements()
    specs = {
        name: (
            args,
            returns,
            "plpgsql",
            "v",
            sql.split("$ctcc$")[1],
            ["search_path=pg_catalog, pg_temp"],
        )
        for (name, args, returns), sql in zip(
            filled.HELPER_IDENTITIES, filled.HELPERS, strict=True
        )
    }
    for name, args, returns, sql, delimiter, public in reports.FUNCTIONS:
        if public and returns != "trigger":
            specs[name] = (
                args,
                returns,
                "plpgsql",
                "v",
                sql.split(delimiter)[1],
                ["search_path=pg_catalog, pg_temp"],
            )
    for name, args, returns, language, volatility, sql in leverage.FUNCTION_IDENTITIES:
        if returns != "trigger":
            specs[name] = (
                ", ".join(part.strip() for part in args.split(",")),
                returns,
                language,
                volatility,
                sql.split("$f$")[1],
                ["search_path=pg_catalog, public, pg_temp"],
            )
    specs[protective.FUNCTION] = (
        protective.FUNCTION_IDENTITY,
        "boolean",
        "plpgsql",
        "v",
        protective.PREDICATE_SQL.split("$ctcc$")[1],
        ["search_path=pg_catalog, pg_temp"],
    )
    if len(specs) != 13:
        raise ValueError("native_live_inherited_helper_scope_changed")
    return specs


def verify_inherited_live_helper_rows_v1(rows, *, owner_oid):
    specs = inherited_live_data_helper_specs_v1()
    if (
        type(rows) is not list
        or len(rows) != len(specs)
        or type(owner_oid) is not int
        or owner_oid <= 0
    ):
        raise ValueError("native_live_inherited_helper_catalog_refused")
    seen = set()
    for row in rows:
        if type(row) is not dict or row.get("name") not in specs or row["name"] in seen:
            raise ValueError("native_live_inherited_helper_catalog_refused")
        args, returns, language, volatility, body, config = specs[row["name"]]
        expected = {
            "name": row["name"],
            "owner": owner_oid,
            "arguments": args,
            "returns": returns,
            "language": language,
            "volatility": volatility,
            "source": body,
            "config": config,
            "security_definer": False,
            "kind": "f",
            "parallel": "u",
            "strict": False,
            "leakproof": False,
            "returns_set": False,
            "defaults": 0,
            "support_zero": True,
            "acl": [
                [0, owner_oid, "EXECUTE", False],
                [owner_oid, owner_oid, "EXECUTE", False],
            ],
        }
        if canonical(row) != canonical(expected):
            raise ValueError("native_live_inherited_helper_catalog_refused")
        seen.add(row["name"])
    return frozenset(seen)


async def require_inherited_live_data_helpers_v1(session, *, owner_oid):
    names = list(inherited_live_data_helper_specs_v1())
    rows = await session.scalar(
        text("""SELECT jsonb_agg(jsonb_build_object(
      'name',p.proname,'owner',p.proowner::bigint,'arguments',oidvectortypes(p.proargtypes),
      'returns',pg_get_function_result(p.oid),'language',l.lanname,'volatility',p.provolatile::text,
      'source',p.prosrc,'config',p.proconfig,'security_definer',p.prosecdef,'kind',p.prokind::text,
      'parallel',p.proparallel::text,'strict',p.proisstrict,'leakproof',p.proleakproof,'returns_set',p.proretset,
      'defaults',p.pronargdefaults,'support_zero',p.prosupport=0,
      'acl',(SELECT jsonb_agg(jsonb_build_array(a.grantee::bigint,a.grantor::bigint,a.privilege_type,a.is_grantable)
       ORDER BY a.grantee,a.privilege_type) FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a))
      ORDER BY p.proname,p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      JOIN pg_language l ON l.oid=p.prolang WHERE n.nspname='public' AND p.proname=ANY(CAST(:names AS text[]))"""),
        {"names": names},
    )
    return verify_inherited_live_helper_rows_v1(rows, owner_oid=owner_oid)


INHERITED_HELPER_SCOPE_SHA256 = sha(
    canonical(
        {
            name: [*values[:-2], sha(values[-2].encode()), values[-1]]
            for name, values in inherited_live_data_helper_specs_v1().items()
        }
    )
)
