"""Opt-in public capture witness seam; no startup wiring or authority grant.

The repository must use a separately provisioned restricted PostgreSQL role.
Even then this seam reports computational evidence only until deployment-level
OS/database isolation and real measured-source acceptance are reviewed.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock, get_ident
from types import FunctionType, MethodType
from weakref import WeakKeyDictionary

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
    PublicWitnessError,
    WitnessRevision,
)
from app.database.repositories.public_runtime_bundle_custody import _native_engine
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
_SOURCE_ISSUER = object()
_SOURCE_LOCK = Lock()
_SOURCE_RESULTS = WeakKeyDictionary()
_SOURCE_USED_PLANS = WeakKeyDictionary()
_SOURCE_BUSY = WeakKeyDictionary()
_SOURCE_REPOSITORY_METHODS = {
    cls: {
        name: member
        for name, member in vars(cls).items()
        if type(member) in (FunctionType, staticmethod)
    }
    for cls in (PublicReceiptWitnessRepository, PublicReceiptPostReadRepository)
}


@dataclass(frozen=True, slots=True)
class _SourceDatabasePins:
    witness: object
    observation: object
    factories: tuple
    engines: tuple
    options: tuple


def _source_database_pins(witness, observation, pins=None):
    """Offline native-factory and method guard, before any repository callback.

    Reuses the reviewed pinned SQLAlchemy/asyncpg engine policy. As with runtime
    bundle custody, hostile arbitrary in-process replacement and DBAs remain
    outside this application-role boundary.
    """
    repositories = (witness, observation)
    classes = (PublicReceiptWitnessRepository, PublicReceiptPostReadRepository)
    fields = ({"session_factory"}, {"session_factory", "witness_repository"})
    factories, engines, options = [], [], []
    for repository, cls, keys in zip(repositories, classes, fields, strict=True):
        if type(repository) is not cls or set(vars(repository)) != keys:
            raise PublicWitnessError("source_native_database_required")
        for name, expected in _SOURCE_REPOSITORY_METHODS[cls].items():
            if vars(cls).get(name) is not expected:
                raise PublicWitnessError("source_native_database_required")
            bound = getattr(repository, name)
            if type(expected) is staticmethod:
                valid = type(bound) is FunctionType and bound is expected.__func__
            else:
                valid = (
                    type(bound) is MethodType
                    and bound.__self__ is repository
                    and bound.__func__ is expected
                )
            if not valid:
                raise PublicWitnessError("source_native_database_required")
        factory = repository.session_factory
        if (
            type(factory) is not async_sessionmaker
            or set(vars(factory)) != {"kw", "class_"}
            or factory.class_ is not AsyncSession
            or type(factory.kw) is not dict
            or not set(factory.kw) <= {"bind", "autoflush", "expire_on_commit"}
            or any(
                key in factory.kw and type(factory.kw[key]) is not bool
                for key in ("autoflush", "expire_on_commit")
            )
        ):
            raise PublicWitnessError("source_native_database_required")
        engine = factory.kw.get("bind")
        if (
            type(engine) is not AsyncEngine
            or engine.dialect.name != "postgresql"
            or engine.dialect.driver != "asyncpg"
            or not _native_engine(engine)
        ):
            raise PublicWitnessError("source_native_database_required")
        factories.append(factory)
        engines.append(engine)
        options.append(tuple(sorted(factory.kw.items())))
    if observation.witness_repository is not witness:
        raise PublicWitnessError("source_native_database_required")
    if pins is not None and (
        type(pins) is not _SourceDatabasePins
        or pins.witness is not witness
        or pins.observation is not observation
        or any(
            old is not new for old, new in zip(pins.factories, factories, strict=True)
        )
        or any(old is not new for old, new in zip(pins.engines, engines, strict=True))
        or pins.options != tuple(options)
    ):
        raise PublicWitnessError("source_native_database_changed")
    return _SourceDatabasePins(
        witness, observation, tuple(factories), tuple(engines), tuple(options)
    )


def _owned_source_repositories(pins):
    """Private factories/repos, so caller instance swaps cannot enter DB awaits.

    Engines are the already verified native engines, not new DB connections or
    a second DB implementation. Shared engine/class hostile mutation remains
    outside the reviewed application-role model.
    """
    if type(pins) is not _SourceDatabasePins:
        raise PublicWitnessError("source_native_database_required")
    factories = tuple(
        async_sessionmaker(class_=AsyncSession, **dict(options))
        for options in pins.options
    )
    witness = PublicReceiptWitnessRepository(factories[0])
    observation = PublicReceiptPostReadRepository(factories[1], witness)
    return witness, observation


def _source_context():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        raise PublicWitnessError("source_capture_task_required") from None
    if task is None:
        raise PublicWitnessError("source_capture_task_required")
    return os.getpid(), get_ident(), task


class NativeSourcePostReadCapture:
    """One-use same-task result, never reconstructible from saved evidence."""

    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _SOURCE_ISSUER:
            raise PublicWitnessError("native_source_capture_required")

    def __copy__(self):
        raise PublicWitnessError("native_source_capture_not_transferable")

    def __deepcopy__(self, memo):
        raise PublicWitnessError("native_source_capture_not_transferable")

    def __reduce_ex__(self, protocol):
        raise PublicWitnessError("native_source_capture_not_transferable")

    def __reduce__(self):
        raise PublicWitnessError("native_source_capture_not_transferable")


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

    __slots__ = ("__weakref__", "_journal", "_journal_id", "_latest", "_repository")

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

    async def capture_source_with_post_read_v3(
        self, *, plan, expected_plan_sha256, observation_repository
    ) -> NativeSourcePostReadCapture:
        """Acquire a fresh source batch without a holdout, seal or candidate.

        The new method burns its plan before any await. Neither a failed append
        nor an uncertain readback can be retried through this service instance.
        Only its process/thread/task can consume the resulting one-use carrier.
        V3's clock/custody/research limitations remain unchanged.
        """
        from app.database.repositories.public_receipt_post_read_observation import (
            PublicReceiptPostReadRepository,
        )
        from app.mie.validation.artifact import FrozenGate3Artifact
        from app.mie.validation.post_publication_availability_v3 import (
            PostPublicationCaptureV3,
            freeze_post_publication_capture_v3,
            verify_post_publication_capture_v3,
        )

        if (
            type(self) is not PublicCheckpointCaptureService
            or type(observation_repository) is not PublicReceiptPostReadRepository
            or observation_repository.witness_repository is not self._repository
        ):
            raise PublicWitnessError("source_capture_services_invalid")
        selected = checked(plan, PublicMinuteCapturePlanV1)
        if (
            type(expected_plan_sha256) is not str
            or selected.canonical_sha256() != expected_plan_sha256
        ):
            raise PublicWitnessError("source_capture_plan_pin_mismatch")
        context = _source_context()
        with _SOURCE_LOCK:
            used = _SOURCE_USED_PLANS.setdefault(self, set())
            if (
                self in _SOURCE_BUSY
                or expected_plan_sha256 in used
                or len(used) >= 1024
            ):
                raise PublicWitnessError("source_capture_already_attempted")
            used.add(expected_plan_sha256)
            _SOURCE_BUSY[self] = context
        owned_service = None
        try:
            database_pins = _source_database_pins(
                self._repository, observation_repository
            )
            owned_repository, owned_observation = _owned_source_repositories(
                database_pins
            )
            owned_pins = _source_database_pins(owned_repository, owned_observation)
            owned_service = PublicCheckpointCaptureService(
                _ISSUER, self._journal, owned_repository, self._journal_id, self._latest
            )
            await owned_repository.verify_role()
            _source_database_pins(
                self._repository, observation_repository, database_pins
            )
            _source_database_pins(owned_repository, owned_observation, owned_pins)
            await owned_observation.verify_role()
            _source_database_pins(
                self._repository, observation_repository, database_pins
            )
            _source_database_pins(owned_repository, owned_observation, owned_pins)
            witnessed = await owned_service.capture(
                plan=selected, expected_plan_sha256=expected_plan_sha256
            )
            _source_database_pins(
                self._repository, observation_repository, database_pins
            )
            _source_database_pins(owned_repository, owned_observation, owned_pins)
            checkpoint = self._journal.checkpoint
            if (
                type(witnessed) is not WitnessedPublicCapture
                or checkpoint.canonical_sha256() != witnessed.checkpoint_sha256
                or checkpoint.genesis_sha256 != owned_service._latest.journal_key
            ):
                raise PublicWitnessError("source_capture_checkpoint_changed")
            source = {
                "journal": self._journal,
                "witness_repository": owned_repository,
                "capture_id": witnessed.capture_id,
                "expected_plan_sha256": expected_plan_sha256,
                "expected_receipt_sha256": witnessed.receipt_sha256,
                "expected_journal_genesis_sha256": checkpoint.genesis_sha256,
                "expected_capture_checkpoint_sha256": witnessed.checkpoint_sha256,
                "expected_witness_revision": witnessed.witness_revision,
                "expected_witness_record_sha256": witnessed.witness_record_sha256,
            }
            frozen = await freeze_post_publication_capture_v3(
                observation_repository=owned_observation, **source
            )
            _source_database_pins(
                self._repository, observation_repository, database_pins
            )
            _source_database_pins(owned_repository, owned_observation, owned_pins)
            if type(frozen) is not FrozenGate3Artifact:
                raise PublicWitnessError("source_post_read_result_invalid")
            verified = await verify_post_publication_capture_v3(
                frozen.payload,
                expected_sha256=frozen.sha256,
                observation_repository=owned_observation,
                **source,
            )
            _source_database_pins(
                self._repository, observation_repository, database_pins
            )
            _source_database_pins(owned_repository, owned_observation, owned_pins)
            if (
                type(verified) is not PostPublicationCaptureV3
                or verified != frozen.contract
                or verified.canonical_json_bytes() != frozen.payload
                or sha(frozen.payload) != frozen.sha256
                or verified.v2_capture.capture_id != witnessed.capture_id
                or verified.v2_capture.plan_sha256 != expected_plan_sha256
                or verified.v2_capture.receipt_sha256 != witnessed.receipt_sha256
                or verified.v2_capture.instrument_id != selected.instrument_id
                or len(verified.v2_capture.rows) != selected.expected_rows
                or _source_context() != context
            ):
                raise PublicWitnessError("source_post_read_result_mismatch")
            result = NativeSourcePostReadCapture(_SOURCE_ISSUER)
            with _SOURCE_LOCK:
                _SOURCE_RESULTS[result] = (self, context, expected_plan_sha256, frozen)
            return result
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - DB/transport details must not enter public evidence.
            raise PublicWitnessError("source_post_read_capture_failed") from None
        finally:
            if owned_service is not None:
                # Includes capture errors and unknown commits; never erase the
                # latest unresolved witness state or restart the old operation.
                self._latest = owned_service._latest
            with _SOURCE_LOCK:
                _SOURCE_BUSY.pop(self, None)

    def consume_source_post_read_v3(self, result, *, expected_plan_sha256):
        """Burn one native result; returned bytes are evidence, never authority."""
        if type(result) is not NativeSourcePostReadCapture:
            raise PublicWitnessError("native_source_capture_required")
        with _SOURCE_LOCK:
            bound = _SOURCE_RESULTS.pop(result, None)
        if (
            bound is None
            or type(expected_plan_sha256) is not str
            or _HEX64.fullmatch(expected_plan_sha256) is None
            or bound[0] is not self
            or bound[1] != _source_context()
            or bound[2] != expected_plan_sha256
        ):
            raise PublicWitnessError("native_source_capture_required")
        frozen = bound[3]
        if (
            frozen.contract.canonical_json_bytes() != frozen.payload
            or sha(frozen.payload) != frozen.sha256
        ):
            raise PublicWitnessError("native_source_capture_changed")
        return frozen

    async def replay_source_post_read_v3(
        self, payload, *, expected_sha256, expected_plan_sha256, observation_repository
    ):
        """Read-only original-source replay. This never issues a native carrier."""
        from app.database.repositories.public_receipt_post_read_observation import (
            PublicReceiptPostReadRepository,
        )
        from app.mie.validation.artifact import _verify
        from app.mie.validation.post_publication_availability_v3 import (
            PostPublicationCaptureV3,
            verify_post_publication_capture_v3,
        )

        if (
            type(self) is not PublicCheckpointCaptureService
            or type(observation_repository) is not PublicReceiptPostReadRepository
            or observation_repository.witness_repository is not self._repository
        ):
            raise PublicWitnessError("source_capture_services_invalid")
        database_pins = _source_database_pins(self._repository, observation_repository)
        owned_repository, owned_observation = _owned_source_repositories(database_pins)
        owned_pins = _source_database_pins(owned_repository, owned_observation)
        recorded = _verify(
            payload,
            expected_sha256=expected_sha256,
            contract_type=PostPublicationCaptureV3,
        )
        source = recorded.v2_capture
        if source.plan_sha256 != expected_plan_sha256:
            raise PublicWitnessError("source_capture_plan_pin_mismatch")
        verified = await verify_post_publication_capture_v3(
            payload,
            expected_sha256=expected_sha256,
            observation_repository=owned_observation,
            journal=self._journal,
            witness_repository=owned_repository,
            capture_id=source.capture_id,
            expected_plan_sha256=source.plan_sha256,
            expected_receipt_sha256=source.receipt_sha256,
            expected_journal_genesis_sha256=source.journal_genesis_sha256,
            expected_capture_checkpoint_sha256=source.capture_checkpoint_sha256,
            expected_witness_revision=source.witness_revision,
            expected_witness_record_sha256=source.witness_record_sha256,
        )
        _source_database_pins(self._repository, observation_repository, database_pins)
        _source_database_pins(owned_repository, owned_observation, owned_pins)
        return verified

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
    "NativeSourcePostReadCapture",
    "OwnedPostReadCapture",
    "PublicCheckpointCaptureService",
    "WitnessedPublicCapture",
)
