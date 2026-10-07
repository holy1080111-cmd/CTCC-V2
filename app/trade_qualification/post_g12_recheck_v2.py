"""Replayable V2 public-only post-G12 recheck of one original base candidate.

This composes the existing raw V2 G1, current G2--G4, original event/zone and
two fixed-geometry projected economics calculations. It cannot infer intrabar
event survival, actual account costs or protected exposure from public bytes.
Every receipt therefore remains DENY and is never a reservation input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import (
    current_conditions_v2,
    current_economics_v2,
    data_v2,
    original_event_v2,
)
from app.trade_qualification.current_conditions_v2 import BASE_STRATEGIES
from app.trade_qualification.engine import PreEvidenceRun
from app.trade_qualification.post_g12_public_join_v1 import (
    replay_post_g12_public_join_v1,
)
from app.trade_qualification.public_source_runtime import MAX_RAW, replay_public_runtime
from app.trade_qualification.recheck_models import RecheckOrigin, copy_recheck_origin

_SCHEMA = "ctcc.post_g12_public_recheck.v2"
_CODES = frozenset(
    {
        "current_g1_rejected",
        "current_g2_g4_rejected",
        "original_event_zone_rejected",
        "projected_economics_rejected",
        "projected_math_consistent_account_path_required",
    }
)
_MAX_RECEIPT_BYTES = 16384
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_FALSE_FIELDS = (
    "complete_path_verified",
    "original_event_survival_verified",
    "actual_account_costs_verified",
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
        "public_join_sha256",
        "public_packet_sha256",
        "origin_sha256",
        "candidate_sha256",
        "original_event_key",
        "report_id",
        "instrument_id",
        "direction",
        "original_entry",
        "original_stop_loss",
        "original_take_profit",
        "executable_reference",
        "quote_v2_packet_sha256",
        "observed_at",
        "g1",
        "current_g2_g4",
        "original_event_zone",
        "projected_economics",
        "admission",
        *_FALSE_FIELDS,
    }
)


class PostG12RecheckV2Error(ValueError):
    """Fixed, source-independent replay or receipt rejection code."""


def _deny(code):
    raise PostG12RecheckV2Error(code)


def _decimal(value):
    if type(value) is not Decimal or not value.is_finite():
        _deny("public_recheck_v2_numeric_invalid")
    return str(value)


def _scenario(value):
    if value is None:
        return None
    return {
        "entry": _decimal(value.entry),
        "stop_loss": _decimal(value.stop_loss),
        "take_profit": _decimal(value.take_profit),
        "code": value.code,
        "measurements": [
            [name, _decimal(measured)] for name, measured in value.measurements
        ],
    }


def _economics(value):
    if value is None:
        return None
    return {
        "code": value.code,
        "failure_stage": value.failure_stage,
        "candidate": _scenario(value.candidate),
        "execution": _scenario(value.execution),
        "comparison": [
            [name, None if measured is None else _decimal(measured)]
            for name, measured in value.comparison
        ],
        "economics_policy_sha256": value.economics_policy_sha256,
        "funding_pair_sha256": value.funding_pair_sha256,
        "quote_inspection_sha256": value.quote_inspection_sha256,
    }


@dataclass(frozen=True, slots=True, repr=False)
class PostG12PublicRecheckReceiptV2:
    receipt_json: bytes

    def __post_init__(self):
        try:
            raw = decode(self.receipt_json, _MAX_RECEIPT_BYTES)
            if (
                type(raw) is not dict
                or set(raw) != _FIELDS
                or canonical(raw) != self.receipt_json
                or raw["schema_version"] != _SCHEMA
                or raw["code"] not in _CODES
                or raw["admission"] != "DENY"
                or any(raw[name] is not False for name in _FALSE_FIELDS)
                or any(
                    type(raw[name]) is not str or _HEX64.fullmatch(raw[name]) is None
                    for name in (
                        "public_join_sha256",
                        "public_packet_sha256",
                        "origin_sha256",
                        "candidate_sha256",
                        "original_event_key",
                        "quote_v2_packet_sha256",
                    )
                )
                or raw["direction"] not in ("long", "short")
                or type(raw["report_id"]) is not str
                or type(raw["instrument_id"]) is not str
                or type(raw["observed_at"]) is not str
                or type(raw["g1"]) is not dict
                or raw["current_g2_g4"] is not None
                and type(raw["current_g2_g4"]) is not dict
                or raw["original_event_zone"] is not None
                and type(raw["original_event_zone"]) is not dict
                or raw["projected_economics"] is not None
                and type(raw["projected_economics"]) is not dict
            ):
                raise ValueError
            instant = datetime.fromisoformat(raw["observed_at"])
            if instant.utcoffset() is None or instant.utcoffset().total_seconds() != 0:
                raise ValueError
            for name in (
                "original_entry",
                "original_stop_loss",
                "original_take_profit",
                "executable_reference",
            ):
                if type(raw[name]) is not str or not Decimal(raw[name]).is_finite():
                    raise ValueError
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            _deny("public_recheck_v2_receipt_invalid")

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def admission(self) -> Literal["DENY"]:
        return "DENY"

    @property
    def execution_authority(self) -> Literal[False]:
        return False


def evaluate_post_g12_public_recheck_v2(
    directory,
    *,
    expected_plan_sha256: str,
    expected_journal_sha256: str,
    expected_packet_sha256: str,
    origin: RecheckOrigin,
    account_plan_sha256: str,
    current_market_json: str,
    reference_json: str,
    observed_at: datetime,
) -> PostG12PublicRecheckReceiptV2:
    """Recompute the same fixed candidate from raw V2 packet/journal bytes.

    Source ownership, complete account state, execution protection and all
    possible fill prices remain unverified. This function never writes a hold.
    """
    try:
        original = copy_recheck_origin(origin)
        pre = original.evidence.pre_evidence
        if (
            type(pre) is not PreEvidenceRun
            or pre.prefix.intent.strategy not in BASE_STRATEGIES
        ):
            _deny("public_recheck_v2_base_original_required")
        joined = replay_post_g12_public_join_v1(
            directory,
            expected_plan_sha256=expected_plan_sha256,
            expected_journal_sha256=expected_journal_sha256,
            expected_packet_sha256=expected_packet_sha256,
            origin=original,
            account_plan_sha256=account_plan_sha256,
            current_market_json=current_market_json,
            reference_json=reference_json,
            observed_at=observed_at,
        )
        summary, _, packet = replay_public_runtime(
            directory, expected_plan_sha256=expected_plan_sha256
        )
        if (
            summary["packet_sha256"] != expected_packet_sha256
            or sha(directory.read("summary.json", MAX_RAW)) != expected_journal_sha256
            or packet is None
            or packet.bundle_sha256 != expected_packet_sha256
        ):
            _deny("public_recheck_v2_public_changed")
        context = current_economics_v2.public_market_context_v2(
            packet,
            expected_bundle_sha256=expected_packet_sha256,
            evaluated_at=observed_at,
        )
        intent = pre.prefix.intent
        entry, stop, target = (
            intent.candidate_entry,
            pre.result.stop_loss,
            pre.result.take_profit,
        )
        candidate = original.candidate
        if (
            type(entry) is not Decimal
            or type(stop) is not Decimal
            or type(target) is not Decimal
            or (candidate.candidate_entry, candidate.stop_loss, candidate.take_profit)
            != (entry, stop, target)
        ):
            _deny("public_recheck_v2_original_geometry_changed")
        reference = (
            context.quote.ticker.ask
            if intent.direction == "long"
            else context.quote.ticker.bid
        )
        g1 = data_v2.evaluate_public_market_data_v2(
            packet,
            expected_bundle_sha256=expected_packet_sha256,
            policy=pre.policy.prefix.data,
            evaluated_at=observed_at,
        )
        g1 = data_v2.verify_public_market_data_v2(
            g1,
            packet,
            expected_bundle_sha256=expected_packet_sha256,
            policy=pre.policy.prefix.data,
            evaluated_at=observed_at,
        )
        current = event = economics = None
        if g1.passed:
            current = current_conditions_v2.evaluate_current_base_conditions_v2(
                packet, origin=original, current_g1=g1
            )
            if current.passed:
                event = original_event_v2.evaluate_original_event_zone_v2(
                    packet, origin=original, current_g1=g1, observed_at=observed_at
                )
                if event.passed:
                    economics = (
                        current_economics_v2.evaluate_current_projected_economics_v2(
                            packet,
                            origin=original,
                            current_g1=g1,
                            observed_at=observed_at,
                        )
                    )
        code = (
            "current_g1_rejected"
            if not g1.passed
            else "current_g2_g4_rejected"
            if current is None or not current.passed
            else "original_event_zone_rejected"
            if event is None or not event.passed
            else "projected_economics_rejected"
            if economics is None or not economics.projected_math_passed
            else "projected_math_consistent_account_path_required"
        )
        result = {
            "schema_version": _SCHEMA,
            "code": code,
            "public_join_sha256": joined.receipt_sha256,
            "public_packet_sha256": expected_packet_sha256,
            "origin_sha256": original.evaluation_sha256,
            "candidate_sha256": decode(joined.receipt_json)["candidate_sha256"],
            "original_event_key": original.original_event_key,
            "report_id": intent.report_id,
            "instrument_id": intent.instrument_id,
            "direction": intent.direction,
            "original_entry": _decimal(entry),
            "original_stop_loss": _decimal(stop),
            "original_take_profit": _decimal(target),
            "executable_reference": _decimal(reference),
            "quote_v2_packet_sha256": decode(joined.receipt_json)[
                "quote_v2_packet_sha256"
            ],
            "observed_at": observed_at.isoformat(),
            "g1": {
                "code": g1.gate.code,
                "passed": g1.passed,
                "evaluation_sha256": g1.evaluation_sha256,
                "source_sha256": g1.source_sha256,
            },
            "current_g2_g4": None
            if current is None
            else {
                "passed": current.passed,
                "codes": [[str(gate.gate), gate.code] for gate in current.gates],
                "source_sha256": current.current_source_sha256,
            },
            "original_event_zone": None
            if event is None
            else {
                "event_code": event.event_code,
                "zone_code": event.zone_code,
                "original_zone_sha256": event.original_zone_sha256,
                "reference_price": None
                if event.reference_price is None
                else _decimal(event.reference_price),
            },
            "projected_economics": _economics(economics),
            "admission": "DENY",
            **{name: False for name in _FALSE_FIELDS},
        }
        return PostG12PublicRecheckReceiptV2(canonical(result))
    except PostG12RecheckV2Error:
        raise
    except Exception:  # noqa: BLE001 -- no raw exchange/account data in failures
        raise PostG12RecheckV2Error("public_recheck_v2_denied") from None


def verify_post_g12_public_recheck_v2(
    receipt: PostG12PublicRecheckReceiptV2, directory, **inputs
) -> PostG12PublicRecheckReceiptV2:
    """A caller-written receipt cannot substitute for full byte-for-byte replay."""
    if type(receipt) is not PostG12PublicRecheckReceiptV2:
        _deny("public_recheck_v2_exact_receipt_required")
    checked = PostG12PublicRecheckReceiptV2(receipt.receipt_json)
    replayed = evaluate_post_g12_public_recheck_v2(directory, **inputs)
    if checked.receipt_json != replayed.receipt_json:
        _deny("public_recheck_v2_receipt_changed")
    return replayed
