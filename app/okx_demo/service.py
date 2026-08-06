from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.config.settings import Settings, get_settings
from app.database.repositories.okx_demo import OkxDemoRepository
from app.domain.okx_demo import (
    OkxDemoAccountConfig,
    OkxDemoAlgoOrderView,
    OkxDemoBalanceSnapshot,
    OkxDemoCancelRequest,
    OkxDemoCloseRequest,
    OkxDemoLeverageRequest,
    OkxDemoMirrorStatus,
    OkxDemoOrderAcknowledgement,
    OkxDemoOrderRequest,
    OkxDemoOrderView,
    OkxDemoPositionView,
    OkxDemoReconcileResult,
    OkxDemoStatus,
    OkxDemoWriteResult,
)
from app.exchange.okx.errors import OkxPrivateApiError, OkxPublicApiError
from app.exchange.okx.private_parsers import (
    parse_account_config,
    parse_algo_order,
    parse_balance,
    parse_order,
    parse_position,
)
from app.exchange.okx.private_rest import OkxPrivateRestClient
from app.exchange.okx.public_rest import OkxPublicRestClient
from app.okx_demo import OkxDemoSafetyError, OkxDemoUnavailableError


class OkxDemoService:
    """Manual-only OKX Demo broker with strict safety gates and DB mirroring."""

    def __init__(
        self,
        private_client: OkxPrivateRestClient,
        public_client: OkxPublicRestClient,
        repository: OkxDemoRepository | None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.private_client = private_client
        self.public_client = public_client
        self.repository = repository
        self._lock = asyncio.Lock()
        self._last_exchange_ok_at: datetime | None = None
        self._last_error: str | None = None

    async def status(self) -> OkxDemoStatus:
        mirror = await self._mirror_status()
        blockers: list[str] = []
        if not self.settings.okx_demo_enabled:
            blockers.append("okx_demo_disabled")
        if self.settings.trading_mode != "okx_demo":
            blockers.append("trading_mode_not_okx_demo")
        if not self.settings.okx_demo_credentials_configured:
            blockers.append("okx_demo_credentials_missing")
        if not self.settings.okx_demo_allow_order_writes:
            blockers.append("okx_demo_order_writes_disabled")
        if self.settings.paper_auto_execution:
            blockers.append("paper_auto_execution_must_be_disabled")
        return OkxDemoStatus(
            enabled=self.settings.okx_demo_enabled,
            trading_mode=self.settings.trading_mode,
            credentials_configured=self.settings.okx_demo_credentials_configured,
            writes_enabled=self.settings.okx_demo_allow_order_writes,
            base_url=self.settings.okx_demo_rest_base_url,
            allowed_symbols=self.settings.okx_demo_allowed_symbol_list,
            max_order_size_contracts=self.settings.okx_demo_max_order_size_contracts,
            max_open_positions=self.settings.okx_demo_max_open_positions,
            max_leverage=self.settings.okx_demo_max_leverage,
            require_protection=self.settings.okx_demo_require_protection,
            local_mirror_available=mirror.available,
            mirrored_order_count=mirror.order_count,
            mirrored_position_count=mirror.position_count,
            mirrored_algo_order_count=mirror.algo_order_count,
            last_reconciled_at=mirror.last_reconciled_at,
            last_exchange_ok_at=self._last_exchange_ok_at,
            last_error=self._last_error or mirror.last_error,
            blockers=blockers,
        )

    async def connectivity_check(self) -> OkxDemoStatus:
        self._ensure_read_ready()
        try:
            rows = await self.private_client.account_config()
            if not rows:
                raise OkxDemoUnavailableError("okx_demo_account_config_empty")
            parse_account_config(rows[0])
            balance_rows = await self.private_client.balance()
            if not balance_rows:
                raise OkxDemoUnavailableError("okx_demo_balance_empty")
            parse_balance(balance_rows[0])
            self._last_exchange_ok_at = datetime.now(timezone.utc)
            self._last_error = None
        except Exception as exc:
            self._record_error(exc)
            raise
        return await self.status()

    async def account_config(self) -> OkxDemoAccountConfig:
        self._ensure_read_ready()
        rows = await self.private_client.account_config()
        if not rows:
            raise OkxDemoUnavailableError("okx_demo_account_config_empty")
        self._last_exchange_ok_at = datetime.now(timezone.utc)
        self._last_error = None
        return parse_account_config(rows[0])

    async def balance(self) -> OkxDemoBalanceSnapshot:
        self._ensure_read_ready()
        rows = await self.private_client.balance()
        if not rows:
            raise OkxDemoUnavailableError("okx_demo_balance_empty")
        self._last_exchange_ok_at = datetime.now(timezone.utc)
        self._last_error = None
        return parse_balance(rows[0])

    async def positions(self, instrument_id: str | None = None) -> list[OkxDemoPositionView]:
        self._ensure_read_ready()
        if instrument_id is not None:
            self._ensure_symbol(instrument_id)
        rows = await self.private_client.positions(instrument_id)
        self._last_exchange_ok_at = datetime.now(timezone.utc)
        self._last_error = None
        return [item for item in (parse_position(row) for row in rows) if item.size != 0]

    async def pending_orders(self, instrument_id: str | None = None) -> list[OkxDemoOrderView]:
        self._ensure_read_ready()
        if instrument_id is not None:
            self._ensure_symbol(instrument_id)
        rows = await self.private_client.pending_orders(instrument_id)
        self._last_exchange_ok_at = datetime.now(timezone.utc)
        self._last_error = None
        return [parse_order(row) for row in rows if row.get("ordId")]

    async def pending_algo_orders(self, instrument_id: str | None = None) -> list[OkxDemoAlgoOrderView]:
        self._ensure_read_ready()
        if instrument_id is not None:
            self._ensure_symbol(instrument_id)
        rows = await self.private_client.pending_algo_orders(instrument_id)
        self._last_exchange_ok_at = datetime.now(timezone.utc)
        self._last_error = None
        return [parse_algo_order(row) for row in rows if row.get("algoId")]

    async def order_detail(
        self,
        instrument_id: str,
        *,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> OkxDemoOrderView:
        self._ensure_read_ready()
        self._ensure_symbol(instrument_id)
        if bool(order_id) == bool(client_order_id):
            raise OkxDemoSafetyError("provide_exactly_one_order_identifier")
        rows = await self.private_client.order_detail(
            instrument_id,
            order_id=order_id,
            client_order_id=client_order_id,
        )
        if not rows:
            raise OkxDemoUnavailableError("okx_demo_order_not_found")
        order = parse_order(rows[0])
        await self._persist_orders([order], action="okx_demo_order_detail_synced")
        self._last_exchange_ok_at = datetime.now(timezone.utc)
        self._last_error = None
        return order

    async def reconcile(self) -> OkxDemoReconcileResult:
        self._ensure_read_ready()
        async with self._lock:
            try:
                config_rows, balance_rows, position_rows, pending_rows, history_rows, algo_rows = await asyncio.gather(
                    self.private_client.account_config(),
                    self.private_client.balance(),
                    self.private_client.positions(),
                    self.private_client.pending_orders(),
                    self.private_client.order_history(limit=100),
                    self.private_client.pending_algo_orders(),
                )
                if not config_rows or not balance_rows:
                    raise OkxDemoUnavailableError("okx_demo_reconcile_missing_account_data")
                account_config = parse_account_config(config_rows[0])
                balance = parse_balance(balance_rows[0])
                positions = [
                    item for item in (parse_position(row) for row in position_rows) if item.size != 0
                ]
                pending_orders = [parse_order(row) for row in pending_rows if row.get("ordId")]
                recent_orders = [parse_order(row) for row in history_rows if row.get("ordId")]
                algo_orders = [parse_algo_order(row) for row in algo_rows if row.get("algoId")]
                persisted = False
                if self.repository is not None:
                    await self.repository.sync_snapshot(
                        account_config=account_config,
                        balance=balance,
                        positions=positions,
                        orders=[*recent_orders, *pending_orders],
                        algo_orders=algo_orders,
                    )
                    persisted = True
                self._last_exchange_ok_at = datetime.now(timezone.utc)
                self._last_error = None
                return OkxDemoReconcileResult(
                    account_config=account_config,
                    balance=balance,
                    positions=positions,
                    pending_orders=pending_orders,
                    recent_orders=recent_orders,
                    pending_algo_orders=algo_orders,
                    persisted=persisted,
                )
            except Exception as exc:
                self._record_error(exc)
                if self.repository is not None:
                    try:
                        await self.repository.mark_failure(self._safe_error(exc))
                    except Exception:
                        pass
                raise

    async def place_order(self, request: OkxDemoOrderRequest) -> OkxDemoWriteResult:
        self._ensure_write_ready()
        self._ensure_symbol(request.instrument_id)
        async with self._lock:
            account_config = await self.account_config()
            instrument = await self._instrument(request.instrument_id)
            self._validate_size(request.size, instrument.minimum_size, instrument.lot_size)
            self._validate_price_alignment(request.price, instrument.tick_size, "order_price")
            self._validate_price_alignment(request.stop_loss, instrument.tick_size, "stop_loss")
            self._validate_price_alignment(request.take_profit, instrument.tick_size, "take_profit")

            current_positions, pending_orders = await asyncio.gather(
                self.positions(),
                self.pending_orders(request.instrument_id),
            )
            if any(item.instrument_id == request.instrument_id for item in current_positions):
                raise OkxDemoSafetyError("position_already_open_for_instrument")
            if pending_orders:
                raise OkxDemoSafetyError("pending_order_already_exists_for_instrument")
            if len(current_positions) >= self.settings.okx_demo_max_open_positions:
                raise OkxDemoSafetyError("okx_demo_max_open_positions_reached")

            if self.settings.okx_demo_require_protection and (
                request.stop_loss is None or request.take_profit is None
            ):
                raise OkxDemoSafetyError("protected_order_required")

            reference_price = request.price
            if reference_price is None:
                ticker = await self.public_client.ticker(request.instrument_id)
                reference_price = ticker.last
            self._validate_protection(
                direction=request.direction,
                reference_price=reference_price,
                stop_loss=request.stop_loss,
                take_profit=request.take_profit,
            )

            position_side = self._position_side(account_config, request.direction)
            client_order_id = request.client_order_id or self._client_id("CTCC")
            side = "buy" if request.direction == "long" else "sell"
            payload: dict[str, object] = {
                "instId": request.instrument_id,
                "tdMode": request.margin_mode,
                "clOrdId": client_order_id,
                "side": side,
                "posSide": position_side,
                "ordType": request.order_type,
                "sz": self._decimal_text(request.size),
                "tag": "CTCCV11",
            }
            if request.price is not None:
                payload["px"] = self._decimal_text(request.price)
            if request.stop_loss is not None and request.take_profit is not None:
                payload["attachAlgoOrds"] = [
                    {
                        "attachAlgoClOrdId": self._client_id("CTCCA"),
                        "tpTriggerPx": self._decimal_text(request.take_profit),
                        "tpOrdPx": "-1",
                        "tpTriggerPxType": request.trigger_price_type,
                        "slTriggerPx": self._decimal_text(request.stop_loss),
                        "slOrdPx": "-1",
                        "slTriggerPxType": request.trigger_price_type,
                    }
                ]

            exchange_data = await self.private_client.place_order(payload)
            acknowledgement = self._ack(exchange_data)
            order = await self._poll_order(
                request.instrument_id,
                order_id=acknowledgement.order_id or None,
                client_order_id=acknowledgement.client_order_id or client_order_id,
            )
            warnings: list[str] = []
            if order is not None:
                try:
                    await self._persist_orders([order], action="okx_demo_order_placed")
                except Exception:
                    warnings.append("exchange_acknowledged_but_local_order_mirror_failed")
            else:
                warnings.append("exchange_acknowledged_order_detail_not_yet_available")
            self._last_exchange_ok_at = datetime.now(timezone.utc)
            self._last_error = None
            return OkxDemoWriteResult(
                action="place_order",
                acknowledged=True,
                acknowledgement=acknowledgement,
                order=order,
                exchange_data=exchange_data,
                reconciled=False,
                warnings=warnings,
            )

    async def cancel_order(self, request: OkxDemoCancelRequest) -> OkxDemoWriteResult:
        self._ensure_write_ready()
        self._ensure_symbol(request.instrument_id)
        async with self._lock:
            payload: dict[str, str] = {"instId": request.instrument_id}
            if request.order_id:
                payload["ordId"] = request.order_id
            if request.client_order_id:
                payload["clOrdId"] = request.client_order_id
            exchange_data = await self.private_client.cancel_order(payload)
            acknowledgement = self._ack(exchange_data)
            order = await self._poll_order(
                request.instrument_id,
                order_id=request.order_id or acknowledgement.order_id or None,
                client_order_id=request.client_order_id or acknowledgement.client_order_id,
            )
            warnings: list[str] = []
            if order is not None:
                try:
                    await self._persist_orders([order], action="okx_demo_order_cancelled")
                except Exception:
                    warnings.append("exchange_acknowledged_but_local_order_mirror_failed")
            return OkxDemoWriteResult(
                action="cancel_order",
                acknowledged=True,
                acknowledgement=acknowledgement,
                order=order,
                exchange_data=exchange_data,
                reconciled=False,
                warnings=warnings,
            )

    async def close_position(self, request: OkxDemoCloseRequest) -> OkxDemoWriteResult:
        self._ensure_write_ready()
        self._ensure_symbol(request.instrument_id)
        async with self._lock:
            config = await self.account_config()
            position_side = self._position_side(config, request.direction)
            positions = await self.positions(request.instrument_id)
            if not positions:
                raise OkxDemoSafetyError("no_open_position_for_instrument")
            if config.position_mode == "long_short_mode" and request.direction is None:
                raise OkxDemoSafetyError("direction_required_for_long_short_position_mode")
            payload = {
                "instId": request.instrument_id,
                "mgnMode": request.margin_mode,
                "posSide": position_side,
                "autoCxl": True,
            }
            exchange_data = await self.private_client.close_position(payload)

        # Reconcile only after releasing the write lock. Exchange close requests
        # are asynchronous and the follow-up may still show a closing position.
        warnings: list[str] = []
        reconciled = False
        try:
            await self.reconcile()
            reconciled = True
        except Exception:
            warnings.append("close_acknowledged_but_reconcile_failed")
        return OkxDemoWriteResult(
            action="close_position",
            acknowledged=True,
            exchange_data=exchange_data,
            reconciled=reconciled,
            warnings=warnings,
        )

    async def set_leverage(self, request: OkxDemoLeverageRequest) -> OkxDemoWriteResult:
        self._ensure_write_ready()
        self._ensure_symbol(request.instrument_id)
        if request.leverage > self.settings.okx_demo_max_leverage:
            raise OkxDemoSafetyError("requested_leverage_exceeds_demo_safety_cap")
        async with self._lock:
            config = await self.account_config()
            payload: dict[str, object] = {
                "instId": request.instrument_id,
                "lever": str(request.leverage),
                "mgnMode": request.margin_mode,
            }
            if config.position_mode == "long_short_mode":
                if request.direction is None:
                    raise OkxDemoSafetyError("direction_required_for_long_short_position_mode")
                payload["posSide"] = request.direction
            exchange_data = await self.private_client.set_leverage(payload)
            return OkxDemoWriteResult(
                action="set_leverage",
                acknowledged=True,
                exchange_data=exchange_data,
                reconciled=False,
            )

    async def startup(self) -> None:
        if not self.settings.okx_demo_auto_reconcile_on_start:
            return
        try:
            await self.reconcile()
        except Exception:
            # Startup reconciliation is observable through /status; it must not
            # prevent the API from starting for manual diagnosis.
            pass

    def _ensure_read_ready(self) -> None:
        if not self.settings.okx_demo_enabled:
            raise OkxDemoSafetyError("okx_demo_disabled")
        if self.settings.trading_mode != "okx_demo":
            raise OkxDemoSafetyError("trading_mode_not_okx_demo")
        if not self.settings.okx_demo_credentials_configured:
            raise OkxDemoSafetyError("okx_demo_credentials_missing")
        if self.settings.live_trading or self.settings.auto_trade:
            raise OkxDemoSafetyError("unsafe_global_trading_flags")

    def _ensure_write_ready(self) -> None:
        self._ensure_read_ready()
        if not self.settings.okx_demo_allow_order_writes:
            raise OkxDemoSafetyError("okx_demo_order_writes_disabled")
        if self.settings.paper_auto_execution:
            raise OkxDemoSafetyError("paper_auto_execution_must_be_disabled")

    def _ensure_symbol(self, instrument_id: str) -> None:
        if instrument_id not in self.settings.okx_demo_allowed_symbol_list:
            raise OkxDemoSafetyError("instrument_not_in_okx_demo_allowlist")

    async def _instrument(self, instrument_id: str):
        instruments = await self.public_client.instruments(instrument_id)
        if len(instruments) != 1:
            raise OkxDemoUnavailableError("okx_instrument_not_found")
        instrument = instruments[0]
        if instrument.state != "live":
            raise OkxDemoSafetyError("okx_instrument_not_live")
        return instrument

    def _validate_size(self, size: Decimal, minimum: Decimal, lot: Decimal) -> None:
        if size > self.settings.okx_demo_max_order_size_contracts:
            raise OkxDemoSafetyError("order_size_exceeds_demo_safety_cap")
        if size < minimum:
            raise OkxDemoSafetyError("order_size_below_instrument_minimum")
        if lot <= 0 or size % lot != 0:
            raise OkxDemoSafetyError("order_size_not_aligned_to_lot_size")

    @staticmethod
    def _validate_price_alignment(
        value: Decimal | None,
        tick_size: Decimal,
        field_name: str,
    ) -> None:
        if value is None:
            return
        if tick_size <= 0 or value % tick_size != 0:
            raise OkxDemoSafetyError(f"{field_name}_not_aligned_to_tick_size")

    @staticmethod
    def _validate_protection(
        *,
        direction: str,
        reference_price: Decimal,
        stop_loss: Decimal | None,
        take_profit: Decimal | None,
    ) -> None:
        if stop_loss is None or take_profit is None:
            return
        if direction == "long" and not stop_loss < reference_price < take_profit:
            raise OkxDemoSafetyError("long_protection_prices_invalid")
        if direction == "short" and not take_profit < reference_price < stop_loss:
            raise OkxDemoSafetyError("short_protection_prices_invalid")

    @staticmethod
    def _position_side(config: OkxDemoAccountConfig, direction: str | None) -> str:
        if config.position_mode == "net_mode":
            return "net"
        if config.position_mode == "long_short_mode":
            if direction is None:
                raise OkxDemoSafetyError("direction_required_for_long_short_position_mode")
            return direction
        raise OkxDemoSafetyError("unsupported_okx_position_mode")

    async def _poll_order(
        self,
        instrument_id: str,
        *,
        order_id: str | None,
        client_order_id: str | None,
    ) -> OkxDemoOrderView | None:
        for attempt in range(self.settings.okx_demo_order_detail_poll_attempts):
            try:
                rows = await self.private_client.order_detail(
                    instrument_id,
                    order_id=order_id,
                    client_order_id=client_order_id if not order_id else None,
                )
                if rows:
                    return parse_order(rows[0])
            except OkxPrivateApiError:
                pass
            if attempt + 1 < self.settings.okx_demo_order_detail_poll_attempts:
                await asyncio.sleep(self.settings.okx_demo_order_detail_poll_delay_seconds)
        return None

    async def _persist_orders(self, orders: list[OkxDemoOrderView], *, action: str) -> None:
        if self.repository is not None:
            await self.repository.upsert_orders(orders, action=action)

    async def _mirror_status(self) -> OkxDemoMirrorStatus:
        if self.repository is None:
            return OkxDemoMirrorStatus(available=False)
        try:
            return await self.repository.mirror_status()
        except Exception as exc:
            self._record_error(exc)
            return OkxDemoMirrorStatus(available=False, last_error=self._safe_error(exc))

    @staticmethod
    def _ack(data: list[dict[str, object]]) -> OkxDemoOrderAcknowledgement:
        if not data:
            raise OkxDemoUnavailableError("okx_demo_write_acknowledgement_empty")
        row = data[0]
        return OkxDemoOrderAcknowledgement(
            order_id=str(row.get("ordId") or ""),
            client_order_id=str(row.get("clOrdId") or "") or None,
            exchange_code=str(row.get("sCode") or "0"),
            exchange_message=str(row.get("sMsg") or ""),
        )

    @staticmethod
    def _client_id(prefix: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
        return f"{prefix}{stamp}{uuid4().hex[:10]}"[:32]

    @staticmethod
    def _decimal_text(value: Decimal) -> str:
        text = format(value.normalize(), "f")
        return "0" if text in {"-0", ""} else text

    def _record_error(self, exc: Exception) -> None:
        self._last_error = self._safe_error(exc)

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, (OkxPrivateApiError, OkxPublicApiError)):
            code = getattr(exc, "code", None)
            return f"{exc.__class__.__name__}:{code or 'unknown'}:{str(exc)}"[:250]
        return f"{exc.__class__.__name__}:{str(exc)}"[:250]


settings = get_settings()
if settings.environment != "test":
    from app.database.session import AsyncSessionFactory

    repository = OkxDemoRepository(AsyncSessionFactory)
else:
    repository = None

okx_demo_service = OkxDemoService(
    OkxPrivateRestClient(settings=settings),
    OkxPublicRestClient(),
    repository,
    settings=settings,
)
