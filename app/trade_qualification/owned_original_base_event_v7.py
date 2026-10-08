"""Source-owned base G1--G5 inspection, always DENY.

Only the private same-task coordinator retains raw packets. This hash-only
diagnostic cannot resume G6, publish G12, reserve risk, or submit an order.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import original_candidate_precursor_v2 as precursor
from app.trade_qualification import original_source_coordinator_v2 as original
from app.trade_qualification import owned_original_base_prefix_v6 as base

_SCHEMA = "ctcc.owned_original_base_event_boundary.v7"
_EVENT_SCHEMA = "ctcc.owned_original_base_event_inspection.v7"
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_MAX_BYTES = 2048
_MAX_EVENT_BYTES = 4096
_FALSE_FIELDS = (
    *base._FALSE_FIELDS,
    "historical_first_availability_verified",
    "event_ledger_authenticated",
    "g6_evaluated",
    "g7_evaluated",
)
_EVENT_FALSE_FIELDS = (*_FALSE_FIELDS, "calibrated_for_trading")
_FIELDS = frozenset(
    {
        "schema_version",
        "code",
        "base_diagnostic_sha256",
        "original_diagnostic_sha256",
        "public_packet_sha256",
        "account_packet_sha256",
        "precursor_receipt_sha256",
        "precursor_intent_sha256",
        "base_prefix_receipt_sha256",
        "event_receipt_sha256",
        "event_key_sha256",
        "g1_g5_replayed",
        "admission",
        *_FALSE_FIELDS,
    }
)
_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "precursor_receipt_sha256",
        "precursor_intent_sha256",
        "public_packet_sha256",
        "base_prefix_receipt_sha256",
        "g1_result_sha256",
        "g1_source_sha256",
        "detection_sha256",
        "event_key_sha256",
        "timeline_sha256",
        "event_prefix_witness_sha256",
        "trigger_expires_at",
        "gate",
        "admission",
        *_EVENT_FALSE_FIELDS,
    }
)


class OwnedOriginalBaseEventError(ValueError):
    """Static denial code; never echoes raw market or account values."""


@dataclass(frozen=True, slots=True, repr=False)
class OwnedOriginalBaseEventDiagnosticV7:
    receipt_json: bytes

    def __post_init__(self):
        try:
            if (
                type(self.receipt_json) is not bytes
                or not 0 < len(self.receipt_json) <= _MAX_BYTES
            ):
                raise ValueError
            record = decode(self.receipt_json, _MAX_BYTES)
            if (
                type(record) is not dict
                or set(record) != _FIELDS
                or canonical(record) != self.receipt_json
                or record["schema_version"] != _SCHEMA
                or record["code"]
                not in {
                    "native_precursor_unavailable",
                    "precursor_no_intent",
                    "base_g1_g5_inspected",
                }
                or record["admission"] != "DENY"
                or any(record[name] is not False for name in _FALSE_FIELDS)
                or type(record["g1_g5_replayed"]) is not bool
                or record["g1_g5_replayed"]
                is not (record["code"] == "base_g1_g5_inspected")
            ):
                raise ValueError
            for name in (
                "base_diagnostic_sha256",
                "original_diagnostic_sha256",
                "public_packet_sha256",
                "account_packet_sha256",
                "precursor_receipt_sha256",
                "precursor_intent_sha256",
                "base_prefix_receipt_sha256",
                "event_receipt_sha256",
                "event_key_sha256",
            ):
                value = record[name]
                if value is not None and (
                    type(value) is not str or _HEX.fullmatch(value) is None
                ):
                    raise ValueError
            if (
                record["base_diagnostic_sha256"] is None
                or record["original_diagnostic_sha256"] is None
            ):
                raise ValueError
            if record["g1_g5_replayed"]:
                if any(
                    record[name] is None
                    for name in (
                        "public_packet_sha256",
                        "account_packet_sha256",
                        "precursor_receipt_sha256",
                        "precursor_intent_sha256",
                        "base_prefix_receipt_sha256",
                        "event_receipt_sha256",
                        "event_key_sha256",
                    )
                ):
                    raise ValueError
            elif (
                record["event_receipt_sha256"] is not None
                or record["event_key_sha256"] is not None
            ):
                raise ValueError
        except Exception:  # noqa: BLE001 -- bounded source-independent error
            raise OwnedOriginalBaseEventError(
                "owned_base_event_receipt_invalid"
            ) from None

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def admission(self) -> Literal["DENY"]:
        return "DENY"

    @property
    def execution_authority(self) -> Literal[False]:
        return False


async def preflight_owned_base_event_v7(
    public_root,
    account_root,
    *,
    instrument_id,
    strategy,
    market_policy,
    account_session,
    session_factory,
) -> OwnedOriginalBaseEventDiagnosticV7:
    """Replay native base G1--G5; caller cannot supply a gate, event or PASS."""
    if type(strategy) is not str or strategy not in precursor._BASE_STRATEGIES:
        raise OwnedOriginalBaseEventError("owned_base_event_strategy_unsupported")
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    handoff = await original._capture_owned_original_base_event_for_boundary_v7(
        public_root,
        account_root,
        instrument_id=instrument_id,
        strategy=strategy,
        market_policy=market_policy,
        account_session=account_session,
        session_factory=session_factory,
    )
    if task is not asyncio.current_task() or task.cancelling():
        raise asyncio.CancelledError
    if type(handoff) is not original._OwnedBaseEventHandoffV7:
        raise OwnedOriginalBaseEventError("owned_base_event_handoff_required")
    base_diagnostic = base._readback_owned_base_prefix_handoff_v6(
        handoff.base, account_session
    )
    base_record = decode(base_diagnostic.receipt_json, base._MAX_BYTES)
    event = handoff.event_receipt_json
    expected = base_record["code"] == "base_g1_g4_inspected"
    if expected is not (event is not None):
        raise OwnedOriginalBaseEventError("owned_base_event_inspection_missing")
    if event is not None:
        if type(event) is not bytes or not 0 < len(event) <= _MAX_EVENT_BYTES:
            raise OwnedOriginalBaseEventError("owned_base_event_inspection_invalid")
        checked = decode(event, _MAX_EVENT_BYTES)
        if (
            type(checked) is not dict
            or set(checked) != _EVENT_FIELDS
            or canonical(checked) != event
            or checked.get("schema_version") != _EVENT_SCHEMA
            or checked.get("precursor_receipt_sha256")
            != base_record["precursor_receipt_sha256"]
            or checked.get("precursor_intent_sha256")
            != base_record["precursor_intent_sha256"]
            or checked.get("public_packet_sha256")
            != base_record["public_packet_sha256"]
            or checked.get("base_prefix_receipt_sha256")
            != base_record["base_prefix_receipt_sha256"]
            or checked.get("gate") != {"gate": "G5", "code": "passed", "passed": True}
            or checked.get("admission") != "DENY"
            or any(checked.get(name) is not False for name in _EVENT_FALSE_FIELDS)
        ):
            raise OwnedOriginalBaseEventError("owned_base_event_inspection_invalid")
        for name in (
            "g1_result_sha256",
            "g1_source_sha256",
            "detection_sha256",
            "event_key_sha256",
            "timeline_sha256",
            "event_prefix_witness_sha256",
        ):
            value = checked.get(name)
            if type(value) is not str or _HEX.fullmatch(value) is None:
                raise OwnedOriginalBaseEventError("owned_base_event_inspection_invalid")
        try:
            expires = datetime.fromisoformat(checked["trigger_expires_at"])
            if expires.tzinfo is None or expires.utcoffset() != UTC.utcoffset(None):
                raise ValueError
        except (TypeError, ValueError):
            raise OwnedOriginalBaseEventError(
                "owned_base_event_inspection_invalid"
            ) from None
    code = "base_g1_g5_inspected" if event is not None else base_record["code"]
    return OwnedOriginalBaseEventDiagnosticV7(
        canonical(
            {
                "schema_version": _SCHEMA,
                "code": code,
                "base_diagnostic_sha256": base_diagnostic.receipt_sha256,
                "original_diagnostic_sha256": base_record["original_diagnostic_sha256"],
                "public_packet_sha256": base_record["public_packet_sha256"],
                "account_packet_sha256": base_record["account_packet_sha256"],
                "precursor_receipt_sha256": base_record["precursor_receipt_sha256"],
                "precursor_intent_sha256": base_record["precursor_intent_sha256"],
                "base_prefix_receipt_sha256": base_record["base_prefix_receipt_sha256"],
                "event_receipt_sha256": None if event is None else sha(event),
                "event_key_sha256": None
                if event is None
                else checked["event_key_sha256"],
                "g1_g5_replayed": event is not None,
                "admission": "DENY",
                **{name: False for name in _FALSE_FIELDS},
            }
        )
    )
