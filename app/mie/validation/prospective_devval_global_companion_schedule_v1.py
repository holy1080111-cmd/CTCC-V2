"""Compile pre-window Global market companion coordinates for a new D/V campaign."""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import pairwise
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.domain.source_primitives import canonical, decode, sha
from app.mie.validation.artifact import SHA256_RE, FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.batch_replay import TARGETS
from app.mie.validation.contracts import (
    DatasetPartition,
    Gate3Contract,
    Identifier,
    Sha256,
    require_utc,
)
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    ProspectiveDevvalCampaignPlanV1,
)
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
)
from app.mie.validation.prospective_devval_research_blueprint_v1 import (
    DevvalResearchBlueprintV1,
    verify_research_campaign,
)
from app.trade_qualification.public_market_collector import (
    PublicMarketCollectionPolicy,
    _digest,
    _policy_copy,
)

_REST = "https://openapi.okx.com"
_SYMBOLS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
_MAX_SLOTS = 4096
_MAX_SCHEDULE_BYTES = 1024 * 1024
_MAX_LEGACY_CONFIG_BYTES = 32 * 1024
_SHORTEST_COMPANION_CANDLE_SECONDS = 300
_MAX_SOURCE_AGE_SECONDS = 5


class GlobalDevvalCompanionSlotV1(Gate3Contract):
    partition: Literal[DatasetPartition.DEVELOPMENT, DatasetPartition.VALIDATION]
    event_closed_at: datetime
    decision_cutoff_at: datetime

    @field_validator("event_closed_at", "decision_cutoff_at")
    @classmethod
    def utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)


class GlobalDevvalCompanionScheduleV1(Gate3Contract):
    """Both declared instruments are required for every ordered close slot."""

    schema_version: Literal["ctcc.mie.devval_global_companion_schedule.v1"] = (
        "ctcc.mie.devval_global_companion_schedule.v1"
    )
    environment: Literal["anonymous_global_public"] = "anonymous_global_public"
    rest_origin: Literal["https://openapi.okx.com"] = _REST
    ws_origin: Literal["wss://ws.okx.com:443/ws/v5/public"] = (
        "wss://ws.okx.com:443/ws/v5/public"
    )
    instrument_ids: tuple[Literal["BTC-USDT-SWAP"], Literal["ETH-USDT-SWAP"]] = _SYMBOLS
    campaign_id: Identifier
    campaign_plan_sha256: Sha256
    research_blueprint_sha256: Sha256
    source_checkpoint: DevvalCandidateSourceV1
    source_archive_sha256: Sha256
    market_policy_sha256: Sha256
    legacy_scorer_config_sha256: Sha256
    planned_at: datetime
    horizon_seconds: Literal[900, 3600, 14400]
    decision_delay_seconds: int = Field(ge=1, lt=_SHORTEST_COMPANION_CANDLE_SECONDS)
    slots: tuple[GlobalDevvalCompanionSlotV1, ...] = Field(
        min_length=2, max_length=_MAX_SLOTS
    )
    timing_basis: Literal["caller_declared_unverified"] = "caller_declared_unverified"
    pre_window_commit_proven: Literal[False] = False
    independent_availability_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("planned_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return require_utc(value, "planned_at")

    @model_validator(mode="after")
    def exact_cutoffs(self) -> GlobalDevvalCompanionScheduleV1:
        delay = timedelta(seconds=self.decision_delay_seconds)
        if (
            self.decision_delay_seconds >= self.horizon_seconds
            or self.slots[0].partition != DatasetPartition.DEVELOPMENT
            or self.slots[-1].partition != DatasetPartition.VALIDATION
            or self.planned_at >= self.slots[0].event_closed_at
            or any(
                slot.decision_cutoff_at - slot.event_closed_at != delay
                for slot in self.slots
            )
            or any(
                left.decision_cutoff_at >= right.event_closed_at
                or (
                    left.partition == DatasetPartition.VALIDATION
                    and right.partition == DatasetPartition.DEVELOPMENT
                )
                for left, right in pairwise(self.slots)
            )
        ):
            raise ValueError("Global companion slot chronology differs")
        return self


def freeze_global_devval_companion_schedule_v1(
    blueprint: DevvalResearchBlueprintV1,
    *,
    expected_blueprint_sha256: str,
    campaign_plan: ProspectiveDevvalCampaignPlanV1,
    source_checkpoint: DevvalCandidateSourceV1,
    source_archive_sha256: str,
    market_policy: PublicMarketCollectionPolicy,
    expected_market_policy_sha256: str,
    legacy_scorer_config_json: bytes,
    decision_delay_seconds: int,
) -> FrozenGate3Artifact[GlobalDevvalCompanionScheduleV1]:
    """Bind declarations and exact inputs without opening any source or clock."""
    if (
        type(blueprint) is not DevvalResearchBlueprintV1
        or type(campaign_plan) is not ProspectiveDevvalCampaignPlanV1
        or type(source_checkpoint) is not DevvalCandidateSourceV1
        or type(expected_blueprint_sha256) is not str
        or type(expected_market_policy_sha256) is not str
        or type(source_archive_sha256) is not str
        or SHA256_RE.fullmatch(source_archive_sha256) is None
        or type(decision_delay_seconds) is not int
        or not 0 < decision_delay_seconds < _SHORTEST_COMPANION_CANDLE_SECONDS
    ):
        raise ValueError("Global companion schedule inputs invalid")
    blueprint = DevvalResearchBlueprintV1.model_validate(blueprint)
    plan = ProspectiveDevvalCampaignPlanV1.model_validate(campaign_plan)
    source = DevvalCandidateSourceV1.model_validate(source_checkpoint)
    blueprint_sha256 = blueprint.canonical_sha256()
    if (
        blueprint_sha256 != expected_blueprint_sha256
        or plan.origin != _REST
        or plan.instrument_ids != _SYMBOLS
        or plan.research_blueprint_sha256 != blueprint_sha256
        or (source.commit, source.tree, source.manifest_sha256)
        != (
            plan.declared_source_commit,
            plan.declared_source_tree,
            plan.manifest_sha256,
        )
    ):
        raise ValueError("Global companion campaign or source differs")
    verify_research_campaign(plan, blueprint)

    policy = _policy_copy(market_policy)
    policy_sha256 = _digest(policy)
    if policy_sha256 != expected_market_policy_sha256 or any(
        component.max_age_seconds > _MAX_SOURCE_AGE_SECONDS
        for component in (policy.quote, policy.market_aux, policy.ws)
    ):
        raise ValueError("Global companion market policy differs")

    config = decode(legacy_scorer_config_json, _MAX_LEGACY_CONFIG_BYTES)
    config_sha256 = sha(legacy_scorer_config_json)
    if (
        type(config) is not dict
        or not config
        or canonical(config) != legacy_scorer_config_json
        or config_sha256 != blueprint.legacy_score_source_sha256
    ):
        raise ValueError("Global companion legacy scorer config differs")

    feature = blueprint.study.feature_plan
    horizon = feature.bar_horizon
    history = feature.history_bars
    if (
        (horizon.label, horizon.seconds) not in TARGETS
        or decision_delay_seconds >= horizon.seconds
        or history * horizon.seconds + decision_delay_seconds
        > blueprint.study.feature_dependency_seconds
    ):
        raise ValueError("Global companion decision delay exceeds declared geometry")
    width = timedelta(seconds=horizon.seconds)
    delay = timedelta(seconds=decision_delay_seconds)
    outcome_bars = feature.outcome_horizon_seconds // horizon.seconds
    slots = []
    for window in (plan.development, plan.validation):
        bars, remainder = divmod(window.end_at - window.start_at, width)
        if remainder or bars < history + outcome_bars:
            raise ValueError("Global companion partition geometry incomplete")
        for offset in range(history, bars - outcome_bars + 1):
            close = window.start_at + offset * width
            slots.append(
                GlobalDevvalCompanionSlotV1(
                    partition=window.partition,
                    event_closed_at=close,
                    decision_cutoff_at=close + delay,
                )
            )

    artifact = _freeze(
        GlobalDevvalCompanionScheduleV1(
            campaign_id=plan.campaign_id,
            campaign_plan_sha256=plan.canonical_sha256(),
            research_blueprint_sha256=blueprint_sha256,
            source_checkpoint=source,
            source_archive_sha256=source_archive_sha256,
            market_policy_sha256=policy_sha256,
            legacy_scorer_config_sha256=config_sha256,
            planned_at=plan.planned_at,
            horizon_seconds=horizon.seconds,
            decision_delay_seconds=decision_delay_seconds,
            slots=tuple(slots),
        ),
        contract_type=GlobalDevvalCompanionScheduleV1,
    )
    if len(artifact.payload) > _MAX_SCHEDULE_BYTES:
        raise ValueError("Global companion schedule exceeds its storage bound")
    return artifact


def verify_global_devval_companion_schedule_v1(
    payload: bytes,
    *,
    expected_schedule_sha256: str,
    blueprint: DevvalResearchBlueprintV1,
    expected_blueprint_sha256: str,
    campaign_plan: ProspectiveDevvalCampaignPlanV1,
    source_checkpoint: DevvalCandidateSourceV1,
    source_archive_sha256: str,
    market_policy: PublicMarketCollectionPolicy,
    expected_market_policy_sha256: str,
    legacy_scorer_config_json: bytes,
    decision_delay_seconds: int,
) -> GlobalDevvalCompanionScheduleV1:
    """Read back the independently pinned bytes and derive the whole grid again."""
    if type(payload) is not bytes or not 0 < len(payload) <= _MAX_SCHEDULE_BYTES:
        raise ValueError("Global companion schedule payload invalid")
    retained = _verify(
        payload,
        expected_sha256=expected_schedule_sha256,
        contract_type=GlobalDevvalCompanionScheduleV1,
    )
    expected = freeze_global_devval_companion_schedule_v1(
        blueprint,
        expected_blueprint_sha256=expected_blueprint_sha256,
        campaign_plan=campaign_plan,
        source_checkpoint=source_checkpoint,
        source_archive_sha256=source_archive_sha256,
        market_policy=market_policy,
        expected_market_policy_sha256=expected_market_policy_sha256,
        legacy_scorer_config_json=legacy_scorer_config_json,
        decision_delay_seconds=decision_delay_seconds,
    )
    if expected.payload != payload:
        raise ValueError("Global companion schedule readback differs")
    return retained
