"""D0: process-local invocation lineage, with production dispatch unavailable.

The existing journal is evidence, never a transferable capability. This module
can retain one invocation's original request and compare its committed v2 intent
with independent repository readback. It cannot issue READY: the source-owned
account/public producers and the guarded first-byte transport do not exist yet.
No constructor, digest, diagnostic observation or Arm display grants authority.
"""

import asyncio
import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from app.database.repositories.qualification_ledger import (
    QualificationLedgerRepository,
)
from app.trade_qualification.reservations import (
    LedgerScope,
    ReservationRequest,
    checked,
    checked_bootstrap,
    digest,
)
from app.trade_qualification.submission_intent import (
    VERSION_V2,
    SubmissionIntentRecord,
    replay_submission_intent,
)
from app.trade_qualification.submission_intent import (
    _guard as _guard_intent_input,
)

MAX_OWNER_INVOCATIONS = 64
_TERMINAL = frozenset(("cancelled", "revoked", "denied"))
_PUBLIC_CODES = frozenset(
    (
        "dispatch_clock_invalid",
        "dispatch_event_already_owned",
        "dispatch_event_loop_changed",
        "dispatch_exact_repository_required",
        "dispatch_intent_binding_failed",
        "dispatch_intent_invalid",
        "dispatch_intent_not_same_invocation",
        "dispatch_invocation_cancelled",
        "dispatch_owned_task_required",
        "dispatch_owner_capacity_reached",
        "dispatch_owner_revoked",
        "dispatch_ownership_not_transferable",
        "dispatch_process_context_changed",
        "dispatch_readback_failed",
        "dispatch_readback_mismatch",
        "dispatch_readback_uncertain_or_revoked",
        "dispatch_request_invalid",
        "dispatch_scope_invalid",
        "dispatch_scope_mismatch",
        "dispatch_slot_expired",
        "dispatch_slot_not_owned",
        "dispatch_slot_owned_creation_required",
        "dispatch_slot_subclass_denied",
        "dispatch_slot_terminal_or_replayed",
        "dispatch_task_creation_failed",
        "dispatch_trusted_producer_unavailable",
    )
)


class DispatchOwnershipError(ValueError):
    """Fixed rejection code; never includes request, account or DB error text."""


def _public_code(error, fallback):
    # The same exception class may also come from a dependency. Do not grant
    # its arbitrary message the right to cross this error boundary.
    if type(error) is DispatchOwnershipError:
        args = error.args
        if (
            type(args) is tuple
            and len(args) == 1
            and type(args[0]) is str
            and args[0] in _PUBLIC_CODES
        ):
            return args[0]
    return fallback


class _NotTransferable:
    __slots__ = ()

    def __copy__(self):
        raise DispatchOwnershipError("dispatch_ownership_not_transferable")

    def __deepcopy__(self, memo):
        raise DispatchOwnershipError("dispatch_ownership_not_transferable")

    def __reduce_ex__(self, protocol):
        raise DispatchOwnershipError("dispatch_ownership_not_transferable")


class DispatchSlot(_NotTransferable):
    """Opaque identity only. Even a forged instance is absent from the registry."""

    __slots__ = ()

    def __new__(cls):
        raise DispatchOwnershipError("dispatch_slot_owned_creation_required")

    def __init_subclass__(cls, **kwargs):
        raise DispatchOwnershipError("dispatch_slot_subclass_denied")


@dataclass(frozen=True, slots=True)
class DispatchObservation:
    """Diagnostic output only; no API consumes this object as authority."""

    state: str
    reason: str
    intent_sha256: str | None
    execution_authority: Literal[False] = field(default=False, init=False)
    order_retry_authority: Literal[False] = field(default=False, init=False)


@dataclass(slots=True, repr=False)
class _Entry:
    slot: DispatchSlot
    request: ReservationRequest
    request_sha256: str
    opened_at: datetime
    opened_monotonic: float
    generation: int
    state: str = "opened"
    reason: str = "dispatch_trusted_producer_unavailable"
    intent: SubmissionIntentRecord | None = None
    task: asyncio.Task | None = None


class DispatchOwnership(_NotTransferable):
    """One account's bounded, process-local diagnostic invocation registry.

    begin() must precede durable consumption. bind_intent() accepts that same
    invocation's committed v2 record and independently reads it back. Neither
    operation proves source authenticity or grants a permit. require_ready()
    always terminates the slot with DENY; there is deliberately no issuer hook.

    Repository and clocks are application dependencies, not trust certificates.
    Python process compromise/reflection is outside this ownership boundary.
    """

    def __init__(self, repository, scope, *, clock=None, monotonic=time.monotonic):
        if type(repository) is not QualificationLedgerRepository:
            raise DispatchOwnershipError("dispatch_exact_repository_required")
        try:
            self._scope = checked_bootstrap(scope, LedgerScope)
        except Exception:  # noqa: BLE001 -- Invalid foreign input must deny locally.
            raise DispatchOwnershipError("dispatch_scope_invalid") from None
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._pid = os.getpid()
        self._thread = threading.get_ident()
        self._loop = None
        self._local = threading.RLock()
        self._mutex = asyncio.Lock()
        self._registry = {}
        self._events = set()
        self._generation = 0
        self._closed = False
        self._last_time = self._last_monotonic = None

    @property
    def execution_authority(self):
        return False

    @property
    def order_retry_authority(self):
        return False

    def _invalidate(self, reason):
        self._generation += 1
        self._closed = True
        for entry in self._registry.values():
            if entry.state not in _TERMINAL:
                entry.state, entry.reason = "revoked", reason

    def _context(self):
        if os.getpid() != self._pid or threading.get_ident() != self._thread:
            self._invalidate("dispatch_process_context_changed")
            raise DispatchOwnershipError("dispatch_process_context_changed")

    def _active(self, generation=None):
        self._context()
        if self._closed or (generation is not None and generation != self._generation):
            raise DispatchOwnershipError("dispatch_owner_revoked")

    def _time(self):
        try:
            now, mono = self._clock(), self._monotonic()
            if (
                type(now) is not datetime
                or now.tzinfo is not UTC
                or type(mono) not in (float, int)
                or not math.isfinite(mono)
                or self._last_time is not None
                and now < self._last_time
                or self._last_monotonic is not None
                and mono < self._last_monotonic
            ):
                raise ValueError
        except Exception:  # noqa: BLE001 -- A broken clock dependency revokes ownership.
            self._invalidate("dispatch_clock_invalid")
            raise DispatchOwnershipError("dispatch_clock_invalid") from None
        self._last_time, self._last_monotonic = now, mono
        return now, mono

    def _entry(self, slot):
        # Do not hash, compare or access properties of a foreign object.
        entry = self._registry.get(id(slot))
        if type(slot) is not DispatchSlot or entry is None or entry.slot is not slot:
            raise DispatchOwnershipError("dispatch_slot_not_owned")
        return entry

    @staticmethod
    def _finish(entry, state, reason):
        if entry.state not in _TERMINAL:
            entry.state, entry.reason = state, reason

    def _current(self, entry, generation, state):
        self._active(generation)
        if entry.generation != generation or entry.state != state:
            raise DispatchOwnershipError("dispatch_slot_terminal_or_replayed")

    def _cancellation(self, entry):
        task = asyncio.current_task()
        if task is None:
            raise DispatchOwnershipError("dispatch_owned_task_required")
        if task.cancelling():
            self._finish(entry, "cancelled", "dispatch_invocation_cancelled")
            raise asyncio.CancelledError

    @staticmethod
    def _deadline(entry, now, mono):
        deadline = entry.request.origin.deadline
        if (
            now >= deadline
            or mono - entry.opened_monotonic
            >= (deadline - entry.opened_at).total_seconds()
        ):
            raise DispatchOwnershipError("dispatch_slot_expired")

    def begin(self, request):
        """Freeze original replay inputs; claims remain unauthenticated/denied."""
        with self._local:
            self._active()
            generation = self._generation
            now, mono = self._time()
            try:
                # Reuse intent replay's bounded exact-type guard before invoking
                # Pydantic serializers. A private copy prevents caller mutation.
                _guard_intent_input(request)
                frozen = checked(request, ReservationRequest)
                request_sha256 = digest(frozen)
            except Exception:  # noqa: BLE001 -- Never echo supplied request contents.
                raise DispatchOwnershipError("dispatch_request_invalid") from None
            self._active(generation)
            if frozen.scope != self._scope:
                raise DispatchOwnershipError("dispatch_scope_mismatch")
            if now >= frozen.origin.deadline:
                raise DispatchOwnershipError("dispatch_slot_expired")
            event = frozen.origin.original_event_key
            if event in self._events:
                raise DispatchOwnershipError("dispatch_event_already_owned")
            if len(self._registry) >= MAX_OWNER_INVOCATIONS:
                raise DispatchOwnershipError("dispatch_owner_capacity_reached")
            slot = object.__new__(DispatchSlot)
            self._registry[id(slot)] = _Entry(
                slot, frozen, request_sha256, now, mono, generation
            )
            # Keep tombstones for this owner's entire lifetime, including failed
            # or cancelled invocations. Renaming a report cannot free an event.
            self._events.add(event)
            return slot

    @staticmethod
    def _record_snapshot(record):
        if (
            type(record) is not SubmissionIntentRecord
            or type(record.canonical_json) is not str
            or type(record.sha256) is not str
            or record.execution_authority is not False
            or record.order_retry_authority is not False
        ):
            raise DispatchOwnershipError("dispatch_intent_invalid")
        return SubmissionIntentRecord(record.canonical_json, record.sha256)

    @classmethod
    def _record(cls, record, entry):
        record = cls._record_snapshot(record)
        try:
            rebuilt, consumed = replay_submission_intent(
                record.canonical_json,
                entry.request,
                expected_sha256=record.sha256,
            )
            # Replay above compares the *entire* canonical representation. This
            # separate marker rejects legacy v1, even when its digest is valid.
            if json.loads(rebuilt.canonical_json)["version"] != VERSION_V2:
                raise ValueError
            if consumed.request_sha256 != entry.request_sha256:
                raise ValueError
        except Exception:  # noqa: BLE001 -- Replay errors must not expose raw records.
            raise DispatchOwnershipError("dispatch_intent_invalid") from None
        return rebuilt, consumed

    def bind_intent(self, slot, committed):
        """Synchronously claim one attempt, then return its owned read task.

        Claiming inside an async function would leave a retryable opened slot
        when its coroutine is cancelled before its first step. The owned task's
        completion callback also handles that case without resurrecting a slot.
        """
        entry = None
        try:
            with self._local:
                self._active()
                entry = self._entry(slot)
                # Capture before the FIRST await, including a queued mutex wait.
                generation = self._generation
                self._current(entry, generation, "opened")
                loop = asyncio.get_running_loop()
                if self._loop is not None and self._loop is not loop:
                    raise DispatchOwnershipError("dispatch_event_loop_changed")
                self._loop = loop
                started, mono = self._time()
                self._current(entry, generation, "opened")
                self._deadline(entry, started, mono)
                frozen, consumed = self._record(committed, entry)
                if not entry.opened_at <= consumed.updated_at <= started:
                    raise DispatchOwnershipError("dispatch_intent_not_same_invocation")
                self._cancellation(entry)
                entry.state = "reading"
                work = self._read_intent(entry, generation, frozen)
                try:
                    task = loop.create_task(work)
                except Exception:  # noqa: BLE001 -- Task-factory messages are private too.
                    work.close()
                    self._finish(entry, "denied", "dispatch_task_creation_failed")
                    raise DispatchOwnershipError(
                        "dispatch_task_creation_failed"
                    ) from None
                except BaseException:
                    work.close()
                    self._finish(entry, "denied", "dispatch_task_creation_failed")
                    raise
                # Strong ownership also prevents a pending task from being
                # collected when an invoker drops its reference after failure.
                entry.task = task
                task.add_done_callback(
                    lambda completed: self._completed(entry, completed)
                )
                return task
        except Exception as exc:  # noqa: BLE001 -- Invalid calls never leave open slots.
            with self._local:
                if entry is not None:
                    self._finish(entry, "denied", "dispatch_intent_binding_failed")
            code = _public_code(exc, "dispatch_intent_binding_failed")
            raise DispatchOwnershipError(code) from None

    def _completed(self, entry, task):
        """Terminal bookkeeping only, including cancellation before first step."""
        with self._local:
            if task.cancelled():
                self._finish(entry, "cancelled", "dispatch_invocation_cancelled")
            else:
                # Always retrieve a failure, even when the caller abandoned the
                # task. Never stringify/log it or pass it to an external hook.
                failure = task.exception()
                if failure is not None:
                    self._finish(entry, "denied", "dispatch_intent_binding_failed")
                    self._invalidate("dispatch_readback_uncertain_or_revoked")
            if entry.task is task:
                entry.task = None

    async def _read_intent(self, entry, generation, frozen):
        try:
            async with self._mutex:
                with self._local:
                    self._cancellation(entry)
                    self._current(entry, generation, "reading")
                    now, mono = self._time()
                    self._current(entry, generation, "reading")
                    self._deadline(entry, now, mono)
                # Call the existing repository implementation, not an instance
                # callback/issuer. It locks the scope in a separate DB session
                # and replays the durable request/transition journal itself.
                try:
                    result = await QualificationLedgerRepository.read_submission_intent(
                        self._repository,
                        entry.request.scope,
                        entry.request.origin.original_event_key,
                        expected_sha256=frozen.sha256,
                    )
                except Exception:  # noqa: BLE001 -- Even foreign exception messages are private.
                    raise DispatchOwnershipError("dispatch_readback_failed") from None
                with self._local:
                    # A dependency may catch CancelledError and return success.
                    # Outstanding cancellation still terminates this attempt.
                    self._cancellation(entry)
                    self._current(entry, generation, "reading")
                    now, mono = self._time()
                    self._current(entry, generation, "reading")
                    self._deadline(entry, now, mono)
                    # The repository just replayed the independently read row.
                    # Exact immutable bytes must equal our pre-await replay; a
                    # third computational replay would add no lineage proof.
                    replayed = self._record_snapshot(result)
                    if replayed != frozen:
                        raise DispatchOwnershipError("dispatch_readback_mismatch")
                    self._current(entry, generation, "reading")
                    entry.intent = replayed
                    entry.state = "bound_denied"
                    return self._observation(entry)
        except asyncio.CancelledError:
            with self._local:
                self._finish(entry, "cancelled", "dispatch_invocation_cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 -- DB/read uncertainty revokes locally.
            with self._local:
                self._finish(entry, "denied", "dispatch_intent_binding_failed")
                self._invalidate("dispatch_readback_uncertain_or_revoked")
            code = _public_code(exc, "dispatch_readback_failed")
            raise DispatchOwnershipError(code) from None

    @staticmethod
    def _observation(entry):
        return DispatchObservation(
            entry.state, entry.reason, entry.intent.sha256 if entry.intent else None
        )

    def inspect(self, slot):
        """Local diagnostic status, not a fresh DB/control/source observation."""
        with self._local:
            self._context()
            return self._observation(self._entry(slot))

    def cancel(self, slot):
        """Synchronously tombstone this invocation, including an awaited read."""
        with self._local:
            self._context()
            entry = self._entry(slot)
            self._finish(entry, "cancelled", "dispatch_invocation_cancelled")

    def revoke_now(self):
        """Synchronous local invalidation; no claim of distributed EStop IO."""
        with self._local:
            self._invalidate("dispatch_owner_revoked")

    def close(self):
        self.revoke_now()

    def require_ready(self, slot):
        """Production issuer deliberately unavailable. No bool/token override."""
        with self._local:
            self._active()
            entry = self._entry(slot)
            self._finish(entry, "denied", "dispatch_trusted_producer_unavailable")
        raise DispatchOwnershipError("dispatch_trusted_producer_unavailable")
