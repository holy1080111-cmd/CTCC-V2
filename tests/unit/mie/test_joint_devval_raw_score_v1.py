"""Synthetic joint artifacts and exact source-binding checks before scoring."""

from dataclasses import replace

import pytest

from app.domain.source_primitives import decode, sha
from app.mie.validation import (
    prospective_devval_joint_companion_coverage_v1 as coverage,
)
from app.mie.validation import prospective_devval_joint_raw_score_v1 as subject
from app.mie.validation.artifact import _freeze
from app.mie.validation.frozen_legacy_raw_scorer_v1 import (
    FrozenLegacyRawScorerConfigV1,
    FrozenLegacyRawScoreV1,
)
from app.mie.validation.prospective_devval_campaign_batch_v1 import _build_batch
from app.mie.validation.prospective_devval_global_companion_schedule_v1 import (
    freeze_global_devval_companion_schedule_v1,
)
from app.mie.validation.prospective_devval_research_blueprint_v1 import (
    compile_research_campaign,
)
from tests.unit.mie.test_devval_campaign_batch import synthetic_grid
from tests.unit.mie.test_devval_global_companion_schedule_v1 import _inputs
from tests.unit.mie.test_devval_joint_companion_coverage_v1 import (
    _audit,
    _event_inputs,
    _install_readback_replays,
)


@pytest.fixture(scope="module")
def joint_score_cohort():
    inputs = _inputs()
    config = FrozenLegacyRawScorerConfigV1(
        strategy="trend_pullback",
        analysis_version="synthetic-v1",
        source_checkpoint=inputs["source_checkpoint"],
        source_archive_sha256=inputs["source_archive_sha256"],
    )
    blueprint = inputs["blueprint"].model_copy(
        update={"legacy_score_source_sha256": config.canonical_sha256()}
    )
    plan = compile_research_campaign(blueprint)
    schedule = freeze_global_devval_companion_schedule_v1(
        **(
            inputs
            | {
                "blueprint": blueprint,
                "expected_blueprint_sha256": blueprint.canonical_sha256(),
                "campaign_plan": plan,
                "legacy_scorer_config_json": config.canonical_json_bytes(),
            }
        )
    )
    campaign, rows, pins = synthetic_grid(plan)
    return (schedule, _build_batch(campaign, rows, **pins)), config


def _score(case, report, companions):
    (schedule, batch), config = case
    return subject.score_joint_devval_companion_event_v1(
        schedule.payload,
        expected_schedule_sha256=schedule.sha256,
        coverage_payload=report.payload,
        expected_coverage_sha256=report.sha256,
        expected_batch_sha256=batch.sha256,
        config_json=config.canonical_json_bytes(),
        expected_config_sha256=config.canonical_sha256(),
        slot_ordinal=0,
        companions=companions,
    )


def _covered_event(case, tmp_path, monkeypatch):
    cohort, config = case
    companions, measured, markets = _event_inputs(cohort, tmp_path)
    seen = _install_readback_replays(monkeypatch, measured, markets)
    report = _audit(cohort, companions)
    assert report.contract.slots[0].status == "covered_computational"
    seen.clear()
    monkeypatch.setattr(
        subject,
        "verify_global_public_measured_companion_v1",
        coverage.verify_global_public_measured_companion_v1,
    )
    monkeypatch.setattr(
        subject,
        "replay_legacy_baseline_market_from_companion_v1",
        coverage.replay_legacy_baseline_market_from_companion_v1,
    )
    scored = []

    def score(**kwargs):
        symbol = kwargs["instrument_id"]
        assert kwargs["decision_at"] == report.contract.slots[0].joint_decision_at
        assert decode(kwargs["snapshot_json"])["instrument_id"] == symbol
        assert sha(kwargs["snapshot_json"]) == kwargs["expected_snapshot_sha256"]
        assert sha(kwargs["receipt_json"]) == kwargs["expected_receipt_sha256"]
        assert kwargs["expected_config_sha256"] == config.canonical_sha256()
        assert kwargs["expected_source_checkpoint"] == config.source_checkpoint
        scored.append(symbol)
        neutral = symbol == "ETH-USDT-SWAP"
        return _freeze(
            FrozenLegacyRawScoreV1(
                instrument_id=symbol,
                decision_at=kwargs["decision_at"],
                strategy=config.strategy,
                raw_direction="neutral" if neutral else "long",
                status="neutral" if neutral else "directional",
                raw_score=None if neutral else 75,
                config_sha256=config.canonical_sha256(),
                source_checkpoint=config.source_checkpoint,
                source_archive_sha256=config.source_archive_sha256,
                market_replay_receipt_sha256=kwargs["expected_receipt_sha256"],
                market_snapshot_sha256=kwargs["expected_snapshot_sha256"],
                analysis_version=config.analysis_version,
            ),
            contract_type=FrozenLegacyRawScoreV1,
        )

    monkeypatch.setattr(subject, "replay_frozen_legacy_raw_score_v1", score)
    return companions, report, seen, scored


def test_complete_event_reopens_both_exact_markets_before_emitting_scores(
    joint_score_cohort, tmp_path, monkeypatch
):
    companions, report, seen, scored = _covered_event(
        joint_score_cohort, tmp_path, monkeypatch
    )
    result = _score(joint_score_cohort, report, companions).contract
    assert [symbol for symbol, _ in seen] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    assert scored == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    assert [side.status for side in result.sides] == ["directional", "neutral"]
    assert [side.raw_result.raw_score for side in result.sides] == [75, None]
    assert result.predictive_oos_eligible is result.execution_authority is False


def test_incomplete_event_preserves_missing_without_reopening(
    joint_score_cohort, monkeypatch
):
    cohort, _ = joint_score_cohort
    report = _audit(cohort)

    def no_read(*args, **kwargs):
        pytest.fail("incomplete event touched a market source")

    monkeypatch.setattr(subject, "verify_global_public_measured_companion_v1", no_read)
    monkeypatch.setattr(
        subject, "replay_legacy_baseline_market_from_companion_v1", no_read
    )
    result = _score(joint_score_cohort, report, None).contract
    assert [side.status for side in result.sides] == ["missing", "missing"]
    assert all(side.raw_result is None for side in result.sides)


def test_second_market_receipt_drift_prevents_any_score(
    joint_score_cohort, tmp_path, monkeypatch
):
    companions, report, _, scored = _covered_event(
        joint_score_cohort, tmp_path, monkeypatch
    )
    original = subject.replay_legacy_baseline_market_from_companion_v1

    def changed(*args, **kwargs):
        replay = original(*args, **kwargs)
        if replay.market.instrument_id == "ETH-USDT-SWAP":
            return replace(replay, receipt_json=replay.receipt_json + b"\n")
        return replay

    monkeypatch.setattr(
        subject, "replay_legacy_baseline_market_from_companion_v1", changed
    )
    with pytest.raises(ValueError):
        _score(joint_score_cohort, report, companions)
    assert scored == []
