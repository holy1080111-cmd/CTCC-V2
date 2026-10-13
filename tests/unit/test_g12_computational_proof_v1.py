"""Synthetic original inputs; the new proof never establishes source custody."""

import hashlib
import json
from datetime import timedelta

import pytest

from app.trade_evidence import g12_computational_proof_v1 as proof
from app.trade_evidence.storage import FILE_NAMES
from app.trade_qualification.engine import evaluate_pre_evidence
from tests.unit.qualification_engine_fixtures import engine_inputs, engine_source


class Clock:
    def __init__(self, at):
        self.values = [at + timedelta(milliseconds=n) for n in (1, 2, 3)]

    def __call__(self):
        return self.values.pop(0)


@pytest.fixture
def original():
    source = engine_source("long")
    inputs = engine_inputs(source)
    run = evaluate_pre_evidence(source.market, **inputs)
    assert run.pre_evidence_complete
    return source, inputs, run


def _roots(tmp_path):
    evidence = tmp_path / "evidence"
    sidecar = tmp_path / "proof"
    evidence.mkdir()
    sidecar.mkdir()
    return evidence, sidecar


def _publish(tmp_path, original, *, render_profile="v1"):
    source, inputs, run = original
    evidence, sidecar = _roots(tmp_path)
    result = proof.publish_g12_computational_proof_v1(
        evidence,
        sidecar,
        source.market,
        run=run,
        original_inputs=inputs,
        purpose="synthetic_test",
        clock=Clock(source.evaluated_at),
        render_profile=render_profile,
    )
    return evidence, sidecar, result


def test_new_g12_proof_replays_all_gates_and_exact_six_bytes(tmp_path, original):
    evidence, sidecar, result = _publish(tmp_path, original)
    source, inputs, run = original
    assert result.admission == "DENY"
    assert result.execution_authority is False
    assert result.source_authenticity_verified is False
    assert result.evidence.pre_evidence == run
    assert result.evidence.result.gates[-1].passed is True
    assert {p.name for p in (evidence / source.report_id).iterdir()} == set(FILE_NAMES)
    proof_bytes = (sidecar / "proof.json").read_bytes()
    assert hashlib.sha256(proof_bytes).hexdigest() == result.proof_sha256
    assert "risk_inputs" not in proof_bytes.decode("ascii")
    assert "account_id" not in proof_bytes.decode("ascii")
    reread = proof.read_g12_computational_proof_v1(
        sidecar,
        evidence,
        expected_proof_sha256=result.proof_sha256,
        evidence=result.evidence,
        original_market=source.market,
        original_inputs=inputs,
    )
    assert reread == result
    with pytest.raises(
        proof.G12ComputationalProofError, match="g12_proof_publish_denied"
    ):
        proof.publish_g12_computational_proof_v1(
            evidence,
            sidecar,
            source.market,
            run=run,
            original_inputs=inputs,
            purpose="synthetic_test",
            clock=Clock(source.evaluated_at),
        )


def test_liquidity_v2_summary_is_replayed_byte_for_byte(tmp_path, original):
    evidence, sidecar, result = _publish(
        tmp_path, original, render_profile="liquidity_v2"
    )
    source, inputs, _ = original
    report = json.loads((evidence / source.report_id / "report.json").read_bytes())
    assert report["renderer"]["renderer_version"] == "ctcc-pillow-evidence-liquidity-v2"
    assert (
        json.loads((sidecar / "proof.json").read_bytes())["render_profile"]
        == "liquidity_v2"
    )
    assert (
        proof.read_g12_computational_proof_v1(
            sidecar,
            evidence,
            expected_proof_sha256=result.proof_sha256,
            evidence=result.evidence,
            original_market=source.market,
            original_inputs=inputs,
        )
        == result
    )


@pytest.mark.parametrize("version", (2, 3, 5, 6))
def test_supported_history_run_replays_under_exact_version(tmp_path, version):
    from app.trade_qualification import history_engine
    from tests.unit.history_qualification_fixtures import history_source
    from tests.unit.qualification_range_v5_fixtures import range_v5_source, v5_inputs
    from tests.unit.qualification_sweep_v6_fixtures import sweep_v6_source, v6_inputs
    from tests.unit.test_history_expansion_v2 import v2_inputs
    from tests.unit.test_history_reversal_v3 import v3_inputs

    if version == 2:
        source = history_source(
            "volatility_expansion", "long", bracket=True, expansion_entry=True
        )
        inputs = v2_inputs(source)
        evaluate = history_engine.evaluate_history_pre_evidence_v2
    elif version == 3:
        source = history_source(
            "structure_reversal", "long", bracket=True, reversal_bracket=True
        )
        inputs = v3_inputs(source)
        evaluate = history_engine.evaluate_history_pre_evidence_v3
    elif version == 5:
        source = range_v5_source()
        inputs = v5_inputs(source)
        evaluate = history_engine.evaluate_history_pre_evidence_v5
    else:
        source = sweep_v6_source("long", "boundary")
        inputs = v6_inputs(source)
        evaluate = history_engine.evaluate_history_pre_evidence_v6
    run = evaluate(source.market, **inputs)
    assert run.pre_evidence_complete, run.result.fail_codes
    evidence_root, proof_root = _roots(tmp_path)
    result = proof.publish_g12_computational_proof_v1(
        evidence_root,
        proof_root,
        source.market,
        run=run,
        original_inputs=inputs,
        purpose="synthetic_test",
        clock=Clock(source.evaluated_at),
    )
    assert result.admission == "DENY"
    assert result.evidence.pre_evidence == run
    assert len(result.evidence.result.gates) == 12
    assert result.evidence.result.gates[-1].passed


def test_v4_history_blocker_cannot_mint_proof(tmp_path):
    from app.trade_qualification.history_engine import evaluate_history_pre_evidence_v4
    from tests.unit.qualification_range_fixtures import range_source, v4_inputs

    source = range_source()
    inputs = v4_inputs(source)
    run = evaluate_history_pre_evidence_v4(source.market, **inputs)
    assert not run.pre_evidence_complete
    evidence_root, proof_root = _roots(tmp_path)
    with pytest.raises(proof.G12ComputationalProofError):
        proof.publish_g12_computational_proof_v1(
            evidence_root,
            proof_root,
            source.market,
            run=run,
            original_inputs=inputs,
            purpose="synthetic_test",
            clock=Clock(source.evaluated_at),
        )
    assert not tuple(evidence_root.iterdir())
    assert not tuple(proof_root.iterdir())


def test_changed_original_or_g12_run_cannot_replay(tmp_path, original):
    evidence, sidecar, result = _publish(tmp_path, original)
    source, inputs, _ = original
    changed = dict(inputs)
    changed["consumed_event_keys"] = frozenset(
        {result.evidence.pre_evidence.prefix.timing.event_key}
    )
    with pytest.raises(
        proof.G12ComputationalProofError, match="g12_proof_readback_denied"
    ):
        proof.read_g12_computational_proof_v1(
            sidecar,
            evidence,
            expected_proof_sha256=result.proof_sha256,
            evidence=result.evidence,
            original_market=source.market,
            original_inputs=changed,
        )
    forged = result.evidence.model_copy(
        update={"publication_started_at": result.evidence.preparation_started_at}
    )
    with pytest.raises(
        proof.G12ComputationalProofError, match="g12_proof_readback_denied"
    ):
        proof.read_g12_computational_proof_v1(
            sidecar,
            evidence,
            expected_proof_sha256=result.proof_sha256,
            evidence=forged,
            original_market=source.market,
            original_inputs=inputs,
        )


def test_tampered_six_file_or_sidecar_denied_without_repair(tmp_path, original):
    evidence, sidecar, result = _publish(tmp_path, original)
    source, inputs, _ = original
    report = evidence / source.report_id / "report.json"
    original_report = report.read_bytes()
    report.write_bytes(original_report + b" ")
    with pytest.raises(
        proof.G12ComputationalProofError, match="g12_proof_readback_denied"
    ):
        proof.read_g12_computational_proof_v1(
            sidecar,
            evidence,
            expected_proof_sha256=result.proof_sha256,
            evidence=result.evidence,
            original_market=source.market,
            original_inputs=inputs,
        )
    assert report.read_bytes() == original_report + b" "
    report.write_bytes(original_report)
    sidecar_file = sidecar / "proof.json"
    original_proof = sidecar_file.read_bytes()
    modified = json.loads(original_proof)
    modified["admission"] = "PASS"
    sidecar_file.write_bytes(proof._canonical(modified))
    with pytest.raises(
        proof.G12ComputationalProofError, match="g12_proof_readback_denied"
    ):
        proof.read_g12_computational_proof_v1(
            sidecar,
            evidence,
            expected_proof_sha256=hashlib.sha256(sidecar_file.read_bytes()).hexdigest(),
            evidence=result.evidence,
            original_market=source.market,
            original_inputs=inputs,
        )


def test_old_g12_or_prefilled_proof_root_cannot_mint_new_proof(tmp_path, original):
    source, inputs, run = original
    evidence, sidecar = _roots(tmp_path)
    (sidecar / "proof.json").write_bytes(b"old")
    with pytest.raises(
        proof.G12ComputationalProofError, match="g12_proof_publish_denied"
    ):
        proof.publish_g12_computational_proof_v1(
            evidence,
            sidecar,
            source.market,
            run=run,
            original_inputs=inputs,
            purpose="synthetic_test",
            clock=Clock(source.evaluated_at),
        )
    assert not list(evidence.iterdir())

    first = tmp_path / "first"
    first.mkdir()
    first_evidence, _, old = _publish(first, original)
    assert first_evidence.exists()
    second = tmp_path / "second"
    second.mkdir()
    new_evidence, new_proof = _roots(second)
    with pytest.raises(
        proof.G12ComputationalProofError, match="g12_proof_publish_denied"
    ):
        proof.publish_g12_computational_proof_v1(
            new_evidence,
            new_proof,
            source.market,
            run=old.evidence,
            original_inputs=inputs,
            purpose="synthetic_test",
            clock=Clock(source.evaluated_at),
        )
    assert not list(new_evidence.iterdir())
    assert not list(new_proof.iterdir())


def test_standalone_readback_cannot_attest_old_packet_publication_time(
    tmp_path, original
):
    source, inputs, run = original
    evidence_root, sidecar = _roots(tmp_path)
    old = proof.gates.publish_qualification_evidence(
        evidence_root,
        source.market,
        run=run,
        purpose="synthetic_test",
        clock=Clock(source.evaluated_at),
        **inputs,
    )
    assert old.result.gates[-1].passed
    fabricated = proof._canonical(proof._proof_document(old, "v1"))
    (sidecar / "proof.json").write_bytes(fabricated)

    replayed = proof.read_g12_computational_proof_v1(
        sidecar,
        evidence_root,
        expected_proof_sha256=hashlib.sha256(fabricated).hexdigest(),
        evidence=old,
        original_market=source.market,
        original_inputs=inputs,
    )
    assert replayed.admission == "DENY"
    assert replayed.publication_provenance_verified is False
    assert replayed.execution_authority is False


def test_late_proof_failure_preserves_new_g12_packet(tmp_path, original, monkeypatch):
    source, inputs, run = original
    evidence, sidecar = _roots(tmp_path)

    def fail_after_g12(_document):
        raise OSError("synthetic_late_proof_io")

    monkeypatch.setattr(proof, "_canonical", fail_after_g12)
    with pytest.raises(
        proof.G12ComputationalProofError, match="g12_proof_publish_denied"
    ):
        proof.publish_g12_computational_proof_v1(
            evidence,
            sidecar,
            source.market,
            run=run,
            original_inputs=inputs,
            purpose="synthetic_test",
            clock=Clock(source.evaluated_at),
        )
    assert {p.name for p in (evidence / source.report_id).iterdir()} == set(FILE_NAMES)
    assert not list(sidecar.iterdir())
