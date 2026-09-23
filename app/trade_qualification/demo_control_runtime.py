"""One process/session's Demo control owner; default denied, no transport permit.

The local revocation generation closes cancellation and queued-operation races.
Cross-process safety remains DB fencing plus the future owned transport; this
module never asserts a cache is an instantaneous distributed stop observation.
"""

import asyncio
import hashlib
import math
import secrets
import threading
import time
from datetime import UTC, datetime

from app.database.repositories.demo_control import ControlObservation
from app.trade_qualification.demo_control import (
    ControlPins,
    ControlScope,
    DemoControlError,
    checked,
    utc,
)


class DemoControlOwner:
    def __init__(
        self, repository, scope, pins, *, clock=None, monotonic=time.monotonic
    ):
        self.repository = repository
        self.scope = checked(scope, ControlScope)
        self.pins = checked(pins, ControlPins)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.monotonic = monotonic
        self._owner_token = secrets.token_bytes(32)
        self._owner_sha256 = hashlib.sha256(self._owner_token).hexdigest()
        self._mutex = asyncio.Lock()
        self._local = threading.RLock()
        self._generation = 0
        self._started = False
        self._poisoned = False
        self._local_estop = False
        self._arm_intent = False
        self._observation = None
        self._last_utc = self._last_monotonic = self._lease_monotonic_deadline = None
        self._arm_monotonic_deadline = None

    @property
    def owner_sha256(self):
        return self._owner_sha256

    @property
    def execution_authority(self):
        return False

    @property
    def uncertain(self):
        with self._local:
            return self._poisoned

    def _revoke(self, *, stop=False):
        with self._local:
            self._generation += 1
            self._arm_intent = False
            self._local_estop = self._local_estop or stop

    def revoke_now(self):
        """Synchronous local revocation, suitable before awaiting shutdown IO."""
        self._revoke()

    def _time(self):
        now = utc(self.clock())
        mono = self.monotonic()
        if type(mono) not in (int, float) or not math.isfinite(mono):
            raise DemoControlError("control_monotonic_invalid")
        if (self._last_utc is not None and now < self._last_utc) or (
            self._last_monotonic is not None and mono < self._last_monotonic
        ):
            raise DemoControlError("control_runtime_clock_regressed")
        self._last_utc, self._last_monotonic = now, mono
        return now, mono

    @property
    def observed_arm_intent(self):
        """Current local intention display only; never an execution safety check."""
        with self._local:
            try:
                now, mono = self._time()
                return bool(
                    not self._poisoned
                    and not self._local_estop
                    and self._arm_intent
                    and self._observation is not None
                    and self._lease_monotonic_deadline is not None
                    and self._arm_monotonic_deadline is not None
                    and mono < self._lease_monotonic_deadline
                    and mono < self._arm_monotonic_deadline
                    and self._observation.state.arm_intent_current(now)
                )
            except Exception:  # noqa: BLE001 -- Clock faults revoke local intention.
                self._poisoned = True
                self._arm_intent = False
                return False

    async def _perform(self, action, *, arm_expires_at=None, pins=None):
        # Capture before the mutex await: a queued Arm must not adopt a newer
        # generation after disarm/shutdown revoked the invocation while waiting.
        with self._local:
            queued_generation = self._generation
        async with self._mutex:
            with self._local:
                if queued_generation != self._generation:
                    raise DemoControlError("control_invocation_revoked")
                if self._poisoned or (action == "acquire") == self._started:
                    raise DemoControlError("control_owner_not_available")
                generation = self._generation
                prior_arm_intent = self._arm_intent
                self._arm_intent = False
                if action == "acquire":
                    self._started = True
            try:
                with self._local:
                    before_utc, before_mono = self._time()
                    previous = self._observation
                command_id = secrets.token_hex(32)
                if action == "acquire":
                    result = await self.repository.acquire(
                        self.scope,
                        owner_token=self._owner_token,
                        pins=self.pins,
                        command_id=command_id,
                    )
                else:
                    if previous is None:
                        raise DemoControlError("control_owner_missing_observation")
                    result = await self.repository.change(
                        self.scope,
                        owner_token=self._owner_token,
                        owner_epoch=previous.state.owner_epoch,
                        expected_revision=previous.state.revision,
                        action=action,
                        command_id=command_id,
                        arm_expires_at=arm_expires_at,
                        pins=pins,
                    )
                with self._local:
                    if type(result) is not ControlObservation:
                        raise DemoControlError("control_observation_type_invalid")
                    result.__post_init__()
                    now, mono = self._time()
                    if (
                        generation != self._generation
                        or result.state.scope != self.scope
                        or result.state.owner_sha256 != self.owner_sha256
                        or result.state.pins
                        != (pins if action == "rebind" else self.pins)
                        or result.observed_at > now
                        or result.observed_at < before_utc
                        or result.state.updated_at < before_utc
                        or (
                            previous is not None
                            and (
                                result.state.owner_epoch != previous.state.owner_epoch
                                or result.state.revision != previous.state.revision + 1
                            )
                        )
                    ):
                        raise DemoControlError("control_runtime_readback_conflict")
                    deadline = min(
                        before_mono + 30,
                        mono + (result.state.lease_until - now).total_seconds(),
                    )
                    if action != "release" and mono >= deadline:
                        raise DemoControlError("control_runtime_lease_expired")
                    self._lease_monotonic_deadline = deadline
                    if action == "arm_requested":
                        self._arm_monotonic_deadline = min(
                            deadline,
                            before_mono
                            + (result.state.arm_expires_at - now).total_seconds(),
                        )
                    self._observation = result
                    self._local_estop = self._local_estop or result.state.emergency_stop
                    if action == "rebind":
                        self.pins = pins
                    self._arm_intent = (
                        (
                            action == "arm_requested"
                            or (action == "renew" and prior_arm_intent)
                        )
                        and not self._local_estop
                        and result.state.arm_intent_current(now)
                    )
                    if action == "release":
                        self._poisoned = True
                    return result
            except BaseException as error:
                with self._local:
                    self._poisoned = True
                    self._arm_intent = False
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise DemoControlError("control_owner_uncertain") from None

    async def start(self):
        return await self._perform("acquire")

    async def renew(self):
        return await self._perform("renew")

    async def request_arm(self, *, expires_at):
        return await self._perform("arm_requested", arm_expires_at=expires_at)

    async def disarm(self):
        self._revoke()
        return await self._tighten(stop=False)

    async def rebind(self, pins):
        self._revoke()
        return await self._perform("rebind", pins=checked(pins, ControlPins))

    async def release(self):
        self._revoke()
        return await self._perform("release")

    async def emergency_stop(self):
        self._revoke(stop=True)
        return await self._tighten(stop=True)

    async def _tighten(self, *, stop):
        # Persist even if an interrupted operation has already poisoned this
        # owner. Safety tightening is not a renewal, takeover or permission.
        async with self._mutex:
            try:
                method = (
                    self.repository.latch_stop if stop else self.repository.revoke_arm
                )
                result = await method(self.scope, command_id=secrets.token_hex(32))
                if type(result) is not ControlObservation:
                    raise DemoControlError("control_observation_type_invalid")
                result.__post_init__()
                if (
                    result.state.scope != self.scope
                    or result.state.arm_request_id is not None
                    or (stop and not result.state.emergency_stop)
                ):
                    raise DemoControlError("control_stop_readback_conflict")
                with self._local:
                    if result.state.owner_sha256 == self.owner_sha256:
                        self._observation = result
                return result
            except BaseException as error:
                with self._local:
                    self._poisoned = True
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise DemoControlError("control_stop_persistence_uncertain") from None

    async def clear_stop(self):
        self._revoke(stop=True)
        raise DemoControlError("control_trusted_reconciliation_producer_unavailable")
