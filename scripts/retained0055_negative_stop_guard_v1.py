"""Source-owned legacy control/audit guards for negative revision-zero stop.

Catalog DATA only. No SQL expression is evaluated or deparsed here. The caller
must first run the native storage/index/type bootstrap admission and hold the
actual source55 marker/table locks. This module grants no write authority.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from scripts import native_owner_index_catalog_v1 as native_index
from scripts import retained0017_catalog_baseline_v1 as baseline

ROOT = Path(__file__).resolve().parents[1]
STATE = "demo_automation_state"
AUDIT = "audit_logs"
GUARD = "demo_automation_control_revision_guard"
REASON = "emergency_stop_engaged"

CATALOG_SQL = r"""SELECT pg_catalog.jsonb_build_object(
 'functions',(SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
  'oid',p.oid::pg_catalog.int8,'owner',p.proowner::pg_catalog.int8,
  'language',l.lanname::pg_catalog.text,'return_type',p.prorettype::pg_catalog.int8,
  'argument_types',p.proargtypes::pg_catalog.oid[]::pg_catalog.int8[],
  'argument_count',p.pronargs,'default_count',p.pronargdefaults,
  'kind',p.prokind::pg_catalog.text,'security_definer',p.prosecdef,
  'strict',p.proisstrict,'retset',p.proretset,'leakproof',p.proleakproof,
  'volatility',p.provolatile::pg_catalog.text,'parallel',p.proparallel::pg_catalog.text,
  'variadic',p.provariadic::pg_catalog.int8,'support',p.prosupport::pg_catalog.oid::pg_catalog.int8,
  'argument_names',p.proargnames,'all_argument_types',p.proallargtypes::pg_catalog.int8[],
  'argument_modes',p.proargmodes,'transforms',p.protrftypes::pg_catalog.int8[],
  'cost_source_default',p.procost=100,'rows_source_default',p.prorows=0,
  'config',p.proconfig,'binary',p.probin,'defaults',p.proargdefaults::pg_catalog.text,
  'parsed_body',p.prosqlbody::pg_catalog.text,'acl',p.proacl,'source',p.prosrc)
  ORDER BY p.oid) FROM pg_catalog.pg_proc p
  JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
  JOIN pg_catalog.pg_language l ON l.oid=p.prolang
  WHERE n.nspname='public' AND p.proname='demo_automation_control_revision_guard'),
 'triggers',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(
  c.relname,t.tgname,t.tgfoid::pg_catalog.int8,t.tgtype,t.tgenabled::pg_catalog.text,
  t.tgisinternal,t.tgnargs,t.tgattr::pg_catalog.text,pg_catalog.encode(t.tgargs,'hex'),
  t.tgqual IS NULL,t.tgdeferrable,t.tginitdeferred,t.tgconstraint::pg_catalog.int8,
  t.tgparentid::pg_catalog.int8,t.tgconstrrelid::pg_catalog.int8,t.tgconstrindid::pg_catalog.int8,
  t.tgoldtable,t.tgnewtable) ORDER BY c.relname,t.tgname),'[]'::pg_catalog.jsonb)
  FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relname IN ('demo_automation_state','audit_logs')),
 'audit_defaults',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(
  a.attname,d.adbin::pg_catalog.text) ORDER BY a.attnum),'[]'::pg_catalog.jsonb)
  FROM pg_catalog.pg_attrdef d JOIN pg_catalog.pg_attribute a
   ON a.attrelid=d.adrelid AND a.attnum=d.adnum
  JOIN pg_catalog.pg_class c ON c.oid=d.adrelid
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relname='audit_logs'),
 'audit_constraints',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(
  k.conname,k.contype::pg_catalog.text,k.conkey,k.condeferrable,k.condeferred,k.convalidated)
  ORDER BY k.conname),'[]'::pg_catalog.jsonb) FROM pg_catalog.pg_constraint k
  JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relname='audit_logs'),
 'state_checks',(SELECT COALESCE(pg_catalog.jsonb_agg(k.conbin::pg_catalog.text
  ORDER BY k.conname),'[]'::pg_catalog.jsonb) FROM pg_catalog.pg_constraint k
  JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relname='demo_automation_state' AND k.contype='c'),
 'indexes',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(
  c.relname,i.indexprs IS NULL,i.indpred IS NULL,i.indisvalid,i.indisready,i.indislive)
  ORDER BY c.relname,i.indexrelid),'[]'::pg_catalog.jsonb)
  FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indrelid
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relname IN ('demo_automation_state','audit_logs')))
"""


class NegativeStopGuardRefused(ValueError):
    pass


def _need(value):
    if value is not True:
        raise NegativeStopGuardRefused(
            "retained0055_negative_stop_catalog_refused"
        ) from None


def _plain_catalog_data(value):
    pending = [(value, 0)]
    containers = set()
    count = capacity = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        _need(depth <= 128 and count <= 100000)
        kind = type(item)
        if kind is list or kind is dict:
            _need(id(item) not in containers)
            containers.add(id(item))
            if kind is dict:
                for key, child in item.items():
                    _need(type(key) is str)
                    capacity += len(key.encode("utf-8"))
                    pending.append((child, depth + 1))
            else:
                pending.extend((child, depth + 1) for child in item)
        elif kind is str:
            capacity += len(item.encode("utf-8"))
        else:
            _need(item is None or kind is int or kind is bool)
        _need(capacity <= 8 * 1024 * 1024)


def source_guard_body_v1():
    path = ROOT / "migrations/versions/0034_demo_automation_control_cas.py"
    tree = ast.parse(path.read_bytes().decode("utf-8"))
    values = [
        node.value.value
        for node in tree.body
        if type(node) is ast.Assign
        and len(node.targets) == 1
        and type(node.targets[0]) is ast.Name
        and node.targets[0].id == "CONTROL_GUARD_FUNCTION_SQL"
        and type(node.value) is ast.Constant
        and type(node.value.value) is str
    ]
    _need(len(values) == 1)
    match = re.fullmatch(
        r"\s*CREATE FUNCTION demo_automation_control_revision_guard\(\)\s*"
        r"RETURNS trigger\s*LANGUAGE plpgsql\s*AS \$\$(.*?)\$\$\s*",
        values[0],
        re.DOTALL,
    )
    _need(match is not None)
    return match[1]


def _now_default(raw):
    node = baseline.normalize_node_tree17_v1(raw)
    _need(type(node) is list and len(node) == 3 and node[:2] == ["NODE", "FUNCEXPR"])
    fields = dict(node[2])
    expected = {
        "funcid": [1299],
        "funcresulttype": [1184],
        "funcretset": ["false"],
        "funcvariadic": ["false"],
        "funcformat": [0],
        "funccollid": [0],
        "inputcollid": [0],
        "args": ["<>"],
        "location": ["$PARSER_LOCATION"],
    }
    _need(fields == expected)


def require_control_audit_v1(document, layouts, owner_oid):
    """No getter/callback, deparser, SQL or caller acceptance flag."""
    _plain_catalog_data(document)
    _plain_catalog_data(layouts)
    _need(
        type(document) is dict
        and set(document)
        == {
            "functions",
            "triggers",
            "audit_defaults",
            "audit_constraints",
            "state_checks",
            "indexes",
        }
    )
    _need(type(owner_oid) is int and owner_oid > 0 and type(layouts) is list)
    named = {name: columns for name, _oid, _kind, columns in layouts}
    contract = baseline.source17_contract_v1()["columns"]
    _need(
        [column[0] for column in named[STATE]]
        == contract[STATE] + ["control_revision", "restart_latch_required"]
    )
    _need([column[0] for column in named[AUDIT]] == contract[AUDIT])
    expected_control = {
        "id": [23, -1, True, 0, "", ""],
        "armed": [16, -1, True, 0, "", ""],
        "emergency_stop": [16, -1, True, 0, "", ""],
        "locked": [16, -1, True, 0, "", ""],
        "lock_reasons": [3802, -1, True, 0, "", ""],
        "control_revision": [20, -1, True, 0, "", ""],
        "restart_latch_required": [16, -1, True, 0, "", ""],
    }
    control_columns = {column[0]: column[1:] for column in named[STATE]}
    _need(
        all(
            baseline.canonical(control_columns.get(name)) == baseline.canonical(value)
            for name, value in expected_control.items()
        )
    )
    _need(
        baseline.canonical(named[AUDIT])
        == baseline.canonical(
            [
                ["id", 2950, -1, True, 0, "", ""],
                ["actor", 1043, 104, True, 100, "", ""],
                ["action", 1043, 104, True, 100, "", ""],
                ["resource_type", 1043, 54, True, 100, "", ""],
                ["resource_id", 1043, 104, False, 100, "", ""],
                ["before", 3802, -1, False, 0, "", ""],
                ["after", 3802, -1, False, 0, "", ""],
                ["ip_address", 869, -1, False, 0, "", ""],
                ["occurred_at", 1184, -1, True, 0, "", ""],
            ]
        )
    )
    functions = document["functions"]
    _need(
        type(functions) is list and len(functions) == 1 and type(functions[0]) is dict
    )
    function = functions[0]
    expected = {
        "owner": owner_oid,
        "language": "plpgsql",
        "return_type": 2279,
        "argument_types": [],
        "argument_count": 0,
        "default_count": 0,
        "kind": "f",
        "security_definer": False,
        "strict": False,
        "retset": False,
        "leakproof": False,
        "volatility": "v",
        "parallel": "u",
        "variadic": 0,
        "support": 0,
        "config": None,
        "argument_names": None,
        "all_argument_types": None,
        "argument_modes": None,
        "transforms": None,
        "cost_source_default": True,
        "rows_source_default": True,
        "binary": None,
        "defaults": None,
        "parsed_body": None,
        "acl": None,
        "source": source_guard_body_v1(),
    }
    _need(set(function) == set(expected) | {"oid"})
    _need(type(function["oid"]) is int and 16384 <= function["oid"] < 2**32)
    _need(
        all(
            type(function[name]) is type(value) and function[name] == value
            for name, value in expected.items()
        )
    )
    _need(
        baseline.canonical(document["triggers"])
        == baseline.canonical(
            [
                [
                    STATE,
                    GUARD,
                    function["oid"],
                    19,
                    "O",
                    False,
                    0,
                    "",
                    "",
                    True,
                    False,
                    False,
                    0,
                    0,
                    0,
                    0,
                    None,
                    None,
                ]
            ]
        )
    )
    defaults = document["audit_defaults"]
    _need(
        type(defaults) is list
        and len(defaults) == 1
        and type(defaults[0]) is list
        and len(defaults[0]) == 2
        and defaults[0][0] == "occurred_at"
        and type(defaults[0][1]) is str
    )
    _now_default(defaults[0][1])
    # No CHECK/FK/exclusion expression may run while inserting the journal.
    _need(
        baseline.canonical(document["audit_constraints"])
        == baseline.canonical([["pk_audit_logs", "p", [1], False, False, True]])
    )
    _need(type(document["indexes"]) is list and bool(document["indexes"]))
    _need(
        all(
            type(row) is list
            and len(row) == 6
            and row[0] in (STATE, AUDIT)
            and all(value is True for value in row[1:])
            for row in document["indexes"]
        )
    )
    checks = document["state_checks"]
    _need(type(checks) is list and len(checks) <= 64)
    facts = native_index._source_facts()
    operators = {row[0] for row in facts["operators"]}
    procedures = {row[0] for row in facts["procedures"]}
    for raw in checks:
        node = baseline.normalize_node_tree17_v1(raw)
        # Source control checks are pure boolean/native comparisons; no user
        # function can run before/after the exact negative CAS.
        pending = [node]
        while pending:
            item = pending.pop()
            if type(item) is not list:
                continue
            if item[:1] == ["NODE"]:
                _need(item[1] not in ("FUNCEXPR", "COERCEVIAIO"))
                for name, values in item[2]:
                    if name == "opno":
                        _need(len(values) == 1 and values[0] in operators)
                    elif name in ("opfuncid", "funcid"):
                        _need(len(values) == 1 and values[0] in procedures)
                    pending.extend(value for value in values if type(value) is list)
            else:
                pending.extend(value for value in item if type(value) is list)
    return baseline.sha(baseline.canonical(document))
