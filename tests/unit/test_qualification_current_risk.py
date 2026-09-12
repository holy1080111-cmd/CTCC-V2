"""Two real cost/risk calculations over explicit synthetic account claims."""

from datetime import timedelta
from decimal import Context, Decimal, Inexact, localcontext

import pytest
from pydantic import create_model

from app.trade_qualification import current_risk as module
from app.trade_qualification.current_risk import (
    CurrentRiskResult,
    copy_current_risk,
    evaluate_current_risk,
    verify_current_risk,
)
from app.trade_qualification.portfolio import PendingReservation
from tests.unit.test_qualification_recheck_models import (
    origin as origin,  # noqa: PLC0414
)
from tests.unit.test_qualification_recheck_models import (
    source as source,  # noqa: PLC0414
)

D = Decimal


def inputs(source):
    return {
        "quote": source.latest_source.quote.quote,
        "current_risk_inputs": source.current_risk_inputs,
        "observed_at": source.latest_source.evaluated_at,
    }


@pytest.fixture
def result(source, origin):
    return evaluate_current_risk(origin, **inputs(source))


def test_both_unchanged_scenarios_are_recomputed(source, origin, result):
    assert result.passed
    assert result.failure_stage is None
    assert result.candidate_risk.passed and result.execution_risk.passed
    assert result.original_entry == origin.candidate.candidate_entry
    assert result.original_stop_loss == origin.candidate.stop_loss
    assert result.original_take_profit == origin.candidate.take_profit
    assert (
        result.original_requested_contracts
        == source.current_risk_inputs.requested_contracts
    )
    assert (
        result.original_requested_leverage
        == source.current_risk_inputs.requested_leverage
    )
    assert result.maximum_displayed_risk_amount == max(
        result.candidate_risk.max_loss_amount, result.execution_risk.max_loss_amount
    )
    assert verify_current_risk(result, origin, **inputs(source)) == result
    assert all(getattr(result, name) is False for name in module._FALSE_FLAGS)
    assert not origin.candidate.qualified


def test_json_record_roundtrip(result):
    restored = CurrentRiskResult.model_validate_json(
        result.model_dump_json(round_trip=True), strict=True
    )
    assert restored == result
    assert restored.evaluation_sha256 == result.evaluation_sha256


@pytest.mark.parametrize(
    "change",
    ("size", "leverage", "account", "contract_value", "correlation_group", "lot_size"),
)
def test_old_candidate_cannot_switch_size_account_or_contract(source, origin, change):
    params = inputs(source)
    risk = params["current_risk_inputs"]
    if change in ("size", "leverage"):
        field = "requested_contracts" if change == "size" else "requested_leverage"
        risk = risk.model_copy(update={field: getattr(risk, field) + 1})
    elif change == "account":
        risk = risk.model_copy(
            update={
                "account": risk.account.model_copy(
                    update={"account_id": "other-account"}
                )
            }
        )
    else:
        value = (
            "other"
            if change == "correlation_group"
            else getattr(risk.instrument, change) * 2
        )
        risk = risk.model_copy(
            update={"instrument": risk.instrument.model_copy(update={change: value})}
        )
    with pytest.raises(ValueError):
        evaluate_current_risk(origin, **{**params, "current_risk_inputs": risk})


@pytest.mark.parametrize("field", ("account", "authority", "instrument"))
def test_missing_current_evidence_denies_not_old_account_fallback(
    source, origin, field
):
    params = inputs(source)
    params["current_risk_inputs"] = params["current_risk_inputs"].model_copy(
        update={field: None}
    )
    result = evaluate_current_risk(origin, **params)
    assert not result.passed and result.failure_stage == "candidate_risk"
    assert field + "_missing" in result.candidate_risk.causes
    assert result.execution_risk is None
    assert result.maximum_displayed_risk_amount is None


@pytest.mark.parametrize(
    "field,value",
    (
        ("emergency_stop", True),
        ("armed", False),
        ("live_trading", True),
        ("live_order_writes", True),
        ("live_auto_execution", True),
        ("simulated_trading_header", "0"),
    ),
)
def test_current_guard_denial_stops_second_risk_scenario(source, origin, field, value):
    params = inputs(source)
    risk = params["current_risk_inputs"]
    params["current_risk_inputs"] = risk.model_copy(
        update={"authority": risk.authority.model_copy(update={field: value})}
    )
    result = evaluate_current_risk(origin, **params)
    assert result.failure_stage == "candidate_risk"
    assert not result.passed
    assert result.execution_risk is None


@pytest.mark.parametrize("kind", ("none", "stale", "wide_spread"))
def test_economic_failure_stops_all_account_evaluation(
    source, origin, monkeypatch, kind
):
    params = inputs(source)
    quote = params["quote"]
    if kind == "none":
        params["quote"] = None
    elif kind == "stale":
        params["observed_at"] += timedelta(seconds=61)
    else:
        params["quote"] = quote.model_copy(update={"ask": quote.ask + D(1)})

    def forbidden(**_kwargs):
        pytest.fail("economic failure must stop before portfolio evaluation")

    monkeypatch.setattr(module, "evaluate_portfolio", forbidden)
    result = evaluate_current_risk(origin, **params)
    assert not result.passed and result.failure_stage == "economics"
    assert result.candidate_risk is result.execution_risk is None


@pytest.mark.parametrize(
    "when", ("barrier", "before_barrier", "deadline", "after_deadline", "naive", "text")
)
def test_strict_original_window_cannot_be_extended(source, origin, when):
    value = {
        "barrier": origin.publication_completed_at,
        "before_barrier": origin.publication_completed_at - timedelta(microseconds=1),
        "deadline": origin.deadline,
        "after_deadline": origin.deadline + timedelta(microseconds=1),
        "naive": source.latest_source.evaluated_at.replace(tzinfo=None),
        "text": source.latest_source.evaluated_at.isoformat(),
    }[when]
    with pytest.raises(ValueError):
        evaluate_current_risk(origin, **{**inputs(source), "observed_at": value})


@pytest.mark.parametrize("field", module._FALSE_FLAGS)
@pytest.mark.parametrize("value", (True, 0, "false"))
def test_no_claim_can_upgrade_to_authority(result, field, value):
    with pytest.raises(ValueError):
        copy_current_risk(result.model_copy(update={field: value}))


@pytest.mark.parametrize(
    "target", ("result", "economics", "candidate_risk", "execution_risk")
)
@pytest.mark.parametrize("defect", ("hidden", "subclass"))
def test_nested_extensions_rejected_before_serializer(result, target, defect):
    record = result if target == "result" else getattr(result, target)
    if defect == "hidden":
        record = record.model_copy(update={"hidden": True})
    else:
        cls = create_model(
            "ExtendedCurrentRisk", __base__=type(record), hidden=(bool, True)
        )
        record = cls.model_construct(**record.__dict__, hidden=True)
    changed = (
        record if target == "result" else result.model_copy(update={target: record})
    )
    with pytest.raises(ValueError):
        copy_current_risk(changed)


def test_changed_account_must_recompute_and_cannot_replay_old_pass(
    source, origin, result
):
    params = inputs(source)
    risk = params["current_risk_inputs"]
    params["current_risk_inputs"] = risk.model_copy(
        update={"account": risk.account.model_copy(update={"available_margin": D(0)})}
    )
    denied = evaluate_current_risk(origin, **params)
    assert not denied.passed
    with pytest.raises(ValueError):
        verify_current_risk(result, origin, **params)


def test_hostile_decimal_context_does_not_change_pass_or_digests(
    source, origin, result
):
    context = Context(prec=2)
    context.traps[Inexact] = True
    with localcontext(context):
        actual = evaluate_current_risk(origin, **inputs(source))
        assert actual == result
        assert actual.evaluation_sha256 == result.evaluation_sha256


def test_worst_sampled_risk_actually_changes_current_portfolio_decision(source, origin):
    params = inputs(source)
    quote = params["quote"]
    shift = D("0.01") if source.latest_source.direction == "long" else D("-0.01")
    params["quote"] = quote.model_copy(
        update={
            "ask": quote.ask + shift,
            "bid": quote.bid + shift,
            "mark_price": quote.mark_price + shift,
        }
    )
    baseline = evaluate_current_risk(origin, **params)
    assert baseline.passed and baseline.economics.passed
    assert (
        baseline.execution_risk.max_loss_amount
        > baseline.candidate_risk.max_loss_amount
    )
    risk = params["current_risk_inputs"]
    with localcontext(Context(prec=100)):
        remaining = (
            origin.evidence.pre_evidence.policy.portfolio.max_portfolio_risk_pct
            * risk.account.equity
            - baseline.candidate_risk.max_loss_amount
        )
    pending = PendingReservation(
        reservation_id="synthetic-new-post-barrier-pending",
        instrument_id="ETH-USDT-SWAP",
        direction=source.latest_source.direction,
        settlement_currency="USDT",
        notional=D(1000),
        margin=D(100),
        risk_amount=remaining,
        correlation_group=risk.instrument.correlation_group,
    )
    params["current_risk_inputs"] = risk.model_copy(
        update={
            "account": risk.account.model_copy(
                update={
                    "pending_reservations": (pending,),
                    "pending_reservation_count": 1,
                }
            )
        }
    )
    result = evaluate_current_risk(origin, **params)
    assert result.economics.passed
    assert result.candidate_risk.passed
    assert result.failure_stage == "execution_risk"
    assert not result.execution_risk.passed
    assert "portfolio_risk_limit_exceeded" in result.execution_risk.causes
    assert result.maximum_displayed_risk_amount is None
    assert result.original_requested_contracts == risk.requested_contracts
    assert result.atomic_risk_reserved is False
