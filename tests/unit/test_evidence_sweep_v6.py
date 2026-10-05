"""Actual synthetic G12 filesystem publication; never source/order authority."""

import hashlib
import json

import pytest

from app.trade_evidence.gates import (
    HistoryEvidenceGateRunV6,
    publish_qualification_evidence,
    verify_pre_evidence_versioned,
)
from app.trade_evidence.service import validate_snapshot
from app.trade_evidence.storage import FILE_NAMES
from app.trade_qualification.history_engine import evaluate_history_pre_evidence_v6
from app.trade_qualification.recheck_models import freeze_recheck_origin
from tests.unit import qualification_sweep_v6_stage_c_fixtures as fixtures
from tests.unit.qualification_sweep_v6_fixtures import sweep_v6_source, v6_inputs
from tests.unit.test_qualification_evidence_gate import Clock

stage_c_case = fixtures.stage_c_case


def test_real_six_artifact_hash_readback_preserves_original_sweep(stage_c_case):
    case = stage_c_case
    evidence, pre = case.evidence, case.run
    assert type(evidence) is HistoryEvidenceGateRunV6
    assert evidence.result.evidence_complete and len(evidence.result.gates) == 12
    assert evidence.result.gates[:11] == pre.result.gates
    assert (
        evidence.result.candidate_entry,
        evidence.result.stop_loss,
        evidence.result.take_profit,
    ) == (pre.result.candidate_entry, pre.result.stop_loss, pre.result.take_profit)
    assert evidence.result.entry_zone == pre.result.entry_zone
    assert evidence.result.trigger == pre.result.trigger
    assert {path.name for path in (case.root / case.source.report_id).iterdir()} == set(
        FILE_NAMES
    )
    assert tuple(row.name for row in evidence.receipt.files) == FILE_NAMES
    for row in evidence.receipt.files:
        raw = (case.root / case.source.report_id / row.name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row.sha256
        assert len(raw) == row.size_bytes
    report = json.loads(
        (case.root / case.source.report_id / "report.json").read_bytes()
    )
    assert (
        report["snapshot"]["qualification"]["contract_version"]
        == "ctcc-history-qualification-result-v6"
    )
    assert validate_snapshot(evidence.snapshot) == evidence.snapshot
    assert case.origin.original_event_key == pre.prefix.timing.event_key
    assert (
        not evidence.execution_authority and not evidence.source_authenticity_verified
    )


@pytest.mark.parametrize("scenario", ("original_outside", "inside"))
@pytest.mark.parametrize("direction", ("long", "short"))
def test_earlier_gate_failure_never_creates_publication(tmp_path, scenario, direction):
    source = sweep_v6_source(direction, scenario)
    inputs = v6_inputs(source)
    run = evaluate_history_pre_evidence_v6(source.market, **inputs)
    assert not run.pre_evidence_complete
    root = tmp_path / "unopened-earlier-denial"
    evidence = publish_qualification_evidence(
        root,
        source.market,
        run=run,
        **inputs,
        purpose="synthetic_test",
        clock=Clock(source.evaluated_at),
    )
    assert evidence.result == run.result
    assert evidence.receipt is None and evidence.snapshot is None
    assert not root.exists() and not evidence.execution_authority


def test_existing_publication_cannot_requalify_same_sweep(stage_c_case):
    case = stage_c_case
    before = {
        name: (case.root / case.source.report_id / name).read_bytes()
        for name in FILE_NAMES
    }
    denied = publish_qualification_evidence(
        case.root,
        case.source.market,
        run=case.run,
        **case.inputs,
        purpose="synthetic_test",
        clock=Clock(case.source.evaluated_at),
    )
    assert not denied.result.evidence_complete
    assert denied.result.gates[-1].code != "passed"
    assert denied.receipt is None or denied.receipt.status != "written"
    assert before == {
        name: (case.root / case.source.report_id / name).read_bytes()
        for name in FILE_NAMES
    }
    assert not denied.execution_authority


@pytest.mark.parametrize(
    "field", ("execution_authority", "source_authenticity_verified")
)
def test_caller_authority_or_unsupported_version_cannot_publish(stage_c_case, field):
    case = stage_c_case
    forged = case.run.model_copy(update={field: True})
    with pytest.raises(ValueError):
        verify_pre_evidence_versioned(forged, case.source.market, **case.inputs)
    unsupported = case.run.model_copy(
        update={"contract_version": "ctcc-history-pre-evidence-v999"}
    )
    with pytest.raises(ValueError):
        verify_pre_evidence_versioned(unsupported, case.source.market, **case.inputs)


def test_immutable_origin_cannot_rewrite_candidate_or_policy(stage_c_case):
    case = stage_c_case
    raw = case.evidence.model_dump(mode="python", round_trip=True)
    raw["result"]["candidate_entry"] += case.source.tick_size
    with pytest.raises(ValueError):
        HistoryEvidenceGateRunV6.model_validate(raw, strict=True)
    assert freeze_recheck_origin(case.evidence) == case.origin


def test_rehashed_structural_audit_cannot_change_original_extreme(stage_c_case):
    case = stage_c_case
    raw = case.evidence.snapshot.model_dump(mode="python", round_trip=True)
    audit = json.loads(raw["protection_audit_json"])
    audit["original_extreme_anchor_id"] = "caller-chosen-other-stop"
    raw["protection_audit_json"] = json.dumps(
        audit, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    with pytest.raises(ValueError):
        changed = type(case.evidence.snapshot).model_validate(raw, strict=True)
        validate_snapshot(changed)
