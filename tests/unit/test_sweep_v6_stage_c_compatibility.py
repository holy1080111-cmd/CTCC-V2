"""Explicit new schema dispatch; old semantics and absence of authority remain."""

import pytest

from app.trade_evidence.gates import verify_pre_evidence_versioned
from app.trade_qualification.history_engine import evaluate_history_pre_evidence_v6
from app.trade_qualification.one_shot import _original
from tests.unit.qualification_sweep_v6_fixtures import sweep_v6_source, v6_inputs


@pytest.mark.parametrize("direction", ("long", "short"))
def test_one_shot_detaches_exact_v6_failed_inputs_without_promoting_them(direction):
    source = sweep_v6_source(direction, "inside")
    inputs = v6_inputs(source)
    run = evaluate_history_pre_evidence_v6(source.market, **inputs)
    assert not run.pre_evidence_complete
    original_market = source.market.model_dump(mode="json", round_trip=True)
    market, checked, detached = _original(source.market, run, inputs)
    assert checked == run and checked is not run
    assert (
        detached["intent"] == inputs["intent"]
        and detached["intent"] is not inputs["intent"]
    )
    assert type(market) is type(source.market) and market is not source.market
    # The established source boundary ignores caller-supplied quality.
    assert market.quality == {} and market.quality is not source.market.quality
    for field in type(source.market).model_fields:
        if field != "quality":
            assert getattr(market, field) == getattr(source.market, field), field
    assert source.market.model_dump(mode="json", round_trip=True) == original_market
    assert not checked.execution_authority


@pytest.mark.parametrize("direction", ("long", "short"))
def test_supported_v6_cannot_implicitly_upgrade_missing_or_mixed_policy_marker(
    direction,
):
    source = sweep_v6_source(direction, "inside")
    inputs = v6_inputs(source)
    run = evaluate_history_pre_evidence_v6(source.market, **inputs)
    for version in ("ctcc-history-pre-evidence-v5", "ctcc-history-pre-evidence-v999"):
        changed = dict(inputs)
        changed["policy"] = inputs["policy"].model_copy(
            update={"contract_version": version}
        )
        with pytest.raises(ValueError):
            verify_pre_evidence_versioned(run, source.market, **changed)
    hidden = run.model_copy()
    hidden.__dict__["caller_passed"] = True
    with pytest.raises(ValueError):
        _original(source.market, hidden, inputs)
