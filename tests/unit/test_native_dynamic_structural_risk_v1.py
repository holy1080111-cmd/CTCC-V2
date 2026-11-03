"""Fictional DATA calculations only: no native owner, source or DB credit."""

from datetime import timedelta
from decimal import Decimal, localcontext

import pytest

from app.domain.source_primitives import decode
from app.strategies import structural_protection as protection
from app.trade_qualification import account_ledger_claims_v2 as codec
from app.trade_qualification import native_dynamic_structural_risk_v1 as native
from app.trade_qualification.models import EntryQualificationResult, EntryZone
from tests.unit.strategies.test_derivative_confirmation import _view
from tests.unit.strategies.test_mathematical_confirmation import _analysis, _core
from tests.unit.test_account_ledger_claims_finite_v1 import (
    finite_claims as finite_claims,  # noqa: PLC0414 -- Pytest collects this imported fixture.
)
from tests.unit.test_demo_structural_risk import structural_settings
from tests.unit.test_qualification_portfolio import NOW, instrument

D = Decimal


@pytest.fixture
def operands(finite_claims):
    settings = structural_settings()
    projected = native.financial._configured_policy_from_settings_v1(
        settings,
        drawdown_window_started_at=finite_claims.risk_policy.drawdown_window_started_at,
    )
    policy = projected.risk_policy
    financial = finite_claims.financial.model_copy(
        update={
            "original_risk_policy_sha256": codec.finite_risk_policy_sha256_v1(policy),
            "position_margin_bucket_usdt": projected.position_margin_bucket_usdt,
        }
    )
    claims = finite_claims.model_copy(
        update={"risk_policy": policy, "financial": financial}
    )
    claims = codec.decode_finite_account_ledger_claims_v1(
        codec.encode_finite_account_ledger_claims_v1(claims)
    )
    expiry = NOW + timedelta(seconds=10)
    result = EntryQualificationResult(
        report_id="pure-native-dynamic-risk",
        symbol="BTC/USDT:USDT",
        strategy="trend_pullback",
        direction="long",
        evaluated_at=NOW,
        raw_score=99,
        effective_score=99,
        candidate_entry=D(100),
        stop_loss=D(99),
        take_profit=D(105),
        entry_zone=EntryZone(
            report_id="pure-native-dynamic-risk",
            zone_low=D("99.5"),
            zone_high=D("100.5"),
            zone_type="fixed",
            zone_source="fictional DATA",
            created_at=NOW - timedelta(seconds=1),
            expires_at=expiry,
            max_allowed_drift_bps=D(30),
            invalidation_price=D(98),
        ),
    )
    stop_anchor = protection.StructuralAnchor(
        "fictional-stop",
        "15m",
        "fixture",
        D("99.5"),
        NOW - timedelta(minutes=15),
        NOW - timedelta(seconds=1),
        D(1),
    )
    target_anchor = protection.StructuralAnchor(
        "fictional-target",
        "15m",
        "fixture",
        D(105),
        NOW - timedelta(minutes=15),
        NOW - timedelta(seconds=1),
        D(1),
    )
    stop = protection.StopAlternative(
        stop_anchor, D("0.5"), D("0.5"), D(99), D("0.01"), D(1), D("0.5"), (), ()
    )
    target = protection.TargetAlternative(target_anchor, D(105), ())
    bracket = protection.ProtectionBracket(
        stop, target, D(5), D(4), (), "fictional DATA"
    )
    selection = protection.StructuralProtectionSelection(
        result.report_id,
        "BTC-USDT-SWAP",
        "long",
        D(100),
        NOW,
        "1" * 64,
        "confirmed_ohlc",
        (stop,),
        (target,),
        (bracket,),
        bracket,
        (),
        (),
        "fictional DATA",
    )
    analysis = _analysis(_core())
    analysis.timeframe_analyses = {
        tf: _view(tf, velocity_ratio="0.9") for tf in ("4H", "1H", "15m", "5m")
    }
    return {
        "settings": settings,
        "analysis": analysis,
        "selection": selection,
        "result": result,
        "claims": claims,
        "instrument": instrument(),
        "contracts": D("0.1"),
        "entry": D(100),
        "cost_per_base": D("0.16"),
        "raw_score": 99,
        "effective_score": 99,
        "leverage_ceiling": 20,
        "configured_leverage": 20,
        "expires_at": expiry,
    }


@pytest.mark.parametrize("score,step", [(72, 3), (80, 5), (90, 8), (95, 10), (98, 20)])
def test_reuses_existing_five_quality_caps_without_authority(operands, score, step):
    data = native.evaluate_native_dynamic_risk_data_v1(
        **{
            **operands,
            "raw_score": score,
            "effective_score": score,
            "configured_leverage": step,
        }
    )
    receipt = decode(data.receipt_json)
    assert receipt["candidate_leverage_cap"] == step
    assert data.selected_leverage == step
    assert receipt["execution_authority"] is data.execution_authority is False
    assert receipt["admission"] == "DENY"
    assert receipt["aggregate_margin_denominator"] == "settlement_account_equity"


def test_lower_caller_ceiling_and_actual_configured_step_remain_legal(operands):
    data = native.evaluate_native_dynamic_risk_data_v1(
        **{**operands, "leverage_ceiling": 3, "configured_leverage": 3}
    )
    assert data.selected_leverage == 3
    assert decode(data.receipt_json)["candidate_leverage_cap"] == 3


def test_lower_actual_configured_step_does_not_force_an_upgrade(operands):
    data = native.evaluate_native_dynamic_risk_data_v1(
        **{**operands, "raw_score": 90, "effective_score": 90, "configured_leverage": 3}
    )
    assert data.selected_leverage == 3
    assert decode(data.receipt_json)["candidate_leverage_cap"] == 8


def test_requesting_20_cannot_lift_a_lower_actual_candidate_cap(operands):
    with pytest.raises(
        native.NativeDynamicRiskError, match="configured_leverage_above_candidate_cap"
    ):
        native.evaluate_native_dynamic_risk_data_v1(
            **{**operands, "raw_score": 72, "effective_score": 72}
        )


def test_explicit_zero_risk_score_is_not_replaced_with_raw_99(operands):
    with pytest.raises(
        native.NativeDynamicRiskError, match="mathematical_risk_score_rejected"
    ):
        native.evaluate_native_dynamic_risk_data_v1(
            **{**operands, "effective_score": 0}
        )


def test_insufficient_actual_derivatives_cannot_qualify_configured_20(operands):
    operands["analysis"].timeframe_analyses = {}
    with pytest.raises(
        native.NativeDynamicRiskError, match="configured_leverage_above_candidate_cap"
    ):
        native.evaluate_native_dynamic_risk_data_v1(**operands)


def test_fixed_quantity_must_fit_its_score_risk_budget(operands):
    with pytest.raises(native.NativeDynamicRiskError, match="score_tier_risk_exceeded"):
        native.evaluate_native_dynamic_risk_data_v1(
            **{**operands, "contracts": D(1000)}
        )


def test_ambient_decimal_context_cannot_change_decision(operands):
    expected = native.evaluate_native_dynamic_risk_data_v1(**operands)
    with localcontext() as context:
        context.prec = 6
        actual = native.evaluate_native_dynamic_risk_data_v1(**operands)
    assert expected.receipt_json == actual.receipt_json


def test_public_data_and_foreign_key_cannot_replace_actual_g11_or_r7_actor():
    with pytest.raises(native.NativeDynamicRiskError, match="fixed_actor_required"):
        native.evaluate_actor_native_dynamic_risk_v1(
            None,
            None,
            result=None,
            analysis=None,
            entry=None,
            cost_per_base=None,
            raw_score=None,
            effective_score=None,
            contracts=None,
            leverage_ceiling=None,
            expires_at=None,
            key=object(),
        )


def test_native_financial_policy_projection_and_dynamic_settings_are_distinct(operands):
    projected = native.financial._configured_policy_from_settings_v1(
        operands["settings"],
        drawdown_window_started_at=operands[
            "claims"
        ].risk_policy.drawdown_window_started_at,
    )
    dynamic, actual = native.validate_native_settings_projection_v1(
        operands["settings"], operands["claims"], projected.selected_settings_sha256
    )
    assert actual.selected_settings_sha256 == projected.selected_settings_sha256
    assert dynamic.sha256 != actual.selected_settings_sha256
    assert actual.execution_authority is dynamic.execution_authority is False


def test_legacy_dynamic_digest_cannot_replace_native_financial_policy_pin(operands):
    dynamic = native.risk_config.freeze_demo_risk_config(operands["settings"])
    with pytest.raises(
        native.NativeDynamicRiskError, match="native_financial_projection_changed"
    ):
        native.validate_native_settings_projection_v1(
            operands["settings"], operands["claims"], dynamic.sha256
        )
