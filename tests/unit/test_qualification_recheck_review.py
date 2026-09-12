"""Independent R1–R4 checks; never actual publication or complete recheck proof.

Original gates, snapshots, prices and subsequent OHLC are genuinely evaluated.
Only the publication dependency is a named synthetic receipt-contract double.
No production dependency is replaced with PASS. Intrabar uncertainty remains.
"""

import asyncio
import hashlib
import json
from datetime import timedelta
from decimal import Context, Decimal, Inexact, Rounded, localcontext
from pathlib import Path

import pytest
from pydantic import create_model, model_serializer

from app.trade_evidence import gates
from app.trade_qualification import current_conditions as conditions_module
from app.trade_qualification import recheck as recheck_module
from app.trade_qualification.continuation import (
    ContinuationResult,
    evaluate_continuation,
    verify_continuation,
)
from app.trade_qualification.current_conditions import (
    CurrentConditionsResult,
    copy_current_conditions,
    evaluate_current_conditions,
    verify_current_conditions,
)
from app.trade_qualification.current_risk import (
    CurrentRiskResult,
    evaluate_current_risk,
    verify_current_risk,
)
from app.trade_qualification.fixed_protection import (
    FixedProtectionResult,
    evaluate_fixed_protection,
)
from app.trade_qualification.portfolio import evaluate_portfolio
from app.trade_qualification.quote_collector import validate_collected_quote
from app.trade_qualification.recheck import (
    RecordedRecheckAssessment,
    copy_recorded_recheck,
    evaluate_recorded_recheck,
    verify_recorded_recheck,
)
from app.trade_qualification.recheck_models import (
    RecheckOrigin,
    copy_recheck_origin,
    freeze_recheck_origin,
    replay_recheck_origin,
)
from app.trade_qualification.service import evaluate_qualification_prefix
from tests.unit.qualification_prefix_fixtures import qualify_data, restore_source
from tests.unit.qualification_recheck_fixtures import recheck_source
from tests.unit.test_qualification_evidence_gate import Clock, synthetic_publisher
from tests.unit.test_qualification_portfolio import position
from tests.unit.test_qualification_quote_collector import Clock as QuoteClock
from tests.unit.test_qualification_quote_collector import capture

D = Decimal
TIMEFRAMES = ("4H", "1H", "15m", "5m")


@pytest.fixture(scope="module", params=["long", "short"])
def source(request):
    return recheck_source(request.param, scenario="next_boundary")


@pytest.fixture(scope="module")
def origin(source):
    # This double is an in-memory receipt contract, not native publication.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(gates, "publish_evidence", synthetic_publisher())
        evidence = gates.publish_qualification_evidence(
            Path.cwd(),
            source.original_source.market,
            run=source.original_run,
            purpose="synthetic_test",
            clock=Clock(source.original_source.evaluated_at),
            **source.original_inputs,
        )
    assert evidence.result.evidence_complete
    result = freeze_recheck_origin(evidence)
    assert result.publication_observed_here is False
    return result


def _continuation(source, market=None, **updates):
    old_market, old_analysis = restore_source(source.original_run.prefix.data_result)
    inputs = {
        "detection": source.original_run.prefix.detection,
        "observed_at": source.latest_source.evaluated_at,
    } | updates
    return evaluate_continuation(
        old_market,
        old_analysis,
        source.latest_source.market if market is None else market,
        **inputs,
    )


def _fixed_inputs(source):
    old_market, old_analysis = restore_source(source.original_run.prefix.data_result)
    new_market, new_analysis = restore_source(qualify_data(source.latest_source))
    pre = source.original_run
    return {
        "original_market": old_market,
        "original_analysis": old_analysis,
        "current_market": new_market,
        "current_analysis": new_analysis,
        "detection": pre.prefix.detection,
        "original_selection_audit_json": pre.protection_audit_json,
        "entry": pre.result.candidate_entry,
        "stop_loss": pre.result.stop_loss,
        "take_profit": pre.result.take_profit,
        "tick_size": source.original_source.tick_size,
        "policy": pre.policy.protection,
        "data_policy": source.latest_source.policy,
        "quote": source.latest_source.quote.quote,
        "observed_at": source.latest_source.evaluated_at,
    }


def _risk_inputs(source):
    return {
        "quote": source.latest_source.quote.quote,
        "current_risk_inputs": source.current_risk_inputs,
        "observed_at": source.latest_source.evaluated_at,
    }


def test_recorded_origin_roundtrip_and_replay_never_claim_current_publication(
    source, origin
):
    restored = RecheckOrigin.model_validate_json(
        origin.model_dump_json(round_trip=True), strict=True
    )
    assert restored == origin
    assert (
        replay_recheck_origin(
            restored, source.original_source.market, **source.original_inputs
        )
        == origin
    )
    assert origin.original_event_key == source.original_event_key
    pre = source.original_run
    assert origin.deadline == min(
        pre.prefix.intent.expires_at,
        pre.prefix.detection.trigger.expires_at,
        pre.result.entry_zone.expires_at,
        pre.prefix.timing.latest_valid_entry_time,
    )
    assert origin.candidate.candidate_entry == pre.result.candidate_entry
    assert (origin.candidate.stop_loss, origin.candidate.take_profit) == (
        pre.result.stop_loss,
        pre.result.take_profit,
    )
    assert len(origin.candidate.gates) == 12 and not origin.candidate.qualified
    assert not origin.publication_observed_here and not origin.execution_authority


def test_self_signed_changed_plot_can_be_recorded_but_cannot_replay_original_snapshot(
    source, origin
):
    snapshot = origin.evidence.snapshot
    panel = snapshot.panels[-1]
    candle = panel.candles[-1]
    changed_candle = candle.model_copy(
        update={
            "close": candle.close
            + (D("-0.001") if source.latest_source.direction == "long" else D("0.001"))
        }
    )
    changed_panel = panel.model_copy(
        update={"candles": (*panel.candles[:-1], changed_candle)}
    )
    changed_snapshot = snapshot.model_copy(
        update={"panels": (*snapshot.panels[:-1], changed_panel)}
    )
    evidence = origin.evidence.model_copy(
        update={
            "snapshot": changed_snapshot,
            "snapshot_sha256": gates._digest(changed_snapshot),
        }
    )
    altered = freeze_recheck_origin(evidence)
    assert altered.evaluation_sha256 != origin.evaluation_sha256
    assert not altered.publication_observed_here and not altered.execution_authority
    with pytest.raises(ValueError, match="snapshot_rebuild_mismatch"):
        replay_recheck_origin(
            altered, source.original_source.market, **source.original_inputs
        )


@pytest.mark.parametrize(
    "field",
    [
        "original_event_key",
        "original_source_sha256",
        "original_policy_sha256",
        "deadline",
        "publication_completed_at",
    ],
)
def test_recorded_original_pins_cannot_be_refreshed_by_model_copy(origin, field):
    old = getattr(origin, field)
    replacement = (
        old + timedelta(microseconds=1)
        if field in {"deadline", "publication_completed_at"}
        else "0" * 64
    )
    with pytest.raises(ValueError):
        copy_recheck_origin(origin.model_copy(update={field: replacement}))


def test_real_append_continuation_stays_bound_to_original_event_and_expiry(source):
    result = _continuation(source)
    assert result.passed
    assert result.original_event_key == source.original_event_key
    assert (
        result.original_trigger_expires_at
        == source.original_run.prefix.detection.trigger.expires_at
    )
    assert result.current_market_sha256 != result.original_market_sha256
    assert {frame.timeframe: frame.appended_count for frame in result.coverage} == {
        "4H": 0,
        "1H": 0,
        "15m": 1,
        "5m": 1,
    }
    old_market, old_analysis = restore_source(source.original_run.prefix.data_result)
    restored = ContinuationResult.model_validate_json(
        result.model_dump_json(round_trip=True), strict=True
    )
    assert restored == result
    assert (
        verify_continuation(
            restored,
            old_market,
            old_analysis,
            source.latest_source.market,
            detection=source.original_run.prefix.detection,
            observed_at=source.latest_source.evaluated_at,
        )
        == result
    )


@pytest.mark.parametrize("timeframe", TIMEFRAMES)
@pytest.mark.parametrize("defect", ["rewrite", "delete", "duplicate", "reorder"])
def test_every_native_timeframe_rejects_non_append_history(source, timeframe, defect):
    market = source.latest_source.market.model_copy(deep=True)
    rows = market.candles[timeframe]
    if defect == "rewrite":
        rows[-20] = rows[-20].model_copy(
            update={"volume_quote": rows[-20].volume_quote + D(1)}
        )
    elif defect == "delete":
        del rows[-20]
    elif defect == "duplicate":
        rows.insert(-20, rows[-20])
    else:
        rows[-20], rows[-19] = rows[-19], rows[-20]
    result = _continuation(source, market)
    assert not result.passed
    assert result.code == (
        "history_rewritten" if defect == "rewrite" else "current_source_invalid"
    )
    assert not result.complete_path_verified and not result.execution_authority


@pytest.mark.parametrize("timeframe", ["5m", "15m"])
def test_omitted_newly_closed_bar_fails_even_while_generic_g1_still_tolerates_it(
    source, timeframe
):
    market = source.latest_source.market.model_copy(deep=True)
    market.candles[timeframe].pop()
    result = _continuation(source, market)
    assert not result.passed and result.code == "closed_tail_missing"


@pytest.mark.parametrize("timeframe", ["5m", "15m"])
def test_post_setup_touch_and_same_bar_rebound_cannot_restore_original_event(
    source, timeframe
):
    market = source.latest_source.market.model_copy(deep=True)
    rows = market.candles[timeframe]
    field = "low" if source.latest_source.direction == "long" else "high"
    invalidation = source.original_run.prefix.detection.invalidation_price
    rows[-1] = rows[-1].model_copy(update={field: invalidation})
    assert rows[-1].close != invalidation  # The new close and fresh quote rebounded.
    assert (
        validate_collected_quote(source.latest_source.quote)
        == source.latest_source.quote
    )
    result = _continuation(source, market)
    assert not result.passed and result.code == "trigger_invalidated"
    assert result.invalidation_timeframe == timeframe
    assert result.invalidation_known_at == result.current_capture_at.replace(
        microsecond=0
    )
    assert result.original_event_key == source.original_event_key


def test_fresh_post_barrier_quote_never_proves_the_unseen_intrabar_path(source):
    quote = validate_collected_quote(source.latest_source.quote)
    result = _continuation(source)
    assert result.passed
    assert all(
        item.request_started_at > source.barrier_completed_at
        for item in quote.provenance
    )
    assert all(frame.intrabar_status == "unknown" for frame in result.coverage)
    assert all(
        frame.blind_from < frame.blind_to == source.latest_source.evaluated_at
        for frame in result.coverage
    )
    assert not result.complete_path_verified
    assert not result.execution_recheck_performed and not result.execution_authority


def test_newly_confirmed_nearer_target_cancels_fixed_bracket_after_valid_append(source):
    assert _continuation(source).passed
    inputs = _fixed_inputs(source)
    result = evaluate_fixed_protection(**inputs)
    assert not result.passed and result.code == "fixed_target_intervening_barrier"
    assert all(check.passed for check in result.checks[:-1])
    assert result.checks[-1].step == "current_target"
    expected = D("101.4") if source.latest_source.direction == "long" else D("98.6")
    assert result.checks[-1].measured_values["nearest_barrier"] == expected
    original = source.original_run.result
    assert (result.entry, result.stop_loss, result.take_profit) == (
        original.candidate_entry,
        original.stop_loss,
        original.take_profit,
    )
    constraints = json.loads(result.current_constraints_json)
    witness = [item for item in constraints["targets"] if D(item["price"]) == expected]
    assert len(witness) == 1 and witness[0]["known_at"].endswith("01:15:00+00:00")
    assert not result.continuation_verified and not result.conditions_verified
    assert not result.execution_authority


@pytest.mark.parametrize("price", ["entry", "stop_loss", "take_profit"])
def test_fixed_protection_never_accepts_changed_original_prices(source, price):
    inputs = _fixed_inputs(source)
    inputs[price] += D("0.01")
    result = evaluate_fixed_protection(**inputs)
    assert not result.passed and result.code == "fixed_original_selection_invalid"
    assert len(result.checks) == 2


def test_source_signatures_and_single_point_quote_do_not_turn_r4_into_full_recheck(
    source, origin
):
    result = evaluate_current_risk(origin, **_risk_inputs(source))
    assert result.passed and result.failure_stage is None
    assert result.candidate_risk.passed and result.execution_risk.passed
    assert result.original_event_key == source.original_event_key
    assert result.original_entry == origin.candidate.candidate_entry
    assert result.original_stop_loss == origin.candidate.stop_loss
    assert result.original_take_profit == origin.candidate.take_profit
    assert not result.original_sources_replayed
    assert (
        not result.execution_recheck_performed and not result.publication_observed_here
    )
    assert not result.atomic_risk_reserved and not result.market_fill_guaranteed
    assert not result.execution_authority
    # Costs/risk still passing cannot repair the independently observed R3 veto.
    assert (
        evaluate_fixed_protection(**_fixed_inputs(source)).code
        == "fixed_target_intervening_barrier"
    )
    restored = CurrentRiskResult.model_validate_json(
        result.model_dump_json(round_trip=True), strict=True
    )
    assert verify_current_risk(restored, origin, **_risk_inputs(source)) == result


@pytest.mark.parametrize(
    "name",
    [
        "requested_contracts",
        "requested_leverage",
        "contract_value",
        "lot_size",
        "correlation_group",
        "account_id",
    ],
)
def test_r4_cannot_resize_releverage_or_change_same_account_contract_to_pass(
    source, origin, name
):
    inputs = _risk_inputs(source)
    risk = source.current_risk_inputs
    if name in {"requested_contracts", "requested_leverage"}:
        risk = risk.model_copy(update={name: getattr(risk, name) + 1})
    elif name == "account_id":
        risk = risk.model_copy(
            update={"account": risk.account.model_copy(update={name: "other-account"})}
        )
    else:
        value = (
            "other-group"
            if name == "correlation_group"
            else getattr(risk.instrument, name) * 2
        )
        risk = risk.model_copy(
            update={"instrument": risk.instrument.model_copy(update={name: value})}
        )
    inputs["current_risk_inputs"] = risk
    with pytest.raises(ValueError):
        evaluate_current_risk(origin, **inputs)


def test_r4_missing_quote_stops_before_either_portfolio_result(source, origin):
    result = evaluate_current_risk(origin, **(_risk_inputs(source) | {"quote": None}))
    assert not result.passed and result.failure_stage == "economics"
    assert result.candidate_risk is result.execution_risk is None
    assert result.maximum_displayed_risk_amount is None


def test_r4_current_guard_denial_stops_without_execution_scenario_repair(
    source, origin
):
    risk = source.current_risk_inputs
    disabled = risk.model_copy(
        update={"authority": risk.authority.model_copy(update={"armed": False})}
    )
    result = evaluate_current_risk(
        origin, **(_risk_inputs(source) | {"current_risk_inputs": disabled})
    )
    assert not result.passed and result.failure_stage == "candidate_risk"
    assert result.economics.passed and not result.candidate_risk.passed
    assert (
        result.execution_risk is None and result.maximum_displayed_risk_amount is None
    )


@pytest.mark.parametrize("record", ["continuation", "fixed"])
@pytest.mark.parametrize("value", [True, 0, 1, "false"])
def test_r2_r3_model_copy_authority_is_not_a_valid_fingerprint(source, record, value):
    result = (
        _continuation(source)
        if record == "continuation"
        else evaluate_fixed_protection(**_fixed_inputs(source))
    )
    with pytest.raises(ValueError):
        _ = result.model_copy(update={"execution_authority": value}).evaluation_sha256


def test_hostile_decimal_context_preserves_source_survival_and_fixed_barrier_denial(
    source,
):
    inputs = _fixed_inputs(source)
    expected_continuation, expected_fixed = (
        _continuation(source),
        evaluate_fixed_protection(**inputs),
    )
    with localcontext(Context(prec=6, traps=[Inexact, Rounded])):
        continuation, fixed = _continuation(source), evaluate_fixed_protection(**inputs)
        assert continuation == expected_continuation
        assert fixed == expected_fixed
        assert fixed.evaluation_sha256 == expected_fixed.evaluation_sha256


def test_fixed_constraints_self_signature_remains_output_only(source):
    result = evaluate_fixed_protection(**_fixed_inputs(source))
    assert not result.passed
    restored = FixedProtectionResult.model_validate_json(
        result.model_dump_json(round_trip=True), strict=True
    )
    assert restored == result
    assert hashlib.sha256(result.current_constraints_json.encode()).hexdigest()
    assert not result.continuation_verified and not result.economics_verified


def _account_near_portfolio_cap(source, ordinary):
    """Add a fictional current position; never loosen the original policy."""
    risk = source.current_risk_inputs
    with localcontext(Context(prec=100)):
        midpoint = (
            ordinary.candidate_risk.max_loss_amount
            + ordinary.execution_risk.max_loss_amount
        ) / 2
        room = (
            source.original_run.policy.portfolio.max_portfolio_risk_pct
            * risk.account.equity
        )
        exposure = position(
            position_id="synthetic-current-new-position",
            instrument_id=risk.instrument.instrument_id,
            direction=source.latest_source.direction,
            settlement_currency=risk.account.settlement_currency,
            notional=D(1000),
            margin=D(100),
            risk_amount=room - midpoint,
            correlation_group=risk.instrument.correlation_group,
        )
    account = risk.account.model_copy(
        update={"positions": (exposure,), "position_count": 1}
    )
    return risk.model_copy(update={"account": account})


def test_adverse_executable_reference_passes_costs_but_second_risk_scenario_must_deny(
    source, origin
):
    inputs = _risk_inputs(source)
    ordinary = evaluate_current_risk(origin, **inputs)
    assert ordinary.passed
    assert (
        ordinary.execution_risk.max_loss_amount
        > ordinary.candidate_risk.max_loss_amount
    )
    limited = _account_near_portfolio_cap(source, ordinary)
    result = evaluate_current_risk(
        origin, **(inputs | {"current_risk_inputs": limited})
    )
    assert result.economics.passed and result.candidate_risk.passed
    assert result.failure_stage == "execution_risk" and not result.execution_risk.passed
    assert "portfolio_risk_limit_exceeded" in result.execution_risk.causes
    assert (
        result.maximum_displayed_risk_amount
        is result.maximum_displayed_notional
        is result.maximum_displayed_margin
        is None
    )
    assert not result.atomic_risk_reserved and not result.execution_authority


def test_favorable_sample_cannot_repair_failing_original_candidate_portfolio(
    source, origin
):
    # R4 consumes typed quote claims; this is deliberately a synthetic component
    # scenario, not a new authenticated CollectedQuote or a complete recheck.
    inputs = _risk_inputs(source)
    entry = origin.candidate.candidate_entry
    long = source.latest_source.direction == "long"
    reference = entry - D("0.01") if long else entry + D("0.01")
    bid, ask = (
        (reference - D("0.01"), reference)
        if long
        else (reference, reference + D("0.01"))
    )
    quote = inputs["quote"].model_copy(
        update={"bid": bid, "ask": ask, "mark_price": reference}
    )
    inputs["quote"] = quote
    ordinary = evaluate_current_risk(origin, **inputs)
    assert ordinary.passed and ordinary.economics.passed
    assert (
        ordinary.execution_risk.max_loss_amount
        < ordinary.candidate_risk.max_loss_amount
    )
    limited = _account_near_portfolio_cap(source, ordinary)
    result = evaluate_current_risk(
        origin, **(inputs | {"current_risk_inputs": limited})
    )
    assert result.failure_stage == "candidate_risk" and not result.candidate_risk.passed
    assert result.execution_risk is None
    assert "portfolio_risk_limit_exceeded" in result.candidate_risk.causes
    # Independently prove that the favorable second scenario really would pass,
    # while the composition correctly refuses to use it to repair the first.
    scenario = ordinary.economics.execution_result
    hypothetical = evaluate_portfolio(
        report_id=source.latest_source.report_id,
        instrument_id=source.latest_source.market.instrument_id,
        direction=source.latest_source.direction,
        candidate_entry=scenario.candidate_entry,
        stop_loss=scenario.stop_loss,
        round_trip_cost_per_base=scenario.cost_per_base,
        requested_contracts=limited.requested_contracts,
        requested_leverage=limited.requested_leverage,
        instrument=limited.instrument,
        account=limited.account,
        authority=limited.authority,
        policy=source.original_run.policy.portfolio,
        current_time=source.latest_source.evaluated_at,
    )
    assert hypothetical.passed and not hypothetical.execution_authority


@pytest.mark.parametrize(
    "field", ["balance_stamp", "positions_stamp", "history_stamp", "reservations_stamp"]
)
def test_one_stale_current_account_component_is_not_repaired_by_new_quote(
    source, origin, field
):
    inputs = _risk_inputs(source)
    risk = source.current_risk_inputs
    old = source.latest_source.evaluated_at - timedelta(seconds=31)
    stamp = getattr(risk.account, field).model_copy(
        update={"observed_at": old, "received_at": old}
    )
    account = risk.account.model_copy(update={field: stamp})
    result = evaluate_current_risk(
        origin,
        **(
            inputs
            | {"current_risk_inputs": risk.model_copy(update={"account": account})}
        ),
    )
    assert result.economics.passed and result.failure_stage == "candidate_risk"
    assert "evidence_stale" in result.candidate_risk.causes
    assert result.execution_risk is None


@pytest.mark.parametrize("when", ["at_publication", "at_expiry", "after_expiry"])
def test_r4_timestamp_must_stay_inside_the_original_open_interval(source, origin, when):
    now = {
        "at_publication": origin.publication_completed_at,
        "at_expiry": origin.deadline,
        "after_expiry": origin.deadline + timedelta(microseconds=1),
    }[when]
    with pytest.raises(ValueError, match="current_risk_outside_original_window"):
        evaluate_current_risk(origin, **(_risk_inputs(source) | {"observed_at": now}))


def _conditions_inputs(source):
    return {
        "intent": source.original_run.prefix.intent,
        "policy": source.original_run.policy.prefix,
        "quote": source.latest_source.quote,
        "reference": source.latest_source.reference,
        "observed_at": source.latest_source.evaluated_at,
    }


def _conditions(source):
    return evaluate_current_conditions(
        source.latest_source.market, **_conditions_inputs(source)
    )


def test_appended_current_conditions_match_real_prefix_without_replacing_old_event(
    source,
):
    original = source.original_run.model_dump_json(round_trip=True)
    result = _conditions(source)
    arguments = _conditions_inputs(source)
    arguments["evaluated_at"] = arguments.pop("observed_at")
    fresh_prefix = evaluate_qualification_prefix(
        source.latest_source.market,
        consumed_event_keys=frozenset(),
        **arguments,
    )
    assert result.passed
    assert result.gates == fresh_prefix.result.gates[:4]
    assert result.data_result == fresh_prefix.data_result
    assert result.result.raw_score == fresh_prefix.result.raw_score
    assert result.result.effective_score == fresh_prefix.result.effective_score
    assert result.result.trigger is result.result.entry_zone is None
    assert result.result.entry_timing_state == "not_evaluated"
    assert not result.event_continuation_verified
    assert not result.execution_recheck_performed and not result.result.qualified
    assert source.original_run.model_dump_json(round_trip=True) == original
    restored = CurrentConditionsResult.model_validate_json(
        result.model_dump_json(round_trip=True), strict=True
    )
    assert restored == result
    assert (
        verify_current_conditions(
            restored, source.latest_source.market, **_conditions_inputs(source)
        )
        == result
    )


def test_current_conditions_self_signed_report_cannot_replace_replayed_evaluation(
    source,
):
    result = _conditions(source)
    last = result.gates[-1]
    changed = last.model_copy(
        update={"reason": "Synthetic altered explanation, not evaluator output."}
    )
    changed_result = result.result.model_copy(
        update={"gates": (*result.gates[:-1], changed)}
    )
    altered = copy_current_conditions(
        result.model_copy(update={"result": changed_result})
    )
    # Consistency fingerprints are not evaluator signatures. Replay is the
    # boundary even when every represented gate remains internally valid.
    assert altered.evaluation_sha256 != result.evaluation_sha256
    with pytest.raises(ValueError, match="current_conditions_replay_mismatch"):
        verify_current_conditions(
            altered, source.latest_source.market, **_conditions_inputs(source)
        )


@pytest.mark.parametrize("field", ["intent", "policy", "data_result", "result"])
@pytest.mark.parametrize("defect", ["hidden", "subclass"])
def test_current_conditions_undeclared_nested_records_rejected_before_fingerprint(
    source, field, defect
):
    result = _conditions(source)
    record = getattr(result, field)
    if defect == "hidden":
        changed = record.model_copy(update={"caller_trusted": True})
    else:
        extended = create_model(
            f"UndeclaredRecheck{type(record).__name__}",
            __base__=type(record),
            caller_trusted=(bool, True),
        )
        changed = extended.model_construct(**record.__dict__, caller_trusted=True)
    with pytest.raises(ValueError):
        _ = result.model_copy(update={field: changed}).evaluation_sha256


def test_current_conditions_unbounded_iterator_is_not_consumed_by_copy(source):
    result = _conditions(source)
    visited = []

    def injected():
        visited.append(True)
        yield result.gates[0]

    changed = result.result.model_copy(update={"gates": injected()})
    with pytest.raises(ValueError):
        copy_current_conditions(result.model_copy(update={"result": changed}))
    assert visited == []


@pytest.mark.parametrize("missing", ["quote", "reference"])
def test_current_g1_missing_evidence_never_reaches_later_condition_evaluators(
    source, monkeypatch, missing
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("G1 source denial must stop before regime or strategy evaluation")

    monkeypatch.setattr(conditions_module, "route_regime", forbidden)
    monkeypatch.setattr(conditions_module, "assess_conditions", forbidden)
    inputs = _conditions_inputs(source) | {missing: None}
    result = evaluate_current_conditions(source.latest_source.market, **inputs)
    assert not result.passed and len(result.gates) == 1
    assert not result.event_continuation_verified and not result.execution_authority


@pytest.fixture(scope="module")
def same_interval_source(source):
    return recheck_source(source.latest_source.direction, scenario="same_interval")


def _recorded_inputs(source, origin):
    return {
        "origin": origin,
        "original_inputs": dict(source.original_inputs),
        "quote": source.latest_source.quote,
        "reference": source.latest_source.reference,
        "current_risk_inputs": source.current_risk_inputs,
        "consumed_event_keys": frozenset(),
        "observed_at": source.latest_source.evaluated_at,
    }


def _recorded(source, origin, **updates):
    return evaluate_recorded_recheck(
        source.original_source.market,
        source.latest_source.market,
        **(_recorded_inputs(source, origin) | updates),
    )


def _forbidden_later(*_args, **_kwargs):
    pytest.fail("A failed earlier recheck stage reached a later dependency")


def test_true_eight_step_offline_recheck_remains_unknown_and_has_no_runtime_permission(
    same_interval_source, origin
):
    source = same_interval_source
    result = _recorded(source, origin)
    assert result.computational_checks_passed and len(result.checks) == 8
    assert all(check.passed for check in result.checks)
    assert result.origin == origin
    assert result.current_conditions.passed and result.current_risk.passed
    assert result.current_conditions.result.trigger is None
    assert result.continuation.original_event_key == origin.original_event_key
    assert result.timing.event_key == origin.original_event_key
    assert result.timing.latest_valid_entry_time == origin.deadline
    assert result.intrabar_status == "unknown"
    assert not result.runtime_admissible and not result.execution_authority
    assert not result.publication_observed_here and not result.complete_path_verified
    assert not result.per_timeframe_capture_verified and not result.atomic_risk_reserved
    assert not result.execution_recheck_performed and not origin.candidate.qualified
    assert all(
        frame.intrabar_status == "unknown" for frame in result.continuation.coverage
    )
    assert copy_recorded_recheck(result) == result
    restored = RecordedRecheckAssessment.model_validate_json(
        result.model_dump_json(round_trip=True), strict=True
    )
    assert restored == result
    assert restored.evaluation_sha256 == result.evaluation_sha256
    assert (
        verify_recorded_recheck(
            restored,
            source.original_source.market,
            source.latest_source.market,
            **_recorded_inputs(source, origin),
        )
        == result
    )


def test_new_target_barrier_stops_coordinator_before_any_current_risk(
    source, origin, monkeypatch
):
    monkeypatch.setattr(recheck_module, "evaluate_current_risk", _forbidden_later)
    # Even wholly malformed later risk inputs must not be visited after the
    # genuine current raw candles expose a nearer fixed-target obstacle.
    result = _recorded(source, origin, current_risk_inputs=object())
    assert not result.computational_checks_passed
    assert len(result.checks) == 7
    assert result.code == "fixed_target_intervening_barrier"
    assert all(check.passed for check in result.checks[:-1])
    assert result.fixed_protection.take_profit == origin.candidate.take_profit
    assert result.current_risk is result.current_risk_inputs_sha256 is None


def test_original_raw_source_rewrite_stops_before_inspecting_any_current_inputs(
    source, origin, monkeypatch
):
    original = source.original_source.market.model_copy(deep=True)
    row = original.candles["4H"][-20]
    original.candles["4H"][-20] = row.model_copy(
        update={"volume_contracts": row.volume_contracts + D(1)}
    )
    monkeypatch.setattr(recheck_module, "_capture", _forbidden_later)
    result = evaluate_recorded_recheck(
        original,
        object(),
        **(
            _recorded_inputs(source, origin)
            | {
                "quote": object(),
                "reference": object(),
                "current_risk_inputs": object(),
                "consumed_event_keys": object(),
            }
        ),
    )
    assert len(result.checks) == 1 and result.code == "original_replay_failed"
    assert (
        result.current_conditions is result.continuation is result.current_risk is None
    )


@pytest.mark.parametrize("endpoint", [0, 1, 2])
def test_every_public_endpoint_request_must_individually_cross_publication_barrier(
    same_interval_source, origin, monkeypatch, endpoint
):
    source = same_interval_source
    monkeypatch.setattr(recheck_module, "evaluate_current_conditions", _forbidden_later)
    quote = source.latest_source.quote
    observations = list(quote.provenance)
    observations[endpoint] = observations[endpoint].model_copy(
        update={"request_started_at": origin.publication_completed_at}
    )
    changed = quote.model_copy(update={"provenance": tuple(observations)})
    result = _recorded(source, origin, quote=changed)
    assert len(result.checks) == 2 and not result.computational_checks_passed
    assert result.checks[-1].step == "capture_barrier"
    assert result.current_conditions is None


@pytest.mark.parametrize("field", ["source_time", "received_at"])
def test_ws_frame_time_before_publication_is_not_refreshed_by_current_market_receipt(
    same_interval_source, origin, monkeypatch, field
):
    source = same_interval_source
    monkeypatch.setattr(recheck_module, "evaluate_current_conditions", _forbidden_later)
    reference = source.latest_source.reference.model_copy(
        update={field: origin.publication_completed_at}
    )
    result = _recorded(source, origin, reference=reference)
    assert len(result.checks) == 2 and result.checks[-1].step == "capture_barrier"
    assert not result.computational_checks_passed


@pytest.mark.parametrize(
    "field",
    [
        "balance_stamp",
        "positions_stamp",
        "history_stamp",
        "reservations_stamp",
        "authority",
        "instrument",
    ],
)
def test_every_current_account_or_contract_claim_requires_its_own_post_barrier_time(
    same_interval_source, origin, monkeypatch, field
):
    source = same_interval_source
    risk = source.current_risk_inputs
    if field == "instrument":
        instrument = risk.instrument.model_copy(
            update={"observed_at": origin.publication_completed_at}
        )
        changed = risk.model_copy(update={"instrument": instrument})
    elif field == "authority":
        stamp = risk.authority.stamp.model_copy(
            update={"observed_at": origin.publication_completed_at}
        )
        changed = risk.model_copy(
            update={"authority": risk.authority.model_copy(update={"stamp": stamp})}
        )
    else:
        stamp = getattr(risk.account, field).model_copy(
            update={"observed_at": origin.publication_completed_at}
        )
        changed = risk.model_copy(
            update={"account": risk.account.model_copy(update={field: stamp})}
        )
    monkeypatch.setattr(recheck_module, "evaluate_current_risk", _forbidden_later)
    result = _recorded(source, origin, current_risk_inputs=changed)
    assert len(result.checks) == 8 and all(c.passed for c in result.checks[:-1])
    assert result.code == "current_account_claim_not_after_publication"
    assert result.current_risk is None
    assert not result.computational_checks_passed and not result.runtime_admissible


def test_consumed_original_event_stops_before_location_even_when_new_conditions_pass(
    same_interval_source, origin, monkeypatch
):
    source = same_interval_source
    monkeypatch.setattr(recheck_module, "evaluate_location", _forbidden_later)
    result = _recorded(
        source, origin, consumed_event_keys=frozenset({origin.original_event_key})
    )
    assert len(result.checks) == 5 and result.checks[-1].step == "timing"
    assert (
        not result.timing.timing_valid
        and result.timing.event_key == origin.original_event_key
    )
    assert result.current_conditions.passed and result.continuation.passed
    assert result.location is result.fixed_protection is result.current_risk is None


def _recapture_one_tick_reference(source, *, adverse):
    """Real collector over fictional bytes; no fabricated CollectedQuote PASS."""
    old = source.latest_source.quote
    direction = D(1) if source.latest_source.direction == "long" else D(-1)
    shift = direction * source.latest_source.tick_size * (1 if adverse else -1)
    bodies = {item.role: json.loads(item.response_body) for item in old.provenance}

    def body(role, _unused):
        packet = bodies[role]
        row = packet["data"][0]
        if role == "ticker":
            row["bidPx"] = str(D(row["bidPx"]) + shift)
            row["askPx"] = str(D(row["askPx"]) + shift)
        elif role == "mark":
            row["markPx"] = str(D(row["markPx"]) + shift)
        return packet

    start = old.provenance[0].request_started_at
    result, requests = asyncio.run(
        capture(
            change=body,
            clock=QuoteClock(
                tuple(start + timedelta(milliseconds=i) for i in range(10))
            ),
            policy=old.policy,
            barrier=source.barrier_completed_at,
            instrument=old.quote.instrument_id,
            report=old.quote.report_id,
        )
    )
    assert len(requests) == 3 and result == validate_collected_quote(result)
    return result


@pytest.mark.parametrize("adverse", [True, False])
def test_real_current_chain_preserves_both_portfolio_scenario_failure_priorities(
    same_interval_source, origin, adverse
):
    source = same_interval_source
    quote = _recapture_one_tick_reference(source, adverse=adverse)
    ordinary = _recorded(source, origin, quote=quote)
    assert ordinary.computational_checks_passed
    assert ordinary.current_risk.economics.passed
    assert ordinary.current_risk.candidate_risk.passed
    assert ordinary.current_risk.execution_risk.passed
    limited = _account_near_portfolio_cap(source, ordinary.current_risk)
    result = _recorded(source, origin, quote=quote, current_risk_inputs=limited)
    assert len(result.checks) == 8 and all(c.passed for c in result.checks[:-1])
    assert result.fixed_protection.passed and result.current_risk.economics.passed
    assert result.current_risk.failure_stage == (
        "execution_risk" if adverse else "candidate_risk"
    )
    failed = (
        result.current_risk.execution_risk
        if adverse
        else result.current_risk.candidate_risk
    )
    assert not failed.passed and "portfolio_risk_limit_exceeded" in failed.causes
    if adverse:
        assert result.current_risk.candidate_risk.passed
    else:
        assert result.current_risk.execution_risk is None
    assert result.current_risk.maximum_displayed_risk_amount is None
    assert not result.computational_checks_passed and not result.runtime_admissible
    assert result.origin == origin
    assert result.fixed_protection.stop_loss == origin.candidate.stop_loss
    assert result.fixed_protection.take_profit == origin.candidate.take_profit


def test_fixed_protection_nested_serializer_cannot_run_during_record_fingerprinting(
    source, origin
):
    result = _recorded(source, origin)
    protection = result.fixed_protection
    check = protection.checks[-1]
    base = type(check)
    serialized = []

    class UndeclaredProtectionCheck(base):
        @model_serializer
        def misleading(self):
            serialized.append(True)
            return check.model_dump(mode="json")

    unknown = UndeclaredProtectionCheck.model_construct(**check.__dict__)
    changed = protection.model_copy(
        update={"checks": (*protection.checks[:-1], unknown)}
    )
    with pytest.raises(ValueError):
        _ = result.model_copy(update={"fixed_protection": changed}).evaluation_sha256
    assert serialized == []
