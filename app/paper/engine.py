from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from threading import RLock
from uuid import UUID, uuid4

from app.domain.paper import (
    PaperAccountView,
    PaperOrderRequest,
    PaperOrderView,
    PaperPositionView,
    PaperStateView,
    PaperTickResult,
)

_Q = Decimal("0.00000001")


def _q(value: Decimal) -> Decimal:
    return value.quantize(_Q, rounding=ROUND_DOWN)


@dataclass
class _Order:
    view: PaperOrderView
    stop_loss: Decimal
    take_profit: Decimal


class PaperBrokerError(ValueError):
    pass


class PaperBroker:
    """Deterministic paper broker core.

    It never calls an exchange. Persistence and restart recovery are provided by
    PaperExecutionService, keeping matching/PnL logic independent from storage.
    """

    def __init__(
        self,
        *,
        starting_balance: Decimal = Decimal("10000"),
        taker_fee_rate: Decimal = Decimal("0.0005"),
        maker_fee_rate: Decimal = Decimal("0.0002"),
        slippage_bps: Decimal = Decimal("2"),
    ) -> None:
        if starting_balance <= 0:
            raise ValueError("starting_balance must be positive")
        self.starting_balance = starting_balance
        self.cash_balance = starting_balance
        self.taker_fee_rate = taker_fee_rate
        self.maker_fee_rate = maker_fee_rate
        self.slippage_bps = slippage_bps
        self.orders: dict[UUID, _Order] = {}
        self.positions: dict[UUID, PaperPositionView] = {}
        self._lock = RLock()

    def restore(self, state: PaperStateView) -> PaperStateView:
        """Replace in-memory state from a validated persisted snapshot."""
        with self._lock:
            self.starting_balance = _q(state.account.starting_balance)
            self.cash_balance = _q(state.account.cash_balance)
            self.orders = {
                order.id: _Order(
                    view=order.model_copy(deep=True),
                    stop_loss=_q(order.stop_loss),
                    take_profit=_q(order.take_profit),
                )
                for order in state.orders
            }
            self.positions = {
                position.id: position.model_copy(deep=True)
                for position in state.positions
            }
            return self.state()

    def reset(self) -> PaperStateView:
        with self._lock:
            self.cash_balance = self.starting_balance
            self.orders.clear()
            self.positions.clear()
            return self.state()

    def submit(self, request: PaperOrderRequest) -> PaperOrderView:
        with self._lock:
            if request.risk_decision != "approved":
                raise PaperBrokerError("risk_decision_rejected")
            if any(
                item.view.client_order_id == request.client_order_id
                for item in self.orders.values()
                if request.client_order_id is not None
            ):
                raise PaperBrokerError("duplicate_client_order_id")

            order_id = uuid4()
            client_order_id = request.client_order_id or f"paper-{order_id.hex}"
            now = datetime.now(timezone.utc)
            order = PaperOrderView(
                id=order_id,
                client_order_id=client_order_id,
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                status="pending",
                quantity=_q(request.quantity),
                reference_price=_q(request.reference_price),
                limit_price=_q(request.limit_price) if request.limit_price is not None else None,
                average_fill_price=None,
                stop_loss=_q(request.stop_loss),
                take_profit=_q(request.take_profit),
                fee=Decimal("0"),
                strategy=request.strategy,
                score=request.score,
                reasons=list(request.reasons),
                created_at=now,
            )
            stored = _Order(view=order, stop_loss=_q(request.stop_loss), take_profit=_q(request.take_profit))
            self.orders[order_id] = stored

            if request.order_type == "market":
                self._fill(stored, request.reference_price, is_maker=False, filled_at=now)
            return stored.view

    def cancel(self, order_id: UUID) -> PaperOrderView:
        with self._lock:
            stored = self._require_order(order_id)
            if stored.view.status != "pending":
                raise PaperBrokerError("only_pending_orders_can_be_cancelled")
            stored.view = stored.view.model_copy(update={"status": "cancelled"})
            return stored.view

    def tick(self, *, symbol: str, price: Decimal, timestamp: datetime | None = None) -> PaperTickResult:
        with self._lock:
            timestamp = timestamp or datetime.now(timezone.utc)
            filled: list[UUID] = []
            closed: list[UUID] = []

            for stored in list(self.orders.values()):
                order = stored.view
                if order.symbol != symbol or order.status != "pending" or order.limit_price is None:
                    continue
                should_fill = (
                    order.side == "long" and price <= order.limit_price
                ) or (
                    order.side == "short" and price >= order.limit_price
                )
                if should_fill:
                    self._fill(stored, order.limit_price, is_maker=True, filled_at=timestamp)
                    filled.append(order.id)

            for position_id, position in list(self.positions.items()):
                if position.symbol != symbol or position.status != "open":
                    continue
                updated = position.model_copy(
                    update={
                        "mark_price": _q(price),
                        "unrealized_pnl": self._gross_pnl(position.side, position.entry_price, price, position.quantity),
                    }
                )
                self.positions[position_id] = updated

                close_reason: str | None = None
                if position.side == "long":
                    if price <= position.stop_loss:
                        close_reason = "stop_loss"
                    elif price >= position.take_profit:
                        close_reason = "take_profit"
                else:
                    if price >= position.stop_loss:
                        close_reason = "stop_loss"
                    elif price <= position.take_profit:
                        close_reason = "take_profit"
                if close_reason:
                    self._close_position(position_id, price, close_reason, timestamp)
                    closed.append(position_id)

            return PaperTickResult(
                symbol=symbol,
                price=_q(price),
                filled_order_ids=filled,
                closed_position_ids=closed,
                account=self.account(),
            )

    def close(self, position_id: UUID, *, price: Decimal, reason: str = "manual") -> PaperPositionView:
        with self._lock:
            return self._close_position(position_id, price, reason, datetime.now(timezone.utc))

    def get_order(self, order_id: UUID) -> PaperOrderView:
        with self._lock:
            return self._require_order(order_id).view

    def get_position(self, position_id: UUID) -> PaperPositionView:
        with self._lock:
            try:
                return self.positions[position_id]
            except KeyError as exc:
                raise PaperBrokerError("position_not_found") from exc

    def state(self) -> PaperStateView:
        with self._lock:
            return PaperStateView(
                account=self.account(),
                orders=[item.view for item in self.orders.values()],
                positions=list(self.positions.values()),
            )

    def account(self) -> PaperAccountView:
        with self._lock:
            open_positions = [p for p in self.positions.values() if p.status == "open"]
            closed_positions = [p for p in self.positions.values() if p.status == "closed"]
            unrealized = sum((p.unrealized_pnl for p in open_positions), Decimal("0"))
            fees = sum((p.fees for p in self.positions.values()), Decimal("0"))
            realized = sum((p.realized_pnl for p in closed_positions), Decimal("0"))
            pending = sum(1 for item in self.orders.values() if item.view.status == "pending")
            return PaperAccountView(
                starting_balance=_q(self.starting_balance),
                cash_balance=_q(self.cash_balance),
                equity=_q(self.cash_balance + unrealized),
                realized_pnl=_q(realized),
                unrealized_pnl=_q(unrealized),
                fees_paid=_q(fees),
                open_positions=len(open_positions),
                pending_orders=pending,
                closed_trades=len(closed_positions),
            )

    def _fill(self, stored: _Order, price: Decimal, *, is_maker: bool, filled_at: datetime) -> None:
        order = stored.view
        if order.status != "pending":
            raise PaperBrokerError("order_not_pending")
        fill_price = _q(price if is_maker else self._apply_slippage(price, order.side, opening=True))
        fee_rate = self.maker_fee_rate if is_maker else self.taker_fee_rate
        entry_fee = _q(fill_price * order.quantity * fee_rate)
        self.cash_balance -= entry_fee
        stored.view = order.model_copy(
            update={
                "status": "filled",
                "average_fill_price": fill_price,
                "fee": entry_fee,
                "filled_at": filled_at,
            }
        )
        position_id = uuid4()
        self.positions[position_id] = PaperPositionView(
            id=position_id,
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            status="open",
            quantity=order.quantity,
            entry_price=fill_price,
            mark_price=fill_price,
            stop_loss=stored.stop_loss,
            take_profit=stored.take_profit,
            unrealized_pnl=Decimal("0"),
            realized_pnl=Decimal("0"),
            fees=entry_fee,
            opened_at=filled_at,
        )

    def _close_position(self, position_id: UUID, price: Decimal, reason: str, timestamp: datetime) -> PaperPositionView:
        position = self.get_position(position_id)
        if position.status != "open":
            raise PaperBrokerError("position_not_open")
        exit_price = _q(self._apply_slippage(price, position.side, opening=False))
        gross = self._gross_pnl(position.side, position.entry_price, exit_price, position.quantity)
        exit_fee = _q(exit_price * position.quantity * self.taker_fee_rate)
        net_after_exit = gross - exit_fee
        self.cash_balance += net_after_exit
        closed = position.model_copy(
            update={
                "status": "closed",
                "mark_price": exit_price,
                "unrealized_pnl": Decimal("0"),
                "realized_pnl": _q(gross - position.fees - exit_fee),
                "fees": _q(position.fees + exit_fee),
                "closed_at": timestamp,
                "close_reason": reason,
            }
        )
        self.positions[position_id] = closed
        return closed

    def _apply_slippage(self, price: Decimal, side: str, *, opening: bool) -> Decimal:
        rate = self.slippage_bps / Decimal("10000")
        is_buy = (side == "long" and opening) or (side == "short" and not opening)
        return price * (Decimal("1") + rate if is_buy else Decimal("1") - rate)

    @staticmethod
    def _gross_pnl(side: str, entry: Decimal, exit_price: Decimal, quantity: Decimal) -> Decimal:
        raw = (exit_price - entry) * quantity if side == "long" else (entry - exit_price) * quantity
        return _q(raw)

    def _require_order(self, order_id: UUID) -> _Order:
        try:
            return self.orders[order_id]
        except KeyError as exc:
            raise PaperBrokerError("order_not_found") from exc
