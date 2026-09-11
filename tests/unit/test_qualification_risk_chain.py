"""Synthetic source → structure → costs → portfolio integration, never orders.

These are independent offline checks, not a 12-gate orchestrator, a collector,
an execution permit, or a shadow/Demo sample. Account claims below are fictional.
"""

import ast
import inspect
from copy import deepcopy
from datetime import timedelta
from decimal import Context, Decimal, localcontext

import pytest

from app.trade_qualification import economics, portfolio
from app.trade_qualification.economics import evaluate_economics
from app.trade_qualification.portfolio import evaluate_portfolio
from app.trade_qualification.timing import evaluate_timing
from tests.unit.test_qualification_economics import policy as cost_policy
from tests.unit.test_qualification_entry_chain import _location, _quote
from tests.unit.test_qualification_events import OBSERVED
from tests.unit.test_qualification_portfolio import (
    STAMP_FIELDS,
    account,
    authority,
    instrument,
    outcome,
    position,
    reservation,
    stamp,
)
from tests.unit.test_qualification_portfolio import (
    policy as risk_policy,
)
from tests.unit.test_qualification_structural_selection import _inputs, _select

D = Decimal


def _risk_evidence(event):
    observed = event.observed_at
    evidence_stamp = stamp(
        environment="demo", observed_at=observed, received_at=observed
    )
    return {
        "instrument": instrument(
            instrument_id=event.instrument_id,
            observed_at=observed,
            received_at=observed,
        ),
        "account": account(
            **{name: evidence_stamp for name in STAMP_FIELDS},
            peak_observed_at=observed - timedelta(days=7),
            peak_window_started_at=observed - timedelta(days=30),
            history_start=observed - timedelta(days=7),
            history_end=observed,
        ),
        "policy": risk_policy(drawdown_window_started_at=observed - timedelta(days=30)),
        "authority": authority(stamp=evidence_stamp),
    }


def _cost(event, selected, quote, *, policy=None):
    return evaluate_economics(
        report_id=event.report_id,
        instrument_id=event.instrument_id,
        direction=event.direction,
        candidate_entry=event.trigger.trigger_price,
        stop_loss=selected.stop.final_stop,
        take_profit=selected.target.final_target,
        quote=quote,
        policy=cost_policy() if policy is None else policy,
        current_time=OBSERVED,
    )


def _parts(direction):
    inputs = _inputs(direction)
    event, zone = inputs[2:]
    structure = _select(inputs)
    assert structure.protection_valid
    quote = _quote(event, event.trigger.trigger_price)
    timing = evaluate_timing(
        event,
        current_time=OBSERVED,
        candidate_created_at=OBSERVED,
        candidate_expires_at=event.trigger.expires_at,
        reference_price=event.trigger.trigger_price,
    )
    location = _location(event, zone, quote)
    assert timing.timing_valid and location.passed
    costs = _cost(event, structure.selected, quote)
    assert costs.passed, costs
    return inputs, structure, quote, timing, location, costs


def _portfolio(parts, **updates):
    event = parts[0][2]
    costs = parts[-1]
    assert costs.passed  # Test fixture consumes only a passing economics result.
    return evaluate_portfolio(
        **(
            {
                "report_id": event.report_id,
                "instrument_id": event.instrument_id,
                "direction": event.direction,
                "candidate_entry": costs.candidate_entry,
                "stop_loss": costs.stop_loss,
                "round_trip_cost_per_base": costs.cost_per_base,
                "requested_contracts": D("10"),
                "requested_leverage": 10,
                "current_time": OBSERVED,
                **_risk_evidence(event),
            }
            | updates
        )
    )


@pytest.mark.parametrize("direction", ["long", "short"])
def test_source_geometry_and_costs_flow_to_portfolio_without_identity_or_price_changes(
    direction,
):
    parts = _parts(direction)
    inputs, structure, quote, timing, location, costs = parts
    event, zone = inputs[2:]
    before = deepcopy(parts)
    risk = _portfolio(parts)
    assert risk.passed and risk.causes == ()
    assert {
        item.report_id for item in (event, zone, timing, location, costs, risk)
    } == {event.report_id}
    assert {
        item.instrument_id
        for item in (event, zone, structure, quote, location, costs, risk)
    } == {event.instrument_id}
    assert {item.direction for item in (event, zone, structure, costs, risk)} == {
        direction
    }
    assert (
        costs.candidate_entry
        == structure.reference_entry
        == event.trigger.trigger_price
    )
    assert costs.stop_loss == structure.selected.stop.final_stop
    assert costs.take_profit == structure.selected.target.final_target
    assert costs.quote_sha256 == location.quote_sha256
    assert structure.source_sha256 == zone.source_sha256 == event.source_sha256
    assert risk.requested_contracts == D("10") and risk.requested_leverage == 10
    with localcontext(Context(prec=100)):
        quantity = D("10") * D("0.01")
        assert risk.base_quantity == quantity
        assert risk.notional == quantity * costs.candidate_entry
        assert risk.required_margin == risk.notional / D(10)
        assert risk.max_loss_amount == quantity * (
            abs(costs.candidate_entry - costs.stop_loss) + costs.cost_per_base
        )
    assert risk.evidence_sha256
    assert all(
        item.execution_authority is False for item in (structure, location, costs, risk)
    )
    assert not hasattr(risk, "order_eligible") and not hasattr(risk, "qualified")
    assert parts == before


@pytest.mark.parametrize("direction", ["long", "short"])
def test_higher_cost_consumes_more_portfolio_risk_without_repricing_or_tightening_stop(
    direction,
):
    parts = _parts(direction)
    event, selected, quote = parts[0][2], parts[1].selected, parts[2]
    before = deepcopy(parts)
    base_cost = parts[-1]
    more_cost = _cost(
        event, selected, quote, policy=cost_policy(round_trip_fee_bps=D("20"))
    )
    assert more_cost.passed
    assert more_cost.cost_per_base > base_cost.cost_per_base
    assert more_cost.net_rr < base_cost.net_rr
    base_risk = _portfolio(parts)
    more_risk = _portfolio((*parts[:-1], more_cost))
    assert base_risk.passed and more_risk.passed
    assert more_risk.max_loss_amount > base_risk.max_loss_amount
    assert more_risk.risk_pct > base_risk.risk_pct
    assert more_risk.notional == base_risk.notional
    assert more_risk.requested_contracts == base_risk.requested_contracts
    assert more_risk.requested_leverage == base_risk.requested_leverage
    failed = _cost(
        event, selected, quote, policy=cost_policy(round_trip_fee_bps=D("50"))
    )
    assert not failed.passed and failed.code == "net_rr_below_minimum"
    for result in (base_cost, more_cost, failed):
        assert (result.candidate_entry, result.stop_loss, result.take_profit) == (
            event.trigger.trigger_price,
            selected.stop.final_stop,
            selected.target.final_target,
        )
        assert result.execution_authority is False
    # No portfolio or order path consumes the failed economics result here.
    assert parts == before


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize(
    "defect,cause",
    [
        ("account_missing", "account_missing"),
        ("armed_false", "demo_authority_denied"),
        ("live_trading", "demo_authority_denied"),
        ("live_order_writes", "demo_authority_denied"),
        ("live_auto_execution", "demo_authority_denied"),
        ("emergency_stop", "emergency_stop_active"),
        ("pending_count", "open_position_limit_exceeded"),
        ("pending_notional", "portfolio_notional_limit_exceeded"),
        ("pending_margin", "available_margin_exceeded"),
        ("drawdown", "drawdown_limit_reached"),
        ("history_gap", "history_invalid"),
        ("history_coverage", "history_coverage_incomplete"),
        ("correlated_positions", "correlation_limit_exceeded"),
        ("correlated_notional", "correlated_notional_limit_exceeded"),
        ("unknown_correlation", "correlation_unknown"),
        ("incomplete_account", "evidence_incomplete"),
    ],
)
def test_valid_market_and_economics_do_not_override_portfolio_denials(
    direction, defect, cause
):
    parts = _parts(direction)
    event = parts[0][2]
    evidence = _risk_evidence(event)
    state, limits, guard = (
        evidence["account"],
        evidence["policy"],
        evidence["authority"],
    )
    if defect == "account_missing":
        evidence["account"] = None
    elif defect == "armed_false":
        evidence["authority"] = guard.model_copy(update={"armed": False})
    elif defect in {
        "live_trading",
        "live_order_writes",
        "live_auto_execution",
        "emergency_stop",
    }:
        # These are fictional evidence fields, never actual settings/environment.
        evidence["authority"] = guard.model_copy(update={defect: True})
    elif defect in {
        "pending_count",
        "pending_notional",
        "pending_margin",
        "correlated_positions",
        "correlated_notional",
    }:
        exposures = {
            "positions": (position(direction=direction),),
            "pending_reservations": (
                reservation(instrument_id="ETH-USDT-SWAP", direction=direction),
            ),
            "position_count": 1,
            "pending_reservation_count": 1,
        }
        evidence["account"] = state.model_copy(update=exposures)
        if defect == "pending_margin":
            evidence["account"] = evidence["account"].model_copy(
                update={"available_margin": D(10)}
            )
        else:
            field, value = {
                "pending_count": ("max_open_positions", 2),
                "pending_notional": ("max_portfolio_notional", D(200)),
                "correlated_positions": ("max_correlated_positions", 2),
                "correlated_notional": ("max_correlated_notional", D(200)),
            }[defect]
            evidence["policy"] = limits.model_copy(update={field: value})
    elif defect == "drawdown":
        evidence["account"] = state.model_copy(
            update={"equity": D(800), "available_margin": D(800)}
        )
    elif defect == "history_gap":
        rows = tuple(
            outcome(
                outcome_id=f"synthetic-risk-chain-outcome-{index}",
                sequence=index,
                closed_at=OBSERVED - timedelta(hours=2 - index // 2),
                realized_pnl=D(1),
            )
            for index in (0, 2)
        )
        evidence["account"] = state.model_copy(update={"loss_history": rows})
    elif defect == "history_coverage":
        evidence["account"] = state.model_copy(
            update={"history_start": OBSERVED - timedelta(days=6)}
        )
    elif defect == "unknown_correlation":
        evidence["instrument"] = evidence["instrument"].model_copy(
            update={"correlation_group": "unknown"}
        )
    else:
        evidence["account"] = state.model_copy(
            update={
                "balance_stamp": state.balance_stamp.model_copy(
                    update={"complete": False}
                )
            }
        )
    before = deepcopy(parts)
    risk = _portfolio(parts, **evidence)
    assert not risk.passed and risk.code == "risk_authority_denied"
    assert cause in risk.causes
    assert risk.execution_authority is False
    assert parts[1].protection_valid and parts[-1].passed
    assert parts == before


@pytest.mark.parametrize("direction", ["long", "short"])
def test_cross_instrument_or_wrong_direction_cannot_reuse_valid_structure_and_cost(
    direction,
):
    parts = _parts(direction)
    evidence = _risk_evidence(parts[0][2])
    wrong_instrument = evidence["instrument"].model_copy(
        update={"instrument_id": "ETH-USDT-SWAP"}
    )
    mismatched = _portfolio(parts, instrument=wrong_instrument)
    assert not mismatched.passed and "instrument_mismatch" in mismatched.causes
    opposite = _portfolio(parts, direction="short" if direction == "long" else "long")
    assert not opposite.passed and "protection_geometry_invalid" in opposite.causes
    assert mismatched.execution_authority is opposite.execution_authority is False


def test_cost_cannot_use_quote_from_another_report_even_with_identical_prices():
    parts = _parts("long")
    quote = parts[2].model_copy(update={"report_id": "different_synthetic_report"})
    failed = _cost(parts[0][2], parts[1].selected, quote)
    assert not failed.passed and failed.code == "identity_mismatch"
    assert failed.candidate_entry == parts[-1].candidate_entry
    assert failed.stop_loss == parts[-1].stop_loss
    assert failed.execution_authority is False


def test_account_failure_never_replaces_missing_evidence_with_empty_positions():
    parts = _parts("long")
    known_empty = _portfolio(parts)
    missing = _portfolio(parts, account=None)
    assert known_empty.passed
    assert not missing.passed and missing.causes == ("account_missing",)
    assert missing.notional is None and missing.evidence_sha256 is None
    assert missing.execution_authority is False


@pytest.mark.parametrize("field", STAMP_FIELDS)
def test_live_account_evidence_cannot_borrow_synthetic_demo_authority(field):
    parts = _parts("long")
    evidence = _risk_evidence(parts[0][2])
    state = evidence["account"]
    demo_stamp = getattr(state, field)
    assert demo_stamp.environment == "demo"
    live_stamp = demo_stamp.model_copy(update={"environment": "live"})
    state = state.model_copy(update={field: live_stamp})
    assert evidence["authority"].environment == "demo"
    risk = _portfolio(parts, account=state)
    assert not risk.passed and "account_invalid" in risk.causes
    assert risk.execution_authority is False


@pytest.mark.parametrize("direction", ["long", "short"])
def test_conflicting_group_for_existing_and_pending_other_instrument_still_denies(
    direction,
):
    parts = _parts(direction)
    state = _risk_evidence(parts[0][2])["account"]
    state = state.model_copy(
        update={
            "positions": (
                position(instrument_id="ETH-USDT-SWAP", correlation_group="group-a"),
            ),
            "pending_reservations": (
                reservation(instrument_id="ETH-USDT-SWAP", correlation_group="group-b"),
            ),
            "position_count": 1,
            "pending_reservation_count": 1,
        }
    )
    risk = _portfolio(parts, account=state)
    assert not risk.passed and "correlation_identity_mismatch" in risk.causes
    assert risk.execution_authority is False


@pytest.mark.parametrize("module", [economics, portfolio])
def test_pure_cost_and_portfolio_modules_have_no_exchange_runtime_or_io_imports(module):
    tree = ast.parse(inspect.getsource(module))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = (
        "app.exchange",
        "app.demo_automation",
        "app.live_automation",
        "app.config",
        "app.market.service",
        "requests",
        "httpx",
        "socket",
        "subprocess",
        "sqlalchemy",
        "os",
        "pathlib",
    )
    assert not any(
        name == prefix or name.startswith(prefix + ".")
        for name in imports
        for prefix in forbidden
    )
    assert not any(isinstance(node, ast.AsyncFunctionDef) for node in ast.walk(tree))
