"""Pure codec for a descriptor-bound Paper WAL handoff journal.

The controller holds the Windows and native locks, measures the pending Open,
authenticates the native observations, and exclusively publishes and reads back
the actual journal files. This module never selects, swaps or opens a ledger.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

INTENT_SCHEMA = "ctcc.paper58.operator.wal_handoff.intent.v1"
TERMINAL_SCHEMA = "ctcc.paper58.operator.wal_handoff.completed.v1"
OBSERVATION_SCHEMA = "ctcc.paper58.operator.wal_handoff.observation.v1"
READBACK_SCHEMA = "ctcc.paper58.operator.wal_handoff.readback.v1"
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
COMPLETED = "COMPLETED"

SOURCE_COMMIT = "d847b9976048c5eca8d2a0e803b0c29c5fcbef2e"
SOURCE_TREE = "cac612139fbdc2bee6784e5058f40720ad2e6540"
MANIFEST_SHA256 = "9a146fa114fbbab45b6d764e56da4df70cfa98521bf76e4aeb9775e1595abeda"
PACKAGE_IDENTITY_SHA256 = (
    "bca1ee206f4caa252c39605096980b4b1ef48b7d95c23534c2e85412875ebbf0"
)

_ID = re.compile(r"[a-f0-9]{32}\Z", re.ASCII)
_SHA = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_MAX_RECORD = 16384
_MAX_U64 = (1 << 64) - 1


class WalHandoffRefused(ValueError):
    """The supplied records do not establish the expected handoff lineage."""


@dataclass(frozen=True, slots=True)
class WalHandoffAnchor:
    sandbox: str
    counter: int
    pending_open_sha256: str
    previous_close_sha256: str
    descriptor_sha256: str
    source_commit: str
    source_tree: str
    manifest_sha256: str
    package_identity_sha256: str
    tls_generation_id: str | None
    tls_profile_sha256: str
    tls_ca_sha256: str
    system_sha256: str


@dataclass(frozen=True, slots=True)
class WalHandoffTarget:
    origin_sha256: str
    candidate_sha256: str
    origin_dev: int
    origin_ino: int
    candidate_dev: int
    candidate_ino: int
    qualification_sha256: str


@dataclass(frozen=True, slots=True)
class WalHandoffState:
    anchor: WalHandoffAnchor
    target: WalHandoffTarget
    operation_id: str
    intent_sha256: str
    terminal_sha256: str | None
    phase: str
    execution_authority: bool = field(default=False, init=False)


def _need(condition):
    if not condition:
        raise WalHandoffRefused("paper_wal_handoff_journal_refused")


def _is_id(value):
    return type(value) is str and _ID.fullmatch(value) is not None


def _is_sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(document):
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _unique(pairs):
    document = {}
    for key, value in pairs:
        _need(key not in document)
        document[key] = value
    return document


def _decode(raw):
    _need(type(raw) is bytes and 0 < len(raw) <= _MAX_RECORD)
    try:
        document = json.loads(raw, object_pairs_hook=_unique)
        _need(type(document) is dict and _canonical(document) == raw)
    except (ValueError, RecursionError):
        raise WalHandoffRefused("paper_wal_handoff_journal_encoding_refused") from None
    return document


def _exact(document, expected):
    _need(type(document) is dict and set(document) == set(expected))
    _need(
        all(
            type(document[key]) is type(value) and document[key] == value
            for key, value in expected.items()
        )
    )


def _validate_anchor(anchor):
    _need(type(anchor) is WalHandoffAnchor)
    _need(
        _is_id(anchor.sandbox)
        and type(anchor.counter) is int
        and 2 <= anchor.counter <= 10000
    )
    _need(
        (
            anchor.source_commit,
            anchor.source_tree,
            anchor.manifest_sha256,
            anchor.package_identity_sha256,
        )
        == (SOURCE_COMMIT, SOURCE_TREE, MANIFEST_SHA256, PACKAGE_IDENTITY_SHA256)
    )
    _need(anchor.tls_generation_id is None or _is_id(anchor.tls_generation_id))
    _need(
        all(
            _is_sha(digest)
            for digest in (
                anchor.pending_open_sha256,
                anchor.previous_close_sha256,
                anchor.descriptor_sha256,
                anchor.tls_profile_sha256,
                anchor.tls_ca_sha256,
                anchor.system_sha256,
            )
        )
    )


def _validate_target(target):
    _need(type(target) is WalHandoffTarget)
    _need(
        all(
            _is_sha(digest)
            for digest in (
                target.origin_sha256,
                target.candidate_sha256,
                target.qualification_sha256,
            )
        )
    )
    _need(target.origin_sha256 != target.candidate_sha256)
    for dev, ino in (
        (target.origin_dev, target.origin_ino),
        (target.candidate_dev, target.candidate_ino),
    ):
        _need(type(dev) is int and 0 <= dev <= _MAX_U64)
        _need(type(ino) is int and 1 <= ino <= _MAX_U64)
    _need(
        (target.origin_dev, target.origin_ino)
        != (target.candidate_dev, target.candidate_ino)
    )


def make_handoff_intent(
    anchor: WalHandoffAnchor, target: WalHandoffTarget, *, operation_id: str
) -> bytes:
    """Encode the immutable intent before any native handoff can be attempted."""
    _validate_anchor(anchor)
    _validate_target(target)
    _need(_is_id(operation_id))
    return _canonical(
        {
            "schema": INTENT_SCHEMA,
            "anchor": asdict(anchor),
            "target": asdict(target),
            "operation_id": operation_id,
            "automatic_retry": False,
            "execution_authority": False,
        }
    )


def _readback(state, frame, *, challenge, publication_sha256, index):
    _need(type(frame) is dict and _is_sha(frame.get("receipt_sha256")))
    anchor, target = state.anchor, state.target
    _exact(
        frame,
        {
            "schema": READBACK_SCHEMA,
            "operation_id": state.operation_id,
            "intent_sha256": state.intent_sha256,
            "challenge": challenge,
            "probe_index": index,
            "receipt_sha256": frame["receipt_sha256"],
            "publication_sha256": publication_sha256,
            "sandbox": anchor.sandbox,
            "system_sha256": anchor.system_sha256,
            "active_data_sha256": target.candidate_sha256,
            "active_dev": target.candidate_dev,
            "active_ino": target.candidate_ino,
            "preserved_origin_sha256": target.origin_sha256,
            "preserved_origin_dev": target.origin_dev,
            "preserved_origin_ino": target.origin_ino,
            "cluster_state": "shut down",
            "execution_authority": False,
        },
    )
    content = {key: value for key, value in frame.items() if key != "receipt_sha256"}
    _need(_sha(_canonical(content)) == frame["receipt_sha256"])
    return frame["receipt_sha256"]


def _terminal_document(state, challenge, observation):
    _need(_is_sha(challenge) and type(observation) is dict)
    publication = observation.get("publication_sha256")
    _need(_is_sha(publication))
    first, second = observation.get("first"), observation.get("second")
    _exact(
        observation,
        {
            "schema": OBSERVATION_SCHEMA,
            "operation_id": state.operation_id,
            "intent_sha256": state.intent_sha256,
            "challenge": challenge,
            "publication_sha256": publication,
            "publication_synced": True,
            "first": first,
            "second": second,
            "execution_authority": False,
        },
    )
    first_sha = _readback(
        state, first, challenge=challenge, publication_sha256=publication, index=1
    )
    second_sha = _readback(
        state, second, challenge=challenge, publication_sha256=publication, index=2
    )
    _need(first_sha != second_sha)
    return {
        "schema": TERMINAL_SCHEMA,
        "status": COMPLETED,
        "operation_id": state.operation_id,
        "intent_sha256": state.intent_sha256,
        "observation": observation,
        "execution_authority": False,
    }


def inspect_handoff(
    intent_raw: bytes,
    terminal_raw: bytes | None,
    *,
    anchor: WalHandoffAnchor,
) -> WalHandoffState:
    """Replay stored evidence. An absent terminal always requires reconciliation."""
    intent = _decode(intent_raw)
    operation_id = intent.get("operation_id")
    try:
        target = WalHandoffTarget(**intent["target"])
    except (KeyError, TypeError):
        raise WalHandoffRefused("paper_wal_handoff_target_refused") from None
    _need(make_handoff_intent(anchor, target, operation_id=operation_id) == intent_raw)
    state = WalHandoffState(
        anchor, target, operation_id, _sha(intent_raw), None, RECONCILIATION_REQUIRED
    )
    if terminal_raw is None:
        return state

    terminal = _decode(terminal_raw)
    observation = terminal.get("observation")
    _need(type(observation) is dict)
    _exact(
        terminal, _terminal_document(state, observation.get("challenge"), observation)
    )
    return WalHandoffState(
        anchor, target, operation_id, state.intent_sha256, _sha(terminal_raw), COMPLETED
    )


def make_handoff_terminal(
    state: WalHandoffState, *, challenge: str, observation: dict
) -> bytes:
    """Encode completion after a fresh, synced publication and two held readbacks."""
    _need(type(state) is WalHandoffState and type(observation) is dict)
    _need(state.phase == RECONCILIATION_REQUIRED and state.terminal_sha256 is None)
    intent = make_handoff_intent(
        state.anchor, state.target, operation_id=state.operation_id
    )
    _need(_sha(intent) == state.intent_sha256)
    try:
        captured = _decode(_canonical(observation))
    except (TypeError, ValueError, RecursionError):
        raise WalHandoffRefused("paper_wal_handoff_observation_refused") from None
    return _canonical(_terminal_document(state, challenge, captured))
