"""Synthetic custody handoff tests over the existing Validation grid."""

import json
from datetime import datetime, timedelta

import pytest

from app.domain.source_primitives import canonical, sha
from app.mie.validation import (
    prospective_devval_validation_prefix_custody_v1 as subject,
)
from app.mie.validation.prospective_devval_validation_inputs_v1 import (
    DevvalValidationInputError,
    build_validation_feature_projection_v1,
)
from tests.unit.mie.test_devval_joint_companion_coverage_v1 import (
    cohort as cohort,  # noqa: PLC0414 -- register the shared synthetic fixture.
)
from tests.unit.mie.test_devval_joint_study_v1 import (
    study_input as study_input,  # noqa: PLC0414 -- register the shared fixture.
)
from tests.unit.mie.test_devval_validation_inputs_v1 import (
    validation_case as validation_case,  # noqa: PLC0414 -- register the shared fixture.
)


def _inputs(validation_case):
    study_args, projection_args, projection, _ = validation_case
    identity = projection.contract.identity
    custody_args = study_args | {"slot_ordinal": identity.slot_ordinal}
    return custody_args, projection_args, projection


def _no_row_index(*args, **kwargs):
    pytest.fail("incomplete custody slot accessed source rows")


def test_exact_custody_prefix_wire_and_projection(validation_case, monkeypatch):
    custody_args, projection_args, expected = _inputs(validation_case)
    incomplete = expected.contract.identity.decision_at is None
    if incomplete:
        monkeypatch.setattr(subject, "_series", _no_row_index)
        monkeypatch.setattr(subject, "_join_coverage", _no_row_index)
        monkeypatch.setattr(subject, "_prefix_rows", _no_row_index)

    frozen = subject.freeze_validation_feature_prefix_from_batch_v1(**custody_args)
    assert frozen.payload == projection_args["prefix_payload"]
    assert frozen.sha256 == projection_args["expected_prefix_sha256"]
    if incomplete:
        assert frozen.contract.bitcoin == frozen.contract.ether == ()
        assert frozen.contract.identity.decision_at is None
    else:
        assert len(frozen.contract.bitcoin) == len(frozen.contract.ether) == 21
        for prefix in (frozen.contract.bitcoin, frozen.contract.ether):
            closes = tuple(
                datetime.fromisoformat(json.loads(row)["bar"]["closed_at"])
                for row in prefix
            )
            close = frozen.contract.identity.event_closed_at
            assert closes == tuple(close + timedelta(hours=i) for i in range(-20, 1))
        replay = build_validation_feature_projection_v1(
            **(
                projection_args
                | {
                    "prefix_payload": frozen.payload,
                    "expected_prefix_sha256": frozen.sha256,
                }
            )
        )
        assert replay.payload == expected.payload


def test_batch_tamper_refuses_even_when_slot_is_incomplete(validation_case):
    custody_args, _, _ = _inputs(validation_case)
    changed = custody_args["batch_payload"] + b"\n"
    with pytest.raises(
        DevvalValidationInputError, match="validation_prefix_custody_refused"
    ):
        subject.freeze_validation_feature_prefix_from_batch_v1(
            **(custody_args | {"batch_payload": changed})
        )


def test_repinned_coverage_cannot_change_scheduled_cutoff(validation_case):
    custody_args, _, projection = _inputs(validation_case)
    document = json.loads(custody_args["coverage_payload"])
    identity = projection.contract.identity
    changed = identity.decision_cutoff_at + timedelta(seconds=1)
    document["slots"][identity.slot_ordinal]["decision_cutoff_at"] = changed.isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    payload = canonical(document)
    with pytest.raises(
        DevvalValidationInputError, match="validation_prefix_event_changed"
    ):
        subject.freeze_validation_feature_prefix_from_batch_v1(
            **(
                custody_args
                | {
                    "coverage_payload": payload,
                    "expected_coverage_sha256": sha(payload),
                }
            )
        )
