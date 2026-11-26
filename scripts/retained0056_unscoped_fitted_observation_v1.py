"""Observe the fitted schema in an existing owner read-only transaction."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy import text

from scripts import current0056_fitted_delta_catalog_v1 as fitted
from scripts import native_owner_upgrade_0056_v1 as installed

_STRUCTURAL_FIELDS = (
    "tables",
    "functions",
    "triggers",
    "constraints",
    "indexes",
    "policies",
    "rules",
    "inheritance",
    "event_triggers",
    "global_default_acl",
    "public_default_acl",
)
_ROLE_FIELDS = ("roles", "role_create", "role_edges", "original_write_access")

_EFFECTIVE_ACL = text("""
WITH fitted_relations AS MATERIALIZED (
    SELECT c.oid,c.relkind,c.relowner,a.amname FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      LEFT JOIN pg_catalog.pg_am a ON a.oid=c.relam
      WHERE n.nspname='public'
        AND pg_catalog.starts_with(c.relname,'gate3_fitted_candidate_')
), fitted_routines AS MATERIALIZED (
    SELECT p.oid,p.proowner,p.prokind FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND pg_catalog.starts_with(p.proname,'gate3_fitted_')
), alternate_subjects AS MATERIALIZED (
    SELECT subject.oid FROM pg_catalog.pg_roles subject
      WHERE EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles login
          WHERE login.rolcanlogin AND login.oid <> CAST(:owner_oid AS pg_catalog.oid)
            AND (login.oid=subject.oid
              OR pg_catalog.pg_has_role(login.oid,subject.oid,'SET')
              OR pg_catalog.pg_has_role(login.oid,subject.oid,'MEMBER WITH ADMIN OPTION'))
      )
), foreign_keys AS MATERIALIZED (
    SELECT k.oid FROM pg_catalog.pg_constraint k JOIN fitted_relations c ON c.oid=k.conrelid
      WHERE k.contype='f'
)
SELECT pg_catalog.jsonb_build_object(
    'owner_bound', EXISTS(SELECT 1 FROM pg_catalog.pg_roles owner
      WHERE owner.oid=CAST(:owner_oid AS pg_catalog.oid) AND owner.rolname=CAST(:owner AS pg_catalog.name)
        AND owner.rolcanlogin AND owner.rolname=CURRENT_USER AND owner.rolname=SESSION_USER),
    'relations', (SELECT count(*) FROM fitted_relations),
    'routines', (SELECT count(*) FROM fitted_routines),
    'owned_heap_relations', NOT EXISTS(SELECT 1 FROM fitted_relations
      WHERE relkind<>'r' OR relowner<>CAST(:owner_oid AS pg_catalog.oid) OR amname IS DISTINCT FROM 'heap'),
    'owned_functions', NOT EXISTS(SELECT 1 FROM fitted_routines
      WHERE prokind<>'f' OR proowner<>CAST(:owner_oid AS pg_catalog.oid)),
    'no_extra_user_triggers', NOT EXISTS(SELECT 1 FROM pg_catalog.pg_trigger g
      JOIN fitted_relations c ON c.oid=g.tgrelid
      WHERE NOT g.tgisinternal AND NOT pg_catalog.starts_with(g.tgname,'gate3_fitted_')),
    'native_fk_triggers', (SELECT count(*) FROM pg_catalog.pg_trigger g
      JOIN foreign_keys k ON k.oid=g.tgconstraint
      WHERE g.tgisinternal AND g.tgenabled='O' AND NOT g.tgdeferrable AND NOT g.tginitdeferred),
    'no_alternate_table_privilege', NOT EXISTS(SELECT 1 FROM alternate_subjects s
      CROSS JOIN fitted_relations c WHERE
        pg_catalog.has_table_privilege(s.oid,c.oid,
          'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
        OR pg_catalog.has_any_column_privilege(s.oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')),
    'no_alternate_execute', NOT EXISTS(SELECT 1 FROM alternate_subjects s
      CROSS JOIN fitted_routines p WHERE pg_catalog.has_function_privilege(s.oid,p.oid,'EXECUTE'))
)
""")


@dataclass(frozen=True, slots=True)
class UnscopedFittedObservationV1:
    structural_sha256: str
    fitted_context_role_sha256: str
    schema: Literal["ctcc.retained0056.unscoped_fitted_observation.v1"] = field(
        default="ctcc.retained0056.unscoped_fitted_observation.v1", init=False
    )
    execution_authority: Literal[False] = field(default=False, init=False)


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise fitted.CatalogRefusal(code)


def _sha(value: object) -> str:
    return hashlib.sha256(fitted._canonical(value)).hexdigest()


def _keys(rows, key):
    _need(
        all(type(row) is dict and type(row.get(key)) is str for row in rows),
        "unscoped_fitted_document_invalid",
    )
    return {row[key] for row in rows}


def _inspect_unscoped_fitted_catalog_v1(document, access, *, owner):
    _need(
        type(document) is dict and len(fitted._canonical(document)) <= 1_000_000,
        "unscoped_fitted_document_invalid",
    )
    context = {
        "schema": fitted.SCHEMA,
        "head": "0056",
        "server_version": "170011",
        "actor": owner,
        "login": owner,
        "tls": True,
        "read_only": True,
        "repeatable": True,
        "durable": True,
        "search_path": "pg_catalog, pg_temp",
    }
    fields = (*_STRUCTURAL_FIELDS, *_ROLE_FIELDS, "function_grants", "table_grants")
    _need(
        set(document) == set(context) | set(fields) | {"server_build"}
        and all(
            type(document[key]) is type(value) and document[key] == value
            for key, value in context.items()
        )
        and type(document["server_build"]) is str
        and document["server_build"].startswith("PostgreSQL 17.11 ")
        and all(type(document[key]) is list for key in fields),
        "unscoped_fitted_context_changed",
    )
    _need(
        document["function_grants"] == document["table_grants"] == [],
        "unscoped_fitted_direct_acl",
    )
    required = {
        "owner_bound": True,
        "relations": len(fitted.TABLES),
        "routines": len(fitted.INTERNAL | fitted.APIS.keys()),
        "owned_heap_relations": True,
        "owned_functions": True,
        "no_extra_user_triggers": True,
        "native_fk_triggers": 12,
        "no_alternate_table_privilege": True,
        "no_alternate_execute": True,
    }
    _need(
        type(access) is dict
        and set(access) == set(required)
        and all(
            type(access[key]) is type(value) and access[key] == value
            for key, value in required.items()
        ),
        "unscoped_fitted_effective_acl_or_storage",
    )
    expected_objects = {
        "tables": ("name", fitted.TABLES),
        "functions": (
            "signature",
            {f"public.{name}" for name in fitted.INTERNAL | fitted.APIS.keys()},
        ),
        "triggers": ("name", fitted.TRIGGERS),
        "constraints": ("name", fitted.CONSTRAINTS.keys()),
        "indexes": ("name", fitted.INDEXES),
    }
    _need(
        all(
            len(document[field]) == len(names) and _keys(document[field], key) == names
            for field, (key, names) in expected_objects.items()
        ),
        "unscoped_fitted_object_identity",
    )
    _need(
        all(
            row.get("owner") == owner
            and row.get("kind") == "r"
            and row.get("persistence") == "p"
            and row.get("rls") is row.get("force_rls") is False
            and type(row.get("columns")) is list
            and row["columns"]
            and all(
                type(column) is list and len(column) == 7 and column[-1] is None
                for column in row["columns"]
            )
            for row in document["tables"]
        )
        and all(
            row.get("owner") == owner and row.get("acl") == [f"{owner}=X/{owner}"]
            for row in document["functions"]
        ),
        "unscoped_fitted_owner_or_column_acl",
    )
    _need(
        all(
            row.get("config") == ["search_path=pg_catalog, pg_temp"]
            and all(
                type(row.get(field)) is str
                and fitted._HEX.fullmatch(row[field]) is not None
                for field in ("definition_sha256", "source_sha256")
            )
            for row in document["functions"]
        )
        and all(
            row.get("internal") is False and row.get("enabled") == "A"
            for row in document["triggers"]
        )
        and all(
            (row.get("table"), row.get("type")) == fitted.CONSTRAINTS[row["name"]]
            and row.get("validated") is True
            and row.get("deferrable") is (row["type"] == "t")
            and row.get("deferred") is (row["type"] == "t")
            for row in document["constraints"]
        )
        and all(
            row.get("valid") is row.get("ready") is True for row in document["indexes"]
        ),
        "unscoped_fitted_enforcement_changed",
    )
    _need(
        all(document[field] == [] for field in _STRUCTURAL_FIELDS[5:]),
        "unscoped_fitted_extra_policy",
    )
    return UnscopedFittedObservationV1(
        structural_sha256=_sha(
            {
                "schema": "ctcc.retained0056.unscoped_fitted_structure.v1",
                **{key: document[key] for key in _STRUCTURAL_FIELDS},
            }
        ),
        fitted_context_role_sha256=_sha(
            {
                "schema": "ctcc.retained0056.unscoped_fitted_context_role.v1",
                **{key: document[key] for key in _ROLE_FIELDS},
            }
        ),
    )


async def observe_unscoped_fitted_catalog_0056_v1(
    connection, *, source: Path, owner: str, owner_oid: int
) -> UnscopedFittedObservationV1:
    """Read measured schema and ACL data through an already-open owner session."""
    _need(
        type(owner) is str
        and re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", owner) is not None
        and type(owner_oid) is int
        and owner_oid == 10,
        "unscoped_fitted_route_pins",
    )
    fitted.pinned_migration(source)
    document = await installed._read_fitted_owner_catalog_v2(connection, read_only=True)
    access = await connection.scalar(
        _EFFECTIVE_ACL, {"owner": owner, "owner_oid": owner_oid}
    )
    return _inspect_unscoped_fitted_catalog_v1(document, access, owner=owner)


def require_reviewed_unscoped_fitted_structure_v1(
    observation: UnscopedFittedObservationV1, *, reviewed_structural_sha256: str
) -> None:
    """Compare structural evidence to an independently reviewed reference."""
    _need(
        type(observation) is UnscopedFittedObservationV1
        and type(reviewed_structural_sha256) is str
        and re.fullmatch(r"[0-9a-f]{64}", reviewed_structural_sha256) is not None
        and observation.structural_sha256 == reviewed_structural_sha256,
        "unscoped_fitted_reviewed_structure_changed",
    )
