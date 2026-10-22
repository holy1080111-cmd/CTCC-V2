"""Replayable facts for future same-native-domain availability, never authority.

Only the separate native owner can issue the one-use consumer capability. V3
and V4 retain their original server-clock and research limitations unchanged.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import Field, StrictInt, field_validator, model_validator

from app.domain import native_clock
from app.domain.source_primitives import ClockStamp, canonical, sha, validate_stamps
from app.mie.validation.artifact import _verify
from app.mie.validation.contracts import Gate3Contract, Sha256
from app.mie.validation.post_publication_availability_v4 import (
    NativePostReadHealthV4,
    PostPublicationClockEvidenceV4,
    health_fresh_at,
)

CONSUMER_LEASE_NS = 5_000_000_000
ACQUISITION_BUDGET_SECONDS = 120
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class AvailabilityContractV5(Gate3Contract):
    @field_validator(
        "native_post_read_measured",
        "restricted_app_role_raw_retained",
        "same_native_clock_domain_only",
        "replay_grants_capability",
        "independently_administered_custody",
        "independent_absolute_utc_verified",
        "evaluator_first_read_proven",
        "historical_bar_close_availability",
        "predictive_oos_eligible",
        "qualification_authority",
        "execution_authority",
        "same_domain_future_availability_eligible",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def exact_boolean_facts(cls, value):
        if type(value) is not bool:
            raise ValueError("availability_fact_requires_native_boolean")
        return value


def ceil_native_utc(value: int) -> datetime:
    """Do not backdate a 100ns native upper bound through microsecond flooring."""
    if type(value) is not int or value < 0 or value % 100:
        raise ValueError("availability_native_unit_invalid")
    return _EPOCH + timedelta(microseconds=(value + 999) // 1000)


def same_health_domain(first: NativePostReadHealthV4, last: NativePostReadHealthV4):
    left = native_clock.validate_os_clock(json.loads(first.raw_bytes())["observation"])[
        "diagnostic"
    ]
    right = native_clock.validate_os_clock(json.loads(last.raw_bytes())["observation"])[
        "diagnostic"
    ]
    if (
        left["profile_id"] != right["profile_id"]
        or canonical(left["clock_domain"]) != canonical(right["clock_domain"])
        or native_clock._host_record(left["host_after"], left["profile_id"])
        != native_clock._host_record(right["host_before"], right["profile_id"])
    ):
        raise ValueError("availability_native_domain_changed")


class DurableReadbackV5(AvailabilityContractV5):
    bundle_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    bundle_sha256: Sha256
    proof_sha256: Sha256
    append_backend_pid: StrictInt = Field(gt=0)
    append_transaction_id: str = Field(pattern=r"^[0-9]+$")
    readback_backend_pid: StrictInt = Field(gt=0)
    readback_transaction_id: str = Field(pattern=r"^[0-9]+$")

    @model_validator(mode="after")
    def different_transaction(self):
        if self.append_transaction_id == self.readback_transaction_id:
            raise ValueError("availability_readback_transaction_not_new")
        return self


class NativeAvailabilityEvidenceV5(AvailabilityContractV5):
    schema_version: Literal["ctcc.mie.native_available_after_read.v5"] = (
        "ctcc.mie.native_available_after_read.v5"
    )
    ordinal_claim_sha256: Sha256
    source_clock_v4: PostPublicationClockEvidenceV4
    deployment_sha256: Sha256
    custody: DurableReadbackV5
    final_health: NativePostReadHealthV4
    final_ready: ClockStamp
    availability_basis: Literal["owned_native_post_read_and_durable_readback.v5"] = (
        "owned_native_post_read_and_durable_readback.v5"
    )
    native_post_read_measured: Literal[True] = True
    restricted_app_role_raw_retained: Literal[True] = True
    same_native_clock_domain_only: Literal[True] = True
    replay_grants_capability: Literal[False] = False
    independently_administered_custody: Literal[False] = False
    independent_absolute_utc_verified: Literal[False] = False
    evaluator_first_read_proven: Literal[False] = False
    historical_bar_close_availability: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    qualification_authority: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def final_boundary(self):
        proof = sha(
            canonical(
                {
                    "schema_version": "ctcc.mie.native_availability_proof.v5",
                    "ordinal_claim_sha256": self.ordinal_claim_sha256,
                    "clock_v4_sha256": self.source_clock_v4.canonical_sha256(),
                    "deployment_sha256": self.deployment_sha256,
                }
            )
        )
        if self.custody.proof_sha256 != proof:
            raise ValueError("availability_durable_proof_binding_invalid")
        first = self.source_clock_v4.sample_observation_completed
        validate_stamps([first.model_dump(), self.final_ready.model_dump()])
        health = health_fresh_at(self.final_health.raw_bytes(), self.final_ready)
        validate_stamps(
            [
                first.model_dump(),
                health["diagnostic"]["host_before"]["request_start"],
                health["sample"],
                self.final_ready.model_dump(),
            ]
        )
        same_health_domain(self.source_clock_v4.health_after, self.final_health)
        if (
            self.final_ready.utc_ns % 100
            or self.final_ready.monotonic_ns - first.monotonic_ns
            > ACQUISITION_BUDGET_SECONDS * 1_000_000_000
        ):
            raise ValueError("availability_final_boundary_invalid")
        ceil_native_utc(self.final_ready.utc_ns)
        return self


class FutureAvailabilityDecisionV5(AvailabilityContractV5):
    schema_version: Literal["ctcc.mie.future_availability_decision.v5"] = (
        "ctcc.mie.future_availability_decision.v5"
    )
    evidence_sha256: Sha256
    bundle_sha256: Sha256
    ordinal_claim_sha256: Sha256
    cutoff: ClockStamp
    source_rows_sha256: Sha256
    row_count: StrictInt = Field(ge=1, le=3000)
    same_domain_future_availability_eligible: Literal[True] = True
    predictive_oos_eligible: Literal[False] = False
    qualification_authority: Literal[False] = False
    execution_authority: Literal[False] = False


def replay_native_availability_v5(payload: bytes, *, expected_sha256: str):
    """Exact bytes only: this function cannot create any native capability."""
    return _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=NativeAvailabilityEvidenceV5,
    )
