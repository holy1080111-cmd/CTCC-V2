"""Outer-only projection of verified N188 Development rows into an isolated wire.

This module reads the full joint study to reproduce it, but serializes no
Validation row, feature, label, raw score, or outcome into the fit input.
"""

from __future__ import annotations

from app.mie.validation.artifact import SHA256_RE, FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.contracts import DatasetPartition
from app.mie.validation.frozen_legacy_raw_scorer_v1 import _checked_config
from app.mie.validation.prospective_development_validation_plan import INSTRUMENTS
from app.mie.validation.prospective_devval_development_fit_v1 import (
    DevelopmentLegacyFitInputV1,
    DevelopmentLegacySideV1,
    DevelopmentLegacySlotV1,
)
from app.mie.validation.prospective_devval_joint_companion_coverage_v1 import (
    PublishedCompanionV1,
    _outcomes,
)
from app.mie.validation.prospective_devval_joint_raw_score_v1 import (
    _MAX_EVENT_BYTES,
    JointDevvalRawScoreEventV1,
    score_joint_devval_companion_event_v1,
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


def _verify_development_raw_roster(study, development, payloads, pins, companions):
    if (
        len(development) < 2
        or type(payloads) is not tuple
        or type(pins) is not tuple
        or type(companions) is not tuple
        or len(payloads) != len(development)
        or len(pins) != len(development)
        or len(companions) != len(development)
        or any(type(pair) is not tuple or len(pair) != 2 for pair in companions)
        or any(type(pin) is not str or SHA256_RE.fullmatch(pin) is None for pin in pins)
        or len(set(pins)) != len(pins)
    ):
        raise DevelopmentLegacyProjectionError("development_raw_roster_changed")
    flat = tuple(item for pair in companions for item in pair)
    if any(type(item) is not PublishedCompanionV1 for item in flat):
        raise DevelopmentLegacyProjectionError("development_raw_roster_changed")
    # Reuse the acquisition inventory's uniqueness and native path checks.
    _outcomes(flat, len(study.slots))
    verified = []
    for slot, payload, pin, pair in zip(
        development, payloads, pins, companions, strict=True
    ):
        coverage = slot.coverage
        if (
            slot.rows is None
            or coverage.status != "covered_computational"
            or coverage.joint_decision_at is None
            or any(
                item.slot_ordinal != coverage.ordinal
                or item.instrument_id != symbol
                or item.expected_publication_sha256 != side.publication_sha256
                or item.expected_readback_sha256 != side.readback_sha256
                for item, symbol, side in zip(
                    pair, INSTRUMENTS, coverage.sides, strict=True
                )
            )
            or type(payload) is not bytes
            or not 0 < len(payload) <= _MAX_EVENT_BYTES
        ):
            raise DevelopmentLegacyProjectionError("development_raw_roster_changed")
        raw = _verify(
            payload, expected_sha256=pin, contract_type=JointDevvalRawScoreEventV1
        )
        if (
            raw.status != "complete_computational"
            or raw.partition != DatasetPartition.DEVELOPMENT
            or raw.slot_ordinal != coverage.ordinal
            or raw.schedule_sha256 != study.schedule_sha256
            or raw.coverage_sha256 != study.coverage_sha256
            or raw.batch_sha256 != study.batch_sha256
            or raw.event_closed_at != coverage.event_closed_at
            or raw.decision_cutoff_at != coverage.decision_cutoff_at
            or raw.joint_decision_at != coverage.joint_decision_at
        ):
            raise DevelopmentLegacyProjectionError("development_raw_roster_changed")
        verified.append(raw)
    return tuple(verified)


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
    scorer_config_json: bytes,
    development_raw_event_payloads: tuple[bytes, ...],
    expected_development_raw_event_sha256s: tuple[str, ...],
    development_companions: tuple[
        tuple[PublishedCompanionV1, PublishedCompanionV1], ...
    ],
) -> FrozenGate3Artifact[DevelopmentLegacyFitInputV1]:
    """Rebuild the study and exact Development scores before projecting rows."""
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
            or study.plan.feature_plan.outcome_horizon_seconds
            != policy.outcome_horizon_seconds
            or study.plan.feature_plan.positive_threshold != policy.positive_threshold
        ):
            raise ValueError("Development policy and source changed")
        _checked_config(
            scorer_config_json,
            expected_config_sha256=policy.scorer_config_sha256,
            expected_source_checkpoint=study.source_checkpoint,
            expected_source_archive_sha256=study.source_archive_sha256,
        )
        development = tuple(
            slot
            for slot in study.slots
            if slot.coverage.partition == DatasetPartition.DEVELOPMENT
        )
        retained = _verify_development_raw_roster(
            study,
            development,
            development_raw_event_payloads,
            expected_development_raw_event_sha256s,
            development_companions,
        )
        if any(raw.config_sha256 != policy.scorer_config_sha256 for raw in retained):
            raise DevelopmentLegacyProjectionError("development_raw_roster_changed")
        projected = []
        for slot, raw, raw_payload, raw_pin, companions in zip(
            development,
            retained,
            development_raw_event_payloads,
            expected_development_raw_event_sha256s,
            development_companions,
            strict=True,
        ):
            rebuilt = score_joint_devval_companion_event_v1(
                schedule_payload,
                expected_schedule_sha256=expected_schedule_sha256,
                coverage_payload=coverage_payload,
                expected_coverage_sha256=expected_coverage_sha256,
                expected_batch_sha256=expected_batch_sha256,
                config_json=scorer_config_json,
                expected_config_sha256=policy.scorer_config_sha256,
                slot_ordinal=slot.coverage.ordinal,
                companions=companions,
            )
            if rebuilt.payload != raw_payload or rebuilt.sha256 != raw_pin:
                raise DevelopmentLegacyProjectionError("development_raw_replay_changed")
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
    except DevelopmentLegacyProjectionError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError):
        raise DevelopmentLegacyProjectionError(
            "development_legacy_projection_refused"
        ) from None
