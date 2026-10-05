"""Closed-bar original-event and original-zone diagnostic over fresh V2 public data.

An intrabar path is not observable from OHLC or a current quote. This module
never turns its bounded calculation into an execution or reservation permit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Context, Decimal, localcontext
from typing import Literal

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import data, data_v2
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification.continuation import (
    ContinuationResult,
    evaluate_continuation,
)
from app.trade_qualification.current_conditions_v2 import BASE_STRATEGIES
from app.trade_qualification.data_v2 import DataQualificationResultV2
from app.trade_qualification.engine import PreEvidenceRun
from app.trade_qualification.location import (
    _evaluate_location_numeric_tail,
    build_entry_zone,
)
from app.trade_qualification.market_bridge_v2 import public_market_context_v2
from app.trade_qualification.recheck_models import copy_recheck_origin
from app.trade_qualification.service import QualificationPrefixPolicy


@dataclass(frozen=True, slots=True)
class OriginalEventZoneDiagnosticV2:
    continuation: ContinuationResult = field(repr=False)
    event_code: str
    zone_code: str
    origin_sha256: str
    original_candidate_sha256: str
    original_event_key: str
    original_zone_sha256: str
    original_policy_sha256: str
    public_bundle_sha256: str
    current_g1_sha256: str
    current_source_sha256: str
    observed_at: datetime
    reference_price: Decimal | None = None
    drift_bps: Decimal | None = None
    record_kind: str = field(default="post_g12_original_event_zone_v2", init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)
    # Closed-bar coverage cannot prove no intrabar touch and return.
    complete_path_verified: Literal[False] = field(default=False, init=False)
    original_event_survival_verified: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    atomic_risk_reserved: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)

    @property
    def passed(self) -> bool:
        return self.continuation.passed and self.zone_code == "passed"


def _source(raw: str):
    document = decode(raw.encode(), data.MAX_SOURCE_BYTES)
    return (
        MarketSnapshot.model_validate_json(
            json.dumps(document["market"], allow_nan=False), strict=True
        ),
        MultiTimeframeAnalysis.model_validate_json(
            json.dumps(document["analysis"], allow_nan=False), strict=True
        ),
    )


def evaluate_original_event_zone_v2(packet, *, origin, current_g1, observed_at):
    """Recheck the same event and source zone; never extract from new candles.

    The old event is replayed only against the pinned original raw market. The
    new V2 packet can then prove append-only closed-bar coverage and current
    quote location. A truncated/revised window or unseen intrabar path remains
    insufficient for execution admission.
    """
    if type(current_g1) is not DataQualificationResultV2:
        raise ValueError("original_event_v2_exact_g1_required")
    original = copy_recheck_origin(origin)
    pre = original.evidence.pre_evidence
    if (
        type(pre) is not PreEvidenceRun
        or type(pre.policy.prefix) is not QualificationPrefixPolicy
        or pre.prefix.intent.strategy not in BASE_STRATEGIES
    ):
        raise ValueError("original_event_v2_base_original_required")
    now = data._utc(observed_at)
    intent, policy = pre.prefix.intent, pre.policy.prefix
    if (
        not original.publication_completed_at
        < current_g1.evaluated_at
        <= now
        < original.deadline
    ):
        raise ValueError("original_event_v2_outside_original_window")
    checked, _ = public_v2._parts(packet)
    document = decode(checked.packet_json, public_v2.MAX_PACKET_BYTES)
    if (
        document["stage"] != "post_publication"
        or document["barrier_completed_at"]
        != original.publication_completed_at.isoformat()
        or document["report_id"] != intent.report_id
        or document["instrument_id"] != intent.instrument_id
    ):
        raise ValueError("original_event_v2_post_g12_scope_mismatch")
    g1 = data_v2.verify_public_market_data_v2(
        current_g1,
        checked,
        expected_bundle_sha256=checked.bundle_sha256,
        policy=policy.data,
        evaluated_at=current_g1.evaluated_at,
    )
    if (
        not g1.passed
        or g1.report_id != intent.report_id
        or g1.instrument_id != intent.instrument_id
        or g1.source_json is None
        or g1.source_sha256 is None
        or pre.prefix.data_result.source_json is None
        or pre.prefix.data_result.source_sha256 != original.original_source_sha256
    ):
        raise ValueError("original_event_v2_g1_or_original_source_rejected")
    # This replays the whole current packet, including the V2 executable quote,
    # at the later native cutoff. The G1 source below is independently replayed.
    context = public_market_context_v2(
        checked, expected_bundle_sha256=checked.bundle_sha256, evaluated_at=now
    )
    old_market, old_analysis = _source(pre.prefix.data_result.source_json)
    current_market, _ = _source(g1.source_json)
    continuation = evaluate_continuation(
        old_market,
        old_analysis,
        current_market,
        detection=pre.prefix.detection,
        observed_at=now,
    )
    zone = pre.result.entry_zone
    if zone is None:
        raise ValueError("original_event_v2_original_zone_missing")
    values = {
        "continuation": continuation,
        "event_code": continuation.code,
        "origin_sha256": original.evaluation_sha256,
        "original_candidate_sha256": sha(
            canonical(original.candidate.model_dump(mode="json", round_trip=True))
        ),
        "original_event_key": original.original_event_key,
        "original_zone_sha256": sha(
            canonical(zone.model_dump(mode="json", round_trip=True))
        ),
        "original_policy_sha256": original.original_policy_sha256,
        "public_bundle_sha256": checked.bundle_sha256,
        "current_g1_sha256": g1.evaluation_sha256,
        "current_source_sha256": g1.source_sha256,
        "observed_at": now,
    }
    if not continuation.passed:
        return OriginalEventZoneDiagnosticV2(**values, zone_code="not_evaluated")
    if (
        continuation.original_event_key != original.original_event_key
        or continuation.original_source_sha256 != original.original_source_sha256
        or continuation.original_trigger_expires_at
        != pre.prefix.detection.trigger.expires_at
    ):
        raise ValueError("original_event_v2_event_pin_mismatch")
    rebuilt, code = build_entry_zone(
        pre.prefix.detection,
        tick_size=policy.tick_size,
        max_allowed_drift_bps=policy.max_allowed_drift_bps,
        expires_at=pre.prefix.timing.latest_valid_entry_time,
    )
    if rebuilt is None or code != "passed" or rebuilt != zone:
        return OriginalEventZoneDiagnosticV2(
            **values, zone_code="original_zone_replay_mismatch"
        )
    if (
        now < zone.created_at
        or now >= zone.expires_at
        or (zone.report_id, zone.instrument_id, zone.direction, zone.source_sha256)
        != (
            intent.report_id,
            intent.instrument_id,
            intent.direction,
            original.original_source_sha256,
        )
    ):
        return OriginalEventZoneDiagnosticV2(
            **values, zone_code="original_zone_scope_or_expiry_invalid"
        )
    quote = context.quote
    if (quote.report_id, quote.instrument_id) != (
        intent.report_id,
        intent.instrument_id,
    ):
        raise ValueError("original_event_v2_quote_scope_mismatch")
    reference = quote.ticker.ask if intent.direction == "long" else quote.ticker.bid
    audit = {}
    with localcontext(Context(prec=100)):
        code, _ = _evaluate_location_numeric_tail(
            entry=intent.candidate_entry,
            direction=intent.direction,
            zone=zone,
            reference=reference,
            bid=quote.ticker.bid,
            ask=quote.ticker.ask,
            mark_price=quote.mark.price,
            audit=audit,
        )
    return OriginalEventZoneDiagnosticV2(
        **values,
        zone_code=code,
        reference_price=reference,
        drift_bps=audit.get("drift_bps"),
    )
