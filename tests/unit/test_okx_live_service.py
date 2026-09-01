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
    LIVE_CLEAR_STOP_PHRASE,
    LIVE_LEVERAGE_PHRASE,
    LIVE_ORDER_PHRASE,
    LIVE_UNRESOLVED_CLEAR_PHRASE,
    OkxLiveArmRequest,
    OkxLiveCancelRequest,
    OkxLiveCloseRequest,
    OkxLiveClearStopRequest,
    OkxLiveExecutionIntentView,
    OkxLiveLeverageRequest,
    OkxLiveMirrorStatus,
    OkxLiveOrderRequest,
    OkxLiveSafetyLatchState,
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
        "okx_live_recovery_flat_poll_attempts": 3,
        "okx_live_recovery_flat_poll_delay_seconds": 0.5,
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


async def no_sleep(_: float) -> None:
    return None


class FakeReadClient:
    def __init__(self) -> None:
        self.position_rows: list[dict[str, str]] = []
        self.position_snapshots: list[list[dict[str, str]]] = []
        self.pending_rows: list[dict[str, object]] = []
        self.history_rows: list[dict[str, object]] = []
        self.algo_rows: list[dict[str, object]] = []
        self.delayed_algo_rows: list[dict[str, object]] = []
        self.algo_delay_remaining = 0
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
        if self.position_snapshots:
            self.position_rows = self.position_snapshots.pop(0)
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
        if self.algo_delay_remaining > 0:
            self.algo_delay_remaining -= 1
            return []
        if self.delayed_algo_rows:
            self.algo_rows = self.delayed_algo_rows
            self.delayed_algo_rows = []
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
        self.cancel_state = "canceled"
        self.cancel_fill_size: str | None = None
        self.cancel_creates_position = False
        self.protection_overrides: dict[str, object] = {}
        self.protection_delay_reads = 0
        self.duplicate_protection = False
        self.unrelated_protection = False
        self.unrelated_position = False
        self.leverage_response_override = None

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
        return (
            self.leverage_response_override
            if self.leverage_response_override is not None
            else [
                {
                    **payload,
                    "posSide": payload.get("posSide", "net"),
                    "sCode": "0",
                }
            ]
        )

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
        self.read.algo_rows = []
        if protection:
            attached = protection[0]
            algo_row: dict[str, object] = {
                "algoId": "live-algo-1",
                "algoClOrdId": attached["attachAlgoClOrdId"],
                "instType": "SWAP",
                "instId": payload["instId"],
                "ordType": "oco",
                "state": "live",
                "side": "sell" if payload["side"] == "buy" else "buy",
                "posSide": payload["posSide"],
                "tdMode": payload["tdMode"],
                "reduceOnly": "true" if payload["posSide"] == "net" else "false",
                "sz": payload["sz"],
                "actualSz": "0",
                "tpTriggerPx": attached["tpTriggerPx"],
                "tpTriggerPxType": attached["tpTriggerPxType"],
                "tpOrdPx": attached["tpOrdPx"],
                "slTriggerPx": attached["slTriggerPx"],
                "slTriggerPxType": attached["slTriggerPxType"],
                "slOrdPx": attached["slOrdPx"],
                "amendPxOnTriggerType": "0",
                "failCode": "",
                "triggerTime": "",
            }
            algo_row.update(self.protection_overrides)
            algo_rows = [algo_row]
            if self.duplicate_protection:
                algo_rows.append({**algo_row, "algoId": "live-algo-duplicate"})
            if self.unrelated_protection:
                algo_rows.append(
                    {
                        **algo_row,
                        "algoId": "unrelated-live-algo",
                        "algoClOrdId": "UNRELATED",
                    }
                )
            if self.protection_delay_reads:
                self.read.algo_delay_remaining = self.protection_delay_reads
                self.read.delayed_algo_rows = algo_rows
            else:
                self.read.algo_rows = algo_rows
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
                "pos": (
                    f"-{payload['sz']}"
                    if payload["posSide"] == "net" and payload["side"] == "sell"
                    else payload["sz"]
                ),
                "availPos": payload["sz"],
                "avgPx": "100000",
                "markPx": "100000",
                "upl": "0",
                "lever": "1",
                "mgnMode": payload["tdMode"],
            }
        ]
        if self.unrelated_position:
            self.read.position_rows.append(
                {
                    "posId": "unrelated-position",
                    "instId": "ETH-USDT-SWAP",
                    "posSide": "net",
                    "pos": "0.1",
                    "availPos": "0.1",
                    "avgPx": "4000",
                    "markPx": "4000",
                    "upl": "0",
                    "lever": "1",
                    "mgnMode": "cross",
                }
            )
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
                "state": self.cancel_state,
                "sz": "1",
                "accFillSz": (
                    self.cancel_fill_size
                    if self.cancel_fill_size is not None
                    else "1" if self.cancel_state == "filled" else "0"
                ),
                "px": "100000",
                "avgPx": "100000" if self.cancel_state == "filled" else "",
                "reduceOnly": "false",
                "attachAlgoOrds": [],
            }
        ]
        if self.cancel_creates_position:
            self.read.position_rows = [
                {
                    "posId": "cancel-race-position",
                    "instId": payload["instId"],
                    "posSide": "net",
                    "pos": "1",
                    "availPos": "1",
                    "avgPx": "100000",
                    "markPx": "100000",
                    "upl": "0",
                    "lever": "1",
                    "mgnMode": "cross",
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
        self.safety_latched = False
        self.safety_latch_code: str | None = None
        self.safety_latch_version = 0
        self.fail_latch_read = False
        self.fail_latch_write = False
        self.engage_during_clear = False

    async def sync_snapshot(self, **kwargs):
        self.snapshots.append(kwargs)
        return OkxLiveMirrorStatus(available=True)

    async def mirror_status(self):
        return OkxLiveMirrorStatus(
            available=bool(self.snapshots),
            safety_latched=self.safety_latched,
            safety_latch_code=self.safety_latch_code,
            safety_latch_version=self.safety_latch_version,
        )

    async def mark_failure(self, code):
        return None

    async def safety_latch_status(self):
        if self.fail_latch_read:
            raise RuntimeError("latch read failed")
        return self._latch_state()

    async def engage_safety_latch(self, code):
        if self.fail_latch_write:
            raise RuntimeError("latch write failed")
        self.safety_latched = True
        self.safety_latch_code = code
        self.safety_latch_version += 1
        return self._latch_state()

    async def clear_safety_latch(self, *, expected_version):
        if self.fail_latch_write:
            raise RuntimeError("latch write failed")
        if self.engage_during_clear:
            self.safety_latched = True
            self.safety_latch_code = "concurrent_safety_event"
            self.safety_latch_version += 1
        if not self.safety_latched or self.safety_latch_version != expected_version:
            raise RuntimeError("stale latch version")
        self.safety_latched = False
        self.safety_latch_code = None
        self.safety_latch_version += 1
        return self._latch_state()

    def _latch_state(self):
        return OkxLiveSafetyLatchState(
            latched=self.safety_latched,
            code=self.safety_latch_code,
            version=self.safety_latch_version,
        )


class FakeIntentRepository:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}
        self.events: list[tuple[str, str]] = []
        self.busy = False
        self.fail_unresolved_load = False
        self.fail_resolution = False
        self.revision = 0

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
        now = self._next_timestamp()
        self.rows[key] = {
            **kwargs,
            "status": "reserved",
            "request_hash": kwargs["request_hash"],
            "exchange_order_id": None,
            "detail_codes": [],
            "operator_reconciled_at": None,
            "operator_resolution_code": None,
            "created_at": now,
            "updated_at": now,
        }
        self.events.append((key, "reserved"))

    async def update_intent(self, key, **kwargs):
        self.rows[key].update(kwargs)
        self.rows[key]["updated_at"] = self._next_timestamp()
        self.events.append((key, kwargs["status"]))

    async def load_unresolved_intents(self, *, limit=100):
        if self.fail_unresolved_load:
            raise RuntimeError("intent read failed")
        values = []
        for key, row in list(self.rows.items())[:limit]:
            self._apply_defaults(key, row)
            if row.get("status") in {"reserved", "acknowledged", "ambiguous"} and row.get(
                "operator_reconciled_at"
            ) is None:
                values.append(self._view(key, row))
        return values

    async def load_protection_intents(self, *, limit=1000):
        values = []
        for key, row in list(self.rows.items())[:limit]:
            self._apply_defaults(key, row)
            if (
                row.get("action") == "place_order"
                and row.get("status") != "rejected"
                and row.get("protection_client_order_id") is not None
            ):
                values.append(self._view(key, row))
        return values

    async def mark_unresolved_intents_operator_reconciled(
        self, *, expectations, reconciled_at, resolution_code
    ):
        if self.fail_resolution:
            raise RuntimeError("intent resolution failed")
        unresolved = await self.load_unresolved_intents(limit=1000)
        actual = {
            item.idempotency_key: (item.status, item.updated_at)
            for item in unresolved
        }
        expected = {
            item.idempotency_key: (item.status, item.updated_at)
            for item in expectations
        }
        if actual != expected:
            raise RuntimeError("intent CAS conflict")
        for item in unresolved:
            row = self.rows[item.idempotency_key]
            row["operator_reconciled_at"] = reconciled_at
            row["operator_resolution_code"] = resolution_code
            row["updated_at"] = reconciled_at
        return len(unresolved)

    def _next_timestamp(self):
        self.revision += 1
        return NOW + timedelta(microseconds=self.revision)

    def _apply_defaults(self, key, row):
        now = row.get("updated_at") or self._next_timestamp()
        row.setdefault("idempotency_key", key)
        row.setdefault("request_hash", "0" * 64)
        row.setdefault("action", "cancel_order")
        row.setdefault("instrument_id", "BTC-USDT-SWAP")
        row.setdefault("client_order_id", None)
        row.setdefault("exchange_order_id", None)
        row.setdefault("protection_client_order_id", None)
        row.setdefault("expected_protection_size", None)
        row.setdefault("expected_stop_loss", None)
        row.setdefault("expected_take_profit", None)
        row.setdefault("expected_trigger_price_type", None)
        row.setdefault("detail_codes", [])
        row.setdefault("operator_reconciled_at", None)
        row.setdefault("operator_resolution_code", None)
        row.setdefault("created_at", now)
        row.setdefault("updated_at", now)

    @staticmethod
    def _view(key, row):
        return OkxLiveExecutionIntentView(
            idempotency_key=key,
            request_hash=row["request_hash"],
            action=row["action"],
            status=row["status"],
            instrument_id=row["instrument_id"],
            client_order_id=row["client_order_id"],
            exchange_order_id=row["exchange_order_id"],
            protection_client_order_id=row["protection_client_order_id"],
            expected_protection_size=row["expected_protection_size"],
            expected_stop_loss=row["expected_stop_loss"],
            expected_take_profit=row["expected_take_profit"],
            expected_trigger_price_type=row["expected_trigger_price_type"],
            detail_codes=row["detail_codes"],
            operator_reconciled_at=row["operator_reconciled_at"],
            operator_resolution_code=row["operator_resolution_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


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
        sleeper=no_sleep,
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


async def clear_stop_request(
    service: OkxLiveService,
) -> OkxLiveClearStopRequest:
    expectations = await service.unresolved_intent_expectations()
    return OkxLiveClearStopRequest(
        confirmation=LIVE_CLEAR_STOP_PHRASE,
        expected_unresolved_intents=expectations,
        unresolved_confirmation=(
            LIVE_UNRESOLVED_CLEAR_PHRASE if expectations else None
        ),
    )


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
    await service.clear_emergency_stop(await clear_stop_request(service))
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
    assert order_payload["tag"] == "CTCCV169"
    assert order_payload["sz"] == "0.1"
    protection = order_payload["attachAlgoOrds"][0]
    assert protection["slTriggerPx"] == "99000"
    assert protection["tpTriggerPx"] == "102000"
    assert protection["attachAlgoClOrdId"] == "CTCCAabcdef"
    assert intents.rows["CTCCLabcdef"]["status"] == "confirmed"
    assert intents.rows["CTCCLabcdef"]["protection_client_order_id"] == (
        "CTCCAabcdef"
    )
    assert intents.rows["CTCCLabcdef"]["expected_protection_size"] == D("0.1")
    assert intents.rows["CTCCLabcdef"]["expected_trigger_price_type"] == "mark"
    assert service.arm_status().unresolved_intent_count == 0


@pytest.mark.parametrize(
    "override",
    [
        {"algoClOrdId": "CTCCAwrong01"},
        {"slTriggerPx": "98000"},
        {"tpTriggerPx": "103000"},
        {"slTriggerPxType": "last"},
        {"tpTriggerPxType": "index"},
        {"sz": "0.01"},
        {"instType": "FUTURES"},
        {"ordType": "conditional"},
        {"state": "pause"},
        {"side": "buy"},
        {"posSide": "short"},
        {"tdMode": "isolated"},
        {"tpOrdPx": "101000"},
        {"slOrdPx": "98000"},
        {"reduceOnly": "false"},
        {"actualSz": "0.01"},
        {"failCode": "51000"},
        {"amendPxOnTriggerType": "1"},
    ],
)
@pytest.mark.asyncio
async def test_live_protection_requires_exact_pending_algo_match(
    override: dict[str, object],
) -> None:
    service, _, execution, intents, _ = service_fixture()
    execution.protection_overrides = override
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )

    result = await service.place_order(live_order())

    assert result.final_state_confirmed is False
    assert "protection_not_confirmed_for_live_exposure" in result.warnings
    assert service.arm_status().emergency_stop is True
    assert intents.rows["CTCCLabcdef"]["status"] == "ambiguous"
    assert service.arm_status().unresolved_intent_count == 1


@pytest.mark.asyncio
async def test_live_protection_rejects_duplicate_client_algo_id() -> None:
    service, _, execution, intents, _ = service_fixture()
    execution.duplicate_protection = True
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )

    result = await service.place_order(live_order())

    assert result.final_state_confirmed is False
    assert intents.rows["CTCCLabcdef"]["status"] == "ambiguous"
    assert service.arm_status().emergency_stop is True


@pytest.mark.parametrize("unexpected_state", ["algo", "position"])
@pytest.mark.asyncio
async def test_post_order_confirmation_rejects_unrelated_exchange_state(
    unexpected_state: str,
) -> None:
    service, _, execution, intents, _ = service_fixture()
    execution.unrelated_protection = unexpected_state == "algo"
    execution.unrelated_position = unexpected_state == "position"
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )

    result = await service.place_order(live_order())

    assert result.final_state_confirmed is False
    assert (
        "post_order_state_not_isolated_or_exactly_protected"
        in result.warnings
    )
    assert intents.rows["CTCCLabcdef"]["status"] == "ambiguous"
    assert service.arm_status().emergency_stop is True


@pytest.mark.asyncio
async def test_live_protection_allows_bounded_exchange_propagation_delay() -> None:
    service, _, execution, intents, _ = service_fixture()
    service.settings = live_settings(okx_live_order_detail_poll_attempts=3)
    execution.protection_delay_reads = 2
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )

    result = await service.place_order(live_order())

    assert result.final_state_confirmed is True
    assert intents.rows["CTCCLabcdef"]["status"] == "confirmed"
    assert service.arm_status().emergency_stop is False


@pytest.mark.asyncio
async def test_continuous_reconcile_uses_persisted_exact_protection_geometry() -> None:
    service, read, _, _, _ = service_fixture()
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )
    await service.place_order(live_order())

    await service.reconcile()
    assert service.arm_status().emergency_stop is False

    read.algo_rows[0]["algoClOrdId"] = "CTCCAunrelated"
    await service.reconcile()

    assert service.arm_status().emergency_stop is True
    mirror = service.mirror_repository
    assert mirror is not None
    assert mirror.safety_latched is True


@pytest.mark.asyncio
async def test_reconcile_engages_stop_for_untrusted_unprotected_position() -> None:
    service, read, _, _, _ = service_fixture()
    read.position_rows = [
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
    read.algo_rows = [
        {
            "algoId": "unrelated-algo",
            "algoClOrdId": "UNRELATED",
            "instId": "BTC-USDT-SWAP",
            "ordType": "oco",
            "state": "live",
            "sz": "0.1",
            "tpTriggerPx": "102000",
            "tpTriggerPxType": "mark",
            "slTriggerPx": "99000",
            "slTriggerPxType": "mark",
        }
    ]

    await service.reconcile()

    assert service.arm_status().emergency_stop is True
    assert service.arm_status().last_error == (
        "live_position_protection_not_confirmed"
    )


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_never_reaches_order_transport() -> None:
    service, read, execution, intents, _ = service_fixture()
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )
    intents.rows["CTCCLabcdef"] = {
        "status": "reserved",
        "operator_reconciled_at": NOW,
    }

    with pytest.raises(OkxLiveSafetyError, match="idempotency_key_already_used"):
        await service.place_order(live_order())

    assert "place_order" not in [name for name, _ in execution.calls]
    assert read.position_rows == []


@pytest.mark.parametrize("intent_status", ["reserved", "acknowledged", "ambiguous"])
@pytest.mark.asyncio
async def test_startup_restores_stop_for_unresolved_durable_intent(
    intent_status: str,
) -> None:
    service, _, _, intents, _ = service_fixture()
    intents.rows["CTCCXunresolved"] = {"status": intent_status}

    await service.startup()

    arm = service.arm_status()
    assert arm.armed is False
    assert arm.emergency_stop is True
    assert arm.unresolved_intent_count == 1
    assert arm.last_error == "okx_live_unresolved_execution_intents"


@pytest.mark.asyncio
async def test_unresolved_intent_blocks_new_key_inside_execution_lock() -> None:
    service, _, execution, intents, _ = service_fixture()
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )
    intents.rows["CTCCXolder01"] = {"status": "ambiguous"}

    with pytest.raises(
        OkxLiveSafetyError, match="unresolved_execution_intents"
    ):
        await service.place_order(live_order(client_order_id="CTCCLnewkey1"))

    assert execution.calls == []


@pytest.mark.asyncio
async def test_unresolved_intent_blocks_leverage_but_allows_close() -> None:
    service, read, execution, intents, _ = service_fixture()
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )
    intents.rows["CTCCXolder02"] = {"status": "acknowledged"}

    with pytest.raises(
        OkxLiveSafetyError, match="unresolved_execution_intents"
    ):
        await service.set_leverage(
            OkxLiveLeverageRequest(
                instrument_id="BTC-USDT-SWAP",
                leverage=1,
                idempotency_key="CTCCXlever01",
                confirmation=LIVE_LEVERAGE_PHRASE,
            )
        )

    read.position_rows = [
        {
            "posId": "position-to-close",
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
    result = await service.close_position(
        OkxLiveCloseRequest(
            instrument_id="BTC-USDT-SWAP",
            idempotency_key="CTCCXclose02",
            confirmation=LIVE_CLOSE_PHRASE,
        )
    )

    assert result.final_state_confirmed is True
    assert "close_position" in [name for name, _ in execution.calls]


@pytest.mark.asyncio
async def test_clear_stop_persists_flat_operator_reconciliation_across_restart() -> None:
    service, read, execution, intents, clock = service_fixture()
    intents.rows["CTCCXolder03"] = {"status": "ambiguous"}
    await service.startup()
    assert service.arm_status().emergency_stop is True

    cleared = await service.clear_emergency_stop(
        await clear_stop_request(service)
    )

    row = intents.rows["CTCCXolder03"]
    assert row["operator_reconciled_at"] == NOW
    assert row["operator_resolution_code"] == (
        "operator_confirmed_flat_exchange_state"
    )
    assert cleared.arm.emergency_stop is False
    restarted = OkxLiveService(
        read,
        FakePublicClient(),
        FakeMirrorRepository(),
        execution_client=execution,
        execution_repository=intents,
        settings=live_settings(),
        clock=clock,
        sleeper=no_sleep,
    )
    await restarted.startup()
    assert restarted.arm_status().emergency_stop is False
    assert restarted.arm_status().unresolved_intent_count == 0


@pytest.mark.asyncio
async def test_clear_stop_requires_exact_unresolved_intent_scope() -> None:
    service, _, _, intents, _ = service_fixture()
    intents.rows["CTCCXscope01"] = {"status": "ambiguous"}
    intents.rows["CTCCXscope02"] = {"status": "reserved"}
    expectations = await service.unresolved_intent_expectations()
    request = OkxLiveClearStopRequest(
        confirmation=LIVE_CLEAR_STOP_PHRASE,
        expected_unresolved_intents=expectations[:1],
        unresolved_confirmation=LIVE_UNRESOLVED_CLEAR_PHRASE,
    )

    with pytest.raises(OkxLiveSafetyError, match="expectation_mismatch"):
        await service.clear_emergency_stop(request)

    assert intents.rows["CTCCXscope01"].get("operator_reconciled_at") is None
    assert intents.rows["CTCCXscope02"].get("operator_reconciled_at") is None


@pytest.mark.asyncio
async def test_clear_stop_rejects_non_stable_flat_exchange_state() -> None:
    service, read, _, intents, _ = service_fixture()
    intents.rows["CTCCXstable1"] = {"status": "ambiguous"}
    position = {
        "posId": "late-position",
        "instId": "BTC-USDT-SWAP",
        "posSide": "net",
        "pos": "0.1",
        "availPos": "0.1",
        "upl": "0",
        "mgnMode": "cross",
    }
    read.position_snapshots = [[], [position], []]

    with pytest.raises(OkxLiveSafetyError, match="positions_block"):
        await service.clear_emergency_stop(await clear_stop_request(service))

    assert intents.rows["CTCCXstable1"].get("operator_reconciled_at") is None


@pytest.mark.asyncio
async def test_clear_stop_cannot_erase_newer_durable_safety_event() -> None:
    service, _, _, intents, _ = service_fixture()
    intents.rows["CTCCXcas0001"] = {"status": "ambiguous"}
    await service.emergency_stop()
    mirror = service.mirror_repository
    assert mirror is not None
    mirror.engage_during_clear = True

    with pytest.raises(OkxLiveUnavailableError, match="persist_failed"):
        await service.clear_emergency_stop(await clear_stop_request(service))

    assert mirror.safety_latched is True
    assert mirror.safety_latch_code == "concurrent_safety_event"
    assert service.arm_status().emergency_stop is True


@pytest.mark.asyncio
async def test_clear_stop_never_resolves_intents_until_exchange_is_flat() -> None:
    service, read, _, intents, _ = service_fixture()
    intents.rows["CTCCXolder04"] = {"status": "reserved"}
    read.pending_rows = [
        {
            "ordId": "still-live",
            "instId": "BTC-USDT-SWAP",
            "side": "buy",
            "ordType": "limit",
            "state": "live",
            "sz": "0.1",
            "accFillSz": "0",
        }
    ]
    await service.startup()

    with pytest.raises(
        OkxLiveSafetyError, match="pending_orders_block_clear_emergency_stop"
    ):
        await service.clear_emergency_stop(await clear_stop_request(service))

    assert intents.rows["CTCCXolder04"].get("operator_reconciled_at") is None
    assert service.arm_status().emergency_stop is True


@pytest.mark.asyncio
async def test_intent_recovery_persistence_failure_remains_stopped() -> None:
    service, _, _, intents, _ = service_fixture()
    intents.rows["CTCCXolder05"] = {"status": "ambiguous"}
    intents.fail_resolution = True
    await service.startup()

    with pytest.raises(
        OkxLiveUnavailableError, match="intent_recovery_persist_failed"
    ):
        await service.clear_emergency_stop(await clear_stop_request(service))

    assert service.arm_status().emergency_stop is True
    assert service.arm_status().last_error == (
        "okx_live_intent_recovery_persist_failed"
    )


@pytest.mark.asyncio
async def test_leverage_response_mismatch_disarms_before_live_order_post() -> None:
    service, _, execution, intents, _ = service_fixture()
    execution.leverage_response_override = [
        {
            "instId": "BTC-USDT-SWAP",
            "mgnMode": "cross",
            "posSide": "net",
            "lever": "2",
        }
    ]
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )

    with pytest.raises(
        OkxLiveSafetyError,
        match="okx_live_leverage_exchange_response_mismatch",
    ):
        await service.place_order(live_order(leverage=1))

    assert service.arm_status().armed is False
    assert service.arm_status().emergency_stop is True
    assert intents.rows["CTCCLabcdef"]["status"] == "ambiguous"
    assert "place_order" not in [name for name, _ in execution.calls]


@pytest.mark.asyncio
async def test_database_wide_execution_lock_blocks_a_second_instance() -> None:
    service, _, execution, intents, _ = service_fixture()
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )
    intents.busy = True

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
async def test_cancel_fill_race_is_not_reported_as_confirmed_cancel() -> None:
    service, _, execution, intents, _ = service_fixture()
    execution.cancel_state = "filled"
    execution.cancel_creates_position = True

    result = await service.cancel_order(
        OkxLiveCancelRequest(
            instrument_id="BTC-USDT-SWAP",
            order_id="live-order-1",
            idempotency_key="CTCCXcancel2",
            confirmation=LIVE_CANCEL_PHRASE,
        )
    )

    assert result.final_state_confirmed is False
    assert "order_partially_filled_before_cancel_confirmation" in result.warnings
    assert "cancel_final_state_not_confirmed" in result.warnings
    assert service.arm_status().emergency_stop is True
    assert intents.rows["CTCCXcancel2"]["status"] == "ambiguous"


@pytest.mark.asyncio
async def test_canceled_order_with_nonzero_fill_is_ambiguous() -> None:
    service, _, execution, intents, _ = service_fixture()
    execution.cancel_state = "canceled"
    execution.cancel_fill_size = "0.25"

    result = await service.cancel_order(
        OkxLiveCancelRequest(
            instrument_id="BTC-USDT-SWAP",
            order_id="live-order-1",
            idempotency_key="CTCCXcancel3",
            confirmation=LIVE_CANCEL_PHRASE,
        )
    )

    assert result.final_state_confirmed is False
    assert "order_partially_filled_before_cancel_confirmation" in result.warnings
    assert intents.rows["CTCCXcancel3"]["status"] == "ambiguous"


@pytest.mark.asyncio
async def test_set_leverage_rechecks_arm_and_flat_state_inside_global_lock() -> None:
    service, read, execution, _, _ = service_fixture()
    await service.arm(
        OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
    )
    read.position_rows = [
        {
            "posId": "external-position",
            "instId": "BTC-USDT-SWAP",
            "posSide": "net",
            "pos": "0.1",
            "availPos": "0.1",
            "upl": "0",
            "mgnMode": "cross",
        }
    ]

    with pytest.raises(OkxLiveSafetyError, match="positions_block_set_leverage"):
        await service.set_leverage(
            OkxLiveLeverageRequest(
                instrument_id="BTC-USDT-SWAP",
                leverage=1,
                idempotency_key="CTCCXlever02",
                confirmation=LIVE_LEVERAGE_PHRASE,
            )
        )

    assert [name for name, _ in execution.calls] == []


@pytest.mark.asyncio
async def test_restart_restores_durable_safety_latch_across_processes() -> None:
    service, read, execution, intents, clock = service_fixture()
    await service.emergency_stop()
    restarted = OkxLiveService(
        read,
        FakePublicClient(),
        service.mirror_repository,
        execution_client=execution,
        execution_repository=intents,
        settings=live_settings(),
        clock=clock,
        sleeper=no_sleep,
    )

    await restarted.startup()

    assert restarted.arm_status().emergency_stop is True
    assert restarted.arm_status().safety_latch_code == "operator_emergency_stop"
    with pytest.raises(OkxLiveSafetyError, match="safety_latch_engaged"):
        await restarted.arm(
            OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
        )


@pytest.mark.asyncio
async def test_safety_latch_read_failure_blocks_new_exposure() -> None:
    service, _, _, _, _ = service_fixture()
    mirror = service.mirror_repository
    assert mirror is not None
    mirror.fail_latch_read = True

    with pytest.raises(OkxLiveUnavailableError, match="latch_unavailable"):
        await service.arm(
            OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
        )

    assert service.arm_status().emergency_stop is True


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
