"""One-slot dispatcher regressions with synthetic V6 carrier and replay seams."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import ClockStamp, canonical, sha
from app.mie.validation import prospective_devval_slot_v6 as slot
from app.mie.validation.post_publication_availability_v6 import (
    FutureAvailabilityDecisionV6,
)
from app.mie.validation.prospective_development_validation_plan import (
    ProspectiveDevelopmentValidationPlanV1,
)

COLLECTION = "a" * 64
ORIGINAL = "b" * 64
EVIDENCE = "c" * 64
BUNDLE = "d" * 64
CLAIM = "e" * 64
JOURNAL = "f" * 64
SECOND = 1_000_000_000
SLOT0 = "slot-00000000"


def scenario(monkeypatch, root, *, ordinal=0, clock_delay=0, mode=None):
    start = datetime(2030, 1, 1, tzinfo=UTC)
    pilot = ProspectiveDevelopmentValidationPlanV1(
        plan_id="synthetic-slot-v6",
        planned_at=start - timedelta(minutes=1),
        origin="https://openapi.okx.com",
        development_start_at=start,
        development_end_at=start + timedelta(minutes=20),
        validation_start_at=start + timedelta(hours=4),
        validation_end_at=start + timedelta(hours=4, minutes=1),
        expected_rows=42,
    )
    payload = pilot.canonical_json_bytes()
    pin = pilot.canonical_sha256()
    plan = slot.build_prospective_devval_source_plan(payload, expected_plan_sha256=pin)
    capture_plan = plan.coordinates[ordinal].capture_plan
    close = capture_plan.end_ns
    identity = SimpleNamespace(
        plan_sha256=plan.canonical_sha256(), canonical_sha256=lambda: COLLECTION
    )
    native_root = root.with_name(root.name + "-native")
    journal_root = root.with_name(root.name + "-journal")
    native_root.mkdir()
    journal_root.mkdir()
    calls = []

    def native_read(number, *, expected_receipt_sha256):
        assert number == ordinal and expected_receipt_sha256 == ORIGINAL
        calls.append("native_readback")
        return SimpleNamespace(
            status="UNRESOLVED"
            if mode == "native_readback"
            else "NATIVE_DURABLE_AVAILABLE",
            canonical_sha256=lambda: ORIGINAL,
        )

    state = slot.collection._State(
        SimpleNamespace(
            plan=plan, identity=identity, root=native_root, read=native_read
        ),
        None,
        None,
        None,
        slot._context(),
        SimpleNamespace(_root=journal_root),
    )
    owner = slot.collection.DevelopmentValidationAvailabilityOwnerV6(
        slot.collection._ISSUER
    )
    slot.collection._OWNERS[owner] = state
    prior = []
    for index in range(ordinal):
        child = root / f"slot-{index:08d}"
        child.mkdir()
        coordinate = plan.coordinates[index]
        old_claim = slot._claim(
            {
                "collection_identity_sha256": COLLECTION,
                "pilot_sha256": pin,
                "source_plan_sha256": identity.plan_sha256,
                "source_inventory_sha256": sha(canonical(("synthetic",))),
            },
            coordinate,
            prior[-1][1] if prior else None,
        )
        old_close = coordinate.capture_plan.end_ns
        old_cutoff = old_close + 4 * SECOND
        old_decision = FutureAvailabilityDecisionV6(
            evidence_sha256=EVIDENCE,
            bundle_sha256=BUNDLE,
            stage1_forensic_sha256="1" * 64,
            ordinal_claim_sha256=CLAIM,
            cutoff=ClockStamp(utc_ns=old_cutoff, monotonic_ns=74 * SECOND),
            source_rows_sha256="2" * 64,
            row_count=1,
        ).canonical_json_bytes()
        prior_pin = f"{index + 1:064x}"
        old_receipt = canonical(
            {
                "schema": slot._RECEIPT_SCHEMA,
                "ordinal": index,
                "collection_identity_sha256": COLLECTION,
                "native_receipt_sha256": prior_pin,
                "claim_sha256": sha(old_claim),
                "decision_sha256": sha(old_decision),
                "evidence_sha256": EVIDENCE,
                "bundle_sha256": BUNDLE,
                "journal_replay_sha256": JOURNAL,
                "public_readback_microsecond_utc_ns": old_close + SECOND,
                "decision_cutoff_utc_ns": old_cutoff,
                "predictive_oos_eligible": False,
                "execution_authority": False,
            }
        )
        for name, raw in (
            ("claim.json", old_claim),
            ("decision.json", old_decision),
            ("receipt.json", old_receipt),
        ):
            (child / name).write_bytes(raw)
        prior.append((prior_pin, sha(old_receipt)))

    def stamp(offset):
        return ClockStamp(
            utc_ns=close + offset,
            monotonic_ns=70 * SECOND + offset,
        )

    due_stamps = iter((stamp(clock_delay), stamp(clock_delay + 1_000)))
    decision = FutureAvailabilityDecisionV6(
        evidence_sha256=EVIDENCE,
        bundle_sha256=BUNDLE,
        stage1_forensic_sha256="1" * 64,
        ordinal_claim_sha256=CLAIM,
        cutoff=stamp((61 if mode == "late_decision" else 4) * SECOND),
        source_rows_sha256="2" * 64,
        row_count=1,
    )
    payload_time = datetime.fromtimestamp(close // SECOND, tz=UTC) + timedelta(
        seconds=61 if mode == "late_public" else 1
    )
    source = SimpleNamespace(
        plan_sha256=capture_plan.canonical_sha256(), payload_readback_at=payload_time
    )
    evidence = SimpleNamespace(
        source_clock_v4=SimpleNamespace(source_v3=SimpleNamespace(v2_capture=source)),
        ordinal_claim_sha256=CLAIM,
        custody=SimpleNamespace(bundle_sha256=BUNDLE),
        final_ready=stamp(3 * SECOND),
    )
    token, binding = object(), object()

    async def capture(self, *, ordinal, expected_previous_receipt_sha256s):
        calls.append("capture")
        assert self is owner
        assert expected_previous_receipt_sha256s == tuple(a for a, _ in prior)
        assert (root / f"slot-{ordinal:08d}/claim.json").is_file()
        if mode == "cancel":
            raise asyncio.CancelledError
        asyncio.get_running_loop().call_soon(calls.append, "event_loop_yielded")
        return token

    def consume(value, *, expected_source_plan_sha256):
        calls.append("consume")
        assert value is token
        assert expected_source_plan_sha256 == identity.plan_sha256
        assert "event_loop_yielded" not in calls
        return binding, {
            "ordinal": ordinal,
            "capture_plan": capture_plan,
            "decision": decision,
            "decision_sha256": decision.canonical_sha256(),
            "receipt_sha256": ORIGINAL,
            "bundle_sha256": BUNDLE,
            "evidence_sha256": EVIDENCE,
            "evidence_base64": "c3ludGhldGlj",
        }

    async def replay(value):
        calls.append("replay")
        assert value is binding
        await asyncio.sleep(0)
        if mode == "replay":
            raise OSError("synthetic replay failure")
        return JOURNAL

    def finish(values):
        calls.append("finish")
        assert values == (binding,)
        if mode == "finish":
            raise ValueError("synthetic lease expired")

    def evidence_replay(raw, *, expected_sha256):
        assert raw == b"synthetic" and expected_sha256 == EVIDENCE
        return evidence

    monkeypatch.setattr(slot.dataset, "_check_code", lambda: None)
    monkeypatch.setattr(slot.dataset, "_source_inventory", lambda: ("synthetic",))
    monkeypatch.setattr(
        slot.native_clock, "native_stamp", lambda: next(due_stamps).model_dump()
    )
    monkeypatch.setattr(slot, "replay_native_availability_v6", evidence_replay)
    monkeypatch.setattr(
        slot.collection.DevelopmentValidationAvailabilityOwnerV6,
        "capture_once",
        capture,
    )
    monkeypatch.setattr(slot.native, "consume_native_devval_group_v6", consume)
    monkeypatch.setattr(slot.native, "replay_native_devval_group_v6", replay)
    monkeypatch.setattr(slot.native, "finish_native_devval_groups_v6", finish)
    monkeypatch.setattr(
        slot.native, "discard_native_devval_group_v6", lambda value: calls.append(value)
    )
    return (
        {
            "owner": owner,
            "pilot_payload": payload,
            "expected_pilot_sha256": pin,
            "expected_collection_identity_sha256": COLLECTION,
            "result_root": root,
            "ordinal": ordinal,
            "prior_receipts": tuple(prior),
        },
        calls,
        state,
        decision,
        token,
        binding,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("ordinal", [0, 33])
async def test_readback_binds_original_decision_and_finishes_same_task(
    tmp_path, monkeypatch, ordinal
):
    args, calls, state, decision, token, binding = scenario(
        monkeypatch, tmp_path, ordinal=ordinal
    )
    original_pin, slot_pin = await slot.capture_prospective_devval_slot_v6(**args)
    root = tmp_path / f"slot-{ordinal:08d}"
    assert original_pin == ORIGINAL
    assert sha((root / "receipt.json").read_bytes()) == slot_pin
    assert (root / "decision.json").read_bytes() == decision.canonical_json_bytes()
    assert calls == [
        "capture",
        "consume",
        "replay",
        "event_loop_yielded",
        "finish",
        "native_readback",
        token,
        binding,
    ]
    assert not state.stopped
    with pytest.raises(slot.ProspectiveDevvalSlotError, match="prefix_inventory"):
        await slot.capture_prospective_devval_slot_v6(**args)
    assert state.stopped and calls.count("capture") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offset,reason,stopped", [(-1, "not_due", False), (60 * SECOND, "expired", True)]
)
async def test_outside_due_window_never_starts_capture(
    tmp_path, monkeypatch, offset, reason, stopped
):
    args, calls, state, *_ = scenario(monkeypatch, tmp_path, clock_delay=offset)
    with pytest.raises(slot.ProspectiveDevvalSlotError, match=reason):
        await slot.capture_prospective_devval_slot_v6(**args)
    assert state.stopped is stopped
    assert not calls and not tuple(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["late_public", "late_decision", "replay", "finish", "cancel"]
)
async def test_unresolved_attempt_retains_claim_and_stops(tmp_path, monkeypatch, mode):
    args, calls, state, _, token, binding = scenario(monkeypatch, tmp_path, mode=mode)
    error = (
        asyncio.CancelledError if mode == "cancel" else slot.ProspectiveDevvalSlotError
    )
    with pytest.raises(error):
        await slot.capture_prospective_devval_slot_v6(**args)
    assert state.stopped
    assert {path.name for path in (tmp_path / SLOT0).iterdir()} == {"claim.json"}
    with pytest.raises(slot.ProspectiveDevvalSlotError, match="owner_unavailable"):
        await slot.capture_prospective_devval_slot_v6(**args)
    assert calls.count("capture") == 1
    if mode != "cancel":
        assert calls[-2:] == [token, binding]


@pytest.mark.asyncio
async def test_late_filesystem_failure_preserves_original_decision(
    tmp_path, monkeypatch
):
    args, calls, state, decision, *_ = scenario(monkeypatch, tmp_path)
    publish = slot._publish

    def reject_receipt(directory, name, raw):
        if name == "receipt.json":
            raise OSError("synthetic late filesystem refusal")
        publish(directory, name, raw)

    monkeypatch.setattr(slot, "_publish", reject_receipt)
    with pytest.raises(slot.ProspectiveDevvalSlotError, match="unresolved"):
        await slot.capture_prospective_devval_slot_v6(**args)
    root = tmp_path / SLOT0
    assert state.stopped and "finish" in calls
    assert (root / "claim.json").is_file()
    assert (root / "decision.json").read_bytes() == decision.canonical_json_bytes()
    assert not (root / "receipt.json").exists()


@pytest.mark.asyncio
async def test_rehashed_previous_sidecar_cannot_add_authority(tmp_path, monkeypatch):
    args, calls, state, *_ = scenario(monkeypatch, tmp_path, ordinal=1)
    receipt = tmp_path / SLOT0 / "receipt.json"
    changed = json.loads(receipt.read_bytes())
    changed["execution_authority"] = True
    raw = canonical(changed)
    receipt.write_bytes(raw)
    ((prior_native, _),) = args["prior_receipts"]
    args["prior_receipts"] = ((prior_native, sha(raw)),)
    with pytest.raises(slot.ProspectiveDevvalSlotError, match="readback_changed"):
        await slot.capture_prospective_devval_slot_v6(**args)
    assert state.stopped and not calls


@pytest.mark.asyncio
async def test_result_root_cannot_overlap_original_storage(tmp_path, monkeypatch):
    args, calls, state, *_ = scenario(monkeypatch, tmp_path)
    args["result_root"] = state.reader.root
    with pytest.raises(slot.ProspectiveDevvalSlotError, match="root_overlaps"):
        await slot.capture_prospective_devval_slot_v6(**args)
    assert not calls and not tuple(state.reader.root.iterdir())


@pytest.mark.asyncio
async def test_late_native_readback_refusal_preserves_decision_without_receipt(
    tmp_path, monkeypatch
):
    args, calls, state, decision, *_ = scenario(
        monkeypatch, tmp_path, mode="native_readback"
    )
    with pytest.raises(
        slot.ProspectiveDevvalSlotError, match="native_readback_changed"
    ):
        await slot.capture_prospective_devval_slot_v6(**args)
    root = tmp_path / SLOT0
    assert state.stopped and calls.count("capture") == 1
    assert (root / "decision.json").read_bytes() == decision.canonical_json_bytes()
    assert not (root / "receipt.json").exists()
