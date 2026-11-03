"""Production authenticated same-request Demo invocation; no alternate engine.

Every positive capability remains in this request's original task and read
lease. Stop may tighten a retained original safety handle from another task.
Submitted/unknown sources are retained; DATA never grants release or retry.
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from app.api.security import require_ctcc_token
from app.config.settings import get_settings
from app.demo_automation import DemoAutomationBusyError, DemoAutomationSafetyError
from app.demo_automation.native_private_root_v2 import require_private_evidence_base_v2
from app.demo_automation.native_requests_v2 import (
    CONFIGURE_LEVERAGE_CONFIRMATION_V1,
    NativeDemoActionResultV2,
    NativeDemoArmExecuteRequestV2,
    NativeDemoClearStopRequestV2,
    NativeDemoConfigureLeverageRequestV1,
    NativeDemoConfigureLeverageResultV1,
    NativeDemoCounterInitializationRequestV2,
    NativeDemoFilledCompleteResultV3,
    NativeDemoZeroCompleteResultV3,
)
from app.domain.demo_automation import ARM_PHRASE, EXECUTE_PHRASE
from app.trade_qualification.demo_control_clear_v2 import CLEAR_CONFIRMATION_V2
from app.trade_qualification.demo_policy_counter_initialization_v1 import (
    COUNTER_CONFIRMATION_V1,
)

_ACTIVE = {}
_HELD = {}
_MAX_HELD = 16
_SETTLEMENT_KEY_V1 = object()
_CONFIGURATION_COMPLETION_KEY_V1 = object()
_ROOT_NAMES = (
    "cold-history-raw",
    "cold-history-companion",
    "current-history-raw",
    "current-history-companion",
    "current-account-proof",
    "current-account-native",
    "original-public",
    "original-public-account",
    "g12-evidence",
    "fresh-public",
    "fresh-public-account",
    "fresh-history-raw",
    "fresh-history-companion",
    "fresh-account-proof",
    "fresh-account-native",
    "operator-private",
    "operator-native",
)


def _need(value, code="native_demo_production_caller_unavailable"):
    if value is not True:
        raise DemoAutomationSafetyError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class _NativeDemoActionPlanV2:
    action: str
    instrument_id: str | None
    strategy: str | None
    contracts: Decimal | None
    leverage: int | None
    confirmation: str | None


def _freeze_request_v2(request):
    """Validate actual fields, never trust model construction/validation history."""
    kind = type(request)
    _need(
        kind
        in (
            NativeDemoArmExecuteRequestV2,
            NativeDemoClearStopRequestV2,
            NativeDemoCounterInitializationRequestV2,
            NativeDemoConfigureLeverageRequestV1,
        ),
        "native_demo_request_invalid",
    )
    try:
        raw = object.__getattribute__(request, "__dict__")
        extra = object.__getattribute__(request, "__pydantic_extra__")
    except Exception:  # noqa: BLE001 - malformed exact model produces only a fixed request code.
        raise DemoAutomationSafetyError("native_demo_request_invalid") from None
    _need(
        type(raw) is dict
        and len(raw) <= 7
        and all(type(key) is str for key in raw)
        and (extra is None or type(extra) is dict and len(extra) == 0),
        "native_demo_request_invalid",
    )
    if kind in (NativeDemoArmExecuteRequestV2, NativeDemoConfigureLeverageRequestV1):
        configure = kind is NativeDemoConfigureLeverageRequestV1
        leverage_field = "leverage_ceiling" if configure else "leverage"
        expected = {"instrument_id", "strategy", "contracts", leverage_field}
        expected.update(
            {"confirmation"}
            if configure
            else {"arm_confirmation", "execute_confirmation"}
        )
        _need(set(raw) == expected, "native_demo_request_invalid")
        instrument, strategy, quantity, leverage = (
            raw[name]
            for name in ("instrument_id", "strategy", "contracts", leverage_field)
        )
        _need(
            type(instrument) is str and instrument.endswith("-USDT-SWAP"),
            "native_demo_request_invalid",
        )
        prefix = instrument[: -len("-USDT-SWAP")]
        _need(
            1 <= len(prefix) <= 16
            and prefix.isascii()
            and prefix.isalnum()
            and prefix.upper() == prefix,
            "native_demo_request_invalid",
        )
        _need(
            type(strategy) is str
            and strategy
            in (
                "trend_pullback",
                "breakout_continuation",
                "liquidity_sweep_reversal",
                "fvg_return",
                "order_block_return",
                "range_reversal",
                "structure_reversal",
                "volatility_expansion",
            ),
            "native_demo_request_invalid",
        )
        _need(
            type(quantity) is str and 0 < len(quantity) <= 41,
            "native_demo_request_invalid",
        )
        pieces = quantity.split(".")
        _need(
            1 <= len(pieces) <= 2
            and 1 <= len(pieces[0]) <= 20
            and all("0" <= char <= "9" for char in pieces[0])
            and (len(pieces[0]) == 1 or pieces[0][0] != "0")
            and (
                len(pieces) == 1
                or 1 <= len(pieces[1]) <= 20
                and all("0" <= char <= "9" for char in pieces[1])
            ),
            "native_demo_request_invalid",
        )
        contracts = Decimal(quantity)
        _need(
            contracts.is_finite()
            and contracts > 0
            and type(leverage) is int
            and 1 <= leverage <= 125,
            "native_demo_request_invalid",
        )
        if configure:
            _need(leverage in (3, 5, 8, 10, 20), "native_demo_request_invalid")
            _need(
                type(raw["confirmation"]) is str
                and raw["confirmation"] == CONFIGURE_LEVERAGE_CONFIRMATION_V1,
                "native_demo_explicit_confirmation_required",
            )
            return _NativeDemoActionPlanV2(
                "configure_leverage", instrument, strategy, contracts, leverage, None
            )
        _need(
            type(raw["arm_confirmation"]) is str
            and raw["arm_confirmation"] == ARM_PHRASE
            and type(raw["execute_confirmation"]) is str
            and raw["execute_confirmation"] == EXECUTE_PHRASE,
            "native_demo_explicit_confirmation_required",
        )
        return _NativeDemoActionPlanV2(
            "arm_execute", instrument, strategy, contracts, leverage, None
        )
    _need(
        set(raw) == {"confirmation"} and type(raw["confirmation"]) is str,
        "native_demo_request_invalid",
    )
    expected = (
        CLEAR_CONFIRMATION_V2
        if kind is NativeDemoClearStopRequestV2
        else COUNTER_CONFIRMATION_V1
    )
    _need(raw["confirmation"] == expected, "native_demo_explicit_confirmation_required")
    return _NativeDemoActionPlanV2(
        "clear_stop"
        if kind is NativeDemoClearStopRequestV2
        else "initialize_policy_counter",
        None,
        None,
        None,
        None,
        raw["confirmation"],
    )


def _check_request_v2(request, selected):
    _need(
        type(selected) is _NativeDemoActionPlanV2
        and _freeze_request_v2(request) == selected,
        "native_demo_request_changed",
    )


def _service(service):
    from app.demo_automation.runtime import safe_demo_automation
    from app.demo_automation.service import SafeDemoAutomation

    _need(
        type(service) is SafeDemoAutomation
        and service is safe_demo_automation
        and service.settings is get_settings()
    )
    return service.settings


def _ready(service, state):
    settings = _service(service)
    _need(
        _ACTIVE.get(service) is state
        and state["task"] is asyncio.current_task()
        and state["revoked"] is False
        and settings is state["settings"]
    )
    _need(
        settings.trading_mode == "okx_demo"
        and settings.okx_demo_enabled is True
        and settings.live_trading is False
        and settings.okx_live_enabled is False
        and settings.okx_live_allow_order_writes is False
        and settings.okx_live_auto_execution is False
        and settings.paper_auto_execution is False,
        "native_demo_live_or_paper_configuration_denied",
    )
    return settings


def _roots(settings, invocation_id):
    from app.trade_qualification import account_native_proof_storage as storage

    base = require_private_evidence_base_v2(settings.okx_demo_native_evidence_root)
    # Existing no-reparse/no-clobber native directory primitives, before GET/Arm.
    with storage._root_context(base) as directory:
        directory.mkdir(invocation_id)
        with directory.child(invocation_id) as attempt:
            for name in _ROOT_NAMES:
                attempt.mkdir(name)
    root = base / invocation_id
    roots = {name: root / name for name in _ROOT_NAMES}
    _need(
        len({storage._root_identity(path) for path in roots.values()})
        == len(_ROOT_NAMES)
    )
    for path in roots.values():
        require_private_evidence_base_v2(str(path))
        with storage._root_context(path) as actual:
            _need(not actual.names())
    return roots


def _market_policy(settings):
    from app.trade_qualification import public_market_collector_v2 as public

    # Existing source policies/4 frames, with the current5s qualification cap.
    # This caller supplies no score, candle, quote, geometry or gate verdict.
    return public.PublicMarketCollectionPolicyV2(
        candles=public.candles.CandleCollectionPolicy(
            requests=tuple(
                public.candles.FrameRequest(
                    timeframe=frame,
                    requested_confirmed_bars=settings.okx_demo_scan_candle_limit,
                )
                for frame in public.candles.TIMEFRAMES
            )
        ),
        market_aux=public.aux.MarketAuxCollectionPolicy(max_age_seconds=5),
        ws=public.ws.WSCollectionPolicy(max_age_seconds=5),
    )


async def _dispose_selection(selection, factories):
    if selection is None:
        return
    selection.close()
    async with asyncio.timeout(5):
        await factories.frame_reader_engine.dispose()
        await factories.custody_engine.dispose()


async def _cold_history(service, state, instrument_id):
    from sqlalchemy import select

    from app.database.models.account_observation_index import (
        DemoAccountObservationBatch,
    )
    from app.demo_automation import native_server_selectors_v2 as selectors
    from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
    from app.trade_qualification import account_history_source_v1 as history
    from app.trade_qualification import native_account_risk_assembly_v1 as native

    client = OkxDemoPrivateRestClient(settings=state["settings"])
    state["cold_client"] = client
    with client.pinned_read_session() as lease:
        selection = await selectors.select_configured_demo_identity_v2(
            private_client=client, read_session=lease, instrument_ids=(instrument_id,)
        )
        state["cold_selection"] = selection
        _ready(service, state)
        scope = selection.scope_config
        factories = selection.factories
        state["cold_factories"] = factories
        async with factories.writer_factory() as session:
            # Existence chooses a genuine bootstrap invocation only. It is not
            # eligibility: formal prepare replays original sequence1 and B1.
            genesis = await session.scalar(
                select(DemoAccountObservationBatch.sequence).where(
                    DemoAccountObservationBatch.environment
                    == selection.current_plan.environment,
                    DemoAccountObservationBatch.account_id == scope.expected_uid,
                    DemoAccountObservationBatch.settlement_currency
                    == selection.current_plan.settlement_currency,
                    DemoAccountObservationBatch.sequence == 1,
                )
            )
        _ready(service, state)
        if genesis is None:
            owner = await history.acquire_native_history_current_source_v2(
                read_session=lease,
                private_client=client,
                scope_config=scope,
                session_factory=factories.writer_factory,
                custody_session_factory=factories.custody_factory,
                raw_root=state["roots"]["cold-history-raw"],
                companion_root=state["roots"]["cold-history-companion"],
            )
            state["cold_owner"] = owner
            _ready(service, state)
            history._release_financial_assembly_v1(owner, key=native._HOOK_KEY)
            state["cold_owner"] = None
        else:
            _need(type(genesis) is int and genesis == 1)
        await _dispose_selection(selection, factories)
        state["cold_selection"] = None
    _ready(service, state)


def _handoff_roots(paths):
    from app.trade_qualification import native_account_risk_assembly_v1 as account
    from app.trade_qualification import native_invocation_v2 as invocation
    from app.trade_qualification import native_postpublication_recheck_v2 as post

    return invocation.NativeCurrent55InvocationRootsV2(
        paths["original-public"],
        paths["original-public-account"],
        invocation.NativeInvocationRootsV2(
            paths["g12-evidence"],
            post.NativePostpublicationRootsV2(
                paths["fresh-public"],
                paths["fresh-public-account"],
                account.PostpublicationAccountRootsV1(
                    paths["fresh-history-raw"],
                    paths["fresh-history-companion"],
                    paths["fresh-account-proof"],
                    paths["fresh-account-native"],
                ),
            ),
        ),
    )


def revoke_native_for_service_v2(service, *, emergency):
    """Revoke every genuine local handle synchronously before persistence IO."""
    _need(type(emergency) is bool)
    unique = {
        id(state): state
        for state in (*_ACTIVE.values(), *_HELD.values())
        if state["service"] is service
    }
    if unique:
        _service(service)
    pending, failed = [], False
    for state in unique.values():
        state["revoked"] = True
        _HELD[state["id"]] = state
    for state in unique.values():
        owner = state.get("controller")
        previous = state.get("negative_request")
        if owner is None:
            continue
        if previous is not None:
            failed |= any(
                item["status"] != "confirmed" for item in state["negative_operations"]
            )
            if previous["kind"] == "emergency" or not emergency:
                continue
        record = {"kind": "emergency" if emergency else "disarm", "status": "pending"}
        state["negative_request"] = record
        state.setdefault("negative_operations", []).append(record)
        try:
            # Native negative operations synchronously revoke before returning
            # IO; they remain available across tasks and after trade expiry.
            operation = owner.emergency_stop() if emergency else owner.disarm()
            record["operation"] = operation
            pending.append((state, record))
        except BaseException:  # noqa: BLE001 - retain every owner and revoke the remaining handles.
            record["status"] = "unknown"
            state["stop_persistence_unknown"] = True
            failed = True

    async def persist():
        try:
            async with asyncio.timeout(5):
                for state, record in pending:
                    await record["operation"]
                    record["status"] = "confirmed"
        except (
            BaseException
        ):  # cancellation/failed persistence remains strongly retained.
            for state, record in pending:
                if record["status"] != "confirmed":
                    record["status"] = "unknown"
                    state["stop_persistence_unknown"] = True
            raise
        if failed:
            raise DemoAutomationSafetyError(
                "native_demo_stop_persistence_unknown"
            ) from None

    return persist()


async def _configure_leverage_v1(service, state, request, selected):
    from app.domain.source_primitives import decode
    from app.trade_qualification import native_flat_leverage_account_source_v1 as source
    from app.trade_qualification import (
        native_leverage_configuration_v1 as configuration,
    )

    owner, roots = state["controller"], state["roots"]
    _ready(service, state)
    _check_request_v2(request, selected)
    held = await source.acquire_owned_flat_leverage_source_v1(
        owner,
        instrument_id=selected.instrument_id,
        strategy=selected.strategy,
        contracts=selected.contracts,
        leverage_ceiling=selected.leverage,
        roots=source.FlatLeverageRootsV1(
            roots["current-account-proof"],
            roots["current-account-native"],
            roots["original-public"],
            roots["original-public-account"],
            roots["fresh-account-proof"],
            roots["fresh-account-native"],
        ),
    )
    state["configuration_source"] = held
    _ready(service, state)
    _check_request_v2(request, selected)
    issued = await configuration.issue_owned_native_leverage_configuration_v1(held)
    state["configuration_intent"] = issued
    _ready(service, state)
    _check_request_v2(request, selected)
    result = await configuration.execute_owned_native_leverage_configuration_v1(issued)
    state["configuration_result"] = result
    _need(
        type(result) is configuration.contracts.NativeLeverageConfigurationResultV1
        and result.classification in ("settled", "no_change")
        and result.execution_authority is False
        and result.order_retry_authority is False
    )
    receipt = configuration._take_native_leverage_configuration_caller_completion_v1(
        result, owner, key=_CONFIGURATION_COMPLETION_KEY_V1
    )
    # Genuine completion has consumed the controller. A later local cleanup
    # failure retains the invocation without revoking a superseding owner.
    state["controller"] = None
    closed = decode(receipt, 65536)
    _need(
        closed["schema_version"] == "ctcc.native_flat_leverage_source_compacted.v1"
        and closed["intent_id"] == result.intent_id
        and closed["execution_authority"] is False
        and closed["order_retry_authority"] is False
    )
    _ready(service, state)
    await _dispose_selection(state["selection"], state["factories"])
    state["selection"] = None
    _ready(service, state)
    return NativeDemoConfigureLeverageResultV1(
        invocation_id=state["id"],
        classification=result.classification,
        configuration_outcome_id=result.outcome_id,
        receipt_sha256=result.receipt_sha256,
    )


async def perform_native_demo_action_v2(service, request, *, operator_token):
    """Same authenticated request/task, explicit action; never scheduled/auto-Armed."""
    selected = _freeze_request_v2(request)
    await require_ctcc_token(operator_token)
    settings = _service(service)

    if service._run_lock.locked():
        raise DemoAutomationBusyError("native_demo_run_in_progress")
    async with service._run_lock:
        _need(
            len(_HELD) + len(_ACTIVE) < _MAX_HELD,
            "native_demo_retained_capacity_exhausted",
        )
        _need(
            service._recovered is True and service.repository is not None,
            "native_demo_local_startup_control_unavailable",
        )
        # Offline compiled source closure is checked BEFORE any GET lease/Arm.
        from app.trade_qualification import (
            native_controlled_account_source_v6 as controlled,
        )

        controlled._source_bootstrap_marker_v6()
        invocation_id = uuid4().hex
        action = selected.action
        instrument = (
            selected.instrument_id
            if selected.action in ("arm_execute", "configure_leverage")
            else settings.okx_demo_scan_symbol_list[0]
        )
        _need(instrument in settings.okx_demo_allowed_symbol_list)
        if selected.action in ("arm_execute", "configure_leverage"):
            _need(
                selected.contracts <= settings.okx_demo_max_order_size_contracts
                and selected.leverage <= settings.okx_demo_max_leverage
            )
        state = {
            "id": invocation_id,
            "service": service,
            "settings": settings,
            "task": asyncio.current_task(),
            "revoked": False,
            "controller": None,
            "selection": None,
            "submitted_or_unknown": False,
            "action": action,
        }
        _ACTIVE[service] = state
        failure = None
        try:
            _ready(service, state)
            state["roots"] = _roots(settings, invocation_id)
            state["market_policy"] = _market_policy(settings)
            await _cold_history(service, state, instrument)
            from app.demo_automation import native_server_selectors_v2 as selectors
            from app.domain.native_clock import native_stamp
            from app.domain.source_primitives import utc_from_ns
            from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
            from app.trade_qualification import demo_control
            from app.trade_qualification import native_demo_control_owner_v2 as facade

            client = OkxDemoPrivateRestClient(settings=settings)
            state["client"] = client
            # This is a NEW lease: cold-history credentials/session are not reused.
            with client.pinned_read_session() as lease:
                selection = await selectors.select_configured_demo_identity_v2(
                    private_client=client,
                    read_session=lease,
                    instrument_ids=(instrument,),
                )
                state["selection"] = selection
                factories = selection.factories
                state["factories"] = factories
                _ready(service, state)
                prepare_started = utc_from_ns(native_stamp()["utc_ns"])
                owner = await facade.prepare_selected_demo_control_owner_v3(selection)
                state["controller"] = owner
                _ready(service, state)
                if action == "configure_leverage":
                    await require_ctcc_token(operator_token)
                    _ready(service, state)
                    _check_request_v2(request, selected)
                    return await _configure_leverage_v1(
                        service, state, request, selected
                    )
                if action == "arm_execute":
                    await require_ctcc_token(operator_token)
                    _ready(service, state)
                    _check_request_v2(request, selected)
                    # Captured BEFORE prepare: this shortens the actual native
                    # lease. The native owner independently enforces its cap.
                    await owner.request_arm(
                        expires_at=min(
                            (
                                selection.current_plan.created_at
                                + timedelta(
                                    seconds=selection.current_plan.max_batch_seconds
                                )
                            ),
                            prepare_started
                            + timedelta(seconds=demo_control.MAX_ARM_SECONDS),
                        )
                    )
                    _ready(service, state)
                    source_roots = controlled.ControlledAccountRootsV6(
                        state["roots"]["current-history-raw"],
                        state["roots"]["current-history-companion"],
                        state["roots"]["current-account-proof"],
                        state["roots"]["current-account-native"],
                    )
                    issued = await owner.acquire_controlled_account(roots=source_roots)
                    state["issued"] = issued
                    _ready(service, state)
                    from app.trade_qualification import (
                        native_invocation_v2 as invocation,
                    )

                    intent = await invocation.complete_owned_native_current55_v2(
                        issued,
                        instrument_id=instrument,
                        strategy=selected.strategy,
                        market_policy=state["market_policy"],
                        requested_contracts=selected.contracts,
                        requested_leverage=selected.leverage,
                        roots=_handoff_roots(state["roots"]),
                    )
                    state["intent"] = intent
                    await require_ctcc_token(operator_token)
                    _ready(service, state)
                    _check_request_v2(request, selected)
                    from app.exchange.okx.native_demo_write_v2 import (
                        submit_owned_native_demo_v2,
                    )

                    state["submitted_or_unknown"] = True
                    observed = await submit_owned_native_demo_v2(intent)
                    state["observed"] = observed
                    from app.trade_qualification.native_postsubmit_reconciliation_v1 import (
                        reconcile_owned_native_response_v1,
                    )

                    # Root's writer transaction has exited. Same task/controller
                    # and original read context remain held for exact MOVE.
                    result = await reconcile_owned_native_response_v1(observed)
                    state["reconciliation_data"] = result
                    _need(
                        type(result.classification) is str
                        and result.classification
                        in (
                            "terminal_zero_fill_flat",
                            "filled_and_protected_hold_retained",
                        )
                    )
                    if result.classification == "filled_and_protected_hold_retained":
                        from app.trade_qualification import (
                            native_postsubmit_reconciliation_v1 as post,
                        )

                        # The continuation authenticates the exact private
                        # filled result. No status/hold boolean grants release.
                        settled = await post.complete_owned_native_filled_demo_v1(
                            result
                        )
                        state["filled_settlement_data"] = settled
                        completed = post._take_native_filled_caller_completion_v1(
                            settled, key=_SETTLEMENT_KEY_V1
                        )
                        _need(
                            type(completed) is post.NativeFilledCallerCompletionV1
                            and completed.controller is state["controller"]
                            and completed.cleanup_outcome_id
                            == settled.cleanup_outcome_id
                            and completed.report_spool_id == settled.report_spool_id
                        )
                        # The post-owned disposal is guarded at every await
                        # and uses its captured actual selector/factories.
                        await post.dispose_owned_native_filled_caller_resources_v1(
                            settled, key=_SETTLEMENT_KEY_V1
                        )
                        state["controller"] = None
                        state["selection"] = None
                        return NativeDemoFilledCompleteResultV3(
                            invocation_id=invocation_id,
                            closure_outcome_id=settled.closure_outcome_id,
                            cleanup_outcome_id=settled.cleanup_outcome_id,
                            forensics_outcome_id=settled.forensics_outcome_id,
                            report_spool_id=settled.report_spool_id,
                            report_delivery_status=(
                                "queued_for_independent_worker"
                                if settled.report_spool_id is not None
                                else "not_eligible_under_current_report_policy"
                            ),
                        )
                    from app.trade_qualification import (
                        native_postsubmit_reconciliation_v1 as post,
                    )

                    settled = await post.settle_owned_native_reconciliation_v1(result)
                    state["zero_settlement_data"] = settled
                    completed = post._take_native_zero_caller_completion_v2(
                        settled, key=_SETTLEMENT_KEY_V1
                    )
                    _need(
                        type(settled) is post.NativeZeroReconciliationSettlementResultV2
                        and type(completed) is post.NativeZeroCallerCompletionV2
                        and completed.controller is state["controller"]
                        and completed.cleanup_outcome_id == settled.cleanup_outcome_id
                    )
                    await post.dispose_owned_native_zero_caller_resources_v2(
                        settled, key=_SETTLEMENT_KEY_V1
                    )
                    state["controller"], state["selection"] = None, None
                    return NativeDemoZeroCompleteResultV3(
                        invocation_id=invocation_id,
                        closure_outcome_id=settled.closure_outcome_id,
                        cleanup_outcome_id=settled.cleanup_outcome_id,
                    )
                _check_request_v2(request, selected)
                if action == "clear_stop":
                    state["operator_data"] = await owner.clear_stop(
                        confirmation=selected.confirmation,
                        private_proof_root=state["roots"]["operator-private"],
                        native_proof_root=state["roots"]["operator-native"],
                    )
                else:
                    state["operator_data"] = await owner.initialize_policy_counter(
                        confirmation=selected.confirmation,
                        private_proof_root=state["roots"]["operator-private"],
                        native_proof_root=state["roots"]["operator-native"],
                    )
                _ready(service, state)
                await owner.release()
                state["controller"] = None
                await _dispose_selection(selection, factories)
                state["selection"] = None
                return NativeDemoActionResultV2(
                    invocation_id=invocation_id,
                    action=action,
                    status="operator_command_recorded_disarmed",
                )
        except BaseException as error:  # preserve original cancellation and retain the whole failed invocation.
            failure = error
            raise
        finally:
            if state["controller"] is not None:
                # Never treat ACK/reconciliation DATA booleans as permission to
                # release a real source, intent, reservation or protection hold.
                state["revoked"] = True
                _HELD[invocation_id] = state
                try:
                    stop = revoke_native_for_service_v2(service, emergency=True)
                    state["final_stop_operation"] = stop
                    await stop
                except BaseException:  # noqa: BLE001 - keep actual source/transaction owners on every failed finalizer.
                    state["stop_persistence_unknown"] = True
                    _ACTIVE.pop(service, None)
                    if failure is None:
                        raise DemoAutomationSafetyError(
                            "native_demo_stop_persistence_unknown"
                        ) from None
            elif failure is not None:
                # Preserve accepted files and actual partial source/factory
                # owners. No delete, retry, partial reconcile or unknown->empty.
                _HELD[invocation_id] = state
            _ACTIVE.pop(service, None)
