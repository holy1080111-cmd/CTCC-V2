"""Synthetic D/V pilot contracts; no future capture or predictive claim is made."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.prospective_development_validation_checkpoint import (
    DevelopmentValidationCollectionCheckpointV1,
    audit_development_validation_journal,
    verify_development_validation_checkpoint,
)
from app.mie.validation.prospective_development_validation_plan import (
    INSTRUMENTS,
    MAX_PILOT_ROWS,
    ProspectiveDevelopmentValidationPlanV1,
)
from app.public_market_source.public_receipt_storage import (
    ControlledPublicReceiptJournal,
    initialize_public_receipt_journal,
)

START = datetime(2030, 1, 1, tzinfo=UTC)


def pilot(*, development_minutes: int = 2, validation_minutes: int = 2):
    validation_start = START + timedelta(minutes=development_minutes, hours=1)
    return ProspectiveDevelopmentValidationPlanV1(
        plan_id="gate3:future:devval:pilot:one",
        planned_at=START - timedelta(minutes=5),
        origin="https://openapi.okx.com",
        development_start_at=START,
        development_end_at=START + timedelta(minutes=development_minutes),
        validation_start_at=validation_start,
        validation_end_at=validation_start + timedelta(minutes=validation_minutes),
        expected_rows=2 * (development_minutes + validation_minutes),
    )


def empty_journal(tmp_path):
    root = tmp_path / "owned-source-journal"
    root.mkdir()
    return initialize_public_receipt_journal(root)


def test_future_dual_symbol_grid_is_deterministic_and_source_only():
    first = pilot()
    second = pilot()
    assert first.canonical_sha256() == second.canonical_sha256()
    assert first.instrument_ids == INSTRUMENTS
    assert first.expected_rows == 8
    assert tuple(first.coordinate(i) for i in range(8)) == (
        ("development", START, INSTRUMENTS[0]),
        ("development", START, INSTRUMENTS[1]),
        ("development", START + timedelta(minutes=1), INSTRUMENTS[0]),
        ("development", START + timedelta(minutes=1), INSTRUMENTS[1]),
        ("validation", START + timedelta(hours=1, minutes=2), INSTRUMENTS[0]),
        ("validation", START + timedelta(hours=1, minutes=2), INSTRUMENTS[1]),
        (
            "validation",
            START + timedelta(hours=1, minutes=3),
            INSTRUMENTS[0],
        ),
        (
            "validation",
            START + timedelta(hours=1, minutes=3),
            INSTRUMENTS[1],
        ),
    )
    for ordinal in range(first.expected_rows):
        source = first.capture_plan(ordinal)
        _, opening, instrument = first.coordinate(ordinal)
        assert source.instrument_id == instrument
        assert source.start_ns == int(opening.timestamp() * 1_000_000_000)
        assert source.end_ns - source.start_ns == 60_000_000_000
        assert source.expected_rows == source.page_size == source.max_pages == 1
        assert source.predictive_oos_eligible is False
        assert source.execution_authority is False
    assert first.candidate_fitted is False
    assert first.holdout_accessed is False
    assert first.predictive_oos_eligible is False
    assert first.runtime_consumers == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"instrument_ids": ("BTC-USDT-SWAP", "BTC-USDT-SWAP")},
        {"instrument_ids": ("ETH-USDT-SWAP", "BTC-USDT-SWAP")},
        {"development_start_at": START + timedelta(seconds=1)},
        {"development_end_at": START},
        {"validation_start_at": START + timedelta(minutes=1)},
        {"planned_at": START},
        {"expected_rows": 6},
        {"predictive_oos_eligible": True},
        {"candidate_fitted": True},
        {"execution_authority": True},
    ],
)
def test_pilot_rejects_changed_grid_or_authority(changes):
    with pytest.raises(ValueError):
        ProspectiveDevelopmentValidationPlanV1.model_validate(
            {**pilot().model_dump(mode="python"), **changes}
        )


def test_pilot_cannot_exceed_one_journal_budget():
    with pytest.raises(ValueError):
        pilot(development_minutes=241, validation_minutes=240)
    assert pilot(development_minutes=240, validation_minutes=240).expected_rows == (
        MAX_PILOT_ROWS
    )


def test_empty_owned_journal_checkpoint_names_every_unobserved_minute(tmp_path):
    plan = pilot()
    journal = empty_journal(tmp_path)
    kwargs = {
        "plan": plan,
        "expected_plan_sha256": plan.canonical_sha256(),
        "journal": journal,
        "expected_journal_checkpoint_sha256": journal.checkpoint.canonical_sha256(),
    }
    first = audit_development_validation_journal(**kwargs)
    second = audit_development_validation_journal(**kwargs)
    assert first.payload == second.payload
    assert first.sha256 == second.sha256
    assert first.contract.status == "INCOMPLETE"
    assert first.contract.missing_ordinals == tuple(range(plan.expected_rows))
    assert first.contract.observed_captures == 0
    assert first.contract.all_planned_minutes_replayed_locally is False
    assert first.contract.predictive_oos_eligible is False
    assert first.contract.source_checkpoint_custody_independently_proven is False
    assert (
        verify_development_validation_checkpoint(
            first.payload, expected_sha256=first.sha256, **kwargs
        )
        == first.contract
    )
    with pytest.raises(ValueError, match="external plan or journal checkpoint"):
        audit_development_validation_journal(
            **{**kwargs, "expected_plan_sha256": "a" * 64}
        )
    with pytest.raises(ArtifactVerificationError, match="SHA256 mismatch"):
        verify_development_validation_checkpoint(
            first.payload, expected_sha256="a" * 64, **kwargs
        )
    with pytest.raises(ValueError):
        DevelopmentValidationCollectionCheckpointV1.model_validate(
            {**first.contract.model_dump(mode="python"), "missing_ordinals": ()}
        )


def _synthetic_entry(plan, ordinal, number, *, bound=True, late=False, conflict=False):
    source = plan.capture_plan(ordinal)
    receipt = SimpleNamespace(
        plan_sha256=source.canonical_sha256(),
        rows=({"identity": f"{ordinal + 1:064x}"},),
        attempt_sha256=f"{number + 100:064x}" if bound else None,
        validation_complete={
            "utc_ns": source.end_ns + (61 if late else 1) * 1_000_000_000
        },
        canonical_sha256=lambda: f"{number:064x}",
    )
    entry = {
        "disposition": "rejected_source_revision" if conflict else "accepted",
        "source_conflicts": [receipt.rows[0]["identity"]] if conflict else [],
        "payload_readback_complete": receipt.validation_complete,
    }
    return source, receipt, {}, entry


def test_synthetic_replay_classifies_gaps_duplicates_late_and_unbound(
    tmp_path, monkeypatch
):
    plan = pilot()
    journal = empty_journal(tmp_path)
    entries = (
        _synthetic_entry(plan, 0, 1),
        _synthetic_entry(plan, 1, 2, late=True),
        _synthetic_entry(plan, 2, 3, bound=False),
        _synthetic_entry(plan, 3, 4, conflict=True),
        _synthetic_entry(plan, 4, 5),
        _synthetic_entry(plan, 4, 6),
    )
    journal._checkpoint = journal.checkpoint.model_copy(
        update={"sequence": len(entries)}
    )
    # Only the checkpoint classifier is under test here. Native raw and
    # attempt-chain replay are independently tested by the owned journal suite.
    monkeypatch.setattr(
        ControlledPublicReceiptJournal, "read_all", lambda _: (entries, {})
    )
    kwargs = {
        "plan": plan,
        "expected_plan_sha256": plan.canonical_sha256(),
        "journal": journal,
        "expected_journal_checkpoint_sha256": journal.checkpoint.canonical_sha256(),
    }
    frozen = audit_development_validation_journal(**kwargs)
    result = frozen.contract
    assert result.observed_captures == 6
    assert result.coverage[0].source_state == "replayed_local_custody_unproven"
    assert result.late_ordinals == (1,)
    assert result.attempt_unbound_ordinals == (2,)
    assert result.source_conflict_ordinals == (3,)
    assert result.duplicate_ordinals == (4,)
    assert result.missing_ordinals == (5, 6, 7)
    assert result.coverage[4].capture_receipt_sha256s == (
        f"{5:064x}",
        f"{6:064x}",
    )
    assert result.all_planned_minutes_replayed_locally is False
    assert result.status == "INCOMPLETE"
    assert (
        verify_development_validation_checkpoint(
            frozen.payload, expected_sha256=frozen.sha256, **kwargs
        )
        == result
    )
    monkeypatch.setattr(
        ControlledPublicReceiptJournal, "read_all", lambda _: (entries[:-1], {})
    )
    with pytest.raises(
        ArtifactVerificationError, match="source readback is unavailable"
    ):
        verify_development_validation_checkpoint(
            frozen.payload, expected_sha256=frozen.sha256, **kwargs
        )


def test_foreign_source_plan_is_not_silently_dropped(tmp_path, monkeypatch):
    plan = pilot()
    journal = empty_journal(tmp_path)
    source, receipt, raw, entry = _synthetic_entry(plan, 0, 1)
    journal._checkpoint = journal.checkpoint.model_copy(update={"sequence": 1})
    foreign = source.model_copy(update={"instrument_id": "SOL-USDT-SWAP"})
    monkeypatch.setattr(
        ControlledPublicReceiptJournal,
        "read_all",
        lambda _: (((foreign, receipt, raw, entry),), {}),
    )
    with pytest.raises(ValueError, match="foreign source plan"):
        audit_development_validation_journal(
            plan=plan,
            expected_plan_sha256=plan.canonical_sha256(),
            journal=journal,
            expected_journal_checkpoint_sha256=journal.checkpoint.canonical_sha256(),
        )
