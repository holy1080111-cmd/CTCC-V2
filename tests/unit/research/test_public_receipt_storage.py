from __future__ import annotations

import asyncio
import os
import shutil

import httpx
import pytest

from app.mie.validation.batch_replay import (
    MinuteAggregationPlan,
    aggregate_point_in_time_minutes,
    minute_source_sha256,
)
from app.mie.validation.contracts import DatasetPartition, PartitionWindow
from app.mie.validation.measured_public_replay import measured_public_minutes
from app.public_market_source import public_market_capture as capture
from app.public_market_source import public_receipt_storage as storage
from app.public_market_source.public_market_receipts import (
    PublicReceiptError,
    utc_from_ns,
)
from app.trade_evidence import storage as native_storage
from tests.unit.research.public_receipt_fixtures import (
    SyntheticCapture,
    storage_fixture_capture,
)


def setup(monkeypatch, tmp_path, rows=2):
    fixture = SyntheticCapture(monkeypatch, rows=rows)
    packet, files = fixture.collect()
    owned = storage_fixture_capture(packet, files)
    monkeypatch.setattr(storage, "native_stamp", fixture.stamp)
    root = tmp_path / "controlled-public-receipts"
    root.mkdir()
    journal = storage.initialize_public_receipt_journal(root)
    return fixture, owned, root, journal


def minutes(journal, receipt):
    return measured_public_minutes(
        journal=journal,
        capture_id=receipt.capture_id,
        expected_receipt_sha256=receipt.canonical_sha256(),
        expected_plan_sha256=receipt.plan_sha256,
    )


def test_native_publication_readback_restart_and_no_clobber(monkeypatch, tmp_path):
    _fixture, owned, root, journal = setup(monkeypatch, tmp_path)
    result = journal._publish_owned(owned)
    original = {p.name: p.read_bytes() for p in (root / "capture-00000001").iterdir()}
    assert result.checkpoint.sequence == 1
    assert result.execution_authority is False
    rows = minutes(journal, owned.receipt)
    assert len(rows) == 2
    assert rows[0].availability.observed_at > rows[-1].row.bar.closed_at
    reopened = storage.resume_public_receipt_journal(
        root=root, trusted_checkpoint=result.checkpoint
    )
    assert minutes(reopened, owned.receipt) == rows
    repeated = reopened._publish_owned(owned)
    assert repeated.checkpoint == result.checkpoint
    assert {
        p.name: p.read_bytes() for p in (root / "capture-00000001").iterdir()
    } == original


def test_mock_dto_never_publishes(monkeypatch, tmp_path):
    _fixture, owned, root, journal = setup(monkeypatch, tmp_path)
    with pytest.raises(PublicReceiptError, match="owned_capture_required"):
        journal._publish_owned(owned.receipt)
    assert {p.name for p in root.iterdir()} == {"genesis.json", "attempts"}


def test_native_rejected_attempt_preserves_raw_and_restarts_with_both_pins(
    monkeypatch, tmp_path
):
    # Real native storage; the response is explicitly synthetic, not TLS proof.
    fixture, _owned, root, journal = setup(monkeypatch, tmp_path)
    prior = journal.checkpoint
    raw = b'{"synthetic":"rejected response retained"}'
    fixture.mutate_response = lambda request, body: httpx.Response(
        503, stream=httpx.ByteStream(raw), headers={"content-type": "application/json"}
    )
    with pytest.raises(PublicReceiptError):
        asyncio.run(
            capture.collect_and_publish_public_minutes(
                plan=fixture.plan,
                expected_plan_sha256=fixture.plan.canonical_sha256(),
                journal=journal,
            )
        )
    retained = root / "attempts" / "attempt-00000001" / "request-000" / "chunk-000.raw"
    assert retained.read_bytes() == raw
    assert journal.checkpoint.sequence == 0
    assert journal.checkpoint.attempt_sequence == 1
    restarted = storage.resume_public_receipt_journal(
        root=root, trusted_checkpoint=journal.checkpoint
    )
    assert restarted.read_all() == ((), {})
    with pytest.raises(PublicReceiptError, match="inventory_mismatch"):
        storage.resume_public_receipt_journal(root=root, trusted_checkpoint=prior)


def test_stale_checkpoint_does_not_discover_or_trust_later_files(monkeypatch, tmp_path):
    _, owned, root, journal = setup(monkeypatch, tmp_path)
    old = journal.checkpoint
    journal._publish_owned(owned)
    with pytest.raises(PublicReceiptError, match="inventory_mismatch"):
        storage.resume_public_receipt_journal(root=root, trusted_checkpoint=old)


def test_copy_is_not_original_pinned_native_root(monkeypatch, tmp_path):
    _, owned, root, journal = setup(monkeypatch, tmp_path)
    result = journal._publish_owned(owned)
    copy = tmp_path / "copied-journal"
    shutil.copytree(root, copy)
    with pytest.raises(PublicReceiptError, match="root_identity_changed"):
        storage.resume_public_receipt_journal(
            root=copy, trusted_checkpoint=result.checkpoint
        )


@pytest.mark.parametrize(
    "name", ["time-before.raw", "page-000.raw", "receipt.json", "entry.json"]
)
def test_tampering_rejected_on_native_readback(monkeypatch, tmp_path, name):
    _, owned, root, journal = setup(monkeypatch, tmp_path)
    journal._publish_owned(owned)
    path = root / "capture-00000001" / name
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        journal.read_all()


@pytest.mark.parametrize(
    "name",
    ["time-before.raw", "page-000.raw", "time-after.raw", "receipt.json", "entry.json"],
)
def test_late_publication_failure_retains_durable_raw_and_rejects_resume(
    monkeypatch, tmp_path, name
):
    _, owned, root, journal = setup(monkeypatch, tmp_path)
    cls = (
        native_storage._WindowsDirectory
        if os.name == "nt"
        else native_storage._PosixDirectory
    )
    original = cls.publish

    def fail_after_publish(directory, filename, payload):
        original(directory, filename, payload)
        if filename == name:
            raise OSError("synthetic late filesystem failure")

    monkeypatch.setattr(cls, "publish", fail_after_publish)
    with pytest.raises(OSError):
        journal._publish_owned(owned)
    assert (root / "capture-00000001" / name).is_file()
    assert journal.checkpoint.sequence == 0
    with pytest.raises(PublicReceiptError, match="inventory_mismatch"):
        storage.resume_public_receipt_journal(
            root=root, trusted_checkpoint=journal.checkpoint
        )


def test_duplicate_observation_preserves_first_receipt(monkeypatch, tmp_path):
    fixture, first, _root, journal = setup(monkeypatch, tmp_path)
    journal._publish_owned(first)
    original = minutes(journal, first.receipt)
    packet, files = fixture.collect()
    second = storage_fixture_capture(packet, files)
    assert second.receipt.capture_id != first.receipt.capture_id
    journal._publish_owned(second)
    assert journal.checkpoint.sequence == 2
    assert minutes(journal, second.receipt) == original
    assert len(journal.read_all()[0]) == 2


def test_source_revision_conflict_preserves_old_record_and_denies(
    monkeypatch, tmp_path
):
    fixture, first, root, journal = setup(monkeypatch, tmp_path)
    journal._publish_owned(first)
    original = journal.checkpoint

    def changed(request, body):
        if request.url.path.endswith("candles"):
            body["data"][0][4] = "100.5"
        return body

    fixture.mutate_response = changed
    packet, files = fixture.collect()
    with pytest.raises(PublicReceiptError, match="source_revision_conflict"):
        journal._publish_owned(storage_fixture_capture(packet, files))
    assert journal.checkpoint.sequence == original.sequence + 1
    assert (root / "capture-00000002" / "entry.json").is_file()
    assert (root / "capture-00000002" / "page-000.raw").read_bytes() == files[
        "page-000.raw"
    ]
    assert len(journal.read_all()[0]) == 2
    with pytest.raises(PublicReceiptError, match="source_revision_conflict"):
        minutes(journal, storage_fixture_capture(packet, files).receipt)


def test_240_measured_minutes_aggregate_deterministically_with_delayed_availability(
    monkeypatch, tmp_path
):
    fixture, owned, _root, journal = setup(monkeypatch, tmp_path, rows=240)
    journal._publish_owned(owned)
    rows = minutes(journal, owned.receipt)
    window = PartitionWindow(
        partition=DatasetPartition.DEVELOPMENT,
        start_at=utc_from_ns(fixture.plan.start_ns),
        end_at=utc_from_ns(fixture.plan.end_ns),
    )
    plan = MinuteAggregationPlan(
        source_manifest_sha256=journal.checkpoint.head_sha256,
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
    one = aggregate_point_in_time_minutes(rows, **arguments)
    two = aggregate_point_in_time_minutes(rows, **arguments)
    assert one.canonical_json_bytes() == two.canonical_json_bytes()
    assert [len(frame.bars) for frame in one.timeframes] == [16, 4, 1]
    assert one.timeframes[-1].bars[0].row.available_at == max(
        row.row.available_at for row in rows
    )
    assert all(row.row.available_at > window.end_at for row in rows)
    assert all(row.availability.basis == "measured_row_receipt" for row in rows)
    assert one.predictive_oos_eligible is False


@pytest.mark.skipif(
    os.name == "nt",
    reason="native POSIX symlink case executed on Linux; Windows uses reparse handles",
)
def test_native_symlink_root_rejected(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        storage.initialize_public_receipt_journal(link)
