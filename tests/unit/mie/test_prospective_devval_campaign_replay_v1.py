"""Restart replay tests with explicit synthetic data and DB seams."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import canonical, sha
from app.mie.features import FeatureBar
from app.mie.validation import prospective_devval_campaign_replay_v1 as replay
from app.mie.validation import prospective_devval_campaign_run_v1 as run
from app.mie.validation.availability import AvailabilityBasis, AvailabilityProvenance
from app.mie.validation.batch_replay import BoundMinute
from app.mie.validation.development_validation_source_collection_v6 import (
    OrdinalReceiptV6,
)
from app.mie.validation.replay import PointInTimeBar
from tests.unit.exchange.test_live_account_config_custody_v1 import _private_root_path
from tests.unit.mie import test_native_availability_v5 as v5
from tests.unit.mie import test_native_devval_dataset_v6 as original
from tests.unit.mie import test_native_stage1_retention_v6 as v6
from tests.unit.mie import test_prospective_devval_campaign_run_v1 as runner_fixture
from tests.unit.research import test_public_journal_contracts as public_fixture

OPEN = runner_fixture.OPEN
VALIDATION = OPEN + timedelta(hours=4)


@pytest.fixture
def one_minute_flow(monkeypatch, tmp_path):
    prior = public_fixture.SyntheticCapture
    monkeypatch.setattr(
        public_fixture,
        "SyntheticCapture",
        lambda patch, **kwargs: prior(patch, **{**kwargs, "rows": 1}),
    )
    case = v5.raw_case.__wrapped__(monkeypatch)
    yield from v6.flow6.__wrapped__(case, monkeypatch, tmp_path)


@pytest.mark.asyncio
async def test_archive_replays_original_single_minute_and_rejects_changed_journal_pin(
    one_minute_flow,
):
    flow = one_minute_flow
    group = await original.complete_original_v6_group.__wrapped__(flow)
    receipt = OrdinalReceiptV6.model_validate_json(
        base64.b64decode(group.receipt_base64)
    )
    raw = base64.b64decode(group.bundle_base64)
    evidence = base64.b64decode(group.evidence_base64)
    bundle = replay.replay_native_availability_bundle_v6(
        raw, expected_sha256=group.bundle_sha256
    )
    source = bundle.clock_v4.source_v3.v2_capture
    slot = {
        "bundle_sha256": group.bundle_sha256,
        "evidence_sha256": group.evidence_sha256,
        "public_readback_microsecond_utc_ns": replay.utc_nanoseconds(
            source.payload_readback_at
        ),
        "journal_replay_sha256": sha(
            canonical(
                {
                    "checkpoint": bundle.checkpoint.model_dump(mode="json"),
                    "files": [(item.path, item.raw_sha256) for item in bundle.files],
                }
            )
        ),
    }
    coordinate = flow.plan.coordinate(0)
    bound, restored, _ = replay._verify_minute(
        coordinate, receipt, slot, group.decision, raw, evidence
    )
    assert bound.row == group.rows[0] and restored == bundle
    assert bound.availability.basis == AvailabilityBasis.MEASURED_ROW_RECEIPT
    replay._check_journal_prefix(restored, bundle, 0)
    with pytest.raises(
        replay.DevvalCampaignReplayError, match="original_journal_pin_changed"
    ):
        replay._verify_minute(
            coordinate,
            receipt,
            {**slot, "journal_replay_sha256": "9" * 64},
            group.decision,
            raw,
            evidence,
        )


def _minute(opening, offset):
    closed = opening + timedelta(minutes=offset)
    available = closed + timedelta(seconds=1)
    pin = sha(closed.isoformat().encode())
    row = PointInTimeBar(
        source_row_id="synthetic:" + pin,
        source_row_sha256=pin,
        instrument_id="BTC-USDT-SWAP",
        available_at=available,
        bar=FeatureBar(
            closed_at=closed,
            open=Decimal(10),
            high=Decimal(11),
            low=Decimal(9),
            close=Decimal(10),
            volume=Decimal(1),
        ),
    )
    return BoundMinute(
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


def _archives(monkeypatch, root, run_args, completion_pin, *, defect=None):
    calls = []
    publication_repository = run_args["publication_repository"]
    custody = object.__new__(replay.RuntimeBundleCustodyRepository)
    roster_file, dispatch_file = root / "roots.json", root / "dispatch.json"
    publication = json.loads(roster_file.read_bytes())["publication"]
    claim = json.loads(dispatch_file.read_bytes())
    read_at = VALIDATION + timedelta(hours=5)
    date = replay.datetime.fromisoformat
    read = replay.DevvalCampaignPublicationV1(
        publication["campaign_sha256"],
        publication["plan_sha256"],
        publication["campaign_id"],
        publication["database_oid"],
        date(publication["recorded_at"]),
        publication["publisher_role_oid"],
        publication["publication_xid"],
        date(publication["acknowledged_at"]),
        publication["observer_role_oid"],
        publication["acknowledgement_xid"],
        read_at,
        "991",
        runner_fixture._bracket(read_at),
    )
    observed_claim = replay.DevvalCampaignDispatchClaimV1(
        claim["campaign_sha256"],
        claim["root_binding_sha256"],
        claim["collector_role_oid"],
        "990" if defect == "db_claim" else claim["dispatch_xid"],
        date(claim["claimed_at"]),
        read_at,
        "992",
        runner_fixture._bracket(read_at),
    )

    async def observe(payload, **kwargs):
        assert payload == runner_fixture.PAYLOAD
        assert kwargs["root_binding_sha256"] == sha(roster_file.read_bytes())
        calls.append("observe_dispatch")
        if defect == "late_slot" and calls.count("observe_dispatch") == 2:
            await asyncio.sleep(0)
            (root / "block-0000/slots/slot-00000000/receipt.json").write_bytes(
                b"late change"
            )
        return read, observed_claim

    async def verify_custody(self):
        assert self is custody
        calls.append("verify_custody")

    async def block(
        path,
        *,
        pairs,
        plan,
        base,
        roster,
        witness,
        observation,
        custody,
        validation_open,
    ):
        index = int(path.name.removeprefix("block-"))
        calls.append(path.name)
        assert (
            plan.canonical_sha256()
            == roster["source_plan_sha256"]
            == base["source_plan_sha256"]
        )
        assert validation_open == replay._ns(VALIDATION)
        opening, partition = (
            (OPEN, "development") if index == 0 else (VALIDATION, "validation")
        )
        values = []
        for number, (native, slot) in enumerate(pairs, start=1):
            row = _minute(opening, number)
            cutoff = replay.utc_nanoseconds(row.row.available_at)
            values.append((partition, row, cutoff, native, slot))
        return tuple(values), "synthetic checkpoint", ()

    def final_readback(path, *, plan, pairs, base, roster, checkpoint, journal_pins):
        assert checkpoint == "synthetic checkpoint" and not journal_pins
        calls.append("final:" + path.name)
        for ordinal, (_, expected) in enumerate(pairs):
            raw = (path / "slots" / replay._slot(ordinal) / "receipt.json").read_bytes()
            if sha(raw) != expected:
                raise replay.DevvalCampaignReplayError("synthetic_late_slot_changed")

    monkeypatch.setattr(publication_repository, "observe_dispatch", observe)
    monkeypatch.setattr(
        replay.RuntimeBundleCustodyRepository, "verify_deployment", verify_custody
    )
    monkeypatch.setattr(replay, "_replay_block", block)
    monkeypatch.setattr(replay, "_final_block_readback", final_readback)
    monkeypatch.setattr(
        replay,
        "verify_prospective_devval_campaign_v1",
        lambda *args, **kwargs: SimpleNamespace(
            plan=SimpleNamespace(
                expected_rows=3,
                block_count=2,
                development=SimpleNamespace(start_at=OPEN),
                validation=SimpleNamespace(start_at=VALIDATION),
            ),
            plan_sha256=runner_fixture.PLAN,
            source_plan_sha256s=runner_fixture.BLOCKS,
        ),
    )
    monkeypatch.setattr(
        replay,
        "_source_plan",
        lambda *args: SimpleNamespace(
            canonical_sha256=lambda: runner_fixture.BLOCKS[args[-1]]
        ),
    )
    keys = (
        "root",
        "expected_campaign_sha256",
        "expected_source_commit",
        "expected_source_tree",
        "expected_source_manifest_sha256",
        "publication_repository",
        "witness_repository",
        "observation_repository",
    )
    return (
        {
            **{key: run_args[key] for key in keys},
            "custody_repository": custody,
            "expected_completion_sha256": completion_pin,
        },
        calls,
        read,
        observed_claim,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", [None, "db_claim", "block_seal", "late_slot"])
async def test_restart_replays_committed_roster_and_final_chain(
    monkeypatch, tmp_path, defect
):
    root = _private_root_path(tmp_path)
    run_args, events = runner_fixture._setup(monkeypatch, root)
    capture = await run.run_prospective_devval_campaign_v1(**run_args)
    args, calls, read, claim = _archives(
        monkeypatch, root, run_args, capture.receipt_sha256, defect=defect
    )
    batch = object()

    def build_batch(campaign, rows, **pins):
        assert calls[-2:] == ["final:block-0000", "final:block-0001"]
        assert len(rows) == campaign.plan.expected_rows == 3
        assert pins["completion_sha256"] == capture.receipt_sha256
        assert pins["development_cutoff_utc_ns"] < replay._ns(VALIDATION)
        calls.append("batch")
        return batch

    monkeypatch.setattr(replay, "_build_batch", build_batch)
    args["include_batch"] = True
    if os.name == "nt":
        open_directory = replay._WindowsAPI.open_directory

        def readonly(api, path, *, publisher=False):
            assert not publisher
            return open_directory(api, path)

        monkeypatch.setattr(replay._WindowsAPI, "open_directory", readonly)
    if defect == "block_seal":
        (root / "block-0001/complete.json").write_bytes(b"changed")
    if defect is not None:
        with pytest.raises(replay.DevvalCampaignReplayError):
            await replay.replay_prospective_devval_campaign_v1(**args)
        if defect == "late_slot":
            assert calls == [
                "observe_dispatch",
                "verify_custody",
                "block-0000",
                "block-0001",
                "observe_dispatch",
                "final:block-0000",
            ]
        else:
            assert calls == (
                ["observe_dispatch"]
                if defect == "db_claim"
                else ["observe_dispatch", "verify_custody", "block-0000"]
            )
        return
    before = {p.relative_to(root): sha(p.read_bytes()) for p in root.rglob("*.json")}
    result = await replay.replay_prospective_devval_campaign_v1(**args)
    assert calls == [
        "observe_dispatch",
        "verify_custody",
        "block-0000",
        "block-0001",
        "observe_dispatch",
        "final:block-0000",
        "final:block-0001",
        "batch",
    ]
    assert result.batch is batch
    assert (result.development_rows, result.validation_rows) == (2, 1)
    assert (
        result.development_cutoff_utc_ns
        < replay._ns(VALIDATION)
        < result.validation_cutoff_utc_ns
    )
    assert not result.execution_authority and not result.predictive_oos_eligible
    assert before == {
        p.relative_to(root): sha(p.read_bytes()) for p in root.rglob("*.json")
    }
    assert events[-1] == (1, 0)

    roster = json.loads((root / "roots.json").read_bytes())
    dispatch = json.loads((root / "dispatch.json").read_bytes())
    roster["publication"]["native_readback"] = [
        stamp.model_dump() for stamp in runner_fixture._bracket(read.database_read_at)
    ]
    with pytest.raises(
        replay.DevvalCampaignReplayError, match="original_pre_window_clock_unproven"
    ):
        replay._check_dispatch(
            read, claim, roster, dispatch, claim.root_binding_sha256, replay._ns(OPEN)
        )


@pytest.mark.asyncio
async def test_partial_campaign_refuses_before_any_database_read(monkeypatch, tmp_path):
    root = _private_root_path(tmp_path)
    run_args, _ = runner_fixture._setup(monkeypatch, root, failure="slot")
    with pytest.raises(run.DevvalCampaignRunError):
        await run.run_prospective_devval_campaign_v1(**run_args)
    args, calls, _, _ = _archives(monkeypatch, root, run_args, "9" * 64)
    with pytest.raises(
        replay.DevvalCampaignReplayError, match="devval_campaign_incomplete"
    ):
        await replay.replay_prospective_devval_campaign_v1(**args)
    assert calls == []
