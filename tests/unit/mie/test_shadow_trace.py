from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.mie.contracts import (
    CalibrationStatus,
    DecisionAction,
    DecisionCandidate,
    DecisionChecks,
    Evidence,
    EvidenceDirection,
    EvidenceUse,
    ForecastHorizon,
    MarketRegime,
    MieShadowTrace,
    ModelHealth,
    ModelHealthStatus,
    ProbabilityForecast,
    ProbabilityVector,
    RegimeProbabilityVector,
    RegimeSnapshot,
    ValidationLevel,
    ValidationMetric,
    ValidationReference,
)

D = Decimal
AS_OF = datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)
CUTOFF = AS_OF - timedelta(seconds=1)
HORIZON = ForecastHorizon(label="15m", seconds=900)
SHA = "d" * 64


def reference(
    source: str = "mie.dynamics.validated",
) -> ValidationReference:
    return ValidationReference(
        artifact_id="walk-forward-001",
        source=source,
        model_version="1.0.0",
        attested_level=ValidationLevel.PREDICTIVE_OOS,
        dataset_id="btc-swap-2024-2026",
        sample_size=1000,
        reviewer_id="quant-review",
        issued_at=AS_OF,
        artifact_sha256="e" * 64,
        metrics=(
            ValidationMetric(name="brier", value=D("0.18")),
        ),
    )


def complete_shadow_trace(
    *,
    instrument_id: str = "BTC-USDT-SWAP",
) -> MieShadowTrace:
    validation = reference()
    forecast_validation = reference("mie.probability.validated")
    evidence = Evidence(
        source="mie.dynamics.validated",
        instrument_id=instrument_id,
        horizon=HORIZON,
        observed_at=AS_OF,
        data_cutoff=CUTOFF,
        generated_at=AS_OF,
        direction=EvidenceDirection.LONG,
        strength=D("0.7"),
        reliability=D("0.8"),
        uncertainty=D("0.2"),
        data_quality=D("0.95"),
        calibrated_probability=D("0.64"),
        validation_level=ValidationLevel.PREDICTIVE_OOS,
        permitted_use=EvidenceUse.DECISION_GATE,
        validation_sample_size=1000,
        validation_metric=D("0.18"),
        validation_reference=validation,
        dependency_group="price.path.shared",
        feature_version="1.0.0",
        model_version="1.0.0",
        provenance_sha256=SHA,
    )
    forecast = ProbabilityForecast(
        instrument_id=instrument_id,
        horizon=HORIZON,
        as_of=AS_OF,
        data_cutoff=CUTOFF,
        generated_at=AS_OF,
        probabilities=ProbabilityVector(
            long=D("0.64"),
            short=D("0.16"),
            neutral=D("0.20"),
        ),
        uncertainty=D("0.2"),
        evidence_ids=(evidence.evidence_id,),
        model_id="mie.probability.validated",
        model_version="1.0.0",
        provenance_sha256=SHA,
        validation_level=ValidationLevel.PREDICTIVE_OOS,
        calibration_status=CalibrationStatus.CALIBRATED,
        validation_reference=forecast_validation,
        calibration_reference=forecast_validation,
    )
    regime = RegimeSnapshot(
        instrument_id=instrument_id,
        horizon=HORIZON,
        as_of=AS_OF,
        data_cutoff=CUTOFF,
        generated_at=AS_OF,
        probabilities=RegimeProbabilityVector(
            bull_trend=D("0.5"),
            bear_trend=D("0.1"),
            range=D("0.2"),
            high_volatility=D("0.1"),
            transition=D("0.1"),
        ),
        dominant_regime=MarketRegime.BULL_TREND,
        evidence_ids=(evidence.evidence_id,),
        model_id="mie.regime.validated",
        model_version="1.0.0",
        provenance_sha256=SHA,
    )
    evidence_health = ModelHealth(
        model_id="mie.dynamics.validated",
        model_version="1.0.0",
        covered_sources=(evidence.source,),
        evaluated_at=AS_OF,
        data_cutoff=CUTOFF,
        status=ModelHealthStatus.HEALTHY,
        data_fresh=True,
        leakage_check_passed=True,
        calibration_status=CalibrationStatus.CALIBRATED,
        validation_level=ValidationLevel.PREDICTIVE_OOS,
        last_oos_validation_at=AS_OF,
        validation_reference=validation,
    )
    forecast_health = ModelHealth(
        model_id=forecast.model_id,
        model_version=forecast.model_version,
        covered_sources=(forecast.model_id,),
        evaluated_at=AS_OF,
        data_cutoff=CUTOFF,
        status=ModelHealthStatus.HEALTHY,
        data_fresh=True,
        leakage_check_passed=True,
        calibration_status=CalibrationStatus.CALIBRATED,
        validation_level=ValidationLevel.PREDICTIVE_OOS,
        last_oos_validation_at=AS_OF,
        validation_reference=forecast_validation,
    )
    regime_health = ModelHealth(
        model_id=regime.model_id,
        model_version=regime.model_version,
        covered_sources=(regime.model_id,),
        evaluated_at=AS_OF,
        data_cutoff=CUTOFF,
        status=ModelHealthStatus.HEALTHY,
        data_fresh=True,
        leakage_check_passed=True,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        validation_level=ValidationLevel.CAUSAL,
    )
    logic_health = ModelHealth(
        model_id="mie.logic.validated",
        model_version="1.0.0",
        covered_sources=("mie.logic.validated",),
        evaluated_at=AS_OF,
        data_cutoff=CUTOFF,
        status=ModelHealthStatus.HEALTHY,
        data_fresh=True,
        leakage_check_passed=True,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        validation_level=ValidationLevel.COMPUTATIONAL,
    )
    health_records = (
        evidence_health,
        forecast_health,
        regime_health,
        logic_health,
    )
    checks = DecisionChecks(
        probability_ok=True,
        ev_net_positive=True,
        risk_ok=True,
        uncertainty_ok=True,
        regime_compatible=True,
        data_fresh=True,
        model_health_ok=True,
    )
    decision = DecisionCandidate(
        instrument_id=instrument_id,
        horizon=HORIZON,
        as_of=AS_OF,
        data_cutoff=CUTOFF,
        generated_at=AS_OF,
        action=DecisionAction.LONG_CANDIDATE,
        net_expected_value=D("0.0012"),
        checks=checks,
        forecast_id=forecast.forecast_id,
        regime_snapshot_id=regime.regime_snapshot_id,
        evidence_ids=(evidence.evidence_id,),
        model_health_ids=tuple(item.health_id for item in health_records),
        logic_id="mie.logic.validated",
        logic_version="1.0.0",
        provenance_sha256=SHA,
    )
    return MieShadowTrace(
        feature_snapshot_id="features-001",
        evidence=(evidence,),
        forecast=forecast,
        regime=regime,
        model_health=health_records,
        decision=decision,
        created_at=AS_OF,
    )


def test_complete_shadow_trace_round_trips_and_hashes_deterministically() -> None:
    trace = complete_shadow_trace()
    restored = MieShadowTrace.model_validate_json(trace.model_dump_json())

    assert restored == trace
    assert restored.replay_sha256() == trace.replay_sha256()
    assert trace.authority == "shadow_only"
    assert trace.execution_authority is False
    assert trace.decision.execution_authority is False


def test_trace_rejects_cross_instrument_linkage() -> None:
    trace = complete_shadow_trace()
    mismatched = trace.regime.model_copy(
        update={"instrument_id": "ETH-USDT-SWAP"}
    )
    with pytest.raises(ValidationError, match="instrument mismatch"):
        MieShadowTrace.model_validate(
            {
                **trace.model_dump(),
                "regime": mismatched,
            }
        )


def test_trace_rejects_missing_evidence_link() -> None:
    trace = complete_shadow_trace()
    forecast = trace.forecast.model_copy(update={"evidence_ids": ()})
    with pytest.raises(ValidationError):
        MieShadowTrace.model_validate(
            {
                **trace.model_dump(),
                "forecast": forecast,
            }
        )


def test_trace_revalidates_nested_execution_authority() -> None:
    trace = complete_shadow_trace()
    unsafe_decision = trace.decision.model_copy(
        update={"execution_authority": True}
    )
    with pytest.raises(ValidationError, match="Input should be False"):
        MieShadowTrace(
            feature_snapshot_id=trace.feature_snapshot_id,
            evidence=trace.evidence,
            forecast=trace.forecast,
            regime=trace.regime,
            model_health=trace.model_health,
            decision=unsafe_decision,
            created_at=trace.created_at,
        )


def test_trace_revalidates_nested_probability_invariants() -> None:
    trace = complete_shadow_trace()
    invalid_probabilities = trace.forecast.probabilities.model_copy(
        update={"long": D("0.90")}
    )
    invalid_forecast = trace.forecast.model_copy(
        update={"probabilities": invalid_probabilities}
    )
    with pytest.raises(ValidationError, match="sum to one"):
        MieShadowTrace(
            feature_snapshot_id=trace.feature_snapshot_id,
            evidence=trace.evidence,
            forecast=invalid_forecast,
            regime=trace.regime,
            model_health=trace.model_health,
            decision=trace.decision,
            created_at=trace.created_at,
        )


def test_directional_trace_requires_oos_decision_gate_evidence() -> None:
    trace = complete_shadow_trace()
    weak_evidence = trace.evidence[0].model_copy(
        update={
            "validation_level": ValidationLevel.CAUSAL,
            "permitted_use": EvidenceUse.RISK_DOWNGRADE_ONLY,
            "calibrated_probability": None,
            "validation_reference": None,
        }
    )
    weak_forecast = trace.forecast.model_copy(
        update={
            "validation_level": ValidationLevel.CAUSAL,
            "calibration_status": CalibrationStatus.UNCALIBRATED,
            "calibration_reference": None,
            "evidence_ids": (weak_evidence.evidence_id,),
        }
    )
    with pytest.raises(ValidationError, match="decision-gate"):
        MieShadowTrace(
            feature_snapshot_id=trace.feature_snapshot_id,
            evidence=(weak_evidence,),
            forecast=weak_forecast,
            regime=trace.regime,
            model_health=trace.model_health,
            decision=trace.decision,
            created_at=trace.created_at,
        )


def test_directional_trace_must_match_dominant_probability() -> None:
    trace = complete_shadow_trace()
    opposed_forecast = trace.forecast.model_copy(
        update={
            "probabilities": ProbabilityVector(
                long=D("0.20"),
                short=D("0.65"),
                neutral=D("0.15"),
            )
        }
    )
    decision = trace.decision.model_copy(
        update={"forecast_id": opposed_forecast.forecast_id}
    )
    with pytest.raises(ValidationError, match="dominant forecast"):
        MieShadowTrace(
            feature_snapshot_id=trace.feature_snapshot_id,
            evidence=trace.evidence,
            forecast=opposed_forecast,
            regime=trace.regime,
            model_health=trace.model_health,
            decision=decision,
            created_at=trace.created_at,
        )


def test_forecast_cannot_reference_evidence_generated_later() -> None:
    trace = complete_shadow_trace()
    future_evidence = trace.evidence[0].model_copy(
        update={"generated_at": AS_OF + timedelta(seconds=1)}
    )
    with pytest.raises(ValidationError, match="future evidence"):
        MieShadowTrace(
            feature_snapshot_id=trace.feature_snapshot_id,
            evidence=(future_evidence,),
            forecast=trace.forecast,
            regime=trace.regime,
            model_health=trace.model_health,
            decision=trace.decision,
            created_at=AS_OF + timedelta(seconds=1),
        )


def test_forecast_regime_and_logic_require_distinct_health_records() -> None:
    trace = complete_shadow_trace()
    reduced_health = tuple(
        item
        for item in trace.model_health
        if item.model_id != trace.regime.model_id
    )
    decision = trace.decision.model_copy(
        update={
            "model_health_ids": tuple(
                item.health_id for item in reduced_health
            )
        }
    )
    with pytest.raises(ValidationError, match="their own"):
        MieShadowTrace(
            feature_snapshot_id=trace.feature_snapshot_id,
            evidence=trace.evidence,
            forecast=trace.forecast,
            regime=trace.regime,
            model_health=reduced_health,
            decision=decision,
            created_at=trace.created_at,
        )


def test_trace_rejects_cutoff_before_linked_evidence() -> None:
    trace = complete_shadow_trace()
    later_evidence = trace.evidence[0].model_copy(
        update={"data_cutoff": CUTOFF + timedelta(milliseconds=500)}
    )
    with pytest.raises(ValidationError, match="forecast data cutoff"):
        MieShadowTrace(
            feature_snapshot_id=trace.feature_snapshot_id,
            evidence=(later_evidence,),
            forecast=trace.forecast,
            regime=trace.regime,
            model_health=trace.model_health,
            decision=trace.decision,
            created_at=trace.created_at,
        )


def test_trace_rejects_decision_cutoff_before_any_linked_input() -> None:
    trace = complete_shadow_trace()
    early_decision = trace.decision.model_copy(
        update={"data_cutoff": CUTOFF - timedelta(seconds=1)}
    )
    with pytest.raises(ValidationError, match="decision data cutoff"):
        MieShadowTrace(
            feature_snapshot_id=trace.feature_snapshot_id,
            evidence=trace.evidence,
            forecast=trace.forecast,
            regime=trace.regime,
            model_health=trace.model_health,
            decision=early_decision,
            created_at=trace.created_at,
        )


def test_trace_rejects_health_data_from_after_decision_as_of() -> None:
    trace = complete_shadow_trace()
    future_health = trace.model_health[0].model_copy(
        update={
            "evaluated_at": AS_OF + timedelta(seconds=1),
            "data_cutoff": AS_OF + timedelta(milliseconds=500),
        }
    )
    health = (future_health, *trace.model_health[1:])
    delayed_decision = trace.decision.model_copy(
        update={
            "generated_at": AS_OF + timedelta(seconds=2),
            "model_health_ids": tuple(item.health_id for item in health),
        }
    )
    with pytest.raises(ValidationError, match="decision data cutoff"):
        MieShadowTrace(
            feature_snapshot_id=trace.feature_snapshot_id,
            evidence=trace.evidence,
            forecast=trace.forecast,
            regime=trace.regime,
            model_health=health,
            decision=delayed_decision,
            created_at=AS_OF + timedelta(seconds=2),
        )


def test_trace_rejects_model_health_version_mismatch() -> None:
    trace = complete_shadow_trace()
    forecast_health = trace.model_health[1]
    mismatched_reference = forecast_health.validation_reference.model_copy(
        update={"model_version": "2.0.0"}
    )
    mismatched_health = forecast_health.model_copy(
        update={
            "model_version": "2.0.0",
            "validation_reference": mismatched_reference,
        }
    )
    health = (
        trace.model_health[0],
        mismatched_health,
        *trace.model_health[2:],
    )
    with pytest.raises(ValidationError, match="health version mismatch"):
        MieShadowTrace(
            feature_snapshot_id=trace.feature_snapshot_id,
            evidence=trace.evidence,
            forecast=trace.forecast,
            regime=trace.regime,
            model_health=health,
            decision=trace.decision,
            created_at=trace.created_at,
        )


def test_trace_rejects_health_validation_below_evidence_claim() -> None:
    trace = complete_shadow_trace()
    weak_evidence_health = trace.model_health[0].model_copy(
        update={
            "calibration_status": CalibrationStatus.UNCALIBRATED,
            "validation_level": ValidationLevel.CAUSAL,
            "last_oos_validation_at": None,
            "validation_reference": None,
        }
    )
    health = (weak_evidence_health, *trace.model_health[1:])
    with pytest.raises(ValidationError, match="below the evidence claim"):
        MieShadowTrace(
            feature_snapshot_id=trace.feature_snapshot_id,
            evidence=trace.evidence,
            forecast=trace.forecast,
            regime=trace.regime,
            model_health=health,
            decision=trace.decision,
            created_at=trace.created_at,
        )


def test_trace_rejects_ambiguous_evidence_health_coverage() -> None:
    trace = complete_shadow_trace()
    forecast_health = trace.model_health[1].model_copy(
        update={
            "covered_sources": (
                *trace.model_health[1].covered_sources,
                trace.evidence[0].source,
            )
        }
    )
    health = (
        trace.model_health[0],
        forecast_health,
        *trace.model_health[2:],
    )
    with pytest.raises(ValidationError, match="exactly one"):
        MieShadowTrace(
            feature_snapshot_id=trace.feature_snapshot_id,
            evidence=trace.evidence,
            forecast=trace.forecast,
            regime=trace.regime,
            model_health=health,
            decision=trace.decision,
            created_at=trace.created_at,
        )


def test_trace_requires_distinct_forecast_regime_and_logic_models() -> None:
    trace = complete_shadow_trace()
    colliding_regime = trace.regime.model_copy(
        update={
            "model_id": trace.forecast.model_id,
            "model_version": trace.forecast.model_version,
        }
    )
    with pytest.raises(ValidationError, match="distinct model identities"):
        MieShadowTrace(
            feature_snapshot_id=trace.feature_snapshot_id,
            evidence=trace.evidence,
            forecast=trace.forecast,
            regime=colliding_regime,
            model_health=trace.model_health,
            decision=trace.decision,
            created_at=trace.created_at,
        )
