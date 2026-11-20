"""Per-call exact input DAG; no retained computation or execution authority.

Every edge/path/multiplicity is recorded. Identity sharing is encoded rather
than content-deduplicated. Expanded subtree costs are charged on every edge,
including cached references, before the bounded plain graph is serialized once.
"""

import dataclasses
import json
import math
import sys
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from enum import EnumType
from fractions import Fraction
from gc import get_referents
from types import MappingProxyType, ModuleType

from pydantic import BaseModel
from pydantic_core import TzInfo

from app.domain.source_primitives import sha

_MAX_NODES = 500_000
_MAX_BYTES = 256 * 1024 * 1024
_MAX_CONTAINER = 8192
_MAX_DEPTH = 40
_MODEL_META = type(BaseModel)


def _need(value):
    if value is not True:
        raise ValueError("native_live_entry_original_inputs_changed") from None


def _class_name(kind):
    """Unknown metaclasses/instance getters are never invoked to classify DATA."""
    _need(type(kind) in (type, _MODEL_META, EnumType))
    module_name = type.__getattribute__(kind, "__module__")
    name = type.__getattribute__(kind, "__name__")
    qualified = type.__getattribute__(kind, "__qualname__")
    _need(type(module_name) is type(name) is type(qualified) is str)
    module = sys.modules.get(module_name)
    _need(
        module_name.startswith("app.")
        and type(module) is ModuleType
        and vars(module).get(name) is kind
    )
    return module_name + "." + qualified


def _model_values(item, fields):
    values = object.__getattribute__(item, "__dict__")
    _need(
        type(values) is dict
        and object.__getattribute__(item, "__pydantic_extra__") is None
        and len(values) == len(fields)
        and all(type(k) is str for k in values)
        and tuple(values) == fields
    )
    return values


def _data_values(item, fields):
    try:
        values = object.__getattribute__(item, "__dict__")
    except AttributeError:
        values = None
    if values is not None:
        _need(
            type(values) is dict
            and all(type(k) is str for k in values)
            and len(values) == len(fields)
            and all(field in values for field in fields)
        )


def _mapping(value):
    if type(value) is dict:
        return value
    refs = get_referents(value)
    _need(type(value) is MappingProxyType and len(refs) == 1 and type(refs[0]) is dict)
    return refs[0]


def exact_input_dag_digest_v2(value, *, invocation):
    """New graph/memo on EVERY call; a hash/DTO never owns a continuation."""
    nodes, memo, active = [], {}, set()
    schemas, schema_memo = [], {}

    def schema(kind, tag):
        found = schema_memo.get(kind)
        if found is not None:
            _need(found[0] == tag)
            return found[1:]
        _need(len(schemas) < 1024)
        name = _class_name(kind)
        _need(len(name) <= 512)
        if tag == "model":
            fields = type.__getattribute__(kind, "model_fields")
            _need(type(fields) is dict and all(type(k) is str for k in fields))
            fields = tuple(fields)
        elif tag == "dataclass":
            fields = dataclasses.fields(kind)
            _need(
                all(
                    type(f) is dataclasses.Field and type(f.name) is str for f in fields
                )
            )
            fields = tuple(field.name for field in fields)
        else:
            fields = ()
        _need(len(fields) <= _MAX_CONTAINER)
        index = len(schemas)
        cost = (
            len(name.encode("utf-8"))
            + sum(len(field.encode("utf-8")) + 8 for field in fields)
            + 16
        )
        schemas.append([tag, name, fields])
        schema_memo[kind] = tag, index, fields, cost
        return index, fields, cost

    def visit(item, depth=0):
        _need(depth <= _MAX_DEPTH)
        identity, kind = id(item), type(item)
        found = memo.get(identity)
        if found is not None:
            _need(found[0] is item and depth + found[4] <= _MAX_DEPTH)
            return found[1:]
        _need(identity not in active)
        _need(len(nodes) < _MAX_NODES)
        index = len(nodes)
        nodes.append(None)
        expanded_nodes, expanded_bytes, height = 1, 16, 0
        children = None
        if item is invocation:
            record = ["original_invocation"]
        elif item is None:
            record = ["none"]
        elif kind is bool:
            record = ["bool", item]
        elif kind is int:
            _need(item.bit_length() <= 4096)
            record = ["int", item]
            expanded_bytes += max(1, (item.bit_length() + 7) // 8)
        elif kind is str:
            _need(len(item) <= 8 * 1024 * 1024)
            expanded_bytes += (
                len(item)
                if item.isascii()
                else len(item.encode("utf-8", "surrogatepass"))
            )
            record = ["str", item]
        elif kind is bytes:
            _need(len(item) <= _MAX_BYTES)
            expanded_bytes += len(item)
            record = ["bytes", len(item), sha(item)]
        elif kind is Decimal:
            _need(item.is_finite())
            payload = str(item)
            expanded_bytes += len(payload)
            record = ["decimal", payload]
        elif kind is Fraction:
            record, children = ["fraction"], (item.numerator, item.denominator)
        elif kind is float:
            _need(math.isfinite(item))
            record = ["float", item.hex()]
            expanded_bytes += 24
        elif kind is datetime:
            tz = item.tzinfo
            _need(tz is UTC or type(tz) in (timezone, TzInfo))
            _need(item.utcoffset() == timedelta(0))
            payload = item.isoformat()
            record = [
                "datetime",
                type(tz).__module__ + "." + type(tz).__qualname__,
                payload,
                item.fold,
            ]
            expanded_bytes += len(payload) + 32
        elif kind in (tuple, list, set, frozenset):
            _need(len(item) <= _MAX_CONTAINER)
            record, children = [kind.__name__], item
        elif kind in (dict, MappingProxyType):
            values = _mapping(item)
            _need(len(values) <= _MAX_CONTAINER)
            record = ["dict" if kind is dict else "mapping_proxy"]
            # Pair order and every key/value edge remain explicit in this
            # flat alternating sequence; no equality/content deduplication.
            children = tuple(child for pair in values.items() for child in pair)
        elif type(kind) is EnumType:
            declared, _fields, cost = schema(kind, "enum")
            member = object.__getattribute__(item, "_name_")
            _need(member is None or type(member) is str)
            record = ["enum", declared, member]
            children = (object.__getattribute__(item, "_value_"),)
            expanded_bytes += cost + (
                0 if member is None else len(member.encode("utf-8"))
            )
        elif type(kind) is _MODEL_META and BaseModel in type.__getattribute__(
            kind, "__mro__"
        ):
            declared, fields, cost = schema(kind, "model")
            values = _model_values(item, fields)
            record, children = ["model", declared], values.values()
            expanded_nodes += len(fields)
            expanded_bytes += cost
        elif type(kind) is type and dataclasses.is_dataclass(kind):
            declared, fields, cost = schema(kind, "dataclass")
            _data_values(item, fields)
            record = ["dataclass", declared]
            children = tuple(object.__getattribute__(item, field) for field in fields)
            expanded_nodes += len(fields)
            expanded_bytes += cost
        else:
            _need(False)
        if children is not None:
            # Only compound nodes need cycle bookkeeping or edge work. In
            # particular, scalar nodes allocate no recursive edge closure.
            active.add(identity)
            references = []
            for child in children:
                target, node_cost, byte_cost, child_height = visit(child, depth + 1)
                references.append(target)
                if child_height >= height:
                    height = 1 + child_height
                expanded_nodes += node_cost
                expanded_bytes += byte_cost + 8
                _need(expanded_nodes <= _MAX_NODES and expanded_bytes <= _MAX_BYTES)
            record.append(references)
            active.remove(identity)
        _need(
            expanded_nodes <= _MAX_NODES
            and expanded_bytes <= _MAX_BYTES
            and depth + height <= _MAX_DEPTH
        )
        nodes[index] = record
        memo[identity] = item, index, expanded_nodes, expanded_bytes, height
        return index, expanded_nodes, expanded_bytes, height

    root, _count, _bytes, _height = visit(value)
    # Every node is built from the closed scalars/arrays above. No object
    # encoder/default callback, repr, model dump or persisted cache is used.
    raw = json.dumps(
        ["ctcc.native_live.exact_entry_input_dag.v2", root, schemas, nodes],
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    _need(len(raw) <= _MAX_BYTES)
    return sha(raw)
