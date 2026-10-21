"""One native source attempt per fixed D/V batch; no research promotion.

The no-clobber claim is flushed and read back before the first async operation.
Interrupted directories are retained. Restart exposes a read-only reconciler,
never another writable owner. Windows has the same power-loss/directory-custody
limitations as the existing native publisher; this is not independent PIT proof.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock, get_ident
from typing import Literal
from weakref import WeakKeyDictionary

from pydantic import Field, field_validator, model_validator

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.mie.validation.contracts import Gate3Contract, Sha256, require_utc
from app.mie.validation.development_validation_source_plan_v1 import (
    checked_development_validation_source_plan,
)
from app.mie.validation.post_publication_availability_v3 import PostPublicationCaptureV3
from app.mie.validation.public_checkpoint_service import PublicCheckpointCaptureService
from app.trade_evidence.storage import (
    _posix_root,
    _root_path,
    _windows_native_path,
    _windows_root,
)

MAX_PLAN_BYTES = 4 * 1024 * 1024
MAX_RECORD_BYTES = 16 * 1024
MAX_CAPTURE_BYTES = 16 * 1024 * 1024
_ISSUER = object()
_OWNERS = WeakKeyDictionary()
_LOCK = Lock()
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class SourceCollectionError(ValueError):
    """Fixed safe codes only; no source/DB response or path interpolation."""


def _digest(payload):
    return hashlib.sha256(payload).hexdigest()


def _pin(value):
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise SourceCollectionError("source_collection_pin_invalid")
    return value


def _context():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        raise SourceCollectionError("source_collection_task_required") from None
    if task is None:
        raise SourceCollectionError("source_collection_task_required")
    return os.getpid(), get_ident(), task


def _root_context(root):
    return (_windows_root if os.name == "nt" else _posix_root)(_root_path(root))


def _root_identity(root):
    info = os.stat(
        _windows_native_path(root) if os.name == "nt" else root, follow_symlinks=False
    )
    return info.st_dev, info.st_ino


def _record(payload, contract):
    result = contract.model_validate_json(payload)
    if result.canonical_json_bytes() != payload:
        raise SourceCollectionError("source_collection_noncanonical_record")
    return result


class SourceCollectionIdentityV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_source_store.v1"] = (
        "ctcc.mie.devval_source_store.v1"
    )
    plan_sha256: Sha256
    journal_genesis_sha256: Sha256
    journal_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    root_device: int = Field(ge=0)
    root_inode: int = Field(ge=1)


class SourceOrdinalClaimV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_source_claim.v1"] = (
        "ctcc.mie.devval_source_claim.v1"
    )
    identity_sha256: Sha256
    ordinal: int = Field(ge=0, le=1023)
    capture_plan_sha256: Sha256


class SourceOrdinalReceiptV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_source_receipt.v1"] = (
        "ctcc.mie.devval_source_receipt.v1"
    )
    identity_sha256: Sha256
    ordinal: int = Field(ge=0, le=1023)
    capture_plan_sha256: Sha256
    status: Literal["CAPTURED_POST_READ_VERIFIED", "FAILED"]
    reason: Literal["captured", "capture_unresolved", "cancelled"]
    capture_v3_sha256: Sha256 | None = None
    capture_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    source_rows_sha256: Sha256 | None = None
    row_count: int | None = Field(default=None, ge=1, le=3000)
    available_at: datetime | None = None
    trusted_clock_verified: Literal[False] = False
    independently_protected: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    candidate_seal_eligible: Literal[False] = False
    holdout_accessed: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator(
        "trusted_clock_verified",
        "independently_protected",
        "predictive_oos_eligible",
        "candidate_seal_eligible",
        "holdout_accessed",
        "execution_authority",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("source receipt authority forbidden")
        return value

    @field_validator("available_at")
    @classmethod
    def utc(cls, value):
        return None if value is None else require_utc(value, "available_at")

    @model_validator(mode="after")
    def outcome(self):
        fields = (
            self.capture_v3_sha256,
            self.capture_id,
            self.source_rows_sha256,
            self.row_count,
            self.available_at,
        )
        if self.status == "CAPTURED_POST_READ_VERIFIED":
            if self.reason != "captured" or any(value is None for value in fields):
                raise ValueError("captured source receipt is incomplete")
        elif self.reason == "captured" or any(value is not None for value in fields):
            raise ValueError("failed source receipt has a completion claim")
        return self


class SourceCollectionReadOnly:
    """Local readback plus optional original-source replay; never a capture API."""

    __slots__ = ("_identity", "_plan", "_root")

    def __init__(self, root, plan, identity):
        self._root, self._plan, self._identity = root, plan, identity

    @classmethod
    def open(cls, *, root, plan, expected_plan_sha256):
        if type(root) is not type(Path()):
            raise SourceCollectionError("source_collection_native_root_required")
        _pin(expected_plan_sha256)
        selected = checked_development_validation_source_plan(
            plan, expected_sha256=expected_plan_sha256
        )
        try:
            with _root_context(root) as directory:
                identity = _record(
                    directory.read("identity.json", MAX_RECORD_BYTES),
                    SourceCollectionIdentityV1,
                )
                if identity.plan_sha256 != expected_plan_sha256:
                    raise SourceCollectionError("source_collection_identity_mismatch")
                reader = cls(root, selected, identity)
                for ordinal in reader._inventory(directory):
                    reader._read_slot(directory, ordinal)
                return reader
        except Exception:  # noqa: BLE001 - Native path/DB details never enter receipts.
            raise SourceCollectionError(
                "source_collection_readback_unavailable"
            ) from None

    def _inventory(self, directory):
        if _root_identity(self._root) != (
            self._identity.root_device,
            self._identity.root_inode,
        ) or directory.read("identity.json", MAX_RECORD_BYTES) != (
            self._identity.canonical_json_bytes()
        ):
            raise SourceCollectionError("source_collection_identity_mismatch")
        if (
            directory.read("plan.json", MAX_PLAN_BYTES)
            != self._plan.canonical_json_bytes()
        ):
            raise SourceCollectionError("source_collection_plan_changed")
        names = set(directory.names())
        fixed = {"identity.json", "plan.json"}
        if not fixed <= names:
            raise SourceCollectionError("source_collection_inventory_unresolved")
        ordinals = []
        for name in names - fixed:
            if not (
                len(name) == 14
                and name.startswith("batch-")
                and name[6:].isascii()
                and name[6:].isdigit()
                and int(name[6:]) < len(self._plan.coordinates)
            ):
                raise SourceCollectionError("source_collection_inventory_unresolved")
            ordinals.append(int(name[6:]))
        if tuple(sorted(ordinals)) != tuple(range(len(ordinals))):
            raise SourceCollectionError("source_collection_ordinal_gap")
        return tuple(sorted(ordinals))

    def _name(self, ordinal):
        self._plan.coordinate(ordinal)
        return f"batch-{ordinal:08d}"

    def _claim(self, ordinal):
        return SourceOrdinalClaimV1(
            identity_sha256=self._identity.canonical_sha256(),
            ordinal=ordinal,
            capture_plan_sha256=self._plan.capture_plan(ordinal).canonical_sha256(),
        )

    def _read_slot(self, directory, ordinal):
        with directory.child(self._name(ordinal)) as child:
            if _record(
                child.read("claim.json", MAX_RECORD_BYTES), SourceOrdinalClaimV1
            ) != self._claim(ordinal):
                raise SourceCollectionError("source_collection_claim_changed")
            receipt = _record(
                child.read("receipt.json", MAX_RECORD_BYTES), SourceOrdinalReceiptV1
            )
            if (
                receipt.identity_sha256 != self._identity.canonical_sha256()
                or receipt.ordinal != ordinal
                or receipt.capture_plan_sha256
                != self._claim(ordinal).capture_plan_sha256
                or child.read("receipt.sha256", 65)
                != receipt.canonical_sha256().encode("ascii") + b"\n"
            ):
                raise SourceCollectionError("source_collection_receipt_changed")
            expected = {"claim.json", "receipt.json", "receipt.sha256"}
            if receipt.status == "CAPTURED_POST_READ_VERIFIED":
                expected.add("capture-v3.json")
                payload = child.read("capture-v3.json", MAX_CAPTURE_BYTES)
                if _digest(payload) != receipt.capture_v3_sha256:
                    raise SourceCollectionError("source_collection_capture_changed")
                capture = _record(payload, PostPublicationCaptureV3)
                if (
                    capture.v2_capture.plan_sha256 != receipt.capture_plan_sha256
                    or capture.v2_capture.capture_id != receipt.capture_id
                    or capture.v2_capture.source_rows_sha256
                    != receipt.source_rows_sha256
                    or len(capture.v2_capture.rows) != receipt.row_count
                    or receipt.row_count
                    != self._plan.capture_plan(ordinal).expected_rows
                    or capture.available_at != receipt.available_at
                    or capture.v2_capture.journal_genesis_sha256
                    != self._identity.journal_genesis_sha256
                ):
                    raise SourceCollectionError("source_collection_capture_changed")
            if set(child.names()) != expected:
                raise SourceCollectionError("source_collection_slot_unresolved")
            return receipt

    def read_ordinal(self, ordinal, *, expected_receipt_sha256):
        """No local hash or saved DTO is treated as a native acquisition lease."""
        _pin(expected_receipt_sha256)
        try:
            with _root_context(self._root) as directory:
                ordinals = self._inventory(directory)
                if ordinal not in ordinals:
                    raise SourceCollectionError("source_collection_ordinal_unclaimed")
                receipt = self._read_slot(directory, ordinal)
                if receipt.canonical_sha256() != expected_receipt_sha256:
                    raise SourceCollectionError(
                        "source_collection_receipt_pin_mismatch"
                    )
                return receipt
        except Exception:  # noqa: BLE001 - Retain an unresolved, redacted readback.
            raise SourceCollectionError(
                "source_collection_ordinal_unresolved"
            ) from None

    async def reconcile_ordinal(
        self,
        ordinal,
        *,
        expected_receipt_sha256,
        capture_service,
        observation_repository,
    ):
        """Read original V3 sources only. No mint, append, retry or refill."""
        receipt = self.read_ordinal(
            ordinal, expected_receipt_sha256=expected_receipt_sha256
        )
        if receipt.status != "CAPTURED_POST_READ_VERIFIED":
            return receipt
        if (
            type(capture_service) is not PublicCheckpointCaptureService
            or capture_service.genesis_sha256 != self._identity.journal_genesis_sha256
            or capture_service.journal_id != self._identity.journal_id
        ):
            raise SourceCollectionError("source_collection_service_changed")
        with _root_context(self._root) as directory:
            self._inventory(directory)
            with directory.child(self._name(ordinal)) as child:
                payload = child.read("capture-v3.json", MAX_CAPTURE_BYTES)
        verified = await capture_service.replay_source_post_read_v3(
            payload,
            expected_sha256=receipt.capture_v3_sha256,
            expected_plan_sha256=receipt.capture_plan_sha256,
            observation_repository=observation_repository,
        )
        if verified.canonical_json_bytes() != payload:
            raise SourceCollectionError("source_collection_source_changed")
        return self.read_ordinal(
            ordinal, expected_receipt_sha256=expected_receipt_sha256
        )


@dataclass(slots=True)
class _OwnerState:
    reader: SourceCollectionReadOnly
    service: PublicCheckpointCaptureService
    observation: PublicReceiptPostReadRepository
    context: tuple
    busy: bool = False
    stopped: bool = False


class DevelopmentValidationSourceOwnerV1:
    """Fresh empty-root owner. There is deliberately no writable resume method."""

    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            raise SourceCollectionError("source_collection_owner_required")

    def __copy__(self):
        raise SourceCollectionError("source_collection_owner_not_transferable")

    def __deepcopy__(self, memo):
        raise SourceCollectionError("source_collection_owner_not_transferable")

    def __reduce_ex__(self, protocol):
        raise SourceCollectionError("source_collection_owner_not_transferable")

    def __reduce__(self):
        raise SourceCollectionError("source_collection_owner_not_transferable")

    @classmethod
    def initialize(
        cls,
        *,
        root,
        plan,
        expected_plan_sha256,
        capture_service,
        observation_repository,
    ):
        if (
            cls is not DevelopmentValidationSourceOwnerV1
            or type(root) is not type(Path())
            or type(capture_service) is not PublicCheckpointCaptureService
            or type(observation_repository) is not PublicReceiptPostReadRepository
        ):
            raise SourceCollectionError("source_collection_services_invalid")
        _pin(expected_plan_sha256)
        selected = checked_development_validation_source_plan(
            plan, expected_sha256=expected_plan_sha256
        )
        context = _context()
        try:
            with _root_context(root) as directory:
                if directory.names():
                    raise SourceCollectionError("source_collection_root_not_empty")
                device, inode = _root_identity(root)
                identity = SourceCollectionIdentityV1(
                    plan_sha256=expected_plan_sha256,
                    journal_genesis_sha256=capture_service.genesis_sha256,
                    journal_id=capture_service.journal_id,
                    root_device=device,
                    root_inode=inode,
                )
                payload = selected.canonical_json_bytes()
                if len(payload) > MAX_PLAN_BYTES:
                    raise SourceCollectionError("source_collection_plan_budget")
                directory.publish("plan.json", payload)
                if directory.read("plan.json", MAX_PLAN_BYTES) != payload:
                    raise SourceCollectionError(
                        "source_collection_plan_readback_failed"
                    )
                payload = identity.canonical_json_bytes()
                directory.publish("identity.json", payload)
                if directory.read("identity.json", MAX_RECORD_BYTES) != payload:
                    raise SourceCollectionError(
                        "source_collection_identity_readback_failed"
                    )
            reader = SourceCollectionReadOnly.open(
                root=root, plan=selected, expected_plan_sha256=expected_plan_sha256
            )
            result = cls(_ISSUER)
            with _LOCK:
                _OWNERS[result] = _OwnerState(
                    reader, capture_service, observation_repository, context
                )
            return result
        except Exception:  # noqa: BLE001 - Preserve partial root; redact native errors.
            raise SourceCollectionError(
                "source_collection_initialization_uncertain"
            ) from None

    def _owned(self):
        if type(self) is not DevelopmentValidationSourceOwnerV1:
            raise SourceCollectionError("source_collection_owner_required")
        with _LOCK:
            state = _OWNERS.get(self)
            if state is None or state.context != _context():
                raise SourceCollectionError("source_collection_owner_required")
            return state

    @staticmethod
    def _publish(reader, ordinal, receipt, payload=None):
        with _root_context(reader._root) as directory:
            reader._inventory(directory)
            with directory.child(reader._name(ordinal)) as child:
                if set(child.names()) != {"claim.json"}:
                    raise SourceCollectionError(
                        "source_collection_publication_unresolved"
                    )
                if payload is not None:
                    if not 0 < len(payload) <= MAX_CAPTURE_BYTES:
                        raise SourceCollectionError("source_collection_capture_budget")
                    child.publish("capture-v3.json", payload)
                    if child.read("capture-v3.json", MAX_CAPTURE_BYTES) != payload:
                        raise SourceCollectionError(
                            "source_collection_capture_readback_failed"
                        )
                encoded = receipt.canonical_json_bytes()
                child.publish("receipt.json", encoded)
                if child.read("receipt.json", MAX_RECORD_BYTES) != encoded:
                    raise SourceCollectionError(
                        "source_collection_receipt_readback_failed"
                    )
                child.publish(
                    "receipt.sha256", receipt.canonical_sha256().encode() + b"\n"
                )
            if reader._read_slot(directory, ordinal) != receipt:
                raise SourceCollectionError("source_collection_final_readback_failed")
        return receipt

    async def capture_once(self, *, ordinal, expected_previous_receipt_sha256s):
        """Claim+readback before every source/DB await; one attempt, no retry."""
        state = self._owned()
        with _LOCK:
            if state.busy or state.stopped:
                raise SourceCollectionError("source_collection_owner_unavailable")
            state.busy = True
        reader = state.reader
        try:
            coordinate = reader._plan.coordinate(ordinal)
            if (
                type(expected_previous_receipt_sha256s) is not tuple
                or len(expected_previous_receipt_sha256s) != ordinal
                or any(
                    type(pin) is not str or _SHA256.fullmatch(pin) is None
                    for pin in expected_previous_receipt_sha256s
                )
            ):
                raise SourceCollectionError("source_collection_prefix_invalid")
            with _root_context(reader._root) as directory:
                if reader._inventory(directory) != tuple(range(ordinal)):
                    raise SourceCollectionError(
                        "source_collection_ordinal_already_claimed"
                    )
                for previous, pin in enumerate(expected_previous_receipt_sha256s):
                    old = reader._read_slot(directory, previous)
                    if (
                        old.status != "CAPTURED_POST_READ_VERIFIED"
                        or old.canonical_sha256() != pin
                    ):
                        raise SourceCollectionError("source_collection_prefix_invalid")
                directory.mkdir(reader._name(ordinal))
                with directory.child(reader._name(ordinal)) as child:
                    claim = reader._claim(ordinal).canonical_json_bytes()
                    child.publish("claim.json", claim)
                    if child.read("claim.json", MAX_RECORD_BYTES) != claim:
                        raise SourceCollectionError(
                            "source_collection_claim_readback_failed"
                        )
            # Nothing above can perform source or database I/O.
            result = await state.service.capture_source_with_post_read_v3(
                plan=coordinate.capture_plan,
                expected_plan_sha256=coordinate.capture_plan_sha256,
                observation_repository=state.observation,
            )
            frozen = state.service.consume_source_post_read_v3(
                result, expected_plan_sha256=coordinate.capture_plan_sha256
            )
            self._owned()
            capture = frozen.contract
            receipt = SourceOrdinalReceiptV1(
                identity_sha256=reader._identity.canonical_sha256(),
                ordinal=ordinal,
                capture_plan_sha256=coordinate.capture_plan_sha256,
                status="CAPTURED_POST_READ_VERIFIED",
                reason="captured",
                capture_v3_sha256=frozen.sha256,
                capture_id=capture.v2_capture.capture_id,
                source_rows_sha256=capture.v2_capture.source_rows_sha256,
                row_count=len(capture.v2_capture.rows),
                available_at=capture.available_at,
            )
            return self._publish(reader, ordinal, receipt, frozen.payload)
        except (Exception, asyncio.CancelledError) as exc:
            state.stopped = True
            # A failed final publication is never overwritten by a failure row.
            # If the directory is still only a claim, retain a safe terminal tombstone.
            try:
                failed = SourceOrdinalReceiptV1(
                    identity_sha256=reader._identity.canonical_sha256(),
                    ordinal=ordinal,
                    capture_plan_sha256=reader._plan.capture_plan(
                        ordinal
                    ).canonical_sha256(),
                    status="FAILED",
                    reason="cancelled"
                    if isinstance(exc, asyncio.CancelledError)
                    else "capture_unresolved",
                )
                self._publish(reader, ordinal, failed)
            except Exception:  # noqa: BLE001, S110 - Keep original partial bytes; no unsafe exception logging.
                pass  # Preserve the original claim/partial files; no retry or cleanup.
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise SourceCollectionError(
                "source_collection_attempt_unresolved"
            ) from None
        finally:
            with _LOCK:
                state.busy = False


__all__ = (
    "DevelopmentValidationSourceOwnerV1",
    "SourceCollectionError",
    "SourceCollectionReadOnly",
    "SourceOrdinalReceiptV1",
)
