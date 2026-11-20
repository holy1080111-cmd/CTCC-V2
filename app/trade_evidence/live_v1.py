"""Explicit Live six-file evidence, with no qualification or submit authority.

The original Live quote keeps its environment, observation types and funding
semantics. A publication receipt records only read-back artifact bytes. Only a
separate, original-source coordinator can decide whether a fresh recheck may
continue; this module never creates an order permit.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from pydantic import WrapValidator

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import ClockStamp, canonical, sha, utc_from_ns
from app.trade_evidence import native_v2 as shared
from app.trade_evidence import service, storage
from app.trade_evidence.models import EvidenceSnapshot
from app.trade_qualification.executable_quote_live_v1 import (
    LiveExecutableQuoteV1,
    inspect_live_executable_quote_v1,
)


def _replay_live_evidence_quote_v1(value, handler):
    return shared._replay_evidence_quote_profile(
        value,
        handler,
        quote_type=LiveExecutableQuoteV1,
        quote_schema="ctcc.executable_quote_live.v1",
        inspector=inspect_live_executable_quote_v1,
    )


class LiveEvidenceSnapshotV1(EvidenceSnapshot):
    schema_version: Literal["ctcc_trade_evidence_live_v1"] = (
        "ctcc_trade_evidence_live_v1"
    )
    quote: Annotated[
        LiveExecutableQuoteV1, WrapValidator(_replay_live_evidence_quote_v1)
    ]


class LiveFamilyEvidenceSnapshotV1(shared.NativeFamilyEvidenceSnapshotV3):
    """Live history-family DATA with its exact qualification/history schema."""

    schema_version: Literal["ctcc_trade_evidence_live_family_v1"] = (
        "ctcc_trade_evidence_live_family_v1"
    )
    quote: Annotated[
        LiveExecutableQuoteV1, WrapValidator(_replay_live_evidence_quote_v1)
    ]


def prepare_live_evidence_v1(
    market,
    analysis,
    *,
    qualification,
    detection,
    quote,
    protection,
    prepared_at,
    zone_tick_size,
    candle_limit=80,
):
    return shared._prepare_native_evidence_profile(
        market,
        analysis,
        qualification=qualification,
        detection=detection,
        quote=quote,
        protection=protection,
        prepared_at=prepared_at,
        zone_tick_size=zone_tick_size,
        candle_limit=candle_limit,
        _profile_choice=shared.NativeEvidenceProfileV1.LIVE,
    )


def validate_live_snapshot_v1(snapshot):
    return shared._validate_native_evidence_profile(
        snapshot, _profile_choice=shared.NativeEvidenceProfileV1.LIVE
    )


def render_live_evidence_v1(snapshot):
    return shared._render_native_evidence_profile(
        snapshot, _profile_choice=shared.NativeEvidenceProfileV1.LIVE
    )


@dataclass(frozen=True, slots=True)
class LiveEvidencePublicationV1:
    """DATA only. Reopening these values cannot recreate an invocation owner."""

    receipt: storage.PublicationReceipt
    snapshot_sha256: str
    publication_barrier: ClockStamp
    execution_authority: Literal[False] = field(default=False, init=False)
    source_authenticity_verified: Literal[False] = field(default=False, init=False)
    recheck_performed: Literal[False] = field(default=False, init=False)


def publish_live_evidence_v1(root: Path, snapshot) -> LiveEvidencePublicationV1:
    """Publish once, read every byte back, then measure the real barrier.

    Idempotent storage readback remains useful to archive readers, but an old
    packet cannot receive a new publication barrier through this entry point.
    """
    checked = validate_live_snapshot_v1(snapshot)
    started = ClockStamp.model_validate(native_stamp(), strict=True)
    packet = render_live_evidence_v1(checked)
    receipt = storage.publish_evidence(root, report_id=checked.report_id, files=packet)
    if receipt.status != "written":
        raise service.EvidenceError("live_new_publication_required")
    barrier = ClockStamp.model_validate(native_stamp(), strict=True)
    if (
        utc_from_ns(barrier.utc_ns) < receipt.completed_at
        or barrier.utc_ns < started.utc_ns
        or barrier.monotonic_ns <= started.monotonic_ns
    ):
        raise service.EvidenceError("live_publication_clock_invalid")
    return LiveEvidencePublicationV1(
        receipt=receipt,
        snapshot_sha256=sha(
            canonical(checked.model_dump(mode="json", round_trip=True))
        ),
        publication_barrier=barrier,
    )
