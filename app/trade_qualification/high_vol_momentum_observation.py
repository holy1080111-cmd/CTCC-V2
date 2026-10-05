"""Source-replayed HighVol/Momentum observation; never a qualification gate.

This versioned, default-off profile records whether the original source-bound
event satisfies a narrow Demo high-volatility policy. It does not change the
legacy regime router, strategy scoring, candidate, G1--G12 or submit boundary.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, DecimalException, localcontext
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.analysis.service import analyze_snapshot_at
from app.domain.market import MarketSnapshot
from app.market.quality.candles import BAR_SECONDS, candle_closed_at
from app.strategies.mathematical_confirmation import mathematical_confirmation
from app.strategies.regime import RouteDecision, route_regime
from app.trade_qualification.continuation import _prepared_market
from app.trade_qualification.event_models import Digest
from app.trade_qualification.events import extract_trigger
from app.trade_qualification.models import (
    MarketRegime,
    QualificationModel,
    ReportId,
    Text,
    require_aware,
)
from app.trade_qualification.original_candidate_precursor_v2 import (
    POLICY_SHA256 as ORIGINAL_CANDIDATE_POLICY_SHA256,
)
from app.trade_qualification.timing import TIMING_POLICIES, TimingPolicy, event_identity

PROFILE_ID = "ctcc-demo-high-vol-momentum-observation-v2"
_SPEC = {
    "profile_id": PROFILE_ID,
    "environment": "demo",
    "default_enabled": False,
    "strategies": ["breakout_continuation", "volatility_expansion"],
    "source": "exact_four_closed_timeframes_rebuilt_from_market",
    "analysis_version": "recorded_exact_in_observation",
    "route": "preserve_original_high_volatility_no_trade",
    "direction": "closed_15m_BOS_with_nonopposed_4H_1H_15m_5m",
    "history": "original_swing_break_or_prior_compression_then_new_5m_transition",
    "event": "original_source_hash_event_key_trigger_times_and_complete_timing_policy_hash",
    "effective_expiry": "minimum_original_trigger_candidate_and_current_policy_expiry",
    "shock": "each_measured_causal_state_below_0.65_unknown_denied",
    "math": "source_recomputed_confirmed_direction_no_instability",
    "score_or_execution_authority": False,
}
PROFILE_SHA256 = hashlib.sha256(
    json.dumps(_SPEC, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
_SETUPS = {
    "breakout_continuation": "confirmed_swing_break",
    "volatility_expansion": "observed_compression_break",
}
_FRAMES = ("4H", "1H", "15m", "5m")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def timing_policy_sha256(policy: TimingPolicy) -> str:
    """Hash the complete validated timing contract, including computed TTL."""
    if type(policy) is not TimingPolicy:
        raise ValueError("exact timing policy required")
    checked = TimingPolicy.model_validate(
        policy.model_dump(round_trip=True), strict=True
    )
    return hashlib.sha256(
        json.dumps(
            checked.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


class HighVolOriginalSourcePin(QualificationModel):
    """Caller-supplied identity to replay, never proof of an actual candidate."""

    environment: Literal["demo"] = "demo"
    report_id: ReportId
    instrument_id: Text
    strategy: Literal["breakout_continuation", "volatility_expansion"]
    direction: Literal["long", "short"]
    source_sha256: Digest
    event_key: Digest
    candidate_policy_sha256: Digest
    profile_sha256: Digest
    timing_policy_sha256: Digest
    trigger_ttl_seconds: int = Field(ge=1, le=3600)
    max_setup_to_trigger_seconds: int = Field(ge=1, le=86400)
    original_setup_time: datetime
    original_trigger_time: datetime
    original_trigger_expires_at: datetime
    original_candidate_expires_at: datetime

    @field_validator(
        "original_setup_time",
        "original_trigger_time",
        "original_trigger_expires_at",
        "original_candidate_expires_at",
    )
    @classmethod
    def aware_original_times(cls, value):
        return require_aware(value)

    @model_validator(mode="after")
    def fixed_original_window(self):
        if (
            not self.original_setup_time
            < self.original_trigger_time
            < self.original_candidate_expires_at
            <= self.original_trigger_expires_at
            or self.original_trigger_expires_at
            != self.original_trigger_time + timedelta(seconds=self.trigger_ttl_seconds)
            or self.original_trigger_time - self.original_setup_time
            > timedelta(seconds=self.max_setup_to_trigger_seconds)
        ):
            raise ValueError("original event timing pin is inconsistent")
        return self


class HighVolMomentumObservation(QualificationModel):
    """A computed research diagnostic that no runtime consumer may spend."""

    profile_id: Literal["ctcc-demo-high-vol-momentum-observation-v2"] = PROFILE_ID
    profile_sha256: Digest = PROFILE_SHA256
    original_candidate_policy_sha256: Digest
    analysis_version: Text
    report_id: ReportId
    instrument_id: Text
    strategy: Literal["breakout_continuation", "volatility_expansion"]
    direction: Literal["long", "short"]
    observed_at: datetime
    code: Text
    source_conditions_met: bool
    original_source_sha256: Digest
    original_event_key: Digest
    original_timing_policy_sha256: Digest
    current_timing_policy_sha256: Digest
    original_trigger_ttl_seconds: int = Field(ge=1, le=3600)
    original_setup_time: datetime
    original_trigger_time: datetime
    original_trigger_expires_at: datetime
    original_candidate_expires_at: datetime
    effective_expires_at: datetime
    recomputed_source_sha256: Digest | None = None
    recomputed_event_key: Digest | None = None
    route_sha256: Digest | None = None
    setup_time: datetime | None = None
    trigger_time: datetime | None = None
    trigger_expires_at: datetime | None = None
    admission: Literal["DENY"] = "DENY"
    source_authenticity_verified: Literal[False] = False
    qualification_performed: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator(
        "observed_at",
        "original_setup_time",
        "original_trigger_time",
        "original_trigger_expires_at",
        "original_candidate_expires_at",
        "effective_expires_at",
        "setup_time",
        "trigger_time",
        "trigger_expires_at",
    )
    @classmethod
    def aware_times(cls, value):
        return None if value is None else require_aware(value)

    @model_validator(mode="after")
    def consistent(self):
        if (
            len(self.analysis_version) > 64
            or self.analysis_version != self.analysis_version.strip()
        ):
            raise ValueError("canonical analysis version required")
        if self.source_conditions_met != (self.code == "source_conditions_met"):
            raise ValueError("source-condition observation and code disagree")
        if (
            self.original_trigger_expires_at
            != self.original_trigger_time
            + timedelta(seconds=self.original_trigger_ttl_seconds)
            or self.effective_expires_at
            > min(self.original_trigger_expires_at, self.original_candidate_expires_at)
            or self.source_conditions_met
            and self.observed_at >= self.effective_expires_at
        ):
            raise ValueError("observed event may not extend its original expiry")
        if self.trigger_time is not None and (
            self.setup_time is None
            or self.trigger_expires_at is None
            or not self.setup_time < self.trigger_time < self.trigger_expires_at
        ):
            raise ValueError("observed event chronology is inconsistent")
        return self


def observe_high_vol_momentum(
    market: MarketSnapshot,
    *,
    original: HighVolOriginalSourcePin,
    observed_at: datetime,
    analysis_version: str,
    enabled: bool = False,
) -> HighVolMomentumObservation:
    """Recompute source facts and the *same* event without allowing entry.

    The original source pin is untrusted input. Matching it is diagnostic only:
    this function never authenticates a market capture or creates a candidate.
    """
    if type(original) is not HighVolOriginalSourcePin:
        raise ValueError("exact original source pin required")
    original = HighVolOriginalSourcePin.model_validate(
        original.model_dump(round_trip=True), strict=True
    )
    if type(observed_at) is not datetime:
        raise ValueError("exact observation time required")
    now = require_aware(observed_at)
    if type(enabled) is not bool:
        raise ValueError("profile switch requires an exact boolean")
    if (
        type(analysis_version) is not str
        or not 0 < len(analysis_version) <= 64
        or analysis_version != analysis_version.strip()
    ):
        raise ValueError("canonical analysis version required")
    current_timing = TIMING_POLICIES[original.strategy]
    current_timing_sha256 = timing_policy_sha256(current_timing)
    effective_expires_at = min(
        original.original_trigger_expires_at,
        original.original_candidate_expires_at,
        original.original_trigger_time
        + timedelta(seconds=current_timing.trigger_ttl_seconds),
    )
    values = {
        "original_candidate_policy_sha256": original.candidate_policy_sha256,
        "analysis_version": analysis_version,
        "report_id": original.report_id,
        "instrument_id": original.instrument_id,
        "strategy": original.strategy,
        "direction": original.direction,
        "observed_at": now,
        "original_source_sha256": original.source_sha256,
        "original_event_key": original.event_key,
        "original_timing_policy_sha256": original.timing_policy_sha256,
        "current_timing_policy_sha256": current_timing_sha256,
        "original_trigger_ttl_seconds": original.trigger_ttl_seconds,
        "original_setup_time": original.original_setup_time,
        "original_trigger_time": original.original_trigger_time,
        "original_trigger_expires_at": original.original_trigger_expires_at,
        "original_candidate_expires_at": original.original_candidate_expires_at,
        "effective_expires_at": effective_expires_at,
    }

    def finish(code: str) -> HighVolMomentumObservation:
        return HighVolMomentumObservation(
            **values, code=code, source_conditions_met=code == "source_conditions_met"
        )

    if (
        original.profile_sha256 != PROFILE_SHA256
        or original.candidate_policy_sha256 != ORIGINAL_CANDIDATE_POLICY_SHA256
    ):
        return finish("original_policy_pin_mismatch")
    if (
        original.timing_policy_sha256 != current_timing_sha256
        or original.trigger_ttl_seconds != current_timing.trigger_ttl_seconds
        or original.max_setup_to_trigger_seconds
        != current_timing.max_setup_to_trigger_seconds
    ):
        return finish("timing_policy_drift")
    if not enabled:
        return finish("profile_disabled")
    if now >= effective_expires_at:
        return finish("original_event_expired_or_invalid")
    try:
        if type(market) is not MarketSnapshot:
            raise ValueError("exact market source required")
        checked = _prepared_market(market, now)
        if checked.instrument_id != original.instrument_id:
            return finish("instrument_changed")
        for frame in _FRAMES:
            rows = checked.candles[frame]
            if len(rows) < 200:
                return finish("insufficient_history")
            seconds = BAR_SECONDS[frame]
            expected = _EPOCH + timedelta(
                seconds=((now - _EPOCH) // timedelta(seconds=seconds)) * seconds
            )
            if candle_closed_at(rows[-1], frame) != expected:
                return finish("confirmed_tail_missing")
        with localcontext(Context(prec=100)):
            analysis = analyze_snapshot_at(
                checked, evaluated_at=now, version=analysis_version
            )
            route = route_regime(analysis)
            detection = extract_trigger(
                checked,
                analysis,
                report_id=original.report_id,
                strategy=original.strategy,
                direction=original.direction,
                observed_at=now,
                trigger_ttl_seconds=original.trigger_ttl_seconds,
            )
        values.update(
            route_sha256=route.snapshot_sha256,
            recomputed_source_sha256=detection.source_sha256,
            recomputed_event_key=event_identity(detection),
            setup_time=detection.setup_time,
            trigger_time=(
                detection.trigger.trigger_time if detection.trigger else None
            ),
            trigger_expires_at=(
                detection.trigger.expires_at if detection.trigger else None
            ),
        )
        if detection.source_sha256 != original.source_sha256:
            return finish("original_source_changed")
        if (
            detection.fail_codes
            or detection.trigger is None
            or detection.setup_time is None
            or detection.setup_type != _SETUPS[original.strategy]
        ):
            return finish("source_chronology_missing")
        if event_identity(detection) != original.event_key:
            return finish("original_event_changed")
        if (
            not detection.setup_time < detection.trigger.trigger_time
            or detection.trigger.trigger_time - detection.setup_time
            > timedelta(seconds=original.max_setup_to_trigger_seconds)
            or detection.setup_time != original.original_setup_time
            or detection.trigger.trigger_time != original.original_trigger_time
            or detection.trigger.expires_at != original.original_trigger_expires_at
            or now >= effective_expires_at
        ):
            return finish("original_event_expired_or_invalid")
        if (
            route.regime != MarketRegime.HIGH_VOLATILITY
            or route.decision != RouteDecision.NO_TRADE
        ):
            return finish("not_blocked_high_volatility")
        if any(
            blocker != "multi_timeframe_not_aligned" for blocker in analysis.blockers
        ):
            return finish("analysis_safety_blocked")
        views = analysis.timeframe_analyses
        if (
            any(
                not views[frame].data_quality_ok or views[frame].data_quality_issues
                for frame in _FRAMES
            )
            or any(views[frame].volatility == "extreme" for frame in _FRAMES)
            or not any(views[frame].volatility == "high" for frame in _FRAMES)
        ):
            return finish("volatility_or_quality_blocked")
        expected_bos = "up" if original.direction == "long" else "down"
        opposite = "short" if original.direction == "long" else "long"
        if (
            views["15m"].structure.bos != expected_bos
            or any(views[frame].directional_bias == opposite for frame in _FRAMES)
            or not any(
                views[frame].directional_bias == original.direction
                for frame in ("4H", "1H")
            )
            or (
                original.strategy == "volatility_expansion"
                and views["15m"].volatility != "high"
            )
        ):
            return finish("source_htf_direction_missing")
        if any(
            views[frame].indicators.causal_state is None
            or views[frame].indicators.causal_state.shock_score >= Decimal("0.65")
            for frame in _FRAMES
        ):
            return finish("causal_shock_or_unknown")
        with localcontext(Context(prec=100)):
            math = mathematical_confirmation(analysis, original.direction)
        if math.status != "confirmed" or math.risk_grade == "blocked":
            return finish("mathematical_direction_unconfirmed")
        return finish("source_conditions_met")
    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        OverflowError,
        DecimalException,
    ):
        return finish("source_invalid")
