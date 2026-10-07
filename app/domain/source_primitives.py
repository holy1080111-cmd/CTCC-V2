"""Pure canonical bytes and clock primitives shared by trusted source domains.

This module has no exchange, account, storage, network, or execution dependency.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_RAW = 1024 * 1024
MAX_TIMESTAMP_NS = 32_503_680_000_000_000_000
MAX_CLOCK_DEVIATION_NS = 5_000_000
MAX_CLOCK_BATCH_NS = 60_000_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
Ns = Annotated[int, Field(ge=0, le=MAX_TIMESTAMP_NS)]


class PublicReceiptError(ValueError):
    """Fixed source-evidence rejection codes, never raw diagnostics."""


def sha(payload: bytes) -> str:
    if type(payload) is not bytes:
        raise PublicReceiptError("bytes_required")
    return hashlib.sha256(payload).hexdigest()


def _plain(value, depth=0):
    if depth > 24:
        raise PublicReceiptError("structure_limit")
    kind = type(value)
    if kind in (str, int, bool) or value is None:
        return
    if kind in (list, tuple):
        if len(value) > 4096:
            raise PublicReceiptError("structure_limit")
        for item in value:
            _plain(item, depth + 1)
        return
    if kind is dict:
        if len(value) > 128 or any(type(key) is not str for key in value):
            raise PublicReceiptError("structure_invalid")
        for item in value.values():
            _plain(item, depth + 1)
        return
    raise PublicReceiptError("plain_scalar_required")


def canonical(value) -> bytes:
    _plain(value)
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


# Immutable, public-only Demo V2 route identity shared by the source journal
# and qualification's independent route policy. These declarations neither
# attest an account's registration region nor grant network or order authority.
_DEMO_V2_ROUTES = MappingProxyType(
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
_DEMO_V2_ROLE_PATHS = MappingProxyType(
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
_DEMO_V2_USER_AGENTS = MappingProxyType(
    {
        "quote": "CTCC-source-quote/1",
        "candles": "CTCC-source-candles/1",
        "market_aux": "CTCC-source-market-aux/1",
    }
)
_DEMO_V2_QUOTE_ORDER = (
    ("funding", "/api/v5/public/funding-rate"),
    ("mark", "/api/v5/public/mark-price"),
    ("ticker", "/api/v5/market/ticker"),
)


def demo_public_v2_route(region: str) -> tuple[str, str, str, str]:
    """Frozen route declaration for replay; the region is an untrusted claim."""
    if type(region) is not str or region not in _DEMO_V2_ROUTES:
        raise PublicReceiptError("demo_public_v2_region_invalid")
    return _DEMO_V2_ROUTES[region]


def demo_public_v2_headers(region: str, role: str) -> dict[str, str]:
    demo_public_v2_route(region)
    if type(role) is not str or role not in _DEMO_V2_USER_AGENTS:
        raise PublicReceiptError("demo_public_v2_role_invalid")
    return {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": _DEMO_V2_USER_AGENTS[role],
        "x-simulated-trading": "1",
    }


def demo_public_v2_policy_document(region: str) -> dict:
    rest, ws, rest_host, ws_host = demo_public_v2_route(region)
    return {
        "schema_version": "ctcc.demo_public_origin_policy.v2",
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
                "headers": demo_public_v2_headers(region, role),
            }
            for role, paths in _DEMO_V2_ROLE_PATHS.items()
        },
        "request_observed": False,
        "response_observed": False,
        "account_region_authenticated": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
        "admission": "DENY",
    }


def demo_public_v2_policy_sha256(region: str) -> str:
    return sha(canonical(demo_public_v2_policy_document(region)))


def demo_public_v2_quote_transport_sha256(region: str) -> str:
    """Hash the historical V3 quote transport declaration without IO imports."""
    rest, _, _, _ = demo_public_v2_route(region)
    return sha(
        canonical(
            {
                "version": "ctcc.raw_quote_transport.v3",
                "origin": rest,
                "method": "GET",
                "request_order": _DEMO_V2_QUOTE_ORDER,
                "request_timeout_seconds": 2,
                "batch_timeout_seconds": 6,
                "max_response_bytes": 32768,
                "retries": 0,
                "redirects": False,
                "proxy": False,
                "native_journal": "existing_owned_source_fetch_http",
                "registration_region": region,
                "request_headers": demo_public_v2_headers(region, "quote"),
                "demo_public_origin_policy_sha256": demo_public_v2_policy_sha256(
                    region
                ),
            }
        )
    )


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PublicReceiptError("duplicate_json_key")
        result[key] = value
    return result


def decode(raw: bytes, maximum=MAX_RAW):
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        raise PublicReceiptError("raw_size_invalid")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        _plain(result)
        return result
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PublicReceiptError("raw_json_invalid") from exc


def utc_from_ns(value: int) -> datetime:
    if type(value) is not int or not 0 <= value <= MAX_TIMESTAMP_NS:
        raise PublicReceiptError("timestamp_invalid")
    # Existing datetime contracts store microseconds. Round up conservatively.
    return EPOCH + timedelta(microseconds=(value + 999) // 1000)


class ClockStamp(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", revalidate_instances="always"
    )

    utc_ns: Ns
    monotonic_ns: Ns

    @model_validator(mode="before")
    @classmethod
    def plain_clock_stamp(cls, value):
        _plain(value)
        if type(value) is dict and any(
            name in value
            and (type(value[name]) is not bool or value[name] is not False)
            for name in ("predictive_oos_eligible", "execution_authority")
        ):
            raise PublicReceiptError("authority_forbidden")
        return value


def validate_stamps(stamps):
    if type(stamps) not in (list, tuple) or not 2 <= len(stamps) <= 512:
        raise PublicReceiptError("clock_sequence_invalid")
    values = [ClockStamp.model_validate(item) for item in stamps]
    first = values[0]
    previous = first
    for current in values[1:]:
        if (
            current.utc_ns < previous.utc_ns
            or current.monotonic_ns < previous.monotonic_ns
        ):
            raise PublicReceiptError("clock_reversed")
        if (
            abs(
                (current.utc_ns - first.utc_ns)
                - (current.monotonic_ns - first.monotonic_ns)
            )
            > MAX_CLOCK_DEVIATION_NS
        ):
            raise PublicReceiptError("clock_jump")
        if current.monotonic_ns - first.monotonic_ns > MAX_CLOCK_BATCH_NS:
            raise PublicReceiptError("clock_lease_expired")
        previous = current
    return tuple(values)
