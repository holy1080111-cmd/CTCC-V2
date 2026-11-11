"""Campaign start, immutable dispatch, ordered slots and fail-closed restart."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import ClockStamp, canonical, sha
from app.mie.validation import prospective_devval_campaign_custody_v1 as custody
from app.mie.validation import prospective_devval_campaign_run_v1 as run
from app.mie.validation import prospective_devval_slot_v6 as slot
from app.mie.validation.contracts import DatasetPartition, PartitionWindow
from app.mie.validation.prospective_devval_campaign_custody_v1 import (
    DevvalCampaignCustodyRepositoryV1,
    DevvalCampaignDispatchClaimV1,
    DevvalCampaignPublicationV1,
)
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    ProspectiveDevvalCampaignPlanV1,
)
from tests.unit.exchange.test_live_account_config_custody_v1 import _private_root_path

OPEN = datetime(2030, 1, 1, tzinfo=UTC)
PIN = "a" * 64
PLAN = "b" * 64
SOURCE = "c" * 64
BLOCKS = ("d" * 64, "e" * 64)
PAYLOAD = b"synthetic campaign bytes"


def _bracket(at):
    return (
        ClockStamp(utc_ns=run._ns(at - timedelta(milliseconds=1)), monotonic_ns=1),
        ClockStamp(
            utc_ns=run._ns(at + timedelta(milliseconds=1)), monotonic_ns=2_000_001
        ),
    )


def _actor_plan(validation_open, *, campaign_id="fixture-devval-campaign"):
    # These actor tests substitute a two-block verifier output. Plan-grid
    # admission is exercised separately with complete windows.
    return ProspectiveDevvalCampaignPlanV1.model_construct(
        campaign_id=campaign_id,
        development=PartitionWindow(
            partition=DatasetPartition.DEVELOPMENT,
            start_at=OPEN,
            end_at=OPEN + timedelta(hours=1),
        ),
        validation=PartitionWindow(
            partition=DatasetPartition.VALIDATION,
            start_at=validation_open,
            end_at=validation_open + timedelta(hours=1),
        ),
        expected_rows=3,
    )


def _setup(monkeypatch, root, *, failure=None):
    events = []
    repository = object.__new__(DevvalCampaignCustodyRepositoryV1)
    repository._role = "collector"
    witness = object.__new__(run.PublicReceiptWitnessRepository)
    observer = run.PublicReceiptPostReadRepository(None, witness)
    campaign = SimpleNamespace(
        plan=_actor_plan(OPEN + timedelta(hours=4)),
        plan_sha256=PLAN,
        source_plan_sha256s=BLOCKS,
    )
    plans = tuple(
        SimpleNamespace(
            canonical_sha256=lambda digest=pin: digest,
            coordinates=tuple(
                SimpleNamespace(
                    ordinal=i,
                    capture_plan=SimpleNamespace(end_ns=run._ns(OPEN) + 60_000_000_000),
                )
                for i in range(2 if ordinal == 0 else 1)
            ),
        )
        for ordinal, pin in enumerate(BLOCKS)
    )
    observed = DevvalCampaignPublicationV1(
        PIN,
        PLAN,
        "fixture-devval-campaign",
        101,
        OPEN - timedelta(minutes=4),
        102,
        "107",
        None if failure == "unacknowledged" else OPEN - timedelta(minutes=3),
        103,
        "108",
        OPEN if failure == "late" else OPEN - timedelta(minutes=2),
        "109",
        _bracket(OPEN if failure == "late" else OPEN - timedelta(minutes=2)),
    )

    async def read(payload, **pins):
        assert payload == PAYLOAD and pins["expected_campaign_sha256"] == PIN
        assert (root / "campaign.json").read_bytes() == PAYLOAD
        events.append("publication")
        return observed

    async def prepare(
        base, plan, ordinal, original_witness, original_observer, factory
    ):
        assert (
            base == root
            and witness is original_witness
            and observer is original_observer
        )
        assert plans[ordinal] is plan
        events.append(f"root:{ordinal}")
        name = f"block-{ordinal:04d}"
        with run._root_context(base) as directory:
            run.mkdir_private_evidence_child_v2(directory, name)
            with directory.child(name) as block_dir:
                for child in run._ROOTS:
                    run.mkdir_private_evidence_child_v2(block_dir, child)
        identities = tuple(run._private(base / name / child) for child in run._ROOTS)
        if failure == "root_conflict":
            identities = (identities[0], identities[0], identities[2])
        return run._Block(
            name,
            plan,
            object(),
            identities,
            f"{ordinal + 4:064x}",
            f"{ordinal + 6:032x}",
            f"{ordinal + 8:064x}",
        )

    async def claim(payload, **pins):
        assert payload == PAYLOAD and pins["expected_publication"] is observed
        binding = (root / "roots.json").read_bytes()
        roster = json.loads(binding)
        assert [item["source_plan_sha256"] for item in roster["blocks"]] == list(BLOCKS)
        assert pins["root_binding_sha256"] == sha(binding)
        events.append("dispatch")
        if failure == "uncertain":
            raise OSError("private fixture error")
        result = DevvalCampaignDispatchClaimV1(
            PIN,
            sha(binding),
            104,
            "111",
            OPEN - timedelta(seconds=31),
            OPEN - timedelta(seconds=30),
            "112",
            _bracket(OPEN - timedelta(seconds=30)),
        )
        custody._OWNED_DISPATCHES[result] = custody._context()
        return result

    async def capture(**params):
        block = params["block_ordinal"]
        ordinal = params["ordinal"]
        events.append((block, ordinal))
        assert (
            custody._dispatch_document(params["dispatch_claim"])
            == (root / "dispatch.json").read_bytes()
        )
        assert len(params["prior_receipts"]) == ordinal
        if failure == "slot" and block == 0 and ordinal == 1:
            raise OSError("private fixture error")
        with run._root_context(params["result_root"]) as directory:
            name = run._slot(ordinal)
            run.mkdir_private_evidence_child_v2(directory, name)
            with directory.child(name) as child:
                digest = run._retain(
                    child,
                    "receipt.json",
                    canonical([block, ordinal]),
                    run._MAX_SLOT_FILE,
                )
        return f"{block * 4 + ordinal + 2:064x}", digest

    async def wait_due(close, previous):
        assert close == run._ns(OPEN) + 60_000_000_000
        return previous

    monkeypatch.setattr(
        run, "verify_prospective_devval_campaign_v1", lambda *a, **k: campaign
    )
    monkeypatch.setattr(run, "_source_plan", lambda plan, pin, ordinal: plans[ordinal])
    monkeypatch.setattr(run, "_prepare_block", prepare)
    monkeypatch.setattr(run, "_block_pins", lambda root, block: None)
    monkeypatch.setattr(run, "_wait_due", wait_due)
    monkeypatch.setattr(run, "capture_prospective_devval_campaign_slot_v6", capture)
    monkeypatch.setattr(
        run,
        "native_stamp",
        lambda: ClockStamp(
            utc_ns=run._ns(OPEN - timedelta(seconds=20)),
            monotonic_ns=1,
        ).model_dump(),
    )
    monkeypatch.setattr(run.dataset, "_check_code", lambda: None)
    monkeypatch.setattr(run.dataset, "_source_inventory", lambda: (SOURCE, "synthetic"))
    monkeypatch.setattr(repository, "read", read)
    monkeypatch.setattr(repository, "claim_dispatch", claim)
    args = {
        "root": root,
        "campaign_payload": PAYLOAD,
        "expected_campaign_sha256": PIN,
        "expected_source_commit": "1" * 40,
        "expected_source_tree": "2" * 40,
        "expected_source_manifest_sha256": SOURCE,
        "publication_repository": repository,
        "witness_repository": witness,
        "observation_repository": observer,
        "custody_session_factory": None,
    }
    return args, events


@pytest.mark.asyncio
async def test_research_campaign_requires_blueprint_before_claim_or_capture(
    monkeypatch, tmp_path
):
    root = _private_root_path(tmp_path)
    args, events = _setup(monkeypatch, root)
    monkeypatch.setattr(
        run,
        "verify_prospective_devval_campaign_v1",
        lambda *a, **k: SimpleNamespace(
            plan=_actor_plan(
                OPEN + timedelta(hours=4), campaign_id="devval:research:" + PIN
            )
        ),
    )
    with pytest.raises(run.DevvalCampaignRunError, match="devval_campaign_unresolved"):
        await run.run_prospective_devval_campaign_v1(**args)
    assert not events
    assert not tuple(root.iterdir())


@pytest.mark.asyncio
async def test_campaign_binds_every_root_before_first_slot_and_never_resumes(
    monkeypatch, tmp_path
):
    root = _private_root_path(tmp_path)
    args, events = _setup(monkeypatch, root)
    result = await run.run_prospective_devval_campaign_v1(**args)
    assert events == [
        "publication",
        "root:0",
        "root:1",
        "dispatch",
        (0, 0),
        (0, 1),
        (1, 0),
    ]
    assert result.row_count == 3 and not result.predictive_oos_eligible
    assert result.receipt_sha256 == sha((root / "complete.json").read_bytes())
    end = json.loads((root / "block-0001/complete.json").read_bytes())
    assert end["previous_block_sha256"] == result.block_receipt_sha256s[0]
    with pytest.raises(run.DevvalCampaignRunError, match="already_claimed"):
        await run.run_prospective_devval_campaign_v1(**args)
    assert events[-1] == (1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["unacknowledged", "late", "root_conflict", "uncertain", "slot"]
)
async def test_campaign_failure_preserves_claim_and_does_not_advance(
    failure, monkeypatch, tmp_path
):
    root = _private_root_path(tmp_path)
    args, events = _setup(monkeypatch, root, failure=failure)
    with pytest.raises(run.DevvalCampaignRunError):
        await run.run_prospective_devval_campaign_v1(**args)
    assert (root / "campaign.json").read_bytes() == PAYLOAD
    assert not (root / "complete.json").exists()
    if failure in ("unacknowledged", "late"):
        assert events == ["publication"]
    elif failure == "root_conflict":
        assert events == ["publication", "root:0", "root:1"]
    elif failure == "uncertain":
        assert events == ["publication", "root:0", "root:1", "dispatch"]
        assert not (root / "dispatch.json").exists()
    else:
        assert events[-2:] == [(0, 0), (0, 1)]
        assert not (root / "block-0000/complete.json").exists()
    with pytest.raises(run.DevvalCampaignRunError, match="already_claimed"):
        await run.run_prospective_devval_campaign_v1(**args)


@pytest.mark.asyncio
async def test_wait_due_rejects_missed_original_deadline(monkeypatch):
    close = run._ns(OPEN)
    previous = ClockStamp(utc_ns=close + 59_999_000_000, monotonic_ns=1)
    now = ClockStamp(utc_ns=close + 60_000_000_000, monotonic_ns=1_000_001)
    monkeypatch.setattr(run, "native_stamp", now.model_dump)
    with pytest.raises(run.DevvalCampaignRunError, match="slot_expired"):
        await run._wait_due(close, previous)


@pytest.mark.asyncio
async def test_slot_requires_owned_dispatch_and_exact_retained_root(tmp_path):
    root = _private_root_path(tmp_path)
    name = "block-0000"
    result = root / name / "slots"
    with run._root_context(root) as directory:
        run.mkdir_private_evidence_child_v2(directory, name)
        with directory.child(name) as child:
            run.mkdir_private_evidence_child_v2(child, "slots")
    raw = canonical(
        {
            "root_identity": run._private(root),
            "blocks": [
                {
                    "block": name,
                    "source_plan_sha256": PLAN,
                    "collection_identity_sha256": SOURCE,
                    "directory_identities": {"slots": run._private(result)},
                }
            ],
        }
    )
    claim = DevvalCampaignDispatchClaimV1(
        PIN,
        sha(raw),
        104,
        "111",
        OPEN - timedelta(seconds=31),
        OPEN - timedelta(seconds=30),
        "112",
        _bracket(OPEN - timedelta(seconds=30)),
    )
    args = {
        "campaign_sha256": PIN,
        "block_ordinal": 0,
        "plan_sha256": PLAN,
        "collection_sha256": SOURCE,
        "result_root": result,
    }
    with pytest.raises(
        custody.DevvalCampaignCustodyError, match="dispatch_owner_required"
    ):
        slot._dispatch_slot_pin(claim, **args)
    custody._OWNED_DISPATCHES[claim] = custody._context()
    document = custody._dispatch_document(claim)
    with run._root_context(root) as directory:
        run._retain(directory, "roots.json", raw, run._ROSTER_MAX)
        run._retain(directory, "dispatch.json", document, run._RECEIPT_MAX)
    assert slot._dispatch_slot_pin(claim, **args) == sha(document)

    async def other_task():
        with pytest.raises(
            custody.DevvalCampaignCustodyError, match="dispatch_owner_required"
        ):
            slot._dispatch_slot_pin(claim, **args)

    await asyncio.create_task(other_task())
    with pytest.raises(slot.ProspectiveDevvalSlotError, match="dispatch_root_changed"):
        slot._dispatch_slot_pin(claim, **{**args, "collection_sha256": "9" * 64})
    result.rename(result.with_name("slots-old"))
    with run._root_context(root) as directory, directory.child(name) as child:
        run.mkdir_private_evidence_child_v2(child, "slots")
    with pytest.raises(slot.ProspectiveDevvalSlotError, match="dispatch_root_changed"):
        slot._dispatch_slot_pin(claim, **args)
    (root / "dispatch.json").write_bytes(b"changed")
    with pytest.raises(
        slot.ProspectiveDevvalSlotError, match="dispatch_binding_changed"
    ):
        slot._dispatch_slot_pin(claim, **args)
