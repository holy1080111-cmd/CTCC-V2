"""Versioned source58 full-catalog DATA codec; no runtime/actor admission.

The source17 public APIs and literal purposes are unchanged. This separate
reader retains their complete typed field/reference/allocation treatment and
extends only the explicitly observed new58 purpose: public callback references,
partial predicates and source-proven CONST19/705. No observed function/type I/O
is invoked. Unknown data and unequal complete normalized references deny.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re

from scripts import paper58_source_shape_v1 as shape
from scripts import retained0017_catalog_baseline_v1 as ancestor

SCHEMA = "ctcc.paper58.normalized_catalog.v1"
RAW_SCHEMA = "ctcc.paper58.structural_raw_catalog.v1"
canonical = ancestor.canonical
catalog_types = ancestor.catalog_types
_acl = ancestor._acl
_bootstrap_acl = ancestor._bootstrap_acl
_REF_TABLES = ancestor._REF_TABLES
_NODE_FIELDS = ancestor._NODE_FIELDS
_NODE_TYPES = ancestor._NODE_TYPES
_CONST_ABI = ancestor._CONST_ABI | {19: (64, False), 705: (-2, False)}
_TYPE_FIELDS = ancestor._TYPE_FIELDS
_PROC_FIELDS = ancestor._PROC_FIELDS
_NODE_TOKEN = ancestor._NODE_TOKEN
_NAME = ancestor._NAME
_HEX = ancestor._HEX


class Paper58CatalogDataRefused(ValueError):
    pass


def _need(value):
    if value is not True:
        raise Paper58CatalogDataRefused("paper58_catalog_data_refused") from None


def _plain(value):
    seen = set()
    budget = [0]

    def visit(item, depth):
        budget[0] += 1
        _need(depth <= 128 and budget[0] <= 1000000)
        kind = type(item)
        if kind in (str, int, bool) or item is None:
            return
        if kind is float:
            _need(math.isfinite(item))
            return
        _need(kind in (dict, list) and id(item) not in seen)
        seen.add(id(item))
        if kind is dict:
            for key, child in item.items():
                _need(type(key) is str)
                visit(child, depth + 1)
        else:
            for child in item:
                visit(child, depth + 1)

    try:
        visit(value, 0)
    finally:
        visit = None


def _qualify_node(value, ref):
    if type(value) is not list:
        return value
    if len(value) == 3 and value[:1] == ["NODE"]:
        fields = []
        for name, values in value[2]:
            if name in _TYPE_FIELDS or name in _PROC_FIELDS or name == "opno":
                catalog = (
                    "pg_type"
                    if name in _TYPE_FIELDS
                    else "pg_operator"
                    if name == "opno"
                    else "pg_proc"
                )
                fields.append([name, [ref(catalog, values[0])]])
            else:
                fields.append([name, [_qualify_node(item, ref) for item in values]])
        return ["NODE", value[1], fields]
    return [_qualify_node(item, ref) for item in value]


def _node58(
    raw, *, ref, sequence_oid=None, sequence_identity=None, column_type_oid=None
):
    """Pure, bounded PG17 node reader. Never evaluate text or call type I/O.

    Every semantic field and constant byte is retained. Only the typed parser
    location field and an exact dependency-proven sequence regclass Datum are
    allocation-independent. No arbitrary integer/string replacement exists.
    """
    _need(
        column_type_oid is None
        or (type(column_type_oid) is int and column_type_oid in (20, 23))
    )
    _need(column_type_oid is None or sequence_oid is not None)
    if raw is None:
        _need(sequence_oid is None)
        return None
    _need(type(raw) is str and 0 < len(raw.encode("utf-8")) <= 2 * 1024 * 1024)
    tokens = []
    end = 0
    for match in _NODE_TOKEN.finditer(raw):
        _need(not raw[end : match.start()].strip())
        tokens.append(match[1])
        end = match.end()
    _need(not raw[end:].strip() and 0 < len(tokens) <= 250000)
    position = 0
    regclasses = 0

    def integer(value):
        _need(type(value) is int)
        return value

    def single_function_argument(node, oid, result_type, form):
        # Exact native SERIAL/BIGSERIAL shapes, captured from frozen source17.
        # Return only for validation; the complete original wrapper remains in
        # the normalized AST and therefore remains part of schema comparison.
        _need(type(node) is list and len(node) == 3)
        _need(node[:2] == ["NODE", "FUNCEXPR"])
        fields = dict(node[2])
        _need(set(fields) == set(_NODE_FIELDS["FUNCEXPR"].split()))
        for name, expected in (
            ("funcid", oid),
            ("funcresulttype", result_type),
            ("funcretset", "false"),
            ("funcvariadic", "false"),
            ("funcformat", form),
            ("funccollid", 0),
            ("inputcollid", 0),
        ):
            _need(fields[name] == [expected])
        args = fields["args"]
        _need(
            len(args) == 1
            and type(args[0]) is list
            and len(args[0]) == 2
            and args[0][0] == "("
            and type(args[0][1]) is list
            and len(args[0][1]) == 1
        )
        return args[0][1][0]

    def parse(depth):
        nonlocal position, regclasses
        _need(depth <= 128 and position < len(tokens))
        token = tokens[position]
        position += 1
        if token == "{":
            _need(position < len(tokens))
            tag = tokens[position]
            position += 1
            _need(tag in _NODE_FIELDS)
            allowed = set(_NODE_FIELDS[tag].split())
            fields = []
            seen = set()
            while position < len(tokens) and tokens[position] != "}":
                field = tokens[position]
                position += 1
                _need(
                    field.startswith(":") and field[1:] in allowed and field not in seen
                )
                seen.add(field)
                values = []
                while (
                    position < len(tokens)
                    and tokens[position] != "}"
                    and not tokens[position].startswith(":")
                ):
                    values.append(parse(depth + 1))
                _need(bool(values))
                name = field[1:]
                if name == "location":
                    _need(len(values) == 1 and integer(values[0]) >= -1)
                    values = ["$PARSER_LOCATION"]
                elif name in _TYPE_FIELDS:
                    _need(
                        len(values) == 1
                        and (
                            integer(values[0]) in _NODE_TYPES
                            or name == "consttype"
                            and values[0] in (19, 705)
                        )
                    )
                elif name in _PROC_FIELDS or name == "opno":
                    _need(len(values) == 1 and 0 <= integer(values[0]) < 2**32)
                    if name in ("hashfuncid", "negfuncid"):
                        _need(values[0] == 0)
                elif name in {
                    "varcollid",
                    "constcollid",
                    "funccollid",
                    "opcollid",
                    "inputcollid",
                    "array_collid",
                    "resultcollid",
                    "coalescecollid",
                    "minmaxcollid",
                    "casecollid",
                    "collation",
                }:
                    # Genuine55 CHECK nodes use the source-pinned native C
                    # collation950. Keep its complete bootstrap row in DATA;
                    # no production index/callback fence is changed here.
                    _need(len(values) == 1 and integer(values[0]) in (0, 100, 950))
                fields.append([name, values])
            _need(position < len(tokens) and tokens[position] == "}")
            position += 1
            mapping = {name: values for name, values in fields}
            if tag == "ARRAYCOERCEEXPR":
                _need(set(mapping) == set(_NODE_FIELDS[tag].split()))
                _need(mapping["resulttype"] in ([1009], [1015]))
                _need(
                    all(
                        len(mapping[name]) == 1
                        and type(mapping[name][0]) is list
                        and mapping[name][0][:1] == ["NODE"]
                        for name in ("arg", "elemexpr")
                    )
                )
            if tag == "CONST":
                _need(set(mapping) == set(_NODE_FIELDS["CONST"].split()))
                value = mapping["constvalue"]
                _need(
                    value == ["<>"]
                    or (
                        len(value) == 2
                        and type(value[0]) is int
                        and type(value[1]) is list
                        and len(value[1]) == 2
                        and value[1][0] == "["
                    )
                )
                _need(
                    mapping["consttype"] != [2249]
                    and mapping["constisnull"] in (["true"], ["false"])
                    and mapping["constbyval"] in (["true"], ["false"])
                    and len(mapping["constlen"]) == 1
                    and integer(mapping["constlen"][0]) in (-2, -1, 1, 2, 4, 8, 16, 64)
                )
                _need(mapping["consttype"][0] in _CONST_ABI)
                expected_length, expected_byval = _CONST_ABI[mapping["consttype"][0]]
                _need(
                    mapping["constlen"] == [expected_length]
                    and mapping["constbyval"] == ["true" if expected_byval else "false"]
                )
                if mapping["constisnull"] == ["true"]:
                    _need(value == ["<>"])
                else:
                    _need(
                        len(value) == 2
                        and 0 <= value[0] <= 2 * 1024 * 1024
                        and len(value[1]) == 2
                        and type(value[1][1]) is list
                        and all(
                            type(v) is int and -128 <= v <= 127 for v in value[1][1]
                        )
                    )
                    if mapping["constbyval"] == ["true"]:
                        _need(
                            value[0] == mapping["constlen"][0]
                            and value[0] in (1, 2, 4, 8)
                            and len(value[1][1]) == 8
                        )
                    else:
                        _need(
                            value[0] > 0
                            and len(value[1][1]) == value[0]
                            and (expected_length < 0 or value[0] == expected_length)
                        )
                if mapping["consttype"] == [2205]:
                    _need(
                        type(sequence_oid) is int
                        and 0 < sequence_oid < 2**32
                        and type(sequence_identity) is dict
                    )
                    _need(
                        mapping["constlen"] == [4]
                        and mapping["constbyval"] == ["true"]
                        and mapping["constisnull"] == ["false"]
                    )
                    _need(
                        len(value) == 2
                        and value[0] == 4
                        and len(value[1]) == 2
                        and type(value[1][1]) is list
                    )
                    signed = value[1][1]
                    _need(
                        len(signed) == 8
                        and all(type(v) is int and -128 <= v <= 127 for v in signed)
                    )
                    payload = bytes(v % 256 for v in signed)
                    _need(payload == sequence_oid.to_bytes(8, "little"))
                    mapping["constvalue"][:] = [
                        "$SOURCE_SEQUENCE_REGCLASS",
                        copy.deepcopy(sequence_identity),
                    ]
                    regclasses += 1
            if tag == "VAR":
                _need(
                    mapping.get("varno") == [1]
                    and mapping.get("varlevelsup") == [0]
                    and len(mapping.get("varattno", [])) == 1
                    and integer(mapping["varattno"][0]) > 0
                )
            return ["NODE", tag, fields]
        if token in ("(", "["):
            closing = ")" if token == "(" else "]"
            values = []
            while position < len(tokens) and tokens[position] != closing:
                values.append(parse(depth + 1))
            _need(position < len(tokens) and tokens[position] == closing)
            position += 1
            return [token, values]
        _need(token not in ("}", ")", "]") and not token.startswith(":"))
        if re.fullmatch(r"-?[0-9]+", token):
            return int(token)
        return token

    try:
        result = parse(0)
        _need(position == len(tokens))
        _need(
            (sequence_oid is None and regclasses == 0)
            or (sequence_oid is not None and regclasses == 1)
        )
        if sequence_oid is not None:
            nextval = result
            if column_type_oid == 23:
                nextval = single_function_argument(result, 480, 23, 2)
            leaf = single_function_argument(nextval, 1574, 20, 0)
            _need(type(leaf) is list and len(leaf) == 3)
            _need(leaf[:2] == ["NODE", "CONST"])
            original_fields = dict(leaf[2])
            _need(
                original_fields["consttype"] == [2205]
                and original_fields["consttypmod"] == [-1]
                and original_fields["constcollid"] == [0]
                and original_fields["constvalue"]
                == ["$SOURCE_SEQUENCE_REGCLASS", sequence_identity]
            )
        return _qualify_node(result, ref)
    finally:
        # Break only this invocation's recursive closure; no global cache or
        # garbage-collection policy changes, including on refusal paths.
        parse = None


def _bootstrap58(value, ref, bootstrap_owner, public_rows, owner):
    _need(
        type(value) is dict
        and set(value)
        == {
            "languages",
            "language_callbacks",
            "expression_operators",
            "expression_callbacks",
            "extension",
            "collations",
        }
    )
    normalized = {}
    languages = []
    callbacks = []
    collations = []
    _need(
        type(value["languages"]) is list
        and {row["lanname"] for row in value["languages"]}
        == {"internal", "c", "sql", "plpgsql"}
    )
    for original in value["languages"]:
        catalog_types.require_catalog_array_metadata_v1("pg_language", original)
        row = copy.deepcopy(original)
        key = ref("pg_language", row.pop("oid"))
        _need(row["lanowner"] == 10)
        row["lanowner"] = "$PG_BOOTSTRAP_OWNER_10"
        row["lanacl"] = _bootstrap_acl(row["lanacl"], bootstrap_owner)
        for field in ("lanplcallfoid", "laninline", "lanvalidator"):
            row[field] = ref("pg_proc", row[field])
        languages.append([key, row])
    _need(
        type(value["language_callbacks"]) is list
        and {row["proname"] for row in value["language_callbacks"]}
        == {
            "plpgsql_call_handler",
            "plpgsql_inline_handler",
            "plpgsql_validator",
            "fmgr_internal_validator",
            "fmgr_c_validator",
            "fmgr_sql_validator",
        }
    )
    for original in value["language_callbacks"]:
        catalog_types.require_catalog_array_metadata_v1("pg_proc", original)
        row = copy.deepcopy(original)
        key = ref("pg_proc", row.pop("oid"))
        _need(row["proowner"] == 10)
        row["proowner"] = "$PG_BOOTSTRAP_OWNER_10"
        for field, catalog in {
            "pronamespace": "pg_namespace",
            "prolang": "pg_language",
            "provariadic": "pg_type",
            "prosupport": "pg_proc",
            "prorettype": "pg_type",
        }.items():
            row[field] = ref(catalog, row[field])
        for field in ("proargtypes", "proallargtypes", "protrftypes"):
            row[field] = (
                None
                if row[field] is None
                else [ref("pg_type", oid) for oid in row[field]]
            )
        row["proacl"] = _bootstrap_acl(row["proacl"], bootstrap_owner)
        callbacks.append([key, row])
    row = copy.deepcopy(value["extension"])
    catalog_types.require_catalog_array_metadata_v1("pg_extension", row)
    _need(type(row) is dict and row["extname"] == "plpgsql" and row["extowner"] == 10)
    _need(type(row.pop("oid")) is int)
    row["extowner"] = "$PG_BOOTSTRAP_OWNER_10"
    row["extnamespace"] = ref("pg_namespace", row["extnamespace"])
    normalized["extension"] = row
    _need(
        type(value["collations"]) is list
        and {row["oid"] for row in value["collations"]} == {100, 950}
    )
    for original in value["collations"]:
        catalog_types.require_catalog_array_metadata_v1("pg_collation", original)
        row = copy.deepcopy(original)
        key = ref("pg_collation", row.pop("oid"))
        _need(row["collowner"] == 10)
        row["collowner"] = "$PG_BOOTSTRAP_OWNER_10"
        row["collnamespace"] = ref("pg_namespace", row["collnamespace"])
        collations.append([key, row])
    normalized.update(
        languages=sorted(languages, key=canonical),
        language_callbacks=sorted(callbacks, key=canonical),
        collations=sorted(collations, key=canonical),
    )
    callbacks = []
    operators = []
    _need(
        type(value["expression_callbacks"]) is list
        and len(value["expression_callbacks"]) <= 1000
        and type(value["expression_operators"]) is list
        and len(value["expression_operators"]) <= 1000
    )
    for original in value["expression_callbacks"]:
        catalog_types.require_catalog_array_metadata_v1("pg_proc", original)
        row = copy.deepcopy(original)
        oid = row.pop("oid")
        key = ref("pg_proc", oid)
        _need(type(oid) is int and 0 < oid < 2**32 and row["prokind"] == "f")
        public = oid in public_rows
        if public:
            # Same exact complete pg_proc row is independently present in the
            # public function inventory. Never invoke or truncate its body.
            _need(original == public_rows[oid])
            _need(ref("pg_authid", row["proowner"]) == "$OWNER")
            _need(ref("pg_namespace", row["pronamespace"])["identity"] == "public")
            row["proowner"] = "$OWNER"
        else:
            # Actual55 DATA contains internal and native SQL callbacks. This
            # DATA codec adds no executable callback/AM/type authority.
            _need(oid < 16384 and row["proowner"] == 10 and row["pronamespace"] == 11)
            _need(row["prolang"] in (12, 14))
            row["source_builtin_oid"] = oid
            row["proowner"] = "$PG_BOOTSTRAP_OWNER_10"
        for field, catalog in {
            "pronamespace": "pg_namespace",
            "prolang": "pg_language",
            "provariadic": "pg_type",
            "prosupport": "pg_proc",
            "prorettype": "pg_type",
        }.items():
            row[field] = ref(catalog, row[field])
        for field in ("proargtypes", "proallargtypes", "protrftypes"):
            row[field] = (
                None if row[field] is None else [ref("pg_type", v) for v in row[field]]
            )
        row["proacl"] = (
            _acl(row["proacl"], owner)
            if public
            else _bootstrap_acl(row["proacl"], bootstrap_owner)
        )
        row["proargdefaults"] = _node58(original["proargdefaults"], ref=ref)
        callbacks.append([key, row])
    for original in value["expression_operators"]:
        catalog_types.require_catalog_array_metadata_v1("pg_operator", original)
        row = copy.deepcopy(original)
        oid = row.pop("oid")
        key = ref("pg_operator", oid)
        _need(
            type(oid) is int
            and 0 < oid < 16384
            and row["oprowner"] == 10
            and row["oprnamespace"] == 11
        )
        row["source_builtin_oid"] = oid
        row["oprowner"] = "$PG_BOOTSTRAP_OWNER_10"
        for field, catalog in {
            "oprnamespace": "pg_namespace",
            "oprleft": "pg_type",
            "oprright": "pg_type",
            "oprresult": "pg_type",
            "oprcom": "pg_operator",
            "oprnegate": "pg_operator",
            "oprcode": "pg_proc",
            "oprrest": "pg_proc",
            "oprjoin": "pg_proc",
        }.items():
            row[field] = ref(catalog, row[field])
        operators.append([key, row])
    _need(
        len({canonical(row[0]) for row in callbacks}) == len(callbacks)
        and len({canonical(row[0]) for row in operators}) == len(operators)
    )
    normalized.update(
        expression_callbacks=sorted(callbacks, key=canonical),
        expression_operators=sorted(operators, key=canonical),
    )
    return normalized


def _normalize58(document, *, contract, raw_schema, head, normalized_schema):
    read_only = "on"  # This NEW structural DATA purpose has no RW mode.
    # Public DATA replay remains READ ONLY. The owner-maintenance procedure
    # supplies its actually observed write-transaction mode explicitly; it
    # must never change/relabel the captured profile to reuse the reader.
    _need(type(read_only) is str and read_only in ("on", "off"))
    _need(
        type(document) is dict
        and set(document)
        == {
            "schema",
            "profile",
            "head",
            "references",
            "toast",
            "entries",
            "dependencies",
            "shared_dependencies",
            "bootstrap",
            "actor_physical",
        }
    )
    _need(document["schema"] == raw_schema and document["head"] == [head])
    profile = document["profile"]
    _need(
        type(profile) is dict
        and set(profile)
        == {
            "owner",
            "session_owner",
            "role",
            "database",
            "read_only",
            "search_path",
            "timezone",
            "server_version",
            "tls",
            "datum_abi",
        }
    )
    owner = profile["owner"]
    _need(
        type(owner) is str
        and _NAME.fullmatch(owner) is not None
        and not owner.startswith("pg_")
    )
    _need(
        profile["session_owner"] == owner
        and profile["role"] == "none"
        and profile["read_only"] == "on"
        and profile["search_path"] == "pg_catalog"
        and profile["timezone"] == "UTC"
        and profile["tls"] is True
    )
    _need(profile["datum_abi"] == "pg17.amd64.little.datum8.signedchar")
    _need(
        type(profile["database"]) is str
        and _NAME.fullmatch(profile["database"]) is not None
    )
    _need(
        type(profile["server_version"]) is str
        and re.fullmatch(r"17[0-9]{4}", profile["server_version"]) is not None
    )
    function_names = frozenset(contract["functions"])
    references = {}
    _need(
        type(document["references"]) is list
        and 0 < len(document["references"]) <= 40000
    )
    for catalog, oid, address in document["references"]:
        _need(
            catalog in {*_REF_TABLES, "pg_authid"}
            and type(oid) is int
            and 0 < oid < 2**32
        )
        _need(
            type(address) is dict
            and set(address) == {"type", "schema", "name", "identity"}
        )
        _need(
            type(address["type"]) is str
            and type(address["identity"]) is str
            and len(address["identity"]) > 0
        )
        _need((catalog, oid) not in references)
        references[catalog, oid] = copy.deepcopy(address)

    def ref(catalog, oid):
        _need(type(oid) is int and 0 <= oid < 2**32)
        if oid == 0:
            return None
        value = references[catalog, oid]
        if catalog == "pg_authid":
            identity = value["identity"]
            if identity == owner:
                return "$OWNER"
            if identity == "pg_database_owner":
                return "pg_catalog.pg_database_owner"
            _need(False)
        if catalog == "pg_database":
            _need(value["identity"] == profile["database"])
            return "$DATABASE"
        return copy.deepcopy(value)

    entries = document["entries"]
    _need(type(entries) is list and 0 < len(entries) <= 20000)
    physical = document["actor_physical"]
    _need(
        type(physical) is dict
        and set(physical)
        == {
            "database_oid",
            "owner",
            "membership_count",
            "membership_sha256",
            "maintenance_settings_sha256",
        }
    )
    _need(
        type(physical["database_oid"]) is int
        and ref("pg_database", physical["database_oid"]) == "$DATABASE"
    )
    role = physical["owner"]
    _need(
        type(role) is list
        and len(role) == 9
        and type(role[0]) is int
        and role[1] == owner
        and ref("pg_authid", role[0]) == "$OWNER"
        and all(type(flag) is bool for flag in role[2:])
    )
    _need(
        type(physical["membership_count"]) is int
        and physical["membership_count"] >= 0
        and type(physical["membership_sha256"]) is str
        and _HEX.fullmatch(physical["membership_sha256"]) is not None
        and type(physical["maintenance_settings_sha256"]) is str
        and _HEX.fullmatch(physical["maintenance_settings_sha256"]) is not None
    )
    dependencies = document["dependencies"]
    _need(type(dependencies) is list and len(dependencies) <= 100000)
    for edge in dependencies:
        _need(
            type(edge) is list
            and len(edge) == 7
            and edge[0] in _REF_TABLES
            and edge[3] in _REF_TABLES
            and type(edge[1]) is int
            and type(edge[2]) is int
            and type(edge[4]) is int
            and type(edge[5]) is int
            and edge[2] >= 0
            and edge[5] >= 0
            and edge[6] in ("n", "a", "i")
        )
    sequence_oids = {
        row["oid"]
        for kind, row, _ in entries
        if kind == "relation" and row["relkind"] == "S"
    }
    # TOAST allocation identities derive only from actual parent pointers.
    for parent, toast, parent_name in document["toast"]:
        _need(
            parent_name in contract["columns"]
            and ref("pg_class", parent)["identity"] == "public." + parent_name
        )
        _need(ref("pg_class", toast)["identity"] == "pg_toast.pg_toast_" + str(parent))
        references["pg_class", toast] = {
            "type": "toast table",
            "schema": "pg_toast",
            "name": None,
            "identity": "$TOAST_OF:public." + parent_name,
        }
        for kind, row, _extra in entries:
            if kind == "index" and row["indrelid"] == toast:
                index_oid = row["indexrelid"]
                references["pg_class", index_oid] = {
                    "type": "toast index",
                    "schema": "pg_toast",
                    "name": None,
                    "identity": "$TOAST_INDEX_OF:public." + parent_name,
                }
    # FK trigger allocation names are replaced only with their exact named
    # constraint, table, function and complete trigger flags retained below.
    for kind, row, extra in entries:
        if kind == "trigger" and row["tgisinternal"] is True:
            _need(
                extra["constraint_type"] == "f"
                and re.fullmatch(r"RI_ConstraintTrigger_[ac]_[0-9]+", row["tgname"])
                is not None
            )
            references["pg_trigger", row["oid"]] = {
                "type": "constraint trigger",
                "schema": "public",
                "name": None,
                "identity": canonical(
                    [
                        ref("pg_constraint", row["tgconstraint"]),
                        ref("pg_class", row["tgrelid"]),
                        ref("pg_proc", row["tgfoid"]),
                        row["tgtype"],
                    ]
                ).decode("ascii"),
            }
    # Default ACL objects have allocated OIDs and owner names inside their
    # identity text; derive their structural key from explicit typed fields.
    for kind, row, _ in entries:
        if kind == "default_acl":
            references["pg_default_acl", row["oid"]] = {
                "type": "default ACL",
                "schema": None,
                "name": None,
                "identity": canonical(
                    [
                        ref("pg_authid", row["defaclrole"]),
                        ref("pg_namespace", row["defaclnamespace"]),
                        row["defaclobjtype"],
                    ]
                ).decode("ascii"),
            }
    output = []
    tables = {}
    columns = {}
    functions = set()
    keys = set()
    mappings = {
        "relation": {
            "relnamespace": "pg_namespace",
            "reltype": "pg_type",
            "relowner": "pg_authid",
            "relam": "pg_am",
            "reltablespace": "pg_tablespace",
            "reltoastrelid": "pg_class",
        },
        "attribute": {
            "attrelid": "pg_class",
            "atttypid": "pg_type",
            "attcollation": "pg_collation",
        },
        "default": {"adrelid": "pg_class"},
        "constraint": {
            "connamespace": "pg_namespace",
            "conrelid": "pg_class",
            "contypid": "pg_type",
            "conindid": "pg_class",
            "confrelid": "pg_class",
            "conparentid": "pg_constraint",
        },
        "trigger": {
            "tgrelid": "pg_class",
            "tgparentid": "pg_trigger",
            "tgfoid": "pg_proc",
            "tgconstraint": "pg_constraint",
            "tgconstrrelid": "pg_class",
            "tgconstrindid": "pg_class",
        },
        "index": {"indexrelid": "pg_class", "indrelid": "pg_class"},
        "function": {
            "pronamespace": "pg_namespace",
            "proowner": "pg_authid",
            "prolang": "pg_language",
            "provariadic": "pg_type",
            "prosupport": "pg_proc",
            "prorettype": "pg_type",
        },
        "type": {
            "typnamespace": "pg_namespace",
            "typowner": "pg_authid",
            "typrelid": "pg_class",
            "typelem": "pg_type",
            "typarray": "pg_type",
            "typbasetype": "pg_type",
            "typcollation": "pg_collation",
            **{
                name: "pg_proc"
                for name in (
                    "typsubscript",
                    "typinput",
                    "typoutput",
                    "typreceive",
                    "typsend",
                    "typmodin",
                    "typmodout",
                    "typanalyze",
                )
            },
        },
        "sequence": {"seqrelid": "pg_class", "seqtypid": "pg_type"},
        "default_acl": {"defaclrole": "pg_authid", "defaclnamespace": "pg_namespace"},
        "namespace": {"nspowner": "pg_authid"},
        "database": {"datdba": "pg_authid", "dattablespace": "pg_tablespace"},
        "initial_acl": {},
    }
    acl_fields = {
        "relation": "relacl",
        "attribute": "attacl",
        "function": "proacl",
        "type": "typacl",
        "default_acl": "defaclacl",
        "namespace": "nspacl",
        "database": "datacl",
    }
    catalog_for = {
        "relation": "pg_class",
        "default": "pg_attrdef",
        "constraint": "pg_constraint",
        "trigger": "pg_trigger",
        "function": "pg_proc",
        "type": "pg_type",
        "default_acl": "pg_default_acl",
        "namespace": "pg_namespace",
        "database": "pg_database",
    }
    for kind, original, extra in entries:
        _need(kind in mappings and type(original) is dict and type(extra) is dict)
        catalog_types.require_catalog_array_metadata_v1(
            {
                "relation": "pg_class",
                "attribute": "pg_attribute",
                "default": "pg_attrdef",
                "constraint": "pg_constraint",
                "trigger": "pg_trigger",
                "index": "pg_index",
                "function": "pg_proc",
                "type": "pg_type",
                "sequence": "pg_sequence",
                "default_acl": "pg_default_acl",
                "namespace": "pg_namespace",
                "database": "pg_database",
                "initial_acl": "pg_init_privs",
            }[kind],
            original,
        )
        row = copy.deepcopy(original)
        if kind == "initial_acl":
            _need(set(extra) == {"catalog"} and extra["catalog"] in _REF_TABLES)
            catalog = extra["catalog"]
            key = [
                catalog,
                ref(catalog, row["objoid"]),
                row["objsubid"],
                row["privtype"],
            ]
            row["classoid"] = catalog
            row["objoid"] = ref(catalog, row["objoid"])
            if catalog in ("pg_proc", "pg_language"):
                row["initprivs"] = _bootstrap_acl(
                    row["initprivs"], references["pg_authid", 10]["identity"]
                )
            else:
                row["initprivs"] = _acl(row["initprivs"], owner)
        elif kind == "attribute":
            key = [ref("pg_class", row["attrelid"]), row["attnum"]]
        elif kind == "index":
            key = ref("pg_class", row["indexrelid"])
        elif kind == "sequence":
            key = ref("pg_class", row["seqrelid"])
        else:
            key = ref(catalog_for[kind], row.pop("oid"))
        identity = canonical([kind, key])
        _need(identity not in keys)
        keys.add(identity)
        for name, catalog in mappings[kind].items():
            row[name] = ref(catalog, row[name])
        if kind in acl_fields:
            field = acl_fields[kind]
            row[field] = _acl(row[field], owner)
        if kind == "relation":
            _need(
                original["relkind"] in {"r", "i", "S", "t"}
                and original["relrewrite"] == 0
                and not original["relispartition"]
            )
            for name in (
                "relfilenode",
                "relpages",
                "reltuples",
                "relallvisible",
                "relfrozenxid",
                "relminmxid",
            ):
                row.pop(name, None)
            if key["schema"] == "public" and original["relkind"] == "r":
                tables[original["relname"]] = original["oid"]
            if key["schema"] == "pg_toast":
                row["relname"] = key["identity"]
        if kind == "attribute":
            _need(
                type(row["attnum"]) is int
                and row["attnum"] > 0
                and not row["attisdropped"]
            )
            columns.setdefault(original["attrelid"], []).append(
                (row["attnum"], row["attname"])
            )
        if kind == "default":
            seqs = [
                edge[4]
                for edge in dependencies
                if edge[0] == "pg_attrdef"
                and edge[1] == original["oid"]
                and edge[2] == 0
                and edge[3] == "pg_class"
                and edge[4] in sequence_oids
                and edge[5] == 0
                and edge[6] == "n"
            ]
            _need(len(seqs) <= 1)
            column_types = [
                attribute["atttypid"]
                for category, attribute, _ in entries
                if category == "attribute"
                and attribute["attrelid"] == original["adrelid"]
                and attribute["attnum"] == original["adnum"]
            ]
            _need(len(column_types) == 1)
            row["adbin"] = _node58(
                original["adbin"],
                ref=ref,
                sequence_oid=seqs[0] if seqs else None,
                sequence_identity=ref("pg_class", seqs[0]) if seqs else None,
                column_type_oid=column_types[0] if seqs else None,
            )
        if kind == "constraint":
            row["conbin"] = _node58(original["conbin"], ref=ref)
            for field in ("conpfeqop", "conppeqop", "conffeqop", "conexclop"):
                row[field] = (
                    None
                    if row[field] is None
                    else [ref("pg_operator", oid) for oid in row[field]]
                )
        if kind == "trigger":
            row["tgqual"] = _node58(original["tgqual"], ref=ref)
            row["tgname"] = key["identity"] if row["tgisinternal"] else row["tgname"]
        if kind == "index":
            _need(row["indexprs"] is None)
            row["indpred"] = _node58(original["indpred"], ref=ref)
            row["indclass"] = [ref("pg_opclass", oid) for oid in row["indclass"]]
            row["indcollation"] = [
                ref("pg_collation", oid) for oid in row["indcollation"]
            ]
        if kind == "function":
            _need(
                row["proname"] in function_names
                and row["prokind"] == "f"
                and row["prosqlbody"] is None
            )
            functions.add(row["proname"])
            row["proargdefaults"] = _node58(original["proargdefaults"], ref=ref)
            for field in ("proargtypes", "proallargtypes", "protrftypes"):
                row[field] = (
                    None
                    if row[field] is None
                    else [ref("pg_type", oid) for oid in row[field]]
                )
        if kind == "type":
            _need(row["typtype"] in {"c", "b"} and row["typdefaultbin"] is None)
        if kind == "database":
            _need(row["datname"] == profile["database"])
            row["datname"] = "$DATABASE"
            row.pop("datfrozenxid", None)
            row.pop("datminmxid", None)
        output.append([kind, key, row])
    _need(set(tables) == set(contract["columns"]) and functions == function_names)
    _need(sum(kind == "function" for kind, _, _ in entries) == len(function_names))
    for name, oid in tables.items():
        actual_names = [column for _, column in sorted(columns[oid])]
        # ORM declaration order is not historical physical attnum order.
        # Every actual attnum is retained in the normalized row/hash below.
        _need(len(actual_names) == len(contract["columns"][name]))
        _need(set(actual_names) == set(contract["columns"][name]))
    normalized_dependencies = []
    for source, oid, subid, target, refid, refsubid, deptype in dependencies:
        normalized_dependencies.append(
            [
                source,
                ref(source, oid),
                subid,
                target,
                ref(target, refid),
                refsubid,
                deptype,
            ]
        )
    _need(
        len({canonical(edge) for edge in normalized_dependencies})
        == len(normalized_dependencies)
    )
    shared = document["shared_dependencies"]
    normalized_shared = []
    _need(type(shared) is list and len(shared) <= 100000)
    for edge in shared:
        _need(type(edge) is list and len(edge) == 7)
        dbid, source, oid, subid, target, refid, deptype = edge
        _need(
            type(dbid) is int
            and dbid in (0, physical["database_oid"])
            and source in _REF_TABLES
            and target in ("pg_authid", "pg_tablespace")
            and type(subid) is int
            and subid >= 0
            and deptype in ("o", "a", "i", "r", "t")
        )
        normalized_shared.append(
            [
                "$DATABASE" if dbid else "$SHARED_OBJECT",
                source,
                ref(source, oid),
                subid,
                target,
                ref(target, refid),
                deptype,
            ]
        )
    _need(
        len({canonical(edge) for edge in normalized_shared}) == len(normalized_shared)
    )
    return {
        "schema": normalized_schema,
        "source58_scope_sha256": contract["source_scope_sha256"],
        "server_version": profile["server_version"],
        "datum_abi": profile["datum_abi"],
        "entries": sorted(output, key=canonical),
        "dependencies": sorted(normalized_dependencies, key=canonical),
        "shared_dependencies": sorted(normalized_shared, key=canonical),
        "bootstrap": _bootstrap58(
            document["bootstrap"],
            ref,
            references["pg_authid", 10]["identity"],
            {row["oid"]: row for kind, row, _ in entries if kind == "function"},
            owner,
        ),
        "schema_admission": False,
        "paper_actor_admission": False,
        "execution_authority": False,
    }


def normalize_catalog58_v1(document):
    _plain(document)
    source = shape.source_shape_v1()
    contract = {
        "columns": source["column_names"],
        "functions": source["functions"],
        "source_scope_sha256": source["source_scope_sha256"],
    }
    return _normalize58(
        document,
        contract=contract,
        raw_schema=RAW_SCHEMA,
        head="0058",
        normalized_schema=SCHEMA,
    )


def _source55_diagnostic_v1(document):
    """NEW development purpose on unretagged genuine55 DATA; no58 claim."""
    _plain(document)
    path = (
        shape.ROOT / "tests/fixtures/paper58_source55_prefix_v1/canonical0055-raw.json"
    )
    raw = path.read_bytes()
    _need(
        hashlib.sha256(raw).hexdigest()
        == "1756e0ad43a255f76199be126ffd4dfd8197b133ae033efccfa27ab24a004d92"
    )
    prefix = json.loads(raw)
    tables = {
        row["oid"]: row["relname"]
        for kind, row, _ in prefix["entries"]
        if kind == "relation" and row["relkind"] == "r"
    }
    columns = {name: [] for name in tables.values()}
    for kind, row, _ in prefix["entries"]:
        if kind == "attribute" and row["attrelid"] in tables:
            columns[tables[row["attrelid"]]].append((row["attnum"], row["attname"]))
    source = shape.source_shape_v1()
    contract = {
        "columns": {
            name: [column for _, column in sorted(items)]
            for name, items in columns.items()
        },
        "functions": [
            row["proname"] for kind, row, _ in prefix["entries"] if kind == "function"
        ],
        "source_scope_sha256": source["source_scope_sha256"],
    }
    return _normalize58(
        document,
        contract=contract,
        raw_schema="ctcc.canonical0055.full_catalog_data.v1",
        head="0055",
        normalized_schema="ctcc.paper58.source55_codec_diagnostic.v1",
    )
