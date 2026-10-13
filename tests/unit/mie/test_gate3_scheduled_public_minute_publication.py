"""Native local publication tests with synthetic sealed services only."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

import app.mie.validation.scheduled_public_minute as capture_owner
import app.mie.validation.scheduled_public_minute_publication as owner
from app.mie.validation.artifact import _freeze
from app.mie.validation.prospective_capture_schedule import MINUTE_NS
from app.public_market_source.public_market_receipts import utc_from_ns

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
        "expected_previous_receipt_sha256s": (),
    }
    return root, pins, call, state, schedule


def _with_due_time(frozen, due_at):
    contract = capture_owner.ScheduledPublicMinuteReceiptV1.model_validate(
        {
            **frozen.contract.model_dump(mode="python"),
            "due_observed_at": due_at,
            "post_read_observed_at": due_at + timedelta(seconds=1),
        }
    )
    return _freeze(contract, contract_type=type(contract))


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


async def test_store_requires_exact_previous_pins_before_any_ordinal_claim(
    case, tmp_path
):
    root, pins, call, state, _ = _inputs(case, tmp_path)
    store = owner.ScheduledMinuteReceiptStore.initialize(root, **pins)

    without_pins = {
        key: value
        for key, value in call.items()
        if key != "expected_previous_receipt_sha256s"
    }
    with pytest.raises(TypeError, match="expected_previous_receipt_sha256s"):
        await store.capture_once(**without_pins)
    with pytest.raises(
        owner.ScheduledMinutePublicationError, match="previous receipt pins"
    ):
        await store.capture_once(**{**call, "expected_previous_receipt_sha256s": None})
    with pytest.raises(owner.ScheduledMinuteUnresolved, match="sequence is unresolved"):
        await store.capture_once(
            **{
                **call,
                "ordinal": 1,
                "expected_previous_receipt_sha256s": ("0" * 64,),
            }
        )
    assert state["captures"] == 0
    assert not (root / "minute-00000000").exists()
    assert not (root / "minute-00000001").exists()


async def test_store_rejects_changed_previous_pin_without_consuming_next_ordinal(
    case, tmp_path
):
    root, pins, call, state, _ = _inputs(case, tmp_path)
    store = owner.ScheduledMinuteReceiptStore.initialize(root, **pins)
    first = await store.capture_once(**call)
    restarted = owner.ScheduledMinuteReceiptStore.resume(root, **pins)
    with pytest.raises(
        owner.ScheduledMinuteUnresolved, match="prior source is unresolved"
    ):
        await restarted.capture_once(
            **{
                **call,
                "ordinal": 1,
                "expected_previous_receipt_sha256s": ("0" * 64,),
            }
        )
    assert restarted.read_ordinal(0, expected_receipt_sha256=first.sha256) == first
    assert not (root / "minute-00000001").exists()
    assert state["captures"] == 1


async def test_store_rejects_incomplete_previous_source_without_new_claim(
    case, tmp_path, monkeypatch
):
    root, pins, call, state, schedule = _inputs(case, tmp_path)
    monkeypatch.setattr(
        capture_owner,
        "native_stamp",
        lambda: {"utc_ns": schedule.plans[0].end_ns - 1},
    )
    store = owner.ScheduledMinuteReceiptStore.initialize(root, **pins)
    first = await store.capture_once(**call)
    assert first.contract.reason_code == "not_due"
    restarted = owner.ScheduledMinuteReceiptStore.resume(root, **pins)
    with pytest.raises(
        owner.ScheduledMinuteUnresolved, match="prior source is unresolved"
    ):
        await restarted.capture_once(
            **{
                **call,
                "ordinal": 1,
                "expected_previous_receipt_sha256s": (first.sha256,),
            }
        )
    assert restarted.read_ordinal(0, expected_receipt_sha256=first.sha256) == first
    assert not (root / "minute-00000001").exists()
    assert state["captures"] == 0


@pytest.mark.parametrize(
    "offset_ns,accepted",
    [
        (-1_000, False),
        (0, True),
        (MINUTE_NS, True),
        (MINUTE_NS + 1_000, False),
    ],
)
async def test_source_replay_due_time_must_match_sealed_minute_before_publish(
    case, tmp_path, monkeypatch, offset_ns, accepted
):
    root, pins, call, _, schedule = _inputs(case, tmp_path)
    original = owner.capture_scheduled_public_minute_once

    async def changed_due(**kwargs):
        frozen = await original(**kwargs)
        due = utc_from_ns(schedule.plans[0].end_ns + offset_ns)
        return _with_due_time(frozen, due)

    monkeypatch.setattr(owner, "capture_scheduled_public_minute_once", changed_due)
    store = owner.ScheduledMinuteReceiptStore.initialize(root, **pins)
    if accepted:
        frozen = await store.capture_once(**call)
        assert store.read_ordinal(0, expected_receipt_sha256=frozen.sha256) == frozen
    else:
        with pytest.raises(owner.ScheduledMinuteUnresolved, match="replay time"):
            await store.capture_once(**call)
        assert (root / "minute-00000000" / "claim.json").is_file()
        assert not (root / "minute-00000000" / "receipt.json").exists()


async def test_replay_readback_rejects_impossible_due_with_matching_local_digest(
    case, tmp_path
):
    root, pins, call, _, schedule = _inputs(case, tmp_path)
    store = owner.ScheduledMinuteReceiptStore.initialize(root, **pins)
    frozen = await store.capture_once(**call)
    impossible = _with_due_time(
        frozen, utc_from_ns(schedule.plans[0].end_ns + MINUTE_NS + 1_000)
    )
    slot = root / "minute-00000000"
    (slot / "receipt.json").write_bytes(impossible.payload)
    (slot / "receipt.sha256").write_bytes(impossible.sha256.encode("ascii") + b"\n")
    restarted = owner.ScheduledMinuteReceiptStore.resume(root, **pins)
    with pytest.raises(owner.ScheduledMinuteUnresolved, match="replay time"):
        restarted.read_ordinal(0, expected_receipt_sha256=impossible.sha256)


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
        await restarted.capture_once(
            **{
                **call,
                "ordinal": 1,
                "expected_previous_receipt_sha256s": ("0" * 64,),
            }
        )
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
