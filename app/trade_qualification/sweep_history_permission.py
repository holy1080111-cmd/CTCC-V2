"""Preregistered neutral-HTF sweep history evidence; never execution authority."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext

from app.analysis.mathematical_core import mathematical_core_snapshot
from app.analysis.service import analyze_snapshot_at, analyze_timeframe
from app.exchange.okx.symbols import to_canonical_symbol
from app.market.quality.candles import (
    BAR_SECONDS,
    candle_closed_at,
    inspect_candles_at,
)
from app.regime.classifier import classify_regime
from app.strategies.base import StrategyContext
from app.strategies.conditions import assess_conditions
from app.strategies.mathematical_confirmation import mathematical_confirmation
from app.strategies.regime import _range_bounds, route_regime
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification.continuation import _prepared_market
from app.trade_qualification.data import _canonical, _sha
from app.trade_qualification.events import _copy_source, extract_trigger
from app.trade_qualification.models import MarketRegime
from app.trade_qualification.regime_admission import _guard, _utc
from app.trade_qualification.timing import TIMING_POLICIES, event_identity

STRATEGY = "liquidity_sweep_reversal"
POLICY_ID = "ctcc-sweep-history-protection-v1"
POLICY_BYTES = journal.canonical(
    {
        "policy_id": POLICY_ID,
        "strategy": STRATEGY,
        "minimum_closed_rows_per_frame": 200,
        "prefix_cutoff": "original_15m_setup_open",
        "prefix_htf": "4H_and_1H_structure_and_bias_neutral",
        "prefix_range": "15m_support_lt_close_lt_resistance_no_BOS_no_CHoCH",
        "prefix_volatility": "measured_low_or_normal_all_frames_not_HTF_both_low",
        "prefix_instability": "existing_classifier_and_math_core_unstable_denied",
        "event": "unchanged_sweep_reclaim_with_co_confirmed_structure",
        "intrabar_sequence": "unknown",
        "chronology": "P_setup_open_then_S_setup_close_lte_T_trigger_open",
        "trigger_ttl_seconds": 300,
        "max_setup_to_trigger_seconds": 1800,
        "current_route": "Unknown_exact_range_transition_history_missing",
        "current_htf": "4H_and_1H_structure_and_bias_neutral",
        "current_volatility": "measured_low_or_normal_all_frames_not_HTF_both_low",
        "retained_alignment_diagnostic": "multi_timeframe_not_aligned",
        "other_analysis_blockers": "deny",
        "current_math": "existing_mathematical_confirmation_no_opposed_unstable_blocked",
        "required_and_veto": "unchanged_complete_sweep_conditions",
        "score_adjustment": False,
        "past_first_availability_verified": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)
_MAX_RECEIPT = 4 * 1024 * 1024
_FRAMES = ("4H", "1H", "15m", "5m")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class SweepHistoryPermissionError(ValueError):
    """Static, source-redacted errors."""


@dataclass(frozen=True, slots=True, repr=False)
class SweepHistoryPermission:
    """Replayable bytes, not an issuer. Use verify with original raw source."""

    receipt_json: bytes

    @property
    def evaluation_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def admitted(self):
        return json.loads(self.receipt_json)["admitted"]

    @property
    def code(self):
        return json.loads(self.receipt_json)["code"]

    @property
    def execution_authority(self):
        return False

    @property
    def source_authenticity_verified(self):
        return False


def _floor(at, timeframe):
    seconds = BAR_SECONDS[timeframe]
    return _EPOCH + timedelta(
        seconds=((at - _EPOCH) // timedelta(seconds=seconds)) * seconds
    )


def _frames_complete(frames, at):
    return set(frames) == set(_FRAMES) and all(
        len(frames[tf]) >= 200
        and candle_closed_at(frames[tf][-1], tf) == _floor(at, tf)
        and inspect_candles_at(frames[tf], tf, current_time=at).ok
        for tf in _FRAMES
    )


def _measured_operands(views):
    # analyze_timeframe's legacy missing-ATR -> normal and missing-EMA -> neutral
    # defaults must not turn unknown operands into this new policy's permission.
    for view in views.values():
        indicators = view.indicators
        for name in ("ema20", "ema50", "ema200", "atr14", "atr_pct"):
            value = getattr(indicators, name)
            if type(value) is not Decimal or not value.is_finite() or value <= 0:
                return False
        if not view.data_quality_ok or view.data_quality_issues:
            return False
    return True


def _neutral_htf(views):
    return all(
        views[tf].structure.trend == "neutral"
        and views[tf].directional_bias == "neutral"
        for tf in ("4H", "1H")
    )


def _controlled_volatility(views):
    return all(
        view.volatility in {"low", "normal"} for view in views.values()
    ) and not all(views[tf].volatility == "low" for tf in ("4H", "1H"))


def _range_context(market, setup_open):
    frames, lineage = {}, []
    for tf in _FRAMES:
        rows = market.candles[tf]
        prefix = [row for row in rows if candle_closed_at(row, tf) <= setup_open]
        # The complete source has already passed ordering/gap/duplicate checks.
        # Slicing is an explicit historical prefix, never repair or dedup.
        frames[tf] = prefix
        lineage.append(
            {
                "timeframe": tf,
                "closed_row_cutoff": setup_open.isoformat(),
                "confirmed_tail": candle_closed_at(prefix[-1], tf).isoformat()
                if prefix
                else None,
                "included_count": len(prefix),
                "excluded_later_count": len(rows) - len(prefix),
                "included_rows_sha256": _sha(
                    _canonical([row.model_dump(mode="json") for row in prefix])
                ),
                "excluded_later_rows_sha256": _sha(
                    _canonical(
                        [row.model_dump(mode="json") for row in rows[len(prefix) :]]
                    )
                ),
            }
        )
    if not _frames_complete(frames, setup_open):
        return "prior_range_prefix_incomplete", {"lineage": lineage}
    views = {
        tf: analyze_timeframe(
            tf, frames[tf], inspect_candles_at(frames[tf], tf, current_time=setup_open)
        )
        for tf in _FRAMES
    }
    core = mathematical_core_snapshot(views)
    legacy = classify_regime(views)
    bounds = _range_bounds(views["15m"])
    context = {
        "record_kind": "candle_prefix_range_context_not_captured_market_snapshot",
        "cutoff": setup_open.isoformat(),
        "actual_current_source_received_at": market.received_at.isoformat(),
        "past_first_available_at": None,
        "lineage": lineage,
        "views": {tf: view.model_dump(mode="json") for tf, view in views.items()},
        "legacy_classifier": legacy,
        "mathematical_core": core.model_dump(mode="json"),
        "range_bounds": [str(value) for value in bounds] if bounds else None,
    }
    if not _measured_operands(views):
        return "prior_range_operands_unknown", context
    if not _neutral_htf(views):
        return "prior_range_htf_not_neutral", context
    if not _controlled_volatility(views):
        return "prior_range_volatility_denied", context
    if legacy != "range_or_transition" or core.status in {"unstable", "insufficient"}:
        return "prior_range_safety_denied", context
    if (
        bounds is None
        or views["15m"].structure.bos is not None
        or views["15m"].structure.choch is not None
    ):
        return "prior_range_structure_missing", context
    return "passed", context


def current_sweep_checks(analysis, market, route, direction):
    """Existing current operands only; never extract or replace an event.

    Callers must rebuild analysis from source. This helper grants no authority.
    Its return values preserve the original v1 evaluator's exact audit fields.
    """
    views = analysis.timeframe_analyses
    if not _measured_operands(views):
        return "current_operands_unknown", None, None
    if not _neutral_htf(views):
        return "current_htf_not_neutral", None, None
    if not _controlled_volatility(views):
        return "current_volatility_denied", None, None
    if route.regime != MarketRegime.UNKNOWN or route.fail_codes != (
        "range_transition_history_missing",
    ):
        return "current_route_not_exact_range_transition", None, None
    if any(code != "multi_timeframe_not_aligned" for code in analysis.blockers):
        return "current_analysis_blocked", None, None
    math = mathematical_confirmation(analysis, direction)
    math_value = math.model_dump(mode="json")
    if (
        math.status in {"insufficient", "unstable", "opposed"}
        or math.risk_grade == "blocked"
    ):
        return "current_mathematical_veto", math_value, None
    assessed = assess_conditions(
        StrategyContext(analysis, market, 0, Decimal(1)), STRATEGY
    )
    conditions = {
        "direction": assessed.direction,
        "score": assessed.score,
        "required_failures": list(assessed.required_failures),
        "veto_failures": list(assessed.veto_failures),
    }
    if (
        assessed.direction != direction
        or assessed.required_failures
        or assessed.veto_failures
    ):
        return "current_sweep_predicates_rejected", math_value, conditions
    return "passed", math_value, conditions


def evaluate_sweep_history_permission(
    market,
    *,
    report_id,
    direction,
    observed_at,
    analysis_version,
    expected_policy_sha256=POLICY_SHA256,
):
    """Compute fixed policy over raw closed history without altering old G2.

    This component does not run complete G1, timing ledger, location, bracket,
    risk or execution checks. Current receipt and old candle close are distinct;
    no historical receipt or predictive point-in-time availability is invented.
    """
    if (
        type(report_id) is not str
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", report_id) is None
        or type(direction) is not str
        or direction not in {"long", "short"}
        or type(analysis_version) is not str
        or not 1 <= len(analysis_version) <= 64
        or analysis_version.strip() != analysis_version
        or type(expected_policy_sha256) is not str
        or expected_policy_sha256 != POLICY_SHA256
    ):
        raise SweepHistoryPermissionError("sweep_policy_inputs_invalid")
    try:
        now = _utc(observed_at).astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        raise SweepHistoryPermissionError("sweep_policy_clock_invalid") from None
    value = {
        "schema_version": "ctcc.sweep_history_permission.v1",
        "policy_id": POLICY_ID,
        "policy_sha256": POLICY_SHA256,
        "report_id": report_id,
        "strategy": STRATEGY,
        "direction": direction,
        "observed_at": now.isoformat(),
        "analysis_version": analysis_version,
        "instrument_id": None,
        "source_sha256": None,
        "source_received_at": None,
        "route": None,
        "detection": None,
        "event_key": None,
        "range_context": None,
        "range_context_sha256": None,
        "chronology": None,
        "conditions": None,
        "mathematical_confirmation": None,
        "retained_analysis_blockers": None,
        "source_authenticity_verified": False,
        "execution_authority": False,
        "complete_path_verified": False,
        "qualification_performed": False,
        "predictive_point_in_time_verified": False,
        "original_source_verified": False,
        "atomic_risk_reserved": False,
        "runtime_admissible": False,
    }

    def finish(code):
        value.update(
            code=code,
            admitted=code == "passed",
            permission_scope="history_evidence_only",
        )
        encoded = journal.canonical(value)
        if len(encoded) > _MAX_RECEIPT:
            raise SweepHistoryPermissionError("sweep_evidence_size_exceeded")
        return SweepHistoryPermission(encoded)

    try:
        _guard(market, source=True)
        checked = _prepared_market(market, now)
        if checked.symbol != to_canonical_symbol(checked.instrument_id):
            return finish("source_identity_mismatch")
        if not _frames_complete(checked.candles, now):
            return finish("source_history_incomplete")
        with localcontext(Context(prec=100)):
            analysis = analyze_snapshot_at(
                checked, evaluated_at=now, version=analysis_version
            )
            _, _, _, source_sha = _copy_source(checked, analysis, now)
            route = route_regime(analysis)
            value.update(
                instrument_id=checked.instrument_id,
                source_sha256=source_sha,
                source_received_at=checked.received_at.isoformat(),
                retained_analysis_blockers=list(analysis.blockers),
                route={
                    "regime": route.regime.value,
                    "decision": route.decision.value,
                    "fail_codes": list(route.fail_codes),
                    "allowed_strategies": list(route.allowed_strategies),
                    "snapshot_sha256": route.snapshot_sha256,
                },
            )
            timing = TIMING_POLICIES[STRATEGY]
            if (
                timing.trigger_ttl_seconds != 300
                or timing.max_setup_to_trigger_seconds != 1800
            ):
                return finish("fixed_timing_policy_changed")
            event = extract_trigger(
                checked,
                analysis,
                report_id=report_id,
                strategy=STRATEGY,
                direction=direction,
                observed_at=now,
                trigger_ttl_seconds=300,
            )
            value.update(
                detection=event.model_dump(mode="json"), event_key=event_identity(event)
            )
            if event.source_sha256 != source_sha:
                return finish("event_source_mismatch")
            if (
                event.fail_codes
                or event.trigger is None
                or event.setup_time is None
                or event.trigger.invalidation_reason is not None
            ):
                return finish("original_sweep_event_rejected")
            basis = dict(event.setup_basis)
            if (
                event.setup_type != "sweep_reclaim_with_co_confirmed_structure"
                or event.source_timeframe != "5m"
                or basis.get("setup_timeframe") != "15m"
                or basis.get("intrabar_sequence") != "unknown"
            ):
                return finish("original_sweep_event_type_mismatch")
            setup, trigger = event.setup_time, event.trigger.trigger_time
            setup_open = setup - timedelta(minutes=15)
            pivot_known = _utc(
                datetime.fromisoformat(basis["pivot_known_at"])
            ).astimezone(UTC)
            if (
                not setup <= trigger - timedelta(minutes=5) <= trigger <= now
                or pivot_known > setup_open
                or trigger - setup > timedelta(seconds=1800)
                or event.trigger.expires_at != trigger + timedelta(seconds=300)
                or now >= event.trigger.expires_at
            ):
                return finish("original_sweep_chronology_rejected")
            value["chronology"] = {
                "P_setup_open": setup_open.isoformat(),
                "S_setup_close": setup.isoformat(),
                "T_trigger_close": trigger.isoformat(),
                "C_decision_cutoff": now.isoformat(),
                "expires_at": event.trigger.expires_at.isoformat(),
                "pivot_known_at": pivot_known.isoformat(),
                "intrabar_sequence": "unknown",
            }
            code, context = _range_context(checked, setup_open)
            value.update(
                range_context=context,
                range_context_sha256=journal.digest(journal.canonical(context)),
            )
            if code != "passed":
                return finish(code)
            code, math_value, conditions = current_sweep_checks(
                analysis, checked, route, direction
            )
            value["mathematical_confirmation"] = math_value
            value["conditions"] = conditions
            if code != "passed":
                return finish(code)
    except Exception:  # noqa: BLE001 -- retain static failure, never source exception values
        return finish("source_invalid")
    return finish("passed")


def verify_sweep_history_permission(result, market, **original_inputs):
    if type(result) is not SweepHistoryPermission:
        raise SweepHistoryPermissionError("exact_sweep_evidence_required")
    raw = object.__getattribute__(result, "receipt_json")
    if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_RECEIPT:
        raise SweepHistoryPermissionError("sweep_evidence_bytes_invalid")
    replayed = evaluate_sweep_history_permission(market, **original_inputs)
    if raw != replayed.receipt_json:
        raise SweepHistoryPermissionError("sweep_history_replay_mismatch")
    return replayed
