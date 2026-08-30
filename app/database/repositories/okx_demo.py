from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.okx_demo import (
    OkxDemoAlgoOrderState,
    OkxDemoBalanceState,
    OkxDemoOrderState,
    OkxDemoPositionState,
    OkxDemoSyncCheckpoint,
)
from app.database.models.operations import AuditLog, SystemEvent
from app.database.models.performance import DemoPerformanceSnapshot
from app.config.settings import get_settings
from app.domain.okx_demo import (
    OkxDemoAccountConfig,
    OkxDemoAlgoOrderView,
    OkxDemoBalanceSnapshot,
    OkxDemoMirrorStatus,
    OkxDemoOrderView,
    OkxDemoPositionView,
)
from app.okx_demo.equity import resolve_demo_risk_capital


class OkxDemoRepository:
    """PostgreSQL mirror for current OKX Demo account state.

    This is a mirror, not the source of truth. Reconciliation always treats
    OKX Demo as authoritative for exchange orders and positions.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def sync_snapshot(
        self,
        *,
        account_config: OkxDemoAccountConfig,
        balance: OkxDemoBalanceSnapshot,
        positions: list[OkxDemoPositionView],
        orders: list[OkxDemoOrderView],
        algo_orders: list[OkxDemoAlgoOrderView],
    ) -> OkxDemoMirrorStatus:
        now = datetime.now(timezone.utc)
        deduped_orders = {item.order_id: item for item in orders if item.order_id}
        deduped_positions = {
            item.position_key: item for item in positions if item.instrument_id and item.size != 0
        }
        deduped_algo_orders = {
            item.algo_order_id: item for item in algo_orders if item.algo_order_id
        }
        performance_capital, performance_blocker = resolve_demo_risk_capital(
            account_config,
            balance,
        )

        async with self.session_factory() as session:
            async with session.begin():
                balance_values = {
                    "id": 1,
                    "total_equity": balance.total_equity,
                    "isolated_equity": balance.isolated_equity,
                    "adjusted_equity": balance.adjusted_equity,
                    "available_equity": balance.available_equity,
                    "details": [item.model_dump(mode="json") for item in balance.details],
                    "raw": balance.raw,
                    "captured_at": balance.captured_at,
                    "persisted_at": now,
                }
                balance_stmt = pg_insert(OkxDemoBalanceState).values(**balance_values)
                await session.execute(
                    balance_stmt.on_conflict_do_update(
                        index_elements=[OkxDemoBalanceState.id],
                        set_={k: v for k, v in balance_values.items() if k != "id"},
                    )
                )

                await session.execute(delete(OkxDemoPositionState))
                for position in deduped_positions.values():
                    session.add(self._position_row(position, now))

                await session.execute(delete(OkxDemoAlgoOrderState))
                for algo in deduped_algo_orders.values():
                    session.add(self._algo_row(algo, now))

                for order in deduped_orders.values():
                    values = self._order_values(order, now)
                    stmt = pg_insert(OkxDemoOrderState).values(**values)
                    await session.execute(
                        stmt.on_conflict_do_update(
                            index_elements=[OkxDemoOrderState.order_id],
                            set_={k: v for k, v in values.items() if k != "order_id"},
                        )
                    )

                details = {
                    "position_mode": account_config.position_mode,
                    "account_level": account_config.account_level,
                    "total_equity": str(balance.total_equity),
                    "available_equity": str(balance.available_equity),
                }
                checkpoint_values = {
                    "id": 1,
                    "status": "reconciled",
                    "order_count": len(deduped_orders),
                    "position_count": len(deduped_positions),
                    "algo_order_count": len(deduped_algo_orders),
                    "details": details,
                    "last_error": None,
                    "reconciled_at": now,
                }
                checkpoint_stmt = pg_insert(OkxDemoSyncCheckpoint).values(**checkpoint_values)
                await session.execute(
                    checkpoint_stmt.on_conflict_do_update(
                        index_elements=[OkxDemoSyncCheckpoint.id],
                        set_={k: v for k, v in checkpoint_values.items() if k != "id"},
                    )
                )
                session.add(
                    AuditLog(
                        actor="ctcc-system",
                        action="okx_demo_reconciled",
                        resource_type="okx_demo_account",
                        resource_id=None,
                        before=None,
                        after={
                            **details,
                            "orders": len(deduped_orders),
                            "positions": len(deduped_positions),
                            "algo_orders": len(deduped_algo_orders),
                        },
                    )
                )
                session.add(
                    SystemEvent(
                        event_type="okx_demo_reconciled",
                        aggregate_type="okx_demo_account",
                        severity="info",
                        payload={
                            **details,
                            "orders": len(deduped_orders),
                            "positions": len(deduped_positions),
                            "algo_orders": len(deduped_algo_orders),
                        },
                    )
                )
                unrealized_pnl = sum(
                    (item.unrealized_pnl for item in balance.details),
                    start=Decimal("0"),
                )
                session.add(
                    DemoPerformanceSnapshot(
                        captured_at=balance.captured_at,
                        total_equity=balance.total_equity,
                        available_equity=balance.available_equity,
                        performance_equity=(
                            performance_capital.risk_equity
                            if performance_capital is not None
                            else None
                        ),
                        performance_available_equity=(
                            performance_capital.available_equity
                            if performance_capital is not None
                            else None
                        ),
                        equity_basis=(
                            performance_capital.basis
                            if performance_capital is not None
                            else None
                        ),
                        equity_currency=(
                            performance_capital.currency
                            if performance_capital is not None
                            else None
                        ),
                        unrealized_pnl=unrealized_pnl,
                        position_count=len(deduped_positions),
                        pending_order_count=len([
                            item for item in deduped_orders.values()
                            if item.state in {"live", "partially_filled"}
                        ]),
                        algo_order_count=len(deduped_algo_orders),
                        details={
                            "source": "okx_demo_reconcile",
                            "position_mode": account_config.position_mode,
                            "account_level": account_config.account_level,
                            "performance_equity_blocker": (
                                performance_blocker or None
                            ),
                        },
                    )
                )
                retention_cutoff = now - timedelta(
                    days=get_settings().okx_demo_performance_snapshot_retention_days
                )
                await session.execute(
                    delete(DemoPerformanceSnapshot).where(
                        DemoPerformanceSnapshot.captured_at < retention_cutoff
                    )
                )
        return await self.mirror_status()

    async def upsert_orders(self, orders: list[OkxDemoOrderView], *, action: str) -> None:
        now = datetime.now(timezone.utc)
        valid = {item.order_id: item for item in orders if item.order_id}
        if not valid:
            return
        async with self.session_factory() as session:
            async with session.begin():
                for order in valid.values():
                    values = self._order_values(order, now)
                    stmt = pg_insert(OkxDemoOrderState).values(**values)
                    await session.execute(
                        stmt.on_conflict_do_update(
                            index_elements=[OkxDemoOrderState.order_id],
                            set_={k: v for k, v in values.items() if k != "order_id"},
                        )
                    )
                session.add(
                    AuditLog(
                        actor="ctcc-system",
                        action=action,
                        resource_type="okx_demo_order",
                        resource_id=next(iter(valid)),
                        before=None,
                        after={"order_ids": list(valid), "count": len(valid)},
                    )
                )

    async def mark_failure(self, message: str) -> None:
        now = datetime.now(timezone.utc)
        safe_message = message[:250]
        async with self.session_factory() as session:
            async with session.begin():
                values = {
                    "id": 1,
                    "status": "error",
                    "order_count": 0,
                    "position_count": 0,
                    "algo_order_count": 0,
                    "details": {},
                    "last_error": safe_message,
                    "reconciled_at": None,
                }
                stmt = pg_insert(OkxDemoSyncCheckpoint).values(**values)
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[OkxDemoSyncCheckpoint.id],
                        set_={
                            "status": "error",
                            "last_error": safe_message,
                            "updated_at": now,
                        },
                    )
                )
                session.add(
                    SystemEvent(
                        event_type="okx_demo_reconcile_failed",
                        aggregate_type="okx_demo_account",
                        severity="error",
                        payload={"error": safe_message},
                    )
                )

    async def mirror_status(self) -> OkxDemoMirrorStatus:
        async with self.session_factory() as session:
            checkpoint = await session.get(OkxDemoSyncCheckpoint, 1)
            order_count = int(
                (await session.scalar(select(func.count()).select_from(OkxDemoOrderState))) or 0
            )
            position_count = int(
                (await session.scalar(select(func.count()).select_from(OkxDemoPositionState))) or 0
            )
            algo_count = int(
                (await session.scalar(select(func.count()).select_from(OkxDemoAlgoOrderState))) or 0
            )
        if checkpoint is None:
            return OkxDemoMirrorStatus(available=False)
        return OkxDemoMirrorStatus(
            available=True,
            order_count=order_count,
            position_count=position_count,
            algo_order_count=algo_count,
            last_reconciled_at=checkpoint.reconciled_at,
            last_error=checkpoint.last_error,
            details=dict(checkpoint.details or {}),
        )

    @staticmethod
    def _order_values(order: OkxDemoOrderView, now: datetime) -> dict[str, Any]:
        return {
            "order_id": order.order_id,
            "client_order_id": order.client_order_id,
            "instrument_id": order.instrument_id,
            "side": order.side,
            "position_side": order.position_side,
            "order_type": order.order_type,
            "state": order.state,
            "size": order.size,
            "accumulated_fill_size": order.accumulated_fill_size,
            "price": order.price,
            "average_fill_price": order.average_fill_price,
            "reduce_only": order.reduce_only,
            "attached_algo_orders": order.attached_algo_orders,
            "raw": order.raw,
            "exchange_created_at": order.created_at,
            "exchange_updated_at": order.updated_at,
            "persisted_at": now,
        }

    @staticmethod
    def _position_row(position: OkxDemoPositionView, now: datetime) -> OkxDemoPositionState:
        return OkxDemoPositionState(
            position_key=position.position_key,
            instrument_id=position.instrument_id,
            position_side=position.position_side,
            size=position.size,
            available_size=position.available_size,
            average_price=position.average_price,
            mark_price=position.mark_price,
            unrealized_pnl=position.unrealized_pnl,
            leverage=position.leverage,
            margin_mode=position.margin_mode,
            liquidation_price=position.liquidation_price,
            raw=position.raw,
            exchange_created_at=position.created_at,
            exchange_updated_at=position.updated_at,
            persisted_at=now,
        )

    @staticmethod
    def _algo_row(algo: OkxDemoAlgoOrderView, now: datetime) -> OkxDemoAlgoOrderState:
        return OkxDemoAlgoOrderState(
            algo_order_id=algo.algo_order_id,
            client_algo_order_id=algo.client_algo_order_id,
            instrument_id=algo.instrument_id,
            order_type=algo.order_type,
            state=algo.state,
            side=algo.side,
            position_side=algo.position_side,
            size=algo.size,
            take_profit_trigger_price=algo.take_profit_trigger_price,
            stop_loss_trigger_price=algo.stop_loss_trigger_price,
            raw=algo.raw,
            exchange_created_at=algo.created_at,
            exchange_updated_at=algo.updated_at,
            persisted_at=now,
        )
