"""Genuine issued55 source → NEW original public capture → fixed local G1–G10.

No OFF V13 owner is elevated. The old precursor and local gate functions are
shared calculation only. Source/context authority comes exclusively from the
same native issued registry, then moves once into the fixed native candidate.
"""

from __future__ import annotations

import asyncio
import json
import sys as _native_sys
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from weakref import WeakKeyDictionary, finalize

from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as catalog55,
)
from app.database.repositories import demo_control as historical_control
from app.database.repositories import demo_control_current55_v2 as control55
from app.database.repositories import public_runtime_bundle_custody as custody
from app.database.repositories import qualification_ledger as repository_module
from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.exchange.okx import private_rest
from app.trade_qualification import account_capture as capture
from app.trade_qualification import captured_execution_mode_v1 as execution_mode
from app.trade_qualification import demo_market_source_v4 as market
from app.trade_qualification import native_account_risk_assembly_v1 as account
from app.trade_qualification import native_continuation_guard_v1 as source_guard
from app.trade_qualification import owned_current_original_prefix_v1 as local
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope

_SOURCE_KEY_V2 = object()
_PUBLIC_KEY_V2 = object()
_HOLDS = {}
_UNKNOWN = {}
_OWNERS = WeakKeyDictionary()
_BORROWERS = WeakKeyDictionary()


class NativeOriginalLocalError(ValueError):
    """Only source-safe closed codes leave this native continuation."""


def _deny(code="native_original55_local_unavailable"):
    raise NativeOriginalLocalError(code) from None


def _raise_safe(error, code):
    if isinstance(error, asyncio.CancelledError):
        raise asyncio.CancelledError() from None
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        raise type(error)() from None
    if type(error) is NativeOriginalLocalError:
        raise error from None
    _deny(code)


@dataclass(frozen=True, slots=True, repr=False)
class IssuedOriginalSourceV2:
    """Inert private view; native registry replay alone authenticates its source."""

    read_session: object
    private_client: object
    session_factory: object
    custody_session_factory: object
    custody_repository: object
    current_plan: capture.CurrentDemoAccountCapturePlanV7
    current_record: object
    private_raw_bundle: bytes
    control_expectation: object
    configured_policy: object
    source_generation: object
    original_expires_at: datetime
    monotonic_deadline_ns: int
    execution_mode: execution_mode.CapturedExecutionModeV1
    execution_authority: bool = field(default=False, init=False)


class OwnedNativeOriginalLocalV2:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny()

    def __copy__(self):
        _deny()

    def __deepcopy__(self, memo):
        _deny()

    def __reduce_ex__(self, protocol):
        _deny()

    def close(self):
        binding = _OWNERS.pop(self, None)
        if binding is not None:
            _release(binding)


def _clock():
    return utc_from_ns(native_stamp()["utc_ns"])


def _source(record):
    source_guard.check_saved_native_guard_v1(record)
    if (
        record.get("closed", False)
        or asyncio.current_task() is not record["task"]
        or record["task"].cancelling()
    ):
        _deny()
    value = account._replay_issued_original_source_v2(
        record["issued_handle"], key=_SOURCE_KEY_V2
    )
    if (
        type(value) is not IssuedOriginalSourceV2
        or type(value.read_session) is not private_rest.DemoPinnedReadSession
        or type(value.private_client) is not private_rest.OkxDemoPrivateRestClient
        or type(value.current_plan) is not capture.CurrentDemoAccountCapturePlanV7
        or type(value.custody_repository) is not custody.RuntimeBundleCustodyRepository
        or value.custody_repository._factory is not value.custody_session_factory
        or value.read_session._owner is not value.private_client
        or value.current_plan.session_binding_id != value.read_session.binding_id
        or type(value.private_raw_bundle) is not bytes
        or not 0 < len(value.private_raw_bundle) <= custody.MAX_BUNDLE_BYTES
        or type(value.original_expires_at) is not datetime
        or value.original_expires_at.tzinfo is not UTC
        or type(value.monotonic_deadline_ns) is not int
        or value.monotonic_deadline_ns <= 0
        or value.current_plan.environment != "demo"
        or value.current_plan.settlement_currency != "USDT"
        or value.execution_authority is not False
    ):
        _deny("native_original55_exact_source_context_required")
    value.read_session.verify_current()
    custody.RuntimeBundleCustodyRepository._check_factory(value.custody_repository)
    packet = value.current_record.packet
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=capture.plan_sha256(value.current_plan)
    )
    if frozen.sha256 != value.current_record.reference.packet_sha256:
        _deny("native_original55_current_packet_changed")
    execution_mode.verify_captured_execution_mode_v1(
        value.execution_mode,
        packet,
        expected_plan_sha256=capture.plan_sha256(value.current_plan),
        expected_packet_sha256=frozen.sha256,
    )
    pin = (
        value.read_session,
        value.private_client,
        value.session_factory,
        value.custody_session_factory,
        value.custody_repository,
        value.current_plan,
        value.configured_policy,
        value.source_generation,
    )
    previous = record.get("source_pin")
    if previous is not None and (
        any(a is not b for a, b in zip(pin, previous, strict=True))
        or value.private_raw_bundle != record["private_raw_bundle"]
        or frozen.payload != record["private_packet_raw"]
        or value.control_expectation != record["control_expectation"]
        or value.original_expires_at != record["original_source_expiry"]
        or value.monotonic_deadline_ns != record["original_source_monotonic_expiry"]
    ):
        _deny("native_original55_original_source_changed")
    stamp = native_stamp()
    validate_stamps((record["last"], stamp))
    record["last"] = stamp
    deadline = (
        value.original_expires_at
        if record.get("deadline") is None
        else min(record["deadline"], value.original_expires_at)
    )
    if (
        utc_from_ns(stamp["utc_ns"]) >= deadline
        or stamp["monotonic_ns"] >= value.monotonic_deadline_ns
    ):
        _deny("native_original55_original_expired")
    return value, frozen, stamp, pin


def _release(binding):
    record = _HOLDS.get(binding)
    if record is None or record.get("closed", False):
        return
    record["closed"] = True
    failure = None
    try:
        public = record.get("public_state")
        if public is not None:
            market.operational._discard_owned_route_v4(public["v4_route_claim"])
        elif record.get("pending_public") is not None:
            market._discard_native_original55_v2_owner(
                record["pending_public"], key=_PUBLIC_KEY_V2
            )
    except BaseException as error:  # noqa: BLE001 -- Retain failure while releasing the independent owners.
        failure = error
    try:
        if not record.get("account_adopted", False):
            account.discard_issued_account_owner_v1(
                record["borrowed_owner"], key=_SOURCE_KEY_V2
            )
    except BaseException as error:  # noqa: BLE001 -- Preserve the first failure through independent cleanup.
        if failure is None:
            failure = error
    if failure is not None:
        _UNKNOWN[binding] = record
        if isinstance(failure, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            raise type(failure)() from None
        _deny("native_original55_release_unknown")
    _HOLDS.pop(binding, None)
    _BORROWERS.pop(record["borrowed_owner"], None)


def _public(record):
    packet, context, stamp = market._replay_native_original55_v2(
        record["public_state"], key=_PUBLIC_KEY_V2
    )
    value, _frozen, final, _pin = _source(record)
    directory, _proof, _raw, events = market.replay_runtime_bundle_v4(
        record["public_state"]["bundle_raw"],
        expected_sha256=record["public_state"]["bundle_sha256"],
    )
    account.scan_issued_original_public_v1(
        record["issued_handle"], directory, events, key=_SOURCE_KEY_V2
    )
    if (
        context.market.instrument_id != record["instrument_id"]
        or record["public_session"]._checked_read_session(record["public_plan"])
        is not value.read_session
        or record["public_state"]["expires_at"] != record["public_expiry"]
    ):
        _deny("native_original55_public_context_changed")
    validate_stamps((stamp, final))
    return packet, context


def _settle(binding, original_error=None):
    try:
        _release(binding)
    except BaseException as error:  # noqa: BLE001 -- Keep release uncertain and preserve shutdown signals.
        if isinstance(
            original_error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
        ):
            _raise_safe(original_error, "native_original55_release_unknown")
        _raise_safe(error, "native_original55_release_unknown")


def _prepare_math(record):
    value, _frozen, stamp, _pin = _source(record)
    packet, context = _public(record)
    created = utc_from_ns(stamp["utc_ns"])
    deadline = min(record["deadline"], record["public_state"]["expires_at"])
    policy = local.fixed_native_original_g1_policy()
    g1 = local.data_v2.evaluate_public_market_data_v2(
        packet,
        expected_bundle_sha256=packet.bundle_sha256,
        policy=policy,
        evaluated_at=created,
    )
    g1 = local.data_v2.verify_public_market_data_v2(
        g1,
        packet,
        expected_bundle_sha256=packet.bundle_sha256,
        policy=policy,
        evaluated_at=created,
    )
    derive = (
        local.precursor.derive_original_candidate_precursor_v2
        if record["strategy"] in local._BASE_STRATEGIES
        else local.precursor.derive_owned_original_candidate_precursor_v3
    )
    original = derive(
        packet,
        value.current_record.packet,
        strategy=record["strategy"],
        expected_public_bundle_sha256=packet.bundle_sha256,
        expected_account_plan_sha256=capture.plan_sha256(value.current_plan),
        expected_account_packet_sha256=value.current_record.reference.packet_sha256,
        data_policy=policy,
        expected_data_policy_sha256=local.DATA_POLICY_SHA256,
        created_at=created,
        service_deadline=deadline,
    )
    replay = (packet, value.current_record, context, g1, original, created, deadline)
    computed = _computed_family_v2(record, replay)
    raw_policy = _policy_family_v2(record)
    numeric = decode(raw_policy)
    if computed.result.raw_score < numeric["minimum_score"]:
        _deny("native_original55_score_below_configured_minimum")
    rules = local.derive_captured_instrument_rules(
        value.current_record.packet,
        instrument_id=record["instrument_id"],
        expected_plan_sha256=capture.plan_sha256(value.current_plan),
        expected_packet_sha256=value.current_record.reference.packet_sha256,
    )
    source = decode(computed.g1.source_json.encode(), local.data.MAX_SOURCE_BYTES)
    original_market = MarketSnapshot.model_validate_json(
        json.dumps(source["market"]), strict=True
    )
    original_analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(source["analysis"]), strict=True
    )
    protection = local.ProtectionPolicy.model_validate_json(
        json.dumps(numeric["protection"]), strict=True
    )
    selector = (
        local.select_structural_protection
        if record["strategy"] in local._BASE_STRATEGIES
        else local._select_owned_family_protection_v2
    )
    selected = selector(
        computed.detection,
        original_market,
        original_analysis,
        observed_at=created,
        entry=computed.intent.candidate_entry,
        tick_size=rules.tick_size,
        **{
            name: item
            for name, item in protection.model_dump().items()
            if name != "policy_id"
        },
    )
    selection_raw = selected.to_audit_json()
    if (
        not 0 < len(selection_raw.encode("ascii")) <= 8 * 1024 * 1024
        or selected.reference_entry != computed.intent.candidate_entry
        or selected.report_id != computed.intent.report_id
        or selected.instrument_id != computed.intent.instrument_id
        or selected.direction != computed.intent.direction
        or selected.strategy != computed.intent.strategy
        or selected.source_sha256 != computed.g1.source_sha256
    ):
        _deny("native_original55_selected_geometry_binding_changed")
    frame = {
        "source_contract": "ctcc.native_original_current55.v2",
        "factory": value.session_factory,
        "custody_factory": value.custody_session_factory,
        "repository": value.custody_repository,
        "read_session": value.read_session,
        "private_client": value.private_client,
        "plans": (record["public_plan"], value.current_plan),
        "policy": record["market_policy"],
        "market_state": record["public_state"],
        "deadline": deadline,
        "local_gate_policy_v13": raw_policy,
    }
    original_deadline = min(
        deadline,
        computed.intent.expires_at,
        computed.original_zone.expires_at,
        computed.detection.trigger.expires_at,
    )
    record.update(
        created_at=created,
        deadline=original_deadline,
        service_deadline=deadline,
        replay=replay,
        original=original,
        computed=computed,
        policy_raw=raw_policy,
        public_packet=packet,
        quote_value=context.quote,
        quote_context=local._replay_local_quote_v13(
            packet, context.quote, computed, evaluated_at=created
        ),
        market=original_market,
        analysis=original_analysis,
        tick_size=rules.tick_size,
        protection=protection,
        selection=selected,
        selection_raw=selection_raw,
        scope=LedgerScope(
            account_id=value.current_plan.expected_uid,
            settlement_currency=value.current_plan.settlement_currency,
        ),
        prefix={"frame": frame, "receipt_json": computed.receipt_json},
    )


def _replay(binding):
    if type(binding) is not object or type(_HOLDS) is not dict:
        _deny()
    record = _HOLDS.get(binding)
    if type(record) is not dict:
        _deny()
    value, _frozen, _stamp, _pin = _source(record)
    packet, context = _public(record)
    policy = local.fixed_native_original_g1_policy()
    verify = (
        local.precursor.verify_original_candidate_precursor_v2
        if record["strategy"] in local._BASE_STRATEGIES
        else local.precursor.verify_owned_original_candidate_precursor_v3
    )
    repeated = verify(
        record["original"],
        packet,
        value.current_record.packet,
        strategy=record["strategy"],
        expected_public_bundle_sha256=packet.bundle_sha256,
        expected_account_plan_sha256=capture.plan_sha256(value.current_plan),
        expected_account_packet_sha256=value.current_record.reference.packet_sha256,
        data_policy=policy,
        expected_data_policy_sha256=local.DATA_POLICY_SHA256,
        created_at=record["created_at"],
        service_deadline=record["service_deadline"],
    )
    replay = (
        packet,
        value.current_record,
        context,
        record["computed"].g1,
        repeated,
        record["created_at"],
        record["service_deadline"],
    )
    if (
        _computed_family_v2(record, replay).receipt_json
        != record["computed"].receipt_json
        or _policy_family_v2(record) != record["policy_raw"]
    ):
        _deny("native_original55_fixed_local_policy_or_source_changed")
    if record.get("result") is not None:
        actual = _local_math_family_v2(
            record, record["local_journal"], observed_at=record["local_evaluated_at"]
        )
        if (
            actual != record["result"]
            or sha(canonical(actual.model_dump(mode="json"))) != record["result_sha256"]
            or len(actual.gates) != 10
            or not all(gate.passed for gate in actual.gates)
        ):
            _deny("native_original55_local_gates_changed")
    return record, replay


def _journal_context_v2(binding, *, key):
    if key is not _SOURCE_KEY_V2:
        _deny()
    record, _replay_value = _replay(binding)
    source = _source(record)[0]
    return record, source


def _original_public_scan_tokens_v2(source_generation, *, key):
    """Native scanner only; never expose terminal secrets in a consumer view."""
    from app.trade_qualification import (
        native_controlled_account_source_v6 as native_source,
    )

    if key is not native_source._SCAN_KEY_V6:
        _deny("native_original55_private_scanner_required")
    matches = [
        record
        for record in _HOLDS.values()
        if record.get("source_generation") is source_generation
        and not record.get("closed", False)
        and record.get("task") is asyncio.current_task()
        and record.get("public_state") is not None
    ]
    if len(matches) != 1:
        _deny("native_original55_exact_public_scan_owner_required")
    record = matches[0]
    source_guard.check_saved_native_guard_v1(record)
    market._check_owner(record["public_state"])
    route = market.operational._owned_route_state_v4(
        record["public_state"]["v4_route_claim"],
        invocation=record["public_state"]["invocation"],
        owner_task=record["task"],
    )
    tokens = route["secret_tokens"]
    if (
        type(tokens) is not tuple
        or len(tokens) < 4
        or any(
            type(token) is not str or not token.isascii() or not token
            for token in tokens
        )
    ):
        _deny("native_original55_actual_route_scan_tokens_required")
    return tokens


class _Attempt(Coroutine):
    def __init__(self, binding):
        self.binding, self.body = binding, _capture(binding)

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            return self.body.send(value)
        except BaseException:
            if not _HOLDS.get(self.binding, {}).get("issued", False):
                _release(self.binding)
            raise

    def throw(self, *args):
        try:
            return self.body.throw(*args)
        except BaseException:
            if not _HOLDS.get(self.binding, {}).get("issued", False):
                _release(self.binding)
            raise

    def close(self):
        try:
            self.body.close()
        finally:
            if not _HOLDS.get(self.binding, {}).get("issued", False):
                _release(self.binding)


def capture_owned_original_current55_local_v2(
    issued_owner,
    *,
    instrument_id,
    strategy,
    market_policy,
    public_root,
    public_account_proof_root,
):
    """Borrow one genuine issued source once; no clock/factory/source/PASS input."""
    if (
        type(issued_owner) is not account.OwnedIssuedAccountRevisionV1
        or len(_HOLDS) + len(_UNKNOWN) >= 16
        or _BORROWERS.get(issued_owner) is not None
        or type(instrument_id) is not str
        or instrument_id not in market.REVIEWED_DEMO_INSTRUMENT_IDS
        or type(strategy) is not str
        or strategy not in local.precursor.STRATEGY_CATALOG
        or any(
            type(root) is not type(Path()) or not root.is_absolute()
            for root in (public_root, public_account_proof_root)
        )
        or public_root == public_account_proof_root
        or public_root in public_account_proof_root.parents
        or public_account_proof_root in public_root.parents
    ):
        _deny("native_original55_capture_configuration_invalid")
    binding = object()
    initial = native_stamp()
    record = {
        "issued_handle": issued_owner,
        "borrowed_owner": issued_owner,
        "task": asyncio.current_task(),
        "last": initial,
        "deadline": None,
        "instrument_id": instrument_id,
        "strategy": strategy,
        "market_policy": market.public._policy_copy(market_policy),
        "public_root": public_root,
        "public_account_proof_root": public_account_proof_root,
    }
    source_guard.save_native_guard_v1(record, _NATIVE_GUARD)
    _HOLDS[binding], _BORROWERS[issued_owner] = record, binding
    try:
        value, frozen, _stamp, pin = _source(record)
        record.update(
            source_pin=pin,
            private_raw_bundle=value.private_raw_bundle,
            private_packet_raw=frozen.payload,
            control_expectation=value.control_expectation,
            original_source_expiry=value.original_expires_at,
            original_source_monotonic_expiry=value.monotonic_deadline_ns,
            deadline=value.original_expires_at,
        )
        record["source_generation"] = value.source_generation
        if strategy not in local._BASE_STRATEGIES:
            record["family_policy_raw"] = local._freeze_owned_family_policy_v2(strategy)
        return _Attempt(binding)
    except BaseException as error:  # noqa: BLE001 -- Settle the source binding before a redacted rethrow.
        _settle(binding, error)
        _raise_safe(error, "native_original55_source_preparation_failed")


async def _capture(binding):
    record = _HOLDS[binding]
    pending_failure = None
    try:
        value, _frozen, _stamp, _pin = _source(record)
        loop_started = asyncio.get_running_loop().time()
        stamp = native_stamp()
        remaining = min(
            (record["deadline"] - utc_from_ns(stamp["utc_ns"])).total_seconds(),
            (value.monotonic_deadline_ns - stamp["monotonic_ns"]) / 1_000_000_000,
        )
        if remaining <= 0:
            _deny("native_original55_original_expired")
        async with asyncio.timeout_at(loop_started + remaining) as budget:
            async with value.session_factory() as session, session.begin():
                await session.execute(
                    repository_module.text(
                        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED"
                    )
                )
                _source(record)
                await session.execute(
                    repository_module.text(
                        "SET LOCAL search_path TO pg_catalog, public, pg_temp"
                    )
                )
                _source(record)
                await session.execute(
                    repository_module.text("SET LOCAL TIME ZONE 'UTC'")
                )
                _source(record)
                await session.execute(
                    repository_module.text("SET LOCAL synchronous_commit='on'")
                )
                _source(record)
                await catalog55.verify_actual_issuer_catalog_v2(session)
                _source(record)
            document = value.current_plan.model_dump(mode="python", round_trip=True)
            document.update(plan_id="original55-" + uuid4().hex, created_at=_clock())
            record["public_plan"] = (
                capture.CurrentDemoAccountCapturePlanV7.model_validate(
                    document, strict=True
                )
            )
            record["public_session"] = (
                ControlledDemoAccountSession.from_pinned_read_session(
                    read_session=value.read_session,
                    plan=record["public_plan"],
                    expected_plan_sha256=capture.plan_sha256(record["public_plan"]),
                )
            )
            record[
                "pending_public"
            ] = await market.acquire_initial_demo_market_source_v4(
                record["public_root"],
                account_proof_root=record["public_account_proof_root"],
                instrument_id=record["instrument_id"],
                market_policy=record["market_policy"],
                account_session=record["public_session"],
                private_client=value.private_client,
                custody_repository=value.custody_repository,
            )
            _source(record)
            record["public_state"] = market._take_native_original55_v2(
                record.pop("pending_public"), key=_PUBLIC_KEY_V2
            )
            record["public_expiry"] = record["public_state"]["expires_at"]
            _prepare_math(record)
            stamp = native_stamp()
            shorter = min(
                (record["deadline"] - utc_from_ns(stamp["utc_ns"])).total_seconds(),
                (value.monotonic_deadline_ns - stamp["monotonic_ns"]) / 1_000_000_000,
            )
            if shorter <= 0:
                _deny("native_original55_original_expired")
            budget.reschedule(
                min(budget.when(), asyncio.get_running_loop().time() + shorter)
            )
            repository = repository_module.QualificationLedgerRepository(
                value.session_factory, clock=_clock
            )
            (
                first,
                control,
            ) = await repository._inspect_native_original55_local_events_v2(
                binding, key=_SOURCE_KEY_V2
            )
            _replay(binding)
            (
                second,
                current_control,
            ) = await repository._inspect_native_original55_local_events_v2(
                binding, key=_SOURCE_KEY_V2
            )
            _replay(binding)
            stable = (
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
            a, b = decode(first.document_json), decode(second.document_json)
            if (
                first.received_at > second.observed_at
                or first.consumed_event_keys != second.consumed_event_keys
                or any(a[name] != b[name] for name in stable)
                or control != current_control
            ):
                _deny("native_original55_local_journal_changed")
            evaluated = _clock()
            result = _local_math_family_v2(record, second, observed_at=evaluated)
            if len(result.gates) != 10 or not all(gate.passed for gate in result.gates):
                _deny("native_original55_local_gates_rejected")
            result_json = canonical(result.model_dump(mode="json"))
            record.update(
                result=result,
                result_sha256=sha(result_json),
                result_json=result_json,
                local_evaluated_at=evaluated,
                local_journal=second,
                control=current_control,
            )
            _replay(binding)
            owner = object.__new__(OwnedNativeOriginalLocalV2)
            _OWNERS[owner] = binding
            record["finalizer"] = finalize(owner, _release, binding)
            record["issued"] = True
            return owner
    except BaseException as error:  # noqa: BLE001 -- Keep capture failure through final owner settlement.
        pending_failure = error
        _raise_safe(error, "native_original55_capture_or_journal_failed")
    finally:
        if not record.get("issued", False):
            _settle(binding, pending_failure)


def _take_for_g11_v2(owner, *, key):
    from app.trade_qualification import owned_native_candidate_v1 as candidate

    if key is not candidate._G11_KEY_V1:
        _deny()
    binding = (
        _OWNERS.pop(owner, None) if type(owner) is OwnedNativeOriginalLocalV2 else None
    )
    if binding is None:
        _deny()
    _HOLDS[binding]["finalizer"].detach()
    try:
        _replay(binding)
        return binding
    except BaseException:
        _release(binding)
        raise


def _adopt_g11_binding_v2(binding, issued_binding, *, key):
    from app.trade_qualification import owned_native_candidate_v1 as candidate

    if key is not candidate._G11_KEY_V1 or type(binding) is not object:
        _deny()
    record = _HOLDS.get(binding)
    if type(record) is not dict or record.get("account_adopted", False):
        _deny()
    # The same native replay authenticates the new held purpose against the
    # original source_generation and raw packet; no replacement issuer is used.
    previous = record["issued_handle"]
    record["issued_handle"] = issued_binding
    try:
        _replay(binding)
    except BaseException:
        record["issued_handle"] = previous
        raise
    record["account_adopted"] = True


def _replay_for_g11_v2(binding, *, key):
    from app.trade_qualification import owned_native_candidate_v1 as candidate

    if key is not candidate._G11_KEY_V1:
        _deny()
    return _replay(binding)


def _release_for_g11_v2(binding, *, key):
    from app.trade_qualification import owned_native_candidate_v1 as candidate

    if key is not candidate._G11_KEY_V1:
        _deny()
    _release(binding)


def _computed_family_v2(record, replay):
    if record["strategy"] in local._BASE_STRATEGIES:
        return local._compute(replay)
    return local._compute_owned_family_v2(replay)


def _policy_family_v2(record):
    if record["strategy"] in local._BASE_STRATEGIES:
        return local._freeze_local_gate_policy_v13()
    actual = local._freeze_owned_family_policy_v2(record["strategy"])
    if actual != record["family_policy_raw"]:
        _deny("native_original55_original_family_policy_changed")
    return actual


def _local_math_family_v2(record, journal, *, observed_at):
    if record["strategy"] in local._BASE_STRATEGIES:
        return local._evaluate_original_local_math_v13(
            record, journal, observed_at=observed_at
        )
    return local._evaluate_owned_family_local_math_v2(
        record, journal, observed_at=observed_at
    )


def _retained_original_family_buffers_v2(source_generation, *, key):
    """Native budget-only actual buffers; no replay, clock, DB or serialization."""
    from app.trade_qualification import native_controlled_account_source_v6 as source

    if key is not source._SOURCE_KEY_V6 or type(source_generation) is not object:
        _deny("native_original55_private_budget_key_required")
    if type(_HOLDS) is not dict or type(_UNKNOWN) is not dict:
        _deny("native_original55_budget_registry_changed")
    records = {}
    for record in (*_HOLDS.values(), *_UNKNOWN.values()):
        if type(record) is not dict or any(type(name) is not str for name in record):
            _deny("native_original55_budget_record_type_changed")
        if record.get("source_generation") is source_generation:
            records[id(record)] = record
    if len(records) > 16:
        _deny("native_original55_budget_registry_capacity")
    result = []
    for record in records.values():
        for name in (
            "private_raw_bundle",
            "private_packet_raw",
            "policy_raw",
            "selection_raw",
        ):
            if name in record:
                value = record[name]
                if type(value) not in (bytes, str):
                    _deny("native_original55_budget_buffer_type_changed")
                result.append(value)
        if "public_state" in record:
            state = record["public_state"]
            if type(state) is not dict or any(type(name) is not str for name in state):
                _deny("native_original55_budget_record_type_changed")
            result.append(state["bundle_raw"])
        if "public_packet" in record:
            packet = record["public_packet"]
            if type(packet) is not local.public_v2.CollectedPublicMarketV2:
                _deny("native_original55_budget_packet_type_changed")
            result.append(object.__getattribute__(packet, "packet_json"))
        if "original" in record:
            value = record["original"]
            if type(value) not in (
                local.precursor.OriginalCandidatePrecursorV2,
                local.precursor.OwnedOriginalCandidatePrecursorV3,
            ):
                _deny("native_original55_budget_original_type_changed")
            result.append(object.__getattribute__(value, "receipt_json"))
        if "computed" in record:
            value = record["computed"]
            if type(value) not in (
                local.CurrentOriginalBasePrefixInputsV1,
                local.CurrentOriginalFamilyPrefixInputsV2,
            ):
                _deny("native_original55_budget_computed_type_changed")
            result.append(object.__getattribute__(value, "receipt_json"))
            if type(value) is local.CurrentOriginalFamilyPrefixInputsV2:
                result.append(object.__getattribute__(value, "family_history_json"))
        if "result" in record:
            # The exact bytes used for its existing result hash were retained at
            # creation. Missing is unknown; this reader never regenerates them.
            result.append(record["result_json"])
    if any(type(value) not in (bytes, str) for value in result):
        _deny("native_original55_budget_buffer_type_changed")
    return tuple(result)


_NATIVE_GUARD = source_guard.install_native_guards_v1(
    _native_sys.modules[__name__],
    ("capture_owned_original_current55_local_v2",),
    dependencies=(
        account,
        local,
        market,
        custody,
        catalog55,
        catalog55.policy,
        catalog55.policy.native_close_intent,
        catalog55.policy.native_close_index,
        local.precursor,
        local._owned_conditions_v2,
        local._owned_conditions_v2._owned_regime_v2,
        local._owned_conditions_v2._owned_range_v2,
        local._owned_conditions_v2._owned_sweep_v2,
        local._owned_conditions_v2._owned_sweep_history_v2,
        local._owned_conditions_v2._owned_sweep_v2.journal,
        local._local_protection_v13,
        local.bracket_policy,
        execution_mode,
        private_rest,
        repository_module,
        control55,
        historical_control,
    ),
)
