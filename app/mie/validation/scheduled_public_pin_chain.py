"""Separate, append-only pins for scheduled public-minute research receipts.

This local store is deliberately *not* an independent custodian. Its hashes
must be exported to a separately administered append-only service before any
Gate 3 availability claim can be reviewed. A changed or unpinned source claim
stops continuation; no receipt or source journal is repaired here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from app.mie.validation.artifact import SHA256_RE
from app.mie.validation.contracts import Gate3Claim, Gate3Contract, Sha256
from app.mie.validation.prospective_capture_schedule import (
    ProspectiveCaptureScheduleV1,
)
from app.mie.validation.public_checkpoint_service import PublicCheckpointCaptureService
from app.mie.validation.scheduled_public_minute import ScheduledPublicMinuteReceiptV2
from app.mie.validation.scheduled_public_minute_publication import (
    ScheduledMinuteReceiptStore,
    _root_context,
    _root_identity,
)
from app.public_market_source.public_market_receipts import (
    PublicRawReceiptV1,
    canonical,
)
from app.public_market_source.public_receipt_storage import (
    ControlledPublicReceiptJournal,
)

MAX_PIN_BYTES = 4096
_SUCCESS = "source_chain_replayed_custody_unproven"


class ScheduledPinChainError(ValueError):
    """A separate pin or original source cannot be replayed."""


class ScheduledPinIdentityV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.gate3.scheduled_pin_identity.v1"] = (
        "ctcc.mie.gate3.scheduled_pin_identity.v1"
    )
    seal_sha256: Sha256
    coordinate_plan_sha256: Sha256
    schedule_sha256: Sha256
    window_key: Sha256
    expected_rows: Literal[480] = 480
    pin_root_device: int = Field(ge=0)
    pin_root_inode: int = Field(ge=1)
    source_root_device: int = Field(ge=0)
    source_root_inode: int = Field(ge=1)
    custody_basis: Literal["separate_local_root_unverified"] = (
        "separate_local_root_unverified"
    )
    independently_protected: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False


class ScheduledReceiptPinV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.gate3.scheduled_receipt_pin.v1"] = (
        "ctcc.mie.gate3.scheduled_receipt_pin.v1"
    )
    identity_sha256: Sha256
    ordinal: int = Field(ge=0, lt=480)
    previous_pin_sha256: Sha256 | None
    plan_sha256: Sha256
    receipt_sha256: Sha256
    reason_code: str = Field(min_length=1, max_length=80)
    due_observed_utc_ns: int | None = Field(default=None, ge=0)
    source_capture_receipt_sha256: Sha256 | None = None
    source_last_page_sha256: Sha256 | None = None
    source_journal_checkpoint_sha256: Sha256 | None = None
    source_witness_record_sha256: Sha256 | None = None
    source_post_read_v3_sha256: Sha256 | None = None
    status: Literal["INCOMPLETE"] = "INCOMPLETE"
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    independently_protected: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def source_shape(self) -> ScheduledReceiptPinV1:
        source = (
            self.source_capture_receipt_sha256,
            self.source_last_page_sha256,
            self.source_journal_checkpoint_sha256,
            self.source_witness_record_sha256,
            self.source_post_read_v3_sha256,
        )
        if self.reason_code == _SUCCESS:
            if any(item is None for item in source) or self.due_observed_utc_ns is None:
                raise ValueError("source-success pin lacks the original page chain")
        elif any(item is not None for item in source):
            raise ValueError("incomplete source cannot borrow source-success pins")
        return self


def _source_inventory(
    capture_service: PublicCheckpointCaptureService,
) -> dict[str, tuple[str, str, str]]:
    """Replay the owned raw journal once, including every page and cursor link."""
    if type(capture_service) is not PublicCheckpointCaptureService:
        raise ScheduledPinChainError("owned_public_capture_service_required")
    journal = capture_service._journal
    if type(journal) is not ControlledPublicReceiptJournal:
        raise ScheduledPinChainError("owned_public_journal_required")
    try:
        entries, _ = journal.read_all()
    except Exception as exc:
        raise ScheduledPinChainError("original_public_journal_unavailable") from exc
    inventory: dict[str, tuple[str, str, str]] = {}
    for plan, receipt, _raw_files, entry in entries:
        if (
            entry["disposition"] != "accepted"
            or receipt.attempt_sha256 is None
            or not receipt.pages
            or receipt.capture_id in inventory
        ):
            raise ScheduledPinChainError("original_public_capture_unresolved")
        previous = None
        for data in receipt.pages:
            page = PublicRawReceiptV1.model_validate_json(canonical(data))
            if page.previous_page_sha256 != previous:
                raise ScheduledPinChainError("original_public_page_chain_changed")
            previous = page.canonical_sha256()
        assert previous is not None
        inventory[receipt.capture_id] = (
            receipt.canonical_sha256(),
            plan.canonical_sha256(),
            previous,
        )
    return inventory


def _exact_record(payload: bytes, contract_type):
    try:
        record = contract_type.model_validate_json(payload)
        if record.canonical_json_bytes() != payload:
            raise ValueError("noncanonical pin record")
        return record
    except (TypeError, ValueError) as exc:
        raise ScheduledPinChainError("scheduled_pin_record_unresolved") from exc


class ScheduledPublicPinChain:
    """A separate native root with no-clobber, hash-linked receipt pins."""

    __slots__ = ("_identity", "_root", "_schedule", "_source_root")

    def __init__(
        self,
        root: Path,
        source_root: Path,
        schedule: ProspectiveCaptureScheduleV1,
        identity: ScheduledPinIdentityV1,
    ) -> None:
        self._root = root
        self._source_root = source_root
        self._schedule = schedule
        self._identity = identity

    @classmethod
    def _inputs(
        cls,
        root: Path,
        source_root: Path,
        schedule: ProspectiveCaptureScheduleV1,
        *,
        expected_seal_sha256: str,
        expected_coordinate_plan_sha256: str,
        expected_schedule_sha256: str,
    ) -> tuple[ProspectiveCaptureScheduleV1, ScheduledPinIdentityV1]:
        if (
            type(root) is not type(Path())
            or type(source_root) is not type(Path())
            or not root.is_absolute()
            or not source_root.is_absolute()
            or type(schedule) is not ProspectiveCaptureScheduleV1
            or any(
                type(digest) is not str or SHA256_RE.fullmatch(digest) is None
                for digest in (
                    expected_seal_sha256,
                    expected_coordinate_plan_sha256,
                    expected_schedule_sha256,
                )
            )
        ):
            raise ScheduledPinChainError("scheduled_pin_inputs_invalid")
        coordinate = schedule.coordinate_plan
        if (
            coordinate.instrument_ids != ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
            or coordinate.expected_rows != 480
            or (coordinate.end_at - coordinate.start_at).total_seconds() != 14_400
            or schedule.canonical_sha256() != expected_schedule_sha256
            or schedule.preregistration_sha256 != expected_seal_sha256
            or schedule.coordinate_plan_sha256 != expected_coordinate_plan_sha256
        ):
            raise ScheduledPinChainError("scheduled_pin_four_hour_grid_invalid")
        try:
            pin_path = root.resolve(strict=True)
            source_path = source_root.resolve(strict=True)
            if (
                pin_path == source_path
                or pin_path in source_path.parents
                or source_path in pin_path.parents
            ):
                raise ScheduledPinChainError("scheduled_pin_roots_overlap")
            with _root_context(root), _root_context(source_root):
                pin_device, pin_inode = _root_identity(root)
                source_device, source_inode = _root_identity(source_root)
                if (pin_device, pin_inode) == (source_device, source_inode):
                    raise ScheduledPinChainError("scheduled_pin_roots_overlap")
        except ScheduledPinChainError:
            raise
        except Exception as exc:
            raise ScheduledPinChainError("scheduled_pin_roots_unavailable") from exc
        identity = ScheduledPinIdentityV1(
            seal_sha256=expected_seal_sha256,
            coordinate_plan_sha256=expected_coordinate_plan_sha256,
            schedule_sha256=expected_schedule_sha256,
            window_key=coordinate.window_key(),
            pin_root_device=pin_device,
            pin_root_inode=pin_inode,
            source_root_device=source_device,
            source_root_inode=source_inode,
        )
        return schedule, identity

    @classmethod
    def initialize(
        cls,
        root: Path,
        source_root: Path,
        schedule: ProspectiveCaptureScheduleV1,
        *,
        expected_seal_sha256: str,
        expected_coordinate_plan_sha256: str,
        expected_schedule_sha256: str,
    ) -> ScheduledPublicPinChain:
        checked, identity = cls._inputs(
            root,
            source_root,
            schedule,
            expected_seal_sha256=expected_seal_sha256,
            expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
            expected_schedule_sha256=expected_schedule_sha256,
        )
        store = cls(root, source_root, checked, identity)
        try:
            with _root_context(root) as directory:
                if directory.names():
                    raise ScheduledPinChainError("scheduled_pin_root_not_empty")
                payload = identity.canonical_json_bytes()
                directory.publish("identity.json", payload)
                if directory.read("identity.json", MAX_PIN_BYTES) != payload:
                    raise ScheduledPinChainError(
                        "scheduled_pin_identity_readback_mismatch"
                    )
        except ScheduledPinChainError:
            raise
        except Exception as exc:
            raise ScheduledPinChainError("scheduled_pin_initialize_uncertain") from exc
        return store

    @classmethod
    def resume(
        cls,
        root: Path,
        source_root: Path,
        schedule: ProspectiveCaptureScheduleV1,
        *,
        expected_seal_sha256: str,
        expected_coordinate_plan_sha256: str,
        expected_schedule_sha256: str,
    ) -> ScheduledPublicPinChain:
        checked, identity = cls._inputs(
            root,
            source_root,
            schedule,
            expected_seal_sha256=expected_seal_sha256,
            expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
            expected_schedule_sha256=expected_schedule_sha256,
        )
        result = cls(root, source_root, checked, identity)
        result._load()
        return result

    def _load_directory(self, directory) -> tuple[ScheduledReceiptPinV1, ...]:
        if _root_identity(self._root) != (
            self._identity.pin_root_device,
            self._identity.pin_root_inode,
        ) or _root_identity(self._source_root) != (
            self._identity.source_root_device,
            self._identity.source_root_inode,
        ):
            raise ScheduledPinChainError("scheduled_pin_root_identity_changed")
        if (
            _exact_record(
                directory.read("identity.json", MAX_PIN_BYTES),
                ScheduledPinIdentityV1,
            )
            != self._identity
        ):
            raise ScheduledPinChainError("scheduled_pin_identity_changed")
        names = set(directory.names())
        if "identity.json" not in names:
            raise ScheduledPinChainError("scheduled_pin_identity_missing")
        count = len(names) - 1
        if count > 480 or names != {
            "identity.json",
            *(f"pin-{ordinal:08d}.json" for ordinal in range(count)),
        }:
            raise ScheduledPinChainError("scheduled_pin_inventory_unresolved")
        pins = []
        previous = None
        for ordinal in range(count):
            pin = _exact_record(
                directory.read(f"pin-{ordinal:08d}.json", MAX_PIN_BYTES),
                ScheduledReceiptPinV1,
            )
            if (
                pin.identity_sha256 != self._identity.canonical_sha256()
                or pin.ordinal != ordinal
                or pin.previous_pin_sha256 != previous
                or pin.plan_sha256 != self._schedule.plans[ordinal].canonical_sha256()
                or (ordinal < count - 1 and pin.reason_code != _SUCCESS)
            ):
                raise ScheduledPinChainError("scheduled_pin_chain_changed")
            previous = pin.canonical_sha256()
            pins.append(pin)
        return tuple(pins)

    def _load(self) -> tuple[ScheduledReceiptPinV1, ...]:
        try:
            with _root_context(self._root) as directory:
                return self._load_directory(directory)
        except ScheduledPinChainError:
            raise
        except Exception as exc:
            raise ScheduledPinChainError("scheduled_pin_readback_unavailable") from exc

    def require_source_root(self, root: Path) -> None:
        """Bind a caller to the exact native source root named by this chain."""
        try:
            if type(root) is not type(Path()):
                raise ValueError("source root type")
            with _root_context(root):
                actual_path = root.resolve(strict=True)
                pinned_path = self._source_root.resolve(strict=True)
                actual_identity = _root_identity(root)
                pinned_identity = _root_identity(self._source_root)
            expected_identity = (
                self._identity.source_root_device,
                self._identity.source_root_inode,
            )
            if (
                actual_path != pinned_path
                or actual_identity != expected_identity
                or pinned_identity != expected_identity
            ):
                raise ValueError("source root differs")
        except Exception as exc:
            raise ScheduledPinChainError("scheduled_pin_source_root_changed") from exc

    def _require_source_store(self, source_store: ScheduledMinuteReceiptStore) -> None:
        if (
            type(source_store) is not ScheduledMinuteReceiptStore
            or source_store._identity.root_device != self._identity.source_root_device
            or source_store._identity.root_inode != self._identity.source_root_inode
        ):
            raise ScheduledPinChainError("scheduled_pin_source_store_changed")
        self.require_source_root(source_store._root)

    def read_prefix(
        self,
        *,
        source_store: ScheduledMinuteReceiptStore,
        capture_service: PublicCheckpointCaptureService,
    ) -> tuple[ScheduledReceiptPinV1, ...]:
        self._require_source_store(source_store)
        pins = self._load()
        identity, claims = source_store.inspect_inventory()
        if (
            identity.schedule_sha256 != self._identity.schedule_sha256
            or identity.seal_sha256 != self._identity.seal_sha256
            or claims != tuple(range(len(pins)))
        ):
            raise ScheduledPinChainError("scheduled_pin_source_claims_unresolved")
        self._replay_originals(
            pins, source_store=source_store, capture_service=capture_service
        )
        return pins

    def _replay_originals(
        self,
        pins: tuple[ScheduledReceiptPinV1, ...],
        *,
        source_store: ScheduledMinuteReceiptStore,
        capture_service: PublicCheckpointCaptureService,
    ) -> None:
        """Recheck every retained pin against its original source bytes."""
        inventory = (
            _source_inventory(capture_service)
            if any(pin.reason_code == _SUCCESS for pin in pins)
            else {}
        )
        for pin in pins:
            receipt = source_store.read_ordinal(
                pin.ordinal, expected_receipt_sha256=pin.receipt_sha256
            ).contract
            if type(receipt) is not ScheduledPublicMinuteReceiptV2 or not self._matches(
                pin, receipt, inventory
            ):
                raise ScheduledPinChainError("scheduled_pin_original_receipt_changed")

    @staticmethod
    def _matches(
        pin: ScheduledReceiptPinV1,
        receipt: ScheduledPublicMinuteReceiptV2,
        inventory: dict[str, tuple[str, str, str]],
    ) -> bool:
        if (
            pin.reason_code != receipt.reason_code
            or pin.due_observed_utc_ns != receipt.due_observed_utc_ns
            or pin.plan_sha256 != receipt.plan_sha256
        ):
            return False
        if receipt.reason_code != _SUCCESS:
            return True
        original = inventory.get(receipt.capture_id)
        return (
            original is not None
            and original
            == (
                receipt.capture_receipt_sha256,
                receipt.plan_sha256,
                pin.source_last_page_sha256,
            )
            and pin.source_capture_receipt_sha256 == receipt.capture_receipt_sha256
            and pin.source_journal_checkpoint_sha256
            == receipt.journal_checkpoint_sha256
            and pin.source_witness_record_sha256 == receipt.witness_record_sha256
            and pin.source_post_read_v3_sha256 == receipt.post_read_v3_sha256
        )

    def append(
        self,
        *,
        ordinal: int,
        expected_receipt_sha256: str,
        expected_previous_pin_sha256: str | None,
        source_store: ScheduledMinuteReceiptStore,
        capture_service: PublicCheckpointCaptureService,
    ) -> ScheduledReceiptPinV1:
        self._require_source_store(source_store)
        pins = self._load()
        if (
            type(ordinal) is not int
            or ordinal != len(pins)
            or ordinal >= 480
            or type(expected_receipt_sha256) is not str
            or SHA256_RE.fullmatch(expected_receipt_sha256) is None
            or expected_previous_pin_sha256
            != (None if not pins else pins[-1].canonical_sha256())
            or (pins and pins[-1].reason_code != _SUCCESS)
        ):
            raise ScheduledPinChainError("scheduled_pin_append_sequence_invalid")
        identity, claims = source_store.inspect_inventory()
        if (
            identity.schedule_sha256 != self._identity.schedule_sha256
            or identity.seal_sha256 != self._identity.seal_sha256
            or claims != tuple(range(ordinal + 1))
        ):
            raise ScheduledPinChainError("scheduled_pin_source_claims_unresolved")
        # The current ordinal has been claimed, so read_prefix's exact
        # preclaim inventory check cannot be used here. Replay the prior
        # originals explicitly before publishing a new pin.
        self._replay_originals(
            pins, source_store=source_store, capture_service=capture_service
        )
        receipt = source_store.read_ordinal(
            ordinal, expected_receipt_sha256=expected_receipt_sha256
        ).contract
        if type(receipt) is not ScheduledPublicMinuteReceiptV2:
            raise ScheduledPinChainError("scheduled_pin_legacy_due_time_unverifiable")
        inventory = (
            _source_inventory(capture_service)
            if receipt.reason_code == _SUCCESS
            else {}
        )
        original = inventory.get(receipt.capture_id) if inventory else None
        if receipt.reason_code == _SUCCESS and (
            original is None
            or original[:2] != (receipt.capture_receipt_sha256, receipt.plan_sha256)
        ):
            raise ScheduledPinChainError(
                "scheduled_pin_original_page_chain_unavailable"
            )
        source_success = receipt.reason_code == _SUCCESS
        pin = ScheduledReceiptPinV1(
            identity_sha256=self._identity.canonical_sha256(),
            ordinal=ordinal,
            previous_pin_sha256=expected_previous_pin_sha256,
            plan_sha256=receipt.plan_sha256,
            receipt_sha256=expected_receipt_sha256,
            reason_code=receipt.reason_code,
            due_observed_utc_ns=receipt.due_observed_utc_ns,
            source_capture_receipt_sha256=(
                receipt.capture_receipt_sha256 if source_success else None
            ),
            source_last_page_sha256=original[2] if source_success else None,
            source_journal_checkpoint_sha256=(
                receipt.journal_checkpoint_sha256 if source_success else None
            ),
            source_witness_record_sha256=(
                receipt.witness_record_sha256 if source_success else None
            ),
            source_post_read_v3_sha256=(
                receipt.post_read_v3_sha256 if source_success else None
            ),
        )
        payload = pin.canonical_json_bytes()
        if len(payload) > MAX_PIN_BYTES:
            raise ScheduledPinChainError("scheduled_pin_payload_too_large")
        try:
            with _root_context(self._root) as directory:
                # Recheck under the native root lock before the irreversible write.
                if self._load_directory(directory) != pins:
                    raise ScheduledPinChainError("scheduled_pin_prefix_changed")
                name = f"pin-{ordinal:08d}.json"
                directory.publish(name, payload)
                if directory.read(name, MAX_PIN_BYTES) != payload:
                    raise ScheduledPinChainError(
                        "scheduled_pin_append_readback_changed"
                    )
        except ScheduledPinChainError:
            raise
        except Exception as exc:
            raise ScheduledPinChainError("scheduled_pin_append_uncertain") from exc
        if self.read_prefix(
            source_store=source_store, capture_service=capture_service
        ) != (*pins, pin):
            raise ScheduledPinChainError("scheduled_pin_chain_readback_changed")
        return pin


__all__ = (
    "ScheduledPinChainError",
    "ScheduledPinIdentityV1",
    "ScheduledPublicPinChain",
    "ScheduledReceiptPinV1",
)
