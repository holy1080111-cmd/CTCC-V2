"""Independent final order-entry authority check for the shared OKX transport.

The existing Demo/Live route classifiers remain the first hard DENY. This
boundary additionally requires a process-owned DispatchOwnership slot tied to
the durable qualification intent. There is no READY issuer yet, so even a
valid diagnostic owner/slot cannot send a write. Caller payloads, flags and
receipts are never considered authority.
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
) -> None:
    """Deny every private write without an owned durable invocation.

    ``require_ready`` currently tombstones and denies every slot. When a
    trusted source-owned issuer is implemented, this boundary must also bind
    its result to the exact signed request bytes, environment, account,
    credential session, expiry and one-shot consumption before permitting IO.
    Merely changing ``require_ready`` to return a truthy value grants nothing.
    """
    if type(method) is not str or type(path) is not str:
        raise OkxPrivateApiError(
            "Private order method or path is invalid",
            code="private_write_authority_unavailable",
        )
    if method.upper() == "GET":
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
