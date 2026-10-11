"""Native local publication tests with synthetic sealed services only."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import app.mie.validation.scheduled_public_minute as capture_owner
import app.mie.validation.scheduled_public_minute_publication as owner

pytestmark = pytest.mark.asyncio
pytest_plugins = ("tests.unit.mie.test_gate3_scheduled_public_minute",)


def _inputs(case, tmp_path: Path):
    kwargs, state, schedule = case
    root = tmp_path / "scheduled-minute"
    root.mkdir()
    pins = {
        "schedule": schedule,
        "expected_coordinate_plan_sha256": kwargs["expected_coordinate_plan_sha256"],
        "expected_seal_sha256": kwargs["expected_seal_sha256"],
        "expected_schedule_sha256": kwargs["expected_schedule_sha256"],
    }
    call = {
        "seal_observer": kwargs["seal_observer"],
        "schedule_repository": kwargs["schedule_repository"],
        "capture_service": kwargs["capture_service"],
        "observation_repository": kwargs["observation_repository"],
        "ordinal": 0,
    }
    return root, pins, call, state, schedule


async def test_due_receipt_is_no_clobber_read_back_and_restart_refuses_duplicate(
    case, tmp_path
):
    root, pins, call, state, _ = _inputs(case, tmp_path)
    store = owner.ScheduledMinuteReceiptStore.initialize(root, **pins)
    frozen = await store.capture_once(**call)
    assert frozen.contract.status == "incomplete"
    assert frozen.contract.reason_code == "source_chain_replayed_custody_unproven"
    assert frozen.contract.current_claim.value == "computational"
    assert not frozen.contract.predictive_oos_eligible
    assert not frozen.contract.execution_authority
    assert store.read_ordinal(0, expected_receipt_sha256=frozen.sha256) == frozen

    restarted = owner.ScheduledMinuteReceiptStore.resume(root, **pins)
    assert restarted.read_ordinal(0, expected_receipt_sha256=frozen.sha256) == frozen
    with pytest.raises(owner.ScheduledMinutePublicationError, match="already claimed"):
        await restarted.capture_once(**call)
    with pytest.raises(owner.ScheduledMinuteUnresolved, match="external receipt pin"):
        restarted.read_ordinal(0, expected_receipt_sha256="0" * 64)
    assert state["captures"] == 1
    assert set((root / "minute-00000000").iterdir()) == {
        root / "minute-00000000" / "claim.json",
        root / "minute-00000000" / "receipt.json",
        root / "minute-00000000" / "receipt.sha256",
    }


@pytest.mark.parametrize("offset,reason", [(-1, "not_due"), (60_000_000_000, "late")])
async def test_early_or_missing_window_is_durable_no_capture_tombstone(
    case, tmp_path, monkeypatch, offset, reason
):
    root, pins, call, state, schedule = _inputs(case, tmp_path)
    monkeypatch.setattr(
        capture_owner,
        "native_stamp",
        lambda: {"utc_ns": schedule.plans[0].end_ns + offset},
    )
    store = owner.ScheduledMinuteReceiptStore.initialize(root, **pins)
    frozen = await store.capture_once(**call)
    assert frozen.contract.reason_code == reason
    assert frozen.contract.status == "incomplete"
    assert state["captures"] == 0
    restarted = owner.ScheduledMinuteReceiptStore.resume(root, **pins)
    assert restarted.read_ordinal(0, expected_receipt_sha256=frozen.sha256) == frozen
    with pytest.raises(owner.ScheduledMinutePublicationError, match="already claimed"):
        await restarted.capture_once(**call)


async def test_unknown_capture_result_is_published_without_retry(
    case, tmp_path, monkeypatch
):
    root, pins, call, state, _ = _inputs(case, tmp_path)

    async def uncertain(_self, **_kwargs):
        state["captures"] += 1
        raise TimeoutError("synthetic database outcome uncertain")

    monkeypatch.setattr(
        type(call["capture_service"]), "capture_with_post_read_v3", uncertain
    )
    store = owner.ScheduledMinuteReceiptStore.initialize(root, **pins)
    frozen = await store.capture_once(**call)
    assert frozen.contract.reason_code == "capture_or_post_read_unresolved"
    assert frozen.contract.post_read_v3_sha256 is None
    restarted = owner.ScheduledMinuteReceiptStore.resume(root, **pins)
    assert restarted.read_ordinal(0, expected_receipt_sha256=frozen.sha256) == frozen
    with pytest.raises(owner.ScheduledMinutePublicationError, match="already claimed"):
        await restarted.capture_once(**call)
    assert state["captures"] == 1


async def test_crash_after_claim_keeps_unresolved_ordinal_and_blocks_restart(
    case, tmp_path, monkeypatch
):
    root, pins, call, state, _ = _inputs(case, tmp_path)

    async def crashed(**_kwargs):
        state["captures"] += 1
        raise RuntimeError("synthetic process stopped after unknown work")

    monkeypatch.setattr(owner, "capture_scheduled_public_minute_once", crashed)
    store = owner.ScheduledMinuteReceiptStore.initialize(root, **pins)
    with pytest.raises(RuntimeError, match="synthetic process stopped"):
        await store.capture_once(**call)
    assert (root / "minute-00000000" / "claim.json").is_file()
    assert not (root / "minute-00000000" / "receipt.json").exists()

    restarted = owner.ScheduledMinuteReceiptStore.resume(root, **pins)
    with pytest.raises(owner.ScheduledMinuteUnresolved, match="receipt is unresolved"):
        restarted.read_ordinal(0, expected_receipt_sha256="0" * 64)
    with pytest.raises(owner.ScheduledMinutePublicationError, match="already claimed"):
        await restarted.capture_once(**call)
    with pytest.raises(owner.ScheduledMinuteUnresolved, match="receipt is unresolved"):
        await restarted.capture_once(**{**call, "ordinal": 1})
    assert state["captures"] == 1


async def test_interrupted_result_file_is_never_replaced_or_recaptured(
    case, tmp_path, monkeypatch
):
    root, pins, call, state, _ = _inputs(case, tmp_path)

    async def crashed(**_kwargs):
        state["captures"] += 1
        raise RuntimeError("synthetic crash")

    monkeypatch.setattr(owner, "capture_scheduled_public_minute_once", crashed)
    store = owner.ScheduledMinuteReceiptStore.initialize(root, **pins)
    with pytest.raises(RuntimeError):
        await store.capture_once(**call)
    partial = root / "minute-00000000" / "receipt.json"
    partial.write_bytes(b'{"partial":true')

    restarted = owner.ScheduledMinuteReceiptStore.resume(root, **pins)
    with pytest.raises(owner.ScheduledMinuteUnresolved, match="receipt is unresolved"):
        restarted.read_ordinal(0, expected_receipt_sha256="0" * 64)
    with pytest.raises(owner.ScheduledMinutePublicationError, match="already claimed"):
        await restarted.capture_once(**call)
    assert partial.read_bytes() == b'{"partial":true'
    assert state["captures"] == 1


async def test_changed_published_bytes_and_foreign_store_pin_fail_closed(
    case, tmp_path
):
    root, pins, call, state, _ = _inputs(case, tmp_path)
    store = owner.ScheduledMinuteReceiptStore.initialize(root, **pins)
    frozen = await store.capture_once(**call)
    with pytest.raises(owner.ScheduledMinutePublicationError, match="not empty"):
        owner.ScheduledMinuteReceiptStore.initialize(root, **pins)
    with pytest.raises(owner.ScheduledMinutePublicationError, match="pin differs"):
        owner.ScheduledMinuteReceiptStore.resume(
            root, **{**pins, "expected_schedule_sha256": "0" * 64}
        )
    receipt_path = root / "minute-00000000" / "receipt.json"
    receipt_path.write_bytes(b"{}")
    restarted = owner.ScheduledMinuteReceiptStore.resume(root, **pins)
    with pytest.raises(owner.ScheduledMinuteUnresolved, match="digest is unresolved"):
        restarted.read_ordinal(0, expected_receipt_sha256=frozen.sha256)
    with pytest.raises(owner.ScheduledMinutePublicationError, match="already claimed"):
        await restarted.capture_once(**call)
    assert receipt_path.read_bytes() == b"{}"
    assert state["captures"] == 1


async def test_second_process_cannot_capture_claimed_ordinal_during_first_await(
    case, tmp_path, monkeypatch
):
    root, pins, call, state, _ = _inputs(case, tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = owner.capture_scheduled_public_minute_once

    async def waiting(**kwargs):
        entered.set()
        await release.wait()
        return await original(**kwargs)

    monkeypatch.setattr(owner, "capture_scheduled_public_minute_once", waiting)
    first_store = owner.ScheduledMinuteReceiptStore.initialize(root, **pins)
    first = asyncio.create_task(first_store.capture_once(**call))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        second_store = owner.ScheduledMinuteReceiptStore.resume(root, **pins)
        with pytest.raises(
            owner.ScheduledMinutePublicationError, match="already claimed"
        ):
            await second_store.capture_once(**call)
    finally:
        release.set()
    frozen = await asyncio.wait_for(first, timeout=5)
    assert frozen.contract.status == "incomplete"
    assert state["captures"] == 1
