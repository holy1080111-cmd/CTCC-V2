"""One-task Demo public route preparation from a controlled account session.

This is a declaration check, not an authenticated source or a network issuer.
The native V2 public runtime must keep its hard DENY until every transport and
replay role enforces this route and the account registration evidence is verified.
"""

from __future__ import annotations

import asyncio
import os
from hashlib import sha256
from threading import get_ident
from weakref import WeakKeyDictionary

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as collector
from app.trade_qualification import demo_public_origin as origin
from app.trade_qualification import demo_public_origin_policy_v2 as policy_v2
from app.trade_qualification.account_runtime import ControlledDemoAccountSession

_ISSUER = object()
_PREPARED = WeakKeyDictionary()


class DemoPublicOriginPreflightError(ValueError):
    """Static local route-declaration rejection; no credential or UID in errors."""


class _PreparedDemoPublicRoute:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            raise DemoPublicOriginPreflightError("controlled_demo_route_required")

    def __copy__(self):
        raise DemoPublicOriginPreflightError("controlled_demo_route_not_transferable")

    def __deepcopy__(self, memo):
        raise DemoPublicOriginPreflightError("controlled_demo_route_not_transferable")

    def __reduce_ex__(self, protocol):
        raise DemoPublicOriginPreflightError("controlled_demo_route_not_transferable")


def _session_route(session):
    if type(session) is not ControlledDemoAccountSession or session._used:
        raise DemoPublicOriginPreflightError("controlled_demo_account_session_required")
    try:
        plan = capture._checked_plan(session._plan, session._pin)
        if (
            type(plan) is not capture.CurrentDemoAccountCapturePlanV6
            or type(session._credentials) is not collector.DemoAccountCredentials
            or session._credentials.session_binding_id != plan.session_binding_id
        ):
            raise ValueError
        route = origin.reviewed_demo_public_route(plan.registration_region)
        if plan.origin != route.rest_origin:
            raise ValueError
        return route, session._pin
    except Exception:  # noqa: BLE001 -- redact private plan and credential fields
        raise DemoPublicOriginPreflightError(
            "controlled_demo_route_scope_invalid"
        ) from None


def _prepare_controlled_demo_route(session):
    """Prepare a non-authoritative route in the session's current task only."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    if task is None or task.cancelling():
        raise DemoPublicOriginPreflightError("controlled_demo_route_task_required")
    route, pin = _session_route(session)
    prepared = _PreparedDemoPublicRoute(_ISSUER)
    _PREPARED[prepared] = {
        "task": task,
        "loop": asyncio.get_running_loop(),
        "thread": get_ident(),
        "pid": os.getpid(),
        "session": session,
        "pin": pin,
        "route": route,
        "consumed": False,
    }
    return prepared


def _consume_controlled_demo_route(prepared, session):
    """Consume once in the original task; returns policy, never IO authority."""
    if type(prepared) is not _PreparedDemoPublicRoute:
        raise DemoPublicOriginPreflightError("controlled_demo_route_required")
    state = _PREPARED.get(prepared)
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    if (
        state is None
        or state["consumed"]
        or task is None
        or task.cancelling()
        or task is not state["task"]
        or asyncio.get_running_loop() is not state["loop"]
        or get_ident() != state["thread"]
        or os.getpid() != state["pid"]
        or session is not state["session"]
    ):
        raise DemoPublicOriginPreflightError("controlled_demo_route_not_owned")
    # Burn before rechecking mutable session state, including after a failure.
    state["consumed"] = True
    route, pin = _session_route(session)
    if route != state["route"] or pin != state["pin"]:
        raise DemoPublicOriginPreflightError("controlled_demo_route_changed")
    return route, pin


def _declared_demo_public_plan(session):
    """Freeze a same-task route declaration, never an authenticated IO permit."""
    prepared = _prepare_controlled_demo_route(session)
    route, account_plan_sha256 = _consume_controlled_demo_route(prepared, session)
    raw_policy = policy_v2.freeze_demo_public_origin_policy_v2(
        route.registration_region
    )
    return {
        "registration_region": route.registration_region,
        "registration_region_authenticated": False,
        "account_plan_sha256": account_plan_sha256,
        "demo_public_origin_policy_sha256": sha256(raw_policy).hexdigest(),
        "rest_origin": route.rest_origin,
        "ws_origin": route.ws_origin,
    }
