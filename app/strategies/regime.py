"""Conservative, deterministic strategy-family routing before score evaluation.

This policy consumes one existing analysis snapshot, not a caller-declared new
regime. It proves neither alpha nor an entry event and grants no order authority.
An instantaneous high-volatility/BOS snapshot cannot prove a prior compression;
the volatility-expansion strategy therefore remains excluded. Likewise the
legacy ``range_or_transition`` label alone is not evidence of a usable range.
The existing engine emits CHoCH only in non-neutral trends, so its current
snapshot cannot prove a neutral-range-to-structural-reversal transition either.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, DecimalException
from enum import StrEnum

from app.domain.analysis import MultiTimeframeAnalysis, TimeframeAnalysis
from app.regime.classifier import classify_regime
from app.trade_qualification.models import MarketRegime

REQUIRED_TIMEFRAMES = ("4H", "1H", "15m", "5m")
TREND_STRATEGIES = (
    "breakout_continuation",
    "fvg_return",
    "order_block_return",
    "trend_pullback",
)
_ALIGNMENT_BLOCKER = "multi_timeframe_not_aligned"
_INSTABILITY_BLOCKER = "mathematical_core_regime_instability"


class RouteDecision(StrEnum):
    ALLOW_SCORING = "allow_scoring"
    NO_TRADE = "no_trade"


@dataclass(frozen=True, slots=True)
class RegimeRoute:
    regime: MarketRegime
    decision: RouteDecision
    allowed_strategies: tuple[str, ...]
    reasons: tuple[str, ...]
    fail_codes: tuple[str, ...]
    snapshot_basis: tuple[tuple[str, str], ...]
    snapshot_sha256: str | None

    @property
    def execution_authority(self) -> bool:
        return False


def _check_scalars(value: object) -> None:
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError("snapshot contains a non-finite decimal")
    if type(value) is float:
        raise ValueError("snapshot measurements require exact Decimal values")
    if isinstance(value, datetime) and (
        value.tzinfo is None or value.utcoffset() is None
    ):
        raise ValueError("snapshot timestamps require time zones")
    if isinstance(value, dict):
        for item in value.values():
            _check_scalars(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_scalars(item)


def _check_view(
    view: TimeframeAnalysis, timeframe: str, generated_at: datetime
) -> None:
    if view.timeframe != timeframe or view.candle_count <= 0 or view.close <= 0:
        raise ValueError("timeframe identity, count or close is invalid")
    if view.last_closed_at > generated_at:
        raise ValueError("closed candle cannot follow snapshot generation")
    indicators = view.indicators
    for name in ("ema20", "ema50", "ema200", "vwap"):
        value = getattr(indicators, name)
        if value is not None and value <= 0:
            raise ValueError("price indicator must be positive")
    for name in ("atr14", "atr_pct", "volume_ratio20"):
        value = getattr(indicators, name)
        if value is not None and value < 0:
            raise ValueError("volatility/volume indicators cannot be negative")
    for name in ("rsi14", "adx14"):
        value = getattr(indicators, name)
        if value is not None and not Decimal(0) <= value <= Decimal(100):
            raise ValueError("bounded indicator is outside its valid range")
    structure = view.structure
    levels = (
        structure.last_swing_high,
        structure.last_swing_low,
        *structure.support_levels,
        *structure.resistance_levels,
    )
    if any(level is not None and level <= 0 for level in levels):
        raise ValueError("structural price levels must be positive")
    if (
        structure.last_swing_low is not None
        and structure.last_swing_high is not None
        and structure.last_swing_low >= structure.last_swing_high
    ):
        raise ValueError("swing price bounds are reversed")
    for zone in (*structure.fair_value_gaps, *structure.order_blocks):
        if not Decimal(0) < zone.lower < zone.upper or zone.created_at > generated_at:
            raise ValueError("structural zone geometry or timestamp is invalid")
    if any(
        not Decimal(0) <= gap.filled_ratio <= Decimal(1)
        for gap in structure.fair_value_gaps
    ):
        raise ValueError("FVG filled ratio is outside its valid range")


def _validated_snapshot(analysis: MultiTimeframeAnalysis) -> MultiTimeframeAnalysis:
    if type(analysis) is not MultiTimeframeAnalysis:
        raise ValueError("router requires the exact analysis snapshot contract")
    payload = analysis.model_dump(mode="python", warnings=False, serialize_as_any=True)
    _check_scalars(payload)
    checked = MultiTimeframeAnalysis.model_validate(payload, strict=True)
    if (
        not checked.symbol.strip()
        or not checked.instrument_id.strip()
        or checked.price <= 0
        or not 0 <= checked.alignment_score <= 100
        or any(not blocker.strip() for blocker in checked.blockers)
    ):
        raise ValueError("snapshot identity, price, score or blocker is invalid")
    for timeframe, view in checked.timeframe_analyses.items():
        _check_view(view, timeframe, checked.generated_at)
    return checked


def _snapshot_basis(analysis: MultiTimeframeAnalysis) -> tuple[tuple[str, str], ...]:
    basis = [
        ("instrument_id", analysis.instrument_id),
        ("generated_at", analysis.generated_at.isoformat()),
        ("legacy_regime", analysis.regime),
        ("overall_bias", analysis.overall_bias),
        ("blockers", ",".join(sorted(analysis.blockers))),
    ]
    for timeframe in REQUIRED_TIMEFRAMES:
        view = analysis.timeframe_analyses.get(timeframe)
        if view is None:
            basis.append((f"{timeframe}.snapshot", "missing"))
            continue
        for field, value in (
            ("closed_at", view.last_closed_at.isoformat()),
            ("close", str(view.close)),
            ("trend", view.structure.trend),
            ("bias", view.directional_bias),
            ("volatility", view.volatility),
            ("bos", view.structure.bos or "missing"),
            ("choch", view.structure.choch or "missing"),
            ("quality_ok", str(view.data_quality_ok)),
        ):
            basis.append((f"{timeframe}.{field}", value))
    return tuple(basis)


def _range_bounds(view: TimeframeAnalysis) -> tuple[Decimal, Decimal] | None:
    supports = [level for level in view.structure.support_levels if level < view.close]
    resistances = [
        level for level in view.structure.resistance_levels if level > view.close
    ]
    if not supports or not resistances:
        return None
    return max(supports), min(resistances)


def route_regime(analysis: MultiTimeframeAnalysis) -> RegimeRoute:
    """Choose only strategy families worth scoring; never bypass their gates.

    ``fail_codes`` can also describe excluded families in an ALLOW_SCORING route.
    Consumers must use ``decision`` and ``allowed_strategies`` together. Existing
    analysis blockers are retained in the evidence, never deleted or rewritten.
    The legacy alignment blocker is not a universal reversal veto; data and
    mathematical-risk blockers remain fail-closed.
    """
    try:
        snapshot = _validated_snapshot(analysis)
    except (ValueError, TypeError, AttributeError, OverflowError, DecimalException):
        return RegimeRoute(
            MarketRegime.UNKNOWN,
            RouteDecision.NO_TRADE,
            (),
            ("Snapshot values or identities failed strict validation.",),
            ("invalid_analysis_snapshot",),
            (),
            None,
        )
    basis = _snapshot_basis(snapshot)
    digest = hashlib.sha256(
        json.dumps(
            snapshot.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    def result(regime, allowed, reasons, codes=(), extra_basis=()):
        return RegimeRoute(
            regime,
            RouteDecision.ALLOW_SCORING if allowed else RouteDecision.NO_TRADE,
            tuple(sorted(allowed)),
            tuple(reasons),
            tuple(sorted(set(codes))),
            (*basis, *extra_basis),
            digest,
        )

    missing = [
        tf for tf in REQUIRED_TIMEFRAMES if tf not in snapshot.timeframe_analyses
    ]
    if missing:
        return result(
            MarketRegime.UNKNOWN,
            (),
            ("Required closed-timeframe evidence is missing.",),
            tuple(f"missing_timeframe:{tf}" for tf in missing),
        )
    views = snapshot.timeframe_analyses
    if any(
        not view.data_quality_ok or view.data_quality_issues for view in views.values()
    ) or any("data_quality" in blocker for blocker in snapshot.blockers):
        return result(
            MarketRegime.UNKNOWN,
            (),
            ("Existing data-quality evidence blocks routing.",),
            ("data_quality_blocked",),
        )
    other_blockers = set(snapshot.blockers) - {_ALIGNMENT_BLOCKER, _INSTABILITY_BLOCKER}
    if other_blockers:
        return result(
            MarketRegime.UNKNOWN,
            (),
            ("An existing non-alignment analysis blocker remains.",),
            ("analysis_blocked",),
        )
    legacy = classify_regime(views)
    if legacy == "unstable_transition" or _INSTABILITY_BLOCKER in snapshot.blockers:
        return result(
            MarketRegime.RISK_OFF,
            (),
            ("Existing transition-instability evidence vetoes entry.",),
            ("risk_off_unstable_transition",),
        )
    if snapshot.regime != legacy:
        return result(
            MarketRegime.UNKNOWN,
            (),
            ("Legacy classification disagrees with its snapshot inputs.",),
            ("regime_snapshot_mismatch",),
        )
    h4, h1, m15, m5 = (views[tf] for tf in REQUIRED_TIMEFRAMES)
    if (
        legacy == "high_volatility"
        or any(view.volatility == "extreme" for view in views.values())
        or any(view.volatility == "high" for view in (h4, h1, m5))
    ):
        return result(
            MarketRegime.HIGH_VOLATILITY,
            (),
            ("High or extreme background/entry volatility is not routed.",),
            ("high_volatility_no_trade",),
        )
    if h4.volatility == "low" and h1.volatility == "low":
        return result(
            MarketRegime.COMPRESSION,
            (),
            ("Current HTF compression alone is not an entry event.",),
            ("compression_wait_for_confirmed_event",),
        )
    if m15.volatility == "high":
        direction = {"up": "long", "down": "short"}.get(m15.structure.bos)
        opposite = {"long": "short", "short": "long"}.get(direction)
        if (
            direction is not None
            and h4.directional_bias == direction
            and h1.directional_bias != opposite
        ):
            return result(
                MarketRegime.EXPANSION,
                ("breakout_continuation",),
                (
                    "Current 15m high volatility and BOS permit breakout scoring only; "
                    "prior compression is unobserved.",
                ),
                ("compression_history_missing",),
                (("expansion.bos_direction", direction),),
            )
        return result(
            MarketRegime.HIGH_VOLATILITY,
            (),
            ("15m high volatility lacks a directionally permitted BOS.",),
            ("expansion_bos_or_htf_permission_missing",),
        )
    if legacy in {"bull_trend", "bear_trend"}:
        direction = "long" if legacy == "bull_trend" else "short"
        if snapshot.overall_bias != direction or h4.directional_bias != direction:
            return result(
                MarketRegime.TREND,
                (),
                ("Trend structure lacks the candidate's required 4H direction.",),
                ("trend_direction_permission_missing",),
            )
        allowed = tuple(
            name
            for name in TREND_STRATEGIES
            if name != "trend_pullback" or h1.directional_bias == direction
        )
        return result(
            MarketRegime.TREND,
            allowed,
            (
                "Only directional trend families may be scored; "
                "each retains its own HTF/setup gates.",
            ),
            ()
            if "trend_pullback" in allowed
            else ("trend_pullback_1h_permission_missing",),
        )
    neutral_htf = all(
        view.structure.trend == "neutral" and view.directional_bias == "neutral"
        for view in (h4, h1)
    )
    bounds = _range_bounds(m15)
    if neutral_htf and m15.volatility in {"low", "normal"} and bounds is not None:
        if m15.structure.bos is not None or m15.structure.choch is not None:
            return result(
                MarketRegime.UNKNOWN,
                (),
                (
                    "A current structural break cannot establish the earlier range "
                    "or sweep/reclaim chronology.",
                ),
                ("range_transition_history_missing",),
            )
        return result(
            MarketRegime.RANGE,
            ("range_reversal",),
            (
                "Neutral HTFs and an intact 15m support/price/resistance bracket "
                "define a conservative range route.",
            ),
            (
                "sweep_transition_history_missing",
                "structural_reversal_history_missing",
            ),
            (("range.support", str(bounds[0])), ("range.resistance", str(bounds[1]))),
        )
    return result(
        MarketRegime.UNKNOWN,
        (),
        (
            "Range/transition ambiguity has no sufficient "
            "snapshot-only routing evidence.",
        ),
        ("regime_evidence_missing",),
    )
