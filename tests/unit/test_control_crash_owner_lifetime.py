"""Independent pure owner-lifetime probe; no PostgreSQL or exchange IO."""

import gc
import weakref
from dataclasses import replace
from datetime import timedelta

import pytest

from app.database.repositories.demo_control import ControlObservation
from app.trade_qualification import demo_control as controls
from app.trade_qualification.demo_control_runtime import DemoControlOwner
from scripts import control_durability_probe as probe
from tests.unit.test_demo_control import NOW


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", (None, "uid", "revoked", "expired", "wrong_owner"))
async def test_crash_seed_keeps_actual_runtime_owners_alive_until_kill(
    monkeypatch, damage
):
    references = []

    class TrackedOwner(DemoControlOwner):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            references.append(weakref.ref(self))

    class MemoryRepository:
        def __init__(self, _sessions, *, clock):
            self.clock = clock
            self.states = {}

        def observed(self, scope):
            return ControlObservation(self.states[scope], "e" * 64, self.clock())

        async def acquire(self, scope, *, owner_token, pins, command_id):
            self.states[scope] = controls.acquire(
                self.states.get(scope),
                scope=scope,
                owner_sha256=controls.owner_binding(owner_token),
                pins=pins,
                now=self.clock(),
            )
            return self.observed(scope)

        async def change(self, scope, *, owner_token, **kwargs):
            self.states[scope] = controls.advance(
                self.states[scope],
                owner_sha256=controls.owner_binding(owner_token),
                now=self.clock(),
                **kwargs,
            )
            return self.observed(scope)

        async def latch_stop(self, scope, *, command_id):
            previous = self.states.get(scope)
            self.states[scope] = (
                controls.stopped_genesis(scope, self.clock())
                if previous is None
                else replace(
                    previous,
                    revision=previous.revision + 1,
                    emergency_stop=True,
                    arm_request_id=None,
                    arm_expires_at=None,
                    updated_at=self.clock(),
                )
            )
            return self.observed(scope)

    monkeypatch.setattr(probe, "DemoControlRepository", MemoryRepository)
    monkeypatch.setattr(probe, "DemoControlOwner", TrackedOwner)
    marker_records = await probe.seed_controls(
        None, clock=lambda: NOW, active_uid="987654321000000000001"
    )
    assert len(marker_records.records) == 3
    gc.collect()
    # A physical kill of active runtime control owners needs those actual owner
    # objects to survive the helper return, until the outer seed process waits.
    assert len(references) == 2
    assert sum(reference() is not None for reference in references) == 2

    import json

    raw = json.dumps(marker_records.records)
    for owner in marker_records.owners:
        assert owner._owner_token.hex() not in raw

    active_uid = "987654321000000000001"
    probe.confirm_ready_controls(marker_records, expected_active_uid=active_uid)
    with pytest.raises(RuntimeError, match="active_scope_mismatch"):
        await probe.verify_controls(
            None, marker_records.records, expected_active_uid="987654321000000000002"
        )
    if damage is None:
        return
    if damage == "uid":
        active_uid = "987654321000000000002"
    elif damage == "revoked":
        marker_records.owners[0].revoke_now()
    elif damage == "expired":
        marker_records.owners[0].clock = lambda: NOW + timedelta(seconds=21)
    else:
        marker_records = probe.SeededControlState(
            marker_records.records, tuple(reversed(marker_records.owners))
        )
    with pytest.raises(
        RuntimeError,
        match="control_probe_(active_scope_mismatch|runtime_no_longer_ready)",
    ):
        probe.confirm_ready_controls(marker_records, expected_active_uid=active_uid)
