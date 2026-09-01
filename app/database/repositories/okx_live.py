from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.okx_live import (
    OkxLiveAccountConfigState,
    OkxLiveAlgoOrderState,
    OkxLiveBalanceState,
    OkxLiveOrderState,
    OkxLivePositionState,
    OkxLiveSyncCheckpoint,
)
from app.domain.okx_live import (
    OkxLiveAccountConfig,
    OkxLiveAlgoOrderView,
    OkxLiveBalanceSnapshot,
    OkxLiveMirrorStatus,
    OkxLiveOrderView,
    OkxLivePositionView,
    OkxLiveSafetyLatchState,
)


_ACCOUNT_FINGERPRINT_NAMESPACE = "ctcc-okx-live-account-v1:"
_GENERIC_FAILURE_CODE = "okx_live_reconcile_failed"
_SAFE_LATCH_CODE = re.compile(r"^[a-z0-9_]{1,80}$")
_ALLOWED_FAILURE_CODES = frozenset(
    {
        _GENERIC_FAILURE_CODE,
        "okx_live_account_config_empty",
        "okx_live_account_identity_incomplete",
        "okx_live_account_identity_mismatch",
        "okx_live_balance_empty",
        "okx_live_capability_rejected",
        "okx_live_credentials_missing",
        "okx_live_parse_failed",
        "okx_live_persistence_failed",
        "okx_live_private_api_unavailable",
    }
)


class OkxLiveRepositoryError(RuntimeError):
    pass


class OkxLiveAccountIdentityError(OkxLiveRepositoryError):
    pass


class OkxLiveSafetyLatchConflict(OkxLiveRepositoryError):
    pass


def fingerprint_account_identifier(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    payload = f"{_ACCOUNT_FINGERPRINT_NAMESPACE}{normalized}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class OkxLiveRepository:
    """Atomic PostgreSQL mirror for authenticated OKX Live read state.

    OKX remains authoritative. This repository never calls the exchange and
    exposes no order-write operation. Account identity is pinned before any
    snapshot state can be replaced, preventing silent cross-account mixing.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def sync_snapshot(
        self,
        *,
        account_config: OkxLiveAccountConfig,
        balance: OkxLiveBalanceSnapshot,
        positions: list[OkxLivePositionView],
        orders: list[OkxLiveOrderView],
        algo_orders: list[OkxLiveAlgoOrderView],
    ) -> OkxLiveMirrorStatus:
        now = datetime.now(timezone.utc)
        account_values = self._account_values(account_config, now)
        deduped_orders = {item.order_id: item for item in orders}
        deduped_positions = {
            item.position_id: item for item in positions if item.size != 0
        }
        deduped_algo_orders = {item.algo_order_id: item for item in algo_orders}

        async with self.session_factory() as session:
            async with session.begin():
                await self._pin_account_identity(session, account_values)

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
                balance_stmt = pg_insert(OkxLiveBalanceState).values(**balance_values)
                await session.execute(
                    balance_stmt.on_conflict_do_update(
                        index_elements=[OkxLiveBalanceState.id],
                        set_={key: value for key, value in balance_values.items() if key != "id"},
                    )
                )

                await session.execute(delete(OkxLivePositionState))
                session.add_all(
                    [self._position_row(position, now) for position in deduped_positions.values()]
                )

                await session.execute(delete(OkxLiveAlgoOrderState))
                session.add_all(
                    [self._algo_row(algo, now) for algo in deduped_algo_orders.values()]
                )

                for order in deduped_orders.values():
                    values = self._order_values(order, now)
                    order_stmt = pg_insert(OkxLiveOrderState).values(**values)
                    await session.execute(
                        order_stmt.on_conflict_do_update(
                            index_elements=[OkxLiveOrderState.order_id],
                            set_={key: value for key, value in values.items() if key != "order_id"},
                        )
                    )

                stored_order_count = int(
                    (
                        await session.scalar(
                            select(func.count()).select_from(OkxLiveOrderState)
                        )
                    )
                    or 0
                )
                details = self._checkpoint_details(account_config)
                checkpoint_values = {
                    "id": 1,
                    "status": "reconciled",
                    "order_count": stored_order_count,
                    "position_count": len(deduped_positions),
                    "algo_order_count": len(deduped_algo_orders),
                    "details": details,
                    "last_error": None,
                    "reconciled_at": now,
                    "updated_at": now,
                }
                checkpoint_stmt = pg_insert(OkxLiveSyncCheckpoint).values(
                    **checkpoint_values
                )
                await session.execute(
                    checkpoint_stmt.on_conflict_do_update(
                        index_elements=[OkxLiveSyncCheckpoint.id],
                        set_={
                            key: value
                            for key, value in checkpoint_values.items()
                            if key != "id"
                        },
                    )
                )

        return await self.mirror_status()

    async def mark_failure(self, code: str) -> None:
        now = datetime.now(timezone.utc)
        safe_code = self._safe_failure_code(code)
        async with self.session_factory() as session:
            async with session.begin():
                values = {
                    "id": 1,
                    "status": "error",
                    "order_count": 0,
                    "position_count": 0,
                    "algo_order_count": 0,
                    "details": {},
                    "last_error": safe_code,
                    "reconciled_at": None,
                    "updated_at": now,
                }
                statement = pg_insert(OkxLiveSyncCheckpoint).values(**values)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[OkxLiveSyncCheckpoint.id],
                        set_={
                            "status": "error",
                            "last_error": safe_code,
                            "updated_at": now,
                        },
                    )
                )

    async def engage_safety_latch(self, code: str) -> OkxLiveSafetyLatchState:
        normalized = (code or "").strip().lower()
        if _SAFE_LATCH_CODE.fullmatch(normalized) is None:
            raise OkxLiveRepositoryError("okx_live_safety_latch_code_invalid")
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            async with session.begin():
                values = {
                    "id": 1,
                    "status": "safety_latched",
                    "order_count": 0,
                    "position_count": 0,
                    "algo_order_count": 0,
                    "safety_latched": True,
                    "safety_latch_code": normalized,
                    "safety_latch_version": 1,
                    "safety_latched_at": now,
                    "details": {},
                    "last_error": normalized,
                    "reconciled_at": None,
                    "updated_at": now,
                }
                statement = pg_insert(OkxLiveSyncCheckpoint).values(**values)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[OkxLiveSyncCheckpoint.id],
                        set_={
                            "safety_latched": True,
                            "safety_latch_code": normalized,
                            "safety_latch_version": (
                                OkxLiveSyncCheckpoint.safety_latch_version + 1
                            ),
                            "safety_latched_at": now,
                            "updated_at": now,
                        },
                    )
                )
                checkpoint = await session.scalar(
                    select(OkxLiveSyncCheckpoint)
                    .where(OkxLiveSyncCheckpoint.id == 1)
                    .with_for_update()
                )
                if checkpoint is None:
                    raise OkxLiveRepositoryError(
                        "okx_live_safety_latch_checkpoint_unavailable"
                    )
                return self._latch_state(checkpoint)

    async def clear_safety_latch(
        self, *, expected_version: int
    ) -> OkxLiveSafetyLatchState:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            async with session.begin():
                checkpoint = await session.scalar(
                    select(OkxLiveSyncCheckpoint)
                    .where(OkxLiveSyncCheckpoint.id == 1)
                    .with_for_update()
                )
                if checkpoint is None:
                    raise OkxLiveRepositoryError(
                        "okx_live_safety_latch_checkpoint_unavailable"
                    )
                if (
                    not checkpoint.safety_latched
                    or checkpoint.safety_latch_version != expected_version
                ):
                    raise OkxLiveSafetyLatchConflict(
                        "okx_live_safety_latch_changed_during_clear"
                    )
                checkpoint.safety_latched = False
                previous_code = checkpoint.safety_latch_code
                checkpoint.safety_latch_code = None
                checkpoint.safety_latch_version += 1
                checkpoint.safety_latched_at = None
                if checkpoint.last_error == previous_code:
                    checkpoint.last_error = None
                if checkpoint.status == "safety_latched":
                    checkpoint.status = "not_reconciled"
                checkpoint.updated_at = now
                await session.flush()
                return self._latch_state(checkpoint)

    async def safety_latch_status(self) -> OkxLiveSafetyLatchState:
        async with self.session_factory() as session:
            checkpoint = await session.get(OkxLiveSyncCheckpoint, 1)
        if checkpoint is None:
            raise OkxLiveRepositoryError(
                "okx_live_safety_latch_checkpoint_unavailable"
            )
        return self._latch_state(checkpoint)

    async def mirror_status(self) -> OkxLiveMirrorStatus:
        async with self.session_factory() as session:
            checkpoint = await session.get(OkxLiveSyncCheckpoint, 1)
            account_state = await session.get(OkxLiveAccountConfigState, 1)
            balance_state = await session.get(OkxLiveBalanceState, 1)
            order_count = int(
                (await session.scalar(select(func.count()).select_from(OkxLiveOrderState))) or 0
            )
            position_count = int(
                (await session.scalar(select(func.count()).select_from(OkxLivePositionState)))
                or 0
            )
            algo_order_count = int(
                (await session.scalar(select(func.count()).select_from(OkxLiveAlgoOrderState)))
                or 0
            )

        if checkpoint is None:
            return OkxLiveMirrorStatus(available=False)

        available = (
            checkpoint.reconciled_at is not None
            and account_state is not None
            and balance_state is not None
        )
        details = dict(checkpoint.details or {})
        details["status"] = checkpoint.status
        details["safety_latched"] = checkpoint.safety_latched
        details["safety_latch_code"] = checkpoint.safety_latch_code
        details["safety_latch_version"] = checkpoint.safety_latch_version
        return OkxLiveMirrorStatus(
            available=available,
            order_count=order_count,
            position_count=position_count,
            algo_order_count=algo_order_count,
            last_reconciled_at=checkpoint.reconciled_at,
            last_error=checkpoint.last_error,
            safety_latched=checkpoint.safety_latched,
            safety_latch_code=checkpoint.safety_latch_code,
            safety_latch_version=checkpoint.safety_latch_version,
            details=details,
        )

    @staticmethod
    def _latch_state(
        checkpoint: OkxLiveSyncCheckpoint,
    ) -> OkxLiveSafetyLatchState:
        return OkxLiveSafetyLatchState(
            latched=checkpoint.safety_latched,
            code=checkpoint.safety_latch_code,
            version=checkpoint.safety_latch_version,
            latched_at=checkpoint.safety_latched_at,
        )

    async def _pin_account_identity(
        self,
        session: AsyncSession,
        values: dict[str, Any],
    ) -> None:
        insert_statement = pg_insert(OkxLiveAccountConfigState).values(**values)
        await session.execute(
            insert_statement.on_conflict_do_nothing(
                index_elements=[OkxLiveAccountConfigState.id]
            )
        )
        account_state = await session.scalar(
            select(OkxLiveAccountConfigState)
            .where(OkxLiveAccountConfigState.id == 1)
            .with_for_update()
        )
        if account_state is None:
            raise OkxLiveRepositoryError("okx_live_account_state_unavailable")
        if (
            account_state.uid_fingerprint != values["uid_fingerprint"]
            or account_state.main_uid_fingerprint != values["main_uid_fingerprint"]
        ):
            raise OkxLiveAccountIdentityError("okx_live_account_identity_mismatch")

        for key, value in values.items():
            if key != "id":
                setattr(account_state, key, value)

    @staticmethod
    def _account_values(
        account_config: OkxLiveAccountConfig,
        captured_at: datetime,
    ) -> dict[str, Any]:
        uid_fingerprint = fingerprint_account_identifier(account_config.uid)
        main_uid_fingerprint = fingerprint_account_identifier(account_config.main_uid)
        if uid_fingerprint is None or main_uid_fingerprint is None:
            raise OkxLiveAccountIdentityError("okx_live_account_identity_incomplete")

        capability = account_config.capability
        return {
            "id": 1,
            "uid_fingerprint": uid_fingerprint,
            "main_uid_fingerprint": main_uid_fingerprint,
            "is_sub_account": account_config.is_sub_account,
            "account_level": account_config.account_level,
            "position_mode": account_config.position_mode,
            "account_stp_mode": account_config.account_stp_mode,
            "account_type": account_config.account_type,
            "permissions": sorted(set(capability.permissions)),
            "unknown_permissions": sorted(set(capability.unknown_permissions)),
            "read_permission": capability.read_permission,
            "trade_permission": capability.trade_permission,
            "withdraw_permission": capability.withdraw_permission,
            "ip_bound": capability.ip_bound,
            "captured_at": captured_at,
            "persisted_at": captured_at,
        }

    @staticmethod
    def _checkpoint_details(account_config: OkxLiveAccountConfig) -> dict[str, Any]:
        capability = account_config.capability
        return {
            "account_level": account_config.account_level,
            "position_mode": account_config.position_mode,
            "is_sub_account": account_config.is_sub_account,
            "read_permission": capability.read_permission,
            "trade_permission": capability.trade_permission,
            "withdraw_permission": capability.withdraw_permission,
            "ip_bound": capability.ip_bound,
            "unknown_permission_count": len(set(capability.unknown_permissions)),
        }

    @staticmethod
    def _order_values(order: OkxLiveOrderView, now: datetime) -> dict[str, Any]:
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
    def _position_row(
        position: OkxLivePositionView,
        now: datetime,
    ) -> OkxLivePositionState:
        return OkxLivePositionState(
            position_id=position.position_id,
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
    def _algo_row(
        algo: OkxLiveAlgoOrderView,
        now: datetime,
    ) -> OkxLiveAlgoOrderState:
        return OkxLiveAlgoOrderState(
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

    @staticmethod
    def _safe_failure_code(code: str) -> str:
        normalized = (code or "").strip().lower()
        if normalized in _ALLOWED_FAILURE_CODES:
            return normalized
        return _GENERIC_FAILURE_CODE
