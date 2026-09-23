"""Synthetic DB0019 state inside the existing hermetic process-kill probe.

The surrounding qualification probe enforces the credential-free private test
network before calling these helpers. Logical fixture clocks test restart state,
not host-clock health, real account reconciliation or execution acceptance.
"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from app.database.repositories.demo_control import DemoControlRepository
from app.trade_qualification import demo_control as controls
from app.trade_qualification.demo_control_runtime import DemoControlOwner

_PINS = controls.ControlPins("1" * 64, "2" * 64, "3" * 64)
_SCENARIOS = ("arm_intent", "estop", "cold_estop")


@dataclass(frozen=True, slots=True, repr=False)
class SeededControlState:
    """Non-serializable keepalive; owners/tokens must never enter the marker."""

    records: list[dict]
    owners: tuple[DemoControlOwner, ...]


def _synthetic_uid():
    return f"986543210{uuid4().int % 10**12:012d}"


async def seed_controls(sessions, *, clock, active_uid):
    """Commit and independently read three states before the host sends SIGKILL."""
    repository = DemoControlRepository(sessions, clock=clock)
    records, owners = [], []
    for scenario in _SCENARIOS:
        scope = controls.ControlScope(
            "demo", active_uid if scenario == "arm_intent" else _synthetic_uid()
        )
        if scenario == "cold_estop":
            observed = await repository.latch_stop(scope, command_id=uuid4().hex * 2)
            if observed.state.owner_epoch != 0 or observed.state.pins is not None:
                raise RuntimeError("control_probe_cold_stop_invented_owner")
        else:
            owner = DemoControlOwner(repository, scope, _PINS, clock=clock)
            owners.append(owner)
            await owner.start()
            if scenario == "arm_intent":
                observed = await owner.request_arm(
                    expires_at=clock() + timedelta(seconds=20)
                )
                if not owner.observed_arm_intent:
                    raise RuntimeError("control_probe_arm_intent_missing")
            else:
                observed = await owner.emergency_stop()
        if (
            observed.execution_authority
            or observed.state.emergency_stop != (scenario != "arm_intent")
            or (observed.state.arm_request_id is not None) != (scenario == "arm_intent")
        ):
            raise RuntimeError("control_probe_seed_mismatch")
        # Only a one-way owner digest can appear in state; no memory token is saved.
        records.append(
            {
                "scenario": scenario,
                "state": controls.document(observed.state),
                "event_sha256": observed.event_sha256,
            }
        )
    return SeededControlState(records, tuple(owners))


def _decode_records(records, *, expected_active_uid):
    if type(expected_active_uid) is not str or not expected_active_uid.startswith(
        "987654321"
    ):
        raise RuntimeError("control_probe_active_scope_invalid")
    if type(records) is not list or len(records) != len(_SCENARIOS):
        raise RuntimeError("control_probe_marker_invalid")
    decoded = []
    for expected, record in zip(_SCENARIOS, records, strict=True):
        if (
            type(record) is not dict
            or set(record) != {"scenario", "state", "event_sha256"}
            or record["scenario"] != expected
        ):
            raise RuntimeError("control_probe_marker_invalid")
        state = controls.decode(controls.canonical(record["state"]))
        controls.sha(record["event_sha256"])
        if (
            not state.scope.account_id.startswith(("987654321", "986543210"))
            or state.emergency_stop != (expected != "arm_intent")
            or (state.arm_request_id is not None) != (expected == "arm_intent")
            or (state.owner_epoch == 0) != (expected == "cold_estop")
        ):
            raise RuntimeError("control_probe_synthetic_state_invalid")
        decoded.append((expected, state, record["event_sha256"]))
    if len({item[1].scope.account_id for item in decoded}) != len(_SCENARIOS):
        raise RuntimeError("control_probe_duplicate_scope")
    if decoded[0][1].scope.account_id != expected_active_uid:
        raise RuntimeError("control_probe_active_scope_mismatch")
    return tuple(decoded)


def confirm_ready_controls(seeded, *, expected_active_uid):
    """Sample current local intent immediately before marker publication.

    This has no await and never renews an expired Arm. It proves the observation
    before publication, not an instantaneous state at the later external kill.
    """
    decoded = _decode_records(seeded.records, expected_active_uid=expected_active_uid)
    if len(seeded.owners) != 2:
        raise RuntimeError("control_probe_runtime_owner_missing")
    for index, owner in enumerate(seeded.owners):
        state = decoded[index][1]
        if (
            owner.scope != state.scope
            or owner.owner_sha256 != state.owner_sha256
            or owner.observed_arm_intent != (index == 0)
            or owner.execution_authority
        ):
            raise RuntimeError("control_probe_runtime_no_longer_ready")


async def verify_controls(sessions, records, *, expected_active_uid):
    """Fresh process: verify saved bytes, then acquire a new unarmed owner epoch."""
    decoded = _decode_records(records, expected_active_uid=expected_active_uid)
    # Advance the explicit synthetic fixture clock past the dead owner's lease.
    # This is not an observed host timestamp and never enters real market evidence.
    now = max(item[1].lease_until for item in decoded) + timedelta(seconds=1)
    repository = DemoControlRepository(sessions, clock=lambda: now)
    for _, before, event_sha in decoded:
        observed = await repository.read(before.scope)
        if observed.state != before or observed.event_sha256 != event_sha:
            raise RuntimeError("control_probe_state_changed_after_restart")
        restarted = DemoControlOwner(repository, before.scope, _PINS, clock=lambda: now)
        if restarted.observed_arm_intent or restarted.execution_authority:
            raise RuntimeError("control_probe_initial_restart_authority")
        after = await restarted.start()
        if (
            after.state.owner_epoch != before.owner_epoch + 1
            or after.state.revision != before.revision + 1
            or after.state.owner_sha256 == before.owner_sha256
            or after.state.arm_request_id is not None
            or after.state.arm_expires_at is not None
            or after.state.emergency_stop != before.emergency_stop
            or restarted.observed_arm_intent
            or restarted.execution_authority
            or after.execution_authority
        ):
            raise RuntimeError("control_probe_restart_regained_authority")
        if before.emergency_stop:
            try:
                await restarted.request_arm(expires_at=now + timedelta(seconds=10))
            except controls.DemoControlError:
                pass
            else:
                raise RuntimeError("control_probe_stopped_owner_armed")
            still = await repository.read(before.scope)
            if still != after:
                raise RuntimeError("control_probe_denied_arm_changed_state")
