"""Owned G12 -> NEW native public/account -> fixed-candidate R7 hold.

The inner V4 public packet keeps its initial-public schema. This distinct live
owner binds its actual raw request starts to the completed G12 readback barrier;
it never relabels that packet or accepts a saved receipt/DTO as authority.
The resulting hold moves only to the existing qualification-ledger transaction.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys as _native_sys
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
from weakref import WeakKeyDictionary, finalize

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
from app.trade_qualification import account_capture as capture
from app.trade_qualification import captured_execution_mode_v1 as _execution_mode
from app.trade_qualification import current_economics_v2 as economics
from app.trade_qualification import data, data_v2
from app.trade_qualification import demo_market_source_v4 as market
from app.trade_qualification import fixed_original_event_path_v1 as _native_path
from app.trade_qualification import fixed_protection as _owned_fixed_v2
from app.trade_qualification import native_account_risk_assembly_v1 as account
from app.trade_qualification import native_continuation_guard_v1 as _native_guard
from app.trade_qualification import native_dynamic_structural_risk_v1 as _dynamic_risk
from app.trade_qualification import native_g12_publication_v2 as publication
from app.trade_qualification import native_postpublication_support_v2 as measured
from app.trade_qualification import owned_current_original_prefix_v1 as local
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.fixed_original_event_path_v1 import (
    evaluate_fixed_original_event_path_v1,
    join_confirmed_original_path_v1,
)
from app.trade_qualification.reservations import (
    RiskCoverage,
    ScenarioOperands,
    exact_coverage,
)

_PUBLICATION_KEY_V2 = object()
_ACCOUNT_KEY_V2 = object()
_DYNAMIC_RISK_KEY_V2 = object()
_PUBLIC_SOURCE_KEY_V2 = object()
_OWNERS = WeakKeyDictionary()
_PUBLIC_SCAN_HOLDS = {}
_DENIAL_MAX_BYTES = 16 * 1024


class NativeR7Error(ValueError):
    """Fixed rejections only; no raw source/private/path/SQL details."""


def _deny(code="native_r7_unavailable"):
    raise NativeR7Error(code) from None


@dataclass(frozen=True, slots=True)
class NativePostpublicationRootsV2:
    public_root: Path
    public_account_proof_root: Path
    account_roots: account.PostpublicationAccountRootsV1


class OwnedNativePostpublicationR7V2:
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
        state = _OWNERS.pop(self, None)
        if state is not None:
            _release(state)


def _release(state):
    if state.get("closed", False):
        return
    state["closed"] = True
    scan_binding = state.get("public_scan_binding")
    if _PUBLIC_SCAN_HOLDS.get(scan_binding) is state:
        _PUBLIC_SCAN_HOLDS.pop(scan_binding)
    try:
        binding = state.get("account_binding")
        if binding is not None:
            account._release_issued_account_for_r7_v2(binding, key=_ACCOUNT_KEY_V2)
        elif state.get("pending_account") is not None:
            account.discard_issued_account_owner_v1(
                state["pending_account"], key=_ACCOUNT_KEY_V2
            )
    finally:
        try:
            public = state.get("public_state")
            if public is not None:
                market.operational._discard_owned_route_v4(public["v4_route_claim"])
            elif state.get("pending_public") is not None:
                market._discard_native_postpublication_v2_owner(
                    state["pending_public"], key=_PUBLIC_SOURCE_KEY_V2
                )
        finally:
            publication._release(state["published"])


def _check(state):
    _native_guard.check_saved_native_guard_v1(state)
    if state.get("closed", False) or asyncio.current_task() is not state["task"]:
        _deny()
    original, replay, issued, stamp = publication._check(state["published"])
    validate_stamps((state["last"], stamp))
    measured._after(state["barrier"], stamp)
    state["last"] = stamp
    if utc_from_ns(stamp["utc_ns"]) >= state["deadline"]:
        _deny("native_r7_original_expired")
    return original, replay, issued, stamp


def _public(state):
    packet, context, _stamp = market._replay_native_postpublication_v2(
        state["public_state"], key=_PUBLIC_SOURCE_KEY_V2
    )
    native, proof, _, events = market.replay_runtime_bundle_v4(
        state["public_state"]["bundle_raw"],
        expected_sha256=state["public_state"]["bundle_sha256"],
    )
    plan = decode(native.read("plan.json", measured.runtime.MAX_RAW))
    for value in (proof["request_start"], plan["invocation_started"]):
        measured._after(state["barrier"], value)
    for event in events:
        if event["kind"] in {"request", "ws_connect"}:
            measured._after(state["barrier"], event["metadata"]["started"])
        elif event["kind"] in {"clock_before", "clock_after"}:
            measured._public_clock_after(
                state["barrier"],
                native.read(
                    f"event-{event['index']:04d}.raw", measured.runtime.MAX_RAW
                ),
            )
    if state.get("account_binding") is not None:
        account.scan_issued_postpublication_public_v2(
            state["account_binding"], native, events, key=_ACCOUNT_KEY_V2
        )
    original, _, issued, final = _check(state)
    if (
        proof["account_identity"]["uid"] != issued.scope.account_id
        or proof["rest_origin"] != issued.plan.origin
        or context.market.instrument_id != original["computed"].intent.instrument_id
        or state["public_session"]._checked_read_session(state["public_session"]._plan)
        is not issued.read_session
    ):
        _deny("native_r7_public_identity_changed")
    state["deadline"] = min(state["deadline"], state["public_state"]["expires_at"])
    if utc_from_ns(final["utc_ns"]) >= state["deadline"]:
        _deny("native_r7_public_expired")
    return packet, context


def _register_postpublication_public_scan_v2(state):
    """Bind genuine fresh account generation before its first public scanner.

    No generation, token tuple or success flag is accepted from the caller.
    The native replay authenticates the one actual moved fresh account owner;
    the existing V4 replay authenticates the held public capture independently.
    """
    _native_guard.check_saved_native_guard_v1(state)
    if (
        state.get("closed", False)
        or state.get("public_scan_binding") is not None
        or state["task"] is not asyncio.current_task()
    ):
        _deny("native_r7_public_scan_registration_not_owned")
    issued = account._replay_issued_account_for_r7_v2(
        state["account_binding"], key=_ACCOUNT_KEY_V2
    )
    _original, _replay, previous, _stamp = _check(state)
    if (
        issued.scope != previous.scope
        or issued.control_expectation != previous.control_expectation
        or issued.issuer_sha256 == previous.issuer_sha256
        or issued.issued_row_sha256 == previous.issued_row_sha256
        or issued.source_generation is None
    ):
        _deny("native_r7_public_scan_fresh_account_required")
    # This replay has no call to our account/public scanner and cannot recurse.
    market._replay_native_postpublication_v2(
        state["public_state"], key=_PUBLIC_SOURCE_KEY_V2
    )
    binding = object()
    state["public_scan_binding"] = binding
    state["public_scan_account_binding"] = state["account_binding"]
    state["public_scan_source_generation"] = issued.source_generation
    state["public_scan_source_pin"] = (
        state["public_state"],
        state["public_state"]["v4_route_claim"],
        state["public_state"]["bundle_sha256"],
    )
    _PUBLIC_SCAN_HOLDS[binding] = state


def _postpublication_public_scan_tokens_v2(source_generation, *, key):
    """Fixed native scanner purpose; terminal signatures never reach consumers.

    This accesses the in-flight hold before `_evaluate` and before the returned
    R7 owner exists. It never calls `_public`, `_fresh_account` or a scanner.
    """
    from app.trade_qualification import (
        native_controlled_account_source_v6 as native_source,
    )

    if key is not native_source._SCAN_KEY_V6:
        _deny("native_r7_private_public_scanner_required")
    matches = [
        state
        for state in _PUBLIC_SCAN_HOLDS.values()
        if state.get("public_scan_source_generation") is source_generation
        and not state.get("closed", False)
        and state.get("task") is asyncio.current_task()
    ]
    if len(matches) != 1:
        _deny("native_r7_exact_public_scan_owner_required")
    state = matches[0]
    _native_guard.check_saved_native_guard_v1(state)
    pin = state["public_scan_source_pin"]
    if (
        _PUBLIC_SCAN_HOLDS.get(state["public_scan_binding"]) is not state
        or state["account_binding"] is not state["public_scan_account_binding"]
        or state["public_state"] is not pin[0]
        or state["public_state"]["v4_route_claim"] is not pin[1]
        or state["public_state"]["bundle_sha256"] != pin[2]
    ):
        _deny("native_r7_public_scan_binding_changed")
    # Source/route expiry, actual custody/raw bytes and task are rechecked by
    # the original V4 purpose. Tokens come from its actual prepared route.
    market._replay_native_postpublication_v2(
        state["public_state"], key=_PUBLIC_SOURCE_KEY_V2
    )
    now = native_stamp()
    measured._after(state["barrier"], now)
    if utc_from_ns(now["utc_ns"]) >= state["deadline"]:
        _deny("native_r7_public_scan_original_expired")
    route = market.operational._owned_route_state_v4(
        state["public_state"]["v4_route_claim"],
        invocation=state["public_state"]["invocation"],
        owner_task=state["task"],
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
        _deny("native_r7_actual_public_route_scan_tokens_required")
    return tokens


def _scan_original_with_fresh_terminal_tokens(state, original):
    public = original["prefix"]["frame"]["market_state"]
    directory, _, _, events = market.replay_runtime_bundle_v4(
        public["bundle_raw"], expected_sha256=public["bundle_sha256"]
    )
    account.scan_issued_postpublication_public_v2(
        state["account_binding"], directory, events, key=_ACCOUNT_KEY_V2
    )
    account.scan_issued_metadata_v1(
        state["account_binding"],
        state["published"]["facts"].original.candidate_json,
        key=_ACCOUNT_KEY_V2,
    )
    # _check() re-reads the actual G12 files and compares these exact bytes.
    # Later known private signatures cannot be hidden in an earlier artifact.
    for filename, raw_artifact in state["published"]["packet"]:
        account.scan_issued_artifact_v1(
            state["account_binding"], filename, raw_artifact, key=_ACCOUNT_KEY_V2
        )


def _fresh_account(state):
    binding = state["account_binding"]
    issued = account._replay_issued_account_for_r7_v2(binding, key=_ACCOUNT_KEY_V2)
    account.require_issued_postpublication_barrier_v2(
        binding, state["barrier"], key=_ACCOUNT_KEY_V2
    )
    _original, _, previous, stamp = _check(state)
    if (
        issued.scope != previous.scope
        or issued.issuer_sha256 == previous.issuer_sha256
        or issued.issued_row_sha256 == previous.issued_row_sha256
        or issued.control_expectation != previous.control_expectation
        or issued.policy_sha256 != previous.policy_sha256
        or issued.session_factory is not previous.session_factory
        or issued.private_client is not previous.private_client
        or issued.plan.session_binding_id != previous.plan.session_binding_id
    ):
        _deny("native_r7_fresh_account_lineage_changed")
    state["deadline"] = min(state["deadline"], issued.expires_at)
    if (
        utc_from_ns(stamp["utc_ns"]) >= state["deadline"]
        or stamp["monotonic_ns"] >= issued.expiry_monotonic_ns
    ):
        _deny("native_r7_fresh_account_expired")
    return issued


def _prefix_policy(original):
    # Exact existing V6 assumptions, checked against their captured original
    # policy digest. This cannot select a new or looser terminal profile.
    policy = local.QualificationPrefixPolicy(
        policy_id=(
            "ctcc-native-original-base-g1-g4-inspection-v6"
            if original["computed"].intent.strategy in local._BASE_STRATEGIES
            else "ctcc-native-owned-original-family-g1-g4-v2"
        ),
        data=local.fixed_native_original_g1_policy(),
        minimum_score=85,
        tick_size=original["tick_size"],
        max_allowed_drift_bps=local.Decimal(30),
        maximum_strategy_spread_bps=local.Decimal(3),
        maximum_adverse_funding_bps=local.Decimal(5),
    )
    expected = decode(original["computed"].receipt_json)["prefix_policy_sha256"]
    if sha(canonical(policy.model_dump(mode="json", round_trip=True))) != expected:
        _deny("native_r7_original_prefix_policy_changed")
    return policy


def _denial_quote(ticker):
    return {
        "bid": str(ticker.bid),
        "ask": str(ticker.ask),
        "source_generated_at": ticker.source_generated_at.isoformat(),
        "request_started_at": ticker.request_started_at.isoformat(),
        "body_completed_at": ticker.body_completed_at.isoformat(),
        "raw_body_sha256": ticker.raw_body_sha256,
    }


def _denial_scenario(scenario):
    net = scenario.measured("net_rr")
    cost = scenario.measured("cost_per_base")
    return {
        "entry": str(scenario.entry),
        "code": scenario.code,
        "net_rr": None if net is None else str(net),
        "cost_per_base": None if cost is None else str(cost),
    }


def _retain_terminal_denial(
    state,
    *,
    code,
    g1,
    packet,
    issued,
    at,
    context=None,
    costs_policy=None,
    scenarios=None,
):
    """Retain a diagnostic only after the owned invocation has decided to deny."""
    if (
        state.get("closed", False)
        or not state.get("started", False)
        or asyncio.current_task() is not state["task"]
    ):
        return
    try:
        _native_guard.check_saved_native_guard_v1(state)
        published = state["published"]
        publication._require_actual_publication(published)
        facts = published["facts"]
        fixed = facts.original
        baseline = facts.snapshot.quote.ticker
        current = None if context is None else context.quote.ticker
        direction = fixed.result.direction
        costs = None
        if scenarios is not None:
            baseline_reference = baseline.ask if direction == "long" else baseline.bid
            comparison = economics._scenario(
                entry=baseline_reference,
                stop=fixed.result.stop_loss,
                target=fixed.result.take_profit,
                direction=direction,
                quote=context.quote,
                policy=costs_policy,
            )
            candidate, executable = scenarios
            adverse = (
                executable.entry > baseline_reference
                if direction == "long"
                else executable.entry < baseline_reference
            )
            costs = {
                "candidate": _denial_scenario(candidate),
                "executable": _denial_scenario(executable),
                "baseline_reference_with_current_costs": _denial_scenario(comparison),
                "minimum_net_rr": str(costs_policy.minimum_net_rr),
                "adverse_executable_move": adverse,
                "adverse_reference_alone_sufficient_for_net_rr_failure": (
                    adverse
                    and candidate.math_checks_passed
                    and comparison.math_checks_passed
                    and executable.code == "net_rr_below_minimum"
                ),
            }
        invocation_id = facts.claim.invocation_id
        if (
            type(invocation_id) is not str
            or len(invocation_id) != 32
            or any(char not in "0123456789abcdef" for char in invocation_id)
        ):
            return
        raw = canonical(
            {
                "schema_version": "ctcc.native_r7_terminal_denial.v1",
                "invocation_id": invocation_id,
                "candidate_sha256": fixed.candidate_sha256,
                "event_key_sha256": sha(fixed.original_event_key.encode()),
                "policy_sha256": fixed.original_policy_sha256,
                "g12_evaluation_sha256": facts.evaluation_sha256,
                "g12_report_sha256": facts.receipt.report_sha256,
                "g12_custody_publication_sha256": facts.publication.publication_sha256,
                "evidence_prepared_at": facts.snapshot.prepared_at.isoformat(),
                "publication_completed_at": facts.receipt.completed_at.isoformat(),
                "post_readback_barrier": state["barrier"],
                "evaluated_at": at.isoformat(),
                "earliest_deadline": state["deadline"].isoformat(),
                "public_bundle_sha256": state["public_state"]["bundle_sha256"],
                "public_packet_sha256": packet.bundle_sha256,
                "account_issuer_sha256": issued.issuer_sha256,
                "account_issued_row_sha256": issued.issued_row_sha256,
                "current_g1_sha256": g1.evaluation_sha256,
                "current_g1_code": g1.gate.code,
                "current_g1_policy_sha256": g1.policy_sha256,
                "direction": direction,
                "entry": str(fixed.result.candidate_entry),
                "stop_loss": str(fixed.result.stop_loss),
                "take_profit": str(fixed.result.take_profit),
                "original_ticker": _denial_quote(baseline),
                "current_ticker": None if current is None else _denial_quote(current),
                "denial_code": code,
                "category": (
                    "economics" if scenarios is not None else "data_freshness"
                ),
                "quote_age_evidence": None
                if costs_policy is None
                else {
                    "maximum_seconds": costs_policy.maximum_quote_age_seconds,
                    "mark_source_at": context.quote.mark.exchange_data_return_at.isoformat(),
                    "funding_source_at": context.quote.funding.exchange_data_return_at.isoformat(),
                    "capture_completed_at": context.quote.capture_completed_at.isoformat(),
                },
                "costs": costs,
                "movement_during_render_proven": False,
                "admission": "DENY",
                "execution_authority": False,
            }
        )
        if not 0 < len(raw) <= _DENIAL_MAX_BYTES:
            return
        account.scan_issued_metadata_v1(
            state["account_binding"], raw, key=_ACCOUNT_KEY_V2
        )
        _publish_denial_packet(
            published["root"],
            facts,
            invocation_id,
            raw,
            barrier=state["barrier"],
            binding=state["account_binding"],
        )
    except Exception:  # noqa: BLE001 -- a missing receipt cannot repair or mask denial.
        return


def _publish_denial_packet(root, facts, invocation_id, raw, *, barrier, binding):
    """Use the existing native no-clobber writer, outside the six G12 files."""
    native = publication.custody.public_source_runtime
    directory_name = f"r7-denial-{invocation_id}"
    expected = facts.claim.values["evidence_root_sha256"]
    with native._native_recheck_root(root) as parent:
        identity = native._native_recheck_root_identity(root)
        if hasattr(parent, "fd"):
            held = os.fstat(parent.fd)
            if identity != (held.st_dev, held.st_ino):
                return
        if sha(canonical([str(root), *identity])) != expected or any(
            name.casefold() == directory_name for name in parent.names()
        ):
            return
        parent.mkdir(directory_name)
        with parent.child(directory_name) as directory:
            child_identity = native._native_recheck_root_identity(directory.path)
            if hasattr(directory, "fd"):
                held = os.fstat(directory.fd)
                if child_identity != (held.st_dev, held.st_ino):
                    return
            directory.publish("decision.json", raw)
            if directory.read("decision.json", _DENIAL_MAX_BYTES) != raw:
                return
            if native._native_recheck_root_identity(root) != identity:
                return
            retained = native_stamp()
            measured._after(barrier, retained)
            receipt = canonical(
                {
                    "schema_version": "ctcc.native_r7_terminal_denial_readback.v1",
                    "decision_sha256": sha(raw),
                    "decision_readback_completed": retained,
                    "g12_custody_publication_sha256": facts.publication.publication_sha256,
                    "execution_authority": False,
                }
            )
            account.scan_issued_metadata_v1(binding, receipt, key=_ACCOUNT_KEY_V2)
            directory.publish("readback.json", receipt)
        # Open a separate child handle for verification. A late failure leaves
        # immutable bytes in place and does not create execution authority.
        with parent.child(directory_name) as readback:
            if (
                native._native_recheck_root_identity(readback.path) != child_identity
                or set(readback.names()) != {"decision.json", "readback.json"}
                or readback.read("decision.json", _DENIAL_MAX_BYTES) != raw
                or readback.read("readback.json", _DENIAL_MAX_BYTES) != receipt
                or native._native_recheck_root_identity(root) != identity
            ):
                raise ValueError("native_r7_denial_readback_failed")


def _evaluate(state, *, active=(), observed_at=None, retain_denial=False):
    """Same math runs again under the existing UID/control/scope transaction."""
    original, _, previous, stamp = _check(state)
    issued = _fresh_account(state)
    _scan_original_with_fresh_terminal_tokens(state, original)
    packet, _ = _public(state)
    at = utc_from_ns(stamp["utc_ns"]) if observed_at is None else observed_at
    if not state["published"]["facts"].receipt.completed_at < at < state["deadline"]:
        _deny("native_r7_cutoff_outside_original_window")
    policy = _prefix_policy(original)
    g1 = data_v2.evaluate_public_market_data_v2(
        packet,
        expected_bundle_sha256=packet.bundle_sha256,
        policy=policy.data,
        evaluated_at=at,
    )
    g1 = data_v2.verify_public_market_data_v2(
        g1,
        packet,
        expected_bundle_sha256=packet.bundle_sha256,
        policy=policy.data,
        evaluated_at=at,
    )
    if not g1.passed:
        if retain_denial and g1.gate.code == "stale_market_data":
            _retain_terminal_denial(
                state,
                code="native_r7_current_g1_rejected",
                g1=g1,
                packet=packet,
                issued=issued,
                at=at,
            )
        _deny("native_r7_current_g1_rejected")
    context = market.public_market_context_v2(
        packet, expected_bundle_sha256=packet.bundle_sha256, evaluated_at=at
    )
    raw = decode(g1.source_json.encode(), data.MAX_SOURCE_BYTES)
    current = MarketSnapshot.model_validate_json(json.dumps(raw["market"]), strict=True)
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(raw["analysis"]), strict=True
    )
    intent, fixed = original["computed"].intent, state["published"]["facts"].original
    if (
        decode(fixed.candidate_json)["event_path_policy_sha256"]
        != _native_path.POLICY_SHA256
    ):
        _deny("native_r7_original_event_policy_changed")
    checks = []

    def gate(kind, code, reason, measured_values):
        checks.append((kind.value, code))
        return code == "passed"

    values = {
        "report_id": intent.report_id,
        "symbol": current.symbol,
        "strategy": intent.strategy,
        "direction": intent.direction,
        "evaluated_at": at,
        "candidate_entry": intent.candidate_entry,
        "raw_score": 0,
        "effective_score": 0,
    }
    if intent.strategy in local._BASE_STRATEGIES:
        local._evaluate_base_g2_g4(
            current,
            analysis,
            intent=intent,
            policy=policy,
            source_sha256=g1.source_sha256,
            quote_bundle_sha256=g1.quote_bundle_sha256,
            bid=context.quote.ticker.bid,
            ask=context.quote.ticker.ask,
            funding_rate=context.quote.funding.forecast.rate,
            values=values,
            gate=gate,
        )
    else:
        local._owned_conditions_v2._evaluate_owned_history_g2_g4_v2(
            current,
            analysis,
            intent=intent,
            policy=policy,
            source_sha256=g1.source_sha256,
            quote_bundle_sha256=g1.quote_bundle_sha256,
            bid=context.quote.ticker.bid,
            ask=context.quote.ticker.ask,
            funding_rate=context.quote.funding.forecast.rate,
            values=values,
            gate=gate,
            detection=original["computed"].detection,
            original_source_sha256=original["computed"].g1.source_sha256,
            original_history_json=original["computed"].family_history_json,
        )
    if len(checks) != 3 or any(code != "passed" for _, code in checks):
        _deny("native_r7_current_g2_g4_rejected")
    captured_minimum_score = decode(original["policy_raw"])["minimum_score"]
    if values["raw_score"] < captured_minimum_score:
        _deny("native_r7_current_score_below_original_configured_minimum")
    joined = join_confirmed_original_path_v1(
        original["market"], current, detection=original["computed"].detection
    )
    path = evaluate_fixed_original_event_path_v1(
        original["market"],
        original["analysis"],
        joined,
        detection=original["computed"].detection,
        observed_at=at,
    )
    if not path.required_event_path_complete:
        _deny("native_r7_original_event_path_rejected")
    timing_policy = local.TimingPolicy.model_validate_json(
        json.dumps(decode(original["policy_raw"])["timing"][intent.strategy]),
        strict=True,
    )
    reference = (
        context.quote.ticker.ask
        if intent.direction == "long"
        else context.quote.ticker.bid
    )
    timing = local.evaluate_timing(
        original["computed"].detection,
        current_time=at,
        candidate_created_at=intent.created_at,
        candidate_expires_at=intent.expires_at,
        reference_price=reference,
        consumed_event_keys=original["local_journal"].consumed_event_keys,
        policy=timing_policy,
    )
    location = local._location_local_quote_v13(
        original["computed"], context, observed_at=at
    )
    if not timing.timing_valid or not location.passed:
        _deny("native_r7_original_timing_or_zone_rejected")
    costs_policy = local.EconomicsPolicy.model_validate_json(
        json.dumps(decode(original["policy_raw"])["economics"]), strict=True
    )
    if not economics.quote_within_economics_policy_age_v2(packet, costs_policy, at):
        if retain_denial:
            _retain_terminal_denial(
                state,
                code="native_r7_original_cost_quote_stale",
                g1=g1,
                packet=packet,
                issued=issued,
                at=at,
                context=context,
                costs_policy=costs_policy,
            )
        _deny("native_r7_original_cost_quote_stale")
    result = fixed.result
    current_fixed = _owned_fixed_v2.evaluate_owned_fixed_protection_v2(
        original["market"],
        original["analysis"],
        current,
        analysis,
        detection=original["computed"].detection,
        original_selection_audit_json=original["selection_raw"],
        stop_loss=result.stop_loss,
        take_profit=result.take_profit,
        entry=result.candidate_entry,
        policy=original["protection"],
        tick_size=original["tick_size"],
        data_policy=policy.data,
        quote=context.quote,
        observed_at=at,
    )
    if current_fixed.passed is not True:
        _deny("native_r7_current_fixed_original_protection_rejected")
    selected_execution = _execution_mode.select_captured_isolated_execution_v1(
        issued.execution_mode,
        instrument_id=intent.instrument_id,
        direction=intent.direction,
        requested_leverage=fixed.requested_leverage,
    )
    original_execution = _execution_mode.select_captured_isolated_execution_v1(
        previous.execution_mode,
        instrument_id=intent.instrument_id,
        direction=intent.direction,
        requested_leverage=fixed.requested_leverage,
    )
    if (
        canonical(decode(fixed.candidate_json)["execution_mode"])
        != original_execution.receipt_json
        or selected_execution.position_mode != original_execution.position_mode
        or selected_execution.position_side != original_execution.position_side
        or selected_execution.leverage != original_execution.leverage
    ):
        _deny("native_r7_original_execution_mode_or_leverage_changed")
    scenarios = tuple(
        economics._scenario(
            entry=entry,
            stop=result.stop_loss,
            target=result.take_profit,
            direction=intent.direction,
            quote=context.quote,
            policy=costs_policy,
        )
        for entry in (result.candidate_entry, reference)
    )
    if not all(item.math_checks_passed for item in scenarios):
        if retain_denial:
            _retain_terminal_denial(
                state,
                code="native_r7_fixed_economics_rejected",
                g1=g1,
                packet=packet,
                issued=issued,
                at=at,
                context=context,
                costs_policy=costs_policy,
                scenarios=scenarios,
            )
        _deny("native_r7_fixed_economics_rejected")
    specs = [
        item
        for item in issued.instrument_specs
        if item.instrument_id == intent.instrument_id
    ]
    old_specs = [
        item
        for item in previous.instrument_specs
        if item.instrument_id == intent.instrument_id
    ]
    if len(specs) != 1 or len(old_specs) != 1:
        _deny("native_r7_original_instrument_missing")
    spec, old_spec = specs[0], old_specs[0]
    if any(
        getattr(spec, name) != getattr(old_spec, name)
        for name in type(spec).model_fields
        if name not in {"source_sha256", "observed_at", "received_at"}
    ):
        _deny("native_r7_original_contract_or_classification_changed")
    original_dynamic = decode(fixed.candidate_json)
    dynamic_risks = tuple(
        _dynamic_risk.evaluate_actor_native_dynamic_risk_v1(
            original,
            issued,
            result=result,
            analysis=analysis,
            entry=scenario.entry,
            cost_per_base=scenario.measured("cost_per_base"),
            raw_score=min(result.raw_score, values["raw_score"]),
            effective_score=min(
                result.effective_score,
                values["effective_score"],
                original_dynamic["dynamic_risk"]["risk_score"],
            ),
            contracts=fixed.requested_contracts,
            leverage_ceiling=original_dynamic["caller_leverage_ceiling"],
            expires_at=state["deadline"],
            key=_DYNAMIC_RISK_KEY_V2,
        )
        for scenario in scenarios
    )
    if any(
        item.selected_leverage != fixed.requested_leverage for item in dynamic_risks
    ):
        _deny("native_r7_fixed_original_dynamic_leverage_changed")
    risks = tuple(
        account.evaluate_issued_g11_v1(
            state["account_binding"],
            report_id=intent.report_id,
            instrument_id=intent.instrument_id,
            direction=intent.direction,
            entry=scenario.entry,
            stop=result.stop_loss,
            cost=scenario.measured("cost_per_base"),
            contracts=fixed.requested_contracts,
            leverage=fixed.requested_leverage,
            evaluated_at=at,
            key=_ACCOUNT_KEY_V2,
            active_reservations=active,
        )
        for scenario in scenarios
    )
    if not all(item.passed for item in risks):
        _deny("native_r7_current_finite_account_caps_rejected")
    operands = tuple(
        ScenarioOperands(
            entry=scenario.entry,
            stop_loss=result.stop_loss,
            cost_per_base=scenario.measured("cost_per_base"),
            contracts=fixed.requested_contracts,
            contract_value=spec.contract_value,
            leverage=fixed.requested_leverage,
        )
        for scenario in scenarios
    )
    coverage = RiskCoverage(
        candidate=operands[0], execution=operands[1], **exact_coverage(*operands)
    )
    proof = canonical(
        {
            "schema_version": "ctcc.owned_native_r7_computation.v2",
            "original_candidate_sha256": fixed.candidate_sha256,
            "original_event_key": fixed.original_event_key,
            "original_policy_sha256": fixed.original_policy_sha256,
            "g12_sha256": state["published"]["facts"].evaluation_sha256,
            "g12_custody_sessions_sha256": state["published"][
                "facts"
            ].custody_session_sha256,
            "publication_completed_at": state["published"][
                "facts"
            ].receipt.completed_at.isoformat(),
            "post_readback_barrier": state["barrier"],
            "public_bundle_sha256": state["public_state"]["bundle_sha256"],
            "public_capture_report_id": context.quote.report_id,
            "public_packet_sha256": packet.bundle_sha256,
            "new_issuer_sha256": issued.issuer_sha256,
            "new_issued_row_sha256": issued.issued_row_sha256,
            "new_account_revision": issued.account_revision,
            "new_ledger_revision": issued.ledger_revision,
            "execution_mode": decode(selected_execution.receipt_json),
            "execution_selection_sha256": selected_execution.receipt_sha256,
            "g1_sha256": g1.evaluation_sha256,
            "current_conditions": checks,
            "current_fixed_protection_json": canonical(
                current_fixed.model_dump(mode="json", round_trip=True)
            ).decode(),
            "current_fixed_protection_sha256": current_fixed.evaluation_sha256,
            **(
                {
                    "family_policy_sha256": original[
                        "computed"
                    ].result.family_policy_sha256,
                    "original_history_record_sha256": sha(
                        original["computed"].family_history_json
                    ),
                }
                if intent.strategy not in local._BASE_STRATEGIES
                else {}
            ),
            "captured_minimum_score": captured_minimum_score,
            "current_raw_score": values["raw_score"],
            "current_effective_score": values["effective_score"],
            "original_dynamic_risk_sha256": original_dynamic["dynamic_risk_sha256"],
            "dynamic_risks": [decode(item.receipt_json) for item in dynamic_risks],
            "dynamic_risk_sha256s": [item.receipt_sha256 for item in dynamic_risks],
            "original_event_path_sha256": path.evaluation_sha256,
            "event_path_policy_sha256": path.policy_sha256,
            "required_event_path_complete": path.required_event_path_complete,
            "continuous_tick_path_claimed": False,
            "original_zone_sha256": sha(
                canonical(result.entry_zone.model_dump(mode="json"))
            ),
            "entry": str(result.candidate_entry),
            "stop_loss": str(result.stop_loss),
            "take_profit": str(result.take_profit),
            "reference": str(reference),
            "coverage": coverage.model_dump(mode="json", round_trip=True),
            "observed_at": at.isoformat(),
            "expires_at": state["deadline"].isoformat(),
            "execution_authority": False,
        }
    )
    _check(state)
    _fresh_account(state)
    return issued, spec, packet, context, coverage, risks, proof


class _Attempt(Coroutine):
    def __init__(self, state):
        self.state, self.body = state, _capture(state)

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
            if not self.state.get("issued", False):
                _release(self.state)
            raise

    def throw(self, *args):
        try:
            return self.body.throw(*args)
        except BaseException:
            if not self.state.get("issued", False):
                _release(self.state)
            raise

    def close(self):
        try:
            self.body.close()
        finally:
            if not self.state.get("issued", False):
                _release(self.state)


def capture_recheck_owned_native_v2(owner, *, roots):
    if (
        type(roots) is not NativePostpublicationRootsV2
        or type(roots.account_roots) is not account.PostpublicationAccountRootsV1
    ):
        _deny("native_r7_exact_roots_required")
    all_roots = (
        roots.public_root,
        roots.public_account_proof_root,
        roots.account_roots.history_raw_root,
        roots.account_roots.history_companion_root,
        roots.account_roots.account_proof_root,
        roots.account_roots.account_native_root,
    )
    if (
        any(
            type(root) is not type(Path()) or not root.is_absolute()
            for root in all_roots
        )
        or len(set(all_roots)) != 6
        or any(a in b.parents for a in all_roots for b in all_roots if a != b)
    ):
        _deny("native_r7_roots_must_be_fresh_and_disjoint")
    published = publication._take_native_published_for_recheck_v2(
        owner, key=_PUBLICATION_KEY_V2
    )
    state = {
        "published": published,
        "roots": roots,
        "task": asyncio.current_task(),
        "barrier": decode(published["facts"].barrier_json),
        "last": native_stamp(),
        "deadline": published["facts"].original.expires_at,
    }
    _native_guard.save_native_guard_v1(state, _NATIVE_GUARD)
    try:
        if any(
            root == published["root"]
            or root in published["root"].parents
            or published["root"] in root.parents
            for root in all_roots
        ):
            _deny("native_r7_roots_overlap_g12")
        _check(state)
        return _Attempt(state)
    except BaseException:
        _release(state)
        raise


async def _capture(state):
    if state.get("started", False):
        _deny("native_r7_attempt_spent")
    state["started"] = True
    try:
        original, _, issued, stamp = _check(state)
        remaining = (state["deadline"] - utc_from_ns(stamp["utc_ns"])).total_seconds()
        async with asyncio.timeout(remaining):
            document = issued.plan.model_dump(mode="python", round_trip=True)
            document.update(
                plan_id="r7-" + uuid4().hex, created_at=utc_from_ns(stamp["utc_ns"])
            )
            plan = capture.CurrentDemoAccountCapturePlanV7.model_validate(
                document, strict=True
            )
            state["public_session"] = (
                ControlledDemoAccountSession.from_pinned_read_session(
                    read_session=issued.read_session,
                    plan=plan,
                    expected_plan_sha256=capture.plan_sha256(plan),
                )
            )
            state[
                "pending_public"
            ] = await market.acquire_initial_demo_market_source_v4(
                state["roots"].public_root,
                account_proof_root=state["roots"].public_account_proof_root,
                instrument_id=original["computed"].intent.instrument_id,
                market_policy=original["prefix"]["frame"]["policy"],
                account_session=state["public_session"],
                private_client=issued.private_client,
                custody_repository=original["prefix"]["frame"]["repository"],
            )
            _check(state)
            state["public_state"] = market._take_native_postpublication_v2(
                state.pop("pending_public"), key=_PUBLIC_SOURCE_KEY_V2
            )
            _public(state)
            state[
                "pending_account"
            ] = await account.acquire_postpublication_issued_account_v1(
                state["published"]["candidate"]["account_binding"],
                barrier=state["barrier"],
                roots=state["roots"].account_roots,
                key=_ACCOUNT_KEY_V2,
            )
            _check(state)
            state["account_binding"] = account._take_issued_account_for_r7_v2(
                state.pop("pending_account"), key=_ACCOUNT_KEY_V2
            )
            _register_postpublication_public_scan_v2(state)
            evaluated = _evaluate(state, retain_denial=True)
            state["prepared_proof"] = evaluated[-1]
            state["prepared_proof_sha256"] = sha(evaluated[-1])
            owner = object.__new__(OwnedNativePostpublicationR7V2)
            _OWNERS[owner] = state
            state["finalizer"] = finalize(owner, _release, state)
            state["task"].add_done_callback(lambda task: _release(state))
            state["issued"] = True
            return owner
    except asyncio.CancelledError:
        raise
    except NativeR7Error:
        raise
    except Exception:  # noqa: BLE001 -- preserve actual ownership and fail closed on unknown/cancellation.
        _deny("native_r7_capture_or_recheck_failed")
    finally:
        if not state.get("issued", False):
            _release(state)


def _take_native_r7_for_ledger_v2(owner, *, key):
    from app.database.repositories import qualification_ledger as ledger

    if key is not ledger._NATIVE_V2_KEY:
        _deny("native_r7_ledger_fixed_purpose_required")
    state = (
        _OWNERS.pop(owner, None)
        if type(owner) is OwnedNativePostpublicationR7V2
        else None
    )
    if state is None:
        _deny()
    state["finalizer"].detach()
    try:
        if sha(state["prepared_proof"]) != state["prepared_proof_sha256"]:
            _deny("native_r7_prepared_proof_changed")
        _evaluate(state)
        return state
    except BaseException:
        _release(state)
        raise


_NATIVE_GUARD = _native_guard.install_native_guards_v1(
    _native_sys.modules[__name__],
    ("capture_recheck_owned_native_v2",),
    dependencies=(
        publication,
        publication.candidate,
        account,
        market,
        measured,
        data,
        data_v2,
        economics,
        local,
        local._local_bridge_v2,
        local._local_location_v13,
        local._local_timing_v13,
        local._owned_conditions_v2,
        local._owned_conditions_v2._owned_regime_v2,
        local._owned_conditions_v2._owned_range_v2,
        local._owned_conditions_v2._owned_sweep_v2,
        local._owned_conditions_v2._owned_sweep_history_v2,
        local._local_protection_v13,
        local.bracket_policy,
        _native_path,
        _native_path.continuation,
        _execution_mode,
        _dynamic_risk,
        _dynamic_risk.risk_profile,
        _dynamic_risk.structural_risk,
        _dynamic_risk.mathematics,
        _dynamic_risk.derivatives,
        _dynamic_risk.risk_config,
        _dynamic_risk.financial,
        _dynamic_risk.financial.configured,
        _dynamic_risk.financial.portfolio,
        _dynamic_risk.hard,
        _dynamic_risk.claims_codec,
        _dynamic_risk.protection,
        _dynamic_risk.candidate_models,
        _owned_fixed_v2,
        _owned_fixed_v2._owned_quote_v2,
        _owned_fixed_v2._owned_selection_v2,
        _owned_fixed_v2._owned_family_policy_v2,
    ),
    bindings=tuple(
        (namespace, name, vars(namespace)[name])
        for namespace, names in (
            (
                publication.custody.public_source_runtime,
                ("_native_recheck_root", "_native_recheck_root_identity"),
            ),
            (
                market.operational,
                (
                    "_probe_initial_demo_operational_route_retained_v4",
                    "probe_initial_demo_operational_route_v4",
                    "native_os_clock_ipc",
                ),
            ),
            (
                measured.runtime,
                (
                    "_capture_initial_public_v4",
                    "_capture_owned",
                    "_accept_clock_sample",
                    "_observe_owned_runtime_clock",
                    "_runtime_clock_pair_matches",
                    "_RUNTIME_CLOCK_IPC",
                ),
            ),
            (
                measured.public_custody.journal,
                ("replay_runtime_attempt", "_replay_semantics", "_record_clock"),
            ),
            (
                measured.public_clock,
                (
                    "_RUNTIME_CLOCK_V2",
                    "_RUNTIME_CLOCK_IPC",
                    "_native_clock",
                    "_native_observation_payload_ipc",
                    "_observe_owned_runtime_clock",
                    "_runtime_clock_pair_matches",
                    "replay_clock_observation",
                ),
            ),
            (
                measured.public_clock._native_clock,
                (
                    "native_os_clock_ipc",
                    "_capture_command",
                    "_check_host_metadata",
                    "_ipc_diagnostic",
                    "_ipc_host_payload",
                    "_validate_v3",
                    "_WindowsHostMetadataIPC",
                ),
            ),
            (
                measured.public_clock._native_clock._WindowsHostMetadataIPC,
                (
                    "__init__",
                    "_read_frames",
                    "_read_stderr",
                    "_start",
                    "_remaining",
                    "capture",
                    "finish",
                    "_finish_process",
                ),
            ),
        )
        for name in names
    ),
)
