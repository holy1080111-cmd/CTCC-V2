"""Synthetic byte/coordinate tests; no real seal, TLS capture, or OOS claim."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.blind_window_dataset import (
    BlindWindowCapturePin,
    BlindWindowDatasetError,
    BlindWindowJournalSegment,
    CompleteBlindWindowDataset,
    _rows_sha256,
    bind_complete_blind_window_dataset,
    verify_complete_blind_window_dataset,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.public_market_source import public_receipt_storage as storage
from app.public_market_source.public_market_receipts import PublicMinuteCapturePlanV1
from tests.unit.mie.test_gate3_prospective import valid_prospective_preregistration
from tests.unit.research import public_receipt_fixtures as fixtures
from tests.unit.research.test_public_journal_contracts import fixture_chain


def fixture_inputs(monkeypatch, *, first_plan_created_ns=None):
    """Four forged owned-labelled minutes in four synthetic rotated journals."""
    start = datetime(2026, 10, 1, tzinfo=UTC)
    original_plan_for = fixtures.plan_for
    captures = {}
    segments = []
    for minute in range(2):
        for symbol in ("BTC", "ETH"):
            monkeypatch.setattr(
                fixtures,
                "START",
                int((start + timedelta(minutes=minute)).timestamp()) * 1_000_000_000,
            )

            def plan_for(rows, *, instrument=symbol, minute_index=minute):
                old = original_plan_for(rows)
                return PublicMinuteCapturePlanV1.model_validate(
                    {
                        **old.model_dump(),
                        "instrument_id": f"{instrument}-USDT-SWAP",
                        "created_ns": (
                            first_plan_created_ns
                            if minute_index == 0
                            and instrument == "BTC"
                            and first_plan_created_ns is not None
                            else old.start_ns - 1_000_000_000
                        ),
                    }
                )

            monkeypatch.setattr(fixtures, "plan_for", plan_for)
            _, _, directory, checkpoint = fixture_chain(monkeypatch, rows=1)
            entries, first_rows = storage._replay_directory(directory, checkpoint)
            # The in-memory fixture reuses one fake inode; emulate distinct
            # journal roots after the source bytes have been replayed.
            checkpoint = checkpoint.model_copy(
                update={"root_inode": 34 + len(segments)}
            )
            plan, receipt, files, entry = entries[0]
            # The synthetic journal's owned/attempt label is forged only to
            # exercise this pure binder; it proves no real attempt or TLS.
            receipt = type(receipt).model_validate(
                {**receipt.model_dump(), "attempt_sha256": "a" * 64}
            )
            journal = object.__new__(storage.ControlledPublicReceiptJournal)
            journal._checkpoint = checkpoint
            captures[id(journal)] = (((plan, receipt, files, entry),), first_rows)
            segments.append(
                BlindWindowJournalSegment(
                    journal=journal,
                    expected_checkpoint_sha256=checkpoint.canonical_sha256(),
                    capture_pins=(
                        BlindWindowCapturePin(
                            capture_id=receipt.capture_id,
                            plan_sha256=plan.canonical_sha256(),
                            receipt_sha256=receipt.canonical_sha256(),
                        ),
                    ),
                )
            )
    monkeypatch.setattr(
        storage.ControlledPublicReceiptJournal,
        "read_all",
        lambda journal: captures[id(journal)],
    )
    source = valid_prospective_preregistration().model_dump(mode="python")
    source["training_dataset"]["source"] = "okx.public_market_source"
    source["training_dataset"]["source_version"] = "okx.history_candles.nine_strings.v1"
    holdout = source["prospective_holdout"]
    holdout.update(
        {
            "source": "okx.public_market_source",
            "source_version": "okx.history_candles.nine_strings.v1",
            "artifact_interval_seconds": 60,
            "start_at": start,
            "end_at": start + timedelta(minutes=2),
            "publication_lag_seconds": 60,
            "first_permitted_access_at": start + timedelta(minutes=3),
            "expected_artifact_count": 4,
            "expected_rows": 4,
        }
    )
    seal = Gate3ProspectivePreregistration.model_validate(source)
    return {
        "preregistration": seal,
        "expected_preregistration_sha256": seal.canonical_sha256(),
        "segments": tuple(segments),
    }, captures


def test_full_two_symbol_window_rotates_and_verifies_original_bytes(monkeypatch):
    args, _ = fixture_inputs(monkeypatch)
    dataset = bind_complete_blind_window_dataset(**args)
    assert dataset.expected_rows == len(dataset.rows) == 4
    assert dataset.instrument_ids == ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
    assert len(dataset.journal_checkpoint_sha256s) == 4
    assert [item.instrument_id for item in dataset.rows] == [
        "BTC-USDT-SWAP",
        "ETH-USDT-SWAP",
        "BTC-USDT-SWAP",
        "ETH-USDT-SWAP",
    ]
    assert all(item.decision_available_at == item.retained_at for item in dataset.rows)
    assert dataset.rows_sha256 == _rows_sha256(dataset.rows)
    assert dataset.predictive_oos_eligible is False
    assert dataset.evaluator_first_read_proven is False
    assert dataset.execution_authority is False
    payload = dataset.canonical_json_bytes()
    assert (
        verify_complete_blind_window_dataset(
            payload, expected_sha256=hashlib.sha256(payload).hexdigest(), **args
        )
        == dataset
    )


@pytest.mark.parametrize("created_at_boundary", ["before_seal", "first_event"])
def test_plan_must_be_strictly_after_seal_and_before_first_event(
    monkeypatch, created_at_boundary
):
    if created_at_boundary == "before_seal":
        created_ns = (
            int(datetime(2026, 9, 2, tzinfo=UTC).timestamp()) * 1_000_000_000 - 1
        )
    else:
        created_ns = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp()) * 1_000_000_000
    args, _ = fixture_inputs(monkeypatch, first_plan_created_ns=created_ns)
    with pytest.raises(BlindWindowDatasetError, match="source or pin"):
        bind_complete_blind_window_dataset(**args)


def test_missing_or_reordered_coordinate_fails_closed(monkeypatch):
    args, _ = fixture_inputs(monkeypatch)
    with pytest.raises(BlindWindowDatasetError, match="incomplete"):
        bind_complete_blind_window_dataset(
            **{**args, "segments": args["segments"][:-1]}
        )
    reordered = (
        args["segments"][1],
        args["segments"][0],
        *args["segments"][2:],
    )
    with pytest.raises(BlindWindowDatasetError, match="coordinate"):
        bind_complete_blind_window_dataset(**{**args, "segments": reordered})


def test_duplicate_segment_or_source_fails_closed(monkeypatch):
    args, captures = fixture_inputs(monkeypatch)
    with pytest.raises(BlindWindowDatasetError, match="checkpoint"):
        bind_complete_blind_window_dataset(
            **{**args, "segments": (args["segments"][0],) * 4}
        )
    first, second = args["segments"][:2]
    captures[id(second.journal)] = captures[id(first.journal)]
    with pytest.raises(BlindWindowDatasetError, match="source or pin"):
        bind_complete_blind_window_dataset(**args)


def test_rotation_requires_distinct_journal_root_identity(monkeypatch):
    args, _ = fixture_inputs(monkeypatch)
    first, second = args["segments"][:2]
    original = second.journal.checkpoint
    second.journal._checkpoint = original.model_copy(
        update={"root_inode": first.journal.checkpoint.root_inode}
    )
    repeated_root = BlindWindowJournalSegment(
        journal=second.journal,
        expected_checkpoint_sha256=second.journal.checkpoint.canonical_sha256(),
        capture_pins=second.capture_pins,
    )
    with pytest.raises(BlindWindowDatasetError, match="checkpoint"):
        bind_complete_blind_window_dataset(
            **{**args, "segments": (first, repeated_root, *args["segments"][2:])}
        )


def test_late_readback_and_conflicting_revision_fail_closed(monkeypatch):
    args, captures = fixture_inputs(monkeypatch)
    segment = args["segments"][-1]
    entries, first_rows = captures[id(segment.journal)]
    plan, receipt, files, entry = entries[0]
    at_first_access = dict(entry)
    at_first_access["payload_readback_complete"] = {
        **at_first_access["payload_readback_complete"],
        "utc_ns": int(datetime(2026, 10, 1, 0, 3, tzinfo=UTC).timestamp())
        * 1_000_000_000,
    }
    captures[id(segment.journal)] = (
        ((plan, receipt, files, at_first_access),),
        first_rows,
    )
    with pytest.raises(ValidationError, match="sealed coordinates"):
        bind_complete_blind_window_dataset(**args)
    late = dict(entry)
    late["payload_readback_complete"] = {
        **late["payload_readback_complete"],
        "utc_ns": int(datetime(2026, 10, 1, 0, 4, tzinfo=UTC).timestamp())
        * 1_000_000_000,
    }
    captures[id(segment.journal)] = (((plan, receipt, files, late),), first_rows)
    with pytest.raises(ValidationError, match="sealed coordinates"):
        bind_complete_blind_window_dataset(**args)
    rejected = {**entry, "disposition": "rejected_source_revision"}
    captures[id(segment.journal)] = (((plan, receipt, files, rejected),), first_rows)
    with pytest.raises(BlindWindowDatasetError, match="source or pin"):
        bind_complete_blind_window_dataset(**args)


def test_raw_change_or_external_pin_change_fails_closed(monkeypatch):
    args, captures = fixture_inputs(monkeypatch)
    segment = args["segments"][0]
    entries, first_rows = captures[id(segment.journal)]
    plan, receipt, files, entry = entries[0]
    changed_files = {**files, "page-000.raw": files["page-000.raw"] + b" "}
    captures[id(segment.journal)] = (
        ((plan, receipt, changed_files, entry),),
        first_rows,
    )
    with pytest.raises(ValueError):
        bind_complete_blind_window_dataset(**args)
    captures[id(segment.journal)] = (entries, first_rows)
    wrong = BlindWindowJournalSegment(
        journal=segment.journal,
        expected_checkpoint_sha256="0" * 64,
        capture_pins=segment.capture_pins,
    )
    with pytest.raises(BlindWindowDatasetError, match="checkpoint"):
        bind_complete_blind_window_dataset(
            **{**args, "segments": (wrong, *args["segments"][1:])}
        )


def test_rehashed_metadata_cannot_replace_original_journal(monkeypatch):
    args, _ = fixture_inputs(monkeypatch)
    dataset = bind_complete_blind_window_dataset(**args)
    forged_row = dataset.rows[0].model_copy(update={"source_row_sha256": "0" * 64})
    forged_rows = (forged_row, *dataset.rows[1:])
    forged = CompleteBlindWindowDataset.model_validate(
        {
            **dataset.model_dump(mode="python"),
            "rows": forged_rows,
            "rows_sha256": _rows_sha256(forged_rows),
        }
    )
    payload = forged.canonical_json_bytes()
    with pytest.raises(ArtifactVerificationError, match="original source"):
        verify_complete_blind_window_dataset(
            payload, expected_sha256=hashlib.sha256(payload).hexdigest(), **args
        )
    with pytest.raises(ValidationError):
        CompleteBlindWindowDataset.model_validate(
            {**dataset.model_dump(mode="python"), "predictive_oos_eligible": True}
        )
