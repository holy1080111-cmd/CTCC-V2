"""Full D/V grid and native campaign-slot binding regressions."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.source_primitives import sha
from app.mie.validation import prospective_devval_slot_v6 as slot
from app.mie.validation.contracts import DatasetPartition, PartitionWindow
from app.mie.validation.prospective_development_validation_plan import _ns
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    ProspectiveDevvalCampaignManifestV1,
    ProspectiveDevvalCampaignPlanV1,
    freeze_prospective_devval_campaign_v1,
    resolve_prospective_devval_campaign_block_v1,
    verify_prospective_devval_campaign_v1,
)
from app.public_market_source.public_market_receipts import MINUTE_NS
from tests.unit.mie.test_prospective_devval_slot_v6 import (
    DISPATCH_RECEIPT,
    ORIGINAL,
    scenario,
)

START = datetime(2030, 1, 1, tzinfo=UTC)
DEV_END = START + timedelta(hours=84)
VAL_START = DEV_END + timedelta(hours=4)
VAL_END = VAL_START + timedelta(hours=84)


def plan_input():
    return {
        "campaign_id": "devval-full-campaign",
        "planned_at": START - timedelta(hours=1),
        "declared_source_commit": "a" * 40,
        "declared_source_tree": "b" * 40,
        "manifest_sha256": "c" * 64,
        "origin": "https://openapi.okx.com",
        "development": PartitionWindow(
            partition=DatasetPartition.DEVELOPMENT,
            start_at=START,
            end_at=DEV_END,
        ),
        "validation": PartitionWindow(
            partition=DatasetPartition.VALIDATION,
            start_at=VAL_START,
            end_at=VAL_END,
        ),
        "expected_rows": 20_160,
    }


@pytest.fixture(scope="module")
def campaign():
    return freeze_prospective_devval_campaign_v1(
        ProspectiveDevvalCampaignPlanV1(**plan_input())
    )


def resolve(campaign, ordinal):
    return resolve_prospective_devval_campaign_block_v1(
        campaign.payload,
        expected_campaign_sha256=campaign.sha256,
        block_ordinal=ordinal,
    )


def test_full_grid_binds_both_partitions_and_exact_native_block_bytes(campaign):
    manifest = campaign.contract
    assert sha(campaign.payload) == campaign.sha256
    assert (
        verify_prospective_devval_campaign_v1(
            campaign.payload,
            expected_campaign_sha256=campaign.sha256,
            expected_source_commit="a" * 40,
            expected_source_tree="b" * 40,
            expected_source_manifest_sha256="c" * 64,
        )
        == manifest
    )
    assert len(manifest.source_plan_sha256s) == 168
    assert len(set(manifest.source_plan_sha256s)) == 168
    assert (
        not manifest.pre_window_commit_proven and not manifest.predictive_oos_eligible
    )
    for index, partition, opening in (
        (0, "development", START),
        (83, "development", DEV_END - timedelta(hours=1)),
        (84, "validation", VAL_START),
        (167, "validation", VAL_END - timedelta(hours=1)),
    ):
        parent, source = resolve(campaign, index)
        assert parent == manifest
        assert source.canonical_sha256() == manifest.source_plan_sha256s[index]
        assert source.expected_rows == 120
        assert all(item.partition == partition for item in source.coordinates)
        assert all(
            item.purpose == "prospective_collection" for item in source.coordinates
        )
        assert source.collection_id == f"devval:{manifest.plan_sha256}:{index:04d}"
        for local, item in enumerate(source.coordinates):
            minute, symbol = divmod(local, 2)
            assert item.ordinal == local
            assert (
                item.capture_plan.instrument_id == manifest.plan.instrument_ids[symbol]
            )
            assert item.capture_plan.start_ns == _ns(opening) + minute * MINUTE_NS
            assert item.capture_plan.end_ns == item.capture_plan.start_ns + MINUTE_NS


@pytest.mark.parametrize(
    "change", ["too_short", "gap", "holdout", "late", "rows", "alignment"]
)
def test_campaign_rejects_incomplete_or_unsafe_windows(change):
    values = plan_input()
    validation = values["validation"]
    if change == "too_short":
        values["validation"] = validation.model_copy(
            update={"end_at": VAL_END - timedelta(hours=4)}
        )
    elif change == "gap":
        values["validation"] = validation.model_copy(update={"start_at": DEV_END})
    elif change == "holdout":
        values["validation"] = validation.model_copy(
            update={"partition": DatasetPartition.RETROSPECTIVE_HOLDOUT}
        )
    elif change == "late":
        values["planned_at"] = START
    elif change == "alignment":
        values["validation"] = validation.model_copy(
            update={"end_at": VAL_END + timedelta(hours=1)}
        )
    else:
        values["expected_rows"] += 120
    with pytest.raises(ValueError):
        ProspectiveDevvalCampaignPlanV1(**values)


def test_block_resolution_rejects_wrong_pin_and_reordered_inventory(campaign):
    with pytest.raises(ValueError):
        resolve_prospective_devval_campaign_block_v1(
            campaign.payload, expected_campaign_sha256="d" * 64, block_ordinal=0
        )
    with pytest.raises(ValueError):
        resolve(campaign, 168)
    pins = campaign.contract.source_plan_sha256s
    swapped = ProspectiveDevvalCampaignManifestV1.model_validate(
        {
            **campaign.contract.model_dump(mode="python"),
            "source_plan_sha256s": (*pins[:-2], pins[-1], pins[-2]),
        }
    ).canonical_json_bytes()
    with pytest.raises(ValueError, match="source inventory digest differs"):
        verify_prospective_devval_campaign_v1(
            swapped,
            expected_campaign_sha256=sha(swapped),
            expected_source_commit="a" * 40,
            expected_source_tree="b" * 40,
            expected_source_manifest_sha256="c" * 64,
        )
    with pytest.raises(ValueError, match="source plan digest differs"):
        resolve_prospective_devval_campaign_block_v1(
            swapped, expected_campaign_sha256=sha(swapped), block_ordinal=167
        )
    with pytest.raises(ValueError, match="source checkpoint differs"):
        verify_prospective_devval_campaign_v1(
            campaign.payload,
            expected_campaign_sha256=campaign.sha256,
            expected_source_commit="d" * 40,
            expected_source_tree="b" * 40,
            expected_source_manifest_sha256="c" * 64,
        )


@pytest.mark.asyncio
async def test_campaign_slot_reuses_native_one_use_receipt_and_binds_parent(
    campaign, tmp_path, monkeypatch
):
    args, calls, _, _, _, _ = scenario(
        monkeypatch, tmp_path, ordinal=1, campaign=(campaign, 84)
    )
    original, receipt = await slot.capture_prospective_devval_campaign_slot_v6(**args)
    root = tmp_path / "slot-00000001"
    claim = json.loads((root / "claim.json").read_bytes())
    assert original == ORIGINAL and sha((root / "receipt.json").read_bytes()) == receipt
    assert claim["campaign_manifest_sha256"] == campaign.sha256
    assert claim["campaign_block_ordinal"] == 84
    assert claim["campaign_dispatch_receipt_sha256"] == DISPATCH_RECEIPT
    assert "pilot_sha256" not in claim
    assert calls[:3] == ["capture", "consume", "replay"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["wrong_manifest", "wrong_block"])
async def test_campaign_slot_stops_before_claim_or_get_if_plan_does_not_match_owner(
    cause, campaign, tmp_path, monkeypatch
):
    args, calls, _, _, _, _ = scenario(monkeypatch, tmp_path, campaign=(campaign, 84))
    if cause == "wrong_manifest":
        monkeypatch.setattr(slot.dataset, "_source_inventory", lambda: ("e" * 64,))
    else:
        args["block_ordinal"] = 0
    with pytest.raises(slot.ProspectiveDevvalSlotError):
        await slot.capture_prospective_devval_campaign_slot_v6(**args)
    assert calls == [] and list(tmp_path.iterdir()) == []
