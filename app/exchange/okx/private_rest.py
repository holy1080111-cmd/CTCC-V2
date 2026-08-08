from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from app.config.settings import Settings, get_settings
from app.exchange.okx.errors import OkxPrivateApiError

Clock = Callable[[], datetime]


def utc_iso_timestamp(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
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
    digest = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


class OkxDemoPrivateRestClient:
    """Authenticated OKX Demo REST client.

    Every request includes x-simulated-trading: 1. Read requests have bounded
    retries; write requests are never automatically retried to avoid duplicate
    orders after ambiguous network failures.
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
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _credentials(self) -> tuple[str, str, str]:
        if not self.settings.okx_demo_credentials_configured:
            raise OkxPrivateApiError("OKX Demo credentials are not configured", code="credentials_missing")
        return (
            self.settings.okx_demo_api_key.get_secret_value(),
            self.settings.okx_demo_api_secret.get_secret_value(),
            self.settings.okx_demo_api_passphrase.get_secret_value(),
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
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"CTCC-V2/{self.settings.app_version}",
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "x-simulated-trading": "1",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        write: bool = False,
    ) -> list[dict[str, Any]]:
        query = str(httpx.QueryParams({k: v for k, v in (params or {}).items() if v not in (None, "")}))
        request_path = path if not query else f"{path}?{query}"
        body_text = "" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False)

        own_client = self._external_client is None
        client = self._external_client or httpx.AsyncClient(
            base_url=self.settings.okx_demo_rest_base_url,
            timeout=httpx.Timeout(self.settings.okx_demo_timeout_seconds),
        )
        attempts = 1 if write else self.settings.okx_demo_read_max_retries + 1
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
                    response = await client.request(
                        method.upper(),
                        request_path,
                        headers=headers,
                        content=body_text if body is not None else None,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise OkxPrivateApiError("OKX private API returned a non-object response")
                    code = str(payload.get("code", ""))
                    if code != "0":
                        raise OkxPrivateApiError(
                            payload.get("msg") or "OKX private API rejected request",
                            code=code or None,
                            data=payload.get("data"),
                        )
                    data = payload.get("data")
                    if not isinstance(data, list):
                        raise OkxPrivateApiError("OKX private API returned non-list data")
                    typed_data = [dict(item) for item in data if isinstance(item, dict)]
                    if len(typed_data) != len(data):
                        raise OkxPrivateApiError("OKX private API returned invalid data items")
                    if write:
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

    async def account_config(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/v5/account/config")

    async def balance(self, currency: str | None = None) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/v5/account/balance", params={"ccy": currency})

    async def positions(self, instrument_id: str | None = None) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/api/v5/account/positions",
            params={"instType": "SWAP", "instId": instrument_id},
        )

    async def pending_orders(self, instrument_id: str | None = None) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/api/v5/trade/orders-pending",
            params={"instType": "SWAP", "instId": instrument_id},
        )

    async def order_history(self, instrument_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/api/v5/trade/orders-history",
            params={"instType": "SWAP", "instId": instrument_id, "limit": str(limit)},
        )

    async def pending_algo_orders(self, instrument_id: str | None = None) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/api/v5/trade/orders-algo-pending",
            params={"ordType": "conditional", "instId": instrument_id},
        )

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
            params={"instId": instrument_id, "ordId": order_id, "clOrdId": client_order_id},
        )

    async def place_order(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request("POST", "/api/v5/trade/order", body=payload, write=True)

    async def cancel_order(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request("POST", "/api/v5/trade/cancel-order", body=payload, write=True)

    async def close_position(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request("POST", "/api/v5/trade/close-position", body=payload, write=True)

    async def set_leverage(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request("POST", "/api/v5/account/set-leverage", body=payload, write=True)
