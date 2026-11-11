"""Freeze complete prospective Development/Validation capture coordinates."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.domain.source_primitives import sha
from app.mie.validation.artifact import FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.contracts import (
    DatasetPartition,
    Gate3Claim,
    Gate3Contract,
    Identifier,
    PartitionWindow,
    Sha256,
    require_utc,
)
from app.mie.validation.development_validation_source_collection_v1 import (
    MAX_PLAN_BYTES,
)
from app.mie.validation.development_validation_source_plan_v1 import (
    DevelopmentValidationSourceCoordinateV1,
    DevelopmentValidationSourcePlanV1,
)
from app.mie.validation.prospective_development_validation_plan import INSTRUMENTS, _ns
from app.public_market_source.public_market_receipts import (
    MINUTE_NS,
    PublicMinuteCapturePlanV1,
)

_BLOCK = timedelta(hours=1)
_MINIMUM_WINDOW = timedelta(hours=21 * 4)
_MAX_BLOCKS = 1008
_MAX_MANIFEST_BYTES = 128 * 1024
_ROWS_PER_BLOCK = 60 * len(INSTRUMENTS)
RESEARCH_CAMPAIGN_PREFIX = "devval:research:"


class DevvalCampaignCaptureCoordinatesV1(Gate3Contract):
    """Complete source coordinates before the campaign identifier is assigned."""

    planned_at: datetime
    declared_source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    declared_source_tree: str = Field(pattern=r"^[0-9a-f]{40}$")
    manifest_sha256: Sha256
    origin: Literal[
        "https://openapi.okx.com", "https://us.okx.com", "https://eea.okx.com"
    ]
    development: PartitionWindow
    validation: PartitionWindow
    instrument_ids: tuple[Literal["BTC-USDT-SWAP"], Literal["ETH-USDT-SWAP"]] = (
        INSTRUMENTS
    )
    hours_per_block: Literal[1] = 1
    expected_rows: int = Field(
        ge=2 * 21 * 4 * _ROWS_PER_BLOCK, le=_MAX_BLOCKS * _ROWS_PER_BLOCK
    )
    planning_time_basis: Literal["caller_declared_unverified"] = (
        "caller_declared_unverified"
    )
    maximum_observation_lag_seconds: Literal[60] = 60
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    pre_window_commit_proven: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("planned_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return require_utc(value, "planned_at")

    @model_validator(mode="after")
    def complete_grid(self) -> DevvalCampaignCaptureCoordinatesV1:
        if (
            self.development.partition != DatasetPartition.DEVELOPMENT
            or self.validation.partition != DatasetPartition.VALIDATION
            or self.planned_at >= self.development.start_at
            or self.validation.start_at - self.development.end_at
            < timedelta(seconds=self.maximum_observation_lag_seconds)
        ):
            raise ValueError("devval campaign partition or cutoff invalid")
        for window in (self.development, self.validation):
            if window.end_at - window.start_at < _MINIMUM_WINDOW or any(
                at.hour % 4 or at.minute or at.second or at.microsecond
                for at in (window.start_at, window.end_at)
            ):
                raise ValueError("devval campaign requires complete four-hour windows")
        if self.expected_rows != self.block_count * _ROWS_PER_BLOCK:
            raise ValueError("devval campaign row count differs")
        return self

    @property
    def block_count(self) -> int:
        return sum(
            (window.end_at - window.start_at) // _BLOCK
            for window in (self.development, self.validation)
        )


class ProspectiveDevvalCampaignPlanV1(DevvalCampaignCaptureCoordinatesV1):
    """Finite source-only plan intended for publication before either window."""

    schema_version: Literal["ctcc.mie.gate3.prospective_devval_campaign_plan.v1"] = (
        "ctcc.mie.gate3.prospective_devval_campaign_plan.v1"
    )
    campaign_id: Identifier

    @field_validator("campaign_id")
    @classmethod
    def check_research_id(cls, value: str) -> str:
        if (
            value.startswith(RESEARCH_CAMPAIGN_PREFIX)
            and re.fullmatch(
                r"[0-9a-f]{64}", value.removeprefix(RESEARCH_CAMPAIGN_PREFIX)
            )
            is None
        ):
            raise ValueError("devval research campaign identifier must pin a blueprint")
        return value

    @property
    def research_blueprint_sha256(self) -> str | None:
        if self.campaign_id.startswith(RESEARCH_CAMPAIGN_PREFIX):
            return self.campaign_id.removeprefix(RESEARCH_CAMPAIGN_PREFIX)
        return None


class ProspectiveDevvalCampaignManifestV1(Gate3Contract):
    """Canonical parent with an ordered SHA256 commitment for every source root."""

    schema_version: Literal[
        "ctcc.mie.gate3.prospective_devval_campaign_manifest.v1"
    ] = "ctcc.mie.gate3.prospective_devval_campaign_manifest.v1"
    plan: ProspectiveDevvalCampaignPlanV1
    plan_sha256: Sha256
    source_plan_sha256s: tuple[Sha256, ...] = Field(max_length=_MAX_BLOCKS)
    pre_window_commit_proven: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def complete_inventory(self) -> ProspectiveDevvalCampaignManifestV1:
        if (
            self.plan_sha256 != self.plan.canonical_sha256()
            or len(self.source_plan_sha256s) != self.plan.block_count
            or len(set(self.source_plan_sha256s)) != len(self.source_plan_sha256s)
        ):
            raise ValueError("devval campaign source inventory differs")
        return self


def _source_plan(
    plan: ProspectiveDevvalCampaignPlanV1, plan_sha256: str, ordinal: int
) -> DevelopmentValidationSourcePlanV1:
    if type(ordinal) is not int or not 0 <= ordinal < plan.block_count:
        raise ValueError("devval campaign block ordinal invalid")
    development_blocks = (plan.development.end_at - plan.development.start_at) // _BLOCK
    if ordinal < development_blocks:
        window, offset = plan.development, ordinal
    else:
        window, offset = plan.validation, ordinal - development_blocks
    opening_ns = _ns(window.start_at + offset * _BLOCK)
    created_ns = _ns(plan.planned_at)
    coordinates = []
    for local in range(_ROWS_PER_BLOCK):
        minute, symbol = divmod(local, len(INSTRUMENTS))
        start_ns = opening_ns + minute * MINUTE_NS
        capture = PublicMinuteCapturePlanV1(
            origin=plan.origin,
            instrument_id=plan.instrument_ids[symbol],
            start_ns=start_ns,
            end_ns=start_ns + MINUTE_NS,
            expected_rows=1,
            created_ns=created_ns,
            page_size=1,
            max_pages=1,
        )
        coordinates.append(
            DevelopmentValidationSourceCoordinateV1(
                ordinal=local,
                partition=window.partition.value,
                purpose="prospective_collection",
                capture_plan=capture,
                capture_plan_sha256=capture.canonical_sha256(),
            )
        )
    return DevelopmentValidationSourcePlanV1(
        collection_id=f"devval:{plan_sha256}:{ordinal:04d}",
        origin=plan.origin,
        planned_at=plan.planned_at,
        coordinates=tuple(coordinates),
        expected_rows=_ROWS_PER_BLOCK,
    )


def _block_digests(plan: ProspectiveDevvalCampaignPlanV1, pin: str) -> tuple[str, ...]:
    digests = []
    for ordinal in range(plan.block_count):
        raw = _source_plan(plan, pin, ordinal).canonical_json_bytes()
        if len(raw) > MAX_PLAN_BYTES:
            raise ValueError("devval campaign source plan exceeds storage budget")
        digests.append(sha(raw))
    return tuple(digests)


def freeze_prospective_devval_campaign_v1(
    plan: ProspectiveDevvalCampaignPlanV1,
) -> FrozenGate3Artifact[ProspectiveDevvalCampaignManifestV1]:
    """Commit every ordered source plan before handing the bytes to a custodian."""
    if type(plan) is not ProspectiveDevvalCampaignPlanV1:
        raise ValueError("devval campaign plan required")
    plan = ProspectiveDevvalCampaignPlanV1.model_validate(
        plan.model_dump(mode="python")
    )
    pin = plan.canonical_sha256()
    return _freeze(
        ProspectiveDevvalCampaignManifestV1(
            plan=plan,
            plan_sha256=pin,
            source_plan_sha256s=_block_digests(plan, pin),
        ),
        contract_type=ProspectiveDevvalCampaignManifestV1,
    )


def _read_manifest(
    payload: bytes, expected_campaign_sha256: str
) -> ProspectiveDevvalCampaignManifestV1:
    if type(payload) is not bytes or not 0 < len(payload) <= _MAX_MANIFEST_BYTES:
        raise ValueError("devval campaign manifest exceeds its bounds")
    return _verify(
        payload,
        expected_sha256=expected_campaign_sha256,
        contract_type=ProspectiveDevvalCampaignManifestV1,
    )


def verify_prospective_devval_campaign_v1(
    payload: bytes,
    *,
    expected_campaign_sha256: str,
    expected_source_commit: str,
    expected_source_tree: str,
    expected_source_manifest_sha256: str,
) -> ProspectiveDevvalCampaignManifestV1:
    """Replay all blocks against a separately verified exact source checkpoint."""
    manifest = _read_manifest(payload, expected_campaign_sha256)
    expected = (
        expected_source_commit,
        expected_source_tree,
        expected_source_manifest_sha256,
    )
    if any(type(value) is not str for value in expected) or expected != (
        manifest.plan.declared_source_commit,
        manifest.plan.declared_source_tree,
        manifest.plan.manifest_sha256,
    ):
        raise ValueError("devval campaign source checkpoint differs")
    if (
        _block_digests(manifest.plan, manifest.plan_sha256)
        != manifest.source_plan_sha256s
    ):
        raise ValueError("devval campaign source inventory digest differs")
    return manifest


def resolve_prospective_devval_campaign_block_v1(
    payload: bytes, *, expected_campaign_sha256: str, block_ordinal: int
) -> tuple[ProspectiveDevvalCampaignManifestV1, DevelopmentValidationSourcePlanV1]:
    """Recover the parent and one exact native plan against a retained digest."""
    manifest = _read_manifest(payload, expected_campaign_sha256)
    source = _source_plan(manifest.plan, manifest.plan_sha256, block_ordinal)
    raw = source.canonical_json_bytes()
    if (
        len(raw) > MAX_PLAN_BYTES
        or sha(raw) != manifest.source_plan_sha256s[block_ordinal]
    ):
        raise ValueError("devval campaign source plan digest differs")
    return manifest, source
