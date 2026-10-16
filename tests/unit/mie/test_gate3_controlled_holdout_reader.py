"""Synthetic one-use read tests; no real OOS dataset or claim is exercised."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Lock

import pytest

import app.mie.validation.controlled_holdout_reader as reader
from app.mie.validation import (
    ControlledHoldoutReadError,
    Gate3Claim,
    Gate3ProspectiveHoldoutReceipt,
    Gate3SealLedger,
    SealLedgerError,
    read_prospective_holdout_once,
    seal_ledger,
)
from tests.unit.mie.test_gate3_prospective import (
    CREATED_AT,
    FIRST_ACCESS,
    valid_prospective_preregistration,
    valid_receipt,
)

MANIFEST = b'{"artifacts":"synthetic"}\n'
DATASET = b"synthetic,public,point-in-time,rows\n"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _published(tmp_path, monkeypatch, *, eligible=True, dataset_bytes=DATASET):
    ledger_path = tmp_path / "seal.sqlite3"
    ledger = Gate3SealLedger(ledger_path)
    manifest_path = tmp_path / "manifest.json"
    dataset_path = tmp_path / "dataset.csv"
    manifest_path.write_bytes(MANIFEST)
    dataset_path.write_bytes(dataset_bytes)
    monkeypatch.setattr(seal_ledger, "_utc_now", lambda: CREATED_AT + timedelta(days=1))
    seal = ledger.publish_seal(valid_prospective_preregistration())
    monkeypatch.setattr(
        seal_ledger, "_utc_now", lambda: FIRST_ACCESS + timedelta(days=1)
    )
    original = valid_receipt()
    receipt = Gate3ProspectiveHoldoutReceipt.model_validate(
        original.model_copy(
            update={
                "holdout_dataset": original.holdout_dataset.model_copy(
                    update={
                        "manifest_sha256": _sha(MANIFEST),
                        "content_sha256": _sha(dataset_bytes),
                    }
                ),
                "all_artifacts_verified": eligible,
                "predictive_oos_eligible": eligible,
            }
        ).model_dump()
    )
    published = ledger.publish_receipt(receipt, expected_seal_sha256=seal.sha256)
    return {
        "ledger_path": ledger_path,
        "expected_seal_sha256": seal.sha256,
        "expected_receipt_sha256": published.sha256,
        "manifest_path": manifest_path,
        "dataset_path": dataset_path,
    }


def test_read_consumes_slot_before_opening_pinned_source(tmp_path, monkeypatch) -> None:
    kwargs = _published(tmp_path, monkeypatch)
    original_read = reader._read_pinned_file
    opened = []

    def after_reservation(path, digest, maximum):
        observed = Gate3SealLedger(kwargs["ledger_path"]).read_evaluation(
            kwargs["expected_seal_sha256"]
        )
        assert observed.receipt_sha256 == kwargs["expected_receipt_sha256"]
        opened.append(path)
        return original_read(path, digest, maximum)

    monkeypatch.setattr(reader, "_read_pinned_file", after_reservation)
    result = read_prospective_holdout_once(**kwargs)
    assert opened == [kwargs["manifest_path"], kwargs["dataset_path"]]
    assert result.manifest_bytes == MANIFEST
    assert result.dataset_bytes == DATASET
    assert result.current_claim == Gate3Claim.COMPUTATIONAL
    assert result.predictive_oos_eligible is False
    assert result.execution_authority is False
    assert (
        Gate3SealLedger(kwargs["ledger_path"]).read_evaluation(
            kwargs["expected_seal_sha256"]
        )
        == result.reservation
    )

    opened.clear()
    with pytest.raises(SealLedgerError, match="already consumed"):
        read_prospective_holdout_once(**kwargs)
    assert opened == []


def test_competing_readers_get_at_most_one_source_open(tmp_path, monkeypatch) -> None:
    kwargs = _published(tmp_path, monkeypatch)
    opened = []
    lock = Lock()
    original_read = reader._read_pinned_file

    def counted_read(path, digest, maximum):
        with lock:
            opened.append(path)
        return original_read(path, digest, maximum)

    monkeypatch.setattr(reader, "_read_pinned_file", counted_read)

    def attempt():
        try:
            return read_prospective_holdout_once(**kwargs)
        except SealLedgerError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))
    accepted = [item for item in outcomes if not isinstance(item, SealLedgerError)]
    denied = [item for item in outcomes if isinstance(item, SealLedgerError)]
    assert len(accepted) == len(denied) == 1
    assert accepted[0].dataset_bytes == DATASET
    assert "already consumed" in str(denied[0])
    assert opened == [kwargs["manifest_path"], kwargs["dataset_path"]]


def test_crash_after_reservation_remains_consumed_after_restart(
    tmp_path, monkeypatch
) -> None:
    kwargs = _published(tmp_path, monkeypatch)
    monkeypatch.setattr(
        reader,
        "_read_pinned_file",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("synthetic crash")),
    )
    with pytest.raises(RuntimeError, match="synthetic crash"):
        read_prospective_holdout_once(**kwargs)
    assert Gate3SealLedger(kwargs["ledger_path"]).read_evaluation(
        kwargs["expected_seal_sha256"]
    )
    with pytest.raises(SealLedgerError, match="already consumed"):
        read_prospective_holdout_once(**kwargs)


@pytest.mark.parametrize("field", ["manifest_path", "dataset_path"])
def test_changed_bytes_fail_closed_and_consume_attempt(
    tmp_path, monkeypatch, field
) -> None:
    kwargs = _published(tmp_path, monkeypatch)
    kwargs[field].write_bytes(b"changed after receipt publication")
    with pytest.raises(ControlledHoldoutReadError, match="digest differs"):
        read_prospective_holdout_once(**kwargs)
    with pytest.raises(SealLedgerError, match="already consumed"):
        read_prospective_holdout_once(**kwargs)


def test_source_changed_between_metadata_and_open_is_rejected(
    tmp_path, monkeypatch
) -> None:
    kwargs = _published(tmp_path, monkeypatch)
    real_open = reader.os.open

    def changed_at_open(path, flags):
        if path == kwargs["dataset_path"]:
            path.write_bytes(b"changed during open")
        return real_open(path, flags)

    monkeypatch.setattr(reader.os, "open", changed_at_open)
    with pytest.raises(ControlledHoldoutReadError, match="changed at open"):
        read_prospective_holdout_once(**kwargs)
    with pytest.raises(SealLedgerError, match="already consumed"):
        read_prospective_holdout_once(**kwargs)


def test_independent_reservation_readback_denies_before_source_open(
    tmp_path, monkeypatch
) -> None:
    kwargs = _published(tmp_path, monkeypatch)
    original_read = Gate3SealLedger.read_evaluation
    reads = 0

    def corrupted_second_read(self, digest):
        nonlocal reads
        reads += 1
        observed = original_read(self, digest)
        if reads == 2:
            return replace(observed, reservation_id="0" * 32)
        return observed

    monkeypatch.setattr(Gate3SealLedger, "read_evaluation", corrupted_second_read)
    monkeypatch.setattr(
        reader,
        "_read_pinned_file",
        lambda *_args: pytest.fail("source opened before durable readback"),
    )
    with pytest.raises(ControlledHoldoutReadError, match="readback differs"):
        read_prospective_holdout_once(**kwargs)
    assert reads == 2
    with pytest.raises(SealLedgerError, match="already consumed"):
        read_prospective_holdout_once(**kwargs)


def test_missing_ledger_does_not_create_a_new_slot(tmp_path) -> None:
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(ControlledHoldoutReadError, match="existing absolute"):
        read_prospective_holdout_once(
            ledger_path=missing,
            expected_seal_sha256="0" * 64,
            expected_receipt_sha256="1" * 64,
            manifest_path=tmp_path / "missing-manifest",
            dataset_path=tmp_path / "missing-dataset",
        )
    assert not missing.exists()


def test_ineligible_receipt_consumes_attempt_without_opening_source(
    tmp_path, monkeypatch
) -> None:
    kwargs = _published(tmp_path, monkeypatch, eligible=False)
    monkeypatch.setattr(
        reader,
        "_read_pinned_file",
        lambda *_args: pytest.fail("ineligible receipt opened source"),
    )
    with pytest.raises(ControlledHoldoutReadError, match="ineligible"):
        read_prospective_holdout_once(**kwargs)
    with pytest.raises(SealLedgerError, match="already consumed"):
        read_prospective_holdout_once(**kwargs)


def test_parent_alias_to_same_file_consumes_slot_without_returning_bytes(
    tmp_path, monkeypatch
) -> None:
    kwargs = _published(tmp_path, monkeypatch, dataset_bytes=MANIFEST)
    (tmp_path / "alias-parent").mkdir()
    alias = tmp_path / "alias-parent" / ".." / "manifest.json"
    assert alias != kwargs["manifest_path"]
    assert alias.samefile(kwargs["manifest_path"])
    kwargs["dataset_path"] = alias
    monkeypatch.setattr(
        reader,
        "_read_pinned_file",
        lambda *_args: pytest.fail("aliased source was opened"),
    )
    with pytest.raises(ControlledHoldoutReadError, match="same file"):
        read_prospective_holdout_once(**kwargs)
    with pytest.raises(SealLedgerError, match="already consumed"):
        read_prospective_holdout_once(**kwargs)


def test_missing_alias_identity_fails_closed_before_open(tmp_path, monkeypatch) -> None:
    kwargs = _published(tmp_path, monkeypatch)
    kwargs["dataset_path"] = tmp_path / "missing-dataset.csv"
    monkeypatch.setattr(
        reader,
        "_read_pinned_file",
        lambda *_args: pytest.fail("source opened after identity error"),
    )
    with pytest.raises(ControlledHoldoutReadError, match="identity check failed"):
        read_prospective_holdout_once(**kwargs)
    with pytest.raises(SealLedgerError, match="already consumed"):
        read_prospective_holdout_once(**kwargs)
