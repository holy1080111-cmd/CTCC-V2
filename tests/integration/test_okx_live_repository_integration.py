from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.database.models.okx_live import (
    OkxLiveAccountConfigState,
    OkxLiveAlgoOrderState,
    OkxLiveBalanceState,
    OkxLiveOrderState,
    OkxLivePositionState,
    OkxLiveSyncCheckpoint,
)
from app.database.repositories.okx_live import (
    OkxLiveAccountIdentityError,
    OkxLiveRepository,
    fingerprint_account_identifier,
)
from app.domain.okx_live import (
    OkxLiveAccountConfig,
    OkxLiveAlgoOrderView,
    OkxLiveApiKeyCapability,
    OkxLiveBalanceDetail,
    OkxLiveBalanceSnapshot,
    OkxLiveOrderView,
    OkxLivePositionView,
)


NOW = datetime(2026, 8, 9, 2, 3, 4, tzinfo=timezone.utc)
LIVE_MODELS = (
    OkxLiveSyncCheckpoint,
    OkxLiveAlgoOrderState,
    OkxLivePositionState,
    OkxLiveOrderState,
    OkxLiveBalanceState,
    OkxLiveAccountConfigState,
)


def account_config(
    uid: str = "integration-live-uid",
    main_uid: str = "integration-live-main-uid",
) -> OkxLiveAccountConfig:
    return OkxLiveAccountConfig(
        uid=uid,
        main_uid=main_uid,
        is_sub_account=uid != main_uid,
        account_level="2",
        position_mode="net_mode",
        account_stp_mode="cancel_maker",
        account_type="1",
        capability=OkxLiveApiKeyCapability(
            permissions=["read_only", "trade"],
            unknown_permissions=[],
            read_permission=True,
            trade_permission=True,
            withdraw_permission=False,
            ip_bound=True,
        ),
    )


def balance(total: str) -> OkxLiveBalanceSnapshot:
    amount = Decimal(total)
    return OkxLiveBalanceSnapshot(
        total_equity=amount,
        isolated_equity=Decimal("0"),
        adjusted_equity=amount,
        available_equity=amount,
        details=[
            OkxLiveBalanceDetail(
                currency="USDT",
                equity=amount,
                cash_balance=amount,
                available_balance=amount,
                frozen_balance=Decimal("0"),
                unrealized_pnl=Decimal("0"),
            )
        ],
        captured_at=NOW,
        raw={"totalEq": total, "uTime": "1786240984000"},
    )


def position(position_id: str = "integration-position-1") -> OkxLivePositionView:
    return OkxLivePositionView(
        position_id=position_id,
        instrument_id="BTC-USDT-SWAP",
        position_side="net",
        size=Decimal("1"),
        available_size=Decimal("1"),
        average_price=Decimal("60000"),
        mark_price=Decimal("60100"),
        unrealized_pnl=Decimal("100"),
        leverage=Decimal("1"),
        margin_mode="cross",
        raw={"posId": position_id},
    )


def order(order_id: str) -> OkxLiveOrderView:
    return OkxLiveOrderView(
        order_id=order_id,
        client_order_id="reusable-client-order-id",
        instrument_id="BTC-USDT-SWAP",
        side="buy",
        position_side="net",
        order_type="market",
        state="filled",
        size=Decimal("1"),
        accumulated_fill_size=Decimal("1"),
        average_fill_price=Decimal("60000"),
        raw={"ordId": order_id},
    )


def algo_order(algo_order_id: str = "integration-algo-1") -> OkxLiveAlgoOrderView:
    return OkxLiveAlgoOrderView(
        algo_order_id=algo_order_id,
        client_algo_order_id="reusable-client-algo-id",
        instrument_id="BTC-USDT-SWAP",
        order_type="conditional",
        state="live",
        side="sell",
        position_side="net",
        size=Decimal("1"),
        stop_loss_trigger_price=Decimal("59000"),
        raw={"algoId": algo_order_id},
    )


def session_factory(
    connection: AsyncConnection,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_repository_is_atomic_account_pinned_and_rollback_isolated() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)

    try:
        async with engine.connect() as connection:
            outer_transaction = await connection.begin()
            try:
                for model in LIVE_MODELS:
                    await connection.execute(delete(model))

                Session = session_factory(connection)
                repository = OkxLiveRepository(Session)
                await repository.mark_failure("okx_live_private_api_unavailable")
                initial_failure = await repository.mirror_status()

                assert initial_failure.available is False
                assert initial_failure.last_reconciled_at is None
                assert initial_failure.last_error == "okx_live_private_api_unavailable"

                first_status = await repository.sync_snapshot(
                    account_config=account_config(),
                    balance=balance("100"),
                    positions=[position()],
                    orders=[order("integration-order-1")],
                    algo_orders=[algo_order()],
                )

                assert first_status.available is True
                assert first_status.order_count == 1
                assert first_status.position_count == 1
                assert first_status.algo_order_count == 1
                assert first_status.last_error is None

                async with Session() as session:
                    account_state = await session.get(OkxLiveAccountConfigState, 1)
                    assert account_state is not None
                    assert account_state.uid_fingerprint == fingerprint_account_identifier(
                        "integration-live-uid"
                    )
                    assert account_state.main_uid_fingerprint == fingerprint_account_identifier(
                        "integration-live-main-uid"
                    )
                    assert "integration-live-uid" not in repr(account_state.__dict__)

                second_status = await repository.sync_snapshot(
                    account_config=account_config(),
                    balance=balance("101"),
                    positions=[],
                    orders=[order("integration-order-2")],
                    algo_orders=[],
                )

                assert second_status.order_count == 2
                assert second_status.position_count == 0
                assert second_status.algo_order_count == 0

                async with Session() as session:
                    stored_orders = list(
                        await session.scalars(
                            select(OkxLiveOrderState).order_by(OkxLiveOrderState.order_id)
                        )
                    )
                    assert [item.order_id for item in stored_orders] == [
                        "integration-order-1",
                        "integration-order-2",
                    ]
                    assert {
                        item.client_order_id for item in stored_orders
                    } == {"reusable-client-order-id"}
                    assert list(await session.scalars(select(OkxLivePositionState))) == []
                    assert list(await session.scalars(select(OkxLiveAlgoOrderState))) == []

                with pytest.raises(
                    OkxLiveAccountIdentityError,
                    match="^okx_live_account_identity_mismatch$",
                ):
                    await repository.sync_snapshot(
                        account_config=account_config(
                            uid="different-live-uid",
                            main_uid="different-live-main-uid",
                        ),
                        balance=balance("999"),
                        positions=[position("must-not-persist")],
                        orders=[order("must-not-persist")],
                        algo_orders=[algo_order("must-not-persist")],
                    )

                async with Session() as session:
                    stored_balance = await session.get(OkxLiveBalanceState, 1)
                    assert stored_balance is not None
                    assert stored_balance.total_equity == Decimal("101")
                    assert (
                        await session.get(OkxLiveOrderState, "must-not-persist")
                        is None
                    )
                    assert (
                        await session.get(OkxLivePositionState, "must-not-persist")
                        is None
                    )
                    assert (
                        await session.get(OkxLiveAlgoOrderState, "must-not-persist")
                        is None
                    )

                await repository.mark_failure(
                    "network failed api-key=must-never-be-persisted"
                )
                failed_status = await repository.mirror_status()

                assert failed_status.available is True
                assert failed_status.order_count == 2
                assert failed_status.position_count == 0
                assert failed_status.algo_order_count == 0
                assert failed_status.last_error == "okx_live_reconcile_failed"
                assert failed_status.last_reconciled_at == second_status.last_reconciled_at
                assert failed_status.details["status"] == "error"
                assert "must-never-be-persisted" not in repr(failed_status)
                assert outer_transaction.is_active
            finally:
                if outer_transaction.is_active:
                    await outer_transaction.rollback()
    finally:
        await engine.dispose()
