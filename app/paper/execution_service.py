from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable, TypeVar
from uuid import UUID

from app.database.repositories.persistence import PersistenceRepository, state_checksum
from app.domain.paper import (
    PaperAccountView,
    PaperOrderRequest,
    PaperOrderView,
    PaperPositionView,
    PaperStateView,
    PaperTickResult,
)
from app.domain.recovery import RecoveryStatus
from app.paper.engine import PaperBroker

T = TypeVar("T")


class PaperPersistenceError(RuntimeError):
    pass


class PaperExecutionService:
    """Atomic paper mutations with optional PostgreSQL persistence.

    The deterministic broker remains synchronous and storage-agnostic. This
    service serializes mutations, persists state, and restores the previous
    in-memory snapshot when persistence fails.
    """

    def __init__(
        self,
        broker: PaperBroker,
        repository: PersistenceRepository | None = None,
        *,
        persist_mark_interval_seconds: int = 30,
    ) -> None:
        self.broker = broker
        self.repository = repository
        self.persist_mark_interval_seconds = max(1, persist_mark_interval_seconds)
        self._lock = asyncio.Lock()
        self._initialized = False
        self._recovered = False
        self._last_recovered_at: datetime | None = None
        self._last_persisted_at: datetime | None = None
        self._last_mark_persist_at: datetime | None = None
        self._last_error: str | None = None

    async def recover(self) -> RecoveryStatus:
        if self.repository is None:
            self._initialized = True
            self._recovered = False
            return await self.recovery_status()

        async with self._lock:
            try:
                loaded = await self.repository.load_paper_state()
                if loaded is None:
                    await self._persist("paper_state_initialized")
                    self._recovered = False
                else:
                    self.broker.restore(loaded)
                    checksum = state_checksum(loaded)
                    now = datetime.now(timezone.utc)
                    await self.repository.mark_recovered(
                        checksum,
                        {
                            "orders": len(loaded.orders),
                            "positions": len(loaded.positions),
                        },
                    )
                    self._last_recovered_at = now
                    self._recovered = True
                self._initialized = True
                self._last_error = None
            except Exception as exc:
                self._last_error = f"{exc.__class__.__name__}: {exc}"
                raise PaperPersistenceError("paper_state_recovery_failed") from exc
        return await self.recovery_status()

    async def submit(self, request: PaperOrderRequest) -> PaperOrderView:
        return await self._mutate(
            "paper_order_submitted",
            lambda: self.broker.submit(request),
            resource_id=request.client_order_id,
        )

    async def cancel(self, order_id: UUID) -> PaperOrderView:
        return await self._mutate(
            "paper_order_cancelled",
            lambda: self.broker.cancel(order_id),
            resource_id=str(order_id),
        )

    async def tick(
        self,
        *,
        symbol: str,
        price: Decimal,
        timestamp: datetime | None = None,
    ) -> PaperTickResult:
        async with self._lock:
            before = self.broker.state()
            result = self.broker.tick(symbol=symbol, price=price, timestamp=timestamp)
            now = datetime.now(timezone.utc)
            material = bool(result.filled_order_ids or result.closed_position_ids)
            interval_due = (
                self._last_mark_persist_at is None
                or (now - self._last_mark_persist_at).total_seconds()
                >= self.persist_mark_interval_seconds
            )
            has_open_positions = result.account.open_positions > 0
            if self.repository is not None and (material or (has_open_positions and interval_due)):
                try:
                    await self._persist(
                        "paper_tick_applied",
                        details={
                            "symbol": symbol,
                            "price": str(price),
                            "filled_order_ids": [str(item) for item in result.filled_order_ids],
                            "closed_position_ids": [str(item) for item in result.closed_position_ids],
                        },
                    )
                    self._last_mark_persist_at = now
                except Exception as exc:
                    self.broker.restore(before)
                    self._last_error = f"{exc.__class__.__name__}: {exc}"
                    raise PaperPersistenceError("paper_tick_persistence_failed") from exc
            return result

    async def close(self, position_id: UUID, *, price: Decimal, reason: str = "manual") -> PaperPositionView:
        return await self._mutate(
            "paper_position_closed",
            lambda: self.broker.close(position_id, price=price, reason=reason),
            resource_id=str(position_id),
            details={"reason": reason, "price": str(price)},
        )

    async def reset(self) -> PaperStateView:
        return await self._mutate("paper_state_reset", self.broker.reset)

    async def persist_now(self, action: str = "paper_state_checkpoint") -> str | None:
        if self.repository is None:
            return None
        async with self._lock:
            return await self._persist(action)

    async def reconcile(self, action: str) -> RecoveryStatus:
        if action == "verify":
            return await self.recovery_status()
        if self.repository is None:
            raise PaperPersistenceError("paper_persistence_disabled")
        async with self._lock:
            if action == "restore_from_database":
                loaded = await self.repository.load_paper_state()
                if loaded is None:
                    raise PaperPersistenceError("persisted_paper_state_not_found")
                self.broker.restore(loaded)
                self._last_recovered_at = datetime.now(timezone.utc)
                self._recovered = True
            elif action == "persist_memory":
                await self._persist("paper_state_manual_reconcile")
            else:
                raise PaperPersistenceError("unsupported_recovery_action")
        return await self.recovery_status()

    async def recovery_status(self) -> RecoveryStatus:
        memory = self.broker.state()
        memory_sum = state_checksum(memory)
        database_sum: str | None = None
        counts = {
            "orders": len(memory.orders),
            "positions": len(memory.positions),
            "history": 0,
            "fingerprints": 0,
        }
        if self.repository is not None:
            try:
                loaded = await self.repository.load_paper_state()
                if loaded is not None:
                    database_sum = state_checksum(loaded)
                counts = await self.repository.counts()
            except Exception as exc:
                self._last_error = f"{exc.__class__.__name__}: {exc}"
        return RecoveryStatus(
            persistence_enabled=self.repository is not None,
            initialized=self._initialized,
            recovered=self._recovered,
            consistent=(memory_sum == database_sum) if database_sum is not None else None,
            memory_checksum=memory_sum,
            database_checksum=database_sum,
            order_count=counts["orders"],
            position_count=counts["positions"],
            orchestrator_history_count=counts["history"],
            fingerprint_count=counts["fingerprints"],
            last_recovered_at=self._last_recovered_at,
            last_persisted_at=self._last_persisted_at,
            last_error=self._last_error,
            details={
                "cash_balance": str(memory.account.cash_balance),
                "open_positions": memory.account.open_positions,
                "pending_orders": memory.account.pending_orders,
            },
        )

    def get_order(self, order_id: UUID) -> PaperOrderView:
        return self.broker.get_order(order_id)

    def get_position(self, position_id: UUID) -> PaperPositionView:
        return self.broker.get_position(position_id)

    def state(self) -> PaperStateView:
        return self.broker.state()

    def account(self) -> PaperAccountView:
        return self.broker.account()

    async def _mutate(
        self,
        action: str,
        operation: Callable[[], T],
        *,
        resource_id: str | None = None,
        details: dict[str, str] | None = None,
    ) -> T:
        async with self._lock:
            before = self.broker.state()
            result = operation()
            if self.repository is not None:
                try:
                    await self._persist(
                        action,
                        resource_id=resource_id,
                        details=details,
                    )
                except Exception as exc:
                    self.broker.restore(before)
                    self._last_error = f"{exc.__class__.__name__}: {exc}"
                    raise PaperPersistenceError(f"{action}_persistence_failed") from exc
            return result

    async def _persist(
        self,
        action: str,
        *,
        resource_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> str:
        if self.repository is None:
            return state_checksum(self.broker.state())
        checksum = await self.repository.save_paper_state(
            self.broker.state(),
            action=action,
            resource_id=resource_id,
            details=details,
        )
        self._last_persisted_at = datetime.now(timezone.utc)
        self._last_error = None
        return checksum
