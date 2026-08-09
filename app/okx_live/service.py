from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from typing import AsyncIterator, Awaitable, Callable

from app.config.settings import Settings, get_settings
from app.database.repositories.okx_live import (
    OkxLiveAccountIdentityError,
    OkxLiveRepository,
)
from app.database.repositories.okx_live_execution import (
    OkxLiveExecutionIntentConflict,
    OkxLiveExecutionIntentReplay,
    OkxLiveExecutionAuthorityBusy,
    OkxLiveExecutionRepository,
)
from app.domain.market import InstrumentInfo
from app.domain.okx_live import (
    OkxLiveAccountConfig,
    OkxLiveAccountSummary,
    OkxLiveAlgoOrderSummary,
    OkxLiveAlgoOrderView,
    OkxLiveArmRequest,
    OkxLiveArmStatus,
    OkxLiveBalanceSnapshot,
    OkxLiveBalanceSummary,
    OkxLiveCancelRequest,
    OkxLiveCloseRequest,
    OkxLiveLeverageRequest,
    OkxLiveMirrorStatus,
    OkxLiveOrderAcknowledgement,
    OkxLiveOrderRequest,
    OkxLiveOrderSummary,
    OkxLiveOrderView,
    OkxLivePositionSummary,
    OkxLivePositionView,
    OkxLiveReconcileResult,
    OkxLiveReconcileSummary,
    OkxLiveStatus,
    OkxLiveWriteResult,
)
from app.exchange.okx.errors import OkxPrivateApiError, OkxPublicApiError
from app.exchange.okx.live_private_parsers import (
    parse_live_account_config,
    parse_live_algo_order,
    parse_live_balance,
    parse_live_order,
    parse_live_position,
)
from app.exchange.okx.private_api import OkxPrivateApiClient
from app.exchange.okx.public_rest import OkxPublicRestClient
from app.okx_live import OkxLiveBusyError, OkxLiveSafetyError, OkxLiveUnavailableError


D = Decimal
Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]
_FINAL_ORDER_STATES = frozenset({"filled", "canceled", "mmp_canceled"})
_OPEN_ORDER_STATES = frozenset({"live", "partially_filled", "effective"})


class OkxLiveService:
    """Production OKX boundary with process-local authority and durable intents.

    The exchange remains authoritative. A REST acknowledgement is never treated
    as final order state, and ambiguous writes engage the emergency stop without
    silently closing an exchange position.
    """

    def __init__(
        self,
        read_client: OkxPrivateApiClient,
        public_client: OkxPublicRestClient,
        mirror_repository: OkxLiveRepository | None,
        *,
        execution_client: OkxPrivateApiClient | None = None,
        execution_repository: OkxLiveExecutionRepository | None = None,
        settings: Settings | None = None,
        clock: Clock | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.read_client = read_client
        self.execution_client = execution_client
        self.public_client = public_client
        self.mirror_repository = mirror_repository
        self.execution_repository = execution_repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleeper or asyncio.sleep
        self._write_lock = asyncio.Lock()
        self._armed_until: datetime | None = None
        self._baseline_equity: Decimal | None = None
        self._submissions = 0
        self._emergency_stop = False
        self._automation_running = False
        self._last_capability = None
        self._last_exchange_ok_at: datetime | None = None
        self._last_error: str | None = None

    async def status(self) -> OkxLiveStatus:
        mirror = await self._mirror_status()
        blockers = self._configuration_blockers(self._last_capability)
        read_blockers = {
            "okx_live_disabled",
            "trading_mode_not_live",
            "okx_live_credentials_missing",
            "okx_live_read_permission_missing",
            "okx_live_api_key_not_ip_bound",
            "okx_live_withdraw_permission_forbidden",
            "okx_live_unknown_api_permission",
        }
        return OkxLiveStatus(
            enabled=self.settings.okx_live_enabled,
            trading_mode=self.settings.trading_mode,
            credentials_configured=self.settings.okx_live_credentials_configured,
            read_ready=(
                self._last_capability is not None
                and not any(item in read_blockers for item in blockers)
            ),
            live_trading_enabled=self.settings.live_trading,
            writes_enabled=self.settings.okx_live_allow_order_writes,
            automation_enabled=self.settings.okx_live_auto_execution,
            base_url=self.settings.okx_live_rest_base_url,
            allowed_symbols=self.settings.okx_live_allowed_symbol_list,
            max_order_size_contracts=self.settings.okx_live_max_order_size_contracts,
            max_notional_usdt=self.settings.okx_live_max_notional_usdt,
            max_open_positions=self.settings.okx_live_max_open_positions,
            max_leverage=self.settings.okx_live_max_leverage,
            require_protection=self.settings.okx_live_require_protection,
            require_ip_bound_key=self.settings.okx_live_require_ip_bound_key,
            forbid_withdraw_permission=self.settings.okx_live_forbid_withdraw_permission,
            capability=self._last_capability,
            local_mirror_available=mirror.available,
            mirrored_order_count=mirror.order_count,
            mirrored_position_count=mirror.position_count,
            mirrored_algo_order_count=mirror.algo_order_count,
            last_reconciled_at=mirror.last_reconciled_at,
            last_exchange_ok_at=self._last_exchange_ok_at,
            last_error=self._last_error or mirror.last_error,
            blockers=blockers,
            arm=self.arm_status(),
        )

    def arm_status(self) -> OkxLiveArmStatus:
        self._expire_arm_if_needed()
        return OkxLiveArmStatus(
            armed=self._armed_until is not None,
            emergency_stop=self._emergency_stop,
            expires_at=self._armed_until,
            baseline_equity=self._baseline_equity,
            submissions=self._submissions,
            max_submissions=self.settings.okx_live_max_submissions_per_arm,
            automation_running=self._automation_running,
            last_error=self._last_error,
        )

    def set_automation_running(self, value: bool) -> None:
        self._automation_running = value

    async def connectivity_check(self) -> OkxLiveStatus:
        await self.account_config()
        await self.balance()
        return await self.status()

    async def account_config(self) -> OkxLiveAccountConfig:
        self._ensure_read_ready()
        rows = await self.read_client.account_config()
        if not rows:
            raise OkxLiveUnavailableError("okx_live_account_config_empty")
        config = parse_live_account_config(rows[0])
        self._validate_read_capability(config)
        self._last_capability = config.capability
        self._record_success()
        return config

    async def balance(self) -> OkxLiveBalanceSnapshot:
        self._ensure_read_ready()
        rows = await self.read_client.balance()
        if not rows:
            raise OkxLiveUnavailableError("okx_live_balance_empty")
        value = parse_live_balance(rows[0])
        self._record_success()
        return value

    async def positions(
        self, instrument_id: str | None = None
    ) -> list[OkxLivePositionView]:
        self._ensure_read_ready()
        if instrument_id is not None:
            self._ensure_symbol(instrument_id)
        rows = await self.read_client.positions(instrument_id)
        result = [parse_live_position(row) for row in rows]
        self._record_success()
        return result

    async def pending_orders(
        self, instrument_id: str | None = None
    ) -> list[OkxLiveOrderView]:
        self._ensure_read_ready()
        if instrument_id is not None:
            self._ensure_symbol(instrument_id)
        rows = await self.read_client.pending_orders(instrument_id)
        result = [parse_live_order(row) for row in rows]
        self._record_success()
        return result

    async def pending_algo_orders(
        self, instrument_id: str | None = None
    ) -> list[OkxLiveAlgoOrderView]:
        self._ensure_read_ready()
        if instrument_id is not None:
            self._ensure_symbol(instrument_id)
        rows = await self.read_client.pending_algo_orders(instrument_id)
        result = [parse_live_algo_order(row) for row in rows]
        self._record_success()
        return result

    async def order_detail(
        self,
        instrument_id: str,
        *,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> OkxLiveOrderView:
        self._ensure_read_ready()
        self._ensure_symbol(instrument_id)
        if bool(order_id) == bool(client_order_id):
            raise OkxLiveSafetyError("provide_exactly_one_order_identifier")
        rows = await self.read_client.order_detail(
            instrument_id,
            order_id=order_id,
            client_order_id=client_order_id,
        )
        if not rows:
            raise OkxLiveUnavailableError("okx_live_order_not_found")
        value = parse_live_order(rows[0])
        self._record_success()
        return value

    async def reconcile(self) -> OkxLiveReconcileResult:
        self._ensure_read_ready()
        try:
            config = await self.account_config()
            (
                balance_rows,
                position_rows,
                pending_rows,
                history_rows,
                algo_rows,
            ) = await asyncio.gather(
                self.read_client.balance(),
                self.read_client.positions(),
                self.read_client.pending_orders(),
                self.read_client.order_history(limit=100),
                self.read_client.pending_algo_orders(),
            )
            if not balance_rows:
                raise OkxLiveUnavailableError("okx_live_balance_empty")
            balance = parse_live_balance(balance_rows[0])
            positions = [parse_live_position(row) for row in position_rows]
            pending = [parse_live_order(row) for row in pending_rows]
            recent = [parse_live_order(row) for row in history_rows]
            algo = [parse_live_algo_order(row) for row in algo_rows]
            persisted = False
            if self.mirror_repository is not None:
                await self.mirror_repository.sync_snapshot(
                    account_config=config,
                    balance=balance,
                    positions=positions,
                    orders=[*recent, *pending],
                    algo_orders=algo,
                )
                persisted = True
            reconciled_at = self._now()
            self._last_exchange_ok_at = reconciled_at
            self._last_error = None
            return OkxLiveReconcileResult(
                account_config=config,
                balance=balance,
                positions=positions,
                pending_orders=pending,
                recent_orders=recent,
                pending_algo_orders=algo,
                persisted=persisted,
                reconciled_at=reconciled_at,
            )
        except Exception as exc:
            self._record_error(exc)
            if self.mirror_repository is not None:
                try:
                    await self.mirror_repository.mark_failure(
                        self._safe_failure_code(exc)
                    )
                except Exception:
                    pass
            raise

    async def arm(self, request: OkxLiveArmRequest) -> OkxLiveStatus:
        self._ensure_write_configuration()
        if self._emergency_stop:
            raise OkxLiveSafetyError("okx_live_emergency_stop_must_be_cleared")
        if request.duration_seconds > self.settings.okx_live_arm_ttl_seconds:
            raise OkxLiveSafetyError("okx_live_arm_duration_exceeds_configured_ttl")
        if self._write_lock.locked():
            raise OkxLiveBusyError("okx_live_write_in_progress")
        snapshot = await self.reconcile()
        self._validate_write_capability(snapshot.account_config)
        self._ensure_flat(snapshot, action="arm")
        if snapshot.balance.total_equity <= 0:
            raise OkxLiveSafetyError("okx_live_equity_not_positive")
        self._baseline_equity = snapshot.balance.total_equity
        self._submissions = 0
        self._armed_until = self._now() + timedelta(seconds=request.duration_seconds)
        self._last_error = None
        return await self.status()

    async def disarm(self) -> OkxLiveStatus:
        self._disarm_local()
        return await self.status()

    async def emergency_stop(self) -> OkxLiveStatus:
        self._engage_emergency_stop("operator_emergency_stop")
        return await self.status()

    async def clear_emergency_stop(self) -> OkxLiveStatus:
        snapshot = await self.reconcile()
        self._ensure_flat(snapshot, action="clear_emergency_stop")
        self._emergency_stop = False
        self._last_error = None
        self._disarm_local()
        return await self.status()

    async def place_order(self, request: OkxLiveOrderRequest) -> OkxLiveWriteResult:
        self._ensure_write_configuration()
        self._require_armed()
        self._ensure_symbol(request.instrument_id)
        if self._write_lock.locked():
            raise OkxLiveBusyError("okx_live_write_in_progress")
        async with self._execution_guard():
            self._require_armed()
            snapshot = await self.reconcile()
            self._validate_write_capability(snapshot.account_config)
            self._ensure_flat(snapshot, action="place_order")
            self._enforce_session_loss(snapshot.balance.total_equity)
            instrument, reference_price = await self._validate_order_market(
                request
            )
            position_side = self._position_side(
                snapshot.account_config, request.direction
            )
            side = "buy" if request.direction == "long" else "sell"
            payload = self._order_payload(
                request,
                side=side,
                position_side=position_side,
            )
            await self._validate_exchange_max_size(
                request,
                side=side,
                reference_price=reference_price,
            )
            request_hash = self._request_hash(request)
            await self._reserve_intent(
                idempotency_key=request.client_order_id,
                request_hash=request_hash,
                action="place_order",
                instrument_id=request.instrument_id,
                client_order_id=request.client_order_id,
            )

            stage = "order_precheck"
            try:
                await self._execution().order_precheck(payload)
                stage = "set_leverage"
                await self._execution().set_leverage(
                    {
                        "instId": request.instrument_id,
                        "lever": str(request.leverage),
                        "mgnMode": request.margin_mode,
                        **(
                            {"posSide": request.direction}
                            if snapshot.account_config.position_mode
                            == "long_short_mode"
                            else {}
                        ),
                    }
                )
                stage = "cancel_all_after"
                await self._execution().cancel_all_after(
                    {
                        "timeOut": str(
                            self.settings.okx_live_cancel_all_after_seconds
                        ),
                        "tag": self.settings.okx_live_order_tag,
                    }
                )
                stage = "final_pre_submit_reconcile"
                final_snapshot = await self.reconcile()
                self._validate_write_capability(final_snapshot.account_config)
                self._ensure_flat(
                    final_snapshot,
                    action="place_order_final_check",
                )
                self._enforce_session_loss(
                    final_snapshot.balance.total_equity
                )
                stage = "final_market_recheck"
                final_reference_price = await self.public_client.mark_price(
                    request.instrument_id
                )
                self._validate_order_at_price(
                    request,
                    instrument,
                    final_reference_price,
                )
                stage = "place_order"
                exchange_data = await self._execution().place_order(payload)
            except Exception as exc:
                await self._record_write_failure(
                    request.client_order_id,
                    exc,
                    stage=stage,
                )
                self._disarm_local()
                raise

            self._submissions += 1
            acknowledgement = await self._ack_after_write(
                request.client_order_id,
                exchange_data,
                stop_code="invalid_acknowledgement_after_live_order",
                detail_code="place_order_ack_invalid",
            )
            try:
                await self._update_intent_or_stop(
                    request.client_order_id,
                    stop_code="intent_update_failed_after_order_ack",
                    status="acknowledged",
                    exchange_order_id=acknowledgement.order_id or None,
                    detail_codes=["okx_rest_acknowledged"],
                )
            finally:
                if self.settings.okx_live_auto_disarm:
                    self._disarm_local(keep_submission_count=True)

            order = await self._poll_order(
                request.instrument_id,
                order_id=acknowledgement.order_id or None,
                client_order_id=(
                    None if acknowledgement.order_id else request.client_order_id
                ),
            )
            warnings: list[str] = []
            reconciled = False
            post_snapshot: OkxLiveReconcileResult | None = None
            try:
                post_snapshot = await self.reconcile()
                reconciled = True
            except Exception:
                warnings.append("post_order_reconcile_failed")

            protection_confirmed = self._protection_confirmed(
                order, post_snapshot, request.instrument_id
            )
            exposure_present = self._exposure_present(
                post_snapshot, request.instrument_id
            )
            final_confirmed = (
                order is not None
                and reconciled
                and order.state in _FINAL_ORDER_STATES
                and (not exposure_present or protection_confirmed)
            )
            if order is None:
                warnings.append("order_detail_not_confirmed")
            elif order.state not in _FINAL_ORDER_STATES:
                warnings.append("order_final_state_not_confirmed")
            if post_snapshot is None:
                warnings.append("post_order_exchange_state_unavailable")
                self._engage_emergency_stop("post_order_reconcile_unavailable")
            elif not protection_confirmed and exposure_present:
                warnings.append("protection_not_confirmed_for_live_exposure")
                self._engage_emergency_stop("live_position_protection_not_confirmed")
            if not final_confirmed and not self._emergency_stop:
                self._engage_emergency_stop("live_order_final_state_unconfirmed")

            await self._update_intent_or_stop(
                request.client_order_id,
                stop_code="intent_finalization_failed_after_order_ack",
                status="confirmed" if final_confirmed else "ambiguous",
                exchange_order_id=acknowledgement.order_id or None,
                detail_codes=(
                    ["order_state_and_protection_confirmed"]
                    if final_confirmed
                    else ["order_final_state_or_protection_unconfirmed"]
                ),
            )
            return OkxLiveWriteResult(
                action="place_order",
                accepted=True,
                final_state_confirmed=final_confirmed,
                acknowledgement=acknowledgement,
                order=None if order is None else self.order_summary(order),
                reconciled=reconciled,
                warnings=warnings,
            )

    async def cancel_order(self, request: OkxLiveCancelRequest) -> OkxLiveWriteResult:
        self._ensure_write_configuration()
        self._ensure_symbol(request.instrument_id)
        async with self._execution_guard():
            config = await self.account_config()
            self._validate_write_capability(config)
            payload: dict[str, str] = {"instId": request.instrument_id}
            if request.order_id:
                payload["ordId"] = request.order_id
            else:
                payload["clOrdId"] = str(request.client_order_id)
            await self._reserve_intent(
                idempotency_key=request.idempotency_key,
                request_hash=self._request_hash(request),
                action="cancel_order",
                instrument_id=request.instrument_id,
                client_order_id=request.client_order_id,
            )
            self._disarm_local()
            try:
                exchange_data = await self._execution().cancel_order(payload)
            except Exception as exc:
                await self._record_write_failure(
                    request.idempotency_key,
                    exc,
                    stage="cancel_order",
                )
                raise
            acknowledgement = await self._ack_after_write(
                request.idempotency_key,
                exchange_data,
                stop_code="invalid_acknowledgement_after_live_cancel",
                detail_code="cancel_order_ack_invalid",
            )
            await self._update_intent_or_stop(
                request.idempotency_key,
                stop_code="intent_update_failed_after_cancel_ack",
                status="acknowledged",
                exchange_order_id=acknowledgement.order_id or request.order_id,
                detail_codes=["cancel_rest_acknowledged"],
            )
            order = await self._poll_order(
                request.instrument_id,
                order_id=request.order_id or acknowledgement.order_id or None,
                client_order_id=request.client_order_id,
            )
            confirmed = order is not None and order.state in _FINAL_ORDER_STATES
            warnings = []
            if order is not None and order.state == "filled":
                warnings.append("order_filled_before_cancel_confirmation")
            if not confirmed:
                warnings.append("cancel_final_state_not_confirmed")
                self._engage_emergency_stop("cancel_final_state_unconfirmed")
            reconciled = await self._best_effort_reconcile(warnings)
            await self._update_intent_or_stop(
                request.idempotency_key,
                stop_code="intent_finalization_failed_after_cancel_ack",
                status="confirmed" if confirmed else "ambiguous",
                exchange_order_id=acknowledgement.order_id or request.order_id,
                detail_codes=[
                    "cancel_final_state_confirmed"
                    if confirmed
                    else "cancel_final_state_unconfirmed"
                ],
            )
            return OkxLiveWriteResult(
                action="cancel_order",
                accepted=True,
                final_state_confirmed=confirmed,
                acknowledgement=acknowledgement,
                order=None if order is None else self.order_summary(order),
                reconciled=reconciled,
                warnings=warnings,
            )

    async def close_position(self, request: OkxLiveCloseRequest) -> OkxLiveWriteResult:
        self._ensure_write_configuration()
        self._ensure_symbol(request.instrument_id)
        async with self._execution_guard():
            snapshot = await self.reconcile()
            self._validate_write_capability(snapshot.account_config)
            matching = [
                item
                for item in snapshot.positions
                if item.instrument_id == request.instrument_id and item.size != 0
            ]
            if not matching:
                raise OkxLiveSafetyError("okx_live_position_not_found")
            position_side = self._position_side(
                snapshot.account_config, request.direction
            )
            await self._reserve_intent(
                idempotency_key=request.idempotency_key,
                request_hash=self._request_hash(request),
                action="close_position",
                instrument_id=request.instrument_id,
            )
            self._disarm_local()
            try:
                await self._execution().close_position(
                    {
                        "instId": request.instrument_id,
                        "mgnMode": request.margin_mode,
                        "posSide": position_side,
                        "autoCxl": True,
                    }
                )
            except Exception as exc:
                await self._record_write_failure(
                    request.idempotency_key,
                    exc,
                    stage="close_position",
                )
                raise
            await self._update_intent_or_stop(
                request.idempotency_key,
                stop_code="intent_update_failed_after_close_ack",
                status="acknowledged",
                detail_codes=["close_rest_acknowledged"],
            )
            confirmed = False
            reconciled = False
            warnings: list[str] = []
            for attempt in range(self.settings.okx_live_order_detail_poll_attempts):
                try:
                    post = await self.reconcile()
                    reconciled = True
                    remaining = [
                        item
                        for item in post.positions
                        if item.instrument_id == request.instrument_id
                        and item.size != 0
                        and (
                            position_side == "net"
                            or item.position_side == position_side
                        )
                    ]
                    if not remaining:
                        confirmed = True
                        break
                except Exception:
                    pass
                if attempt + 1 < self.settings.okx_live_order_detail_poll_attempts:
                    await self._sleep(
                        self.settings.okx_live_order_detail_poll_delay_seconds
                    )
            if not confirmed:
                warnings.append("position_close_not_yet_confirmed")
                self._engage_emergency_stop("position_close_unconfirmed")
            await self._update_intent_or_stop(
                request.idempotency_key,
                stop_code="intent_finalization_failed_after_close_ack",
                status="confirmed" if confirmed else "ambiguous",
                detail_codes=[
                    "position_close_confirmed"
                    if confirmed
                    else "position_close_unconfirmed"
                ],
            )
            return OkxLiveWriteResult(
                action="close_position",
                accepted=True,
                final_state_confirmed=confirmed,
                reconciled=reconciled,
                warnings=warnings,
            )

    async def set_leverage(self, request: OkxLiveLeverageRequest) -> OkxLiveWriteResult:
        self._ensure_write_configuration()
        self._require_armed()
        self._ensure_symbol(request.instrument_id)
        if request.leverage > self.settings.okx_live_max_leverage:
            raise OkxLiveSafetyError("requested_leverage_exceeds_live_safety_cap")
        async with self._execution_guard():
            config = await self.account_config()
            self._validate_write_capability(config)
            position_side = self._position_side(config, request.direction)
            payload: dict[str, object] = {
                "instId": request.instrument_id,
                "lever": str(request.leverage),
                "mgnMode": request.margin_mode,
            }
            if position_side != "net":
                payload["posSide"] = position_side
            await self._reserve_intent(
                idempotency_key=request.idempotency_key,
                request_hash=self._request_hash(request),
                action="set_leverage",
                instrument_id=request.instrument_id,
            )
            try:
                await self._execution().set_leverage(payload)
            except Exception as exc:
                await self._record_write_failure(
                    request.idempotency_key,
                    exc,
                    stage="set_leverage",
                )
                self._disarm_local()
                raise
            await self._update_intent_or_stop(
                request.idempotency_key,
                stop_code="intent_update_failed_after_leverage_ack",
                status="acknowledged",
                detail_codes=["leverage_rest_acknowledged"],
            )
            await self._update_intent_or_stop(
                request.idempotency_key,
                stop_code="intent_finalization_failed_after_leverage_ack",
                status="confirmed",
                detail_codes=["leverage_exchange_response_confirmed"],
            )
            return OkxLiveWriteResult(
                action="set_leverage",
                accepted=True,
                final_state_confirmed=True,
            )

    async def startup(self) -> None:
        self._disarm_local()
        if not self.settings.okx_live_auto_reconcile_on_start:
            return
        try:
            await self.reconcile()
        except Exception:
            pass

    async def shutdown(self) -> None:
        self._disarm_local()
        self._automation_running = False

    async def _validate_order_market(
        self, request: OkxLiveOrderRequest
    ) -> tuple[InstrumentInfo, Decimal]:
        instruments, reference_price = await asyncio.gather(
            self.public_client.instruments(request.instrument_id),
            self.public_client.mark_price(request.instrument_id),
        )
        if len(instruments) != 1:
            raise OkxLiveUnavailableError("okx_live_instrument_not_found")
        instrument = instruments[0]
        if instrument.state != "live" or instrument.instrument_type != "SWAP":
            raise OkxLiveSafetyError("okx_live_instrument_not_live_swap")
        self._validate_order_at_price(request, instrument, reference_price)
        return instrument, reference_price

    def _validate_order_at_price(
        self,
        request: OkxLiveOrderRequest,
        instrument: InstrumentInfo,
        reference_price: Decimal,
    ) -> None:
        if reference_price <= 0:
            raise OkxLiveSafetyError("okx_live_reference_price_invalid")
        self._validate_size(request.size, instrument)
        self._validate_tick(request.stop_loss, instrument.tick_size, "stop_loss")
        self._validate_tick(request.take_profit, instrument.tick_size, "take_profit")
        if request.direction == "long" and not (
            request.stop_loss < reference_price < request.take_profit
        ):
            raise OkxLiveSafetyError("okx_live_long_protection_prices_invalid")
        if request.direction == "short" and not (
            request.take_profit < reference_price < request.stop_loss
        ):
            raise OkxLiveSafetyError("okx_live_short_protection_prices_invalid")
        if request.leverage > self.settings.okx_live_max_leverage:
            raise OkxLiveSafetyError("requested_leverage_exceeds_live_safety_cap")
        notional = self._contract_notional(request.size, reference_price, instrument)
        if notional > self.settings.okx_live_max_notional_usdt:
            raise OkxLiveSafetyError("okx_live_order_notional_exceeds_safety_cap")

    async def _validate_exchange_max_size(
        self,
        request: OkxLiveOrderRequest,
        *,
        side: str,
        reference_price: Decimal,
    ) -> None:
        rows = await self._execution().max_order_size(
            request.instrument_id,
            margin_mode=request.margin_mode,
            price=self._decimal_text(reference_price),
            leverage=str(request.leverage),
        )
        if not rows:
            raise OkxLiveUnavailableError("okx_live_max_order_size_empty")
        key = "maxBuy" if side == "buy" else "maxSell"
        value = rows[0].get(key)
        if value in (None, "") or D(str(value)) < request.size:
            raise OkxLiveSafetyError("okx_live_order_size_exceeds_exchange_maximum")

    def _validate_size(self, size: Decimal, instrument: InstrumentInfo) -> None:
        if size > self.settings.okx_live_max_order_size_contracts:
            raise OkxLiveSafetyError("okx_live_order_size_exceeds_safety_cap")
        if size < instrument.minimum_size:
            raise OkxLiveSafetyError("okx_live_order_size_below_instrument_minimum")
        if instrument.lot_size <= 0 or size % instrument.lot_size != 0:
            raise OkxLiveSafetyError("okx_live_order_size_not_aligned_to_lot_size")

    @staticmethod
    def _validate_tick(value: Decimal, tick: Decimal, field: str) -> None:
        if tick <= 0 or value % tick != 0:
            raise OkxLiveSafetyError(f"okx_live_{field}_not_aligned_to_tick_size")

    @staticmethod
    def _contract_notional(
        size: Decimal, reference_price: Decimal, instrument: InstrumentInfo
    ) -> Decimal:
        contract_value = instrument.contract_value
        contract_currency = (instrument.contract_currency or "").upper()
        if contract_value is None or contract_value <= 0:
            raise OkxLiveSafetyError("okx_live_contract_value_missing")
        base, quote, _ = instrument.instrument_id.split("-", 2)
        if contract_currency == base:
            return size * contract_value * reference_price
        if contract_currency == quote:
            return size * contract_value
        raise OkxLiveSafetyError("okx_live_contract_value_currency_unsupported")

    def _order_payload(
        self,
        request: OkxLiveOrderRequest,
        *,
        side: str,
        position_side: str,
    ) -> dict[str, object]:
        return {
            "instId": request.instrument_id,
            "tdMode": request.margin_mode,
            "clOrdId": request.client_order_id,
            "tag": self.settings.okx_live_order_tag,
            "side": side,
            "posSide": position_side,
            "ordType": "market",
            "sz": self._decimal_text(request.size),
            "attachAlgoOrds": [
                {
                    "tpTriggerPx": self._decimal_text(request.take_profit),
                    "tpOrdPx": "-1",
                    "tpTriggerPxType": request.trigger_price_type,
                    "slTriggerPx": self._decimal_text(request.stop_loss),
                    "slOrdPx": "-1",
                    "slTriggerPxType": request.trigger_price_type,
                }
            ],
        }

    def _ensure_read_ready(self) -> None:
        if not self.settings.okx_live_enabled:
            raise OkxLiveSafetyError("okx_live_disabled")
        if self.settings.trading_mode != "live":
            raise OkxLiveSafetyError("trading_mode_not_live")
        if not self.settings.okx_live_credentials_configured:
            raise OkxLiveSafetyError("okx_live_credentials_missing")
        if self.settings.auto_trade or self.settings.paper_auto_execution:
            raise OkxLiveSafetyError("conflicting_automatic_execution_enabled")
        if (
            self.settings.okx_demo_allow_order_writes
            or self.settings.okx_demo_auto_execution
        ):
            raise OkxLiveSafetyError("okx_demo_execution_must_be_disabled")

    def _ensure_write_configuration(self) -> None:
        self._ensure_read_ready()
        blockers = self._configuration_blockers(self._last_capability)
        write_blockers = [
            item
            for item in blockers
            if item
            not in {
                "okx_live_read_permission_missing",
                "okx_live_trade_permission_missing",
                "okx_live_api_key_not_ip_bound",
                "okx_live_withdraw_permission_forbidden",
                "okx_live_unknown_api_permission",
            }
        ]
        if write_blockers:
            raise OkxLiveSafetyError(";".join(write_blockers))
        if (
            self.mirror_repository is None
            or self.execution_client is None
            or self.execution_repository is None
        ):
            raise OkxLiveSafetyError("okx_live_execution_persistence_unavailable")

    def _configuration_blockers(self, capability) -> list[str]:
        blockers: list[str] = []
        if not self.settings.okx_live_enabled:
            blockers.append("okx_live_disabled")
        if self.settings.trading_mode != "live":
            blockers.append("trading_mode_not_live")
        if not self.settings.okx_live_credentials_configured:
            blockers.append("okx_live_credentials_missing")
        if not self.settings.live_trading:
            blockers.append("live_trading_disabled")
        if not self.settings.okx_live_allow_order_writes:
            blockers.append("okx_live_order_writes_disabled")
        if self.settings.environment != "production":
            blockers.append("production_environment_required")
        if not self.settings.api_token_is_safe:
            blockers.append("safe_api_token_required")
        if self.settings.web_concurrency != 1:
            blockers.append("single_worker_required")
        if capability is not None:
            if not capability.read_permission:
                blockers.append("okx_live_read_permission_missing")
            if not capability.trade_permission:
                blockers.append("okx_live_trade_permission_missing")
            if capability.unknown_permissions:
                blockers.append("okx_live_unknown_api_permission")
            if self.settings.okx_live_require_ip_bound_key and not capability.ip_bound:
                blockers.append("okx_live_api_key_not_ip_bound")
            if (
                self.settings.okx_live_forbid_withdraw_permission
                and capability.withdraw_permission
            ):
                blockers.append("okx_live_withdraw_permission_forbidden")
        return sorted(set(blockers))

    def _validate_read_capability(self, config: OkxLiveAccountConfig) -> None:
        capability = config.capability
        if not capability.read_permission:
            raise OkxLiveSafetyError("okx_live_read_permission_missing")
        if capability.unknown_permissions:
            raise OkxLiveSafetyError("okx_live_unknown_api_permission")
        if self.settings.okx_live_require_ip_bound_key and not capability.ip_bound:
            raise OkxLiveSafetyError("okx_live_api_key_not_ip_bound")
        if (
            self.settings.okx_live_forbid_withdraw_permission
            and capability.withdraw_permission
        ):
            raise OkxLiveSafetyError("okx_live_withdraw_permission_forbidden")

    def _validate_write_capability(self, config: OkxLiveAccountConfig) -> None:
        self._validate_read_capability(config)
        if not config.capability.trade_permission:
            raise OkxLiveSafetyError("okx_live_trade_permission_missing")
        if not config.uid or not config.main_uid:
            raise OkxLiveSafetyError("okx_live_account_identity_incomplete")

    def _ensure_symbol(self, instrument_id: str) -> None:
        if instrument_id not in self.settings.okx_live_allowed_symbol_list:
            raise OkxLiveSafetyError("instrument_not_in_okx_live_allowlist")

    def _require_armed(self) -> None:
        self._expire_arm_if_needed()
        if self._emergency_stop:
            raise OkxLiveSafetyError("okx_live_emergency_stop_engaged")
        if self._armed_until is None:
            raise OkxLiveSafetyError("okx_live_not_armed")
        if self._submissions >= self.settings.okx_live_max_submissions_per_arm:
            self._disarm_local(keep_submission_count=True)
            raise OkxLiveSafetyError("okx_live_arm_submission_limit_reached")

    def _ensure_flat(self, snapshot: OkxLiveReconcileResult, *, action: str) -> None:
        if not self.settings.okx_live_require_flat_start:
            raise OkxLiveSafetyError("okx_live_flat_start_guard_disabled")
        if any(item.size != 0 for item in snapshot.positions):
            raise OkxLiveSafetyError(f"okx_live_positions_block_{action}")
        if snapshot.pending_orders:
            raise OkxLiveSafetyError(f"okx_live_pending_orders_block_{action}")
        if snapshot.pending_algo_orders:
            raise OkxLiveSafetyError(f"okx_live_algo_orders_block_{action}")

    def _enforce_session_loss(self, equity: Decimal) -> None:
        baseline = self._baseline_equity
        if baseline is None or baseline <= 0:
            raise OkxLiveSafetyError("okx_live_arm_baseline_missing")
        loss = max(D("0"), baseline - equity)
        if loss / baseline >= self.settings.okx_live_session_loss_limit_pct:
            self._engage_emergency_stop("okx_live_session_loss_limit_reached")
            raise OkxLiveSafetyError("okx_live_session_loss_limit_reached")

    @staticmethod
    def _position_side(config: OkxLiveAccountConfig, direction: str | None) -> str:
        if config.position_mode == "net_mode":
            return "net"
        if config.position_mode == "long_short_mode":
            if direction is None:
                raise OkxLiveSafetyError(
                    "direction_required_for_long_short_position_mode"
                )
            return direction
        raise OkxLiveSafetyError("unsupported_okx_live_position_mode")

    async def _poll_order(
        self,
        instrument_id: str,
        *,
        order_id: str | None,
        client_order_id: str | None,
    ) -> OkxLiveOrderView | None:
        last_observed: OkxLiveOrderView | None = None
        for attempt in range(self.settings.okx_live_order_detail_poll_attempts):
            try:
                rows = await self.read_client.order_detail(
                    instrument_id,
                    order_id=order_id,
                    client_order_id=client_order_id if not order_id else None,
                )
                if rows:
                    last_observed = parse_live_order(rows[0])
                    if last_observed.state in _FINAL_ORDER_STATES:
                        return last_observed
            except Exception:
                pass
            if attempt + 1 < self.settings.okx_live_order_detail_poll_attempts:
                await self._sleep(
                    self.settings.okx_live_order_detail_poll_delay_seconds
                )
        return last_observed

    @staticmethod
    def _protection_confirmed(
        order: OkxLiveOrderView | None,
        snapshot: OkxLiveReconcileResult | None,
        instrument_id: str,
    ) -> bool:
        if order is not None:
            for item in order.attached_algo_orders:
                if item.get("tpTriggerPx") not in (None, "") and item.get(
                    "slTriggerPx"
                ) not in (None, ""):
                    return True
        if snapshot is None:
            return False
        for item in snapshot.pending_algo_orders:
            if (
                item.instrument_id == instrument_id
                and item.take_profit_trigger_price is not None
                and item.stop_loss_trigger_price is not None
            ):
                return True
        return False

    @staticmethod
    def _exposure_present(
        snapshot: OkxLiveReconcileResult | None, instrument_id: str
    ) -> bool:
        if snapshot is None:
            return True
        return any(
            item.instrument_id == instrument_id and item.size != 0
            for item in snapshot.positions
        )

    async def _best_effort_reconcile(self, warnings: list[str]) -> bool:
        try:
            await self.reconcile()
            return True
        except Exception:
            warnings.append("post_write_reconcile_failed")
            return False

    async def _reserve_intent(self, **kwargs) -> None:
        repository = self.execution_repository
        if repository is None:
            raise OkxLiveSafetyError("okx_live_execution_persistence_unavailable")
        try:
            await repository.reserve_intent(**kwargs)
        except (OkxLiveExecutionIntentConflict, OkxLiveExecutionIntentReplay) as exc:
            raise OkxLiveSafetyError(str(exc)) from exc

    @asynccontextmanager
    async def _execution_guard(self) -> AsyncIterator[None]:
        if self._write_lock.locked():
            raise OkxLiveBusyError("okx_live_write_in_progress")
        repository = self.execution_repository
        if repository is None:
            raise OkxLiveSafetyError("okx_live_execution_persistence_unavailable")
        async with self._write_lock:
            try:
                async with repository.execution_lock():
                    yield
            except OkxLiveExecutionAuthorityBusy as exc:
                raise OkxLiveBusyError(str(exc)) from exc

    async def _update_intent(self, idempotency_key: str, **kwargs) -> None:
        repository = self.execution_repository
        if repository is None:
            raise OkxLiveSafetyError("okx_live_execution_persistence_unavailable")
        await repository.update_intent(idempotency_key, **kwargs)

    async def _update_intent_or_stop(
        self,
        idempotency_key: str,
        *,
        stop_code: str,
        **kwargs,
    ) -> None:
        try:
            await self._update_intent(idempotency_key, **kwargs)
        except Exception as exc:
            self._engage_emergency_stop(stop_code)
            raise OkxLiveUnavailableError(
                "okx_live_intent_update_failed_after_exchange_write"
            ) from exc

    async def _ack_after_write(
        self,
        idempotency_key: str,
        data: list[dict[str, object]],
        *,
        stop_code: str,
        detail_code: str,
    ) -> OkxLiveOrderAcknowledgement:
        """Parse a production-write acknowledgement without losing ambiguity.

        Once the transport has returned from a write, malformed or empty data
        cannot safely be interpreted as a rejection. Persist ambiguity and stop
        all new exposure until an operator reconciles the exchange.
        """

        try:
            return self._ack(data)
        except Exception as exc:
            self._engage_emergency_stop(stop_code)
            try:
                await self._update_intent(
                    idempotency_key,
                    status="ambiguous",
                    detail_codes=[detail_code],
                )
            except Exception as update_exc:
                raise OkxLiveUnavailableError(
                    "okx_live_intent_update_failed_after_exchange_write"
                ) from update_exc
            raise OkxLiveUnavailableError(
                "okx_live_write_acknowledgement_invalid"
            ) from exc

    async def _record_write_failure(
        self,
        idempotency_key: str,
        exc: Exception,
        *,
        stage: str,
    ) -> None:
        ambiguous = (
            not isinstance(exc, OkxPrivateApiError)
            or getattr(exc, "code", None)
            in {"transport_error", "ambiguous_response"}
        )
        status = "ambiguous" if ambiguous else "rejected"
        try:
            await self._update_intent(
                idempotency_key,
                status=status,
                detail_codes=[f"{stage}_{status}"],
            )
        finally:
            if ambiguous:
                self._engage_emergency_stop("ambiguous_live_order_submission")
            else:
                self._record_error(exc)

    def _execution(self) -> OkxPrivateApiClient:
        if self.execution_client is None:
            raise OkxLiveSafetyError("okx_live_execution_transport_unavailable")
        return self.execution_client

    @staticmethod
    def _ack(data: list[dict[str, object]]) -> OkxLiveOrderAcknowledgement:
        if not data:
            raise OkxLiveUnavailableError("okx_live_write_acknowledgement_empty")
        row = data[0]
        return OkxLiveOrderAcknowledgement(
            order_id=str(row.get("ordId") or ""),
            client_order_id=str(row.get("clOrdId") or "") or None,
            exchange_code=str(row.get("sCode") or "0"),
        )

    def _request_hash(self, request) -> str:
        payload = request.model_dump(mode="json", exclude={"confirmation"})
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _expire_arm_if_needed(self) -> None:
        if self._armed_until is not None and self._now() >= self._armed_until:
            self._disarm_local()

    def _disarm_local(self, *, keep_submission_count: bool = False) -> None:
        self._armed_until = None
        self._baseline_equity = None
        if not keep_submission_count:
            self._submissions = 0

    def _engage_emergency_stop(self, code: str) -> None:
        self._emergency_stop = True
        self._last_error = code[:250]
        self._disarm_local(keep_submission_count=True)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _record_success(self) -> None:
        self._last_exchange_ok_at = self._now()
        self._last_error = None

    def _record_error(self, exc: Exception) -> None:
        self._last_error = self._safe_failure_code(exc)

    @staticmethod
    def _safe_failure_code(exc: Exception) -> str:
        if isinstance(exc, OkxLiveAccountIdentityError):
            return str(exc)[:250]
        if isinstance(exc, OkxPrivateApiError):
            return (
                "okx_live_private_api_unavailable"
                if getattr(exc, "code", None) == "transport_error"
                else "okx_live_private_api_rejected"
            )
        if isinstance(exc, OkxPublicApiError):
            return "okx_live_public_api_unavailable"
        if isinstance(exc, OkxLiveUnavailableError):
            return str(exc)[:250]
        if isinstance(exc, OkxLiveSafetyError):
            return "okx_live_capability_rejected"
        return "okx_live_reconcile_failed"

    async def _mirror_status(self) -> OkxLiveMirrorStatus:
        if self.mirror_repository is None:
            return OkxLiveMirrorStatus(available=False)
        try:
            return await self.mirror_repository.mirror_status()
        except Exception:
            return OkxLiveMirrorStatus(
                available=False,
                last_error="okx_live_mirror_status_unavailable",
            )

    @staticmethod
    def account_summary(value: OkxLiveAccountConfig) -> OkxLiveAccountSummary:
        return OkxLiveAccountSummary(
            is_sub_account=value.is_sub_account,
            account_level=value.account_level,
            position_mode=value.position_mode,
            account_stp_mode=value.account_stp_mode,
            account_type=value.account_type,
            capability=value.capability,
        )

    @staticmethod
    def balance_summary(value: OkxLiveBalanceSnapshot) -> OkxLiveBalanceSummary:
        return OkxLiveBalanceSummary(
            total_equity=value.total_equity,
            isolated_equity=value.isolated_equity,
            adjusted_equity=value.adjusted_equity,
            available_equity=value.available_equity,
            captured_at=value.captured_at,
        )

    @staticmethod
    def position_summary(value: OkxLivePositionView) -> OkxLivePositionSummary:
        return OkxLivePositionSummary(**value.model_dump(exclude={"raw"}))

    @staticmethod
    def order_summary(value: OkxLiveOrderView) -> OkxLiveOrderSummary:
        payload = value.model_dump(exclude={"raw", "attached_algo_orders"})
        payload["protection_count"] = len(value.attached_algo_orders)
        return OkxLiveOrderSummary(**payload)

    @staticmethod
    def algo_summary(value: OkxLiveAlgoOrderView) -> OkxLiveAlgoOrderSummary:
        return OkxLiveAlgoOrderSummary(**value.model_dump(exclude={"raw"}))

    @staticmethod
    def reconcile_summary(value: OkxLiveReconcileResult) -> OkxLiveReconcileSummary:
        return OkxLiveReconcileSummary(
            total_equity=value.balance.total_equity,
            position_count=sum(item.size != 0 for item in value.positions),
            pending_order_count=len(value.pending_orders),
            recent_order_count=len(value.recent_orders),
            pending_algo_order_count=len(value.pending_algo_orders),
            persisted=value.persisted,
            capability=value.account_config.capability,
            reconciled_at=value.reconciled_at,
        )

    @staticmethod
    def _decimal_text(value: Decimal) -> str:
        text = format(value.normalize(), "f")
        return "0" if text in {"-0", ""} else text
