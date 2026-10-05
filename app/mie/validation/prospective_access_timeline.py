"""Computational audit of blind acquisition and declared evaluator access.

This schema binds a ``bind_blind_window_minute`` record by an external hash; it
does not itself authenticate the capture's origin or prove that the declared
evaluator read was the *first* read. No controlled access journal exists yet.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import field_validator, model_validator

from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    _freeze,
    _verify,
)
from app.mie.validation.blind_window_capture import BlindWindowMinuteCapture
from app.mie.validation.contracts import Gate3Claim, Gate3Contract, Sha256, require_utc

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class DeclaredEvaluatorFirstRead(Gate3Contract):
    """A retained access declaration, explicitly without first-read proof."""

    schema_version: Literal["ctcc.mie.gate3.declared_evaluator_first_read.v1"] = (
        "ctcc.mie.gate3.declared_evaluator_first_read.v1"
    )
    preregistration_sha256: Sha256
    blind_capture_sha256: Sha256
    declared_first_read_at: datetime
    declaration_recorded_at: datetime
    access_basis: Literal["caller_declared_unverified"] = "caller_declared_unverified"
    first_read_independently_verified: Literal[False] = False

    @field_validator("declared_first_read_at", "declaration_recorded_at")
    @classmethod
    def validate_utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_declaration(self) -> DeclaredEvaluatorFirstRead:
        if self.declaration_recorded_at < self.declared_first_read_at:
            raise ValueError("access declaration predates its declared read")
        return self


class BlindWindowAccessTimeline(Gate3Contract):
    """One captured minute followed by a distinct, unverified evaluator read."""

    schema_version: Literal["ctcc.mie.gate3.blind_window_access_timeline.v1"] = (
        "ctcc.mie.gate3.blind_window_access_timeline.v1"
    )
    preregistration_sha256: Sha256
    blind_capture: BlindWindowMinuteCapture
    blind_capture_sha256: Sha256
    evaluator_read: DeclaredEvaluatorFirstRead
    evaluator_read_sha256: Sha256
    recorded_at: datetime
    acquisition_mode: Literal["preplanned_window_minute_automated_capture"] = (
        "preplanned_window_minute_automated_capture"
    )
    evaluator_first_read_proven: Literal[False] = False
    complete_dataset_proven: Literal[False] = False
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("recorded_at")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        return require_utc(value, "recorded_at")

    @model_validator(mode="after")
    def validate_timeline(self) -> BlindWindowAccessTimeline:
        capture = self.blind_capture
        declaration = self.evaluator_read
        if (
            type(capture) is not BlindWindowMinuteCapture
            or type(declaration) is not DeclaredEvaluatorFirstRead
        ):
            raise ValueError("access timeline requires exact evidence schemas")
        if (
            self.preregistration_sha256 != capture.preregistration_sha256
            or self.blind_capture_sha256 != capture.canonical_sha256()
            or declaration.preregistration_sha256 != self.preregistration_sha256
            or declaration.blind_capture_sha256 != self.blind_capture_sha256
            or self.evaluator_read_sha256 != declaration.canonical_sha256()
        ):
            raise ValueError("access timeline evidence hash mismatch")
        if not (
            capture.observed_at
            <= capture.retained_at
            <= declaration.declared_first_read_at
            and declaration.declared_first_read_at
            >= capture.first_permitted_evaluator_access_at
            and self.recorded_at >= declaration.declaration_recorded_at
        ):
            raise ValueError("access timeline violates acquisition/read chronology")
        return self


def _check_external_pins(
    timeline: BlindWindowAccessTimeline,
    *,
    expected_preregistration_sha256: str,
    expected_blind_capture_sha256: str,
    expected_evaluator_read_sha256: str,
) -> None:
    for label, expected, actual in (
        (
            "preregistration",
            expected_preregistration_sha256,
            timeline.preregistration_sha256,
        ),
        (
            "blind capture",
            expected_blind_capture_sha256,
            timeline.blind_capture_sha256,
        ),
        (
            "evaluator declaration",
            expected_evaluator_read_sha256,
            timeline.evaluator_read_sha256,
        ),
    ):
        if (
            type(expected) is not str
            or not _SHA256.fullmatch(expected)
            or expected != actual
        ):
            raise ArtifactVerificationError(f"external {label} pin mismatch")


def freeze_blind_window_access_timeline(
    timeline: BlindWindowAccessTimeline,
    *,
    expected_preregistration_sha256: str,
    expected_blind_capture_sha256: str,
    expected_evaluator_read_sha256: str,
) -> FrozenGate3Artifact[BlindWindowAccessTimeline]:
    """Freeze the distinction, without authenticating a first-read claim."""

    if type(timeline) is not BlindWindowAccessTimeline:
        raise ArtifactVerificationError("access timeline exact schema required")
    frozen = _freeze(timeline, contract_type=BlindWindowAccessTimeline)
    _check_external_pins(
        frozen.contract,
        expected_preregistration_sha256=expected_preregistration_sha256,
        expected_blind_capture_sha256=expected_blind_capture_sha256,
        expected_evaluator_read_sha256=expected_evaluator_read_sha256,
    )
    return frozen


def verify_blind_window_access_timeline(
    payload: bytes,
    *,
    expected_sha256: str,
    expected_preregistration_sha256: str,
    expected_blind_capture_sha256: str,
    expected_evaluator_read_sha256: str,
) -> BlindWindowAccessTimeline:
    """Verify exact bytes and independently supplied earlier-stage hash pins."""

    timeline = _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=BlindWindowAccessTimeline,
    )
    _check_external_pins(
        timeline,
        expected_preregistration_sha256=expected_preregistration_sha256,
        expected_blind_capture_sha256=expected_blind_capture_sha256,
        expected_evaluator_read_sha256=expected_evaluator_read_sha256,
    )
    return timeline
