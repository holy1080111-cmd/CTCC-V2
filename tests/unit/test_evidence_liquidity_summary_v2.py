"""Versioned source-derived summary; real native publication, synthetic market."""

import hashlib
import json
from decimal import Decimal
from io import BytesIO

import pytest
from PIL import Image

from app.trade_evidence import gates, renderer
from app.trade_evidence.service import EvidenceError
from app.trade_qualification.engine import evaluate_pre_evidence
from tests.unit.qualification_engine_fixtures import engine_inputs, engine_source
from tests.unit.test_qualification_evidence_gate import Clock, assert_no_authority
from tests.unit.test_trade_evidence_pipeline import synthetic_snapshot


@pytest.mark.parametrize("case", ("long", "short", "wait", "cancel"))
def test_v2_preserves_exact_candidate_and_source_without_granting_authority(case):
    snapshot = synthetic_snapshot(case)
    before = snapshot.model_dump_json(round_trip=True)
    old = renderer.render_evidence(snapshot)
    new = renderer.render_evidence_liquidity_v2(snapshot)
    assert new == renderer.render_evidence_liquidity_v2(snapshot)
    assert snapshot.model_dump_json(round_trip=True) == before
    report = json.loads(new["report.json"])
    assert report["snapshot"] == json.loads(old["report.json"])["snapshot"]
    assert report["renderer"]["renderer_version"] == renderer.LIQUIDITY_RENDERER_VERSION
    for field in (
        "execution_authority",
        "gate_assessments_verified",
        "source_authenticity_verified",
    ):
        assert report[field] is False
    for name in renderer.IMAGE_NAMES:
        assert report["images"][name]["sha256"] == hashlib.sha256(new[name]).hexdigest()
        with Image.open(BytesIO(new[name])) as image:
            image.load()
            assert image.info["Software"] == renderer.LIQUIDITY_RENDERER_VERSION
            if name == "summary.png":
                assert image.size == renderer.LIQUIDITY_SUMMARY_SIZE
            else:
                # This profile changes summary content only; panel pixels retain
                # every existing candle, geometry, source label and warning.
                with Image.open(BytesIO(old[name])) as prior:
                    assert image.tobytes() == prior.tobytes()


def test_v2_does_not_render_caller_replacement_liquidity_inventory():
    snapshot = synthetic_snapshot("long")
    panel = next(
        p for p in snapshot.panels if any(x.kind == "liquidity" for x in p.levels)
    )
    level = next(x for x in panel.levels if x.kind == "liquidity")
    replacement = level.model_copy(update={"low": Decimal(999), "high": Decimal(999)})
    changed_panel = panel.model_copy(
        update={"levels": tuple(replacement if x is level else x for x in panel.levels)}
    )
    changed = snapshot.model_copy(
        update={
            "panels": tuple(changed_panel if p is panel else p for p in snapshot.panels)
        }
    )
    with pytest.raises(EvidenceError):
        renderer.render_evidence_liquidity_v2(changed)


def test_v2_rechecks_report_bound_after_liquidity_overflow_notes(monkeypatch):
    snapshot = synthetic_snapshot("long")
    packet = renderer.render_evidence_liquidity_v2(snapshot)
    monkeypatch.setattr(renderer, "MAX_REPORT_BYTES", len(packet["report.json"]) + 1000)
    original = renderer._summary

    def overflow(snapshot, **kwargs):
        canvas = original(snapshot, **kwargs)
        canvas.truncated.append("liquidity_" + "x" * 10000)
        return canvas

    monkeypatch.setattr(renderer, "_summary", overflow)
    with pytest.raises(
        renderer.EvidenceRenderError, match="report_byte_limit_exceeded"
    ):
        renderer.render_evidence_liquidity_v2(snapshot)


@pytest.mark.parametrize("profile", (True, 2, None, "", "caller_passed"))
def test_unknown_profile_rejects_before_clock_or_publication(tmp_path, profile):
    def forbidden():
        pytest.fail("profile validation must precede clock or source work")

    with pytest.raises(gates.EvidenceGateError, match="preparation_policy_invalid"):
        gates.publish_qualification_evidence(
            tmp_path,
            None,
            run=None,
            intent=None,
            quote=None,
            reference=None,
            policy=None,
            risk_inputs=None,
            consumed_event_keys=frozenset(),
            evaluated_at=None,
            purpose="synthetic_test",
            clock=forbidden,
            render_profile=profile,
        )
    assert not tuple(tmp_path.iterdir())


def test_native_v2_g12_hash_readback_and_no_requalification(tmp_path):
    source = engine_source("long")
    inputs = engine_inputs(source)
    run = evaluate_pre_evidence(source.market, **inputs)
    assert run.pre_evidence_complete
    before = run.model_dump_json(round_trip=True)

    def publish():
        return gates.publish_qualification_evidence_liquidity_v2(
            tmp_path,
            source.market,
            run=run,
            purpose="synthetic_test",
            clock=Clock(source.evaluated_at),
            **inputs,
        )

    result = publish()
    assert result.result.evidence_complete, result.error_detail
    assert_no_authority(result)
    directory = tmp_path / source.report_id
    assert result.receipt.status == "written"
    saved = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert len(saved) == 6
    for record in result.receipt.files:
        assert hashlib.sha256(saved[record.name]).hexdigest() == record.sha256
        assert len(saved[record.name]) == record.size_bytes
    report = json.loads(saved["report.json"])
    assert report["renderer"]["renderer_version"] == renderer.LIQUIDITY_RENDERER_VERSION
    assert len(report["snapshot"]["qualification"]["gates"]) == 11
    assert report["snapshot"]["evidence_gate"] == "not_evaluated"
    assert run.model_dump_json(round_trip=True) == before
    repeated = publish()
    assert not repeated.result.evidence_complete
    assert repeated.receipt.status == "already_present"
    assert_no_authority(repeated)
    assert saved == {p.name: p.read_bytes() for p in directory.iterdir()}
