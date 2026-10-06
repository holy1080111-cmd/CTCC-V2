"""Private initial account time witnesses; portable evidence cannot issue them."""

import asyncio
import os
import uuid
from contextlib import contextmanager
from threading import get_ident
from types import CoroutineType
from weakref import WeakKeyDictionary

from app.domain import native_clock as clock
from app.domain.source_primitives import (
    canonical,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.trade_qualification.account_capture_journal import _OwnedAccountJournal

_ISSUER = object()
_STAGES = WeakKeyDictionary()
_OBSERVERS = WeakKeyDictionary()
_SESSION_CLAIMS = {}
_B1_FINALIZER_CODES = {
    "source": _OwnedAccountJournal.close_acquisition.__code__,
    "journal": _OwnedAccountJournal.finish.__code__,
}
PAGE_PHASES = (
    "request_start",
    "request_dispatch",
    "headers_received",
    "body_exhausted",
    "response_closed",
)


class NativeAccountClockError(ValueError):
    """Fixed rejection codes; never interpolate source/OS/credential exceptions."""


class _InitialAccountStage:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise NativeAccountClockError("native_account_stage_not_constructible")

    def __copy__(self):
        raise NativeAccountClockError("native_account_stage_not_transferable")

    def __deepcopy__(self, memo):
        raise NativeAccountClockError("native_account_stage_not_transferable")

    def __reduce_ex__(self, protocol):
        raise NativeAccountClockError("native_account_stage_not_transferable")


class _PhaseObserver(_InitialAccountStage):
    __slots__ = ()


def _state(stage, *, _clock_dependency=False, _durable_cleanup=False):
    if type(stage) is not _InitialAccountStage:
        raise NativeAccountClockError("native_account_stage_required")
    value = _STAGES.get(stage)
    task = asyncio.current_task()
    if (
        value is None
        or (
            value["parent"] is not task
            and (not _clock_dependency or task not in value["finalizers"])
        )
        or task is None
        or (task.cancelling() and not _durable_cleanup)
        or value["loop"] is not asyncio.get_running_loop()
        or value["pid"] != os.getpid()
        or value["thread"] != get_ident()
    ):
        raise NativeAccountClockError("native_account_stage_context_invalid")
    return value


def _sample(stage, *, _clock_dependency=False):
    value = _state(stage, _clock_dependency=_clock_dependency)
    stamp = clock.native_stamp()
    validate_stamps((value["started"], value["last"], stamp))
    value["last"] = stamp
    return stamp


def _session_claim(session):
    from app.trade_qualification.account_runtime import ControlledDemoAccountSession

    if type(session) is not ControlledDemoAccountSession:
        raise NativeAccountClockError("native_account_session_claim_invalid")
    value = _SESSION_CLAIMS.get(session)
    task = asyncio.current_task()
    if (
        value is None
        or task is None
        or task.cancelling()
        or value["parent"] is not task
        or value["loop"] is not asyncio.get_running_loop()
        or value["pid"] != os.getpid()
        or value["thread"] != get_ident()
        or session._used is not True
        or session._plan is not value["plan"]
        or type(session._pin) is not str
        or session._pin != value["plan_sha256"]
        or session._credentials is not value["credentials"]
    ):
        raise NativeAccountClockError("native_account_session_claim_invalid")
    return value


@contextmanager
def _claim_initial_session(session):
    """Burn once before native sampling or await; no transferable claim exists."""
    from app.trade_qualification.account_runtime import ControlledDemoAccountSession

    task = asyncio.current_task()
    if (
        type(session) is not ControlledDemoAccountSession
        or session._used is not False
        or session in _SESSION_CLAIMS
        or task is None
        or task.cancelling()
    ):
        raise NativeAccountClockError("native_account_session_claim_invalid")
    value = {
        "parent": task,
        "loop": asyncio.get_running_loop(),
        "pid": os.getpid(),
        "thread": get_ident(),
        "plan": session._plan,
        "plan_sha256": session._pin,
        "credentials": session._credentials,
        "bootstrap_used": False,
    }
    session._used = True
    _SESSION_CLAIMS[session] = value
    try:
        yield
    finally:
        _SESSION_CLAIMS.pop(session, None)


def _claim_collector_session(observer, session):
    """Adopt only the initial issuer's exact session once in its parent task."""
    if type(observer) is not _PhaseObserver:
        raise NativeAccountClockError("native_account_session_claim_invalid")
    value = _state(_OBSERVERS.get(observer))
    claim = value.get("session_claim")
    if claim is None or session is not value["session"] or claim["bootstrap_used"]:
        raise NativeAccountClockError("native_account_session_claim_invalid")
    claim["bootstrap_used"] = True
    if _session_claim(session) is not claim:
        raise NativeAccountClockError("native_account_session_claim_invalid")


@contextmanager
def _initial_stage(*, plan_sha256, scope_sha256, _claimed_session=None):
    """Internal issuer. No caller callback, boundary stamp or proof is accepted."""
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise NativeAccountClockError("native_account_stage_context_invalid")
    claim = None
    if _claimed_session is not None:
        claim = _session_claim(_claimed_session)
        if claim["plan_sha256"] != plan_sha256:
            raise NativeAccountClockError("native_account_session_claim_invalid")
    started = clock.native_stamp()
    stage = object.__new__(_InitialAccountStage)
    observer = object.__new__(_PhaseObserver)
    _STAGES[stage] = {
        "invocation": object(),
        "invocation_id": uuid.uuid4().hex,
        "parent": task,
        "loop": asyncio.get_running_loop(),
        "pid": os.getpid(),
        "thread": get_ident(),
        "started": started,
        "last": started,
        "plan_sha256": plan_sha256,
        "scope_sha256": scope_sha256,
        "clock_claims": set(),
        "clock_results": {},
        "witnesses": [],
        "closed": False,
        "bound": False,
        "finalizers": set(),
        "finalization_witnesses": [],
        "observer": observer,
        "session_claim": claim,
        "session": _claimed_session,
    }

    # Exact private function identity is checked by the source adapter. Its
    # samples serve B1's existing clock dependency without claiming old evidence.
    def owned_utc():
        return utc_from_ns(_sample(stage, _clock_dependency=True)["utc_ns"])

    _STAGES[stage]["clock"] = owned_utc
    _OBSERVERS[observer] = stage
    try:
        yield stage
    finally:
        _OBSERVERS.pop(observer, None)
        _STAGES.pop(stage, None)


def _finalization_awaitable(observer, source_journal, awaitable, *, purpose="journal"):
    """Register just the original B1 bounded child for this one finalization."""
    try:
        if type(observer) is not _PhaseObserver:
            raise NativeAccountClockError("native_account_observer_required")
        stage = _OBSERVERS.get(observer)
        # Preparing existing durable cleanup during cancellation grants only
        # this bounded child's UTC dependency. Source/issuer checks stay strict.
        value = _state(stage, _durable_cleanup=True)
        if (
            value.get("journal") is not source_journal
            or type(source_journal) is not _OwnedAccountJournal
            or type(purpose) is not str
            or purpose not in {"source", "journal"}
            or type(awaitable) is not CoroutineType
            or awaitable.cr_code is not _B1_FINALIZER_CODES[purpose]
            or awaitable.cr_frame is None
            or awaitable.cr_frame.f_locals.get("self") is not source_journal
        ):
            raise NativeAccountClockError("native_account_finalizer_binding_invalid")
    except BaseException:
        if type(awaitable) is CoroutineType:
            awaitable.close()
        raise
    finalizer_id = uuid.uuid4().hex

    async def finalize():
        task = asyncio.current_task()
        if (
            task is None
            or task is value["parent"]
            or task.cancelling()
            or asyncio.get_running_loop() is not value["loop"]
            or os.getpid() != value["pid"]
            or get_ident() != value["thread"]
            or stage not in _STAGES
            or value["finalizers"]
        ):
            awaitable.close()
            raise NativeAccountClockError("native_account_finalizer_binding_invalid")
        value["finalizers"].add(task)
        try:
            begin = _sample(stage, _clock_dependency=True)
            result = await awaitable
            end = _sample(stage, _clock_dependency=True)
            value["finalization_witnesses"].append(
                {
                    "kind": f"original_b1_bounded_{purpose}_finalization",
                    "finalizer_id": finalizer_id,
                    "invocation_binding_sha256": sha(
                        canonical([value["invocation_id"], finalizer_id])
                    ),
                    "started": begin,
                    "completed": end,
                }
            )
            return result
        finally:
            value["finalizers"].discard(task)

    return finalize()


def _claim_account_clock(stage, phase):
    value = _state(stage)
    if type(phase) is not str or phase not in {"before", "after"}:
        raise NativeAccountClockError("native_account_clock_stage_invalid")
    if phase in value["clock_claims"] or (
        phase == "after"
        and (not value["closed"] or value["clock_results"].get("before") != "accepted")
    ):
        raise NativeAccountClockError("native_account_clock_stage_invalid")
    value["clock_claims"].add(phase)


def _host_observation(stage, phase):
    value = _state(stage)
    _claim_account_clock(stage, phase)
    raw = clock._native_observation_payload()
    replay = clock.replay_clock_observation(raw)
    value["clock_results"][phase] = replay["outcome"]
    if replay["outcome"] != "accepted":
        # Return the original negative bytes for durable companion retention.
        # Binding/source admission still requires the exact accepted result.
        return raw
    sample = replay["observation"]["sample"]
    try:
        validate_stamps((value["started"], value["last"], sample))
    except Exception:  # noqa: BLE001 -- original raw observation still survives
        value["clock_results"][phase] = "unadmitted_clock_sequence"
        return raw
    value["last"] = sample
    return raw


def _bind_collector(observer, owned_clock, plan_sha256, source_journal):
    if type(observer) is not _PhaseObserver:
        raise NativeAccountClockError("native_account_observer_required")
    stage = _OBSERVERS.get(observer)
    value = _state(stage)
    if (
        value["bound"]
        or value["clock"] is not owned_clock
        or type(plan_sha256) is not str
        or value["plan_sha256"] != plan_sha256
        or type(source_journal) is not _OwnedAccountJournal
        or source_journal.closed
        or value["clock_results"] != {"before": "accepted"}
    ):
        raise NativeAccountClockError("native_account_observer_binding_invalid")
    value["bound"] = True
    value["journal"] = source_journal


def _phase(observer, owned_clock, *, phase, request_index, stream, page_index):
    """Called explicitly at the source phase, never inferred from clock calls."""
    if type(observer) is not _PhaseObserver:
        raise NativeAccountClockError("native_account_observer_required")
    value = _state(_OBSERVERS.get(observer))
    witnesses = value["witnesses"]
    if (
        not value["bound"]
        or value["closed"]
        or value["clock"] is not owned_clock
        or type(request_index) is not int
        or type(page_index) is not int
        or type(stream) is not str
        or type(phase) is not str
    ):
        raise NativeAccountClockError("native_account_phase_invalid")
    if phase == "source_closed":
        if (
            not witnesses
            or witnesses[-1]["phase"] != "response_closed"
            or request_index != len(witnesses) // len(PAGE_PHASES)
            or stream != "account_source"
            or page_index != 0
        ):
            raise NativeAccountClockError("native_account_phase_inventory_invalid")
    else:
        expected_index, expected_phase = divmod(len(witnesses), len(PAGE_PHASES))
        if request_index != expected_index or phase != PAGE_PHASES[expected_phase]:
            raise NativeAccountClockError("native_account_phase_inventory_invalid")
        if expected_phase and (
            stream != witnesses[-1]["stream"]
            or page_index != witnesses[-1]["page_index"]
        ):
            raise NativeAccountClockError("native_account_phase_inventory_invalid")
    stamp = _sample(_OBSERVERS[observer])
    observed = utc_from_ns(stamp["utc_ns"])
    record = {
        "index": len(witnesses),
        "phase": phase,
        "request_index": request_index,
        "stream": stream,
        "page_index": page_index,
        "stamp": stamp,
        "source_utc": observed.isoformat(),
        "previous_sha256": None if not witnesses else sha(canonical(witnesses[-1])),
    }
    witnesses.append(record)
    if phase == "source_closed":
        value["closed"] = True
    return observed
