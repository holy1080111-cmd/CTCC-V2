from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import itertools
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config.settings import Settings, get_settings
from app.exchange.okx.errors import OkxPrivateApiError
from app.okx_live.execution_authority import enforce_live_final_dispatch
from app.trade_qualification.execution_authority import (
    enforce_demo_submission_boundary,
    enforce_live_submission_boundary,
)

Clock = Callable[[], datetime]
_ALGO_ORDER_TYPES = ("conditional", "oco", "trigger", "move_order_stop")
_PRIVATE_PAGE_SIZE = 100
_PRIVATE_MAX_PAGES = 16
_CANCEL_ALL_AFTER_PATH = "/api/v5/trade/cancel-all-after"


def _validate_cancel_all_after_dispatch(
    *, method: str, path: str, request_path: str, body_text: str
) -> None:
    """Validate the immutable bytes-to-send after signing and before HTTP IO."""
    if method.upper() != "POST" or path != _CANCEL_ALL_AFTER_PATH:
        return
    try:
        payload = json.loads(body_text)
    except (TypeError, ValueError) as exc:
        raise OkxPrivateApiError(
            "Cancel All After payload is invalid",
            code="cancel_all_after_payload_rejected",
        ) from exc
    if (
        request_path != path
        or type(payload) is not dict
        or set(payload) not in ({"timeOut"}, {"timeOut", "tag"})
        or type(payload.get("timeOut")) is not str
        or re.fullmatch(r"[1-9][0-9]{1,2}", payload["timeOut"]) is None
        or not 10 <= int(payload["timeOut"]) <= 120
        or (
            "tag" in payload
            and (
                type(payload["tag"]) is not str
                or re.fullmatch(r"[A-Za-z0-9]{1,16}", payload["tag"]) is None
            )
        )
    ):
        raise OkxPrivateApiError(
            "Cancel All After payload is invalid",
            code="cancel_all_after_payload_rejected",
        )


def _validated_origin(value: str, hosts: frozenset[str]) -> str:
    origin = httpx.URL(value)
    if (
        origin.scheme != "https"
        or origin.host not in hosts
        or origin.port not in (None, 443)
        or origin.userinfo
        or origin.raw_path not in (b"", b"/")
        or origin.query
        or origin.fragment
    ):
        raise OkxPrivateApiError(
            "Private REST origin is invalid", code="private_transport_target_rejected"
        )
    return str(origin).rstrip("/")


def utc_iso_timestamp(now: datetime | None = None) -> str:
    value = now or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_signature(
    *,
    timestamp: str,
    method: str,
    request_path: str,
    body: str,
    secret: str,
) -> str:
    prehash = f"{timestamp}{method.upper()}{request_path}{body}"
    digest = hmac.new(
        secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("ascii")


class _OkxPrivateRestClientBase:
    """Authenticated OKX private REST transport shared by fixed environments.

    Environment-specific subclasses supply credentials and headers. Read requests
    have bounded retries; write requests are never automatically retried to avoid
    duplicate orders after ambiguous network failures.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        settings: Settings | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._external_client = client
        self._clock = clock or (lambda: datetime.now(UTC))

    def _credentials(self) -> tuple[str, str, str]:
        raise NotImplementedError

    def _rest_base_url(self) -> str:
        raise NotImplementedError

    def _timeout_seconds(self) -> float:
        raise NotImplementedError

    def _read_max_retries(self) -> int:
        raise NotImplementedError

    def _extra_headers(self) -> dict[str, str]:
        return {}

    def _request_extra_headers(
        self, *, method: str, path: str, write: bool
    ) -> dict[str, str]:
        return {}

    def _before_send(self, *, method: str, path: str) -> None:
        """Synchronous application check; the HTTP client still has internal awaits."""

    def _final_dispatch_check(self, *, method: str, path: str) -> None:
        """Environment-specific authority after signing, immediately before HTTP."""

    def _validate_external_client(self, client: httpx.AsyncClient) -> None:
        """Injected clients are a bounded, network-free test adapter only.

        Runtime clients are owned here. An injected client may not change signed
        requests through headers, default query/cookies, auth, hooks or mounted
        network transports. Recheck after signing on every attempt.
        """
        if self._external_client is None:
            return
        allowed_headers = {"accept", "accept-encoding", "connection", "user-agent"}
        if (
            type(client) is not httpx.AsyncClient
            or type(client._transport) is not httpx.MockTransport
            or any(value is not None for value in client._mounts.values())
            or client.auth is not None
            or any(client.event_hooks.values())
            or set(client.headers) - allowed_headers
            or client.params
            or client.cookies
        ):
            raise OkxPrivateApiError(
                "Injected private transport is not a controlled test adapter",
                code="private_transport_adapter_rejected",
            )

    def _headers(self, *, method: str, request_path: str, body: str) -> dict[str, str]:
        api_key, api_secret, passphrase = self._credentials()
        timestamp = utc_iso_timestamp(self._clock())
        signature = build_signature(
            timestamp=timestamp,
            method=method,
            request_path=request_path,
            body=body,
            secret=api_secret,
        )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"CTCC-V2/{self.settings.app_version}",
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "OK-ACCESS-TIMESTAMP": timestamp,
        }
        headers.update(self._extra_headers())
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        write: bool = False,
    ) -> list[dict[str, Any]]:
        self._before_send(method=method, path=path)
        if (
            type(path) is not str
            or re.fullmatch(r"/api/v5/[a-z0-9]+(?:[-/][a-z0-9]+)*", path) is None
        ):
            raise OkxPrivateApiError(
                "Private REST path is invalid", code="private_transport_target_rejected"
            )
        # Non-GET requests cannot gain retries by claiming to be reads.
        write = write or method.upper() != "GET"
        query = str(
            httpx.QueryParams(
                {k: v for k, v in (params or {}).items() if v not in (None, "")}
            )
        )
        request_path = path if not query else f"{path}?{query}"
        request_url = self._rest_base_url() + request_path
        body_text = (
            ""
            if body is None
            else json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        )

        own_client = self._external_client is None
        client = self._external_client or httpx.AsyncClient(
            base_url=self._rest_base_url(),
            timeout=httpx.Timeout(self._timeout_seconds()),
            trust_env=False,
            follow_redirects=False,
        )
        attempts = 1 if write else self._read_max_retries() + 1
        last_error: Exception | None = None
        try:
            for attempt in range(attempts):
                try:
                    # Generate a fresh timestamp and signature for every read retry.
                    # Write operations are single-attempt and therefore cannot be
                    # duplicated by this client after an ambiguous transport error.
                    headers = self._headers(
                        method=method,
                        request_path=request_path,
                        body=body_text,
                    )
                    headers.update(
                        self._request_extra_headers(
                            method=method.upper(),
                            path=path,
                            write=write,
                        )
                    )
                    self._validate_external_client(client)
                    self._before_send(method=method, path=path)
                    # `body_text` is the same immutable string signed above and
                    # passed to HTTPX below. A caller's mutable dict cannot
                    # change the CAA timeout after this final dispatch check.
                    _validate_cancel_all_after_dispatch(
                        method=method,
                        path=path,
                        request_path=request_path,
                        body_text=body_text,
                    )
                    self._final_dispatch_check(method=method, path=path)
                    response = await client.request(
                        method.upper(),
                        request_url,
                        headers=headers,
                        content=body_text if body is not None else None,
                        auth=None,
                        follow_redirects=False,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    response_shape_code = "ambiguous_response" if write else None
                    if not isinstance(payload, dict):
                        raise OkxPrivateApiError(
                            "OKX private API returned a non-object response",
                            code=response_shape_code,
                        )
                    code = str(payload.get("code", ""))
                    if code != "0":
                        raise OkxPrivateApiError(
                            payload.get("msg") or "OKX private API rejected request",
                            code=code or None,
                            data=payload.get("data"),
                        )
                    data = payload.get("data")
                    if not isinstance(data, list):
                        raise OkxPrivateApiError(
                            "OKX private API returned non-list data",
                            code=response_shape_code,
                        )
                    typed_data = [dict(item) for item in data if isinstance(item, dict)]
                    if len(typed_data) != len(data):
                        raise OkxPrivateApiError(
                            "OKX private API returned invalid data items",
                            code=response_shape_code,
                        )
                    if write:
                        if not typed_data:
                            raise OkxPrivateApiError(
                                "OKX private API returned empty write data",
                                code="ambiguous_response",
                            )
                        for item in typed_data:
                            item_code = str(item.get("sCode", "0") or "0")
                            if item_code != "0":
                                raise OkxPrivateApiError(
                                    item.get("sMsg") or "OKX rejected write operation",
                                    code=item_code,
                                    data=typed_data,
                                )
                    return typed_data
                except OkxPrivateApiError:
                    raise
                except (httpx.HTTPError, ValueError, TypeError) as exc:
                    last_error = exc
                    if attempt + 1 >= attempts:
                        break
                    await asyncio.sleep(0.25 * (2**attempt))
            raise OkxPrivateApiError(
                f"OKX private API unavailable: {last_error.__class__.__name__ if last_error else 'unknown'}",
                code="transport_error",
            )
        finally:
            if own_client:
                await client.aclose()

    async def _cursor_chain(
        self,
        path: str,
        *,
        params: dict[str, Any],
        cursor_field: str,
        page_size: int = 100,
        max_pages: int = 16,
    ) -> list[dict[str, Any]]:
        """Read an ordered cursor chain through an explicit empty terminal page."""
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()
        after = None
        for _ in range(max_pages):
            query = dict(params)
            query["limit"] = str(page_size)
            if after is not None:
                query["after"] = after
            page = await self._request("GET", path, params=query)
            if not page:
                return collected
            if len(page) > page_size:
                raise OkxPrivateApiError(
                    "OKX private cursor page exceeds its declared limit",
                    code="pagination_incomplete",
                )
            identities = [row.get(cursor_field) for row in page]
            if any(
                type(value) is not str
                or re.fullmatch(r"[1-9][0-9]{0,39}", value) is None
                for value in identities
            ):
                raise OkxPrivateApiError(
                    "OKX private cursor identity is missing or invalid",
                    code="pagination_incomplete",
                )
            numeric = [int(value) for value in identities]
            if any(left <= right for left, right in itertools.pairwise(numeric)):
                raise OkxPrivateApiError(
                    "OKX private cursor page is not newest-first",
                    code="pagination_incomplete",
                )
            if after is not None and any(value >= int(after) for value in numeric):
                raise OkxPrivateApiError(
                    "OKX private cursor did not advance",
                    code="pagination_incomplete",
                )
            if any(value in seen for value in identities):
                raise OkxPrivateApiError(
                    "OKX private cursor chain contains duplicate identities",
                    code="pagination_incomplete",
                )
            seen.update(identities)
            collected.extend(page)
            after = identities[-1]
        raise OkxPrivateApiError(
            "OKX private cursor chain did not reach an empty terminal page",
            code="pagination_incomplete",
        )

    async def account_config(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/v5/account/config")

    async def balance(self, currency: str | None = None) -> list[dict[str, Any]]:
        return await self._request(
            "GET", "/api/v5/account/balance", params={"ccy": currency}
        )

    async def positions(self, instrument_id: str | None = None) -> list[dict[str, Any]]:
        params = {} if instrument_id is None else {"instId": instrument_id}
        return await self._request(
            "GET",
            "/api/v5/account/positions",
            params=params,
        )

    async def pending_orders(
        self, instrument_id: str | None = None
    ) -> list[dict[str, Any]]:
        params = {} if instrument_id is None else {"instId": instrument_id}
        return await self._cursor_chain(
            "/api/v5/trade/orders-pending",
            params=params,
            cursor_field="ordId",
            page_size=_PRIVATE_PAGE_SIZE,
            max_pages=_PRIVATE_MAX_PAGES,
        )

    async def order_history(
        self, instrument_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/api/v5/trade/orders-history",
            params={"instType": "SWAP", "instId": instrument_id, "limit": str(limit)},
        )

    async def pending_algo_orders(
        self, instrument_id: str | None = None
    ) -> list[dict[str, Any]]:
        path = "/api/v5/trade/orders-algo-pending"
        rows = []
        seen: set[str] = set()
        for order_type in _ALGO_ORDER_TYPES:
            current = await self._cursor_chain(
                path,
                params={"ordType": order_type, "instId": instrument_id},
                cursor_field="algoId",
                page_size=_PRIVATE_PAGE_SIZE,
                max_pages=_PRIVATE_MAX_PAGES,
            )
            for row in current:
                identity = row["algoId"]
                if identity in seen:
                    raise OkxPrivateApiError(
                        "OKX pending algo identity conflicts across order types",
                        code="pagination_incomplete",
                    )
                seen.add(identity)
            rows.extend(current)
        return rows

    async def order_detail(
        self,
        instrument_id: str,
        *,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/api/v5/trade/order",
            params={
                "instId": instrument_id,
                "ordId": order_id,
                "clOrdId": client_order_id,
            },
        )

    async def max_order_size(
        self,
        instrument_id: str,
        *,
        margin_mode: str,
        price: str | None = None,
        leverage: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/api/v5/account/max-size",
            params={
                "instId": instrument_id,
                "tdMode": margin_mode,
                "px": price,
                "leverage": leverage,
            },
        )

    async def order_precheck(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request(
            "POST",
            "/api/v5/trade/order-precheck",
            body=payload,
            write=True,
        )

    async def place_order(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request(
            "POST", "/api/v5/trade/order", body=payload, write=True
        )

    async def cancel_order(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request(
            "POST", "/api/v5/trade/cancel-order", body=payload, write=True
        )

    async def close_position(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request(
            "POST", "/api/v5/trade/close-position", body=payload, write=True
        )

    async def set_leverage(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request(
            "POST", "/api/v5/account/set-leverage", body=payload, write=True
        )

    async def cancel_all_after(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request(
            "POST",
            "/api/v5/trade/cancel-all-after",
            body=payload,
            write=True,
        )


class OkxDemoPrivateRestClient(_OkxPrivateRestClientBase):
    """Authenticated OKX Demo REST client with simulation permanently enabled."""

    def _before_send(self, *, method: str, path: str) -> None:
        enforce_demo_submission_boundary(method, path)

    def _credentials(self) -> tuple[str, str, str]:
        if not self.settings.okx_demo_credentials_configured:
            raise OkxPrivateApiError(
                "OKX Demo credentials are not configured",
                code="credentials_missing",
            )
        return (
            self.settings.okx_demo_api_key.get_secret_value(),
            self.settings.okx_demo_api_secret.get_secret_value(),
            self.settings.okx_demo_api_passphrase.get_secret_value(),
        )

    def _rest_base_url(self) -> str:
        return _validated_origin(
            self.settings.okx_demo_rest_base_url,
            frozenset(
                {
                    "openapi.okx.com",
                    "www.okx.com",
                    "us.okx.com",
                    "eea.okx.com",
                    "tr.okx.com",
                }
            ),
        )

    def _timeout_seconds(self) -> float:
        return self.settings.okx_demo_timeout_seconds

    def _read_max_retries(self) -> int:
        return self.settings.okx_demo_read_max_retries

    def _extra_headers(self) -> dict[str, str]:
        return {"x-simulated-trading": "1"}


class OkxLivePrivateRestClient(_OkxPrivateRestClientBase):
    """Authenticated OKX Production REST client with writes hard-blocked."""

    def _before_send(self, *, method: str, path: str) -> None:
        # Keep the read-only invariant in the shared transport's final check.
        # Explicit base-method dispatch must not bypass this client's _request.
        if type(method) is not str or method.upper() != "GET":
            raise OkxPrivateApiError(
                "OKX Live write operations are disabled",
                code="live_writes_disabled",
            )

    def _credentials(self) -> tuple[str, str, str]:
        if not self.settings.okx_live_credentials_configured:
            raise OkxPrivateApiError(
                "OKX Live credentials are not configured",
                code="credentials_missing",
            )
        return (
            self.settings.okx_live_api_key.get_secret_value(),
            self.settings.okx_live_api_secret.get_secret_value(),
            self.settings.okx_live_api_passphrase.get_secret_value(),
        )

    def _rest_base_url(self) -> str:
        return _validated_origin(
            self.settings.okx_live_rest_base_url,
            frozenset({"openapi.okx.com", "eea.okx.com"}),
        )

    def _timeout_seconds(self) -> float:
        return self.settings.okx_live_timeout_seconds

    def _read_max_retries(self) -> int:
        return self.settings.okx_live_read_max_retries

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        write: bool = False,
    ) -> list[dict[str, Any]]:
        if write or method.upper() != "GET":
            raise OkxPrivateApiError(
                "OKX Live write operations are disabled",
                code="live_writes_disabled",
            )
        return await super()._request(
            method,
            path,
            params=params,
            body=body,
            write=False,
        )


class OkxLiveExecutionRestClient(_OkxPrivateRestClientBase):
    """Production write transport reachable only through explicit Live gates.

    This class never sets the simulated-trading header and never retries a
    write. The separate read-only Live client remains permanently unable to
    send non-GET requests.
    """

    def _ensure_write_configuration(self) -> None:
        if not (
            self.settings.environment == "production"
            and self.settings.trading_mode == "live"
            and self.settings.okx_live_enabled
            and self.settings.live_trading
            and self.settings.okx_live_allow_order_writes
            and self.settings.web_concurrency == 1
        ):
            raise OkxPrivateApiError(
                "OKX Live execution transport is not enabled",
                code="live_execution_not_enabled",
            )

    def _before_send(self, *, method: str, path: str) -> None:
        if method.upper() != "GET":
            self._ensure_write_configuration()
        enforce_live_submission_boundary(method, path)

    def _final_dispatch_check(self, *, method: str, path: str) -> None:
        self._before_send(method=method, path=path)
        enforce_live_final_dispatch(method, path)

    def _credentials(self) -> tuple[str, str, str]:
        if not self.settings.okx_live_credentials_configured:
            raise OkxPrivateApiError(
                "OKX Live credentials are not configured",
                code="credentials_missing",
            )
        return (
            self.settings.okx_live_api_key.get_secret_value(),
            self.settings.okx_live_api_secret.get_secret_value(),
            self.settings.okx_live_api_passphrase.get_secret_value(),
        )

    def _rest_base_url(self) -> str:
        return _validated_origin(
            self.settings.okx_live_rest_base_url,
            frozenset({"openapi.okx.com", "eea.okx.com"}),
        )

    def _timeout_seconds(self) -> float:
        return self.settings.okx_live_timeout_seconds

    def _read_max_retries(self) -> int:
        return self.settings.okx_live_read_max_retries

    def _request_extra_headers(
        self, *, method: str, path: str, write: bool
    ) -> dict[str, str]:
        if write and method == "POST" and path == "/api/v5/trade/order":
            now = self._clock()
            if now.tzinfo is None:
                now = now.replace(tzinfo=UTC)
            expiry = int(now.astimezone(UTC).timestamp() * 1000)
            expiry += self.settings.okx_live_order_expiry_milliseconds
            return {"expTime": str(expiry)}
        return {}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        write: bool = False,
    ) -> list[dict[str, Any]]:
        if write or method.upper() != "GET":
            self._ensure_write_configuration()
            return await super()._request(
                method,
                path,
                params=params,
                body=body,
                write=True,
            )
        return await super()._request(
            method,
            path,
            params=params,
            body=body,
            write=False,
        )
