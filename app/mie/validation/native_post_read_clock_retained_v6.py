"""One-use retained Stage1 handoff; old V4 calculation and leases stay intact."""

import asyncio
import os
from threading import get_ident
from weakref import WeakKeyDictionary

from app.mie.validation import native_stage1_forensics_v6 as forensics
from app.mie.validation.native_post_read_clock_v4 import (
    _acquire_native_post_read_clock,
    consume_internal_post_read_clock_v4,
)

_ISSUER = object()
_RESULTS = WeakKeyDictionary()


def _context():
    return os.getpid(), get_ident(), asyncio.get_running_loop(), asyncio.current_task()


class RetainedPostReadClockSampleV6:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            raise forensics.Stage1ForensicError("stage1_retained_owner_required")

    def __copy__(self):
        raise forensics.Stage1ForensicError("stage1_retained_not_transferable")

    def __deepcopy__(self, memo):
        raise forensics.Stage1ForensicError("stage1_retained_not_transferable")

    def __reduce_ex__(self, protocol):
        raise forensics.Stage1ForensicError("stage1_retained_not_transferable")


async def acquire_retained_post_read_clock_v6(
    *, service, source, expected_plan_sha256, observation_repository, retention
):
    forensics.require_pending(retention)
    sample = await _acquire_native_post_read_clock(
        service=service,
        source=source,
        expected_plan_sha256=expected_plan_sha256,
        observation_repository=observation_repository,
        _retention=retention,
    )
    snapshot = forensics.readback(retention)
    if snapshot.status != "completed":
        raise forensics.Stage1ForensicError("stage1_retained_incomplete")
    result = RetainedPostReadClockSampleV6(_ISSUER)
    _RESULTS[result] = (_context(), sample, retention, snapshot)
    return result


def consume_retained_post_read_clock_v6(sample):
    if type(sample) is not RetainedPostReadClockSampleV6:
        raise forensics.Stage1ForensicError("stage1_retained_owner_required")
    bound = _RESULTS.pop(sample, None)
    if bound is None or bound[0] != _context():
        raise forensics.Stage1ForensicError("stage1_retained_owner_required")
    frozen = consume_internal_post_read_clock_v4(bound[1])
    snapshot = forensics.readback(bound[2])
    if snapshot != bound[3]:
        raise forensics.Stage1ForensicError("stage1_retained_bytes_changed")
    return frozen, snapshot
