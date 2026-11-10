"""Owned fresh native observations after new G12 publication, not qualification.

The original replay inputs remain unauthenticated. This private acquisition
owner proves newly measured source custody and chronology only. It cannot mint
G6, complete portfolio risk, candidate qualification, reservation or orders.
The inner initial V4 schemas are retained verbatim, never relabelled as an
original frame or as qualified post-G12 evidence.
"""

from __future__ import annotations

import asyncio
import builtins as _source_builtins
import os
import sys
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from struct import pack as _pack
from threading import get_ident
from types import CodeType, FunctionType
from typing import Literal
from weakref import WeakKeyDictionary, finalize, ref

from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain import source_primitives as primitives
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.exchange.okx.private_rest import (
    DemoPinnedReadSession,
    OkxDemoPrivateRestClient,
)
from app.public_market_source import public_clock
from app.public_market_source import public_runtime_custody_v4 as public_custody
from app.trade_evidence import gates
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_v4 as account
from app.trade_qualification import demo_market_source_v4 as market
from app.trade_qualification import g12_invocation_custody as publication
from app.trade_qualification import owned_current_original_frame_v1 as original_frame
from app.trade_qualification import public_source_runtime as runtime
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.recheck_models import freeze_recheck_origin

_HOOK_KEY = object()
_MISSING = object()
_OWNERS = WeakKeyDictionary()
SCHEMA = "ctcc.postpublication_native_source_links.v1"
_MAX_LINK = 32768


class PostpublicationSourceError(ValueError):
    """Fixed codes only; no raw source, credentials, paths or DB exceptions."""


class _State(dict):
    __slots__ = ("__weakref__",)


def _deny(code="postpublication_sources_unavailable"):
    raise PostpublicationSourceError(code)


class OwnedPostpublicationSourcesV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny()

    def __copy__(self):
        _deny()

    def __deepcopy__(self, memo):
        _deny()

    def __reduce_ex__(self, protocol):
        _deny()

    def close(self):
        state = (
            _OWNERS.pop(self, None)
            if type(self) is OwnedPostpublicationSourcesV1
            else None
        )
        if state is not None:
            state["cleanup"]()


@dataclass(frozen=True, slots=True, repr=False)
class PostpublicationSourceOperandsV1:
    """One-use measured operands, never a source issuer or an execution permit."""

    original_origin: object
    public_packet: object
    public_context: object
    account_replay: object
    receipt_json: bytes
    publication_completed_at: object
    barrier: object
    expires_at: object
    native_sources_verified: Literal[True] = field(default=True, init=False)
    raw_custody_readback_verified: Literal[True] = field(default=True, init=False)
    original_qualification: Literal["UNKNOWN"] = field(default="UNKNOWN", init=False)
    registration_site: Literal["UNKNOWN"] = field(default="UNKNOWN", init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    g6_evaluated: Literal[False] = field(default=False, init=False)
    candidate_qualified: Literal[False] = field(default=False, init=False)
    execution_recheck_complete: Literal[False] = field(default=False, init=False)
    post_g12_eligible: Literal[False] = field(default=False, init=False)
    atomic_risk_reserved: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


def _release(state):
    state["cleanup"]()


def _bind_cleanup(state):
    # Only in-memory owner registries are revoked. No exchange/file/DB callback,
    # unlink or journal mutation runs on failure or weak owner abandonment.
    public_registry, account_registry, routes = (
        market._CURRENT,
        account._CURRENT,
        market.operational._OWNED_ROUTES,
    )
    state_ref = ref(state)

    def cleanup():
        state = state_ref()
        if state is None:
            return
        state["closed"] = True
        pending = state.pop("pending_public_owner", None)
        pending_state = (
            public_registry.pop(pending, None) if pending is not None else None
        )
        held = state.pop("market_state", None)
        for item in (pending_state, held):
            if item is not None:
                routes.pop(item["v4_route_claim"], None)
        pending = state.pop("pending_account_owner", None)
        if pending is not None:
            account_registry.pop(pending, None)
        state.pop("account_state", None)
        for name in (
            "public_session",
            "account_session",
            "read_session",
            "private_client",
            "terminal_tokens",
        ):
            state.pop(name, None)

    return cleanup


class _Attempt(Coroutine):
    """No first-send/cancel/throw/discard path can leave a reusable attempt."""

    def __init__(self, state):
        self._state = state
        self._body = _bounded_run(state)

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def _retire(self):
        state, self._state = self._state, None
        if state is not None and not state.get("issued", False):
            state["cleanup"]()

    def send(self, value):
        try:
            return self._body.send(value)
        except BaseException:
            self._retire()
            raise

    def throw(self, *args):
        try:
            return self._body.throw(*args)
        except BaseException:
            self._retire()
            raise

    def close(self):
        try:
            self._body.close()
        finally:
            self._retire()


def _after(barrier, stamp):
    validate_stamps((barrier, stamp))
    if any(stamp[name] <= barrier[name] for name in ("utc_ns", "monotonic_ns")):
        _deny("postpublication_request_not_after_barrier")


def _context(state):
    task = asyncio.current_task()
    if (
        task is None
        or task.cancelling()
        or state.get("closed", False)
        or task is not state["task_ref"]()
        or asyncio.get_running_loop() is not state["loop_ref"]()
        or os.getpid() != state["pid"]
        or get_ident() != state["thread"]
    ):
        _deny()
    state["read_session"].verify_current()
    if state["read_session"]._owner is not state["private_client"]:
        _deny()
    if (
        original_frame._database_pins(state["factory"], state["custody_factory"])
        != state["database_pins"]
    ):
        _deny()
    custody.RuntimeBundleCustodyRepository._check_factory(state["repository"])
    if state["repository"]._factory is not state["custody_factory"]:
        _deny()
    if (
        type(state["publication_repository"])
        is not publication.G12InvocationCustodyRepository
        or state["publication_repository"].session_factory is not state["factory"]
        or capture.plan_sha256(state["plan_template"]) != state["plan_template_sha256"]
        or market.public._policy_digest(
            state["policy"], market.operational.reviewed_demo_public_route("global")
        )
        != state["capture_policy_sha256"]
    ):
        _deny("postpublication_policy_or_factory_changed")


def _lineage(state):
    claim = state.get("claim")
    if claim is not None:
        if (claim.claim_json, claim.claim_sha256) != state["claim_pin"]:
            _deny()
        publication.require_original_unchanged(
            claim, state["original_market"], state["pre"], state["inputs"]
        )
        if claim.values["account_plan_sha256"] != state["plan_template_sha256"]:
            _deny("postpublication_original_lineage_changed")
    evidence = state.get("evidence")
    if evidence is not None:
        origin = freeze_recheck_origin(evidence)
        if origin.model_dump(mode="json", round_trip=True) != state["origin_document"]:
            _deny("postpublication_original_lineage_changed")
    committed = state.get("committed")
    if (
        committed is not None
        and (committed.publication_json, committed.publication_sha256)
        != state["publication_pin"]
    ):
        _deny("postpublication_custody_changed")


def _check(state, *, publication_required=False, _any=any, _zip=zip):
    guard = state["guard"]
    if (
        _pins is not guard
        or guard.__code__ is not state["guard_code"]
        or _any(
            cell.cell_contents is not held
            for cell, held in _zip(
                guard.__closure__ or (), state["guard_cells"], strict=True
            )
        )
    ):
        _deny("postpublication_source_leaf_changed")
    guard()
    _context(state)
    _lineage(state)
    now = native_stamp()
    validate_stamps((state["last"], now))
    deadline = state.get("deadline", state["original_deadline"])
    if utc_from_ns(now["utc_ns"]) >= deadline:
        _deny("postpublication_original_deadline_expired")
    if publication_required:
        _after(state["barrier"], now)
    state["last"] = now
    return now


def _fresh_session(state):
    # This is a NEW measurement plan on the SAME active native read lease. It
    # never extends the original candidate/source expiry or reuses _used=True.
    now = _check(state, publication_required=True)
    document = state["plan_template"].model_dump(mode="python", round_trip=True)
    document.update(
        plan_id="post-" + uuid.uuid4().hex, created_at=utc_from_ns(now["utc_ns"])
    )
    plan = capture.CurrentDemoAccountCapturePlanV7.model_validate(document, strict=True)
    session = ControlledDemoAccountSession.from_pinned_read_session(
        read_session=state["read_session"],
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )
    if (
        session._checked_read_session(session._plan) is not state["read_session"]
        or session._used is not False
    ):
        _deny()
    return session


def _clock_after(barrier, raw):
    result = public_clock.replay_clock_observation(raw)
    if result["outcome"] != "accepted":
        _deny("postpublication_clock_incomplete")
    observation = result["observation"]
    if observation["schema_version"] != "ctcc.windows_clock_observation.v2":
        _deny("postpublication_clock_incomplete")
    _after(barrier, observation["diagnostic"]["host_before"]["request_start"])


def _secret_public_journal(directory, events, tokens):
    """Scan an already replayed captured V4 journal without parsing fragments."""
    if (
        type(directory) is not public_custody.RuntimeBundleDirectoryV4
        or type(tokens) is not tuple
        or len(tokens) < 6
        or any(
            type(token) is not str or not token or not token.isascii()
            for token in tokens
        )
        or type(events) is not tuple
        or not 0 < len(events) <= public_custody.journal.MAX_EVENTS
    ):
        _deny("postpublication_public_secret_journal_incomplete")
    plan_raw = directory.read("plan.json", runtime.MAX_RAW)
    summary_raw = directory.read("summary.json", runtime.MAX_RAW)
    account.runtime._secret_checked(plan_raw, tokens)
    account.runtime._secret_checked(summary_raw, tokens)
    plan, summary = (
        decode(plan_raw, runtime.MAX_RAW),
        decode(summary_raw, runtime.MAX_RAW),
    )
    if (
        type(plan) is not dict
        or type(summary) is not dict
        or canonical(plan) != plan_raw
        or canonical(summary) != summary_raw
        or set(summary)
        != {
            "schema_version",
            "plan_sha256",
            "event_count",
            "head_sha256",
            "disposition",
            "completed",
            "packet_sha256",
            "execution_authority",
            "measured_availability_eligible",
            "original_source_verified",
            "account_complete",
        }
        or summary["schema_version"] != "ctcc.public.runtime_summary.v1"
        or any(
            summary[name] is not False
            for name in (
                "execution_authority",
                "measured_availability_eligible",
                "original_source_verified",
                "account_complete",
            )
        )
        or plan["schema_version"] != "ctcc.public.initial_runtime_plan.v4"
        or summary["disposition"] != "captured"
        or type(summary["event_count"]) is not int
        or summary["event_count"] != len(events)
    ):
        _deny("postpublication_public_secret_journal_incomplete")
    plan_pin = sha(plan_raw)
    previous = plan_pin
    total = len(plan_raw) + len(summary_raw)
    expected = {"plan.json", "summary.json"}
    requests = {}
    json_raw_kinds = {
        "clock_before",
        "clock_after",
        "ws_subscribe",
        "ws_ack",
        "ws_ticker",
        "packet",
    }
    for index, event in enumerate(events):
        if (
            type(event) is not dict
            or set(event)
            != {
                "schema_version",
                "index",
                "kind",
                "plan_sha256",
                "previous_sha256",
                "metadata",
                "raw_sha256",
                "raw_bytes",
            }
            or event["schema_version"] != "ctcc.public.runtime_event.v1"
            or type(event.get("index")) is not int
            or event["index"] != index
            or type(event.get("kind")) is not str
            or event["kind"] not in public_custody.journal._KINDS
            or event["kind"] in {"request_failed", "component_failed"}
            or event.get("plan_sha256") != plan_pin
            or event.get("previous_sha256") != previous
            or type(event.get("metadata")) is not dict
        ):
            _deny("postpublication_public_secret_journal_incomplete")
        name = f"event-{index:04d}.json"
        encoded = directory.read(name, runtime.MAX_RAW)
        if encoded != canonical(event):
            _deny("postpublication_public_secret_journal_incomplete")
        account.runtime._secret_checked(encoded, tokens)
        expected.add(name)
        total += len(encoded)
        raw = None
        if event["raw_bytes"] is not None:
            if (
                type(event["raw_bytes"]) is not int
                or not 0 <= event["raw_bytes"] <= runtime.MAX_RAW
            ):
                _deny("postpublication_public_secret_journal_incomplete")
            name = f"event-{index:04d}.raw"
            raw = directory.read(name, runtime.MAX_RAW)
            if len(raw) != event["raw_bytes"] or sha(raw) != event["raw_sha256"]:
                _deny("postpublication_public_secret_journal_incomplete")
            # Every retained fragment is scanned as bytes. A fragment is not
            # a complete JSON document, even when it happens to parse alone.
            account.runtime.collector._no_secrets(raw, tokens)
            expected.add(name)
            total += len(raw)
        elif event["raw_sha256"] is not None:
            _deny("postpublication_public_secret_journal_incomplete")
        if total > public_custody.journal.MAX_TOTAL:
            _deny("postpublication_public_secret_journal_incomplete")
        kind, meta = event["kind"], event["metadata"]
        if kind == "request":
            if (
                raw is not None
                or set(meta)
                != {
                    "id",
                    "role",
                    "method",
                    "origin",
                    "endpoint",
                    "query",
                    "started",
                    "request_headers",
                }
                or type(meta.get("id")) is not int
                or meta["id"] != len(requests)
                or len(requests) >= 64
            ):
                _deny("postpublication_public_secret_journal_incomplete")
            requests[meta["id"]] = {
                "body": bytearray(),
                "chunks": 0,
                "headers": False,
                "content_length": None,
                "complete": False,
                "closed": False,
            }
        elif kind in {"headers", "chunk", "body_complete", "response_closed"}:
            if type(meta.get("id")) is not int or meta["id"] not in requests:
                _deny("postpublication_public_secret_journal_incomplete")
            request = requests[meta["id"]]
            if request["closed"]:
                _deny("postpublication_public_secret_journal_incomplete")
            if kind == "headers":
                if (
                    request["headers"]
                    or set(meta)
                    != {"id", "received", "status", "tls", "safe_headers", "truncated"}
                    or raw is not None
                    or type(meta.get("status")) is not int
                    or meta["status"] != 200
                    or meta.get("truncated") is not False
                ):
                    _deny("postpublication_public_secret_journal_incomplete")
                headers = meta["safe_headers"]
                if (
                    type(headers) is not list
                    or len(headers) > 16
                    or any(
                        type(pair) is not list
                        or len(pair) != 2
                        or type(pair[0]) is not str
                        or pair[0]
                        not in {
                            "content-type",
                            "content-length",
                            "content-encoding",
                            "date",
                        }
                        or type(pair[1]) is not str
                        or len(pair[1]) > 1024
                        for pair in headers
                    )
                    or len(dict(headers)) != len(headers)
                ):
                    _deny("postpublication_public_secret_journal_incomplete")
                fields = dict(headers)
                length = fields.get("content-length")
                if length is not None:
                    if not length.isascii() or not length.isdigit() or len(length) > 10:
                        _deny("postpublication_public_secret_journal_incomplete")
                    request["content_length"] = int(length)
                request["headers"] = True
            elif kind == "chunk":
                if (
                    not request["headers"]
                    or set(meta)
                    != {
                        "id",
                        "received",
                        "observed_bytes",
                        "observed_sha256",
                        "truncated",
                    }
                    or request["complete"]
                    or raw is None
                    or type(meta.get("observed_bytes")) is not int
                    or meta["observed_bytes"] != len(raw)
                    or meta.get("observed_sha256") != sha(raw)
                    or meta.get("truncated") is not False
                    or len(request["body"]) + len(raw) > runtime.MAX_RAW
                ):
                    _deny("postpublication_public_secret_journal_incomplete")
                request["body"].extend(raw)
                request["chunks"] += 1
            elif kind == "body_complete":
                if (
                    not request["headers"]
                    or set(meta) != {"id", "completed", "body_bytes", "body_sha256"}
                    or request["complete"]
                    or not request["chunks"]
                    or raw is not None
                    or type(meta.get("body_bytes")) is not int
                    or meta["body_bytes"] != len(request["body"])
                    or meta.get("body_sha256") != sha(bytes(request["body"]))
                    or (
                        request["content_length"] is not None
                        and request["content_length"] != len(request["body"])
                    )
                ):
                    _deny("postpublication_public_secret_journal_incomplete")
                # The original event order within this exact request ID is the
                # only assembly order. Both byte and decoded-JSON union scans
                # run on the complete body; no sorting, repair or substitution.
                account.runtime._secret_checked(bytes(request["body"]), tokens)
                request["complete"] = True
            else:
                if (
                    not request["complete"]
                    or set(meta) != {"id", "completed"}
                    or raw is not None
                ):
                    _deny("postpublication_public_secret_journal_incomplete")
                request["closed"] = True
        elif kind in json_raw_kinds:
            if raw is None:
                _deny("postpublication_public_secret_journal_incomplete")
            account.runtime._secret_checked(raw, tokens)
        elif raw is not None:
            _deny("postpublication_public_secret_journal_incomplete")
        previous = sha(encoded)
    names = directory.names()
    if (
        type(names) is not tuple
        or len(names) != len(expected)
        or len(names) != len(set(names))
        or set(names) != expected
        or summary["plan_sha256"] != plan_pin
        or summary["head_sha256"] != previous
        or events[-1]["kind"] != "packet"
        or summary["packet_sha256"] != events[-1]["raw_sha256"]
        or not requests
        or any(not request["closed"] for request in requests.values())
    ):
        _deny("postpublication_public_secret_journal_incomplete")


def _secret_complete(state, directory, document, recorded, events):
    """Only closed, fully replayed native schema fields are decoded/scanned."""
    account_tokens = state["account_state"]["private_secret_tokens"]
    route = market.operational._owned_route_state_v4(
        state["market_state"]["v4_route_claim"],
        invocation=state["market_state"]["invocation"],
        owner_task=asyncio.current_task(),
    )
    public_tokens = route.get("secret_tokens")
    if (
        type(account_tokens) is not tuple
        or len(account_tokens) < 5
        or type(public_tokens) is not tuple
        or len(public_tokens) < 4
        or account_tokens[:3] != state["read_session"]._credentials
        or public_tokens[:3] != state["read_session"]._credentials
        or account_tokens[3] != state["read_session"].binding_id
        or any(
            type(token) is not str or not token or not token.isascii()
            for token in (*account_tokens, *public_tokens)
        )
    ):
        _deny("postpublication_terminal_secret_custody_unknown")
    tokens = (*account_tokens, *public_tokens[3:])
    # Raw bytes remain under their original DB0046 custody. No arbitrary JSON
    # string is unhexed, no value is repaired and no retained bytes are edited.
    _secret_public_journal(directory, events, tokens)
    account.runtime._secret_checked(route["raw"], tokens)
    account.runtime._secret_checked(state["account_state"]["bundle_raw"], tokens)
    account.runtime._secret_checked(canonical(document["route_proof"]), tokens)
    # Full V4 replay above has already checked the exact CLOSED route schema,
    # 8192-byte config bound and hash. This earlier preflight is a different
    # GET from chain config_before; scan its original plaintext separately.
    account.runtime._secret_checked(
        account._unhex(document["route_proof"]["config_raw_hex"], 8192), tokens
    )
    chain = account._chain_replay(document["chain"])
    for point in chain:
        event = account.journal.checked_event(point.event)
        account.runtime._secret_checked(
            point.event.event_json, (*tokens[:3], *tokens[4:])
        )
        if event["kind"] == "capture_start":
            if event["data"]["plan"]["session_binding_id"] != tokens[3]:
                _deny("postpublication_secret_label_binding_changed")
            # Fresh parsed scanner view, identical to the existing owned-packet
            # label exception. Original durable event bytes remain unchanged.
            event["data"]["plan"]["session_binding_id"] = ""
        account.runtime._secret_checked(canonical(event), (tokens[3],))
        if point.event.raw_body is not None:
            account.runtime._secret_checked(point.event.raw_body, tokens)
        if point.event.packet_payload is not None:
            account.journal._check_owned_packet_for_storage(
                point.event.packet_payload, tokens, recorded.packet.plan
            )
    for name, maximum in (
        ("native_proof_hex", account.proof.MAX_PROOF),
        ("native_readback_hex", account.storage.MAX_PART),
    ):
        account.runtime._secret_checked(account._unhex(document[name], maximum), tokens)
    for name in account.proof.FILES:
        account.runtime._secret_checked(
            account._unhex(document["clock_files"][name], account.storage.MAX_PART),
            tokens,
        )
    state["terminal_tokens"] = tokens


def _replay_sources(state):
    _check(state, publication_required=True)
    public_state, account_state = state["market_state"], state["account_state"]
    packet, context, _ = market._replay_postpublication_sources_v1(
        public_state, key=_HOOK_KEY
    )
    replay, _ = account._replay_postpublication_sources_v1(account_state, key=_HOOK_KEY)
    public_raw, account_raw = public_state["bundle_raw"], account_state["bundle_raw"]
    if (sha(public_raw), sha(account_raw)) != state["source_pins"]:
        _deny("postpublication_source_changed")
    directory, public_proof, _, events = market.replay_runtime_bundle_v4(
        public_raw, expected_sha256=state["source_pins"][0]
    )
    public_plan = decode(directory.read("plan.json", runtime.MAX_RAW), runtime.MAX_RAW)
    _after(state["barrier"], public_proof["request_start"])
    _after(state["barrier"], public_plan["invocation_started"])
    for event in events:
        if event["kind"] == "request" or event["kind"] == "ws_connect":
            _after(state["barrier"], event["metadata"]["started"])
        elif event["kind"] in {"clock_before", "clock_after"}:
            _clock_after(
                state["barrier"],
                directory.read(f"event-{event['index']:04d}.raw", runtime.MAX_RAW),
            )
    account_document = decode(account_raw, custody.MAX_BUNDLE_BYTES)
    _after(state["barrier"], account_document["route_proof"]["request_start"])
    native = decode(
        account._unhex(account_document["native_proof_hex"], account.proof.MAX_PROOF),
        account.proof.MAX_PROOF,
    )
    _after(state["barrier"], native["stage"]["started"])
    for witness in native["witnesses"]:
        if witness["phase"] in {"request_start", "request_dispatch"}:
            _after(state["barrier"], witness["stamp"])
    for name in ("host-before.json", "host-after.json"):
        _clock_after(
            state["barrier"],
            account._unhex(
                account_document["clock_files"][name], account.storage.MAX_PART
            ),
        )
    _secret_complete(state, directory, account_document, replay, events)
    if (
        public_proof["account_identity"]
        != account_document["route_proof"]["account_identity"]
        or public_proof["account_identity"]["uid"]
        != state["plan_template"].expected_uid
        or public_proof["rest_origin"] != state["plan_template"].origin
        or context.quote.instrument_id != state["instrument_id"]
        or account_state["read_session"] is not state["read_session"]
        or public_proof["account_plan_sha256"] != state["public_session"]._pin
        or account_state["session"] is not state["account_session"]
        or any(
            session._checked_read_session(session._plan) is not state["read_session"]
            for session in (state["public_session"], state["account_session"])
        )
    ):
        _deny("postpublication_identity_changed")
    deadline = min(state["deadline"], public_state["expires_at"], replay.expires_at)
    if deadline > state["original_deadline"]:
        _deny()
    state["deadline"] = deadline
    now = _check(state, publication_required=True)
    market.public_market_context_v2(
        packet,
        expected_bundle_sha256=packet.bundle_sha256,
        evaluated_at=utc_from_ns(now["utc_ns"]),
    )
    account._check_current(replay, now)
    return packet, context, replay


def _links(state):
    return canonical(
        {
            "schema_version": SCHEMA,
            "claim_sha256": state["claim"].claim_sha256,
            "publication_sha256": state["committed"].publication_sha256,
            "original_origin_sha256": sha(canonical(state["origin_document"])),
            "original_qualification": "UNKNOWN",
            "fixed_original_pins": {
                name: state["claim"].values[name]
                for name in (
                    "candidate_sha256",
                    "event_key",
                    "entry",
                    "stop_loss",
                    "take_profit",
                    "deadline",
                    "original_run_sha256",
                    "original_inputs_sha256",
                )
            },
            "original_policy_sha256": state["origin"].original_policy_sha256,
            "publication_completed_at": state[
                "origin"
            ].publication_completed_at.isoformat(),
            "post_readback_barrier": state["barrier"],
            "original_deadline": state["original_deadline"].isoformat(),
            "expires_at": state["deadline"].isoformat(),
            "public_bundle_sha256": state["source_pins"][0],
            "public_custody_id": str(state["market_state"]["custody_id"]),
            "account_bundle_sha256": state["source_pins"][1],
            "account_custody_id": str(state["account_state"]["custody_id"]),
            "inner_public_schema": "ctcc.public_current_raw_bundle.v4",
            "inner_account_schema": "ctcc.demo_current_account_raw_bundle.v4",
            "inner_stage_semantics": "fresh_initial_observations_after_owned_publication",
            "registration_site": "UNKNOWN",
            "account_complete": False,
            "candidate_qualified": False,
            "post_g12_eligible": False,
            "atomic_risk_reserved": False,
            "execution_authority": False,
        }
    )


async def _run(state, _checked=_check, _any=any, _zip=zip):
    if state.get("started", False):
        _deny("postpublication_attempt_spent")
    # Burn the private attempt before any fallible first-send check or await.
    state["started"] = True
    guard = state["guard"]
    guard_code, checker_code = state["guard_code"], state["checker_code"]
    guard_cells = state["guard_cells"]

    def checked(**kwargs):
        # Inspect captured originals BEFORE invoking them after every await.
        if (
            _pins is not guard
            or guard.__code__ is not guard_code
            or _check is not _checked
            or _checked.__code__ is not checker_code
            or _any(
                cell.cell_contents is not held
                for cell, held in _zip(
                    guard.__closure__ or (), guard_cells, strict=True
                )
            )
        ):
            raise PostpublicationSourceError("postpublication_source_leaf_changed")
        guard()
        return _checked(state, **kwargs)

    issued = False
    try:
        checked()
        claim = publication.prepare_g12_claim(
            state["roots"][0],
            state["original_market"],
            state["pre"],
            state["inputs"],
            account_uid=state["plan_template"].expected_uid,
            account_plan_sha256=capture.plan_sha256(state["plan_template"]),
        )
        state["claim"] = claim
        state["claim_pin"] = (claim.claim_json, claim.claim_sha256)
        committed_claim = await publication.G12InvocationCustodyRepository.claim(
            state["publication_repository"], claim
        )
        checked()
        if type(committed_claim) is not publication.G12InvocationClaim:
            _deny("postpublication_custody_changed")
        state["claim"] = committed_claim
        checked()
        publication.require_prepublication_root(state["claim"], state["roots"][0])

        def clock():
            return utc_from_ns(checked()["utc_ns"])

        evidence = gates.publish_qualification_evidence_liquidity_v2(
            state["roots"][0],
            state["original_market"],
            run=state["pre"],
            **state["inputs"],
            purpose="observed",
            clock=clock,
        )
        origin = freeze_recheck_origin(evidence)
        state.update(
            evidence=evidence,
            origin=origin,
            origin_document=origin.model_dump(mode="json", round_trip=True),
        )
        if origin.deadline != state["original_deadline"]:
            _deny("postpublication_original_deadline_changed")
        pending = publication.prepare_g12_publication(
            state["claim"], evidence, state["roots"][0]
        )
        committed_publication = (
            await publication.G12InvocationCustodyRepository.publication(
                state["publication_repository"], state["claim"], pending
            )
        )
        checked()
        if type(committed_publication) is not publication.G12InvocationPublication:
            _deny("postpublication_custody_changed")
        state["committed"] = committed_publication
        state["publication_pin"] = (
            committed_publication.publication_json,
            committed_publication.publication_sha256,
        )
        publication.require_committed_publication_files(
            state["claim"], state["committed"], evidence, state["roots"][0]
        )
        publication.require_replayed_publication_computation(
            state["claim"],
            state["committed"],
            evidence,
            state["roots"][0],
            state["original_market"],
            state["pre"],
            state["inputs"],
        )
        barrier = checked()
        if utc_from_ns(barrier["utc_ns"]) <= origin.publication_completed_at:
            _deny("postpublication_barrier_invalid")
        state["barrier"] = barrier
        state["deadline"] = origin.deadline
        public_session = _fresh_session(state)
        state["public_session"] = public_session
        public_owner = await market.acquire_initial_demo_market_source_v4(
            state["roots"][1],
            account_proof_root=state["roots"][2],
            instrument_id=state["instrument_id"],
            market_policy=state["policy"],
            account_session=public_session,
            private_client=state["private_client"],
            custody_repository=state["repository"],
        )
        state["pending_public_owner"] = public_owner
        checked(publication_required=True)
        state["market_state"] = market._take_postpublication_sources_v1(
            public_owner, key=_HOOK_KEY
        )
        state.pop("pending_public_owner")
        checked(publication_required=True)
        account_session = _fresh_session(state)
        state["account_session"] = account_session
        account_owner = await account.acquire_initial_demo_account_source_v4(
            account_session=account_session,
            private_client=state["private_client"],
            session_factory=state["factory"],
            private_proof_root=state["roots"][3],
            native_proof_root=state["roots"][4],
            custody_repository=state["repository"],
        )
        state["pending_account_owner"] = account_owner
        checked(publication_required=True)
        state["account_state"] = account._take_postpublication_sources_v1(
            account_owner, key=_HOOK_KEY
        )
        state.pop("pending_account_owner")
        state["source_pins"] = (
            state["market_state"]["bundle_sha256"],
            state["account_state"]["bundle_sha256"],
        )
        _replay_sources(state)
        raw = _links(state)
        if not 0 < len(raw) <= _MAX_LINK:
            _deny()
        account.runtime._secret_checked(raw, state["terminal_tokens"])
        with runtime._native_recheck_root(state["roots"][5]) as directory:
            if directory.names():
                _deny("postpublication_receipt_root_not_empty")
            directory.publish("postpublication-source-links.v1.json", raw)
            if directory.read("postpublication-source-links.v1.json", _MAX_LINK) != raw:
                _deny("postpublication_receipt_readback_changed")
        state["receipt_json"] = raw
        _replay_sources(state)
        if _links(state) != raw:
            _deny("postpublication_links_changed")
        publication.require_committed_publication_files(
            state["claim"], state["committed"], evidence, state["roots"][0]
        )
        checked(publication_required=True)
        result = object.__new__(OwnedPostpublicationSourcesV1)
        _OWNERS[result] = state
        # Finalizer arguments retain the state without retaining the owner.
        # cleanup itself holds only a weak exact state reference (no cycle).
        finalize(result, lambda held: held["cleanup"](), state)
        state["task_ref"]().add_done_callback(
            lambda _task, cleanup=state["cleanup"]: cleanup()
        )
        state["issued"] = issued = True
        return result
    except asyncio.CancelledError:
        raise
    except PostpublicationSourceError:
        raise
    except Exception:  # noqa: BLE001 -- preserve journals, redact private failures
        raise PostpublicationSourceError("postpublication_acquisition_failed") from None
    finally:
        if not issued:
            state["cleanup"]()


async def _bounded_run(state, _checked=_check):
    if _check is not _checked or _checked.__code__ is not state["checker_code"]:
        _deny("postpublication_source_leaf_changed")
    now = _checked(state)
    remaining = (
        state["original_deadline"] - utc_from_ns(now["utc_ns"])
    ).total_seconds()
    if remaining <= 0:
        _deny("postpublication_original_deadline_expired")
    # One actual asyncio deadline, same task/client/lease, never a fresh task or
    # policy-cap increase. Native cancellation retains accepted evidence.
    async with asyncio.timeout(remaining):
        return await _run(state)


def _open_acquisition(
    *,
    read_session,
    private_client,
    plan_template,
    evidence_root,
    public_root,
    public_account_proof_root,
    account_proof_root,
    account_native_root,
    receipt_root,
    original_market,
    run,
    original_inputs,
    market_policy,
    session_factory,
    custody_session_factory,
    _source_guard,
):
    """Acquire once after actual new physical publication; original trust UNKNOWN.

    There is no saved-receipt/barrier/source adapter. The caller's replay inputs
    never become native-qualified; no network runs before actual fresh readback.
    """
    # Reject a foreign callback by identity BEFORE it can execute. The only
    # production caller is the source-bound private entry factory.
    if _source_guard is not _pins:
        raise PostpublicationSourceError("postpublication_source_leaf_changed")
    _source_guard()
    task = asyncio.current_task()
    if (
        task is None
        or task.cancelling()
        or type(read_session) is not DemoPinnedReadSession
        or type(private_client) is not OkxDemoPrivateRestClient
        or read_session._owner is not private_client
        or type(plan_template) is not capture.CurrentDemoAccountCapturePlanV7
        or type(original_inputs) is not dict
    ):
        _deny()
    read_session.verify_current()
    selected = capture._checked_plan(plan_template, capture.plan_sha256(plan_template))
    run = gates.verify_pre_evidence_versioned(run, original_market, **original_inputs)
    instrument_id = run.prefix.intent.instrument_id
    if (
        selected.session_binding_id != read_session.binding_id
        or selected.origin != read_session._origin
        or selected.registration_region != "global"
        or selected.settlement_currency != "USDT"
        or instrument_id not in selected.leverage_instrument_ids
        or instrument_id not in market.REVIEWED_DEMO_INSTRUMENT_IDS
    ):
        _deny()
    roots = (
        evidence_root,
        public_root,
        public_account_proof_root,
        account_proof_root,
        account_native_root,
        receipt_root,
    )
    if (
        any(type(root) is not type(Path()) or not root.is_absolute() for root in roots)
        or len(set(roots)) != 6
        or any(a in b.parents for a in roots for b in roots if a != b)
    ):
        _deny()
    pins = original_frame._database_pins(session_factory, custody_session_factory)
    repository = custody.RuntimeBundleCustodyRepository(custody_session_factory)
    deadline = min(
        run.prefix.intent.expires_at,
        run.prefix.detection.trigger.expires_at,
        run.result.entry_zone.expires_at,
        run.prefix.timing.latest_valid_entry_time,
    )
    state = _State(
        {
            "task_ref": ref(task),
            "loop_ref": ref(asyncio.get_running_loop()),
            "pid": os.getpid(),
            "thread": get_ident(),
            "read_session": read_session,
            "private_client": private_client,
            "plan_template": selected,
            "roots": roots,
            "instrument_id": instrument_id,
            "original_market": original_market,
            "pre": run,
            "inputs": dict(original_inputs),
            "policy": market.public._policy_copy(market_policy),
            "factory": session_factory,
            "custody_factory": custody_session_factory,
            "repository": repository,
            "publication_repository": publication.G12InvocationCustodyRepository(
                session_factory
            ),
            "database_pins": pins,
            "original_deadline": deadline,
            "last": native_stamp(),
            "guard": _source_guard,
            "guard_code": _source_guard.__code__,
            "checker_code": _check.__code__,
            "guard_cells": tuple(
                cell.cell_contents for cell in (_source_guard.__closure__ or ())
            ),
        }
    )
    state["plan_template_sha256"] = capture.plan_sha256(selected)
    state["capture_policy_sha256"] = market.public._policy_digest(
        state["policy"], market.operational.reviewed_demo_public_route("global")
    )
    _check(state)
    state["cleanup"] = _bind_cleanup(state)
    return _Attempt(state)


def consume_postpublication_source_operands_v1(source, _checked=_check):
    state = (
        _OWNERS.pop(source, None)
        if type(source) is OwnedPostpublicationSourcesV1
        else None
    )
    if state is None:
        _deny()
    try:
        if _check is not _checked or _checked.__code__ is not state["checker_code"]:
            raise PostpublicationSourceError("postpublication_source_leaf_changed")
        _checked(state, publication_required=True)
        packet, context, replay = _replay_sources(state)
        if _links(state) != state["receipt_json"]:
            _deny()
        with runtime._native_recheck_root(state["roots"][5]) as directory:
            if (
                directory.read("postpublication-source-links.v1.json", _MAX_LINK)
                != state["receipt_json"]
            ):
                _deny("postpublication_receipt_readback_changed")
        _check(state, publication_required=True)
        return PostpublicationSourceOperandsV1(
            state["origin"],
            packet,
            context,
            replay,
            state["receipt_json"],
            state["origin"].publication_completed_at,
            dict(state["barrier"]),
            state["deadline"],
        )
    finally:
        state["cleanup"]()


def _build_guard(
    records,
    sources,
    _vars=vars,
    _getattr=getattr,
    _sha=sha,
    _error=PostpublicationSourceError,
    _any=any,
    _zip=zip,
    _tuple=tuple,
    _sorted=sorted,
    _type=type,
    _function_type=FunctionType,
    _classmethod=classmethod,
    _missing=_MISSING,
):
    # Captured immutable records include identity AND code/default/closure
    # objects; replacing a mutable module alias or a callable.__code__ cannot
    # bless itself. This is a finite source fence, not universal memory defense.
    def guard():
        for (
            namespace,
            name,
            value,
            code,
            defaults,
            kwdefaults,
            closure,
            cells,
            kwitems,
            defining_globals,
            defining_builtins,
            builtin_lookups,
        ) in records:
            current = _vars(namespace).get(name, _missing)
            if current is not value:
                raise _error("postpublication_source_leaf_changed")
            function = (
                current.__func__
                if _type(current) is _classmethod
                else current
                if _type(current) is _function_type
                else None
            )
            if (
                _getattr(function, "__code__", None) is not code
                or _getattr(function, "__defaults__", None) is not defaults
                or _getattr(function, "__kwdefaults__", None) is not kwdefaults
                or _getattr(function, "__closure__", None) is not closure
                or _getattr(function, "__globals__", None) is not defining_globals
                or _getattr(function, "__builtins__", None) is not defining_builtins
                or (
                    closure is not None
                    and _any(
                        cell.cell_contents is not held
                        for cell, held in _zip(closure, cells, strict=True)
                    )
                )
                or (
                    kwdefaults is not None
                    and (
                        _tuple(_sorted(kwdefaults))
                        != _tuple(name for name, _ in kwitems)
                        or _any(kwdefaults[name] is not held for name, held in kwitems)
                    )
                )
            ):
                raise _error("postpublication_source_leaf_changed")
            for lookup, original in builtin_lookups:
                # Exact module dictionaries only. Inspect before any leaf call;
                # no mutable globals/type/getattr callback verifies itself.
                if (
                    defining_globals.get(lookup, original) is not original
                    or defining_builtins.get(lookup) is not original
                ):
                    raise _error("postpublication_source_leaf_changed")
        for path, digest in sources:
            try:
                raw = path.read_bytes()
            except OSError:
                raise _error("postpublication_source_file_unavailable") from None
            if _sha(raw) != digest:
                raise _error("postpublication_source_file_changed")

    return guard


def _code_fields(code):
    """Closed code-value fingerprint; no repr, frames, pickle or execution."""
    nodes = 0
    encoded_bytes = 0

    def frame(tag, payload):
        nonlocal encoded_bytes
        encoded_bytes += len(payload)
        if encoded_bytes > 8 * 1024 * 1024:
            raise ValueError("postpublication_code_fingerprint_byte_capacity")
        return tag + len(payload).to_bytes(8, "big") + payload

    def encode(value, depth):
        nonlocal nodes
        nodes += 1
        if depth > 128 or nodes > 100000:
            raise ValueError("postpublication_code_fingerprint_node_capacity")
        kind = type(value)
        if value is None:
            return frame(b"N", b"")
        if value is Ellipsis:
            return frame(b"E", b"")
        if kind is bool:
            return frame(b"B", b"1" if value else b"0")
        if kind is int:
            size = max(1, (value.bit_length() + 7) // 8)
            if size > 1024 * 1024:
                raise ValueError("postpublication_code_integer_capacity")
            return frame(
                b"I", (b"-" if value < 0 else b"+") + abs(value).to_bytes(size, "big")
            )
        if kind is float:
            return frame(b"F", _pack(">d", value))
        if kind is complex:
            return frame(b"J", _pack(">dd", value.real, value.imag))
        if kind is str:
            return frame(b"S", value.encode("utf-8", "surrogatepass"))
        if kind is bytes:
            return frame(b"Y", value)
        if kind is tuple:
            return frame(b"T", b"".join(encode(item, depth + 1) for item in value))
        if kind is frozenset:
            return frame(
                b"R", b"".join(sorted(encode(item, depth + 1) for item in value))
            )
        if kind is CodeType:
            fields = (
                value.co_filename,
                value.co_name,
                value.co_qualname,
                value.co_firstlineno,
                value.co_argcount,
                value.co_posonlyargcount,
                value.co_kwonlyargcount,
                value.co_nlocals,
                value.co_stacksize,
                value.co_flags,
                value.co_code,
                value.co_consts,
                value.co_names,
                value.co_varnames,
                value.co_freevars,
                value.co_cellvars,
                value.co_linetable,
                value.co_exceptiontable,
            )
            return frame(b"C", encode(fields, depth + 1))
        raise ValueError("postpublication_code_constant_type_not_supported")

    if type(code) is not CodeType:
        raise ValueError("postpublication_code_type_changed")
    return encode(code, 0)


def _source_code(value, raw, filename):
    """Compile known pinned source as data; never execute its imports or body."""
    pending = [compile(raw, filename, "exec", dont_inherit=True)]
    found = []
    while pending:
        code = pending.pop()
        if code.co_qualname == value.__code__.co_qualname:
            found.append(code)
        pending.extend(item for item in code.co_consts if type(item) is type(code))
    try:
        matching = len(found) == 1 and _code_fields(found[0]) == _code_fields(
            value.__code__
        )
    except ValueError:
        _deny("postpublication_source_code_unbound")
    if not matching:
        _deny("postpublication_source_code_unbound")
    # A matching code object alone cannot bless a preexisting FunctionType
    # clone. Resolve its defining module from the pinned SOURCE PATH, never
    # value.__module__ or an observed foreign function namespace.
    path = Path(filename).resolve()
    relative = path.relative_to(_ROOT).as_posix()
    defining_globals = sys.modules[relative[:-3].replace("/", ".")].__dict__
    defining_builtins = _source_builtins.__dict__
    if (
        value.__globals__ is not defining_globals
        or value.__builtins__ is not defining_builtins
    ):
        _deny("postpublication_source_namespace_unbound")
    # Inspect only builtin names read by this exact source-compiled function
    # and nested code. No leaf or source module is executed by this traversal.
    pending = [found[0]]
    names = set()
    while pending:
        code = pending.pop()
        names.update(code.co_names)
        pending.extend(item for item in code.co_consts if type(item) is type(code))
    lookups = tuple(
        (name, defining_builtins[name])
        for name in sorted(names)
        if name in defining_builtins and not name.startswith("_")
    )
    for name, original in lookups:
        if (
            defining_globals.get(name, original) is not original
            or defining_builtins.get(name) is not original
        ):
            _deny("postpublication_source_namespace_unbound")
    return defining_globals, defining_builtins, lookups


def _source_wrapped_leaf(value, raw, filename):
    """Bind only the pinned plan_sha256 bounded wrapper and its original leaf."""
    if (
        type(value) is not FunctionType
        or value is not vars(capture).get("plan_sha256")
        or Path(filename).resolve()
        != _ROOT / "app/trade_qualification/account_capture.py"
        or sha(raw)
        != _KNOWN_SOURCE_HASHES["app/trade_qualification/account_capture.py"]
        or value.__code__.co_qualname != "_bounded_api.<locals>.checked"
        or value.__code__.co_freevars != ("function",)
        or value.__defaults__ is not None
        or value.__kwdefaults__ is not None
        or type(value.__closure__) is not tuple
        or len(value.__closure__) != 1
    ):
        _deny("postpublication_source_closure_unbound")
    _source_code(value, raw, filename)
    try:
        original = value.__closure__[0].cell_contents
    except ValueError:
        _deny("postpublication_source_closure_unbound")
    if (
        type(original) is not FunctionType
        or vars(value).get("__wrapped__") is not original
        or original.__code__.co_qualname != "plan_sha256"
        or original.__closure__ is not None
        or original.__defaults__ is not None
        or original.__kwdefaults__ is not None
    ):
        _deny("postpublication_source_closure_unbound")
    defining_globals, defining_builtins, lookups = _source_code(original, raw, filename)
    return (
        value,
        "__wrapped__",
        original,
        original.__code__,
        None,
        None,
        None,
        (),
        (),
        defining_globals,
        defining_builtins,
        lookups,
    )


def _make_entry(guard, opening, _tuple=tuple, _any=any, _zip=zip):
    guard_code, opening_code = guard.__code__, opening.__code__
    cells = _tuple(cell.cell_contents for cell in guard.__closure__)

    def entry(
        *,
        read_session,
        private_client,
        plan_template,
        evidence_root,
        public_root,
        public_account_proof_root,
        account_proof_root,
        account_native_root,
        receipt_root,
        original_market,
        run,
        original_inputs,
        market_policy,
        session_factory,
        custody_session_factory,
    ):
        if (
            _pins is not guard
            or guard.__code__ is not guard_code
            or opening.__code__ is not opening_code
            or _any(
                cell.cell_contents is not held
                for cell, held in _zip(guard.__closure__, cells, strict=True)
            )
        ):
            raise PostpublicationSourceError("postpublication_source_leaf_changed")
        guard()
        return opening(
            read_session=read_session,
            private_client=private_client,
            plan_template=plan_template,
            evidence_root=evidence_root,
            public_root=public_root,
            public_account_proof_root=public_account_proof_root,
            account_proof_root=account_proof_root,
            account_native_root=account_native_root,
            receipt_root=receipt_root,
            original_market=original_market,
            run=run,
            original_inputs=original_inputs,
            market_policy=market_policy,
            session_factory=session_factory,
            custody_session_factory=custody_session_factory,
            _source_guard=guard,
        )

    return entry


# SOURCE_GUARD_INITIALIZATION: filled from exact proposed source by the bounded
# source-only packaging script. No caller supplied digest or eventual seal.

_ROOT = Path(__file__).resolve().parents[2]
_OWN_PATH = Path(__file__).resolve()
_OWN_RAW = _OWN_PATH.read_bytes()
_KNOWN_SOURCE_HASHES = {
    "app/trade_qualification/demo_market_source_v4.py": "9d002d53e12e32186f5ec526a4ac4a00e00b7a0f9b479cdc09330155e749319e",
    "app/trade_qualification/account_current_source_v4.py": "a62beb79cece2ad866ff9029e7bcb591526e57b6cacfbef7ed90871e8d111bf6",
    "app/trade_qualification/g12_invocation_custody.py": "941274a9df5d2528abd6ec4ad5a733806e633e396b7fa89a23a0483f2a9cd111",
    "app/trade_qualification/owned_current_original_frame_v1.py": "73fc39319f3a0b5ab0a9972d556695cef396607b0d1dd3445ced4bf25fa99256",
    "app/database/repositories/public_runtime_bundle_custody.py": "6c841ee62723601086a58796ef327a7ce2e79f41d4a86930903054e7a2287b30",
    "app/exchange/okx/private_rest.py": "34d66da9949e18e926b29b54115e7d7ae904dde12b4809e3bb79ac0c6ed447ee",
    "app/trade_qualification/account_runtime.py": "048c00f61dc814f473c0e2b894b45dc36b09e6a809693571fd9c301a651319c6",
    "app/trade_qualification/account_capture.py": "9404161f880eac133746984538d2f21fcaac63d794b984f848a51272eb2ed88e",
    "app/trade_evidence/gates.py": "7be0f614d16c42fab49e923a97890d6a09e9a512b789d940f8bb379c0e344d50",
    "app/public_market_source/public_clock.py": "33ed2e33d6626ddc1292c009c734cb8cf739eacab80769b1e0658f6fbf1f03c5",
    "app/trade_qualification/public_source_runtime.py": "bd99dd32d32d4dc7b48c89c86a6c2e7ddc26cde7e1c4ee7719e369afd05a225e",
    "app/domain/native_clock.py": "5fcae37c99007dfba52ffe98afa2a8d51547713752e0e45efbf3122d8da41dae",
    "app/domain/source_primitives.py": "660a18a2bab787297c370d5316fa08cbf0ecf01273ea95ed990b949c4953420d",
    "app/trade_qualification/market_bridge_v2.py": "978bd603bbbe0fcd535fac3ae29ad06947eb4b428412a175480bd6ca3afa0988",
    "app/public_market_source/public_runtime_custody_v4.py": "b666b0746a9a7160f73502b9b3b31cffbcb528087acf605b1e138d61ac909b1a",
    "app/trade_qualification/recheck_models.py": "fc1e07bf5e84a1978ce41c41a4e84250f4449e9c0abb41ad86b426c747184fc4",
    "app/trade_qualification/demo_operational_route_v4.py": "2d96ff4e0f830829e5d76c5b5b2173ae019b6a2a70fc66f4607f8d7d6898f35f",
    "app/trade_qualification/account_native_runtime.py": "32cb9e1fde514ef0e91b447b7c0dfef459ce276cd08bf4b9a293cce896d3615c",
    "app/trade_qualification/account_collector.py": "6ea758f8cb02acc608eb918506f765a62aa44e72ff063fbc85202223397af6b7",
    "app/trade_qualification/account_capture_journal.py": "a68bc042208eccb0f45ea0f4a46a39d6332b1a5732e6e50645757a70a7c9720d",
    "app/trade_qualification/demo_public_origin.py": "14ea0410408d308257f87443045caa557ffbbc8a3d0ac3c1dc74a6c3595d8f20",
    "app/trade_qualification/public_market_collector.py": "6742a28ccbf1937a772a946c0ffc4b3c36524bb91e93b2554c3053abf6e113bf",
    "app/trade_qualification/public_market_collector_v2.py": "3a34151d1a7e418f8efc2c456cd48fc294652f1fe80a2fe8e4caf1d942d7fedd",
    "app/public_market_source/public_runtime_journal.py": "5706889e3e819852a11777738b2798e2e8f8f16ddea77734e328c2dea79a8dd7",
}

# These are existing engineering caps, not caller choices or new tolerances.
if (
    primitives.MAX_CLOCK_DEVIATION_NS != 5_000_000
    or primitives.MAX_CLOCK_BATCH_NS != 60_000_000_000
    or account._MAX_NATIVE_WALL_SECONDS != 30
    or market.operational._LEASE_NS != 5_000_000_000
    or market.operational._MAX_CONFIG_WALL_SECONDS != 5
):
    _deny("postpublication_native_policy_changed")

# Reuse the journal's existing engineering bounds; changing them requires a
# new reviewed source policy, never a terminal scanner tolerance adjustment.
if (
    public_custody.journal.MAX_EVENTS != 4096
    or public_custody.journal.MAX_RAW != 32 * 1024 * 1024
    or public_custody.journal.MAX_TOTAL != 64 * 1024 * 1024
    or runtime.MAX_RAW != public_custody.journal.MAX_RAW
    or type(public_custody.journal._KINDS) is not frozenset
    or public_custody.journal._KINDS
    != {
        "clock_before",
        "clock_after",
        "request",
        "headers",
        "chunk",
        "body_complete",
        "response_closed",
        "request_failed",
        "time_validated",
        "ws_connect",
        "ws_connected",
        "ws_subscribe",
        "ws_subscribed",
        "ws_ack",
        "ws_ticker",
        "ws_closed",
        "component_failed",
        "packet",
    }
):
    _deny("postpublication_native_policy_changed")

_RECORDS = []
_SOURCES = {_OWN_PATH: sha(_OWN_RAW)}
for _namespace, _name in (
    (sys.modules[__name__], "_source_builtins"),
    (sys.modules[__name__], "primitives"),
    (sys.modules[__name__], "asyncio"),
    (sys.modules[__name__], "os"),
    (sys.modules[__name__], "sys"),
    (sys.modules[__name__], "uuid"),
    (sys.modules[__name__], "get_ident"),
    (primitives, "MAX_CLOCK_DEVIATION_NS"),
    (primitives, "MAX_CLOCK_BATCH_NS"),
    (primitives, "ClockStamp"),
    (account, "_MAX_NATIVE_WALL_SECONDS"),
    (market.operational, "_LEASE_NS"),
    (market.operational, "_MAX_CONFIG_WALL_SECONDS"),
    (sys.modules[__name__], "Path"),
    (sys.modules[__name__], "FunctionType"),
    (market.public, "_policy_copy"),
    (market.public, "_policy_digest"),
    (market.operational, "reviewed_demo_public_route"),
    (sys.modules[__name__], "PostpublicationSourceError"),
    (sys.modules[__name__], "_Attempt"),
    (sys.modules[__name__], "OwnedPostpublicationSourcesV1"),
    (sys.modules[__name__], "public_custody"),
    (sys.modules[__name__], "_secret_public_journal"),
    (public_custody, "RuntimeBundleDirectoryV4"),
    (public_custody.RuntimeBundleDirectoryV4, "__init__"),
    (public_custody.RuntimeBundleDirectoryV4, "names"),
    (public_custody.RuntimeBundleDirectoryV4, "read"),
    (public_custody, "_deny"),
    (public_custody, "PublicReceiptError"),
    (public_custody, "journal"),
    (public_custody.journal, "MAX_EVENTS"),
    (public_custody.journal, "MAX_RAW"),
    (public_custody.journal, "MAX_TOTAL"),
    (public_custody.journal, "_KINDS"),
    (public_custody.journal, "replay_runtime_attempt"),
    (public_custody.journal, "_replay_semantics"),
    (runtime, "MAX_RAW"),
    (account, "runtime"),
    (account.runtime, "collector"),
    (sys.modules[__name__], "_secret_complete"),
    (market, "_CURRENT"),
    (market, "_SCOPES"),
    (market, "_acquire_initial_demo_market_source_v4"),
    (market, "_check_owner"),
    (market, "_route_proof"),
    (account, "_CURRENT"),
    (account, "_INITIAL"),
    (account, "_acquire_native"),
    (account, "_check_owner"),
    (account, "_check_context"),
    (account, "_freeze_bundle"),
    (account, "_chain_replay"),
    (market.operational, "_OWNED_ROUTES"),
    (market.operational, "probe_initial_demo_operational_route_v4"),
    (market.operational, "_probe_initial_demo_operational_route_retained_v4"),
    (market.operational, "_consume_initial_route_state_v4"),
    (market.operational, "_owned_route_state_v4"),
    (market.operational, "_claim_initial_route_v4"),
    (account.runtime, "_secret_checked"),
    (account.runtime.collector, "_no_secrets"),
    (account.runtime.collector, "_no_secret_json"),
    (account.journal, "checked_event"),
    (account.journal, "_check_owned_packet_for_storage"),
    (_Attempt, "__init__"),
    (_Attempt, "send"),
    (_Attempt, "throw"),
    (_Attempt, "close"),
    (_Attempt, "_retire"),
    (OwnedPostpublicationSourcesV1, "close"),
    (sys.modules[__name__], "_bind_cleanup"),
    (sys.modules[__name__], "_State"),
    (sys.modules[__name__], "ref"),
    (sys.modules[__name__], "finalize"),
    (sys.modules[__name__], "market"),
    (sys.modules[__name__], "account"),
    (sys.modules[__name__], "publication"),
    (sys.modules[__name__], "custody"),
    (sys.modules[__name__], "runtime"),
    (sys.modules[__name__], "capture"),
    (sys.modules[__name__], "gates"),
    (sys.modules[__name__], "original_frame"),
    (sys.modules[__name__], "public_clock"),
    (sys.modules[__name__], "ControlledDemoAccountSession"),
    (sys.modules[__name__], "DemoPinnedReadSession"),
    (sys.modules[__name__], "OkxDemoPrivateRestClient"),
    (sys.modules[__name__], "_HOOK_KEY"),
    (sys.modules[__name__], "_OWNERS"),
    (sys.modules[__name__], "SCHEMA"),
    (sys.modules[__name__], "_MAX_LINK"),
    (sys.modules[__name__], "_deny"),
    (sys.modules[__name__], "_release"),
    (sys.modules[__name__], "_after"),
    (sys.modules[__name__], "_context"),
    (sys.modules[__name__], "_lineage"),
    (sys.modules[__name__], "_check"),
    (sys.modules[__name__], "_fresh_session"),
    (sys.modules[__name__], "_clock_after"),
    (sys.modules[__name__], "_replay_sources"),
    (sys.modules[__name__], "_links"),
    (sys.modules[__name__], "_run"),
    (sys.modules[__name__], "_bounded_run"),
    (sys.modules[__name__], "_open_acquisition"),
    (sys.modules[__name__], "consume_postpublication_source_operands_v1"),
    (sys.modules[__name__], "_build_guard"),
    (sys.modules[__name__], "_source_code"),
    (sys.modules[__name__], "_code_fields"),
    (sys.modules[__name__], "_source_wrapped_leaf"),
    (sys.modules[__name__], "CodeType"),
    (sys.modules[__name__], "_pack"),
    (sys.modules[__name__], "_make_entry"),
    (sys.modules[__name__], "native_stamp"),
    (sys.modules[__name__], "canonical"),
    (sys.modules[__name__], "decode"),
    (sys.modules[__name__], "sha"),
    (sys.modules[__name__], "utc_from_ns"),
    (sys.modules[__name__], "validate_stamps"),
    (sys.modules[__name__], "freeze_recheck_origin"),
    (market, "acquire_initial_demo_market_source_v4"),
    (market, "_take_postpublication_sources_v1"),
    (market, "_replay_postpublication_sources_v1"),
    (market, "replay_runtime_bundle_v4"),
    (market, "public_market_context_v2"),
    (account, "acquire_initial_demo_account_source_v4"),
    (account, "_take_postpublication_sources_v1"),
    (account, "_replay_postpublication_sources_v1"),
    (account, "replay_current_account_bundle_v4"),
    (account, "_check_current"),
    (account, "_unhex"),
    (publication, "prepare_g12_claim"),
    (publication, "prepare_g12_publication"),
    (publication, "require_prepublication_root"),
    (publication, "require_original_unchanged"),
    (publication, "require_committed_publication_files"),
    (publication, "require_replayed_publication_computation"),
    (publication.G12InvocationCustodyRepository, "claim"),
    (publication.G12InvocationCustodyRepository, "publication"),
    (original_frame, "_database_pins"),
    (custody.RuntimeBundleCustodyRepository, "_check_factory"),
    (DemoPinnedReadSession, "verify_current"),
    (ControlledDemoAccountSession, "from_pinned_read_session"),
    (capture, "CurrentDemoAccountCapturePlanV7"),
    (capture, "_checked_plan"),
    (capture, "plan_sha256"),
    (gates, "verify_pre_evidence_versioned"),
    (gates, "publish_qualification_evidence_liquidity_v2"),
    (public_clock, "replay_clock_observation"),
    (runtime, "_native_recheck_root"),
):
    _value = vars(_namespace)[_name]
    _function = (
        _value.__func__
        if type(_value) is classmethod
        else _value
        if type(_value) is FunctionType
        else None
    )
    _code = getattr(_function, "__code__", None)
    _defining_globals, _defining_builtins, _builtin_lookups = None, None, ()
    if _code is not None:
        _path = Path(_code.co_filename).resolve()
        if _path == _OWN_PATH:
            _source_raw = _OWN_RAW
        else:
            _relative = _path.relative_to(_ROOT).as_posix()
            _source_raw = _path.read_bytes()
            if (
                _relative not in _KNOWN_SOURCE_HASHES
                or sha(_source_raw) != _KNOWN_SOURCE_HASHES[_relative]
            ):
                _deny("postpublication_source_code_unbound")
        _defining_globals, _defining_builtins, _builtin_lookups = _source_code(
            _function, _source_raw, _code.co_filename
        )
        _SOURCES[_path] = sha(_source_raw)
    _closure = getattr(_function, "__closure__", None)
    if _closure is not None:
        # Only the pinned bounded-api plan wrapper is permitted. Both its
        # source-compiled original leaf and the closure/metadata identities
        # remain under the same guard; no arbitrary observed cell is trusted.
        if _namespace is not capture or _name != "plan_sha256":
            _deny("postpublication_source_closure_unbound")
        _RECORDS.append(_source_wrapped_leaf(_function, _source_raw, _code.co_filename))
    _kwdefaults = getattr(_function, "__kwdefaults__", None)
    _RECORDS.append(
        (
            _namespace,
            _name,
            _value,
            _code,
            getattr(_function, "__defaults__", None),
            getattr(_function, "__kwdefaults__", None),
            _closure,
            () if _closure is None else tuple(cell.cell_contents for cell in _closure),
            () if _kwdefaults is None else tuple(sorted(_kwdefaults.items())),
            _defining_globals,
            _defining_builtins,
            _builtin_lookups,
        )
    )
for _name in (
    "type",
    "dict",
    "tuple",
    "list",
    "bytes",
    "str",
    "int",
    "bool",
    "any",
    "all",
    "zip",
    "len",
    "min",
    "max",
    "vars",
    "getattr",
    "set",
    "isinstance",
    "compile",
    "Exception",
    "BaseException",
    "OSError",
    "StopIteration",
    "object",
    "globals",
    "bytearray",
    "frozenset",
):
    if _name in vars(sys.modules[__name__]):
        _deny("postpublication_builtin_shadow_unbound")
    _RECORDS.append(
        (
            sys.modules[__name__],
            _name,
            _MISSING,
            None,
            None,
            None,
            None,
            (),
            (),
            None,
            None,
            (),
        )
    )
_pins = _build_guard(tuple(_RECORDS), tuple(_SOURCES.items()))
publish_acquire_postpublication_sources_v1 = _make_entry(_pins, _open_acquisition)
