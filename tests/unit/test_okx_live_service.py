from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest

from app.config.settings import Settings
from app.domain.market import InstrumentInfo
from app.domain.okx_live import (
    LIVE_ARM_PHRASE,
    LIVE_CANCEL_PHRASE,
    LIVE_CLOSE_PHRASE,
    LIVE_ORDER_PHRASE,
    OkxLiveArmRequest,
    OkxLiveCancelRequest,
    OkxLiveCloseRequest,
    OkxLiveMirrorStatus,
    OkxLiveOrderRequest,
)
from app.exchange.okx.errors import OkxPrivateApiError
from app.okx_live import OkxLiveBusyError, OkxLiveSafetyError, OkxLiveUnavailableError
from app.okx_live.service import OkxLiveService


D = Decimal
NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def live_settings(**updates) -> Settings:
    values = {
        "environment": "production",
        "trading_mode": "live",
        "live_trading": True,
        "okx_live_enabled": True,
        "okx_live_allow_order_writes": True,
        "okx_live_api_key": "live-key",
        "okx_live_api_secret": "live-secret",
        "okx_live_api_passphrase": "live-passphrase",
        "api_token": "x" * 40,
        "web_concurrency": 1,
        "okx_live_order_detail_poll_attempts": 1,
        "okx_live_order_detail_poll_delay_seconds": 0,
        "okx_live_max_notional_usdt": "1000",
        "okx_live_max_order_size_contracts": "1",
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


class MutableClock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value


class FakeReadClient:
    def __init__(self) -> None:
        self.position_rows: list[dict[str, str]] = []
        self.pending_rows: list[dict[str, object]] = []
        self.history_rows: list[dict[str, object]] = []
        self.algo_rows: list[dict[str, object]] = []
        self.order_rows: list[dict[str, object]] = []
        self.permissions = "read_only,trade"
        self.ip = "203.0.113.8"

    async def account_config(self):
        return [
            {
                "uid": "live-user",
                "mainUid": "live-main",
                "acctLv": "2",
                "posMode": "net_mode",
                "perm": self.permissions,
                "ip": self.ip,
            }
        ]

    async def balance(self, currency=None):
        return [
            {
                "totalEq": "10000",
                "isoEq": "0",
                "adjEq": "10000",
                "availEq": "9900",
                "uTime": "1786276800000",
                "details": [],
            }
        ]

    async def positions(self, instrument_id=None):
        return [
            row
            for row in self.position_rows
            if instrument_id is None or row["instId"] == instrument_id
        ]

    async def pending_orders(self, instrument_id=None):
        return list(self.pending_rows)

    async def order_history(self, instrument_id=None, limit=100):
        return list(self.history_rows)

    async def pending_algo_orders(self, instrument_id=None):
        return list(self.algo_rows)

    async def order_detail(
        self, instrument_id, *, order_id=None, client_order_id=None
    ):
        return list(self.order_rows)


class FakeExecutionClient:
    def __init__(self, read: FakeReadClient) -> None:
        self.read = read
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.fail_place = False
        self.empty_place_ack = False
        self.include_protection = True
        self.inject_exposure_after_caa = False
        self.order_state = "filled"

    async def max_order_size(
        self, instrument_id, *, margin_mode, price=None, leverage=None
    ):
        self.calls.append(("max_order_size", {"instId": instrument_id}))
        return [{"maxBuy": "10", "maxSell": "10"}]

    async def order_precheck(self, payload):
        self.calls.append(("order_precheck", dict(payload)))
        return [{}]

    async def set_leverage(self, payload):
        self.calls.append(("set_leverage", dict(payload)))
        return [{"sCode": "0"}]

    async def cancel_all_after(self, payload):
        self.calls.append(("cancel_all_after", dict(payload)))
        if self.inject_exposure_after_caa:
            self.read.position_rows = [
                {
                    "posId": "external-position",
                    "instId": "BTC-USDT-SWAP",
                    "posSide": "net",
                    "pos": "0.1",
                    "availPos": "0.1",
                    "avgPx": "100000",
                    "markPx": "100000",
                    "upl": "0",
                    "lever": "1",
                    "mgnMode": "cross",
                }
            ]
        return [{"sCode": "0"}]

    async def place_order(self, payload):
        self.calls.append(("place_order", dict(payload)))
        if self.fail_place:
            raise OkxPrivateApiError("network", code="transport_error")
        protection = payload["attachAlgoOrds"] if self.include_protection else []
        self.read.order_rows = [
            {
                "ordId": "live-order-1",
                "clOrdId": payload["clOrdId"],
                "instId": payload["instId"],
                "side": payload["side"],
                "posSide": payload["posSide"],
                "ordType": "market",
                "state": self.order_state,
                "sz": payload["sz"],
                "accFillSz": payload["sz"],
                "avgPx": "100000",
                "px": "",
                "reduceOnly": "false",
                "attachAlgoOrds": protection,
            }
        ]
        self.read.position_rows = [
            {
                "posId": "position-1",
                "instId": payload["instId"],
                "posSide": payload["posSide"],
                "pos": payload["sz"],
                "availPos": payload["sz"],
                "avgPx": "100000",
                "markPx": "100000",
                "upl": "0",
                "lever": "1",
                "mgnMode": "cross",
            }
        ]
        acknowledgement = [
            {
                "ordId": "live-order-1",
                "clOrdId": payload["clOrdId"],
                "sCode": "0",
            }
        ]
        return [] if self.empty_place_ack else acknowledgement

    async def cancel_order(self, payload):
        self.calls.append(("cancel_order", dict(payload)))
        self.read.order_rows = [
            {
                "ordId": payload.get("ordId", "live-order-1"),
                "clOrdId": payload.get("clOrdId", ""),
                "instId": payload["instId"],
                "side": "buy",
                "posSide": "net",
                "ordType": "limit",
                "state": "canceled",
                "sz": "1",
                "accFillSz": "0",
                "px": "100000",
                "avgPx": "",
                "reduceOnly": "false",
                "attachAlgoOrds": [],
            }
        ]
        return [
            {
                "ordId": payload.get("ordId", "live-order-1"),
                "clOrdId": payload.get("clOrdId", ""),
                "sCode": "0",
            }
        ]

    async def close_position(self, payload):
        self.calls.append(("close_position", dict(payload)))
        self.read.position_rows = []
        return [{"sCode": "0"}]


class FakePublicClient:
    async def instruments(self, instrument_id):
        return [
            InstrumentInfo(
                symbol=instrument_id,
                instrument_id=instrument_id,
                instrument_type="SWAP",
                state="live",
                tick_size=D("0.1"),
                lot_size=D("0.1"),
                minimum_size=D("0.1"),
                contract_value=D("0.01"),
                contract_currency="BTC",
            )
        ]

    async def mark_price(self, instrument_id):
        return D("100000")


class FakeMirrorRepository:
    def __init__(self) -> None:
        self.snapshots: list[dict[str, object]] = []

    async def sync_snapshot(self, **kwargs):
        self.snapshots.append(kwargs)
        return OkxLiveMirrorStatus(available=True)

    async def mirror_status(self):
        return OkxLiveMirrorStatus(available=bool(self.snapshots))

    async def mark_failure(self, code):
        return None


class FakeIntentRepository:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}
        self.events: list[tuple[str, str]] = []
        self.busy = False

    @asynccontextmanager
    async def execution_lock(self):
        if self.busy:
            from app.database.repositories.okx_live_execution import (
                OkxLiveExecutionAuthorityBusy,
            )

            raise OkxLiveExecutionAuthorityBusy(
                "okx_live_global_execution_lock_busy"
            )
        yield

    async def reserve_intent(self, **kwargs):
        key = kwargs["idempotency_key"]
        if key in self.rows:
            from app.database.repositories.okx_live_execution import (
                OkxLiveExecutionIntentReplay,
            )

            raise OkxLiveExecutionIntentReplay(
                "okx_live_idempotency_key_already_used:reserved"
            )
        self.rows[key] = {**kwargs, "status": "reserved"}
        self.events.append((key, "reserved"))

    async def update_intent(self, key, **kwargs):
        self.rows[key].update(kwargs)
        self.events.append((key, kwargs["status"]))


def service_fixture():
    read = FakeReadClient()
    execution = FakeExecutionClient(read)
    intents = FakeIntentRepository()
    clock = MutableClock()
    service = OkxLiveService(
        read,
        FakePublicClient(),
        FakeMirrorRepository(),
        execution_client=execution,
        execution_repository=intents,
        settings=live_settings(),
        clock=clock,
    )
    return service, read, execution, intents, clock


def live_order(**updates) -> OkxLiveOrderRequest:
    values = {
        "instrument_id": "BTC-USDT-SWAP",
        "direction": "long",
        "size": D("0.1"),
        "stop_loss": D("99000"),
        "take_profit": D("102000"),
        "client_order_id": "CTCCLabcdef",
        "confirmation": LIVE_ORDER_PHRASE,
    }
    values.update(updates)
    return OkxLiveOrderRequest(**values)


@pytest.mark.asyncio
async def test_reconcile_persists_atomic_snapshot_and_public_summaries_hide_identity() -> None:
    service, _, _, _, _ = service_fixture()

    snapshot = await service.reconcile()
    account = service.account_summary(snapshot.account_config)
    balance = service.balance_summary(snapshot.balance)

    assert snapshot.persisted is True
    assert account.position_mode == "net_mode"
    assert "live-user" not in account.model_dump_json()
    assert "live-main" not in account.model_dump_json()
    assert balance.total_equity == D("10000")


@pytest.mark.asyncio
async def test_arm_requires_flat_exchange_and_expires_process_locally() -> None:
    service, read, _, _, clock = service_fixture()
    read.position_rows = [
        {
            "posId": "existing",
            "instId": "BTC-USDT-SWAP",
            "posSide": "net",
            "pos": "1",
            "availPos": "1",
            "upl": "0",
        }
    ]
    with pytest.raises(OkxLiveSafetyError, match="positions_block_arm"):
        await service.arm(
            OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
        )

    read.position_rows = []
    status = await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )
    assert status.arm.armed is True

    clock.value += timedelta(seconds=61)
    assert service.arm_status().armed is False


@pytest.mark.asyncio
async def test_place_order_is_protected_idempotent_one_shot_and_auto_disarms() -> None:
    service, _, execution, intents, _ = service_fixture()
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )

    result = await service.place_order(live_order())

    assert result.accepted is True
    assert result.final_state_confirmed is True
    assert result.order is not None
    assert not hasattr(result.order, "raw")
    assert service.arm_status().armed is False
    assert service.arm_status().submissions == 1
    assert [name for name, _ in execution.calls] == [
        "max_order_size",
        "order_precheck",
        "set_leverage",
        "cancel_all_after",
        "place_order",
    ]
    order_payload = execution.calls[-1][1]
    assert order_payload["tag"] == "CTCCV168"
    assert order_payload["sz"] == "0.1"
    protection = order_payload["attachAlgoOrds"][0]
    assert protection["slTriggerPx"] == "99000"
    assert protection["tpTriggerPx"] == "102000"
    assert intents.rows["CTCCLabcdef"]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_never_reaches_order_transport() -> None:
    service, read, execution, intents, _ = service_fixture()
    intents.rows["CTCCLabcdef"] = {"status": "reserved"}
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )

    with pytest.raises(OkxLiveSafetyError, match="idempotency_key_already_used"):
        await service.place_order(live_order())

    assert "place_order" not in [name for name, _ in execution.calls]
    assert read.position_rows == []


@pytest.mark.asyncio
async def test_database_wide_execution_lock_blocks_a_second_instance() -> None:
    service, _, execution, intents, _ = service_fixture()
    intents.busy = True
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )

    with pytest.raises(OkxLiveBusyError, match="global_execution_lock_busy"):
        await service.place_order(live_order())

    assert execution.calls == []


@pytest.mark.asyncio
async def test_ambiguous_order_transport_engages_stop_without_retry_or_auto_close() -> None:
    service, _, execution, intents, _ = service_fixture()
    execution.fail_place = True
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )

    with pytest.raises(OkxPrivateApiError, match="network"):
        await service.place_order(live_order())

    assert service.arm_status().emergency_stop is True
    assert intents.rows["CTCCLabcdef"]["status"] == "ambiguous"
    names = [name for name, _ in execution.calls]
    assert names.count("place_order") == 1
    assert "close_position" not in names


@pytest.mark.asyncio
async def test_missing_protection_for_live_exposure_stops_but_never_silently_closes() -> None:
    service, _, execution, intents, _ = service_fixture()
    execution.include_protection = False
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )

    result = await service.place_order(live_order())

    assert result.final_state_confirmed is False
    assert "protection_not_confirmed_for_live_exposure" in result.warnings
    assert service.arm_status().emergency_stop is True
    assert intents.rows["CTCCLabcdef"]["status"] == "ambiguous"
    assert "close_position" not in [name for name, _ in execution.calls]


@pytest.mark.asyncio
async def test_nonfinal_order_with_requested_protection_is_ambiguous_and_stops() -> None:
    service, _, execution, intents, _ = service_fixture()
    execution.order_state = "live"
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )

    result = await service.place_order(live_order())

    assert result.final_state_confirmed is False
    assert "order_final_state_not_confirmed" in result.warnings
    assert service.arm_status().emergency_stop is True
    assert intents.rows["CTCCLabcdef"]["status"] == "ambiguous"
    assert "close_position" not in [name for name, _ in execution.calls]


@pytest.mark.asyncio
async def test_empty_ack_after_single_live_post_is_persisted_as_ambiguous() -> None:
    service, _, execution, intents, _ = service_fixture()
    execution.empty_place_ack = True
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )

    with pytest.raises(
        OkxLiveUnavailableError, match="write_acknowledgement_invalid"
    ):
        await service.place_order(live_order())

    assert service.arm_status().emergency_stop is True
    assert intents.rows["CTCCLabcdef"]["status"] == "ambiguous"
    names = [name for name, _ in execution.calls]
    assert names.count("place_order") == 1
    assert "close_position" not in names


@pytest.mark.asyncio
async def test_external_exposure_race_is_reconciled_before_actual_order_post() -> None:
    service, _, execution, intents, _ = service_fixture()
    execution.inject_exposure_after_caa = True
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )

    with pytest.raises(
        OkxLiveSafetyError, match="positions_block_place_order_final_check"
    ):
        await service.place_order(live_order())

    assert service.arm_status().emergency_stop is True
    assert intents.rows["CTCCLabcdef"]["status"] == "ambiguous"
    assert "place_order" not in [name for name, _ in execution.calls]


@pytest.mark.asyncio
async def test_cancel_is_exposure_reducing_and_requires_durable_key_not_arm() -> None:
    service, _, execution, intents, _ = service_fixture()

    result = await service.cancel_order(
        OkxLiveCancelRequest(
            instrument_id="BTC-USDT-SWAP",
            order_id="live-order-1",
            idempotency_key="CTCCXcancel1",
            confirmation=LIVE_CANCEL_PHRASE,
        )
    )

    assert result.final_state_confirmed is True
    assert "cancel_order" in [name for name, _ in execution.calls]
    assert intents.rows["CTCCXcancel1"]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_close_position_reconciles_until_exchange_exposure_is_flat() -> None:
    service, read, execution, intents, _ = service_fixture()
    read.position_rows = [
        {
            "posId": "position-1",
            "instId": "BTC-USDT-SWAP",
            "posSide": "net",
            "pos": "0.1",
            "availPos": "0.1",
            "upl": "0",
        }
    ]

    result = await service.close_position(
        OkxLiveCloseRequest(
            instrument_id="BTC-USDT-SWAP",
            idempotency_key="CTCCXclose01",
            confirmation=LIVE_CLOSE_PHRASE,
        )
    )

    assert result.final_state_confirmed is True
    assert read.position_rows == []
    assert "close_position" in [name for name, _ in execution.calls]
    assert intents.rows["CTCCXclose01"]["status"] == "confirmed"
