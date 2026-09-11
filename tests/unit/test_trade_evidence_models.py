"""Offline synthetic charts bind sources, not callers' claimed permissions."""

import ast
import hashlib
import inspect
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, timedelta, timezone
from decimal import ROUND_UP, Context, Decimal, Inexact, localcontext

import pytest
from pydantic import ValidationError

from app.indicators.core import ema
from app.market.quality.candles import BAR_SECONDS, candle_closed_at
from app.structure.engine import find_swings
from app.trade_evidence import models, service
from app.trade_evidence.models import EvidenceSnapshot
from app.trade_evidence.service import (
    EvidenceError,
    prepare_evidence,
    validate_snapshot,
)
from app.trade_qualification.models import (
    GATE_ORDER,
    EntryQualificationResult,
    GateAssessment,
    MarketRegime,
)
from tests.unit.test_qualification_entry_chain import _extract, _quote, _zone
from tests.unit.test_qualification_events import OBSERVED, _rows, _snapshot
from tests.unit.test_qualification_structural_selection import _inputs, _select

D = Decimal


def evidence_inputs(direction="long", *, prefix=0):
    """All market data and gate claims here are synthetic, never Demo samples."""
    market, analysis, detection, zone = _inputs(direction)
    protection = _select((market, analysis, detection, zone))
    selected = protection.selected
    entry = detection.trigger.trigger_price
    with localcontext(Context(prec=100)):
        gross = (
            abs(selected.target.final_target - entry)
            / abs(entry - selected.stop.final_stop)
        ).quantize(D("1e-20"))
    qualification = EntryQualificationResult(
        report_id=detection.report_id,
        symbol=detection.symbol,
        strategy=detection.strategy,
        direction=direction,
        evaluated_at=OBSERVED,
        raw_score=95,
        effective_score=90,
        market_regime=MarketRegime.TREND,
        htf_bias=direction,
        setup_state="valid",
        entry_timing_state="valid",
        trigger=detection.trigger,
        entry_zone=zone,
        candidate_entry=entry,
        reference_price=entry,
        stop_loss=selected.stop.final_stop,
        take_profit=selected.target.final_target,
        gross_rr=gross,
        net_rr=D(2),
        gates=tuple(
            GateAssessment(
                report_id=detection.report_id,
                gate=gate,
                passed=True,
                code="passed",
                reason="Synthetic caller statement; not independently evaluated",
                measured_values={"synthetic_test": True},
            )
            for gate in GATE_ORDER[:prefix]
        ),
    )
    return (
        market,
        analysis,
        {
            "qualification": qualification,
            "detection": detection,
            "quote": _quote(detection, entry),
            "protection": protection,
            "prepared_at": OBSERVED,
            "purpose": "synthetic_test",
        },
    )


def _prepared(direction="long", **updates):
    market, analysis, arguments = evidence_inputs(direction)
    return prepare_evidence(market, analysis, **(arguments | updates))


@pytest.mark.parametrize("direction", ["long", "short"])
def test_complete_source_candidate_identity_and_rebuild_roundtrip(direction):
    market, analysis, arguments = evidence_inputs(direction, prefix=11)
    before = (deepcopy(market), deepcopy(analysis))
    result = prepare_evidence(market, analysis, **arguments)
    assert validate_snapshot(result) == result
    assert (
        validate_snapshot(
            EvidenceSnapshot.model_validate_json(
                result.model_dump_json(round_trip=True)
            )
        )
        == result
    )
    assert (
        result.report_id
        == result.qualification.report_id
        == result.detection.report_id
        == result.quote.report_id
    )
    assert (
        result.direction == direction and result.instrument_id == market.instrument_id
    )
    assert result.source_sha256 == result.detection.source_sha256
    assert (
        result.source_sha256 == hashlib.sha256(result.source_json.encode()).hexdigest()
    )
    source = json.loads(result.source_json)
    assert len(source["market"]["candles"]["5m"]) == 240
    assert result.protection_audit_json == arguments["protection"].to_audit_json()
    assert result.zone_tick_size == D("0.01")
    assert result.execution_authority is False
    assert result.gate_assessments_verified is False
    assert result.source_authenticity_verified is False
    assert result.evidence_gate == result.execution_recheck == "not_evaluated"
    assert not result.qualification.qualified
    assert not result.qualification.evidence_complete
    assert (market, analysis) == before


def test_crop_uses_full_history_prefix_ema_not_repeated_terminal_indicators():
    market, analysis, arguments = evidence_inputs()
    result = prepare_evidence(market, analysis, **arguments)
    larger = prepare_evidence(market, analysis, **arguments, candle_limit=120)
    assert result.source_json == larger.source_json
    assert result.candidate_sha256 == larger.candidate_sha256
    assert tuple(p.timeframe for p in result.panels) == ("4H", "1H", "15m", "5m")
    for panel in result.panels:
        rows = market.candles[panel.timeframe]
        assert len(panel.candles) == 80
        assert panel.total_confirmed_candles == 240
        assert panel.crop_start == rows[-80].timestamp
        assert panel.crop_end == candle_closed_at(rows[-1], panel.timeframe)
        assert (
            panel.candles[0].open_time + timedelta(seconds=BAR_SECONDS[panel.timeframe])
            == panel.candles[0].close_time
        )
        with localcontext(Context(prec=100)):
            assert panel.candles[0].ema20 == ema([row.close for row in rows[:-79]], 20)
        if panel.timeframe == "5m":
            assert len({candle.ema20 for candle in panel.candles}) > 1
        assert panel.candles[0].ema200 is None
        assert panel.candles[-1].ema200 is not None


def test_pivots_are_known_only_after_two_right_hand_bar_closes():
    market, analysis, arguments = evidence_inputs()
    result = prepare_evidence(market, analysis, **arguments)
    for panel in result.panels:
        rows = market.candles[panel.timeframe]
        swings = find_swings(rows)
        for level in panel.levels:
            assert level.known_at <= level.as_of == panel.crop_end
            if level.kind in {"support", "resistance"}:
                kind = "low" if level.kind == "support" else "high"
                candidates = [s for s in swings if s.kind == kind][-3:]
                assert any(
                    level.low == s.price
                    and level.known_at
                    == candle_closed_at(rows[s.index + 2], panel.timeframe)
                    for s in candidates
                )


def test_blocked_high_score_packet_retains_unknown_without_creating_trigger_or_permission():
    market, analysis, arguments = evidence_inputs()
    q = arguments["qualification"]
    q = EntryQualificationResult(
        report_id=q.report_id,
        symbol=q.symbol,
        strategy=q.strategy,
        direction=q.direction,
        evaluated_at=OBSERVED,
        raw_score=95,
        effective_score=95,
        gates=(
            GateAssessment(
                report_id=q.report_id,
                gate=GATE_ORDER[0],
                passed=False,
                code="synthetic_data_rejected",
                reason="Explicit failure despite usable chart OHLC",
                measured_values={"synthetic_test": True},
            ),
        ),
    )
    result = prepare_evidence(
        market,
        analysis,
        qualification=q,
        prepared_at=OBSERVED,
        purpose="synthetic_test",
    )
    assert result.qualification.fail_codes == ("synthetic_data_rejected",)
    assert result.qualification.candidate_entry is None
    assert result.detection is result.quote is result.protection_audit_json is None
    assert result.qualification.htf_bias is None
    assert result.qualification.market_regime == MarketRegime.UNKNOWN
    assert validate_snapshot(result) == result


def test_short_history_keeps_missing_indicators_none_and_labels_actual_crop():
    frames = {tf: _rows(tf, count=80) for tf in ("4H", "1H", "15m", "5m")}
    market, analysis = _snapshot(frames)
    q = EntryQualificationResult(
        report_id="synthetic_short_history",
        symbol=market.symbol,
        strategy="trend_pullback",
        direction="long",
        evaluated_at=OBSERVED,
        raw_score=95,
        effective_score=95,
    )
    result = prepare_evidence(
        market,
        analysis,
        qualification=q,
        prepared_at=OBSERVED,
        purpose="synthetic_test",
    )
    assert all(p.total_confirmed_candles == len(p.candles) == 80 for p in result.panels)
    assert all(c.ema200 is None for p in result.panels for c in p.candles)
    assert all(p.candles[0].ema20 is None for p in result.panels)


@pytest.mark.parametrize("prefix", [12, 13])
def test_evidence_cannot_consume_already_passed_g12_or_execution_recheck(prefix):
    market, analysis, arguments = evidence_inputs(prefix=prefix)
    with pytest.raises(EvidenceError, match="precede_g12"):
        prepare_evidence(market, analysis, **arguments)


@pytest.mark.parametrize(
    "defect",
    [
        "event_hash",
        "event_price",
        "event_time",
        "event_direction",
        "event_report",
        "quote_report",
        "quote_instrument",
        "quote_reference",
        "zone_price",
        "zone_hash",
        "stop",
        "target",
        "entry",
    ],
)
def test_candidate_source_identity_and_geometry_cannot_be_reassigned(defect):
    market, analysis, arguments = evidence_inputs()
    q = arguments["qualification"]
    event = arguments["detection"]
    quote = arguments["quote"]
    if defect == "event_hash":
        arguments["detection"] = event.model_copy(update={"source_sha256": "a" * 64})
    elif defect == "event_price":
        altered = event.trigger.model_copy(
            update={"trigger_price": event.trigger.trigger_price + D(1)}
        )
        arguments["detection"] = event.model_copy(update={"trigger": altered})
    elif defect == "event_time":
        arguments["detection"] = event.model_copy(
            update={"observed_at": OBSERVED + timedelta(seconds=1)}
        )
    elif defect in {"event_direction", "event_report"}:
        key, value = (
            ("direction", "short")
            if defect == "event_direction"
            else ("report_id", "different_report")
        )
        arguments["detection"] = event.model_copy(update={key: value})
    elif defect in {"quote_report", "quote_instrument", "quote_reference"}:
        key, value = {
            "quote_report": ("report_id", "different_report"),
            "quote_instrument": ("instrument_id", "ETH-USDT-SWAP"),
            "quote_reference": ("ask", quote.ask + D(1)),
        }[defect]
        arguments["quote"] = quote.model_copy(update={key: value})
    elif defect in {"zone_price", "zone_hash"}:
        update = (
            {"zone_high": q.entry_zone.zone_high + D(1)}
            if defect == "zone_price"
            else {"source_sha256": "b" * 64}
        )
        arguments["qualification"] = q.model_copy(
            update={"entry_zone": q.entry_zone.model_copy(update=update)}
        )
    else:
        key = {
            "stop": "stop_loss",
            "target": "take_profit",
            "entry": "candidate_entry",
        }[defect]
        arguments["qualification"] = q.model_copy(
            update={key: getattr(q, key) + D("0.01")}
        )
    with pytest.raises(EvidenceError):
        prepare_evidence(market, analysis, **arguments)


@pytest.mark.parametrize(
    "target",
    [
        "market",
        "candle",
        "analysis",
        "indicator",
        "qualification",
        "trigger",
        "quote",
        "zone",
        "protection",
    ],
)
def test_nested_dirty_model_or_dataclass_extras_are_not_silently_discarded(target):
    market, analysis, arguments = evidence_inputs()
    q = arguments["qualification"]
    extra = {"execution_authority": True}
    if target == "market":
        market = market.model_copy(update=extra)
    elif target == "candle":
        market.candles["5m"][-1] = market.candles["5m"][-1].model_copy(update=extra)
    elif target == "analysis":
        analysis = analysis.model_copy(update=extra)
    elif target == "indicator":
        analysis.timeframe_analyses["5m"].indicators = analysis.timeframe_analyses[
            "5m"
        ].indicators.model_copy(update=extra)
    elif target == "qualification":
        arguments["qualification"] = q.model_copy(update=extra)
    elif target == "trigger":
        arguments["detection"] = arguments["detection"].model_copy(
            update={"trigger": q.trigger.model_copy(update=extra)}
        )
    elif target == "quote":
        arguments["quote"] = arguments["quote"].model_copy(update=extra)
    elif target == "zone":
        arguments["qualification"] = q.model_copy(
            update={"entry_zone": q.entry_zone.model_copy(update=extra)}
        )
    else:
        object.__setattr__(arguments["protection"], "hidden_authority", True)
    with pytest.raises(EvidenceError, match="dirty"):
        prepare_evidence(market, analysis, **arguments)


@pytest.mark.parametrize(
    "defect",
    ["nan", "duplicate", "gap", "unsorted", "future", "quality", "analysis_close"],
)
def test_bad_source_never_becomes_trusted_chart(defect):
    market, analysis, arguments = evidence_inputs()
    rows = market.candles["5m"]
    if defect == "nan":
        rows[-1] = rows[-1].model_copy(update={"close": D("NaN")})
    elif defect == "duplicate":
        rows[-1] = rows[-2]
    elif defect == "gap":
        rows.pop(-2)
    elif defect == "unsorted":
        rows[-2], rows[-3] = rows[-3], rows[-2]
    elif defect == "future":
        rows[-1] = rows[-1].model_copy(update={"timestamp": OBSERVED})
    elif defect == "quality":
        market.quality["5m"].ok = False
    else:
        analysis.timeframe_analyses["5m"].close += D(1)
    with pytest.raises(EvidenceError):
        prepare_evidence(market, analysis, **arguments)


def test_protection_is_rebuilt_not_trusted_as_frozen_dataclass():
    market, analysis, arguments = evidence_inputs()
    protection = arguments["protection"]
    altered = replace(
        protection.selected,
        stop=replace(
            protection.selected.stop,
            final_stop=protection.selected.stop.final_stop + D("0.01"),
        ),
    )
    arguments["protection"] = replace(protection, selected=altered)
    with pytest.raises(EvidenceError, match="structural_rebuild_mismatch"):
        prepare_evidence(market, analysis, **arguments)


def test_unprotected_zone_needs_explicit_tick_and_protected_tick_must_match():
    market, analysis, arguments = evidence_inputs()
    with pytest.raises(EvidenceError, match="zone_tick_conflict"):
        prepare_evidence(market, analysis, **arguments, zone_tick_size=D("0.1"))
    q = arguments["qualification"].model_copy(
        update={"stop_loss": None, "take_profit": None}
    )
    arguments.update(qualification=q, protection=None)
    with pytest.raises(EvidenceError, match="zone_tick_missing"):
        prepare_evidence(market, analysis, **arguments)
    result = prepare_evidence(market, analysis, **arguments, zone_tick_size=D("0.01"))
    assert validate_snapshot(result) == result


@pytest.mark.parametrize("direction", ["long", "short"])
def test_legal_existing_zone_entry_can_differ_from_trigger_without_render_repricing(
    direction,
):
    market, analysis, arguments = evidence_inputs(direction)
    q, event = arguments["qualification"], arguments["detection"]
    entry = q.candidate_entry + (D("-0.01") if direction == "long" else D("0.01"))
    assert q.entry_zone.zone_low <= entry <= q.entry_zone.zone_high
    protection = _select((market, analysis, event, q.entry_zone), entry=entry)
    assert protection.protection_valid
    q = q.model_copy(
        update={
            "candidate_entry": entry,
            "reference_price": entry,
            "stop_loss": protection.selected.stop.final_stop,
            "take_profit": protection.selected.target.final_target,
        }
    )
    arguments.update(qualification=q, protection=protection, quote=_quote(event, entry))
    result = prepare_evidence(market, analysis, **arguments)
    assert result.qualification.candidate_entry == entry
    assert (
        result.qualification.trigger.trigger_price
        == event.trigger.trigger_price
        != entry
    )
    assert result.qualification.stop_loss == protection.selected.stop.final_stop
    assert validate_snapshot(result) == result


def test_panel_calculation_and_rebuild_ignore_hostile_decimal_context():
    market, analysis, arguments = evidence_inputs()
    baseline = prepare_evidence(market, analysis, **arguments)
    with localcontext(Context(prec=6, rounding=ROUND_UP)) as context:
        context.traps[Inexact] = True
        assert prepare_evidence(market, analysis, **arguments) == baseline
        assert validate_snapshot(baseline) == baseline


def test_caller_analysis_levels_and_ema_cannot_be_drawn_as_historical_truth():
    market, analysis, arguments = evidence_inputs()
    baseline = prepare_evidence(market, analysis, **arguments)
    q = arguments["qualification"]
    modified = deepcopy(analysis)
    for view in modified.timeframe_analyses.values():
        view.indicators.ema20 = D("1234")
        view.structure.support_levels = [D("1")]
        view.structure.resistance_levels = [D("9999")]
        view.structure.trend = "strong_bearish"
    event = _extract(market, modified, q.strategy, q.direction)
    zone, code = _zone(event)
    assert code == "passed"
    protection = _select((market, modified, event, zone))
    assert protection.protection_valid
    q = q.model_copy(update={"trigger": event.trigger, "entry_zone": zone})
    arguments.update(qualification=q, detection=event, protection=protection)
    result = prepare_evidence(market, modified, **arguments)
    assert result.source_sha256 != baseline.source_sha256
    assert result.candidate_sha256 != baseline.candidate_sha256
    assert result.panels == baseline.panels
    assert validate_snapshot(result) == result


def test_protection_observation_offset_is_normalized_in_canonical_audit():
    market, analysis, arguments = evidence_inputs()
    baseline = prepare_evidence(market, analysis, **arguments)
    protection = replace(
        arguments["protection"],
        observed_at=OBSERVED.astimezone(timezone(timedelta(hours=8))),
    )
    result = prepare_evidence(
        market, analysis, **(arguments | {"protection": protection})
    )
    assert result == baseline
    assert validate_snapshot(result) == result


@pytest.mark.parametrize(
    "update",
    [
        {"purpose": "real_profit_proven"},
        {"candle_limit": 201},
        {"source_json": "界" * (3 * 1024 * 1024)},
        {"source_json": "x" * (8 * 1024 * 1024 + 1)},
    ],
)
def test_snapshot_limits_include_utf8_source_bytes_and_known_purpose(update):
    result = _prepared()
    with pytest.raises(EvidenceError):
        validate_snapshot(result.model_copy(update=update))


@pytest.mark.parametrize(
    "value",
    [
        "[" * 1100 + "0" + "]" * 1100,
        "[" * 30 + "0" + "]" * 30,
        '{"x":NaN}',
        '{"x":' + "9" * 5000 + "}",
    ],
)
def test_adversarial_json_is_bounded_and_errors_are_stable(value):
    result = _prepared()
    with pytest.raises(EvidenceError):
        validate_snapshot(result.model_copy(update={"source_json": value}))


@pytest.mark.parametrize(
    "defect",
    [
        "digest",
        "candle",
        "extra",
        "source_whitespace",
        "source_duplicate",
        "audit",
        "panel_order",
    ],
)
def test_renderer_consumers_must_reject_tampered_snapshots(defect):
    result = _prepared()
    if defect == "digest":
        altered = result.model_copy(update={"candidate_sha256": "c" * 64})
    elif defect == "candle":
        panel = result.panels[0]
        candle = panel.candles[0].model_copy(update={"ema20": D(123)})
        panel = panel.model_copy(update={"candles": (candle, *panel.candles[1:])})
        altered = result.model_copy(update={"panels": (panel, *result.panels[1:])})
    elif defect == "extra":
        altered = result.model_copy(update={"hidden_order_permission": True})
    elif defect == "source_whitespace":
        altered = result.model_copy(update={"source_json": result.source_json + " "})
    elif defect == "source_duplicate":
        altered = result.model_copy(
            update={"source_json": '{"market":{},' + result.source_json[1:]}
        )
    elif defect == "audit":
        audit = json.loads(result.protection_audit_json)
        audit["selected"]["stop"]["final_stop"] = "1"
        altered = result.model_copy(
            update={
                "protection_audit_json": json.dumps(
                    audit, sort_keys=True, separators=(",", ":")
                )
            }
        )
    else:
        altered = result.model_copy(update={"panels": tuple(reversed(result.panels))})
    with pytest.raises(EvidenceError):
        validate_snapshot(altered)


def test_snapshot_nested_structures_are_immutable():
    result = _prepared()
    with pytest.raises(ValidationError):
        result.report_id = "changed"
    with pytest.raises(ValidationError):
        result.panels[0].candles[0].close = D(1)
    with pytest.raises(TypeError):
        result.panels[0] = result.panels[1]


@pytest.mark.parametrize(
    "field",
    [
        "execution_authority",
        "gate_assessments_verified",
        "source_authenticity_verified",
    ],
)
@pytest.mark.parametrize("value", [True, 0, "false"])
def test_authority_flags_cannot_be_coerced(field, value):
    result = _prepared()
    with pytest.raises(EvidenceError):
        validate_snapshot(result.model_copy(update={field: value}))


@pytest.mark.parametrize("limit", [True, 79, 201, D(80), "80"])
def test_chart_cropping_policy_is_strict_and_bounded(limit):
    with pytest.raises(EvidenceError):
        _prepared(candle_limit=limit)


def test_equivalent_utc_offset_preparation_is_identical_and_naive_or_future_rejected():
    baseline = _prepared()
    assert (
        _prepared(prepared_at=OBSERVED.astimezone(timezone(timedelta(hours=8))))
        == baseline
    )
    assert baseline.prepared_at.tzinfo == UTC
    with pytest.raises(EvidenceError):
        _prepared(prepared_at=OBSERVED.replace(tzinfo=None))
    with pytest.raises(EvidenceError):
        _prepared(prepared_at=OBSERVED - timedelta(seconds=1))


@pytest.mark.parametrize("module", [models, service])
def test_evidence_preparation_is_pure_and_never_imports_runtime_or_io(module):
    tree = ast.parse(inspect.getsource(module))
    prohibited = (
        "app.exchange",
        "app.demo_automation",
        "app.live_automation",
        "app.config",
        "app.market.service",
        "requests",
        "httpx",
        "socket",
        "subprocess",
        "sqlalchemy",
        "os",
        "pathlib",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(name.name.startswith(prohibited) for name in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(prohibited)
        assert not isinstance(node, ast.AsyncFunctionDef)
