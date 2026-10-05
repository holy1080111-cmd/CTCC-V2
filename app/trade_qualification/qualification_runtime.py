"""Initial native public acquisition, before a candidate or G12 exists.

The one-use private handoff belongs to this invocation. Returned market/packet
copies are diagnostic data, never input permits for qualification or execution.
No entry policy, metadata/account completion, strategy choice or order IO exists.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import PosixPath, WindowsPath
from threading import get_ident
from typing import Literal
from weakref import WeakKeyDictionary

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    utc_from_ns,
    validate_stamps,
)
from app.exchange.okx.symbols import REVIEWED_DEMO_INSTRUMENT_IDS
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification.market_bridge import public_market_snapshot
from app.trade_qualification.market_bridge_v2 import public_market_context_v2
from app.trade_qualification.public_market_collector import _digest, _policy_copy
from app.trade_qualification.public_source_runtime import (
    _capture_initial_public,
    _capture_initial_public_v2,
    _consume_initial_public_capture,
    _consume_initial_public_capture_v2,
)

_ISSUER = object()
_INITIAL_SCOPES = WeakKeyDictionary()


class _InitialScope:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            raise ValueError("owned_initial_scope_required")

    def __copy__(self):
        raise ValueError("initial_scope_not_transferable")

    def __deepcopy__(self, memo):
        raise ValueError("initial_scope_not_transferable")

    def __reduce_ex__(self, protocol):
        raise ValueError("initial_scope_not_transferable")


class _InitialScopeV2(_InitialScope):
    """Exact new stage type; neither v1 nor publication consumers accept it."""

    __slots__ = ()


def _take_initial_scope(value):
    return _take_initial_scope_exact(value, _InitialScope)


def _take_initial_scope_v2(value):
    return _take_initial_scope_exact(value, _InitialScopeV2)


def _take_initial_scope_exact(value, expected):
    if type(value) is not expected:
        raise ValueError("owned_initial_scope_required")
    result = _INITIAL_SCOPES.pop(value, None)
    task = asyncio.current_task()
    if (
        result is None
        or result["parent"] is not task
        or result["pid"] != os.getpid()
        or result["thread"] != get_ident()
        or result["loop"] is not asyncio.get_running_loop()
        or task is None
        or task.cancelling()
    ):
        raise ValueError("owned_initial_scope_unavailable")
    return result


@dataclass(frozen=True, slots=True)
class InitialPublicDiagnostic:
    code: str
    report_id: str
    observed_at: datetime | None
    journal_sha256: str | None = None
    packet: object | None = field(default=None, repr=False)
    market: object | None = field(default=None, repr=False)
    record_kind: Literal["initial_owned_public_diagnostic_v1"] = field(
        default="initial_owned_public_diagnostic_v1", init=False
    )
    admission: Literal["DENY"] = field(default="DENY", init=False)
    original_source_verified: Literal[False] = field(default=False, init=False)
    candidate_created: Literal[False] = field(default=False, init=False)
    g12_published: Literal[False] = field(default=False, init=False)
    metadata_complete: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    atomic_risk_reserved: Literal[False] = field(default=False, init=False)
    order_submitted: Literal[False] = field(default=False, init=False)


async def capture_initial_public_market(
    public_root, *, instrument_id, market_policy
) -> InitialPublicDiagnostic:
    """Capture once into an existing empty absolute root using native sources.

    This is a public-only diagnostic entry, not an HTTP/API route. Configuration
    is bounded and copied before any await. There is no supplied market, report,
    clock, client, scope, receipt or candidate. Every invocation acquires anew;
    persisted journals are offline audit data and cannot resume this handoff.
    """
    if (
        type(public_root) not in (PosixPath, WindowsPath)
        or not public_root.is_absolute()
        or type(instrument_id) is not str
        or instrument_id not in REVIEWED_DEMO_INSTRUMENT_IDS
    ):
        raise ValueError("initial_public_inputs_invalid")
    selected = _policy_copy(market_policy)
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    invocation, scope = object(), None
    invocation_id = uuid.uuid4().hex
    report = "initial-" + invocation_id
    journal = None
    try:
        started = native_stamp()
        expires = utc_from_ns(started["utc_ns"]) + timedelta(
            seconds=selected.total_timeout_seconds
        )
        scope = _InitialScope(_ISSUER)
        _INITIAL_SCOPES[scope] = {
            "invocation": invocation,
            "parent": task,
            "pid": os.getpid(),
            "thread": get_ident(),
            "loop": asyncio.get_running_loop(),
            "plan": {
                "stage": "initial_public",
                "invocation_id": invocation_id,
                "environment": "demo",
                "report_id": report,
                "instrument_id": instrument_id,
                "invocation_started": started,
                "expires_at": expires.isoformat(),
                "rest_origin": "https://www.okx.com",
                "ws_origin": "wss://ws.okx.com:443/ws/v5/public",
                "policy_sha256": _digest(selected),
            },
            "report_id": report,
            "instrument_id": instrument_id,
            "invocation_started": started,
            "publication_completed_at": None,
            "expires_at": expires,
        }
        carrier = await _capture_initial_public(scope, selected, public_root)
        packet, journal, observed = _consume_initial_public_capture(carrier, invocation)
        # Only this same task's one-use native handoff reaches the bridge. The
        # serializable copies returned below never replace the internal lineage.
        market = public_market_snapshot(
            packet, expected_bundle_sha256=packet.bundle_sha256
        )
        finished = native_stamp()
        validate_stamps((observed, finished))
        if task.cancelling():
            raise asyncio.CancelledError
        if utc_from_ns(finished["utc_ns"]) >= expires:
            raise ValueError("initial_public_expired")
        return InitialPublicDiagnostic(
            "initial_public_captured_metadata_account_required",
            report,
            utc_from_ns(finished["utc_ns"]),
            journal,
            packet,
            market,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- no raw transport/private exception payload
        if task.cancelling():
            raise asyncio.CancelledError from None
        return InitialPublicDiagnostic("initial_public_denied", report, None, journal)
    finally:
        if scope is not None:
            _INITIAL_SCOPES.pop(scope, None)


@dataclass(frozen=True, slots=True)
class InitialPublicDiagnosticV2:
    code: str
    report_id: str | None
    observed_at: datetime | None
    journal_sha256: str | None = None
    packet: object | None = field(default=None, repr=False)
    context: object | None = field(default=None, repr=False)
    record_kind: str = field(default="initial_owned_public_diagnostic_v2", init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)
    original_source_verified: Literal[False] = field(default=False, init=False)
    candidate_created: Literal[False] = field(default=False, init=False)
    g12_published: Literal[False] = field(default=False, init=False)
    metadata_complete: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    atomic_risk_reserved: Literal[False] = field(default=False, init=False)
    order_submitted: Literal[False] = field(default=False, init=False)


async def _capture_initial_lineage_v2(
    public_root, *, instrument_id, market_policy, invocation
):
    """Private actual acquisition for this task; no persisted packet input.

    A future owned candidate coordinator calls this helper and immediately
    consumes its one-use carrier in the same invocation. It must not accept the
    diagnostic entry's returned DTO as a replacement for this acquisition.
    """
    if (
        type(public_root) not in (PosixPath, WindowsPath)
        or not public_root.is_absolute()
        or type(instrument_id) is not str
        or instrument_id not in REVIEWED_DEMO_INSTRUMENT_IDS
        or type(invocation) is not object
    ):
        raise ValueError("initial_public_inputs_invalid")
    selected = public_v2._policy_copy(market_policy)
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    invocation_id = uuid.uuid4().hex
    report = "initial-" + invocation_id
    started = native_stamp()
    expires = utc_from_ns(started["utc_ns"]) + timedelta(
        seconds=selected.total_timeout_seconds
    )
    scope = _InitialScopeV2(_ISSUER)
    _INITIAL_SCOPES[scope] = {
        "invocation": invocation,
        "parent": task,
        "pid": os.getpid(),
        "thread": get_ident(),
        "loop": asyncio.get_running_loop(),
        "plan": {
            "stage": "initial_public",
            "invocation_id": invocation_id,
            "environment": "demo",
            "report_id": report,
            "instrument_id": instrument_id,
            "invocation_started": started,
            "expires_at": expires.isoformat(),
            "rest_origin": "https://www.okx.com",
            "ws_origin": "wss://ws.okx.com:443/ws/v5/public",
            "policy_sha256": public_v2._policy_digest(selected),
            **public_v2._plan_pins(),
        },
        "report_id": report,
        "instrument_id": instrument_id,
        "invocation_started": started,
        "publication_completed_at": None,
        "expires_at": expires,
    }
    try:
        carrier = await _capture_initial_public_v2(scope, selected, public_root)
        return carrier, report, expires
    finally:
        _INITIAL_SCOPES.pop(scope, None)


async def capture_initial_public_market_v2(
    public_root, *, instrument_id, market_policy
):
    """Real native public-only capture; returned copies are never permissions."""
    invocation = object()
    report = journal = packet = None
    try:
        carrier, report, expires = await _capture_initial_lineage_v2(
            public_root,
            instrument_id=instrument_id,
            market_policy=market_policy,
            invocation=invocation,
        )
        packet, journal, observed = _consume_initial_public_capture_v2(
            carrier, invocation
        )
        context = public_market_context_v2(
            packet,
            expected_bundle_sha256=packet.bundle_sha256,
            evaluated_at=utc_from_ns(observed["utc_ns"]),
        )
        finished = native_stamp()
        validate_stamps((observed, finished))
        task = asyncio.current_task()
        if task is None or task.cancelling():
            raise asyncio.CancelledError
        if utc_from_ns(finished["utc_ns"]) >= expires:
            raise ValueError("initial_public_expired")
        return InitialPublicDiagnosticV2(
            "initial_public_v2_captured_g1_metadata_account_required",
            report,
            utc_from_ns(finished["utc_ns"]),
            journal,
            packet,
            context,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- no host/transport/private exception payload
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError from None
        return InitialPublicDiagnosticV2(
            "initial_public_v2_denied", report, None, journal, packet
        )
