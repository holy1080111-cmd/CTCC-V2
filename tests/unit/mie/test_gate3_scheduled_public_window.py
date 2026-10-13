"""Offline window coordination with synthetic services and no public request."""

from __future__ import annotations

from datetime import timedelta

import pytest

import app.mie.validation.scheduled_public_minute as minute_owner
from app.mie.validation.prospective_capture_schedule import MINUTE_NS
from app.mie.validation.public_checkpoint_service import (
    OwnedPostReadCapture,
    PublicCheckpointCaptureService,
    WitnessedPublicCapture,
)
from app.mie.validation.scheduled_public_minute_publication import (
    ScheduledMinuteReceiptStore,
)
from app.mie.validation.scheduled_public_window import (
    ScheduledPublicWindowCoordinator,
    ScheduledWindowReadbackV1,
)
from app.public_market_source.public_market_receipts import utc_from_ns

pytestmark = pytest.mark.asyncio
pytest_plugins = ("tests.unit.mie.test_gate3_scheduled_public_minute",)


def _setup(case, tmp_path, monkeypatch):
    kwargs, state, schedule = case
    root = tmp_path / "window"
    root.mkdir()
    pins = {
        "schedule": schedule,
        "expected_coordinate_plan_sha256": kwargs["expected_coordinate_plan_sha256"],
        "expected_seal_sha256": kwargs["expected_seal_sha256"],
        "expected_schedule_sha256": kwargs["expected_schedule_sha256"],
    }
    store = ScheduledMinuteReceiptStore.initialize(root, **pins)
    coordinator = ScheduledPublicWindowCoordinator(store=store, **pins)
    clock = {"utc_ns": schedule.plans[0].end_ns + 1_000_000_000}
    monkeypatch.setattr(minute_owner, "native_stamp", lambda: clock)

    async def capture_one(_self, *, plan, **_kwargs):
        state["captures"] += 1
        index = schedule.plans.index(plan)
        number = index + 1
        return OwnedPostReadCapture(
            witnessed=WitnessedPublicCapture(
                capture_id=f"{number:032x}",
                receipt_sha256=f"{number:064x}",
                witness_revision=number + 2,
                witness_record_sha256=f"{number + 10:064x}",
                checkpoint_sha256=f"{number + 20:064x}",
            ),
            blind_capture_sha256=f"{number + 30:064x}",
            post_read_v3_sha256=f"{number + 40:064x}",
            post_read_observed_at=utc_from_ns(plan.end_ns) + timedelta(seconds=2),
        )

    monkeypatch.setattr(
        PublicCheckpointCaptureService,
        "capture_with_post_read_v3",
        capture_one,
    )
    services = {
        "seal_observer": kwargs["seal_observer"],
        "schedule_repository": kwargs["schedule_repository"],
        "capture_service": kwargs["capture_service"],
        "observation_repository": kwargs["observation_repository"],
    }
    return coordinator, services, schedule, clock, state, root, pins


def _due(clock, schedule, ordinal, offset_ns=1_000_000_000):
    clock["utc_ns"] = schedule.plans[ordinal].end_ns + offset_ns


async def test_window_is_default_off_and_rejects_skipped_first_ordinal(
    case, tmp_path, monkeypatch
):
    coordinator, services, _, _, state, root, _ = _setup(case, tmp_path, monkeypatch)
    disabled = await coordinator.capture_next_once(
        ordinal=0, expected_previous_receipt_sha256s=(), **services
    )
    assert disabled.status == "INCOMPLETE"
    assert disabled.reason_code == "disabled"
    assert state["captures"] == 0
    assert not (root / "minute-00000000").exists()

    skipped = await coordinator.capture_next_once(
        ordinal=1,
        expected_previous_receipt_sha256s=("0" * 64,),
        enabled=True,
        **services,
    )
    assert skipped.reason_code == "missing_ordinal"
    assert skipped.verified_receipts == 0
    assert state["captures"] == 0
    assert not (root / "minute-00000001").exists()


async def test_all_ordinals_replay_but_window_remains_incomplete_after_restart(
    case, tmp_path, monkeypatch
):
    coordinator, services, schedule, clock, state, root, pins = _setup(
        case, tmp_path, monkeypatch
    )
    external_pins: tuple[str, ...] = ()
    for ordinal in range(schedule.coordinate_plan.expected_rows):
        _due(clock, schedule, ordinal)
        result = await coordinator.capture_next_once(
            ordinal=ordinal,
            expected_previous_receipt_sha256s=external_pins,
            enabled=True,
            **services,
        )
        assert result.status == "INCOMPLETE"
        assert result.verified_receipts == ordinal + 1
        assert result.source_replayed_prefix == ordinal + 1
        assert result.last_verified_receipt_sha256 is not None
        external_pins = (*external_pins, result.last_verified_receipt_sha256)
    assert result.reason_code == "source_chain_replayed_custody_unproven"
    assert result.current_claim.value == "computational"
    assert not result.predictive_oos_eligible
    assert not result.promotion_eligible
    assert not result.execution_authority
    assert result.runtime_consumers == 0
    assert state["captures"] == schedule.coordinate_plan.expected_rows

    restarted = ScheduledPublicWindowCoordinator(
        store=ScheduledMinuteReceiptStore.resume(root, **pins), **pins
    )
    readback = restarted.read_window(expected_receipt_sha256s=external_pins)
    assert readback == result
    assert (
        ScheduledWindowReadbackV1.model_validate_json(result.canonical_json_bytes())
        == result
    )
    assert result.canonical_sha256() == readback.canonical_sha256()
    with pytest.raises(ValueError):
        ScheduledWindowReadbackV1.model_validate(
            {**result.model_dump(mode="python"), "predictive_oos_eligible": True}
        )
    with pytest.raises(ValueError):
        ScheduledWindowReadbackV1.model_validate(
            {**result.model_dump(mode="python"), "status": "PASS"}
        )
    repeated = await restarted.capture_next_once(
        ordinal=schedule.coordinate_plan.expected_rows - 1,
        expected_previous_receipt_sha256s=external_pins[:-1],
        enabled=True,
        **services,
    )
    assert repeated.reason_code == "external_pin_missing"
    assert state["captures"] == schedule.coordinate_plan.expected_rows


@pytest.mark.parametrize(
    "offset_ns,underlying_reason",
    [(-1, "not_due"), (MINUTE_NS, "late")],
)
async def test_early_or_late_tombstone_stops_the_whole_window(
    case, tmp_path, monkeypatch, offset_ns, underlying_reason
):
    coordinator, services, schedule, clock, state, root, _ = _setup(
        case, tmp_path, monkeypatch
    )
    _due(clock, schedule, 0, offset_ns)
    first = await coordinator.capture_next_once(
        ordinal=0, expected_previous_receipt_sha256s=(), enabled=True, **services
    )
    assert first.reason_code == "source_incomplete"
    assert first.status == "INCOMPLETE"
    assert first.last_verified_receipt_sha256 is not None
    receipt = coordinator._store.read_ordinal(
        0, expected_receipt_sha256=first.last_verified_receipt_sha256
    )
    assert receipt.contract.reason_code == underlying_reason
    assert state["captures"] == 0
    _due(clock, schedule, 1)
    second = await coordinator.capture_next_once(
        ordinal=1,
        expected_previous_receipt_sha256s=(first.last_verified_receipt_sha256,),
        enabled=True,
        **services,
    )
    assert second.reason_code == "source_incomplete"
    assert state["captures"] == 0
    assert not (root / "minute-00000001").exists()


@pytest.mark.parametrize("failure", (TimeoutError, ValueError))
async def test_unknown_or_missing_public_page_is_durable_and_never_retried(
    case, tmp_path, monkeypatch, failure
):
    coordinator, services, schedule, clock, state, root, _ = _setup(
        case, tmp_path, monkeypatch
    )

    async def uncertain(_self, **_kwargs):
        state["captures"] += 1
        raise failure("synthetic unknown result or missing public page")

    monkeypatch.setattr(
        PublicCheckpointCaptureService,
        "capture_with_post_read_v3",
        uncertain,
    )
    _due(clock, schedule, 0)
    first = await coordinator.capture_next_once(
        ordinal=0, expected_previous_receipt_sha256s=(), enabled=True, **services
    )
    assert first.reason_code == "source_incomplete"
    assert first.last_verified_receipt_sha256 is not None
    repeated = await coordinator.capture_next_once(
        ordinal=0, expected_previous_receipt_sha256s=(), enabled=True, **services
    )
    assert repeated.reason_code == "external_pin_missing"
    assert state["captures"] == 1
    assert (root / "minute-00000000" / "claim.json").is_file()


async def test_crash_after_claim_keeps_unknown_tombstone(case, tmp_path, monkeypatch):
    coordinator, services, schedule, clock, state, root, pins = _setup(
        case, tmp_path, monkeypatch
    )

    async def crashed(**_kwargs):
        state["captures"] += 1
        raise RuntimeError("synthetic crash after claim")

    monkeypatch.setattr(
        "app.mie.validation.scheduled_public_minute_publication.capture_scheduled_public_minute_once",
        crashed,
    )
    _due(clock, schedule, 0)
    first = await coordinator.capture_next_once(
        ordinal=0, expected_previous_receipt_sha256s=(), enabled=True, **services
    )
    assert first.reason_code == "capture_unresolved"
    assert (root / "minute-00000000" / "claim.json").is_file()
    assert not (root / "minute-00000000" / "receipt.json").exists()
    restarted = ScheduledPublicWindowCoordinator(
        store=ScheduledMinuteReceiptStore.resume(root, **pins), **pins
    )
    assert restarted.read_window(expected_receipt_sha256s=()).reason_code == (
        "external_pin_missing"
    )
    repeated = await restarted.capture_next_once(
        ordinal=0, expected_previous_receipt_sha256s=(), enabled=True, **services
    )
    assert repeated.reason_code == "external_pin_missing"
    assert state["captures"] == 1


async def test_duplicate_source_identity_stops_later_ordinals(
    case, tmp_path, monkeypatch
):
    coordinator, services, schedule, clock, state, root, _ = _setup(
        case, tmp_path, monkeypatch
    )

    async def duplicate(_self, *, plan, **_kwargs):
        state["captures"] += 1
        return OwnedPostReadCapture(
            witnessed=WitnessedPublicCapture(
                capture_id="a" * 32,
                receipt_sha256="b" * 64,
                witness_revision=3,
                witness_record_sha256="c" * 64,
                checkpoint_sha256="d" * 64,
            ),
            blind_capture_sha256="e" * 64,
            post_read_v3_sha256="f" * 64,
            post_read_observed_at=utc_from_ns(plan.end_ns) + timedelta(seconds=2),
        )

    monkeypatch.setattr(
        PublicCheckpointCaptureService,
        "capture_with_post_read_v3",
        duplicate,
    )
    _due(clock, schedule, 0)
    first = await coordinator.capture_next_once(
        ordinal=0, expected_previous_receipt_sha256s=(), enabled=True, **services
    )
    _due(clock, schedule, 1)
    second = await coordinator.capture_next_once(
        ordinal=1,
        expected_previous_receipt_sha256s=(first.last_verified_receipt_sha256,),
        enabled=True,
        **services,
    )
    assert second.reason_code == "duplicate_source"
    assert second.status == "INCOMPLETE"
    assert state["captures"] == 2
    assert second.last_verified_receipt_sha256 is not None
    _due(clock, schedule, 2)
    third = await coordinator.capture_next_once(
        ordinal=2,
        expected_previous_receipt_sha256s=(
            first.last_verified_receipt_sha256,
            second.last_verified_receipt_sha256,
        ),
        enabled=True,
        **services,
    )
    assert third.reason_code == "duplicate_source"
    assert state["captures"] == 2
    assert not (root / "minute-00000002").exists()


async def test_wrong_external_receipt_pin_prevents_next_capture(
    case, tmp_path, monkeypatch
):
    coordinator, services, schedule, clock, state, root, _ = _setup(
        case, tmp_path, monkeypatch
    )
    _due(clock, schedule, 0)
    first = await coordinator.capture_next_once(
        ordinal=0, expected_previous_receipt_sha256s=(), enabled=True, **services
    )
    assert first.last_verified_receipt_sha256 is not None
    _due(clock, schedule, 1)
    wrong = await coordinator.capture_next_once(
        ordinal=1,
        expected_previous_receipt_sha256s=("0" * 64,),
        enabled=True,
        **services,
    )
    assert wrong.reason_code == "receipt_unresolved"
    assert state["captures"] == 1
    assert not (root / "minute-00000001").exists()


async def test_store_rechecks_prior_pin_at_claim_after_coordinator_read(
    case, tmp_path, monkeypatch
):
    coordinator, services, schedule, clock, state, root, _ = _setup(
        case, tmp_path, monkeypatch
    )
    _due(clock, schedule, 0)
    first = await coordinator.capture_next_once(
        ordinal=0, expected_previous_receipt_sha256s=(), enabled=True, **services
    )
    assert first.last_verified_receipt_sha256 is not None
    original = ScheduledPublicWindowCoordinator.read_window
    altered = False

    def change_after_read(self, *, expected_receipt_sha256s):
        nonlocal altered
        result = original(self, expected_receipt_sha256s=expected_receipt_sha256s)
        if expected_receipt_sha256s and not altered:
            altered = True
            (root / "minute-00000000" / "receipt.json").write_bytes(b"{}")
        return result

    monkeypatch.setattr(
        ScheduledPublicWindowCoordinator, "read_window", change_after_read
    )
    _due(clock, schedule, 1)
    second = await coordinator.capture_next_once(
        ordinal=1,
        expected_previous_receipt_sha256s=(first.last_verified_receipt_sha256,),
        enabled=True,
        **services,
    )
    assert second.reason_code == "capture_unresolved"
    assert state["captures"] == 1
    assert not (root / "minute-00000001").exists()
