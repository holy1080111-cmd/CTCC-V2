"""Pure V4 native-bracket calculation; saved bytes grant no runtime authority.

Stage one records a current DB sample and unchanged V3 lineage. It is not DB0046
custody, a historical availability boundary, a same-host witness or Gate 3.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.domain import native_clock
from app.domain.source_primitives import ClockStamp, canonical, sha, validate_stamps
from app.mie.validation.artifact import _verify
from app.mie.validation.contracts import Gate3Claim, Gate3Contract, Sha256, require_utc
from app.mie.validation.post_publication_availability_v3 import PostPublicationCaptureV3

MAX_BRACKET_NS = 5_000_000_000
SAMPLE_LEASE_NS = 5_000_000_000
PG_QUANTUM_NS = 1000
PG_LAST_FILETIME_TICK_NS = 900


def utc_nanoseconds(value: datetime) -> int:
    require_utc(value, "database_sample")
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 10**9 + delta.microseconds * 1000


def accepted_health(raw: bytes) -> dict:
    value = native_clock.replay_clock_observation(raw)
    if (
        value["outcome"] != "accepted"
        or value["observation"]["schema_version"] != "ctcc.windows_clock_observation.v2"
    ):
        raise ValueError("post_read_native_health_required")
    return value["observation"]


def health_fresh_at(raw: bytes, stamp: ClockStamp) -> dict:
    observed = accepted_health(raw)
    data = observed["diagnostic"]
    fields = native_clock._localized_status(
        native_clock._check_probe(data["status"], "w32tm_status.v2", "cp950")
    )
    lag = stamp.monotonic_ns - data["status"]["request_start"]["monotonic_ns"]
    if (
        lag < 0
        or Decimal(fields[native_clock._ENGLISH[-1]][:-1]) + Decimal(lag) / 10**9
        > native_clock.MAX_SYNC_AGE_SECONDS
    ):
        raise ValueError("post_read_native_health_stale")
    return observed


class NativePostReadHealthV4(Gate3Contract):
    raw_base64: str = Field(min_length=4, max_length=700_000)
    raw_sha256: Sha256

    def raw_bytes(self) -> bytes:
        try:
            raw = base64.b64decode(self.raw_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("post_read_native_health_encoding") from None
        if (
            base64.b64encode(raw).decode("ascii") != self.raw_base64
            or sha(raw) != self.raw_sha256
        ):
            raise ValueError("post_read_native_health_hash")
        accepted_health(raw)
        return raw

    @model_validator(mode="after")
    def validate_raw(self) -> NativePostReadHealthV4:
        self.raw_bytes()
        return self


class PostReadDatabaseIdentityV4(Gate3Contract):
    backend_pid: int = Field(gt=0, le=2**31 - 1)
    database_oid: int = Field(gt=0, le=2**32 - 1)
    login_oid: int = Field(gt=0, le=2**32 - 1)
    transaction_start: datetime
    postmaster_start: datetime
    transaction_xid: str | None = Field(pattern=r"^(?:0|[1-9][0-9]{0,19})$")
    server_version_num: Literal[170011]
    server_build: str = Field(
        pattern=(
            r"^PostgreSQL 17\.11 on x86_64-windows, compiled by "
            r"msvc-[0-9]{2}\.[0-9]{1,2}\.[0-9]{4,6}, 64-bit$"
        ),
        max_length=160,
    )
    transaction_read_only: Literal["on"]

    @field_validator("transaction_start", "postmaster_start")
    @classmethod
    def utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)


class PostReadDatabaseSampleV4(Gate3Contract):
    identity: PostReadDatabaseIdentityV4
    sampled_at: datetime

    @field_validator("sampled_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return require_utc(value, "sampled_at")


class PostPublicationClockEvidenceV4(Gate3Contract):
    schema_version: Literal["ctcc.mie.post_read_native_clock_sample.v4"] = (
        "ctcc.mie.post_read_native_clock_sample.v4"
    )
    source_v3: PostPublicationCaptureV3
    source_v3_sha256: Sha256
    health_before: NativePostReadHealthV4
    data_readback_completed: ClockStamp
    sample_before: ClockStamp
    database_sample: PostReadDatabaseSampleV4
    sample_after: ClockStamp
    health_after: NativePostReadHealthV4
    sample_observation_completed: ClockStamp
    quantization_model: Literal[
        "pg17.11_msvc_precise_filetime_floor_us_nonnegative.v1"
    ] = "pg17.11_msvc_precise_filetime_floor_us_nonnegative.v1"
    quantization_cell_contained_in_native_bracket: Literal[True] = True
    scope: Literal["internal_same_task_sample_handoff_without_durable_custody"] = (
        "internal_same_task_sample_handoff_without_durable_custody"
    )
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    db0046_custody_verified: Literal[False] = False
    db0035_schema_custody_verified: Literal[False] = False
    witness_schema_custody_verified: Literal[False] = False
    database_host_identified: Literal[False] = False
    database_binary_attested: Literal[False] = False
    independent_absolute_utc_verified: Literal[False] = False
    historical_availability_verified: Literal[False] = False
    evaluator_first_read_proven: Literal[False] = False
    future_decision_authority: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def replay_sample(self) -> PostPublicationClockEvidenceV4:
        if self.source_v3.canonical_sha256() != self.source_v3_sha256:
            raise ValueError("post_read_source_v3_hash")
        before = health_fresh_at(
            self.health_before.raw_bytes(), self.sample_observation_completed
        )
        after = health_fresh_at(
            self.health_after.raw_bytes(), self.sample_observation_completed
        )
        left, right = before["diagnostic"], after["diagnostic"]
        if (
            left["profile_id"] != right["profile_id"]
            or canonical(left["clock_domain"]) != canonical(right["clock_domain"])
            or native_clock._host_record(left["host_after"], left["profile_id"])
            != native_clock._host_record(right["host_before"], right["profile_id"])
        ):
            raise ValueError("post_read_native_profile_changed")
        validate_stamps(
            [
                before["sample"],
                self.data_readback_completed.model_dump(),
                self.sample_before.model_dump(),
                self.sample_after.model_dump(),
                right["host_before"]["request_start"],
                after["sample"],
                self.sample_observation_completed.model_dump(),
            ]
        )
        if (
            self.sample_after.monotonic_ns - self.sample_before.monotonic_ns
            > MAX_BRACKET_NS
            or self.sample_observation_completed.monotonic_ns
            - self.sample_after.monotonic_ns
            > SAMPLE_LEASE_NS
            or any(
                stamp.utc_ns % 100
                for stamp in (
                    self.data_readback_completed,
                    self.sample_before,
                    self.sample_after,
                    self.sample_observation_completed,
                )
            )
        ):
            raise ValueError("post_read_native_bracket_expired_or_unit_invalid")
        sampled = utc_nanoseconds(self.database_sample.sampled_at)
        if not (
            0 <= sampled <= (2**31 - 1) * 10**9 + 999_999_000
            and utc_nanoseconds(self.source_v3.available_at)
            <= self.data_readback_completed.utc_ns
            and self.sample_before.utc_ns <= sampled
            and sampled + PG_LAST_FILETIME_TICK_NS <= self.sample_after.utc_ns
        ):
            raise ValueError("post_read_database_quantization_cell_outside_bracket")
        return self


def replay_post_read_clock_v4(
    payload: bytes, *, expected_sha256: str
) -> PostPublicationClockEvidenceV4:
    """Strict calculation only. Replaying bytes cannot mint an owned sample."""
    return _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=PostPublicationClockEvidenceV4,
    )
