"""Synthetic full-grid controls for the computational campaign batch seam.

These rows exercise shape and arithmetic only. They are not a source receipt,
independent custody observation, predictive claim, or trading authority.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.source_primitives import sha
from app.mie.features import FeatureBar
from app.mie.validation.availability import AvailabilityBasis, AvailabilityProvenance
from app.mie.validation.batch_replay import BoundMinute
from app.mie.validation.prospective_devval_campaign_batch_v1 import (
    DevvalCampaignBatchError,
    _build_batch,
)
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    ProspectiveDevvalCampaignPlanV1,
    freeze_prospective_devval_campaign_v1,
)
from app.mie.validation.replay import PointInTimeBar
from tests.unit.mie.test_prospective_devval_campaign_plan_v1 import plan_input


def _ns(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (
        (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    ) * 1000


@pytest.fixture(scope="module")
def full_grid():
    return synthetic_grid(ProspectiveDevvalCampaignPlanV1(**plan_input()))


def synthetic_grid(plan: ProspectiveDevvalCampaignPlanV1):
    frozen = freeze_prospective_devval_campaign_v1(plan)
    campaign = frozen.contract
    rows = []
    for window in (campaign.plan.development, campaign.plan.validation):
        minute_count = (window.end_at - window.start_at) // timedelta(minutes=1)
        for minute in range(1, minute_count + 1):
            closed = window.start_at + timedelta(minutes=minute)
            available = closed + timedelta(seconds=1)
            for symbol_index, symbol in enumerate(campaign.plan.instrument_ids):
                pin = sha(
                    f"synthetic:{window.partition.value}:{minute}:{symbol}".encode()
                )
                price = (
                    Decimal(100 + symbol_index * 10)
                    + Decimal(minute % 127) / Decimal(100)
                    + Decimal((minute // 127) % 7) / Decimal(10)
                )
                row = PointInTimeBar(
                    source_row_id="synthetic:" + pin,
                    source_row_sha256=pin,
                    instrument_id=symbol,
                    available_at=available,
                    bar=FeatureBar(
                        closed_at=closed,
                        open=price,
                        high=price + Decimal(2),
                        low=price - Decimal(1),
                        close=price + Decimal("0.25"),
                        volume=Decimal(1 + minute % 11),
                    ),
                )
                rows.append(
                    BoundMinute(
                        row=row,
                        availability=AvailabilityProvenance(
                            basis=AvailabilityBasis.MEASURED_ROW_RECEIPT,
                            source_row_sha256=pin,
                            receipt_sha256=pin,
                            bar_closed_at=closed,
                            observed_at=available,
                            retrieved_at=available,
                            available_at=available,
                        ),
                    )
                )
    assert len(rows) == campaign.plan.expected_rows == 20_160
    pins = {
        "completion_sha256": sha(b"synthetic-completion"),
        "root_binding_sha256": sha(b"synthetic-root-binding"),
        "row_inventory_sha256": sha(b"synthetic-original-replay-inventory"),
        "development_cutoff_utc_ns": _ns(campaign.plan.development.end_at)
        + 2_000_000_000,
        "validation_cutoff_utc_ns": _ns(campaign.plan.validation.end_at)
        + 2_000_000_000,
    }
    return campaign, tuple(rows), pins


def test_full_measured_grid_aggregates_and_replays_deterministically(full_grid):
    campaign, rows, pins = full_grid
    first = _build_batch(campaign, rows, **pins)
    second = _build_batch(campaign, rows, **pins)
    assert first.payload == second.payload
    assert first.sha256 == second.sha256
    contract = first.contract
    assert contract.campaign_sha256 == campaign.canonical_sha256()
    assert contract.completion_sha256 == pins["completion_sha256"]
    assert not contract.predictive_oos_eligible
    assert not contract.execution_authority
    assert [
        (
            series.aggregation.plan.window.partition.value,
            series.aggregation.plan.instrument_id,
        )
        for series in contract.series
    ] == [
        ("development", "BTC-USDT-SWAP"),
        ("development", "ETH-USDT-SWAP"),
        ("validation", "BTC-USDT-SWAP"),
        ("validation", "ETH-USDT-SWAP"),
    ]
    for series in contract.series:
        assert series.aggregation.plan.expected_minute_rows == 5_040
        assert (
            series.aggregation.plan.source_manifest_sha256
            == campaign.canonical_sha256()
        )
        assert series.aggregation.plan.volume_unit == "contracts"
        assert [len(frame.bars) for frame in series.aggregation.timeframes] == [
            336,
            84,
            21,
        ]
        assert len(series.features) == 3


def test_missing_or_reordered_original_coordinate_is_refused(full_grid):
    campaign, rows, pins = full_grid
    with pytest.raises(DevvalCampaignBatchError, match="cutoff_or_count_invalid"):
        _build_batch(campaign, rows[:-1], **pins)
    changed = list(rows)
    changed[0], changed[1] = changed[1], changed[0]
    with pytest.raises(DevvalCampaignBatchError, match="minute_changed"):
        _build_batch(campaign, tuple(changed), **pins)


@pytest.mark.parametrize("defect", ("late", "assumed"))
def test_late_or_assumed_minute_cannot_enter_measured_batch(full_grid, defect):
    campaign, rows, pins = full_grid
    changed = list(rows)
    index = 10_079 if defect == "late" else 0
    original = changed[index]
    available = (
        campaign.plan.development.end_at + timedelta(seconds=3)
        if defect == "late"
        else original.row.bar.closed_at
    )
    basis = (
        AvailabilityBasis.MEASURED_ROW_RECEIPT
        if defect == "late"
        else AvailabilityBasis.ASSUMED_BAR_CLOSE
    )
    changed[index] = BoundMinute(
        row=original.row.model_copy(update={"available_at": available}),
        availability=original.availability.model_copy(
            update={
                "basis": basis,
                "observed_at": available,
                "retrieved_at": available,
                "available_at": available,
                "assumption": "none" if defect == "late" else "confirmed_at_bar_close",
            }
        ),
    )
    with pytest.raises(DevvalCampaignBatchError, match="minute_changed"):
        _build_batch(campaign, tuple(changed), **pins)
