"""Native append-only local journal, bounded by a separately retained checkpoint.

The service must own the root and protect its latest checkpoint independently.
That configuration is a trust prerequisite, not established by a caller's hash.
This journal defends against path races and accidental mutation, not a malicious
administrator/same-user process or loss of an unanchored final append. Windows
has file fsync/readback but no claim of atomic power-loss directory durability.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.public_market_source.public_clock import native_stamp
from app.public_market_source.public_market_receipts import (
    MAX_RAW,
    MeasuredPublicMinuteReceiptV1,
    PublicReceiptError,
    ReceiptContract,
    Sha,
    canonical,
    checked,
    decode,
    sha,
    validate_stamps,
)
from app.trade_evidence.storage import (
    _posix_root,
    _root_path,
    _windows_native_path,
    _windows_root,
)

MAX_CAPTURES = 1024
MAX_JOURNAL_BYTES = 1024 * 1024 * 1024
_JOURNAL_ISSUER = object()


class PublicJournalCheckpointV1(ReceiptContract):
    schema_version: Literal["ctcc.public.journal_checkpoint.v1"] = (
        "ctcc.public.journal_checkpoint.v1"
    )
    genesis_sha256: Sha
    root_device: int = Field(ge=0)
    root_inode: int = Field(ge=1)
    sequence: int = Field(ge=0, le=MAX_CAPTURES)
    head_sha256: Sha
    attempt_sequence: int = Field(ge=0, le=1024, default=0)
    attempt_head_sha256: Sha | None = None


@dataclass(frozen=True, slots=True)
class PublishedMeasuredCapture:
    capture_id: str
    receipt_sha256: str
    checkpoint: PublicJournalCheckpointV1
    completed_ns: int
    execution_authority: Literal[False] = False


def _root_context(root):
    if type(root) is not type(Path()):
        raise PublicReceiptError("native_path_required")
    return (_windows_root if os.name == "nt" else _posix_root)(_root_path(root))


def _root_identity(root):
    info = os.stat(
        _windows_native_path(root) if os.name == "nt" else root, follow_symlinks=False
    )
    return info.st_dev, info.st_ino


def _read_json(directory, name, maximum=MAX_RAW):
    raw = directory.read(name, maximum)
    value = decode(raw, maximum)
    if canonical(value) != raw:
        raise PublicReceiptError("journal_noncanonical_record")
    return value, raw


def _receipt_from_json(raw):
    # JSON mode preserves strict tuple semantics without modifying old DTOs.
    return MeasuredPublicMinuteReceiptV1.model_validate_json(raw)


def _replay_directory(directory, checkpoint):
    from app.public_market_source.public_market_capture import replay_public_capture

    checkpoint = checked(checkpoint, PublicJournalCheckpointV1)
    if _root_identity(directory.path) != (
        checkpoint.root_device,
        checkpoint.root_inode,
    ):
        raise PublicReceiptError("journal_root_identity_changed")
    genesis, raw = _read_json(directory, "genesis.json")
    if (
        set(genesis) != {"schema_version", "journal_id", "root_device", "root_inode"}
        or genesis["schema_version"] != "ctcc.public.journal_genesis.v1"
        or sha(raw) != checkpoint.genesis_sha256
        or (genesis["root_device"], genesis["root_inode"])
        != (checkpoint.root_device, checkpoint.root_inode)
    ):
        raise PublicReceiptError("journal_genesis_mismatch")
    names = directory.names()
    expected_names = {"genesis.json"}
    attempts, attempt_bytes = {}, 0
    if checkpoint.attempt_head_sha256 is not None:
        from app.public_market_source.public_attempt_journal import replay_attempt_chain

        expected_names.add("attempts")
        with directory.child("attempts") as attempt_directory:
            attempts, attempt_bytes = replay_attempt_chain(
                attempt_directory,
                sequence=checkpoint.attempt_sequence,
                expected_head=checkpoint.attempt_head_sha256,
                genesis_sha256=checkpoint.genesis_sha256,
            )
    elif checkpoint.attempt_sequence:
        raise PublicReceiptError("attempt_checkpoint_missing")
    entries, first_rows, capture_ids = [], {}, set()
    previous = checkpoint.genesis_sha256
    total, previous_time = len(raw) + attempt_bytes, None
    if total > MAX_JOURNAL_BYTES:
        raise PublicReceiptError("journal_byte_budget")
    for sequence in range(1, checkpoint.sequence + 1):
        name = f"capture-{sequence:08d}"
        expected_names.add(name)
        with directory.child(name) as child:
            entry, entry_bytes = _read_json(child, "entry.json")
            if (
                set(entry)
                != {
                    "schema_version",
                    "sequence",
                    "previous_sha256",
                    "receipt_sha256",
                    "payload_readback_complete",
                    "disposition",
                    "source_conflicts",
                }
                or entry["schema_version"] != "ctcc.public.journal_entry.v1"
                or type(entry["sequence"]) is not int
                or entry["sequence"] != sequence
                or entry["previous_sha256"] != previous
            ):
                raise PublicReceiptError("journal_chain_invalid")
            receipt_bytes = child.read("receipt.json", 8 * MAX_RAW)
            if sha(receipt_bytes) != entry["receipt_sha256"]:
                raise PublicReceiptError("journal_receipt_mismatch")
            receipt = _receipt_from_json(receipt_bytes)
            if receipt.capture_id in capture_ids:
                raise PublicReceiptError("capture_id_conflict")
            capture_ids.add(receipt.capture_id)
            if (
                receipt.canonical_bytes() != receipt_bytes
                or receipt.transport_origin != "owned_native_tls"
            ):
                raise PublicReceiptError("journal_owned_source_required")
            raw_files = {
                filename: child.read(filename, MAX_RAW)
                for filename, _ in receipt.raw_files
            }
            if set(child.names()) != {"receipt.json", "entry.json", *raw_files}:
                raise PublicReceiptError("journal_capture_incomplete")
            plan, receipt = replay_public_capture(receipt, raw_files)
            if receipt.attempt_sha256 is not None:
                from app.public_market_source.public_attempt_journal import (
                    bind_measured_attempt,
                )

                bind_measured_attempt(receipt, raw_files, attempts)
            validate_stamps(
                (receipt.validation_complete, entry["payload_readback_complete"])
            )
            if (
                previous_time is not None
                and receipt.validation_complete["utc_ns"] < previous_time
            ):
                raise PublicReceiptError("journal_observation_reordered")
            previous_time = entry["payload_readback_complete"]["utc_ns"]
            conflicts = []
            for locator in receipt.rows:
                identity, content = locator["identity"], locator["content_sha256"]
                if identity in first_rows and first_rows[identity][0] != content:
                    conflicts.append(identity)
            expected_disposition = (
                "rejected_source_revision" if conflicts else "accepted"
            )
            if (
                entry["disposition"] != expected_disposition
                or entry["source_conflicts"] != conflicts
            ):
                raise PublicReceiptError("source_revision_disposition_mismatch")
            if not conflicts:
                for locator in receipt.rows:
                    first_rows.setdefault(
                        locator["identity"],
                        (locator["content_sha256"], len(entries), locator),
                    )
            total += (
                len(entry_bytes)
                + len(receipt_bytes)
                + sum(map(len, raw_files.values()))
            )
            if total > MAX_JOURNAL_BYTES:
                raise PublicReceiptError("journal_byte_budget")
            entries.append((plan, receipt, raw_files, entry))
            previous = sha(entry_bytes)
    if set(names) != expected_names:
        # Unanchored/truncated/partial append needs explicit recovery; never infer
        # success or erase durable raw files to make a checkpoint look complete.
        raise PublicReceiptError("journal_checkpoint_inventory_mismatch")
    if previous != checkpoint.head_sha256:
        raise PublicReceiptError("journal_checkpoint_head_mismatch")
    return tuple(entries), first_rows


class ControlledPublicReceiptJournal:
    __slots__ = ("_checkpoint", "_root")

    def __init__(self, issuer, root, checkpoint):
        if issuer is not _JOURNAL_ISSUER:
            raise PublicReceiptError("controlled_journal_required")
        self._root, self._checkpoint = root, checkpoint

    @property
    def checkpoint(self):
        return checked(self._checkpoint, PublicJournalCheckpointV1)

    def read_all(self):
        with _root_context(self._root) as directory:
            return _replay_directory(directory, self.checkpoint)

    @contextmanager
    def _begin_attempt(self, plan):
        from app.public_market_source.public_attempt_journal import (
            MAX_ATTEMPTS,
            _WithoutChain,
            owned_attempt,
            replay_attempt,
        )

        checkpoint = self.checkpoint
        if (
            checkpoint.attempt_head_sha256 is None
            or checkpoint.attempt_sequence >= MAX_ATTEMPTS
        ):
            raise PublicReceiptError("attempt_journal_not_available")
        with _root_context(self._root) as directory:
            _replay_directory(directory, checkpoint)
            with directory.child("attempts") as attempts:
                sequence = checkpoint.attempt_sequence + 1
                name = f"attempt-{sequence:08d}"
                attempts.mkdir(name)
                with (
                    attempts.child(name) as child,
                    owned_attempt(child, plan, stamp=native_stamp()) as attempt,
                ):
                    try:
                        yield attempt
                    finally:
                        if attempt.summary is not None:
                            _, _, _, digest = replay_attempt(_WithoutChain(child))
                            entry = canonical(
                                {
                                    "schema_version": "ctcc.public.attempt_chain.v1",
                                    "sequence": sequence,
                                    "previous_sha256": checkpoint.attempt_head_sha256,
                                    "summary_sha256": digest,
                                }
                            )
                            child.publish("chain.json", entry)
                            if child.read("chain.json", MAX_RAW) != entry:
                                raise PublicReceiptError("attempt_readback_failed")
                            next_checkpoint = PublicJournalCheckpointV1.model_validate(
                                {
                                    **checkpoint.model_dump(),
                                    "attempt_sequence": sequence,
                                    "attempt_head_sha256": sha(entry),
                                }
                            )
                            _replay_directory(directory, next_checkpoint)
                            self._checkpoint = next_checkpoint

    def _publish_owned(self, capture):
        from app.public_market_source.public_market_capture import (
            _OwnedPublicCapture,
            replay_public_capture,
        )

        if type(capture) is not _OwnedPublicCapture:
            raise PublicReceiptError("owned_capture_required")
        receipt, files = capture.receipt, dict(capture.raw_files)
        _, receipt = replay_public_capture(receipt, files)
        if receipt.transport_origin != "owned_native_tls":
            raise PublicReceiptError("native_public_capture_required")
        checkpoint = self.checkpoint
        if checkpoint.sequence >= MAX_CAPTURES:
            raise PublicReceiptError("journal_capture_budget")
        with _root_context(self._root) as directory:
            entries, first_rows = _replay_directory(directory, checkpoint)
            if receipt.attempt_sha256 is not None:
                from app.public_market_source.public_attempt_journal import (
                    bind_measured_attempt,
                    replay_attempt_chain,
                )

                with directory.child("attempts") as attempt_directory:
                    attempts, _ = replay_attempt_chain(
                        attempt_directory,
                        sequence=checkpoint.attempt_sequence,
                        expected_head=checkpoint.attempt_head_sha256,
                        genesis_sha256=checkpoint.genesis_sha256,
                    )
                bind_measured_attempt(receipt, files, attempts)
            for _, existing, _, entry in entries:
                if existing.capture_id == receipt.capture_id:
                    if existing.canonical_sha256() != receipt.canonical_sha256():
                        raise PublicReceiptError("capture_id_conflict")
                    if entry["disposition"] != "accepted":
                        raise PublicReceiptError("source_revision_conflict")
                    return PublishedMeasuredCapture(
                        receipt.capture_id,
                        existing.canonical_sha256(),
                        checkpoint,
                        native_stamp()["utc_ns"],
                    )
            if (
                entries
                and receipt.validation_complete["utc_ns"]
                < entries[-1][3]["payload_readback_complete"]["utc_ns"]
            ):
                raise PublicReceiptError("journal_observation_reordered")
            conflicts = []
            for locator in receipt.rows:
                if (
                    locator["identity"] in first_rows
                    and locator["content_sha256"] != first_rows[locator["identity"]][0]
                ):
                    conflicts.append(locator["identity"])
            sequence = checkpoint.sequence + 1
            name = f"capture-{sequence:08d}"
            directory.mkdir(name)
            with directory.child(name) as child:
                for filename, _ in receipt.raw_files:
                    child.publish(filename, files[filename])
                receipt_bytes = receipt.canonical_bytes()
                child.publish("receipt.json", receipt_bytes)
                for filename, _ in receipt.raw_files:
                    if child.read(filename, MAX_RAW) != files[filename]:
                        raise PublicReceiptError("publication_readback_mismatch")
                if child.read("receipt.json", 8 * MAX_RAW) != receipt_bytes:
                    raise PublicReceiptError("publication_readback_mismatch")
                readback = native_stamp()
                validate_stamps((receipt.validation_complete, readback))
                entry = {
                    "schema_version": "ctcc.public.journal_entry.v1",
                    "sequence": sequence,
                    "previous_sha256": checkpoint.head_sha256,
                    "receipt_sha256": sha(receipt_bytes),
                    "payload_readback_complete": readback,
                    "disposition": "rejected_source_revision"
                    if conflicts
                    else "accepted",
                    "source_conflicts": conflicts,
                }
                entry_bytes = canonical(entry)
                child.publish("entry.json", entry_bytes)
                if child.read("entry.json", MAX_RAW) != entry_bytes:
                    raise PublicReceiptError("publication_readback_mismatch")
            next_checkpoint = PublicJournalCheckpointV1(
                genesis_sha256=checkpoint.genesis_sha256,
                root_device=checkpoint.root_device,
                root_inode=checkpoint.root_inode,
                sequence=sequence,
                head_sha256=sha(entry_bytes),
                attempt_sequence=checkpoint.attempt_sequence,
                attempt_head_sha256=checkpoint.attempt_head_sha256,
            )
            _replay_directory(directory, next_checkpoint)
            finished = native_stamp()
            validate_stamps((receipt.validation_complete, readback, finished))
            self._checkpoint = next_checkpoint
            if conflicts:
                # Preserve the raw correction with an anchored rejection marker.
                raise PublicReceiptError("source_revision_conflict")
            return PublishedMeasuredCapture(
                receipt.capture_id,
                sha(receipt_bytes),
                next_checkpoint,
                finished["utc_ns"],
            )


def initialize_public_receipt_journal(root: Path):
    """Explicit service setup; requires a preexisting empty owned local directory."""
    with _root_context(root) as directory:
        if directory.names():
            raise PublicReceiptError("journal_root_not_empty")
        device, inode = _root_identity(root)
        genesis = canonical(
            {
                "schema_version": "ctcc.public.journal_genesis.v1",
                "journal_id": uuid.uuid4().hex,
                "root_device": device,
                "root_inode": inode,
            }
        )
        directory.publish("genesis.json", genesis)
        if directory.read("genesis.json", MAX_RAW) != genesis:
            raise PublicReceiptError("publication_readback_mismatch")
        directory.mkdir("attempts")
        checkpoint = PublicJournalCheckpointV1(
            genesis_sha256=sha(genesis),
            root_device=device,
            root_inode=inode,
            sequence=0,
            head_sha256=sha(genesis),
            attempt_head_sha256=sha(genesis),
        )
    return ControlledPublicReceiptJournal(_JOURNAL_ISSUER, root, checkpoint)


def resume_public_receipt_journal(
    *, root: Path, trusted_checkpoint: PublicJournalCheckpointV1
):
    """Service-only restart seam, not a public caller attestation verifier.

    `trusted_checkpoint` must come from independently protected service config.
    Supplying an arbitrary root/hash establishes only byte consistency, never
    source authentication. No checkpoint is auto-discovered from this journal.
    """
    checkpoint = checked(trusted_checkpoint, PublicJournalCheckpointV1)
    with _root_context(root) as directory:
        _replay_directory(directory, checkpoint)
    return ControlledPublicReceiptJournal(_JOURNAL_ISSUER, root, checkpoint)
