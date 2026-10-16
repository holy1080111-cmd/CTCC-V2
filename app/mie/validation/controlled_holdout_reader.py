"""One-use, computational-only reader for a prospectively sealed holdout.

This local reader cannot prove that nobody accessed the files by another path.
It consumes the existing durable evaluation slot before opening either input.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from app.mie.validation.contracts import Gate3Claim
from app.mie.validation.prospective import ProspectiveAccessOutcome
from app.mie.validation.seal_ledger import (
    FormalEvaluationReservation,
    Gate3SealLedger,
)

MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_DATASET_BYTES = 128 * 1024 * 1024


class ControlledHoldoutReadError(ValueError):
    """The one-use read could not establish the exact sealed input bytes."""


@dataclass(frozen=True, slots=True)
class ControlledHoldoutRead:
    reservation: FormalEvaluationReservation
    manifest_bytes: bytes
    dataset_bytes: bytes
    current_claim: Gate3Claim = field(default=Gate3Claim.COMPUTATIONAL, init=False)
    predictive_oos_eligible: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    stable = (
        left.st_dev,
        left.st_ino,
        left.st_size,
        left.st_mtime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_size,
        right.st_mtime_ns,
    )
    # Windows path and descriptor stat can disagree on st_ctime after a write.
    # The content digest remains mandatory on every platform.
    return stable and (os.name == "nt" or left.st_ctime_ns == right.st_ctime_ns)


def _read_pinned_file(
    path: Path, expected_sha256: str, maximum: int
) -> tuple[bytes, tuple[int, int]]:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ControlledHoldoutReadError("holdout input path must be absolute")
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise ControlledHoldoutReadError(
                "holdout input is not a bounded regular file"
            )
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or not _same_file(before, opened):
                raise ControlledHoldoutReadError("holdout input changed at open")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                payload = stream.read(maximum + 1)
            after_fd = os.fstat(fd)
        finally:
            os.close(fd)
        after_path = path.lstat()
        if (
            len(payload) != before.st_size
            or not _same_file(before, after_fd)
            or not _same_file(before, after_path)
        ):
            raise ControlledHoldoutReadError("holdout input changed during read")
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise ControlledHoldoutReadError(
                "holdout input digest differs from receipt"
            )
        return payload, (after_fd.st_dev, after_fd.st_ino)
    except OSError as exc:
        raise ControlledHoldoutReadError("holdout input read failed") from exc


def read_prospective_holdout_once(
    *,
    ledger_path: Path,
    expected_seal_sha256: str,
    expected_receipt_sha256: str,
    manifest_path: Path,
    dataset_path: Path,
) -> ControlledHoldoutRead:
    """Consume one formal attempt, read it back, then open exact pinned files.

    Every failure after reservation permanently consumes the local seal's slot.
    No retry or result publication is performed here.
    """

    if (
        not isinstance(ledger_path, Path)
        or not ledger_path.is_absolute()
        or ledger_path.is_symlink()
        or not ledger_path.is_file()
    ):
        raise ControlledHoldoutReadError("existing absolute seal ledger required")
    ledger = Gate3SealLedger(ledger_path)
    reservation = ledger.reserve_formal_evaluation(
        expected_seal_sha256=expected_seal_sha256,
        expected_receipt_sha256=expected_receipt_sha256,
    )
    independent = Gate3SealLedger(ledger_path)
    if independent.read_evaluation(expected_seal_sha256) != reservation:
        raise ControlledHoldoutReadError("evaluation reservation readback differs")
    receipt, _ = independent.read_receipt(expected_seal_sha256, expected_receipt_sha256)
    if (
        not receipt.predictive_oos_eligible
        or not receipt.all_artifacts_verified
        or not receipt.access_policy_compliant
        or receipt.access_outcome != ProspectiveAccessOutcome.SEALED_UNREAD
        or receipt.candidate_changed_after_preregistration
    ):
        raise ControlledHoldoutReadError("holdout receipt is ineligible for first read")
    if (
        not isinstance(manifest_path, Path)
        or not manifest_path.is_absolute()
        or not isinstance(dataset_path, Path)
        or not dataset_path.is_absolute()
    ):
        raise ControlledHoldoutReadError("holdout input paths must be absolute")
    try:
        if os.path.samefile(manifest_path, dataset_path):
            raise ControlledHoldoutReadError(
                "manifest and dataset resolve to the same file"
            )
    except OSError as exc:
        raise ControlledHoldoutReadError("holdout input identity check failed") from exc
    manifest, manifest_identity = _read_pinned_file(
        manifest_path, receipt.holdout_dataset.manifest_sha256, MAX_MANIFEST_BYTES
    )
    dataset, dataset_identity = _read_pinned_file(
        dataset_path, receipt.holdout_dataset.content_sha256, MAX_DATASET_BYTES
    )
    if manifest_identity == dataset_identity:
        raise ControlledHoldoutReadError("manifest and dataset opened the same file")
    return ControlledHoldoutRead(
        reservation=reservation, manifest_bytes=manifest, dataset_bytes=dataset
    )
