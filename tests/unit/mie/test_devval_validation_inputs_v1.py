"""Synthetic label-free outer Validation boundary checks."""

import json
from datetime import timedelta

import pytest

from app.domain.source_primitives import canonical, sha
from app.mie.validation import prospective_devval_validation_inputs_v1 as subject
from app.mie.validation.contracts import DatasetPartition
from app.mie.validation.frozen_legacy_raw_scorer_v1 import FrozenLegacyRawScoreV1
from app.mie.validation.prospective_devval_campaign_batch_v1 import (
    DevvalCampaignBatchV1,
)
from app.mie.validation.prospective_devval_joint_companion_coverage_v1 import _series
from app.mie.validation.prospective_devval_joint_raw_score_v1 import (
    JointDevvalRawScoreEventV1,
    JointDevvalRawScoreSideV1,
)
from app.mie.validation.prospective_devval_joint_study_v1 import (
    build_devval_joint_study_v1,
)
from tests.unit.mie.test_devval_joint_companion_coverage_v1 import (
    cohort as cohort,  # noqa: PLC0414 -- register the shared synthetic fixture.
)
from tests.unit.mie.test_devval_joint_study_v1 import (
    _fully_covered_slot,
    _with_coverage,
)
from tests.unit.mie.test_devval_joint_study_v1 import (
    study_input as study_input,  # noqa: PLC0414 -- register the shared fixture.
)


def _raw_event(identity, slot, schedule):
    complete = identity.decision_at is not None
    sides = []
    for side in slot.sides:
        raw = None
        if complete:
            raw = FrozenLegacyRawScoreV1(
                instrument_id=side.aggregate.instrument_id,
                decision_at=identity.decision_at,
                strategy="trend_pullback",
                raw_direction="long",
                status="directional",
                raw_score=37,
                config_sha256=schedule.legacy_scorer_config_sha256,
                source_checkpoint=schedule.source_checkpoint,
                source_archive_sha256=schedule.source_archive_sha256,
                market_replay_receipt_sha256=side.replay_receipt_sha256,
                market_snapshot_sha256=sha(side.aggregate.instrument_id.encode()),
                analysis_version="synthetic-v1",
            )
        sides.append(
            JointDevvalRawScoreSideV1(
                instrument_id=side.aggregate.instrument_id,
                aggregate=side.aggregate,
                coverage_status=side.status,
                status="directional" if complete else "missing",
                publication_sha256=side.publication_sha256,
                readback_sha256=side.readback_sha256,
                market_replay_receipt_sha256=side.replay_receipt_sha256,
                raw_result_sha256=raw.canonical_sha256() if raw else None,
                raw_result=raw,
            )
        )
    return JointDevvalRawScoreEventV1(
        schedule_sha256=identity.schedule_sha256,
        coverage_sha256=identity.coverage_sha256,
        batch_sha256=identity.batch_sha256,
        config_sha256=schedule.legacy_scorer_config_sha256,
        slot_ordinal=identity.slot_ordinal,
        partition=identity.partition,
        event_closed_at=identity.event_closed_at,
        decision_cutoff_at=identity.decision_cutoff_at,
        joint_decision_at=identity.decision_at,
        status="complete_computational" if complete else "incomplete",
        sides=(sides[0], sides[1]),
    )


@pytest.fixture(scope="module", params=(True, False), ids=("covered", "incomplete"))
def validation_case(study_input, cohort, request):
    source_args, _, empty = study_input
    schedule = cohort[0].contract
    ordinal = next(
        slot.ordinal
        for slot in empty.slots
        if slot.partition == DatasetPartition.VALIDATION
    )
    selected = empty.slots[ordinal]
    coverage = empty
    if request.param:
        selected = _fully_covered_slot(selected)
        coverage = empty.model_copy(
            update={
                "slots": (
                    *empty.slots[:ordinal],
                    selected,
                    *empty.slots[ordinal + 1 :],
                ),
                "complete_computational_slots": 1,
                "incomplete_slots": len(empty.slots) - 1,
            }
        )
    study_args = _with_coverage(source_args, coverage)
    identity = subject.ValidationEventIdentityV1(
        schedule_sha256=study_args["expected_schedule_sha256"],
        coverage_sha256=study_args["expected_coverage_sha256"],
        batch_sha256=study_args["expected_batch_sha256"],
        slot_ordinal=ordinal,
        event_closed_at=selected.event_closed_at,
        decision_at=selected.joint_decision_at,
        decision_cutoff_at=selected.decision_cutoff_at,
    )
    rows = ((), ())
    if request.param:
        # Reopen canonical batch bytes exactly as the N188 builder does.
        batch = DevvalCampaignBatchV1.model_validate_json(study_args["batch_payload"])
        lookup = _series(batch, schedule)
        close = identity.event_closed_at
        rows = tuple(
            tuple(
                lookup[
                    (identity.partition, symbol, "1H", close + timedelta(hours=offset))
                ].row.model_dump_json()
                for offset in range(-20, 1)
            )
            for symbol in schedule.instrument_ids
        )
    prefix = subject.ValidationEventPrefixV1(
        identity=identity, bitcoin=rows[0], ether=rows[1]
    )
    arguments = dict(study_args)
    del arguments["batch_payload"]
    arguments.update(
        prefix_payload=prefix.canonical_json_bytes(),
        expected_prefix_sha256=prefix.canonical_sha256(),
    )
    projection = subject.build_validation_feature_projection_v1(**arguments)
    raw = _raw_event(identity, selected, schedule)
    return study_args, arguments, projection, raw


def _evaluator_args(projection, raw):
    return {
        "projection_payload": projection.payload,
        "expected_projection_sha256": projection.sha256,
        "raw_event_payload": raw.canonical_json_bytes(),
        "expected_raw_event_sha256": raw.canonical_sha256(),
    }


def test_exact_n188_feature_wire_and_incomplete_disposition(validation_case):
    study_args, _, projection, raw = validation_case
    reopened, reopened_raw = subject.verify_label_free_validation_inputs_v1(
        **_evaluator_args(projection, raw)
    )
    assert reopened == projection.contract and reopened_raw == raw
    if reopened.identity.decision_at is None:
        assert reopened.status == raw.status == "incomplete"
        assert reopened.sides is None
        assert all(side.status == "missing" for side in reopened_raw.sides)
        return
    study = build_devval_joint_study_v1(**study_args).contract
    slot = study.slots[reopened.identity.slot_ordinal]
    assert reopened.status == "complete_computational"
    assert reopened.sides is not None and slot.rows is not None
    for feature, row in zip(reopened.sides, slot.rows, strict=True):
        assert feature.feature_replay_json == row.feature_replay_json
        assert feature.feature_replay_sha256 == row.feature_replay_sha256


@pytest.mark.parametrize(
    ("artifact", "field"), (("projection", "outcome"), ("raw_event", "label"))
)
def test_evaluator_refuses_repinned_label_or_outcome_fields(
    validation_case, artifact, field
):
    _, _, projection, raw = validation_case
    arguments = _evaluator_args(projection, raw)
    payload_key = f"{artifact}_payload"
    pin_key = f"expected_{artifact}_sha256"
    document = json.loads(arguments[payload_key])
    document[field] = {"positive": True}
    injected = canonical(document)
    with pytest.raises(subject.DevvalValidationInputError, match="readback_refused"):
        subject.verify_label_free_validation_inputs_v1(
            **(arguments | {payload_key: injected, pin_key: sha(injected)})
        )


def test_repinned_raw_event_cannot_replace_validation_identity(validation_case):
    _, _, projection, raw = validation_case
    repinned = raw.model_copy(update={"slot_ordinal": raw.slot_ordinal + 1})
    with pytest.raises(subject.DevvalValidationInputError, match="raw_event_changed"):
        subject.verify_label_free_validation_inputs_v1(
            **_evaluator_args(projection, repinned)
        )


def test_repinned_prefix_cannot_append_outcome_field(validation_case):
    _, arguments, _, _ = validation_case
    document = json.loads(arguments["prefix_payload"])
    document["outcome_rows"] = []
    injected = canonical(document)
    with pytest.raises(subject.DevvalValidationInputError, match="projection_refused"):
        subject.build_validation_feature_projection_v1(
            **(
                arguments
                | {"prefix_payload": injected, "expected_prefix_sha256": sha(injected)}
            )
        )
