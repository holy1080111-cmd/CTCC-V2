"""Opt-in public capture witness seam; no startup wiring or authority grant.

The repository must use a separately provisioned restricted PostgreSQL role.
Even then this seam reports computational evidence only until deployment-level
OS/database isolation and real measured-source acceptance are reviewed.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
    PublicWitnessError,
    WitnessRevision,
)
from app.public_market_source.public_checkpoint_hook import (
    _ISSUER as _HOOK_ISSUER,
)
from app.public_market_source.public_checkpoint_hook import (
    OwnedPublicCheckpointHook,
)
from app.public_market_source.public_market_receipts import (
    PublicMinuteCapturePlanV1,
    checked,
    sha,
)
from app.public_market_source.public_receipt_storage import (
    ControlledPublicReceiptJournal,
    _read_json,
    _root_context,
    initialize_public_receipt_journal,
    resume_public_receipt_journal,
)

_ISSUER = object()
_HEX32 = re.compile(r"[a-f0-9]{32}\Z")
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")


@dataclass(frozen=True, slots=True)
class WitnessedPublicCapture:
    capture_id: str
    receipt_sha256: str
    witness_revision: int
    witness_record_sha256: str
    checkpoint_sha256: str
    independently_protected: bool = False
    predictive_oos_eligible: bool = False
    execution_authority: bool = False


@dataclass(frozen=True, slots=True)
class OwnedPostReadCapture:
    """Hash-only result after independent V3 database and source readback."""

    witnessed: WitnessedPublicCapture
    blind_capture_sha256: str
    post_read_v3_sha256: str
    post_read_observed_at: datetime
    predictive_oos_eligible: bool = False
    execution_authority: bool = False


def _journal_id(root: Path, expected_genesis_sha256: str) -> str:
    with _root_context(root) as directory:
        genesis, raw = _read_json(directory, "genesis.json")
    journal_id = genesis.get("journal_id")
    if (
        type(journal_id) is not str
        or _HEX32.fullmatch(journal_id) is None
        or sha(raw) != expected_genesis_sha256
    ):
        raise PublicWitnessError("witness_journal_genesis_invalid")
    return journal_id


class ControlledPublicCheckpointWitness:
    """One capture invocation; never accepts a caller's checkpoint as proof."""

    __slots__ = (
        "_journal",
        "_journal_id",
        "_latest",
        "_operation_id",
        "_plan_sha256",
        "_repository",
    )

    def __init__(
        self,
        issuer,
        journal: ControlledPublicReceiptJournal,
        repository: PublicReceiptWitnessRepository,
        journal_id: str,
        latest: WitnessRevision,
    ):
        if (
            issuer is not _ISSUER
            or type(journal) is not ControlledPublicReceiptJournal
            or type(repository) is not PublicReceiptWitnessRepository
            or type(latest) is not WitnessRevision
            or type(journal_id) is not str
            or _HEX32.fullmatch(journal_id) is None
        ):
            raise PublicWitnessError("controlled_witness_required")
        self._journal = journal
        self._repository = repository
        self._journal_id = journal_id
        self._latest = latest
        self._operation_id = None
        self._plan_sha256 = None

    async def open(self, journal, plan):
        if journal is not self._journal or self._operation_id is not None:
            raise PublicWitnessError("witness_operation_unavailable")
        plan = checked(plan, PublicMinuteCapturePlanV1)
        latest = await self._repository.read_latest(self._latest.journal_key)
        if (
            latest != self._latest
            or latest.state != "idle"
            or latest.checkpoint != journal.checkpoint
        ):
            raise PublicWitnessError("witness_checkpoint_changed")
        journal.read_all()
        operation_id = uuid.uuid4().hex
        next_row = await self._repository.append(
            journal_id=self._journal_id,
            checkpoint=journal.checkpoint,
            transition="open_attempt",
            expected=latest,
            operation_id=operation_id,
            plan_sha256=plan.canonical_sha256(),
        )
        self._latest = next_row
        self._operation_id = operation_id
        self._plan_sha256 = plan.canonical_sha256()

    async def after_attempt(self, journal, disposition):
        if (
            journal is not self._journal
            or self._operation_id is None
            or self._latest.state != "open"
            or disposition not in ("completed_collection", "rejected", "incomplete")
        ):
            raise PublicWitnessError("witness_attempt_state_invalid")
        journal.read_all()
        next_row = await self._repository.append(
            journal_id=self._journal_id,
            checkpoint=journal.checkpoint,
            transition="append_attempt",
            expected=self._latest,
            operation_id=self._operation_id,
            plan_sha256=self._plan_sha256,
            attempt_outcome=disposition,
        )
        self._latest = next_row
        if disposition != "completed_collection":
            # Separate transaction/readback retains the negative attempt.
            self._latest = await self._repository.append(
                journal_id=self._journal_id,
                checkpoint=journal.checkpoint,
                transition="close_rejected_attempt",
                expected=next_row,
                operation_id=self._operation_id,
                plan_sha256=self._plan_sha256,
                attempt_outcome=disposition,
            )

    async def after_capture(self, journal):
        if (
            journal is not self._journal
            or self._latest.state != "attempt_anchored"
            or self._latest.attempt_outcome != "completed_collection"
            or journal.checkpoint.sequence != self._latest.checkpoint.sequence + 1
        ):
            raise PublicWitnessError("witness_capture_state_invalid")
        journal.read_all()
        self._latest = await self._repository.append(
            journal_id=self._journal_id,
            checkpoint=journal.checkpoint,
            transition="append_capture",
            expected=self._latest,
            operation_id=self._operation_id,
            plan_sha256=self._plan_sha256,
            attempt_outcome="completed_collection",
        )

    @property
    def before_capture_sequence(self):
        return self._latest.checkpoint.sequence

    def result(self, published):
        if (
            self._latest.state != "idle"
            or self._latest.transition != "append_capture"
            or self._latest.checkpoint != published.checkpoint
        ):
            raise PublicWitnessError("witness_result_unanchored")
        return WitnessedPublicCapture(
            capture_id=published.capture_id,
            receipt_sha256=published.receipt_sha256,
            witness_revision=self._latest.revision,
            witness_record_sha256=self._latest.record_sha256,
            checkpoint_sha256=self._latest.checkpoint_sha256,
        )


class PublicCheckpointCaptureService:
    """Explicit one-root owner; never created by API startup or scheduler."""

    __slots__ = ("_journal", "_journal_id", "_latest", "_repository")

    def __init__(self, issuer, journal, repository, journal_id, latest):
        if issuer is not _ISSUER:
            raise PublicWitnessError("controlled_witness_required")
        self._journal = journal
        self._repository = repository
        self._journal_id = journal_id
        self._latest = latest

    @classmethod
    async def initialize(
        cls, *, root: Path, repository: PublicReceiptWitnessRepository
    ):
        if type(repository) is not PublicReceiptWitnessRepository:
            raise PublicWitnessError("restricted_witness_repository_required")
        await repository.verify_role()
        journal = initialize_public_receipt_journal(root)
        journal_id = _journal_id(root, journal.checkpoint.genesis_sha256)
        latest = await repository.append(
            journal_id=journal_id,
            checkpoint=journal.checkpoint,
            transition="initialize",
            expected=None,
        )
        return cls(_ISSUER, journal, repository, journal_id, latest)

    @property
    def genesis_sha256(self):
        return self._latest.journal_key

    @property
    def journal_id(self):
        return self._journal_id

    @classmethod
    async def resume(
        cls,
        *,
        root: Path,
        expected_genesis_sha256: str,
        expected_journal_id: str,
        repository: PublicReceiptWitnessRepository,
    ):
        if (
            type(repository) is not PublicReceiptWitnessRepository
            or type(expected_genesis_sha256) is not str
            or _HEX64.fullmatch(expected_genesis_sha256) is None
            or type(expected_journal_id) is not str
            or _HEX32.fullmatch(expected_journal_id) is None
        ):
            raise PublicWitnessError("witness_resume_pin_invalid")
        latest = await repository.read_latest(expected_genesis_sha256)
        if latest is None or latest.journal_id != expected_journal_id:
            raise PublicWitnessError("witness_resume_identity_missing")
        if latest.state != "idle":
            raise PublicWitnessError("witness_operation_unresolved")
        if _journal_id(root, expected_genesis_sha256) != expected_journal_id:
            raise PublicWitnessError("witness_journal_genesis_invalid")
        journal = resume_public_receipt_journal(
            root=root, trusted_checkpoint=latest.checkpoint
        )
        return cls(_ISSUER, journal, repository, expected_journal_id, latest)

    async def capture(self, *, plan, expected_plan_sha256):
        from app.public_market_source.public_market_capture import (
            collect_and_publish_public_minutes,
        )

        witness = ControlledPublicCheckpointWitness(
            _ISSUER,
            self._journal,
            self._repository,
            self._journal_id,
            self._latest,
        )
        hook = OwnedPublicCheckpointHook(_HOOK_ISSUER, self._journal, witness)
        try:
            published = await collect_and_publish_public_minutes(
                plan=plan,
                expected_plan_sha256=expected_plan_sha256,
                journal=self._journal,
                _witness=hook,
            )
            return witness.result(published)
        finally:
            # A negative attempt can be fully anchored and closed before the
            # collector raises. Unknown commits still block the next open.
            self._latest = witness._latest

    async def capture_with_post_read_v3(
        self,
        *,
        plan,
        expected_plan_sha256,
        preregistration,
        expected_preregistration_sha256,
        observation_repository,
    ) -> OwnedPostReadCapture:
        """Capture once, persist V3 once, then independently replay both sources.

        A failure after capture or an uncertain observation append is never
        retried. The journal and any committed database row remain available
        for a separate reconciliation; this method grants no predictive claim.
        """
        from app.database.repositories.public_receipt_post_read_observation import (
            PublicReceiptPostReadRepository,
        )
        from app.mie.validation.blind_window_capture import (
            bind_blind_window_minute,
        )
        from app.mie.validation.post_publication_availability_v3 import (
            freeze_post_publication_capture_v3,
            verify_post_publication_capture_v3,
        )
        from app.mie.validation.prospective import Gate3ProspectivePreregistration

        if (
            type(observation_repository) is not PublicReceiptPostReadRepository
            or type(preregistration) is not Gate3ProspectivePreregistration
            or type(expected_preregistration_sha256) is not str
            or preregistration.canonical_sha256() != expected_preregistration_sha256
        ):
            raise PublicWitnessError("scheduled_capture_inputs_invalid")
        selected = checked(plan, PublicMinuteCapturePlanV1)
        if selected.canonical_sha256() != expected_plan_sha256:
            raise PublicWitnessError("scheduled_capture_plan_pin_mismatch")
        witnessed = await self.capture(
            plan=selected, expected_plan_sha256=expected_plan_sha256
        )
        checkpoint = self._journal.checkpoint
        if (
            checkpoint.canonical_sha256() != witnessed.checkpoint_sha256
            or checkpoint.genesis_sha256 != self._latest.journal_key
        ):
            raise PublicWitnessError("scheduled_capture_checkpoint_changed")
        source = {
            "journal": self._journal,
            "witness_repository": self._repository,
            "capture_id": witnessed.capture_id,
            "expected_plan_sha256": expected_plan_sha256,
            "expected_receipt_sha256": witnessed.receipt_sha256,
            "expected_journal_genesis_sha256": checkpoint.genesis_sha256,
            "expected_capture_checkpoint_sha256": witnessed.checkpoint_sha256,
            "expected_witness_revision": witnessed.witness_revision,
            "expected_witness_record_sha256": witnessed.witness_record_sha256,
        }
        frozen = await freeze_post_publication_capture_v3(
            observation_repository=observation_repository, **source
        )
        verified = await verify_post_publication_capture_v3(
            frozen.payload,
            expected_sha256=frozen.sha256,
            observation_repository=observation_repository,
            **source,
        )
        blind = bind_blind_window_minute(
            journal=self._journal,
            preregistration=preregistration,
            expected_preregistration_sha256=expected_preregistration_sha256,
            expected_capture_plan_sha256=expected_plan_sha256,
            expected_capture_receipt_sha256=witnessed.receipt_sha256,
            expected_journal_checkpoint_sha256=witnessed.checkpoint_sha256,
            capture_id=witnessed.capture_id,
        )
        if (
            verified != frozen.contract
            or verified.v2_capture.capture_id != witnessed.capture_id
            or verified.v2_capture.receipt_sha256 != witnessed.receipt_sha256
            or verified.v2_capture.witness_record_sha256
            != witnessed.witness_record_sha256
            or verified.v2_capture.capture_sequence != blind.journal_sequence
            or len(verified.v2_capture.rows) != 1
            or verified.v2_capture.rows[0].source_row_sha256 != blind.source_row_sha256
            or verified.available_at < blind.retained_at
        ):
            raise PublicWitnessError("scheduled_capture_post_read_mismatch")
        return OwnedPostReadCapture(
            witnessed=witnessed,
            blind_capture_sha256=blind.canonical_sha256(),
            post_read_v3_sha256=frozen.sha256,
            post_read_observed_at=verified.available_at,
        )


__all__ = (
    "OwnedPostReadCapture",
    "PublicCheckpointCaptureService",
    "WitnessedPublicCapture",
)
