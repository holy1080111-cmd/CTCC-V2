"""Fail-closed owned-original entry for a future publish-and-recheck pipeline.

The V4 native precursor is derived in this task. Its hash-only diagnostic is
never promoted to a G1--G11 run or a reusable publication capability. Current
V6 account evidence cannot supply complete portfolio risk or protection inputs.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import original_source_coordinator_v2 as original

_SCHEMA = "ctcc.owned_original_candidate_boundary.v5"
_MAX_BYTES = 2048
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_FALSE_FIELDS = (
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
        "original_diagnostic_sha256",
        "public_packet_sha256",
        "account_packet_sha256",
        "precursor_receipt_sha256",
        "precursor_intent_sha256",
        "admission",
        *_FALSE_FIELDS,
    }
)


class OwnedOriginalCandidateBoundaryError(ValueError):
    """Static denial; never includes source, account, credential or receipt text."""


@dataclass(frozen=True, slots=True, repr=False)
class OwnedOriginalCandidateBoundaryV5:
    receipt_json: bytes

    def __post_init__(self):
        try:
            if (
                type(self.receipt_json) is not bytes
                or not 0 < len(self.receipt_json) <= _MAX_BYTES
            ):
                raise ValueError
            raw = decode(self.receipt_json, _MAX_BYTES)
            if (
                type(raw) is not dict
                or set(raw) != _FIELDS
                or canonical(raw) != self.receipt_json
                or raw["schema_version"] != _SCHEMA
                or raw["code"]
                not in (
                    "native_precursor_unavailable",
                    "precursor_no_intent",
                    "g1_g11_source_inputs_unavailable",
                )
                or raw["admission"] != "DENY"
                or any(raw[name] is not False for name in _FALSE_FIELDS)
                or type(raw["original_diagnostic_sha256"]) is not str
                or _DIGEST.fullmatch(raw["original_diagnostic_sha256"]) is None
            ):
                raise ValueError
            for name in (
                "public_packet_sha256",
                "account_packet_sha256",
                "precursor_receipt_sha256",
                "precursor_intent_sha256",
            ):
                value = raw[name]
                if value is not None and (
                    type(value) is not str or _DIGEST.fullmatch(value) is None
                ):
                    raise ValueError
            precursor_pin = raw["precursor_receipt_sha256"]
            intent_pin = raw["precursor_intent_sha256"]
            source_complete = (
                raw["public_packet_sha256"] is not None
                and raw["account_packet_sha256"] is not None
            )
            if raw["code"] == "native_precursor_unavailable":
                if precursor_pin is not None or intent_pin is not None:
                    raise ValueError
            elif raw["code"] == "precursor_no_intent":
                if (
                    precursor_pin is None
                    or intent_pin is not None
                    or not source_complete
                ):
                    raise ValueError
            elif precursor_pin is None or intent_pin is None or not source_complete:
                raise ValueError
        except Exception:  # noqa: BLE001 -- never serialize private source detail
            raise OwnedOriginalCandidateBoundaryError(
                "owned_original_candidate_boundary_invalid"
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


async def preflight_owned_publish_and_recheck_v5(
    public_root,
    account_root,
    *,
    instrument_id,
    strategy,
    market_policy,
    account_session,
    session_factory,
) -> OwnedOriginalCandidateBoundaryV5:
    """Acquire V4 in this task, then explicitly stop before G12.

    No caller run, candidate, old receipt, G12 result, account packet, clock or
    continuation callback enters this boundary. V4's diagnostic cannot restore
    its consumed native raw leases. The missing engine inputs must be acquired
    and checked inside a future owner before G12 can be invoked.
    """
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    result = await original.capture_owned_original_precursor_v4(
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
    if type(result) is not original.InitialOwnedPrecursorDiagnosticV4:
        raise OwnedOriginalCandidateBoundaryError("owned_original_precursor_required")
    original.InitialOwnedPrecursorDiagnosticV4(result.receipt_json)
    record = decode(result.receipt_json, original._MAX_RECEIPT_BYTES)
    if (
        account_session._used is not True
        or record["account_plan_sha256"] != account_session._pin
    ):
        raise OwnedOriginalCandidateBoundaryError("owned_original_session_mismatch")
    # The V4 receipt is an audit record, not an exportable source capability.
    # Its derived intent still lacks protection, full account risk and G2--G11.
    inspected = record["code"] == "original_precursor_inspected"
    inspected_intent = inspected and record["precursor_intent_derived"] is True
    return OwnedOriginalCandidateBoundaryV5(
        canonical(
            {
                "schema_version": _SCHEMA,
                "code": (
                    "g1_g11_source_inputs_unavailable"
                    if inspected_intent
                    else "precursor_no_intent"
                    if inspected
                    else "native_precursor_unavailable"
                ),
                "original_diagnostic_sha256": result.receipt_sha256,
                "public_packet_sha256": record["public_packet_sha256"],
                "account_packet_sha256": record["account_packet_sha256"],
                "precursor_receipt_sha256": record["precursor_receipt_sha256"],
                "precursor_intent_sha256": record["precursor_intent_sha256"],
                "admission": "DENY",
                **{name: False for name in _FALSE_FIELDS},
            }
        )
    )
