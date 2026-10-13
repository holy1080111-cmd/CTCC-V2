"""Same-invocation fixed structural geometry inspection, always DENY.

This records a source-owned original entry/stop/target only when an inspection
scenario finds a bracket. It never evaluates ordered G6--G9, authenticates
account history, creates a candidate, publishes G12, or grants order authority.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import native_original_bracket_policy_v1 as policy
from app.trade_qualification import original_source_coordinator_v2 as original

_SCHEMA = "ctcc.owned_original_bracket_boundary.v11"
_MAX_BYTES = 4096
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_CODES = frozenset(
    {
        "source_unavailable",
        "precursor_no_intent",
        "bracket_unavailable",
        "fixed_bracket_observed_unqualified",
    }
)
_DIGEST_FIELDS = (
    "original_diagnostic_sha256",
    "public_packet_sha256",
    "account_packet_sha256",
    "account_plan_sha256",
    "precursor_receipt_sha256",
    "precursor_intent_sha256",
    "base_prefix_receipt_sha256",
    "base_event_receipt_sha256",
    "event_key_sha256",
    "bracket_policy_sha256",
    "bracket_selection_sha256",
)
_FALSE_FIELDS = (
    "historical_first_availability_verified",
    "event_ledger_authenticated",
    "g6_evaluated",
    "g7_evaluated",
    "g8_evaluated",
    "g9_evaluated",
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
_FIELDS = frozenset(
    {
        "schema_version",
        "code",
        "report_id",
        "instrument_id",
        "direction",
        "original_entry",
        "fixed_stop_loss",
        "fixed_take_profit",
        "bracket_inspected",
        "bracket_available",
        "g1_g5_replayed",
        "admission",
        *_DIGEST_FIELDS,
        *_FALSE_FIELDS,
    }
)


class OwnedOriginalBracketError(ValueError):
    """Fixed refusal; never reveals raw market, account, or credential values."""


def _price(value):
    if type(value) is not str or not 0 < len(value) <= 64:
        raise ValueError
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError from None
    if (
        not number.is_finite()
        or number <= 0
        or len(number.as_tuple().digits) > 40
        or abs(number.as_tuple().exponent) > 20
        or format(number, "f") != value
    ):
        raise ValueError
    return number


@dataclass(frozen=True, slots=True, repr=False)
class OwnedOriginalBracketDiagnosticV11:
    receipt_json: bytes

    def __post_init__(self):
        try:
            if type(self.receipt_json) is not bytes:
                raise ValueError
            record = decode(self.receipt_json, _MAX_BYTES)
            if (
                type(record) is not dict
                or set(record) != _FIELDS
                or canonical(record) != self.receipt_json
                or record["schema_version"] != _SCHEMA
                or record["code"] not in _CODES
                or record["admission"] != "DENY"
                or any(record[name] is not False for name in _FALSE_FIELDS)
                or record["bracket_policy_sha256"] != policy.POLICY_RECORD_SHA256
                or type(record["bracket_inspected"]) is not bool
                or type(record["bracket_available"]) is not bool
                or type(record["g1_g5_replayed"]) is not bool
                or record["bracket_inspected"] is not record["g1_g5_replayed"]
            ):
                raise ValueError
            for name in _DIGEST_FIELDS:
                value = record[name]
                if value is not None and (
                    type(value) is not str or _HEX.fullmatch(value) is None
                ):
                    raise ValueError
            if any(
                record[name] is None
                for name in (
                    "original_diagnostic_sha256",
                    "account_plan_sha256",
                    "bracket_policy_sha256",
                )
            ):
                raise ValueError
            inspected = record["bracket_inspected"]
            if inspected:
                if any(record[name] is None for name in _DIGEST_FIELDS):
                    raise ValueError
                if (
                    record["code"]
                    != (
                        "fixed_bracket_observed_unqualified"
                        if record["bracket_available"]
                        else "bracket_unavailable"
                    )
                    or type(record["report_id"]) is not str
                    or not 0 < len(record["report_id"]) <= 128
                    or type(record["instrument_id"]) is not str
                    or not 0 < len(record["instrument_id"]) <= 64
                    or record["direction"] not in {"long", "short"}
                ):
                    raise ValueError
                entry = _price(record["original_entry"])
                if record["bracket_available"]:
                    stop = _price(record["fixed_stop_loss"])
                    target = _price(record["fixed_take_profit"])
                    if not (
                        stop < entry < target
                        if record["direction"] == "long"
                        else target < entry < stop
                    ):
                        raise ValueError
                elif (
                    record["fixed_stop_loss"] is not None
                    or record["fixed_take_profit"] is not None
                ):
                    raise ValueError
            elif (
                record["code"] not in {"source_unavailable", "precursor_no_intent"}
                or record["bracket_available"]
                or any(
                    record[name] is not None
                    for name in (
                        "report_id",
                        "instrument_id",
                        "direction",
                        "original_entry",
                        "fixed_stop_loss",
                        "fixed_take_profit",
                        "base_prefix_receipt_sha256",
                        "base_event_receipt_sha256",
                        "event_key_sha256",
                        "bracket_selection_sha256",
                    )
                )
                or record["code"] == "precursor_no_intent"
                and (
                    record["precursor_receipt_sha256"] is None
                    or record["precursor_intent_sha256"] is not None
                )
            ):
                raise ValueError
        except Exception:  # noqa: BLE001 -- bounded untrusted receipt refusal
            raise OwnedOriginalBracketError("owned_bracket_receipt_invalid") from None

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def admission(self) -> Literal["DENY"]:
        return "DENY"

    @property
    def execution_authority(self) -> Literal[False]:
        return False


async def inspect_owned_original_bracket_v11(
    public_root,
    account_root,
    *,
    instrument_id,
    strategy,
    market_policy,
    account_session,
    session_factory,
) -> OwnedOriginalBracketDiagnosticV11:
    """Accept no caller packet, prior receipt, gate, candidate, or geometry."""
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    receipt = await original._capture_owned_original_bracket_for_boundary_v11(
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
    checked = OwnedOriginalBracketDiagnosticV11(receipt)
    record = decode(checked.receipt_json, _MAX_BYTES)
    if record["account_plan_sha256"] != account_session._pin or (
        record["bracket_inspected"] and account_session._used is not True
    ):
        raise OwnedOriginalBracketError("owned_bracket_session_mismatch")
    return checked
