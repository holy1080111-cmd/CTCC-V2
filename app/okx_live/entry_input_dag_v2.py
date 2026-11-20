"""Per-call exact input DAG; no retained computation or execution authority.

Every edge/path/multiplicity is recorded. Identity sharing is encoded rather
than content-deduplicated. Expanded subtree costs are charged on every edge,
including cached references, before the bounded plain graph is serialized once.
"""

import dataclasses
import math
import sys
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from enum import EnumType
from fractions import Fraction
from gc import get_referents
from types import (
    GetSetDescriptorType,
    MappingProxyType,
    MemberDescriptorType,
    ModuleType,
)

from pydantic import BaseModel
from pydantic_core import TzInfo

from app.domain.source_primitives import sha

_MAX_NODES = 500_000
_MAX_BYTES = 256 * 1024 * 1024
_MAX_CONTAINER = 8192
_MAX_DEPTH = 40
_MODEL_META = type(BaseModel)
_ABSENT = object()


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


def _native_descriptor(kind, name, expected):
    for owner in type.__getattribute__(kind, "__mro__"):
        namespace = type.__getattribute__(owner, "__dict__")
        _need(type(namespace) is MappingProxyType)
        if name in namespace:
            descriptor = namespace[name]
            _need(
                type(descriptor) is expected
                and descriptor.__objclass__ is owner
                and descriptor.__name__ == name
            )
            return descriptor
    return _ABSENT


def _data_values(item, fields):
    """Read actual dictionary/native slots without executing a property."""
    kind = type(item)
    descriptor = _native_descriptor(kind, "__dict__", GetSetDescriptorType)
    if descriptor is not _ABSENT:
        values = GetSetDescriptorType.__get__(descriptor, item, kind)
        _need(
            type(values) is dict
            and all(type(k) is str for k in values)
            and len(values) == len(fields)
            and all(field in values for field in fields)
        )
        return tuple(values[field] for field in fields)
    result = []
    for field in fields:
        descriptor = _native_descriptor(kind, field, MemberDescriptorType)
        _need(descriptor is not _ABSENT)
        try:
            value = MemberDescriptorType.__get__(descriptor, item, kind)
        except AttributeError:
            _need(False)
        result.append(value)
    return tuple(result)


def _mapping(value):
    if type(value) is dict:
        return value
    refs = get_referents(value)
    _need(type(value) is MappingProxyType and len(refs) == 1 and type(refs[0]) is dict)
    return refs[0]


def exact_input_dag_digest_v2(value, *, invocation):
    """Fresh exact DAG with untracked byte records, never a cross-call cache."""
    from hashlib import sha256

    prefix = b"ctcc.native_live.exact_entry_input_dag.binary.v2\n"
    digest = sha256(prefix)
    wire_total = len(prefix)
    _need(wire_total <= _MAX_BYTES)
    memo, active, schema_memo = {}, set(), {}
    items, node_costs, byte_costs, heights = [], [], [], []

    def schema(kind, tag):
        nonlocal wire_total
        found = schema_memo.get(kind)
        if found is not None:
            _need(found[0] == tag)
            return found[1:]
        _need(len(schema_memo) < 1024)
        name = _class_name(kind)
        _need(len(name) <= 512)
        if tag == b"M":
            namespace = type.__getattribute__(kind, "__dict__")
            _need(type(namespace) is MappingProxyType)
            declared = namespace.get("__pydantic_fields__")
            _need(type(declared) is dict and all(type(k) is str for k in declared))
            fields = tuple(declared)
        elif tag == b"D":
            declared = dataclasses.fields(kind)
            _need(
                all(
                    type(f) is dataclasses.Field and type(f.name) is str
                    for f in declared
                )
            )
            fields = tuple(f.name for f in declared)
        else:
            fields = ()
        _need(len(fields) <= _MAX_CONTAINER)
        index = len(schema_memo)
        encoded = name.encode("utf-8")
        wire = bytearray(b"S" + index.to_bytes(8, "big") + tag)
        wire.extend(len(encoded).to_bytes(8, "big"))
        wire.extend(encoded)
        wire.extend(len(fields).to_bytes(8, "big"))
        cost = len(encoded) + 16
        for field in fields:
            raw = field.encode("utf-8")
            wire.extend(len(raw).to_bytes(8, "big"))
            wire.extend(raw)
            cost += len(raw) + 8
        wire_total += len(wire)
        _need(wire_total <= _MAX_BYTES)
        digest.update(wire)
        schema_memo[kind] = tag, index, fields, cost
        return index, fields, cost

    def visit(item, depth=0):
        nonlocal wire_total
        _need(depth <= _MAX_DEPTH)
        identity, kind = id(item), type(item)
        _need(identity not in active)
        found = memo.get(identity)
        if found is not None:
            _need(
                items[found] is item
                and node_costs[found] > 0
                and depth + heights[found] <= _MAX_DEPTH
            )
            return found
        _need(len(items) < _MAX_NODES)
        index = len(items)
        memo[identity] = index
        items.append(item)  # Retain every object so id reuse cannot merge nodes.
        node_costs.append(0)
        byte_costs.append(0)
        heights.append(0)
        expanded_nodes, expanded_bytes, height = 1, 16, 0
        children, payload = None, b""
        if item is invocation:
            tag = b"V"
        elif item is None:
            tag = b"N"
        elif kind is bool:
            tag, payload = b"B", b"1" if item else b"0"
        elif kind is int:
            _need(item.bit_length() <= 4096)
            tag, payload = b"I", str(item).encode("ascii")
            expanded_bytes += max(1, (item.bit_length() + 7) // 8)
        elif kind is str:
            _need(len(item) <= 8 * 1024 * 1024)
            tag, payload = b"S", item.encode("utf-8", "surrogatepass")
            expanded_bytes += len(payload)
        elif kind is bytes:
            _need(len(item) <= _MAX_BYTES)
            tag, payload = (
                b"R",
                len(item).to_bytes(8, "big") + sha(item).encode("ascii"),
            )
            expanded_bytes += len(item)
        elif kind is Decimal:
            _need(item.is_finite())
            tag, payload = b"d", str(item).encode("ascii")
            expanded_bytes += len(payload)
        elif kind is float:
            _need(math.isfinite(item))
            tag, payload = b"f", item.hex().encode("ascii")
            expanded_bytes += 24
        elif kind is datetime:
            tz = item.tzinfo
            _need(tz is UTC or type(tz) in (timezone, TzInfo))
            _need(item.utcoffset() == timedelta(0))
            name = (type(tz).__module__ + "." + type(tz).__qualname__).encode("ascii")
            at = item.isoformat().encode("ascii")
            tag = b"T"
            payload = len(name).to_bytes(8, "big") + name + bytes((item.fold,)) + at
            expanded_bytes += len(at) + 32
        elif kind is Fraction:
            tag, children = b"q", (item.numerator, item.denominator)
        elif kind in (tuple, list, set, frozenset):
            _need(len(item) <= _MAX_CONTAINER)
            tag = {tuple: b"t", list: b"l", set: b"s", frozenset: b"F"}[kind]
            children = item
        elif kind in (dict, MappingProxyType):
            values = _mapping(item)
            _need(len(values) <= _MAX_CONTAINER)
            tag = b"o" if kind is dict else b"p"
            children = (child for pair in values.items() for child in pair)
        elif type(kind) is EnumType:
            declared, _fields, cost = schema(kind, b"E")
            member = object.__getattribute__(item, "_name_")
            _need(member is None or type(member) is str)
            encoded = b"N" if member is None else b"S" + member.encode("utf-8")
            tag, payload = b"E", declared.to_bytes(8, "big") + encoded
            children = (object.__getattribute__(item, "_value_"),)
            expanded_bytes += cost + len(encoded)
        elif type(kind) is _MODEL_META and BaseModel in type.__getattribute__(
            kind, "__mro__"
        ):
            declared, fields, cost = schema(kind, b"M")
            values = _model_values(item, fields)
            tag, payload, children = b"M", declared.to_bytes(8, "big"), values.values()
            expanded_nodes += len(fields)
            expanded_bytes += cost
        elif type(kind) is type and dataclasses.is_dataclass(kind):
            declared, fields, cost = schema(kind, b"D")
            children = _data_values(item, fields)
            tag, payload = b"D", declared.to_bytes(8, "big")
            expanded_nodes += len(fields)
            expanded_bytes += cost
        else:
            _need(False)
        references = bytearray()
        if children is not None:
            active.add(identity)
            for child in children:
                target = visit(child, depth + 1)
                references.extend(target.to_bytes(8, "big"))
                child_height = heights[target]
                if child_height >= height:
                    height = 1 + child_height
                expanded_nodes += node_costs[target]
                expanded_bytes += byte_costs[target] + 8
                _need(expanded_nodes <= _MAX_NODES and expanded_bytes <= _MAX_BYTES)
            active.remove(identity)
        _need(
            expanded_nodes <= _MAX_NODES
            and expanded_bytes <= _MAX_BYTES
            and depth + height <= _MAX_DEPTH
        )
        # Every metadata/edge segment is length framed; the node id is explicit.
        # Bytes/bytearrays and flat cost lists avoid a tracked container per node.
        wire_total += 26 + len(payload) + len(references)
        _need(wire_total <= _MAX_BYTES)
        digest.update(
            b"N" + index.to_bytes(8, "big") + tag + len(payload).to_bytes(8, "big")
        )
        digest.update(payload)
        digest.update(len(references).to_bytes(8, "big"))
        digest.update(references)
        node_costs[index], byte_costs[index], heights[index] = (
            expanded_nodes,
            expanded_bytes,
            height,
        )
        return index

    try:
        root = visit(value)
        _need(wire_total + 20 <= _MAX_BYTES)
        digest.update(b"ROOT" + root.to_bytes(8, "big") + len(items).to_bytes(8, "big"))
        return digest.hexdigest()
    finally:
        visit = None  # Break the private recursion cell on success AND rejection.
