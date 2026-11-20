"""Synthetic Live calculations only; no authentic source, Arm or order IO."""

import json
from dataclasses import fields, replace
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.trade_qualification import engine, portfolio
from app.trade_qualification import executable_quote_live_v1 as live_quote
from tests.unit import test_executable_quote_v2 as quote_fixture
from tests.unit import test_qualification_portfolio as risk_fixture
from tests.unit.qualification_engine_fixtures import engine_inputs, engine_source


def _stamp(value):
    return portfolio.LiveEvidenceStamp(
        environment="live",
        account_id=value.account_id,
        source_sha256=value.source_sha256,
        observed_at=value.observed_at,
        received_at=value.received_at,
        complete=value.complete,
    )


def _account(value):
    raw = value.model_dump(mode="python")
    for name in risk_fixture.STAMP_FIELDS:
        raw[name] = _stamp(getattr(value, name))
    return portfolio.LivePortfolioRiskSnapshot(**raw)


def _authority(stamp):
    return portfolio.LiveRiskAuthority(
        stamp=stamp,
        environment="live",
        live_enabled=True,
        armed=True,
        order_writes_allowed=True,
        one_shot=True,
        emergency_stop=False,
        demo_enabled=False,
        automation_enabled=False,
        simulated_trading_header=None,
        credential_session_sha256="a" * 64,
        arm_id="b" * 64,
    )


def _risk_inputs():
    return {
        "report_id": "declared-synthetic-live-risk",
        "instrument_id": risk_fixture.INSTRUMENT,
        "direction": "long",
        "candidate_entry": Decimal(100),
        "stop_loss": Decimal(95),
        "round_trip_cost_per_base": Decimal("0.1"),
        "requested_contracts": Decimal(10),
        "requested_leverage": 10,
        "instrument": risk_fixture.instrument(),
        "account": _account(risk_fixture.account()),
        "policy": risk_fixture.policy(),
        "authority": _authority(_stamp(risk_fixture.stamp())),
        "current_time": risk_fixture.NOW,
    }


def test_live_risk_uses_same_arithmetic_but_distinct_exact_environment_claims():
    args = _risk_inputs()
    live = portfolio.evaluate_live_portfolio(**args)
    assert live.passed and live.execution_authority is False
    demo = portfolio.evaluate_portfolio(
        **(
            args
            | {"account": risk_fixture.account(), "authority": risk_fixture.authority()}
        )
    )
    for name in ("notional", "required_margin", "max_loss_amount", "risk_pct"):
        assert getattr(live, name) == getattr(demo, name)
    assert live.evidence_sha256 != demo.evidence_sha256
    assert {"account_invalid", "authority_invalid"} <= set(
        portfolio.evaluate_portfolio(**args).causes
    )
    assert {"account_invalid", "authority_invalid"} <= set(
        portfolio.evaluate_live_portfolio(
            **(
                args
                | {
                    "account": risk_fixture.account(),
                    "authority": risk_fixture.authority(),
                }
            )
        ).causes
    )


@pytest.mark.parametrize(
    "field,value,cause",
    [
        ("live_enabled", False, "live_authority_denied"),
        ("armed", False, "live_authority_denied"),
        ("order_writes_allowed", False, "live_authority_denied"),
        ("one_shot", False, "live_authority_denied"),
        ("demo_enabled", True, "live_authority_denied"),
        ("automation_enabled", True, "live_authority_denied"),
        ("emergency_stop", True, "emergency_stop_active"),
    ],
)
def test_live_control_claim_failure_remains_denied(field, value, cause):
    args = _risk_inputs()
    raw = args["authority"].model_dump(mode="python") | {field: value}
    args["authority"] = portfolio.LiveRiskAuthority(**raw)
    result = portfolio.evaluate_live_portfolio(**args)
    assert not result.passed and cause in result.causes
    assert result.execution_authority is False


def test_live_uses_structural_hard_cap_even_with_loose_requested_policy():
    args = _risk_inputs() | {"requested_contracts": Decimal(100)}
    result = portfolio.evaluate_live_portfolio(**args)
    assert not result.passed and "trade_risk_limit_exceeded" in result.causes
    assert result.max_loss_amount == Decimal("5.1")


def test_live_uses_aggregate_margin_hard_cap():
    args = _risk_inputs()
    args["account"] = _account(
        risk_fixture.account(
            positions=(
                risk_fixture.position(notional=Decimal(600), margin=Decimal(600)),
            )
        )
    )
    result = portfolio.evaluate_live_portfolio(**args)
    assert not result.passed and "portfolio_margin_limit_exceeded" in result.causes


def test_live_unknown_stale_and_other_account_evidence_cannot_qualify():
    args = _risk_inputs()
    raw = args["account"].model_dump(mode="python")
    for name, update in (
        ("balance_stamp", {"complete": False}),
        ("positions_stamp", {"account_id": "other-live-account"}),
        ("history_stamp", {"received_at": risk_fixture.NOW + timedelta(seconds=1)}),
    ):
        changed = dict(raw)
        changed[name] = portfolio.LiveEvidenceStamp(**(raw[name] | update))
        result = portfolio.evaluate_live_portfolio(
            **(args | {"account": portfolio.LivePortfolioRiskSnapshot(**changed)})
        )
        assert not result.passed


def test_live_simulated_header_and_demo_stamp_are_structurally_refused():
    args = _risk_inputs()
    with pytest.raises(ValidationError):
        portfolio.LiveRiskAuthority(
            **(
                args["authority"].model_dump(mode="python")
                | {"simulated_trading_header": "1"}
            )
        )
    with pytest.raises(ValidationError):
        portfolio.LivePortfolioRiskSnapshot(
            **(
                args["account"].model_dump(mode="python")
                | {"balance_stamp": risk_fixture.stamp()}
            )
        )


def test_live_g1_g11_replays_the_original_gates_and_cannot_become_demo_run():
    source = engine_source()
    args = engine_inputs(source)
    original = args["risk_inputs"]
    account = _account(original.account)
    args["risk_inputs"] = engine.LivePortfolioInputs(
        requested_contracts=original.requested_contracts,
        requested_leverage=original.requested_leverage,
        instrument=original.instrument,
        account=account,
        authority=_authority(account.balance_stamp),
    )
    result = engine.evaluate_live_pre_evidence(source.market, **args)
    assert type(result) is engine.LivePreEvidenceRun
    assert result.pre_evidence_complete and result.portfolio.passed
    assert result.execution_authority is False
    assert engine.verify_live_pre_evidence(result, source.market, **args) == result
    with pytest.raises(ValueError):
        engine.evaluate_pre_evidence(source.market, **args)
    with pytest.raises(ValueError):
        engine.verify_pre_evidence(result, source.market, **args)


def _quote():
    original = quote_fixture.quote()
    return live_quote.LiveExecutableQuoteV1(
        **{
            field.name: (
                "live" if field.name == "environment" else getattr(original, field.name)
            )
            for field in fields(original)
            if field.init
        }
    )


def test_live_quote_components_share_strict_clocks_but_no_demo_scope():
    quote = _quote()
    result = json.loads(
        live_quote.inspect_live_executable_quote_v1(
            quote, current_time=quote_fixture.NOW
        ).receipt_json
    )
    assert result["profile_satisfied"] is True
    assert result["quote_document"]["environment"] == "live"
    assert result["admission"] == "DENY" and result["execution_authority"] is False
    assert quote_fixture.result(quote)["profile_satisfied"] is False
    assert (
        json.loads(
            live_quote.inspect_live_executable_quote_v1(
                quote_fixture.quote(), current_time=quote_fixture.NOW
            ).receipt_json
        )["profile_satisfied"]
        is False
    )
    future = replace(
        quote,
        ticker=replace(
            quote.ticker, source_generated_at=quote_fixture.NOW + timedelta(seconds=1)
        ),
    )
    denied = json.loads(
        live_quote.inspect_live_executable_quote_v1(
            future, current_time=quote_fixture.NOW
        ).receipt_json
    )
    assert denied["profile_satisfied"] is False
