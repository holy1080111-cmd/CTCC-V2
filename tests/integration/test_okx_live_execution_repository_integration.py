from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.database.models.okx_live import OkxLiveExecutionIntent
from app.database.repositories.okx_live_execution import (
    OkxLiveExecutionAuthorityBusy,
    OkxLiveExecutionIntentConflict,
    OkxLiveExecutionIntentReplay,
    OkxLiveExecutionRepository,
    OkxLiveExecutionRepositoryError,
)
from app.domain.okx_live import OkxLiveIntentResolutionExpectation
from tests.unit.test_okx_live_service import (
    live_order,
    seed_legacy_local_lease_for_downstream_unit_test,
    service_fixture,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_intent_commit_ack_loss_stops_before_private_write_and_retains_unresolved() -> (
    None
):
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    repository = OkxLiveExecutionRepository(Session)
    key = "CTCCL" + uuid4().hex[:20]

    class CommitAckLostRepository:
        reserved_kwargs: dict[str, object] | None = None

        def __getattr__(self, name: str):
            return getattr(repository, name)

        async def reserve_intent(self, **kwargs):
            self.reserved_kwargs = dict(kwargs)
            await repository.reserve_intent(**kwargs)
            raise RuntimeError("synthetic post-commit acknowledgement loss")

    service, _, execution, _, _ = service_fixture()
    await seed_legacy_local_lease_for_downstream_unit_test(service)
    ack_lost_repository = CommitAckLostRepository()
    service.execution_repository = ack_lost_repository
    try:
        with pytest.raises(RuntimeError, match="post-commit acknowledgement loss"):
            await service.place_order(live_order(client_order_id=key))

        assert service.arm_status().armed is False
        assert service.arm_status().emergency_stop is True
        assert not {
            "order_precheck",
            "set_leverage",
            "cancel_all_after",
            "place_order",
        } & {name for name, _ in execution.calls}
        # A fresh repository reads the committed tombstone independently.
        # A full process-restart acceptance test is still required.
        restarted = OkxLiveExecutionRepository(Session)
        stored = await restarted.load_intent(key)
        assert stored is not None
        assert stored.status == "ambiguous"
        assert stored.operator_reconciled_at is None
        assert key in {
            item.idempotency_key for item in await restarted.load_unresolved_intents()
        }
        assert ack_lost_repository.reserved_kwargs is not None
        with pytest.raises(OkxLiveExecutionIntentReplay):
            await restarted.reserve_intent(**ack_lost_repository.reserved_kwargs)
    finally:
        async with Session() as session, session.begin():
            await session.execute(
                delete(OkxLiveExecutionIntent).where(
                    OkxLiveExecutionIntent.idempotency_key == key
                )
            )
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_execution_intent_is_durable_idempotent_and_secret_free() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    repository = OkxLiveExecutionRepository(Session)
    key = "CTCCX" + uuid4().hex[:20]

    async def cleanup() -> None:
        async with Session() as session, session.begin():
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
        assert key in {
            item.idempotency_key for item in await repository.load_unresolved_intents()
        }

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
        assert key not in {
            item.idempotency_key for item in await repository.load_unresolved_intents()
        }

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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ambiguous_live_intent_requires_flat_operator_resolution() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    repository = OkxLiveExecutionRepository(Session)
    key = "CTCCX" + uuid4().hex[:20]

    async def cleanup() -> None:
        async with Session() as session, session.begin():
            await session.execute(
                delete(OkxLiveExecutionIntent).where(
                    OkxLiveExecutionIntent.idempotency_key == key
                )
            )

    try:
        await cleanup()
        await repository.reserve_intent(
            idempotency_key=key,
            request_hash="c" * 64,
            action="close_position",
            instrument_id="BTC-USDT-SWAP",
        )
        ambiguous = await repository.update_intent(
            key,
            status="ambiguous",
            detail_codes=["position_close_unconfirmed"],
        )
        assert ambiguous.operator_reconciled_at is None
        assert key in {
            item.idempotency_key for item in await repository.load_unresolved_intents()
        }

        reconciled_at = datetime.now(UTC)
        expectation = OkxLiveIntentResolutionExpectation(
            idempotency_key=ambiguous.idempotency_key,
            status=ambiguous.status,
            updated_at=ambiguous.updated_at,
        )
        async with repository.execution_lock():
            count = await repository.mark_unresolved_intents_operator_reconciled(
                expectations=[expectation],
                reconciled_at=reconciled_at,
                resolution_code="operator_confirmed_flat_exchange_state",
            )

        assert count == 1
        assert key not in {
            item.idempotency_key for item in await repository.load_unresolved_intents()
        }
        stored = await repository.load_intent(key)
        assert stored is not None
        assert stored.status == "ambiguous"
        assert stored.operator_reconciled_at is not None
        assert stored.operator_resolution_code == (
            "operator_confirmed_flat_exchange_state"
        )
    finally:
        await cleanup()
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_operator_resolution_is_exact_set_all_or_none_cas() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    repository = OkxLiveExecutionRepository(Session)
    keys = ["CTCCX" + uuid4().hex[:20] for _ in range(2)]

    async def cleanup() -> None:
        async with Session() as session, session.begin():
            await session.execute(
                delete(OkxLiveExecutionIntent).where(
                    OkxLiveExecutionIntent.idempotency_key.in_(keys)
                )
            )

    try:
        await cleanup()
        for index, key in enumerate(keys):
            await repository.reserve_intent(
                idempotency_key=key,
                request_hash=str(index) * 64,
                action="close_position",
                instrument_id="BTC-USDT-SWAP",
            )
        captured = [
            OkxLiveIntentResolutionExpectation(
                idempotency_key=item.idempotency_key,
                status=item.status,
                updated_at=item.updated_at,
            )
            for item in await repository.load_unresolved_intents(limit=1000)
            if item.idempotency_key in keys
        ]
        await repository.update_intent(
            keys[0], status="ambiguous", detail_codes=["recovery_race"]
        )

        with pytest.raises(OkxLiveExecutionIntentConflict):
            await repository.mark_unresolved_intents_operator_reconciled(
                expectations=captured,
                reconciled_at=datetime.now(UTC),
                resolution_code="operator_confirmed_flat_exchange_state",
            )

        for key in keys:
            stored = await repository.load_intent(key)
            assert stored is not None
            assert stored.operator_reconciled_at is None
    finally:
        await cleanup()
        await engine.dispose()
