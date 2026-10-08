"""Bounded Demo risk-config identity; no source authentication or order permit.

The selected values come from an exact Settings instance. A caller must still
prove where that instance came from, own the account source, and pass the
existing control/reservation checks. This module cannot grant those facts.
"""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from app.config.settings import Settings
from app.trade_qualification import demo_control as control
from app.trade_qualification.portfolio import PortfolioRiskPolicy

VERSION = "ctcc-owned-demo-risk-config-subset-v1"
RISK_CEILING = Decimal("0.005")
PORTFOLIO_RISK_CEILING = Decimal("0.01")
MARGIN_CEILING = Decimal("0.60")
BUCKET_CEILING = Decimal(300)
LEVERAGE_CEILINGS = (3, 5, 8, 10, 20)

_ENABLED = (
    "okx_demo_score_risk_enabled",
    "okx_demo_capital_bucket_enabled",
    "okx_demo_structural_dynamic_leverage_enabled",
    "okx_demo_continuous_session_enabled",
    "okx_demo_require_protection",
)
_RISK_TIERS = (
    "okx_demo_structural_low_risk_pct",
    "okx_demo_structural_medium_risk_pct",
    "okx_demo_structural_high_risk_pct",
    "okx_demo_structural_elite_risk_pct",
    "okx_demo_structural_extreme_risk_pct",
)
_LEVERAGE_TIERS = (
    "okx_demo_structural_low_leverage_cap",
    "okx_demo_structural_medium_leverage_cap",
    "okx_demo_structural_high_leverage_cap",
    "okx_demo_structural_elite_leverage_cap",
    "okx_demo_structural_extreme_leverage_cap",
)
_SCORE_THRESHOLDS = (
    "strategy_min_score",
    "okx_demo_score_medium_min",
    "okx_demo_score_high_min",
    "okx_demo_structural_score_elite_min",
    "okx_demo_structural_score_extreme_min",
)
_COSTS = (
    "okx_demo_structural_round_trip_fee_bps",
    "okx_demo_structural_round_trip_slippage_bps",
    "okx_demo_structural_funding_buffer_bps",
)
_DECIMALS = (
    *_RISK_TIERS,
    "okx_demo_portfolio_max_risk_pct",
    "okx_demo_portfolio_max_margin_pct",
    "okx_demo_position_margin_bucket_usdt",
    "okx_demo_daily_loss_limit_pct",
    *_COSTS,
    "okx_demo_structural_min_net_risk_reward",
    "okx_demo_structural_20x_min_confidence",
    "okx_demo_structural_20x_min_reliability",
    "okx_demo_structural_20x_max_instability",
)
_INTEGERS = (
    *_LEVERAGE_TIERS,
    *_SCORE_THRESHOLDS,
    "okx_demo_max_leverage",
    "okx_demo_automation_max_consecutive_losses",
    "okx_demo_max_open_positions",
    "okx_demo_trade_cooldown_seconds",
)


class OwnedRiskPolicyError(ValueError):
    """Stable local refusal, without config or credential values in the text."""


def _deny(code: str) -> None:
    raise OwnedRiskPolicyError(code)


def _plain_decimal(value: Decimal) -> str:
    if not value.is_finite():
        _deny("owned_risk_nonfinite_value")
    if value.is_zero():
        return "0"
    raw = format(value, "f")
    return raw.rstrip("0").rstrip(".") if "." in raw else raw


@dataclass(frozen=True, slots=True, repr=False)
class OwnedDemoRiskPolicyIdentity:
    canonical_json: str
    sha256: str

    @property
    def admission(self) -> str:
        return "DENY"

    @property
    def execution_authority(self) -> bool:
        return False


def freeze_demo_risk_config(settings: Settings) -> OwnedDemoRiskPolicyIdentity:
    """Freeze only explicitly selected nonsecret Settings fields, fail closed.

    An exact Settings record is necessary but does not authenticate environment,
    file, operator, account, deployment, or the live process using it.
    """
    if (
        type(settings) is not Settings
        or set(settings.__dict__) != set(Settings.model_fields)
        or settings.__pydantic_extra__
    ):
        _deny("owned_risk_exact_settings_required")
    # Copy immutable scalar references once; a mutable Settings object's later
    # fields must not be reread into a different policy within this call.
    values = {
        name: settings.__dict__[name] for name in (*_ENABLED, *_DECIMALS, *_INTEGERS)
    }
    if any(type(values[name]) is not bool or not values[name] for name in _ENABLED):
        _deny("owned_risk_required_guard_disabled")
    if any(type(values[name]) is not Decimal for name in _DECIMALS):
        _deny("owned_risk_decimal_missing_or_invalid")
    if any(type(values[name]) is not int for name in _INTEGERS):
        _deny("owned_risk_integer_missing_or_invalid")
    if any(not values[name].is_finite() for name in _DECIMALS):
        _deny("owned_risk_nonfinite_value")

    risks = tuple(values[name] for name in _RISK_TIERS)
    leverage = tuple(values[name] for name in _LEVERAGE_TIERS)
    scores = tuple(values[name] for name in _SCORE_THRESHOLDS)
    if (
        any(not 0 < rate <= RISK_CEILING for rate in risks)
        or tuple(sorted(risks)) != risks
        or not risks[-1] <= values["okx_demo_portfolio_max_risk_pct"]
        or not 0 < values["okx_demo_portfolio_max_risk_pct"] <= PORTFOLIO_RISK_CEILING
        or not 0 < values["okx_demo_portfolio_max_margin_pct"] <= MARGIN_CEILING
        or not 0 < values["okx_demo_position_margin_bucket_usdt"] <= BUCKET_CEILING
        or not 0 < values["okx_demo_daily_loss_limit_pct"] <= Decimal("0.01")
    ):
        _deny("owned_risk_cap_weakened_or_invalid")
    if (
        leverage != LEVERAGE_CEILINGS
        or values["okx_demo_max_leverage"] != 20
        or scores[0] < 72
        or any(current < floor for current, floor in zip(scores[1:], (80, 90, 95, 98)))
        or tuple(sorted(set(scores))) != scores
        or scores[-1] > 100
        or not 1 <= values["okx_demo_automation_max_consecutive_losses"] <= 3
        or not 2 <= values["okx_demo_max_open_positions"] <= 10
        or values["okx_demo_trade_cooldown_seconds"] != 0
    ):
        _deny("owned_risk_tier_or_guard_invalid")
    if (
        any(values[name] < 0 for name in _COSTS)
        or sum((values[name] for name in _COSTS), Decimal(0)) < Decimal(16)
        or values["okx_demo_structural_min_net_risk_reward"] < 2
        or values["okx_demo_structural_20x_min_confidence"] < Decimal("0.65")
        or values["okx_demo_structural_20x_min_reliability"] < Decimal("0.65")
        or values["okx_demo_structural_20x_max_instability"] > Decimal("0.20")
        or values["okx_demo_structural_20x_max_instability"] < 0
    ):
        _deny("owned_risk_20x_quality_weakened_or_invalid")

    document = {
        "version": VERSION,
        "settings": {
            **{name: values[name] for name in _ENABLED},
            **{name: _plain_decimal(values[name]) for name in _DECIMALS},
            **{name: values[name] for name in _INTEGERS},
        },
        "admission": "DENY",
        "execution_authority": False,
        "source_authenticity_verified": False,
    }
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return OwnedDemoRiskPolicyIdentity(
        canonical_json=raw,
        sha256=hashlib.sha256(raw.encode("ascii")).hexdigest(),
    )


def guard_owned_risk_policy_identity(
    settings: Settings,
    state: control.ControlState,
    qualification_policy: PortfolioRiskPolicy,
) -> OwnedDemoRiskPolicyIdentity:
    """Compare a fresh selected-config digest with the durable control pin.

    The caller must independently bind ``qualification_policy`` to the original
    G1--G12 digest and verify current control inside the account transaction.
    This guard never supplies either of those missing runtime authorities.
    """
    identity = freeze_demo_risk_config(settings)
    control.checked(state, control.ControlState)
    if state.pins is None or state.pins.config_sha256 != identity.sha256:
        _deny("owned_risk_control_config_conflict")
    if (
        type(qualification_policy) is not PortfolioRiskPolicy
        or set(qualification_policy.__dict__) != set(PortfolioRiskPolicy.model_fields)
        or qualification_policy.__pydantic_extra__
    ):
        _deny("owned_risk_exact_qualification_policy_required")
    try:
        policy = PortfolioRiskPolicy.model_validate(
            qualification_policy.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (ValueError, TypeError, RecursionError):
        _deny("owned_risk_qualification_policy_invalid")
    selected = json.loads(identity.canonical_json)["settings"]
    if (
        policy.risk_per_trade_pct > RISK_CEILING
        or policy.risk_per_trade_pct
        > max(Decimal(selected[name]) for name in _RISK_TIERS)
        or policy.max_portfolio_risk_pct
        > Decimal(selected["okx_demo_portfolio_max_risk_pct"])
        or policy.max_portfolio_margin_pct
        > Decimal(selected["okx_demo_portfolio_max_margin_pct"])
        or policy.max_leverage > selected["okx_demo_max_leverage"]
        or policy.max_daily_loss_pct
        > Decimal(selected["okx_demo_daily_loss_limit_pct"])
        or policy.max_consecutive_losses
        > selected["okx_demo_automation_max_consecutive_losses"]
        or policy.max_open_positions > selected["okx_demo_max_open_positions"]
    ):
        _deny("owned_risk_qualification_cap_conflict")
    return identity
