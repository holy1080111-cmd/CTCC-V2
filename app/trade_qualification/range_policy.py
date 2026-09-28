"""Explicit neutral-range permission; no legacy-policy rewrite or authority."""

from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext

from app.analysis.service import analyze_snapshot_at
from app.strategies.base import StrategyContext
from app.strategies.conditions import assess_conditions
from app.strategies.mathematical_confirmation import mathematical_confirmation
from app.strategies.regime import RouteDecision, route_regime
from app.trade_qualification.continuation import _prepared_market
from app.trade_qualification.data import _canonical, _sha
from app.trade_qualification.events import _copy_source, extract_trigger
from app.trade_qualification.models import MarketRegime
from app.trade_qualification.timing import TIMING_POLICIES, event_identity

RANGE_PROTECTION_POLICY = "ctcc-neutral-range-protection-v1"


def range_current_permission(analysis, market, route, direction):
    """Callers rebuild source first; the original event is checked separately."""
    views = analysis.timeframe_analyses
    if (
        direction not in ("long", "short")
        or route.regime != MarketRegime.RANGE
        or route.decision != RouteDecision.ALLOW_SCORING
        or "range_reversal" not in route.allowed_strategies
        or any(code != "multi_timeframe_not_aligned" for code in analysis.blockers)
        or analysis.mathematical_core is None
        or set(views) != {"4H", "1H", "15m", "5m"}
    ):
        return False
    # Only actual operands of the range predicates: no legacy ATR fallback.
    atr = views["15m"].indicators.atr14
    momentum = views["5m"].indicators
    if (
        type(atr) is not Decimal
        or not atr.is_finite()
        or atr <= 0
        or momentum.macd_histogram is None
        or momentum.rsi14 is None
    ):
        return False
    assessment = assess_conditions(
        StrategyContext(analysis, market, 0, Decimal(1)), "range_reversal"
    )
    math = mathematical_confirmation(analysis, direction)
    return (
        assessment.direction == direction
        and not assessment.required_failures
        and not assessment.veto_failures
        and math.status not in ("unstable", "opposed")
        and math.risk_grade != "blocked"
    )


def replay_range_permission(market, analysis, event):
    """Independently rebuild the original analysis and exact closed-bar event.

    A digest records this policy calculation; it grants no runtime authority.
    The structural selector also retains its unchanged event/TTL/source checks.
    """
    from app.market.quality.candles import BAR_SECONDS, candle_closed_at
    from app.trade_qualification.one_shot import _guard_original

    _guard_original({"market": market, "event": event})
    guard_range_analysis(analysis)
    if (
        event.strategy != "range_reversal"
        or event.setup_type != "confirmed_range_edge_reclaim"
        or event.trigger is None
        or event.setup_time is None
    ):
        raise ValueError("range_protection_history_denied")
    now = event.observed_at.astimezone(UTC)
    checked = _prepared_market(market, now)
    for timeframe, rows in checked.candles.items():
        seconds = BAR_SECONDS[timeframe]
        epoch = datetime(1970, 1, 1, tzinfo=now.tzinfo)
        expected = epoch + timedelta(
            seconds=((now - epoch) // timedelta(seconds=seconds)) * seconds
        )
        if len(rows) < 200 or candle_closed_at(rows[-1], timeframe) != expected:
            raise ValueError("range_protection_source_incomplete")
    with localcontext(Context(prec=100)):
        rebuilt = analyze_snapshot_at(
            checked, evaluated_at=now, version=analysis.version
        )
        if _canonical(rebuilt) != _canonical(analysis):
            raise ValueError("range_protection_analysis_mismatch")
        route = route_regime(rebuilt)
        if not range_current_permission(rebuilt, checked, route, event.direction):
            raise ValueError("range_protection_permission_denied")
        duration = event.trigger.expires_at - event.trigger.trigger_time
        ttl = duration.days * 86400 + duration.seconds
        if (
            duration.microseconds
            or not 0 < ttl <= TIMING_POLICIES["range_reversal"].trigger_ttl_seconds
        ):
            raise ValueError("range_protection_history_denied")
        detection = extract_trigger(
            checked,
            rebuilt,
            report_id=event.report_id,
            strategy=event.strategy,
            direction=event.direction,
            observed_at=now,
            trigger_ttl_seconds=ttl,
        )
        _, _, _, source_sha = _copy_source(checked, rebuilt, now)
        if detection != event or source_sha != event.source_sha256:
            raise ValueError("range_protection_history_denied")
    return _sha(
        _canonical(
            {
                "policy": RANGE_PROTECTION_POLICY,
                "source_sha256": source_sha,
                "analysis_sha256": route.snapshot_sha256,
                "event_key": event_identity(event),
                "retained_analysis_blockers": tuple(rebuilt.blockers),
                "regime": route.regime.value,
            }
        )
    )


def guard_range_analysis(value, depth=0, budget=None):
    """Exact analysis tree before serializers, property hooks or user callbacks."""
    from app.trade_qualification.fixed_protection import _ANALYSIS_TYPES
    from app.trade_qualification.one_shot import _guard_original

    if budget is None:
        budget = [100000]
    budget[0] -= 1
    if depth > 24 or budget[0] < 0:
        raise ValueError("range_analysis_tree_invalid")
    kind = type(value)
    if any(kind is model for model in _ANALYSIS_TYPES):
        raw = object.__getattribute__(value, "__dict__")
        fields = object.__getattribute__(value, "__pydantic_fields_set__")
        if (
            type(raw) is not dict
            or any(type(key) is not str for key in raw)
            or set(raw) != set(kind.model_fields)
            or type(fields) is not set
            or any(type(key) is not str for key in fields)
            or not fields <= set(raw)
            or object.__getattribute__(value, "__pydantic_extra__") is not None
            or object.__getattribute__(value, "__pydantic_private__") is not None
        ):
            raise ValueError("range_analysis_tree_invalid")
        for item in raw.values():
            guard_range_analysis(item, depth + 1, budget)
    elif kind is dict:
        if len(value) > 128 or any(type(key) is not str for key in value):
            raise ValueError("range_analysis_tree_invalid")
        for item in value.values():
            guard_range_analysis(item, depth + 1, budget)
    elif kind in (tuple, list):
        if len(value) > 2048:
            raise ValueError("range_analysis_tree_invalid")
        for item in value:
            guard_range_analysis(item, depth + 1, budget)
    else:
        _guard_original(value)
