"""Fail-closed entry boundaries while trusted runtime authority is absent.

G12 receipts, recorded rechecks, and DB0017 durable intent records are evidence,
not permission to dispatch an order. There is deliberately no permit issuer,
configuration bypass, payload flag, or historical-receipt admission here.

Existing cancel and close operations keep their service safety gates.
Order precheck belongs to order submission and remains denied without authority.
Set-leverage remains denied while the trusted flat-account authority is absent:
legacy execution can reach it before the later order-create denial. These
boundaries do not arm maintenance operations or grant Demo or Live authority.
"""

from app.exchange.okx.errors import OkxPrivateApiError

_MAINTENANCE_POST_PATHS = frozenset(
    {
        "/api/v5/trade/cancel-order",
        "/api/v5/trade/close-position",
        "/api/v5/trade/cancel-all-after",
    }
)


def enforce_demo_submission_boundary(method: str, path: str) -> None:
    """Called before signing and again immediately before transport dispatch.

    All order, batch-order, algo-entry, amend, and unclassified non-GET paths are
    denied. A caller's `write=False`, `passed=True`, or `reduceOnly=True` cannot
    substitute for qualified authority. Reduction uses the existing close-position
    operation; active protection readback and order cancellation remain available.
    """
    if type(method) is str and type(path) is str:
        if method.upper() == "GET":
            return
        if method.upper() == "POST" and path in _MAINTENANCE_POST_PATHS:
            return
    raise OkxPrivateApiError(
        "Demo entry requires trusted qualification runtime authority, which is unavailable",
        code="demo_qualification_authority_unavailable",
    )


def enforce_live_submission_boundary(method: str, path: str) -> None:
    """No Live entry is authorized by flags, a service Arm, or a journal replay.

    A future Live issuer must require the contemporaneous operator confirmation,
    fresh qualified input, durable one-shot intent and final safety guards. Until
    that integration exists, even a direct configured execution client cannot
    dispatch new exposure. Maintenance retains its existing service safeguards.
    """
    if type(method) is str and type(path) is str:
        if method.upper() == "GET":
            return
        if method.upper() == "POST" and path in _MAINTENANCE_POST_PATHS:
            return
    raise OkxPrivateApiError(
        "Live entry requires trusted one-shot qualification authority, which is unavailable",
        code="live_qualification_authority_unavailable",
    )
