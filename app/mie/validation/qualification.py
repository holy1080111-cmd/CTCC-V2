from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from app.mie.validation.contracts import (
    Gate3Claim,
    Gate3Contract,
    Identifier,
    Sha256,
    require_utc,
)


class HoldoutAccessState(StrEnum):
    """Human-visible access state for a proposed Gate 3 holdout."""

    UNREAD = "unread"
    DESCRIPTIVE_SUMMARY_EXPOSED = "descriptive_summary_exposed"


class Gate3DatasetQualification(Gate3Contract):
    """Immutable bridge from external data quality to Gate 3 eligibility.

    This receipt does not make an external reference batch a replay dataset. It
    records whether the batch may still support an untouched predictive claim.
    """

    schema_version: Literal["ctcc.mie.gate3.dataset_qualification.v1"] = (
        "ctcc.mie.gate3.dataset_qualification.v1"
    )
    qualification_id: Identifier
    recorded_at: datetime
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    dataset_plan_id: Identifier
    plan_contract_sha256: Sha256
    plan_file_sha256: Sha256
    preparation_contract_sha256: Sha256
    preparation_file_sha256: Sha256
    evidence_file_sha256: Sha256
    evidence_generated_at: datetime
    completed_artifact_count: int = Field(ge=1)
    total_artifact_bytes: int = Field(ge=1)
    total_minute_rows: int = Field(ge=1)
    partition_summary_count: int = Field(ge=1)
    partition_overlap_count: Literal[0] = 0
    holdout_semantics: Literal["retrospective_not_prospective"] = (
        "retrospective_not_prospective"
    )
    holdout_access_state: HoldoutAccessState
    candidate_design_predated_holdout_access: StrictBool
    predictive_oos_eligible: StrictBool
    current_claim: Gate3Claim
    strategy_evaluated: Literal[False] = False
    costs_evaluated: Literal[False] = False
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False
    real_order_tested: Literal[False] = False

    @field_validator("recorded_at", "evidence_generated_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_qualification(self) -> Gate3DatasetQualification:
        if self.recorded_at < self.evidence_generated_at:
            raise ValueError("qualification cannot predate its evidence")
        untouched = self.holdout_access_state == HoldoutAccessState.UNREAD
        if self.predictive_oos_eligible and not untouched:
            raise ValueError("predictive OOS eligibility requires an unread holdout")
        if (
            self.predictive_oos_eligible
            and not self.candidate_design_predated_holdout_access
        ):
            raise ValueError(
                "predictive OOS eligibility requires a pre-existing candidate design"
            )
        if (
            self.current_claim == Gate3Claim.PREDICTIVE_OOS
            and not self.predictive_oos_eligible
        ):
            raise ValueError("predictive OOS claim exceeds dataset eligibility")
        return self
