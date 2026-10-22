"""Fresh-root finite source/availability owner; restart is read-only, no refill."""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Literal
from weakref import WeakKeyDictionary

from pydantic import Field, StrictInt

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.domain.source_primitives import canonical, decode, sha
from app.mie.validation.contracts import Gate3Contract, Sha256
from app.mie.validation.development_validation_source_collection_v1 import (
    MAX_PLAN_BYTES,
    MAX_RECORD_BYTES,
    SourceCollectionError,
    _context,
    _pin,
    _record,
    _root_context,
    _root_identity,
)
from app.mie.validation.development_validation_source_plan_v1 import (
    checked_development_validation_source_plan,
)
from app.mie.validation.post_publication_availability_v3 import PostPublicationCaptureV3
from app.mie.validation.post_publication_availability_v6 import (
    ACQUISITION_BUDGET_SECONDS,
    AvailabilityContractV6,
)
from app.mie.validation.public_checkpoint_service import (
    ControlledPublicReceiptJournal,
    PublicCheckpointCaptureService,
)

_ISSUER = object()
_LOCK = Lock()
_OWNERS, _CLAIMS = WeakKeyDictionary(), WeakKeyDictionary()
_CLAIM_RECORDS = WeakKeyDictionary()
_RETAINED_NAMES = {"bundle-v6.json", "final-health.json", "evidence-v6.json"}
_MAX_RETAINED = 64 * 1024 * 1024


class CollectionIdentityV6(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_availability_store.v6"] = (
        "ctcc.mie.devval_availability_store.v6"
    )
    plan_sha256: Sha256
    journal_genesis_sha256: Sha256
    journal_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    root_device: StrictInt = Field(ge=0)
    root_inode: StrictInt = Field(gt=0)


class OrdinalClaimV6(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_availability_claim.v6"] = (
        "ctcc.mie.devval_availability_claim.v6"
    )
    identity_sha256: Sha256
    ordinal: StrictInt = Field(ge=0, le=1023)
    capture_plan_sha256: Sha256


class RetainedFileV6(Gate3Contract):
    name: Literal["bundle-v6.json", "final-health.json", "evidence-v6.json"]
    sha256: Sha256


class ForensicFileV6(Gate3Contract):
    name: Literal[
        "attempt.json",
        "health-before.raw",
        "sample.json",
        "health-after.raw",
        "outcome.json",
    ]
    sha256: Sha256


class OrdinalReceiptV6(AvailabilityContractV6):
    schema_version: Literal["ctcc.mie.devval_availability_receipt.v6"] = (
        "ctcc.mie.devval_availability_receipt.v6"
    )
    claim_sha256: Sha256
    status: Literal["NATIVE_DURABLE_AVAILABLE", "FAILED_RETAINED"]
    reason: Literal["captured", "cancelled", "unresolved"]
    files: tuple[RetainedFileV6, ...]
    forensic_files: tuple[ForensicFileV6, ...]
    forensic_status: Literal[
        "not_started", "unresolved", "completed", "failed", "cancelled"
    ]
    stage1_forensic_sha256: Sha256 | None
    replay_grants_capability: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False


class PartialForensicReadbackV6(AvailabilityContractV6):
    """Read-only file observations; no receipt repair, lease, retry or authority."""

    schema_version: Literal["ctcc.mie.devval_partial_forensic_readback.v6"] = (
        "ctcc.mie.devval_partial_forensic_readback.v6"
    )
    claim_sha256: Sha256
    ordinal: StrictInt = Field(ge=0, le=1023)
    files: tuple[RetainedFileV6, ...]
    forensic_files: tuple[ForensicFileV6, ...]
    forensic_status: Literal[
        "not_started", "unresolved", "completed", "failed", "cancelled"
    ]
    stage1_forensic_sha256: Sha256 | None
    published_receipt_sha256: Sha256 | None
    replay_grants_capability: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False


def inspect_partial_forensics_v6(*, root, plan, expected_plan_sha256, ordinal):
    """Inspect an already-claimed exact native root, including a late failed publish."""
    try:
        if type(root) is not type(Path()) or type(ordinal) is not int:
            raise SourceCollectionError("availability_partial_read_invalid")
        plan = checked_development_validation_source_plan(
            plan, expected_sha256=_pin(expected_plan_sha256)
        )
        with _root_context(root) as directory:
            identity = _record(
                directory.read("identity.json", MAX_RECORD_BYTES), CollectionIdentityV6
            )
            reader = CollectionReadOnlyV6(root, plan, identity)
            if ordinal not in reader.inventory(directory):
                raise SourceCollectionError("availability_ordinal_unclaimed")
            claim = reader.claim(ordinal)
            with directory.child(f"batch-{ordinal:08d}") as child:
                names = set(child.names())
                if not {"claim.json"} <= names or not names <= {
                    "claim.json",
                    "stage1",
                    "receipt.json",
                    "receipt.sha256",
                    *_RETAINED_NAMES,
                }:
                    raise SourceCollectionError(
                        "availability_partial_inventory_changed"
                    )
                if (
                    child.read("claim.json", MAX_RECORD_BYTES)
                    != claim.canonical_json_bytes()
                ):
                    raise SourceCollectionError("availability_claim_changed")
                files = tuple(
                    RetainedFileV6(
                        name=name, sha256=sha(child.read(name, _MAX_RETAINED))
                    )
                    for name in sorted(names & _RETAINED_NAMES)
                )
                raw = _read_forensic_directory(child)
                from app.mie.validation.native_stage1_forensics_v6 import (
                    replay_forensic_files_v6,
                )

                snapshot = replay_forensic_files_v6(
                    raw, expected_claim_sha256=claim.canonical_sha256()
                )
                published = None
                if {"receipt.json", "receipt.sha256"} <= names:
                    published = reader._read_slot(directory, ordinal).canonical_sha256()
                elif "receipt.json" in names:
                    record = _record(
                        child.read("receipt.json", MAX_RECORD_BYTES), OrdinalReceiptV6
                    )
                    if record.claim_sha256 != claim.canonical_sha256():
                        raise SourceCollectionError(
                            "availability_partial_receipt_changed"
                        )
                elif "receipt.sha256" in names:
                    pin = child.read("receipt.sha256", 65)
                    if (
                        len(pin) != 65
                        or pin[-1:] != b"\n"
                        or _pin(pin[:-1].decode()) != pin[:-1].decode()
                    ):
                        raise SourceCollectionError(
                            "availability_partial_receipt_changed"
                        )
                return PartialForensicReadbackV6(
                    claim_sha256=claim.canonical_sha256(),
                    ordinal=ordinal,
                    files=files,
                    forensic_files=tuple(
                        ForensicFileV6(name=name, sha256=sha(raw[name]))
                        for name in _forensic_names()
                        if name in raw
                    ),
                    forensic_status="not_started"
                    if "stage1" not in names
                    else "unresolved"
                    if snapshot is None
                    else snapshot.status,
                    stage1_forensic_sha256=None
                    if snapshot is None
                    else snapshot.sha256,
                    published_receipt_sha256=published,
                )
    except Exception:  # noqa: BLE001 - Never expose private paths, never write/refill a partial store.
        raise SourceCollectionError(
            "availability_partial_readback_unresolved"
        ) from None


class CollectionReadOnlyV6:
    def __init__(self, root, plan, identity):
        self.root, self.plan, self.identity = root, plan, identity

    @classmethod
    def open(cls, *, root, plan, expected_plan_sha256):
        try:
            return cls._open(
                root=root, plan=plan, expected_plan_sha256=expected_plan_sha256
            )
        except Exception:  # noqa: BLE001 - Private native paths never leave read-only replay.
            raise SourceCollectionError("availability_readback_unresolved") from None

    @classmethod
    def _open(cls, *, root, plan, expected_plan_sha256):
        if type(root) is not type(Path()):
            raise SourceCollectionError("availability_native_root_required")
        plan = checked_development_validation_source_plan(
            plan, expected_sha256=_pin(expected_plan_sha256)
        )
        with _root_context(root) as directory:
            identity = _record(
                directory.read("identity.json", MAX_RECORD_BYTES), CollectionIdentityV6
            )
            result = cls(root, plan, identity)
            for ordinal in result.inventory(directory):
                result._read_slot(directory, ordinal)
        return result

    def inventory(self, directory):
        if (
            self.identity.plan_sha256 != self.plan.canonical_sha256()
            or _root_identity(self.root)
            != (self.identity.root_device, self.identity.root_inode)
            or directory.read("identity.json", MAX_RECORD_BYTES)
            != self.identity.canonical_json_bytes()
            or directory.read("plan.json", MAX_PLAN_BYTES)
            != self.plan.canonical_json_bytes()
        ):
            raise SourceCollectionError("availability_collection_identity_changed")
        names = set(directory.names())
        if not {"identity.json", "plan.json"} <= names:
            raise SourceCollectionError("availability_collection_inventory_missing")
        slots = []
        for name in names - {"identity.json", "plan.json"}:
            if (
                len(name) != 14
                or not name.startswith("batch-")
                or not name[6:].isascii()
                or not name[6:].isdigit()
                or int(name[6:]) >= len(self.plan.coordinates)
            ):
                raise SourceCollectionError("availability_collection_inventory_invalid")
            slots.append(int(name[6:]))
        if tuple(sorted(slots)) != tuple(range(len(slots))):
            raise SourceCollectionError("availability_collection_gap")
        return tuple(sorted(slots))

    def claim(self, ordinal):
        return OrdinalClaimV6(
            identity_sha256=self.identity.canonical_sha256(),
            ordinal=ordinal,
            capture_plan_sha256=self.plan.coordinate(ordinal).capture_plan_sha256,
        )

    def read(self, ordinal, *, expected_receipt_sha256=None):
        try:
            with _root_context(self.root) as directory:
                return self._read_slot(
                    directory, ordinal, expected_receipt_sha256=expected_receipt_sha256
                )
        except Exception:  # noqa: BLE001 - Missing/partial stores cannot initialise or disclose paths.
            raise SourceCollectionError("availability_readback_unresolved") from None

    def _read_slot(self, directory, ordinal, *, expected_receipt_sha256=None):
        claim = self.claim(ordinal)
        if ordinal not in self.inventory(directory):
            raise SourceCollectionError("availability_ordinal_unclaimed")
        with directory.child(f"batch-{ordinal:08d}") as child:
            if (
                child.read("claim.json", MAX_RECORD_BYTES)
                != claim.canonical_json_bytes()
            ):
                raise SourceCollectionError("availability_claim_changed")
            receipt = _record(
                child.read("receipt.json", MAX_RECORD_BYTES), OrdinalReceiptV6
            )
            if (
                receipt.claim_sha256 != claim.canonical_sha256()
                or child.read("receipt.sha256", 65)
                != receipt.canonical_sha256().encode() + b"\n"
                or (
                    expected_receipt_sha256 is not None
                    and receipt.canonical_sha256() != _pin(expected_receipt_sha256)
                )
            ):
                raise SourceCollectionError("availability_receipt_changed")
            if len({f.name for f in receipt.files}) != len(receipt.files) or set(
                child.names()
            ) != {
                "claim.json",
                "receipt.json",
                "receipt.sha256",
                *(f.name for f in receipt.files),
                *({"stage1"} if receipt.forensic_status != "not_started" else set()),
            }:
                raise SourceCollectionError("availability_slot_unresolved")
            for item in receipt.files:
                if sha(child.read(item.name, _MAX_RETAINED)) != item.sha256:
                    raise SourceCollectionError("availability_retained_bytes_changed")
            raw_forensics = _read_forensic_directory(child)
            from app.mie.validation.native_stage1_forensics_v6 import (
                replay_forensic_files_v6,
            )

            snapshot = replay_forensic_files_v6(
                raw_forensics, expected_claim_sha256=claim.canonical_sha256()
            )
            if tuple(
                (item.name, item.sha256) for item in receipt.forensic_files
            ) != tuple(
                (name, sha(raw_forensics[name]))
                for name in _forensic_names()
                if name in raw_forensics
            ):
                raise SourceCollectionError("availability_forensic_files_changed")
            status = (
                "not_started"
                if "stage1" not in child.names()
                else "unresolved"
                if snapshot is None
                else snapshot.status
            )
            if receipt.forensic_status != status or receipt.stage1_forensic_sha256 != (
                None if snapshot is None else snapshot.sha256
            ):
                raise SourceCollectionError("availability_forensic_transcript_changed")
            required = {"bundle-v6.json", "final-health.json", "evidence-v6.json"}
            if receipt.status == "NATIVE_DURABLE_AVAILABLE" and (
                receipt.reason != "captured"
                or {f.name for f in receipt.files} != required
                or receipt.forensic_status != "completed"
                or receipt.stage1_forensic_sha256 is None
            ):
                raise SourceCollectionError("availability_completion_incomplete")
            if receipt.status == "NATIVE_DURABLE_AVAILABLE":
                _saved_lineage(child, claim, self.identity)
            if receipt.status == "FAILED_RETAINED" and receipt.reason == "captured":
                raise SourceCollectionError("availability_failed_claim_invalid")
            return receipt


@dataclass(slots=True)
class _State:
    reader: CollectionReadOnlyV6
    service: PublicCheckpointCaptureService
    observation: PublicReceiptPostReadRepository
    custody_factory: object
    context: tuple
    journal: object
    busy: bool = False
    stopped: bool = False


def _saved_lineage(child, claim, identity):
    """Audit saved bytes against the original finite root, never reissue a lease."""
    raw = child.read("bundle-v6.json", _MAX_RETAINED)
    data = decode(raw, _MAX_RETAINED)
    if canonical(data) != raw:
        raise SourceCollectionError("availability_saved_bundle_noncanonical")
    source = PostPublicationCaptureV3.model_validate_json(
        canonical(data["clock_v4"]["source_v3"])
    )
    genesis_files = [item for item in data["files"] if item["path"] == "genesis.json"]
    if len(genesis_files) != 1:
        raise SourceCollectionError("availability_saved_genesis_missing")
    genesis_raw = base64.b64decode(genesis_files[0]["raw_base64"], validate=True)
    genesis = decode(genesis_raw, _MAX_RETAINED)
    if (
        data["ordinal_claim_sha256"] != claim.canonical_sha256()
        or source.v2_capture.journal_genesis_sha256 != identity.journal_genesis_sha256
        or source.v2_capture.plan_sha256 != claim.capture_plan_sha256
        or sha(genesis_raw) != identity.journal_genesis_sha256
        or genesis_files[0]["raw_sha256"] != identity.journal_genesis_sha256
        or genesis["journal_id"] != identity.journal_id
    ):
        raise SourceCollectionError("availability_saved_original_root_changed")


class _ClaimLease:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            raise SourceCollectionError("availability_claim_owner_required")


@dataclass(frozen=True, slots=True, weakref_slot=True, eq=False)
class _ClaimState:
    state: _State
    claim: OrdinalClaimV6
    deadline_ns: int


def _check_service(state):
    identity = state.reader.identity
    if (
        type(state.service) is not PublicCheckpointCaptureService
        or type(state.journal) is not ControlledPublicReceiptJournal
        or state.service._journal is not state.journal
        or state.service.journal_id != identity.journal_id
        or state.service.genesis_sha256 != identity.journal_genesis_sha256
        or state.journal.checkpoint.genesis_sha256 != identity.journal_genesis_sha256
    ):
        raise SourceCollectionError("availability_original_source_changed")


def _check_claim_evidence(claim):
    if type(claim) is not _ClaimState or claim.state.context != _context():
        raise SourceCollectionError("availability_claim_owner_required")
    expected = _CLAIM_RECORDS.get(claim)
    if expected != (
        claim.claim.canonical_sha256(),
        claim.deadline_ns,
        claim.state.context,
    ):
        raise SourceCollectionError("availability_claim_owner_required")
    reader = claim.state.reader
    with _root_context(reader.root) as directory:
        reader.inventory(directory)
        with directory.child(f"batch-{claim.claim.ordinal:08d}") as child:
            if (
                child.read("claim.json", MAX_RECORD_BYTES)
                != claim.claim.canonical_json_bytes()
            ):
                raise SourceCollectionError("availability_claim_changed")
    return claim


def _check_claim(claim):
    claim = _check_claim_evidence(claim)
    if time.monotonic_ns() > claim.deadline_ns:
        raise SourceCollectionError("availability_claim_expired")
    return claim


def _forensic_names():
    from app.mie.validation.native_stage1_forensics_v6 import NAMES

    return NAMES


def _read_forensic_directory(child):
    if "stage1" not in child.names():
        return {}
    with child.child("stage1") as directory:
        names = set(directory.names())
        if not names <= set(_forensic_names()):
            raise SourceCollectionError("availability_forensic_inventory_changed")
        return {
            name: directory.read(name, 2 * 1024 * 1024)
            for name in _forensic_names()
            if name in names
        }


def _start_stage1(claim):
    _check_claim_evidence(claim)
    with (
        _root_context(claim.state.reader.root) as root,
        root.child(f"batch-{claim.claim.ordinal:08d}") as child,
    ):
        child.mkdir("stage1")


def _write_stage1(claim, name, raw):
    _check_claim_evidence(claim)
    if (
        name not in _forensic_names()
        or type(raw) is not bytes
        or not 0 < len(raw) <= 2 * 1024 * 1024
    ):
        raise SourceCollectionError("availability_forensic_retention_invalid")
    with (
        _root_context(claim.state.reader.root) as root,
        root.child(f"batch-{claim.claim.ordinal:08d}") as child,
        child.child("stage1") as directory,
    ):
        directory.publish(name, raw)
        if directory.read(name, 2 * 1024 * 1024) != raw:
            raise SourceCollectionError("availability_forensic_readback_failed")


def _read_stage1(claim):
    _check_claim_evidence(claim)
    with (
        _root_context(claim.state.reader.root) as root,
        root.child(f"batch-{claim.claim.ordinal:08d}") as child,
    ):
        return _read_forensic_directory(child)


def _consume_claim(lease):
    if type(lease) is not _ClaimLease:
        raise SourceCollectionError("availability_claim_owner_required")
    with _LOCK:
        claim = _CLAIMS.pop(lease, None)
    claim = _check_claim(claim)
    _check_service(claim.state)
    return claim


def _retain(claim, name, raw):
    _check_claim(claim)
    if (
        name not in _RETAINED_NAMES
        or type(raw) is not bytes
        or not 0 < len(raw) <= _MAX_RETAINED
    ):
        raise SourceCollectionError("availability_retention_invalid")
    with (
        _root_context(claim.state.reader.root) as directory,
        directory.child(f"batch-{claim.claim.ordinal:08d}") as child,
    ):
        child.publish(name, raw)
        if child.read(name, _MAX_RETAINED) != raw:
            raise SourceCollectionError("availability_retention_readback_failed")


def _publish(
    claim, *, success, cancelled=False, expected_files=None, expected_forensics=None
):
    _check_claim_evidence(claim)
    with (
        _root_context(claim.state.reader.root) as directory,
        directory.child(f"batch-{claim.claim.ordinal:08d}") as child,
    ):
        names = set(child.names())
        if not {"claim.json"} <= names or not names <= {
            "claim.json",
            "stage1",
            *_RETAINED_NAMES,
        }:
            raise SourceCollectionError("availability_publication_unresolved")
        files = tuple(
            RetainedFileV6(name=name, sha256=sha(child.read(name, _MAX_RETAINED)))
            for name in sorted(names - {"claim.json", "stage1"})
        )
        if success and (
            type(expected_files) is not tuple
            or tuple((f.name, f.sha256) for f in files) != expected_files
        ):
            raise SourceCollectionError("availability_owned_retained_files_changed")
        from app.mie.validation.native_stage1_forensics_v6 import (
            replay_forensic_files_v6,
        )

        forensic_raw = _read_forensic_directory(child)
        forensic_snapshot = replay_forensic_files_v6(
            forensic_raw, expected_claim_sha256=claim.claim.canonical_sha256()
        )
        forensic_pins = tuple(
            (name, sha(forensic_raw[name]))
            for name in _forensic_names()
            if name in forensic_raw
        )
        forensic_sha = None if forensic_snapshot is None else forensic_snapshot.sha256
        forensic_status = (
            "not_started"
            if "stage1" not in names
            else "unresolved"
            if forensic_snapshot is None
            else forensic_snapshot.status
        )
        if success and (
            forensic_status != "completed"
            or expected_forensics != (forensic_sha, forensic_pins)
        ):
            raise SourceCollectionError("availability_owned_forensics_changed")
        receipt = OrdinalReceiptV6(
            claim_sha256=claim.claim.canonical_sha256(),
            status="NATIVE_DURABLE_AVAILABLE" if success else "FAILED_RETAINED",
            reason="captured"
            if success
            else "cancelled"
            if cancelled
            else "unresolved",
            files=files,
            forensic_files=tuple(
                ForensicFileV6(name=name, sha256=pin) for name, pin in forensic_pins
            ),
            forensic_status=forensic_status,
            stage1_forensic_sha256=forensic_sha,
        )
        child.publish("receipt.json", receipt.canonical_json_bytes())
        if (
            child.read("receipt.json", MAX_RECORD_BYTES)
            != receipt.canonical_json_bytes()
        ):
            raise SourceCollectionError("availability_publication_readback_failed")
        child.publish("receipt.sha256", receipt.canonical_sha256().encode() + b"\n")
    return claim.state.reader.read(
        claim.claim.ordinal, expected_receipt_sha256=receipt.canonical_sha256()
    )


class DevelopmentValidationAvailabilityOwnerV6:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            raise SourceCollectionError("availability_owner_required")

    def __reduce_ex__(self, protocol):
        raise SourceCollectionError("availability_owner_not_transferable")

    @classmethod
    def initialize(
        cls,
        *,
        root,
        plan,
        expected_plan_sha256,
        capture_service,
        observation_repository,
        custody_session_factory,
    ):
        try:
            return cls._initialize(
                root=root,
                plan=plan,
                expected_plan_sha256=expected_plan_sha256,
                capture_service=capture_service,
                observation_repository=observation_repository,
                custody_session_factory=custody_session_factory,
            )
        except Exception:  # noqa: BLE001 - Preserve partial state, redact private native paths.
            raise SourceCollectionError(
                "availability_initialization_unresolved"
            ) from None

    @classmethod
    def _initialize(
        cls,
        *,
        root,
        plan,
        expected_plan_sha256,
        capture_service,
        observation_repository,
        custody_session_factory,
    ):
        if (
            cls is not DevelopmentValidationAvailabilityOwnerV6
            or type(root) is not type(Path())
            or type(capture_service) is not PublicCheckpointCaptureService
            or type(observation_repository) is not PublicReceiptPostReadRepository
        ):
            raise SourceCollectionError("availability_services_invalid")
        plan = checked_development_validation_source_plan(
            plan, expected_sha256=_pin(expected_plan_sha256)
        )
        context = _context()
        with _root_context(root) as directory:
            if directory.names():
                raise SourceCollectionError("availability_root_not_empty")
            device, inode = _root_identity(root)
            identity = CollectionIdentityV6(
                plan_sha256=plan.canonical_sha256(),
                journal_genesis_sha256=capture_service.genesis_sha256,
                journal_id=capture_service.journal_id,
                root_device=device,
                root_inode=inode,
            )
            for name, raw in (
                ("plan.json", plan.canonical_json_bytes()),
                ("identity.json", identity.canonical_json_bytes()),
            ):
                if len(raw) > MAX_PLAN_BYTES:
                    raise SourceCollectionError("availability_plan_budget")
                directory.publish(name, raw)
                if directory.read(name, MAX_PLAN_BYTES) != raw:
                    raise SourceCollectionError("availability_store_readback_failed")
        reader = CollectionReadOnlyV6.open(
            root=root, plan=plan, expected_plan_sha256=expected_plan_sha256
        )
        result = cls(_ISSUER)
        _OWNERS[result] = _State(
            reader,
            capture_service,
            observation_repository,
            custody_session_factory,
            context,
            capture_service._journal,
        )
        _check_service(_OWNERS[result])
        return result

    async def capture_once(self, *, ordinal, expected_previous_receipt_sha256s):
        from app.mie.validation.native_availability_v6 import (
            _complete,
            _expected_forensic_files,
            _expected_retained_files,
            _prepare,
            _publish_capability,
        )

        state = (
            _OWNERS.get(self)
            if type(self) is DevelopmentValidationAvailabilityOwnerV6
            else None
        )
        if state is None or state.context != _context() or state.busy or state.stopped:
            raise SourceCollectionError("availability_owner_unavailable")
        state.busy = True
        claim = None
        try:
            coordinate = state.reader.plan.coordinate(ordinal)
            if (
                type(expected_previous_receipt_sha256s) is not tuple
                or len(expected_previous_receipt_sha256s) != ordinal
            ):
                raise SourceCollectionError("availability_prefix_invalid")
            with _root_context(state.reader.root) as directory:
                if state.reader.inventory(directory) != tuple(range(ordinal)):
                    raise SourceCollectionError("availability_ordinal_already_claimed")
                for index, pin in enumerate(expected_previous_receipt_sha256s):
                    if (
                        state.reader._read_slot(
                            directory, index, expected_receipt_sha256=pin
                        ).status
                        != "NATIVE_DURABLE_AVAILABLE"
                    ):
                        raise SourceCollectionError("availability_prefix_failed")
                directory.mkdir(f"batch-{ordinal:08d}")
                with directory.child(f"batch-{ordinal:08d}") as child:
                    record = state.reader.claim(ordinal)
                    child.publish("claim.json", record.canonical_json_bytes())
                    if (
                        child.read("claim.json", MAX_RECORD_BYTES)
                        != record.canonical_json_bytes()
                    ):
                        raise SourceCollectionError(
                            "availability_claim_readback_failed"
                        )
            claim = _ClaimState(
                state,
                record,
                time.monotonic_ns() + ACQUISITION_BUDGET_SECONDS * 1_000_000_000,
            )
            _CLAIM_RECORDS[claim] = (
                record.canonical_sha256(),
                claim.deadline_ns,
                state.context,
            )
            lease = _ClaimLease(_ISSUER)
            _CLAIMS[lease] = claim
            # The exclusive durable claim/readback above precedes every await.
            async with asyncio.timeout(ACQUISITION_BUDGET_SECONDS):
                acquisition = await _prepare(lease)
                source = await state.service.capture_source_with_post_read_v3(
                    plan=coordinate.capture_plan,
                    expected_plan_sha256=coordinate.capture_plan_sha256,
                    observation_repository=state.observation,
                )
                completed = await _complete(acquisition, source)
            receipt = _publish(
                claim,
                success=True,
                expected_files=_expected_retained_files(completed),
                expected_forensics=_expected_forensic_files(completed),
            )
            return _publish_capability(completed, receipt.canonical_sha256())
        except (Exception, asyncio.CancelledError) as exc:
            state.stopped = True
            if claim is not None:
                try:
                    _publish(
                        claim,
                        success=False,
                        cancelled=isinstance(exc, asyncio.CancelledError),
                    )
                except Exception:  # noqa: BLE001, S110 - Preserve partial/late publication; never overwrite.
                    pass
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise SourceCollectionError("availability_attempt_unresolved") from None
        finally:
            state.busy = False
