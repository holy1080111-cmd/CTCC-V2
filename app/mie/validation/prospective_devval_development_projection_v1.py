"""Outer-only projection of verified N188 Development rows into an isolated wire.

This module reads the full joint study to reproduce it, but serializes no
Validation row, feature, label, raw score, or outcome into the fit input.
"""

from __future__ import annotations

from app.mie.validation.artifact import FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.contracts import DatasetPartition
from app.mie.validation.prospective_devval_development_fit_v1 import (
    DevelopmentLegacyFitInputV1,
    DevelopmentLegacySideV1,
    DevelopmentLegacySlotV1,
)
from app.mie.validation.prospective_devval_joint_raw_score_v1 import (
    _MAX_EVENT_BYTES,
    JointDevvalRawScoreEventV1,
)
from app.mie.validation.prospective_devval_joint_study_v1 import (
    verify_devval_joint_study_v1,
)
from app.mie.validation.prospective_devval_legacy_calibration_policy_v1 import (
    LegacyCalibrationPolicyV1,
)

_MAX_POLICY_BYTES = 8 * 1024


class DevelopmentLegacyProjectionError(ValueError):
    """Bounded refusal; no labels, paths, or score values in the message."""


def project_development_legacy_fit_input_v1(
    *,
    joint_study_payload: bytes,
    expected_joint_study_sha256: str,
    schedule_payload: bytes,
    expected_schedule_sha256: str,
    coverage_payload: bytes,
    expected_coverage_sha256: str,
    batch_payload: bytes,
    expected_batch_sha256: str,
    blueprint_payload: bytes,
    expected_blueprint_sha256: str,
    study_plan_payload: bytes,
    expected_study_plan_sha256: str,
    policy_payload: bytes,
    expected_policy_sha256: str,
    development_raw_event_payloads: tuple[bytes, ...],
    expected_development_raw_event_sha256s: tuple[str, ...],
) -> FrozenGate3Artifact[DevelopmentLegacyFitInputV1]:
    """Rebuild the outer study, then publish only its Development projection."""
    try:
        study = verify_devval_joint_study_v1(
            joint_study_payload,
            expected_joint_study_sha256=expected_joint_study_sha256,
            schedule_payload=schedule_payload,
            expected_schedule_sha256=expected_schedule_sha256,
            coverage_payload=coverage_payload,
            expected_coverage_sha256=expected_coverage_sha256,
            batch_payload=batch_payload,
            expected_batch_sha256=expected_batch_sha256,
            blueprint_payload=blueprint_payload,
            expected_blueprint_sha256=expected_blueprint_sha256,
            study_plan_payload=study_plan_payload,
            expected_study_plan_sha256=expected_study_plan_sha256,
        )
        if (
            study.status != "complete_computational"
            or type(policy_payload) is not bytes
            or not 0 < len(policy_payload) <= _MAX_POLICY_BYTES
        ):
            raise ValueError("outer Development study unavailable")
        policy = _verify(
            policy_payload,
            expected_sha256=expected_policy_sha256,
            contract_type=LegacyCalibrationPolicyV1,
        )
        if (
            study.source_checkpoint != policy.source_checkpoint
            or study.source_archive_sha256 != policy.source_archive_sha256
            or study.feature_plan_sha256 != policy.joint_feature_plan_sha256
        ):
            raise ValueError("Development policy and source changed")
        development = tuple(
            slot
            for slot in study.slots
            if slot.coverage.partition == DatasetPartition.DEVELOPMENT
        )
        if (
            len(development) < 2
            or type(development_raw_event_payloads) is not tuple
            or type(expected_development_raw_event_sha256s) is not tuple
            or len(development) != len(development_raw_event_payloads)
            or len(development) != len(expected_development_raw_event_sha256s)
        ):
            raise ValueError("Development raw event roster changed")
        projected = []
        for slot, raw_payload, raw_pin in zip(
            development,
            development_raw_event_payloads,
            expected_development_raw_event_sha256s,
            strict=True,
        ):
            raw = (
                _verify(
                    raw_payload,
                    expected_sha256=raw_pin,
                    contract_type=JointDevvalRawScoreEventV1,
                )
                if type(raw_payload) is bytes
                and 0 < len(raw_payload) <= _MAX_EVENT_BYTES
                else None
            )
            if (
                raw is None
                or slot.rows is None
                or raw.status != "complete_computational"
                or raw.partition != DatasetPartition.DEVELOPMENT
                or raw.slot_ordinal != slot.coverage.ordinal
                or raw.schedule_sha256 != study.schedule_sha256
                or raw.coverage_sha256 != study.coverage_sha256
                or raw.batch_sha256 != study.batch_sha256
                or raw.config_sha256 != policy.scorer_config_sha256
                or raw.event_closed_at != slot.coverage.event_closed_at
                or raw.decision_cutoff_at != slot.coverage.decision_cutoff_at
                or raw.joint_decision_at != slot.coverage.joint_decision_at
            ):
                raise ValueError("Development raw event does not match joint study")
            sides = []
            for row, scored, observed in zip(
                slot.rows, raw.sides, slot.coverage.sides, strict=True
            ):
                result = scored.raw_result
                if (
                    scored.instrument_id != row.label.instrument_id
                    or scored.aggregate != observed.aggregate
                    or scored.coverage_status != observed.status
                    or scored.publication_sha256 != observed.publication_sha256
                    or scored.readback_sha256 != observed.readback_sha256
                    or scored.market_replay_receipt_sha256
                    != observed.replay_receipt_sha256
                    or (result is None) != (scored.status == "missing")
                    or (
                        result is not None
                        and (
                            scored.raw_result_sha256 != result.canonical_sha256()
                            or result.status != scored.status
                            or result.instrument_id != scored.instrument_id
                            or result.config_sha256 != policy.scorer_config_sha256
                            or result.source_checkpoint != policy.source_checkpoint
                            or result.source_archive_sha256
                            != policy.source_archive_sha256
                            or result.decision_at != raw.joint_decision_at
                            or result.market_replay_receipt_sha256
                            != scored.market_replay_receipt_sha256
                        )
                    )
                ):
                    raise ValueError("Development raw side changed")
                sides.append(
                    DevelopmentLegacySideV1(
                        instrument_id=scored.instrument_id,
                        feature_history_start_at=row.feature_history_start_at,
                        feature_replay_sha256=row.feature_replay_sha256,
                        label=row.label,
                        status=scored.status,
                        direction="neutral" if result is None else result.raw_direction,
                        raw_score=None if result is None else result.raw_score,
                        raw_result_sha256=scored.raw_result_sha256,
                    )
                )
            projected.append(
                DevelopmentLegacySlotV1(
                    ordinal=slot.coverage.ordinal,
                    event_closed_at=slot.coverage.event_closed_at,
                    decision_at=slot.coverage.joint_decision_at,
                    sides=tuple(sides),
                )
            )
        return _freeze(
            DevelopmentLegacyFitInputV1(
                study_plan_sha256=study.study_plan_sha256,
                schedule_sha256=study.schedule_sha256,
                coverage_sha256=study.coverage_sha256,
                batch_sha256=study.batch_sha256,
                completion_sha256=study.completion_sha256,
                root_binding_sha256=study.root_binding_sha256,
                row_inventory_sha256=study.row_inventory_sha256,
                source_checkpoint=study.source_checkpoint,
                source_archive_sha256=study.source_archive_sha256,
                feature_plan_sha256=study.feature_plan_sha256,
                scorer_config_sha256=policy.scorer_config_sha256,
                development_event_times_sha256=study.development_event_times_sha256,
                slots=tuple(projected),
                development_folds=study.development_folds,
            ),
            contract_type=DevelopmentLegacyFitInputV1,
        )
    except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError):
        raise DevelopmentLegacyProjectionError(
            "development_legacy_projection_refused"
        ) from None
