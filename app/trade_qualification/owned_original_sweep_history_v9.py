"""Same-invocation V6 sweep history lineage, always WAIT/DENY.

Only the private original-source coordinator sees raw public/account packets.
The returned digest receipt is not a candidate, past availability proof or
permission to publish, reserve risk or submit an order.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import original_source_coordinator_v2 as original
from app.trade_qualification.sweep_history_permission import POLICY_SHA256

_SCHEMA = "ctcc.owned_original_sweep_history_boundary.v9"
_INNER_SCHEMA = "ctcc.owned_original_sweep_history_inspection.v9"
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_MAX_BYTES = 2048
_MAX_INNER_BYTES = 4096
_FALSE_FIELDS = (
    "historical_first_availability_verified",
    "original_source_verified",
    "candidate_created",
    "g1_g11_complete",
    "g12_published",
    "account_complete",
    "execution_recheck_performed",
    "atomic_risk_reserved",
    "execution_authority",
    "order_submitted",
)
_INNER_FIELDS = frozenset(
    {
        "schema_version",
        "precursor_receipt_sha256",
        "public_packet_sha256",
        "account_packet_sha256",
        "account_plan_sha256",
        "g1_evaluation_sha256",
        "g1_source_sha256",
        "sweep_policy_sha256",
        "sweep_permission_sha256",
        "sweep_code",
        "sweep_admitted",
        "event_key_sha256",
        "intent_sha256",
        "event_expires_at",
        "event_prefix_witness_sha256",
        "admission",
        *_FALSE_FIELDS,
    }
)
_FIELDS = frozenset(
    {
        "schema_version",
        "code",
        "original_diagnostic_sha256",
        "public_packet_sha256",
        "account_packet_sha256",
        "precursor_receipt_sha256",
        "sweep_inspection_sha256",
        "sweep_policy_sha256",
        "sweep_permission_sha256",
        "event_key_sha256",
        "intent_sha256",
        "event_expires_at",
        "history_evidence_replayed",
        "admission",
        *_FALSE_FIELDS,
    }
)


class OwnedOriginalSweepHistoryError(ValueError):
    """Bounded static errors; no private source or credential values."""


def _digest(value, *, optional=False):
    if optional and value is None:
        return
    if type(value) is not str or _HEX.fullmatch(value) is None:
        raise ValueError


def _utc(value):
    if value is None:
        return
    if type(value) is not str or len(value) > 40:
        raise ValueError
    at = datetime.fromisoformat(value)
    if at.tzinfo is not UTC or at.utcoffset() != UTC.utcoffset(None):
        raise ValueError


@dataclass(frozen=True, slots=True, repr=False)
class OwnedOriginalSweepHistoryDiagnosticV9:
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
                    "sweep_history_not_reached",
                    "sweep_history_rejected",
                    "sweep_history_observed_no_intent",
                    "sweep_history_observed_wait_pit",
                }
                or record["admission"] != "DENY"
                or any(record[name] is not False for name in _FALSE_FIELDS)
                or type(record["history_evidence_replayed"]) is not bool
                or record["history_evidence_replayed"]
                is not (record["sweep_inspection_sha256"] is not None)
            ):
                raise ValueError
            _digest(record["original_diagnostic_sha256"])
            _digest(record["sweep_policy_sha256"])
            if record["sweep_policy_sha256"] != POLICY_SHA256:
                raise ValueError
            for name in (
                "public_packet_sha256",
                "account_packet_sha256",
                "precursor_receipt_sha256",
                "sweep_inspection_sha256",
                "sweep_permission_sha256",
                "event_key_sha256",
                "intent_sha256",
            ):
                _digest(record[name], optional=True)
            _utc(record["event_expires_at"])
            if record["history_evidence_replayed"]:
                if any(
                    record[name] is None
                    for name in (
                        "public_packet_sha256",
                        "account_packet_sha256",
                        "precursor_receipt_sha256",
                        "sweep_permission_sha256",
                    )
                ) or record["code"] in {
                    "native_precursor_unavailable",
                    "sweep_history_not_reached",
                }:
                    raise ValueError
            elif (
                record["sweep_permission_sha256"] is not None
                or record["event_key_sha256"] is not None
                or record["intent_sha256"] is not None
                or record["event_expires_at"] is not None
            ):
                raise ValueError
            if record["code"] == "sweep_history_observed_wait_pit" and (
                record["event_key_sha256"] is None
                or record["intent_sha256"] is None
                or record["event_expires_at"] is None
            ):
                raise ValueError
            if record["code"] == "sweep_history_observed_no_intent" and (
                record["event_key_sha256"] is None
                or record["intent_sha256"] is not None
            ):
                raise ValueError
            if (
                record["code"] == "sweep_history_rejected"
                and record["intent_sha256"] is not None
            ):
                raise ValueError
        except Exception:  # noqa: BLE001 -- static source-independent denial
            raise OwnedOriginalSweepHistoryError(
                "owned_sweep_history_receipt_invalid"
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


async def preflight_owned_sweep_history_v9(
    public_root,
    account_root,
    *,
    instrument_id,
    market_policy,
    account_session,
    session_factory,
) -> OwnedOriginalSweepHistoryDiagnosticV9:
    """Capture source-owned V6 sweep evidence; never accept caller gates/events."""
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    handoff = await original._capture_owned_original_sweep_history_for_boundary_v9(
        public_root,
        account_root,
        instrument_id=instrument_id,
        market_policy=market_policy,
        account_session=account_session,
        session_factory=session_factory,
    )
    if task is not asyncio.current_task() or task.cancelling():
        raise asyncio.CancelledError
    return _readback_owned_sweep_handoff_v9(handoff, account_session)


def _readback_owned_sweep_handoff_v9(
    handoff, account_session
) -> OwnedOriginalSweepHistoryDiagnosticV9:
    if type(handoff) is not original._OwnedSweepHistoryHandoffV9:
        raise OwnedOriginalSweepHistoryError("owned_sweep_handoff_required")
    diagnostic = handoff.original
    if type(diagnostic) is not original.InitialOwnedPrecursorDiagnosticV4:
        raise OwnedOriginalSweepHistoryError("owned_sweep_precursor_required")
    original.InitialOwnedPrecursorDiagnosticV4(diagnostic.receipt_json)
    record = decode(diagnostic.receipt_json, original._MAX_RECEIPT_BYTES)
    if (
        account_session._used is not True
        or record["account_plan_sha256"] != account_session._pin
    ):
        raise OwnedOriginalSweepHistoryError("owned_sweep_session_mismatch")
    inner = handoff.sweep_receipt_json
    inspected = record["code"] == "original_precursor_inspected"
    if inner is not None:
        if (
            not inspected
            or type(inner) is not bytes
            or not 0 < len(inner) <= _MAX_INNER_BYTES
        ):
            raise OwnedOriginalSweepHistoryError("owned_sweep_inspection_invalid")
        checked = decode(inner, _MAX_INNER_BYTES)
        if (
            type(checked) is not dict
            or set(checked) != _INNER_FIELDS
            or canonical(checked) != inner
            or checked["schema_version"] != _INNER_SCHEMA
            or checked["precursor_receipt_sha256"] != record["precursor_receipt_sha256"]
            or checked["public_packet_sha256"] != record["public_packet_sha256"]
            or checked["account_packet_sha256"] != record["account_packet_sha256"]
            or checked["account_plan_sha256"] != record["account_plan_sha256"]
            or checked["sweep_policy_sha256"] != POLICY_SHA256
            or type(checked["sweep_admitted"]) is not bool
            or checked["sweep_admitted"] is not (checked["sweep_code"] == "passed")
            or type(checked["sweep_code"]) is not str
            or not 1 <= len(checked["sweep_code"]) <= 96
            or checked["admission"] != "DENY"
            or any(checked[name] is not False for name in _FALSE_FIELDS)
        ):
            raise OwnedOriginalSweepHistoryError("owned_sweep_inspection_invalid")
        for name in (
            "g1_evaluation_sha256",
            "g1_source_sha256",
            "sweep_permission_sha256",
        ):
            try:
                _digest(checked[name])
            except ValueError:
                raise OwnedOriginalSweepHistoryError(
                    "owned_sweep_inspection_invalid"
                ) from None
        for name in (
            "event_key_sha256",
            "intent_sha256",
            "event_prefix_witness_sha256",
        ):
            try:
                _digest(checked[name], optional=True)
            except ValueError:
                raise OwnedOriginalSweepHistoryError(
                    "owned_sweep_inspection_invalid"
                ) from None
        try:
            _utc(checked["event_expires_at"])
        except (ValueError, TypeError):
            raise OwnedOriginalSweepHistoryError(
                "owned_sweep_inspection_invalid"
            ) from None
        if (
            checked["intent_sha256"] != record["precursor_intent_sha256"]
            or checked["sweep_admitted"] is False
            and checked["intent_sha256"] is not None
        ):
            raise OwnedOriginalSweepHistoryError("owned_sweep_inspection_invalid")
    elif inspected and record["precursor_code"] in {
        "sweep_stage_C_closed",
        "fixed_initial_entry_outside_original_zone",
    }:
        raise OwnedOriginalSweepHistoryError("owned_sweep_inspection_missing")
    code = (
        "native_precursor_unavailable"
        if not inspected
        else "sweep_history_not_reached"
        if inner is None
        else "sweep_history_rejected"
        if not checked["sweep_admitted"]
        else "sweep_history_observed_wait_pit"
        if checked["intent_sha256"] is not None
        else "sweep_history_observed_no_intent"
    )
    return OwnedOriginalSweepHistoryDiagnosticV9(
        canonical(
            {
                "schema_version": _SCHEMA,
                "code": code,
                "original_diagnostic_sha256": diagnostic.receipt_sha256,
                "public_packet_sha256": record["public_packet_sha256"],
                "account_packet_sha256": record["account_packet_sha256"],
                "precursor_receipt_sha256": record["precursor_receipt_sha256"],
                "sweep_inspection_sha256": None if inner is None else sha(inner),
                "sweep_policy_sha256": POLICY_SHA256,
                "sweep_permission_sha256": None
                if inner is None
                else checked["sweep_permission_sha256"],
                "event_key_sha256": None
                if inner is None
                else checked["event_key_sha256"],
                "intent_sha256": None if inner is None else checked["intent_sha256"],
                "event_expires_at": None
                if inner is None
                else checked["event_expires_at"],
                "history_evidence_replayed": inner is not None,
                "admission": "DENY",
                **{name: False for name in _FALSE_FIELDS},
            }
        )
    )
