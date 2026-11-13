import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.routers import paper as paper_api
from app.api.security import require_ctcc_token
from app.database.repositories.persistence import (
    PaperCommitOutcomeUnknown,
    PersistenceRepository,
    state_checksum,
)
from app.domain.paper import ManualCloseRequest, MarketTickRequest, PaperOrderRequest
from app.paper.engine import PaperBroker, PaperBrokerError
from app.paper.execution_service import (
    PaperExecutionService,
    PaperPersistenceError,
    PaperPersistenceOutcomeUnknown,
)


class FakeRepository:
    def __init__(self) -> None:
        self.state = None
        self.actions: list[str] = []
        self.fail = False

    async def save_paper_state(
        self, state, *, action, resource_id=None, actor="ctcc-system", details=None
    ):
        if self.fail:
            raise RuntimeError("database unavailable")
        self.state = state.model_copy(deep=True)
        self.actions.append(action)
        return state_checksum(state)

    async def load_paper_state(self):
        return self.state.model_copy(deep=True) if self.state is not None else None

    async def mark_recovered(self, checksum, details):
        return None

    async def counts(self):
        state = self.state
        return {
            "orders": len(state.orders) if state else 0,
            "positions": len(state.positions) if state else 0,
            "history": 0,
            "fingerprints": 0,
        }


def request() -> PaperOrderRequest:
    return PaperOrderRequest(
        symbol="BTC-USDT-SWAP",
        side="long",
        quantity=Decimal(1),
        reference_price=Decimal(100),
        stop_loss=Decimal(95),
        take_profit=Decimal(110),
        strategy="unit_test",
        score=80,
    )


@pytest.mark.asyncio
async def test_submit_persists_full_state() -> None:
    repository = FakeRepository()
    service = PaperExecutionService(PaperBroker(), repository)
    await service.recover()
    order = await service.submit(request())

    assert order.status == "filled"
    assert repository.state is not None
    assert len(repository.state.orders) == 1
    assert len(repository.state.positions) == 1
    assert repository.actions[-1] == "paper_order_submitted"


@pytest.mark.asyncio
async def test_persistence_failure_rolls_back_memory() -> None:
    repository = FakeRepository()
    service = PaperExecutionService(PaperBroker(), repository)
    await service.recover()
    before = service.state()
    repository.fail = True

    with pytest.raises(PaperPersistenceError):
        await service.submit(request())

    assert service.state() == before
    assert repository.state == before
    repository.fail = False
    assert (await service.submit(request())).status == "filled"


@pytest.mark.asyncio
@pytest.mark.parametrize("committing", [False, True])
async def test_repository_distinguishes_failure_before_commit_boundary(
    monkeypatch, committing: bool
) -> None:
    repository = PersistenceRepository(None)
    underlying = RuntimeError("private connection failure")

    async def fail(*_args, mark_committing, **_kwargs):
        if committing:
            mark_committing()
        raise underlying

    save = AsyncMock(side_effect=fail)
    monkeypatch.setattr(repository, "_save_paper_state", save)
    if committing:
        with pytest.raises(
            PaperCommitOutcomeUnknown, match="^paper_persistence_outcome_unknown$"
        ):
            await repository.save_paper_state(PaperBroker().state(), action="test")
    else:
        with pytest.raises(RuntimeError) as raised:
            await repository.save_paper_state(PaperBroker().state(), action="test")
        assert raised.value is underlying
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_committed_but_unacknowledged_save_fences_until_fresh_recovery() -> None:
    class CommittedThenLostRepository(FakeRepository):
        async def save_paper_state(self, state, *, action, **kwargs):
            written = await super().save_paper_state(state, action=action, **kwargs)
            if action == "paper_order_submitted":
                raise PaperCommitOutcomeUnknown("do not expose private driver details")
            return written

    repository = CommittedThenLostRepository()
    broker = PaperBroker()
    service = PaperExecutionService(broker, repository)
    await service.recover()
    before = broker.state()
    with pytest.raises(
        PaperPersistenceOutcomeUnknown, match="^paper_persistence_outcome_unknown$"
    ):
        await service.submit(request())
    assert broker.state() == before
    assert len(repository.state.orders) == 1
    for action in (
        lambda: service.submit(request()),
        lambda: service.cancel(uuid4()),
        lambda: service.close(uuid4(), price=Decimal(100)),
        lambda: service.tick(symbol=request().symbol, price=Decimal(100)),
        service.reset,
        service.recover,
        lambda: service.reconcile("persist_memory"),
        lambda: service.reconcile("restore_from_database"),
        service.recovery_status,
        service.runtime_state,
    ):
        with pytest.raises(PaperPersistenceOutcomeUnknown):
            await action()
    for load in (service.state, service.account):
        with pytest.raises(PaperPersistenceOutcomeUnknown):
            load()
    assert await service.persist_now("paper_state_shutdown_checkpoint") is None
    assert repository.actions == ["paper_state_initialized", "paper_order_submitted"]
    fresh_process = PaperExecutionService(PaperBroker(), repository)
    assert (await fresh_process.recover()).recovered
    assert fresh_process.state() == repository.state
    restored_runtime = await fresh_process.runtime_state()
    assert restored_runtime.persistent is True
    assert restored_runtime.recovered is True
    assert not service.matches_runtime(str(restored_runtime.runtime_id))
    assert restored_runtime.state == repository.state
    assert repository.actions == ["paper_state_initialized", "paper_order_submitted"]


def test_local_paper_response_is_fixed_when_commit_outcome_is_unknown(monkeypatch):
    class UnacknowledgedRepository(FakeRepository):
        async def save_paper_state(self, state, *, action, **kwargs):
            await super().save_paper_state(state, action=action, **kwargs)
            raise PaperCommitOutcomeUnknown("private failure detail")

    repository = UnacknowledgedRepository()
    service = PaperExecutionService(PaperBroker(), repository)
    monkeypatch.setattr(paper_api, "paper_service", service)
    app = FastAPI()
    app.include_router(paper_api.router)
    app.dependency_overrides[require_ctcc_token] = lambda: None
    with TestClient(app) as client:
        for action in (
            lambda: client.post(
                "/api/paper/orders", json=request().model_dump(mode="json")
            ),
            lambda: client.get("/api/paper/state"),
            lambda: client.get("/api/paper/account"),
            lambda: client.post(
                "/api/paper/orders", json=request().model_dump(mode="json")
            ),
        ):
            response = action()
            assert response.status_code == 503
            assert response.json() == {"detail": "paper_persistence_outcome_unknown"}
    assert repository.actions == ["paper_order_submitted"]


def test_runtime_binding_rejects_stale_or_duplicate_header_and_preserves_original_routes(
    monkeypatch,
):
    repository = FakeRepository()
    service = PaperExecutionService(PaperBroker(), repository)
    asyncio.run(service.recover())
    monkeypatch.setattr(paper_api, "paper_service", service)
    app = FastAPI()
    app.include_router(paper_api.router)
    app.include_router(paper_api.local_paper_runtime_router)
    app.dependency_overrides[require_ctcc_token] = lambda: None
    with TestClient(app) as client:
        response = client.get("/api/paper/runtime/state")
        document = response.json()
        assert (
            response.status_code == 200
            and response.headers["cache-control"] == "no-store"
        )
        assert document["persistent"] is True
        assert document["recovered"] is False
        assert len(document["installation_binding"]) == 64
        assert document["state"] == client.get("/api/paper/state").json()
        correct = ("X-CTCC-Paper-Runtime", document["runtime_id"])
        route = "/api/paper/orders"
        payload = request().model_dump(mode="json")
        for headers in ([correct, correct], [(correct[0], str(uuid4()))]):
            denied = client.post(route, json=payload, headers=headers)
            assert denied.status_code == 409
            assert denied.json() == {"detail": "paper_runtime_changed"}
        limit = {**payload, "order_type": "limit", "limit_price": "100"}
        order = client.post(route, json=limit, headers=[correct]).json()
        assert order["status"] == "pending"
        cancel = client.post(f"{route}/{order['id']}/cancel", headers=[correct])
        assert cancel.status_code == 200 and cancel.json()["status"] == "cancelled"
        assert client.post(route, json=payload).status_code == 201
        position = client.get("/api/paper/state").json()["positions"][0]
        closed = client.post(
            f"/api/paper/positions/{position['id']}/close",
            json={"price": "102", "reason": "manual_local:test"},
            headers=[correct],
        )
        assert (
            closed.status_code == 200
            and closed.json()["close_reason"] == "manual_local:test"
        )
    assert repository.actions == [
        "paper_state_initialized",
        "paper_order_submitted",
        "paper_order_cancelled",
        "paper_order_submitted",
        "paper_position_closed",
    ]


@pytest.mark.asyncio
async def test_failed_fill_leaves_no_partial_order_even_without_persistence() -> None:
    service = PaperExecutionService(PaperBroker())
    before = service.state()
    payload = request().model_dump()
    payload.update(
        quantity=Decimal("1e17"),
        reference_price=Decimal("1e17"),
        stop_loss=Decimal("9e16"),
        take_profit=Decimal("2e17"),
    )
    with pytest.raises(PaperBrokerError, match="^paper_numeric_range_exceeded$"):
        await service.submit(PaperOrderRequest.model_validate(payload))
    assert service.state() == before
    assert (await service.submit(request())).status == "filled"


@pytest.mark.asyncio
async def test_failed_tick_restores_already_changed_positions(monkeypatch) -> None:
    broker = PaperBroker()
    repository = FakeRepository()
    service = PaperExecutionService(broker, repository)
    await service.recover()
    await service.submit(request())
    before = service.state()
    before_actions = list(repository.actions)
    original_tick = broker.tick

    def fail_after_change(**arguments):
        original_tick(**arguments)
        assert broker.state().positions[0].status == "closed"
        raise PaperBrokerError("test_failed_after_partial_mutation")

    monkeypatch.setattr(broker, "tick", fail_after_change)
    with pytest.raises(PaperBrokerError, match="^test_failed_after_partial_mutation$"):
        await service.tick(symbol=request().symbol, price=Decimal(94))

    assert service.state() == before
    assert repository.state == before

    assert repository.actions == before_actions


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_succeeds", [True, False])
async def test_cancelled_mutation_holds_lock_until_single_persistence_attempt_finishes(
    commit_succeeds: bool,
) -> None:
    began, complete = asyncio.Event(), asyncio.Event()

    class PausedRepository(FakeRepository):
        async def save_paper_state(self, state, *, action, **kwargs):
            if action == "paper_order_submitted":
                began.set()
                await complete.wait()
                self.fail = not commit_succeeds
            return await super().save_paper_state(state, action=action, **kwargs)

    repository = PausedRepository()
    service = PaperExecutionService(PaperBroker(), repository)
    await service.recover()
    before = service.state()
    order = asyncio.create_task(service.submit(request()))
    await began.wait()
    order.cancel()
    await asyncio.sleep(0)
    order.cancel()
    await asyncio.sleep(0)
    assert not order.done()
    assert service._lock.locked()
    assert repository.state == before

    observation = asyncio.create_task(service.runtime_state())
    await asyncio.sleep(0)
    assert not observation.done()

    complete.set()
    with pytest.raises(asyncio.CancelledError):
        await order

    assert not service._lock.locked()
    assert service.state() == repository.state
    assert (await observation).state == repository.state
    assert len(service.state().orders) == int(commit_succeeds)
    assert repository.actions.count("paper_order_submitted") == int(commit_succeeds)


@pytest.mark.parametrize("invalid", ["1e-9", "1e18", "NaN", "Infinity"])
def test_paper_requests_reject_unrepresentable_positive_operands(invalid: str) -> None:
    for model, values in (
        (PaperOrderRequest, {**request().model_dump(), "quantity": invalid}),
        (MarketTickRequest, {"symbol": request().symbol, "price": invalid}),
        (ManualCloseRequest, {"price": invalid}),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(values)


def test_paper_request_preserves_engine_rounding_for_valid_extra_precision() -> None:
    accepted = PaperOrderRequest.model_validate(
        {**request().model_dump(), "quantity": "0.000000019"}
    )
    assert PaperBroker().submit(accepted).quantity == Decimal("0.00000001")


@pytest.mark.asyncio
async def test_recover_restores_persisted_position() -> None:
    repository = FakeRepository()
    first = PaperExecutionService(PaperBroker(), repository)
    await first.recover()
    await first.submit(request())

    second = PaperExecutionService(PaperBroker(), repository)
    status = await second.recover()

    assert status.recovered is True
    assert second.account().open_positions == 1
    assert (
        second.state().orders[0].client_order_id
        == first.state().orders[0].client_order_id
    )


def test_checksum_ignores_decimal_scale() -> None:
    broker = PaperBroker()
    state = broker.state()
    scaled = state.model_copy(
        update={
            "account": state.account.model_copy(
                update={"cash_balance": Decimal("10000.0000000000")}
            )
        }
    )
    assert state_checksum(state) == state_checksum(scaled)


def test_checksum_ignores_runtime_market_fields() -> None:
    broker = PaperBroker()
    broker.submit(request())
    state = broker.state()
    position = state.positions[0]

    changed = state.model_copy(
        update={
            "account": state.account.model_copy(
                update={
                    "equity": Decimal(12345),
                    "unrealized_pnl": Decimal(2345),
                    "open_positions": 99,
                }
            ),
            "positions": [
                position.model_copy(
                    update={
                        "mark_price": Decimal(999),
                        "unrealized_pnl": Decimal(555),
                    }
                )
            ],
        }
    )

    assert state_checksum(state) == state_checksum(changed)


def test_checksum_is_independent_of_collection_order() -> None:
    broker = PaperBroker()
    broker.submit(request())
    second = request().model_copy(
        update={
            "symbol": "ETH-USDT-SWAP",
            "client_order_id": "unit-test-second",
        }
    )
    broker.submit(second)
    state = broker.state()
    reordered = state.model_copy(
        update={
            "orders": list(reversed(state.orders)),
            "positions": list(reversed(state.positions)),
        }
    )

    assert state_checksum(state) == state_checksum(reordered)
