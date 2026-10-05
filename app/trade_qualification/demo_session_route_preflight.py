"""Local Demo credential/route alignment before any authenticated capture.

Matching local configuration cannot authenticate an OKX response or prove an
account's registration region. This module deliberately issues no transport,
public-source, portfolio, or order capability. A future native capture must
perform its own signed/TLS/UID checks and bind the result within that task.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from typing import Literal

from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as collector
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.demo_public_origin import (
    DemoPublicOriginError,
    reviewed_demo_public_route,
)


class DemoSessionRoutePreflightError(ValueError):
    """Fixed local reason only; never contains a credential, UID, or URL."""


@dataclass(frozen=True, slots=True, repr=False)
class DemoSessionRoutePreflight:
    """A local equality check, not a transferable provenance receipt."""

    credential_route_aligned: Literal[True] = field(default=True, init=False)
    source_authenticated: Literal[False] = field(default=False, init=False)
    registration_region_verified: Literal[False] = field(default=False, init=False)
    same_task_session_verified: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def preflight_demo_session_route(
    session: ControlledDemoAccountSession,
    private_client: OkxDemoPrivateRestClient,
) -> DemoSessionRoutePreflight:
    """Compare one owned session to private-client configuration without IO.

    Exact object types and a native client are required. Strings, caller hashes,
    saved capture packets, and test transports cannot substitute for either side.
    Call again if configuration changes; the result is never an authority token.
    """
    if (
        type(session) is not ControlledDemoAccountSession
        or type(private_client) is not OkxDemoPrivateRestClient
    ):
        raise DemoSessionRoutePreflightError("demo_session_route_objects_required")
    if session._used is not False or private_client._external_client is not None:
        raise DemoSessionRoutePreflightError("demo_session_route_not_fresh_owned")
    try:
        plan = capture._checked_plan(session._plan, session._pin)
        route = reviewed_demo_public_route(plan.registration_region)
        if plan.origin != route.rest_origin:
            raise DemoSessionRoutePreflightError("demo_session_route_mismatch")
        if private_client._rest_base_url() != plan.origin:
            raise DemoSessionRoutePreflightError("demo_session_route_mismatch")
        if private_client._extra_headers() != {"x-simulated-trading": "1"}:
            raise DemoSessionRoutePreflightError("demo_session_route_mismatch")
        copied = collector._credential_values(session._credentials)
        configured = private_client._credentials()
        if (
            type(configured) is not tuple
            or len(configured) != 3
            or any(type(value) is not str for value in configured)
            or not all(
                hmac.compare_digest(left, right)
                for left, right in zip(copied[:3], configured, strict=True)
            )
        ):
            raise DemoSessionRoutePreflightError("demo_session_credential_mismatch")
    except DemoSessionRoutePreflightError:
        raise
    except DemoPublicOriginError:
        raise DemoSessionRoutePreflightError("demo_public_region_unreviewed") from None
    except Exception:  # noqa: BLE001 -- do not leak credentials/settings/parser detail
        raise DemoSessionRoutePreflightError("demo_session_route_invalid") from None
    return DemoSessionRoutePreflight()
