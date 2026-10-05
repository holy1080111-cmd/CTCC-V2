"""Reviewed OKX Demo public routes; route selection is not account provenance.

The native v2 capture remains disabled until a controlled credential session
establishes the exact registration region and this policy is wired end to end.
No caller-supplied route, packet, or receipt can act as that session binding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from urllib.parse import urlsplit

import httpx


class DemoPublicOriginError(ValueError):
    """Fixed local route-policy failure; never contains remote data."""


# OKX regional API overviews and 2026-09-30 WS port change. Turkey is omitted:
# its reviewed overview does not establish an exact Demo public WS domain.
_ROUTES = MappingProxyType(
    {
        "global": (
            "https://openapi.okx.com",
            "wss://wspap.okx.com:443/ws/v5/public",
        ),
        "us_au": (
            "https://us.okx.com",
            "wss://wsuspap.okx.com:443/ws/v5/public",
        ),
        "eea": (
            "https://eea.okx.com",
            "wss://wseeapap.okx.com:443/ws/v5/public",
        ),
    }
)
_PATHS = MappingProxyType(
    {
        "quote": frozenset(
            {
                "/api/v5/market/ticker",
                "/api/v5/public/mark-price",
                "/api/v5/public/funding-rate",
            }
        ),
        "candles": frozenset({"/api/v5/market/candles"}),
        "market_aux": frozenset(
            {"/api/v5/market/books", "/api/v5/public/open-interest"}
        ),
    }
)
_USER_AGENTS = MappingProxyType(
    {
        "quote": "CTCC-source-quote/1",
        "candles": "CTCC-source-candles/1",
        "market_aux": "CTCC-source-market-aux/1",
    }
)


@dataclass(frozen=True, slots=True)
class ReviewedDemoPublicRoute:
    """Exact public transport policy, with no account or execution authority."""

    registration_region: str
    rest_origin: str
    ws_origin: str
    environment: str = field(default="demo", init=False)
    admission: str = field(default="DENY", init=False)
    source_authenticity_verified: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)

    def __post_init__(self):
        if (
            type(self.registration_region) is not str
            or self.registration_region not in _ROUTES
            or type(self.rest_origin) is not str
            or type(self.ws_origin) is not str
            or (self.rest_origin, self.ws_origin) != _ROUTES[self.registration_region]
        ):
            raise DemoPublicOriginError("demo_public_route_not_reviewed")

    @property
    def rest_hostname(self) -> str:
        return urlsplit(self.rest_origin).hostname or ""

    @property
    def ws_hostname(self) -> str:
        return urlsplit(self.ws_origin).hostname or ""


def reviewed_demo_public_route(registration_region: str) -> ReviewedDemoPublicRoute:
    """Return a reviewed route; the region argument is not authenticated here."""
    if type(registration_region) is not str or registration_region not in _ROUTES:
        raise DemoPublicOriginError("demo_public_region_unreviewed")
    rest, ws = _ROUTES[registration_region]
    return ReviewedDemoPublicRoute(registration_region, rest, ws)


def _checked_route(route: ReviewedDemoPublicRoute) -> ReviewedDemoPublicRoute:
    if type(route) is not ReviewedDemoPublicRoute:
        raise DemoPublicOriginError("demo_public_route_required")
    # Revalidate even if a frozen dataclass was mutated with object.__setattr__.
    ReviewedDemoPublicRoute(
        route.registration_region, route.rest_origin, route.ws_origin
    )
    if (
        route.environment != "demo"
        or route.admission != "DENY"
        or route.source_authenticity_verified is not False
        or route.execution_authority is not False
    ):
        raise DemoPublicOriginError("demo_public_route_not_reviewed")
    return route


def demo_public_headers(route: ReviewedDemoPublicRoute, role: str) -> dict[str, str]:
    """Exact unauthenticated Demo REST headers for a reviewed public role."""
    _checked_route(route)
    if type(role) is not str or role not in _USER_AGENTS:
        raise DemoPublicOriginError("demo_public_role_invalid")
    return {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": _USER_AGENTS[role],
        "x-simulated-trading": "1",
    }


def validate_demo_public_request(
    route: ReviewedDemoPublicRoute, role: str, request: httpx.Request
) -> None:
    """Reject wrong host, route, Demo header, method, or hidden auth before IO."""
    expected = demo_public_headers(route, role)
    if type(request) is not httpx.Request:
        raise DemoPublicOriginError("demo_public_request_invalid")
    try:
        body = request.content
    except httpx.RequestNotRead:
        raise DemoPublicOriginError("demo_public_request_invalid") from None
    header_items = tuple(
        (key.lower(), value) for key, value in request.headers.multi_items()
    )
    headers = dict(header_items)
    required = {key.lower(): value for key, value in expected.items()}
    required["host"] = route.rest_hostname
    if (
        request.method != "GET"
        or request.url.scheme != "https"
        or request.url.host != route.rest_hostname
        or request.url.port not in (None, 443)
        or request.url.path not in _PATHS[role]
        or request.url.username
        or request.url.password
        or body
        or len(headers) != len(header_items)
        or headers != required
    ):
        raise DemoPublicOriginError("demo_public_request_invalid")


def validate_demo_public_ws(
    route: ReviewedDemoPublicRoute, endpoint: str, tls_hostname: str
) -> None:
    """Require the region's Demo socket and the same verified TLS hostname."""
    _checked_route(route)
    if (
        type(endpoint) is not str
        or type(tls_hostname) is not str
        or endpoint != route.ws_origin
        or tls_hostname != route.ws_hostname
    ):
        raise DemoPublicOriginError("demo_public_ws_origin_mismatch")
