"""Offline joint-study checks over the existing synthetic measured grid."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain.source_primitives import sha, utc_from_ns
from app.mie.validation import prospective_devval_joint_study_v1 as subject
from app.mie.validation.contracts import DatasetPartition
from app.mie.validation.prospective_devval_campaign_study_v1 import (
    DevvalCampaignStudyPlanV1,
)
from tests.unit.mie.test_devval_campaign_batch import _ns
from tests.unit.mie.test_devval_global_companion_schedule_v1 import _inputs
from tests.unit.mie.test_devval_joint_companion_coverage_v1 import _audit
from tests.unit.mie.test_devval_joint_companion_coverage_v1 import (
    cohort as cohort,  # noqa: PLC0414 -- register the synthetic fixture.
)


def _with_coverage(arguments, coverage):
    payload = coverage.canonical_json_bytes()
    return arguments | {
        "coverage_payload": payload,
        "expected_coverage_sha256": sha(payload),
    }


def _replace_first_slot(coverage, replacement):
    return coverage.model_copy(update={"slots": (replacement, *coverage.slots[1:])})


@pytest.fixture(scope="module")
def study_input(cohort):
    schedule, batch = cohort
    blueprint = _inputs()["blueprint"]
    plan = DevvalCampaignStudyPlanV1(
        **blueprint.study.model_dump(mode="python"),
        campaign_sha256=batch.contract.campaign_sha256,
    )
    empty_coverage = _audit(cohort).contract
    first = empty_coverage.slots[0]
    close = first.event_closed_at
    readback_times = (
        close + timedelta(seconds=2),
        utc_from_ns(_ns(close + timedelta(seconds=4)) + 1),
    )
    sides = tuple(
        side.model_copy(
            update={
                "status": "covered_computational",
                "publication_sha256": sha(f"synthetic-publication:{index}".encode()),
                "readback_sha256": sha(f"synthetic-readback:{index}".encode()),
                "replay_receipt_sha256": sha(f"synthetic-replay:{index}".encode()),
                "postpublication_readback_at": readback_at,
            }
        )
        for index, (side, readback_at) in enumerate(
            zip(first.sides, readback_times, strict=True)
        )
    )
    covered_first = first.model_copy(
        update={
            "status": "covered_computational",
            "joint_decision_at": readback_times[1],
            "sides": sides,
        }
    )
    coverage = _replace_first_slot(empty_coverage, covered_first).model_copy(
        update={
            "complete_computational_slots": 1,
            "incomplete_slots": len(empty_coverage.slots) - 1,
        }
    )
    arguments = _with_coverage(
        {
            "schedule_payload": schedule.payload,
            "expected_schedule_sha256": schedule.sha256,
            "batch_payload": batch.payload,
            "expected_batch_sha256": batch.sha256,
            "blueprint_payload": blueprint.canonical_json_bytes(),
            "expected_blueprint_sha256": blueprint.canonical_sha256(),
            "study_plan_payload": plan.canonical_json_bytes(),
            "expected_study_plan_sha256": plan.canonical_sha256(),
        },
        coverage,
    )
    return arguments, coverage, empty_coverage


def test_pair_binds_later_readback_and_preserves_all_uncovered_slots(
    study_input, monkeypatch
):
    arguments, coverage, _ = study_input

    def no_partial_folds(*args, **kwargs):
        pytest.fail("incomplete cohort attempted grouped folds")

    monkeypatch.setattr(subject, "_folds", no_partial_folds)
    frozen = subject.build_devval_joint_study_v1(**arguments)
    study = frozen.contract
    assert frozen.sha256 == sha(frozen.payload)
    assert study.canonical_json_bytes() == frozen.payload
    assert subject.DevvalJointStudyV1.model_validate_json(frozen.payload) == study
    assert (
        subject.verify_devval_joint_study_v1(
            frozen.payload, expected_joint_study_sha256=frozen.sha256, **arguments
        )
        == study
    )
    assert study.status == "incomplete_cohort"
    assert study.development_folds == ()
    assert study.development_event_times_sha256 is None
    assert tuple(slot.coverage for slot in study.slots) == coverage.slots
    assert all(
        slot.status == "coverage_incomplete" and slot.rows is None
        for slot in study.slots[1:]
    )

    first = study.slots[0]
    assert first.status == "replayed_computational"
    assert first.rows is not None
    decision = first.coverage.event_closed_at + timedelta(seconds=4, microseconds=1)
    assert first.coverage.joint_decision_at == decision
    assert tuple(row.feature.instrument_id for row in first.rows) == (
        "BTC-USDT-SWAP",
        "ETH-USDT-SWAP",
    )
    for row, side in zip(first.rows, first.coverage.sides, strict=True):
        assert row.feature.as_of == row.label.decision_at == decision
        assert row.feature.data_cutoff == row.label.base_bar_closed_at
        assert row.feature_replay_json == row.feature.model_dump_json()
        assert row.feature.replay_sha256 == row.label.feature_replay_sha256
        assert row.label.base_row_sha256 == side.aggregate.source_row_sha256
        assert row.label.base_available_at == side.aggregate.available_at < decision
        assert row.label.outcome_at == row.feature.data_cutoff + timedelta(hours=1)
        assert row.label.outcome_available_at <= row.label.read_at
        assert row.label.read_at > decision
    assert study.independent_availability_verified is False
    assert study.evaluator_first_access_verified is False
    assert study.source_authenticity_verified is False
    assert study.predictive_oos_eligible is study.execution_authority is False


def _fully_covered_slot(slot):
    readback = slot.event_closed_at + timedelta(seconds=4)
    sides = tuple(
        side.model_copy(
            update={
                "status": "covered_computational",
                "publication_sha256": sha(
                    f"publication:{slot.ordinal}:{index}".encode()
                ),
                "readback_sha256": sha(f"readback:{slot.ordinal}:{index}".encode()),
                "replay_receipt_sha256": sha(f"replay:{slot.ordinal}:{index}".encode()),
                "postpublication_readback_at": readback,
            }
        )
        for index, side in enumerate(slot.sides)
    )
    return slot.model_copy(
        update={
            "status": "covered_computational",
            "joint_decision_at": readback,
            "sides": sides,
        }
    )


def test_complete_cohort_retains_grouped_purged_development_folds(study_input):
    arguments, _, empty = study_input
    coverage = empty.model_copy(
        update={
            "slots": tuple(_fully_covered_slot(slot) for slot in empty.slots),
            "complete_computational_slots": len(empty.slots),
            "incomplete_slots": 0,
        }
    )
    arguments = _with_coverage(arguments, coverage)
    frozen = subject.build_devval_joint_study_v1(**arguments)
    study = subject.verify_devval_joint_study_v1(
        frozen.payload, expected_joint_study_sha256=frozen.sha256, **arguments
    )
    assert study.status == "complete_computational"
    assert study.development_event_times_sha256 is not None
    assert len(study.development_folds) == 2
    assert all(slot.status == "replayed_computational" for slot in study.slots)
    development = tuple(
        row
        for slot in study.slots
        if slot.coverage.partition == DatasetPartition.DEVELOPMENT
        for row in slot.rows
    )
    validation = tuple(
        row
        for slot in study.slots
        if slot.coverage.partition == DatasetPartition.VALIDATION
        for row in slot.rows
    )
    assert max(row.label.read_at for row in development) < min(
        row.feature_history_start_at for row in validation
    )
    evaluated = set()
    for fold in study.development_folds:
        for indices in (fold.training_indices, fold.validation_indices):
            assert all(
                left % 2 == 0 and right == left + 1
                for left, right in zip(indices[::2], indices[1::2], strict=True)
            )
        assert evaluated.isdisjoint(fold.validation_indices)
        evaluated.update(fold.validation_indices)
        assert max(
            development[index].label.read_at for index in fold.training_indices
        ) < min(
            development[index].feature_history_start_at
            for index in fold.validation_indices
        )
    assert study.evaluator_first_access_verified is False
    assert study.execution_authority is False


def test_scaled_decimal_keeps_native_replay_identity_in_outer_wire(study_input):
    arguments, _, _ = study_input
    study = subject.build_devval_joint_study_v1(**arguments).contract
    first = study.slots[0]
    assert first.rows is not None
    original = first.rows[0]
    snapshot = original.feature.feature_snapshot
    # The same numeric alpha with a distinct exact Pydantic JSON spelling.
    alpha = Decimal("0.2500")
    assert alpha == snapshot.signal.alpha
    scaled = original.feature.model_copy(
        update={
            "feature_snapshot": snapshot.model_copy(
                update={"signal": snapshot.signal.model_copy(update={"alpha": alpha})}
            )
        }
    )
    native = scaled.model_dump_json()
    assert '"alpha":"0.2500"' in native
    assert scaled.replay_sha256 != original.feature_replay_sha256
    revised = subject.DevvalJointStudyRowV1(
        feature_history_start_at=original.feature_history_start_at,
        feature_replay_sha256=scaled.replay_sha256,
        feature_replay_json=native,
        label=original.label.model_copy(
            update={"feature_replay_sha256": scaled.replay_sha256}
        ),
    )
    slot = first.model_copy(update={"rows": (revised, first.rows[1])})
    scaled_study = study.model_copy(update={"slots": (slot, *study.slots[1:])})
    wire = scaled_study.canonical_json_bytes()
    reopened = subject.DevvalJointStudyV1.model_validate_json(wire)
    assert reopened.canonical_json_bytes() == wire
    assert reopened.slots[0].rows is not None
    row = reopened.slots[0].rows[0]
    assert row.feature_replay_json == native
    assert row.feature.replay_sha256 == row.label.feature_replay_sha256
    assert row.feature.feature_snapshot.signal.alpha.as_tuple().exponent == -4


def test_repin_output_does_not_override_rebuilt_parent_lineage(study_input):
    arguments, _, _ = study_input
    study = subject.build_devval_joint_study_v1(**arguments).contract
    modified = study.model_copy(update={"coverage_sha256": sha(b"other coverage")})
    payload = modified.canonical_json_bytes()
    with pytest.raises(subject.DevvalJointStudyError, match="output_readback_changed"):
        subject.verify_devval_joint_study_v1(
            payload, expected_joint_study_sha256=sha(payload), **arguments
        )


def test_incomplete_output_cannot_claim_complete_fold_status(study_input):
    arguments, _, _ = study_input
    study = subject.build_devval_joint_study_v1(**arguments).contract
    modified = study.model_copy(update={"status": "complete_computational"})
    payload = modified.canonical_json_bytes()
    with pytest.raises(subject.DevvalJointStudyError, match="output_readback_refused"):
        subject.verify_devval_joint_study_v1(
            payload, expected_joint_study_sha256=sha(payload), **arguments
        )


@pytest.mark.parametrize(
    "parent", ("schedule", "coverage", "batch", "blueprint", "study_plan")
)
def test_each_expected_parent_digest_is_checked_before_geometry(
    study_input, monkeypatch, parent
):
    arguments, _, _ = study_input

    def no_geometry(*args, **kwargs):
        pytest.fail("bad parent pin reached geometry derivation")

    monkeypatch.setattr(subject, "_campaign_geometry", no_geometry)
    pin_key = f"expected_{parent}_sha256"
    foreign_pin = sha(f"foreign:{parent}".encode())
    assert foreign_pin != arguments[pin_key]
    with pytest.raises(
        subject.DevvalJointStudyError, match="joint_study_replay_refused"
    ):
        subject.build_devval_joint_study_v1(**(arguments | {pin_key: foreign_pin}))


def test_repin_cannot_override_parent_lineage_or_canonical_wire(study_input):
    arguments, coverage, _ = study_input
    foreign = coverage.model_copy(update={"batch_sha256": sha(b"unrelated batch")})
    with pytest.raises(subject.DevvalJointStudyError, match="parent_pins_changed"):
        subject.build_devval_joint_study_v1(**_with_coverage(arguments, foreign))

    noncanonical = arguments["coverage_payload"] + b"\n"
    with pytest.raises(
        subject.DevvalJointStudyError, match="joint_study_replay_refused"
    ):
        subject.build_devval_joint_study_v1(
            **(
                arguments
                | {
                    "coverage_payload": noncanonical,
                    "expected_coverage_sha256": sha(noncanonical),
                }
            )
        )


@pytest.mark.parametrize(
    ("defect", "refusal"),
    (
        ("clock", "joint_study_decision_changed"),
        ("swapped", "joint_study_aggregate_binding_changed"),
        ("missing_with_decision", "joint_study_incomplete_decision_claimed"),
    ),
)
def test_repin_cannot_replace_joint_clock_or_pair(study_input, defect, refusal):
    arguments, coverage, empty_coverage = study_input
    first = coverage.slots[0]
    if defect == "clock":
        # Retain the covered pair and replace only its common timestamp with
        # the earlier aggregate-only decision used by Study V1.
        bad = first.model_copy(
            update={
                "joint_decision_at": max(
                    side.aggregate.available_at for side in first.sides
                )
            }
        )
    elif defect == "swapped":
        bad = first.model_copy(update={"sides": tuple(reversed(first.sides))})
    else:
        coverage = empty_coverage
        bad = coverage.slots[0].model_copy(
            update={"joint_decision_at": first.joint_decision_at}
        )
    with pytest.raises(subject.DevvalJointStudyError, match=refusal):
        subject.build_devval_joint_study_v1(
            **_with_coverage(arguments, _replace_first_slot(coverage, bad))
        )
