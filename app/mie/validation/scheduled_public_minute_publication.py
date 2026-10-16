"""One-use local publication of a scheduled public-minute diagnostic.

The directory is a service-owned, pre-existing root for one sealed schedule.
Creating a minute directory is the irreversible local claim. A crash or an
uncertain source/database result leaves that ordinal unavailable for recapture.
The stored receipt is computational and incomplete, never a Gate 3 promotion.
Windows file flush/readback does not prove power-loss directory durability or
independent custody; these remain deployment-level prerequisites.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.mie.validation.artifact import SHA256_RE, FrozenGate3Artifact
from app.mie.validation.contracts import Gate3Contract, Identifier, Sha256
from app.mie.validation.prospective_capture_schedule import (
    MINUTE_NS,
    ProspectiveCaptureScheduleV1,
)
from app.mie.validation.scheduled_public_minute import (
    ScheduledPublicMinuteReceiptV1,
    ScheduledPublicMinuteReceiptV2,
    capture_scheduled_public_minute_once,
    verify_scheduled_public_minute_receipt,
)
from app.trade_evidence.storage import (
    _posix_root,
    _root_path,
    _windows_native_path,
    _windows_root,
)

MAX_RECORD_BYTES = 16 * 1024
MAX_ORDINAL = 4095


def _root_context(root: Path):
    return (_windows_root if os.name == "nt" else _posix_root)(_root_path(root))


def _root_identity(root: Path) -> tuple[int, int]:
    info = os.stat(
        _windows_native_path(root) if os.name == "nt" else root,
        follow_symlinks=False,
    )
    return info.st_dev, info.st_ino


class ScheduledMinutePublicationError(ValueError):
    """A local one-use receipt cannot be claimed, published or trusted."""


class ScheduledMinuteUnresolved(ScheduledMinutePublicationError):
    """A prior claim has no complete, verified local receipt."""


class ScheduledMinuteStoreIdentityV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.gate3.scheduled_minute_store.v1"] = (
        "ctcc.mie.gate3.scheduled_minute_store.v1"
    )
    seal_sha256: Sha256
    coordinate_plan_sha256: Sha256
    schedule_sha256: Sha256
    holdout_id: Identifier
    root_device: int = Field(ge=0)
    root_inode: int = Field(ge=1)


class ScheduledMinuteClaimV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.gate3.scheduled_minute_claim.v1"] = (
        "ctcc.mie.gate3.scheduled_minute_claim.v1"
    )
    store_identity_sha256: Sha256
    ordinal: int = Field(ge=0, le=MAX_ORDINAL)
    plan_sha256: Sha256


def _ordinal_name(ordinal: int, expected_rows: int) -> str:
    if type(ordinal) is not int or not 0 <= ordinal < min(expected_rows, 4096):
        raise ScheduledMinutePublicationError("scheduled ordinal is invalid")
    return f"minute-{ordinal:08d}"


def _record(payload: bytes, contract_type):
    try:
        result = contract_type.model_validate_json(payload)
        if result.canonical_json_bytes() != payload:
            raise ValueError("noncanonical record")
        return result
    except (TypeError, ValueError) as exc:
        raise ScheduledMinuteUnresolved(
            "scheduled minute record is unresolved"
        ) from exc


class ScheduledMinuteReceiptStore:
    """Dedicated one-schedule root; no retry, deletion, or trading authority."""

    __slots__ = ("_coordinate_plan", "_identity", "_root", "_schedule")

    def __init__(
        self,
        root: Path,
        schedule: ProspectiveCaptureScheduleV1,
        identity: ScheduledMinuteStoreIdentityV1,
    ) -> None:
        self._root = root
        self._coordinate_plan = schedule.coordinate_plan
        self._schedule = schedule
        self._identity = identity

    @classmethod
    def _inputs(
        cls,
        root: Path,
        *,
        schedule: ProspectiveCaptureScheduleV1,
        expected_coordinate_plan_sha256: str,
        expected_seal_sha256: str,
        expected_schedule_sha256: str,
    ) -> tuple[ProspectiveCaptureScheduleV1, ScheduledMinuteStoreIdentityV1]:
        if type(root) is not type(Path()) or not root.is_absolute():
            raise ScheduledMinutePublicationError("native absolute store root required")
        if type(schedule) is not ProspectiveCaptureScheduleV1:
            raise ScheduledMinutePublicationError("exact capture schedule required")
        if any(
            type(value) is not str or SHA256_RE.fullmatch(value) is None
            for value in (
                expected_coordinate_plan_sha256,
                expected_seal_sha256,
                expected_schedule_sha256,
            )
        ):
            raise ScheduledMinutePublicationError("store identity pin is invalid")
        checked = ProspectiveCaptureScheduleV1.model_validate(
            schedule.model_dump(mode="python")
        )
        if (
            checked.canonical_sha256() != expected_schedule_sha256
            or checked.preregistration_sha256 != expected_seal_sha256
            or checked.coordinate_plan_sha256 != expected_coordinate_plan_sha256
        ):
            raise ScheduledMinutePublicationError("capture schedule pin differs")
        try:
            with _root_context(root):
                device, inode = _root_identity(root)
        except Exception as exc:
            raise ScheduledMinutePublicationError("store root is unavailable") from exc
        identity = ScheduledMinuteStoreIdentityV1(
            seal_sha256=expected_seal_sha256,
            coordinate_plan_sha256=expected_coordinate_plan_sha256,
            schedule_sha256=expected_schedule_sha256,
            holdout_id=checked.coordinate_plan.holdout_id,
            root_device=device,
            root_inode=inode,
        )
        return checked, identity

    @classmethod
    def initialize(
        cls,
        root: Path,
        *,
        schedule: ProspectiveCaptureScheduleV1,
        expected_coordinate_plan_sha256: str,
        expected_seal_sha256: str,
        expected_schedule_sha256: str,
    ) -> ScheduledMinuteReceiptStore:
        """Publish the schedule identity once into an empty, owned directory."""
        checked_schedule, identity = cls._inputs(
            root,
            schedule=schedule,
            expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
            expected_seal_sha256=expected_seal_sha256,
            expected_schedule_sha256=expected_schedule_sha256,
        )
        store = cls(root, checked_schedule, identity)
        try:
            with _root_context(root) as directory:
                if directory.names() or _root_identity(root) != (
                    identity.root_device,
                    identity.root_inode,
                ):
                    raise ScheduledMinutePublicationError("store root is not empty")
                payload = identity.canonical_json_bytes()
                directory.publish("identity.json", payload)
                if directory.read("identity.json", MAX_RECORD_BYTES) != payload:
                    raise ScheduledMinutePublicationError(
                        "store identity readback differs"
                    )
        except ScheduledMinutePublicationError:
            raise
        except Exception as exc:
            raise ScheduledMinutePublicationError(
                "store initialization is uncertain"
            ) from exc
        return store

    @classmethod
    def resume(
        cls,
        root: Path,
        *,
        schedule: ProspectiveCaptureScheduleV1,
        expected_coordinate_plan_sha256: str,
        expected_seal_sha256: str,
        expected_schedule_sha256: str,
    ) -> ScheduledMinuteReceiptStore:
        """Reopen with external identity pins; never infer missing receipts."""
        checked_schedule, identity = cls._inputs(
            root,
            schedule=schedule,
            expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
            expected_seal_sha256=expected_seal_sha256,
            expected_schedule_sha256=expected_schedule_sha256,
        )
        store = cls(root, checked_schedule, identity)
        try:
            with _root_context(root) as directory:
                store._check_root(directory)
        except ScheduledMinutePublicationError:
            raise
        except Exception as exc:
            raise ScheduledMinutePublicationError(
                "store resume is unavailable"
            ) from exc
        return store

    def _check_root(self, directory) -> tuple[str, ...]:
        if _root_identity(self._root) != (
            self._identity.root_device,
            self._identity.root_inode,
        ):
            raise ScheduledMinutePublicationError("store root identity changed")
        payload = directory.read("identity.json", MAX_RECORD_BYTES)
        if _record(payload, ScheduledMinuteStoreIdentityV1) != self._identity:
            raise ScheduledMinutePublicationError("store identity differs")
        names = tuple(directory.names())
        for name in names:
            if name == "identity.json":
                continue
            if (
                len(name) != 15
                or not name.startswith("minute-")
                or not name[7:].isascii()
                or not name[7:].isdigit()
                or int(name[7:]) >= self._coordinate_plan.expected_rows
            ):
                raise ScheduledMinuteUnresolved("store inventory is unresolved")
        if len(set(names)) != len(names) or "identity.json" not in names:
            raise ScheduledMinuteUnresolved("store inventory is unresolved")
        return names

    def inspect_inventory(
        self,
    ) -> tuple[ScheduledMinuteStoreIdentityV1, tuple[int, ...]]:
        """Read the pinned root and every claimed ordinal without inferring pins.

        A caller must still supply an independently retained receipt digest to
        ``read_ordinal`` for each claim. This inventory is only a durable
        presence check; a directory name does not prove successful capture.
        """
        try:
            with _root_context(self._root) as directory:
                names = self._check_root(directory)
        except ScheduledMinutePublicationError:
            raise
        except Exception as exc:
            raise ScheduledMinuteUnresolved("store inventory is unresolved") from exc
        ordinals = tuple(
            sorted(int(name[7:]) for name in names if name != "identity.json")
        )
        return self._identity, ordinals

    def _claim_contract(self, ordinal: int) -> ScheduledMinuteClaimV1:
        return ScheduledMinuteClaimV1(
            store_identity_sha256=self._identity.canonical_sha256(),
            ordinal=ordinal,
            plan_sha256=self._schedule.plans[ordinal].canonical_sha256(),
        )

    def _read_claim(self, child, ordinal: int) -> None:
        claim = _record(
            child.read("claim.json", MAX_RECORD_BYTES), ScheduledMinuteClaimV1
        )
        if claim != self._claim_contract(ordinal):
            raise ScheduledMinuteUnresolved("scheduled minute claim differs")

    def _check_due_minute(
        self, ordinal: int, receipt: ScheduledPublicMinuteReceiptV1
    ) -> None:
        if receipt.reason_code != "source_chain_replayed_custody_unproven":
            return
        # V1 retained only microsecond display time and cannot replay the
        # original half-open nanosecond decision. Preserve its bytes for audit,
        # but never use a V1 success to continue a sealed window.
        if type(receipt) is not ScheduledPublicMinuteReceiptV2:
            raise ScheduledMinuteUnresolved(
                "legacy scheduled minute lacks exact replay time"
            )
        due_ns = receipt.due_observed_utc_ns
        end_ns = self._schedule.plans[ordinal].end_ns
        if due_ns is None or not end_ns <= due_ns < end_ns + MINUTE_NS:
            raise ScheduledMinuteUnresolved(
                "scheduled minute replay time differs from sealed plan"
            )

    def _read_slot(self, directory, ordinal: int) -> FrozenGate3Artifact:
        name = _ordinal_name(ordinal, self._coordinate_plan.expected_rows)
        with directory.child(name) as child:
            names = set(child.names())
            if "claim.json" not in names:
                raise ScheduledMinuteUnresolved("scheduled minute claim is unresolved")
            self._read_claim(child, ordinal)
            if names != {"claim.json", "receipt.json", "receipt.sha256"}:
                raise ScheduledMinuteUnresolved(
                    "scheduled minute receipt is unresolved"
                )
            payload = child.read("receipt.json", MAX_RECORD_BYTES)
            marker = child.read("receipt.sha256", 65)
            digest = hashlib.sha256(payload).hexdigest()
            if marker != digest.encode("ascii") + b"\n":
                raise ScheduledMinuteUnresolved("scheduled minute digest is unresolved")
            try:
                receipt = verify_scheduled_public_minute_receipt(
                    payload, expected_sha256=digest
                )
            except ValueError as exc:
                raise ScheduledMinuteUnresolved(
                    "scheduled minute receipt is unresolved"
                ) from exc
            if (
                receipt.seal_sha256 != self._identity.seal_sha256
                or receipt.coordinate_plan_sha256
                != self._identity.coordinate_plan_sha256
                or receipt.schedule_sha256 != self._identity.schedule_sha256
                or receipt.holdout_id != self._identity.holdout_id
                or receipt.ordinal != ordinal
                or receipt.plan_sha256 != self._claim_contract(ordinal).plan_sha256
            ):
                raise ScheduledMinuteUnresolved("scheduled minute receipt differs")
            self._check_due_minute(ordinal, receipt)
            return FrozenGate3Artifact(payload=payload, sha256=digest, contract=receipt)

    def _claim(
        self,
        ordinal: int,
        *,
        expected_previous_receipt_sha256s: tuple[str, ...],
    ) -> None:
        name = _ordinal_name(ordinal, self._coordinate_plan.expected_rows)
        if (
            type(expected_previous_receipt_sha256s) is not tuple
            or len(expected_previous_receipt_sha256s) != ordinal
            or any(
                type(digest) is not str or SHA256_RE.fullmatch(digest) is None
                for digest in expected_previous_receipt_sha256s
            )
        ):
            raise ScheduledMinutePublicationError(
                "scheduled minute previous receipt pins are invalid"
            )
        try:
            with _root_context(self._root) as directory:
                names = self._check_root(directory)
                existing_ordinals = tuple(
                    sorted(
                        int(existing[7:])
                        for existing in names
                        if existing != "identity.json"
                    )
                )
                if name in names:
                    raise ScheduledMinutePublicationError(
                        "scheduled minute ordinal already claimed"
                    )
                if existing_ordinals != tuple(range(ordinal)):
                    raise ScheduledMinuteUnresolved(
                        "scheduled minute sequence is unresolved"
                    )
                for existing in names:
                    if existing == "identity.json":
                        continue
                    existing_ordinal = int(existing[7:])
                    checked = self._read_slot(directory, existing_ordinal)
                    if (
                        checked.sha256
                        != expected_previous_receipt_sha256s[existing_ordinal]
                        or checked.contract.reason_code
                        != "source_chain_replayed_custody_unproven"
                    ):
                        raise ScheduledMinuteUnresolved(
                            "scheduled minute prior source is unresolved"
                        )
                directory.mkdir(name)
                with directory.child(name) as child:
                    payload = self._claim_contract(ordinal).canonical_json_bytes()
                    child.publish("claim.json", payload)
                    if child.read("claim.json", MAX_RECORD_BYTES) != payload:
                        raise ScheduledMinuteUnresolved(
                            "scheduled minute claim readback differs"
                        )
        except ScheduledMinutePublicationError:
            raise
        except Exception as exc:
            raise ScheduledMinutePublicationError(
                "scheduled minute claim uncertain"
            ) from exc

    def _publish(
        self, ordinal: int, frozen: FrozenGate3Artifact[ScheduledPublicMinuteReceiptV1]
    ) -> FrozenGate3Artifact[ScheduledPublicMinuteReceiptV1]:
        try:
            with _root_context(self._root) as directory:
                names = self._check_root(directory)
                name = _ordinal_name(ordinal, self._coordinate_plan.expected_rows)
                if name not in names:
                    raise ScheduledMinuteUnresolved(
                        "scheduled minute claim disappeared"
                    )
                with directory.child(name) as child:
                    if set(child.names()) != {"claim.json"}:
                        raise ScheduledMinuteUnresolved(
                            "scheduled minute publication is unresolved"
                        )
                    self._read_claim(child, ordinal)
                    verified = verify_scheduled_public_minute_receipt(
                        frozen.payload, expected_sha256=frozen.sha256
                    )
                    if (
                        verified != frozen.contract
                        or verified.seal_sha256 != self._identity.seal_sha256
                        or verified.coordinate_plan_sha256
                        != self._identity.coordinate_plan_sha256
                        or verified.schedule_sha256 != self._identity.schedule_sha256
                        or verified.holdout_id != self._identity.holdout_id
                        or verified.ordinal != ordinal
                        or verified.plan_sha256
                        != self._claim_contract(ordinal).plan_sha256
                    ):
                        raise ScheduledMinuteUnresolved(
                            "scheduled minute delegate result differs"
                        )
                    self._check_due_minute(ordinal, verified)
                    child.publish("receipt.json", frozen.payload)
                    if child.read("receipt.json", MAX_RECORD_BYTES) != frozen.payload:
                        raise ScheduledMinuteUnresolved(
                            "scheduled minute receipt readback differs"
                        )
                    child.publish(
                        "receipt.sha256", frozen.sha256.encode("ascii") + b"\n"
                    )
                result = self._read_slot(directory, ordinal)
                if result.payload != frozen.payload or result.sha256 != frozen.sha256:
                    raise ScheduledMinuteUnresolved(
                        "scheduled minute final readback differs"
                    )
                return result
        except ScheduledMinutePublicationError:
            raise
        except Exception as exc:
            raise ScheduledMinutePublicationError(
                "scheduled minute publication uncertain"
            ) from exc

    async def capture_once(
        self,
        *,
        seal_observer,
        schedule_repository,
        capture_service,
        observation_repository,
        ordinal: int,
        expected_previous_receipt_sha256s: tuple[str, ...],
    ) -> FrozenGate3Artifact[ScheduledPublicMinuteReceiptV1]:
        """Verify the pinned complete prefix, then claim before any await."""
        self._claim(
            ordinal,
            expected_previous_receipt_sha256s=expected_previous_receipt_sha256s,
        )
        frozen = await capture_scheduled_public_minute_once(
            seal_observer=seal_observer,
            schedule_repository=schedule_repository,
            capture_service=capture_service,
            observation_repository=observation_repository,
            coordinate_plan=self._coordinate_plan,
            expected_coordinate_plan_sha256=self._identity.coordinate_plan_sha256,
            expected_seal_sha256=self._identity.seal_sha256,
            expected_schedule_sha256=self._identity.schedule_sha256,
            ordinal=ordinal,
        )
        return self._publish(ordinal, frozen)

    def read_ordinal(
        self, ordinal: int, *, expected_receipt_sha256: str
    ) -> FrozenGate3Artifact[ScheduledPublicMinuteReceiptV1]:
        """Read a complete local receipt against a separately retained digest."""
        if (
            type(expected_receipt_sha256) is not str
            or SHA256_RE.fullmatch(expected_receipt_sha256) is None
        ):
            raise ScheduledMinutePublicationError("expected receipt pin is invalid")
        name = _ordinal_name(ordinal, self._coordinate_plan.expected_rows)
        try:
            with _root_context(self._root) as directory:
                if name not in self._check_root(directory):
                    raise ScheduledMinutePublicationError(
                        "scheduled minute ordinal was never claimed"
                    )
                result = self._read_slot(directory, ordinal)
                if result.sha256 != expected_receipt_sha256:
                    raise ScheduledMinuteUnresolved(
                        "scheduled minute external receipt pin differs"
                    )
                return result
        except ScheduledMinutePublicationError:
            raise
        except Exception as exc:
            raise ScheduledMinuteUnresolved(
                "scheduled minute readback is unresolved"
            ) from exc


__all__ = (
    "ScheduledMinutePublicationError",
    "ScheduledMinuteReceiptStore",
    "ScheduledMinuteUnresolved",
)
