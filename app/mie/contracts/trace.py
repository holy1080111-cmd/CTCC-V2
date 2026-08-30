from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from app.mie.contracts._base import MieContract, require_utc
from app.mie.contracts.decision import DecisionCandidate
from app.mie.contracts.decision import DecisionAction
from app.mie.contracts.evidence import Evidence
from app.mie.contracts.forecast import (
    CalibrationStatus,
    ProbabilityForecast,
)
from app.mie.contracts.health import ModelHealth, ModelHealthStatus
from app.mie.contracts.regime import RegimeSnapshot
from app.mie.contracts.validation import (
    EvidenceUse,
    ValidationLevel,
    validation_at_least,
)


class MieShadowTrace(MieContract):
    """Replayable MIE chain that is structurally incapable of execution."""

    trace_id: UUID = Field(default_factory=uuid4)
    feature_snapshot_id: str = Field(min_length=1, max_length=160)
    evidence: tuple[Evidence, ...] = Field(min_length=1)
    forecast: ProbabilityForecast
    regime: RegimeSnapshot
    model_health: tuple[ModelHealth, ...] = Field(min_length=1)
    decision: DecisionCandidate
    created_at: datetime
    authority: Literal["shadow_only"] = "shadow_only"
    execution_authority: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return require_utc(value, "created_at")

    @model_validator(mode="after")
    def validate_trace_links(self) -> "MieShadowTrace":
        nested_contracts = [
            *self.evidence,
            self.forecast,
            self.regime,
            *self.model_health,
            self.decision,
        ]
        if self.execution_authority or any(
            item.execution_authority for item in nested_contracts
        ):
            raise ValueError("MIE shadow trace cannot carry execution authority")
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("shadow trace evidence ids must be unique")
        if set(self.forecast.evidence_ids) != set(evidence_ids):
            raise ValueError("forecast must reference all trace evidence")
        if not set(self.regime.evidence_ids).issubset(set(evidence_ids)):
            raise ValueError("regime references evidence outside the trace")
        if set(self.decision.evidence_ids) != set(evidence_ids):
            raise ValueError("decision must reference all trace evidence")
        if self.decision.forecast_id != self.forecast.forecast_id:
            raise ValueError("decision forecast link is invalid")
        if (
            self.decision.regime_snapshot_id
            != self.regime.regime_snapshot_id
        ):
            raise ValueError("decision regime link is invalid")

        health_ids = tuple(item.health_id for item in self.model_health)
        if len(health_ids) != len(set(health_ids)):
            raise ValueError("shadow trace model health ids must be unique")
        health_model_ids = tuple(
            item.model_id for item in self.model_health
        )
        if len(health_model_ids) != len(set(health_model_ids)):
            raise ValueError(
                "shadow trace model health identities must be unique"
            )
        if set(self.decision.model_health_ids) != set(health_ids):
            raise ValueError("decision must reference all model health records")
        required_model_health = (
            self.forecast.model_id,
            self.regime.model_id,
            self.decision.logic_id,
        )
        if len(required_model_health) != len(set(required_model_health)):
            raise ValueError(
                "forecast, regime, and logic require distinct model identities"
            )
        health_by_model_id = {
            health.model_id: health for health in self.model_health
        }
        if not set(required_model_health).issubset(health_by_model_id):
            raise ValueError(
                "forecast, regime, and logic each require their own "
                "model health record"
            )
        forecast_health = health_by_model_id[self.forecast.model_id]
        regime_health = health_by_model_id[self.regime.model_id]
        logic_health = health_by_model_id[self.decision.logic_id]
        if forecast_health.model_version != self.forecast.model_version:
            raise ValueError("forecast model health version mismatch")
        if regime_health.model_version != self.regime.model_version:
            raise ValueError("regime model health version mismatch")
        if logic_health.model_version != self.decision.logic_version:
            raise ValueError("logic model health version mismatch")
        if not validation_at_least(
            forecast_health.validation_level,
            self.forecast.validation_level,
        ):
            raise ValueError(
                "forecast model health validation is below the forecast claim"
            )
        if (
            self.forecast.validation_reference is not None
            and forecast_health.validation_reference is not None
            and self.forecast.validation_reference.artifact_sha256
            != forecast_health.validation_reference.artifact_sha256
        ):
            raise ValueError(
                "forecast and model health validation artifacts disagree"
            )

        for item in self.evidence:
            covering_health = [
                health
                for health in self.model_health
                if item.source in health.covered_sources
            ]
            if len(covering_health) != 1:
                raise ValueError(
                    "every evidence source requires exactly one model "
                    "health record"
                )
            health = covering_health[0]
            if health.model_version != item.model_version:
                raise ValueError("evidence model health version mismatch")
            if not validation_at_least(
                health.validation_level, item.validation_level
            ):
                raise ValueError(
                    "evidence model health validation is below the evidence claim"
                )
            if (
                item.validation_reference is not None
                and health.validation_reference is not None
                and item.validation_reference.artifact_sha256
                != health.validation_reference.artifact_sha256
            ):
                raise ValueError(
                    "evidence and model health validation artifacts disagree"
                )

        directional = self.decision.action in {
            DecisionAction.LONG_CANDIDATE,
            DecisionAction.SHORT_CANDIDATE,
        }
        if directional:
            desired_direction = (
                "long"
                if self.decision.action == DecisionAction.LONG_CANDIDATE
                else "short"
            )
            if not any(
                item.permitted_use == EvidenceUse.DECISION_GATE
                and item.direction == desired_direction
                for item in self.evidence
            ):
                raise ValueError(
                    "directional trace requires aligned OOS decision-gate evidence"
                )
            probabilities = self.forecast.probabilities
            if (
                desired_direction == "long"
                and probabilities.long
                <= max(probabilities.short, probabilities.neutral)
            ) or (
                desired_direction == "short"
                and probabilities.short
                <= max(probabilities.long, probabilities.neutral)
            ):
                raise ValueError(
                    "directional trace must match a uniquely dominant forecast"
                )
            if not validation_at_least(
                self.forecast.validation_level,
                ValidationLevel.PREDICTIVE_OOS,
            ):
                raise ValueError(
                    "directional trace requires an OOS-validated forecast"
                )
            if (
                self.forecast.calibration_status
                != CalibrationStatus.CALIBRATED
            ):
                raise ValueError(
                    "directional trace requires a calibrated forecast"
                )
            if any(
                health.status != ModelHealthStatus.HEALTHY
                for health in self.model_health
            ):
                raise ValueError(
                    "directional trace requires healthy model records"
                )

        instrument_id = self.forecast.instrument_id
        horizon = self.forecast.horizon
        linked_instruments = [
            *(item.instrument_id for item in self.evidence),
            self.regime.instrument_id,
            self.decision.instrument_id,
        ]
        linked_horizons = [
            *(item.horizon for item in self.evidence),
            self.regime.horizon,
            self.decision.horizon,
        ]
        if any(item != instrument_id for item in linked_instruments):
            raise ValueError("shadow trace instrument mismatch")
        if any(item != horizon for item in linked_horizons):
            raise ValueError("shadow trace horizon mismatch")

        if not (
            self.forecast.as_of
            == self.regime.as_of
            == self.decision.as_of
        ):
            raise ValueError("shadow trace as_of timestamps must match")
        if any(
            item.data_cutoff > self.forecast.data_cutoff
            for item in self.evidence
        ):
            raise ValueError(
                "forecast data cutoff cannot precede evidence data"
            )
        regime_evidence_ids = set(self.regime.evidence_ids)
        if any(
            item.data_cutoff > self.regime.data_cutoff
            for item in self.evidence
            if item.evidence_id in regime_evidence_ids
        ):
            raise ValueError(
                "regime data cutoff cannot precede linked evidence data"
            )
        decision_inputs = [
            *(item.data_cutoff for item in self.evidence),
            self.forecast.data_cutoff,
            self.regime.data_cutoff,
            *(item.data_cutoff for item in self.model_health),
        ]
        if any(
            data_cutoff > self.decision.data_cutoff
            for data_cutoff in decision_inputs
        ):
            raise ValueError(
                "decision data cutoff cannot precede a linked input"
            )
        if any(
            item.observed_at > self.forecast.as_of
            or item.generated_at > self.forecast.generated_at
            for item in self.evidence
        ):
            raise ValueError("forecast cannot reference future evidence")
        if any(
            item.generated_at > self.regime.generated_at
            for item in self.evidence
            if item.evidence_id in self.regime.evidence_ids
        ):
            raise ValueError("regime cannot reference future evidence")
        if (
            self.forecast.generated_at > self.decision.generated_at
            or self.regime.generated_at > self.decision.generated_at
            or any(
                item.evaluated_at > self.decision.generated_at
                for item in self.model_health
            )
        ):
            raise ValueError(
                "decision cannot reference future forecast, regime, or health"
            )

        generated_times = [
            *(item.generated_at for item in self.evidence),
            self.forecast.generated_at,
            self.regime.generated_at,
            *(item.evaluated_at for item in self.model_health),
            self.decision.generated_at,
        ]
        if self.created_at < max(generated_times):
            raise ValueError("trace cannot precede its component records")
        return self

    def replay_sha256(self) -> str:
        payload = self.model_dump(mode="json")
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()
