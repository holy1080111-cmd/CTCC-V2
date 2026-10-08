"""Private one-use source fence; injected B5 clocks still have no issuer.

Legacy B1 TLS evidence and an injected UTC callback cannot register this boundary.
The separate initial native-account runtime validates original phase companions
and durable source/proof readback before inserting its current-only registration.
Historical readback never creates one; B5 component ownership remains unissued.
"""

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import get_ident
from weakref import WeakKeyDictionary

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    canonical,
    decode,
    utc_from_ns,
    validate_stamps,
)
from app.trade_qualification import account_capture_journal as journal

PROOF_SCHEMA = "ctcc.demo_account_native_clock_proof.v2"
V3_PROOF_SCHEMA = "ctcc.demo_account_native_clock_proof.v3"
V7_FLAT_PROOF_SCHEMA = "ctcc.demo_account_native_clock_proof.v5"
MAX_LIFETIME_NS = 30_000_000_000
_NATIVE_ISSUER = object()
_BOUNDARIES = WeakKeyDictionary()


class AccountClockBoundaryError(ValueError):
    """Fixed private-data-safe rejection codes."""


class _AccountClockBoundary:
    """An exact private registry identity, never an input clock attestation."""

    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise AccountClockBoundaryError("account_native_clock_not_constructible")

    def __copy__(self):
        raise AccountClockBoundaryError("account_native_clock_not_transferable")

    def __deepcopy__(self, memo):
        raise AccountClockBoundaryError("account_native_clock_not_transferable")

    def __reduce_ex__(self, protocol):
        raise AccountClockBoundaryError("account_native_clock_not_transferable")


@dataclass(frozen=True, slots=True, repr=False)
class _NativeClockRegistration:
    # Only the actual initial native runtime may populate this registry after
    # its original source/proof readback. No registration API exists here.
    issuer: object
    invocation: object
    parent_task: object
    loop: object
    pid: int
    thread: int
    receipt_sha256: str
    proof_schema: str
    durable_clock_proof_sha256: str
    native_issue_stamp_json: bytes
    expires_at: datetime
    monotonic_deadline_ns: int


def _discard_boundary(value):
    if type(value) is _AccountClockBoundary:
        _BOUNDARIES.pop(value, None)


def _consume_boundary(value, *, invocation, receipt_sha256):
    """Burn first; independently sample time after exact private context checks.

    There is deliberately no register/issue API in this boundary. A caller-created
    identity, native handle, callback, proof hash or dictionary has no registry
    admission. Even a real TLS B1 acquisition cannot produce this capability.
    """
    if type(value) is not _AccountClockBoundary:
        raise AccountClockBoundaryError("account_native_clock_boundary_required")
    found = _BOUNDARIES.pop(value, None)
    if (
        type(found) is not _NativeClockRegistration
        or found.issuer is not _NATIVE_ISSUER
    ):
        raise AccountClockBoundaryError("account_native_clock_issuer_unavailable")
    try:
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
    except RuntimeError:
        raise AccountClockBoundaryError(
            "account_native_clock_context_invalid"
        ) from None
    if (
        invocation is None
        or found.invocation is not invocation
        or found.parent_task is not task
        or found.loop is not loop
        or type(found.pid) is not int
        or found.pid != os.getpid()
        or type(found.thread) is not int
        or found.thread != get_ident()
        or task is None
        or task.cancelling()
    ):
        raise AccountClockBoundaryError("account_native_clock_context_invalid")
    if (
        type(receipt_sha256) is not str
        or type(found.receipt_sha256) is not str
        or found.receipt_sha256 != receipt_sha256
        or type(found.proof_schema) is not str
        or found.proof_schema
        not in {PROOF_SCHEMA, V3_PROOF_SCHEMA, V7_FLAT_PROOF_SCHEMA}
    ):
        raise AccountClockBoundaryError("account_native_clock_proof_binding_invalid")
    try:
        journal._sha(found.receipt_sha256)
        journal._sha(found.durable_clock_proof_sha256)
        if type(found.native_issue_stamp_json) is not bytes:
            raise ValueError
        issue = decode(found.native_issue_stamp_json, maximum=1024)
        if canonical(issue) != found.native_issue_stamp_json:
            raise ValueError
        # The native sampler is the existing reviewed OS implementation. There
        # is no stored callback and no caller-selected clock to invoke here.
        current = native_stamp()
        issued_at = utc_from_ns(issue["utc_ns"])
        if (
            type(found.expires_at) is not datetime
            or found.expires_at.tzinfo is None
            or not issued_at < found.expires_at <= issued_at + timedelta(seconds=30)
            or type(found.monotonic_deadline_ns) is not int
            or not issue["monotonic_ns"]
            < found.monotonic_deadline_ns
            <= issue["monotonic_ns"] + MAX_LIFETIME_NS
        ):
            raise ValueError
        if (
            current["monotonic_ns"] >= found.monotonic_deadline_ns
            or utc_from_ns(current["utc_ns"]) >= found.expires_at
        ):
            raise AccountClockBoundaryError("account_native_clock_expired")
        validate_stamps((issue, current))
    except AccountClockBoundaryError:
        raise
    except Exception:  # noqa: BLE001 -- native OS errors contain no usable attestation
        raise AccountClockBoundaryError("account_native_clock_sample_invalid") from None
    return current
