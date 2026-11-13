"""Custody-side handoff of one exact, label-free Validation feature prefix."""

from __future__ import annotations

from datetime import timedelta

from app.mie.validation.artifact import FrozenGate3Artifact, _freeze
from app.mie.validation.contracts import DatasetPartition
from app.mie.validation.prospective_development_validation_plan import INSTRUMENTS
from app.mie.validation.prospective_devval_campaign_batch_v1 import (
    _MAX_BATCH_BYTES,
    DevvalCampaignBatchV1,
)
from app.mie.validation.prospective_devval_campaign_study_v1 import (
    DevvalCampaignStudyPlanV1,
)
from app.mie.validation.prospective_devval_global_companion_schedule_v1 import (
    _MAX_SCHEDULE_BYTES,
    GlobalDevvalCompanionScheduleV1,
)
from app.mie.validation.prospective_devval_joint_companion_coverage_v1 import (
    _MAX_REPORT_BYTES,
    DevvalJointCompanionCoverageV1,
    _series,
)
from app.mie.validation.prospective_devval_joint_study_v1 import (
    _campaign_geometry,
    _join_coverage,
)
from app.mie.validation.prospective_devval_research_blueprint_v1 import (
    DevvalResearchBlueprintV1,
    _verify_study_policy,
)
from app.mie.validation.prospective_devval_validation_inputs_v1 import (
    _MAX_POLICY_BYTES,
    _MAX_PREFIX_BYTES,
    DevvalValidationInputError,
    ValidationEventIdentityV1,
    ValidationEventPrefixV1,
    _read,
)


def freeze_validation_feature_prefix_from_batch_v1(
    *,
    batch_payload: bytes,
    expected_batch_sha256: str,
    schedule_payload: bytes,
    expected_schedule_sha256: str,
    coverage_payload: bytes,
    expected_coverage_sha256: str,
    blueprint_payload: bytes,
    expected_blueprint_sha256: str,
    study_plan_payload: bytes,
    expected_study_plan_sha256: str,
    slot_ordinal: int,
) -> FrozenGate3Artifact[ValidationEventPrefixV1]:
    """Verify custody parents and emit only a pinned N190 event prefix."""
    try:
        if type(slot_ordinal) is not int or slot_ordinal < 0:
            raise DevvalValidationInputError("validation_prefix_slot_invalid")
        schedule = _read(
            schedule_payload,
            expected_schedule_sha256,
            GlobalDevvalCompanionScheduleV1,
            _MAX_SCHEDULE_BYTES,
        )
        coverage = _read(
            coverage_payload,
            expected_coverage_sha256,
            DevvalJointCompanionCoverageV1,
            _MAX_REPORT_BYTES,
        )
        batch = _read(
            batch_payload,
            expected_batch_sha256,
            DevvalCampaignBatchV1,
            _MAX_BATCH_BYTES,
        )
        blueprint = _read(
            blueprint_payload,
            expected_blueprint_sha256,
            DevvalResearchBlueprintV1,
            _MAX_POLICY_BYTES,
        )
        study = _read(
            study_plan_payload,
            expected_study_plan_sha256,
            DevvalCampaignStudyPlanV1,
            _MAX_POLICY_BYTES,
        )
        if (
            coverage.schedule_sha256 != expected_schedule_sha256
            or coverage.batch_sha256 != expected_batch_sha256
            or coverage.campaign_plan_sha256 != schedule.campaign_plan_sha256
            or coverage.research_blueprint_sha256 != expected_blueprint_sha256
            or schedule.research_blueprint_sha256 != expected_blueprint_sha256
            or batch.plan_sha256 != schedule.campaign_plan_sha256
            or batch.research_blueprint_sha256 != expected_blueprint_sha256
            or batch.source_commit != schedule.source_checkpoint.commit
            or batch.source_tree != schedule.source_checkpoint.tree
            or batch.source_manifest_sha256
            != schedule.source_checkpoint.manifest_sha256
            or len(coverage.slots) != len(schedule.slots)
            or slot_ordinal >= len(schedule.slots)
        ):
            raise DevvalValidationInputError("validation_prefix_parent_pins_changed")
        _verify_study_policy(blueprint, batch.campaign_sha256, study)
        _campaign_geometry(blueprint, schedule, batch, study)

        planned = schedule.slots[slot_ordinal]
        observed = coverage.slots[slot_ordinal]
        decision = observed.joint_decision_at
        complete = observed.status == "covered_computational"
        if (
            observed.ordinal != slot_ordinal
            or planned.partition != DatasetPartition.VALIDATION
            or observed.partition != planned.partition
            or observed.event_closed_at != planned.event_closed_at
            or observed.decision_cutoff_at != planned.decision_cutoff_at
            or tuple(side.aggregate.instrument_id for side in observed.sides)
            != INSTRUMENTS
            or any(
                side.aggregate.bar_closed_at != planned.event_closed_at
                for side in observed.sides
            )
            or complete != (decision is not None)
            or complete
            != all(side.status == "covered_computational" for side in observed.sides)
        ):
            raise DevvalValidationInputError("validation_prefix_event_changed")
        identity = ValidationEventIdentityV1(
            schedule_sha256=expected_schedule_sha256,
            coverage_sha256=expected_coverage_sha256,
            batch_sha256=expected_batch_sha256,
            slot_ordinal=slot_ordinal,
            event_closed_at=planned.event_closed_at,
            decision_at=decision,
            decision_cutoff_at=planned.decision_cutoff_at,
        )
        bitcoin, ether = (), ()
        if decision is not None:
            feature = study.feature_plan
            horizon = feature.bar_horizon
            width = timedelta(seconds=horizon.seconds)
            history = feature.history_bars
            close = planned.event_closed_at
            if decision - (close - history * width) > timedelta(
                seconds=study.feature_dependency_seconds
            ):
                raise DevvalValidationInputError("validation_prefix_causal_row_changed")
            lookup = _series(batch, schedule)
            _join_coverage(schedule, coverage, lookup, horizon.label)
            bitcoin, ether = (
                _prefix_rows(
                    lookup, symbol, horizon.label, close, width, history, decision
                )
                for symbol in INSTRUMENTS
            )

        frozen = _freeze(
            ValidationEventPrefixV1(identity=identity, bitcoin=bitcoin, ether=ether),
            contract_type=ValidationEventPrefixV1,
        )
        if len(frozen.payload) > _MAX_PREFIX_BYTES:
            raise DevvalValidationInputError("validation_prefix_size_invalid")
        return frozen
    except DevvalValidationInputError:
        raise
    except (ArithmeticError, AttributeError, KeyError, TypeError, ValueError):
        raise DevvalValidationInputError("validation_prefix_custody_refused") from None


def _prefix_rows(lookup, symbol, horizon, close, width, history, decision):
    wires = []
    for offset in range(1 - history, 1):
        bar_closed_at = close + offset * width
        row = lookup[(DatasetPartition.VALIDATION, symbol, horizon, bar_closed_at)].row
        if (
            row.instrument_id != symbol
            or row.bar.closed_at != bar_closed_at
            or row.available_at > decision
        ):
            raise DevvalValidationInputError("validation_prefix_causal_row_changed")
        wires.append(row.model_dump_json())
    return tuple(wires)
