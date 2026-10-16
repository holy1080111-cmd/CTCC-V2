"""One-process Gate 3 entry point; every service and market result is synthetic."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

import app.mie.validation.scheduled_public_minute_publication as publication_owner
from app.mie.validation.prospective_capture_schedule import MINUTE_NS
from app.mie.validation.public_checkpoint_service import PublicCheckpointCaptureService
from app.mie.validation.scheduled_public_one_shot import (
    ScheduledPublicOneShotError,
    drive_scheduled_public_minute_once,
)
from tests.unit.mie.test_gate3_scheduled_public_window import _due, _setup

pytestmark = pytest.mark.asyncio
pytest_plugins = ("tests.unit.mie.test_gate3_scheduled_public_minute",)


def _invocation(coordinator, services, schedule, root, pins, *, ordinal, previous):
    del coordinator  # The one-shot entry point must reopen the original store.
    return {
        "root": root,
        "schedule": schedule,
        **{key: value for key, value in pins.items() if key != "schedule"},
        "ordinal": ordinal,
        "expected_previous_receipt_sha256s": previous,
        **services,
    }


async def test_default_off_does_not_open_store_or_call_source(case, tmp_path):
    kwargs, state, schedule = case
    missing_root = tmp_path / "not-initialized"
    with pytest.raises(ScheduledPublicOneShotError, match="disabled"):
        await drive_scheduled_public_minute_once(
            root=missing_root,
            schedule=schedule,
            expected_seal_sha256=kwargs["expected_seal_sha256"],
            expected_coordinate_plan_sha256=kwargs["expected_coordinate_plan_sha256"],
            expected_schedule_sha256=kwargs["expected_schedule_sha256"],
            ordinal=0,
            expected_previous_receipt_sha256s=(),
            seal_observer=kwargs["seal_observer"],
            schedule_repository=kwargs["schedule_repository"],
            capture_service=kwargs["capture_service"],
            observation_repository=kwargs["observation_repository"],
        )
    assert not missing_root.exists()
    assert state["seal_reads"] == state["schedule_reads"] == state["captures"] == 0


async def test_each_due_invocation_reopens_and_rechecks_seal_schedule_and_prefix(
    case, tmp_path, monkeypatch
):
    coordinator, services, schedule, clock, state, root, pins = _setup(
        case, tmp_path, monkeypatch
    )
    state["schedule_ack"] = replace(
        state["schedule_ack"],
        schedule_recorded_at=schedule.planned_at + timedelta(milliseconds=500),
    )
    first = await drive_scheduled_public_minute_once(
        **_invocation(
            coordinator, services, schedule, root, pins, ordinal=0, previous=()
        ),
        enabled=True,
    )
    assert first.status == "INCOMPLETE"
    assert first.reason_code == "missing_ordinal"
    assert first.verified_receipts == first.source_replayed_prefix == 1
    assert first.last_verified_receipt_sha256 is not None
    assert state["seal_reads"] == state["ack_reads"] == state["schedule_reads"] == 2

    _due(clock, schedule, 1)
    second = await drive_scheduled_public_minute_once(
        **_invocation(
            coordinator,
            services,
            schedule,
            root,
            pins,
            ordinal=1,
            previous=(first.last_verified_receipt_sha256,),
        ),
        enabled=True,
    )
    assert second.verified_receipts == second.source_replayed_prefix == 2
    assert state["captures"] == 2
    assert state["seal_reads"] == state["ack_reads"] == state["schedule_reads"] == 4
    assert not second.predictive_oos_eligible
    assert not second.execution_authority


async def test_db0030_or_db0029_pin_drift_denies_before_one_use_claim(
    case, tmp_path, monkeypatch
):
    coordinator, services, schedule, _, state, root, pins = _setup(
        case, tmp_path, monkeypatch
    )
    state["schedule_ack"] = replace(state["schedule_ack"], seal_sha256="0" * 64)
    with pytest.raises(ScheduledPublicOneShotError, match="committed_preflight"):
        await drive_scheduled_public_minute_once(
            **_invocation(
                coordinator, services, schedule, root, pins, ordinal=0, previous=()
            ),
            enabled=True,
        )
    assert state["seal_reads"] == state["ack_reads"] == state["schedule_reads"] == 1
    assert state["captures"] == 0
    assert not (root / "minute-00000000").exists()


async def test_db_preflight_timeout_does_not_consume_one_use_slot(
    case, tmp_path, monkeypatch
):
    coordinator, services, schedule, _, state, root, pins = _setup(
        case, tmp_path, monkeypatch
    )
    repository = services["schedule_repository"]
    original_read = repository.read

    async def unavailable(**_kwargs):
        raise TimeoutError("synthetic committed schedule read timeout")

    monkeypatch.setattr(repository, "read", unavailable)
    call = _invocation(
        coordinator, services, schedule, root, pins, ordinal=0, previous=()
    )
    with pytest.raises(ScheduledPublicOneShotError, match="committed_preflight"):
        await drive_scheduled_public_minute_once(**call, enabled=True)
    assert state["captures"] == 0
    assert not (root / "minute-00000000").exists()

    monkeypatch.setattr(repository, "read", original_read)
    recovered = await drive_scheduled_public_minute_once(**call, enabled=True)
    assert recovered.verified_receipts == recovered.source_replayed_prefix == 1
    assert state["captures"] == 1


async def test_db_pin_changes_after_preflight_are_caught_after_claim(
    case, tmp_path, monkeypatch
):
    coordinator, services, schedule, _, state, root, pins = _setup(
        case, tmp_path, monkeypatch
    )
    repository = services["schedule_repository"]
    original_read = repository.read

    async def changed_second_read(**kwargs):
        observed = await original_read(**kwargs)
        if state["schedule_reads"] == 2:
            return replace(observed, seal_sha256="0" * 64)
        return observed

    monkeypatch.setattr(repository, "read", changed_second_read)
    result = await drive_scheduled_public_minute_once(
        **_invocation(
            coordinator, services, schedule, root, pins, ordinal=0, previous=()
        ),
        enabled=True,
    )
    assert result.reason_code == "capture_unresolved"
    assert result.verified_receipts == 0
    assert (root / "minute-00000000" / "claim.json").is_file()
    assert not (root / "minute-00000000" / "receipt.json").exists()
    assert state["captures"] == 0


@pytest.mark.parametrize(
    ("offset_ns", "receipt_reason"),
    [(-1, "not_due"), (MINUTE_NS, "late")],
)
async def test_early_or_late_invocation_is_tombstoned_without_retry(
    case, tmp_path, monkeypatch, offset_ns, receipt_reason
):
    coordinator, services, schedule, clock, state, root, pins = _setup(
        case, tmp_path, monkeypatch
    )
    _due(clock, schedule, 0, offset_ns)
    call = _invocation(
        coordinator, services, schedule, root, pins, ordinal=0, previous=()
    )
    first = await drive_scheduled_public_minute_once(**call, enabled=True)
    assert first.reason_code == "source_incomplete"
    assert first.verified_receipts == 1
    receipt = coordinator._store.read_ordinal(
        0, expected_receipt_sha256=first.last_verified_receipt_sha256
    )
    assert receipt.contract.reason_code == receipt_reason
    assert state["captures"] == 0
    _due(clock, schedule, 0)
    repeat = await drive_scheduled_public_minute_once(**call, enabled=True)
    assert repeat.reason_code == "external_pin_missing"
    assert state["captures"] == 0


async def test_unknown_source_result_keeps_original_tombstone(
    case, tmp_path, monkeypatch
):
    coordinator, services, schedule, _, state, root, pins = _setup(
        case, tmp_path, monkeypatch
    )

    async def uncertain(_self, **_kwargs):
        state["captures"] += 1
        raise TimeoutError("synthetic uncertain public response")

    monkeypatch.setattr(
        PublicCheckpointCaptureService, "capture_with_post_read_v3", uncertain
    )
    call = _invocation(
        coordinator, services, schedule, root, pins, ordinal=0, previous=()
    )
    first = await drive_scheduled_public_minute_once(**call, enabled=True)
    assert first.reason_code == "source_incomplete"
    receipt = coordinator._store.read_ordinal(
        0, expected_receipt_sha256=first.last_verified_receipt_sha256
    )
    assert receipt.contract.reason_code == "capture_or_post_read_unresolved"
    repeat = await drive_scheduled_public_minute_once(**call, enabled=True)
    assert repeat.reason_code == "external_pin_missing"
    assert state["captures"] == 1


async def test_crash_after_claim_cannot_be_retried_by_new_process(
    case, tmp_path, monkeypatch
):
    coordinator, services, schedule, _, state, root, pins = _setup(
        case, tmp_path, monkeypatch
    )

    async def crash(**_kwargs):
        state["captures"] += 1
        raise RuntimeError("synthetic process crash")

    monkeypatch.setattr(
        publication_owner, "capture_scheduled_public_minute_once", crash
    )
    call = _invocation(
        coordinator, services, schedule, root, pins, ordinal=0, previous=()
    )
    first = await drive_scheduled_public_minute_once(**call, enabled=True)
    assert first.reason_code == "capture_unresolved"
    assert (root / "minute-00000000" / "claim.json").is_file()
    assert not (root / "minute-00000000" / "receipt.json").exists()
    restarted = await drive_scheduled_public_minute_once(**call, enabled=True)
    assert restarted.reason_code == "external_pin_missing"
    assert state["captures"] == 1
