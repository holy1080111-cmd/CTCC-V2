"""Source/DTO/capability negatives; no database, exchange key or Arm."""

import copy

import pytest

from app.database.repositories import native_live_financial_epoch_v1 as subject


@pytest.mark.asyncio
async def test_unconfigured_factory_or_role_dto_fails_before_source_or_database(
    monkeypatch,
):
    monkeypatch.setattr(
        subject.rows_source,
        "read_owned_live_financial_rows_data_v1",
        lambda *a: pytest.fail("source accessed"),
    )
    with pytest.raises(subject.LiveFinancialEpochError):
        await subject.observe_owned_live_financial_epoch_v1(
            {"complete": True, "seed": 0}, factory=object(), role_pins=object()
        )


@pytest.mark.asyncio
async def test_constructor_key_saved_facts_or_copied_receipt_cannot_mint_financial_owner():
    with pytest.raises(subject.LiveFinancialEpochError):
        subject.OwnedLiveFinancialEpochV1(key=object())
    forged = subject.OwnedLiveFinancialEpochV1(key=subject._KEY)
    with pytest.raises(subject.LiveFinancialEpochError):
        subject._replay_owned_live_financial_epoch_v1(forged, key=subject._KEY)
    with pytest.raises(subject.LiveFinancialEpochError):
        copy.copy(forged)
    with pytest.raises(subject.LiveFinancialEpochError):
        subject._replay_owned_live_financial_epoch_v1(
            {"passed": True}, key=subject._KEY
        )


def test_constraint_plan_normalization_preserves_actual_predicate_differences():
    expected = [
        {"Plan": {"Node Type": "Seq Scan", "Filter": "equity > 0", "Total Cost": 100}}
    ]
    same = [
        {"Plan": {"Node Type": "Seq Scan", "Filter": "equity > 0", "Total Cost": 200}}
    ]
    drift = [
        {"Plan": {"Node Type": "Seq Scan", "Filter": "equity >= 0", "Total Cost": 100}}
    ]
    assert subject._plan_shape(expected) == subject._plan_shape(same)
    assert subject._plan_shape(expected) != subject._plan_shape(drift)
