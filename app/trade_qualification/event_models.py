"""Source-bound event records, not caller-granted trading permission."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.trade_qualification.models import (
    EntryTrigger,
    Price,
    QualificationModel,
    ReportId,
    Text,
    require_aware,
)

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
StrategyName = Literal[
    "trend_pullback",
    "breakout_continuation",
    "liquidity_sweep_reversal",
    "fvg_return",
    "order_block_return",
    "range_reversal",
    "structure_reversal",
    "volatility_expansion",
]


class TriggerDetection(QualificationModel):
    report_id: ReportId
    symbol: Text
    instrument_id: Text
    strategy: StrategyName
    direction: Literal["long", "short"]
    observed_at: datetime
    source_sha256: Digest | None
    source_timeframe: Literal["5m", "15m", "1H", "4H"]
    setup_time: datetime | None = None
    setup_type: Text | None = None
    trigger: EntryTrigger | None = None
    invalidation_price: Price | None = None
    setup_basis: tuple[tuple[Text, Text], ...] = ()
    fail_codes: tuple[Text, ...] = ()

    _aware = field_validator("observed_at")(require_aware)

    @field_validator("setup_time")
    @classmethod
    def aware_setup(cls, value):
        return None if value is None else require_aware(value)

    @model_validator(mode="after")
    def consistent_event(self):
        if len(self.setup_basis) > 32 or len(dict(self.setup_basis)) != len(
            self.setup_basis
        ):
            raise ValueError("event basis must be bounded and uniquely keyed")
        if self.setup_time is not None and self.setup_time > self.observed_at:
            raise ValueError("setup cannot follow observation")
        if self.trigger is not None:
            if self.trigger.report_id != self.report_id:
                raise ValueError("trigger report mismatch")
            if self.setup_time is None or not (
                self.setup_time <= self.trigger.trigger_time <= self.observed_at
            ):
                raise ValueError("trigger must follow observed setup")
            if self.source_sha256 is None or self.invalidation_price is None:
                raise ValueError("trigger requires source identity and invalidation")
        return self
