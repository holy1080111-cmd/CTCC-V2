"""Fail-closed domain records for the entry-qualification pipeline.

These records validate consistency, not market truth or execution authority.
Gate evaluators, evidence production and the final executable-quote recheck
must supply their own evidence before a runtime consumer can use this domain.
Computed decision fields are output only. Use ``round_trip=True`` when dumping
model inputs for validated reconstruction; never trust ``model_copy(update=)``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Context, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializationInfo,
    ValidationInfo,
    computed_field,
    field_serializer,
    field_validator,
    model_validator,
)

ReportId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")]
Text = Annotated[str, Field(min_length=1, max_length=512)]
Price = Annotated[Decimal, Field(gt=0, max_digits=40, decimal_places=20)]
Measurement = str | int | bool | Decimal | None


class QualificationModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
        str_strip_whitespace=True,
    )


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("qualification timestamps must be timezone-aware")
    # Equal tzinfo objects otherwise use local wall time for Python arithmetic
    # and comparisons, which is not causal across a daylight-saving clock fold.
    return value.astimezone(UTC)


class MarketRegime(StrEnum):
    TREND = "Trend"
    RANGE = "Range"
    EXPANSION = "Expansion"
    COMPRESSION = "Compression"
    HIGH_VOLATILITY = "High Volatility"
    RISK_OFF = "Risk-Off"
    UNKNOWN = "Unknown"


class QualificationState(StrEnum):
    SIGNAL_DETECTED = "SIGNAL_DETECTED"
    DATA_VALID = "DATA_VALID"
    REGIME_VALID = "REGIME_VALID"
    HTF_VALID = "HTF_VALID"
    SETUP_VALID = "SETUP_VALID"
    TRIGGER_VALID = "TRIGGER_VALID"
    ENTRY_TIMING_VALID = "ENTRY_TIMING_VALID"
    ENTRY_LOCATION_VALID = "ENTRY_LOCATION_VALID"
    PROTECTION_VALID = "PROTECTION_VALID"
    ECONOMICS_VALID = "ECONOMICS_VALID"
    RISK_VALID = "RISK_VALID"
    EVIDENCE_COMPLETE = "EVIDENCE_COMPLETE"
    EXECUTION_RECHECK_PASS = "EXECUTION_RECHECK_PASS"
    ORDER_ELIGIBLE = "ORDER_ELIGIBLE"


class QualificationGate(StrEnum):
    DATA = "G1"
    REGIME = "G2"
    HTF = "G3"
    SETUP = "G4"
    TRIGGER = "G5"
    TIMING = "G6"
    LOCATION = "G7"
    STOP = "G8"
    TARGET = "G9"
    ECONOMICS = "G10"
    RISK = "G11"
    EVIDENCE = "G12"
    EXECUTION_RECHECK = "execution_recheck"


GATE_ORDER = tuple(QualificationGate)
_PASSED_STATES = (
    QualificationState.DATA_VALID,
    QualificationState.REGIME_VALID,
    QualificationState.HTF_VALID,
    QualificationState.SETUP_VALID,
    QualificationState.TRIGGER_VALID,
    QualificationState.ENTRY_TIMING_VALID,
    QualificationState.ENTRY_LOCATION_VALID,
    QualificationState.ENTRY_LOCATION_VALID,  # SL alone is not full protection.
    QualificationState.PROTECTION_VALID,
    QualificationState.ECONOMICS_VALID,
    QualificationState.RISK_VALID,
    QualificationState.EVIDENCE_COMPLETE,
    QualificationState.EXECUTION_RECHECK_PASS,
)


class EntryZone(QualificationModel):
    report_id: ReportId
    # Legacy records may omit provenance, but the actual location evaluator
    # rejects that absence. Free-form zone_source remains human-readable audit
    # text, never the instrument/direction identity boundary.
    instrument_id: Text | None = None
    direction: Literal["long", "short"] | None = None
    source_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    zone_low: Price
    zone_high: Price
    zone_type: Text
    zone_source: Text
    created_at: datetime
    expires_at: datetime
    max_allowed_drift_bps: Decimal = Field(
        ge=0, le=10000, max_digits=30, decimal_places=20
    )
    invalidation_price: Price

    _aware_times = field_validator("created_at", "expires_at")(require_aware)

    @model_validator(mode="after")
    def valid_zone(self) -> EntryZone:
        if self.zone_low > self.zone_high:
            raise ValueError("entry zone bounds are reversed")
        if self.expires_at <= self.created_at:
            raise ValueError("entry zone expiry must follow creation")
        if self.zone_low <= self.invalidation_price <= self.zone_high:
            raise ValueError("invalidation must be outside the entry zone")
        return self


class EntryTrigger(QualificationModel):
    report_id: ReportId
    trigger_type: Text
    trigger_time: datetime
    trigger_price: Price
    expires_at: datetime
    invalidation_reason: Text | None = None

    _aware_times = field_validator("trigger_time", "expires_at")(require_aware)

    @model_validator(mode="after")
    def valid_trigger(self) -> EntryTrigger:
        if self.expires_at <= self.trigger_time:
            raise ValueError("trigger expiry must follow trigger time")
        return self


class GateAssessment(QualificationModel):
    report_id: ReportId
    gate: QualificationGate
    passed: bool
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,95}$")]
    reason: Text
    measured_values: Mapping[Text, Measurement] = Field(min_length=1, max_length=32)

    @field_validator("measured_values", mode="before")
    @classmethod
    def decode_decimal_measurements(cls, values, info: ValidationInfo):
        if info.mode != "json" or not isinstance(values, dict):
            return values
        decoded = {}
        for key, value in values.items():
            if isinstance(value, dict):
                if (
                    set(value) != {"type", "value"}
                    or value["type"] != "decimal"
                    or not isinstance(value["value"], str)
                    or len(value["value"]) > 128
                ):
                    raise ValueError("invalid typed decimal measurement")
                try:
                    value = Decimal(value["value"])
                except InvalidOperation as exc:
                    raise ValueError("invalid decimal measurement") from exc
            decoded[key] = value
        return decoded

    @field_validator("measured_values")
    @classmethod
    def bounded_values(cls, values: Mapping[str, Measurement]):
        for value in values.values():
            if isinstance(value, str) and len(value) > 512:
                raise ValueError("measurement text is too long")
            if isinstance(value, int) and abs(value) > 10**40:
                raise ValueError("measurement integer is too large")
            if isinstance(value, Decimal) and len(str(value)) > 128:
                raise ValueError("measurement decimal is too long")
        return MappingProxyType(dict(values))

    @field_serializer("measured_values")
    def serialize_values(
        self, values: Mapping[str, Measurement], info: SerializationInfo
    ):
        return {
            key: {"type": "decimal", "value": str(value)}
            if info.mode == "json" and isinstance(value, Decimal)
            else value
            for key, value in values.items()
        }

    @model_validator(mode="after")
    def consistent_code(self) -> GateAssessment:
        if self.passed != (self.code == "passed"):
            raise ValueError("passed gates use passed; failures need a fail code")
        return self


class EntryQualificationResult(QualificationModel):
    report_id: ReportId
    symbol: Text
    strategy: Text
    direction: Literal["long", "short"]
    evaluated_at: datetime
    raw_score: int = Field(ge=0, le=100)
    effective_score: int = Field(ge=0, le=100)
    market_regime: MarketRegime = MarketRegime.UNKNOWN
    htf_bias: Literal["long", "short", "neutral"] | None = None
    setup_state: Literal["not_evaluated", "waiting", "valid", "invalid"] = (
        "not_evaluated"
    )
    entry_timing_state: Literal["not_evaluated", "wait", "valid", "cancel"] = (
        "not_evaluated"
    )
    trigger: EntryTrigger | None = None
    entry_zone: EntryZone | None = None
    candidate_entry: Price | None = None
    reference_price: Price | None = None
    stop_loss: Price | None = None
    take_profit: Price | None = None
    gross_rr: Decimal | None = Field(
        default=None, gt=0, max_digits=60, decimal_places=30
    )
    net_rr: Decimal | None = Field(default=None, gt=0, max_digits=60, decimal_places=30)
    gates: tuple[GateAssessment, ...] = Field(default=(), max_length=13)

    _aware_time = field_validator("evaluated_at")(require_aware)

    def _passed(self, gate: QualificationGate) -> bool:
        return any(item.gate == gate and item.passed for item in self.gates)

    @model_validator(mode="after")
    def consistent_chain(self) -> EntryQualificationResult:
        if self.effective_score > self.raw_score:
            raise ValueError("effective score cannot raise the raw score")
        if tuple(item.gate for item in self.gates) != GATE_ORDER[: len(self.gates)]:
            raise ValueError("gates must form the ordered, nonduplicate prefix")
        if any(not item.passed for item in self.gates[:-1]):
            raise ValueError("qualification must stop at the first failed gate")
        records = (*self.gates, self.trigger, self.entry_zone)
        if any(item and item.report_id != self.report_id for item in records):
            raise ValueError("all qualification records must use the same report_id")
        if self._passed(QualificationGate.REGIME) and self.market_regime in {
            MarketRegime.UNKNOWN,
            MarketRegime.RISK_OFF,
        }:
            raise ValueError("unknown/risk-off regime cannot pass")
        if self._passed(QualificationGate.HTF) and self.htf_bias is None:
            raise ValueError("HTF evidence is missing")
        if self._passed(QualificationGate.SETUP) and self.setup_state != "valid":
            raise ValueError("setup evidence is not valid")
        if self._passed(QualificationGate.TRIGGER):
            trigger = self.trigger
            if (
                trigger is None
                or trigger.invalidation_reason is not None
                or not trigger.trigger_time <= self.evaluated_at < trigger.expires_at
            ):
                raise ValueError("passing trigger requires a current valid event")
        if (
            self._passed(QualificationGate.TIMING)
            and self.entry_timing_state != "valid"
        ):
            raise ValueError("entry timing evidence is not valid")
        if self._passed(QualificationGate.LOCATION):
            zone, reference = self.entry_zone, self.reference_price
            if (
                zone is None
                or reference is None
                or self.candidate_entry is None
                or not zone.created_at <= self.evaluated_at < zone.expires_at
                or not zone.zone_low <= reference <= zone.zone_high
                or not zone.zone_low <= self.candidate_entry <= zone.zone_high
                or self.entry_drift_bps is None
                or self.entry_drift_bps > zone.max_allowed_drift_bps
                or (self.direction == "long" and zone.invalidation_price >= reference)
                or (self.direction == "short" and zone.invalidation_price <= reference)
            ):
                raise ValueError("passing location requires a valid current entry zone")
        entry = self.candidate_entry
        if self._passed(QualificationGate.STOP) and (
            entry is None
            or self.stop_loss is None
            or (self.direction == "long" and self.stop_loss >= entry)
            or (self.direction == "short" and self.stop_loss <= entry)
        ):
            raise ValueError("invalid stop geometry")
        if self._passed(QualificationGate.TARGET) and (
            entry is None
            or self.take_profit is None
            or (self.direction == "long" and self.take_profit <= entry)
            or (self.direction == "short" and self.take_profit >= entry)
        ):
            raise ValueError("invalid target geometry")
        if self._passed(QualificationGate.ECONOMICS) and (
            self.gross_rr is None or self.net_rr is None or self.net_rr > self.gross_rr
        ):
            raise ValueError("passing economics requires consistent RR evidence")
        if self._passed(QualificationGate.ECONOMICS):
            # All geometry operands are present and nonzero after G7/G8/G9.
            # Use a fresh context: callers' precision/rounding/traps must not
            # change eligibility. RR reports agree to twelve decimal places.
            with localcontext(Context(prec=100)):
                geometry_rr = abs(self.take_profit - entry) / abs(
                    entry - self.stop_loss
                )
                reporting_quantum = Decimal("0.000000000001")
                if geometry_rr.quantize(reporting_quantum) != self.gross_rr.quantize(
                    reporting_quantum
                ):
                    raise ValueError("gross RR does not match candidate price geometry")
        return self

    @computed_field
    @property
    def qualified(self) -> bool:
        return len(self.gates) == len(GATE_ORDER) and all(
            item.passed for item in self.gates
        )

    @computed_field
    @property
    def state(self) -> QualificationState:
        if self.qualified:
            return QualificationState.ORDER_ELIGIBLE
        count = sum(item.passed for item in self.gates)
        return (
            _PASSED_STATES[count - 1] if count else (QualificationState.SIGNAL_DETECTED)
        )

    @computed_field
    @property
    def trigger_state(self) -> str:
        if self.trigger is None:
            return "missing"
        if self.trigger.invalidation_reason:
            return "invalidated"
        if self.evaluated_at < self.trigger.trigger_time:
            return "pending"
        return "expired" if self.evaluated_at >= self.trigger.expires_at else "valid"

    @computed_field
    @property
    def trigger_timestamp(self) -> datetime | None:
        return self.trigger.trigger_time if self.trigger else None

    @computed_field
    @property
    def entry_drift_bps(self) -> Decimal | None:
        if self.candidate_entry is None or self.reference_price is None:
            return None
        with localcontext(Context(prec=100)):
            return (
                abs(self.reference_price - self.candidate_entry)
                / self.candidate_entry
                * Decimal(10000)
            )

    @computed_field
    @property
    def risk_permission(self) -> bool:
        return self._passed(QualificationGate.RISK)

    @computed_field
    @property
    def evidence_complete(self) -> bool:
        return self._passed(QualificationGate.EVIDENCE)

    @computed_field
    @property
    def execution_recheck_passed(self) -> bool:
        return self._passed(QualificationGate.EXECUTION_RECHECK)

    @computed_field
    @property
    def failed_gates(self) -> tuple[QualificationGate, ...]:
        return tuple(item.gate for item in self.gates if not item.passed)

    @computed_field
    @property
    def fail_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.gates if not item.passed)
