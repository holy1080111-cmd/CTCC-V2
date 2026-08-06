from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.operations import AuditLog, SystemEvent
from app.database.models.persistence import (
    OrchestratorFingerprintState,
    OrchestratorRunState,
    PaperAccountState,
    PaperOrderState,
    PaperPositionState,
    RecoveryCheckpoint,
)
from app.domain.orchestrator import OrchestratorRunResult
from app.domain.paper import (
    PaperAccountView,
    PaperOrderView,
    PaperPositionView,
    PaperStateView,
)
from app.domain.recovery import AuditEntryView


def _canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def durable_state_payload(state: PaperStateView) -> dict[str, Any]:
    """Return the restart-critical paper state in a deterministic form.

    Runtime-only fields such as mark price, unrealized PnL, equity and derived
    counters are deliberately excluded. They can change immediately after a
    market tick and are recalculated by PaperBroker after recovery. Orders and
    positions are sorted by UUID so checksum equality does not depend on list
    or database row order.
    """

    account = state.account.model_dump(
        mode="python",
        include={"starting_balance", "cash_balance"},
    )
    orders = [
        order.model_dump(mode="python")
        for order in sorted(state.orders, key=lambda item: str(item.id))
    ]
    positions = [
        position.model_dump(
            mode="python",
            exclude={"mark_price", "unrealized_pnl"},
        )
        for position in sorted(state.positions, key=lambda item: str(item.id))
    ]
    return {
        "checksum_version": 2,
        "account": account,
        "orders": orders,
        "positions": positions,
    }


def state_checksum(state: PaperStateView) -> str:
    payload = _canonical(durable_state_payload(state))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PersistenceRepository:
    """PostgreSQL persistence boundary for paper state and orchestrator recovery."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def save_paper_state(
        self,
        state: PaperStateView,
        *,
        action: str,
        resource_id: str | None = None,
        actor: str = "ctcc-system",
        details: dict[str, Any] | None = None,
    ) -> str:
        checksum = state_checksum(state)
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            async with session.begin():
                account_values = {
                    "id": 1,
                    "starting_balance": state.account.starting_balance,
                    "cash_balance": state.account.cash_balance,
                    "equity": state.account.equity,
                    "realized_pnl": state.account.realized_pnl,
                    "unrealized_pnl": state.account.unrealized_pnl,
                    "fees_paid": state.account.fees_paid,
                    "open_positions": state.account.open_positions,
                    "pending_orders": state.account.pending_orders,
                    "closed_trades": state.account.closed_trades,
                    "revision": 1,
                    "state_checksum": checksum,
                    "persisted_at": now,
                }
                account_insert = pg_insert(PaperAccountState).values(**account_values)
                await session.execute(
                    account_insert.on_conflict_do_update(
                        index_elements=[PaperAccountState.id],
                        set_={
                            **{key: value for key, value in account_values.items() if key != "id"},
                            "revision": PaperAccountState.revision + 1,
                        },
                    )
                )

                order_ids = [order.id for order in state.orders]
                position_ids = [position.id for position in state.positions]

                if position_ids:
                    await session.execute(
                        delete(PaperPositionState).where(PaperPositionState.id.not_in(position_ids))
                    )
                else:
                    await session.execute(delete(PaperPositionState))

                if order_ids:
                    await session.execute(
                        delete(PaperOrderState).where(PaperOrderState.id.not_in(order_ids))
                    )
                else:
                    await session.execute(delete(PaperOrderState))

                for order in state.orders:
                    values = {
                        "id": order.id,
                        "client_order_id": order.client_order_id,
                        "symbol": order.symbol,
                        "side": order.side,
                        "order_type": order.order_type,
                        "status": order.status,
                        "quantity": order.quantity,
                        "reference_price": order.reference_price,
                        "limit_price": order.limit_price,
                        "average_fill_price": order.average_fill_price,
                        "stop_loss": order.stop_loss,
                        "take_profit": order.take_profit,
                        "fee": order.fee,
                        "strategy": order.strategy,
                        "score": order.score,
                        "reasons": order.reasons,
                        "created_at": order.created_at,
                        "filled_at": order.filled_at,
                        "persisted_at": now,
                    }
                    stmt = pg_insert(PaperOrderState).values(**values)
                    await session.execute(
                        stmt.on_conflict_do_update(
                            index_elements=[PaperOrderState.id],
                            set_={key: value for key, value in values.items() if key != "id"},
                        )
                    )

                for position in state.positions:
                    values = {
                        "id": position.id,
                        "order_id": position.order_id,
                        "symbol": position.symbol,
                        "side": position.side,
                        "status": position.status,
                        "quantity": position.quantity,
                        "entry_price": position.entry_price,
                        "mark_price": position.mark_price,
                        "stop_loss": position.stop_loss,
                        "take_profit": position.take_profit,
                        "unrealized_pnl": position.unrealized_pnl,
                        "realized_pnl": position.realized_pnl,
                        "fees": position.fees,
                        "opened_at": position.opened_at,
                        "closed_at": position.closed_at,
                        "close_reason": position.close_reason,
                        "persisted_at": now,
                    }
                    stmt = pg_insert(PaperPositionState).values(**values)
                    await session.execute(
                        stmt.on_conflict_do_update(
                            index_elements=[PaperPositionState.id],
                            set_={key: value for key, value in values.items() if key != "id"},
                        )
                    )

                summary = {
                    "checksum": checksum,
                    "orders": len(state.orders),
                    "positions": len(state.positions),
                    "cash_balance": str(state.account.cash_balance),
                    **(details or {}),
                }
                session.add(
                    AuditLog(
                        actor=actor,
                        action=action,
                        resource_type="paper_state",
                        resource_id=resource_id,
                        before=None,
                        after=summary,
                    )
                )
                session.add(
                    SystemEvent(
                        event_type=action,
                        aggregate_type="paper_state",
                        severity="info",
                        payload=summary,
                    )
                )
                checkpoint_values = {
                    "id": 1,
                    "status": "persisted",
                    "state_checksum": checksum,
                    "details": summary,
                    "persisted_at": now,
                }
                checkpoint_insert = pg_insert(RecoveryCheckpoint).values(**checkpoint_values)
                await session.execute(
                    checkpoint_insert.on_conflict_do_update(
                        index_elements=[RecoveryCheckpoint.id],
                        set_={key: value for key, value in checkpoint_values.items() if key != "id"},
                    )
                )
        return checksum

    async def load_paper_state(self) -> PaperStateView | None:
        async with self.session_factory() as session:
            account = await session.get(PaperAccountState, 1)
            if account is None:
                return None
            orders = list(
                (await session.scalars(select(PaperOrderState).order_by(PaperOrderState.created_at))).all()
            )
            positions = list(
                (await session.scalars(select(PaperPositionState).order_by(PaperPositionState.opened_at))).all()
            )

        return PaperStateView(
            account=PaperAccountView(
                starting_balance=account.starting_balance,
                cash_balance=account.cash_balance,
                equity=account.equity,
                realized_pnl=account.realized_pnl,
                unrealized_pnl=account.unrealized_pnl,
                fees_paid=account.fees_paid,
                open_positions=account.open_positions,
                pending_orders=account.pending_orders,
                closed_trades=account.closed_trades,
            ),
            orders=[
                PaperOrderView(
                    id=row.id,
                    client_order_id=row.client_order_id,
                    symbol=row.symbol,
                    side=row.side,
                    order_type=row.order_type,
                    status=row.status,
                    quantity=row.quantity,
                    reference_price=row.reference_price,
                    limit_price=row.limit_price,
                    average_fill_price=row.average_fill_price,
                    stop_loss=row.stop_loss,
                    take_profit=row.take_profit,
                    fee=row.fee,
                    strategy=row.strategy,
                    score=row.score,
                    reasons=list(row.reasons or []),
                    created_at=row.created_at,
                    filled_at=row.filled_at,
                )
                for row in orders
            ],
            positions=[
                PaperPositionView(
                    id=row.id,
                    order_id=row.order_id,
                    symbol=row.symbol,
                    side=row.side,
                    status=row.status,
                    quantity=row.quantity,
                    entry_price=row.entry_price,
                    mark_price=row.mark_price,
                    stop_loss=row.stop_loss,
                    take_profit=row.take_profit,
                    unrealized_pnl=row.unrealized_pnl,
                    realized_pnl=row.realized_pnl,
                    fees=row.fees,
                    opened_at=row.opened_at,
                    closed_at=row.closed_at,
                    close_reason=row.close_reason,
                )
                for row in positions
            ],
        )

    async def save_orchestrator_run(self, run: OrchestratorRunResult, *, history_limit: int) -> None:
        values = {
            "run_id": run.run_id,
            "trigger": run.trigger,
            "execute": run.execute,
            "payload": run.model_dump(mode="json"),
            "started_at": run.started_at,
            "completed_at": run.completed_at,
        }
        async with self.session_factory() as session:
            async with session.begin():
                stmt = pg_insert(OrchestratorRunState).values(**values)
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[OrchestratorRunState.run_id],
                        set_={key: value for key, value in values.items() if key != "run_id"},
                    )
                )
                keep_ids = list(
                    (
                        await session.scalars(
                            select(OrchestratorRunState.run_id)
                            .order_by(OrchestratorRunState.completed_at.desc())
                            .limit(history_limit)
                        )
                    ).all()
                )
                if keep_ids:
                    await session.execute(
                        delete(OrchestratorRunState).where(OrchestratorRunState.run_id.not_in(keep_ids))
                    )

    async def load_orchestrator_runs(self, limit: int) -> list[OrchestratorRunResult]:
        async with self.session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(OrchestratorRunState)
                        .order_by(OrchestratorRunState.completed_at.desc())
                        .limit(limit)
                    )
                ).all()
            )
        rows.reverse()
        return [OrchestratorRunResult.model_validate(row.payload) for row in rows]

    async def clear_orchestrator_runs(self) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(delete(OrchestratorRunState))

    async def save_fingerprint(
        self,
        fingerprint: str,
        expires_at: datetime,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        values = {
            "fingerprint": fingerprint,
            "expires_at": expires_at,
            "details": details or {},
        }
        async with self.session_factory() as session:
            async with session.begin():
                stmt = pg_insert(OrchestratorFingerprintState).values(**values)
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[OrchestratorFingerprintState.fingerprint],
                        set_={"expires_at": expires_at, "details": details or {}},
                    )
                )

    async def load_fingerprints(self, now: datetime) -> dict[str, datetime]:
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    delete(OrchestratorFingerprintState).where(
                        OrchestratorFingerprintState.expires_at <= now
                    )
                )
                rows = list((await session.scalars(select(OrchestratorFingerprintState))).all())
        return {row.fingerprint: row.expires_at for row in rows}

    async def delete_expired_fingerprints(self, now: datetime) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    delete(OrchestratorFingerprintState).where(
                        OrchestratorFingerprintState.expires_at <= now
                    )
                )

    async def clear_fingerprints(self) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(delete(OrchestratorFingerprintState))

    async def record_dashboard_snapshot_audit(
        self,
        *,
        snapshot_id: str,
        contract_version: str,
        generated_at: datetime,
        duration_ms: int,
        complete: bool,
        failed_sources: list[str],
        timed_out_sources: list[str],
    ) -> None:
        if generated_at.tzinfo is None:
            raise ValueError(
                "dashboard_snapshot_generated_at_timezone_required"
            )

        payload = {
            "snapshot_id": str(snapshot_id),
            "contract_version": str(contract_version),
            "generated_at": (
                generated_at
                .astimezone(timezone.utc)
                .isoformat()
            ),
            "duration_ms": max(0, int(duration_ms)),
            "complete": bool(complete),
            "failed_sources": sorted(
                {
                    str(source)
                    for source in failed_sources
                }
            ),
            "timed_out_sources": sorted(
                {
                    str(source)
                    for source in timed_out_sources
                }
            ),
        }

        async with self.session_factory() as session:
            async with session.begin():
                session.add(
                    SystemEvent(
                        event_type="dashboard_snapshot_generated",
                        aggregate_type="dashboard_snapshot",
                        severity=(
                            "info"
                            if complete
                            else "warning"
                        ),
                        payload=payload,
                    )
                )
    async def mark_recovered(self, checksum: str, details: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        values = {
            "id": 1,
            "status": "recovered",
            "state_checksum": checksum,
            "details": details,
            "recovered_at": now,
        }
        async with self.session_factory() as session:
            async with session.begin():
                stmt = pg_insert(RecoveryCheckpoint).values(**values)
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[RecoveryCheckpoint.id],
                        set_={key: value for key, value in values.items() if key != "id"},
                    )
                )
                session.add(
                    SystemEvent(
                        event_type="paper_state_recovered",
                        aggregate_type="paper_state",
                        severity="info",
                        payload={"checksum": checksum, **details},
                    )
                )

    async def audit_entries(self, limit: int = 50) -> list[AuditEntryView]:
        async with self.session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(AuditLog)
                        .where(AuditLog.resource_type == "paper_state")
                        .order_by(AuditLog.occurred_at.desc())
                        .limit(limit)
                    )
                ).all()
            )
        return [
            AuditEntryView(
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                actor=row.actor,
                after=row.after,
                occurred_at=row.occurred_at,
            )
            for row in rows
        ]

    async def counts(self, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(timezone.utc)
        async with self.session_factory() as session:
            order_count = len((await session.scalars(select(PaperOrderState.id))).all())
            position_count = len((await session.scalars(select(PaperPositionState.id))).all())
            history_count = len((await session.scalars(select(OrchestratorRunState.run_id))).all())
            fingerprint_count = len(
                (
                    await session.scalars(
                        select(OrchestratorFingerprintState.fingerprint).where(
                            OrchestratorFingerprintState.expires_at > now
                        )
                    )
                ).all()
            )
        return {
            "orders": order_count,
            "positions": position_count,
            "history": history_count,
            "fingerprints": fingerprint_count,
        }
