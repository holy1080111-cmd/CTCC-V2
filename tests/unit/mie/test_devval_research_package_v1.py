"""Synthetic decisions and fail-closed canonical package readback."""

from dataclasses import replace

import pytest

from app.domain.source_primitives import canonical, decode, sha
from app.mie.validation.frozen_legacy_raw_scorer_v1 import FrozenLegacyRawScorerConfigV1
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
)
from app.mie.validation.prospective_devval_legacy_calibration_policy_v1 import (
    LegacyCalibrationPolicyV1,
)
from app.mie.validation.prospective_devval_research_package_v1 import (
    DevvalResearchPackageError,
    DevvalResearchPackageInputsV1,
    compile_devval_research_package_v1,
    verify_devval_research_package_v1,
)
from app.trade_qualification.public_market_collector import _digest
from tests.unit.mie.test_devval_research_blueprint import _blueprint
from tests.unit.test_qualification_public_market_collector import policy


@pytest.fixture(scope="module")
def selections():
    blueprint = _blueprint()
    source = DevvalCandidateSourceV1(
        commit=blueprint.capture.declared_source_commit,
        tree=blueprint.capture.declared_source_tree,
        manifest_sha256=blueprint.capture.manifest_sha256,
    )
    scorer = FrozenLegacyRawScorerConfigV1(
        strategy="trend_pullback",
        analysis_version="synthetic-v1",
        source_checkpoint=source,
        source_archive_sha256="d" * 64,
    )
    feature = blueprint.study.feature_plan
    calibration = LegacyCalibrationPolicyV1(
        scorer_config_sha256=scorer.canonical_sha256(),
        joint_feature_plan_sha256=feature.canonical_sha256,
        outcome_horizon_seconds=feature.outcome_horizon_seconds,
        positive_threshold=feature.positive_threshold,
        source_checkpoint=source,
        source_archive_sha256=scorer.source_archive_sha256,
        score_cuts=(20, 80, 100),
        prior_alpha=2,
        prior_beta=3,
        minimum_distinct_joint_events=2,
    )
    blueprint = blueprint.model_copy(
        update={"legacy_score_source_sha256": scorer.canonical_sha256()}
    )
    market = policy(counts=(240,) * 4)
    inputs = DevvalResearchPackageInputsV1(
        blueprint_bytes=blueprint.canonical_json_bytes(),
        expected_blueprint_sha256=blueprint.canonical_sha256(),
        scorer_config_bytes=scorer.canonical_json_bytes(),
        calibration_policy_bytes=calibration.canonical_json_bytes(),
        expected_calibration_policy_sha256=calibration.canonical_sha256(),
        market_policy=market,
        expected_market_policy_sha256=_digest(market),
        expected_source_checkpoint=source,
        expected_source_archive_sha256=scorer.source_archive_sha256,
        decision_delay_seconds=60,
    )
    return inputs, blueprint, calibration


@pytest.fixture(scope="module")
def package(selections):
    return compile_devval_research_package_v1(selections[0])


def test_exact_package_reproduces_both_capture_rosters_and_declared_boundaries(
    selections, package
):
    inputs, blueprint, calibration = selections
    plan = package.campaign.contract.plan
    schedule = package.companion_schedule.contract
    contract = package.package.contract
    assert contract.campaign_id == plan.campaign_id == schedule.campaign_id
    assert contract.blueprint_sha256 == blueprint.canonical_sha256()
    assert contract.calibration_policy_sha256 == calibration.canonical_sha256()
    assert contract.scorer_config_sha256 == schedule.legacy_scorer_config_sha256
    assert contract.campaign_plan_sha256 == plan.canonical_sha256()
    assert contract.campaign_manifest_sha256 == package.campaign.sha256
    assert contract.companion_schedule_sha256 == package.companion_schedule.sha256
    assert contract.planned_minute_rows == 20_160
    assert contract.planned_companion_slots == len(schedule.slots) > 0
    assert len(package.campaign.contract.source_plan_sha256s) == 168
    assert schedule.instrument_ids == ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
    assert contract.declared_planned_at == plan.planned_at
    assert contract.development_opens_at == plan.development.start_at
    assert contract.development_closes_at == plan.development.end_at
    assert contract.validation_opens_at == plan.validation.start_at
    assert contract.validation_closes_at == plan.validation.end_at
    assert contract.timing_basis == "caller_declared_unverified"
    assert contract.pre_window_publication_verified is False
    assert contract.independent_availability_verified is False
    assert contract.predictive_oos_eligible is False
    assert contract.execution_authority is False

    rebuilt = verify_devval_research_package_v1(
        package.package.payload,
        expected_package_sha256=package.package.sha256,
        inputs=inputs,
    )
    assert rebuilt == package


@pytest.mark.parametrize("replacement", ("calibration", "market", "delay"))
def test_valid_substitute_choices_do_not_match_the_original_pinned_package(
    selections, package, replacement
):
    inputs, _, calibration = selections
    if replacement == "calibration":
        changed = calibration.model_copy(update={"prior_beta": 4})
        modified = replace(
            inputs,
            calibration_policy_bytes=changed.canonical_json_bytes(),
            expected_calibration_policy_sha256=changed.canonical_sha256(),
        )
    elif replacement == "market":
        changed_market = policy(counts=(240, 240, 240, 241))
        modified = replace(
            inputs,
            market_policy=changed_market,
            expected_market_policy_sha256=_digest(changed_market),
        )
    else:
        modified = replace(inputs, decision_delay_seconds=59)
    with pytest.raises(DevvalResearchPackageError, match="readback_refused"):
        verify_devval_research_package_v1(
            package.package.payload,
            expected_package_sha256=package.package.sha256,
            inputs=modified,
        )


def test_inconsistent_source_or_scorer_pin_is_refused(selections):
    inputs, blueprint, _ = selections
    changed_blueprint = blueprint.model_copy(
        update={"legacy_score_source_sha256": "9" * 64}
    )
    for modified in (
        replace(
            inputs,
            blueprint_bytes=changed_blueprint.canonical_json_bytes(),
            expected_blueprint_sha256=changed_blueprint.canonical_sha256(),
        ),
        replace(inputs, expected_source_archive_sha256="9" * 64),
    ):
        with pytest.raises(DevvalResearchPackageError, match="package_refused"):
            compile_devval_research_package_v1(modified)


@pytest.mark.parametrize(
    ("input_name", "pin_name", "injected_key"),
    (
        ("blueprint_bytes", "expected_blueprint_sha256", "validation_outcomes"),
        ("calibration_policy_bytes", "expected_calibration_policy_sha256", "labels"),
    ),
)
def test_outcome_injection_refused_even_if_repinned(
    selections, input_name, pin_name, injected_key
):
    inputs = selections[0]
    wire = decode(getattr(inputs, input_name))
    wire[injected_key] = [{"direction": "long", "realized": "1.0"}]
    altered = canonical(wire)
    with pytest.raises(DevvalResearchPackageError, match="package_refused"):
        compile_devval_research_package_v1(
            replace(inputs, **{input_name: altered, pin_name: sha(altered)})
        )


@pytest.mark.parametrize(
    ("injected_key", "injected_value"),
    (
        ("pre_window_publication_verified", True),
        ("independent_availability_verified", True),
        ("predictive_oos_eligible", True),
        ("execution_authority", True),
        ("validation_outcomes", [{"result": 1}]),
        ("campaign_manifest_sha256", "9" * 64),
    ),
)
def test_rehashed_report_cannot_grant_authority_or_replace_child_artifact(
    selections, package, injected_key, injected_value
):
    wire = decode(package.package.payload)
    wire[injected_key] = injected_value
    altered = canonical(wire)
    with pytest.raises(DevvalResearchPackageError, match="readback_refused"):
        verify_devval_research_package_v1(
            altered, expected_package_sha256=sha(altered), inputs=selections[0]
        )
