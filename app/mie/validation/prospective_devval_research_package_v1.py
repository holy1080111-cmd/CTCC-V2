"""Compile one label-free D/V research package from explicit pinned decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.mie.validation.artifact import SHA256_RE, FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.contracts import (
    DatasetPartition,
    Gate3Contract,
    Identifier,
    Sha256,
)
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    _MAX_MANIFEST_BYTES,
    ProspectiveDevvalCampaignManifestV1,
    freeze_prospective_devval_campaign_v1,
)
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
)
from app.mie.validation.prospective_devval_global_companion_schedule_v1 import (
    GlobalDevvalCompanionScheduleV1,
    freeze_global_devval_companion_schedule_v1,
)
from app.mie.validation.prospective_devval_legacy_calibration_policy_v1 import (
    verify_legacy_calibration_policy_v1,
)
from app.mie.validation.prospective_devval_research_blueprint_v1 import (
    DevvalResearchBlueprintV1,
    compile_research_campaign,
)
from app.trade_qualification.public_market_collector import PublicMarketCollectionPolicy

_MAX_PACKAGE_BYTES = 8 * 1024


class DevvalResearchPackageError(ValueError):
    """Fixed offline refusal with no source or experiment payload in the message."""


class DevvalResearchPackageV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_research_package.v1"] = (
        "ctcc.mie.devval_research_package.v1"
    )
    campaign_id: Identifier
    source_checkpoint: DevvalCandidateSourceV1
    source_archive_sha256: Sha256
    blueprint_sha256: Sha256
    campaign_plan_sha256: Sha256
    campaign_manifest_sha256: Sha256
    companion_schedule_sha256: Sha256
    scorer_config_sha256: Sha256
    calibration_policy_sha256: Sha256
    market_policy_sha256: Sha256
    declared_planned_at: datetime
    development_opens_at: datetime
    development_closes_at: datetime
    validation_opens_at: datetime
    validation_closes_at: datetime
    decision_delay_seconds: int
    planned_minute_rows: int
    planned_companion_slots: int
    timing_basis: Literal["caller_declared_unverified"] = "caller_declared_unverified"
    pre_window_publication_verified: Literal[False] = False
    independent_availability_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False


@dataclass(frozen=True, slots=True, repr=False)
class DevvalResearchPackageInputsV1:
    blueprint_bytes: bytes
    expected_blueprint_sha256: str
    scorer_config_bytes: bytes
    calibration_policy_bytes: bytes
    expected_calibration_policy_sha256: str
    market_policy: PublicMarketCollectionPolicy
    expected_market_policy_sha256: str
    expected_source_checkpoint: DevvalCandidateSourceV1
    expected_source_archive_sha256: str
    decision_delay_seconds: int


@dataclass(frozen=True, slots=True, repr=False)
class CompiledDevvalResearchPackageV1:
    package: FrozenGate3Artifact[DevvalResearchPackageV1]
    campaign: FrozenGate3Artifact[ProspectiveDevvalCampaignManifestV1]
    companion_schedule: FrozenGate3Artifact[GlobalDevvalCompanionScheduleV1]


def compile_devval_research_package_v1(
    inputs: DevvalResearchPackageInputsV1,
) -> CompiledDevvalResearchPackageV1:
    """Recompute both capture rosters from a pinned label-free research design."""
    try:
        if (
            type(inputs) is not DevvalResearchPackageInputsV1
            or type(inputs.blueprint_bytes) is not bytes
            or not 0 < len(inputs.blueprint_bytes) <= _MAX_MANIFEST_BYTES
            or type(inputs.expected_source_checkpoint) is not DevvalCandidateSourceV1
            or any(
                type(pin) is not str or SHA256_RE.fullmatch(pin) is None
                for pin in (
                    inputs.expected_blueprint_sha256,
                    inputs.expected_calibration_policy_sha256,
                    inputs.expected_market_policy_sha256,
                    inputs.expected_source_archive_sha256,
                )
            )
        ):
            raise ValueError("package inputs invalid")
        blueprint = _verify(
            inputs.blueprint_bytes,
            expected_sha256=inputs.expected_blueprint_sha256,
            contract_type=DevvalResearchBlueprintV1,
        )
        source = DevvalCandidateSourceV1.model_validate(
            inputs.expected_source_checkpoint
        )
        plan = compile_research_campaign(blueprint)
        calibration = verify_legacy_calibration_policy_v1(
            inputs.calibration_policy_bytes,
            expected_policy_sha256=inputs.expected_calibration_policy_sha256,
            scorer_config_json=inputs.scorer_config_bytes,
            feature_plan=blueprint.study.feature_plan,
            expected_source_checkpoint=source,
            expected_source_archive_sha256=inputs.expected_source_archive_sha256,
        )
        if calibration.scorer_config_sha256 != blueprint.legacy_score_source_sha256:
            raise ValueError("calibration baseline differs")
        schedule = freeze_global_devval_companion_schedule_v1(
            blueprint,
            expected_blueprint_sha256=inputs.expected_blueprint_sha256,
            campaign_plan=plan,
            source_checkpoint=source,
            source_archive_sha256=inputs.expected_source_archive_sha256,
            market_policy=inputs.market_policy,
            expected_market_policy_sha256=inputs.expected_market_policy_sha256,
            legacy_scorer_config_json=inputs.scorer_config_bytes,
            decision_delay_seconds=inputs.decision_delay_seconds,
        )
        development_events = sum(
            slot.partition == DatasetPartition.DEVELOPMENT
            for slot in schedule.contract.slots
        )
        if calibration.minimum_distinct_joint_events > development_events:
            raise ValueError("calibration support exceeds planned population")
        campaign = freeze_prospective_devval_campaign_v1(plan)
        package = _freeze(
            DevvalResearchPackageV1(
                campaign_id=plan.campaign_id,
                source_checkpoint=source,
                source_archive_sha256=inputs.expected_source_archive_sha256,
                blueprint_sha256=inputs.expected_blueprint_sha256,
                campaign_plan_sha256=campaign.contract.plan_sha256,
                campaign_manifest_sha256=campaign.sha256,
                companion_schedule_sha256=schedule.sha256,
                scorer_config_sha256=calibration.scorer_config_sha256,
                calibration_policy_sha256=inputs.expected_calibration_policy_sha256,
                market_policy_sha256=inputs.expected_market_policy_sha256,
                declared_planned_at=plan.planned_at,
                development_opens_at=plan.development.start_at,
                development_closes_at=plan.development.end_at,
                validation_opens_at=plan.validation.start_at,
                validation_closes_at=plan.validation.end_at,
                decision_delay_seconds=schedule.contract.decision_delay_seconds,
                planned_minute_rows=plan.expected_rows,
                planned_companion_slots=len(schedule.contract.slots),
            ),
            contract_type=DevvalResearchPackageV1,
        )
        return CompiledDevvalResearchPackageV1(package, campaign, schedule)
    except (ArithmeticError, TypeError, ValueError):
        raise DevvalResearchPackageError("devval_research_package_refused") from None


def verify_devval_research_package_v1(
    package_bytes: bytes,
    *,
    expected_package_sha256: str,
    inputs: DevvalResearchPackageInputsV1,
) -> CompiledDevvalResearchPackageV1:
    """Verify externally pinned bytes and reproduce the package's exact artifacts."""
    try:
        if (
            type(package_bytes) is not bytes
            or not 0 < len(package_bytes) <= _MAX_PACKAGE_BYTES
        ):
            raise ValueError("package size invalid")
        _verify(
            package_bytes,
            expected_sha256=expected_package_sha256,
            contract_type=DevvalResearchPackageV1,
        )
        compiled = compile_devval_research_package_v1(inputs)
        if compiled.package.payload != package_bytes:
            raise ValueError("package differs from replay")
        return compiled
    except (ArithmeticError, TypeError, ValueError):
        raise DevvalResearchPackageError(
            "devval_research_package_readback_refused"
        ) from None
