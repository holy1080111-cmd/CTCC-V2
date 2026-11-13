"""Synthetic controls for ordered Development source replay before projection."""

from types import SimpleNamespace

import pytest

from app.domain.source_primitives import sha
from app.mie.validation import prospective_devval_development_projection_v1 as subject
from app.mie.validation.artifact import FrozenGate3Artifact
from app.mie.validation.contracts import DatasetPartition
from app.mie.validation.prospective_devval_joint_companion_coverage_v1 import (
    PublishedCompanionV1,
)
from tests.unit.mie.test_devval_joint_companion_coverage_v1 import (
    cohort as cohort,  # noqa: PLC0414 -- register the shared fixture.
)
from tests.unit.mie.test_devval_joint_study_v1 import _fully_covered_slot
from tests.unit.mie.test_devval_joint_study_v1 import (
    study_input as study_input,  # noqa: PLC0414 -- register the shared fixture.
)
from tests.unit.mie.test_devval_legacy_calibration_policy_v1 import (
    policy_input as policy_input,  # noqa: PLC0414 -- register the shared fixture.
)
from tests.unit.mie.test_devval_validation_inputs_v1 import _raw_event


@pytest.fixture
def projection_case(study_input, policy_input, cohort, tmp_path, monkeypatch):
    study_args, _, empty = study_input
    policy, pins = policy_input
    schedule = cohort[0].contract.model_copy(
        update={"legacy_scorer_config_sha256": policy.scorer_config_sha256}
    )
    parents = {
        field: study_args[f"expected_{field}"]
        for field in ("schedule_sha256", "coverage_sha256", "batch_sha256")
    }
    selected = tuple(_fully_covered_slot(slot) for slot in empty.slots[:2])
    assert all(slot.partition == DatasetPartition.DEVELOPMENT for slot in selected)
    study = SimpleNamespace(
        **parents,
        source_checkpoint=policy.source_checkpoint,
        source_archive_sha256=policy.source_archive_sha256,
        feature_plan_sha256=policy.joint_feature_plan_sha256,
        plan=SimpleNamespace(feature_plan=pins["feature_plan"]),
        status="complete_computational",
        slots=tuple(
            SimpleNamespace(coverage=slot, rows=(object(), object()))
            for slot in selected
        ),
    )
    monkeypatch.setattr(subject, "verify_devval_joint_study_v1", lambda *a, **kw: study)

    roster, events = [], []
    for slot in selected:
        identity = SimpleNamespace(
            **parents,
            slot_ordinal=slot.ordinal,
            partition=slot.partition,
            event_closed_at=slot.event_closed_at,
            decision_cutoff_at=slot.decision_cutoff_at,
            decision_at=slot.joint_decision_at,
        )
        events.append(_raw_event(identity, slot, schedule))
        roster.append(
            tuple(
                PublishedCompanionV1(
                    slot_ordinal=slot.ordinal,
                    instrument_id=side.aggregate.instrument_id,
                    journal_root=tmp_path / f"journal-{slot.ordinal}-{index}",
                    companion_root=tmp_path / f"companion-{slot.ordinal}-{index}",
                    publication_json=b"{}",
                    expected_publication_sha256=side.publication_sha256,
                    readback_json=b"{}",
                    expected_readback_sha256=side.readback_sha256,
                )
                for index, side in enumerate(slot.sides)
            )
        )
    arguments = study_args | {
        "joint_study_payload": b"synthetic verified study",
        "expected_joint_study_sha256": sha(b"synthetic verified study"),
        "policy_payload": policy.canonical_json_bytes(),
        "expected_policy_sha256": policy.canonical_sha256(),
        "scorer_config_json": pins["scorer_config_json"],
        "development_raw_event_payloads": tuple(
            event.canonical_json_bytes() for event in events
        ),
        "expected_development_raw_event_sha256s": tuple(
            event.canonical_sha256() for event in events
        ),
        "development_companions": tuple(roster),
    }
    return arguments, tuple(events)


@pytest.mark.parametrize("defect", ("missing", "symbol_order", "event_order", "pin"))
def test_invalid_roster_is_refused_before_any_source_replay(
    projection_case, monkeypatch, defect
):
    arguments, _ = projection_case
    first, second = arguments["development_companions"]
    if defect == "missing":
        update = {"development_companions": (first,)}
    elif defect == "symbol_order":
        update = {"development_companions": (first, tuple(reversed(second)))}
    elif defect == "event_order":
        update = {"development_companions": (second, first)}
    else:
        first_pin, _ = arguments["expected_development_raw_event_sha256s"]
        update = {"expected_development_raw_event_sha256s": (first_pin, sha(b"wrong"))}

    def no_reads(*args, **kwargs):
        pytest.fail("ineligible roster attempted source replay")

    monkeypatch.setattr(subject, "score_joint_devval_companion_event_v1", no_reads)
    with pytest.raises(subject.DevelopmentLegacyProjectionError):
        subject.project_development_legacy_fit_input_v1(**(arguments | update))


def test_changed_raw_result_refuses_before_next_event(projection_case, monkeypatch):
    arguments, events = projection_case
    first = events[0]
    side = first.sides[0]
    changed_result = side.raw_result.model_copy(update={"raw_score": 38})
    changed_side = side.model_copy(
        update={
            "raw_result": changed_result,
            "raw_result_sha256": changed_result.canonical_sha256(),
        }
    )
    changed = first.model_copy(update={"sides": (changed_side, first.sides[1])})
    calls = []

    def replay(*args, slot_ordinal, companions, **kwargs):
        calls.append(slot_ordinal)
        assert companions == arguments["development_companions"][0]
        return FrozenGate3Artifact(
            payload=changed.canonical_json_bytes(),
            sha256=changed.canonical_sha256(),
            contract=changed,
        )

    monkeypatch.setattr(subject, "score_joint_devval_companion_event_v1", replay)
    with pytest.raises(
        subject.DevelopmentLegacyProjectionError, match="development_raw_replay_changed"
    ):
        subject.project_development_legacy_fit_input_v1(**arguments)
    assert calls == [first.slot_ordinal]
