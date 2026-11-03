"""Independent final order-entry authority check for the shared OKX transport.

Legacy Demo/Live routes and diagnostic DispatchOwnership slots remain denied.
The distinct native Demo branch requires a genuine same-invocation durable
intent, actual retained database locks and the exact prepared signed request.
Its independent TLS/socket gate replays that ownership after internal awaits.
Caller payloads, flags, historical receipts and copied DATA grant no authority.
"""

from __future__ import annotations

from app.exchange.okx.errors import OkxPrivateApiError
from app.trade_qualification.dispatch_ownership import (
    DispatchOwnership,
    DispatchOwnershipError,
    DispatchSlot,
)


def require_owned_private_write_authority(
    method: str,
    path: str,
    *,
    owner: DispatchOwnership | None,
    slot: DispatchSlot | None,
    client: object | None = None,
) -> None:
    """Deny every private write without an owned durable invocation.

    The native branch compares the exact signed bytes, environment, account,
    credential session, original expiry and one-shot intent; it also requires
    the separately guarded physical transport. Diagnostic ``require_ready``
    remains a terminal denial and cannot grant a native slot.
    """
    if type(method) is not str or type(path) is not str:
        raise OkxPrivateApiError(
            "Private order method or path is invalid",
            code="private_write_authority_unavailable",
        )
    if method.upper() == "GET":
        return
    from app.exchange.okx import native_demo_write_v2 as native_write

    if native_write._native_leverage_configuration_route_v1(client, method, path):
        native_write._require_native_leverage_configuration_private_write_v1(
            client, method, path
        )
        return
    if native_write._native_route_v2(client, method, path):
        native_write._require_native_private_write_v2(client, method, path)
        return
    if type(owner) is DispatchOwnership and type(slot) is DispatchSlot:
        try:
            owner.require_ready(slot)
        except DispatchOwnershipError:
            pass
    raise OkxPrivateApiError(
        "Private write requires owned durable qualification authority",
        code="private_write_authority_unavailable",
    )
