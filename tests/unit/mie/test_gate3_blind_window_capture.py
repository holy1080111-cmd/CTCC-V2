"""Synthetic binder tests; no real TLS, clock, holdout, or predictive claim."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.mie.validation.blind_window_capture import (
    BlindWindowMinuteCapture,
    bind_blind_window_minute,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.public_market_source import public_receipt_storage as storage
from app.public_market_source.public_market_receipts import (
    PublicMinuteCapturePlanV1,
    PublicReceiptError,
)
from tests.unit.mie.test_gate3_prospective import (
    CREATED_AT,
    valid_prospective_preregistration,
)
from tests.unit.research import public_receipt_fixtures
from tests.unit.research.test_public_journal_contracts import fixture_chain


def fixture_inputs(monkeypatch, *, plan_created_ns=None):
    # Deliberately forged owned labels exercise binding without claiming native
    # source authenticity or a real future candidate seal.
    start = datetime(2026, 10, 1, tzinfo=UTC)
    monkeypatch.setattr(
        public_receipt_fixtures, "START", int(start.timestamp()) * 1_000_000_000
    )
    original_plan_for = public_receipt_fixtures.plan_for

    def plan_for(rows):
        old = original_plan_for(rows)
        return PublicMinuteCapturePlanV1.model_validate(
            {
                **old.model_dump(),
                "created_ns": (
                    old.start_ns - 1_000_000_000
                    if plan_created_ns is None
                    else plan_created_ns
                ),
            }
        )

    monkeypatch.setattr(public_receipt_fixtures, "plan_for", plan_for)
    _, _, directory, checkpoint = fixture_chain(monkeypatch, rows=1)
    entries, first_rows = storage._replay_directory(directory, checkpoint)
    plan, receipt, files, entry = entries[0]
    receipt = type(receipt).model_validate(
        {**receipt.model_dump(), "attempt_sha256": "a" * 64}
    )
    synthetic_entries = ((plan, receipt, files, entry),)
    monkeypatch.setattr(
        storage.ControlledPublicReceiptJournal,
        "read_all",
        lambda _: (synthetic_entries, first_rows),
    )
    journal = object.__new__(storage.ControlledPublicReceiptJournal)
    journal._checkpoint = checkpoint

    source = valid_prospective_preregistration().model_dump(mode="python")
    source["training_dataset"]["source"] = "okx.public_market_source"
    source["training_dataset"]["source_version"] = "okx.history_candles.nine_strings.v1"
    source["prospective_holdout"]["source"] = "okx.public_market_source"
    source["prospective_holdout"]["source_version"] = (
        "okx.history_candles.nine_strings.v1"
    )
    sealed = Gate3ProspectivePreregistration.model_validate(source)
    args = {
        "journal": journal,
        "preregistration": sealed,
        "expected_preregistration_sha256": sealed.canonical_sha256(),
        "expected_capture_plan_sha256": plan.canonical_sha256(),
        "expected_capture_receipt_sha256": receipt.canonical_sha256(),
        "expected_journal_checkpoint_sha256": checkpoint.canonical_sha256(),
        "capture_id": receipt.capture_id,
    }
    return args


def test_one_blind_minute_binds_native_journal_shape_without_claim(monkeypatch):
    result = bind_blind_window_minute(**fixture_inputs(monkeypatch))
    assert result.bar_closed_at == datetime(2026, 10, 1, 0, 1, tzinfo=UTC)
    assert result.request_started_at >= result.bar_closed_at
    assert result.observed_at < result.bar_closed_at + timedelta(minutes=1)
    assert result.observed_at < result.first_permitted_evaluator_access_at
    assert result.source_row_count == 1
    assert result.predictive_oos_eligible is False
    assert result.execution_authority is False
    assert result.runtime_consumers == 0
    assert result.current_claim == "computational"
    assert not {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "raw_body",
    }.intersection(type(result).model_fields)
    assert (
        BlindWindowMinuteCapture.model_validate_json(result.canonical_json()) == result
    )


@pytest.mark.parametrize(
    "name",
    [
        "expected_preregistration_sha256",
        "expected_capture_plan_sha256",
        "expected_capture_receipt_sha256",
        "expected_journal_checkpoint_sha256",
        "capture_id",
    ],
)
def test_external_identity_pins_are_required(monkeypatch, name):
    args = fixture_inputs(monkeypatch)
    args[name] = "0" * (32 if name == "capture_id" else 64)
    with pytest.raises(PublicReceiptError):
        bind_blind_window_minute(**args)


def test_unbound_legacy_attempt_is_rejected(monkeypatch):
    args = fixture_inputs(monkeypatch)
    journal = args["journal"]
    entries, first_rows = journal.read_all()
    plan, receipt, files, entry = entries[0]
    legacy = type(receipt).model_validate(
        {**receipt.model_dump(), "attempt_sha256": None}
    )
    monkeypatch.setattr(
        storage.ControlledPublicReceiptJournal,
        "read_all",
        lambda _: (((plan, legacy, files, entry),), first_rows),
    )
    with pytest.raises(PublicReceiptError, match="owned_attempt_required"):
        bind_blind_window_minute(**args)


def test_wrong_source_seal_cannot_bind_okx_capture(monkeypatch):
    args = fixture_inputs(monkeypatch)
    incompatible = valid_prospective_preregistration()
    args["preregistration"] = incompatible
    args["expected_preregistration_sha256"] = incompatible.canonical_sha256()
    with pytest.raises(PublicReceiptError, match="prospective_source_incompatible"):
        bind_blind_window_minute(**args)


def test_duplicate_capture_cannot_take_first_observation_availability(monkeypatch):
    args = fixture_inputs(monkeypatch)
    journal = args["journal"]
    entries, first_rows = journal.read_all()
    plan, receipt, files, entry = entries[0]
    duplicate = type(receipt).model_validate(
        {**receipt.model_dump(), "capture_id": "b" * 32}
    )
    checkpoint = journal.checkpoint.model_copy(update={"sequence": 2})
    journal._checkpoint = checkpoint
    monkeypatch.setattr(
        storage.ControlledPublicReceiptJournal,
        "read_all",
        lambda _: (entries + ((plan, duplicate, files, entry),), first_rows),
    )
    args["capture_id"] = duplicate.capture_id
    args["expected_capture_receipt_sha256"] = duplicate.canonical_sha256()
    args["expected_journal_checkpoint_sha256"] = checkpoint.canonical_sha256()
    with pytest.raises(PublicReceiptError, match="not_first_observation"):
        bind_blind_window_minute(**args)
    args["capture_id"] = receipt.capture_id
    args["expected_capture_receipt_sha256"] = receipt.canonical_sha256()
    original = bind_blind_window_minute(**args)
    assert original.journal_sequence == 1


def test_late_or_unsealed_observation_cannot_be_relabeled_prompt(monkeypatch):
    result = bind_blind_window_minute(**fixture_inputs(monkeypatch))
    with pytest.raises(ValidationError, match="causally and promptly"):
        BlindWindowMinuteCapture.model_validate(
            {
                **result.model_dump(mode="python"),
                "observed_at": result.bar_closed_at + timedelta(minutes=2),
            }
        )
    with pytest.raises(ValidationError):
        BlindWindowMinuteCapture.model_validate(
            {**result.model_dump(mode="python"), "predictive_oos_eligible": True}
        )
    with pytest.raises(ValidationError, match="outside the sealed holdout"):
        BlindWindowMinuteCapture.model_validate(
            {
                **result.model_dump(mode="python"),
                "holdout_start_at": result.bar_closed_at,
            }
        )


@pytest.mark.parametrize(
    ("created_ns", "accepted"),
    [
        (int(CREATED_AT.timestamp()) * 1_000_000_000 - 1, False),
        (int(CREATED_AT.timestamp()) * 1_000_000_000, False),
        (int(CREATED_AT.timestamp()) * 1_000_000_000 + 1, True),
        (int(datetime(2026, 10, 1, tzinfo=UTC).timestamp()) * 1_000_000_000, False),
    ],
)
def test_plan_creation_strictly_between_seal_and_first_event(
    monkeypatch, created_ns, accepted
):
    args = fixture_inputs(monkeypatch, plan_created_ns=created_ns)
    if accepted:
        result = bind_blind_window_minute(**args)
        assert result.predictive_oos_eligible is False
    else:
        with pytest.raises(PublicReceiptError, match="not_preplanned_in_window"):
            bind_blind_window_minute(**args)
