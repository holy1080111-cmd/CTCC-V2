"""Read-only coverage checkpoint for the prospective D/V source pilot.

The existing owned public journal replays every raw byte and attempt chain.
This file never invokes capture, fixes gaps, or grants predictive authority.
The external plan and checkpoint hashes are caller pins, not independent custody.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.mie.validation.artifact import (
    SHA256_RE,
    ArtifactVerificationError,
    FrozenGate3Artifact,
    _freeze,
    _verify,
)
from app.mie.validation.contracts import Gate3Claim, Gate3Contract, Sha256, require_utc
from app.mie.validation.prospective_development_validation_plan import (
    MAX_PILOT_ROWS,
    ProspectiveDevelopmentValidationPlanV1,
)
from app.public_market_source.public_receipt_storage import (
    ControlledPublicReceiptJournal,
)

_State = Literal[
    "missing_from_journal",
    "replayed_local_custody_unproven",
    "late_local_observation",
    "duplicate_capture",
    "source_conflict",
    "attempt_unbound",
]


class DevelopmentValidationMinuteCoverageV1(Gate3Contract):
    ordinal: int = Field(ge=0, lt=MAX_PILOT_ROWS)
    partition: Literal["development", "validation"]
    opening_at: datetime
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    capture_receipt_sha256s: tuple[Sha256, ...] = Field(max_length=1024)
    source_state: _State

    @field_validator("opening_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return require_utc(value, "opening_at")

    @model_validator(mode="after")
    def state_matches_inventory(self) -> DevelopmentValidationMinuteCoverageV1:
        count = len(self.capture_receipt_sha256s)
        if (
            (count == 0 and self.source_state != "missing_from_journal")
            or (
                count == 1
                and self.source_state in {"missing_from_journal", "duplicate_capture"}
            )
            or (count > 1 and self.source_state != "duplicate_capture")
        ):
            raise ValueError("devval coverage state differs from capture inventory")
        return self


class DevelopmentValidationCollectionCheckpointV1(Gate3Contract):
    """A hashable gap inventory, not an accepted Point-in-Time dataset."""

    schema_version: Literal["ctcc.mie.gate3.prospective_devval_checkpoint.v1"] = (
        "ctcc.mie.gate3.prospective_devval_checkpoint.v1"
    )
    plan_sha256: Sha256
    journal_checkpoint_sha256: Sha256
    journal_genesis_sha256: Sha256
    journal_head_sha256: Sha256
    journal_capture_sequence: int = Field(ge=0, le=1024)
    journal_attempt_sequence: int = Field(ge=0, le=1024)
    expected_rows: int = Field(ge=4, le=MAX_PILOT_ROWS)
    observed_captures: int = Field(ge=0, le=1024)
    coverage: tuple[DevelopmentValidationMinuteCoverageV1, ...] = Field(
        min_length=4, max_length=MAX_PILOT_ROWS
    )
    missing_ordinals: tuple[int, ...]
    late_ordinals: tuple[int, ...]
    duplicate_ordinals: tuple[int, ...]
    source_conflict_ordinals: tuple[int, ...]
    attempt_unbound_ordinals: tuple[int, ...]
    all_planned_minutes_replayed_locally: bool
    source_time_basis: Literal["owned_journal_local_clock_unverified"] = (
        "owned_journal_local_clock_unverified"
    )
    source_checkpoint_custody_independently_proven: Literal[False] = False
    status: Literal["INCOMPLETE"] = "INCOMPLETE"
    candidate_fitted: Literal[False] = False
    holdout_accessed: Literal[False] = False
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    reference_only: Literal[True] = True
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def coverage_matches_status(self) -> DevelopmentValidationCollectionCheckpointV1:
        if len(self.coverage) != self.expected_rows or tuple(
            item.ordinal for item in self.coverage
        ) != tuple(range(self.expected_rows)):
            raise ValueError("devval checkpoint coverage is incomplete or reordered")
        groups = (
            ("missing_from_journal", self.missing_ordinals),
            ("late_local_observation", self.late_ordinals),
            ("duplicate_capture", self.duplicate_ordinals),
            ("source_conflict", self.source_conflict_ordinals),
            ("attempt_unbound", self.attempt_unbound_ordinals),
        )
        if any(
            ordinals
            != tuple(
                item.ordinal for item in self.coverage if item.source_state == state
            )
            for state, ordinals in groups
        ):
            raise ValueError("devval checkpoint gap inventory differs")
        if (
            self.observed_captures
            != sum(len(item.capture_receipt_sha256s) for item in self.coverage)
            or self.observed_captures != self.journal_capture_sequence
        ):
            raise ValueError("devval checkpoint capture count differs")
        complete = all(
            item.source_state == "replayed_local_custody_unproven"
            for item in self.coverage
        )
        if self.all_planned_minutes_replayed_locally != complete:
            raise ValueError("devval checkpoint local coverage claim differs")
        return self


def audit_development_validation_journal(
    *,
    plan: ProspectiveDevelopmentValidationPlanV1,
    expected_plan_sha256: str,
    journal: ControlledPublicReceiptJournal,
    expected_journal_checkpoint_sha256: str,
) -> FrozenGate3Artifact[DevelopmentValidationCollectionCheckpointV1]:
    """Replay one dedicated journal and enumerate every absent or unsafe minute."""
    if (
        type(plan) is not ProspectiveDevelopmentValidationPlanV1
        or type(journal) is not ControlledPublicReceiptJournal
        or type(expected_plan_sha256) is not str
        or type(expected_journal_checkpoint_sha256) is not str
        or SHA256_RE.fullmatch(expected_plan_sha256) is None
        or SHA256_RE.fullmatch(expected_journal_checkpoint_sha256) is None
    ):
        raise ValueError("exact devval plan, journal and external pins required")
    checked_plan = ProspectiveDevelopmentValidationPlanV1.model_validate(
        plan.model_dump(mode="python")
    )
    checkpoint = journal.checkpoint
    if (
        checked_plan.canonical_sha256() != expected_plan_sha256
        or checkpoint.canonical_sha256() != expected_journal_checkpoint_sha256
    ):
        raise ValueError("devval external plan or journal checkpoint differs")
    expected = {
        checked_plan.capture_plan(ordinal).canonical_sha256(): ordinal
        for ordinal in range(checked_plan.expected_rows)
    }
    if len(expected) != checked_plan.expected_rows:
        raise ValueError("devval source plans are not unique")
    entries, _ = journal.read_all()
    observed: list[list[tuple[object, object, object]]] = [
        [] for _ in range(checked_plan.expected_rows)
    ]
    conflicted_identities = {
        identity for _, _, _, entry in entries for identity in entry["source_conflicts"]
    }
    for source_plan, receipt, _, entry in entries:
        ordinal = expected.get(source_plan.canonical_sha256())
        if ordinal is None or receipt.plan_sha256 != source_plan.canonical_sha256():
            raise ValueError("dedicated devval journal contains a foreign source plan")
        if len(receipt.rows) != 1:
            raise ValueError("devval minute capture has an unexpected row count")
        observed[ordinal].append((receipt, entry, receipt.rows[0]))
    # The journal itself detects an unanchored or changed append. This second
    # replay also rejects mutation while the checkpoint inventory was built.
    again, _ = journal.read_all()
    if journal.checkpoint != checkpoint or tuple(
        (source_plan.canonical_sha256(), receipt.canonical_sha256(), entry)
        for source_plan, receipt, _, entry in again
    ) != tuple(
        (source_plan.canonical_sha256(), receipt.canonical_sha256(), entry)
        for source_plan, receipt, _, entry in entries
    ):
        raise ValueError("devval journal changed during checkpoint replay")

    coverage = []
    for ordinal, observations in enumerate(observed):
        partition, opening, instrument = checked_plan.coordinate(ordinal)
        if not observations:
            state: _State = "missing_from_journal"
        elif len(observations) != 1:
            state = "duplicate_capture"
        else:
            receipt, entry, row = observations[0]
            capture_plan = checked_plan.capture_plan(ordinal)
            if (
                entry["disposition"] != "accepted"
                or row["identity"] in conflicted_identities
            ):
                state = "source_conflict"
            elif receipt.attempt_sha256 is None:
                state = "attempt_unbound"
            elif (
                max(
                    receipt.validation_complete["utc_ns"],
                    entry["payload_readback_complete"]["utc_ns"],
                )
                > capture_plan.end_ns
                + checked_plan.maximum_observation_lag_seconds * 1_000_000_000
            ):
                state = "late_local_observation"
            else:
                state = "replayed_local_custody_unproven"
        coverage.append(
            DevelopmentValidationMinuteCoverageV1(
                ordinal=ordinal,
                partition=partition,
                opening_at=opening,
                instrument_id=instrument,
                capture_receipt_sha256s=tuple(
                    receipt.canonical_sha256() for receipt, _, _ in observations
                ),
                source_state=state,
            )
        )
    rows = tuple(coverage)
    result = DevelopmentValidationCollectionCheckpointV1(
        plan_sha256=expected_plan_sha256,
        journal_checkpoint_sha256=expected_journal_checkpoint_sha256,
        journal_genesis_sha256=checkpoint.genesis_sha256,
        journal_head_sha256=checkpoint.head_sha256,
        journal_capture_sequence=checkpoint.sequence,
        journal_attempt_sequence=checkpoint.attempt_sequence,
        expected_rows=checked_plan.expected_rows,
        observed_captures=len(entries),
        coverage=rows,
        missing_ordinals=tuple(
            row.ordinal for row in rows if row.source_state == "missing_from_journal"
        ),
        late_ordinals=tuple(
            row.ordinal for row in rows if row.source_state == "late_local_observation"
        ),
        duplicate_ordinals=tuple(
            row.ordinal for row in rows if row.source_state == "duplicate_capture"
        ),
        source_conflict_ordinals=tuple(
            row.ordinal for row in rows if row.source_state == "source_conflict"
        ),
        attempt_unbound_ordinals=tuple(
            row.ordinal for row in rows if row.source_state == "attempt_unbound"
        ),
        all_planned_minutes_replayed_locally=all(
            row.source_state == "replayed_local_custody_unproven" for row in rows
        ),
    )
    return _freeze(result, contract_type=DevelopmentValidationCollectionCheckpointV1)


def verify_development_validation_checkpoint(
    payload: bytes,
    *,
    expected_sha256: str,
    plan: ProspectiveDevelopmentValidationPlanV1,
    expected_plan_sha256: str,
    journal: ControlledPublicReceiptJournal,
    expected_journal_checkpoint_sha256: str,
) -> DevelopmentValidationCollectionCheckpointV1:
    """Verify frozen bytes against a new full source-journal readback."""
    retained = _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=DevelopmentValidationCollectionCheckpointV1,
    )
    try:
        fresh = audit_development_validation_journal(
            plan=plan,
            expected_plan_sha256=expected_plan_sha256,
            journal=journal,
            expected_journal_checkpoint_sha256=expected_journal_checkpoint_sha256,
        )
    except (OSError, ValueError) as exc:
        raise ArtifactVerificationError(
            "devval source readback is unavailable"
        ) from exc
    if fresh.payload != payload:
        raise ArtifactVerificationError("devval checkpoint differs from source journal")
    return retained


__all__ = (
    "DevelopmentValidationCollectionCheckpointV1",
    "DevelopmentValidationMinuteCoverageV1",
    "audit_development_validation_journal",
    "verify_development_validation_checkpoint",
)
