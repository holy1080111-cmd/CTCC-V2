"""Read-only temporal diagnosis of a sealed public runtime journal.

The exchange's market-content timestamp and this host's HTTP/WS observation
times answer different questions. A newly dispatched GET can return content
generated before dispatch. This versioned inspection preserves that distinction
without changing V1/V2 wire receipts or issuing a source/trading capability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.source_primitives import (
    PublicReceiptError,
    canonical,
    decode,
    sha,
    validate_stamps,
)
from app.public_market_source.public_runtime_journal import (
    MAX_RAW,
    replay_runtime_attempt,
)

SCHEMA_VERSION = "ctcc.public.temporal_evidence.v2"
_POST_BARRIER_GENERATION_ROLES = frozenset({"ticker", "books", "ws_ticker"})
_SEMANTICS = {
    "ticker": "ticker_generation",
    "mark": "exchange_data_return",
    "funding": "exchange_data_return",
    "books": "book_generation",
    "open_interest": "exchange_data_return",
    "ws_ticker": "ticker_generation",
}
_ENDPOINTS = {
    "/api/v5/market/ticker": "ticker",
    "/api/v5/public/mark-price": "mark",
    "/api/v5/public/funding-rate": "funding",
    "/api/v5/market/books": "books",
    "/api/v5/public/open-interest": "open_interest",
}
_PROFILE = {
    "schema_version": "ctcc.public.temporal_policy.v2",
    "post_barrier_generated_content_roles": sorted(_POST_BARRIER_GENERATION_ROLES),
    "source_age_basis": "packet_preseal_stamp_minus_immutable_content_ts",
    "http_phase_basis": "replayed_native_runtime_request_headers_body_eof_close",
    "ws_phase_basis": "replayed_native_connect_message_receipt_close",
    "barrier_basis": "new_dispatch_and_selected_generated_content",
    "authority": False,
}
PROFILE_SHA256 = sha(canonical(_PROFILE))


class PublicTemporalEvidenceError(ValueError):
    """Fixed local code, with no remote body or exception text."""


@dataclass(frozen=True, slots=True)
class PublicTemporalEvidenceV2:
    receipt_json: bytes

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def admission(self) -> str:
        return "DENY"


def _deny(code: str):
    raise PublicTemporalEvidenceError(code)


def _content_ns(raw: str) -> int:
    if type(raw) is not str or re.fullmatch(r"[1-9][0-9]{0,14}", raw) is None:
        _deny("temporal_content_timestamp_invalid")
    return int(raw) * 1_000_000


def _age_limit_seconds(role: str, packet: dict) -> int:
    if role in {"ticker", "mark"}:
        return 5  # Frozen executable_quote_v2 policy, not a new tolerance.
    if role == "funding":
        return 90  # Frozen executable_quote_v2 funding return policy.
    name = "ws" if role == "ws_ticker" else "market_aux"
    value = packet["policy"][name]["max_age_seconds"]
    if type(value) is not int or not 1 <= value <= 60:
        _deny("temporal_source_age_policy_invalid")
    return value


def _assess_component(
    *,
    role: str,
    ts_raw: str,
    raw_sha256: str,
    dispatch: dict,
    receipt: dict,
    eof: dict | None,
    closed: dict,
    validation_upper_bound: dict,
    barrier: dict | None,
    max_age_seconds: int,
) -> dict:
    """Pure replay check; passed stamps alone never prove native ownership."""
    if role not in _SEMANTICS:
        _deny("temporal_role_invalid")
    if type(raw_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", raw_sha256) is None:
        _deny("temporal_raw_hash_invalid")
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 90:
        _deny("temporal_source_age_policy_invalid")
    if (role == "ws_ticker") != (eof is None):
        _deny("temporal_phase_shape_invalid")
    stamps = [dispatch, receipt]
    if eof is not None:
        stamps.append(eof)
    stamps.extend((closed, validation_upper_bound))
    try:
        validate_stamps(stamps)
        if barrier is not None:
            validate_stamps((barrier, dispatch))
    except (ValueError, TypeError, KeyError, ArithmeticError):
        _deny("temporal_native_clock_invalid")
    content_ns = _content_ns(ts_raw)
    if content_ns > receipt["utc_ns"]:
        _deny("temporal_source_after_receipt")
    if validation_upper_bound["utc_ns"] - content_ns > max_age_seconds * 1_000_000_000:
        _deny("temporal_source_stale")
    if barrier is not None and (
        dispatch["utc_ns"] <= barrier["utc_ns"]
        or dispatch["monotonic_ns"] <= barrier["monotonic_ns"]
    ):
        _deny("temporal_dispatch_before_barrier")

    classification = (
        "pre_request_cached_content"
        if content_ns < dispatch["utc_ns"]
        else "content_timestamp_within_request"
    )
    required = barrier is not None and role in _POST_BARRIER_GENERATION_ROLES
    generated_after_barrier = (
        None if barrier is None else content_ns > barrier["utc_ns"]
    )
    return {
        "role": role,
        "timestamp_semantics": _SEMANTICS[role],
        "content_timestamp_has_generation_semantics": _SEMANTICS[role]
        in {"ticker_generation", "book_generation"},
        "content_ts_raw": ts_raw,
        "content_ts_utc_ns": content_ns,
        "content_relation_to_request": classification,
        "raw_sha256": raw_sha256,
        "dispatch": dispatch,
        "headers_or_message_received": receipt,
        "http_body_eof": eof,
        "closed": closed,
        "packet_preseal_stamp": validation_upper_bound,
        "barrier": barrier,
        "content_after_barrier": generated_after_barrier,
        "post_barrier_generation_required": required,
        "post_barrier_generation_satisfied": (
            None if not required else generated_after_barrier
        ),
        "source_age_limit_seconds": max_age_seconds,
    }


def inspect_runtime_temporal_evidence_v2(
    directory, *, expected_plan_sha256: str
) -> PublicTemporalEvidenceV2:
    """Replay the full sealed runtime chain before classifying content time.

    Journal bytes remain a self-consistent historical claim, not independent
    proof of TLS, native execution, origin, or a current execution permit.
    """
    try:
        summary, events = replay_runtime_attempt(
            directory, expected_plan_sha256=expected_plan_sha256
        )
        if summary["disposition"] != "captured":
            _deny("temporal_runtime_capture_incomplete")
        plan_raw = directory.read("plan.json", MAX_RAW)
        if sha(plan_raw) != expected_plan_sha256:
            _deny("temporal_plan_readback_changed")
        plan = decode(plan_raw)
        if plan["schema_version"] not in {
            "ctcc.public.initial_runtime_plan.v2",
            "ctcc.public.runtime_plan.v2",
        }:
            _deny("temporal_v2_runtime_required")
        packet_raw = directory.read(f"event-{events[-1]['index']:04d}.raw", MAX_RAW)
        if sha(packet_raw) != summary["packet_sha256"]:
            _deny("temporal_packet_readback_changed")
        packet = decode(packet_raw, MAX_RAW)
        if packet["schema_version"] != "ctcc.collected_public_market.v2":
            _deny("temporal_v2_packet_required")
        barrier = plan["barrier"] if plan["stage"] == "post_publication" else None
        completed = summary["completed"]
        if completed is None:
            _deny("temporal_validation_time_missing")

        request_events: dict[int, dict] = {}
        ws_events: dict[str, dict] = {}
        for event in events:
            kind, metadata = event["kind"], event["metadata"]
            if kind == "request":
                request_events[metadata["id"]] = {"request": metadata}
            elif kind in {"headers", "body_complete", "response_closed"}:
                request_events[metadata["id"]][kind] = metadata
            elif kind in {"ws_connect", "ws_connected", "ws_ticker", "ws_closed"}:
                ws_events[kind] = {**metadata, "raw_sha256": event["raw_sha256"]}

        records = [
            *packet["quote"]["provenance"],
            *packet["market_aux"]["provenance"],
        ]
        if len(records) != 5:
            _deny("temporal_component_inventory_invalid")
        inspections = []
        remaining = dict(request_events)
        for record in records:
            role = _ENDPOINTS.get(record["endpoint"])
            if role is None or role != record["role"]:
                _deny("temporal_component_role_invalid")
            matches = [
                index
                for index, item in remaining.items()
                if item["request"]["endpoint"] == record["endpoint"]
                and item["request"]["query"]
                == [list(pair) for pair in record["parameters"]]
                and item["body_complete"]["body_sha256"] == record["body_sha256"]
            ]
            if len(matches) != 1:
                _deny("temporal_native_raw_join_invalid")
            item = remaining.pop(matches[0])
            inspections.append(
                _assess_component(
                    role=role,
                    ts_raw=record["ts_raw"],
                    raw_sha256=record["body_sha256"],
                    dispatch=item["request"]["started"],
                    receipt=item["headers"]["received"],
                    eof=item["body_complete"]["completed"],
                    closed=item["response_closed"]["completed"],
                    validation_upper_bound=completed,
                    barrier=barrier,
                    max_age_seconds=_age_limit_seconds(role, packet),
                )
            )
        if any(item["request"]["role"] != "candles" for item in remaining.values()):
            _deny("temporal_native_raw_join_invalid")
        ticker = packet["ws"]["ticker"]
        if ws_events["ws_ticker"]["raw_sha256"] != ticker["frame_sha256"]:
            _deny("temporal_ws_raw_join_invalid")
        inspections.append(
            _assess_component(
                role="ws_ticker",
                ts_raw=ticker["ts_raw"],
                raw_sha256=ticker["frame_sha256"],
                dispatch=ws_events["ws_connect"]["started"],
                receipt=ws_events["ws_ticker"]["observed"],
                eof=None,
                closed=ws_events["ws_closed"]["observed"],
                validation_upper_bound=completed,
                barrier=barrier,
                max_age_seconds=_age_limit_seconds("ws_ticker", packet),
            )
        )
        summary_raw = directory.read("summary.json", MAX_RAW)
        if summary_raw != canonical(summary):
            _deny("temporal_summary_readback_changed")
        result = {
            "schema_version": SCHEMA_VERSION,
            "profile_sha256": PROFILE_SHA256,
            "plan_sha256": expected_plan_sha256,
            "runtime_summary_sha256": sha(summary_raw),
            "packet_sha256": summary["packet_sha256"],
            "stage": plan["stage"],
            "components": inspections,
            "post_barrier_generation_required_roles": sorted(
                _POST_BARRIER_GENERATION_ROLES if barrier is not None else ()
            ),
            "post_barrier_generation_satisfied": None
            if barrier is None
            else all(
                item["post_barrier_generation_satisfied"] is not False
                for item in inspections
            ),
            "generation_semantics_unproven_roles": sorted(
                item["role"]
                for item in inspections
                if not item["content_timestamp_has_generation_semantics"]
            ),
            "per_component_online_validation_instant": None,
            "online_validation_time_semantics": (
                "packet_checked_before_preseal_stamp;"
                "journal_readback_validation_after_stamp_unmeasured"
            ),
            "journal_native_origin_independently_attested": False,
            "source_authenticity_verified": False,
            "execution_authority": False,
            "admission": "DENY",
        }
        return PublicTemporalEvidenceV2(canonical(result))
    except PublicTemporalEvidenceError:
        raise
    except (
        PublicReceiptError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        OSError,
        RecursionError,
    ):
        raise PublicTemporalEvidenceError("temporal_runtime_replay_denied") from None
