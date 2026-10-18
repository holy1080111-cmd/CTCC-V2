"""Finite prospective public research orchestration with synthetic services."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

import app.mie.validation.scheduled_public_four_hour_runner as runner_owner
import app.mie.validation.scheduled_public_minute as minute_owner
import app.mie.validation.scheduled_public_pin_chain as pin_owner
from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
)
from app.mie.validation.gate3_capture_schedule_pin import (
    Gate3CaptureSchedulePinRepository,
)
from app.mie.validation.gate3_capture_schedule_publication_ack import (
    Gate3CaptureSchedulePublicationAckRepository,
    Gate3SchedulePublicationAckReadback,
)
from app.mie.validation.gate3_preregistration_seal_observer import (
    Gate3CommittedSealAckReadback,
    Gate3CommittedSealReadback,
    Gate3PreregistrationSealObserver,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_capture_schedule import (
    ProspectiveCoordinatePlanV1,
    build_capture_schedule,
)
from app.mie.validation.public_checkpoint_service import (
    _ISSUER,
    OwnedPostReadCapture,
    PublicCheckpointCaptureService,
    WitnessedPublicCapture,
)
from app.mie.validation.scheduled_public_four_hour_runner import (
    ScheduledPublicFourHourRunner,
)
from app.mie.validation.scheduled_public_minute_publication import (
    ScheduledMinuteReceiptStore,
)
from app.mie.validation.scheduled_public_pin_chain import (
    ScheduledPinChainError,
    ScheduledPublicPinChain,
)
from app.public_market_source.public_market_receipts import (
    PublicRawReceiptV1,
    canonical,
    utc_from_ns,
)
from tests.unit.mie.test_gate3_blind_window_capture import fixture_inputs
from tests.unit.mie.test_gate3_prospective import valid_prospective_preregistration

pytestmark = pytest.mark.asyncio


@pytest.fixture
def original_source(monkeypatch):
    return fixture_inputs(monkeypatch)


async def test_page_chain_digest_replays_original_owned_journal(original_source):
    source = original_source
    journal = source["journal"]
    service = PublicCheckpointCaptureService(_ISSUER, journal, None, None, None)
    inventory = pin_owner._source_inventory(service)
    entry = inventory[source["capture_id"]]
    assert entry[0] == source["expected_capture_receipt_sha256"]
    assert entry[1] == source["expected_capture_plan_sha256"]
    final_page = PublicRawReceiptV1.model_validate_json(
        canonical(journal.read_all()[0][0][1].pages[-1])
    )
    assert entry[2] == final_page.canonical_sha256()


@pytest.fixture(scope="module")
def four_hour():
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    start = now.replace(hour=(now.hour // 4) * 4) + timedelta(hours=8)
    end = start + timedelta(hours=4)
    planned_at = now - timedelta(minutes=2)
    coordinate = ProspectiveCoordinatePlanV1(
        holdout_id="okx:synthetic:two-symbol:four-hour",
        origin="https://openapi.okx.com",
        instrument_ids=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
        start_at=start,
        end_at=end,
        expected_rows=480,
    )
    raw = valid_prospective_preregistration().model_dump(mode="python")
    raw["created_at"] = planned_at - timedelta(minutes=2)
    raw["prospective_holdout"].update(
        holdout_id=coordinate.holdout_id,
        source=coordinate.source,
        source_version=coordinate.source_version,
        instrument_ids=coordinate.instrument_ids,
        coordinate_plan_sha256=coordinate.canonical_sha256(),
        bar_interval_seconds=60,
        artifact_interval_seconds=60,
        start_at=start,
        end_at=end,
        publication_lag_seconds=60,
        first_permitted_access_at=end + timedelta(minutes=1),
        expected_artifact_count=480,
        expected_rows=480,
    )
    seal = Gate3ProspectivePreregistration.model_validate(raw)
    schedule = build_capture_schedule(
        seal=seal, coordinate_plan=coordinate, planned_at=planned_at
    )
    return seal, schedule


@pytest.fixture
def setup(four_hour, tmp_path, monkeypatch):
    seal, schedule = four_hour
    source_root = tmp_path / "scheduled-source"
    pin_root = tmp_path / "separate-pins"
    source_root.mkdir()
    pin_root.mkdir()
    expected = {
        "expected_seal_sha256": seal.canonical_sha256(),
        "expected_coordinate_plan_sha256": schedule.coordinate_plan_sha256,
        "expected_schedule_sha256": schedule.canonical_sha256(),
    }
    ScheduledMinuteReceiptStore.initialize(source_root, schedule=schedule, **expected)
    pin_chain = ScheduledPublicPinChain.initialize(
        pin_root, source_root, schedule, **expected
    )
    observer = Gate3PreregistrationSealObserver(None)
    schedule_repository = Gate3CaptureSchedulePublicationAckRepository(
        None, Gate3CaptureSchedulePinRepository(None)
    )
    capture_service = PublicCheckpointCaptureService(_ISSUER, None, None, None, None)
    observation_repository = PublicReceiptPostReadRepository(
        None, PublicReceiptWitnessRepository(None)
    )
    recorded_at = schedule.planned_at - timedelta(seconds=30)
    acknowledged_at = schedule.planned_at - timedelta(seconds=15)
    seal_read = Gate3CommittedSealReadback(
        seal_sha256=seal.canonical_sha256(),
        coordinate_plan_sha256=schedule.coordinate_plan_sha256,
        window_key=schedule.coordinate_plan.window_key(),
        seal_recorded_at=recorded_at,
        database_readback_at=recorded_at + timedelta(seconds=1),
        seal=seal,
    )
    seal_ack = Gate3CommittedSealAckReadback(
        seal_sha256=seal.canonical_sha256(),
        seal_recorded_at=recorded_at,
        acknowledged_at=acknowledged_at,
        database_readback_at=acknowledged_at + timedelta(seconds=1),
    )
    schedule_ack = Gate3SchedulePublicationAckReadback(
        schedule_sha256=schedule.canonical_sha256(),
        seal_sha256=seal.canonical_sha256(),
        coordinate_plan_sha256=schedule.coordinate_plan_sha256,
        window_key=schedule.coordinate_plan.window_key(),
        holdout_id=schedule.coordinate_plan.holdout_id,
        schedule_recorded_at=schedule.planned_at,
        acknowledged_at=schedule.planned_at + timedelta(seconds=1),
        database_readback_at=schedule.planned_at + timedelta(seconds=2),
        schedule=schedule,
    )
    state = {
        "captures": 0,
        "source": {},
        "clock_ns": schedule.plans[0].end_ns + 1_000_000_000,
    }

    async def read_seal(**_kwargs):
        return seal_read

    async def read_ack(**_kwargs):
        return seal_ack

    async def read_schedule(**_kwargs):
        return schedule_ack

    async def capture_one(_self, *, plan, **_kwargs):
        state["captures"] += 1
        ordinal = schedule.plans.index(plan)
        marker = ordinal + 1
        capture_id = f"{marker:032x}"
        receipt_sha = f"{marker:064x}"
        state["source"][capture_id] = (
            receipt_sha,
            plan.canonical_sha256(),
            f"{marker + 1000:064x}",
        )
        return OwnedPostReadCapture(
            witnessed=WitnessedPublicCapture(
                capture_id=capture_id,
                receipt_sha256=receipt_sha,
                witness_revision=marker + 2,
                witness_record_sha256=f"{marker + 2000:064x}",
                checkpoint_sha256=f"{marker + 3000:064x}",
            ),
            blind_capture_sha256=f"{marker + 4000:064x}",
            post_read_v3_sha256=f"{marker + 5000:064x}",
            post_read_observed_at=utc_from_ns(plan.end_ns) + timedelta(seconds=2),
        )

    monkeypatch.setattr(observer, "read_seal", read_seal)
    monkeypatch.setattr(observer, "read_ack", read_ack)
    monkeypatch.setattr(schedule_repository, "read", read_schedule)
    monkeypatch.setattr(
        PublicCheckpointCaptureService, "capture_with_post_read_v3", capture_one
    )
    monkeypatch.setattr(
        pin_owner, "_source_inventory", lambda _service: state["source"].copy()
    )
    monkeypatch.setattr(
        runner_owner,
        "native_stamp",
        lambda: {"utc_ns": state["clock_ns"], "monotonic_ns": state["clock_ns"]},
    )
    monkeypatch.setattr(
        minute_owner,
        "native_stamp",
        lambda: {"utc_ns": state["clock_ns"], "monotonic_ns": state["clock_ns"]},
    )
    inputs = {
        "root": source_root,
        "schedule": schedule,
        **expected,
        "pin_chain": pin_chain,
        "seal_observer": observer,
        "schedule_repository": schedule_repository,
        "capture_service": capture_service,
        "observation_repository": observation_repository,
    }
    return inputs, state, source_root, pin_root


async def test_default_off_and_two_due_rows_survive_restart(setup):
    inputs, state, source_root, pin_root = setup
    runner = ScheduledPublicFourHourRunner(**inputs)
    disabled = await runner.dispatch_ordinal_once(ordinal=0)
    assert disabled.reason_code == "disabled"
    assert state["captures"] == 0
    assert not (source_root / "minute-00000000").exists()

    first = await runner.dispatch_ordinal_once(ordinal=0, enabled=True)
    assert first.reason_code == "window_in_progress_custody_unverified"
    assert first.pinned_rows == first.source_replayed_rows == 1
    assert first.status == "INCOMPLETE"
    assert not first.independently_protected
    assert not first.predictive_oos_eligible
    assert not first.execution_authority
    assert first.latest_pin_sha256 is not None
    first_pin_bytes = (pin_root / "pin-00000000.json").read_bytes()
    assert state["captures"] == 1

    state["clock_ns"] = inputs["schedule"].plans[1].end_ns + 1_000_000_000
    restarted_pin = ScheduledPublicPinChain.resume(
        pin_root,
        source_root,
        inputs["schedule"],
        expected_seal_sha256=inputs["expected_seal_sha256"],
        expected_coordinate_plan_sha256=inputs["expected_coordinate_plan_sha256"],
        expected_schedule_sha256=inputs["expected_schedule_sha256"],
    )
    second = await ScheduledPublicFourHourRunner(
        **{**inputs, "pin_chain": restarted_pin}
    ).dispatch_ordinal_once(ordinal=1, enabled=True)
    assert second.pinned_rows == second.source_replayed_rows == 2
    assert state["captures"] == 2
    assert (pin_root / "pin-00000000.json").read_bytes() == first_pin_bytes
    pins = restarted_pin.read_prefix(
        source_store=ScheduledMinuteReceiptStore.resume(
            source_root,
            schedule=inputs["schedule"],
            expected_seal_sha256=inputs["expected_seal_sha256"],
            expected_coordinate_plan_sha256=inputs["expected_coordinate_plan_sha256"],
            expected_schedule_sha256=inputs["expected_schedule_sha256"],
        ),
        capture_service=inputs["capture_service"],
    )
    assert pins[1].previous_pin_sha256 == pins[0].canonical_sha256()
    assert pins[0].source_last_page_sha256 == f"{1001:064x}"
    assert pins[0].source_capture_receipt_sha256 == f"{1:064x}"


async def test_duplicate_after_restart_cannot_recapture(setup):
    inputs, state, _source_root, _pin_root = setup
    runner = ScheduledPublicFourHourRunner(**inputs)
    await runner.dispatch_ordinal_once(ordinal=0, enabled=True)
    duplicate = await ScheduledPublicFourHourRunner(**inputs).dispatch_ordinal_once(
        ordinal=0, enabled=True
    )
    assert duplicate.reason_code == "prior_minute_incomplete"
    assert duplicate.pinned_rows == 1
    assert state["captures"] == 1


async def test_two_dispatchers_race_one_source_claim(setup, monkeypatch):
    inputs, state, _source_root, pin_root = setup
    original = PublicCheckpointCaptureService.capture_with_post_read_v3

    async def delayed_capture(self, **kwargs):
        await asyncio.sleep(0)
        return await original(self, **kwargs)

    monkeypatch.setattr(
        PublicCheckpointCaptureService,
        "capture_with_post_read_v3",
        delayed_capture,
    )
    statuses = await asyncio.gather(
        *(
            ScheduledPublicFourHourRunner(**inputs).dispatch_ordinal_once(
                ordinal=0, enabled=True
            )
            for _ in range(2)
        )
    )
    assert state["captures"] == 1
    assert len(list(pin_root.glob("pin-*.json"))) == 1
    assert (
        sum(
            status.reason_code == "window_in_progress_custody_unverified"
            for status in statuses
        )
        == 1
    )


async def test_late_original_receipt_is_pinned_and_stops_window(setup):
    inputs, state, source_root, pin_root = setup
    state["clock_ns"] = inputs["schedule"].plans[0].end_ns + 60_000_000_000
    runner = ScheduledPublicFourHourRunner(**inputs)
    late = await runner.dispatch_ordinal_once(ordinal=0, enabled=True)
    assert late.reason_code == "scheduled_source_incomplete"
    assert late.pinned_rows == 1
    assert late.source_replayed_rows == 0
    assert state["captures"] == 0
    assert (source_root / "minute-00000000" / "receipt.json").is_file()
    assert (pin_root / "pin-00000000.json").is_file()
    repeat = await runner.dispatch_ordinal_once(ordinal=1, enabled=True)
    assert repeat.reason_code == "prior_minute_incomplete"
    assert state["captures"] == 0


async def test_unknown_source_result_is_one_use_and_never_retried(setup, monkeypatch):
    inputs, state, source_root, _pin_root = setup

    async def uncertain(_self, **_kwargs):
        state["captures"] += 1
        raise TimeoutError("synthetic source timeout")

    monkeypatch.setattr(
        PublicCheckpointCaptureService, "capture_with_post_read_v3", uncertain
    )
    runner = ScheduledPublicFourHourRunner(**inputs)
    first = await runner.dispatch_ordinal_once(ordinal=0, enabled=True)
    assert first.reason_code == "scheduled_source_incomplete"
    assert first.pinned_rows == 1
    assert (source_root / "minute-00000000" / "claim.json").is_file()
    repeated = await runner.dispatch_ordinal_once(ordinal=0, enabled=True)
    assert repeated.reason_code == "prior_minute_incomplete"
    assert state["captures"] == 1


async def test_finite_runner_stops_on_first_unknown_without_next_dispatch(
    setup, monkeypatch
):
    inputs, state, source_root, _pin_root = setup

    async def uncertain(_self, **_kwargs):
        state["captures"] += 1
        raise TimeoutError("synthetic source timeout")

    monkeypatch.setattr(
        PublicCheckpointCaptureService, "capture_with_post_read_v3", uncertain
    )
    status = await ScheduledPublicFourHourRunner(**inputs).run_window(enabled=True)
    assert status.reason_code == "scheduled_source_incomplete"
    assert status.pinned_rows == 1
    assert state["captures"] == 1
    assert not (source_root / "minute-00000001").exists()


async def test_native_clock_failure_does_not_claim_or_open_source(setup, monkeypatch):
    inputs, state, source_root, _pin_root = setup

    def broken_stamp():
        raise OSError("synthetic native clock unavailable")

    monkeypatch.setattr(runner_owner, "native_stamp", broken_stamp)
    status = await ScheduledPublicFourHourRunner(**inputs).dispatch_ordinal_once(
        ordinal=0, enabled=True
    )
    assert status.reason_code == "native_clock_unavailable"
    assert status.pinned_rows == 0
    assert state["captures"] == 0
    assert not (source_root / "minute-00000000").exists()


async def test_crash_between_original_receipt_and_pin_blocks_restart(
    setup, monkeypatch
):
    inputs, state, source_root, pin_root = setup

    def pin_failure(_self, **_kwargs):
        raise ScheduledPinChainError("synthetic pin sink unavailable")

    monkeypatch.setattr(ScheduledPublicPinChain, "append", pin_failure)
    first = await ScheduledPublicFourHourRunner(**inputs).dispatch_ordinal_once(
        ordinal=0, enabled=True
    )
    assert first.reason_code == "scheduled_pin_append_unresolved"
    assert state["captures"] == 1
    assert (source_root / "minute-00000000" / "receipt.json").is_file()
    assert not (pin_root / "pin-00000000.json").exists()
    resumed = await ScheduledPublicFourHourRunner(**inputs).dispatch_ordinal_once(
        ordinal=0, enabled=True
    )
    assert resumed.reason_code == "pin_or_original_source_unavailable"
    assert state["captures"] == 1


async def test_changed_original_page_chain_or_pin_denies_continuation(setup):
    inputs, state, _source_root, pin_root = setup
    runner = ScheduledPublicFourHourRunner(**inputs)
    first = await runner.dispatch_ordinal_once(ordinal=0, enabled=True)
    assert first.pinned_rows == 1
    state["source"][f"{1:032x}"] = (
        f"{1:064x}",
        inputs["schedule"].plans[0].canonical_sha256(),
        "f" * 64,
    )
    changed_source = await runner.dispatch_ordinal_once(ordinal=1, enabled=True)
    assert changed_source.reason_code == "pin_or_original_source_unavailable"
    assert state["captures"] == 1
    state["source"][f"{1:032x}"] = (
        f"{1:064x}",
        inputs["schedule"].plans[0].canonical_sha256(),
        f"{1001:064x}",
    )
    payload = (pin_root / "pin-00000000.json").read_bytes()
    (pin_root / "pin-00000000.json").write_bytes(payload + b" ")
    changed_pin = await runner.dispatch_ordinal_once(ordinal=1, enabled=True)
    assert changed_pin.reason_code == "pin_or_original_source_unavailable"
    assert state["captures"] == 1


async def test_source_changed_during_due_wait_blocks_next_capture(setup, monkeypatch):
    inputs, state, source_root, pin_root = setup
    runner = ScheduledPublicFourHourRunner(**inputs)
    assert (
        await runner.dispatch_ordinal_once(ordinal=0, enabled=True)
    ).pinned_rows == 1
    state["clock_ns"] = inputs["schedule"].plans[1].end_ns + 1_000_000_000

    async def change_prior_source(_due_ns):
        state["source"][f"{1:032x}"] = (
            f"{1:064x}",
            inputs["schedule"].plans[0].canonical_sha256(),
            "f" * 64,
        )

    monkeypatch.setattr(runner_owner, "_wait_until_due", change_prior_source)
    status = await runner.dispatch_ordinal_once(ordinal=1, enabled=True)
    assert status.reason_code == "pin_or_original_source_unavailable"
    assert status.pinned_rows == 1
    assert state["captures"] == 1
    assert not (source_root / "minute-00000001").exists()
    assert not (pin_root / "pin-00000001.json").exists()


async def test_source_changed_during_capture_blocks_next_pin(setup, monkeypatch):
    inputs, state, source_root, pin_root = setup
    runner = ScheduledPublicFourHourRunner(**inputs)
    assert (
        await runner.dispatch_ordinal_once(ordinal=0, enabled=True)
    ).pinned_rows == 1
    state["clock_ns"] = inputs["schedule"].plans[1].end_ns + 1_000_000_000
    original = runner_owner.drive_scheduled_public_minute_once

    async def change_after_capture(**kwargs):
        result = await original(**kwargs)
        state["source"][f"{1:032x}"] = (
            f"{1:064x}",
            inputs["schedule"].plans[0].canonical_sha256(),
            "f" * 64,
        )
        return result

    monkeypatch.setattr(
        runner_owner, "drive_scheduled_public_minute_once", change_after_capture
    )
    status = await runner.dispatch_ordinal_once(ordinal=1, enabled=True)
    assert status.reason_code == "scheduled_pin_append_unresolved"
    assert status.pinned_rows == 1
    assert state["captures"] == 2
    assert (source_root / "minute-00000001" / "receipt.json").is_file()
    assert not (pin_root / "pin-00000001.json").exists()
    resumed = await ScheduledPublicFourHourRunner(**inputs).dispatch_ordinal_once(
        ordinal=1, enabled=True
    )
    assert resumed.reason_code == "pin_or_original_source_unavailable"
    assert state["captures"] == 2


async def test_separate_root_and_four_hour_geometry_are_required(
    setup, four_hour, tmp_path
):
    inputs, _state, source_root, _pin_root = setup
    with pytest.raises(ScheduledPinChainError, match="overlap"):
        ScheduledPublicPinChain.resume(
            source_root,
            source_root,
            inputs["schedule"],
            expected_seal_sha256=inputs["expected_seal_sha256"],
            expected_coordinate_plan_sha256=inputs["expected_coordinate_plan_sha256"],
            expected_schedule_sha256=inputs["expected_schedule_sha256"],
        )
    _seal, schedule = four_hour
    fake_root = tmp_path / "short-pins"
    fake_root.mkdir()
    with pytest.raises(ScheduledPinChainError, match="four_hour_grid"):
        ScheduledPublicPinChain.initialize(
            fake_root,
            source_root,
            schedule.model_copy(
                update={
                    "coordinate_plan": schedule.coordinate_plan.model_copy(
                        update={"expected_rows": 4}
                    )
                }
            ),
            expected_seal_sha256=inputs["expected_seal_sha256"],
            expected_coordinate_plan_sha256=inputs["expected_coordinate_plan_sha256"],
            expected_schedule_sha256=inputs["expected_schedule_sha256"],
        )


async def test_pin_identity_cannot_name_a_different_source_root(setup, tmp_path):
    inputs, state, source_a, pin_root = setup
    source_b = tmp_path / "other-scheduled-source"
    source_b.mkdir()
    source_b_store = ScheduledMinuteReceiptStore.initialize(
        source_b,
        schedule=inputs["schedule"],
        expected_seal_sha256=inputs["expected_seal_sha256"],
        expected_coordinate_plan_sha256=inputs["expected_coordinate_plan_sha256"],
        expected_schedule_sha256=inputs["expected_schedule_sha256"],
    )
    with pytest.raises(ScheduledPinChainError, match="source_root_changed"):
        ScheduledPublicFourHourRunner(**{**inputs, "root": source_b})
    with pytest.raises(ScheduledPinChainError, match="source_store_changed"):
        inputs["pin_chain"].read_prefix(
            source_store=source_b_store,
            capture_service=inputs["capture_service"],
        )
    with pytest.raises(ScheduledPinChainError, match="source_store_changed"):
        inputs["pin_chain"].append(
            ordinal=0,
            expected_receipt_sha256="a" * 64,
            expected_previous_pin_sha256=None,
            source_store=source_b_store,
            capture_service=inputs["capture_service"],
        )
    assert state["captures"] == 0
    assert not (source_a / "minute-00000000").exists()
    assert not (source_b / "minute-00000000").exists()
    assert not (pin_root / "pin-00000000.json").exists()


async def test_replaced_source_directory_inode_denies_runner(setup, tmp_path):
    inputs, state, source_root, pin_root = setup
    moved = tmp_path / "original-scheduled-source"
    source_root.rename(moved)
    source_root.mkdir()
    with pytest.raises(ScheduledPinChainError, match="source_root_changed"):
        ScheduledPublicFourHourRunner(**inputs)
    assert state["captures"] == 0
    assert not (pin_root / "pin-00000000.json").exists()
