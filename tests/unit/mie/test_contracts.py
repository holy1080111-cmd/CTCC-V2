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
NOW = datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(seconds=1)
HORIZON = ForecastHorizon(label="15m", seconds=900)
SHA = "a" * 64


def validation_reference(
    *,
    source: str = "mie.dynamics.test",
    model_version: str = "1.0.0",
) -> ValidationReference:
    return ValidationReference(
        artifact_id="validation-2026-08-12",
        source=source,
        model_version=model_version,
        attested_level=ValidationLevel.PREDICTIVE_OOS,
        dataset_id="dataset-frozen-001",
        sample_size=500,
        reviewer_id="quant-review",
        issued_at=NOW,
        artifact_sha256="b" * 64,
        metrics=(
            ValidationMetric(name="brier", value=D("0.19")),
            ValidationMetric(
                name="delta_log_loss",
                value=D("-0.02"),
            ),
        ),
    )


def evidence(
    *,
    validation_level: ValidationLevel = ValidationLevel.CAUSAL,
    permitted_use: EvidenceUse = EvidenceUse.RISK_DOWNGRADE_ONLY,
    reference: ValidationReference | None = None,
) -> Evidence:
    return Evidence(
        source="mie.dynamics.test",
        instrument_id="BTC-USDT-SWAP",
        horizon=HORIZON,
        observed_at=NOW,
        data_cutoff=CUTOFF,
        generated_at=NOW,
        direction=EvidenceDirection.LONG,
        strength=D("0.6"),
        reliability=D("0.7"),
        uncertainty=D("0.3"),
        data_quality=D("0.9"),
        validation_level=validation_level,
        permitted_use=permitted_use,
        validation_sample_size=(reference.sample_size if reference else 0),
        validation_reference=reference,
        dependency_group="price.path.shared",
        feature_version="1.0.0",
        model_version="1.0.0",
        provenance_sha256=SHA,
    )


def test_horizon_label_and_seconds_must_agree() -> None:
    with pytest.raises(ValidationError):
        ForecastHorizon(label="15m", seconds=3600)


def test_evidence_rejects_future_data_and_non_utc_timestamps() -> None:
    payload = evidence().model_dump()
    payload["data_cutoff"] = NOW + timedelta(seconds=1)
    with pytest.raises(ValidationError):
        Evidence.model_validate(payload)

    payload = evidence().model_dump()
    payload["observed_at"] = datetime(2026, 8, 12, 4, 0)
    with pytest.raises(ValidationError):
        Evidence.model_validate(payload)


def test_causal_evidence_cannot_become_decision_gate() -> None:
    with pytest.raises(ValidationError, match="exceeds"):
        evidence(permitted_use=EvidenceUse.DECISION_GATE)


def test_predictive_evidence_requires_external_validation_artifact() -> None:
    with pytest.raises(ValidationError, match="external artifact"):
        evidence(
            validation_level=ValidationLevel.PREDICTIVE_OOS,
            permitted_use=EvidenceUse.DECISION_GATE,
        )

    item = evidence(
        validation_level=ValidationLevel.PREDICTIVE_OOS,
        permitted_use=EvidenceUse.DECISION_GATE,
        reference=validation_reference(),
    )
    assert item.execution_authority is False

    mismatched_sample = item.model_dump()
    mismatched_sample["validation_sample_size"] = 499
    with pytest.raises(ValidationError, match="sample size must match"):
        Evidence.model_validate(mismatched_sample)


def test_auxiliary_evidence_is_limited_to_shadow_or_tie_break() -> None:
    item = evidence(
        validation_level=ValidationLevel.AUXILIARY,
        permitted_use=EvidenceUse.AUXILIARY_TIE_BREAK,
    )
    assert item.permitted_use == EvidenceUse.AUXILIARY_TIE_BREAK

    with pytest.raises(ValidationError):
        evidence(
            validation_level=ValidationLevel.AUXILIARY,
            permitted_use=EvidenceUse.RISK_DOWNGRADE_ONLY,
        )


def test_calibrated_probability_requires_prequential_artifact() -> None:
    payload = evidence().model_dump()
    payload["calibrated_probability"] = D("0.7")
    with pytest.raises(ValidationError, match="prequential"):
        Evidence.model_validate(payload)


def test_probability_vector_must_sum_to_one() -> None:
    ProbabilityVector(long=D("0.4"), short=D("0.3"), neutral=D("0.3"))
    with pytest.raises(ValidationError, match="sum to one"):
        ProbabilityVector(long=D("0.5"), short=D("0.4"), neutral=D("0.2"))


def test_regime_dominant_label_must_match_probability() -> None:
    item = evidence()
    probabilities = RegimeProbabilityVector(
        bull_trend=D("0.1"),
        bear_trend=D("0.1"),
        range=D("0.6"),
        high_volatility=D("0.1"),
        transition=D("0.1"),
    )
    with pytest.raises(ValidationError, match="maximum probability"):
        RegimeSnapshot(
            instrument_id=item.instrument_id,
            horizon=HORIZON,
            as_of=NOW,
            data_cutoff=CUTOFF,
            generated_at=NOW,
            probabilities=probabilities,
            dominant_regime=MarketRegime.BULL_TREND,
            evidence_ids=(item.evidence_id,),
            model_id="mie.regime.test",
            model_version="1.0.0",
            provenance_sha256=SHA,
        )


def test_regime_requires_a_unique_dominant_probability() -> None:
    item = evidence()
    with pytest.raises(ValidationError, match="must be unique"):
        RegimeSnapshot(
            instrument_id=item.instrument_id,
            horizon=HORIZON,
            as_of=NOW,
            data_cutoff=CUTOFF,
            generated_at=NOW,
            probabilities=RegimeProbabilityVector(
                bull_trend=D("0.4"),
                bear_trend=D("0.4"),
                range=D("0.1"),
                high_volatility=D("0.05"),
                transition=D("0.05"),
            ),
            dominant_regime=MarketRegime.BULL_TREND,
            evidence_ids=(item.evidence_id,),
            model_id="mie.regime.test",
            model_version="1.0.0",
            provenance_sha256=SHA,
        )


def test_healthy_model_requires_fresh_leakage_safe_state() -> None:
    with pytest.raises(ValidationError, match="healthy model"):
        ModelHealth(
            model_id="mie.dynamics.test",
            model_version="1.0.0",
            covered_sources=("mie.dynamics.test",),
            evaluated_at=NOW,
            data_cutoff=CUTOFF,
            status=ModelHealthStatus.HEALTHY,
            data_fresh=False,
            leakage_check_passed=True,
            calibration_status=CalibrationStatus.UNCALIBRATED,
            validation_level=ValidationLevel.CAUSAL,
        )


def test_calibrated_model_health_requires_validation_artifact() -> None:
    with pytest.raises(ValidationError, match="validation evidence"):
        ModelHealth(
            model_id="mie.probability.test",
            model_version="1.0.0",
            covered_sources=("mie.probability.test",),
            evaluated_at=NOW,
            data_cutoff=CUTOFF,
            status=ModelHealthStatus.HEALTHY,
            data_fresh=True,
            leakage_check_passed=True,
            calibration_status=CalibrationStatus.CALIBRATED,
            validation_level=ValidationLevel.PREQUENTIAL,
        )


def test_degraded_calibration_cannot_be_reported_as_healthy() -> None:
    with pytest.raises(ValidationError, match="healthy model"):
        ModelHealth(
            model_id="mie.probability.test",
            model_version="1.0.0",
            covered_sources=("mie.probability.test",),
            evaluated_at=NOW,
            data_cutoff=CUTOFF,
            status=ModelHealthStatus.HEALTHY,
            data_fresh=True,
            leakage_check_passed=True,
            calibration_status=CalibrationStatus.DEGRADED,
            validation_level=ValidationLevel.PREQUENTIAL,
            failure_codes=(),
        )


def test_model_health_and_decision_codes_cannot_be_blank() -> None:
    with pytest.raises(ValidationError, match="blank"):
        ModelHealth(
            model_id="mie.logic.test",
            model_version="1.0.0",
            covered_sources=(" ",),
            evaluated_at=NOW,
            data_cutoff=CUTOFF,
            status=ModelHealthStatus.UNKNOWN,
            data_fresh=False,
            leakage_check_passed=False,
            calibration_status=CalibrationStatus.UNCALIBRATED,
            validation_level=ValidationLevel.COMPUTATIONAL,
        )

    item = evidence()
    with pytest.raises(ValidationError, match="cannot be blank"):
        DecisionCandidate(
            instrument_id=item.instrument_id,
            horizon=HORIZON,
            as_of=NOW,
            data_cutoff=CUTOFF,
            generated_at=NOW,
            action=DecisionAction.NO_TRADE,
            net_expected_value=D("0"),
            checks=DecisionChecks(
                probability_ok=False,
                ev_net_positive=False,
                risk_ok=False,
                uncertainty_ok=False,
                regime_compatible=False,
                data_fresh=False,
                model_health_ok=False,
            ),
            forecast_id=item.evidence_id,
            regime_snapshot_id=item.evidence_id,
            evidence_ids=(item.evidence_id,),
            model_health_ids=(item.evidence_id,),
            reason_codes=(" ",),
            logic_id="mie.logic.test",
            logic_version="1.0.0",
            provenance_sha256=SHA,
        )


def test_directional_decision_requires_every_gate_and_positive_ev() -> None:
    item = evidence()
    checks = DecisionChecks(
        probability_ok=False,
        ev_net_positive=True,
        risk_ok=True,
        uncertainty_ok=True,
        regime_compatible=True,
        data_fresh=True,
        model_health_ok=True,
    )
    with pytest.raises(ValidationError, match="every logic gate"):
        DecisionCandidate(
            instrument_id=item.instrument_id,
            horizon=HORIZON,
            as_of=NOW,
            data_cutoff=CUTOFF,
            generated_at=NOW,
            action=DecisionAction.LONG_CANDIDATE,
            net_expected_value=D("0.001"),
            checks=checks,
            forecast_id=item.evidence_id,
            regime_snapshot_id=item.evidence_id,
            evidence_ids=(item.evidence_id,),
            model_health_ids=(item.evidence_id,),
            logic_id="mie.logic.test",
            logic_version="1.0.0",
            provenance_sha256=SHA,
        )


def test_ev_check_cannot_disagree_with_numeric_net_ev() -> None:
    item = evidence()
    checks = DecisionChecks(
        probability_ok=False,
        ev_net_positive=True,
        risk_ok=False,
        uncertainty_ok=False,
        regime_compatible=False,
        data_fresh=True,
        model_health_ok=False,
    )
    with pytest.raises(ValidationError, match="must match"):
        DecisionCandidate(
            instrument_id=item.instrument_id,
            horizon=HORIZON,
            as_of=NOW,
            data_cutoff=CUTOFF,
            generated_at=NOW,
            action=DecisionAction.NO_TRADE,
            net_expected_value=D("-0.001"),
            checks=checks,
            forecast_id=item.evidence_id,
            regime_snapshot_id=item.evidence_id,
            evidence_ids=(item.evidence_id,),
            model_health_ids=(item.evidence_id,),
            reason_codes=("negative_ev",),
            logic_id="mie.logic.test",
            logic_version="1.0.0",
            provenance_sha256=SHA,
        )


def test_validation_artifact_requires_reviewer_and_immutable_metrics() -> None:
    reference = validation_reference()
    with pytest.raises(ValidationError):
        ValidationReference.model_validate(
            {
                **reference.model_dump(),
                "reviewer_id": "",
            }
        )
    with pytest.raises(ValidationError, match="unique"):
        ValidationReference.model_validate(
            {
                **reference.model_dump(),
                "metrics": (
                    ValidationMetric(name="brier", value=D("0.19")),
                    ValidationMetric(name="brier", value=D("0.20")),
                ),
            }
        )


def test_validation_artifact_cannot_support_a_higher_claim() -> None:
    weak_reference = validation_reference().model_copy(
        update={"attested_level": ValidationLevel.PREQUENTIAL}
    )
    with pytest.raises(ValidationError, match="below the evidence claim"):
        evidence(
            validation_level=ValidationLevel.PREDICTIVE_OOS,
            permitted_use=EvidenceUse.DECISION_GATE,
            reference=weak_reference,
        )

    predictive_reference = validation_reference(
        source="mie.probability.test"
    )
    with pytest.raises(ValidationError, match="below the model health claim"):
        ModelHealth(
            model_id="mie.probability.test",
            model_version="1.0.0",
            covered_sources=("mie.probability.test",),
            evaluated_at=NOW,
            data_cutoff=CUTOFF,
            status=ModelHealthStatus.HEALTHY,
            data_fresh=True,
            leakage_check_passed=True,
            calibration_status=CalibrationStatus.UNCALIBRATED,
            validation_level=ValidationLevel.DEMO_EXECUTION,
            last_oos_validation_at=NOW,
            validation_reference=predictive_reference,
        )


def test_contracts_forbid_execution_authority_and_unknown_fields() -> None:
    payload = evidence().model_dump()
    payload["execution_authority"] = True
    with pytest.raises(ValidationError):
        Evidence.model_validate(payload)

    payload = evidence().model_dump()
    payload["order_size"] = "1"
    with pytest.raises(ValidationError):
        Evidence.model_validate(payload)


def test_uncalibrated_forecast_remains_non_executable() -> None:
    item = evidence()
    forecast = ProbabilityForecast(
        instrument_id=item.instrument_id,
        horizon=HORIZON,
        as_of=NOW,
        data_cutoff=CUTOFF,
        generated_at=NOW,
        probabilities=ProbabilityVector(
            long=D("0.4"),
            short=D("0.2"),
            neutral=D("0.4"),
        ),
        uncertainty=D("0.7"),
        evidence_ids=(item.evidence_id,),
        model_id="mie.probability.test",
        model_version="1.0.0",
        provenance_sha256=SHA,
        validation_level=ValidationLevel.CAUSAL,
        calibration_status=CalibrationStatus.UNCALIBRATED,
    )
    assert forecast.execution_authority is False


def test_predictive_forecast_requires_external_validation_artifact() -> None:
    item = evidence()
    payload = {
        "instrument_id": item.instrument_id,
        "horizon": HORIZON,
        "as_of": NOW,
        "data_cutoff": CUTOFF,
        "generated_at": NOW,
        "probabilities": ProbabilityVector(
            long=D("0.5"),
            short=D("0.2"),
            neutral=D("0.3"),
        ),
        "uncertainty": D("0.4"),
        "evidence_ids": (item.evidence_id,),
        "model_id": "mie.probability.test",
        "model_version": "1.0.0",
        "provenance_sha256": SHA,
        "validation_level": ValidationLevel.PREDICTIVE_OOS,
        "calibration_status": CalibrationStatus.UNCALIBRATED,
    }
    with pytest.raises(ValidationError, match="external validation artifact"):
        ProbabilityForecast(**payload)

    reference = validation_reference(source="mie.probability.test")
    forecast = ProbabilityForecast(
        **payload,
        validation_reference=reference,
    )
    assert forecast.validation_reference == reference

    weak_reference = reference.model_copy(
        update={"attested_level": ValidationLevel.PREQUENTIAL}
    )
    with pytest.raises(ValidationError, match="below the forecast claim"):
        ProbabilityForecast(
            **payload,
            validation_reference=weak_reference,
        )
