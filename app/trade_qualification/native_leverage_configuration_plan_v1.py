"""Prospective flat-account DATA using the existing structural risk engines.

This does not relabel a desired value as an observed configuration, mint an
account revision, establish historical financial facts or permit any POST.
The native configuration producer must authenticate and replay every input.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from fractions import Fraction

from app.config.settings import Settings
from app.domain import strategy as candidate_models
from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.source_primitives import canonical, sha
from app.strategies import derivative_confirmation as derivatives
from app.strategies import mathematical_confirmation as mathematics
from app.trade_qualification import captured_execution_mode_v1 as execution
from app.trade_qualification import hard_risk_caps as hard
from app.trade_qualification import native_dynamic_structural_risk_v1 as dynamic
from app.trade_qualification import owned_current_original_prefix_v1 as local
from app.trade_qualification import owned_demo_risk_policy as risk_config
from app.trade_qualification import portfolio

VERSION = "ctcc.native_flat_leverage_configuration.plan.v1"
LEVERAGE_CEILINGS = (3, 5, 8, 10, 20)


class NativeLeverageConfigurationPlanError(ValueError):
    pass


def _need(condition, code="native_flat_leverage_configuration_plan_invalid"):
    if condition is not True:
        raise NativeLeverageConfigurationPlanError(code) from None


def _number(value, *, positive=True):
    _need(type(value) is Decimal and value.is_finite())
    _need(
        Decimal(0) < value < Decimal("1e20")
        if positive
        else Decimal(0) <= value < Decimal("1e20")
    )
    _need(len(value.as_tuple().digits) <= 40 and value.as_tuple().exponent >= -20)
    return value


@dataclass(frozen=True, slots=True, repr=False)
class NativeLeverageConfigurationPlanDataV1:
    configured_leverage: int
    selected_leverage: int
    caller_ceiling: int
    candidate_cap: int
    request_json: bytes
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return sha(self.receipt_json)

    @property
    def execution_authority(self):
        return False


def select_flat_configuration_plan_data_v1(
    *,
    settings,
    analysis,
    result,
    selection,
    selected_execution,
    instrument,
    contracts,
    cost_per_base,
    account_equity,
    available_margin,
    risk_policy,
    position_margin_bucket_usdt,
    raw_score,
    effective_score,
    leverage_ceiling,
    expires_at,
):
    """Compute a proposal; typed DATA and this result grant no source authority.

    Current configuration and future target are separate values. Preserve a
    valid lower existing step. An explicit configuration action may raise an
    unsupported 1x/2x setting only to the minimum permitted ladder member;
    it never forces the solver's preferred full-budget upgrade or resizes.
    """
    _need(type(settings) is Settings and type(analysis) is MultiTimeframeAnalysis)
    _need(
        type(result)
        in (local.EntryQualificationResult, local.OwnedFamilyEntryQualificationResultV2)
    )
    _need(
        type(instrument) is portfolio.ContractRiskSpec
        and type(risk_policy) is portfolio.PortfolioRiskPolicy
    )
    _need(type(selected_execution) is execution.SelectedIsolatedExecutionV1)
    _need(
        type(raw_score) is int
        and 0 <= raw_score <= 100
        and type(effective_score) is int
        and 0 <= effective_score <= raw_score
    )
    _need(type(leverage_ceiling) is int and leverage_ceiling in LEVERAGE_CEILINGS)
    _need(
        type(expires_at) is datetime
        and expires_at.tzinfo is UTC
        and expires_at <= result.entry_zone.expires_at
    )
    for value in (
        contracts,
        account_equity,
        available_margin,
        position_margin_bucket_usdt,
    ):
        _number(value)
    _number(cost_per_base, positive=False)
    _need(
        type(selected_execution.position_mode) is str
        and selected_execution.position_mode in ("net_mode", "long_short_mode")
    )
    expected_side = (
        "net" if selected_execution.position_mode == "net_mode" else result.direction
    )
    _need(
        expected_side in ("net", "long", "short")
        and selected_execution.position_side == expected_side
    )
    member = selected_execution.observation
    _need(
        type(member) is execution.CapturedLeverageObservationV1
        and type(member.instrument_id) is str
        and type(member.margin_mode) is str
        and type(member.position_side) is str
        and member.instrument_id == instrument.instrument_id
        and member.margin_mode == "isolated"
        and member.position_side == expected_side
        and type(member.raw_member_json) is bytes
        and sha(member.raw_member_json) == member.raw_member_sha256
    )
    actual = _number(selected_execution.leverage)
    _need(
        actual == actual.to_integral_value()
        and Decimal(1) <= actual <= Decimal(125)
        and member.leverage == actual
    )
    configured = int(actual)
    identity = risk_config.freeze_demo_risk_config(settings)
    with localcontext(Context(prec=100)):
        entry = _number(result.candidate_entry)
        cost = max(
            cost_per_base,
            entry * dynamic.structural_risk.structural_cost_rate(settings),
        )
        stop, target = _number(result.stop_loss), _number(result.take_profit)
        price_risk, reward = abs(entry - stop), abs(target - entry)
        _need(price_risk > 0 and reward > cost)
        net_rr = (reward - cost) / (price_risk + cost)
        _need(net_rr >= settings.okx_demo_structural_min_net_risk_reward)
        candidate = candidate_models.TradeCandidate(
            strategy=result.strategy,
            direction=result.direction,
            score=raw_score,
            risk_score=effective_score,
            entry=entry,
            stop_loss=stop,
            take_profit=target,
            risk_reward=net_rr,
            invalidation="fixed original structural stop",
            expires_at=expires_at,
            mathematical_confirmation=mathematics.mathematical_confirmation(
                analysis, result.direction
            ),
            derivative_confirmation=derivatives.derivative_confirmation(
                analysis, result.direction
            ),
            protection_model="structure",
            structural_protection=dynamic._geometry(selection, result),
            estimated_round_trip_cost_pct=cost / entry,
            gross_risk_reward=reward / price_risk,
            net_risk_reward=net_rr,
        )
        score, rejection = dynamic.risk_profile.mathematical_adjusted_score(
            candidate, settings
        )
        _need(rejection is None and score >= settings.strategy_min_score)
        candidate = candidate_models.TradeCandidate.model_validate(
            {**candidate.model_dump(), "risk_score": score}
        )
        tier = dynamic.risk_profile.score_risk_tier(score, settings)
        tier_risk = min(tier.risk_pct, risk_policy.risk_per_trade_pct, Decimal("0.005"))
        maximum = min(
            tier.leverage,
            leverage_ceiling,
            instrument.max_leverage,
            risk_policy.max_leverage,
            20,
        )
        allowed = tuple(value for value in LEVERAGE_CEILINGS if value <= maximum)
        _need(len(allowed) > 0, "native_flat_leverage_no_permitted_step")
        solver_tier = type(tier).model_validate(
            {**tier.model_dump(), "leverage": allowed[-1], "risk_pct": tier_risk}
        )
        margin_cap = min(
            account_equity,
            available_margin,
            position_margin_bucket_usdt,
            settings.okx_demo_position_margin_bucket_usdt,
            Decimal(300),
            account_equity
            * min(
                risk_policy.max_portfolio_margin_pct,
                settings.okx_demo_portfolio_max_margin_pct,
                Decimal("0.60"),
            ),
        )
        _need(margin_cap > 0)
        solved = dynamic.structural_risk.select_structural_leverage(
            candidate,
            solver_tier,
            settings,
            account_equity=account_equity,
            position_margin_cap=margin_cap,
        )
        allowed = tuple(value for value in allowed if value <= solved.leverage_cap)
        _need(len(allowed) > 0, "native_flat_leverage_no_source_quality_step")
        lower = tuple(value for value in allowed if value <= configured)
        selected = (
            configured if configured in allowed else lower[-1] if lower else allowed[0]
        )
        risk = hard.rounded_up_amount(
            Fraction(contracts)
            * Fraction(instrument.contract_value)
            * (abs(Fraction(entry) - Fraction(stop)) + Fraction(cost))
        )
        margin = hard.rounded_up_amount(
            Fraction(contracts)
            * Fraction(instrument.contract_value)
            * Fraction(entry)
            / selected
        )
        _need(
            risk <= Fraction(account_equity) * Fraction(tier_risk),
            "native_flat_leverage_fixed_quantity_risk",
        )
        _need(
            margin <= Fraction(margin_cap), "native_flat_leverage_fixed_quantity_margin"
        )
        request = {
            "instId": instrument.instrument_id,
            "lever": str(selected),
            "mgnMode": "isolated",
        }
        if selected_execution.position_mode == "long_short_mode":
            request["posSide"] = expected_side
        request_json = canonical(request)
        receipt = canonical(
            {
                "schema_version": VERSION,
                "dynamic_settings_sha256": identity.sha256,
                "action": "NO_CHANGE" if selected == configured else "SET_LEVERAGE",
                "configured_leverage": configured,
                "target_leverage": selected,
                "caller_ceiling": leverage_ceiling,
                "candidate_cap": solved.leverage_cap,
                "solver_preferred_leverage": solved.selected_leverage,
                "source_execution_receipt_sha256": selected_execution.receipt_sha256,
                "source_leverage_member_sha256": member.raw_member_sha256,
                "position_mode": selected_execution.position_mode,
                "raw_score": raw_score,
                "original_effective_score": effective_score,
                "risk_score": score,
                "mathematical_confirmation": candidate.mathematical_confirmation.model_dump(
                    mode="json"
                ),
                "derivative_confirmation": candidate.derivative_confirmation.model_dump(
                    mode="json"
                ),
                "tier_risk_pct": str(tier_risk),
                "twenty_x_eligible": solved.twenty_x_eligible,
                "cap_reasons": list(solved.cap_reasons),
                "equity": str(account_equity),
                "margin_cap": str(margin_cap),
                "contracts": str(contracts),
                "entry": str(entry),
                "stop_loss": str(stop),
                "take_profit": str(target),
                "request_sha256": sha(request_json),
                "expires_at": expires_at.isoformat(),
                "historical_financial_authority": None,
                "account_complete": False,
                "execution_authority": False,
                "order_retry_authority": False,
            }
        )
    _need(0 < len(request_json) <= 2048 and 0 < len(receipt) <= 32768)
    return NativeLeverageConfigurationPlanDataV1(
        configured,
        selected,
        leverage_ceiling,
        solved.leverage_cap,
        request_json,
        receipt,
    )
