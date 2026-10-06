"""Frozen, replayable Demo public-route policy; never source or order authority.

The V2 table is intentionally copied from the reviewed route policy. Future
route changes require a new policy version: old V2 bytes must remain replayable.
No account, request, socket, response, or credential is observed in this module.
"""

from __future__ import annotations

from types import MappingProxyType

from app.domain.source_primitives import PublicReceiptError, canonical, decode
from app.trade_qualification import demo_public_origin as origin

SCHEMA_VERSION = "ctcc.demo_public_origin_policy.v2"
MAX_POLICY_BYTES = 4096

_ROUTES_V2 = MappingProxyType(
    {
        "global": (
            "https://openapi.okx.com",
            "wss://wspap.okx.com:443/ws/v5/public",
            "openapi.okx.com",
            "wspap.okx.com",
        ),
        "us_au": (
            "https://us.okx.com",
            "wss://wsuspap.okx.com:443/ws/v5/public",
            "us.okx.com",
            "wsuspap.okx.com",
        ),
        "eea": (
            "https://eea.okx.com",
            "wss://wseeapap.okx.com:443/ws/v5/public",
            "eea.okx.com",
            "wseeapap.okx.com",
        ),
    }
)
_ROLE_PATHS_V2 = MappingProxyType(
    {
        "quote": (
            "/api/v5/market/ticker",
            "/api/v5/public/funding-rate",
            "/api/v5/public/mark-price",
        ),
        "candles": ("/api/v5/market/candles",),
        "market_aux": (
            "/api/v5/market/books",
            "/api/v5/public/open-interest",
        ),
    }
)
_USER_AGENTS_V2 = MappingProxyType(
    {
        "quote": "CTCC-source-quote/1",
        "candles": "CTCC-source-candles/1",
        "market_aux": "CTCC-source-market-aux/1",
    }
)


def _document(region: str) -> dict:
    if type(region) is not str or region not in _ROUTES_V2:
        raise origin.DemoPublicOriginError("demo_public_v2_region_unreviewed")
    rest, ws, rest_host, ws_host = _ROUTES_V2[region]
    return {
        "schema_version": SCHEMA_VERSION,
        "environment": "demo",
        "registration_region": region,
        "rest_origin": rest,
        "rest_tls_hostname": rest_host,
        "ws_origin": ws,
        "ws_tls_hostname": ws_host,
        "rest_roles": {
            role: {
                "methods": ["GET"],
                "paths": list(paths),
                "headers": {
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "User-Agent": _USER_AGENTS_V2[role],
                    "x-simulated-trading": "1",
                },
            }
            for role, paths in _ROLE_PATHS_V2.items()
        },
        "request_observed": False,
        "response_observed": False,
        "account_region_authenticated": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
        "admission": "DENY",
    }


def freeze_demo_public_origin_policy_v2(region: str) -> bytes:
    """Freeze reviewed policy only; a region argument is still a caller claim."""
    document = _document(region)
    reviewed = origin.reviewed_demo_public_route(region)
    if (
        reviewed.rest_origin != document["rest_origin"]
        or reviewed.ws_origin != document["ws_origin"]
        or reviewed.rest_hostname != document["rest_tls_hostname"]
        or reviewed.ws_hostname != document["ws_tls_hostname"]
        or any(
            origin.demo_public_headers(reviewed, role)
            != document["rest_roles"][role]["headers"]
            or sorted(origin._PATHS[role])
            != sorted(document["rest_roles"][role]["paths"])
            for role in _ROLE_PATHS_V2
        )
    ):
        raise origin.DemoPublicOriginError("demo_public_v2_current_policy_changed")
    raw = canonical(document)
    if len(raw) > MAX_POLICY_BYTES:
        raise origin.DemoPublicOriginError("demo_public_v2_policy_unbounded")
    return raw


def replay_demo_public_origin_policy_v2(
    raw: bytes, *, expected_registration_region: str
) -> bytes:
    """Replay exact V2 bytes against an independently selected region.

    This only validates declared policy. Even a matching record cannot prove
    account registration, actual transport, a source response, or permission.
    Historical V2 replay uses its frozen table, not a later route table.
    """
    expected = canonical(_document(expected_registration_region))
    try:
        if (
            type(raw) is not bytes
            or not 0 < len(raw) <= MAX_POLICY_BYTES
            or decode(raw, MAX_POLICY_BYTES) != _document(expected_registration_region)
            or raw != expected
        ):
            raise ValueError
    except (PublicReceiptError, ValueError):
        raise origin.DemoPublicOriginError(
            "demo_public_v2_policy_replay_invalid"
        ) from None
    return expected
