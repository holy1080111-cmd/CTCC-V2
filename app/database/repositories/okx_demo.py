from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import get_settings
from app.database.models.okx_demo import (
    OkxDemoAlgoOrderState,
    OkxDemoBalanceState,
    OkxDemoOrderState,
    OkxDemoPositionState,
    OkxDemoSyncCheckpoint,
)
from app.database.models.operations import AuditLog, SystemEvent
from app.database.models.performance import DemoPerformanceSnapshot
from app.domain.okx_demo import (
    OkxDemoAccountConfig,
    OkxDemoAlgoOrderView,
    OkxDemoBalanceSnapshot,
    OkxDemoMirrorStatus,
    OkxDemoOrderView,
    OkxDemoPositionView,
)
from app.okx_demo.equity import resolve_demo_risk_capital

_TERMINAL_ORDER_STATES = ("filled", "canceled", "mmp_canceled")
_CAPTURE_LOCK_KEY = 71062020260001
_UNRESOLVED_ORDER_ERRORS = frozenset(
    {
        "okx_demo_order_write_requires_reconcile",
        "okx_demo_order_mirror_failure",
    }
)


@dataclass(slots=True, eq=False)
class _DemoCaptureLease:
    repository: OkxDemoRepository
    session: AsyncSession
    task: asyncio.Task[Any]
    backend_pid: int
    transaction_id: int
    active: bool = True

    async def verify_current(self, repository: OkxDemoRepository) -> None:
        if (
            not self.active
            or self.repository is not repository
            or self.task is not asyncio.current_task()
            or _ACTIVE_DEMO_CAPTURE.get() is not self
        ):
            raise RuntimeError("okx_demo_capture_lease_invalid")
        row = (
            await self.session.execute(text("SELECT pg_backend_pid(), txid_current()"))
        ).one()
        if (row[0], row[1]) != (self.backend_pid, self.transaction_id):
            raise RuntimeError("okx_demo_capture_transaction_changed")


_ACTIVE_DEMO_CAPTURE: ContextVar[_DemoCaptureLease | None] = ContextVar(
    "ctcc_okx_demo_capture_lease", default=None
)


class OkxDemoRepository:
    """PostgreSQL mirror for current OKX Demo account state.

    This is a mirror, not the source of truth. Reconciliation always treats
    OKX Demo as authoritative for exchange orders and positions.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    @asynccontextmanager
    async def capture_lease(self) -> AsyncIterator[None]:
        """Serialize the complete singleton capture before its first private GET.

        The same PostgreSQL transaction is used for the mirror write. A crash
        rolls back the write and releases this transaction-scoped lock.
        """
        task = asyncio.current_task()
        if task is None or _ACTIVE_DEMO_CAPTURE.get() is not None:
            raise RuntimeError("okx_demo_capture_lease_nested")
        async with self.session_factory() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '15s'"))
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": _CAPTURE_LOCK_KEY},
            )
            row = (
                await session.execute(text("SELECT pg_backend_pid(), txid_current()"))
            ).one()
            lease = _DemoCaptureLease(self, session, task, row[0], row[1])
            token = _ACTIVE_DEMO_CAPTURE.set(lease)
            try:
                await lease.verify_current(self)
                yield
                await lease.verify_current(self)
            finally:
                lease.active = False
                _ACTIVE_DEMO_CAPTURE.reset(token)

    async def sync_snapshot(
        self,
        *,
        account_config: OkxDemoAccountConfig,
        balance: OkxDemoBalanceSnapshot,
        positions: list[OkxDemoPositionView],
        orders: list[OkxDemoOrderView],
        algo_orders: list[OkxDemoAlgoOrderView],
        credential_session_binding_id: str | None = None,
    ) -> OkxDemoMirrorStatus:
        lease = _ACTIVE_DEMO_CAPTURE.get()
        if lease is None:
            raise RuntimeError("okx_demo_capture_lease_required")
        await lease.verify_current(self)
        if credential_session_binding_id is not None and (
            type(credential_session_binding_id) is not str
            or len(credential_session_binding_id) != 64
            or any(
                char not in "0123456789abcdef" for char in credential_session_binding_id
            )
        ):
            raise ValueError("okx_demo_credential_session_binding_invalid")
        now = datetime.now(UTC)
        unique_orders = self._unique_orders(orders)
        deduped_positions = {
            item.position_key: item
            for item in positions
            if item.instrument_id and item.size != 0
        }
        deduped_algo_orders = {
            item.algo_order_id: item for item in algo_orders if item.algo_order_id
        }
        performance_capital, performance_blocker = resolve_demo_risk_capital(
            account_config,
            balance,
        )

        session = lease.session
        async with session.begin_nested():
            # B5 takes SHARE locks in this order. Hold stronger locks for
            # the complete mirror transaction so two captures cannot
            # publish a stale active count or race an order-state check.
            await session.execute(
                text(
                    "LOCK TABLE okx_demo_sync_checkpoints, okx_demo_order_state, "
                    "okx_demo_position_state, okx_demo_algo_order_state "
                    "IN SHARE ROW EXCLUSIVE MODE"
                )
            )
            existing_checkpoint = await session.get(OkxDemoSyncCheckpoint, 1)
            unresolved_order_error = (
                existing_checkpoint.last_error
                if existing_checkpoint is not None
                and existing_checkpoint.last_error in _UNRESOLVED_ORDER_ERRORS
                else None
            )
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

            for order in unique_orders.values():
                await self._upsert_order_locked(session, order, now)

            # Recent terminal orders remain in the mirror for forensic
            # history. Count every still nonterminal mirrored order for
            # the B5 checkpoint, including one absent from a later bounded
            # history page; absence alone cannot prove it was closed.
            active_order_count = await session.scalar(
                select(func.count())
                .select_from(OkxDemoOrderState)
                .where(OkxDemoOrderState.state.not_in(_TERMINAL_ORDER_STATES))
            )
            if active_order_count is None:
                raise RuntimeError("okx_demo_active_order_count_unknown")
            uid = account_config.uid
            uid_bound = (
                type(uid) is str
                and uid.isascii()
                and uid.isdigit()
                and 1 <= len(uid) <= 32
                and type(account_config.raw) is dict
                and account_config.raw.get("uid") == uid
            )
            details = {
                "position_mode": account_config.position_mode,
                "account_level": account_config.account_level,
                "total_equity": str(balance.total_equity),
                "available_equity": str(balance.available_equity),
                "orders_observed_in_snapshot": len(unique_orders),
                "account_uid_sha256": (
                    hashlib.sha256(uid.encode("ascii")).hexdigest()
                    if uid_bound
                    else None
                ),
                # Random per-capture nonce; no credential or key digest is stored.
                "credential_session_binding_id": credential_session_binding_id,
            }
            checkpoint_values = {
                "id": 1,
                # A directly observed order may have followed an exchange
                # acknowledgement. A later bounded account read must not
                # silently erase that durable uncertainty.
                "status": "error" if unresolved_order_error else "reconciled",
                "order_count": active_order_count,
                "position_count": len(deduped_positions),
                "algo_order_count": len(deduped_algo_orders),
                "details": details,
                "last_error": unresolved_order_error,
                "reconciled_at": None if unresolved_order_error else now,
            }
            checkpoint_stmt = pg_insert(OkxDemoSyncCheckpoint).values(
                **checkpoint_values
            )
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
                        "orders": len(unique_orders),
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
                        "orders": len(unique_orders),
                        "positions": len(deduped_positions),
                        "algo_orders": len(deduped_algo_orders),
                    },
                )
            )
            unrealized_pnl = sum(
                (item.unrealized_pnl for item in balance.details),
                start=Decimal(0),
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
                    pending_order_count=len(
                        [
                            item
                            for item in unique_orders.values()
                            if item.state in {"live", "partially_filled"}
                        ]
                    ),
                    algo_order_count=len(deduped_algo_orders),
                    details={
                        "source": "okx_demo_reconcile",
                        "position_mode": account_config.position_mode,
                        "account_level": account_config.account_level,
                        "performance_equity_blocker": (performance_blocker or None),
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
        await lease.verify_current(self)
        return await self.mirror_status(session=session)

    async def upsert_orders(
        self, orders: list[OkxDemoOrderView], *, action: str
    ) -> None:
        if _ACTIVE_DEMO_CAPTURE.get() is not None:
            raise RuntimeError("okx_demo_order_write_inside_capture_denied")
        now = datetime.now(UTC)
        try:
            unique_orders = self._unique_orders(orders)
            if not unique_orders:
                return
            # Commit revocation before changing a directly observed order.
            # A process crash during the following transaction must not leave
            # a formerly flat checkpoint eligible for reuse.
            await self.mark_failure("okx_demo_order_write_requires_reconcile")
            async with self.session_factory() as session, session.begin():
                await session.execute(text("SET LOCAL lock_timeout = '15s'"))
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": _CAPTURE_LOCK_KEY},
                )
                # B5 reads checkpoint before orders under SHARE locks. Keep
                # that same table order or the reader and this writer can
                # deadlock after an exchange acknowledgement.
                await session.execute(
                    text(
                        "LOCK TABLE okx_demo_sync_checkpoints, "
                        "okx_demo_order_state IN SHARE ROW EXCLUSIVE MODE"
                    )
                )
                for order in unique_orders.values():
                    await self._upsert_order_locked(session, order, now)
                # An acknowledged fill may create exposure before the next
                # positions GET. No direct order update may preserve an old
                # flat/reconciled checkpoint, including a terminal state.
                await session.execute(
                    update(OkxDemoSyncCheckpoint)
                    .where(OkxDemoSyncCheckpoint.id == 1)
                    .values(
                        status="error",
                        last_error="okx_demo_order_write_requires_reconcile",
                        updated_at=now,
                    )
                )
                session.add(
                    AuditLog(
                        actor="ctcc-system",
                        action=action,
                        resource_type="okx_demo_order",
                        resource_id=next(iter(unique_orders)),
                        before=None,
                        after={
                            "order_ids": list(unique_orders),
                            "count": len(unique_orders),
                        },
                    )
                )
        except Exception:
            # Any failure can follow an exchange acknowledgement, including
            # a database deadlock or failed commit. Preserve the order row
            # from the last committed transaction and revoke old flat proof.
            await self.mark_failure("okx_demo_order_mirror_failure")
            raise

    async def mark_failure(self, message: str) -> None:
        if _ACTIVE_DEMO_CAPTURE.get() is not None:
            raise RuntimeError("okx_demo_failure_write_inside_capture_denied")
        now = datetime.now(UTC)
        safe_message = message[:250]
        async with self.session_factory() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '15s'"))
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": _CAPTURE_LOCK_KEY},
            )
            previous = await session.get(OkxDemoSyncCheckpoint, 1)
            if (
                previous is not None
                and previous.last_error in _UNRESOLVED_ORDER_ERRORS
                and safe_message not in _UNRESOLVED_ORDER_ERRORS
            ):
                safe_message = previous.last_error
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

    async def mirror_status(
        self, *, session: AsyncSession | None = None
    ) -> OkxDemoMirrorStatus:
        if session is None:
            async with self.session_factory() as owned_session:
                return await self.mirror_status(session=owned_session)
        checkpoint = await session.get(OkxDemoSyncCheckpoint, 1)
        order_count = int(
            (await session.scalar(select(func.count()).select_from(OkxDemoOrderState)))
            or 0
        )
        position_count = int(
            (
                await session.scalar(
                    select(func.count()).select_from(OkxDemoPositionState)
                )
            )
            or 0
        )
        algo_count = int(
            (
                await session.scalar(
                    select(func.count()).select_from(OkxDemoAlgoOrderState)
                )
            )
            or 0
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
        if order.updated_at is not None and (
            order.updated_at.tzinfo is None or order.updated_at.utcoffset() is None
        ):
            raise ValueError("okx_demo_order_update_time_invalid")
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
    def _unique_orders(
        orders: list[OkxDemoOrderView],
    ) -> dict[str, OkxDemoOrderView]:
        unique: dict[str, OkxDemoOrderView] = {}
        for order in orders:
            if type(order.order_id) is not str or not order.order_id:
                raise ValueError("okx_demo_order_identity_missing")
            if order.order_id in unique:
                raise ValueError("okx_demo_order_identity_duplicate")
            unique[order.order_id] = order
        return unique

    async def _upsert_order_locked(
        self, session: AsyncSession, order: OkxDemoOrderView, now: datetime
    ) -> None:
        values = self._order_values(order, now)
        previous = await session.get(OkxDemoOrderState, order.order_id)
        if previous is not None:
            changed = any(
                getattr(previous, key) != value
                for key, value in values.items()
                if key not in {"order_id", "persisted_at"}
            )
            if not changed:
                return
            previous_time = previous.exchange_updated_at
            current_time = order.updated_at
            if (
                previous_time is None
                or current_time is None
                or current_time <= previous_time
            ):
                raise ValueError("okx_demo_order_revision_unproven")
        stmt = pg_insert(OkxDemoOrderState).values(**values)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[OkxDemoOrderState.order_id],
                set_={k: v for k, v in values.items() if k != "order_id"},
            )
        )

    @staticmethod
    def _position_row(
        position: OkxDemoPositionView, now: datetime
    ) -> OkxDemoPositionState:
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
