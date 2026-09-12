"""Synthetic actual-evaluator prefix tests; no network, samples, or orders."""

import hashlib
import json
from datetime import timedelta, timezone
from decimal import ROUND_DOWN, Context, Decimal, Inexact, localcontext

import pytest

from app.strategies import base
from app.trade_qualification import service as module
from app.trade_qualification.models import QualificationGate
from app.trade_qualification.service import (
    QualificationIntent,
    QualificationPrefixPolicy,
    QualificationPrefixRun,
    evaluate_qualification_prefix,
    verify_qualification_prefix,
)
from tests.unit.qualification_prefix_fixtures import prefix_source

D = Decimal
ORDER = tuple(QualificationGate)[:7]


@pytest.fixture(scope="module", params=("long", "short"))
def source(request):
    return prefix_source(request.param)


def inputs(source, **updates):
    args = {
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
            policy_id="synthetic-prefix-policy",
            data=source.policy,
            minimum_score=85,
            tick_size=source.tick_size,
            max_allowed_drift_bps=source.maximum_drift_bps,
        ),
        "quote": source.quote,
        "reference": source.reference,
        "consumed_event_keys": frozenset(),
        "evaluated_at": source.evaluated_at,
    }
    args.update(updates)
    return args


def check(source, *, market=None, **updates):
    return evaluate_qualification_prefix(
        source.market if market is None else market, **inputs(source, **updates)
    )


def forbidden(*args, **kwargs):
    pytest.fail("evaluation continued after the first failure or used a legacy path")


def test_real_seven_gate_path_is_not_order_eligible(source):
    before = source.market.model_dump_json(round_trip=True)
    quote_before = source.quote.model_dump_json(round_trip=True)
    result = check(source)
    assert result.prefix_complete
    assert tuple(g.gate for g in result.result.gates) == ORDER
    assert all(g.code == "passed" and g.measured_values for g in result.result.gates)
    assert result.result.raw_score == result.result.effective_score == 100
    assert not result.result.qualified
    assert result.result.state.value == "ENTRY_LOCATION_VALID"
    assert not any(
        (
            result.execution_authority,
            result.source_authenticity_verified,
            result.event_ledger_verified,
            result.execution_recheck_performed,
            result.result.risk_permission,
            result.result.evidence_complete,
        )
    )
    assert result.detection.source_sha256 == result.data_result.source_sha256
    assert result.result.entry_zone.source_sha256 == result.data_result.source_sha256
    assert result.result.candidate_entry == source.market.ticker.last
    assert result.result.reference_price == (
        source.quote.quote.ask if source.direction == "long" else source.quote.quote.bid
    )
    assert result.result.entry_zone.expires_at <= result.timing.latest_valid_entry_time
    assert result.result.stop_loss is None and result.result.take_profit is None
    assert result.result.gross_rr is None and result.result.net_rr is None
    assert source.market.model_dump_json(round_trip=True) == before
    assert source.quote.model_dump_json(round_trip=True) == quote_before
    assert (
        verify_qualification_prefix(result, source.market, **inputs(source)) == result
    )
    restored = QualificationPrefixRun.model_validate_json(
        result.model_dump_json(round_trip=True)
    )
    assert restored == result and restored.evaluation_sha256 == result.evaluation_sha256


def test_g1_failure_stops_before_router(source, monkeypatch):
    monkeypatch.setattr(module, "route_regime", forbidden)
    result = check(source, reference=None)
    assert result.result.fail_codes == ("reference_source_missing",)
    assert len(result.result.gates) == 1
    assert result.detection is result.timing is result.location is None


@pytest.mark.parametrize(
    "strategy",
    (
        "liquidity_sweep_reversal",
        "structure_reversal",
        "volatility_expansion",
        "range_reversal",
    ),
)
def test_unadmitted_family_stops_g2_before_conditions(source, monkeypatch, strategy):
    monkeypatch.setattr(module, "assess_conditions", forbidden)
    intent = inputs(source)["intent"].model_copy(update={"strategy": strategy})
    result = check(source, intent=intent)
    assert len(result.result.gates) == 2
    assert result.result.fail_codes == ("regime_strategy_not_allowed",)
    assert result.result.raw_score == result.result.effective_score == 0


def test_requested_direction_must_match_actual_strategy(source, monkeypatch):
    monkeypatch.setattr(module, "extract_trigger", forbidden)
    intent = inputs(source)["intent"].model_copy(
        update={"direction": "short" if source.direction == "long" else "long"}
    )
    result = check(source, intent=intent)
    assert len(result.result.gates) == 3
    assert result.result.fail_codes == ("htf_strategy_permission_denied",)
    assert result.result.effective_score == 0


def change_momentum_bar(source, offset, long_price):
    market = source.market.model_copy(deep=True)
    price = D(long_price) if source.direction == "long" else D(200) - D(long_price)
    row = market.candles["5m"][offset]
    market.candles["5m"][offset] = row.model_copy(
        update={
            "open": price,
            "close": price,
            "low": price - D("0.01"),
            "high": price + D("0.01"),
        }
    )
    return market


def test_missing_current_momentum_stops_g4_before_historical_event(source, monkeypatch):
    market = change_momentum_bar(source, -1, "100.79")
    monkeypatch.setattr(module, "extract_trigger", forbidden)
    result = check(source, market=market)
    assert len(result.result.gates) == 4
    assert result.result.fail_codes == ("required_setup_missing",)
    assert "5m_trigger" in result.result.gates[-1].measured_values["required_failures"]
    assert result.result.setup_state == "invalid" and result.result.effective_score == 0


def test_score_100_current_momentum_without_new_event_stops_g5(source, monkeypatch):
    market = change_momentum_bar(source, -2, "100.89")
    monkeypatch.setattr(module, "evaluate_timing", forbidden)
    result = check(source, market=market)
    assert len(result.result.gates) == 5
    assert result.result.raw_score == result.result.effective_score == 100
    assert result.result.setup_state == "valid"
    assert result.result.fail_codes == ("trigger_missing",)
    assert result.detection.trigger is None
    assert not result.prefix_complete and not result.result.qualified


@pytest.mark.parametrize(
    "mutation,code",
    (
        ("future", "entry_window_not_open"),
        ("expired", "stale_candidate"),
        ("before_trigger", "stale_candidate"),
        ("consumed", "stale_candidate"),
    ),
)
def test_timing_failure_stops_before_build_zone(source, monkeypatch, mutation, code):
    baseline = check(source)
    args = inputs(source)
    intent = args["intent"]
    if mutation == "future":
        intent = intent.model_copy(
            update={"created_at": source.evaluated_at + timedelta(seconds=1)}
        )
    elif mutation == "expired":
        intent = intent.model_copy(
            update={
                "created_at": source.evaluated_at - timedelta(seconds=1),
                "expires_at": source.evaluated_at,
            }
        )
    elif mutation == "before_trigger":
        intent = intent.model_copy(
            update={
                "created_at": baseline.detection.trigger.trigger_time
                - timedelta(microseconds=1)
            }
        )
    else:
        args["consumed_event_keys"] = frozenset({baseline.timing.event_key})
    args["intent"] = intent
    monkeypatch.setattr(module, "build_entry_zone", forbidden)
    result = evaluate_qualification_prefix(source.market, **args)
    assert len(result.result.gates) == 6
    assert result.result.fail_codes == (code,)
    assert result.result.entry_timing_state == (
        "wait" if mutation == "future" else "cancel"
    )
    assert result.location is None


def test_missing_ticks_fail_g7_without_location(source, monkeypatch):
    policy = inputs(source)["policy"].model_copy(update={"tick_size": D(1000)})
    monkeypatch.setattr(module, "evaluate_location", forbidden)
    result = check(source, policy=policy)
    assert len(result.result.gates) == 7
    assert result.result.fail_codes == ("no_executable_zone",)
    assert result.location is None and result.result.entry_zone is None


def test_original_candidate_is_not_repriced_into_zone(source):
    price = D(102) if source.direction == "long" else D(98)
    intent = inputs(source)["intent"].model_copy(update={"candidate_entry": price})
    result = check(source, intent=intent)
    assert len(result.result.gates) == 7
    assert result.result.fail_codes == ("candidate_outside_entry_zone",)
    assert result.result.candidate_entry == result.intent.candidate_entry == price
    assert result.result.reference_price != price
    assert not result.prefix_complete and not result.result.qualified


def test_drift_is_independent_of_zone_and_score(source):
    offset = D("-0.01") if source.direction == "long" else D("0.01")
    intent = inputs(source)["intent"].model_copy(
        update={"candidate_entry": source.market.ticker.last + offset}
    )
    policy = inputs(source)["policy"].model_copy(update={"max_allowed_drift_bps": D(0)})
    result = check(source, intent=intent, policy=policy)
    assert result.result.raw_score == 100
    assert result.result.fail_codes == ("entry_drift_exceeds_limit",)


def test_does_not_read_legacy_funding_or_mark_as_current(source):
    market = source.market.model_copy(
        update={
            "funding_rate": D(999),
            "mark_price": D(1),
            "next_funding_time": source.evaluated_at - timedelta(days=1),
        }
    )
    result = check(source, market=market)
    assert result.prefix_complete
    gate = result.result.gates[3]
    expected = source.quote.quote.funding_rate * (
        D(10000) if source.direction == "long" else D(-10000)
    )
    assert gate.measured_values["fresh_signed_funding_cost_bps"] == expected
    assert result.data_result.source_sha256 != check(source).data_result.source_sha256


def test_never_uses_caller_quality_or_legacy_candidate(source, monkeypatch):
    class Unreadable:
        def __iter__(self):
            forbidden()

    monkeypatch.setattr(base, "build_candidate", forbidden)
    monkeypatch.setattr(base, "common_vetoes", forbidden)
    monkeypatch.setattr(base, "evaluate_conditions", forbidden)
    market = source.market.model_copy(update={"quality": Unreadable()})
    assert check(source, market=market).prefix_complete


@pytest.mark.parametrize("precision", (6, 17, 55))
def test_ambient_decimal_context_does_not_change_run(source, precision):
    baseline = check(source)
    with localcontext(Context(prec=precision, rounding=ROUND_DOWN)) as ctx:
        ctx.traps[Inexact] = True
        result = check(source)
        assert result == baseline
        assert result.evaluation_sha256 == baseline.evaluation_sha256


def test_equivalent_timezone_normalizes_to_same_run(source):
    args = inputs(source)
    tz = timezone(timedelta(hours=8))
    args["evaluated_at"] = source.evaluated_at.astimezone(tz)
    args["intent"] = args["intent"].model_copy(
        update={
            "created_at": args["intent"].created_at.astimezone(tz),
            "expires_at": args["intent"].expires_at.astimezone(tz),
        }
    )
    result = evaluate_qualification_prefix(source.market, **args)
    assert result.evaluation_sha256 == check(source).evaluation_sha256


@pytest.mark.parametrize("mutation", ("source", "policy", "intent", "time", "ledger"))
def test_replay_rejects_changed_original_inputs(source, mutation):
    original = check(source)
    args = inputs(source)
    market = source.market
    if mutation == "source":
        market = market.model_copy(update={"open_interest_contracts": D(101)})
    elif mutation == "policy":
        args["policy"] = args["policy"].model_copy(update={"policy_id": "changed"})
    elif mutation == "intent":
        args["intent"] = args["intent"].model_copy(
            update={"expires_at": source.evaluated_at + timedelta(minutes=4)}
        )
    elif mutation == "time":
        args["evaluated_at"] += timedelta(microseconds=1)
    else:
        args["consumed_event_keys"] = frozenset({"a" * 64})
    with pytest.raises(ValueError, match="qualification_prefix_replay_mismatch"):
        verify_qualification_prefix(original, market, **args)


def test_self_signed_source_cannot_replace_original(source):
    original = check(source)
    data = original.data_result.model_copy(
        update={"source_json": "{}", "source_sha256": hashlib.sha256(b"{}").hexdigest()}
    )
    forged = original.model_copy(update={"data_result": data})
    with pytest.raises(ValueError):
        verify_qualification_prefix(forged, source.market, **inputs(source))


@pytest.mark.parametrize(
    "target", ("run", "result", "gate", "data", "policy", "intent", "timing")
)
def test_hidden_model_copy_fields_rejected_before_replay(source, target):
    original = check(source)
    if target == "run":
        forged = original.model_copy(update={"grant": True})
    elif target == "gate":
        gates = (
            original.result.gates[0].model_copy(update={"grant": True}),
            *original.result.gates[1:],
        )
        forged = original.model_copy(
            update={"result": original.result.model_copy(update={"gates": gates})}
        )
    else:
        name = "data_result" if target == "data" else target
        forged = original.model_copy(
            update={name: getattr(original, name).model_copy(update={"grant": True})}
        )
    with pytest.raises(ValueError, match="hidden or missing"):
        verify_qualification_prefix(forged, source.market, **inputs(source))


@pytest.mark.parametrize(
    "flag",
    (
        "execution_authority",
        "source_authenticity_verified",
        "event_ledger_verified",
        "execution_recheck_performed",
    ),
)
@pytest.mark.parametrize("value", (True, 0, 1, "false"))
def test_prefix_authority_flags_cannot_be_forged(source, flag, value):
    result = check(source).model_copy(update={flag: value})
    with pytest.raises(ValueError):
        verify_qualification_prefix(result, source.market, **inputs(source))


@pytest.mark.parametrize(
    "keys",
    (
        None,
        [],
        set(),
        ("a" * 64,),
        frozenset({"A" * 64}),
        frozenset({"x"}),
        frozenset({True}),
    ),
)
def test_consumed_event_input_is_explicit_bounded_and_exact(source, keys):
    with pytest.raises(ValueError):
        check(source, consumed_event_keys=keys)


@pytest.mark.parametrize(
    "field,value",
    (
        ("candidate_entry", 100),
        ("candidate_entry", D("NaN")),
        ("candidate_entry", D("1E100000")),
        ("candidate_entry", D("1." + "0" * 5000)),
        ("direction", "neutral"),
        ("strategy", "unknown"),
        ("report_id", "../escape"),
    ),
)
def test_invalid_intent_copy_cannot_reach_data(source, monkeypatch, field, value):
    intent = inputs(source)["intent"].model_copy(update={field: value})
    monkeypatch.setattr(module, "evaluate_data", forbidden)
    with pytest.raises(ValueError):
        check(source, intent=intent)


@pytest.mark.parametrize(
    "field,value",
    (
        ("minimum_score", True),
        ("minimum_score", 101),
        ("tick_size", D(0)),
        ("tick_size", D("1E100000")),
        ("maximum_strategy_spread_bps", D("8.00001")),
        ("maximum_adverse_funding_bps", D("15.00001")),
    ),
)
def test_policy_cannot_loosen_strategy_limits(source, monkeypatch, field, value):
    policy = inputs(source)["policy"].model_copy(update={field: value})
    monkeypatch.setattr(module, "evaluate_data", forbidden)
    with pytest.raises(ValueError):
        check(source, policy=policy)


def test_json_computed_qualifications_cannot_be_submitted_as_inputs(source):
    payload = json.loads(check(source).model_dump_json(round_trip=True))
    payload["result"]["qualified"] = True
    with pytest.raises(ValueError):
        QualificationPrefixRun.model_validate_json(json.dumps(payload))
