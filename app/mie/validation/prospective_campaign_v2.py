"""Bounded, computational-only campaign over unchanged V1 public-minute seals.

This joins declared child seals and schedules; it does not attest their database
commit, source capture, independent custody, or evaluator first access. No V1
limit is widened and no child window is eligible on this contract's authority.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    _freeze,
    _verify,
)
from app.mie.validation.contracts import (
    Gate3Claim,
    Gate3Contract,
    Identifier,
    Sha256,
    require_utc,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_capture_schedule import (
    ProspectiveCaptureScheduleV1,
    checked_capture_schedule,
)
from app.mie.validation.replay import FrozenFeatureReplayPlanV2

INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
BAR_SECONDS = 14_400
ROWS_PER_FOUR_HOURS = 240 * len(INSTRUMENTS)
MAX_CHILD_ROWS = 3_840
MAX_CAMPAIGN_CHILDREN = 16
MAX_CAMPAIGN_ROWS = MAX_CHILD_ROWS * MAX_CAMPAIGN_CHILDREN


class ProspectiveCampaignError(ValueError):
    """A declared child, frozen feature plan, or complete grid differs."""


class CampaignChildV2(Gate3Contract):
    ordinal: int = Field(ge=0, lt=MAX_CAMPAIGN_CHILDREN)
    first_row: int = Field(ge=0, lt=MAX_CAMPAIGN_ROWS)
    expected_rows: int = Field(ge=ROWS_PER_FOUR_HOURS, le=MAX_CHILD_ROWS)
    holdout_id: Identifier
    seal_sha256: Sha256
    coordinate_plan_sha256: Sha256
    schedule_sha256: Sha256
    protocol_sha256: Sha256
    start_at: datetime
    end_at: datetime
    first_permitted_access_at: datetime
    seal_declared_at: datetime
    schedule_declared_at: datetime

    @field_validator(
        "start_at",
        "end_at",
        "first_permitted_access_at",
        "seal_declared_at",
        "schedule_declared_at",
    )
    @classmethod
    def utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def exact_child_grid(self) -> CampaignChildV2:
        if (
            self.expected_rows % ROWS_PER_FOUR_HOURS
            or self.end_at - self.start_at
            != timedelta(minutes=self.expected_rows // len(INSTRUMENTS))
            or self.end_at <= self.start_at
            or self.first_permitted_access_at < self.end_at
            or self.seal_declared_at >= self.schedule_declared_at
            or self.schedule_declared_at >= self.start_at
            or any(
                value.minute or value.second or value.microsecond or value.hour % 4
                for value in (self.start_at, self.end_at)
            )
        ):
            raise ValueError("campaign child is not a complete declared 4H grid")
        return self


def _child_chain_sha256(children: tuple[CampaignChildV2, ...]) -> str:
    digest = hashlib.sha256()
    for child in children:
        payload = child.canonical_json_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _protocol_sha256(seal: Gate3ProspectivePreregistration) -> str:
    """Exclude only child-specific identity/time/window; retain all model policy."""
    protocol = json.loads(seal.canonical_json_bytes())
    for child_specific in ("preregistration_id", "created_at", "prospective_holdout"):
        protocol.pop(child_specific)
    return hashlib.sha256(
        json.dumps(
            protocol, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


class ProspectiveCampaignV2(Gate3Contract):
    """A hashable geometry/protocol join, never an availability or OOS receipt."""

    schema_version: Literal["ctcc.mie.gate3.prospective_campaign.v2"] = (
        "ctcc.mie.gate3.prospective_campaign.v2"
    )
    campaign_id: Identifier
    instrument_ids: tuple[Literal["BTC-USDT-SWAP"], Literal["ETH-USDT-SWAP"]] = (
        INSTRUMENTS
    )
    source_tree_sha256: Sha256
    protocol_sha256: Sha256
    feature_plan_sha256: Sha256
    history_bars: int = Field(ge=21, le=10_000)
    outcome_horizon_seconds: int = Field(ge=BAR_SECONDS)
    minimum_candidate_bars: int = Field(ge=1, le=10_000)
    start_at: datetime
    end_at: datetime
    first_permitted_access_at: datetime
    declared_at: datetime
    expected_rows: int = Field(ge=1, le=MAX_CAMPAIGN_ROWS)
    children: tuple[CampaignChildV2, ...] = Field(
        min_length=1, max_length=MAX_CAMPAIGN_CHILDREN
    )
    child_chain_sha256: Sha256
    declaration_time_basis: Literal["caller_declared_unverified"] = (
        "caller_declared_unverified"
    )
    pre_window_commit_proven: Literal[False] = False
    complete_source_capture_proven: Literal[False] = False
    evaluator_first_read_proven: Literal[False] = False
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("start_at", "end_at", "first_permitted_access_at", "declared_at")
    @classmethod
    def utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def exact_global_grid(self) -> ProspectiveCampaignV2:
        children = self.children
        if (
            self.outcome_horizon_seconds % BAR_SECONDS
            or self.child_chain_sha256 != _child_chain_sha256(children)
            or self.start_at != children[0].start_at
            or self.end_at != children[-1].end_at
            or self.first_permitted_access_at
            != max(child.first_permitted_access_at for child in children)
            or self.declared_at != max(child.schedule_declared_at for child in children)
            or self.declared_at >= self.start_at
            or self.expected_rows != sum(child.expected_rows for child in children)
            or self.expected_rows
            != (self.end_at - self.start_at) // timedelta(minutes=1) * len(INSTRUMENTS)
            or self.expected_rows > MAX_CAMPAIGN_ROWS
        ):
            raise ValueError("campaign global grid or child chain differs")
        seen_seals: set[str] = set()
        seen_schedules: set[str] = set()
        seen_holdouts: set[str] = set()
        next_row = 0
        for ordinal, child in enumerate(children):
            if (
                child.ordinal != ordinal
                or child.first_row != next_row
                or child.protocol_sha256 != self.protocol_sha256
                or child.seal_sha256 in seen_seals
                or child.schedule_sha256 in seen_schedules
                or child.holdout_id in seen_holdouts
                or child.seal_declared_at >= self.start_at
                or child.schedule_declared_at >= self.start_at
                or (ordinal and child.start_at != children[ordinal - 1].end_at)
            ):
                raise ValueError("campaign child is missing, overlapping, or changed")
            next_row += child.expected_rows
            seen_seals.add(child.seal_sha256)
            seen_schedules.add(child.schedule_sha256)
            seen_holdouts.add(child.holdout_id)
        required_bars = (
            self.history_bars
            + self.outcome_horizon_seconds // BAR_SECONDS
            + self.minimum_candidate_bars
            - 1
        )
        if self.expected_rows < required_bars * ROWS_PER_FOUR_HOURS:
            raise ValueError("campaign cannot fit feature history and outcome")
        return self


def _rebuild_campaign(
    *,
    campaign_id: str,
    children: tuple[
        tuple[Gate3ProspectivePreregistration, ProspectiveCaptureScheduleV1], ...
    ],
    feature_plan: FrozenFeatureReplayPlanV2,
    expected_feature_plan_sha256: str,
    minimum_candidate_bars: int,
) -> ProspectiveCampaignV2:
    if (
        type(children) is not tuple
        or not 1 <= len(children) <= MAX_CAMPAIGN_CHILDREN
        or type(feature_plan) is not FrozenFeatureReplayPlanV2
        or type(expected_feature_plan_sha256) is not str
        or feature_plan.canonical_sha256 != expected_feature_plan_sha256
        or feature_plan.bar_horizon.seconds != BAR_SECONDS
        or feature_plan.bar_horizon.label != "4H"
        or type(minimum_candidate_bars) is not int
        or not 1 <= minimum_candidate_bars <= 10_000
    ):
        raise ProspectiveCampaignError("campaign inputs or frozen feature pin invalid")
    checked_plan = FrozenFeatureReplayPlanV2.model_validate(
        feature_plan.model_dump(mode="python")
    )
    refs: list[CampaignChildV2] = []
    first_seal: Gate3ProspectivePreregistration | None = None
    first_coordinate = None
    first_protocol = None
    next_row = 0
    first_start = None
    for ordinal, pair in enumerate(children):
        if (
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not Gate3ProspectivePreregistration
            or type(pair[1]) is not ProspectiveCaptureScheduleV1
        ):
            raise ProspectiveCampaignError("exact V1 seal and schedule pairs required")
        seal, schedule = pair
        checked_seal = Gate3ProspectivePreregistration.model_validate(
            seal.model_dump(mode="python")
        )
        checked_schedule = checked_capture_schedule(schedule, seal=checked_seal)
        coordinate = checked_schedule.coordinate_plan
        window = checked_seal.prospective_holdout
        protocol_sha = _protocol_sha256(checked_seal)
        if ordinal == 0:
            first_seal = checked_seal
            first_coordinate = coordinate
            first_protocol = protocol_sha
            first_start = coordinate.start_at
        assert first_seal is not None and first_coordinate is not None
        assert first_protocol is not None and first_start is not None
        feature_plan_pins = [
            parameter.value
            for feature in checked_seal.features
            for parameter in feature.parameters
            if parameter.name == "feature_plan_sha256"
        ]
        history_values = [
            parameter.value
            for feature in checked_seal.features
            for parameter in feature.parameters
            if parameter.name == "history_bars"
        ]
        if len(feature_plan_pins) != 1 or len(history_values) != 1:
            raise ProspectiveCampaignError("campaign feature plan binding is ambiguous")
        if (
            coordinate.instrument_ids != INSTRUMENTS
            or coordinate.origin != first_coordinate.origin
            or coordinate.source != first_coordinate.source
            or coordinate.source_version != first_coordinate.source_version
            or checked_seal.source_tree_sha256 != first_seal.source_tree_sha256
            or protocol_sha != first_protocol
            or window.expected_rows != coordinate.expected_rows
            or coordinate.expected_rows > MAX_CHILD_ROWS
            or checked_seal.created_at >= first_start
            or checked_schedule.planned_at >= first_start
            or checked_seal.outcome_label.horizon_seconds
            != checked_plan.outcome_horizon_seconds
            or checked_seal.outcome_label.positive_threshold
            != checked_plan.positive_threshold
            or window.publication_lag_seconds
            != first_seal.prospective_holdout.publication_lag_seconds
            or checked_seal.selection_split.max_feature_dependency_seconds
            < checked_plan.history_bars * BAR_SECONDS
            or feature_plan_pins[0] != expected_feature_plan_sha256
            or history_values[0] != checked_plan.history_bars
        ):
            raise ProspectiveCampaignError("campaign source or frozen protocol differs")
        refs.append(
            CampaignChildV2(
                ordinal=ordinal,
                first_row=next_row,
                expected_rows=coordinate.expected_rows,
                holdout_id=coordinate.holdout_id,
                seal_sha256=checked_seal.canonical_sha256(),
                coordinate_plan_sha256=coordinate.canonical_sha256(),
                schedule_sha256=checked_schedule.canonical_sha256(),
                protocol_sha256=protocol_sha,
                start_at=coordinate.start_at,
                end_at=coordinate.end_at,
                first_permitted_access_at=window.first_permitted_access_at,
                seal_declared_at=checked_seal.created_at,
                schedule_declared_at=checked_schedule.planned_at,
            )
        )
        next_row += coordinate.expected_rows
    return ProspectiveCampaignV2(
        campaign_id=campaign_id,
        source_tree_sha256=first_seal.source_tree_sha256,
        protocol_sha256=first_protocol,
        feature_plan_sha256=expected_feature_plan_sha256,
        history_bars=checked_plan.history_bars,
        outcome_horizon_seconds=checked_plan.outcome_horizon_seconds,
        minimum_candidate_bars=minimum_candidate_bars,
        start_at=refs[0].start_at,
        end_at=refs[-1].end_at,
        first_permitted_access_at=refs[-1].first_permitted_access_at,
        declared_at=max(ref.schedule_declared_at for ref in refs),
        expected_rows=next_row,
        children=tuple(refs),
        child_chain_sha256=_child_chain_sha256(tuple(refs)),
    )


def freeze_prospective_campaign_v2(
    *,
    campaign_id: str,
    children: tuple[
        tuple[Gate3ProspectivePreregistration, ProspectiveCaptureScheduleV1], ...
    ],
    feature_plan: FrozenFeatureReplayPlanV2,
    expected_feature_plan_sha256: str,
    minimum_candidate_bars: int = 1,
) -> FrozenGate3Artifact[ProspectiveCampaignV2]:
    """Freeze a source-declared campaign; never infer publication or capture."""
    campaign = _rebuild_campaign(
        campaign_id=campaign_id,
        children=children,
        feature_plan=feature_plan,
        expected_feature_plan_sha256=expected_feature_plan_sha256,
        minimum_candidate_bars=minimum_candidate_bars,
    )
    return _freeze(campaign, contract_type=ProspectiveCampaignV2)


def verify_prospective_campaign_v2(
    payload: bytes,
    *,
    expected_sha256: str,
    campaign_id: str,
    children: tuple[
        tuple[Gate3ProspectivePreregistration, ProspectiveCaptureScheduleV1], ...
    ],
    feature_plan: FrozenFeatureReplayPlanV2,
    expected_feature_plan_sha256: str,
    minimum_candidate_bars: int = 1,
) -> ProspectiveCampaignV2:
    """Read exact frozen bytes and recompute every child from its V1 source."""
    retained = _verify(
        payload, expected_sha256=expected_sha256, contract_type=ProspectiveCampaignV2
    )
    try:
        rebuilt = _rebuild_campaign(
            campaign_id=campaign_id,
            children=children,
            feature_plan=feature_plan,
            expected_feature_plan_sha256=expected_feature_plan_sha256,
            minimum_candidate_bars=minimum_candidate_bars,
        )
    except (ValueError, TypeError) as exc:
        raise ArtifactVerificationError("campaign child source replay failed") from exc
    if retained.canonical_json_bytes() != rebuilt.canonical_json_bytes():
        raise ArtifactVerificationError("campaign differs from sealed children")
    return rebuilt


__all__ = (
    "CampaignChildV2",
    "ProspectiveCampaignError",
    "ProspectiveCampaignV2",
    "freeze_prospective_campaign_v2",
    "verify_prospective_campaign_v2",
)
