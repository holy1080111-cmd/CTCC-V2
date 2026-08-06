from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.domain.realtime import RealtimeBookLevel, RealtimeSnapshot
from app.paper.execution_service import PaperPersistenceError

logger = logging.getLogger(__name__)


class RealtimeMarketHub:
    def __init__(self, *, paper_auto_ticks: bool, paper_execution=None) -> None:
        self.paper_auto_ticks = paper_auto_ticks
        if paper_execution is None:
            from app.paper.service import paper_service
            paper_execution = paper_service
        self.paper_execution = paper_execution
        self._snapshots: dict[str, RealtimeSnapshot] = {}
        self._lock = asyncio.Lock()

    async def apply(self, event: dict[str, Any]) -> RealtimeSnapshot:
        symbol = str(event["symbol"])
        now = datetime.now(timezone.utc)
        tick_price: Decimal | None = event.get("last")

        async with self._lock:
            current = self._snapshots.get(symbol) or RealtimeSnapshot(symbol=symbol)
            updates: dict[str, Any] = {
                key: value for key, value in event.items()
                if key in RealtimeSnapshot.model_fields and value is not None
            }
            if "bids" in event:
                updates["best_bids"] = [RealtimeBookLevel(**level) for level in event["bids"]]
            if "asks" in event:
                updates["best_asks"] = [RealtimeBookLevel(**level) for level in event["asks"]]
            updates["received_at"] = now
            updates["sequence"] = current.sequence + 1
            snapshot = current.model_copy(update=updates)
            self._snapshots[symbol] = snapshot

        # Keep the paper broker outside the asyncio lock. It has its own lock.
        if self.paper_auto_ticks and tick_price is not None:
            try:
                await self.paper_execution.tick(
                    symbol=symbol,
                    price=tick_price,
                    timestamp=event.get("exchange_timestamp") or now,
                )
            except PaperPersistenceError:
                # Keep market data alive. The paper service already rolled back
                # the mutation and exposes the persistence error via recovery status.
                logger.exception("paper_tick_persistence_failed symbol=%s", symbol)
        return snapshot

    async def snapshot(self, symbol: str) -> RealtimeSnapshot | None:
        async with self._lock:
            return self._snapshots.get(symbol)

    async def snapshots(self) -> list[RealtimeSnapshot]:
        async with self._lock:
            return list(self._snapshots.values())

    async def clear(self) -> None:
        async with self._lock:
            self._snapshots.clear()
