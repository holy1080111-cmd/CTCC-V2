"""Synthetic predicate-only assessments, never entry/event evidence or fills."""

import ast
import hashlib
import inspect
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

import pytest

from app.config import settings
from app.strategies import base
from app.strategies.conditions import GROUPS, StrategyConditionSet, assess_conditions
from app.strategies.registry import STRATEGY_NAMES
from tests.unit.strategies.test_required_conditions import REQUIRED, context

D = Decimal

# Captured before this extraction from HEAD 6d87530 evaluate's body, excluding
# only its final evaluate_conditions call. AST ignores formatting, while covering
# direction derivation, labels, weights, predicates, required flags and veto flags.
ORIGINAL_FORMULA_AST = {
    "trend_pullback": "4fbefb4233131ec8f34dedb4357d6ebdec60eb875c33851b028db0ae1b8f8179",
    "breakout_continuation": "be7e836d1ed6ac374779a4e2dfdad633311238730a154d28f8e65f9ae660a3d2",
    "liquidity_sweep_reversal": "a68abd33319faf8dd01bd085563021eeaf2d53005dacf1be4cb2d904c428eede",
    "fvg_return": "9dfc6c513f7afc6c2e1c55ac9b028368f1bead07c90e33b95d1858cb5fdd19d6",
    "order_block_return": "cc52ac9715eb143da0dbd983e396b07613a91af1462536fdac9d8bea0e0a7f73",
    "range_reversal": "f3e63ce26212e3a60763eb04a47d4a026a83bddb46f4e78173ad0a9d6d9e2552",
    "structure_reversal": "3b5b38430dfc6825ea4074f4579a102f94695a4760579de66cac611542127f89",
    "volatility_expansion": "0859369e568f88a89752b9026102cbc75bbfb376f4b2407d43a344981bd19194",
}
HTF = {
    "trend_pullback": {"4h_trend", "1h_trend"},
    "breakout_continuation": {"4h_permission"},
    "liquidity_sweep_reversal": set(),
    "fvg_return": {"4h_direction", "1h_context"},
    "order_block_return": {"4h_direction", "1h_context"},
    "range_reversal": set(),
    "structure_reversal": {"4h_not_opposed"},
    "volatility_expansion": set(),
}
SETUP = {
    "trend_pullback": {"15m_pullback"},
    "breakout_continuation": {"15m_bos"},
    "liquidity_sweep_reversal": {"15m_sweep", "15m_choch"},
    "fvg_return": {"15m_fvg"},
    "order_block_return": {"15m_order_block"},
    "range_reversal": {"range_regime", "range_edge"},
    "structure_reversal": {"1h_choch", "15m_follow"},
    "volatility_expansion": {"15m_expansion", "15m_bos"},
}


def module(strategy):
    return import_module(f"app.strategies.{strategy}")


def fixture(strategy, direction="long"):
    # A threshold above 100 prevents legacy candidate creation in comparison
    # tests. The pure interface has no eligible flag and ignores this threshold.
    ctx = context(strategy, minimum_score=101)
    if direction == "long":
        return ctx
    ctx.analysis.overall_bias = direction
    for view in ctx.analysis.timeframe_analyses.values():
        view.directional_bias = direction
        view.structure.trend = "bearish" if direction == "short" else "neutral"
        view.structure.bos = "down" if direction == "short" else None
        view.structure.choch = "down" if direction == "short" else None
        view.indicators.macd_histogram = D("-0.5") if direction == "short" else D(0)
        view.indicators.rsi14 = D(45) if direction == "short" else D(50)
        view.structure.support_levels = [D(95)] if direction == "short" else []
        view.structure.resistance_levels = [D(100)] if direction == "short" else []
        for gap in view.structure.fair_value_gaps:
            gap.direction = "bearish"
        for block in view.structure.order_blocks:
            block.direction = "bearish"
    return ctx


@pytest.mark.parametrize("strategy", STRATEGY_NAMES)
def test_extracted_formula_ast_is_unchanged_from_pre_refactor(strategy):
    node = ast.parse(inspect.getsource(module(strategy).conditions)).body[0]
    formula = ast.Module(body=node.body[:-1], type_ignores=[])
    digest = hashlib.sha256(
        ast.dump(formula, include_attributes=False).encode()
    ).hexdigest()
    assert digest == ORIGINAL_FORMULA_AST[strategy]


@pytest.mark.parametrize("strategy", STRATEGY_NAMES)
@pytest.mark.parametrize("direction", ["long", "short", "neutral"])
def test_pure_and_legacy_conditions_direction_score_and_hard_gates_agree(
    strategy, direction
):
    ctx = fixture(strategy, direction)
    result = assess_conditions(ctx, strategy)
    legacy = module(strategy).evaluate(ctx)
    assert result.direction == legacy.direction == direction
    assert result.score == legacy.score
    assert result.required_failures == tuple(legacy.required_failures)
    assert legacy.candidate is None  # Deliberately blocked by the fixture threshold.
    assert {item.code for item in result.items if item.required} == REQUIRED[strategy]
    for condition, component in zip(result.items, legacy.score_components, strict=True):
        assert (
            condition.code,
            condition.label,
            condition.maximum,
            condition.passed,
            condition.detail,
            condition.required,
        ) == (
            component.code,
            component.label,
            component.maximum,
            component.passed,
            component.detail,
            component.required,
        )
        if condition.veto and not condition.passed:
            assert condition.detail in legacy.vetoes
    if direction != "neutral":
        assert result.score == 100
        assert result.required_failures == result.veto_failures == ()


@pytest.mark.parametrize("strategy", STRATEGY_NAMES)
def test_grouping_is_explicit_complete_and_does_not_invent_htf_permission(strategy):
    result = assess_conditions(fixture(strategy), strategy)
    assert {item.code for item in result.for_group("htf")} == HTF[strategy]
    assert {item.code for item in result.for_group("setup")} == SETUP[strategy]
    assert len(result.for_group("trigger")) == 1
    assert all(item.required for item in result.for_group("trigger"))
    assert tuple(code for code, _group in result.classification) == tuple(
        item.code for item in result.items
    )
    grouped = [item.code for group in GROUPS for item in result.for_group(group)]
    assert len(grouped) == len(set(grouped)) == len(result.items)
    quality = result.for_group("quality")
    assert len(quality) == 1 and quality[0].required and quality[0].veto
    for name in (
        "eligible",
        "candidate",
        "execution_authority",
        "trigger_time",
        "expires_at",
    ):
        assert not hasattr(result, name)
    if strategy in {"fvg_return", "order_block_return"}:
        optional = next(
            item for item in result.for_group("htf") if item.code == "1h_context"
        )
        assert not optional.required and not optional.veto
    if strategy == "structure_reversal":
        assert (
            result.for_group("htf")[0].veto and not result.for_group("htf")[0].required
        )


@pytest.mark.parametrize("strategy", STRATEGY_NAMES)
def test_pure_dispatch_cannot_call_legacy_candidate_veto_settings_or_time(
    monkeypatch, strategy
):
    ctx = fixture(strategy)

    def forbidden(*_args, **_kwargs):
        pytest.fail("pure conditions touched a legacy evaluation or runtime dependency")

    monkeypatch.setattr(base, "build_candidate", forbidden)
    monkeypatch.setattr(base, "evaluate_conditions", forbidden)
    monkeypatch.setattr(base, "common_vetoes", forbidden)
    monkeypatch.setattr(base, "datetime", SimpleNamespace(now=forbidden))
    monkeypatch.setattr(settings, "get_settings", forbidden)
    for name in STRATEGY_NAMES:
        target = module(name)
        monkeypatch.setattr(target, "evaluate", forbidden)
        monkeypatch.setattr(target, "evaluate_conditions", forbidden)
        monkeypatch.setattr(target, "common_vetoes", forbidden)
    assert assess_conditions(ctx, strategy).score == 100


@pytest.mark.parametrize("strategy", STRATEGY_NAMES)
def test_legacy_snapshot_funding_spread_or_quote_clock_is_not_a_pure_condition(
    strategy,
):
    ctx = fixture(strategy)
    result = assess_conditions(ctx, strategy)
    # These remain legacy common_vetoes, not evidence of a fresh funding quote.
    ctx.market.funding_rate = D(1)
    ctx.market.ticker.bid = D(1)
    ctx.market.ticker.ask = D(200)
    ctx.market.ticker.timestamp = ctx.market.ticker.timestamp.replace(year=2000)
    assert assess_conditions(ctx, strategy) == result
    legacy = module(strategy).evaluate(ctx)
    assert "funding_excessively_positive_for_long" in legacy.vetoes
    assert "spread_above_0.08_percent" in legacy.vetoes


@pytest.mark.parametrize("strategy", STRATEGY_NAMES)
def test_legacy_wrapper_consumes_exact_shared_items_and_keeps_common_vetoes(
    monkeypatch, strategy
):
    ctx = fixture(strategy)
    result = assess_conditions(ctx, strategy)
    changed = replace(
        result, items=tuple(replace(item, passed=False) for item in result.items)
    )
    monkeypatch.setattr(module(strategy), "conditions", lambda _: changed)
    monkeypatch.setattr(
        module(strategy), "common_vetoes", lambda *_: ["legacy_market_veto"]
    )

    def record(actual_ctx, name, direction, items, vetoes):
        assert actual_ctx is ctx and name == strategy and direction == changed.direction
        assert items == list(changed.items) and vetoes == ["legacy_market_veto"]
        return "delegated"

    monkeypatch.setattr(module(strategy), "evaluate_conditions", record)
    assert module(strategy).evaluate(ctx) == "delegated"


@pytest.mark.parametrize("strategy", STRATEGY_NAMES)
def test_failures_are_stable_codes_and_other_points_cannot_clear_them(strategy):
    result = assess_conditions(fixture(strategy), strategy)
    for failing in result.items:
        altered = replace(
            result,
            items=tuple(
                replace(item, passed=False) if item.code == failing.code else item
                for item in result.items
            ),
        )
        assert altered.score == 100 - failing.maximum
        assert altered.required_failures == (
            (failing.code,) if failing.required else ()
        )
        assert altered.veto_failures == ((failing.code,) if failing.veto else ())


def test_immutable_complete_bounded_set_and_unknown_dispatch_fail_closed():
    result = assess_conditions(fixture("trend_pullback"), "trend_pullback")
    with pytest.raises(FrozenInstanceError):
        result.direction = "short"
    with pytest.raises(FrozenInstanceError):
        result.items[0].passed = False
    with pytest.raises(ValueError):
        result.for_group("assumed_permission")
    for invalid in ("unknown", "../base", 1, None):
        with pytest.raises(ValueError):
            assess_conditions(fixture("trend_pullback"), invalid)
    for items in (
        list(result.items),
        result.items * 1000,
        result.items[:-1],
        (result.items[0],) * 5,
    ):
        with pytest.raises(ValueError):
            replace(result, items=items)
    with pytest.raises(ValueError):
        StrategyConditionSet("unknown", "long", result.items)


def test_pure_ast_contains_no_legacy_or_authority_operations():
    forbidden = {
        "evaluate",
        "evaluate_conditions",
        "common_vetoes",
        "build_candidate",
        "now",
        "get_settings",
        "TradeCandidate",
    }
    functions = [
        assess_conditions,
        *(module(name).conditions for name in STRATEGY_NAMES),
    ]
    for function in functions:
        tree = ast.parse(inspect.getsource(function))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                called = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else ""
                )
                assert called not in forbidden
