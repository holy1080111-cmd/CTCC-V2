from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config.settings import Settings
from app.demo_automation.risk_profile import (
    configured_score_risk_tiers,
    derivative_adjusted_score,
    score_risk_tier,
)
from app.domain.strategy import (
    DerivativeConfirmation,
    MathematicalConfirmation,
    TradeCandidate,
)


def adaptive_settings() -> Settings:
    return Settings(
        _env_file=None,
        okx_demo_score_risk_enabled=True,
        okx_demo_max_open_positions=3,
        okx_demo_daily_loss_limit_pct=Decimal("0.03"),
    )


@pytest.mark.parametrize(
    ("score", "name", "risk_pct", "leverage", "margin_pct"),
    [
        (72, "low", Decimal("0.005"), 1, Decimal("0.15")),
        (79, "low", Decimal("0.005"), 1, Decimal("0.15")),
        (80, "medium", Decimal("0.0075"), 2, Decimal("0.20")),
        (89, "medium", Decimal("0.0075"), 2, Decimal("0.20")),
        (90, "high", Decimal("0.01"), 3, Decimal("0.25")),
        (100, "high", Decimal("0.01"), 3, Decimal("0.25")),
    ],
)
def test_score_selects_expected_risk_tier(
    score: int,
    name: str,
    risk_pct: Decimal,
    leverage: int,
    margin_pct: Decimal,
) -> None:
    tier = score_risk_tier(score, adaptive_settings())

    assert tier.name == name
    assert tier.risk_pct == risk_pct
    assert tier.leverage == leverage
    assert tier.margin_allocation_pct == margin_pct


def test_configured_tiers_are_contiguous() -> None:
    tiers = configured_score_risk_tiers(adaptive_settings())

    assert [(item.minimum_score, item.maximum_score) for item in tiers] == [
        (72, 79),
        (80, 89),
        (90, 100),
    ]


def _candidate(status: str, confidence: str) -> TradeCandidate:
    return TradeCandidate(
        strategy="trend_pullback",
        direction="long",
        score=95,
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("110"),
        risk_reward=Decimal("2"),
        invalidation="stop",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        derivative_confirmation=DerivativeConfirmation(
            status=status,
            confidence=Decimal(confidence),
            alignment_score=(
                Decimal("-0.8") if status == "opposed" else Decimal("0.8")
            ),
        ),
    )


@pytest.mark.parametrize(
    ("status", "confidence", "expected_score"),
    [
        ("confirmed", "0.80", 95),
        ("confirmed", "0.50", 89),
        ("confirmed", "0.20", 79),
        ("mixed", "0.90", 79),
        ("insufficient", "0", 79),
    ],
)
def test_derivative_confidence_can_only_cap_risk_score(
    status: str, confidence: str, expected_score: int
) -> None:
    adjusted, blocker = derivative_adjusted_score(
        _candidate(status, confidence), adaptive_settings()
    )

    assert adjusted == expected_score
    assert adjusted <= 95
    assert blocker is None


def test_opposing_derivative_blocks_adaptive_trade() -> None:
    adjusted, blocker = derivative_adjusted_score(
        _candidate("opposed", "0.90"), adaptive_settings()
    )

    assert adjusted == 95
    assert blocker == "causal_derivative_opposes_trade_direction"


def _mathematical_candidate(
    status: str, risk_grade: str, confidence: str = "0.8"
) -> TradeCandidate:
    candidate = _candidate("confirmed", "0.9")
    return candidate.model_copy(
        update={
            "mathematical_confirmation": MathematicalConfirmation(
                status=status,
                risk_grade=risk_grade,
                confidence=Decimal(confidence),
                directional_support=(
                    Decimal("-0.8")
                    if status == "opposed"
                    else Decimal("0.8")
                ),
                reliability=Decimal("0.8"),
                coverage=Decimal("0.9"),
                consensus=Decimal("0.9"),
                instability=(
                    Decimal("0.9")
                    if status == "unstable"
                    else Decimal("0.1")
                ),
                component_codes=(
                    ["derivative", "state", "conformal"]
                    if risk_grade in {"high", "medium"}
                    else []
                ),
            )
        }
    )


@pytest.mark.parametrize(
    ("risk_grade", "expected_score"),
    [
        ("high", 95),
        ("medium", 89),
        ("low", 79),
    ],
)
def test_unified_mathematical_grade_caps_demo_score(
    risk_grade: str, expected_score: int
) -> None:
    adjusted, blocker = derivative_adjusted_score(
        _mathematical_candidate("confirmed", risk_grade),
        adaptive_settings(),
    )

    assert adjusted == expected_score
    assert adjusted <= 95
    assert blocker is None


def test_auxiliary_bonus_never_changes_demo_risk_score() -> None:
    candidate = _mathematical_candidate("confirmed", "medium")
    confirmation = candidate.mathematical_confirmation
    assert confirmation is not None
    candidate = candidate.model_copy(
        update={
            "mathematical_confirmation": confirmation.model_copy(
                update={
                    "auxiliary_bonus": 5,
                    "auxiliary_directional_support": Decimal("1"),
                    "auxiliary_component_codes": ["structure", "momentum"],
                }
            )
        }
    )

    adjusted, blocker = derivative_adjusted_score(
        candidate,
        adaptive_settings(),
    )

    assert adjusted == 89
    assert blocker is None


@pytest.mark.parametrize(
    ("status", "expected_blocker"),
    [
        ("opposed", "mathematical_core_opposes_trade_direction"),
        ("unstable", "mathematical_core_regime_instability"),
    ],
)
def test_unified_mathematical_blockers_fail_closed_before_demo_write(
    status: str, expected_blocker: str
) -> None:
    adjusted, blocker = derivative_adjusted_score(
        _mathematical_candidate(status, "blocked"),
        adaptive_settings(),
    )

    assert adjusted == 95
    assert blocker == expected_blocker
