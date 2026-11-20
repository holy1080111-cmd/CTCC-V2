"""Exact input/byte observations; a digest or DTO never owns an entry permit.

The coordinator's private same-invocation pending proof is the only consumer.
Every invocation of these readers reopens the original custody. The temporary
Merkle memo visits shared objects once in ONE synchronous traversal only; no
model, source observation or authorization is retained across guard calls.
"""

import asyncio
import re

from app.domain.source_primitives import canonical, decode, sha
from app.exchange.okx import live_account_config_custody_v1 as custody
from app.exchange.okx import live_account_current_chain_v1 as current
from app.exchange.okx import live_account_history_chain_v1 as history
from app.okx_live import entry_input_dag_v2 as dag
from app.okx_live import trusted_source_bridge_v1 as source

_ID = re.compile(r"[a-f0-9]{32}\Z", re.ASCII)
_MAX_BYTES = 256 * 1024 * 1024


def _need(value):
    if value is not True:
        raise ValueError("native_live_entry_original_inputs_changed") from None


def exact_input_digest_v1(value, *, invocation):
    """Every call observes a fresh exact DAG; no saved hash grants permission."""
    return dag.exact_input_dag_digest_v2(value, invocation=invocation)


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
