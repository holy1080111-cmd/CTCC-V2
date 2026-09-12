"""Synthetic regression proof for a pending rollout, not deployment evidence.

The four existing tracked cross/ATR positions are not changed by these tests.
Enabling dynamic leverage does not relabel them as isolated/structural trades.
Restart recovery disarms automation; rearming requires both exchange exposure
and local tracked trades to be flat/resolved. No account, environment, secret,
qualification pipeline, or private runtime service is used here.
"""

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.demo_automation.capital_bucket import build_demo_capital_bucket_plan
from app.demo_automation.risk_profile import (
    configured_score_risk_tiers,
    score_risk_tier,
)
from app.demo_automation.structural_risk import (
    LEVERAGE_LADDER,
    apply_cost_adjusted_reward_risk,
    candidate_with_structural_prices,
    select_structural_leverage,
)
from app.domain.strategy import TradeCandidate
from tests.unit.test_demo_structural_risk import D, structural_candidate

BANDS = ("low", "medium", "high", "elite", "extreme")
CAPS = (3, 5, 8, 10, 20)
RISK = D("0.005")
PORTFOLIO_RISK = D("0.01")
BUCKET = D("300")
DEFAULT_EQUITY = D("2000")
RISK_FIELDS = tuple(f"okx_demo_structural_{band}_risk_pct" for band in BANDS)


class InitOnlySettings(Settings):
    """Use supplied Python values only; never call external settings sources."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (init_settings,)


def restore_values():
    return {
        "okx_demo_score_risk_enabled": True,
        "okx_demo_capital_bucket_enabled": True,
        "okx_demo_continuous_session_enabled": True,
        "okx_demo_trade_cooldown_seconds": 0,
        "okx_demo_structural_dynamic_leverage_enabled": True,
        "okx_demo_max_open_positions": 8,
        "okx_demo_max_leverage": 20,
        "okx_demo_portfolio_max_risk_pct": PORTFOLIO_RISK,
        "okx_demo_portfolio_max_margin_pct": D("0.60"),
        "okx_demo_position_margin_bucket_usdt": BUCKET,
        "max_weekly_loss_pct": 0.10,
        "max_drawdown_pct": 0.10,
        "okx_demo_require_protection": True,
        "okx_demo_automation_leverage": 1,
        "okx_demo_score_low_risk_pct": RISK,
        "okx_demo_score_medium_risk_pct": RISK,
        "okx_demo_score_high_risk_pct": RISK,
        "okx_demo_score_low_leverage": 1,
        "okx_demo_score_medium_leverage": 1,
        "okx_demo_score_high_leverage": 1,
        **dict.fromkeys(RISK_FIELDS, RISK),
        **{
            f"okx_demo_structural_{band}_leverage_cap": cap
            for band, cap in zip(BANDS, CAPS, strict=True)
        },
    }


def restore_settings(**updates):
    return InitOnlySettings(_env_file=None, **(restore_values() | updates))


def finalized(settings, *, score=99, risk_score=99, high_math=True):
    raw = structural_candidate(high_math=high_math).model_dump()
    raw.update(score=score, risk_score=risk_score)
    candidate = candidate_with_structural_prices(
        TradeCandidate.model_validate(raw), reference_price=D("100")
    )
    assert candidate is not None
    result, blocker = apply_cost_adjusted_reward_risk(candidate, settings)
    assert blocker is None and result is not None
    assert result.protection_model == "structure"
    assert result.stop_loss == D("99.90")
    assert result.take_profit == D("101")
    assert result.estimated_round_trip_cost_pct == D("0.0016")
    return result


def select(candidate, settings, *, tier_score=99, equity=DEFAULT_EQUITY):
    return select_structural_leverage(
        candidate,
        score_risk_tier(tier_score, settings),
        settings,
        account_equity=equity,
        position_margin_cap=min(equity, settings.okx_demo_position_margin_bucket_usdt),
    )


def test_settings_source_contract_selects_only_init_source():
    init_source = object()

    def external_source():
        pytest.fail("external environment/dotenv/secret source must not be called")

    sources = InitOnlySettings.settings_customise_sources(
        InitOnlySettings,
        init_source,
        external_source,
        external_source,
        external_source,
    )
    assert sources == (init_source,)


def test_equal_half_percent_risks_restore_five_caps_without_raising_risk():
    settings = restore_settings()
    tiers = configured_score_risk_tiers(settings)
    assert tuple(tier.name for tier in tiers) == BANDS
    assert tuple(tier.risk_pct for tier in tiers) == (RISK,) * 5
    assert tuple(tier.leverage for tier in tiers) == CAPS
    assert settings.okx_demo_portfolio_max_risk_pct == PORTFOLIO_RISK
    assert settings.okx_demo_portfolio_max_margin_pct == D("0.60")
    assert settings.okx_demo_position_margin_bucket_usdt == BUCKET
    assert settings.okx_demo_max_open_positions == 8
    assert settings.max_weekly_loss_pct == settings.max_drawdown_pct == 0.10
    assert settings.okx_demo_require_protection is True
    assert settings.okx_demo_automation_leverage == 1
    assert (
        settings.okx_demo_score_low_leverage,
        settings.okx_demo_score_medium_leverage,
        settings.okx_demo_score_high_leverage,
    ) == (1, 1, 1)
    assert (
        settings.okx_demo_score_low_risk_pct,
        settings.okx_demo_score_medium_risk_pct,
        settings.okx_demo_score_high_risk_pct,
    ) == (RISK,) * 3


@pytest.mark.parametrize(
    "risk_field,reason",
    [
        *[(name, "risk tiers must be nondecreasing") for name in RISK_FIELDS[:-1]],
        (RISK_FIELDS[-1], "extreme risk cannot exceed portfolio stop-risk limit"),
    ],
)
def test_every_half_percent_override_is_needed_under_one_percent_portfolio(
    risk_field, reason
):
    values = restore_values()
    del values[risk_field]
    # Omission restores an older, higher default. Do not loosen the portfolio
    # cap or require strictly increasing risk: all five explicit .005s are valid.
    with pytest.raises(ValidationError, match=reason):
        InitOnlySettings(_env_file=None, **values)


@pytest.mark.parametrize("risk_score", [0, 1, 75, 97, None, 98, 99])
def test_twenty_x_eligibility_uses_none_only_score_fallback(risk_score):
    settings = restore_settings()
    candidate = finalized(settings, score=99, risk_score=risk_score)
    before = candidate.model_dump()
    # Deliberately present an extreme tier to exercise the helper's independent
    # eligibility gate. A zero effective score must not inherit nominal 99.
    selection = select(candidate, settings)
    eligible = risk_score is None or risk_score >= 98
    assert selection.required_leverage == 13
    assert selection.twenty_x_eligible is eligible
    assert selection.selected_leverage == (20 if eligible else 10)
    assert selection.leverage_cap == (20 if eligible else 10)
    if eligible:
        assert selection.cap_reasons == ()
    else:
        assert "effective_score_below_20x_threshold" in selection.cap_reasons
        assert (
            "required_leverage_exceeds_approved_leverage_cap" in selection.cap_reasons
        )
    assert candidate.model_dump() == before


def test_absent_effective_score_uses_nominal_score_without_upgrading_it():
    settings = restore_settings()
    candidate = finalized(settings, score=97, risk_score=None)
    selection = select(candidate, settings)
    assert selection.twenty_x_eligible is False
    assert selection.selected_leverage == 10
    assert "effective_score_below_20x_threshold" in selection.cap_reasons


@pytest.mark.parametrize(
    "score,band,cap",
    [
        (72, "low", 3),
        (79, "low", 3),
        (80, "medium", 5),
        (89, "medium", 5),
        (90, "high", 8),
        (94, "high", 8),
        (95, "elite", 10),
        (97, "elite", 10),
        (98, "extreme", 20),
        (100, "extreme", 20),
    ],
)
def test_dynamic_tier_boundaries_override_legacy_one_x_but_keep_score_caps(
    score, band, cap
):
    settings = restore_settings()
    tier = score_risk_tier(score, settings)
    candidate = finalized(settings, score=99, risk_score=score)
    selection = select(candidate, settings, tier_score=score)
    assert tier.name == band and tier.risk_pct == RISK
    assert tier.leverage == cap
    assert selection.required_leverage == 13
    assert selection.selected_leverage == cap
    assert selection.leverage_cap == cap
    if cap < 13:
        assert "required_leverage_exceeds_score_tier_cap" in selection.cap_reasons
    else:
        assert selection.cap_reasons == ()


@pytest.mark.parametrize("score", [0, 1, 71])
def test_scores_below_admission_cannot_obtain_a_risk_tier(score):
    with pytest.raises(ValueError, match="score_outside_configured_demo_risk_tiers"):
        score_risk_tier(score, restore_settings())


@pytest.mark.parametrize(
    "equity,required,selected",
    [
        ("300", 2, 3),
        ("625", 5, 5),
        ("1000", 7, 8),
        ("1500", 10, 10),
        ("2000", 13, 20),
    ],
)
def test_twenty_x_cap_is_not_an_automatic_twenty_x_selection(
    equity, required, selected
):
    settings = restore_settings()
    candidate = finalized(settings)
    selection = select(candidate, settings, equity=D(equity))
    assert LEVERAGE_LADDER == CAPS
    assert selection.twenty_x_eligible is True
    assert selection.leverage_cap == 20
    assert selection.required_leverage == required
    assert selection.selected_leverage == selected
    assert selected == min(value for value in LEVERAGE_LADDER if value >= required)


def test_insufficient_twenty_x_cap_does_not_raise_the_risk_budget_or_bucket():
    settings = restore_settings()
    candidate = finalized(settings)
    selection = select(candidate, settings, equity=D("5000"))
    assert selection.required_leverage == 33
    assert selection.selected_leverage == selection.leverage_cap == 20
    assert selection.cap_reasons == ("required_leverage_exceeds_20x_safety_cap",)
    assert score_risk_tier(99, settings).risk_pct == RISK
    assert settings.okx_demo_position_margin_bucket_usdt == BUCKET
    assert settings.okx_demo_portfolio_max_risk_pct == PORTFOLIO_RISK


def test_twenty_x_still_requires_high_mathematical_quality():
    settings = restore_settings()
    candidate = finalized(settings, high_math=False)
    selection = select(candidate, settings)
    assert selection.twenty_x_eligible is False
    assert selection.selected_leverage == selection.leverage_cap == 10
    assert "mathematical_grade_below_20x_threshold" in selection.cap_reasons


def test_disabling_dynamic_restores_unchanged_legacy_one_x_tiers():
    settings = restore_settings(okx_demo_structural_dynamic_leverage_enabled=False)
    tiers = configured_score_risk_tiers(settings)
    assert tuple(tier.name for tier in tiers) == BANDS[:3]
    assert tuple(tier.leverage for tier in tiers) == (1, 1, 1)
    assert tuple(tier.risk_pct for tier in tiers) == (RISK,) * 3
    assert settings.okx_demo_automation_leverage == 1
    assert settings.okx_demo_portfolio_max_risk_pct == PORTFOLIO_RISK
    assert settings.okx_demo_position_margin_bucket_usdt == BUCKET
    assert settings.okx_demo_portfolio_max_margin_pct == D("0.60")
    assert settings.okx_demo_max_open_positions == 8
    assert settings.max_weekly_loss_pct == settings.max_drawdown_pct == 0.10
    assert settings.okx_demo_require_protection is True


def test_three_hundred_bucket_and_exchange_availability_remain_sizing_ceilings():
    settings = restore_settings()
    plan = build_demo_capital_bucket_plan(
        risk_equity_usdt=D("2000"),
        available_equity_usdt=D("250"),
        configured_bucket_usdt=settings.okx_demo_position_margin_bucket_usdt,
        configured_position_limit=settings.okx_demo_max_open_positions,
    )
    assert plan.configured_bucket_usdt == plan.target_position_margin_usdt == BUCKET
    assert plan.available_position_margin_cap_usdt == D("250")
    assert plan.capital_slot_count == plan.effective_position_limit == 6


def test_synthetic_configuration_does_not_enable_exchange_writes():
    settings = restore_settings()
    assert settings.okx_demo_enabled is False
    assert settings.okx_demo_allow_order_writes is False
    assert settings.okx_demo_auto_execution is False
    assert settings.okx_demo_require_protection is True


def test_pending_example_explicitly_pins_all_five_risks_without_authority_keys():
    path = (
        Path(__file__).resolve().parents[2]
        / "docs/demo_dynamic_leverage_conservative.env.example"
    )
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# PENDING ROLLOUT ONLY.")
    overlay = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        assert key not in overlay, "duplicate overlay assignment"
        overlay[key] = value
    expected = {
        "OKX_DEMO_MAX_LEVERAGE": "20",
        "OKX_DEMO_STRUCTURAL_DYNAMIC_LEVERAGE_ENABLED": "true",
        "OKX_DEMO_PORTFOLIO_MAX_RISK_PCT": "0.01",
        **{name.upper(): "0.005" for name in RISK_FIELDS},
        **{
            f"OKX_DEMO_STRUCTURAL_{band.upper()}_LEVERAGE_CAP": str(cap)
            for band, cap in zip(BANDS, CAPS, strict=True)
        },
    }
    # Parse this checked-in example as literal text, not through dotenv; exact
    # keys exclude credentials, arming, write flags, and any Live controls.
    assert overlay == expected
    settings = restore_settings(
        **{key.lower(): value for key, value in overlay.items()}
    )
    assert (
        tuple(tier.risk_pct for tier in configured_score_risk_tiers(settings))
        == (RISK,) * 5
    )
    assert settings.okx_demo_position_margin_bucket_usdt == BUCKET
    assert settings.okx_demo_portfolio_max_margin_pct == D("0.60")
    assert settings.okx_demo_max_open_positions == 8
    assert settings.okx_demo_allow_order_writes is False


def service_method(name):
    # Read this worktree's source without importing its runtime singleton or
    # constructing a service that could load settings/private dependencies.
    path = Path(__file__).resolve().parents[2] / "app/demo_automation/service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SafeDemoAutomation"
    )
    return next(node for node in cls.body if getattr(node, "name", None) == name)


def test_restart_source_unconditionally_disarms_before_marking_recovered():
    method = service_method("recover")
    assignments = {
        ast.unparse(target): (node.value, index)
        for index, node in enumerate(method.body)
        if isinstance(node, ast.Assign)
        for target in node.targets
    }
    armed, armed_index = assignments["self._state['armed']"]
    recovered, recovered_index = assignments["self._recovered"]
    assert isinstance(armed, ast.Constant) and armed.value is False
    assert isinstance(recovered, ast.Constant) and recovered.value is True
    assert armed_index < recovered_index


@pytest.mark.parametrize("method_name", ["arm", "clear_emergency_stop"])
def test_source_retains_exchange_and_local_flat_barriers(method_name):
    method = service_method(method_name)
    barrier_suffix = "arming" if method_name == "arm" else "clearing_stop"
    expected = {
        f"exchange_exposure_must_be_zero_before_{barrier_suffix}": (
            "snapshot.positions or snapshot.pending_orders or snapshot.pending_algo_orders"
        ),
        f"tracked_trade_state_must_be_resolved_before_{barrier_suffix}": (
            "self._active_trades()"
        ),
    }
    found = {}
    for index, node in enumerate(method.body):
        if not isinstance(node, ast.If):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Raise) or not isinstance(child.exc, ast.Call):
                continue
            if child.exc.args and isinstance(child.exc.args[0], ast.Constant):
                reason = child.exc.args[0].value
                if reason in expected:
                    found[reason] = ast.unparse(node.test), index
    assert {key: value[0] for key, value in found.items()} == expected
    mutation_key = "armed" if method_name == "arm" else "emergency_stop"
    mutation_index = next(
        index
        for index, node in enumerate(method.body)
        if isinstance(node, ast.Assign)
        and any(
            ast.unparse(target) == f"self._state['{mutation_key}']"
            for target in node.targets
        )
    )
    assert all(index < mutation_index for _, index in found.values())
