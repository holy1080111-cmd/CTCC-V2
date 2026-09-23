"""Versioned Demo control intentions, never an execution permit.

These records deliberately cannot clear an EStop without the future owned
reconciliation producer. Hash-shaped pins bind identity, not source authenticity.
"""

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

LEASE_SECONDS = 30
MAX_ARM_SECONDS = 30


class DemoControlError(ValueError):
    pass


def _text(value, pattern, code):
    if type(value) is not str or re.fullmatch(pattern, value, flags=re.ASCII) is None:
        raise DemoControlError(code)
    return value


def sha(value):
    return _text(value, r"[a-f0-9]{64}", "control_digest_invalid")


def utc(value):
    # Reject foreign tzinfo callbacks before comparing or serializing anything.
    if type(value) is not datetime or value.tzinfo is not UTC:
        raise DemoControlError("control_exact_utc_required")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class ControlScope:
    environment: str
    account_id: str

    def __post_init__(self):
        if type(self.environment) is not str or self.environment != "demo":
            raise DemoControlError("control_demo_only")
        _text(self.account_id, r"[0-9]{1,32}", "control_exact_uid_required")


@dataclass(frozen=True, slots=True, repr=False)
class ControlPins:
    config_sha256: str
    policy_sha256: str
    credential_session_sha256: str

    def __post_init__(self):
        for value in (
            self.config_sha256,
            self.policy_sha256,
            self.credential_session_sha256,
        ):
            sha(value)


def checked(value, kind):
    if type(value) is not kind:
        raise DemoControlError("control_exact_record_required")
    value.__post_init__()
    return value


@dataclass(frozen=True, slots=True, repr=False)
class ControlState:
    scope: ControlScope
    revision: int
    owner_sha256: str | None
    owner_epoch: int
    lease_until: datetime
    pins: ControlPins | None
    emergency_stop: bool
    arm_request_id: str | None
    arm_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self):
        checked(self.scope, ControlScope)
        if type(self.revision) is not int or not 1 <= self.revision < 2**63:
            raise DemoControlError("control_revision_invalid")
        if type(self.owner_epoch) is not int or not 0 <= self.owner_epoch < 2**63:
            raise DemoControlError("control_epoch_invalid")
        if type(self.emergency_stop) is not bool:
            raise DemoControlError("control_stop_invalid")
        for value in (self.lease_until, self.created_at, self.updated_at):
            utc(value)
        if self.owner_epoch == 0:
            if (
                self.owner_sha256 is not None
                or self.pins is not None
                or self.emergency_stop is not True
                or self.arm_request_id is not None
                or self.lease_until != self.updated_at
            ):
                raise DemoControlError("control_unowned_stop_invalid")
        else:
            checked(self.pins, ControlPins)
            sha(self.owner_sha256)
        if self.owner_epoch > self.revision:
            raise DemoControlError("control_epoch_invalid")
        if not self.created_at <= self.updated_at <= self.lease_until:
            raise DemoControlError("control_clock_order_invalid")
        if (self.arm_request_id is None) != (self.arm_expires_at is None):
            raise DemoControlError("control_arm_pair_invalid")
        if self.arm_request_id is not None:
            sha(self.arm_request_id)
            utc(self.arm_expires_at)
            if (
                self.emergency_stop
                or not self.created_at < self.arm_expires_at <= self.lease_until
            ):
                raise DemoControlError("control_arm_state_invalid")

    @property
    def execution_authority(self):
        return False

    def arm_intent_current(self, now):
        checked(self, ControlState)
        utc(now)
        return bool(
            now >= self.updated_at
            and now < self.lease_until
            and not self.emergency_stop
            and self.arm_expires_at is not None
            and now < self.arm_expires_at
        )


def owner_binding(token):
    if type(token) is not bytes or len(token) != 32:
        raise DemoControlError("control_owner_token_required")
    return hashlib.sha256(token).hexdigest()


def account_lock_key(scope):
    checked(scope, ControlScope)
    value = f"ctcc-qualification-account-v1\0{scope.environment}\0{scope.account_id}".encode(
        "ascii"
    )
    return int.from_bytes(hashlib.sha256(value).digest()[:8], "big", signed=True)


def document(state):
    checked(state, ControlState)
    return {
        "version": "ctcc.demo_control.v1",
        "environment": state.scope.environment,
        "account_id": state.scope.account_id,
        "revision": state.revision,
        "owner_sha256": state.owner_sha256,
        "owner_epoch": state.owner_epoch,
        "lease_until": state.lease_until.isoformat(),
        "config_sha256": state.pins.config_sha256 if state.pins else None,
        "policy_sha256": state.pins.policy_sha256 if state.pins else None,
        "credential_session_sha256": state.pins.credential_session_sha256
        if state.pins
        else None,
        "emergency_stop": state.emergency_stop,
        "arm_request_id": state.arm_request_id,
        "arm_expires_at": state.arm_expires_at.isoformat()
        if state.arm_expires_at
        else None,
        "created_at": state.created_at.isoformat(),
        "updated_at": state.updated_at.isoformat(),
    }


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(raw):
    if type(raw) is not str:
        raise DemoControlError("control_exact_json_required")
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def decode(raw):
    if type(raw) is not str or not 1 <= len(raw) <= 8192:
        raise DemoControlError("control_state_bound")
    try:
        value = json.loads(raw)
        state = ControlState(
            scope=ControlScope(value["environment"], value["account_id"]),
            revision=value["revision"],
            owner_sha256=value["owner_sha256"],
            owner_epoch=value["owner_epoch"],
            lease_until=datetime.fromisoformat(value["lease_until"]),
            pins=ControlPins(
                value["config_sha256"],
                value["policy_sha256"],
                value["credential_session_sha256"],
            )
            if value["config_sha256"] is not None
            or value["policy_sha256"] is not None
            or value["credential_session_sha256"] is not None
            else None,
            emergency_stop=value["emergency_stop"],
            arm_request_id=value["arm_request_id"],
            arm_expires_at=datetime.fromisoformat(value["arm_expires_at"])
            if value["arm_expires_at"] is not None
            else None,
            created_at=datetime.fromisoformat(value["created_at"]),
            updated_at=datetime.fromisoformat(value["updated_at"]),
        )
        if canonical(document(state)) != raw:
            raise DemoControlError("control_state_noncanonical")
        return state
    except (KeyError, TypeError, ValueError, OverflowError):
        raise DemoControlError("control_state_invalid") from None


def stopped_genesis(scope, now):
    """Durable stop before any owner exists; identities remain explicitly unknown."""
    return ControlState(
        scope=checked(scope, ControlScope),
        revision=1,
        owner_sha256=None,
        owner_epoch=0,
        lease_until=utc(now),
        pins=None,
        emergency_stop=True,
        arm_request_id=None,
        arm_expires_at=None,
        created_at=now,
        updated_at=now,
    )


def acquire(previous, *, scope, owner_sha256, pins, now):
    checked(scope, ControlScope)
    checked(pins, ControlPins)
    sha(owner_sha256)
    utc(now)
    if previous is not None:
        checked(previous, ControlState)
        if previous.scope != scope or now < previous.updated_at:
            raise DemoControlError("control_identity_or_clock_conflict")
        if previous.lease_until > now or previous.owner_sha256 == owner_sha256:
            raise DemoControlError("control_owner_conflict")
    return ControlState(
        scope=scope,
        revision=previous.revision + 1 if previous else 1,
        owner_sha256=owner_sha256,
        owner_epoch=previous.owner_epoch + 1 if previous else 1,
        lease_until=now + timedelta(seconds=LEASE_SECONDS),
        pins=pins,
        emergency_stop=previous.emergency_stop if previous else False,
        arm_request_id=None,
        arm_expires_at=None,
        created_at=previous.created_at if previous else now,
        updated_at=now,
    )


def advance(
    previous,
    *,
    owner_sha256,
    owner_epoch,
    expected_revision,
    action,
    command_id,
    now,
    arm_expires_at=None,
    pins=None,
):
    checked(previous, ControlState)
    sha(owner_sha256)
    sha(command_id)
    utc(now)
    if (
        type(owner_epoch) is not int
        or type(expected_revision) is not int
        or (previous.owner_sha256, previous.owner_epoch, previous.revision)
        != (owner_sha256, owner_epoch, expected_revision)
    ):
        raise DemoControlError("control_owner_or_revision_conflict")
    if not previous.updated_at <= now < previous.lease_until:
        raise DemoControlError("control_lease_or_clock_invalid")
    if type(action) is not str or action not in {
        "renew",
        "arm_requested",
        "disarm",
        "estop",
        "rebind",
        "release",
    }:
        raise DemoControlError("control_action_not_supported")
    if (pins is not None) != (action == "rebind") or (arm_expires_at is not None) != (
        action == "arm_requested"
    ):
        raise DemoControlError("control_action_fields_conflict")
    changes = {"revision": previous.revision + 1, "updated_at": now}
    if previous.arm_expires_at is not None and now >= previous.arm_expires_at:
        changes.update(arm_request_id=None, arm_expires_at=None)
    if action == "renew":
        changes["lease_until"] = now + timedelta(seconds=LEASE_SECONDS)
    elif action == "arm_requested":
        utc(arm_expires_at)
        if previous.emergency_stop:
            raise DemoControlError("control_estop_latched")
        if previous.arm_intent_current(now):
            raise DemoControlError("control_arm_already_current")
        if (
            not now
            < arm_expires_at
            <= min(previous.lease_until, now + timedelta(seconds=MAX_ARM_SECONDS))
        ):
            raise DemoControlError("control_arm_expiry_invalid")
        changes.update(arm_request_id=command_id, arm_expires_at=arm_expires_at)
    else:
        changes.update(arm_request_id=None, arm_expires_at=None)
        if action == "estop":
            changes["emergency_stop"] = True
        elif action == "rebind":
            changes["pins"] = checked(pins, ControlPins)
        elif action == "release":
            changes["lease_until"] = now
    return replace(previous, **changes)
