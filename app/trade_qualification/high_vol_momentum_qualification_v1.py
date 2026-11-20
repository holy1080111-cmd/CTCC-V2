"""Explicit raw-source HighVol permission for two existing strategy families.

This calculation does not change the legacy router or create an execution
owner. The original-source coordinator must select and freeze this profile;
G12, current-source recheck, risk, reservation and intent remain mandatory.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext

from app.analysis.service import analyze_snapshot_at
from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.source_primitives import canonical, sha
from app.market.quality.candles import BAR_SECONDS, candle_closed_at
from app.strategies.mathematical_confirmation import mathematical_confirmation
from app.strategies.regime import RouteDecision, route_regime
from app.trade_qualification.continuation import _prepared_market
from app.trade_qualification.events import _copy_source
from app.trade_qualification.models import MarketRegime

STRATEGIES = frozenset({"breakout_continuation", "volatility_expansion"})
PROFILE_BYTES = canonical(
    {
        "schema_version": "ctcc.high_vol_momentum.qualification_profile.v1",
        "environment": "demo",
        "families": sorted(STRATEGIES),
        "actual_regime": "High Volatility",
        "router_relabelled": False,
        "raw_frames": ["4H", "1H", "15m", "5m"],
        "minimum_closed_rows": 200,
        "extreme_allowed": False,
        "maximum_exclusive_causal_shock": "0.65",
        "unknown_causal_state_allowed": False,
        "htf": "4H_same_direction_1H_same_or_neutral_no_opposed_frame",
        "setup": "directional_15m_BOS_existing_original_event",
        "expansion": "original_compression_then_later_5m_momentum_required",
        "mathematics": "confirmed_existing_confirmation_no_score_addition",
        "current": "fresh_source_same_original_candidate_policy_event_zone_SL_TP",
        "expiry_extension": False,
        "risk_caps_changed": False,
        "execution_authority": False,
    }
)
PROFILE_SHA256 = sha(PROFILE_BYTES)
_FRAMES = ("4H", "1H", "15m", "5m")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def profile_arguments_v1(value):
    """Preserve the old default call shape and refuse unknown profile versions."""
    if value is None:
        return {}
    if type(value) is not str or value != PROFILE_SHA256:
        raise ValueError("high_vol_qualification_profile_unknown")
    return {"high_vol_profile_sha256": PROFILE_SHA256}


def prefix_policy_id_v1(base, profile):
    profile_arguments_v1(profile)
    return base if profile is None else base + "-highvol-v1"


def configured_profile_sha256_v1(settings):
    """Selected config DATA only; native source replay authenticates its owner."""
    from app.config.settings import Settings

    if (
        type(settings) is not Settings
        or type(settings.ctcc_demo_high_vol_momentum_v2) is not bool
    ):
        raise ValueError("high_vol_qualification_exact_settings_required")
    return PROFILE_SHA256 if settings.ctcc_demo_high_vol_momentum_v2 else None


@dataclass(frozen=True, slots=True)
class HighVolMomentumPermissionV1:
    admitted: bool
    code: str
    receipt_json: bytes

    @property
    def evaluation_sha256(self):
        return sha(self.receipt_json)

    @property
    def execution_authority(self):
        return False


def evaluate_high_vol_momentum_permission_v1(
    market, analysis, *, strategy, direction, observed_at, source_sha256
):
    """Rebuild source/analysis; a supplied analysis or matching hash cannot pass.

    Event survival and original history are deliberately evaluated by the shared
    original-event path, not by finding a different current trigger here.
    """
    if strategy not in STRATEGIES or direction not in {"long", "short"}:
        raise ValueError("high_vol_qualification_family_or_direction_invalid")
    if type(observed_at) is not datetime or observed_at.tzinfo is not UTC:
        raise ValueError("high_vol_qualification_exact_UTC_required")
    if type(source_sha256) is not str or len(source_sha256) != 64:
        raise ValueError("high_vol_qualification_source_pin_required")
    values = {
        "schema_version": "ctcc.high_vol_momentum.source_permission.v1",
        "profile_sha256": PROFILE_SHA256,
        "environment": "demo",
        "strategy": strategy,
        "direction": direction,
        "observed_at": observed_at.isoformat(),
        "source_sha256": source_sha256,
        "actual_regime": None,
        "router_sha256": None,
        "execution_authority": False,
        "source_authenticity_verified": False,
        "router_relabelled": False,
    }

    def finish(code):
        values.update(admitted=code == "passed", code=code)
        return HighVolMomentumPermissionV1(code == "passed", code, canonical(values))

    if (
        type(market) is not MarketSnapshot
        or type(analysis) is not MultiTimeframeAnalysis
    ):
        return finish("high_vol_exact_source_required")
    # The normal strict source validators reject hidden fields, malformed OHLC,
    # causal timestamp violations and incomplete/interleaved confirmations.
    checked = _prepared_market(market, observed_at)
    with localcontext(Context(prec=100)):
        repeated = analyze_snapshot_at(
            checked, evaluated_at=observed_at, version=analysis.version
        )
        if repeated != analysis:
            return finish("high_vol_analysis_source_mismatch")
        _, _, frames, actual_source = _copy_source(checked, repeated, observed_at)
        if actual_source != source_sha256:
            return finish("high_vol_source_pin_mismatch")
        route = route_regime(repeated)
        values.update(
            actual_regime=route.regime.value, router_sha256=route.snapshot_sha256
        )
        if (
            route.regime != MarketRegime.HIGH_VOLATILITY
            or route.decision != RouteDecision.NO_TRADE
            or set(route.fail_codes) != {"high_volatility_no_trade"}
        ):
            return finish("high_vol_source_regime_required")
        if analysis.blockers:
            return finish("high_vol_analysis_blocked")
        for frame in _FRAMES:
            rows = frames[frame]
            seconds = BAR_SECONDS[frame]
            expected = _EPOCH + timedelta(
                seconds=((observed_at - _EPOCH) // timedelta(seconds=seconds)) * seconds
            )
            if len(rows) < 200 or candle_closed_at(rows[-1], frame) != expected:
                return finish("high_vol_complete_closed_tail_required")
        views = repeated.timeframe_analyses
        if any(views[frame].volatility == "extreme" for frame in _FRAMES):
            return finish("high_vol_extreme_blocked")
        if not any(views[frame].volatility == "high" for frame in _FRAMES):
            return finish("high_vol_source_regime_required")
        opposite = "short" if direction == "long" else "long"
        expected_bos = "up" if direction == "long" else "down"
        if (
            views["4H"].directional_bias != direction
            or views["1H"].directional_bias not in {direction, "neutral"}
            or any(views[frame].directional_bias == opposite for frame in _FRAMES)
            or views["15m"].structure.bos != expected_bos
            or (
                strategy == "volatility_expansion" and views["15m"].volatility != "high"
            )
        ):
            return finish("high_vol_source_HTF_permission_missing")
        if any(
            views[frame].indicators.causal_state is None
            or views[frame].indicators.causal_state.shock_score >= Decimal("0.65")
            for frame in _FRAMES
        ):
            return finish("high_vol_causal_shock_or_unknown")
        math = mathematical_confirmation(repeated, direction)
        if math.status != "confirmed" or math.risk_grade == "blocked":
            return finish("high_vol_mathematical_confirmation_missing")
        return finish("passed")
