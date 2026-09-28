"""Real native publisher with synthetic source; no exchange/source acceptance."""

from app.trade_evidence.gates import publish_qualification_evidence
from app.trade_qualification.history_engine import evaluate_history_pre_evidence_v5
from tests.unit.qualification_range_v5_fixtures import range_v5_source, v5_inputs
from tests.unit.test_qualification_evidence_gate import Clock


def test_v5_native_render_publish_and_readback(tmp_path):
    source = range_v5_source()
    inputs = v5_inputs(source)
    run = evaluate_history_pre_evidence_v5(source.market, **inputs)
    assert run.pre_evidence_complete
    evidence = publish_qualification_evidence(
        tmp_path,
        source.market,
        run=run,
        **inputs,
        purpose="synthetic_test",
        clock=Clock(source.evaluated_at),
    )
    assert evidence.result.gates[-1].passed, evidence.error_detail
    assert len(evidence.receipt.files) == 6
    assert not evidence.execution_authority
