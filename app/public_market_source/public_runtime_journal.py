"""Bounded component/WS observation journal; replay is never a source permit.

This format is separate from the sealed minute-capture attempt formats. Native
storage failure leaves an incomplete tail, never deletes already written bytes.
"""

import re
from contextlib import contextmanager
from datetime import datetime
from threading import Lock
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

from app.public_market_source.public_market_receipts import (
    ClockStamp,
    PublicReceiptError,
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.public_market_source.public_receipt_storage import _root_context

_ISSUER = object()
_ATTEMPTS = WeakKeyDictionary()
_LOCK = Lock()
MAX_EVENTS = 4096
MAX_RAW = 32 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
_PUBLIC_WS_ORIGINS = frozenset(
    {
        "wss://ws.okx.com:443/ws/v5/public",
        "wss://ws.okx.com:8443/ws/v5/public",  # historical replay only
    }
)
_SAFE_CLOCK_FAILURE_CODES = frozenset(
    {
        "clock_jump",
        "clock_lease_expired",
        "clock_reversed",
        "native_clock_domain_unsupported",
        "native_clock_filetime_invalid",
        "native_clock_sample_unbounded",
        "native_precise_clock_unavailable",
        "timestamp_invalid",
        "utc_conversion_failed",
    }
)
_REQUEST_FAILURE_CODES = _SAFE_CLOCK_FAILURE_CODES | frozenset(
    {"cancelled", "source_or_transport_rejected"}
)
_KINDS = frozenset(
    {
        "clock_before",
        "clock_after",
        "request",
        "headers",
        "chunk",
        "body_complete",
        "response_closed",
        "request_failed",
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
)


class _RuntimeAttempt:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            raise PublicReceiptError("runtime_attempt_required")

    def __copy__(self):
        raise PublicReceiptError("runtime_attempt_not_transferable")

    def __deepcopy__(self, memo):
        raise PublicReceiptError("runtime_attempt_not_transferable")

    def __reduce_ex__(self, protocol):
        raise PublicReceiptError("runtime_attempt_not_transferable")


def _state(attempt):
    if type(attempt) is not _RuntimeAttempt:
        raise PublicReceiptError("runtime_attempt_required")
    value = _ATTEMPTS.get(attempt)
    if value is None or value["closed"] or value["poisoned"] or value["sealed"]:
        raise PublicReceiptError("runtime_attempt_unavailable")
    return value


def _claim_runtime_clock(attempt, stage):
    with _LOCK:
        state = _state(attempt)
        if type(stage) is not str or stage not in ("before", "after"):
            raise PublicReceiptError("runtime_clock_stage_invalid")
        if stage in state["clock_claims"] or (
            stage == "after" and state["clock_results"].get("before") != "accepted"
        ):
            raise PublicReceiptError("runtime_clock_already_claimed")
        state["clock_claims"].add(stage)


def _append(attempt, kind, metadata, raw=None):
    state = _state(attempt)
    if type(kind) is not str or kind not in _KINDS or type(metadata) is not dict:
        raise PublicReceiptError("runtime_event_invalid")
    if raw is not None and (type(raw) is not bytes or len(raw) > MAX_RAW):
        raise PublicReceiptError("runtime_raw_invalid")
    index = len(state["events"])
    if index >= MAX_EVENTS:
        raise PublicReceiptError("runtime_event_limit")
    record = {
        "schema_version": "ctcc.public.runtime_event.v1",
        "index": index,
        "kind": kind,
        "plan_sha256": state["plan_sha256"],
        "previous_sha256": state["head"],
        "metadata": metadata,
        "raw_sha256": None if raw is None else sha(raw),
        "raw_bytes": None if raw is None else len(raw),
    }
    encoded = canonical(record)
    if (
        len(encoded) > MAX_RAW
        or state["total"] + len(encoded) + len(raw or b"") > MAX_TOTAL
    ):
        raise PublicReceiptError("runtime_byte_limit")
    directory = state["directory"]
    try:
        if raw is not None:
            directory.publish(f"event-{index:04d}.raw", raw)
            if directory.read(f"event-{index:04d}.raw", MAX_RAW) != raw:
                raise PublicReceiptError("runtime_raw_readback_failed")
        directory.publish(f"event-{index:04d}.json", encoded)
        if directory.read(f"event-{index:04d}.json", MAX_RAW) != encoded:
            raise PublicReceiptError("runtime_event_readback_failed")
    except BaseException:
        state["poisoned"] = True
        raise
    state["events"].append(record)
    state["head"] = sha(encoded)
    state["total"] += len(encoded) + len(raw or b"")
    return state["head"]


def _record_clock(attempt, stage, carrier):
    from app.public_market_source.public_clock import (
        _owned_clock_payload,
        replay_clock_observation,
    )

    state = _state(attempt)
    if stage not in state["clock_claims"] or stage in state["clock_results"]:
        raise PublicReceiptError("runtime_clock_stage_invalid")
    raw = _owned_clock_payload(carrier, attempt, stage)
    result = replay_clock_observation(raw)
    _append(attempt, f"clock_{stage}", {"outcome": result["outcome"]}, raw)
    state["clock_results"][stage] = result["outcome"]
    return result


def _seal(attempt, *, success, stamp, packet_sha256=None):
    state = _state(attempt)
    if type(success) is not bool or state["sealed"]:
        raise PublicReceiptError("runtime_seal_invalid")
    if success and (
        state["clock_results"] != {"before": "accepted", "after": "accepted"}
        or stamp is None
        or type(packet_sha256) is not str
        or not state["events"]
        or state["events"][-1]["kind"] != "packet"
        or state["events"][-1]["raw_sha256"] != packet_sha256
    ):
        raise PublicReceiptError("runtime_capture_incomplete")
    if not success and packet_sha256 is not None:
        raise PublicReceiptError("runtime_rejected_packet_forbidden")
    summary = {
        "schema_version": "ctcc.public.runtime_summary.v1",
        "plan_sha256": state["plan_sha256"],
        "event_count": len(state["events"]),
        "head_sha256": state["head"],
        "disposition": "captured" if success else "rejected",
        "completed": None
        if stamp is None
        else ClockStamp.model_validate(stamp).model_dump(),
        "packet_sha256": packet_sha256,
        "execution_authority": False,
        "measured_availability_eligible": False,
        "original_source_verified": False,
        "account_complete": False,
    }
    encoded = canonical(summary)
    try:
        state["directory"].publish("summary.json", encoded)
        if state["directory"].read("summary.json", MAX_RAW) != encoded:
            raise PublicReceiptError("runtime_summary_readback_failed")
        replay_runtime_attempt(
            state["directory"], expected_plan_sha256=state["plan_sha256"]
        )
    except BaseException:
        state["poisoned"] = True
        raise
    state["sealed"] = True
    return sha(encoded)


def replay_runtime_attempt(directory, *, expected_plan_sha256):
    plan_raw = directory.read("plan.json", MAX_RAW)
    plan = decode(plan_raw)
    if canonical(plan) != plan_raw or sha(plan_raw) != expected_plan_sha256:
        raise PublicReceiptError("runtime_plan_pin_mismatch")
    _validate_plan(plan)
    raw = directory.read("summary.json", MAX_RAW)
    summary = decode(raw)
    keys = {
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
    if (
        canonical(summary) != raw
        or set(summary) != keys
        or summary["schema_version"] != "ctcc.public.runtime_summary.v1"
        or summary["plan_sha256"] != expected_plan_sha256
    ):
        raise PublicReceiptError("runtime_summary_invalid")
    if any(
        summary[k] is not False
        for k in (
            "execution_authority",
            "measured_availability_eligible",
            "original_source_verified",
            "account_complete",
        )
    ):
        raise PublicReceiptError("runtime_authority_forbidden")
    count = summary["event_count"]
    if type(count) is not int or not 0 <= count <= MAX_EVENTS:
        raise PublicReceiptError("runtime_event_limit")
    expected, previous, total, events, clocks, payloads = (
        {"plan.json", "summary.json"},
        expected_plan_sha256,
        len(plan_raw) + len(raw),
        [],
        {},
        [],
    )
    for index in range(count):
        name = f"event-{index:04d}.json"
        encoded = directory.read(name, MAX_RAW)
        event = decode(encoded)
        if (
            canonical(event) != encoded
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
            or type(event["index"]) is not int
            or event["index"] != index
            or event["kind"] not in _KINDS
            or event["plan_sha256"] != expected_plan_sha256
            or event["previous_sha256"] != previous
            or type(event["metadata"]) is not dict
        ):
            raise PublicReceiptError("runtime_event_invalid")
        expected.add(name)
        total += len(encoded)
        payload = None
        if event["raw_bytes"] is not None:
            if (
                type(event["raw_bytes"]) is not int
                or not 0 <= event["raw_bytes"] <= MAX_RAW
            ):
                raise PublicReceiptError("runtime_raw_invalid")
            name = f"event-{index:04d}.raw"
            expected.add(name)
            payload = directory.read(name, MAX_RAW)
            if (
                len(payload) != event["raw_bytes"]
                or sha(payload) != event["raw_sha256"]
            ):
                raise PublicReceiptError("runtime_raw_pin_mismatch")
            total += len(payload)
        elif event["raw_sha256"] is not None:
            raise PublicReceiptError("runtime_raw_invalid")
        if event["kind"].startswith("clock_"):
            from app.public_market_source.public_clock import replay_clock_observation

            stage = event["kind"].removeprefix("clock_")
            observed = replay_clock_observation(payload)
            if (
                stage in clocks
                or (stage == "after" and clocks.get("before") != "accepted")
                or event["metadata"] != {"outcome": observed["outcome"]}
            ):
                raise PublicReceiptError("runtime_clock_inventory_invalid")
            clocks[stage] = observed["outcome"]
        events.append(event)
        payloads.append(payload)
        previous = sha(encoded)
    if (
        total > MAX_TOTAL
        or previous != summary["head_sha256"]
        or set(directory.names()) != expected
    ):
        raise PublicReceiptError("runtime_inventory_invalid")
    if summary["completed"] is not None:
        ClockStamp.model_validate(summary["completed"])
    if summary["disposition"] == "captured":
        if (
            clocks != {"before": "accepted", "after": "accepted"}
            or summary["completed"] is None
            or not events
            or events[-1]["kind"] != "packet"
            or events[-1]["raw_sha256"] != summary["packet_sha256"]
        ):
            raise PublicReceiptError("runtime_capture_incomplete")
    elif summary["disposition"] != "rejected" or summary["packet_sha256"] is not None:
        raise PublicReceiptError("runtime_summary_invalid")
    _replay_semantics(plan, summary, events, payloads)
    return summary, tuple(events)


def _sealed_readback(attempt):
    """Read immutable bytes again; this is evidence, never a source capability."""
    if type(attempt) is not _RuntimeAttempt:
        raise PublicReceiptError("runtime_attempt_required")
    state = _ATTEMPTS.get(attempt)
    if state is None or state["closed"] or state["poisoned"] or not state["sealed"]:
        raise PublicReceiptError("runtime_attempt_unavailable")
    directory = state["directory"]
    summary, events = replay_runtime_attempt(
        directory, expected_plan_sha256=state["plan_sha256"]
    )
    if summary["disposition"] != "captured":
        raise PublicReceiptError("runtime_capture_incomplete")
    return (
        directory.read("plan.json", MAX_RAW),
        directory.read("summary.json", MAX_RAW),
        directory.read(f"event-{events[-1]['index']:04d}.raw", MAX_RAW),
    )


@contextmanager
def _runtime_attempt(root, plan):
    _validate_plan(plan)
    encoded = canonical(plan)
    if len(encoded) > MAX_RAW:
        raise PublicReceiptError("runtime_plan_invalid")
    with _root_context(root) as directory:
        if directory.names():
            raise PublicReceiptError("runtime_root_not_empty")
        directory.publish("plan.json", encoded)
        if directory.read("plan.json", MAX_RAW) != encoded:
            raise PublicReceiptError("runtime_plan_readback_failed")
        attempt = _RuntimeAttempt(_ISSUER)
        _ATTEMPTS[attempt] = {
            "directory": directory,
            "plan_sha256": sha(encoded),
            "events": [],
            "head": sha(encoded),
            "total": len(encoded),
            "clock_claims": set(),
            "clock_results": {},
            "closed": False,
            "poisoned": False,
            "sealed": False,
        }
        try:
            yield attempt
        finally:
            state = _ATTEMPTS.pop(attempt, None)
            if state is not None:
                state["closed"] = True


def _time(value):
    if type(value) is not str:
        raise PublicReceiptError("runtime_time_invalid")
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None or result.utcoffset().total_seconds() != 0:
        raise PublicReceiptError("runtime_time_invalid")
    return result


def _validate_plan(plan):
    if type(plan) is dict and plan.get("schema_version") in {
        "ctcc.public.initial_runtime_plan.v2",
        "ctcc.public.runtime_plan.v2",
    }:
        return _validate_v2_plan(plan)
    if (
        type(plan) is dict
        and plan.get("schema_version") == "ctcc.public.initial_runtime_plan.v1"
    ):
        return _validate_initial_plan(plan)
    keys = {
        "schema_version",
        "invocation_id",
        "environment",
        "report_id",
        "instrument_id",
        "candidate_sha256",
        "event_key",
        "original_policy_sha256",
        "evidence_sha256",
        "report_sha256",
        "publication_completed_at",
        "barrier",
        "expires_at",
        "rest_origin",
        "ws_origin",
        "policy_sha256",
    }
    if (
        type(plan) is not dict
        or set(plan) != keys
        or plan["schema_version"] != "ctcc.public.runtime_plan.v1"
        or plan["environment"] != "demo"
        or plan["rest_origin"] != "https://www.okx.com"
        or plan["ws_origin"] not in _PUBLIC_WS_ORIGINS
    ):
        raise PublicReceiptError("runtime_plan_invalid")
    if (
        type(plan["invocation_id"]) is not str
        or re.fullmatch("[0-9a-f]{32}", plan["invocation_id"]) is None
        or type(plan["instrument_id"]) is not str
        or re.fullmatch("[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP", plan["instrument_id"])
        is None
        or type(plan["report_id"]) is not str
        or not 1 <= len(plan["report_id"]) <= 128
    ):
        raise PublicReceiptError("runtime_plan_invalid")
    for name in (
        "candidate_sha256",
        "event_key",
        "original_policy_sha256",
        "evidence_sha256",
        "report_sha256",
        "policy_sha256",
    ):
        if (
            type(plan[name]) is not str
            or re.fullmatch("[0-9a-f]{64}", plan[name]) is None
        ):
            raise PublicReceiptError("runtime_plan_pin_invalid")
    ClockStamp.model_validate(plan["barrier"])
    if (
        not _time(plan["publication_completed_at"])
        <= utc_from_ns(plan["barrier"]["utc_ns"])
        < _time(plan["expires_at"])
    ):
        raise PublicReceiptError("runtime_plan_chronology_invalid")


def _validate_initial_plan(plan):
    # No G12, candidate, event, or qualification-policy claim exists at this
    # stage. An initial observation time must never stand in for publication.
    if (
        set(plan)
        != {
            "schema_version",
            "stage",
            "invocation_id",
            "environment",
            "report_id",
            "instrument_id",
            "invocation_started",
            "expires_at",
            "rest_origin",
            "ws_origin",
            "policy_sha256",
        }
        or plan["stage"] != "initial_public"
        or plan["environment"] != "demo"
        or plan["rest_origin"] != "https://www.okx.com"
        or plan["ws_origin"] not in _PUBLIC_WS_ORIGINS
        or type(plan["invocation_id"]) is not str
        or re.fullmatch("[0-9a-f]{32}", plan["invocation_id"]) is None
        or type(plan["instrument_id"]) is not str
        or re.fullmatch("[A-Z0-9]{1,16}-USDT-SWAP", plan["instrument_id"]) is None
        or type(plan["report_id"]) is not str
        or plan["report_id"] != "initial-" + plan["invocation_id"]
        or type(plan["policy_sha256"]) is not str
        or re.fullmatch("[0-9a-f]{64}", plan["policy_sha256"]) is None
    ):
        raise PublicReceiptError("runtime_initial_plan_invalid")
    ClockStamp.model_validate(plan["invocation_started"])
    lifetime = (
        _time(plan["expires_at"]) - utc_from_ns(plan["invocation_started"]["utc_ns"])
    ).total_seconds()
    if not 0 < lifetime <= 60:
        raise PublicReceiptError("runtime_initial_plan_chronology_invalid")


def _validate_v2_plan(plan):
    initial = plan["schema_version"] == "ctcc.public.initial_runtime_plan.v2"
    required = {
        "public_packet_schema",
        "quote_collector_schema",
        "quote_profile_sha256",
        "quote_transport_policy_sha256",
        "stage",
    }
    route_fields = {
        "registration_region",
        "registration_region_authenticated",
        "account_plan_sha256",
        "demo_public_origin_policy_sha256",
    }
    routed = bool(route_fields & plan.keys())
    if (
        not required.issubset(plan)
        or plan["stage"] != ("initial_public" if initial else "post_publication")
        or plan["public_packet_schema"] != "ctcc.collected_public_market.v2"
        or plan["quote_collector_schema"] != "ctcc.collected_executable_quote.v2"
        or any(
            type(plan[k]) is not str or re.fullmatch("[0-9a-f]{64}", plan[k]) is None
            for k in ("quote_profile_sha256", "quote_transport_policy_sha256")
        )
    ):
        raise PublicReceiptError("runtime_v2_plan_invalid")
    if routed:
        from app.trade_qualification import demo_public_origin as demo_origin
        from app.trade_qualification import demo_public_origin_policy_v2 as demo_policy
        from app.trade_qualification import quote_collector_v2 as quote_v2

        try:
            route = demo_origin.reviewed_demo_public_route(plan["registration_region"])
            if (
                not route_fields.issubset(plan)
                or plan["registration_region_authenticated"] is not False
                or plan["rest_origin"] != route.rest_origin
                or plan["ws_origin"] != route.ws_origin
                or any(
                    type(plan[name]) is not str
                    or re.fullmatch("[0-9a-f]{64}", plan[name]) is None
                    for name in (
                        "account_plan_sha256",
                        "demo_public_origin_policy_sha256",
                    )
                )
                or plan["demo_public_origin_policy_sha256"]
                != sha(
                    demo_policy.freeze_demo_public_origin_policy_v2(
                        route.registration_region
                    )
                )
                or plan["quote_transport_policy_sha256"]
                != quote_v2._transport_policy_sha256(route)
            ):
                raise ValueError
        except (KeyError, ValueError):
            raise PublicReceiptError("runtime_v2_route_plan_invalid") from None
    # Reuse exact existing identity/chronology validation without changing those
    # schemas or converting any source, publication receipt or capability.
    base = {
        key: value for key, value in plan.items() if key not in required | route_fields
    }
    if routed:
        # V1's frozen identity/chronology contract is reused solely for its
        # non-route fields; the complete V2 Demo route was checked above.
        base["rest_origin"] = "https://www.okx.com"
        base["ws_origin"] = "wss://ws.okx.com:443/ws/v5/public"
    if initial:
        base.update(
            schema_version="ctcc.public.initial_runtime_plan.v1", stage="initial_public"
        )
        _validate_initial_plan(base)
    else:
        base["schema_version"] = "ctcc.public.runtime_plan.v1"
        _validate_plan(base)


def _plan_start(plan):
    if plan["schema_version"] in {
        "ctcc.public.initial_runtime_plan.v1",
        "ctcc.public.initial_runtime_plan.v2",
    }:
        return plan["invocation_started"]
    return plan["barrier"]


def _packet_stage_matches(plan, barrier, *, exact=False):
    if plan["schema_version"] in {
        "ctcc.public.initial_runtime_plan.v1",
        "ctcc.public.initial_runtime_plan.v2",
    }:
        return barrier is None
    return barrier is not None and (
        barrier == plan["publication_completed_at"]
        if exact
        else _time(barrier) == _time(plan["publication_completed_at"])
    )


def _tls(value, hostname):
    if (
        type(value) is not dict
        or set(value) != {"classification", "hostname", "version", "peer_sha256"}
        or value["hostname"] != hostname
    ):
        raise PublicReceiptError("runtime_tls_invalid")
    if value["classification"] == "unverified":
        if value["version"] is not None or value["peer_sha256"] is not None:
            raise PublicReceiptError("runtime_tls_invalid")
        return False
    if (
        value["classification"] != "owned_native_tls"
        or value["version"] not in ("TLSv1.2", "TLSv1.3")
        or type(value["peer_sha256"]) is not str
        or re.fullmatch("[0-9a-f]{64}", value["peer_sha256"]) is None
    ):
        raise PublicReceiptError("runtime_tls_invalid")
    return True


def _routed_request_headers(plan, role):
    """Public-only header receipt expected from one reviewed Demo route."""
    from app.trade_qualification import demo_public_origin as demo_origin

    route = demo_origin.reviewed_demo_public_route(plan["registration_region"])
    if plan["rest_origin"] != route.rest_origin:
        raise PublicReceiptError("runtime_demo_request_headers_invalid")
    headers = demo_origin.demo_public_headers(route, role)
    headers["Host"] = route.rest_hostname
    return [
        [name.lower(), value]
        for name, value in sorted(headers.items(), key=lambda pair: pair[0].lower())
    ]


def _replay_semantics(plan, summary, events, payloads):
    """Audit integrity only; a forged/replayed document never issues a carrier."""
    requests, websocket, observations = {}, {}, []
    boundary = _plan_start(plan)
    last = boundary
    complete = summary["disposition"] == "captured"
    before_seen, after_seen = False, False
    for event, raw in zip(events, payloads, strict=True):
        kind, meta = event["kind"], event["metadata"]
        stamp = None
        if kind.startswith("clock_"):
            from app.public_market_source.public_clock import replay_clock_observation

            value = replay_clock_observation(raw)
            if kind == "clock_before":
                if event["index"] != 0:
                    raise PublicReceiptError("runtime_clock_inventory_invalid")
                before_seen = value["outcome"] == "accepted"
            elif not before_seen:
                raise PublicReceiptError("runtime_clock_inventory_invalid")
            if value["outcome"] == "accepted":
                evidence = value["observation"]
                if evidence["schema_version"] != "ctcc.windows_clock_observation.v2":
                    raise PublicReceiptError("runtime_native_clock_version_required")
                validate_stamps(
                    (
                        last,
                        evidence["diagnostic"]["host_before"]["request_start"],
                        evidence["sample"],
                    )
                )
                stamp = evidence["sample"]
            elif complete:
                raise PublicReceiptError("runtime_capture_incomplete")
            if kind == "clock_after":
                after_seen = True
        elif kind == "request":
            routed = (
                plan["schema_version"]
                in {
                    "ctcc.public.initial_runtime_plan.v2",
                    "ctcc.public.runtime_plan.v2",
                }
                and "registration_region" in plan
            )
            expected_keys = {
                "id",
                "role",
                "method",
                "origin",
                "endpoint",
                "query",
                "started",
            }
            if routed:
                expected_keys.add("request_headers")
            if (
                set(meta) != expected_keys
                or type(meta["id"]) is not int
                or meta["id"] != len(requests)
                or len(requests) >= 64
                or meta["method"] != "GET"
                or meta["origin"] != plan["rest_origin"]
                or meta["role"] not in {"quote", "candles", "market_aux"}
                or not before_seen
                or after_seen
            ):
                raise PublicReceiptError("runtime_request_invalid")
            paths = {
                "quote": {
                    "/api/v5/market/ticker",
                    "/api/v5/public/mark-price",
                    "/api/v5/public/funding-rate",
                },
                "candles": {"/api/v5/market/candles"},
                "market_aux": {"/api/v5/market/books", "/api/v5/public/open-interest"},
            }
            pairs = meta["query"]
            if (
                type(pairs) is not list
                or any(
                    type(p) is not list
                    or len(p) != 2
                    or any(type(v) is not str for v in p)
                    for p in pairs
                )
                or len(dict(pairs)) != len(pairs)
                or dict(pairs).get("instId") != plan["instrument_id"]
                or meta["endpoint"] not in paths[meta["role"]]
                or raw is not None
            ):
                raise PublicReceiptError("runtime_request_invalid")
            if routed and meta["request_headers"] != _routed_request_headers(
                plan, meta["role"]
            ):
                raise PublicReceiptError("runtime_demo_request_headers_invalid")
            stamp = meta["started"]
            requests[meta["id"]] = {
                "request": meta,
                "body": bytearray(),
                "headers": None,
                "body_complete": None,
                "closed": None,
                "failed": False,
                "truncated": False,
            }
        elif kind in {
            "headers",
            "chunk",
            "body_complete",
            "response_closed",
            "request_failed",
        }:
            if (
                type(meta.get("id")) is not int
                or meta["id"] not in requests
                or after_seen
            ):
                raise PublicReceiptError("runtime_response_without_request")
            item = requests[meta["id"]]
            if item["failed"] or item["closed"] is not None:
                raise PublicReceiptError("runtime_response_after_terminal")
            if kind == "headers":
                if (
                    set(meta)
                    != {"id", "received", "status", "tls", "safe_headers", "truncated"}
                    or item["headers"] is not None
                    or raw is not None
                    or type(meta["status"]) is not int
                    or type(meta["truncated"]) is not bool
                ):
                    raise PublicReceiptError("runtime_headers_invalid")
                verified = _tls(meta["tls"], urlsplit(plan["rest_origin"]).hostname)
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
                ):
                    raise PublicReceiptError("runtime_headers_invalid")
                if complete and (
                    not verified
                    or meta["status"] != 200
                    or meta["truncated"]
                    or len(dict(headers)) != len(headers)
                ):
                    raise PublicReceiptError("runtime_headers_rejected")
                if complete:
                    fields = dict(headers)
                    if (
                        fields.get("content-type", "").split(";", 1)[0].strip().lower()
                        != "application/json"
                        or fields.get("content-encoding", "identity").lower()
                        != "identity"
                    ):
                        raise PublicReceiptError("runtime_headers_rejected")
                item["headers"] = meta
                stamp = meta["received"]
            elif kind == "chunk":
                if (
                    set(meta)
                    != {
                        "id",
                        "received",
                        "observed_bytes",
                        "observed_sha256",
                        "truncated",
                    }
                    or item["headers"] is None
                    or item["body_complete"] is not None
                    or raw is None
                    or type(meta["observed_bytes"]) is not int
                    or meta["observed_bytes"] < len(raw)
                    or type(meta["truncated"]) is not bool
                    or meta["truncated"] != (meta["observed_bytes"] != len(raw))
                    or type(meta["observed_sha256"]) is not str
                    or re.fullmatch("[0-9a-f]{64}", meta["observed_sha256"]) is None
                    or (not meta["truncated"] and meta["observed_sha256"] != sha(raw))
                ):
                    raise PublicReceiptError("runtime_chunk_invalid")
                item["body"].extend(raw)
                item["truncated"] |= meta["truncated"]
                stamp = meta["received"]
            elif kind == "body_complete":
                if (
                    set(meta) != {"id", "completed", "body_bytes", "body_sha256"}
                    or item["headers"] is None
                    or item["body_complete"] is not None
                    or item["truncated"]
                    or raw is not None
                    or type(meta["body_bytes"]) is not int
                    or meta["body_bytes"] != len(item["body"])
                    or meta["body_sha256"] != sha(bytes(item["body"]))
                ):
                    raise PublicReceiptError("runtime_body_invalid")
                if complete:
                    length = dict(item["headers"]["safe_headers"]).get("content-length")
                    if length is not None and (
                        not length.isascii()
                        or not length.isdigit()
                        or len(length) > 10
                        or int(length) != len(item["body"])
                    ):
                        raise PublicReceiptError("runtime_body_length_mismatch")
                item["body_complete"] = meta
                stamp = meta["completed"]
            elif kind == "response_closed":
                if (
                    set(meta) != {"id", "completed"}
                    or item["body_complete"] is None
                    or raw is not None
                ):
                    raise PublicReceiptError("runtime_close_invalid")
                item["closed"] = meta["completed"]
                stamp = meta["completed"]
            else:
                if (
                    set(meta) != {"id", "code"}
                    or type(meta["code"]) is not str
                    or meta["code"] not in _REQUEST_FAILURE_CODES
                    or raw is not None
                    or complete
                ):
                    raise PublicReceiptError("runtime_failure_invalid")
                item["failed"] = True
        elif kind.startswith("ws_"):
            stages = (
                "ws_connect",
                "ws_connected",
                "ws_subscribe",
                "ws_subscribed",
                "ws_ack",
                "ws_ticker",
                "ws_closed",
            )
            if (
                len(websocket) >= len(stages)
                or kind != stages[len(websocket)]
                or not before_seen
                or after_seen
            ):
                raise PublicReceiptError("runtime_ws_order_invalid")
            if kind == "ws_connect":
                if (
                    set(meta) != {"endpoint", "started"}
                    or meta["endpoint"] != plan["ws_origin"]
                    or raw is not None
                ):
                    raise PublicReceiptError("runtime_ws_scope_invalid")
                stamp = meta["started"]
            elif kind == "ws_connected":
                if (
                    set(meta) != {"connected", "tls"}
                    or not _tls(meta["tls"], urlsplit(plan["ws_origin"]).hostname)
                    or raw is not None
                ):
                    raise PublicReceiptError("runtime_ws_tls_invalid")
                stamp = meta["connected"]
            elif kind in {"ws_ack", "ws_ticker"}:
                if (
                    set(meta)
                    != {
                        "observed",
                        "message_type",
                        "observed_bytes",
                        "observed_sha256",
                        "truncated",
                    }
                    or raw is None
                    or type(meta["observed_bytes"]) is not int
                    or meta["observed_bytes"] < len(raw)
                    or type(meta["truncated"]) is not bool
                    or meta["truncated"] != (len(raw) != meta["observed_bytes"])
                    or type(meta["observed_sha256"]) is not str
                    or re.fullmatch("[0-9a-f]{64}", meta["observed_sha256"]) is None
                    or (not meta["truncated"] and meta["observed_sha256"] != sha(raw))
                    or meta["message_type"] not in {"text", "binary"}
                ):
                    raise PublicReceiptError("runtime_ws_message_invalid")
                if complete and (
                    meta["truncated"] or meta["message_type"] != "text" or not raw
                ):
                    raise PublicReceiptError("runtime_ws_message_rejected")
                stamp = meta["observed"]
            else:
                if set(meta) != {"observed"} or (kind == "ws_subscribe") != (
                    raw is not None
                ):
                    raise PublicReceiptError("runtime_ws_event_invalid")
                stamp = meta["observed"]
            websocket[kind] = (stamp, raw)
        elif kind == "component_failed":
            if (
                complete
                or raw is not None
                or set(meta) != {"role", "code"}
                or meta["role"] not in {"quote", "candles", "market_aux", "ws"}
                or meta["code"] != "component_failed"
            ):
                raise PublicReceiptError("runtime_failure_invalid")
        elif kind == "packet":
            if (
                set(meta) != {"bundle_sha256"}
                or not after_seen
                or raw is None
                or observations
            ):
                raise PublicReceiptError("runtime_packet_invalid")
            observations.append(decode(raw, MAX_RAW))
            bundle_pin = (
                sha(raw)
                if plan["schema_version"]
                in {
                    "ctcc.public.initial_runtime_plan.v2",
                    "ctcc.public.runtime_plan.v2",
                }
                else observations[0].get("bundle_sha256")
            )
            if bundle_pin != meta["bundle_sha256"]:
                raise PublicReceiptError("runtime_packet_pin_mismatch")
        if stamp is not None:
            validate_stamps((last, stamp))
            if (
                stamp["utc_ns"] <= boundary["utc_ns"]
                or stamp["monotonic_ns"] <= boundary["monotonic_ns"]
            ):
                raise PublicReceiptError("runtime_before_barrier")
            last = stamp
        elif complete and kind not in {"packet"}:
            raise PublicReceiptError("runtime_missing_receipt_time")
    # A failure keeps absent receipt time explicitly absent. If a terminal time
    # is present, its causal ordering is still checked; a reversal leaves the
    # retained files unaccepted instead of laundering it as a valid failure log.
    if summary["completed"] is not None:
        validate_stamps((last, summary["completed"]))
    if complete:
        if (
            not requests
            or any(
                v["closed"] is None or v["failed"] or v["truncated"]
                for v in requests.values()
            )
            or len(websocket) != 7
            or len(observations) != 1
        ):
            raise PublicReceiptError("runtime_capture_incomplete")
        if utc_from_ns(summary["completed"]["utc_ns"]) >= _time(plan["expires_at"]):
            raise PublicReceiptError("runtime_capture_expired")
        _packet_join(plan, observations[0], requests, websocket)


def _packet_join(plan, packet, requests, websocket):
    if plan["schema_version"] in {
        "ctcc.public.initial_runtime_plan.v2",
        "ctcc.public.runtime_plan.v2",
    } and (
        packet.get("schema_version") != "ctcc.collected_public_market.v2"
        or any(
            packet.get(name) != plan[name]
            for name in (
                "stage",
                "invocation_id",
                "environment",
                "policy_sha256",
                "public_packet_schema",
                "quote_collector_schema",
                "quote_profile_sha256",
                "quote_transport_policy_sha256",
            )
        )
        or packet.get("quote", {}).get("schema_version")
        != plan["quote_collector_schema"]
    ):
        raise PublicReceiptError("runtime_v2_packet_plan_mismatch")
    if "registration_region" in plan and any(
        packet.get(name) != plan[name]
        for name in (
            "registration_region",
            "registration_region_authenticated",
            "account_plan_sha256",
            "demo_public_origin_policy_sha256",
            "rest_origin",
            "ws_origin",
        )
    ):
        raise PublicReceiptError("runtime_v2_route_packet_mismatch")
    if (
        packet.get("report_id") != plan["report_id"]
        or packet.get("instrument_id") != plan["instrument_id"]
        or not _packet_stage_matches(plan, packet["barrier_completed_at"])
    ):
        raise PublicReceiptError("runtime_packet_scope_mismatch")
    for flag in (
        "execution_authority",
        "source_authenticity_verified",
        "socket_binding_verified",
        "account_evidence_authenticated",
        "atomic_risk_reserved",
    ):
        if flag in packet and packet[flag] is not False:
            raise PublicReceiptError("runtime_authority_forbidden")
    records = [
        *packet["quote"]["provenance"],
        *(page for frame in packet["candles"]["frames"] for page in frame["pages"]),
        *packet["market_aux"]["provenance"],
    ]
    remaining = list(requests.values())
    for record in records:
        matched = [
            item
            for item in remaining
            if item["request"]["endpoint"] == record["endpoint"]
            and dict(item["request"]["query"]) == dict(record["parameters"])
            and bytes(item["body"]) == record["response_body"].encode("utf-8")
            and utc_from_ns(item["request"]["started"]["utc_ns"])
            == _time(record["request_started_at"])
            and utc_from_ns(item["headers"]["received"]["utc_ns"])
            == _time(record["received_at"])
            and utc_from_ns(item["closed"]["utc_ns"]) == _time(record["completed_at"])
        ]
        if len(matched) != 1:
            raise PublicReceiptError("runtime_packet_raw_join_mismatch")
        remaining.remove(matched[0])
    if remaining:
        raise PublicReceiptError("runtime_packet_inventory_mismatch")
    wire = packet["ws"]
    expected = {
        "ws_connect": (wire["connection_started_at"], None),
        "ws_connected": (wire["connected_at"], None),
        "ws_subscribe": (
            wire["subscribe_started_at"],
            wire["subscription_body"].encode("utf-8"),
        ),
        "ws_subscribed": (wire["subscribe_completed_at"], None),
        "ws_ack": (
            wire["ack"]["received_at"],
            wire["ack"]["raw_frame"].encode("utf-8"),
        ),
        "ws_ticker": (
            wire["ticker"]["received_at"],
            wire["ticker"]["raw_frame"].encode("utf-8"),
        ),
        "ws_closed": (wire["closed_at"], None),
    }
    if any(
        (utc_from_ns(websocket[k][0]["utc_ns"]), websocket[k][1]) != (_time(v[0]), v[1])
        for k, v in expected.items()
    ):
        raise PublicReceiptError("runtime_packet_ws_join_mismatch")
