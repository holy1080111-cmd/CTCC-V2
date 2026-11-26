"""Structural verification of the actual granted owner catalog on 0055/0056."""

from copy import deepcopy

from app.domain import collector_current_0055_catalog_v1 as current55
from app.domain import collector_current_0056_inherited_catalog_v1 as current56
from app.domain import controlled_history_current55_schema_v2 as history
from scripts import current0055_native_lifecycle_acl_v1 as lifecycle
from scripts import current0055_runtime_grants as core

_GRANTED_TABLES = (
    "native_configuration55",
    "operator55",
    "native_report55",
    "native_close_intent55",
)
_LINEAGE_TABLES = (
    ("issued", current55.issued.TABLE),
    ("lineage", current55.lineage.TABLE),
)


class GrantedOwnerCatalogError(ValueError):
    """The granted catalog could not be verified for maintenance."""


def _need(condition):
    if condition is not True:
        raise GrantedOwnerCatalogError("native_granted_owner_catalog_refused") from None


def _acl_structure(rows, owner_oid, expected):
    _need(type(rows) is list)
    all_rows, grant_rows = set(), set()
    structure = []
    for row in rows:
        _need(
            type(row) is list
            and len(row) == 4
            and type(row[0]) is int
            and 0 <= row[0] < 2**32
            and type(row[1]) is int
            and row[1] == owner_oid
            and type(row[2]) is str
            and type(row[3]) is bool
            and tuple(row) not in all_rows
        )
        all_rows.add(tuple(row))
        if row[0] in (0, owner_oid):
            # The original structure validator checks owner and PUBLIC rows.
            structure.append(row)
        else:
            grant_rows.add(tuple(row))
    _need(grant_rows == expected)
    return structure


def _lineage_structure(view, owner_oid, grants):
    rows = view["acl"]
    names = {table for _, table in _LINEAGE_TABLES}
    _need(
        type(rows) is list
        and all(
            type(row) is list
            and len(row) == 5
            and type(row[0]) is str
            and row[0] in names
            for row in rows
        )
    )
    for section, table in _LINEAGE_TABLES:
        allowed = grants[("TABLE", table)]
        relation = view[section]
        actual = [row[1:] for row in rows if row[0] == table]
        structure = _acl_structure(actual, owner_oid, allowed)
        _need(
            bool(allowed)
            and relation["owner"] == relation["actor"] == owner_oid
            and all(row[0] == owner_oid for row in structure)
            and all(
                grant[1] == owner_oid
                and grant[2] in ("SELECT", "INSERT")
                and grant[3] is False
                for grant in allowed
            )
            and relation["bad_table_acl"]
            is any(
                grantee != owner_oid
                and (
                    grantee != relation["actor"]
                    or privilege not in ("SELECT", "INSERT")
                    or grantable
                )
                for grantee, _, privilege, grantable in actual
            )
        )
        # The legacy SQL bit tests grants against its session user. For the
        # structural replay only, every corresponding raw grant is now proven.
        relation["bad_table_acl"] = False
    view["acl"] = [row for row in rows if row[1] in (0, owner_oid)]


def _controlled_history_structure(view, owner_oid, role_oids, grants):
    functions = view["functions"]
    signatures = history.SIGNATURES | history.ORIGINAL_SIGNATURES
    observed_grantees = set()
    _need(type(functions) is list)
    for function in functions:
        _need(
            type(function) is dict
            and type(function["name"]) is str
            and function["name"] in signatures
            and function["can_execute"] is True
        )
        name = function["name"]
        arguments = signatures[name]
        _need(function["arguments"] == arguments)
        actual = history._grantees(function, owner_oid) - {owner_oid}
        expected = grants.get(("FUNCTION", f"{name}({arguments})"), set())
        function["acl"] = _acl_structure(function["acl"], owner_oid, expected)
        observed_grantees.update(actual)

    roles = view["roles"]
    names_by_oid = {oid: name for name, oid in role_oids.items()}
    seen = set()
    _need(type(roles) is list)
    for role in roles:
        _need(
            type(role) is dict
            and set(role) == history._ROLE_KEYS
            and type(role["oid"]) is int
            and role["oid"] in names_by_oid
            and role["oid"] not in seen
            and role["name"] == names_by_oid[role["oid"]]
            and all(
                role[flag] is (flag == "login")
                for flag in history._ROLE_KEYS - {"name", "oid"}
            )
        )
        seen.add(role["oid"])
    _need(seen == observed_grantees)
    view["roles"] = []


def observe_granted_owner_structure(
    document,
    *,
    head,
    read_only,
    owner_name,
    owner_oid,
    role_names,
    role_oids,
    granted_maintainer_oid=None,
):
    """Replay only the structure, after the held transaction verifies actual ACLs."""
    _need(
        type(head) is str
        and head in ("0055", "0056")
        and type(read_only) is bool
        and type(document) is dict
        and type(owner_name) is str
        and bool(owner_name)
        and type(owner_oid) is int
        and 0 < owner_oid < 2**32
        and type(role_names) is tuple
        and len(role_names) == 3
        and all(type(name) is str for name in role_names)
        and type(role_oids) is dict
        and set(role_oids) == set(role_names)
        and len(role_oids) == 3
        and all(type(oid) is int and 0 < oid < 2**32 for oid in role_oids.values())
        and len({owner_oid, *role_oids.values()}) == 4
        and (
            granted_maintainer_oid is None
            or (
                type(granted_maintainer_oid) is int
                and granted_maintainer_oid == owner_oid
            )
        )
    )
    try:
        role = document["role"]
        _need(role["name"] == owner_name and role["oid"] == owner_oid)
        base, additions = lifecycle._plans(*role_names)
        _need(len(base) == 39 and len(additions) == 3)
        grants = core._expected_acl(base + additions, role_oids, owner_oid)
        view = deepcopy(document)
        for section in _GRANTED_TABLES:
            tables = view[section]["tables"]
            _need(type(tables) is list)
            for table in tables:
                _need(type(table) is dict and type(table["name"]) is str)
                table["acl"] = _acl_structure(
                    table["acl"], owner_oid, grants.get(("TABLE", table["name"]), set())
                )
        _lineage_structure(view, owner_oid, grants)
        _controlled_history_structure(
            view["controlled_history55"], owner_oid, role_oids, grants
        )
        inspect = (
            current55._observe if head == "0055" else current56._observe_inherited_graph
        )
        observed = inspect(
            view,
            owner_mode=True,
            read_only="on" if read_only else "off",
            isolation="repeatable read" if read_only else "read committed",
            _granted_maintainer_oid=granted_maintainer_oid,
        )
        if head == "0055":
            _need(
                observed["native_definitions_sha256"] == current55._required_baseline()
            )
        _need(
            observed["database_head"] == head
            and observed["role_oid"] == owner_oid
            and observed["execution_authority"] is False
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise GrantedOwnerCatalogError("native_granted_owner_catalog_refused") from None
