"""Pure R3 fixed-bracket checks from synthetic, genuinely qualified OHLC.

No new event, candidate, bracket, source authenticity or execution permission is
invented. Current-history mutations test this component only, not an R2 append
proof. Every changed valid raw source gets a real explicit-clock analysis.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Context, Decimal, Inexact, Rounded, localcontext
from fractions import Fraction

import pytest
from pydantic import ValidationError, create_model

from app.analysis.service import analyze_snapshot_at
from app.market.quality.candles import inspect_candles_at
from app.strategies.structural_protection import _source_anchors
from app.trade_qualification import fixed_protection as module
from app.trade_qualification.engine import evaluate_pre_evidence
from app.trade_qualification.fixed_protection import (
    FixedProtectionError,
    FixedProtectionResult,
    evaluate_fixed_protection,
)
from app.trade_qualification.location import quote_fingerprint
from tests.unit.qualification_engine_fixtures import engine_inputs, engine_source
from tests.unit.qualification_prefix_fixtures import restore_source

D = Decimal
TIMEFRAMES = ("4H", "1H", "15m", "5m")
COMPONENTS = ("quote_time", "mark_time", "funding_time", "received_at")
STEPS = (
    "original_source",
    "original_selection",
    "current_source",
    "quote",
    "fixed_geometry",
    "current_noise",
    "current_liquidity",
    "current_target",
)
FLAGS = (
    "execution_authority",
    "source_authenticity_verified",
    "continuation_verified",
    "conditions_verified",
    "economics_verified",
    "atomic_risk_reserved",
)


@pytest.fixture(scope="module", params=("long", "short"))
def baseline(request):
    source = engine_source(request.param)
    inputs = engine_inputs(source)
    run = evaluate_pre_evidence(source.market, **inputs)
    assert run.pre_evidence_complete, run.result
    market, analysis = restore_source(run.prefix.data_result)
    return {
        "original_market": market,
        "original_analysis": analysis,
        "current_market": market.model_copy(deep=True),
        "current_analysis": analysis.model_copy(deep=True),
        "detection": run.prefix.detection,
        "original_selection_audit_json": run.protection_audit_json,
        "stop_loss": run.result.stop_loss,
        "take_profit": run.result.take_profit,
        "entry": inputs["intent"].candidate_entry,
        "policy": inputs["policy"].protection,
        "tick_size": source.tick_size,
        "data_policy": source.policy,
        "quote": source.quote.quote,
        "observed_at": source.evaluated_at,
    }


def evaluate(baseline, **updates):
    return evaluate_fixed_protection(**(baseline | updates))


def rebuild(baseline, market, *, now=None):
    now = baseline["observed_at"] if now is None else now
    quality = {
        tf: inspect_candles_at(rows, tf, current_time=now)
        for tf, rows in market.candles.items()
    }
    assert all(item.ok for item in quality.values()), quality
    market = market.model_copy(update={"quality": quality})
    analysis = analyze_snapshot_at(
        market,
        evaluated_at=now,
        version=baseline["data_policy"].analysis_version,
    )
    return {"current_market": market, "current_analysis": analysis}


def extended(record):
    subclass = create_model(
        f"Untrusted{type(record).__name__}",
        __base__=type(record),
        hidden_declared=(bool, True),
    )
    return subclass.model_construct(**record.__dict__, hidden_declared=True)


def assert_fixed(result, baseline):
    assert (result.entry, result.stop_loss, result.take_profit) == (
        baseline["entry"],
        baseline["stop_loss"],
        baseline["take_profit"],
    )
    assert result.report_id == baseline["detection"].report_id
    assert result.instrument_id == baseline["detection"].instrument_id
    assert result.direction == baseline["detection"].direction
    assert result.strategy == baseline["detection"].strategy
    assert result.policy == baseline["policy"]
    assert result.data_policy == baseline["data_policy"]
    assert type(result.checks) is tuple
    assert tuple(check.step for check in result.checks) == STEPS[: len(result.checks)]
    assert all(getattr(result, flag) is False for flag in FLAGS)
    if result.passed:
        assert all(check.passed for check in result.checks)
    else:
        assert result.checks and not result.checks[-1].passed
        assert all(check.passed for check in result.checks[:-1])
    assert len(result.evaluation_sha256) == 64


def test_real_original_bracket_passes_without_repricing(baseline):
    before = baseline["original_market"].model_dump_json()
    result = evaluate(baseline)
    assert result.passed, result
    assert_fixed(result, baseline)
    assert len(result.checks) == 8
    assert not hasattr(result, "qualified")
    assert not hasattr(result, "gates")
    assert result.original_source_sha256 == baseline["detection"].source_sha256
    assert result.current_source_sha256 == result.original_source_sha256
    assert (
        result.original_audit_sha256
        == hashlib.sha256(
            baseline["original_selection_audit_json"].encode()
        ).hexdigest()
    )
    assert result.quote_sha256 == quote_fingerprint(baseline["quote"])
    assert baseline["original_market"].model_dump_json() == before
    assert (
        FixedProtectionResult.model_validate_json(result.model_dump_json(), strict=True)
        == result
    )
    with pytest.raises(ValidationError):
        result.passed = False


def test_changed_but_safe_raw_source_has_a_new_pin_and_same_fixed_prices(baseline):
    market = baseline["current_market"].model_copy(deep=True)
    row = market.candles["5m"][0]
    market.candles["5m"][0] = row.model_copy(
        update={"volume_quote": row.volume_quote + D(1)}
    )
    changed = rebuild(baseline, market)
    result = evaluate(baseline, **changed)
    assert result.passed, result
    assert result.original_source_sha256 != result.current_source_sha256
    assert_fixed(result, baseline)


def test_selector_is_only_called_on_original_source_never_current_history(
    baseline, monkeypatch
):
    original_selector = module.select_structural_protection
    calls = []

    def inspect_source(detection, market, analysis, **kwargs):
        calls.append(market)
        assert market == baseline["original_market"]
        assert analysis == baseline["original_analysis"]
        assert kwargs["entry"] == baseline["entry"]
        return original_selector(detection, market, analysis, **kwargs)

    monkeypatch.setattr(module, "select_structural_protection", inspect_source)
    market = baseline["current_market"].model_copy(deep=True)
    row = market.candles["5m"][0]
    market.candles["5m"][0] = row.model_copy(update={"volume_quote": D(10001)})
    result = evaluate(baseline, **rebuild(baseline, market))
    assert result.passed, result
    assert len(calls) == 1
    assert_fixed(result, baseline)


def test_increased_current_atr_cancels_instead_of_moving_original_stop(baseline):
    market = baseline["current_market"].model_copy(deep=True)
    for timeframe in ("15m", "1H", "4H"):
        rows = market.candles[timeframe]
        for index in range(-14, 0):
            rows[index] = rows[index].model_copy(update={"high": D(120), "low": D(80)})
    changed = rebuild(baseline, market)
    result = evaluate(baseline, **changed)
    assert not result.passed, result
    assert result.code == "fixed_stop_noise_insufficient"
    assert result.checks[-1].step == "current_noise"
    assert result.current_source_sha256 != result.original_source_sha256
    assert_fixed(result, baseline)


@pytest.mark.parametrize("timeframe", ("15m", "1H", "4H"))
def test_new_nearer_confirmed_target_blocks_original_take_profit(baseline, timeframe):
    market = baseline["current_market"].model_copy(deep=True)
    long = baseline["detection"].direction == "long"
    field, price = ("high", D(103)) if long else ("low", D(97))
    rows = market.candles[timeframe]
    # Bring the old distant pivot closer without inflating the original stop's
    # current ATR first; this must reach the target check, not fail at noise.
    rows[-15] = rows[-15].model_copy(update={field: price})
    changed = rebuild(baseline, market)
    _, targets = _source_anchors(
        changed["current_market"].candles, baseline["detection"]
    )
    assert any(
        item.timeframe == timeframe and item.anchor_price == price for item in targets
    )
    result = evaluate(baseline, **changed)
    assert not result.passed, result
    assert result.code == "fixed_target_intervening_barrier"
    assert result.checks[-1].step == "current_target"
    assert_fixed(result, baseline)


@pytest.mark.parametrize("timeframe", ("15m", "1H", "4H"))
def test_new_equal_price_pool_cannot_be_cleared_by_replacing_fixed_stop(
    baseline, timeframe
):
    market = baseline["current_market"].model_copy(deep=True)
    long = baseline["detection"].direction == "long"
    field = "low" if long else "high"
    price = baseline["stop_loss"] + (D("0.03") if long else -D("0.03"))
    rows = market.candles[timeframe]
    # The previously deeper second low/high moves toward the first; ATR does
    # not rise and the actual equal-pivot exclusion is reached independently.
    for index in (-25, -20):
        rows[index] = rows[index].model_copy(update={field: price})
    changed = rebuild(baseline, market)
    stops, _ = _source_anchors(changed["current_market"].candles, baseline["detection"])
    assert any(
        item.timeframe == timeframe
        and item.source == ("equal_lows" if long else "equal_highs")
        and item.anchor_price == price
        for item in stops
    )
    result = evaluate(baseline, **changed)
    assert not result.passed, result
    assert result.code == "fixed_stop_liquidity_buffer"
    assert result.checks[-1].step == "current_liquidity"
    assert_fixed(result, baseline)


def test_current_spread_increases_buffer_without_repricing_entry(baseline):
    quote = baseline["quote"]
    updates = (
        {"bid": quote.bid - D("0.1")}
        if baseline["detection"].direction == "long"
        else {"ask": quote.ask + D("0.1")}
    )
    result = evaluate(baseline, quote=quote.model_copy(update=updates))
    assert not result.passed, result
    assert result.code == "fixed_stop_noise_insufficient"
    assert_fixed(result, baseline)


@pytest.mark.parametrize("field", ("entry", "stop_loss", "take_profit"))
def test_changed_original_price_cannot_be_reselected(baseline, field):
    result = evaluate(baseline, **{field: baseline[field] + D("0.01")})
    assert not result.passed, result
    assert result.code == "fixed_original_selection_invalid"


@pytest.mark.parametrize("change", ("whitespace", "selected", "source_pin", "hidden"))
def test_original_audit_requires_exact_selector_replay(baseline, change):
    raw = baseline["original_selection_audit_json"]
    if change == "whitespace":
        raw = " " + raw
    else:
        payload = json.loads(raw)
        if change == "selected":
            payload["selected"]["stop"]["final_stop"] = "1.00"
        elif change == "source_pin":
            payload["source_sha256"] = "0" * 64
        else:
            payload["caller_authorized"] = True
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    result = evaluate(baseline, original_selection_audit_json=raw)
    assert not result.passed, result
    assert result.code == "fixed_original_selection_invalid"
    assert_fixed(result, baseline)


@pytest.mark.parametrize("timeframe", TIMEFRAMES)
def test_raw_source_quality_cannot_be_replaced_by_original_analysis_or_flags(
    baseline, timeframe
):
    market = baseline["current_market"].model_copy(deep=True)
    market.candles[timeframe][-1] = market.candles[timeframe][-1].model_copy(
        update={"confirmed": False}
    )
    result = evaluate(baseline, current_market=market)
    assert not result.passed, result
    assert result.code == "fixed_current_source_invalid"
    assert_fixed(result, baseline)


@pytest.mark.parametrize("side", ("original", "current"))
def test_claimed_analysis_must_equal_recomputation_from_raw(baseline, side):
    analysis = baseline[f"{side}_analysis"].model_copy(deep=True)
    view = analysis.timeframe_analyses["15m"]
    view.indicators = view.indicators.model_copy(update={"atr14": D("0.00000001")})
    result = evaluate(baseline, **{f"{side}_analysis": analysis})
    assert not result.passed, result
    assert result.code == f"fixed_{side}_source_invalid"
    assert_fixed(result, baseline)


@pytest.mark.parametrize("side", ("original", "current"))
@pytest.mark.parametrize("kind", ("hidden", "subclass"))
def test_raw_nested_candle_cannot_hide_undeclared_state(baseline, side, kind):
    market = baseline[f"{side}_market"].model_copy(deep=True)
    row = market.candles["15m"][-1]
    market.candles["15m"][-1] = (
        row.model_copy(update={"hidden": True}) if kind == "hidden" else extended(row)
    )
    result = evaluate(baseline, **{f"{side}_market": market})
    assert not result.passed, result
    assert result.code == f"fixed_{side}_source_invalid"


@pytest.mark.parametrize(
    "mutation", ("gap", "duplicate", "off_grid", "negative_volume", "precision", "nan")
)
def test_invalid_raw_history_fails_before_trusting_matching_quality(baseline, mutation):
    market = baseline["current_market"].model_copy(deep=True)
    rows = market.candles["15m"]
    row = rows[-8]
    if mutation == "gap":
        del rows[-8]
    else:
        updates = {
            "duplicate": {"timestamp": rows[-9].timestamp},
            "off_grid": {"timestamp": row.timestamp + timedelta(microseconds=1)},
            "negative_volume": {"volume_contracts": D(-1)},
            "precision": {"high": D("102.000000000000000000001")},
            "nan": {"high": D("NaN")},
        }[mutation]
        rows[-8] = row.model_copy(update=updates)
    result = evaluate(baseline, current_market=market)
    assert not result.passed, result
    assert result.code == "fixed_current_source_invalid"
    assert_fixed(result, baseline)


@pytest.mark.parametrize("field", ("entry", "stop_loss", "take_profit", "tick_size"))
@pytest.mark.parametrize("value", (None, True, "1", 1, 1.0, D(0), D("NaN"), D("1e-21")))
def test_invalid_scalar_contract_never_becomes_a_failed_source_record(
    baseline, field, value
):
    with pytest.raises(FixedProtectionError):
        evaluate(baseline, **{field: value})


@pytest.mark.parametrize("field", ("policy", "data_policy", "detection"))
@pytest.mark.parametrize("kind", ("hidden", "subclass"))
def test_declared_contracts_reject_model_copy_extras_and_subclasses(
    baseline, field, kind
):
    record = baseline[field]
    changed = (
        record.model_copy(update={"hidden": True})
        if kind == "hidden"
        else extended(record)
    )
    with pytest.raises(FixedProtectionError):
        evaluate(baseline, **{field: changed})


@pytest.mark.parametrize(
    "value",
    (None, True, "2026-09-12", datetime(2026, 9, 12, tzinfo=UTC).replace(tzinfo=None)),
)
def test_invalid_observation_contract_is_rejected(baseline, value):
    with pytest.raises(FixedProtectionError):
        evaluate(baseline, observed_at=value)


@pytest.mark.parametrize("component", COMPONENTS)
@pytest.mark.parametrize("offset", ("stale", "future"))
def test_each_quote_component_independently_expires_or_rejects_future(
    baseline, component, offset
):
    now = baseline["observed_at"]
    quote = baseline["quote"]
    at = (
        now
        - timedelta(seconds=baseline["data_policy"].maximum_quote_age_seconds)
        - timedelta(microseconds=1)
        if offset == "stale"
        else now + timedelta(microseconds=1)
    )
    result = evaluate(baseline, quote=quote.model_copy(update={component: at}))
    assert not result.passed, result
    assert result.code == "fixed_quote_invalid"
    assert_fixed(result, baseline)


@pytest.mark.parametrize("component", COMPONENTS)
def test_component_age_exactly_at_policy_limit_is_allowed(baseline, component):
    at = baseline["observed_at"] - timedelta(
        seconds=baseline["data_policy"].maximum_quote_age_seconds
    )
    updates = {component: at}
    if component == "received_at":
        updates = {name: at for name in COMPONENTS}
        updates["request_started_at"] = at
    result = evaluate(baseline, quote=baseline["quote"].model_copy(update=updates))
    assert result.passed, result
    assert_fixed(result, baseline)


@pytest.mark.parametrize("field", ("bid", "ask", "mark_price", "funding_rate"))
def test_quote_finite_model_copy_tamper_fails_closed(baseline, field):
    quote = baseline["quote"].model_copy(update={field: D("NaN")})
    result = evaluate(baseline, quote=quote)
    assert not result.passed, result
    assert result.code == "fixed_quote_invalid"


def test_equivalent_utc_clock_and_hostile_decimal_context_preserve_full_result(
    baseline,
):
    expected = evaluate(baseline)
    assert expected.passed, expected
    shifted = baseline["observed_at"].astimezone(timezone(timedelta(hours=8)))
    context = Context(prec=3, Emin=-3, Emax=3)
    context.traps[Inexact] = True
    context.traps[Rounded] = True
    with localcontext(context):
        actual = evaluate(baseline, observed_at=shifted)
        assert actual.evaluation_sha256 == expected.evaluation_sha256
    assert actual == expected
    assert actual.observed_at.tzinfo is UTC


@pytest.mark.parametrize("field", ("policy", "data_policy", "checks"))
@pytest.mark.parametrize("kind", ("hidden", "subclass"))
def test_result_hash_rejects_dirty_nested_records(baseline, field, kind):
    result = evaluate(baseline)
    assert result.passed, result
    original = result.checks[0] if field == "checks" else getattr(result, field)
    changed = (
        original.model_copy(update={"hidden": True})
        if kind == "hidden"
        else extended(original)
    )
    updates = {field: (changed, *result.checks[1:]) if field == "checks" else changed}
    dirty = result.model_copy(update=updates)
    with pytest.raises((FixedProtectionError, ValueError, TypeError)):
        _ = dirty.evaluation_sha256


@pytest.mark.parametrize("flag", FLAGS)
@pytest.mark.parametrize("value", (True, 0, "false"))
def test_output_cannot_coerce_any_authority_flag(baseline, flag, value):
    result = evaluate(baseline)
    assert result.passed, result
    dirty = result.model_copy(update={flag: value})
    with pytest.raises((FixedProtectionError, ValueError, TypeError)):
        _ = dirty.evaluation_sha256


def test_output_checks_and_their_measurements_are_frozen(baseline):
    result = evaluate(baseline)
    assert result.passed, result
    with pytest.raises(ValidationError):
        result.checks[0].passed = False
    with pytest.raises(TypeError):
        result.checks[0].measured_values["new_authority"] = True
    with pytest.raises((FixedProtectionError, ValueError, TypeError)):
        _ = extended(result).evaluation_sha256


@pytest.mark.parametrize("field", ("report_id", "instrument_id"))
def test_fresh_quote_identity_must_match_original_event(baseline, field):
    quote = baseline["quote"].model_copy(update={field: "unrelated-synthetic-source"})
    result = evaluate(baseline, quote=quote)
    assert result.code == "fixed_quote_invalid"
    assert_fixed(result, baseline)


@pytest.mark.parametrize(
    "kind", ("hidden", "subclass", "crossed_book", "bad_chronology")
)
def test_quote_shape_and_causal_chain_are_revalidated(baseline, kind):
    quote = baseline["quote"]
    if kind == "hidden":
        quote = quote.model_copy(update={"hidden": True})
    elif kind == "subclass":
        quote = extended(quote)
    elif kind == "crossed_book":
        quote = quote.model_copy(update={"bid": quote.ask + D("0.01")})
    else:
        quote = quote.model_copy(
            update={"request_started_at": quote.received_at + timedelta(microseconds=1)}
        )
    result = evaluate(baseline, quote=quote)
    assert result.code == "fixed_quote_invalid"
    assert_fixed(result, baseline)


@pytest.mark.parametrize("value", (None, True, b"{}", ""))
def test_unidentifiable_audit_is_an_invalid_contract(baseline, value):
    with pytest.raises(FixedProtectionError):
        evaluate(baseline, original_selection_audit_json=value)


@pytest.mark.parametrize("side", ("original", "current"))
def test_caller_quality_is_neither_trusted_nor_walked(baseline, side):
    class Unreadable:
        def __iter__(self):
            pytest.fail("Caller quality must be ignored before reconstruction")

        def model_dump(self, *_args, **_kwargs):
            pytest.fail("Caller quality must not be serialized")

    market = baseline[f"{side}_market"].model_copy(update={"quality": Unreadable()})
    result = evaluate(baseline, **{f"{side}_market": market})
    assert result.passed, result
    assert_fixed(result, baseline)


@pytest.mark.parametrize("component", ("ticker", "order_book"))
def test_source_quote_age_uses_quote_policy_not_snapshot_policy(baseline, component):
    now = baseline["observed_at"] + timedelta(seconds=1)
    policy = baseline["data_policy"].model_copy(
        update={"maximum_snapshot_age_seconds": 60, "maximum_quote_age_seconds": 2}
    )
    market = baseline["current_market"].model_copy(deep=True)
    market.received_at = now
    other = "order_book" if component == "ticker" else "ticker"
    setattr(market, other, getattr(market, other).model_copy(update={"timestamp": now}))
    assert now - getattr(market, component).timestamp > timedelta(seconds=2)
    quote = baseline["quote"].model_copy(
        update={**dict.fromkeys(COMPONENTS, now), "request_started_at": now}
    )
    result = evaluate(
        baseline,
        **rebuild(baseline, market, now=now),
        data_policy=policy,
        quote=quote,
        observed_at=now,
    )
    assert result.code == "fixed_current_source_invalid", result
    assert result.checks[-1].step == "current_source"
    assert result.checks[0].passed and result.checks[1].passed


@pytest.mark.parametrize(
    ("failed_stage", "forbidden_function"),
    (
        ("original_source", "select_structural_protection"),
        ("current_source", "inspect_executable_quote"),
        ("quote", "atr"),
        ("current_noise", "_source_anchors"),
    ),
)
def test_genuine_failure_stops_later_computation(
    baseline, monkeypatch, failed_stage, forbidden_function
):
    def forbidden(*_args, **_kwargs):
        pytest.fail(f"Unexpected downstream {forbidden_function}")

    monkeypatch.setattr(module, forbidden_function, forbidden)
    updates = {}
    if failed_stage in {"original_source", "current_source"}:
        side = failed_stage.removesuffix("_source")
        market = baseline[f"{side}_market"].model_copy(deep=True)
        market.candles["5m"][-1] = market.candles["5m"][-1].model_copy(
            update={"confirmed": False}
        )
        updates[f"{side}_market"] = market
    elif failed_stage == "quote":
        updates["quote"] = baseline["quote"].model_copy(
            update={"funding_time": baseline["observed_at"] - timedelta(days=1)}
        )
    else:
        quote = baseline["quote"]
        updates["quote"] = quote.model_copy(update={"bid": quote.bid - D("0.1")})
    result = evaluate(baseline, **updates)
    assert not result.passed, result
    assert result.checks[-1].step == failed_stage
    assert_fixed(result, baseline)


@pytest.mark.parametrize("length", (1, 4, 7))
def test_truncated_all_passing_prefix_is_not_a_completed_or_failed_result(
    baseline, length
):
    result = evaluate(baseline)
    assert result.passed, result
    truncated = result.model_copy(
        update={"checks": result.checks[:length], "passed": False, "code": "passed"}
    )
    with pytest.raises((FixedProtectionError, ValueError, TypeError)):
        _ = truncated.evaluation_sha256
    with pytest.raises(ValidationError):
        FixedProtectionResult.model_validate_json(
            truncated.model_dump_json(), strict=True
        )


@pytest.mark.parametrize(
    "kind",
    (
        "depth",
        "duplicate",
        "noncanonical",
        "empty",
        "unknown_key",
        "float",
        "nan",
        "map_limit",
        "sequence_limit",
        "oversize",
    ),
)
def test_embedded_constraints_cannot_bypass_bounded_canonical_output_contract(
    baseline, kind
):
    result = evaluate(baseline)
    assert result.passed, result
    if kind == "depth":
        raw = '{"noise":' + "[" * 25 + "0" + "]" * 25 + "}"
    elif kind == "duplicate":
        raw = '{"noise":{},"noise":{}}'
    elif kind == "noncanonical":
        raw = " " + result.current_constraints_json
    elif kind == "empty":
        raw = "{}"
    elif kind == "unknown_key":
        raw = '{"caller_approval":true}'
    elif kind == "float":
        raw = '{"noise":{"caller_value":1.5}}'
    elif kind == "nan":
        raw = '{"noise":{"caller_value":NaN}}'
    elif kind == "map_limit":
        raw = json.dumps(
            {"noise": dict.fromkeys((str(i) for i in range(65)), 0)},
            sort_keys=True,
            separators=(",", ":"),
        )
    elif kind == "sequence_limit":
        raw = '{"noise":[' + ",".join(["0"] * 2049) + "]}"
    else:
        raw = "x" * (8 * 1024 * 1024 + 1)
    dirty = result.model_copy(update={"current_constraints_json": raw})
    with pytest.raises((FixedProtectionError, ValueError, TypeError)):
        _ = dirty.evaluation_sha256


def test_noise_exact_rational_trace_matches_original_prices_policy_and_fresh_spread(
    baseline,
):
    result = evaluate(baseline)
    assert result.passed, result
    noise = json.loads(result.current_constraints_json)["noise"]
    quote, policy = baseline["quote"], baseline["policy"]
    entry, volatility = Fraction(baseline["entry"]), Fraction(noise["atr"])
    expected = (
        max(
            entry * Fraction(policy.minimum_buffer_bps) / 10000,
            volatility * Fraction(policy.atr_buffer_multiplier),
        )
        + Fraction(quote.ask)
        - Fraction(quote.bid)
        + entry * Fraction(policy.expected_slippage_bps) / 10000
        + Fraction(baseline["tick_size"])
    )
    encoded = noise["required_buffer_exact"]
    actual = Fraction(int(encoded["numerator"]), int(encoded["denominator"]))
    assert actual == expected
    encoded_distance = noise["minimum_stop_distance_exact"]
    actual_distance = Fraction(
        int(encoded_distance["numerator"]), int(encoded_distance["denominator"])
    )
    assert actual_distance == volatility * Fraction(policy.min_stop_distance_atr)
    assert Fraction(noise["clearance"]) >= actual
    assert Fraction(noise["actual_stop_distance"]) >= actual_distance
