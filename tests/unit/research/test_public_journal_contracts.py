"""Pure journal/adapter replay with synthetic owned-labelled bytes.

The in-memory directory intentionally proves no native path safety or TLS. Native
positive tests remain separate in test_public_receipt_storage.py.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.mie.validation.batch_replay import (
    MinuteAggregationPlan,
    aggregate_point_in_time_minutes,
    minute_source_sha256,
)
from app.mie.validation.contracts import DatasetPartition, PartitionWindow
from app.mie.validation.measured_public_replay import measured_public_minutes
from app.public_market_source import public_receipt_storage as storage
from app.public_market_source.public_market_receipts import (
    PublicReceiptError,
    canonical,
    sha,
    utc_from_ns,
)
from tests.unit.research.public_receipt_fixtures import (
    SyntheticCapture,
    storage_fixture_capture,
)


class MemoryDirectory:
    def __init__(self, content):
        self.content = content
        self.path = Path.cwd()

    def names(self):
        return tuple(self.content)

    def read(self, name, maximum):
        value = self.content[name]
        assert type(value) is bytes and len(value) <= maximum
        return value

    def mkdir(self, name):
        if name in self.content:
            raise FileExistsError(name)
        self.content[name] = {}

    def publish(self, name, payload):
        if name in self.content:
            raise FileExistsError(name)
        self.content[name] = payload

    @contextmanager
    def child(self, name):
        yield MemoryDirectory(self.content[name])


def fixture_chain(monkeypatch, rows=240):
    fixture = SyntheticCapture(monkeypatch, rows=rows)
    packet, files = fixture.collect()
    packet = storage_fixture_capture(packet, files).receipt
    genesis = canonical(
        {
            "schema_version": "ctcc.public.journal_genesis.v1",
            "journal_id": "synthetic",
            "root_device": 12,
            "root_inode": 34,
        }
    )
    marker = canonical(
        {
            "schema_version": "ctcc.public.journal_entry.v1",
            "sequence": 1,
            "previous_sha256": sha(genesis),
            "receipt_sha256": packet.canonical_sha256(),
            "payload_readback_complete": fixture.stamp(),
            "disposition": "accepted",
            "source_conflicts": [],
        }
    )
    content = {
        "genesis.json": genesis,
        "capture-00000001": {
            **files,
            "receipt.json": packet.canonical_bytes(),
            "entry.json": marker,
        },
    }
    checkpoint = storage.PublicJournalCheckpointV1(
        genesis_sha256=sha(genesis),
        root_device=12,
        root_inode=34,
        sequence=1,
        head_sha256=sha(marker),
    )
    monkeypatch.setattr(storage, "_root_identity", lambda _: (12, 34))
    return fixture, packet, MemoryDirectory(content), checkpoint


def test_full_wire_contract_and_row_locator_replay(monkeypatch):
    _, packet, directory, checkpoint = fixture_chain(monkeypatch)
    entries, index = storage._replay_directory(directory, checkpoint)
    assert entries[0][1].canonical_bytes() == packet.canonical_bytes()
    assert len(index) == 240
    assert len(entries) == 1


@pytest.mark.parametrize(
    "mutation",
    ["raw", "marker", "receipt", "extra", "partial", "genesis", "head", "device"],
)
def test_byte_and_chain_replay_rejections(monkeypatch, mutation):
    _, _, directory, checkpoint = fixture_chain(monkeypatch, rows=2)
    if mutation in {"raw", "marker", "receipt"}:
        name = {
            "raw": "page-000.raw",
            "marker": "entry.json",
            "receipt": "receipt.json",
        }[mutation]
        directory.content["capture-00000001"][name] += b" "
    elif mutation == "extra":
        directory.content["unexpected"] = b"{}"
    elif mutation == "partial":
        directory.content["capture-00000002"] = {}
    elif mutation == "genesis":
        directory.content["genesis.json"] += b" "
    elif mutation == "head":
        checkpoint = checkpoint.model_copy(update={"head_sha256": "a" * 64})
    elif mutation == "device":
        checkpoint = checkpoint.model_copy(update={"root_device": 99})
    with pytest.raises(ValueError):
        storage._replay_directory(directory, checkpoint)


def test_memory_adapter_240_minute_aggregation_is_deterministic(monkeypatch):
    fixture, packet, directory, checkpoint = fixture_chain(monkeypatch)
    verified = storage._replay_directory(directory, checkpoint)
    monkeypatch.setattr(
        storage.ControlledPublicReceiptJournal, "read_all", lambda _: verified
    )
    journal = object.__new__(storage.ControlledPublicReceiptJournal)
    rows = measured_public_minutes(
        journal=journal,
        capture_id=packet.capture_id,
        expected_receipt_sha256=packet.canonical_sha256(),
        expected_plan_sha256=packet.plan_sha256,
    )
    window = PartitionWindow(
        partition=DatasetPartition.DEVELOPMENT,
        start_at=utc_from_ns(fixture.plan.start_ns),
        end_at=utc_from_ns(fixture.plan.end_ns),
    )
    plan = MinuteAggregationPlan(
        source_manifest_sha256=checkpoint.head_sha256,
        window=window,
        instrument_id=fixture.plan.instrument_id,
        expected_minute_rows=240,
        volume_unit="contracts",
    )
    arguments = {
        "plan": plan,
        "expected_plan_sha256": plan.canonical_sha256(),
        "expected_source_sha256": minute_source_sha256(rows),
    }
    first = aggregate_point_in_time_minutes(rows, **arguments)
    second = aggregate_point_in_time_minutes(rows, **arguments)
    assert first.canonical_json_bytes() == second.canonical_json_bytes()
    assert [len(frame.bars) for frame in first.timeframes] == [16, 4, 1]
    assert all(row.row.available_at > window.end_at for row in rows)
    assert first.timeframes[-1].bars[0].row.available_at == max(
        row.row.available_at for row in rows
    )
    assert all(row.availability.basis == "measured_row_receipt" for row in rows)
    assert first.predictive_oos_eligible is False


def test_resigned_wrong_locator_is_not_accepted(monkeypatch):
    _, packet, directory, checkpoint = fixture_chain(monkeypatch, rows=2)
    data = copy.deepcopy(packet.model_dump())
    data["rows"][0]["row_ordinal"] = 1
    altered = type(packet).model_validate(data)
    directory.content["capture-00000001"]["receipt.json"] = altered.canonical_bytes()
    marker = storage.decode(directory.content["capture-00000001"]["entry.json"])
    marker["receipt_sha256"] = altered.canonical_sha256()
    encoded = canonical(marker)
    directory.content["capture-00000001"]["entry.json"] = encoded
    checkpoint = checkpoint.model_copy(update={"head_sha256": sha(encoded)})
    with pytest.raises(PublicReceiptError, match="locator_mismatch"):
        storage._replay_directory(directory, checkpoint)


def memory_publisher(monkeypatch):
    fixture = SyntheticCapture(monkeypatch, rows=2)
    directory = MemoryDirectory({})

    @contextmanager
    def context(_):
        yield directory

    monkeypatch.setattr(storage, "_root_context", context)
    monkeypatch.setattr(storage, "_root_identity", lambda _: (12, 34))
    monkeypatch.setattr(storage, "native_stamp", fixture.stamp)
    journal = storage.initialize_public_receipt_journal(Path.cwd())
    packet, files = fixture.collect()
    return fixture, directory, journal, storage_fixture_capture(packet, files)


def test_pure_publisher_keeps_conflicting_source_observation_and_rejects_adapter(
    monkeypatch,
):
    fixture, directory, journal, owned = memory_publisher(monkeypatch)
    first = journal._publish_owned(owned)
    original = copy.deepcopy(directory.content["capture-00000001"])

    def revised(request, body):
        if request.url.path.endswith("candles"):
            body["data"][0][4] = "100.5"
        return body

    fixture.mutate_response = revised
    packet, files = fixture.collect()
    changed = storage_fixture_capture(packet, files)
    with pytest.raises(PublicReceiptError, match="source_revision_conflict"):
        journal._publish_owned(changed)
    assert journal.checkpoint.sequence == first.checkpoint.sequence + 1
    assert directory.content["capture-00000001"] == original
    assert (
        directory.content["capture-00000002"]["page-000.raw"] == files["page-000.raw"]
    )
    assert journal.read_all()[0][1][3]["disposition"] == "rejected_source_revision"
    with pytest.raises(PublicReceiptError, match="source_revision_conflict"):
        measured_public_minutes(
            journal=journal,
            capture_id=changed.receipt.capture_id,
            expected_receipt_sha256=changed.receipt.canonical_sha256(),
            expected_plan_sha256=changed.receipt.plan_sha256,
        )
    with pytest.raises(PublicReceiptError, match="source_revision_conflict"):
        journal._publish_owned(changed)
    assert journal.checkpoint.sequence == 2


@pytest.mark.parametrize(
    "name",
    ["time-before.raw", "page-000.raw", "time-after.raw", "receipt.json", "entry.json"],
)
def test_pure_late_failures_preserve_written_data_and_deny_unanchored_resume(
    monkeypatch, name
):
    _, directory, journal, owned = memory_publisher(monkeypatch)
    original = MemoryDirectory.publish

    def fail_after_write(child, filename, payload):
        original(child, filename, payload)
        if filename == name:
            raise OSError("synthetic late failure")

    monkeypatch.setattr(MemoryDirectory, "publish", fail_after_write)
    with pytest.raises(OSError):
        journal._publish_owned(owned)
    assert name in directory.content["capture-00000001"]
    assert journal.checkpoint.sequence == 0
    with pytest.raises(PublicReceiptError, match="inventory_mismatch"):
        journal.read_all()
