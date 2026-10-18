"""DENY-only, immutable Demo FOK request plan from a replayed durable intent.

This module performs no credential access, signing, network IO, or order write.
The plan is an exact-byte proposal for a future controlled transport, not a
permit. A caller can construct or copy the value, so it must never be accepted
as execution authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

from app.trade_qualification import control_bound_ledger
from app.trade_qualification.reservations import ReservationRequest
from app.trade_qualification.submission_intent import VERSION_V2

VERSION = "ctcc.demo_fok_intent_request_plan.v1"
_METHOD = "POST"
_PATH = "/api/v5/trade/order"
_BODY_KEYS = frozenset(
    {
        "instId",
        "tdMode",
        "clOrdId",
        "side",
        "posSide",
        "ordType",
        "sz",
        "px",
        "attachAlgoOrds",
    }
)
_PROTECTION_KEYS = frozenset(
    {
        "attachAlgoClOrdId",
        "tpTriggerPx",
        "tpOrdPx",
        "tpTriggerPxType",
        "slTriggerPx",
        "slOrdPx",
        "slTriggerPxType",
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DemoFokIntentRequestPlan:
    """Non-secret exact body bytes and headers; always DENY for execution."""

    intent_sha256: str
    logical_request_sha256: str
    body_bytes: bytes
    body_sha256: str
    client_order_id: str
    protection_client_order_id: str
    exp_time: str
    safe_headers: tuple[tuple[str, str], ...]
    method: Literal["POST"] = field(default=_METHOD, init=False)
    path: Literal["/api/v5/trade/order"] = field(default=_PATH, init=False)
    environment: Literal["demo"] = field(default="demo", init=False)
    version: Literal["ctcc.demo_fok_intent_request_plan.v1"] = field(
        default=VERSION, init=False
    )
    admission: Literal["DENY"] = field(default="DENY", init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    order_retry_authority: Literal[False] = field(default=False, init=False)
    order_submitted: Literal[False] = field(default=False, init=False)


def plan_demo_fok_from_control_bound_intent(
    request: ReservationRequest,
    committed: control_bound_ledger.ControlBoundSubmissionIntentRecord,
) -> DemoFokIntentRequestPlan:
    """Replay the entire control-bound record before deriving any wire fields.

    The body encoding matches the order-create branch of OKX private REST:
    ``sort_keys=True``, compact separators, UTF-8, and ``ensure_ascii=False``.
    An account UID, credential, host, signature, or current Arm is not attested
    by this historical plan; every dispatch boundary remains hard DENY.
    """
    try:
        if (
            type(committed)
            is not control_bound_ledger.ControlBoundSubmissionIntentRecord
        ):
            raise ValueError
        replayed, consumed = control_bound_ledger.replay_control_bound_intent(
            committed.canonical_json,
            request,
            expected_sha256=committed.sha256,
        )
        outer = json.loads(replayed.canonical_json)
        inner = json.loads(outer["intent_json"])
        logical = inner["exchange_request"]
        headers = logical["headers"]
        body = logical["body"]
        attached = body["attachAlgoOrds"]
        expected_client = "CTQ" + consumed.reservation_id[:29]
        expected_protection = "CTA" + consumed.reservation_id[:29]
        if (
            inner["version"] != VERSION_V2
            or inner["execution_authority"] is not False
            or inner["order_retry_authority"] is not False
            or inner["order_submitted"] is not False
            or inner["source_authenticity_verified"] is not False
            or inner["account_complete"] is not False
            or logical["method"] != _METHOD
            or logical["path"] != _PATH
            or logical["environment"] != "demo"
            or logical["account_uid"] != request.scope.account_id
            or type(headers) is not dict
            or headers.keys() != {"x-simulated-trading", "expTime"}
            or headers["x-simulated-trading"] != "1"
            or type(headers["expTime"]) is not str
            or not headers["expTime"].isdecimal()
            or type(body) is not dict
            or body.keys() != _BODY_KEYS
            or body["instId"] != consumed.instrument_id
            or body["tdMode"] != "isolated"
            or body["ordType"] != "fok"
            or body["clOrdId"] != expected_client
            or inner["client_order_id"] != expected_client
            or type(attached) is not list
            or len(attached) != 1
            or type(attached[0]) is not dict
            or attached[0].keys() != _PROTECTION_KEYS
            or attached[0]["attachAlgoClOrdId"] != expected_protection
            or inner["protection_client_order_id"] != expected_protection
        ):
            raise ValueError
        body_bytes = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return DemoFokIntentRequestPlan(
            intent_sha256=replayed.sha256,
            logical_request_sha256=inner["exchange_request_sha256"],
            body_bytes=body_bytes,
            body_sha256=hashlib.sha256(body_bytes).hexdigest(),
            client_order_id=expected_client,
            protection_client_order_id=expected_protection,
            exp_time=headers["expTime"],
            safe_headers=(
                ("Content-Type", "application/json"),
                ("x-simulated-trading", "1"),
                ("expTime", headers["expTime"]),
            ),
        )
    except Exception:  # noqa: BLE001 - never echo private or foreign record contents
        raise ValueError("demo_fok_intent_request_plan_invalid") from None
