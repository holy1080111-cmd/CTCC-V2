"""Public-source owner around the shared native host-clock adapter.

Account-specific issuers live with the account proof runtime.
"""

from __future__ import annotations

from threading import Lock
from weakref import WeakKeyDictionary

import app.domain.native_clock as _native_clock
from app.domain.source_primitives import PublicReceiptError, canonical

CLOCK_RESULT_VERSION = _native_clock.CLOCK_RESULT_VERSION
MAX_CLOCK_PAYLOAD = _native_clock.MAX_CLOCK_PAYLOAD
PROFILE_ID = _native_clock.PROFILE_ID
RESOURCE_IDS = _native_clock.RESOURCE_IDS
NativeClockObservationError = _native_clock.NativeClockObservationError
_native_observation_payload = _native_clock._native_observation_payload
_native_observation_payload_ipc = _native_clock._native_observation_payload_ipc
clock_timezone = _native_clock.clock_timezone
native_os_clock = _native_clock.native_os_clock
native_stamp = _native_clock.native_stamp
replay_clock_observation = _native_clock.replay_clock_observation
validate_os_clock = _native_clock.validate_os_clock

_CLOCK_ISSUER = object()
_CLOCK_RESULTS = WeakKeyDictionary()
_CLOCK_RESULTS_LOCK = Lock()
_RUNTIME_CLOCK_V2 = "ctcc.windows_clock_observation.v2"
_RUNTIME_CLOCK_IPC = "ctcc.windows_clock_observation.v3"


def _runtime_clock_pair_matches(first, last):
    """Compare two independently replayed component-clock observations."""
    version = first["schema_version"]
    if version != last["schema_version"]:
        return False
    if version == _RUNTIME_CLOCK_V2:
        return True  # Historical v2 pairs keep their original validation.
    if version != _RUNTIME_CLOCK_IPC:
        return False
    left, right = first["diagnostic"], last["diagnostic"]
    return (
        left["profile_id"] == right["profile_id"]
        and canonical(left["clock_domain"]) == canonical(right["clock_domain"])
        and canonical(_native_clock._ipc_host_payload(left["host_after"], "after"))
        == canonical(_native_clock._ipc_host_payload(right["host_before"], "before"))
    )


class _OwnedClockObservation:
    # Payload and scope live outside the carrier. Ordinary object copying or
    # deserialization cannot mint an identity registered by the native adapter.
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _CLOCK_ISSUER:
            raise PublicReceiptError("owned_clock_observation_required")

    def __copy__(self):
        raise PublicReceiptError("owned_clock_observation_not_transferable")

    def __deepcopy__(self, memo):
        raise PublicReceiptError("owned_clock_observation_not_transferable")

    def __reduce_ex__(self, protocol):
        raise PublicReceiptError("owned_clock_observation_not_transferable")

    def __reduce__(self):
        raise PublicReceiptError("owned_clock_observation_not_transferable")


def _owned_clock_payload(value, attempt, stage):
    if type(value) is not _OwnedClockObservation:
        raise PublicReceiptError("owned_clock_observation_required")
    with _CLOCK_RESULTS_LOCK:
        entry = _CLOCK_RESULTS.pop(value, None)
    if entry is None or entry[0] is not attempt or entry[1] != stage:
        raise PublicReceiptError("owned_clock_observation_required")
    payload = entry[2]
    replay_clock_observation(payload)
    return payload


def _observe_owned_clock(attempt, stage):
    """No supplied exception, diagnostic, clock callback or path is accepted."""
    from app.public_market_source.public_attempt_journal import _OwnedAttempt

    if type(attempt) is not _OwnedAttempt or type(stage) is not str:
        raise PublicReceiptError("owned_clock_observation_required")
    with _CLOCK_RESULTS_LOCK:
        attempt.claim_clock_stage(stage)
    payload = _native_observation_payload_ipc()
    result = _OwnedClockObservation(_CLOCK_ISSUER)
    with _CLOCK_RESULTS_LOCK:
        _CLOCK_RESULTS[result] = (attempt, stage, payload)
    return result


def _observe_owned_runtime_clock(attempt, stage):
    """Separate exact component-journal scope; no supplied diagnostic or clock."""
    from app.public_market_source.public_runtime_journal import _claim_runtime_clock

    _claim_runtime_clock(attempt, stage)
    payload = _native_observation_payload_ipc()
    result = _OwnedClockObservation(_CLOCK_ISSUER)
    with _CLOCK_RESULTS_LOCK:
        _CLOCK_RESULTS[result] = (attempt, stage, payload)
    return result
