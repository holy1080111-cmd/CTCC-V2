"""Synthetic-only chronology tests; no real evaluator access is authenticated."""

import hashlib
from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.blind_window_capture import bind_blind_window_minute
from app.mie.validation.prospective_access_timeline import (
    BlindWindowAccessTimeline,
    DeclaredEvaluatorFirstRead,
    freeze_blind_window_access_timeline,
    verify_blind_window_access_timeline,
)
from tests.unit.mie.test_gate3_blind_window_capture import fixture_inputs


def fixture_timeline(monkeypatch):
    capture = bind_blind_window_minute(**fixture_inputs(monkeypatch))
    first_read_at = capture.first_permitted_evaluator_access_at + timedelta(seconds=1)
    declaration = DeclaredEvaluatorFirstRead(
        preregistration_sha256=capture.preregistration_sha256,
        blind_capture_sha256=capture.canonical_sha256(),
        declared_first_read_at=first_read_at,
        declaration_recorded_at=first_read_at + timedelta(seconds=1),
    )
    timeline = BlindWindowAccessTimeline(
        preregistration_sha256=capture.preregistration_sha256,
        blind_capture=capture,
        blind_capture_sha256=capture.canonical_sha256(),
        evaluator_read=declaration,
        evaluator_read_sha256=declaration.canonical_sha256(),
        recorded_at=declaration.declaration_recorded_at + timedelta(seconds=1),
    )
    pins = {
        "expected_preregistration_sha256": capture.preregistration_sha256,
        "expected_blind_capture_sha256": capture.canonical_sha256(),
        "expected_evaluator_read_sha256": declaration.canonical_sha256(),
    }
    return timeline, pins


def test_automated_capture_and_declared_evaluator_read_have_distinct_times(monkeypatch):
    timeline, pins = fixture_timeline(monkeypatch)
    frozen = freeze_blind_window_access_timeline(timeline, **pins)

    assert timeline.blind_capture.observed_at < timeline.blind_capture.retained_at
    assert (
        timeline.blind_capture.retained_at
        < timeline.evaluator_read.declared_first_read_at
    )
    assert (
        timeline.evaluator_read.declared_first_read_at
        >= timeline.blind_capture.first_permitted_evaluator_access_at
    )
    assert timeline.evaluator_read.first_read_independently_verified is False
    assert timeline.evaluator_first_read_proven is False
    assert timeline.complete_dataset_proven is False
    assert timeline.predictive_oos_eligible is False
    assert timeline.promotion_eligible is False
    assert timeline.execution_authority is False
    assert timeline.runtime_consumers == 0
    assert timeline.current_claim == "computational"
    assert (
        verify_blind_window_access_timeline(
            frozen.payload, expected_sha256=frozen.sha256, **pins
        )
        == timeline
    )


def test_evaluator_read_before_lag_or_capture_retention_is_rejected(monkeypatch):
    timeline, _ = fixture_timeline(monkeypatch)
    early = timeline.evaluator_read.model_dump(mode="python")
    early["declared_first_read_at"] = (
        timeline.blind_capture.first_permitted_evaluator_access_at
        - timedelta(microseconds=1)
    )
    early["declaration_recorded_at"] = (
        timeline.blind_capture.first_permitted_evaluator_access_at
    )
    declaration = DeclaredEvaluatorFirstRead.model_validate(early)
    with pytest.raises(ValidationError, match="acquisition/read chronology"):
        BlindWindowAccessTimeline.model_validate(
            {
                **timeline.model_dump(mode="python"),
                "evaluator_read": declaration,
                "evaluator_read_sha256": declaration.canonical_sha256(),
            }
        )

    delayed_capture = type(timeline.blind_capture).model_validate(
        {
            **timeline.blind_capture.model_dump(mode="python"),
            "retained_at": timeline.evaluator_read.declared_first_read_at
            + timedelta(seconds=1),
        }
    )
    delayed_read = DeclaredEvaluatorFirstRead.model_validate(
        {
            **timeline.evaluator_read.model_dump(mode="python"),
            "blind_capture_sha256": delayed_capture.canonical_sha256(),
        }
    )
    with pytest.raises(ValidationError, match="acquisition/read chronology"):
        BlindWindowAccessTimeline.model_validate(
            {
                **timeline.model_dump(mode="python"),
                "blind_capture": delayed_capture,
                "blind_capture_sha256": delayed_capture.canonical_sha256(),
                "evaluator_read": delayed_read,
                "evaluator_read_sha256": delayed_read.canonical_sha256(),
            }
        )


def test_access_declaration_cannot_be_recorded_before_its_claimed_read(monkeypatch):
    timeline, _ = fixture_timeline(monkeypatch)
    with pytest.raises(ValidationError, match="predates its declared read"):
        DeclaredEvaluatorFirstRead.model_validate(
            {
                **timeline.evaluator_read.model_dump(mode="python"),
                "declaration_recorded_at": (
                    timeline.evaluator_read.declared_first_read_at
                    - timedelta(microseconds=1)
                ),
            }
        )


def test_rehashed_access_replacement_cannot_pass_original_external_pin(monkeypatch):
    timeline, pins = fixture_timeline(monkeypatch)
    replacement = DeclaredEvaluatorFirstRead.model_validate(
        {
            **timeline.evaluator_read.model_dump(mode="python"),
            "declared_first_read_at": (
                timeline.evaluator_read.declared_first_read_at + timedelta(seconds=1)
            ),
        }
    )
    changed = BlindWindowAccessTimeline.model_validate(
        {
            **timeline.model_dump(mode="python"),
            "evaluator_read": replacement,
            "evaluator_read_sha256": replacement.canonical_sha256(),
        }
    )
    with pytest.raises(ArtifactVerificationError, match="evaluator declaration pin"):
        freeze_blind_window_access_timeline(changed, **pins)
    with pytest.raises(ArtifactVerificationError, match="evaluator declaration pin"):
        verify_blind_window_access_timeline(
            changed.canonical_json_bytes(),
            expected_sha256=changed.canonical_sha256(),
            **pins,
        )


@pytest.mark.parametrize(
    "pin_name",
    [
        "expected_preregistration_sha256",
        "expected_blind_capture_sha256",
        "expected_evaluator_read_sha256",
    ],
)
def test_independently_supplied_pin_mismatch_fails(monkeypatch, pin_name):
    timeline, pins = fixture_timeline(monkeypatch)
    pins[pin_name] = "0" * 64
    with pytest.raises(ArtifactVerificationError, match="pin mismatch"):
        freeze_blind_window_access_timeline(timeline, **pins)


@pytest.mark.parametrize(
    "field,value",
    [
        ("predictive_oos_eligible", True),
        ("evaluator_first_read_proven", True),
        ("complete_dataset_proven", True),
        ("execution_authority", True),
        ("runtime_consumers", 1),
    ],
)
def test_claim_or_authority_escalation_is_rejected(monkeypatch, field, value):
    timeline, _ = fixture_timeline(monkeypatch)
    with pytest.raises(ValidationError):
        BlindWindowAccessTimeline.model_validate(
            {**timeline.model_dump(mode="python"), field: value}
        )


def test_hash_and_canonical_readback_fail_closed(monkeypatch):
    timeline, pins = fixture_timeline(monkeypatch)
    frozen = freeze_blind_window_access_timeline(timeline, **pins)
    with pytest.raises(ArtifactVerificationError, match="SHA256 mismatch"):
        verify_blind_window_access_timeline(
            frozen.payload, expected_sha256="0" * 64, **pins
        )
    with pytest.raises(ArtifactVerificationError, match="not canonical"):
        verify_blind_window_access_timeline(
            frozen.payload + b" ",
            expected_sha256=hashlib.sha256(frozen.payload + b" ").hexdigest(),
            **pins,
        )
