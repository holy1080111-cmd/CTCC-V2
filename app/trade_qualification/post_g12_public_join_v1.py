"""Diagnostic join of a sealed post-G12 V2 public journal and its raw packet.

This is an offline integrity and projection comparison. A caller-provided journal,
digest, origin, or projection cannot authenticate the source or grant a reserve or
submit capability. The V2 quote is deliberately not converted to a legacy quote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification.market_bridge_v2 import public_market_context_v2
from app.trade_qualification.public_source_runtime import MAX_RAW, replay_public_runtime
from app.trade_qualification.recheck_models import RecheckOrigin, copy_recheck_origin

_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_SCHEMA = "ctcc.post_g12_public_join.v1"
_CODE = "public_v2_integrity_join_only"
_MAX_RECEIPT_BYTES = 4096
_MAX_MARKET_BYTES = 8 * 1024 * 1024
_MAX_REFERENCE_BYTES = 32768
_FALSE_FIELDS = (
    "legacy_quote_bound",
    "source_authenticity_verified",
    "execution_recheck_performed",
    "account_complete",
    "atomic_risk_reserved",
    "execution_authority",
    "order_submitted",
)
_DIGEST_FIELDS = (
    "public_plan_sha256",
    "public_journal_sha256",
    "public_packet_sha256",
    "quote_v2_packet_sha256",
    "current_market_sha256",
    "reference_sha256",
    "account_plan_sha256",
    "candidate_sha256",
    "event_key",
)
_FIELDS = frozenset(
    {
        "schema_version",
        "code",
        "report_id",
        "instrument_id",
        "publication_completed_at",
        "observed_at",
        "admission",
        *_DIGEST_FIELDS,
        *_FALSE_FIELDS,
    }
)


class PostG12PublicJoinError(ValueError):
    """Static, source-independent diagnostic rejection code."""


def _deny(code: str):
    raise PostG12PublicJoinError(code)


def _digest(value):
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        _deny("public_join_pin_invalid")
    return value


def _document(raw, maximum):
    if type(raw) is not str:
        _deny("public_join_projection_invalid")
    encoded = raw.encode("utf-8")
    if (
        not 0 < len(encoded) <= maximum
        or canonical(decode(encoded, maximum)) != encoded
    ):
        _deny("public_join_projection_invalid")
    return encoded


@dataclass(frozen=True, slots=True, repr=False)
class PostG12PublicJoinReceiptV1:
    receipt_json: bytes

    def __post_init__(self):
        try:
            raw = decode(self.receipt_json, _MAX_RECEIPT_BYTES)
            if (
                type(raw) is not dict
                or set(raw) != _FIELDS
                or canonical(raw) != self.receipt_json
                or raw["schema_version"] != _SCHEMA
                or raw["code"] != _CODE
                or raw["admission"] != "DENY"
                or any(raw[name] is not False for name in _FALSE_FIELDS)
                or any(_HEX64.fullmatch(raw[name]) is None for name in _DIGEST_FIELDS)
                or type(raw["report_id"]) is not str
                or type(raw["instrument_id"]) is not str
                or any(
                    type(raw[name]) is not str or len(raw[name]) > 40
                    for name in ("publication_completed_at", "observed_at")
                )
            ):
                raise ValueError
            for name in ("publication_completed_at", "observed_at"):
                stamp = datetime.fromisoformat(raw[name])
                if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
                    raise ValueError
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            _deny("public_join_receipt_invalid")

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def admission(self) -> Literal["DENY"]:
        return "DENY"

    @property
    def execution_authority(self) -> Literal[False]:
        return False


def replay_post_g12_public_join_v1(
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
) -> PostG12PublicJoinReceiptV1:
    """Replay the entire journal, then compare only source-derived projections.

    ``directory`` must be a readback directory from the native no-clobber store.
    The journal and all pins remain inspectable evidence, never bearer authority.
    """
    try:
        for pin in (
            expected_plan_sha256,
            expected_journal_sha256,
            expected_packet_sha256,
            account_plan_sha256,
        ):
            _digest(pin)
        origin = copy_recheck_origin(origin)
        if (
            type(observed_at) is not datetime
            or observed_at.utcoffset() is None
            or observed_at.utcoffset().total_seconds() != 0
            or not origin.publication_completed_at < observed_at < origin.deadline
        ):
            _deny("public_join_observation_time_invalid")
        market_raw = _document(current_market_json, _MAX_MARKET_BYTES)
        reference_raw = _document(reference_json, _MAX_REFERENCE_BYTES)
        summary, events, packet = replay_public_runtime(
            directory, expected_plan_sha256=expected_plan_sha256
        )
        if packet is None or not events or summary["disposition"] != "captured":
            _deny("public_join_capture_required")
        plan_raw = directory.read("plan.json", MAX_RAW)
        summary_raw = directory.read("summary.json", MAX_RAW)
        packet_raw = directory.read(f"event-{events[-1]['index']:04d}.raw", MAX_RAW)
        plan = decode(plan_raw, MAX_RAW)
        document = decode(packet_raw, public_v2.MAX_PACKET_BYTES)
        if (
            sha(plan_raw) != expected_plan_sha256
            or sha(summary_raw) != expected_journal_sha256
            or sha(packet_raw) != expected_packet_sha256
            or packet.packet_json != packet_raw
            or packet.bundle_sha256 != summary["packet_sha256"]
            or summary["plan_sha256"] != expected_plan_sha256
        ):
            _deny("public_join_journal_pin_mismatch")
        intent = origin.evidence.pre_evidence.prefix.intent
        expected_candidate = sha(
            canonical(origin.candidate.model_dump(mode="json", round_trip=True))
        )
        if (
            plan["schema_version"] != "ctcc.public.runtime_plan.v2"
            or plan["stage"] != "post_publication"
            or plan["environment"] != "demo"
            or "registration_region" not in plan
            or plan["registration_region_authenticated"] is not False
            or plan["account_plan_sha256"] != account_plan_sha256
            or plan["report_id"] != intent.report_id
            or plan["instrument_id"] != intent.instrument_id
            or plan["candidate_sha256"] != expected_candidate
            or plan["event_key"] != origin.original_event_key
            or plan["original_policy_sha256"] != origin.original_policy_sha256
            or plan["evidence_sha256"] != origin.evidence_sha256
            or plan["report_sha256"] != origin.evidence.receipt.report_sha256
            or plan["publication_completed_at"]
            != origin.publication_completed_at.isoformat()
            or plan["expires_at"] != origin.deadline.isoformat()
            or document["account_plan_sha256"] != account_plan_sha256
            or document["barrier_completed_at"]
            != origin.publication_completed_at.isoformat()
        ):
            _deny("public_join_lineage_mismatch")
        context = public_market_context_v2(
            packet,
            expected_bundle_sha256=expected_packet_sha256,
            evaluated_at=observed_at,
        )
        derived_market = canonical(
            context.market.model_dump(mode="json", round_trip=True)
        )
        derived_reference = canonical(
            context.reference.model_dump(mode="json", round_trip=True)
        )
        if market_raw != derived_market or reference_raw != derived_reference:
            _deny("public_join_projection_mismatch")
        result = {
            "schema_version": _SCHEMA,
            "code": _CODE,
            "report_id": intent.report_id,
            "instrument_id": intent.instrument_id,
            "publication_completed_at": origin.publication_completed_at.isoformat(),
            "observed_at": observed_at.isoformat(),
            "public_plan_sha256": expected_plan_sha256,
            "public_journal_sha256": expected_journal_sha256,
            "public_packet_sha256": expected_packet_sha256,
            "quote_v2_packet_sha256": document["quote_sha256"],
            "current_market_sha256": sha(market_raw),
            "reference_sha256": sha(reference_raw),
            "account_plan_sha256": account_plan_sha256,
            "candidate_sha256": expected_candidate,
            "event_key": origin.original_event_key,
            "admission": "DENY",
            **{name: False for name in _FALSE_FIELDS},
        }
        return PostG12PublicJoinReceiptV1(canonical(result))
    except PostG12PublicJoinError:
        raise
    except Exception:  # noqa: BLE001 -- never leak raw source or caller document data
        raise PostG12PublicJoinError("public_join_replay_denied") from None
