"""Closed-candle entry windows; expiry cannot be renewed by copying a candidate.

Defaults are conservative engineering limits, not calibrated trading edge.
An expired or already used event requires a new event, not a new report ID alone.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from types import MappingProxyType
from typing import Literal

from pydantic import Field, computed_field, field_validator, model_validator

from app.market.quality.candles import BAR_SECONDS
from app.trade_qualification.event_models import TriggerDetection
from app.trade_qualification.models import (
    QualificationModel,
    ReportId,
    Text,
    require_aware,
)


class TimingPolicy(QualificationModel):
    policy_id: Text
    bar_seconds: int = Field(default=300, ge=1, le=14400)
    maximum_trigger_bars: int = Field(ge=1, le=12)
    max_setup_to_trigger_seconds: int = Field(ge=1, le=86400)
    minimum_wait_seconds: int = Field(default=0, ge=0, le=3600)
    max_favorable_move_r: Decimal = Field(default=Decimal("0.5"), gt=0, le=2)
    candle_close_only: Literal[True] = True
    intrabar_allowed: Literal[False] = False
    retracement_reentry_allowed: Literal[False] = False

    @field_validator(
        "candle_close_only",
        "intrabar_allowed",
        "retracement_reentry_allowed",
        mode="before",
    )
    @classmethod
    def exact_policy_flags(cls, value):
        if type(value) is not bool:
            raise ValueError("timing policy flags require exact booleans")
        return value

    @computed_field
    @property
    def trigger_ttl_seconds(self) -> int:
        return self.bar_seconds * self.maximum_trigger_bars

    @model_validator(mode="after")
    def valid_window(self):
        if self.minimum_wait_seconds >= self.trigger_ttl_seconds:
            raise ValueError("minimum wait must precede the timing deadline")
        return self


TIMING_POLICIES = MappingProxyType(
    {
        name: TimingPolicy(
            policy_id=f"closed-v1:{name}",
            maximum_trigger_bars=bars,
            max_setup_to_trigger_seconds=setup_age,
        )
        for name, bars, setup_age in (
            ("trend_pullback", 2, 3600),
            ("breakout_continuation", 1, 1800),
            ("liquidity_sweep_reversal", 1, 1800),
            ("fvg_return", 2, 14400),
            ("order_block_return", 2, 14400),
            ("range_reversal", 1, 1800),
            ("structure_reversal", 2, 7200),
            ("volatility_expansion", 1, 3600),
        )
    }
)


class TimingResult(QualificationModel):
    report_id: ReportId
    action: Literal["WAIT", "CANCEL", "CONTINUE"]
    code: Text
    reason: Text
    setup_time: datetime | None
    trigger_time: datetime | None
    current_time: datetime
    candles_since_trigger: int | None
    seconds_since_trigger: int | None
    timing_window_type: Text
    latest_valid_entry_time: datetime | None
    late_entry_risk: Literal["unknown", "low", "high"]
    event_key: str | None

    _aware = field_validator("current_time")(require_aware)

    @computed_field
    @property
    def timing_valid(self) -> bool:
        return self.action == "CONTINUE" and self.code == "passed"

    @model_validator(mode="after")
    def consistent_result(self):
        if (self.action == "CONTINUE") != (self.code == "passed"):
            raise ValueError("only a passing result can continue")
        for value in (self.setup_time, self.trigger_time, self.latest_valid_entry_time):
            if value is not None:
                require_aware(value)
        return self


def event_identity(detection: TriggerDetection) -> str | None:
    """Stable across reports/snapshot refreshes; a report rename is not reentry."""
    if detection.trigger is None:
        return None
    event = detection.trigger
    payload = [
        detection.instrument_id,
        detection.strategy,
        detection.direction,
        event.trigger_type,
        event.trigger_time.astimezone(UTC).isoformat(),
        # Decimal numeric identity is independent of trailing zero spelling.
        format(event.trigger_price, "f").rstrip("0").rstrip(".")
        if "." in format(event.trigger_price, "f")
        else format(event.trigger_price, "f"),
    ]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":")).encode()
    ).hexdigest()


def evaluate_timing(
    detection: TriggerDetection,
    *,
    current_time: datetime,
    candidate_created_at: datetime,
    candidate_expires_at: datetime,
    reference_price: Decimal,
    consumed_event_keys: frozenset[str] = frozenset(),
    policy: TimingPolicy | None = None,
) -> TimingResult:
    """Evaluate evidence at an explicit clock and price without IO or score input.

    The pipeline supplies trusted observation/quote data and a durable consumed
    event set. This pure evaluator is not itself an authority or replay ledger.
    """
    current_time = require_aware(current_time).astimezone(UTC)
    candidate_created_at = require_aware(candidate_created_at).astimezone(UTC)
    candidate_expires_at = require_aware(candidate_expires_at).astimezone(UTC)
    # Reconstruct nested records so ordinary model_copy cannot skip validation.
    detection = TriggerDetection.model_validate(detection.model_dump(round_trip=True))
    selected_policy = policy or TIMING_POLICIES[detection.strategy]
    selected_policy = TimingPolicy.model_validate(
        selected_policy.model_dump(round_trip=True)
    )
    trigger = detection.trigger
    trigger_time = None if trigger is None else trigger.trigger_time.astimezone(UTC)
    trigger_expires_at = None if trigger is None else trigger.expires_at.astimezone(UTC)
    setup_time = (
        None if detection.setup_time is None else detection.setup_time.astimezone(UTC)
    )
    key = event_identity(detection)
    elapsed = (
        None
        if trigger is None
        else (current_time - trigger_time) // timedelta(seconds=1)
    )
    deadline = (
        None
        if trigger is None
        else min(
            trigger_expires_at,
            trigger_time + timedelta(seconds=selected_policy.trigger_ttl_seconds),
            candidate_expires_at,
        )
    )

    def result(action, code, reason):
        return TimingResult(
            report_id=detection.report_id,
            action=action,
            code=code,
            reason=reason,
            setup_time=setup_time,
            trigger_time=trigger_time,
            current_time=current_time,
            candles_since_trigger=None
            if elapsed is None or elapsed < 0
            else elapsed // selected_policy.bar_seconds,
            seconds_since_trigger=elapsed,
            timing_window_type=selected_policy.policy_id,
            latest_valid_entry_time=deadline,
            late_entry_risk=(
                "high"
                if action == "CANCEL"
                else "low"
                if action == "CONTINUE"
                else "unknown"
            ),
            event_key=key,
        )

    if (
        not isinstance(reference_price, Decimal)
        or not reference_price.is_finite()
        or reference_price <= 0
    ):
        return result(
            "CANCEL", "reference_price_invalid", "Executable reference is invalid."
        )
    if (
        candidate_expires_at <= candidate_created_at
        or current_time >= candidate_expires_at
    ):
        return result(
            "CANCEL", "stale_candidate", "Candidate lifetime has ended or is invalid."
        )
    if trigger is not None and current_time >= trigger_expires_at:
        return result("CANCEL", "trigger_expired", "The original trigger has expired.")
    if trigger is not None and deadline is not None and current_time >= deadline:
        return result(
            "CANCEL", "entry_window_expired", "The strategy entry window has ended."
        )
    if selected_policy.bar_seconds != BAR_SECONDS[detection.source_timeframe]:
        return result(
            "CANCEL",
            "timing_policy_mismatch",
            "Timing bars must match the trigger source.",
        )
    if (
        current_time < candidate_created_at
        or current_time < detection.observed_at.astimezone(UTC)
    ):
        return result(
            "WAIT", "entry_window_not_open", "Evaluation clock predates the evidence."
        )
    if detection.fail_codes:
        action = (
            "WAIT"
            if set(detection.fail_codes) <= {"trigger_missing", "setup_missing"}
            else "CANCEL"
        )
        return result(
            action,
            detection.fail_codes[0],
            "Source event did not pass its required checks.",
        )
    if detection.setup_time is None or trigger is None:
        return result(
            "WAIT",
            "too_early_no_trigger",
            "Wait for a completed setup and a new trigger event.",
        )
    if trigger.invalidation_reason:
        return result("CANCEL", "trigger_invalidated", trigger.invalidation_reason)
    if key in consumed_event_keys or candidate_created_at < trigger_time:
        return result(
            "CANCEL",
            "stale_candidate",
            "A reused event or old candidate cannot acquire new authority.",
        )
    if (
        trigger_time - setup_time
    ).total_seconds() > selected_policy.max_setup_to_trigger_seconds:
        return result(
            "CANCEL", "late_entry", "The trigger followed an already stale setup."
        )
    if current_time < trigger_time + timedelta(
        seconds=selected_policy.minimum_wait_seconds
    ):
        return result(
            "WAIT",
            "entry_window_not_open",
            "The configured closed-event entry window is not open.",
        )
    anchor = detection.invalidation_price
    with localcontext(Context(prec=100)):
        risk = (
            trigger.trigger_price - anchor
            if detection.direction == "long"
            else anchor - trigger.trigger_price
        )
        favorable_move = (
            reference_price - trigger.trigger_price
            if detection.direction == "long"
            else trigger.trigger_price - reference_price
        )
        if risk <= 0:
            return result(
                "CANCEL",
                "trigger_invalidated",
                "Trigger invalidation geometry is inconsistent.",
            )
        if (detection.direction == "long" and reference_price <= anchor) or (
            detection.direction == "short" and reference_price >= anchor
        ):
            return result(
                "CANCEL",
                "trigger_invalidated",
                "Price crossed the recorded invalidation.",
            )
        if favorable_move >= risk * selected_policy.max_favorable_move_r:
            return result(
                "CANCEL",
                "impulse_already_extended",
                "Price already consumed the allowed impulse distance.",
            )
    return result(
        "CONTINUE",
        "passed",
        "Closed trigger remains inside its original strategy entry window.",
    )
