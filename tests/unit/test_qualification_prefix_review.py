"""Adversarial real-source G1--G7 replay, never fake passing gate fixtures."""

from datetime import timedelta, timezone
from decimal import ROUND_UP, Context, Decimal, Inexact, localcontext

import pytest
from pydantic import ValidationError, create_model

from app.trade_qualification import service as module
from app.trade_qualification.models import QualificationGate, QualificationState
from app.trade_qualification.service import (
    QualificationIntent,
    QualificationPrefixPolicy,
    QualificationPrefixRun,
    evaluate_qualification_prefix,
    verify_qualification_prefix,
)
from tests.unit.qualification_prefix_fixtures import MINIMUM_SCORE, prefix_source

D = Decimal
GATES = tuple(QualificationGate)[:7]
AUTHORITY_FIELDS = (
    "execution_authority",
    "source_authenticity_verified",
    "event_ledger_verified",
    "execution_recheck_performed",
)
LATER_FUNCTIONS = (
    "route_regime",
    "assess_conditions",
    "extract_trigger",
    "evaluate_timing",
    "build_entry_zone",
    "evaluate_location",
)


@pytest.fixture(scope="module", params=("long", "short"))
def source(request):
    return prefix_source(request.param)


def arguments(source):
    return {
        "intent": QualificationIntent(
            report_id=source.report_id,
            instrument_id=source.market.instrument_id,
            strategy=source.strategy,
            direction=source.direction,
            candidate_entry=source.market.ticker.last,
            created_at=source.evaluated_at,
            expires_at=source.evaluated_at + timedelta(minutes=5),
        ),
        "quote": source.quote,
        "reference": source.reference,
        "policy": QualificationPrefixPolicy(
            policy_id="synthetic-prefix-review-policy",
            data=source.policy,
            minimum_score=MINIMUM_SCORE,
            tick_size=source.tick_size,
            max_allowed_drift_bps=source.maximum_drift_bps,
        ),
        "consumed_event_keys": frozenset(),
        "evaluated_at": source.evaluated_at,
    }


def evaluate(source, *, market=None, **updates):
    inputs = arguments(source)
    inputs.update(updates)
    return evaluate_qualification_prefix(
        source.market if market is None else market, **inputs
    )


@pytest.fixture(scope="module")
def baseline(source):
    result = evaluate(source)
    assert result.prefix_complete, result.result.fail_codes
    return result


def forbid(monkeypatch, *names):
    def forbidden(*_args, **_kwargs):
        pytest.fail("a downstream stage was called after an earlier failure")

    for name in names:
        monkeypatch.setattr(module, name, forbidden)


def assert_stopped(run, count, code):
    assert tuple(item.gate for item in run.result.gates) == GATES[:count]
    assert all(item.passed for item in run.result.gates[:-1])
    assert not run.result.gates[-1].passed
    assert run.result.gates[-1].code == code
    assert run.result.failed_gates == (GATES[count - 1],)
    assert not run.prefix_complete
    assert not run.result.qualified
    assert all(getattr(run, name) is False for name in AUTHORITY_FIELDS)
    assert (run.detection is not None) == (count >= 5)
    assert (run.timing is not None) == (count >= 6)
    if count < 7:
        assert run.location is None
        assert run.result.entry_zone is None


def test_real_prefix_has_exact_seven_gates_without_later_permissions(source, baseline):
    assert tuple(item.gate for item in baseline.result.gates) == GATES
    assert all(item.passed for item in baseline.result.gates)
    assert baseline.result.state == QualificationState.ENTRY_LOCATION_VALID
    assert (
        baseline.result.candidate_entry == arguments(source)["intent"].candidate_entry
    )
    assert baseline.result.entry_zone is not None
    assert baseline.location.passed
    assert baseline.timing.timing_valid
    assert baseline.detection.trigger is not None
    assert baseline.result.trigger == baseline.detection.trigger
    assert all(getattr(baseline, name) is False for name in AUTHORITY_FIELDS)
    assert not baseline.data_result.execution_authority
    assert not baseline.data_result.source_authenticity_verified
    assert not baseline.location.execution_authority
    for name in (
        "qualified",
        "risk_permission",
        "evidence_complete",
        "execution_recheck_passed",
    ):
        assert getattr(baseline.result, name) is False
    for name in ("stop_loss", "take_profit", "gross_rr", "net_rr"):
        assert getattr(baseline.result, name) is None
    assert not any(
        item.gate in tuple(QualificationGate)[7:] for item in baseline.result.gates
    )


def test_valid_prefix_roundtrip_and_full_replay_are_identical(source, baseline):
    restored = QualificationPrefixRun.model_validate_json(
        baseline.model_dump_json(round_trip=True)
    )
    assert restored == baseline
    assert restored.evaluation_sha256 == baseline.evaluation_sha256
    assert (
        verify_qualification_prefix(baseline, source.market, **arguments(source))
        == baseline
    )


def test_evaluation_does_not_mutate_any_supplied_input(source):
    inputs = arguments(source)
    before = (
        source.market.model_dump_json(round_trip=True),
        *(
            inputs[name].model_dump_json(round_trip=True)
            for name in ("intent", "quote", "reference", "policy")
        ),
    )
    result = evaluate_qualification_prefix(source.market, **inputs)
    after = (
        source.market.model_dump_json(round_trip=True),
        *(
            inputs[name].model_dump_json(round_trip=True)
            for name in ("intent", "quote", "reference", "policy")
        ),
    )
    assert result.prefix_complete
    assert before == after
    assert inputs["consumed_event_keys"] == frozenset()


@pytest.mark.parametrize(
    "field,value",
    [
        ("report_id", "../bad"),
        ("strategy", "unregistered"),
        ("direction", "neutral"),
        ("direction", ["long"]),
        ("candidate_entry", 100.0),
        ("candidate_entry", "100"),
        ("candidate_entry", D("NaN")),
        ("candidate_entry", D("Infinity")),
        ("candidate_entry", D("1e999999")),
        ("candidate_entry", D("1e-999999")),
        ("candidate_entry", D(0)),
        ("hidden", True),
    ],
)
def test_dirty_intent_raises_before_data_stage(source, monkeypatch, field, value):
    forbid(monkeypatch, "evaluate_data", *LATER_FUNCTIONS)
    intent = arguments(source)["intent"].model_copy(update={field: value})
    with pytest.raises((ValueError, TypeError)):
        evaluate(source, intent=intent)


@pytest.mark.parametrize(
    "field,value",
    [
        ("minimum_score", True),
        ("minimum_score", 101),
        ("tick_size", D(0)),
        ("tick_size", 0.01),
        ("max_allowed_drift_bps", D(-1)),
        ("maximum_strategy_spread_bps", D("8.00000000000000000001")),
        ("maximum_adverse_funding_bps", D("15.00000000000000000001")),
        ("hidden", True),
    ],
)
def test_dirty_policy_raises_before_data_stage(source, monkeypatch, field, value):
    forbid(monkeypatch, "evaluate_data", *LATER_FUNCTIONS)
    policy = arguments(source)["policy"].model_copy(update={field: value})
    with pytest.raises((ValueError, TypeError)):
        evaluate(source, policy=policy)


@pytest.mark.parametrize(
    "defect", ["nested_hidden", "nested_nonfinite", "missing_field", "mapping"]
)
def test_nested_or_structurally_dirty_policy_cannot_be_serialized_clean(
    source, monkeypatch, defect
):
    forbid(monkeypatch, "evaluate_data", *LATER_FUNCTIONS)
    policy = arguments(source)["policy"]
    if defect == "nested_hidden":
        policy = policy.model_copy(
            update={"data": policy.data.model_copy(update={"hidden": True})}
        )
    elif defect == "nested_nonfinite":
        policy = policy.model_copy(
            update={
                "data": policy.data.model_copy(update={"maximum_spread_bps": D("NaN")})
            }
        )
    elif defect == "missing_field":
        policy = policy.model_copy()
        policy.__dict__.pop("tick_size")
    else:
        policy = policy.model_dump(round_trip=True)
    with pytest.raises((ValueError, TypeError)):
        evaluate(source, policy=policy)


@pytest.mark.parametrize(
    "ledger",
    [
        None,
        set(),
        [],
        (),
        frozenset({""}),
        frozenset({"a" * 63}),
        frozenset({"G" * 64}),
        frozenset({1}),
        frozenset(f"{i:064x}" for i in range(10001)),
    ],
    ids=[
        "missing",
        "set",
        "list",
        "tuple",
        "empty_key",
        "short_key",
        "uppercase",
        "number",
        "oversized",
    ],
)
def test_consumed_ledger_requires_bounded_exact_frozenset(source, monkeypatch, ledger):
    forbid(monkeypatch, "evaluate_data", *LATER_FUNCTIONS)
    with pytest.raises((ValueError, TypeError)):
        evaluate(source, consumed_event_keys=ledger)


def test_consumed_ledger_generator_is_not_consumed(source, monkeypatch):
    forbid(monkeypatch, "evaluate_data", *LATER_FUNCTIONS)
    touched = []

    def infinite():
        touched.append(True)
        while True:
            yield "a" * 64

    with pytest.raises((ValueError, TypeError)):
        evaluate(source, consumed_event_keys=infinite())
    assert not touched


@pytest.mark.parametrize("defect", ["missing", "naive", "text", "number"])
def test_evaluation_clock_must_be_explicit_aware_datetime(source, monkeypatch, defect):
    forbid(monkeypatch, "evaluate_data", *LATER_FUNCTIONS)
    value = {
        "missing": None,
        "naive": source.evaluated_at.replace(tzinfo=None),
        "text": source.evaluated_at.isoformat(),
        "number": 123,
    }[defect]
    with pytest.raises((ValueError, TypeError)):
        evaluate(source, evaluated_at=value)


@pytest.mark.parametrize(
    "defect", ["market_hidden", "quote_missing", "quote_hidden", "reference_missing"]
)
def test_real_g1_failure_never_calls_route_or_any_later_stage(
    source, monkeypatch, defect
):
    forbid(monkeypatch, *LATER_FUNCTIONS)
    updates = {}
    market = source.market
    code = "quote_provenance_invalid"
    if defect == "market_hidden":
        market = market.model_copy(update={"hidden": True})
        code = "market_source_invalid"
    elif defect == "quote_missing":
        updates["quote"] = None
    elif defect == "quote_hidden":
        updates["quote"] = source.quote.model_copy(update={"hidden": True})
    else:
        updates["reference"] = None
        code = "reference_source_missing"
    assert_stopped(evaluate(source, market=market, **updates), 1, code)


@pytest.mark.parametrize(
    "strategy",
    [
        "range_reversal",
        "structure_reversal",
        "liquidity_sweep_reversal",
        "volatility_expansion",
    ],
)
def test_real_g2_disallowed_strategy_never_calls_conditions(
    source, monkeypatch, strategy
):
    forbid(monkeypatch, *LATER_FUNCTIONS[1:])
    intent = arguments(source)["intent"].model_copy(update={"strategy": strategy})
    assert_stopped(evaluate(source, intent=intent), 2, "regime_strategy_not_allowed")


def test_real_g3_direction_mismatch_stops_before_trigger(source, monkeypatch):
    forbid(monkeypatch, *LATER_FUNCTIONS[2:])
    direction = "short" if source.direction == "long" else "long"
    intent = arguments(source)["intent"].model_copy(update={"direction": direction})
    assert_stopped(evaluate(source, intent=intent), 3, "htf_strategy_permission_denied")


def test_real_g4_missing_fvg_cannot_be_compensated_by_other_conditions(
    source, monkeypatch
):
    forbid(monkeypatch, *LATER_FUNCTIONS[2:])
    rows = [
        row.model_copy(
            update={
                "open": D(100),
                "close": D(100),
                "high": D("100.1"),
                "low": D("99.9"),
            }
        )
        for row in source.market.candles["15m"]
    ]
    market = source.market.model_copy(
        update={"candles": {**source.market.candles, "15m": rows}}
    )
    run = evaluate(source, market=market)
    assert_stopped(run, 4, "required_setup_missing")
    assert "15m_fvg" in run.result.gates[-1].measured_values["required_failures"]
    assert run.result.raw_score > 0
    assert run.result.effective_score == 0


def test_consumed_event_stops_at_g6_without_building_a_zone(
    source, baseline, monkeypatch
):
    forbid(monkeypatch, "build_entry_zone", "evaluate_location")
    ledger = frozenset({baseline.timing.event_key})
    run = evaluate(source, consumed_event_keys=ledger)
    assert_stopped(run, 6, "stale_candidate")
    assert run.timing.event_key == baseline.timing.event_key
    assert ledger == frozenset({baseline.timing.event_key})


def test_report_rename_does_not_restore_consumed_event(source, baseline, monkeypatch):
    renamed = prefix_source(source.direction, report_id=source.report_id + "-renamed")
    forbid(monkeypatch, "build_entry_zone", "evaluate_location")
    run = evaluate(renamed, consumed_event_keys=frozenset({baseline.timing.event_key}))
    assert_stopped(run, 6, "stale_candidate")
    assert run.intent.report_id != baseline.intent.report_id
    assert run.timing.event_key == baseline.timing.event_key


def test_expired_candidate_is_not_refreshed_by_a_current_quote(source, monkeypatch):
    forbid(monkeypatch, "build_entry_zone", "evaluate_location")
    intent = arguments(source)["intent"].model_copy(
        update={
            "created_at": source.evaluated_at - timedelta(minutes=1),
            "expires_at": source.evaluated_at,
        }
    )
    assert_stopped(evaluate(source, intent=intent), 6, "stale_candidate")


def test_location_rejects_outside_candidate_instead_of_repricing(source, baseline):
    zone = baseline.result.entry_zone
    requested = (
        zone.zone_high + D(1) if source.direction == "long" else zone.zone_low - D(1)
    )
    intent = arguments(source)["intent"].model_copy(
        update={"candidate_entry": requested}
    )
    run = evaluate(source, intent=intent)
    assert_stopped(run, 7, "candidate_outside_entry_zone")
    assert run.intent.candidate_entry == run.result.candidate_entry == requested
    assert run.result.reference_price != requested
    assert run.result.entry_zone == zone


def test_valid_nearby_candidate_is_not_silently_replaced_with_executable_price(
    source, baseline
):
    original = baseline.intent.candidate_entry
    requested = (
        original - source.tick_size
        if source.direction == "long"
        else original + source.tick_size
    )
    intent = arguments(source)["intent"].model_copy(
        update={"candidate_entry": requested}
    )
    run = evaluate(source, intent=intent)
    assert run.prefix_complete
    assert run.intent.candidate_entry == run.result.candidate_entry == requested
    assert run.result.reference_price == baseline.result.reference_price != requested
    assert run.location.drift_bps > 0


@pytest.mark.parametrize("field", AUTHORITY_FIELDS)
@pytest.mark.parametrize("value", [True, 1, "false"])
def test_authority_flags_cannot_be_coerced_or_tampered(source, baseline, field, value):
    altered = baseline.model_copy(update={field: value})
    with pytest.raises((ValueError, TypeError)):
        _ = altered.evaluation_sha256
    with pytest.raises((ValueError, TypeError)):
        verify_qualification_prefix(altered, source.market, **arguments(source))


@pytest.mark.parametrize(
    "target",
    [
        "run",
        "intent",
        "policy",
        "data_policy",
        "data_result",
        "data_gate",
        "result",
        "gate",
        "detection",
        "trigger",
        "timing",
        "location",
        "zone",
    ],
)
def test_hidden_nested_fields_are_rejected_before_replay(
    source, baseline, monkeypatch, target
):
    if target == "run":
        altered = baseline.model_copy(update={"hidden": True})
    elif target == "data_policy":
        policy = baseline.policy.model_copy(
            update={"data": baseline.policy.data.model_copy(update={"hidden": True})}
        )
        altered = baseline.model_copy(update={"policy": policy})
    elif target == "data_gate":
        data = baseline.data_result.model_copy(
            update={
                "gate": baseline.data_result.gate.model_copy(update={"hidden": True})
            }
        )
        altered = baseline.model_copy(update={"data_result": data})
    elif target == "gate":
        gates = (
            baseline.result.gates[0].model_copy(update={"hidden": True}),
            *baseline.result.gates[1:],
        )
        altered = baseline.model_copy(
            update={"result": baseline.result.model_copy(update={"gates": gates})}
        )
    elif target == "trigger":
        detection = baseline.detection.model_copy(
            update={
                "trigger": baseline.detection.trigger.model_copy(
                    update={"hidden": True}
                )
            }
        )
        altered = baseline.model_copy(update={"detection": detection})
    elif target == "zone":
        result = baseline.result.model_copy(
            update={
                "entry_zone": baseline.result.entry_zone.model_copy(
                    update={"hidden": True}
                )
            }
        )
        altered = baseline.model_copy(update={"result": result})
    else:
        altered = baseline.model_copy(
            update={
                target: getattr(baseline, target).model_copy(update={"hidden": True})
            }
        )
    forbid(monkeypatch, "evaluate_qualification_prefix")
    with pytest.raises((ValueError, TypeError)):
        _ = altered.evaluation_sha256
    with pytest.raises((ValueError, TypeError)):
        verify_qualification_prefix(altered, source.market, **arguments(source))


@pytest.mark.parametrize(
    "field,value",
    [
        ("stop_loss", D(1)),
        ("take_profit", D(200)),
        ("gross_rr", D(2)),
        ("net_rr", D(2)),
    ],
)
def test_prefix_cannot_acquire_later_protection_or_economics(
    source, baseline, field, value
):
    altered = baseline.model_copy(
        update={"result": baseline.result.model_copy(update={field: value})}
    )
    with pytest.raises((ValueError, TypeError)):
        _ = altered.evaluation_sha256
    with pytest.raises((ValueError, TypeError)):
        verify_qualification_prefix(altered, source.market, **arguments(source))


@pytest.mark.parametrize(
    "defect",
    ["missing_gate", "duplicate_gate", "reversed_gates", "raw_score", "missing_field"],
)
def test_malformed_prefix_records_never_gain_a_verified_hash(source, baseline, defect):
    result = baseline.result
    if defect == "missing_gate":
        result = result.model_copy(update={"gates": result.gates[:-1]})
    elif defect == "duplicate_gate":
        result = result.model_copy(
            update={"gates": (*result.gates[:-1], result.gates[-2])}
        )
    elif defect == "reversed_gates":
        result = result.model_copy(update={"gates": tuple(reversed(result.gates))})
    elif defect == "raw_score":
        result = result.model_copy(update={"raw_score": 101})
    else:
        result = result.model_copy()
        result.__dict__.pop("direction")
    altered = baseline.model_copy(update={"result": result})
    with pytest.raises((ValueError, TypeError)):
        _ = altered.evaluation_sha256
    with pytest.raises((ValueError, TypeError)):
        verify_qualification_prefix(altered, source.market, **arguments(source))


@pytest.mark.parametrize(
    "changed", ["clock", "policy", "ledger", "reference", "intent_lifetime", "source"]
)
def test_replay_requires_the_original_complete_input_set(source, baseline, changed):
    inputs = arguments(source)
    market = source.market
    if changed == "clock":
        inputs["evaluated_at"] += timedelta(microseconds=1)
    elif changed == "policy":
        inputs["policy"] = inputs["policy"].model_copy(
            update={"policy_id": "synthetic-other-policy"}
        )
    elif changed == "ledger":
        inputs["consumed_event_keys"] = frozenset({"a" * 64})
    elif changed == "reference":
        inputs["reference"] = source.reference.model_copy(
            update={"bid": source.reference.bid - D("0.001")}
        )
    elif changed == "intent_lifetime":
        inputs["intent"] = inputs["intent"].model_copy(
            update={
                "expires_at": inputs["intent"].expires_at + timedelta(microseconds=1)
            }
        )
    else:
        rows = list(source.market.candles["5m"])
        rows[0] = rows[0].model_copy(
            update={"volume_quote": rows[0].volume_quote + D(1)}
        )
        market = source.market.model_copy(
            update={"candles": {**source.market.candles, "5m": rows}}
        )
    with pytest.raises(ValueError, match="qualification_prefix_replay_mismatch"):
        verify_qualification_prefix(baseline, market, **inputs)


def test_result_and_measured_records_are_frozen(baseline):
    with pytest.raises(ValidationError):
        baseline.execution_authority = True
    with pytest.raises(ValidationError):
        baseline.intent.candidate_entry = D(1)
    with pytest.raises(ValidationError):
        baseline.result.gates[0].passed = False
    with pytest.raises(TypeError):
        baseline.result.gates[0].measured_values["execution_authority"] = True


def test_hostile_decimal_context_and_equivalent_timezone_preserve_the_prefix(
    source, baseline
):
    context = Context(prec=6, rounding=ROUND_UP)
    context.traps[Inexact] = True
    with localcontext(context):
        result = evaluate(
            source,
            evaluated_at=source.evaluated_at.astimezone(timezone(timedelta(hours=8))),
        )
        assert result == baseline
        assert result.evaluation_sha256 == baseline.evaluation_sha256


def extended_record(record):
    extended = create_model(
        "SyntheticDeclaredExtra" + type(record).__name__,
        __base__=type(record),
        hidden_declared=(bool, True),
    )
    return extended.model_validate(record.model_dump(round_trip=True), strict=True)


def test_nested_data_policy_subclass_cannot_drop_declared_extra_before_g1(
    source, monkeypatch
):
    policy = arguments(source)["policy"]
    policy = policy.model_copy(update={"data": extended_record(policy.data)})
    forbid(monkeypatch, "evaluate_data", *LATER_FUNCTIONS)
    with pytest.raises((ValueError, TypeError)):
        evaluate(source, policy=policy)


@pytest.mark.parametrize(
    "target",
    ["intent", "policy", "data_result", "result", "detection", "timing", "location"],
)
def test_nested_result_subclass_declared_extra_is_not_omitted_from_hash_or_replay(
    source, baseline, monkeypatch, target
):
    altered = baseline.model_copy(
        update={target: extended_record(getattr(baseline, target))}
    )
    forbid(monkeypatch, "evaluate_qualification_prefix")
    with pytest.raises((ValueError, TypeError)):
        _ = altered.evaluation_sha256
    with pytest.raises((ValueError, TypeError)):
        verify_qualification_prefix(altered, source.market, **arguments(source))
