from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class OkxPrivateApiClient(Protocol):
    """Structural contract for authenticated OKX private REST clients."""

    async def account_config(self) -> list[dict[str, Any]]: ...

    async def balance(self, currency: str | None = None) -> list[dict[str, Any]]: ...

    async def positions(self, instrument_id: str | None = None) -> list[dict[str, Any]]: ...

    async def pending_orders(self, instrument_id: str | None = None) -> list[dict[str, Any]]: ...

    async def order_history(
        self, instrument_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]: ...

    async def pending_algo_orders(self, instrument_id: str | None = None) -> list[dict[str, Any]]: ...

    async def order_detail(
        self,
        instrument_id: str,
        *,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def max_order_size(
        self,
        instrument_id: str,
        *,
        margin_mode: str,
        price: str | None = None,
        leverage: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def order_precheck(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...

    async def place_order(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...

    async def cancel_order(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...

    async def close_position(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...

    async def set_leverage(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...

    async def cancel_all_after(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...
