from decimal import Decimal

import pytest

from app.database.repositories.persistence import state_checksum
from app.domain.paper import PaperOrderRequest
from app.paper.engine import PaperBroker
from app.paper.execution_service import PaperExecutionService, PaperPersistenceError


class FakeRepository:
    def __init__(self) -> None:
        self.state = None
        self.actions: list[str] = []
        self.fail = False

    async def save_paper_state(self, state, *, action, resource_id=None, actor="ctcc-system", details=None):
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
        quantity=Decimal("1"),
        reference_price=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("110"),
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
    assert second.state().orders[0].client_order_id == first.state().orders[0].client_order_id


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
                    "equity": Decimal("12345"),
                    "unrealized_pnl": Decimal("2345"),
                    "open_positions": 99,
                }
            ),
            "positions": [
                position.model_copy(
                    update={
                        "mark_price": Decimal("999"),
                        "unrealized_pnl": Decimal("555"),
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
