"""Exact input/byte observations; a digest or DTO never owns an entry permit.

The coordinator's private same-invocation pending proof is the only consumer.
Every invocation of these readers reopens the original custody. The temporary
Merkle memo visits shared objects once in ONE synchronous traversal only; no
model, source observation or authorization is retained across guard calls.
"""

import asyncio
import dataclasses
import math
import re
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from types import MappingProxyType, ModuleType

from pydantic import BaseModel

from app.domain.source_primitives import canonical, decode, sha
from app.exchange.okx import live_account_config_custody_v1 as custody
from app.exchange.okx import live_account_current_chain_v1 as current
from app.exchange.okx import live_account_history_chain_v1 as history
from app.okx_live import trusted_source_bridge_v1 as source

_ID = re.compile(r"[a-f0-9]{32}\Z", re.ASCII)
_MAX_BYTES = 256 * 1024 * 1024


def _need(value):
    if value is not True:
        raise ValueError("native_live_entry_original_inputs_changed") from None


def _original_class(kind):
    module = sys.modules.get(kind.__module__)
    _need(
        kind.__module__.startswith("app.")
        and type(module) is ModuleType
        and vars(module).get(kind.__name__) is kind
    )


def exact_input_digest_v1(value, *, invocation):
    """Hash the actual model graph, never a model's saved receipt alone."""
    memo, active = {}, set()
    budget = [500_000]

    def visit(item, depth=0):
        budget[0] -= 1
        _need(depth <= 40 and budget[0] >= 0)
        kind = type(item)
        if item is invocation:
            return b"V"
        elif item is None or kind in (str, bool, int):
            _need(kind is not str or len(item) <= 8 * 1024 * 1024)
            if item is None:
                return b"N"
            if kind is bool:
                return b"T" if item else b"F"
            if kind is int:
                _need(item.bit_length() <= 4096)
                return _frame(b"I", str(item).encode("ascii"))
            return _frame(b"S", item.encode("utf-8", "surrogatepass"))
        elif kind is bytes:
            _need(len(item) <= _MAX_BYTES)
            return _frame(
                b"Y", str(len(item)).encode("ascii") + b":" + sha(item).encode("ascii")
            )
        elif kind is Decimal:
            _need(item.is_finite())
            return _frame(b"D", str(item).encode("ascii"))
        elif kind is Fraction:
            return _frame(
                b"Q",
                visit(item.numerator, depth + 1) + visit(item.denominator, depth + 1),
            )
        elif kind is float:
            _need(math.isfinite(item))
            return _frame(b"R", item.hex().encode("ascii"))
        elif kind is datetime:
            _need(item.tzinfo is not None and item.utcoffset() == timedelta(0))
            return _frame(b"U", item.isoformat().encode("ascii"))
        elif isinstance(item, Enum):
            _original_class(kind)
            return _frame(
                b"E",
                visit(kind.__module__, depth + 1)
                + visit(kind.__qualname__, depth + 1)
                + visit(item.value, depth + 1),
            )
        else:
            identity = id(item)
            _need(identity not in active)
            if identity in memo:
                original, encoded = memo[identity]
                _need(original is item)
                return encoded
            active.add(identity)
            if kind in (list, tuple, set, frozenset):
                _need(len(item) <= 8192)
                parts = [visit(child, depth + 1) for child in item]
                if kind in (set, frozenset):
                    parts.sort()
                encoded = _frame(kind.__name__.encode("ascii"), b"".join(parts))
            elif kind in (dict, MappingProxyType):
                _need(len(item) <= 8192)
                pairs = sorted(
                    (visit(key, depth + 1), visit(child, depth + 1))
                    for key, child in item.items()
                )
                encoded = _frame(
                    b"K" if kind is dict else b"P",
                    b"".join(key + child for key, child in pairs),
                )
            elif isinstance(item, BaseModel):
                _original_class(kind)
                _need(
                    kind.__module__.startswith("app.")
                    and item.__pydantic_extra__ is None
                )
                values = object.__getattribute__(item, "__dict__")
                _need(type(values) is dict and set(values) == set(kind.model_fields))
                encoded = _frame(
                    b"M",
                    visit(kind.__module__, depth + 1)
                    + visit(kind.__qualname__, depth + 1)
                    + visit(values, depth + 1),
                )
            elif dataclasses.is_dataclass(item) and not isinstance(item, type):
                _original_class(kind)
                _need(kind.__module__.startswith("app."))
                members = b"".join(
                    visit(field.name, depth + 1)
                    + visit(object.__getattribute__(item, field.name), depth + 1)
                    for field in dataclasses.fields(item)
                )
                encoded = _frame(
                    b"C",
                    visit(kind.__module__, depth + 1)
                    + visit(kind.__qualname__, depth + 1)
                    + members,
                )
            else:
                _need(False)
            active.remove(identity)
            _need(len(encoded) <= _MAX_BYTES)
            memo[identity] = item, encoded
            return encoded

    return sha(b"ctcc.native_live.exact_entry_input_tree.v1\n" + visit(value))


def _frame(tag, payload):
    """Typed, length-framed bytes; no per-leaf JSON/hashing or saved model cache."""
    return len(tag).to_bytes(2, "big") + tag + len(payload).to_bytes(8, "big") + payload


def _config_bytes(directory, expected, evidence):
    identity = custody._check_directory(directory)
    _need(set(directory.names()) == set(custody._LIMITS))
    files = {
        name: custody._read(directory, name, body_limit=limit)
        for name, limit in custody._LIMITS.items()
    }
    _need(sha(files["custody.json"]) == expected)
    receipt = decode(files["custody.json"], maximum=2048)
    _need(
        receipt["files_sha256"]
        == {name: sha(files[name]) for name in custody._DATA_FILES}
    )
    evidence.append(
        ["config", identity, [[name, sha(files[name])] for name in sorted(files)]]
    )
    _need(
        custody._check_directory(directory) == identity
        and set(directory.names()) == set(files)
    )


def _attempt_bytes(base, *, module, attempt_id, digest, pages, evidence, budget):
    _need(type(attempt_id) is str and _ID.fullmatch(attempt_id) is not None)
    with base.child(attempt_id) as directory:
        identities = custody._check_directory(base), custody._check_directory(directory)
        raw = custody._read(directory, "custody.json", body_limit=65536)
        _need(sha(raw) == digest)
        receipt = decode(raw, maximum=65536)
        count = receipt["page_count"]
        _need(
            type(count) is int
            and 1 <= count <= module._PAGE_CAP
            and len(pages) == count
        )
        _need(
            receipt["schema_version"] == module.SCHEMA
            and receipt["attempt_id"] == attempt_id
        )
        _need(receipt["directory_identities"] == [list(item) for item in identities])
        expected = current._inventory(count) | {"custody.json"}
        _need(set(directory.names()) == expected)
        plan = custody._read(directory, "observation.json", body_limit=65536)
        _need(sha(plan) == receipt["plan_sha256"])
        evidence.append(["attempt", attempt_id, identities, sha(raw), sha(plan)])
        for name, expected_config in (
            (module._CONFIG_BEFORE, receipt["config_before_custody_sha256"]),
            (module._CONFIG_AFTER, receipt["config_after_custody_sha256"]),
        ):
            with directory.child(name) as config:
                _config_bytes(config, expected_config, evidence)
        for index, page in enumerate(pages):
            _need(page.custody_sha256 == digest)
            with directory.child(current._page_name(index)) as part:
                identity = custody._check_directory(part)
                _need(set(part.names()) == set(current._PAGE_FILES))
                body = custody._read(part, "body.raw", body_limit=8 * 1024 * 1024)
                headers = custody._read(
                    part, "headers.raw", body_limit=custody._LIMITS["headers.raw"]
                )
                metadata = custody._read(part, "observation.json", body_limit=8192)
                budget[0] += len(body) + len(headers) + len(metadata)
                _need(budget[0] <= _MAX_BYTES)
                _need(
                    body == page.raw_body
                    and headers == custody._encode_headers(page.raw_headers)
                    and sha(metadata) == page.page_sha256
                )
                evidence.append(
                    [
                        "page",
                        attempt_id,
                        index,
                        identity,
                        sha(body),
                        sha(headers),
                        sha(metadata),
                    ]
                )
                _need(
                    custody._check_directory(part) == identity
                    and set(part.names()) == set(current._PAGE_FILES)
                )
        _need(
            custody._check_directory(directory) == identities[1]
            and set(directory.names()) == expected
        )
        _need(
            custody._read(directory, "custody.json", body_limit=65536) == raw
            and custody._read(directory, "observation.json", body_limit=65536) == plan
        )


def account_raw_inventory_v1(owner):
    """Read every genuine current/history/baseline attempt, including prefixes."""
    source._retained_source_identity_v1(owner)
    chain, seen = [], set()
    cursor = owner
    while cursor is not None:
        _need(
            type(cursor) is source.OwnedLiveSourceV1
            and cursor not in seen
            and len(chain) < 3
        )
        seen.add(cursor)
        state = source._SOURCES.get(cursor)
        _need(type(state) is dict and state["task"] is asyncio.current_task())
        chain.append(state)
        cursor = state.get("previous_source_owner")
    evidence, budget, visited = [], [0], {}
    for state in reversed(chain):
        tail = decode(state["financial_tail_json"], maximum=16384)
        _need(
            sha(state["financial_tail_json"])
            == decode(state["receipt_json"], maximum=65536)["financial_tail_sha256"]
        )
        attempts = [
            (
                current,
                state["current_attempt"],
                state["current_digest"],
                state["current_pages"],
            ),
            (
                current,
                tail["baseline_attempt"],
                tail["baseline_custody_sha256"],
                state["baseline_current_pages"],
            ),
        ]
        for attempt_id, _selector, digest, _receipt in state["history"]:
            pages = tuple(
                page for page in state["history_pages"] if page.custody_sha256 == digest
            )
            attempts.append((history, attempt_id, digest, pages))
        with current._root_context(state["root"]) as base:
            root_identity = custody._check_directory(base)
            names = tuple(sorted(base.names()))
            _need(
                len(names) == len(set(names))
                and all(
                    type(name) is str and _ID.fullmatch(name) is not None
                    for name in names
                )
            )
            evidence.append(["root", root_identity, names])
            for module, attempt_id, digest, pages in attempts:
                identity = str(state["root"]), attempt_id
                fingerprint = (
                    module.SCHEMA,
                    digest,
                    tuple(page.page_sha256 for page in pages),
                )
                if identity in visited:
                    _need(visited[identity] == fingerprint)
                    continue
                visited[identity] = fingerprint
                _attempt_bytes(
                    base,
                    module=module,
                    attempt_id=attempt_id,
                    digest=digest,
                    pages=pages,
                    evidence=evidence,
                    budget=budget,
                )
            _need(
                custody._check_directory(base) == root_identity
                and tuple(sorted(base.names())) == names
            )
    return sha(canonical(evidence))
