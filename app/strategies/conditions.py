"""Pure strategy predicates, not candidates, source proofs or entry authority.

Only legacy strategy formulas are classified here. In particular, an empty HTF
group is absent evidence, never an implicit permission. Trigger predicates are
current boolean states, not independently reconstructed historical events.
Common market vetoes (including unstamped legacy funding) stay outside this API.
"""

from dataclasses import dataclass
from types import MappingProxyType

from app.strategies.base import Condition, StrategyContext

GROUPS = ("htf", "setup", "trigger", "quality", "safety", "score")
_CLASSIFICATION = MappingProxyType(
    {
        "trend_pullback": MappingProxyType(
            {
                "4h_trend": "htf",
                "1h_trend": "htf",
                "15m_pullback": "setup",
                "5m_momentum": "trigger",
                "quality": "quality",
            }
        ),
        "breakout_continuation": MappingProxyType(
            {
                "4h_permission": "htf",
                "15m_bos": "setup",
                "5m_trend": "trigger",
                "5m_volume": "score",
                "quality": "quality",
            }
        ),
        "liquidity_sweep_reversal": MappingProxyType(
            {
                "15m_sweep": "setup",
                "15m_choch": "setup",
                "5m_momentum": "trigger",
                "not_extreme": "safety",
                "quality": "quality",
            }
        ),
        "fvg_return": MappingProxyType(
            {
                "4h_direction": "htf",
                "15m_fvg": "setup",
                "1h_context": "htf",
                "5m_trigger": "trigger",
                "quality": "quality",
            }
        ),
        "order_block_return": MappingProxyType(
            {
                "4h_direction": "htf",
                "1h_context": "htf",
                "15m_order_block": "setup",
                "5m_trigger": "trigger",
                "quality": "quality",
            }
        ),
        "range_reversal": MappingProxyType(
            {
                "range_regime": "setup",
                "range_edge": "setup",
                "5m_turn": "trigger",
                "normal_vol": "safety",
                "quality": "quality",
            }
        ),
        "structure_reversal": MappingProxyType(
            {
                "1h_choch": "setup",
                "15m_follow": "setup",
                "5m_trigger": "trigger",
                "4h_not_opposed": "htf",
                "quality": "quality",
            }
        ),
        "volatility_expansion": MappingProxyType(
            {
                "15m_expansion": "setup",
                "15m_bos": "setup",
                "5m_volume": "score",
                "5m_momentum": "trigger",
                "not_extreme": "safety",
                "quality": "quality",
            }
        ),
    }
)


@dataclass(frozen=True)
class StrategyConditionSet:
    strategy: str
    direction: str
    items: tuple[Condition, ...]

    def __post_init__(self):
        if type(self.strategy) is not str or self.strategy not in _CLASSIFICATION:
            raise ValueError("unknown strategy condition set")
        if type(self.direction) is not str or self.direction not in {
            "long",
            "short",
            "neutral",
        }:
            raise ValueError("unknown strategy direction")
        expected = _CLASSIFICATION[self.strategy]
        if type(self.items) is not tuple or len(self.items) != len(expected):
            raise ValueError("strategy conditions require the complete bounded tuple")
        if any(type(item) is not Condition for item in self.items):
            raise ValueError("an exact Condition is required")
        if {item.code for item in self.items} != set(expected):
            raise ValueError(
                "strategy condition codes are missing, duplicated or unknown"
            )

    @property
    def classification(self) -> tuple[tuple[str, str], ...]:
        mapping = _CLASSIFICATION[self.strategy]
        return tuple((item.code, mapping[item.code]) for item in self.items)

    def for_group(self, group: str) -> tuple[Condition, ...]:
        if group not in GROUPS:
            raise ValueError("unknown condition group")
        mapping = _CLASSIFICATION[self.strategy]
        return tuple(item for item in self.items if mapping[item.code] == group)

    @property
    def required_failures(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                item.code for item in self.items if item.required and not item.passed
            )
        )

    @property
    def veto_failures(self) -> tuple[str, ...]:
        """Stable predicate codes; not legacy common_vetoes' market messages."""
        return tuple(
            sorted(item.code for item in self.items if item.veto and not item.passed)
        )

    @property
    def score(self) -> int:
        # Deliberately identical to legacy evaluate_conditions, not a win rate.
        total = sum(item.maximum for item in self.items) or 1
        earned = sum(item.maximum for item in self.items if item.passed)
        return min(100, round(earned / total * 100))


def assess_conditions(ctx: StrategyContext, strategy: str) -> StrategyConditionSet:
    """Closed dispatcher; never call evaluate, common_vetoes or build_candidate."""
    # Local imports avoid a cycle while legacy registry imports each strategy.
    from app.strategies import (
        breakout_continuation,
        fvg_return,
        liquidity_sweep_reversal,
        order_block_return,
        range_reversal,
        structure_reversal,
        trend_pullback,
        volatility_expansion,
    )

    extractors = {
        item.NAME: item.conditions
        for item in (
            trend_pullback,
            breakout_continuation,
            liquidity_sweep_reversal,
            fvg_return,
            order_block_return,
            range_reversal,
            structure_reversal,
            volatility_expansion,
        )
    }
    if type(strategy) is not str or strategy not in extractors:
        raise ValueError("unknown strategy")
    return extractors[strategy](ctx)
