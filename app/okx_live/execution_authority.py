"""Fail-closed Live maintenance dispatch boundary.

The current Live account mirror does not prove a complete page chain, a fresh
account revision, or a controlled credential-session binding. A persisted
legacy execution intent and a manual confirmation phrase therefore cannot
issue an account-scoped, one-use maintenance permit. No permit issuer exists in
this module; all Live cancel, close, and Cancel All After writes remain disabled
until that authority is implemented and independently verified.

Read-only reconciliation, local disarm, and the emergency-stop latch do not
pass through this boundary and remain available.
"""

from __future__ import annotations

from typing import Literal

from app.exchange.okx.errors import OkxPrivateApiError
from app.okx_live import OkxLiveSafetyError
from app.trade_qualification.execution_authority import (
    enforce_live_submission_boundary,
)

LiveMaintenanceAction = Literal["cancel_order", "close_position", "cancel_all_after"]

_MAINTENANCE_PATHS = frozenset(
    {
        "/api/v5/trade/cancel-order",
        "/api/v5/trade/close-position",
        "/api/v5/trade/cancel-all-after",
    }
)


def require_live_maintenance_service_authority(action: LiveMaintenanceAction) -> None:
    """Reject a service write before reserving an unusable legacy intent.

    The action argument is for auditability, never a caller-supplied permit.
    There is intentionally no boolean, token, or receipt input that can turn
    this into an approval while trusted account authority is absent.
    """
    if action not in {"cancel_order", "close_position", "cancel_all_after"}:
        raise OkxLiveSafetyError("okx_live_maintenance_action_unknown")
    raise OkxLiveSafetyError("okx_live_maintenance_authority_unavailable")


def enforce_live_final_dispatch(method: str, path: str) -> None:
    """Run synchronously after signing and just before private HTTP dispatch.

    Also repeats the entry boundary so a direct base-class call cannot turn
    unclassified writes into maintenance. A future permit must bind exact
    account, credential session, request bytes, source revision, and one-use
    durable state; none can currently be proven here.
    """
    enforce_live_submission_boundary(method, path)
    if method.upper() == "POST" and path in _MAINTENANCE_PATHS:
        raise OkxPrivateApiError(
            "Live maintenance requires trusted one-use account authority",
            code="live_maintenance_authority_unavailable",
        )
