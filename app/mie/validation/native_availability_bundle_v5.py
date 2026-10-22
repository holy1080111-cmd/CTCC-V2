"""Bounded MIE raw bundle and strict replay; saved bytes grant no capability."""

from __future__ import annotations

import base64
import re
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from pydantic import Field, StrictInt, model_validator

from app.database.repositories.public_receipt_witness import verify_witness_chain
from app.domain.source_primitives import canonical, sha, validate_stamps
from app.mie.features import FeatureBar
from app.mie.validation.artifact import _freeze, _verify
from app.mie.validation.contracts import Gate3Contract, Sha256, require_utc
from app.mie.validation.post_publication_availability_v4 import (
    PostPublicationClockEvidenceV4,
)
from app.mie.validation.post_publication_availability_v5 import AvailabilityContractV5
from app.public_market_source.public_attempt_journal import (
    bind_measured_attempt,
    replay_attempt_chain,
)
from app.public_market_source.public_market_capture import replay_public_capture
from app.public_market_source.public_market_receipts import (
    MeasuredPublicMinuteReceiptV1,
    decode,
    parsed_rows,
    utc_from_ns,
)
from app.public_market_source.public_receipt_storage import (
    ControlledPublicReceiptJournal,
    PublicJournalCheckpointV1,
    _root_context,
)

MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
_NAME = re.compile(r"[a-zA-Z0-9_.-]{1,100}\Z")


class PackedPublicFileV5(Gate3Contract):
    path: str
    raw_base64: str
    raw_sha256: Sha256

    def raw(self):
        raw = base64.b64decode(self.raw_base64, validate=True)
        if (
            not 0 < len(raw) <= MAX_FILE_BYTES
            or sha(raw) != self.raw_sha256
            or base64.b64encode(raw).decode("ascii") != self.raw_base64
        ):
            raise ValueError("availability_bundle_raw_invalid")
        return raw

    @model_validator(mode="after")
    def checked_path(self):
        parts = self.path.split("/")
        if not 1 <= len(parts) <= 4 or any(
            p in {".", ".."} or _NAME.fullmatch(p) is None for p in parts
        ):
            raise ValueError("availability_bundle_path_invalid")
        self.raw()
        return self


class WitnessRawRowV5(Gate3Contract):
    journal_key: Sha256
    revision: StrictInt = Field(ge=0, le=8192)
    journal_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    root_device: StrictInt = Field(ge=0, le=2**64 - 1)
    root_inode: StrictInt = Field(ge=1, le=2**128 - 1)
    transition: str
    state: str
    operation_id: str | None
    plan_sha256: Sha256 | None
    attempt_outcome: str | None
    previous_record_sha256: Sha256 | None
    checkpoint_json: str
    checkpoint_sha256: Sha256
    record_sha256: Sha256
    recorded_at: datetime

    @model_validator(mode="after")
    def utc(self):
        require_utc(self.recorded_at, "recorded_at")
        return self


class NativeAvailabilityBundleV5(AvailabilityContractV5):
    schema_version: str = Field(
        default="ctcc.mie.native_availability_raw_bundle.v5",
        pattern=r"^ctcc\.mie\.native_availability_raw_bundle\.v5$",
    )
    ordinal_claim_sha256: Sha256
    clock_v4: PostPublicationClockEvidenceV4
    checkpoint: PublicJournalCheckpointV1
    files: tuple[PackedPublicFileV5, ...] = Field(min_length=1, max_length=65536)
    witness_rows: tuple[WitnessRawRowV5, ...] = Field(min_length=4, max_length=8193)
    deployment_json_base64: str
    deployment_sha256: Sha256

    @model_validator(mode="after")
    def replay(self):
        if len({f.path for f in self.files}) != len(self.files):
            raise ValueError("availability_bundle_duplicate_path")
        deployment = base64.b64decode(self.deployment_json_base64, validate=True)
        if (
            not 0 < len(deployment) <= 65536
            or sha(deployment) != self.deployment_sha256
            or canonical(decode(deployment, 65536)) != deployment
        ):
            raise ValueError("availability_bundle_deployment_invalid")
        _replay_source(self)
        return self


class _Files:
    def __init__(self, files, prefix=""):
        self.files, self.prefix = files, prefix

    def names(self):
        return tuple(
            dict.fromkeys(
                p[len(self.prefix) :].split("/")[0]
                for p in self.files
                if p.startswith(self.prefix)
            )
        )

    def read(self, name, maximum):
        raw = self.files[self.prefix + name]
        if len(raw) > maximum:
            raise ValueError("availability_bundle_file_budget")
        return raw

    @contextmanager
    def child(self, name):
        yield _Files(self.files, self.prefix + name + "/")


def _replay_source(bundle):
    files = {f.path: f.raw() for f in bundle.files}
    root = _Files(files)
    point, source = bundle.checkpoint, bundle.clock_v4.source_v3
    genesis_raw = root.read("genesis.json", MAX_FILE_BYTES)
    genesis = decode(genesis_raw, MAX_FILE_BYTES)
    if (
        canonical(genesis) != genesis_raw
        or set(genesis) != {"schema_version", "journal_id", "root_device", "root_inode"}
        or genesis["schema_version"] != "ctcc.public.journal_genesis.v1"
        or type(genesis["root_device"]) is not int
        or type(genesis["root_inode"]) is not int
        or type(genesis["journal_id"]) is not str
        or re.fullmatch(r"[0-9a-f]{32}", genesis["journal_id"]) is None
        or sha(genesis_raw) != point.genesis_sha256
        or (genesis["root_device"], genesis["root_inode"])
        != (point.root_device, point.root_inode)
    ):
        raise ValueError("availability_bundle_genesis_mismatch")
    raw_rows = tuple(SimpleNamespace(**r.model_dump()) for r in bundle.witness_rows)
    verify_witness_chain(raw_rows)
    observed = source.post_read_observation
    selected = verify_witness_chain(raw_rows[: observed.witness_revision + 1])
    if (
        selected.record_sha256 != observed.witness_record_sha256
        or selected.checkpoint != point
        or selected.transition != "append_capture"
        or selected.state != "idle"
        or selected.attempt_outcome != "completed_collection"
        or bundle.witness_rows[observed.witness_revision].recorded_at
        != observed.witness_recorded_at
        or selected.journal_key != point.genesis_sha256
        or selected.journal_id != genesis["journal_id"]
    ):
        raise ValueError("availability_bundle_witness_mismatch")
    if (
        point.canonical_sha256() != source.v2_capture.capture_checkpoint_sha256
        or point.sequence != source.v2_capture.capture_sequence
    ):
        raise ValueError("availability_bundle_checkpoint_mismatch")
    with root.child("attempts") as directory:
        attempts, _ = replay_attempt_chain(
            directory,
            sequence=point.attempt_sequence,
            expected_head=point.attempt_head_sha256,
            genesis_sha256=point.genesis_sha256,
        )
    expected_names = {"genesis.json", "attempts"}
    previous, seen, first_rows, selected_receipt = point.genesis_sha256, set(), {}, None
    for sequence in range(1, point.sequence + 1):
        name = f"capture-{sequence:08d}"
        expected_names.add(name)
        with root.child(name) as directory:
            entry_raw = directory.read("entry.json", MAX_FILE_BYTES)
            entry = decode(entry_raw, MAX_FILE_BYTES)
            if (
                canonical(entry) != entry_raw
                or set(entry)
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
                raise ValueError("availability_bundle_capture_chain_invalid")
            receipt_raw = directory.read("receipt.json", MAX_FILE_BYTES)
            receipt = MeasuredPublicMinuteReceiptV1.model_validate_json(receipt_raw)
            raw = {n: directory.read(n, MAX_FILE_BYTES) for n, _ in receipt.raw_files}
            if (
                sha(receipt_raw) != entry["receipt_sha256"]
                or receipt.canonical_bytes() != receipt_raw
                or receipt.capture_id in seen
                or receipt.transport_origin != "owned_native_tls"
                or set(directory.names()) != {"entry.json", "receipt.json", *raw}
            ):
                raise ValueError("availability_bundle_capture_invalid")
            seen.add(receipt.capture_id)
            replay_public_capture(receipt, raw)
            bind_measured_attempt(receipt, raw, attempts)
            validate_stamps(
                [receipt.validation_complete, entry["payload_readback_complete"]]
            )
            conflicts = [
                r["identity"]
                for r in receipt.rows
                if r["identity"] in first_rows
                and first_rows[r["identity"]] != r["content_sha256"]
            ]
            if (
                conflicts
                or entry["source_conflicts"]
                or entry["disposition"] != "accepted"
            ):
                raise ValueError("availability_bundle_disputed_source")
            for locator in receipt.rows:
                first_rows.setdefault(locator["identity"], locator["content_sha256"])
            if receipt.capture_id == source.v2_capture.capture_id:
                selected_receipt = receipt
                if (
                    receipt.canonical_sha256() != source.v2_capture.receipt_sha256
                    or receipt.plan_sha256 != source.v2_capture.plan_sha256
                ):
                    raise ValueError("availability_bundle_source_pin_mismatch")
                by_identity = {r.source_row_identity: r for r in source.v2_capture.rows}
                if len(by_identity) != len(receipt.rows):
                    raise ValueError("availability_bundle_row_count_mismatch")
                for locator in receipt.rows:
                    row = parsed_rows(raw[f"page-{locator['page_index']:03d}.raw"])[0][
                        locator["row_ordinal"]
                    ]
                    expected = by_identity[locator["identity"]]
                    bar = FeatureBar(
                        closed_at=utc_from_ns(int(row[0]) * 1_000_000 + 60_000_000_000),
                        open=Decimal(row[1]),
                        high=Decimal(row[2]),
                        low=Decimal(row[3]),
                        close=Decimal(row[4]),
                        volume=Decimal(row[5]),
                    )
                    if (
                        expected.bar != bar
                        or expected.source_row_sha256 != locator["content_sha256"]
                        or expected.raw_page_sha256 != locator["raw_sha256"]
                    ):
                        raise ValueError("availability_bundle_row_mismatch")
            previous = sha(entry_raw)
    if (
        set(root.names()) != expected_names
        or previous != point.head_sha256
        or selected_receipt is None
    ):
        raise ValueError("availability_bundle_inventory_mismatch")


def snapshot_public_journal_v5(journal):
    """Native root reader only. Complete old journal replay brackets raw copying."""
    if type(journal) is not ControlledPublicReceiptJournal:
        raise ValueError("availability_native_journal_required")
    before = journal.checkpoint
    journal.read_all()
    result, size = [], 0

    def walk(directory, prefix="", depth=0):
        nonlocal size
        if depth > 3:
            raise ValueError("availability_bundle_directory_depth")
        for name in directory.names():
            if _NAME.fullmatch(name) is None or name in {".", ".."}:
                raise ValueError("availability_bundle_native_path_invalid")
            path = prefix + name
            if name.endswith((".json", ".raw")):
                raw = directory.read(name, MAX_FILE_BYTES)
                size += len(raw)
                if size > MAX_BUNDLE_BYTES * 3 // 4 - 1_048_576 or len(result) >= 65536:
                    raise ValueError("availability_bundle_budget")
                result.append(
                    PackedPublicFileV5(
                        path=path,
                        raw_base64=base64.b64encode(raw).decode("ascii"),
                        raw_sha256=sha(raw),
                    )
                )
            else:
                with directory.child(name) as child:
                    walk(child, path + "/", depth + 1)

    with _root_context(journal._root) as directory:
        walk(directory)
    journal.read_all()
    if journal.checkpoint != before:
        raise ValueError("availability_journal_changed")
    return before, tuple(result)


def freeze_native_availability_bundle_v5(contract):
    frozen = _freeze(contract, contract_type=NativeAvailabilityBundleV5)
    if len(frozen.payload) > MAX_BUNDLE_BYTES:
        raise ValueError("availability_bundle_budget")
    return frozen


def replay_native_availability_bundle_v5(payload, *, expected_sha256):
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_BUNDLE_BYTES:
        raise ValueError("availability_bundle_budget")
    return _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=NativeAvailabilityBundleV5,
    )
