"""Actual synthetic raw V2 owner and evaluators; no native or trading claim.

Only raw fixture construction, clock, TLS, HTTP/WS and storage are replaced.
G1, analysis, conditions, history, event detection and zone arithmetic are real.
Synchronous raw fixture constructors run before any asyncio.run event loop.
"""

import asyncio
import inspect
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Context, Decimal, Inexact, localcontext
from pathlib import Path
from types import MappingProxyType

import pytest
from pydantic import BaseModel, model_serializer

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot, SwapTickerV2
from app.public_market_source.public_market_receipts import canonical, decode, sha
from app.strategies.base import StrategyContext
from app.strategies.conditions import assess_conditions
from app.strategies.regime import route_regime
from app.trade_qualification import account_capture as account
from app.trade_qualification import data, data_v2
from app.trade_qualification import original_candidate_precursor_v2 as module
from app.trade_qualification import public_market_collector as legacy
from app.trade_qualification import public_market_collector_v2 as public
from app.trade_qualification.events import extract_trigger
from app.trade_qualification.location import build_entry_zone
from app.trade_qualification.service import QualificationIntent
from app.trade_qualification.timing import TIMING_POLICIES
from tests.unit import qualification_prefix_fixtures as prefix
from tests.unit import test_original_candidate_policy as base_fixture
from tests.unit.history_qualification_fixtures import history_source
from tests.unit.qualification_range_v5_fixtures import range_v5_source
from tests.unit.qualification_sweep_v6_fixtures import sweep_v6_source
from tests.unit.research.test_owned_public_runtime_v2 import (
    empty_registries,
    invoke,
    setup,
)
from tests.unit.test_qualification_events import OBSERVED
from tests.unit.test_qualification_market_bridge import v2_engine_source

D = Decimal
NOW = base_fixture.NOW
HISTORY_CASES = tuple(
    (strategy, direction)
    for strategy in (
        "structure_reversal",
        "volatility_expansion",
        "range_reversal",
        "liquidity_sweep_reversal",
    )
    for direction in ("long", "short")
)


def v2_market(market):
    values = market.ticker.model_dump()
    if type(market.ticker) is not SwapTickerV2:
        contracts = values.pop("volume_24h")
        values.pop("volume_quote_24h")
        market.ticker = SwapTickerV2(
            **values,
            schema_version="okx-swap-ticker-v2",
            volume_contracts_24h=contracts,
            volume_currency_24h=D("123.4567"),
        )
    return market


def history_raw_source(strategy, direction, *, sweep_scenario="inside"):
    if strategy == "range_reversal":
        source = range_v5_source(direction)
    elif strategy == "liquidity_sweep_reversal":
        source = sweep_v6_source(direction, sweep_scenario)
    else:
        source = history_source(
            strategy,
            direction,
            bracket=True,
            expansion_entry=strategy == "volatility_expansion",
            reversal_bracket=strategy == "structure_reversal",
        )
    market = source.market.model_copy(deep=True)
    shift = NOW - OBSERVED
    assert shift.seconds == 0 and shift.microseconds == 0
    market.candles = {
        frame: [
            row.model_copy(update={"timestamp": row.timestamp + shift}) for row in rows
        ]
        for frame, rows in market.candles.items()
    }
    # This constructs a new declared synthetic raw source before capture. No
    # collected row, source timestamp, receipt or old event is rewritten.
    market.received_at = NOW
    market.ticker.timestamp += shift
    market.order_book.timestamp += shift
    if market.next_funding_time is not None:
        market.next_funding_time += shift
    return replace(
        source, market=v2_market(market), evaluated_at=NOW + timedelta(milliseconds=10)
    )


def declared_history_funding_row(source):
    """New raw scenario declared before capture; normalized None stays None."""
    original = source.quote.provenance[2]
    assert original.role == "funding"
    payload = json.loads(original.response_body)
    assert payload["code"] == "0" and len(payload["data"]) == 1
    row = payload["data"][0]

    def epoch_ms(value):
        return (value - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)

    assert row["instId"] == source.market.instrument_id
    assert row["fundingRate"] == "0"
    assert row["ts"] == str(epoch_ms(OBSERVED - timedelta(seconds=1)))
    assert row["fundingTime"] == str(epoch_ms(OBSERVED + timedelta(hours=1)))
    assert row["nextFundingTime"] == str(epoch_ms(OBSERVED + timedelta(hours=9)))
    shift = NOW - OBSERVED
    assert shift.seconds == 0 and shift.microseconds == 0
    shift_ms = shift // timedelta(milliseconds=1)
    declared = dict(row)
    for key in ("ts", "fundingTime", "nextFundingTime"):
        declared[key] = str(int(row[key]) + shift_ms)
    # Additional raw fields use the already declared owned-V2 fixture rules.
    # This is an independent synthetic input, not an old receipt rewrite.
    declared.update(
        method="current_period",
        formulaType="withRate",
        minFundingRate="-0.00375",
        maxFundingRate="0.00375",
        settFundingRate="-0.0002",
        settState="settled",
    )
    return declared


def capture_raw_source(source, root, *, declared_funding=None):
    with pytest.MonkeyPatch.context() as patch:
        _, _, harness, publications = setup(patch, source)
        funding_before = source.quote.provenance[2].response_body
        normalized_before = source.market.next_funding_time
        served_funding = []
        if declared_funding is not None:
            original_public = harness.public

            def public_response(request):
                if request.url.path.endswith("/funding-rate"):
                    # Do not enter the older harness's None.timestamp builder.
                    row = dict(declared_funding)
                    served_funding.append(row)
                    return [row]
                return original_public(request)

            harness.public = public_response
        diagnostic = asyncio.run(invoke(root))
        assert source.quote.provenance[2].response_body == funding_before
        assert source.market.next_funding_time is normalized_before
        if declared_funding is not None:
            assert served_funding == [declared_funding]
        assert diagnostic.packet is not None and diagnostic.context is not None
        harness.assert_closed()
        assert not publications
        assert all(request.method == "GET" for request in harness.requests)
        assert not any(
            "/account/" in request.url.path or "/trade/" in request.url.path
            for request in harness.requests
        )
        empty_registries()
    return diagnostic


@pytest.fixture(scope="module", params=["long", "short"])
def captured(request, tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(prefix, "CAPTURED_AT", NOW)
        source = v2_engine_source(request.param)
    source = replace(source, evaluated_at=NOW + timedelta(milliseconds=10))
    diagnostic = capture_raw_source(source, tmp_path_factory.mktemp("precursor-v2"))
    return request.param, diagnostic, base_fixture.account_source()


@pytest.fixture(scope="module", params=HISTORY_CASES, ids=lambda case: "-".join(case))
def history_captured(request, tmp_path_factory):
    strategy, direction = request.param
    source = history_raw_source(strategy, direction)
    declared_funding = declared_history_funding_row(source)
    diagnostic = capture_raw_source(
        source, tmp_path_factory.mktemp("history-v2"), declared_funding=declared_funding
    )
    return strategy, direction, diagnostic, base_fixture.account_source()


def inputs(diagnostic, packet, *, strategy="fvg_return", **changes):
    frozen = account.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    return {
        "strategy": strategy,
        "expected_public_bundle_sha256": diagnostic.packet.bundle_sha256,
        "expected_account_plan_sha256": packet.plan_sha256,
        "expected_account_packet_sha256": frozen.sha256,
        "data_policy": prefix.DATA_POLICY,
        "expected_data_policy_sha256": data._sha(data._canonical(prefix.DATA_POLICY)),
        "created_at": max(diagnostic.context.evaluated_at, packet.completed_at)
        + timedelta(microseconds=1),
        "service_deadline": NOW + timedelta(hours=1),
        **changes,
    }


def derive(diagnostic, packet, **changes):
    return module.derive_original_candidate_precursor_v2(
        diagnostic.packet, packet, **inputs(diagnostic, packet, **changes)
    )


def verify(result, diagnostic, packet, **changes):
    return module.verify_original_candidate_precursor_v2(
        result, diagnostic.packet, packet, **inputs(diagnostic, packet, **changes)
    )


@pytest.fixture(scope="module")
def passing(captured):
    _, diagnostic, packet = captured
    result = derive(diagnostic, packet)
    assert result.intent is not None, json.loads(result.receipt_json)["code"]
    return result


def assert_closed_diagnostic(result):
    value = json.loads(result.receipt_json)
    assert value["admission"] == "DENY"
    assert value["action"] in {"WAIT", "CANCEL", "NO_TRADE"}
    assert all(
        value[name] is False
        for name in (
            "native_clock_verified",
            "metadata_current_owned",
            "account_complete",
            "source_authenticity_verified",
            "original_source_verified",
            "pre_evidence_complete",
            "qualification_performed",
            "execution_recheck_performed",
            "atomic_risk_reserved",
            "execution_authority",
            "historical_first_availability_verified",
            "predictive_point_in_time_verified",
            "sweep_stage_C_integrated",
        )
    )
    assert all(
        value[name] is None
        for name in (
            "quantity",
            "leverage",
            "stop_loss",
            "take_profit",
            "actual_rr",
            "portfolio_risk",
            "event_ledger_revision",
        )
    )
    assert (
        result.execution_authority is False and result.original_source_verified is False
    )
    assert "all_state_event_ledger" in value["unverified"]
    return value


def test_preregistered_bytes_and_closed_explicit_api():
    path = (
        Path(module.__file__).parents[2]
        / "docs"
        / "original_candidate_precursor_v2_preregistration.json"
    )
    assert path.read_bytes() == module.POLICY_BYTES
    assert (
        module.POLICY_SHA256
        == "6a2626a911e6c6497cec033472a0f5feeb7d99c41abcd967540467bc3e27d0bb"
    )
    assert (
        tuple(json.loads(module.POLICY_BYTES)["strategy_catalog"])
        == module.STRATEGY_CATALOG
    )
    forbidden = {
        "direction",
        "entry",
        "score",
        "risk_score",
        "event",
        "zone",
        "quantity",
        "leverage",
        "stop_loss",
        "take_profit",
        "passed",
        "engine_contract",
        "engine_callback",
        "context",
        "g1",
    }
    for function in (
        module.derive_original_candidate_precursor_v2,
        module.verify_original_candidate_precursor_v2,
    ):
        signature = inspect.signature(function)
        assert not forbidden.intersection(signature.parameters)
        assert all(
            parameter.kind is not inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )


def test_real_raw_g1_fixes_original_intent_and_retains_actual_funding_pair(
    captured, passing
):
    direction, diagnostic, packet = captured
    before_public = diagnostic.packet.packet_json
    before_account = account.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    value = assert_closed_diagnostic(passing)
    assert value["code"] == "precursor_derived_remaining_dependencies_unverified"
    assert value["action"] == "WAIT" and value["g1"]["passed"] is True
    assert passing.intent.direction == direction
    side = "ask" if direction == "long" else "bid"
    assert passing.intent.candidate_entry == getattr(
        diagnostic.context.quote.ticker, side
    )
    assert value["entry_quote_side"] == side
    assert value["detection"]["source_sha256"] == value["g1"]["source_sha256"]
    assert passing.intent.expires_at == NOW + timedelta(minutes=10)
    assert value["event_key"] and value["zone"] and value["event_prefix_witness"]
    pair = value["funding_pair"]
    assert sha(canonical(pair)) == value["funding_pair_sha256"]
    assert (
        pair["settlement_at"]
        == diagnostic.context.quote.funding.forecast.settlement_at.isoformat()
    )
    assert (
        pair["settlement_at"]
        != diagnostic.context.quote.funding.following_settlement_forecast_at.isoformat()
    )
    assert (
        pair["rate_generated_at"] is None
        and pair["historical_first_available_at"] is None
    )
    assert verify(passing, diagnostic, packet) == passing
    assert diagnostic.packet.packet_json == before_public
    assert (
        account.freeze_demo_account_packet(
            packet, expected_plan_sha256=packet.plan_sha256
        )
        == before_account
    )
    assert derive(diagnostic, packet) == passing


def test_raw_page_lineage_retains_order_and_unknown_past_availability(
    captured, passing
):
    _, diagnostic, packet = captured
    value = json.loads(passing.receipt_json)
    assert sha(canonical(value["timeline"])) == value["timeline_sha256"]
    checked, (_, _, _, candles, _, _) = public._parts(diagnostic.packet)
    assert checked.bundle_sha256 == value["public_bundle_sha256"]
    for frame, timeline in zip(candles.frames, value["timeline"], strict=True):
        assert timeline["frame_sha256"] == frame.frame_sha256
        expected = [
            (page.page_index, index, row)
            for page in frame.pages
            for index, row in enumerate(json.loads(page.response_body)["data"])
        ]
        assert len(expected) == len(timeline["rows_in_original_page_order"])
        for (page, index, raw), row in zip(
            expected, timeline["rows_in_original_page_order"], strict=True
        ):
            assert row["page_index"] == page and row["row_index"] == index
            assert row["raw_row_sha256"] == sha(canonical(raw))
            assert row["historical_first_available_at"] is None
    assert "account_identity" not in value
    assert "uid" not in value and "main_uid" not in value
    assert "session_binding_id" not in value
    assert value["account_plan_sha256"] == packet.plan_sha256
    assert b'"uid":' not in passing.receipt_json
    assert b'"main_uid":' not in passing.receipt_json
    assert b'"session_binding_id":' not in passing.receipt_json


def test_fixed_expiry_cannot_renew_and_shorter_deadline_changes_only_lifetime(
    captured, passing
):
    _, diagnostic, packet = captured
    later = derive(
        diagnostic,
        packet,
        created_at=passing.intent.created_at + timedelta(milliseconds=1),
    )
    shorter = derive(diagnostic, packet, service_deadline=NOW + timedelta(minutes=1))
    assert later.intent and shorter.intent
    assert later.intent.expires_at == passing.intent.expires_at
    assert shorter.intent.expires_at == NOW + timedelta(minutes=1)
    for result in (later, shorter):
        value = json.loads(result.receipt_json)
        original = json.loads(passing.receipt_json)
        assert result.intent.candidate_entry == passing.intent.candidate_entry
        assert value["event_key"] == original["event_key"]
        assert (
            value["detection"]["trigger"]["trigger_time"]
            == original["detection"]["trigger"]["trigger_time"]
        )
        assert (
            value["original_event_expires_at"] == original["original_event_expires_at"]
        )


def test_expired_service_cancels_without_resetting_event(captured):
    _, diagnostic, packet = captured
    result = derive(diagnostic, packet, service_deadline=NOW)
    value = assert_closed_diagnostic(result)
    assert result.intent is None
    assert value["code"] == "service_deadline_expired" and value["action"] == "CANCEL"


def test_raw_tick_mismatch_cancels_fixed_quote_without_rounding(captured):
    _, diagnostic, _ = captured
    packet = base_fixture.account_source("3")
    result = derive(diagnostic, packet)
    value = assert_closed_diagnostic(result)
    assert value["code"] == "initial_entry_off_tick" and value["action"] == "CANCEL"
    assert result.intent is None and value["zone"] is None


@pytest.mark.parametrize("strategy", module.STRATEGY_CATALOG)
def test_fixed_catalog_dispatch_keeps_actual_source_failures(captured, strategy):
    _, diagnostic, packet = captured
    result = derive(diagnostic, packet, strategy=strategy)
    value = assert_closed_diagnostic(result)
    assert value["strategy"] == strategy
    assert value["code"] != "selected_strategy_version_not_integrated"
    g1 = data_v2.evaluate_public_market_data_v2(
        diagnostic.packet,
        expected_bundle_sha256=diagnostic.packet.bundle_sha256,
        policy=prefix.DATA_POLICY,
        evaluated_at=inputs(diagnostic, packet)["created_at"],
    )
    source = json.loads(g1.source_json)
    market = MarketSnapshot.model_validate_json(
        json.dumps(source["market"]), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(source["analysis"]), strict=True
    )
    route = route_regime(analysis)
    assert value["route"]["regime"] == route.regime.value
    assert value["route"]["fail_codes"] == list(route.fail_codes)
    if value["conditions"] is not None:
        conditions = assess_conditions(
            StrategyContext(
                analysis,
                market,
                85,
                D(2)
                if strategy
                in {
                    "trend_pullback",
                    "breakout_continuation",
                    "fvg_return",
                    "order_block_return",
                }
                else D(1),
            ),
            strategy,
        )
        assert value["conditions"]["required_failures"] == list(
            conditions.required_failures
        )
        assert value["conditions"]["veto_failures"] == list(conditions.veto_failures)
        assert value["conditions"]["score"] == conditions.score
    assert verify(result, diagnostic, packet, strategy=strategy) == result


def test_actual_history_routes_keep_detection_and_stage_c_closed(history_captured):
    strategy, direction, diagnostic, packet = history_captured
    result = derive(diagnostic, packet, strategy=strategy)
    value = assert_closed_diagnostic(result)
    assert result.intent is not None, value["code"]
    assert result.intent.direction == direction
    assert value["event_prefix_witness"] and value["timeline"]
    assert value["detection"]["source_sha256"] == value["g1"]["source_sha256"]
    assert verify(result, diagnostic, packet, strategy=strategy) == result
    if strategy == "liquidity_sweep_reversal":
        assert value["code"] == "sweep_stage_C_closed"
        assert value["route"]["regime"] == "Unknown"
        assert value["route"]["fail_codes"] == ["range_transition_history_missing"]
        assert value["history_admission"]["permission_scope"] == "history_evidence_only"
        assert value["history_admission"]["complete_path_verified"] is False
        assert value["history_admission"]["admitted"] is True
        assert dict(value["detection"]["setup_basis"])["intrabar_sequence"] == "unknown"
    elif strategy == "range_reversal":
        assert value["range_permission_replay_sha256"] is not None
        assert value["history_admission"] is None
    else:
        assert value["history_admission"]["admitted"] is True
        assert value["history_admission"]["detection"] == value["detection"]


@pytest.mark.parametrize("direction", ["long", "short"])
def test_original_sweep_outside_quote_cancels_without_repair(direction, tmp_path):
    source = history_raw_source(
        "liquidity_sweep_reversal", direction, sweep_scenario="original_outside"
    )
    declared_funding = declared_history_funding_row(source)
    diagnostic = capture_raw_source(source, tmp_path, declared_funding=declared_funding)
    result = derive(
        diagnostic, base_fixture.account_source(), strategy="liquidity_sweep_reversal"
    )
    value = assert_closed_diagnostic(result)
    assert value["code"] == "fixed_initial_entry_outside_original_zone"
    assert value["action"] == "CANCEL" and result.intent is None
    assert value["history_admission"]["admitted"] is True
    assert value["route"]["regime"] == "Unknown"
    assert Decimal(value["initial_entry"]) == getattr(
        diagnostic.context.quote.ticker, "ask" if direction == "long" else "bid"
    )
    assert value["zone"] is not None and value["event_key"] is not None


def test_actual_neutral_raw_history_cannot_be_relabelled_as_trend(tmp_path):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(prefix, "CAPTURED_AT", NOW)
        source = v2_engine_source("long")
    source = replace(source, evaluated_at=NOW + timedelta(milliseconds=10))
    for frame, rows in source.market.candles.items():
        source.market.candles[frame] = [
            row.model_copy(
                update={
                    "open": D(100),
                    "high": D("100.1"),
                    "low": D("99.9"),
                    "close": D(100),
                }
            )
            for row in rows
        ]
    diagnostic = capture_raw_source(source, tmp_path)
    result = derive(diagnostic, base_fixture.account_source())
    value = assert_closed_diagnostic(result)
    assert result.intent is None
    assert value["action"] in {"WAIT", "NO_TRADE"}
    if value["route"] is not None:
        assert value["route"]["regime"] == "Unknown"
        assert value["code"] == "source_regime_strategy_not_allowed"


def test_event_and_zone_are_existing_raw_evaluator_outputs(captured, passing):
    _, diagnostic, packet = captured
    g1 = data_v2.evaluate_public_market_data_v2(
        diagnostic.packet,
        expected_bundle_sha256=diagnostic.packet.bundle_sha256,
        policy=prefix.DATA_POLICY,
        evaluated_at=inputs(diagnostic, packet)["created_at"],
    )
    raw = json.loads(g1.source_json)
    market = MarketSnapshot.model_validate_json(json.dumps(raw["market"]), strict=True)
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(raw["analysis"]), strict=True
    )
    event = extract_trigger(
        market,
        analysis,
        report_id=passing.intent.report_id,
        strategy=passing.intent.strategy,
        direction=passing.intent.direction,
        observed_at=passing.intent.created_at,
        trigger_ttl_seconds=TIMING_POLICIES["fvg_return"].trigger_ttl_seconds,
    )
    value = json.loads(passing.receipt_json)
    assert value["detection"] == event.model_dump(mode="json")
    zone, _ = build_entry_zone(
        event,
        tick_size=D("0.01"),
        max_allowed_drift_bps=D(30),
        expires_at=passing.intent.expires_at,
    )
    assert value["zone"] == zone.model_dump(mode="json")


@pytest.mark.parametrize(
    "field",
    [
        "expected_public_bundle_sha256",
        "expected_account_plan_sha256",
        "expected_account_packet_sha256",
        "expected_data_policy_sha256",
        "expected_policy_sha256",
    ],
)
def test_external_pin_mismatch_cannot_be_self_signed(captured, field):
    _, diagnostic, packet = captured
    with pytest.raises(ValueError):
        derive(diagnostic, packet, **{field: "0" * 64})


@pytest.mark.parametrize("kind", ["context", "quote", "market", "v1", "raw-dict"])
def test_original_requires_exact_full_v2_packet(captured, kind):
    _, diagnostic, packet = captured
    replacement = {
        "context": diagnostic.context,
        "quote": diagnostic.context.quote,
        "market": diagnostic.context.market,
        "v1": legacy.CollectedPublicMarket.model_construct(),
        "raw-dict": decode(diagnostic.packet.packet_json, public.MAX_PACKET_BYTES),
    }[kind]
    with pytest.raises(ValueError):
        module.derive_original_candidate_precursor_v2(
            replacement, packet, **inputs(diagnostic, packet)
        )


@pytest.mark.parametrize(
    "field", ["stage", "barrier_completed_at", "environment", "quote_profile_sha256"]
)
def test_rehashed_stage_or_profile_cannot_create_an_original(captured, field):
    _, diagnostic, packet = captured
    raw = decode(diagnostic.packet.packet_json, public.MAX_PACKET_BYTES)
    raw[field] = {
        "stage": "post_publication",
        "barrier_completed_at": NOW.isoformat(),
        "environment": "live",
        "quote_profile_sha256": "0" * 64,
    }[field]
    changed = public.CollectedPublicMarketV2(canonical(raw))
    with pytest.raises(ValueError):
        module.derive_original_candidate_precursor_v2(
            changed,
            packet,
            **inputs(
                diagnostic, packet, expected_public_bundle_sha256=changed.bundle_sha256
            ),
        )


def test_six_second_stale_replay_cannot_refresh_received_data(captured):
    _, diagnostic, packet = captured
    with pytest.raises(ValueError):
        derive(
            diagnostic,
            packet,
            created_at=inputs(diagnostic, packet)["created_at"] + timedelta(seconds=6),
        )


def test_creation_before_actual_metadata_receipt_fails_closed(captured):
    _, diagnostic, original_packet = captured
    decision_at = inputs(diagnostic, original_packet)["created_at"]
    packet = base_fixture.account_source(receipt_delay=timedelta(seconds=1))
    assert decision_at < packet.completed_at
    with pytest.raises(ValueError, match="precursor_v2_creation_precedes_sources"):
        derive(diagnostic, packet, created_at=decision_at)


@pytest.mark.parametrize("field", ["direction", "candidate_entry", "expires_at"])
def test_copying_intent_and_self_signing_receipt_cannot_replace_original(
    captured, passing, field
):
    _, diagnostic, packet = captured
    original = passing.intent
    new_value = {
        "direction": "short" if original.direction == "long" else "long",
        "candidate_entry": original.candidate_entry + D("0.01"),
        "expires_at": original.expires_at + timedelta(seconds=1),
    }[field]
    changed = replace(passing, intent=original.model_copy(update={field: new_value}))
    raw = json.loads(changed.receipt_json)
    raw["intent"] = module._intent_tree(changed.intent)
    changed = replace(changed, receipt_json=canonical(raw))
    assert changed.receipt_sha256 != passing.receipt_sha256
    with pytest.raises(ValueError, match="original_replay_mismatch"):
        verify(changed, diagnostic, packet)


@pytest.mark.parametrize(
    "field",
    [
        "execution_authority",
        "original_source_verified",
        "account_complete",
        "metadata_current_owned",
        "funding_pair",
        "timeline",
        "event_key",
        "conditions",
    ],
)
def test_rehashed_result_fields_require_complete_raw_replay(captured, passing, field):
    _, diagnostic, packet = captured
    raw = json.loads(passing.receipt_json)
    if field in {"funding_pair", "timeline", "event_key", "conditions"}:
        raw[field] = None
    else:
        raw[field] = True
    changed = replace(passing, receipt_json=canonical(raw))
    with pytest.raises(ValueError, match="original_replay_mismatch"):
        verify(changed, diagnostic, packet)


class ForeignMapping(Mapping):
    def __init__(self, called):
        self.called = called

    def __len__(self):
        self.called.append("len")
        raise AssertionError("foreign mapping executed")

    def __iter__(self):
        self.called.append("iter")
        raise AssertionError("foreign mapping executed")

    def __getitem__(self, key):
        self.called.append("getitem")
        raise AssertionError("foreign mapping executed")

    def items(self):
        self.called.append("items")
        raise AssertionError("foreign mapping executed")


class ForeignDict(dict):
    """Installable CPython __dict__ subtype whose callbacks must never run."""

    def __init__(self, fields, called):
        dict.__init__(self, fields)
        self.called = called

    def __len__(self):
        self.called.append("dict_len")
        raise AssertionError("foreign dict executed")

    def __iter__(self):
        self.called.append("dict_iter")
        raise AssertionError("foreign dict executed")

    def __getitem__(self, key):
        self.called.append("dict_getitem")
        raise AssertionError("foreign dict executed")

    def items(self):
        self.called.append("dict_items")
        raise AssertionError("foreign dict executed")


def reject_without_callbacks(result, diagnostic, packet, called):
    assert not called
    with pytest.raises(ValueError):
        _ = result.receipt_sha256
    assert not called
    with pytest.raises(ValueError):
        verify(result, diagnostic, packet)
    assert not called


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize(
    "field", ["__dict__", "__pydantic_extra__", "__pydantic_private__"]
)
def test_foreign_mapping_direct_or_proxy_is_not_traversed(
    captured, passing, field, wrapped
):
    _, diagnostic, packet = captured
    called = []
    foreign = ForeignMapping(called)
    value = MappingProxyType(foreign) if wrapped else foreign
    intent = passing.intent.model_copy()
    if field == "__dict__":
        fields = dict(object.__getattribute__(intent, "__dict__"))
        if wrapped:
            # An exact native dict is installable; a declared scalar contains
            # the hostile proxy. Production must reject it without traversal.
            fields["candidate_entry"] = value
            installed = fields
        else:
            installed = ForeignDict(fields, called)
        object.__setattr__(intent, field, installed)
        assert object.__getattribute__(intent, field) is installed
    else:
        object.__setattr__(intent, field, value)
    reject_without_callbacks(
        replace(passing, intent=intent), diagnostic, packet, called
    )


@pytest.mark.parametrize(
    "field", ["report_id", "instrument_id", "strategy", "direction", "candidate_entry"]
)
def test_foreign_model_serializer_cannot_run_in_intent(captured, passing, field):
    _, diagnostic, packet = captured
    called = []

    class ForeignModel(BaseModel):
        @model_serializer
        def unsafe(self):
            called.append("serializer")
            raise AssertionError("foreign serializer executed")

    intent = passing.intent.model_copy(update={field: ForeignModel()})
    reject_without_callbacks(
        replace(passing, intent=intent), diagnostic, packet, called
    )


@pytest.mark.parametrize("number", ["1" * 129, "1e129", "1e-129", "NaN", "Infinity"])
def test_unbounded_decimal_is_rejected_before_replay(captured, passing, number):
    _, diagnostic, packet = captured
    intent = passing.intent.model_copy(update={"candidate_entry": D(number)})
    reject_without_callbacks(replace(passing, intent=intent), diagnostic, packet, [])


def test_decimal_subclass_and_timezone_callbacks_cannot_execute(captured, passing):
    _, diagnostic, packet = captured
    called = []

    class ForeignDecimal(Decimal):
        def is_finite(self):
            called.append("decimal")
            raise AssertionError("foreign decimal executed")

    intent = passing.intent.model_copy(
        update={"candidate_entry": ForeignDecimal("100")}
    )
    reject_without_callbacks(
        replace(passing, intent=intent), diagnostic, packet, called
    )

    class ForeignZone(tzinfo):
        def utcoffset(self, dt):
            called.append("timezone")
            raise AssertionError("foreign timezone executed")

    foreign_time = datetime(2026, 9, 12, 10, 10, tzinfo=ForeignZone())
    for field in ("created_at", "expires_at"):
        intent = passing.intent.model_copy(update={field: foreign_time})
        reject_without_callbacks(
            replace(passing, intent=intent), diagnostic, packet, called
        )
    with pytest.raises(ValueError):
        derive(diagnostic, packet, created_at=foreign_time)
    assert not called


@pytest.mark.parametrize(
    "field",
    ["hidden", "__pydantic_extra__", "__pydantic_private__", "__pydantic_fields_set__"],
)
def test_hidden_model_fields_cannot_be_erased_by_serialization(
    captured, passing, field
):
    _, diagnostic, packet = captured
    intent = passing.intent.model_copy()
    if field == "hidden":
        intent.__dict__["hidden"] = True
    else:
        object.__setattr__(
            intent,
            field,
            {"hidden": True} if field != "__pydantic_fields_set__" else {"hidden"},
        )
    reject_without_callbacks(replace(passing, intent=intent), diagnostic, packet, [])


@pytest.mark.parametrize(
    "kind",
    ["wrong-result", "foreign-intent", "wrong-bytes", "oversized", "noncanonical"],
)
def test_result_and_receipt_exact_bounds_precede_raw_recompute(captured, passing, kind):
    _, diagnostic, packet = captured
    changed = {
        "wrong-result": QualificationIntent.model_construct(),
        "foreign-intent": replace(passing, intent=object()),
        "wrong-bytes": replace(passing, receipt_json="{}"),
        "oversized": replace(passing, receipt_json=b" " * (module._MAX_RECEIPT + 1)),
        "noncanonical": replace(passing, receipt_json=passing.receipt_json + b"\n"),
    }[kind]
    with pytest.raises(ValueError):
        module.verify_original_candidate_precursor_v2(
            changed, None, None, **inputs(diagnostic, packet)
        )


@pytest.mark.parametrize(
    "name",
    [
        "direction",
        "entry",
        "score",
        "event",
        "quantity",
        "protection",
        "passed",
        "engine_callback",
    ],
)
def test_caller_decision_fields_have_no_signature_path(name):
    with pytest.raises(TypeError):
        module.derive_original_candidate_precursor_v2(None, None, **{name: True})


def test_data_policy_foreign_serializer_is_rejected_before_callbacks(captured):
    _, diagnostic, packet = captured
    called = []

    class ForeignModel(BaseModel):
        @model_serializer
        def unsafe(self):
            called.append("serializer")
            raise AssertionError("foreign serializer executed")

    selected = prefix.DATA_POLICY.model_copy(
        update={"maximum_spread_bps": ForeignModel()}
    )
    with pytest.raises(ValueError):
        derive(diagnostic, packet, data_policy=selected)
    assert not called


def test_hostile_decimal_context_cannot_change_original_bytes(captured, passing):
    _, diagnostic, packet = captured
    with localcontext(Context(prec=3, traps=[Inexact])):
        assert derive(diagnostic, packet).receipt_json == passing.receipt_json
        assert verify(passing, diagnostic, packet).receipt_json == passing.receipt_json
