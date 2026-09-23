"""Control intention/fencing contracts; no database or exchange acceptance."""

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo

import pytest

from app.database.repositories.demo_control import (
    ControlObservation,
    DemoControlRepository,
)
from app.trade_qualification import demo_control as c
from app.trade_qualification.demo_control_runtime import DemoControlOwner

NOW = datetime(2026, 9, 19, tzinfo=UTC)
SCOPE = c.ControlScope("demo", "123456789")
PINS = c.ControlPins("1" * 64, "2" * 64, "3" * 64)
OWNER = "4" * 64


def state():
    return c.acquire(None, scope=SCOPE, owner_sha256=OWNER, pins=PINS, now=NOW)


def change(previous, action, **kwargs):
    return c.advance(
        previous,
        owner_sha256=previous.owner_sha256,
        owner_epoch=previous.owner_epoch,
        expected_revision=previous.revision,
        action=action,
        command_id="5" * 64,
        now=NOW + timedelta(seconds=1),
        **kwargs,
    )


@pytest.mark.parametrize("environment", ["live", "Demo", "test", None, True])
def test_control_rejects_every_non_demo_scope(environment):
    with pytest.raises(c.DemoControlError):
        c.ControlScope(environment, "123456789")


@pytest.mark.parametrize(
    "uid", [None, 123456789, "", "１２３４５", "123/456", "1" * 33]
)
def test_exact_numeric_uid_is_mandatory(uid):
    with pytest.raises(c.DemoControlError):
        c.ControlScope("demo", uid)


def test_uid_lock_matches_existing_ledger_protocol_and_ignores_no_currency():
    expected = int.from_bytes(
        hashlib.sha256(b"ctcc-qualification-account-v1\0demo\x00123456789").digest()[
            :8
        ],
        "big",
        signed=True,
    )
    assert c.account_lock_key(SCOPE) == expected
    assert c.account_lock_key(c.ControlScope("demo", "123456780")) != expected


def test_roundtrip_canonical_record_and_no_authority():
    current = state()
    assert c.decode(c.canonical(c.document(current))) == current
    assert current.execution_authority is False
    assert not current.arm_intent_current(NOW)
    armed = change(current, "arm_requested", arm_expires_at=NOW + timedelta(seconds=10))
    assert armed.arm_intent_current(NOW + timedelta(seconds=2))
    assert not armed.arm_intent_current(NOW + timedelta(seconds=10))
    assert not armed.execution_authority


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", True),
        ("owner_epoch", 2),
        ("emergency_stop", 1),
        ("owner_sha256", "x" * 64),
        ("lease_until", NOW - timedelta(seconds=1)),
        ("arm_request_id", "6" * 64),
    ],
)
def test_state_rejects_invalid_critical_fields(field, value):
    with pytest.raises(c.DemoControlError):
        replace(state(), **{field: value})


@pytest.mark.parametrize(
    "mutation", ["unknown", "duplicate", "whitespace", "version", "live"]
)
def test_json_is_exact_canonical_known_version(mutation):
    value = c.document(state())
    if mutation == "unknown":
        value["execution_authority"] = True
    if mutation == "version":
        value["version"] = "ctcc.demo_control.v2"
    if mutation == "live":
        value["environment"] = "live"
    raw = c.canonical(value)
    if mutation == "duplicate":
        raw = raw[:-1] + ',"revision":1}'
    if mutation == "whitespace":
        raw += "\n"
    with pytest.raises(c.DemoControlError):
        c.decode(raw)


def test_clock_rejects_foreign_tzinfo_without_callback():
    class Foreign(tzinfo):
        def utcoffset(self, _value):
            raise AssertionError("must not invoke foreign clock")

    with pytest.raises(c.DemoControlError, match="exact_utc"):
        c.utc(datetime(2026, 1, 1, tzinfo=Foreign()))


def test_live_lease_excludes_second_owner_and_restart_cannot_inherit_arm():
    old = change(state(), "arm_requested", arm_expires_at=NOW + timedelta(seconds=20))
    with pytest.raises(c.DemoControlError, match="owner_conflict"):
        c.acquire(
            old,
            scope=SCOPE,
            owner_sha256="7" * 64,
            pins=PINS,
            now=NOW + timedelta(seconds=2),
        )
    new = c.acquire(
        old, scope=SCOPE, owner_sha256="7" * 64, pins=PINS, now=old.lease_until
    )
    assert new.owner_epoch == 2 and new.revision == 3
    assert new.arm_request_id is None and not new.arm_intent_current(new.updated_at)
    with pytest.raises(c.DemoControlError, match="owner_conflict"):
        c.acquire(old, scope=SCOPE, owner_sha256=OWNER, pins=PINS, now=old.lease_until)


def test_estop_survives_restart_and_clear_stop_not_an_action():
    stopped = change(state(), "estop")
    newer = c.acquire(
        stopped, scope=SCOPE, owner_sha256="7" * 64, pins=PINS, now=stopped.lease_until
    )
    assert newer.emergency_stop and newer.arm_request_id is None
    with pytest.raises(c.DemoControlError, match="action_not_supported"):
        change(state(), "clear_stop")
    with pytest.raises(c.DemoControlError, match="estop_latched"):
        change(stopped, "arm_requested", arm_expires_at=NOW + timedelta(seconds=10))


@pytest.mark.parametrize(
    "expiry", [NOW, NOW + timedelta(seconds=31), NOW.replace(tzinfo=None)]
)
def test_arm_never_extends_lease_or_accepts_naive_expiry(expiry):
    with pytest.raises(c.DemoControlError):
        change(state(), "arm_requested", arm_expires_at=expiry)


def test_renew_does_not_extend_original_arm_and_rebind_clears_it():
    armed = change(state(), "arm_requested", arm_expires_at=NOW + timedelta(seconds=10))
    renewed = change(armed, "renew")
    assert renewed.arm_expires_at == armed.arm_expires_at
    rebound = change(renewed, "rebind", pins=replace(PINS, config_sha256="a" * 64))
    assert rebound.arm_request_id is None and rebound.pins != PINS


@pytest.mark.parametrize(
    "action", ["renew", "arm_requested", "disarm", "estop", "rebind", "release"]
)
def test_expired_owner_cannot_mutate_or_renew(action):
    old = state()
    with pytest.raises(c.DemoControlError, match="lease_or_clock"):
        c.advance(
            old,
            owner_sha256=OWNER,
            owner_epoch=1,
            expected_revision=1,
            action=action,
            command_id="5" * 64,
            now=old.lease_until,
        )


class Clock:
    def __init__(self):
        self.value, self.mono = NOW, 1000.0

    def __call__(self):
        return self.value

    def monotonic(self):
        return self.mono


class MemoryFailureDouble:
    """Fault-injection double only, explicitly not a PostgreSQL acceptance."""

    def __init__(self, clock):
        self.clock, self.state, self.pause, self.fail = clock, None, None, None
        self.entered = asyncio.Event()
        self.calls = []

    def observation(self):
        return ControlObservation(self.state, "e" * 64, self.clock())

    async def acquire(self, scope, *, owner_token, pins, command_id):
        self.calls.append("acquire")
        self.state = c.acquire(
            self.state,
            scope=scope,
            owner_sha256=c.owner_binding(owner_token),
            pins=pins,
            now=self.clock(),
        )
        if self.fail:
            raise RuntimeError("synthetic-private-diagnostic")
        return self.observation()

    async def change(self, scope, **kwargs):
        self.calls.append(kwargs["action"])
        kwargs["owner_sha256"] = c.owner_binding(kwargs.pop("owner_token"))
        self.state = c.advance(self.state, now=self.clock(), **kwargs)
        self.entered.set()
        if self.pause:
            await self.pause.wait()
        if self.fail:
            raise RuntimeError("synthetic-private-diagnostic")
        return self.observation()

    async def _tighten(self, scope, *, stop):
        self.calls.append("estop" if stop else "disarm")
        if self.fail:
            raise RuntimeError("synthetic-private-diagnostic")
        if self.state is None and stop:
            self.state = c.stopped_genesis(scope, self.clock())
            return self.observation()
        self.state = replace(
            self.state,
            revision=self.state.revision + 1,
            emergency_stop=self.state.emergency_stop or stop,
            arm_request_id=None,
            arm_expires_at=None,
            updated_at=self.clock(),
        )
        return self.observation()

    async def latch_stop(self, scope, *, command_id):
        return await self._tighten(scope, stop=True)

    async def revoke_arm(self, scope, *, command_id):
        return await self._tighten(scope, stop=False)


def runtime():
    clock = Clock()
    db = MemoryFailureDouble(clock)
    return (
        DemoControlOwner(db, SCOPE, PINS, clock=clock, monotonic=clock.monotonic),
        db,
        clock,
    )


@pytest.mark.asyncio
async def test_owner_token_not_stored_and_arm_is_only_an_intention():
    owner, db, _ = runtime()
    assert not owner.observed_arm_intent and not owner.execution_authority
    result = await owner.start()
    assert owner.owner_sha256 == hashlib.sha256(owner._owner_token).hexdigest()
    raw = c.canonical(c.document(result.state))
    assert owner._owner_token.hex() not in raw
    assert not result.execution_authority
    await owner.request_arm(expires_at=NOW + timedelta(seconds=10))
    assert owner.observed_arm_intent and not owner.execution_authority
    await owner.disarm()
    assert not owner.observed_arm_intent and db.state.arm_request_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["start", "arm", "renew", "stop"])
async def test_database_or_lost_commit_ack_never_returns_authority(operation):
    owner, db, _ = runtime()
    if operation != "start":
        await owner.start()
    db.fail = True
    with pytest.raises(c.DemoControlError) as error:
        if operation == "start":
            await owner.start()
        elif operation == "arm":
            await owner.request_arm(expires_at=NOW + timedelta(seconds=10))
        elif operation == "renew":
            await owner.renew()
        else:
            await owner.emergency_stop()
    assert "synthetic-private-diagnostic" not in str(error.value)
    assert (
        owner.uncertain
        and not owner.observed_arm_intent
        and not owner.execution_authority
    )
    db.fail = False
    with pytest.raises(c.DemoControlError, match="not_available"):
        await owner.renew()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", [False, True])
async def test_safety_revokes_inflight_arm_before_awaited_persistence(stop):
    owner, db, _ = runtime()
    await owner.start()
    db.pause = asyncio.Event()
    arm = asyncio.create_task(owner.request_arm(expires_at=NOW + timedelta(seconds=10)))
    await db.entered.wait()
    safety = asyncio.create_task(owner.emergency_stop() if stop else owner.disarm())
    await asyncio.sleep(0)
    assert not owner.observed_arm_intent
    db.pause.set()
    with pytest.raises(c.DemoControlError, match="uncertain"):
        await arm
    await safety
    assert db.state.arm_request_id is None and db.state.emergency_stop is stop
    assert owner.uncertain and not owner.execution_authority


@pytest.mark.asyncio
async def test_cancellation_after_durable_change_poisoned_owner_cannot_retry():
    owner, db, _ = runtime()
    await owner.start()
    db.pause = asyncio.Event()
    arm = asyncio.create_task(owner.request_arm(expires_at=NOW + timedelta(seconds=10)))
    await db.entered.wait()
    arm.cancel()
    with pytest.raises(asyncio.CancelledError):
        await arm
    assert owner.uncertain and not owner.observed_arm_intent
    with pytest.raises(c.DemoControlError):
        await owner.renew()
    await owner.emergency_stop()
    assert db.state.emergency_stop


@pytest.mark.asyncio
async def test_monotonic_lease_expiry_and_regression_fail_closed():
    owner, _, clock = runtime()
    await owner.start()
    await owner.request_arm(expires_at=NOW + timedelta(seconds=10))
    clock.mono += 30
    assert not owner.observed_arm_intent
    clock.mono -= 31
    assert not owner.observed_arm_intent and owner.uncertain


@pytest.mark.asyncio
async def test_missing_reconciliation_cannot_clear_latch():
    owner, _, _ = runtime()
    await owner.start()
    await owner.emergency_stop()
    with pytest.raises(c.DemoControlError, match="producer_unavailable"):
        await owner.clear_stop()
    assert not owner.observed_arm_intent and not owner.execution_authority


@pytest.mark.asyncio
async def test_renew_after_synchronous_revocation_cannot_resurrect_arm():
    owner, _, _ = runtime()
    await owner.start()
    await owner.request_arm(expires_at=NOW + timedelta(seconds=10))
    owner.revoke_now()
    await owner.renew()
    assert not owner.observed_arm_intent


@pytest.mark.asyncio
async def test_arm_monotonic_deadline_survives_frozen_utc_and_renewal():
    owner, _, clock = runtime()
    await owner.start()
    await owner.request_arm(expires_at=NOW + timedelta(seconds=10))
    clock.mono += 9
    await owner.renew()
    clock.mono += 2
    assert not owner.observed_arm_intent


@pytest.mark.asyncio
async def test_policy_rebind_revokes_before_await_and_does_not_rearm():
    owner, db, _ = runtime()
    await owner.start()
    await owner.request_arm(expires_at=NOW + timedelta(seconds=10))
    db.entered.clear()
    db.pause = asyncio.Event()
    new_pins = replace(PINS, config_sha256="a" * 64)
    pending = asyncio.create_task(owner.rebind(new_pins))
    await db.entered.wait()
    assert not owner.observed_arm_intent
    db.pause.set()
    result = await pending
    assert result.state.pins == new_pins and result.state.arm_request_id is None
    assert not owner.execution_authority


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_token", [OWNER, OWNER.encode(), bytearray(32), b"short", None]
)
async def test_database_binding_does_not_reconstruct_owner_token(invalid_token):
    def no_session():
        raise AssertionError("invalid token must be rejected before database access")

    repo = DemoControlRepository(no_session, clock=lambda: NOW)
    with pytest.raises(c.DemoControlError, match="owner_token_required"):
        await repo.acquire(
            SCOPE, owner_token=invalid_token, pins=PINS, command_id="5" * 64
        )
    with pytest.raises(c.DemoControlError, match="owner_token_required"):
        await repo.change(
            SCOPE,
            owner_token=invalid_token,
            owner_epoch=1,
            expected_revision=1,
            action="renew",
            command_id="5" * 64,
        )


@pytest.mark.asyncio
async def test_equal_state_hash_cannot_hide_cross_session_clock_regression():
    original = state()

    class ReadbackDouble(DemoControlRepository):
        async def read(self, scope):
            return ControlObservation(original, "e" * 64, NOW)

    repo = ReadbackDouble(None, clock=lambda: NOW)
    with pytest.raises(c.DemoControlError, match="readback_conflict"):
        await repo._readback(SCOPE, original, "e" * 64, NOW + timedelta(microseconds=1))
    with pytest.raises(c.DemoControlError, match="commit_clock_regressed"):
        repo._now_after(NOW + timedelta(microseconds=1))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["owner", "scope", "epoch", "revision", "future", "before_invocation"]
)
async def test_runtime_rejects_mismatched_or_stale_readback(case):
    owner, db, clock = runtime()
    await owner.start()
    original_change = db.change

    async def wrong(scope, **kwargs):
        result = await original_change(scope, **kwargs)
        altered = result.state
        observed = result.observed_at
        if case == "owner":
            altered = replace(altered, owner_sha256="f" * 64)
        if case == "scope":
            altered = replace(altered, scope=c.ControlScope("demo", "123456780"))
        if case == "epoch":
            altered = replace(altered, owner_epoch=2)
        if case == "revision":
            altered = replace(altered, revision=3)
        if case == "future":
            observed += timedelta(seconds=1)
        if case == "before_invocation":
            altered = replace(altered, updated_at=NOW)
            observed = NOW
        return ControlObservation(altered, result.event_sha256, observed)

    db.change = wrong
    clock.value += timedelta(seconds=1)
    with pytest.raises(c.DemoControlError, match="uncertain"):
        await owner.renew()
    assert owner.uncertain and not owner.observed_arm_intent


@pytest.mark.asyncio
async def test_queued_arm_before_shutdown_revocation_never_becomes_current():
    owner, db, _ = runtime()
    await owner.start()
    entered, release = asyncio.Event(), asyncio.Event()
    original = db.revoke_arm

    async def delayed(scope, *, command_id):
        entered.set()
        await release.wait()
        return await original(scope, command_id=command_id)

    db.revoke_arm = delayed
    disarm = asyncio.create_task(owner.disarm())
    await entered.wait()
    arm = asyncio.create_task(owner.request_arm(expires_at=NOW + timedelta(seconds=10)))
    await asyncio.sleep(0)
    owner.revoke_now()
    release.set()
    await disarm
    with pytest.raises(c.DemoControlError, match="invocation_revoked"):
        await arm
    assert not owner.observed_arm_intent
    assert db.state.arm_request_id is None and "arm_requested" not in db.calls


@pytest.mark.asyncio
async def test_cold_estop_genesis_keeps_unknown_owner_and_pins_through_restart():
    old, db, clock = runtime()
    result = await old.emergency_stop()
    assert result.state.owner_epoch == 0 and result.state.owner_sha256 is None
    assert result.state.pins is None and result.state.emergency_stop
    assert c.decode(c.canonical(c.document(result.state))) == result.state
    newer = DemoControlOwner(db, SCOPE, PINS, clock=clock, monotonic=clock.monotonic)
    acquired = await newer.start()
    assert acquired.state.owner_epoch == 1 and acquired.state.revision == 2
    assert acquired.state.emergency_stop and acquired.state.arm_request_id is None
    with pytest.raises(c.DemoControlError, match="uncertain"):
        await newer.request_arm(expires_at=NOW + timedelta(seconds=10))
    assert not newer.execution_authority


@pytest.mark.parametrize(
    "field,value",
    [
        ("emergency_stop", False),
        ("pins", PINS),
        ("owner_sha256", OWNER),
        ("lease_until", NOW + timedelta(seconds=1)),
    ],
)
def test_unowned_genesis_cannot_claim_session_owner_or_positive_lease(field, value):
    with pytest.raises(c.DemoControlError, match="unowned_stop_invalid"):
        replace(c.stopped_genesis(SCOPE, NOW), **{field: value})
