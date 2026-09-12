"""Synthetic, actual-evaluator current G1--G4 checks; never event or IO authority.

All eight families are compared with the existing prefix on the same raw data.
Rejection is retained: these fixtures do not manufacture eight passing routes.
Changed executable fields are recaptured from in-memory MockTransport payloads.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Context, Decimal, Inexact, Rounded, localcontext

import pytest
from pydantic import ValidationError, create_model

from app.strategies import base
from app.trade_qualification import current_conditions as module
from app.trade_qualification import events, location, service, timing
from app.trade_qualification.current_conditions import (
    CurrentConditionsResult,
    copy_current_conditions,
    evaluate_current_conditions,
    verify_current_conditions,
)
from app.trade_qualification.models import QualificationGate
from app.trade_qualification.quote_collector import validate_collected_quote
from app.trade_qualification.service import (
    QualificationIntent,
    QualificationPrefixPolicy,
    evaluate_qualification_prefix,
)
from tests.unit.qualification_prefix_fixtures import prefix_source
from tests.unit.test_qualification_prefix import change_momentum_bar
from tests.unit.test_qualification_quote_collector import Clock, _ms, capture

D = Decimal
ORDER = tuple(QualificationGate)[:4]
STRATEGIES = (
    "trend_pullback",
    "breakout_continuation",
    "liquidity_sweep_reversal",
    "fvg_return",
    "order_block_return",
    "range_reversal",
    "structure_reversal",
    "volatility_expansion",
)
FLAGS = (
    "execution_authority",
    "source_authenticity_verified",
    "event_continuation_verified",
    "execution_recheck_performed",
)


@pytest.fixture(scope="module", params=("long", "short"))
def source(request):
    return prefix_source(request.param)


def inputs(source, **updates):
    values = {
        "intent": QualificationIntent(
            report_id=source.report_id,
            instrument_id=source.market.instrument_id,
            strategy=source.strategy,
            direction=source.direction,
            candidate_entry=source.market.ticker.last,
            created_at=source.evaluated_at,
            expires_at=source.evaluated_at + timedelta(minutes=5),
        ),
        "policy": QualificationPrefixPolicy(
            policy_id="synthetic-current-conditions-policy",
            data=source.policy,
            minimum_score=85,
            tick_size=source.tick_size,
            max_allowed_drift_bps=source.maximum_drift_bps,
        ),
        "quote": source.quote,
        "reference": source.reference,
        "observed_at": source.evaluated_at,
    }
    return values | updates


def check(source, *, market=None, **updates):
    return evaluate_current_conditions(
        source.market if market is None else market, **inputs(source, **updates)
    )


def parity(source, *, market=None, **updates):
    market = source.market if market is None else market
    arguments = inputs(source, **updates)
    result = evaluate_current_conditions(market, **arguments)
    original_arguments = arguments | {
        "evaluated_at": arguments["observed_at"],
        "consumed_event_keys": frozenset(),
    }
    del original_arguments["observed_at"]
    prefix = evaluate_qualification_prefix(market, **original_arguments)
    assert result.gates == prefix.result.gates[:4]
    assert result.data_result == prefix.data_result
    for field in (
        "report_id",
        "symbol",
        "strategy",
        "direction",
        "evaluated_at",
        "raw_score",
        "effective_score",
        "market_regime",
        "htf_bias",
        "setup_state",
        "candidate_entry",
    ):
        assert getattr(result.result, field) == getattr(prefix.result, field), field
    assert_no_authority(result)
    return result


def assert_no_authority(result):
    assert all(getattr(result, name) is False for name in FLAGS)
    assert not result.result.qualified
    assert not result.result.risk_permission
    assert not result.result.evidence_complete
    assert result.result.entry_timing_state == "not_evaluated"
    assert all(
        getattr(result.result, name) is None
        for name in (
            "trigger",
            "entry_zone",
            "reference_price",
            "stop_loss",
            "take_profit",
            "gross_rr",
            "net_rr",
        )
    )
    assert tuple(gate.gate for gate in result.gates) == ORDER[: len(result.gates)]
    assert len(result.gates) <= 4
    assert len(result.evaluation_sha256) == 64


def forbidden(*_args, **_kwargs):
    pytest.fail("Current conditions reached a forbidden later stage or legacy path")


def extended(record):
    subclass = create_model(
        f"Undeclared{type(record).__name__}",
        __base__=type(record),
        hidden_declared=(bool, True),
    )
    return subclass.model_construct(**record.__dict__, hidden_declared=True)


def recapture(
    source,
    *,
    funding=None,
    bid=None,
    ask=None,
    metadata=None,
    role_times=None,
    capture_at=None,
):
    quote = source.quote.quote
    funding = D(0) if funding is None else funding
    bid = quote.bid if bid is None else bid
    ask = quote.ask if ask is None else ask
    at = source.market.received_at if capture_at is None else capture_at

    def modify(role, body):
        if metadata is not None:
            body["synthetic_capture_note"] = metadata
        row = body["data"][0]
        row["ts"] = _ms((role_times or {}).get(role, source.market.ticker.timestamp))
        if role == "ticker":
            row.update(bidPx=str(bid), askPx=str(ask))
        elif role == "mark":
            row["markPx"] = str(quote.mark_price)
        else:
            row.update(
                fundingRate=str(funding),
                fundingTime=_ms(at + timedelta(hours=1)),
                nextFundingTime=_ms(at + timedelta(hours=9)),
            )

    result, requests = asyncio.run(
        capture(
            change=modify,
            clock=Clock(tuple(at + timedelta(milliseconds=i) for i in range(10))),
            report=source.report_id,
        )
    )
    assert len(requests) == 3
    return result


def test_true_four_gate_result_is_strict_frozen_replayable_and_json_roundtrippable(
    source,
):
    before = source.market.model_dump_json()
    result = parity(source)
    assert result.passed and result.code == "passed"
    assert result.result.raw_score == result.result.effective_score == 100
    assert copy_current_conditions(result) == result
    assert verify_current_conditions(result, source.market, **inputs(source)) == result
    restored = CurrentConditionsResult.model_validate_json(
        result.model_dump_json(round_trip=True), strict=True
    )
    assert restored == result
    assert restored.evaluation_sha256 == result.evaluation_sha256
    # JSON's explicit decoding path must not leak coercion into Python input.
    with pytest.raises(ValidationError):
        CurrentConditionsResult.model_validate(
            json.loads(result.model_dump_json(round_trip=True)), strict=True
        )
    assert source.market.model_dump_json() == before
    with pytest.raises(ValidationError):
        result.execution_authority = True
    with pytest.raises(TypeError):
        result.gates[0].measured_values["caller_approval"] = True


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_each_family_matches_actual_prefix_first_four_gates(source, strategy):
    intent = inputs(source)["intent"].model_copy(update={"strategy": strategy})
    result = parity(source, intent=intent)
    if strategy in {"fvg_return", "trend_pullback"}:
        assert result.passed
    elif strategy in {"breakout_continuation", "order_block_return"}:
        assert result.code == "required_setup_missing"
    else:
        assert result.code == "regime_strategy_not_allowed"


@pytest.mark.parametrize("missing", ("quote", "reference"))
def test_g1_missing_source_stops_before_route_and_conditions(
    source, monkeypatch, missing
):
    monkeypatch.setattr(module, "route_regime", forbidden)
    monkeypatch.setattr(module, "assess_conditions", forbidden)
    result = check(source, **{missing: None})
    assert not result.passed and len(result.gates) == 1
    assert_no_authority(result)


@pytest.mark.parametrize("kind", ("hidden", "subclass", "nan", "unconfirmed"))
def test_g1_rejects_dirty_raw_candle_without_any_route(source, monkeypatch, kind):
    monkeypatch.setattr(module, "route_regime", forbidden)
    monkeypatch.setattr(module, "assess_conditions", forbidden)
    market = source.market.model_copy(deep=True)
    row = market.candles["15m"][-1]
    if kind == "subclass":
        row = extended(row)
    else:
        row = row.model_copy(
            update={
                "hidden": {"hidden": True},
                "nan": {"high": D("NaN")},
                "unconfirmed": {"confirmed": False},
            }[kind]
        )
    market.candles["15m"][-1] = row
    result = check(source, market=market)
    assert not result.passed and len(result.gates) == 1
    assert_no_authority(result)


@pytest.mark.parametrize(
    "strategy",
    (
        "liquidity_sweep_reversal",
        "range_reversal",
        "structure_reversal",
        "volatility_expansion",
    ),
)
def test_actual_disallowed_route_never_calls_conditions(source, monkeypatch, strategy):
    monkeypatch.setattr(module, "assess_conditions", forbidden)
    intent = inputs(source)["intent"].model_copy(update={"strategy": strategy})
    result = check(source, intent=intent)
    assert len(result.gates) == 2
    assert result.code == "regime_strategy_not_allowed"
    assert_no_authority(result)


def test_opposite_requested_direction_is_not_repaired_by_strategy_score(source):
    intent = inputs(source)["intent"].model_copy(
        update={"direction": "short" if source.direction == "long" else "long"}
    )
    result = parity(source, intent=intent)
    assert len(result.gates) == 3
    assert result.code == "htf_strategy_permission_denied"


def test_required_current_momentum_cannot_be_repaired_by_minimum_score_zero(source):
    market = change_momentum_bar(source, -1, "100.79")
    policy = inputs(source)["policy"].model_copy(update={"minimum_score": 0})
    result = parity(source, market=market, policy=policy)
    assert len(result.gates) == 4
    assert result.code == "required_setup_missing"
    assert "5m_trigger" in result.gates[-1].measured_values["required_failures"]


def test_current_boolean_state_is_not_a_new_event_or_event_survival_proof(
    source, monkeypatch
):
    market = change_momentum_bar(source, -2, "100.89")
    original = parity(source, market=market)
    assert original.passed
    for owner, name in (
        (events, "extract_trigger"),
        (service, "extract_trigger"),
        (service, "evaluate_qualification_prefix"),
        (timing, "evaluate_timing"),
        (location, "build_entry_zone"),
        (base, "build_candidate"),
        (base, "common_vetoes"),
        (base, "evaluate_conditions"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    result = check(source, market=market)
    assert result == original and result.passed
    for name in ("extract_trigger", "evaluate_qualification_prefix", "build_candidate"):
        assert not hasattr(module, name)
    assert_no_authority(result)


@pytest.mark.parametrize("state", ("expired", "future", "outside_zone"))
def test_current_conditions_preserves_intent_without_claiming_timing_or_location(
    source, state
):
    intent = inputs(source)["intent"]
    if state == "expired":
        changed = {
            "created_at": source.evaluated_at - timedelta(seconds=1),
            "expires_at": source.evaluated_at,
        }
    elif state == "future":
        changed = {"created_at": source.evaluated_at + timedelta(seconds=1)}
    else:
        changed = {"candidate_entry": D(102) if source.direction == "long" else D(98)}
    intent = intent.model_copy(update=changed)
    result = parity(source, intent=intent)
    assert result.passed and result.intent == intent
    assert result.result.candidate_entry == intent.candidate_entry
    assert_no_authority(result)


def test_caller_quality_is_ignored_before_any_serialization(source):
    class Unreadable:
        def __iter__(self):
            forbidden()

        def model_dump(self, *_args, **_kwargs):
            forbidden()

    market = source.market.model_copy(update={"quality": Unreadable()})
    assert check(source, market=market) == check(source)


def test_legacy_unstamped_market_funding_and_mark_do_not_replace_fresh_quote(source):
    market = source.market.model_copy(
        update={
            "funding_rate": D(999),
            "mark_price": D(1),
            "next_funding_time": source.evaluated_at - timedelta(days=1),
        }
    )
    result = parity(source, market=market)
    assert result.passed
    assert result.gates[3].measured_values["fresh_signed_funding_cost_bps"] == 0


@pytest.mark.parametrize("offset", (-1, 0, 1))
def test_fresh_signed_adverse_funding_uses_exact_boundary(source, offset):
    rate = D("0.0001") + D(offset) * D("0.00000000000000000001")
    if source.direction == "short":
        rate = -rate
    quote = recapture(source, funding=rate)
    policy = inputs(source)["policy"].model_copy(
        update={"maximum_adverse_funding_bps": D(1)}
    )
    result = parity(source, quote=quote, policy=policy)
    assert result.passed is (offset <= 0)
    assert result.code == (
        "passed" if offset <= 0 else "strategy_adverse_funding_exceeded"
    )


def test_favorable_funding_is_not_misread_as_adverse_cost(source):
    rate = D("-0.0002") if source.direction == "long" else D("0.0002")
    quote = recapture(source, funding=rate)
    policy = inputs(source)["policy"].model_copy(
        update={"maximum_adverse_funding_bps": D(0)}
    )
    result = parity(source, quote=quote, policy=policy)
    assert result.passed
    assert result.gates[3].measured_values["fresh_signed_funding_cost_bps"] == D(-2)


@pytest.mark.parametrize("offset", (-1, 0, 1))
def test_fresh_spread_exact_limit_is_inclusive_without_float_rounding(source, offset):
    midpoint = source.market.ticker.last
    width = midpoint / D(10000) + D(offset) * D("0.00000000000000000002")
    quote = recapture(source, bid=midpoint - width / D(2), ask=midpoint + width / D(2))
    policy = inputs(source)["policy"].model_copy(
        update={"maximum_strategy_spread_bps": D(1)}
    )
    result = parity(source, quote=quote, policy=policy)
    assert result.passed is (offset <= 0)
    assert result.code == ("passed" if offset <= 0 else "strategy_spread_exceeded")


@pytest.mark.parametrize("minimum", (80, 81))
def test_real_breakout_nonrequired_volume_score_obeys_minimum_boundary(source, minimum):
    market = source.market.model_copy(deep=True)
    long = source.direction == "long"
    market.candles["15m"][-10] = market.candles["15m"][-10].model_copy(
        update={"high" if long else "low": D("100.2") if long else D("99.8")}
    )
    market.candles["5m"][-1] = market.candles["5m"][-1].model_copy(
        update={"volume_contracts": D(1)}
    )
    args = inputs(source)
    intent = args["intent"].model_copy(update={"strategy": "breakout_continuation"})
    policy = args["policy"].model_copy(update={"minimum_score": minimum})
    result = parity(source, market=market, intent=intent, policy=policy)
    measured = result.gates[-1].measured_values
    assert measured["required_failures"] == measured["veto_failures"] == "none"
    assert measured["5m_volume"] is False
    assert measured["raw_score"] == 80
    assert result.passed is (minimum == 80)
    assert result.code == (
        "passed" if minimum == 80 else "strategy_score_below_minimum"
    )


@pytest.mark.parametrize("field", ("intent", "policy", "data_policy"))
@pytest.mark.parametrize("kind", ("hidden", "subclass"))
def test_input_declared_models_reject_dirty_nested_state_before_g1(
    source, monkeypatch, field, kind
):
    monkeypatch.setattr(module, "evaluate_data", forbidden)
    args = inputs(source)
    original = args["policy"].data if field == "data_policy" else args[field]
    changed = (
        original.model_copy(update={"undeclared": True})
        if kind == "hidden"
        else extended(original)
    )
    if field == "data_policy":
        args["policy"] = args["policy"].model_copy(update={"data": changed})
    else:
        args[field] = changed
    with pytest.raises(ValueError):
        evaluate_current_conditions(source.market, **args)


@pytest.mark.parametrize(
    ("record", "field", "value"),
    (
        ("intent", "candidate_entry", "100"),
        ("intent", "candidate_entry", D("NaN")),
        ("intent", "candidate_entry", True),
        ("intent", "direction", ["long"]),
        ("intent", "strategy", "caller_approved_strategy"),
        ("intent", "report_id", "../other-report"),
        ("policy", "minimum_score", "85"),
        ("policy", "minimum_score", True),
        ("policy", "maximum_adverse_funding_bps", D("15.00000000000000000001")),
        ("policy", "maximum_strategy_spread_bps", D("8.00000000000000000001")),
        ("policy", "tick_size", 0.01),
    ),
)
def test_invalid_contract_cannot_be_coerced_before_g1(
    source, monkeypatch, record, field, value
):
    monkeypatch.setattr(module, "evaluate_data", forbidden)
    original = inputs(source)[record]
    with pytest.raises(ValueError):
        check(source, **{record: original.model_copy(update={field: value})})


@pytest.mark.parametrize(
    "value",
    (None, True, "2026-09-12", datetime(2026, 9, 12, tzinfo=UTC).replace(tzinfo=None)),
)
def test_observation_requires_exact_aware_datetime_before_g1(
    source, monkeypatch, value
):
    monkeypatch.setattr(module, "evaluate_data", forbidden)
    with pytest.raises(ValueError):
        check(source, observed_at=value)


@pytest.mark.parametrize(
    "field", ("intent", "policy", "data_policy", "data_result", "result", "gate")
)
@pytest.mark.parametrize("kind", ("hidden", "subclass"))
def test_copy_and_fingerprint_reject_every_dirty_nested_declared_model(
    source, field, kind
):
    result = check(source)
    assert result.passed
    if field == "data_policy":
        original = result.policy.data
    elif field == "gate":
        original = result.gates[0]
    else:
        original = getattr(result, field)
    changed = (
        original.model_copy(update={"undeclared": True})
        if kind == "hidden"
        else extended(original)
    )
    if field == "data_policy":
        updates = {"policy": result.policy.model_copy(update={"data": changed})}
    elif field == "gate":
        updates = {
            "result": result.result.model_copy(
                update={"gates": (changed, *result.gates[1:])}
            )
        }
    else:
        updates = {field: changed}
    dirty = result.model_copy(update=updates)
    with pytest.raises(ValueError):
        copy_current_conditions(dirty)
    with pytest.raises(ValueError):
        _ = dirty.evaluation_sha256


@pytest.mark.parametrize("value", (True, 0, "false"))
@pytest.mark.parametrize("flag", FLAGS)
def test_no_false_authority_flag_can_be_replaced_or_coerced(source, flag, value):
    result = check(source)
    dirty = result.model_copy(update={flag: value})
    with pytest.raises(ValueError):
        copy_current_conditions(dirty)


@pytest.mark.parametrize("field", ("candidate_entry", "created_at", "gates", "score"))
def test_json_roundtrip_fix_does_not_coerce_python_nested_inputs(source, field):
    result = check(source)
    payload = module._plain(result)
    if field == "candidate_entry":
        payload["intent"][field] = str(result.intent.candidate_entry)
    elif field == "created_at":
        payload["intent"][field] = result.intent.created_at.isoformat()
    elif field == "gates":
        payload["result"][field] = list(payload["result"][field])
    else:
        payload["policy"]["minimum_score"] = str(result.policy.minimum_score)
    with pytest.raises(ValidationError):
        CurrentConditionsResult.model_validate(payload, strict=True)


@pytest.mark.parametrize("length", (1, 2, 3))
def test_successful_current_checks_cannot_end_before_fourth_gate(source, length):
    result = check(source)
    dirty = result.model_copy(
        update={
            "result": result.result.model_copy(update={"gates": result.gates[:length]})
        }
    )
    with pytest.raises(ValueError):
        copy_current_conditions(dirty)


@pytest.mark.parametrize(
    "field", ("trigger", "entry_zone", "reference_price", "stop_loss", "take_profit")
)
def test_current_record_cannot_import_preexisting_event_or_protection(source, field):
    result = check(source)
    original = inputs(source)
    prefix_args = original | {
        "evaluated_at": original["observed_at"],
        "consumed_event_keys": frozenset(),
    }
    del prefix_args["observed_at"]
    prefix = evaluate_qualification_prefix(source.market, **prefix_args)
    assert prefix.prefix_complete
    value = (
        getattr(prefix.result, field)
        if field in {"trigger", "entry_zone", "reference_price"}
        else D(100)
    )
    dirty = result.model_copy(
        update={"result": result.result.model_copy(update={field: value})}
    )
    with pytest.raises(ValueError):
        copy_current_conditions(dirty)


@pytest.mark.parametrize(
    "change",
    (
        "market",
        "quote",
        "quote_metadata",
        "reference",
        "policy",
        "intent",
        "observed_at",
    ),
)
def test_replay_rejects_replaced_original_source_policy_metadata_or_intent(
    source, change
):
    result = check(source)
    args = inputs(source)
    market = source.market
    if change == "market":
        market = market.model_copy(deep=True)
        row = market.candles["5m"][0]
        market.candles["5m"][0] = row.model_copy(
            update={"volume_quote": row.volume_quote + D(1)}
        )
    elif change == "quote":
        args["quote"] = recapture(source, funding=D("0.00001"))
    elif change == "quote_metadata":
        args["quote"] = recapture(source, metadata="different synthetic wire record")
        assert args["quote"].quote == source.quote.quote
        assert args["quote"].bundle_sha256 != source.quote.bundle_sha256
    elif change == "reference":
        args["reference"] = args["reference"].model_copy(
            update={
                "received_at": args["reference"].received_at + timedelta(microseconds=1)
            }
        )
    elif change == "policy":
        args["policy"] = args["policy"].model_copy(update={"tick_size": D("0.02")})
    elif change == "intent":
        args["intent"] = args["intent"].model_copy(
            update={"expires_at": args["intent"].expires_at + timedelta(seconds=1)}
        )
    else:
        args["observed_at"] += timedelta(microseconds=1)
    changed = evaluate_current_conditions(market, **args)
    assert changed.passed, changed
    assert changed != result
    with pytest.raises(ValueError, match="replay_mismatch"):
        verify_current_conditions(result, market, **args)


@pytest.mark.parametrize("precision", (3, 17))
def test_hostile_decimal_context_and_equivalent_utc_offset_preserve_all_pins(
    source, precision
):
    expected = check(source)
    context = Context(prec=precision, rounding=ROUND_DOWN)
    context.traps[Inexact] = True
    context.traps[Rounded] = True
    with localcontext(context):
        result = check(
            source,
            observed_at=source.evaluated_at.astimezone(timezone(timedelta(hours=8))),
        )
        assert result == expected
        assert result.evaluation_sha256 == expected.evaluation_sha256


def test_current_result_root_subclass_and_hidden_fields_are_not_copyable(source):
    result = check(source)
    for dirty in (extended(result), result.model_copy(update={"hidden": True})):
        with pytest.raises(ValueError):
            copy_current_conditions(dirty)


@pytest.mark.parametrize("role", ("ticker", "mark", "funding"))
@pytest.mark.parametrize("state", ("stale", "future"))
def test_g1_rejects_self_consistent_component_capture_freshness_before_route(
    source, monkeypatch, role, state
):
    now = source.evaluated_at
    at = source.market.received_at
    if state == "stale":
        component_at = at - timedelta(seconds=3)
        capture_at = at
    else:
        # A causal future source observation necessarily has a future receipt.
        # The collector validates that capture; G1 must reject it relative to
        # this older decision time, never relabel its timestamps as current.
        component_at = at + timedelta(seconds=1)
        capture_at = at + timedelta(seconds=2)
    quote = recapture(source, role_times={role: component_at}, capture_at=capture_at)
    assert validate_collected_quote(quote) == quote
    time_fields = {
        "ticker": "quote_time",
        "mark": "mark_time",
        "funding": "funding_time",
    }
    assert getattr(quote.quote, time_fields[role]) == component_at
    assert all(
        getattr(quote.quote, field) <= now
        for name, field in time_fields.items()
        if name != role
    )
    policy = inputs(source)["policy"]
    policy = policy.model_copy(
        update={"data": policy.data.model_copy(update={"maximum_quote_age_seconds": 2})}
    )
    monkeypatch.setattr(module, "route_regime", forbidden)
    monkeypatch.setattr(module, "assess_conditions", forbidden)
    result = check(source, quote=quote, policy=policy)
    assert len(result.gates) == 1 and not result.passed
    assert result.code == "stale_market_data"
    assert_no_authority(result)
