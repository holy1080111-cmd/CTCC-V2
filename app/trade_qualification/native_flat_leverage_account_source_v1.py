"""Genuine disarmed CURRENT/public source for flat-only configuration.

This purpose does not issue G11/account/trade/Arm rights or adopt unknown
private financial history. Root/SQL configuration integration is mandatory.
"""

import asyncio
import json
import secrets
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as catalog,
)
from app.database.repositories import demo_control_current55_v2 as controls
from app.database.repositories import qualification_ledger as ledger
from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_original_position_path_v1 as positions
from app.trade_qualification import captured_execution_mode_v1 as execution
from app.trade_qualification import controlled_current_mirror_current55_v1 as mirror
from app.trade_qualification import demo_control as control
from app.trade_qualification import demo_market_source_v4 as market
from app.trade_qualification import native_account_risk_assembly_v1 as account
from app.trade_qualification import native_demo_control_owner_v2 as facade
from app.trade_qualification import native_leverage_configuration_plan_v1 as planner
from app.trade_qualification import native_original_local_current55_v2 as original
from app.trade_qualification import owned_current_original_prefix_v1 as local
from app.trade_qualification import public_market_collector_v2 as public

_CONTROL_KEY_V1 = object()
_PUBLIC_KEY_V1 = object()
_SOURCE_KEY_V1 = object()
_SCAN_KEY_V1 = object()
_MAX_SOURCES = 16
_MAX_BYTES = 16 * 1024 * 1024
_ACTIVE = {}
_UNKNOWN = {}
_OWNERS = {}
_BINDINGS = {}
_CONTROL_COMPLETIONS = {}


class NativeFlatLeverageSourceError(ValueError):
    pass


def _need(condition, code="native_flat_leverage_source_unavailable"):
    if condition is not True:
        raise NativeFlatLeverageSourceError(code) from None


class OwnedFlatLeverageSourceV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise NativeFlatLeverageSourceError(
            "native_flat_leverage_source_not_constructible"
        )

    def __copy__(self):
        raise NativeFlatLeverageSourceError(
            "native_flat_leverage_source_not_transferable"
        )

    def __deepcopy__(self, _memo):
        raise NativeFlatLeverageSourceError(
            "native_flat_leverage_source_not_transferable"
        )

    def __reduce_ex__(self, _protocol):
        raise NativeFlatLeverageSourceError(
            "native_flat_leverage_source_not_transferable"
        )


@dataclass(frozen=True, slots=True, repr=False)
class FlatLeverageRootsV1:
    current_proof_root: Path
    current_native_root: Path
    public_root: Path
    public_account_root: Path
    readback_proof_root: Path
    readback_native_root: Path

    def __post_init__(self):
        roots = tuple(getattr(self, item.name) for item in fields(self))
        _need(all(type(path) is type(Path()) and path.is_absolute() for path in roots))
        _need(
            len(set(roots)) == 6
            and not any(
                left in right.parents
                for left in roots
                for right in roots
                if left != right
            )
        )


@dataclass(frozen=True, slots=True, repr=False)
class FlatLeverageSourceViewV1:
    binding: object
    source_state: object
    session_factory: object
    scope: object
    control_state: object
    control_event_sha256: str
    control_owner: object
    selector: object
    private_client: object
    read_session: object
    settings: object
    current_record: object
    public_packet: object
    result: object
    selection: object
    analysis: object
    instrument: object
    selected_execution: object
    account_equity: Decimal
    available_margin: Decimal
    configured_policy: object
    local_checkpoint: object
    local_journal: object
    plan: planner.NativeLeverageConfigurationPlanDataV1
    source_receipt_json: bytes
    source_generation: object
    original_expires_at: datetime
    monotonic_deadline_ns: int


@dataclass(frozen=True, slots=True, repr=False)
class _CurrentRootsV1:
    account_proof_root: Path
    account_native_root: Path


# Bound only by the actual original Source bootstrap before any GET lease.
# Its existing compiled-source guard captures this exact module identity.
_SOURCE_MODULE_V1 = None


def _source():
    _need(
        _SOURCE_MODULE_V1 is not None, "native_flat_leverage_source_bootstrap_required"
    )
    return _SOURCE_MODULE_V1


def _control_stamp_for_source_v1(state, *, key):
    source = _source()
    _need(key is source._SOURCE_KEY_V6)
    # Unrecognized is a fixed fallback to the unchanged Armed source checker,
    # never an empty/accepted configuration context.
    if type(state) is not dict or _ACTIVE.get(id(state)) is not state:
        return None
    _need(
        state.get("configuration_binding") in _BINDINGS
        and _BINDINGS[state["configuration_binding"]] is state
    )
    return facade._check_flat_leverage_control_v1(state["control"], key=_CONTROL_KEY_V1)


def _check(state):
    source = _source()
    _need(type(state) is dict and _ACTIVE.get(id(state)) is state)
    stamp = source._check_v6(state)
    _budget(state)
    return stamp


def _budget(state, *, extra=()):
    source = _source()
    _need(type(extra) is tuple and all(type(item) in (bytes, str) for item in extra))
    values = [
        state.get(name)
        for name in (
            "math_record",
            "coverage_reads",
            "mirror_observation",
            "source_receipt_json",
            "configuration_plan",
            "selected_execution",
            "source_view",
            "base_scope_json",
            "configuration_readback_attempt",
            "configuration_lineage_json",
        )
    ]
    pub = state.get("public_state")
    if pub is not None:
        values.append(pub["bundle_raw"])
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    values.append(
        actor._retained_native_leverage_configuration_buffers_v1(
            state["generation"], key=source._SOURCE_KEY_V6
        )
    )
    if state.get("configuration_actor_binding") is not None:
        from app.exchange.okx import native_demo_write_v2 as root

        values.append(
            root._native_leverage_configuration_retained_buffers_v1(
                state["configuration_actor_binding"], key=actor._ROOT_DATA_KEY_V1
            )
        )
    values.extend(extra)
    snapshot = source._close_write_buffer_bytes_v6(tuple(values), preserve_text=True)
    source._retained_budget_v6(state)
    # Both the original CURRENT buffers and new raw public/local/plan buffers
    # are checked together, not through two independently reset allowances.
    original_buffers = source._filled_cleanup_original_buffers_v6(state)
    source._retained_budget_v6({"history_tokens": (original_buffers, snapshot)})


def _market_policy(settings):
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


def acquire_owned_flat_leverage_source_v1(
    owner, *, instrument_id, strategy, contracts, leverage_ceiling, roots
):
    """Synchronous real disarmed MOVE; no caller source/factory/clock/verdict."""
    source = _source()
    source._REQUIRE_SOURCE_KERNEL_V6()
    _need(len(_ACTIVE) + len(_UNKNOWN) < _MAX_SOURCES)
    _need(
        type(instrument_id) is str
        and instrument_id in market.REVIEWED_DEMO_INSTRUMENT_IDS
        and type(strategy) is str
        and strategy in local.precursor.STRATEGY_CATALOG
        and type(contracts) is Decimal
        and contracts.is_finite()
        and Decimal(0) < contracts < Decimal("1e20")
        and len(contracts.as_tuple().digits) <= 40
        and contracts.as_tuple().exponent >= -20
        and type(leverage_ceiling) is int
        and leverage_ceiling in planner.LEVERAGE_CEILINGS
        and type(roots) is FlatLeverageRootsV1
    )
    roots.__post_init__()
    ctrl = facade._take_flat_leverage_control_v1(owner, key=_CONTROL_KEY_V1)
    state = None
    try:
        selected = facade._selected_record_v3(ctrl)
        _need(
            selected is not None
            and selected.writer_factory is ctrl["factory"]
            and selected.custody_factory is ctrl["custody_original_factory"]
        )
        stamp = facade._check_flat_leverage_control_v1(ctrl, key=_CONTROL_KEY_V1)
        binding = object()
        source_roots = _CurrentRootsV1(
            roots.current_proof_root, roots.current_native_root
        )
        # Only account_proof_root/account_native_root are used in _current;
        # configuration never calls the private financial-history/issuer path.
        state = {
            "context": source._context_v6(),
            "control": ctrl,
            "roots": source_roots,
            "configuration_roots": roots,
            "scope_config": selected.scope_config,
            "phase": "ACQUIRING",
            "generation": object(),
            "invocation_id": secrets.token_hex(32),
            "expires_at": ctrl["configuration_expires_at"],
            "monotonic_deadline_ns": ctrl["configuration_monotonic_deadline_ns"],
            "last": stamp,
            "invocation_started": stamp,
            "inventory": account._package_inventory_controlled55_v1(),
            "settings_pin": account._v3_data_pin(
                ctrl["read_session"]._settings.__dict__
            ),
            "factories": tuple(
                (
                    factory,
                    source.history._factory_pin(factory),
                    account._database_connection_pin_v4(factory),
                )
                for factory in (
                    ctrl["factory"],
                    selected.raw_reader_factory,
                    ctrl["custody_factory"],
                )
            ),
            "current": None,
            "history": None,
            "barrier": None,
            "original_state": None,
            "commit_binding": None,
            "issued_readback": None,
            "configuration_binding": binding,
            "instrument_id": instrument_id,
            "strategy": strategy,
            "contracts": contracts,
            "leverage_ceiling": leverage_ceiling,
            "market_policy": _market_policy(ctrl["read_session"]._settings),
            "coverage_reads": [],
            "source_view": None,
        }
        _ACTIVE[id(state)], _BINDINGS[binding], source._STATES_V6[id(state)] = (
            state,
            state,
            state,
        )
        _check(state)
        return source._AcquisitionAttemptV6(state, _acquire(state), _retire=_retire)
    except BaseException:
        if state is not None:
            _retire(state, uncertain=True)
        else:
            facade._retain_flat_leverage_control_v1(ctrl, key=_CONTROL_KEY_V1)
        raise


def _retire(state, *, uncertain):
    _need(type(uncertain) is bool)
    if type(state) is not dict or _ACTIVE.get(id(state)) is not state:
        return
    source = _source()
    source._retire_v6(state, uncertain=uncertain)
    if uncertain:
        _UNKNOWN[id(state)] = state
        _ACTIVE.pop(id(state), None)
    # Successful retirement is only called after the real result/Root/Source
    # cleanup and independent DB completion cap, not a DATA success flag.


def _replay_flat_leverage_mirror_context_v1(binding, *, key):
    _need(key is mirror._CONFIGURATION_SOURCE_KEY_V1 and type(binding) is object)
    state = _BINDINGS.get(binding)
    _check(state)
    source = _source()
    return source._coverage_context_v6(state["coverage_binding"])


async def _local_snapshot(state):
    _check(state)
    source, ctrl = _source(), state["control"]
    repository = ledger.QualificationLedgerRepository(
        ctrl["factory"], clock=lambda: utc_from_ns(_check(state)["utc_ns"])
    )
    async with ctrl["factory"]() as session, session.begin():
        _check(state)
        _need(type(session) is AsyncSession)
        for statement in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET LOCAL search_path TO pg_catalog,public,pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL synchronous_commit='on'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='1000ms'",
        ):
            await session.execute(text(statement))
            _check(state)
        await catalog.verify_actual_issuer_catalog_v2(session)
        _check(state)
        actual, event = await controls.DemoControlRepositoryV2.read_in_transaction(
            session, ctrl["scope"]
        )
        _check(state)
        _need(
            canonical(control.document(actual))
            == canonical(control.document(ctrl["observation"].state))
            and event == ctrl["observation"].event_sha256
        )
        row = await repository._locked(session, ctrl["ledger_scope"])
        _check(state)
        from app.database.repositories import (
            native_leverage_configuration_v55 as configuration_repository,
        )

        scope_raw = configuration_repository._row(
            {
                name: getattr(row, name)
                for name in configuration_repository._SCOPE_NAMES
            },
            configuration_repository._SCOPE_NAMES,
        )
        previous = state.get("base_scope_json")
        _need(previous is None or previous == scope_raw)
        state["base_scope_json"] = scope_raw
        before = await source.lineage55._metadata(session)
        _check(state)
        checkpoint = await repository._bootstrap_checkpoint(
            session, ctrl["ledger_scope"], row
        )
        _check(state)
        local_point = await repository._read_portfolio_checkpoint_locked(
            session, ctrl["ledger_scope"], row
        )
        _check(state)
        journal = await repository._inspect_prospective_event_journal_locked(
            session, ctrl["ledger_scope"], row
        )
        _check(state)
        after = await source.lineage55._metadata(session)
        _check(state)
        _need(
            before[:9] == after[:9]
            and before[9] <= after[9]
            and type(local_point) is ledger.PortfolioLocalCheckpoint
            and local_point.known_flat is True
        )
        result = (
            checkpoint,
            local_point,
            journal,
            before,
            after,
            actual,
            event,
            session,
        )
        state["coverage_reads"].append(result)
        _check(state)
    _check(state)
    return result


def _same_local(first, second):
    _need(
        first[7] is not second[7]
        and first[3][8] != second[3][8]
        and first[4][9] <= second[3][9]
        and ledger.canonical(first[0].state) == ledger.canonical(second[0].state)
        and first[1].document_json == second[1].document_json
        and first[2].consumed_event_keys == second[2].consumed_event_keys
        and first[5] == second[5]
        and first[6] == second[6]
    )
    a, b = decode(first[2].document_json), decode(second[2].document_json)
    names = (
        "scope_sha256",
        "account_revision",
        "ledger_revision",
        "claims_sha256",
        "event_count",
        "transition_count",
        "state_counts",
        "event_keys_sha256",
        "rows_sha256",
        "transitions_sha256",
    )
    _need(
        all(a[name] == b[name] for name in names),
        "native_flat_leverage_local_journal_changed",
    )


async def _acquire(state):
    source = _source()
    try:
        async with asyncio.timeout(source._remaining_v6(state)) as budget:
            from app.database.repositories import (
                native_leverage_configuration_v55 as configuration_repository,
            )

            async with state["control"]["factory"]() as session, session.begin():
                await configuration_repository.verify_actual_configuration_catalog_v1(
                    session
                )
                _check(state)
            await source._current_v6(state)
            _check(state)
            budget.reschedule(
                min(
                    budget.when(),
                    asyncio.get_running_loop().time() + source._remaining_v6(state),
                )
            )
            coverage_binding = object.__new__(source._CoverageBindingV6)
            source._COVERAGE_V6[coverage_binding] = state
            state["coverage_binding"] = coverage_binding
            state[
                "mirror_observation"
            ] = await mirror.synchronize_owned_configuration_current_mirror_v1(
                state["configuration_binding"]
            )
            _check(state)
            first = await _local_snapshot(state)
            await _record_configuration_lineage_v1(state, first)
            await _capture_public_and_math(state)
            _check(state)
            second = await _local_snapshot(state)
            _same_local(first, second)
            await _complete_plan(state, second)
            _check(state)
            owner = object.__new__(OwnedFlatLeverageSourceV1)
            _OWNERS[owner] = state
            state["source_owner"], state["phase"] = owner, "CONFIGURATION_SOURCE_READY"
            return owner
    except asyncio.CancelledError:
        _retire(state, uncertain=True)
        raise asyncio.CancelledError() from None
    except Exception:  # noqa: BLE001 -- Retire the consumed acquisition on an unknown result.
        _retire(state, uncertain=True)
        raise NativeFlatLeverageSourceError(
            "native_flat_leverage_acquisition_unknown"
        ) from None
    except BaseException:
        _retire(state, uncertain=True)
        raise


async def _record_configuration_lineage_v1(state, snapshot):
    source, ctrl = _source(), state["control"]
    _check(state)
    observed = await source.lineage55._record_held_current_revision_lineage_v2(
        state["current"], key=source.native._CONTROLLED_SOURCE_KEY_V1
    )
    _check(state)
    policy = source.lineage55.policy
    _need(
        type(observed) is policy.CollectorCurrentRevisionLineageReadback
        and type(observed.lineage_json) is bytes
        and sha(observed.lineage_json) == observed.lineage_sha256
    )
    scope = ctrl["ledger_scope"]
    reference = source.observed.reference_document(state["current_reference"])
    scope_row = decode(state["base_scope_json"])
    control_state, control_event = snapshot[5:7]
    validated = policy.validate_document(
        observed.lineage_json,
        scope=[scope.environment, scope.account_id, scope.settlement_currency],
        reference=reference,
        claims_json=scope_row["claims_json"],
        account_revision=scope_row["account_revision"],
        ledger_revision=scope_row["ledger_revision"],
        control_revision=control_state.revision,
        control_event_sha256=control_event,
        control_state_sha256=source.lineage55._control_pin(control_state),
    )
    current = state["current_record"]
    _need(
        validated["source_bundle_sha256"] == state["current"]["bundle_sha256"]
        and validated["proof_envelope_sha256"] == current.proof_envelope_sha256
        and validated["native_proof_sha256"] == current.native_proof_sha256
        and validated["native_readback_sha256"] == current.native_readback_sha256
        and validated["claims_binding_kind"] == "UNAUTHENTICATED_LEDGER_SNAPSHOT"
        and validated["account_revision_issued"] is False
        and validated["account_complete"] is False
        and validated["execution_authority"] is False
        and validated["admission"] == "DENY"
    )
    _check(state)
    source._scan_reconciliation_complete_document_v6(
        observed.lineage_json, _scan_tokens(state)
    )
    _budget(state, extra=(observed.lineage_json,))
    state["configuration_lineage_json"] = observed.lineage_json


async def _capture_public_and_math(state):
    source, ctrl = _source(), state["control"]
    stamp = _check(state)
    document = state["current_plan"].model_dump(mode="python", round_trip=True)
    document.update(
        plan_id="configuration-" + secrets.token_hex(16),
        created_at=utc_from_ns(stamp["utc_ns"]),
    )
    plan = capture.CurrentDemoAccountCapturePlanV7.model_validate(document, strict=True)
    public_session = source.ControlledDemoAccountSession.from_pinned_read_session(
        read_session=ctrl["read_session"],
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )
    state["public_session"], state["public_plan"] = public_session, plan
    pending = await market.acquire_initial_demo_market_source_v4(
        state["configuration_roots"].public_root,
        account_proof_root=state["configuration_roots"].public_account_root,
        instrument_id=state["instrument_id"],
        market_policy=state["market_policy"],
        account_session=public_session,
        private_client=ctrl["private_client"],
        custody_repository=ctrl["custody_repository"],
    )
    state["pending_public"] = pending
    _check(state)
    held = market._take_native_flat_leverage_configuration_v1(
        pending, key=_PUBLIC_KEY_V1
    )
    state["public_state"], state["pending_public"] = held, None
    packet, context, public_stamp = (
        market._replay_native_flat_leverage_configuration_v1(held, key=_PUBLIC_KEY_V1)
    )
    _check(state)
    _need(public_session._checked_read_session(plan) is ctrl["read_session"])
    source._shorten_v6(
        state,
        expires_at=held["expires_at"],
        monotonic_deadline_ns=state["monotonic_deadline_ns"],
    )
    directory, _proof, _raw, events = market.replay_runtime_bundle_v4(
        held["bundle_raw"], expected_sha256=held["bundle_sha256"]
    )
    _scan_public(state, directory, events)
    at = utc_from_ns(public_stamp["utc_ns"])
    data_policy = local.fixed_native_original_g1_policy()
    g1 = local.data_v2.evaluate_public_market_data_v2(
        packet,
        expected_bundle_sha256=packet.bundle_sha256,
        policy=data_policy,
        evaluated_at=at,
    )
    g1 = local.data_v2.verify_public_market_data_v2(
        g1,
        packet,
        expected_bundle_sha256=packet.bundle_sha256,
        policy=data_policy,
        evaluated_at=at,
    )
    derive = (
        local.precursor.derive_original_candidate_precursor_v2
        if state["strategy"] in local._BASE_STRATEGIES
        else local.precursor.derive_owned_original_candidate_precursor_v3
    )
    precursor = derive(
        packet,
        state["current_record"].packet,
        strategy=state["strategy"],
        expected_public_bundle_sha256=packet.bundle_sha256,
        expected_account_plan_sha256=state["current_plan_sha256"],
        expected_account_packet_sha256=state["current_record"].reference.packet_sha256,
        data_policy=data_policy,
        expected_data_policy_sha256=local.DATA_POLICY_SHA256,
        created_at=at,
        service_deadline=state["expires_at"],
    )
    record = {"strategy": state["strategy"], "instrument_id": state["instrument_id"]}
    if state["strategy"] not in local._BASE_STRATEGIES:
        record["family_policy_raw"] = local._freeze_owned_family_policy_v2(
            state["strategy"]
        )
    computed = original._computed_family_v2(
        record,
        (
            packet,
            state["current_record"],
            context,
            g1,
            precursor,
            at,
            state["expires_at"],
        ),
    )
    _need(
        computed.intent is not None and computed.detection.trigger is not None,
        "native_flat_leverage_real_candidate_missing",
    )
    policy_raw = original._policy_family_v2(record)
    numeric = decode(policy_raw)
    _need(computed.result.raw_score >= numeric["minimum_score"])
    rules = local.derive_captured_instrument_rules(
        state["current_record"].packet,
        instrument_id=state["instrument_id"],
        expected_plan_sha256=state["current_plan_sha256"],
        expected_packet_sha256=state["current_record"].reference.packet_sha256,
    )
    inputs = decode(computed.g1.source_json.encode(), local.data.MAX_SOURCE_BYTES)
    original_market = MarketSnapshot.model_validate_json(
        json.dumps(inputs["market"]), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(inputs["analysis"]), strict=True
    )
    protection = local.ProtectionPolicy.model_validate_json(
        json.dumps(numeric["protection"]), strict=True
    )
    selector = (
        local.select_structural_protection
        if state["strategy"] in local._BASE_STRATEGIES
        else local._select_owned_family_protection_v2
    )
    selected = selector(
        computed.detection,
        original_market,
        analysis,
        observed_at=at,
        entry=computed.intent.candidate_entry,
        tick_size=rules.tick_size,
        **{
            name: value
            for name, value in protection.model_dump().items()
            if name != "policy_id"
        },
    )
    _need(
        selected.reference_entry == computed.intent.candidate_entry
        and selected.report_id == computed.intent.report_id
        and selected.instrument_id == computed.intent.instrument_id
        and selected.direction == computed.intent.direction
        and selected.strategy == computed.intent.strategy
        and selected.source_sha256 == computed.g1.source_sha256
    )
    record.update(
        computed=computed,
        created_at=at,
        service_deadline=state["expires_at"],
        policy_raw=policy_raw,
        public_packet=packet,
        quote_value=context.quote,
        quote_context=local._replay_local_quote_v13(
            packet, context.quote, computed, evaluated_at=at
        ),
        market=original_market,
        analysis=analysis,
        tick_size=rules.tick_size,
        protection=protection,
        selection=selected,
        selection_raw=selected.to_audit_json(),
        original=precursor,
    )
    state["math_record"], state["public_packet"], state["public_context"] = (
        record,
        packet,
        context,
    )
    source._shorten_v6(
        state,
        expires_at=min(
            computed.intent.expires_at,
            computed.original_zone.expires_at,
            computed.detection.trigger.expires_at,
        ),
        monotonic_deadline_ns=state["monotonic_deadline_ns"],
    )
    _check(state)


def _scan_tokens(state):
    from app.demo_automation import native_server_selectors_v2 as selector

    source = _source()
    values = list(state["current"]["private_secret_tokens"])
    _need(len(values) >= 4)
    values.extend(
        selector._configured_demo_selector_scan_tokens_v2(
            state["control"]["identity_selection"], key=source._SCAN_KEY_V6
        )
    )
    held = state.get("public_state")
    if held is not None:
        route = market.operational._owned_route_state_v4(
            held["v4_route_claim"],
            invocation=held["invocation"],
            owner_task=asyncio.current_task(),
        )
        tokens = route["secret_tokens"]
        _need(type(tokens) is tuple and len(tokens) >= 4)
        values.extend(tokens)
    readback = state.get("configuration_readback_attempt")
    if type(readback) is dict and type(readback.get("current")) is dict:
        values.extend(readback["current"]["private_secret_tokens"])
    if state.get("configuration_actor_binding") is not None:
        from app.exchange.okx import native_demo_write_v2 as root
        from app.trade_qualification import native_leverage_configuration_v1 as actor

        values.extend(
            root._native_leverage_configuration_scan_tokens_v1(
                state["configuration_actor_binding"], key=actor._ROOT_DATA_KEY_V1
            )
        )
    _need(
        all(
            type(value) is str and value.isascii() and len(value) > 0
            for value in values
        )
    )
    return tuple(dict.fromkeys(values))


def _scan_public(state, directory, events):
    from app.trade_qualification import native_postpublication_support_v2 as support

    support._secret_public_journal(directory, events, _scan_tokens(state))


async def _complete_plan(state, snapshot):
    source, record, ctrl = _source(), state["math_record"], state["control"]
    now = utc_from_ns(_check(state)["utc_ns"])
    result = original._local_math_family_v2(record, snapshot[2], observed_at=now)
    _need(
        len(result.gates) == 10 and all(gate.passed for gate in result.gates),
        "native_flat_leverage_source_local_gates_rejected",
    )
    state["instrument_specs"] = source._instrument_specs_v6(state)
    matched = tuple(
        item
        for item in state["instrument_specs"]
        if item.instrument_id == state["instrument_id"]
    )
    _need(len(matched) == 1)
    mode = state["execution_mode"]
    side = "net" if mode.position_mode == "net_mode" else result.direction
    observations = tuple(
        item
        for item in mode.leverage_observations
        if item.instrument_id == state["instrument_id"]
        and item.margin_mode == "isolated"
        and item.position_side == side
    )
    _need(len(observations) == 1 and type(observations[0].leverage) is Decimal)
    actual = observations[0].leverage
    _need(
        actual.is_finite()
        and actual == actual.to_integral_value()
        and Decimal(1) <= actual <= Decimal(125)
    )
    selected_execution = execution.select_captured_isolated_execution_v1(
        mode,
        instrument_id=state["instrument_id"],
        direction=result.direction,
        requested_leverage=int(actual),
    )
    anchor = positions._coupled_anchor_from_held_current_record_v3(
        state["current_record"], scope=ctrl["ledger_scope"], checked_at=now
    )
    current_receipt = decode(
        state["current_record"].current_receipt_json, capture.MAX_PACKET_BYTES
    )
    _need(
        current_receipt.get("observed_flat") is True
        and all(item.contracts == 0 for item in anchor.positions)
        and snapshot[1].known_flat is True,
        "native_flat_leverage_complete_flatness_required",
    )
    equity = source._decimal_exact_v6(anchor.account_equity_usdt)
    available = source._decimal_exact_v6(
        anchor.separately_observed_available_margin_usdt
    )
    quote = local._replay_local_quote_v13(
        state["public_packet"],
        record["quote_value"],
        record["computed"],
        evaluated_at=now,
    )
    economics_policy = local.EconomicsPolicy.model_validate_json(
        json.dumps(decode(record["policy_raw"])["economics"]), strict=True
    )
    costs = local._economics_local_quote_v13(
        record,
        quote,
        stop=result.stop_loss,
        target=result.take_profit,
        policy=economics_policy,
        observed_at=now,
    )
    _need(costs.passed is True)
    plan = planner.select_flat_configuration_plan_data_v1(
        settings=ctrl["read_session"]._settings,
        analysis=record["analysis"],
        result=result,
        selection=record["selection"],
        selected_execution=selected_execution,
        instrument=matched[0],
        contracts=state["contracts"],
        cost_per_base=costs.cost_per_base,
        account_equity=equity,
        available_margin=available,
        risk_policy=ctrl["configured_policy"].risk_policy,
        position_margin_bucket_usdt=ctrl[
            "configured_policy"
        ].position_margin_bucket_usdt,
        raw_score=result.raw_score,
        effective_score=result.effective_score,
        leverage_ceiling=state["leverage_ceiling"],
        expires_at=state["expires_at"],
    )
    state["configuration_plan"], state["selected_execution"] = plan, selected_execution
    source_receipt = canonical(
        {
            "schema_version": "ctcc.native_flat_leverage_source.v1",
            "scope": [
                ctrl["ledger_scope"].environment,
                ctrl["ledger_scope"].account_id,
                ctrl["ledger_scope"].settlement_currency,
            ],
            "current_reference": source.observed.reference_document(
                state["current_reference"]
            ),
            "current_bundle_sha256": state["current"]["bundle_sha256"],
            "current_lineage_sha256": sha(state["configuration_lineage_json"]),
            "current_lineage_json": state["configuration_lineage_json"].decode("utf-8"),
            "current_receipt_sha256": sha(state["current_record"].current_receipt_json),
            "current_receipt_json": state["current_record"].current_receipt_json.decode(
                "utf-8"
            ),
            "local_checkpoint_json": snapshot[1].document_json.decode(),
            "local_journal_sha256": snapshot[2].sha256,
            "base_scope_sha256": sha(state["base_scope_json"]),
            "public_packet_sha256": state["public_packet"].bundle_sha256,
            "public_bundle_sha256": state["public_state"]["bundle_sha256"],
            "computed_receipt_sha256": sha(record["computed"].receipt_json),
            "local_result_sha256": sha(canonical(result.model_dump(mode="json"))),
            "selection_sha256": sha(record["selection_raw"].encode("ascii")),
            "native_config_sha256": ctrl["pins"].config_sha256,
            "native_financial_policy_sha256": ctrl["pins"].policy_sha256,
            "configuration_plan_sha256": plan.receipt_sha256,
            "expires_at": state["expires_at"].isoformat(),
            "account_complete": False,
            "account_revision_issued": False,
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    _need(0 < len(source_receipt) <= 1048576)
    state["source_receipt_json"] = source_receipt
    state["source_view"] = FlatLeverageSourceViewV1(
        state["configuration_binding"],
        state,
        ctrl["factory"],
        ctrl["ledger_scope"],
        ctrl["observation"].state,
        ctrl["observation"].event_sha256,
        ctrl["owner"],
        ctrl["identity_selection"],
        ctrl["private_client"],
        ctrl["read_session"],
        ctrl["read_session"]._settings,
        state["current_record"],
        state["public_packet"],
        result,
        record["selection"],
        record["analysis"],
        matched[0],
        selected_execution,
        equity,
        available,
        ctrl["configured_policy"],
        snapshot[1],
        snapshot[2],
        plan,
        source_receipt,
        state["generation"],
        state["expires_at"],
        state["monotonic_deadline_ns"],
    )
    _check(state)


def _take_flat_leverage_source_v1(owner, *, key):
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    _need(key is actor._SOURCE_KEY_V1)
    state = (
        _OWNERS.pop(owner, None) if type(owner) is OwnedFlatLeverageSourceV1 else None
    )
    _check(state)
    _need(
        state["phase"] == "CONFIGURATION_SOURCE_READY"
        and state["source_owner"] is owner
    )
    state["configuration_consumer_key"] = key
    state["phase"] = "CONFIGURATION_ISSUING"
    return state["source_view"]


def _replay_flat_leverage_source_v1(binding, *, key):
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    _need(key is actor._SOURCE_KEY_V1)
    state = _BINDINGS.get(binding)
    _check(state)
    _need(
        state.get("configuration_consumer_key") is key
        and state["phase"] == "CONFIGURATION_ISSUING"
    )
    return state["source_view"]


def _retain_flat_leverage_source_unknown_v1(binding, *, key):
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    _need(key is actor._SOURCE_KEY_V1)
    state = _BINDINGS.get(binding)
    _need(
        type(state) is dict
        and (_ACTIVE.get(id(state)) is state or _UNKNOWN.get(id(state)) is state)
    )
    actual, expected = _source()._context_v6(), state["context"]
    _need(actual[:3] == expected[:3] and actual[3] is expected[3]())
    _UNKNOWN[id(state)] = state
    state["phase"] = "UNKNOWN"
    facade._retain_flat_leverage_control_v1(state["control"], key=_CONTROL_KEY_V1)


def _inhibit_flat_leverage_source_v1(binding, *, key):
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    _need(key is actor._SOURCE_KEY_V1)
    state = _BINDINGS.get(binding)
    _retain_flat_leverage_source_unknown_v1(binding, key=key)
    owner = state["control"]["owner"]
    _need(type(owner) is facade.original.DemoControlOwner)
    # Actual original safety owner, synchronous revoke before returned awaitable.
    # Persistent account EStop is a negative action and never configuration retry.
    facade.original.DemoControlOwner._revoke(owner, stop=True)
    return _persist_flat_leverage_stop_v1(state, owner)


async def _persist_flat_leverage_stop_v1(state, owner):
    try:
        async with asyncio.timeout(5):
            result = await facade.original.DemoControlOwner._tighten(owner, stop=True)
            _need(
                type(result) is facade.original.ControlObservation
                and result.state.scope == state["control"]["scope"]
                and result.state.emergency_stop is True
                and result.state.arm_request_id is None
            )
            state["configuration_stop_observation"] = result
            return result
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except Exception:  # noqa: BLE001 -- Keep stop persistence unknown and redact the database failure.
        raise NativeFlatLeverageSourceError(
            "native_flat_leverage_stop_persistence_unknown"
        ) from None


_READBACKS_V1 = {}
_CLEANUPS_V1 = {}
_RETIRED_V1 = {}
_TERMINAL_V1 = {}


@dataclass(frozen=True, slots=True, repr=False)
class FlatLeverageReadbackV1:
    binding: object
    current_record: object
    execution_mode: object
    bundle_raw: bytes
    receipt_json: bytes
    observed_at: datetime
    received_at: datetime


class _ConfigurationSourceCompletionV1(OwnedFlatLeverageSourceV1):
    __slots__ = ()


class _ConfigurationSourceRetirementV1(OwnedFlatLeverageSourceV1):
    __slots__ = ()


@dataclass(frozen=True, slots=True, repr=False)
class FlatLeverageSourceCleanupViewV1:
    binding: object
    original_source_generation: object
    root_closed_handle: object
    result_cap: object
    readback_receipt_sha256: str
    receipt_json: bytes


def _negative_context_v1(binding, *, key):
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    _need(key is actor._SOURCE_KEY_V1)
    state = _BINDINGS.get(binding)
    _need(
        type(state) is dict
        and (_ACTIVE.get(id(state)) is state or _UNKNOWN.get(id(state)) is state)
    )
    source = _source()
    source._REQUIRE_SOURCE_KERNEL_V6()
    actual, expected = source._context_v6(), state["context"]
    _need(actual[:3] == expected[:3] and actual[3] is expected[3]())
    ctrl = state["control"]
    _need(
        facade._HANDLES.get(ctrl["public_owner"]) is ctrl
        and source.configured.get_settings() is ctrl["read_session"]._settings
        and account._v3_data_pin(ctrl["read_session"]._settings.__dict__)
        == state["settings_pin"]
    )
    for factory, pin, route in state["factories"]:
        _need(
            source.history._factory_pin(factory) == pin
            and account._database_connection_pin_v4(factory) == route
        )
    return state


def _negative_window_v1(record):
    stamp = _source()._stamp_v6()
    _source().validate_stamps((record["started"], record["last"], stamp))
    _need(
        all(
            stamp[name] - record["started"][name] < record["budget_ns"]
            for name in ("utc_ns", "monotonic_ns")
        ),
        "native_flat_leverage_negative_budget_exhausted",
    )
    record["last"] = stamp
    return stamp


def acquire_flat_leverage_readback_v1(binding, *, key):
    state = _negative_context_v1(binding, key=key)
    _need(state.get("configuration_readback_attempt") is None)
    stamp = _source()._stamp_v6()
    record = {
        "state": state,
        "started": stamp,
        "last": stamp,
        "budget_ns": 10_000_000_000,
        "phase": "READING",
        "current": None,
        "result": None,
    }
    state["configuration_readback_attempt"] = record
    return _collect_flat_leverage_readback_v1(record)


async def _collect_flat_leverage_readback_v1(record):
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    state, source = record["state"], _source()
    ctrl = state["control"]
    try:
        async with asyncio.timeout(10):
            _negative_context_v1(
                state["configuration_binding"], key=actor._SOURCE_KEY_V1
            )
            stamp = _negative_window_v1(record)
            ctrl["read_session"].verify_current()
            values = ctrl["plan"].model_dump(mode="python", round_trip=True)
            values.update(
                plan_id="configuration-readback-" + secrets.token_hex(16),
                created_at=utc_from_ns(stamp["utc_ns"]),
            )
            plan = capture.CurrentDemoAccountCapturePlanV7.model_validate(
                values, strict=True
            )
            account_session = (
                source.ControlledDemoAccountSession.from_pinned_read_session(
                    read_session=ctrl["read_session"],
                    plan=plan,
                    expected_plan_sha256=capture.plan_sha256(plan),
                )
            )
            record["plan"], record["account_session"] = plan, account_session
            pending = await source.current.acquire_initial_demo_account_source_v4(
                account_session=account_session,
                private_client=ctrl["private_client"],
                session_factory=ctrl["factory"],
                private_proof_root=state["configuration_roots"].readback_proof_root,
                native_proof_root=state["configuration_roots"].readback_native_root,
                custody_repository=ctrl["custody_repository"],
            )
            record["pending"] = pending
            _negative_context_v1(
                state["configuration_binding"], key=actor._SOURCE_KEY_V1
            )
            _negative_window_v1(record)
            held = source.current._take_account_revision_lineage_v1(
                pending, key=facade.legacy_lineage._SOURCE_KEY
            )
            record["current"], record["pending"] = held, None
            current_record, _stamp = source.current._replay_account_revision_lineage_v1(
                held, key=facade.legacy_lineage._SOURCE_KEY
            )
            _negative_window_v1(record)
            mode = execution.derive_captured_execution_mode_v1(
                current_record.packet,
                expected_plan_sha256=capture.plan_sha256(plan),
                expected_packet_sha256=current_record.reference.packet_sha256,
            )
            raw = decode(current_record.current_receipt_json, capture.MAX_PACKET_BYTES)
            anchor = positions._coupled_anchor_from_held_current_record_v3(
                current_record,
                scope=ctrl["ledger_scope"],
                checked_at=utc_from_ns(_negative_window_v1(record)["utc_ns"]),
            )
            _need(
                raw.get("observed_flat") is True
                and all(item.contracts == 0 for item in anchor.positions)
                and mode.position_mode == state["execution_mode"].position_mode
                and mode.account_level == state["execution_mode"].account_level,
                "native_flat_leverage_readback_not_exact_flat_modes",
            )
            selected = execution.select_captured_isolated_execution_v1(
                mode,
                instrument_id=state["instrument_id"],
                direction=state["source_view"].result.direction,
                requested_leverage=state["configuration_plan"].selected_leverage,
            )
            _need(
                selected.leverage
                == Decimal(state["configuration_plan"].selected_leverage)
            )
            # Preserve the complete original and NEW authenticated current
            # bundles, not just the selected leverage or a one-page DTO.
            at = utc_from_ns(_negative_window_v1(record)["utc_ns"])
            receipt = canonical(
                {
                    "schema_version": "ctcc.native_flat_leverage_configuration.readback.v1",
                    "scope": [
                        ctrl["ledger_scope"].environment,
                        ctrl["ledger_scope"].account_id,
                        ctrl["ledger_scope"].settlement_currency,
                    ],
                    "original_source_receipt_sha256": sha(state["source_receipt_json"]),
                    "current_reference": source.observed.reference_document(
                        current_record.reference
                    ),
                    "current_plan_sha256": capture.plan_sha256(plan),
                    "current_receipt_json": current_record.current_receipt_json.decode(
                        "utf-8"
                    ),
                    "current_bundle_sha256": held["bundle_sha256"],
                    "selected_execution_receipt_sha256": selected.receipt_sha256,
                    "request_sha256": sha(state["configuration_plan"].request_json),
                    "target_leverage": state["configuration_plan"].selected_leverage,
                    "observed_at": utc_from_ns(record["started"]["utc_ns"]).isoformat(),
                    "received_at": at.isoformat(),
                    "account_complete": False,
                    "account_revision_issued": False,
                    "execution_authority": False,
                    "order_retry_authority": False,
                }
            )
            _need(0 < len(receipt) <= 1048576)
            result = FlatLeverageReadbackV1(
                state["configuration_binding"],
                current_record,
                mode,
                held["bundle_raw"],
                receipt,
                utc_from_ns(record["started"]["utc_ns"]),
                at,
            )
            record["result"], record["phase"] = result, "COMPLETE"
            _READBACKS_V1[id(result)] = record
            _budget(state)
            return result
    except BaseException:
        record["phase"] = "UNKNOWN"
        _retain_flat_leverage_source_unknown_v1(
            state["configuration_binding"], key=actor._SOURCE_KEY_V1
        )
        raise


def _replay_flat_leverage_readback_v1(value, *, key):
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    _need(key is actor._SOURCE_KEY_V1)
    record = _READBACKS_V1.get(id(value))
    _need(
        record is not None
        and record["result"] is value
        and record["phase"] == "COMPLETE"
    )
    _negative_context_v1(record["state"]["configuration_binding"], key=key)
    return value


async def _configuration_control_read_v1(record):
    from app.database.repositories import (
        native_leverage_configuration_v55 as repository,
    )
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    state, ctrl = record["state"], record["state"]["control"]
    _negative_context_v1(state["configuration_binding"], key=actor._SOURCE_KEY_V1)
    _negative_window_v1(record)
    async with ctrl["factory"]() as session, session.begin():
        _need(type(session) is AsyncSession)
        for sql in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET LOCAL search_path=pg_catalog,public,pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL synchronous_commit='on'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='1000ms'",
        ):
            await session.execute(text(sql))
            _negative_window_v1(record)
        await repository.verify_actual_configuration_catalog_v1(session)
        _negative_window_v1(record)
        value, event = await controls.DemoControlRepositoryV2.read_in_transaction(
            session, ctrl["scope"]
        )
        _negative_window_v1(record)
        local = ledger.QualificationLedgerRepository(
            ctrl["factory"],
            clock=lambda: utc_from_ns(_negative_window_v1(record)["utc_ns"]),
        )
        row = await local._locked(session, ctrl["ledger_scope"])
        _negative_window_v1(record)
        before = await _source().lineage55._metadata(session)
        _negative_window_v1(record)
        checkpoint = await local._read_portfolio_checkpoint_locked(
            session, ctrl["ledger_scope"], row
        )
        _negative_window_v1(record)
        meta = await _source().lineage55._metadata(session)
        _negative_window_v1(record)
        _source()._filled_cleanup_control_metadata_v12(meta)
        _need(
            before[:9] == meta[:9]
            and before[9] <= meta[9]
            and checkpoint.known_flat is True
        )
        scope_json = repository._row(
            {name: getattr(row, name) for name in repository._SCOPE_NAMES},
            repository._SCOPE_NAMES,
        )
        _need(scope_json == record["expected_scope_json"])
        result = (value, event, meta, scope_json, checkpoint.document_json, session)
        record["control_reads"].append(result)
    _negative_window_v1(record)
    return result


def _configuration_control_document_v1(value):
    return {
        "state": control.document(value[0]),
        "event_sha256": value[1],
        "metadata": [*value[2][:9], value[2][9].isoformat()],
        "scope_json": value[3].decode("utf-8"),
        "local_checkpoint_sha256": sha(value[4]),
    }


async def prepare_flat_leverage_source_cleanup_v1(result_cap, *, key):
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    _need(key is actor._SOURCE_KEY_V1)
    inputs = actor._replay_configuration_cleanup_input_for_source_v1(
        result_cap, key=_SOURCE_KEY_V1
    )
    state = _negative_context_v1(inputs.source_view.binding, key=key)
    _need(
        inputs.source_view is state["source_view"]
        and state.get("configuration_cleanup") is None
    )
    stamp = _source()._stamp_v6()
    record = {
        "state": state,
        "inputs": inputs,
        "result_cap": result_cap,
        "started": stamp,
        "last": stamp,
        "budget_ns": 5_000_000_000,
        "phase": "PREPARING",
        "control_reads": [],
        "expected_scope_json": inputs.scope_json,
        "completion": None,
        "retirement": None,
    }
    state["configuration_cleanup"] = record
    try:
        async with asyncio.timeout(5):
            ctrl, owner = state["control"], state["control"]["owner"]
            _need(
                type(owner) is facade.original.DemoControlOwner
                and facade._HANDLES.get(ctrl["public_owner"]) is ctrl
            )
            # Local revoke has no DB effect and precedes every possible await.
            facade.original.DemoControlOwner._revoke(owner, stop=False)
            before = await _configuration_control_read_v1(record)
            kind = _source()._filled_cleanup_control_kind_v12(
                before[0],
                inputs.source_view.control_state.owner_sha256,
                inputs.source_view.control_state.owner_epoch,
                before[2][9],
            )
            released = None
            if kind == "release_required":
                _need(
                    canonical(control.document(owner._observation.state))
                    == canonical(control.document(before[0]))
                    and owner._observation.event_sha256 == before[1]
                    and before[0].arm_request_id is None
                    and before[0].arm_expires_at is None
                )
                released = await facade.original.DemoControlOwner.release(owner)
                _negative_window_v1(record)
                _need(type(released) is facade.original.ControlObservation)
            after = await _configuration_control_read_v1(record)
            _need(
                before[5] is not after[5]
                and before[2][8] != after[2][8]
                and before[2][9] <= after[2][9]
                and before[3] == after[3]
                and before[4] == after[4]
            )
            if released is not None:
                _need(
                    released.state == after[0]
                    and released.event_sha256 == after[1]
                    and after[0].revision == before[0].revision + 1
                    and after[0].owner_sha256 == before[0].owner_sha256
                    and after[0].owner_epoch == before[0].owner_epoch
                    and after[0].arm_request_id is None
                    and after[0].arm_expires_at is None
                    and after[0].emergency_stop is before[0].emergency_stop
                    and after[0].pins == before[0].pins
                    and after[0].lease_until <= after[2][9]
                    and after[0].lease_until <= before[0].lease_until
                )
                classification = "own_released"
            else:
                _need(
                    after[0] == before[0]
                    and after[1] == before[1]
                    and _source()._filled_cleanup_control_kind_v12(
                        after[0],
                        inputs.source_view.control_state.owner_sha256,
                        inputs.source_view.control_state.owner_epoch,
                        after[2][9],
                    )
                    == kind
                )
                classification = kind
            resolution = canonical(
                {
                    "schema_version": "ctcc.native_flat_leverage_configuration.control_resolution.v1",
                    "classification": classification,
                    "original_owner_sha256": inputs.source_view.control_state.owner_sha256,
                    "original_owner_epoch": inputs.source_view.control_state.owner_epoch,
                    "before": _configuration_control_document_v1(before),
                    "after": _configuration_control_document_v1(after),
                    "release_observation": None
                    if released is None
                    else {
                        "state": control.document(released.state),
                        "event_sha256": released.event_sha256,
                    },
                    "execution_authority": False,
                    "order_retry_authority": False,
                }
            )
            _need(len(resolution) <= 16384)
            source = _source()
            current = source.current.replay_current_account_bundle_v4(
                state["current"]["bundle_raw"],
                expected_sha256=state["current"]["bundle_sha256"],
            )
            _need(current == state["current_record"])
            held = state["public_state"]
            directory, proof, _raw, _events = market.replay_runtime_bundle_v4(
                held["bundle_raw"], expected_sha256=held["bundle_sha256"]
            )
            _need(
                sha(canonical(proof)) == held["proof_sha256"]
                and sha(directory.read("summary.json", market.runtime.MAX_RAW))
                == held["journal_sha256"]
            )
            contracts = (
                (
                    state["current"]["bundle_sha256"],
                    current.proof_envelope_sha256,
                    current.native_proof_sha256,
                    current.native_readback_sha256,
                ),
                (
                    held["bundle_sha256"],
                    held["proof_sha256"],
                    held["journal_sha256"],
                    state["public_packet"].bundle_sha256,
                ),
            )
            readback_contract = None
            if inputs.readback is not None:
                actual = _replay_flat_leverage_readback_v1(inputs.readback, key=key)
                replayed = source.current.replay_current_account_bundle_v4(
                    actual.bundle_raw, expected_sha256=sha(actual.bundle_raw)
                )
                _need(replayed == actual.current_record)
                readback_contract = (
                    sha(actual.bundle_raw),
                    replayed.proof_envelope_sha256,
                    replayed.native_proof_sha256,
                    replayed.native_readback_sha256,
                )
            record["control_resolution_json"] = resolution
            receipt = canonical(
                {
                    "schema_version": "ctcc.native_flat_leverage_source_cleanup.v1",
                    "intent_id": inputs.intent_id,
                    "request_sha256": sha(state["configuration_plan"].request_json),
                    "source_receipt_sha256": sha(state["source_receipt_json"]),
                    "verified_result_receipt_sha256": sha(inputs.result_receipt_json),
                    "readback_receipt_sha256": None
                    if inputs.readback is None
                    else sha(inputs.readback.receipt_json),
                    "control_resolution_json": resolution.decode("ascii"),
                    "control_resolution_sha256": sha(resolution),
                    "trusted_original_sdk_contracts": contracts,
                    "trusted_readback_sdk_contract": readback_contract,
                    "root_socket_sdk_observation": None
                    if inputs.closed_view is None
                    else inputs.closed_view.socket_sdk_observation,
                    "no_root_transport_created": inputs.closed_view is None,
                    "execution_authority": False,
                    "order_retry_authority": False,
                }
            )
            _need(0 < len(receipt) <= 65536)
            source._scan_reconciliation_complete_document_v6(
                receipt, _scan_tokens(state)
            )
            _budget(state, extra=(resolution, receipt))
            cap = object.__new__(_ConfigurationSourceCompletionV1)
            view = FlatLeverageSourceCleanupViewV1(
                state["configuration_binding"],
                state["generation"],
                inputs.closed_handle,
                result_cap,
                None if inputs.readback is None else sha(inputs.readback.receipt_json),
                receipt,
            )
            record.update(completion=cap, view=view, phase="COMPLETE")
            _CLEANUPS_V1[cap] = record
            return cap
    except BaseException:
        record["phase"] = "UNKNOWN"
        _retain_flat_leverage_source_unknown_v1(state["configuration_binding"], key=key)
        raise


def _replay_flat_leverage_source_cleanup_v1(completion, *, key):
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    _need(key is actor._SOURCE_KEY_V1)
    record = (
        _CLEANUPS_V1.get(completion)
        if type(completion) is _ConfigurationSourceCompletionV1
        else None
    )
    _need(
        record is not None
        and record["completion"] is completion
        and record["phase"] == "COMPLETE"
    )
    _negative_context_v1(record["state"]["configuration_binding"], key=key)
    return record["view"]


def retire_flat_leverage_source_v1(completion, *, key):
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    view = _replay_flat_leverage_source_cleanup_v1(completion, key=key)
    record, source = _CLEANUPS_V1[completion], _source()
    state, inputs = record["state"], record["inputs"]
    root_receipt = actor._replay_configuration_root_finalization_for_source_v1(
        record["result_cap"], key=_SOURCE_KEY_V1
    )
    _need(type(root_receipt) is tuple and len(root_receipt) == 4)
    from app.exchange.okx import native_demo_write_v2 as root

    root_buffers = (
        ()
        if inputs.closed_view is None
        else root._native_leverage_configuration_retained_buffers_v1(
            state["configuration_actor_binding"], key=actor._ROOT_DATA_KEY_V1
        )
    )
    snapshot = source._close_write_buffer_bytes_v6(
        (
            root_buffers,
            source._filled_cleanup_original_buffers_v6(state),
            state["math_record"],
            state["source_receipt_json"],
            state["configuration_lineage_json"],
            state["configuration_readback_attempt"]
            if inputs.readback is not None
            else (),
            actor._retained_native_leverage_configuration_buffers_v1(
                state["generation"], key=source._SOURCE_KEY_V6
            ),
        ),
        preserve_text=True,
    )
    tokens = _scan_tokens(state)
    receipt = canonical(
        {
            "schema_version": "ctcc.native_flat_leverage_source_retirement.v1",
            "intent_id": inputs.intent_id,
            "source_cleanup_receipt_json": view.receipt_json.decode("ascii"),
            "source_cleanup_receipt_sha256": sha(view.receipt_json),
            "root_finalized_receipt": root_receipt,
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    _need(len(receipt) <= 65536)
    source._retained_budget_v6({"history_tokens": (snapshot, tokens)}, extra=(receipt,))
    source._scan_reconciliation_complete_document_v6(receipt, tokens)
    # Only authenticated historical closed sources leave positive maps. Keep
    # all full bytes/tokens and the exact facade handle through final DB replay.
    for mapping, identity in (
        (source._STATES_V6, id(state)),
        (source._UNKNOWN_V6, id(state)),
        (source._CURRENT_V6, id(state["current"])),
        (source._COVERAGE_V6, state["coverage_binding"]),
        (_ACTIVE, id(state)),
        (_UNKNOWN, id(state)),
    ):
        actual = mapping.get(identity)
        _need(actual is None or actual is state)
    for mapping, identity in (
        (source._STATES_V6, id(state)),
        (source._UNKNOWN_V6, id(state)),
        (source._CURRENT_V6, id(state["current"])),
        (source._COVERAGE_V6, state["coverage_binding"]),
        (_ACTIVE, id(state)),
        (_UNKNOWN, id(state)),
    ):
        if mapping.get(identity) is state:
            mapping.pop(identity)
    retirement = object.__new__(_ConfigurationSourceRetirementV1)
    record.update(
        retirement=retirement,
        phase="RETIRED",
        receipt_json=receipt,
        retained_buffers=snapshot,
        tokens=tokens,
    )
    state["phase"] = "RETIRED"
    _RETIRED_V1[retirement] = record
    return retirement


def _retired_flat_configuration_v1(retirement, *, key):
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    _need(key is actor._SOURCE_KEY_V1)
    record = (
        _RETIRED_V1.get(retirement)
        if type(retirement) is _ConfigurationSourceRetirementV1
        else None
    )
    _need(
        record is not None
        and record["retirement"] is retirement
        and record["phase"] == "RETIRED"
    )
    _source()._REQUIRE_SOURCE_KERNEL_V6()
    actual, expected = _source()._context_v6(), record["state"]["context"]
    _need(actual[:3] == expected[:3] and actual[3] is expected[3]())
    from app.exchange.okx import native_demo_write_v2 as root

    inputs, state = record["inputs"], record["state"]
    current_root = (
        ()
        if inputs.closed_view is None
        else root._native_leverage_configuration_retained_buffers_v1(
            state["configuration_actor_binding"], key=actor._ROOT_DATA_KEY_V1
        )
    )
    current_actor = actor._retained_native_leverage_configuration_buffers_v1(
        state["generation"], key=_source()._SOURCE_KEY_V6
    )
    combined = _source()._close_write_buffer_bytes_v6(
        (record["retained_buffers"], current_root, current_actor), preserve_text=True
    )
    record["latest_retained_buffers"] = combined
    _source()._retained_budget_v6(
        {"history_tokens": (combined, record["tokens"])},
        extra=(record["receipt_json"],),
    )
    return record


def _replay_flat_leverage_retirement_v1(retirement, *, key):
    return _retired_flat_configuration_v1(retirement, key=key)["receipt_json"]


def scan_flat_leverage_retired_data_v1(retirement, raw, *, key):
    record = _retired_flat_configuration_v1(retirement, key=key)
    _need(type(raw) is bytes and 0 < len(raw) <= 320000)
    _source()._retained_budget_v6(
        {"history_tokens": (record["latest_retained_buffers"], record["tokens"])},
        extra=(record["receipt_json"], raw),
    )
    _source()._scan_reconciliation_complete_document_v6(raw, record["tokens"])


def compact_flat_leverage_source_retirement_v1(retirement, committed, *, key):
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    record = _retired_flat_configuration_v1(retirement, key=key)
    proof = actor._take_committed_configuration_for_source_v1(
        committed, key=_SOURCE_KEY_V1
    )
    _need(
        type(proof) is tuple
        and len(proof) == 4
        and proof[0] is retirement
        and type(proof[1]) is bytes
        and type(proof[2]) is tuple
        and type(proof[3]) is bytes
    )
    scan_flat_leverage_retired_data_v1(retirement, proof[1], key=key)
    _source()._filled_cleanup_control_metadata_v12(proof[2])
    final = decode(proof[1], 320000)
    _need(
        final["phase"] in ("settled", "no_change")
        and final["intent_id"] == record["inputs"].intent_id
    )
    state, ctrl = record["state"], record["state"]["control"]
    _need(
        facade._HANDLES.get(ctrl["public_owner"]) is ctrl
        and ctrl["owner"]._arm_intent is False
    )
    receipt = canonical(
        {
            "schema_version": "ctcc.native_flat_leverage_source_compacted.v1",
            "intent_id": final["intent_id"],
            "final_row_sha256": sha(proof[1]),
            "retirement_receipt_sha256": sha(record["receipt_json"]),
            "read_xid": proof[2][8],
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    completion = object.__new__(_ConfigurationSourceCompletionV1)
    _CONTROL_COMPLETIONS[completion] = (
        ctrl,
        ctrl["public_owner"],
        ctrl["owner"],
        receipt,
    )
    facade._finish_flat_leverage_control_v1(ctrl, completion, key=_CONTROL_KEY_V1)
    _need(ctrl["public_owner"] not in facade._HANDLES)
    _need(
        _BINDINGS.get(state["configuration_binding"]) is state
        and _CLEANUPS_V1.get(record["completion"]) is record
    )
    _BINDINGS.pop(state["configuration_binding"])
    _READBACKS_V1.pop(id(record["inputs"].readback), None)
    _CLEANUPS_V1.pop(record["completion"])
    _RETIRED_V1.pop(retirement)
    _TERMINAL_V1[retirement] = receipt
    # Only fully retired tombstones may be evicted. UNKNOWN is never evicted.
    if len(_TERMINAL_V1) > _MAX_SOURCES:
        _TERMINAL_V1.pop(next(iter(_TERMINAL_V1)))
    record.clear()
    state.clear()
    return receipt


def _take_flat_leverage_control_completion_v1(completion, *, key):
    _need(key is facade._SOURCE_KEY)
    value = (
        _CONTROL_COMPLETIONS.pop(completion, None)
        if type(completion) is _ConfigurationSourceCompletionV1
        else None
    )
    _need(type(value) is tuple and len(value) == 4)
    return value
