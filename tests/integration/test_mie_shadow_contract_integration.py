from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.analysis import (
    MathematicalCoreComponent,
    MathematicalCoreSnapshot,
)
from app.mie import (
    CalibrationStatus,
    DecisionAction,
    DecisionCandidate,
    DecisionChecks,
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
    adapt_legacy_mathematical_core,
)

D = Decimal


@pytest.mark.integration
def test_legacy_mathematics_builds_replayable_no_trade_shadow_chain() -> None:
    as_of = datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)
    cutoff = as_of - timedelta(seconds=1)
    horizon = ForecastHorizon(label="15m", seconds=900)
    provenance = "f" * 64
    legacy = MathematicalCoreSnapshot(
        status="long",
        directional_score=D("0.45"),
        confidence=D("0.40"),
        coverage=D("0.70"),
        consensus=D("0.75"),
        instability=D("0.10"),
        auxiliary_directional_score=D("0.15"),
        auxiliary_confidence=D("0.20"),
        components=[
            MathematicalCoreComponent(
                code="derivative",
                signal=D("0.60"),
                reliability=D("0.70"),
                validation_level="analytical",
                detail="derivative_multi_timeframe_causal_aggregate",
            ),
            MathematicalCoreComponent(
                code="state",
                signal=D("0.40"),
                reliability=D("0.65"),
                validation_level="analytical",
                detail="state_multi_timeframe_causal_aggregate",
            ),
            MathematicalCoreComponent(
                code="structure",
                signal=D("0.20"),
                reliability=D("0.40"),
                validation_level="auxiliary",
                detail="structure_multi_timeframe_causal_aggregate",
            ),
        ],
    )
    evidence = adapt_legacy_mathematical_core(
        legacy,
        instrument_id="BTC-USDT-SWAP",
        horizon=horizon,
        observed_at=as_of,
        data_cutoff=cutoff,
        provenance_sha256=provenance,
    )

    forecast = ProbabilityForecast(
        instrument_id="BTC-USDT-SWAP",
        horizon=horizon,
        as_of=as_of,
        data_cutoff=cutoff,
        generated_at=as_of,
        probabilities=ProbabilityVector(
            long=D("0.34"),
            short=D("0.33"),
            neutral=D("0.33"),
        ),
        uncertainty=D("0.90"),
        evidence_ids=tuple(item.evidence_id for item in evidence),
        model_id="mie.probability.shadow_baseline",
        model_version="0.1.0",
        provenance_sha256=provenance,
        validation_level=ValidationLevel.COMPUTATIONAL,
        calibration_status=CalibrationStatus.UNCALIBRATED,
    )
    regime = RegimeSnapshot(
        instrument_id="BTC-USDT-SWAP",
        horizon=horizon,
        as_of=as_of,
        data_cutoff=cutoff,
        generated_at=as_of,
        probabilities=RegimeProbabilityVector(
            bull_trend=D("0.25"),
            bear_trend=D("0.15"),
            range=D("0.30"),
            high_volatility=D("0.10"),
            transition=D("0.20"),
        ),
        dominant_regime=MarketRegime.RANGE,
        evidence_ids=tuple(item.evidence_id for item in evidence),
        model_id="mie.regime.shadow_baseline",
        model_version="0.1.0",
        provenance_sha256=provenance,
    )
    evidence_health = ModelHealth(
        model_id="legacy.mathematical_core",
        model_version="v1.6.8",
        covered_sources=tuple(item.source for item in evidence),
        evaluated_at=as_of,
        data_cutoff=cutoff,
        status=ModelHealthStatus.HEALTHY,
        data_fresh=True,
        leakage_check_passed=True,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        validation_level=ValidationLevel.CAUSAL,
    )
    forecast_health = ModelHealth(
        model_id=forecast.model_id,
        model_version=forecast.model_version,
        covered_sources=(forecast.model_id,),
        evaluated_at=as_of,
        data_cutoff=cutoff,
        status=ModelHealthStatus.HEALTHY,
        data_fresh=True,
        leakage_check_passed=True,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        validation_level=ValidationLevel.COMPUTATIONAL,
    )
    regime_health = ModelHealth(
        model_id=regime.model_id,
        model_version=regime.model_version,
        covered_sources=(regime.model_id,),
        evaluated_at=as_of,
        data_cutoff=cutoff,
        status=ModelHealthStatus.HEALTHY,
        data_fresh=True,
        leakage_check_passed=True,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        validation_level=ValidationLevel.CAUSAL,
    )
    logic_health = ModelHealth(
        model_id="mie.logic.shadow_only",
        model_version="0.1.0",
        covered_sources=("mie.logic.shadow_only",),
        evaluated_at=as_of,
        data_cutoff=cutoff,
        status=ModelHealthStatus.HEALTHY,
        data_fresh=True,
        leakage_check_passed=True,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        validation_level=ValidationLevel.COMPUTATIONAL,
    )
    health = (
        evidence_health,
        forecast_health,
        regime_health,
        logic_health,
    )
    decision = DecisionCandidate(
        instrument_id="BTC-USDT-SWAP",
        horizon=horizon,
        as_of=as_of,
        data_cutoff=cutoff,
        generated_at=as_of,
        action=DecisionAction.NO_TRADE,
        net_expected_value=D("0"),
        checks=DecisionChecks(
            probability_ok=False,
            ev_net_positive=False,
            risk_ok=True,
            uncertainty_ok=False,
            regime_compatible=True,
            data_fresh=True,
            model_health_ok=True,
        ),
        forecast_id=forecast.forecast_id,
        regime_snapshot_id=regime.regime_snapshot_id,
        evidence_ids=tuple(item.evidence_id for item in evidence),
        model_health_ids=tuple(item.health_id for item in health),
        reason_codes=(
            "no_predictive_oos_evidence",
            "probability_uncalibrated",
            "uncertainty_above_limit",
        ),
        logic_id=logic_health.model_id,
        logic_version=logic_health.model_version,
        provenance_sha256=provenance,
    )
    trace = MieShadowTrace(
        feature_snapshot_id="legacy-analysis-snapshot-001",
        evidence=evidence,
        forecast=forecast,
        regime=regime,
        model_health=health,
        decision=decision,
        created_at=as_of,
    )

    replayed = MieShadowTrace.model_validate_json(trace.model_dump_json())
    assert replayed == trace
    assert replayed.replay_sha256() == trace.replay_sha256()
    assert replayed.decision.action == DecisionAction.NO_TRADE
    assert replayed.execution_authority is False
    assert all(item.execution_authority is False for item in replayed.evidence)
