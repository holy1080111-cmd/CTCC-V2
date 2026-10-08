"""Same-invocation native base G1--G4 inspection, always DENY.

The private coordinator owns both raw packets while doing the arithmetic. This
hash-only result is neither a source certificate nor a candidate/G12 capability.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import original_candidate_precursor_v2 as precursor
from app.trade_qualification import original_source_coordinator_v2 as original

_SCHEMA = "ctcc.owned_original_base_prefix_boundary.v6"
_PREFIX_SCHEMA = "ctcc.owned_original_base_prefix_inspection.v6"
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_MAX_BYTES = 2048
_MAX_PREFIX_BYTES = 4096
_FALSE_FIELDS = (
    "original_source_verified",
    "candidate_created",
    "g1_g11_complete",
    "g12_published",
    "account_complete",
    "qualification_performed",
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
        "base_prefix_receipt_sha256",
        "base_prefix_result_sha256",
        "base_prefix_policy_sha256",
        "base_g1_g4_evaluated",
        "admission",
        *_FALSE_FIELDS,
    }
)
_PREFIX_FIELDS = frozenset(
    {
        "schema_version",
        "precursor_receipt_sha256",
        "precursor_intent_sha256",
        "public_packet_sha256",
        "account_packet_sha256",
        "instrument_rules_sha256",
        "g1_result_sha256",
        "g1_source_sha256",
        "prefix_policy_sha256",
        "result_sha256",
        "gates",
        "calibrated_for_trading",
        "admission",
        *_FALSE_FIELDS,
    }
)


class OwnedOriginalBasePrefixError(ValueError):
    """Fixed denial codes; never echoes private source, account or credential."""


@dataclass(frozen=True, slots=True, repr=False)
class OwnedOriginalBasePrefixDiagnosticV6:
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
                not in {
                    "native_precursor_unavailable",
                    "precursor_no_intent",
                    "base_g1_g4_inspected",
                }
                or raw["admission"] != "DENY"
                or any(raw[name] is not False for name in _FALSE_FIELDS)
                or type(raw["base_g1_g4_evaluated"]) is not bool
            ):
                raise ValueError
            for name in (
                "original_diagnostic_sha256",
                "public_packet_sha256",
                "account_packet_sha256",
                "precursor_receipt_sha256",
                "precursor_intent_sha256",
                "base_prefix_receipt_sha256",
                "base_prefix_result_sha256",
                "base_prefix_policy_sha256",
            ):
                value = raw[name]
                if value is not None and (
                    type(value) is not str or _HEX.fullmatch(value) is None
                ):
                    raise ValueError
            if raw["original_diagnostic_sha256"] is None:
                raise ValueError
            inspected = raw["code"] == "base_g1_g4_inspected"
            if raw["base_g1_g4_evaluated"] is not inspected:
                raise ValueError
            if inspected:
                if any(
                    raw[name] is None
                    for name in (
                        "public_packet_sha256",
                        "account_packet_sha256",
                        "precursor_receipt_sha256",
                        "precursor_intent_sha256",
                        "base_prefix_receipt_sha256",
                        "base_prefix_result_sha256",
                        "base_prefix_policy_sha256",
                    )
                ):
                    raise ValueError
            elif any(
                raw[name] is not None
                for name in (
                    "base_prefix_receipt_sha256",
                    "base_prefix_result_sha256",
                    "base_prefix_policy_sha256",
                )
            ):
                raise ValueError
            if raw["code"] == "precursor_no_intent" and (
                raw["precursor_receipt_sha256"] is None
                or raw["precursor_intent_sha256"] is not None
            ):
                raise ValueError
            if raw["code"] == "native_precursor_unavailable" and (
                raw["precursor_receipt_sha256"] is not None
                or raw["precursor_intent_sha256"] is not None
            ):
                raise ValueError
        except Exception:  # noqa: BLE001 -- bound, source-independent error
            raise OwnedOriginalBasePrefixError(
                "owned_base_prefix_receipt_invalid"
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


async def preflight_owned_base_prefix_v6(
    public_root,
    account_root,
    *,
    instrument_id,
    strategy,
    market_policy,
    account_session,
    session_factory,
) -> OwnedOriginalBasePrefixDiagnosticV6:
    """Evaluate owned initial G1--G4 for one base strategy, without a submit path.

    A caller cannot provide a packet, intent, gate, PASS bit, prior receipt,
    publication barrier, clock, callback or candidate geometry.
    """
    if type(strategy) is not str or strategy not in precursor._BASE_STRATEGIES:
        raise OwnedOriginalBasePrefixError("owned_base_prefix_strategy_unsupported")
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    handoff = await original._capture_owned_original_base_prefix_for_boundary_v6(
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
    return _readback_owned_base_prefix_handoff_v6(handoff, account_session)


def _readback_owned_base_prefix_handoff_v6(
    handoff, account_session
) -> OwnedOriginalBasePrefixDiagnosticV6:
    """Validate a private hash-only handoff; never create execution authority."""
    if type(handoff) is not original._OwnedBasePrefixHandoffV6:
        raise OwnedOriginalBasePrefixError("owned_base_prefix_handoff_required")
    diagnostic = handoff.original
    if type(diagnostic) is not original.InitialOwnedPrecursorDiagnosticV4:
        raise OwnedOriginalBasePrefixError("owned_base_prefix_precursor_required")
    original.InitialOwnedPrecursorDiagnosticV4(diagnostic.receipt_json)
    record = decode(diagnostic.receipt_json, original._MAX_RECEIPT_BYTES)
    if (
        account_session._used is not True
        or record["account_plan_sha256"] != account_session._pin
    ):
        raise OwnedOriginalBasePrefixError("owned_base_prefix_session_mismatch")
    prefix = handoff.prefix_receipt_json
    inspected = record["code"] == "original_precursor_inspected"
    intent = inspected and record["precursor_intent_derived"] is True
    if prefix is not None:
        if type(prefix) is not bytes or not 0 < len(prefix) <= _MAX_PREFIX_BYTES:
            raise OwnedOriginalBasePrefixError("owned_base_prefix_inspection_invalid")
        checked = decode(prefix, _MAX_PREFIX_BYTES)
        if (
            not intent
            or type(checked) is not dict
            or set(checked) != _PREFIX_FIELDS
            or canonical(checked) != prefix
            or checked.get("schema_version") != _PREFIX_SCHEMA
            or checked.get("precursor_receipt_sha256")
            != record["precursor_receipt_sha256"]
            or checked.get("precursor_intent_sha256")
            != record["precursor_intent_sha256"]
            or checked.get("public_packet_sha256") != record["public_packet_sha256"]
            or checked.get("account_packet_sha256") != record["account_packet_sha256"]
            or checked.get("calibrated_for_trading") is not False
            or checked.get("admission") != "DENY"
            or any(checked.get(name) is not False for name in _FALSE_FIELDS)
            or checked.get("gates")
            != [
                {"gate": f"G{number}", "code": "passed", "passed": True}
                for number in range(1, 5)
            ]
        ):
            raise OwnedOriginalBasePrefixError("owned_base_prefix_inspection_invalid")
        for name in (
            "result_sha256",
            "prefix_policy_sha256",
            "g1_result_sha256",
            "g1_source_sha256",
            "instrument_rules_sha256",
        ):
            value = checked.get(name)
            if type(value) is not str or _HEX.fullmatch(value) is None:
                raise OwnedOriginalBasePrefixError(
                    "owned_base_prefix_inspection_invalid"
                )
    elif intent:
        raise OwnedOriginalBasePrefixError("owned_base_prefix_inspection_missing")
    code = (
        "base_g1_g4_inspected"
        if prefix is not None
        else "precursor_no_intent"
        if inspected
        else "native_precursor_unavailable"
    )
    return OwnedOriginalBasePrefixDiagnosticV6(
        canonical(
            {
                "schema_version": _SCHEMA,
                "code": code,
                "original_diagnostic_sha256": diagnostic.receipt_sha256,
                "public_packet_sha256": record["public_packet_sha256"],
                "account_packet_sha256": record["account_packet_sha256"],
                "precursor_receipt_sha256": record["precursor_receipt_sha256"],
                "precursor_intent_sha256": record["precursor_intent_sha256"],
                "base_prefix_receipt_sha256": None if prefix is None else sha(prefix),
                "base_prefix_result_sha256": None
                if prefix is None
                else checked["result_sha256"],
                "base_prefix_policy_sha256": None
                if prefix is None
                else checked["prefix_policy_sha256"],
                "base_g1_g4_evaluated": prefix is not None,
                "admission": "DENY",
                **{name: False for name in _FALSE_FIELDS},
            }
        )
    )
