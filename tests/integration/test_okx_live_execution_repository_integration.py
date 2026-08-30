from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.database.models.okx_live import OkxLiveExecutionIntent
from app.database.repositories.okx_live_execution import (
    OkxLiveExecutionIntentConflict,
    OkxLiveExecutionIntentReplay,
    OkxLiveExecutionAuthorityBusy,
    OkxLiveExecutionRepository,
    OkxLiveExecutionRepositoryError,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_execution_intent_is_durable_idempotent_and_secret_free() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    repository = OkxLiveExecutionRepository(Session)
    key = "CTCCX" + uuid4().hex[:20]

    async def cleanup() -> None:
        async with Session() as session:
            async with session.begin():
                await session.execute(
                    delete(OkxLiveExecutionIntent).where(
                        OkxLiveExecutionIntent.idempotency_key == key
                    )
                )

    try:
        await cleanup()
        async with repository.execution_lock():
            with pytest.raises(OkxLiveExecutionAuthorityBusy):
                async with repository.execution_lock():
                    pytest.fail("duplicate Live execution lock was acquired")

        async with repository.execution_lock():
            pass

        reserved = await repository.reserve_intent(
            idempotency_key=key,
            request_hash="a" * 64,
            action="cancel_order",
            instrument_id="BTC-USDT-SWAP",
            client_order_id=None,
        )
        assert reserved.status == "reserved"

        with pytest.raises(OkxLiveExecutionIntentReplay):
            await repository.reserve_intent(
                idempotency_key=key,
                request_hash="a" * 64,
                action="cancel_order",
                instrument_id="BTC-USDT-SWAP",
                client_order_id=None,
            )
        with pytest.raises(OkxLiveExecutionIntentConflict):
            await repository.reserve_intent(
                idempotency_key=key,
                request_hash="b" * 64,
                action="cancel_order",
                instrument_id="BTC-USDT-SWAP",
                client_order_id=None,
            )

        acknowledged = await repository.update_intent(
            key,
            status="acknowledged",
            exchange_order_id="live-order-1",
            detail_codes=["cancel_rest_acknowledged"],
        )
        confirmed = await repository.update_intent(
            key,
            status="confirmed",
            detail_codes=["cancel_final_state_confirmed"],
        )
        assert acknowledged.status == "acknowledged"
        assert confirmed.status == "confirmed"

        with pytest.raises(OkxLiveExecutionRepositoryError):
            await repository.update_intent(key, status="ambiguous")

        async with Session() as session:
            stored = (
                await session.scalars(
                    select(OkxLiveExecutionIntent).where(
                        OkxLiveExecutionIntent.idempotency_key == key
                    )
                )
            ).one()
        serialized = json.dumps(
            {
                column.name: getattr(stored, column.name)
                for column in OkxLiveExecutionIntent.__table__.columns
                if column.name not in {"created_at", "updated_at"}
            },
            default=str,
        ).lower()
        for forbidden in (
            "api_key",
            "api_secret",
            "passphrase",
            "private_key",
            "request_payload",
            "response_payload",
        ):
            assert forbidden not in serialized
    finally:
        await cleanup()
        await engine.dispose()
