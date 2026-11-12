"""Capture and retain a separate, no-clobber Global public readback sample."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import ClockStamp, decode, sha, validate_stamps
from app.public_market_source.public_market_receipts import (
    ReceiptContract,
    Sha,
    checked,
)
from app.public_market_source.public_receipt_storage import (
    NATIVE_DEVICE_MAX,
    NATIVE_INODE_MAX,
)
from app.trade_evidence.storage import _root_path
from app.trade_qualification import global_public_runtime_v1 as public
from app.trade_qualification import public_source_runtime as native
from app.trade_qualification.public_market_collector import PublicMarketCollectionPolicy

_FILE = "post-readback.json"
_MAX_BYTES = 1024
_NativeIdentity = tuple[
    Annotated[int, Field(ge=0, le=NATIVE_DEVICE_MAX)],
    Annotated[int, Field(ge=1, le=NATIVE_INODE_MAX)],
]


class GlobalPublicCompanionError(ValueError):
    """Fixed refusal without a path, payload, or connection detail."""


def _unverified_availability(value):
    if value is not False:
        raise GlobalPublicCompanionError("global_companion_pin_invalid")
    return value


class GlobalPublicCapturePublicationV1(ReceiptContract):
    schema_version: Literal["ctcc.public.global_companion_publication.v1"] = (
        "ctcc.public.global_companion_publication.v1"
    )
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    plan_sha256: Sha
    journal_sha256: Sha
    packet_sha256: Sha
    market_policy_sha256: Sha
    companion_sha256: Sha
    journal_root_identity: _NativeIdentity
    companion_root_identity: _NativeIdentity
    independent_availability_verified: Literal[False] = False
    execution_authority: Literal[False] = False

    _no_independent_availability = field_validator(
        "independent_availability_verified", mode="before"
    )(_unverified_availability)


class GlobalPublicCompanionReadbackV1(ReceiptContract):
    schema_version: Literal["ctcc.public.global_companion_readback.v1"] = (
        "ctcc.public.global_companion_readback.v1"
    )
    publication_sha256: Sha
    companion_sha256: Sha
    readback_completed: ClockStamp
    independent_availability_verified: Literal[False] = False
    execution_authority: Literal[False] = False

    _no_independent_availability = field_validator(
        "independent_availability_verified", mode="before"
    )(_unverified_availability)


def verify_global_public_companion_publication_v1(
    raw: bytes, *, expected_sha256: str
) -> GlobalPublicCapturePublicationV1:
    """Load independently pinned canonical publication bytes for restart."""
    try:
        if (
            type(raw) is not bytes
            or not 0 < len(raw) <= _MAX_BYTES
            or type(expected_sha256) is not str
            or sha(raw) != expected_sha256
        ):
            raise GlobalPublicCompanionError("global_companion_pin_invalid")
        publication = GlobalPublicCapturePublicationV1.model_validate_json(raw)
        if publication.canonical_bytes() != raw:
            raise GlobalPublicCompanionError("global_companion_pin_invalid")
        return publication
    except ValueError:
        raise GlobalPublicCompanionError("global_companion_pin_invalid") from None


def _measured_companion_readback(publication, companion, completed):
    original, observed = validate_stamps(
        (decode(companion, _MAX_BYTES)["sample"], completed)
    )
    if observed.monotonic_ns <= original.monotonic_ns:
        raise GlobalPublicCompanionError("global_companion_readback_clock_invalid")
    return GlobalPublicCompanionReadbackV1(
        publication_sha256=publication.canonical_sha256(),
        companion_sha256=publication.companion_sha256,
        readback_completed=observed.model_dump(),
    )


def _paths(journal_root, companion_root):
    if type(journal_root) is not type(Path()) or type(companion_root) is not type(
        Path()
    ):
        raise GlobalPublicCompanionError("global_companion_root_invalid")
    journal_path, companion_path = _root_path(journal_root), _root_path(companion_root)
    if journal_path.is_relative_to(companion_path) or companion_path.is_relative_to(
        journal_path
    ):
        raise GlobalPublicCompanionError("global_companion_roots_overlap")
    return journal_path, companion_path


def _identity(directory):
    identity = native._native_recheck_root_identity(directory.path)
    if hasattr(directory, "fd"):
        held = os.fstat(directory.fd)
        if identity != (held.st_dev, held.st_ino):
            raise GlobalPublicCompanionError("global_companion_root_changed")
    return identity


def _check_pair(journal_path, companion_path, journal_identity, companion_identity):
    identity = native._native_recheck_root_identity
    if (
        identity(journal_path) != journal_identity
        or identity(companion_path) != companion_identity
    ):
        raise GlobalPublicCompanionError("global_companion_root_changed")
    # Native identities also detect aliases that spell the same directory
    # differently, including Windows short names and mounted paths.
    if journal_identity == companion_identity or any(
        identity(ancestor) == opposite
        for path, opposite in (
            (journal_path, companion_identity),
            (companion_path, journal_identity),
        )
        for ancestor in path.parents
    ):
        raise GlobalPublicCompanionError("global_companion_roots_overlap")


def _check_bytes(raw, publication):
    if sha(raw) != publication.companion_sha256:
        raise GlobalPublicCompanionError("global_companion_digest_changed")
    value = decode(raw, _MAX_BYTES)
    sample = ClockStamp.model_validate(value["sample"])
    if raw != public._post_readback_receipt(
        instrument_id=publication.instrument_id,
        plan_sha256=publication.plan_sha256,
        journal_sha256=publication.journal_sha256,
        packet_sha256=publication.packet_sha256,
        sample=sample,
    ):
        raise GlobalPublicCompanionError("global_companion_scope_changed")


def read_global_public_companion_v1(
    journal_root: Path,
    companion_root: Path,
    *,
    publication: GlobalPublicCapturePublicationV1,
) -> bytes:
    """Reopen exact local bytes under the externally retained publication pins."""
    try:
        publication = checked(publication, GlobalPublicCapturePublicationV1)
        journal_path, companion_path = _paths(journal_root, companion_root)
        with native._native_recheck_root(companion_path) as directory:
            if _identity(directory) != publication.companion_root_identity:
                raise GlobalPublicCompanionError("global_companion_root_changed")
            _check_pair(
                journal_path,
                companion_path,
                publication.journal_root_identity,
                publication.companion_root_identity,
            )
            if set(directory.names()) != {_FILE}:
                raise GlobalPublicCompanionError("global_companion_inventory_changed")
            raw = directory.read(_FILE, _MAX_BYTES)
            _check_bytes(raw, publication)
            _check_pair(
                journal_path,
                companion_path,
                publication.journal_root_identity,
                publication.companion_root_identity,
            )
            if set(directory.names()) != {_FILE}:
                raise GlobalPublicCompanionError("global_companion_inventory_changed")
        return raw
    except GlobalPublicCompanionError:
        raise
    except Exception:  # noqa: BLE001 -- keep native paths and raw data private.
        raise GlobalPublicCompanionError("global_companion_readback_refused") from None


async def capture_global_public_with_companion_v1(
    journal_root: Path,
    companion_root: Path,
    *,
    instrument_id: str,
    market_policy: PublicMarketCollectionPolicy,
) -> GlobalPublicCapturePublicationV1:
    """Run the existing anonymous collector once and publish its original sample."""
    try:
        journal_path, companion_path = _paths(journal_root, companion_root)
        with native._native_recheck_root(companion_path) as directory:
            companion_identity = _identity(directory)
            if directory.names():
                raise GlobalPublicCompanionError("global_companion_root_occupied")
            with native._native_recheck_root(journal_path) as journal_directory:
                journal_identity = _identity(journal_directory)
                _check_pair(
                    journal_path, companion_path, journal_identity, companion_identity
                )
                if journal_directory.names():
                    raise GlobalPublicCompanionError(
                        "global_companion_journal_occupied"
                    )
            capture = await public.capture_initial_global_public_market_v1(
                journal_path, instrument_id=instrument_id, market_policy=market_policy
            )
            packet = capture.packet
            raw = capture.post_readback_receipt_json
            if (
                capture.code != "initial_global_public_captured"
                or type(packet) is not public.GlobalPublicComponentPacketV1
                or packet.instrument_id != instrument_id
                or capture.plan_sha256 is None
                or capture.journal_sha256 is None
                or type(raw) is not bytes
                or not 0 < len(raw) <= _MAX_BYTES
            ):
                raise GlobalPublicCompanionError("global_companion_capture_incomplete")
            publication = GlobalPublicCapturePublicationV1(
                instrument_id=packet.instrument_id,
                plan_sha256=capture.plan_sha256,
                journal_sha256=capture.journal_sha256,
                packet_sha256=packet.bundle_sha256,
                market_policy_sha256=packet.policy_sha256,
                companion_sha256=sha(raw),
                journal_root_identity=journal_identity,
                companion_root_identity=companion_identity,
            )
            _check_bytes(raw, publication)
            reopened = public.read_global_public_capture_v1(
                journal_path,
                expected_plan_sha256=publication.plan_sha256,
                expected_journal_sha256=publication.journal_sha256,
                expected_root_identity=publication.journal_root_identity,
            )
            if (
                type(reopened.packet) is not public.GlobalPublicComponentPacketV1
                or reopened.packet.bundle_sha256 != publication.packet_sha256
            ):
                raise GlobalPublicCompanionError("global_companion_capture_incomplete")
            _check_pair(
                journal_path, companion_path, journal_identity, companion_identity
            )
            if directory.names():
                raise GlobalPublicCompanionError("global_companion_root_occupied")
            directory.publish(_FILE, raw)
            if directory.read(_FILE, _MAX_BYTES) != raw or set(directory.names()) != {
                _FILE
            }:
                raise GlobalPublicCompanionError("global_companion_publication_refused")
        if (
            read_global_public_companion_v1(
                journal_path, companion_path, publication=publication
            )
            != raw
        ):
            raise GlobalPublicCompanionError("global_companion_publication_refused")
        return publication
    except GlobalPublicCompanionError:
        raise
    except Exception:  # noqa: BLE001 -- preserve raw and partial artifacts on failure.
        raise GlobalPublicCompanionError(
            "global_companion_publication_refused"
        ) from None


async def capture_global_public_with_measured_companion_v1(
    journal_root: Path,
    companion_root: Path,
    *,
    instrument_id: str,
    market_policy: PublicMarketCollectionPolicy,
) -> tuple[GlobalPublicCapturePublicationV1, GlobalPublicCompanionReadbackV1]:
    """Measure the final native companion readback of the original capture."""
    publication = await capture_global_public_with_companion_v1(
        journal_root,
        companion_root,
        instrument_id=instrument_id,
        market_policy=market_policy,
    )
    try:
        companion = read_global_public_companion_v1(
            journal_root, companion_root, publication=publication
        )
        return publication, _measured_companion_readback(
            publication, companion, native_stamp()
        )
    except ValueError:
        raise GlobalPublicCompanionError(
            "global_companion_readback_measurement_refused"
        ) from None


def verify_global_public_measured_companion_v1(
    journal_root: Path,
    companion_root: Path,
    *,
    publication: GlobalPublicCapturePublicationV1,
    readback_json: bytes,
    expected_readback_sha256: str,
) -> GlobalPublicCompanionReadbackV1:
    """Recheck an externally pinned native sample without sampling a new clock."""
    try:
        if (
            type(readback_json) is not bytes
            or not 0 < len(readback_json) <= _MAX_BYTES
            or type(expected_readback_sha256) is not str
            or sha(readback_json) != expected_readback_sha256
        ):
            raise GlobalPublicCompanionError("global_companion_readback_pin_invalid")
        recorded = GlobalPublicCompanionReadbackV1.model_validate_json(readback_json)
        companion = read_global_public_companion_v1(
            journal_root, companion_root, publication=publication
        )
        checked_record = _measured_companion_readback(
            publication, companion, recorded.readback_completed.model_dump()
        )
        if checked_record.canonical_bytes() != readback_json:
            raise GlobalPublicCompanionError("global_companion_readback_pin_invalid")
        return checked_record
    except ValueError:
        raise GlobalPublicCompanionError(
            "global_companion_readback_measurement_refused"
        ) from None
