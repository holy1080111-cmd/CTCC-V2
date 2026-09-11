"""Synthetic packet end-to-end tests, not an actual twelve-gate trading engine.

Explicit fixture assessments exercise presentation. They do not prove that G1–11
ran, establish actual account truth, or count as shadow/Demo observations.
"""

import hashlib
import json
from datetime import timedelta
from decimal import Context, Decimal, localcontext

import pytest

from app.trade_evidence.renderer import render_evidence
from app.trade_evidence.service import prepare_evidence, validate_snapshot
from app.trade_evidence.storage import publish_evidence
from app.trade_qualification.models import (
    GATE_ORDER,
    EntryQualificationResult,
    GateAssessment,
    MarketRegime,
)
from tests.unit.test_qualification_entry_chain import _quote
from tests.unit.test_qualification_events import OBSERVED, _snapshot, detect, source
from tests.unit.test_qualification_risk_chain import _parts

D = Decimal
IMAGE_NAMES = {"4h.png", "1h.png", "15m.png", "5m.png", "summary.png"}


def synthetic_snapshot(case="long"):
    """A reproducible presentation fixture; no settings, IO or exchange access."""
    if case == "wait":
        market, _ = source("fvg_return")
        market.candles["5m"] = market.candles["5m"][-34:]
        market, analysis = _snapshot(market.candles)
        event = detect(market, analysis, "fvg_return")
        assert event.trigger is None and event.fail_codes == ("trigger_missing",)
        q = EntryQualificationResult(
            report_id=event.report_id,
            symbol=event.symbol,
            strategy=event.strategy,
            direction=event.direction,
            evaluated_at=OBSERVED,
            raw_score=95,
            effective_score=90,
            setup_state="valid",
            entry_timing_state="wait",
            gates=(),  # Indicator history is insufficient; do not fake G1 pass.
        )
        return prepare_evidence(
            market,
            analysis,
            qualification=q,
            detection=event,
            prepared_at=OBSERVED,
            purpose="synthetic_test",
        )

    direction = "short" if case == "short" else "long"
    inputs, structure, quote, _, _, costs = _parts(direction)
    market, analysis, event, zone = inputs
    prefix = 7 if case == "cancel" else 11
    gates = tuple(
        GateAssessment(
            report_id=event.report_id,
            gate=gate,
            passed=not (case == "cancel" and index == 6),
            code="entry_zone_missed"
            if case == "cancel" and index == 6
            else "passed",
            reason="Synthetic renderer assessment; not actual gate execution.",
            measured_values={"synthetic": True, "source_bound_fixture": True},
        )
        for index, gate in enumerate(GATE_ORDER[:prefix])
    )
    if case == "cancel":
        quote = _quote(event, zone.zone_high + D(1))
    reference = quote.ask if direction == "long" else quote.bid
    with localcontext(Context(prec=100)):
        gross_rr = costs.gross_rr.quantize(D("1e-20"))
        net_rr = costs.net_rr.quantize(D("1e-20"))
    q = EntryQualificationResult(
        report_id=event.report_id,
        symbol=event.symbol,
        strategy=event.strategy,
        direction=direction,
        evaluated_at=OBSERVED,
        raw_score=95,
        effective_score=90,
        market_regime=MarketRegime.TREND,
        htf_bias=direction,
        setup_state="valid",
        entry_timing_state="valid",
        trigger=event.trigger,
        entry_zone=zone,
        candidate_entry=costs.candidate_entry,
        reference_price=reference,
        stop_loss=costs.stop_loss,
        take_profit=costs.take_profit,
        gross_rr=gross_rr,
        net_rr=net_rr,
        gates=gates,
    )
    return prepare_evidence(
        market,
        analysis,
        qualification=q,
        detection=event,
        quote=quote,
        protection=structure,
        prepared_at=OBSERVED,
        purpose="synthetic_test",
    )


@pytest.mark.parametrize("case", ["long", "short", "wait", "cancel"])
def test_source_to_render_to_publication_preserves_one_immutable_packet(tmp_path, case):
    snapshot = synthetic_snapshot(case)
    assert validate_snapshot(snapshot) == snapshot
    assert snapshot.execution_authority is False
    assert snapshot.gate_assessments_verified is False
    assert snapshot.qualification.qualified is False
    source_before = snapshot.model_dump_json(round_trip=True)
    rendered = render_evidence(snapshot)
    assert set(rendered) == IMAGE_NAMES | {"report.json"}
    report = json.loads(rendered["report.json"])
    assert report["report_id"] == snapshot.report_id
    assert report["schema_version"] == "ctcc.trade_evidence.v1"
    assert set(report["images"]) == IMAGE_NAMES
    for name in IMAGE_NAMES:
        assert report["images"][name]["sha256"] == hashlib.sha256(
            rendered[name]
        ).hexdigest()
        assert report["images"][name]["size_bytes"] == len(rendered[name])
    root = tmp_path / "trusted-evidence-root"
    root.mkdir()
    completed_at = OBSERVED + timedelta(seconds=2)
    receipt = publish_evidence(
        root,
        report_id=snapshot.report_id,
        files=rendered,
        clock=lambda: completed_at,
    )
    directory = root / snapshot.report_id
    assert {path.name for path in directory.iterdir()} == set(rendered)
    for name, payload in rendered.items():
        assert (directory / name).read_bytes() == payload
    assert receipt.completed_at == completed_at
    assert receipt.report_sha256 == hashlib.sha256(rendered["report.json"]).hexdigest()
    assert snapshot.model_dump_json(round_trip=True) == source_before
    assert snapshot.evidence_gate == snapshot.execution_recheck == "not_evaluated"
    # Render and successful publication cannot mark an independent gate as passed.
    assert snapshot.qualification.evidence_complete is False
    assert snapshot.qualification.execution_recheck_passed is False
    again = publish_evidence(
        root,
        report_id=snapshot.report_id,
        files=rendered,
        clock=lambda: completed_at + timedelta(seconds=1),
    )
    assert again.status == "already_present"
    assert again.report_sha256 == receipt.report_sha256
