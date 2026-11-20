"""Failure-only synthetic fixture diagnostics; no raw values or admission."""

import json
import re

_EVENT = re.compile(r"event-[0-9]{4}\.json\Z")
_ROLES = frozenset({"quote", "candles", "market_aux", "ws", "time_pre", "time_post"})
_CODES = frozenset(
    {
        "cancelled",
        "source_or_transport_rejected",
        "component_failed",
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
)


def retained_public_failure_stage_v1(content):
    """Inspect only fixed codes/counts after a test capture has already refused."""
    result = {
        "schema": "ctcc.synthetic_raw_capture_failure_stage.v1",
        "execution_authority": False,
        "journal_parse_complete": False,
    }
    if type(content) is not dict or len(content) > 8194:
        return result
    result["plan_retained"] = "plan.json" in content
    roles, kinds, failures = {}, {}, []
    try:
        summary_raw = content.get("summary.json")
        if type(summary_raw) is bytes and len(summary_raw) <= 65536:
            summary = json.loads(summary_raw)
            disposition = summary.get("disposition")
            result["journal_disposition"] = (
                disposition if disposition in {"captured", "rejected"} else "unknown"
            )
        for name in sorted(content):
            if type(name) is not str or _EVENT.fullmatch(name) is None:
                continue
            raw = content[name]
            if type(raw) is not bytes or len(raw) > 4 * 1024 * 1024:
                return result
            event = json.loads(raw)
            kind, meta = event.get("kind"), event.get("metadata", {})
            if kind not in _KINDS or type(meta) is not dict:
                return result
            kinds[kind] = kinds.get(kind, 0) + 1
            result["last_event_kind"] = kind
            if kind == "request" and meta.get("role") in _ROLES:
                role = meta["role"]
                roles[role] = roles.get(role, 0) + 1
            if kind in {"component_failed", "request_failed"}:
                code = meta.get("code")
                failures.append(
                    {
                        "kind": kind,
                        "role": meta.get("role")
                        if meta.get("role") in _ROLES
                        else "unknown",
                        "code": code if code in _CODES else "unreported",
                    }
                )
        result.update(
            request_role_counts=roles,
            event_kind_counts=kinds,
            failure_stages=failures[:16],
            journal_parse_complete=True,
        )
    except (ValueError, TypeError, AttributeError):
        return result
    return result
