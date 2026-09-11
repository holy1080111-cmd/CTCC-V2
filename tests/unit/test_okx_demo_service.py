from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.domain.market import InstrumentInfo, Ticker
from app.domain.okx_demo import (
    OkxDemoCloseRequest,
    OkxDemoLeverageRequest,
    OkxDemoOrderRequest,
)
from app.okx_demo import OkxDemoSafetyError
from app.okx_demo.service import OkxDemoService


def settings(**updates) -> Settings:
    values = {
        "environment": "test",
        "trading_mode": "okx_demo",
        "paper_auto_execution": False,
        "okx_demo_enabled": True,
        "okx_demo_allow_order_writes": True,
        "okx_demo_api_key": "key",
        "okx_demo_api_secret": "secret",
        "okx_demo_api_passphrase": "pass",
        "okx_demo_order_detail_poll_attempts": 1,
        "okx_demo_order_detail_poll_delay_seconds": 0,
        "okx_demo_max_order_size_contracts": Decimal("1"),
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


class FakePrivate:
    def __init__(
        self,
        *,
        position_mode="net_mode",
        positions=None,
        pending_orders=None,
        pending_algos=None,
    ) -> None:
        self.position_mode = position_mode
        self.position_rows = positions or []
        self._pending_orders = pending_orders or []
        self._pending_algos = pending_algos or []
        self.placed_payload = None
        self.closed_payload = None
        self.leverage_response_override = None
        self.include_order_protection = True
        self.confirm_pending_protection = True
        self.pending_algo_client_id_override = None
        self.pending_algo_row_overrides: dict[str, object] = {}
        self.pending_algo_missing_fields: tuple[str, ...] = ()
        self.pending_algo_duplicate_row_overrides: dict[str, object] | None = None
        self.pending_algo_post_place_empty_calls = 0
        self.pending_algo_post_place_calls = 0
        self.order_detail_calls = 0
        self.order_states: list[str] = ["filled"]
        self.order_fill_size = Decimal("0.1")
        self.order_average_fill_price: Decimal | None = Decimal("100000")
        self.order_average_fill_prices: list[Decimal | None] | None = None

    async def account_config(self):
        return [{"uid": "1", "acctLv": "2", "posMode": self.position_mode}]

    async def balance(self, currency=None):
        return [{"totalEq": "10000", "isoEq": "0", "adjEq": "10000", "availEq": "9000", "details": []}]

    async def positions(self, instrument_id=None):
        return list(self.position_rows)

    async def pending_orders(self, instrument_id=None):
        return list(self._pending_orders)

    async def order_history(self, instrument_id=None, limit=100):
        return []

    async def pending_algo_orders(self, instrument_id=None):
        if self.placed_payload is None:
            return list(self._pending_algos)
        self.pending_algo_post_place_calls += 1
        if (
            not self.confirm_pending_protection
            or self.pending_algo_post_place_calls
            <= self.pending_algo_post_place_empty_calls
        ):
            return []
        attached = self.placed_payload.get("attachAlgoOrds", [])[0]
        row = {
            "algoId": "algo-123",
            "algoClOrdId": (
                self.pending_algo_client_id_override
                or attached["attachAlgoClOrdId"]
            ),
            "instId": self.placed_payload["instId"],
            "ordType": "oco",
            "state": "live",
            "sz": self.placed_payload["sz"],
            "side": "sell" if self.placed_payload["side"] == "buy" else "buy",
            "posSide": self.placed_payload["posSide"],
            "slTriggerPx": attached["slTriggerPx"],
            "tpTriggerPx": attached["tpTriggerPx"],
            "slTriggerPxType": attached["slTriggerPxType"],
            "tpTriggerPxType": attached["tpTriggerPxType"],
        }
        row.update(self.pending_algo_row_overrides)
        for field in self.pending_algo_missing_fields:
            row.pop(field, None)
        rows = [row]
        if self.pending_algo_duplicate_row_overrides is not None:
            rows.append({**row, **self.pending_algo_duplicate_row_overrides})
        return rows

    async def place_order(self, payload):
        self.placed_payload = payload
        return [{"ordId": "123", "clOrdId": payload["clOrdId"], "sCode": "0", "sMsg": ""}]

    async def order_detail(self, instrument_id, *, order_id=None, client_order_id=None):
        self.order_detail_calls += 1
        state = self.order_states[
            min(self.order_detail_calls - 1, len(self.order_states) - 1)
        ]
        average_fill_price = (
            self.order_average_fill_prices[
                min(
                    self.order_detail_calls - 1,
                    len(self.order_average_fill_prices) - 1,
                )
            ]
            if self.order_average_fill_prices
            else self.order_average_fill_price
        )
        payload = self.placed_payload or {}
        return [{
            "ordId": order_id or "123",
            "clOrdId": client_order_id or payload.get("clOrdId", "CTCC1"),
            "instId": instrument_id,
            "side": payload.get("side", "buy"),
            "posSide": payload.get("posSide", "net"),
            "ordType": payload.get("ordType", "market"),
            "state": state,
            "sz": payload.get("sz", "0.1"),
            "accFillSz": str(self.order_fill_size),
            "avgPx": (
                str(average_fill_price)
                if average_fill_price is not None
                else ""
            ),
            "px": payload.get("px", ""),
            "reduceOnly": "false",
            "cTime": "1785858062000",
            "uTime": "1785858063000",
            "attachAlgoOrds": (
                (self.placed_payload or {}).get("attachAlgoOrds", [])
                if self.include_order_protection
                else []
            ),
        }]

    async def cancel_order(self, payload):
        return [{"ordId": payload.get("ordId", "123"), "clOrdId": payload.get("clOrdId", ""), "sCode": "0", "sMsg": ""}]

    async def close_position(self, payload):
        self.closed_payload = payload
        return [{"instId": payload["instId"], "posSide": payload["posSide"]}]

    async def set_leverage(self, payload):
        return (
            self.leverage_response_override
            if self.leverage_response_override is not None
            else [payload]
        )


class FakePublic:
    def __init__(
        self,
        lot=Decimal("0.1"),
        minimum=Decimal("0.1"),
        *,
        bid=Decimal("99999"),
        ask=Decimal("100001"),
        mark=Decimal("100000"),
    ) -> None:
        self.lot = lot
        self.minimum = minimum
        self.bid = bid
        self.ask = ask
        self.mark = mark

    async def instruments(self, instrument_id):
        return [InstrumentInfo(
            symbol=instrument_id,
            instrument_id=instrument_id,
            instrument_type="SWAP",
            state="live",
            tick_size=Decimal("0.1"),
            lot_size=self.lot,
            minimum_size=self.minimum,
            contract_value=Decimal("0.01"),
            contract_currency="BTC",
        )]

    async def ticker(self, instrument_id):
        return Ticker(
            instrument_id=instrument_id,
            last=Decimal("100000"),
            bid=self.bid,
            ask=self.ask,
            bid_size=Decimal("1"),
            ask_size=Decimal("1"),
            open_24h=Decimal("99000"),
            high_24h=Decimal("101000"),
            low_24h=Decimal("98000"),
            volume_24h=Decimal("100"),
            volume_quote_24h=Decimal("10000000"),
            timestamp=datetime.now(timezone.utc),
        )

    async def mark_price(self, instrument_id):
        return self.mark


class FakeRepository:
    def __init__(self) -> None:
        self.orders = []
        self.synced = False

    async def upsert_orders(self, orders, *, action):
        self.orders.extend(orders)

    async def sync_snapshot(self, **kwargs):
        self.synced = True

    async def mirror_status(self):
        from app.domain.okx_demo import OkxDemoMirrorStatus
        return OkxDemoMirrorStatus(available=True)

    async def mark_failure(self, message):
        return None


def request(**updates):
    values = {
        "instrument_id": "BTC-USDT-SWAP",
        "direction": "long",
        "size": Decimal("0.1"),
        "stop_loss": Decimal("99000"),
        "take_profit": Decimal("102000"),
        "confirmation": "OKX_DEMO_ONLY",
    }
    values.update(updates)
    return OkxDemoOrderRequest(**values)


@pytest.mark.asyncio
async def test_place_long_maps_to_demo_buy_and_attached_protection() -> None:
    private = FakePrivate()
    repository = FakeRepository()
    service = OkxDemoService(private, FakePublic(), repository, settings=settings())

    result = await service.place_order(request())

    assert result.acknowledged is True
    assert private.placed_payload["side"] == "buy"
    assert private.placed_payload["posSide"] == "net"
    assert private.placed_payload["sz"] == "0.1"
    attached = private.placed_payload["attachAlgoOrds"][0]
    assert attached["slTriggerPx"] == "99000"
    assert attached["tpTriggerPx"] == "102000"
    assert attached["slOrdPx"] == "-1"
    assert result.protection_confirmed is True
    assert result.protection_client_order_id == attached["attachAlgoClOrdId"]
    assert len(repository.orders) == 1


@pytest.mark.asyncio
async def test_before_submit_callback_can_block_write_after_preflight() -> None:
    preflight_complete = False

    class PreflightPublic(FakePublic):
        async def ticker(self, instrument_id):
            nonlocal preflight_complete
            result = await super().ticker(instrument_id)
            preflight_complete = True
            return result

    private = FakePrivate()
    service = OkxDemoService(private, PreflightPublic(), None, settings=settings())

    def reject_submit() -> None:
        assert preflight_complete is True
        assert private.placed_payload is None
        raise OkxDemoSafetyError("automation_disarmed_during_preflight")

    with pytest.raises(OkxDemoSafetyError, match="automation_disarmed_during_preflight"):
        await service.place_order(request(), before_submit=reject_submit)

    assert private.placed_payload is None
    assert private.order_detail_calls == 0
    assert private.pending_algo_post_place_calls == 0

    private.position_rows = [{
        "instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "0.1", "availPos": "0.1",
        "avgPx": "100000", "markPx": "100000", "upl": "0", "lever": "3", "mgnMode": "cross",
    }]
    close_result = await service.close_position(OkxDemoCloseRequest(
        instrument_id="BTC-USDT-SWAP", confirmation="OKX_DEMO_ONLY",
    ))

    assert close_result.acknowledged is True
    assert private.closed_payload["instId"] == "BTC-USDT-SWAP"
    assert private.closed_payload["posSide"] == "net"


@pytest.mark.asyncio
async def test_before_submit_callback_runs_once_immediately_before_write() -> None:
    events: list[str] = []

    class SubmissionPrivate(FakePrivate):
        async def place_order(self, payload):
            events.append("write")
            return await super().place_order(payload)

    private = SubmissionPrivate()
    service = OkxDemoService(private, FakePublic(), None, settings=settings())

    result = await service.place_order(
        request(), before_submit=lambda: events.append("before_submit"),
    )

    assert events == ["before_submit", "write"]
    assert result.acknowledged is True
    assert result.protection_confirmed is True


@pytest.mark.asyncio
async def test_place_fok_maps_price_bound_and_attached_protection() -> None:
    private = FakePrivate()
    service = OkxDemoService(private, FakePublic(), None, settings=settings())

    result = await service.place_order(
        request(order_type="fok", price=Decimal("100010"))
    )

    assert private.placed_payload["ordType"] == "fok"
    assert private.placed_payload["px"] == "100010"
    assert private.placed_payload["attachAlgoOrds"]
    assert result.order is not None
    assert result.order.order_type == "fok"
    assert result.order.state == "filled"
    assert result.protection_confirmed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("order_type", ["market", "limit", "fok"])
@pytest.mark.parametrize("disable", ["writes", "enabled", "mode"])
async def test_manual_entry_rechecks_authority_after_last_preflight_await(
    order_type, disable,
) -> None:
    config = settings()

    class RevokingPublic(FakePublic):
        async def mark_price(self, instrument_id):
            result = await super().mark_price(instrument_id)
            if disable == "writes":
                config.okx_demo_allow_order_writes = False
            elif disable == "enabled":
                config.okx_demo_enabled = False
            else:
                config.trading_mode = "analysis_only"
            return result

    private = FakePrivate()
    service = OkxDemoService(private, RevokingPublic(), None, settings=config)
    callbacks = []
    with pytest.raises(OkxDemoSafetyError):
        await service.place_order(
            request(
                order_type=order_type,
                price=None if order_type == "market" else Decimal("100010"),
            ),
            before_submit=lambda: callbacks.append("submission_started"),
        )
    assert private.placed_payload is None
    assert private.order_detail_calls == 0
    assert callbacks == []


@pytest.mark.asyncio
async def test_fok_order_detail_poll_waits_for_terminal_state() -> None:
    private = FakePrivate()
    private.order_states = ["live", "filled"]
    service = OkxDemoService(
        private,
        FakePublic(),
        None,
        settings=settings(okx_demo_order_detail_poll_attempts=2),
    )

    result = await service.place_order(
        request(order_type="fok", price=Decimal("100010"))
    )

    assert private.order_detail_calls == 2
    assert result.order is not None
    assert result.order.state == "filled"


@pytest.mark.asyncio
async def test_fok_order_detail_poll_waits_for_positive_average_fill_price() -> None:
    private = FakePrivate()
    private.order_states = ["filled", "filled"]
    private.order_average_fill_prices = [None, Decimal("100000")]
    service = OkxDemoService(
        private,
        FakePublic(),
        None,
        settings=settings(okx_demo_order_detail_poll_attempts=2),
    )

    result = await service.place_order(
        request(order_type="fok", price=Decimal("100010"))
    )

    assert private.order_detail_calls == 2
    assert result.order is not None
    assert result.order.state == "filled"
    assert result.order.average_fill_price == Decimal("100000")


@pytest.mark.parametrize("terminal_state", ["canceled", "mmp_canceled"])
@pytest.mark.asyncio
async def test_zero_fill_fok_is_terminal_without_protection_poll(
    terminal_state: str,
) -> None:
    private = FakePrivate()
    private.order_states = [terminal_state]
    private.order_fill_size = Decimal("0")
    private.order_average_fill_price = None
    service = OkxDemoService(private, FakePublic(), None, settings=settings())

    result = await service.place_order(
        request(order_type="fok", price=Decimal("100010"))
    )

    assert result.order is not None
    assert result.order.state == terminal_state
    assert result.order.accumulated_fill_size == 0
    assert result.protection_confirmed is False
    assert "fok_order_not_filled" in result.warnings
    assert private.pending_algo_post_place_calls == 0




@pytest.mark.asyncio
async def test_place_ack_without_confirmed_protection_is_explicitly_unsafe() -> None:
    private = FakePrivate()
    private.confirm_pending_protection = False
    service = OkxDemoService(private, FakePublic(), None, settings=settings())

    result = await service.place_order(request())

    assert result.acknowledged is True
    assert result.protection_confirmed is False
    assert (
        "exchange_acknowledged_but_protection_not_confirmed"
        in result.warnings
    )


@pytest.mark.asyncio
async def test_place_rejects_preexisting_pending_algo_order() -> None:
    private = FakePrivate(
        pending_algos=[{
            "algoId": "existing",
            "instId": "BTC-USDT-SWAP",
            "ordType": "oco",
            "state": "live",
            "sz": "0.1",
        }]
    )
    service = OkxDemoService(private, FakePublic(), None, settings=settings())

    with pytest.raises(
        OkxDemoSafetyError,
        match="pending_algo_order_already_exists_for_instrument",
    ):
        await service.place_order(request())

    assert private.placed_payload is None


@pytest.mark.asyncio
async def test_protection_confirmation_requires_exact_unique_client_id() -> None:
    private = FakePrivate()
    private.pending_algo_client_id_override = "WRONGPROTECTIONID"
    service = OkxDemoService(private, FakePublic(), None, settings=settings())

    result = await service.place_order(request())

    assert result.acknowledged is True
    assert result.protection_confirmed is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sz", "0.01"),
        ("sz", "0.2"),
        ("sz", "0"),
        ("sz", "-0.1"),
        ("sz", "NaN"),
        ("sz", "Infinity"),
        ("sz", "invalid"),
        ("side", "buy"),
        ("posSide", "long"),
        ("posSide", "short"),
        ("attachAlgoClOrdId", "CONFLICTINGPROTECTIONID"),
    ],
)
@pytest.mark.asyncio
async def test_protection_confirmation_rejects_mismatched_protection(
    field: str, value: str,
) -> None:
    private = FakePrivate()
    private.pending_algo_row_overrides = {field: value}
    service = OkxDemoService(private, FakePublic(), None, settings=settings())

    result = await service.place_order(request())

    assert result.acknowledged is True
    assert result.protection_confirmed is False
    assert "exchange_acknowledged_but_protection_not_confirmed" in result.warnings


@pytest.mark.parametrize(
    "field",
    [
        "algoClOrdId", "instId", "sz", "side", "posSide", "slTriggerPx",
        "tpTriggerPx", "slTriggerPxType", "tpTriggerPxType",
    ],
)
@pytest.mark.parametrize("missing", [True, False], ids=["missing", "empty"])
@pytest.mark.asyncio
async def test_protection_confirmation_requires_complete_exchange_evidence(
    field: str, missing: bool,
) -> None:
    private = FakePrivate()
    if missing:
        private.pending_algo_missing_fields = (field,)
    else:
        private.pending_algo_row_overrides = {field: ""}
    service = OkxDemoService(private, FakePublic(), None, settings=settings())

    result = await service.place_order(request())

    assert result.acknowledged is True
    assert result.protection_confirmed is False
    assert "exchange_acknowledged_but_protection_not_confirmed" in result.warnings


@pytest.mark.parametrize(
    "duplicate_updates",
    [{}, {"sz": "0.01"}, {"side": "buy"}, {"instId": "ETH-USDT-SWAP"}],
)
@pytest.mark.asyncio
async def test_protection_confirmation_rejects_duplicate_client_id(
    duplicate_updates: dict[str, object],
) -> None:
    private = FakePrivate()
    private.pending_algo_duplicate_row_overrides = duplicate_updates
    service = OkxDemoService(private, FakePublic(), None, settings=settings())

    result = await service.place_order(request())

    assert result.acknowledged is True
    assert result.protection_confirmed is False
    assert "exchange_acknowledged_but_protection_not_confirmed" in result.warnings


@pytest.mark.parametrize("position_mode", ["net_mode", "long_short_mode"])
@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("matching_position_side", [True, False])
@pytest.mark.asyncio
async def test_protection_confirmation_uses_exchange_account_position_mode(
    position_mode: str, direction: str, matching_position_side: bool,
) -> None:
    private = FakePrivate(position_mode=position_mode)
    expected_position_side = "net" if position_mode == "net_mode" else direction
    if not matching_position_side:
        private.pending_algo_row_overrides = {
            "posSide": direction if position_mode == "net_mode" else "net",
        }
    service = OkxDemoService(private, FakePublic(), None, settings=settings())
    prices = (
        {"stop_loss": Decimal(102000), "take_profit": Decimal(99000)}
        if direction == "short"
        else {}
    )

    result = await service.place_order(request(direction=direction, **prices))

    assert private.placed_payload["posSide"] == expected_position_side
    assert result.acknowledged is True
    assert result.protection_confirmed is matching_position_side


@pytest.mark.asyncio
async def test_protection_confirmation_uses_bounded_pending_algo_poll() -> None:
    private = FakePrivate()
    private.pending_algo_post_place_empty_calls = 1
    service = OkxDemoService(
        private,
        FakePublic(),
        None,
        settings=settings(okx_demo_order_detail_poll_attempts=2),
    )

    result = await service.place_order(request())

    assert result.protection_confirmed is True
    assert private.pending_algo_post_place_calls == 2


@pytest.mark.asyncio
async def test_long_short_mode_uses_direction_as_position_side() -> None:
    private = FakePrivate(position_mode="long_short_mode")
    service = OkxDemoService(private, FakePublic(), None, settings=settings())
    await service.place_order(request(direction="short", stop_loss=Decimal("102000"), take_profit=Decimal("99000")))
    assert private.placed_payload["side"] == "sell"
    assert private.placed_payload["posSide"] == "short"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "account_row",
    [{}, {"posMode": None}, {"posMode": ""}, {"posMode": "unknown_mode"}],
)
async def test_place_rejects_unknown_or_missing_account_position_mode(
    account_row: dict[str, object],
) -> None:
    class UnknownModePrivate(FakePrivate):
        async def account_config(self):
            return [account_row]

    private = UnknownModePrivate()
    service = OkxDemoService(private, FakePublic(), None, settings=settings())

    with pytest.raises(OkxDemoSafetyError, match="unsupported_okx_position_mode"):
        await service.place_order(request())

    assert private.placed_payload is None
    assert private.order_detail_calls == 0
    assert private.pending_algo_post_place_calls == 0


@pytest.mark.asyncio
async def test_place_rejects_unaligned_contract_size() -> None:
    service = OkxDemoService(FakePrivate(), FakePublic(lot=Decimal("0.1")), None, settings=settings())
    with pytest.raises(OkxDemoSafetyError, match="order_size_not_aligned"):
        await service.place_order(request(size=Decimal("0.15")))


@pytest.mark.asyncio
async def test_place_rejects_unprotected_order_when_required() -> None:
    service = OkxDemoService(FakePrivate(), FakePublic(), None, settings=settings())
    with pytest.raises(OkxDemoSafetyError, match="protected_order_required"):
        await service.place_order(request(stop_loss=None, take_profit=None))


@pytest.mark.asyncio
async def test_demo_market_order_rechecks_executable_quote_and_mark() -> None:
    service = OkxDemoService(
        FakePrivate(),
        FakePublic(ask=Decimal("102500")),
        None,
        settings=settings(),
    )
    with pytest.raises(
        OkxDemoSafetyError,
        match="okx_demo_mark_execution_basis_exceeds_limit",
    ):
        await service.place_order(request())

    service = OkxDemoService(
        FakePrivate(),
        FakePublic(mark=Decimal("100025")),
        None,
        settings=settings(),
    )
    with pytest.raises(
        OkxDemoSafetyError,
        match="long_protection_prices_invalid",
    ):
        await service.place_order(
            request(
                stop_loss=Decimal("99990"),
                take_profit=Decimal("100020"),
            )
        )


def test_demo_order_model_rejects_unreviewed_trigger_price_sources() -> None:
    with pytest.raises(ValidationError):
        request(trigger_price_type="last")


@pytest.mark.asyncio
async def test_place_rejects_existing_position_for_same_instrument() -> None:
    private = FakePrivate(positions=[{
        "instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "0.1", "availPos": "0.1",
        "avgPx": "100000", "markPx": "100000", "upl": "0", "lever": "3", "mgnMode": "cross"
    }])
    service = OkxDemoService(private, FakePublic(), None, settings=settings())
    with pytest.raises(OkxDemoSafetyError, match="position_already_open"):
        await service.place_order(request())


@pytest.mark.asyncio
async def test_write_disabled_blocks_order_before_network_call() -> None:
    private = FakePrivate()
    service = OkxDemoService(
        private,
        FakePublic(),
        None,
        settings=settings(okx_demo_allow_order_writes=False),
    )
    with pytest.raises(OkxDemoSafetyError, match="writes_disabled"):
        await service.place_order(request())
    assert private.placed_payload is None


@pytest.mark.asyncio
async def test_set_leverage_requires_matching_exchange_response_fields() -> None:
    private = FakePrivate()
    private.leverage_response_override = [
        {
            "instId": "BTC-USDT-SWAP",
            "mgnMode": "isolated",
            "posSide": "net",
            "lever": "10",
        }
    ]
    service = OkxDemoService(
        private,
        FakePublic(),
        None,
        settings=settings(okx_demo_max_leverage=20),
    )

    with pytest.raises(
        OkxDemoSafetyError,
        match="okx_demo_leverage_exchange_response_mismatch",
    ):
        await service.set_leverage(
            OkxDemoLeverageRequest(
                instrument_id="BTC-USDT-SWAP",
                leverage=20,
                margin_mode="isolated",
                direction="long",
                confirmation="OKX_DEMO_ONLY",
            )
        )


@pytest.mark.asyncio
async def test_reconcile_persists_exchange_snapshot() -> None:
    repository = FakeRepository()
    service = OkxDemoService(FakePrivate(), FakePublic(), repository, settings=settings())
    result = await service.reconcile()
    assert result.persisted is True
    assert repository.synced is True


@pytest.mark.asyncio
async def test_place_rejects_existing_pending_order_for_same_instrument() -> None:
    private = FakePrivate(pending_orders=[{
        "ordId": "pending-1", "clOrdId": "CTCCPENDING1", "instId": "BTC-USDT-SWAP",
        "side": "buy", "posSide": "net", "ordType": "limit", "state": "live",
        "sz": "0.1", "accFillSz": "0", "px": "90000", "avgPx": "",
        "reduceOnly": "false"
    }])
    service = OkxDemoService(private, FakePublic(), None, settings=settings())
    with pytest.raises(OkxDemoSafetyError, match="pending_order_already_exists"):
        await service.place_order(request())
    assert private.placed_payload is None


@pytest.mark.asyncio
async def test_place_rejects_price_not_aligned_to_tick_size() -> None:
    service = OkxDemoService(FakePrivate(), FakePublic(), None, settings=settings())
    with pytest.raises(OkxDemoSafetyError, match="stop_loss_not_aligned_to_tick_size"):
        await service.place_order(request(stop_loss=Decimal("99000.05")))
