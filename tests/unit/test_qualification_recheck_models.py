"""Original pin replay with real calculations and synthetic storage contract.

No test claims actual publication, source authentication or runtime admission.
"""

from datetime import timedelta
from decimal import Context, Decimal, Inexact, localcontext
from pathlib import Path

import pytest
from pydantic import create_model

from app.trade_evidence import gates
from app.trade_qualification.recheck_models import (
    RecheckOrigin,
    copy_recheck_origin,
    freeze_recheck_origin,
    replay_recheck_origin,
)
from tests.unit.qualification_recheck_fixtures import recheck_source
from tests.unit.test_qualification_evidence_gate import Clock, synthetic_publisher

FLAGS = (
    "execution_authority",
    "source_authenticity_verified",
    "account_evidence_authenticated",
    "publication_observed_here",
    "atomic_risk_reserved",
    "execution_recheck_performed",
)


@pytest.fixture(scope="module", params=("long", "short"))
def source(request):
    return recheck_source(request.param)


@pytest.fixture(scope="module")
def origin(source):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(gates, "publish_evidence", synthetic_publisher())
        evidence = gates.publish_qualification_evidence(
            Path.cwd(),
            source.original_source.market,
            run=source.original_run,
            **source.original_inputs,
            purpose="synthetic_test",
            clock=Clock(source.original_source.evaluated_at),
        )
    assert len(evidence.result.gates) == 12
    assert all(gate.passed for gate in evidence.result.gates)
    return freeze_recheck_origin(evidence)


def test_original_pin_replay_preserves_every_fact_and_has_no_authority(source, origin):
    checked = replay_recheck_origin(
        origin, source.original_source.market, **source.original_inputs
    )
    assert checked == origin
    assert checked.evidence.pre_evidence == source.original_run
    assert checked.candidate.gates == origin.evidence.result.gates
    assert len(checked.candidate.gates) == 12
    assert checked.candidate.qualified is False
    assert checked.original_event_key == source.original_event_key
    assert all(getattr(checked, name) is False for name in FLAGS)
    assert checked.publication_completed_at < checked.deadline
    assert checked.evaluation_sha256 == copy_recheck_origin(checked).evaluation_sha256


def test_json_roundtrip_is_record_only(origin):
    decoded = RecheckOrigin.model_validate_json(
        origin.model_dump_json(round_trip=True), strict=True
    )
    assert decoded == origin
    assert decoded.evaluation_sha256 == origin.evaluation_sha256
    assert decoded.record_kind == "recorded_origin_not_runtime_permission"


@pytest.mark.parametrize("field", FLAGS)
@pytest.mark.parametrize("value", (True, 0, 1, "false", None))
def test_record_cannot_gain_authority(origin, field, value):
    with pytest.raises(ValueError):
        copy_recheck_origin(origin.model_copy(update={field: value}))


@pytest.mark.parametrize(
    "field",
    (
        "evidence_sha256",
        "pre_evidence_sha256",
        "original_source_sha256",
        "original_policy_sha256",
        "original_event_key",
        "publication_completed_at",
        "deadline",
        "record_kind",
    ),
)
def test_cannot_change_original_pin(origin, field):
    value = getattr(origin, field)
    changed = (
        value + timedelta(microseconds=1)
        if field in ("publication_completed_at", "deadline")
        else "0" * 64
    )
    with pytest.raises(ValueError):
        copy_recheck_origin(origin.model_copy(update={field: changed}))


@pytest.mark.parametrize(
    "target", ("origin", "evidence", "pre_evidence", "snapshot", "receipt")
)
@pytest.mark.parametrize("defect", ("hidden", "subclass"))
def test_nested_dirty_models_rejected_before_serializer(origin, target, defect):
    record = (
        origin
        if target == "origin"
        else origin.evidence
        if target == "evidence"
        else getattr(origin.evidence, target)
    )
    if defect == "hidden":
        record = record.model_copy(update={"hidden": True})
    else:
        extended = create_model(
            "ExtendedOriginRecord", __base__=type(record), hidden=(bool, True)
        )
        record = extended.model_construct(**record.__dict__, hidden=True)
    altered = (
        record
        if target == "origin"
        else origin.model_copy(
            update={
                "evidence": record
                if target == "evidence"
                else origin.evidence.model_copy(update={target: record})
            }
        )
    )
    with pytest.raises(ValueError):
        copy_recheck_origin(altered)


@pytest.mark.parametrize(
    "change",
    ("source", "policy", "size", "leverage", "expiry", "clock", "ledger", "reference"),
)
def test_replay_rejects_different_original_inputs(source, origin, change):
    market = source.original_source.market.model_copy(deep=True)
    inputs = dict(source.original_inputs)
    if change == "source":
        market.candles["5m"][0].volume_quote += Decimal(1)
    elif change == "policy":
        inputs["policy"] = inputs["policy"].model_copy(
            update={"policy_id": "different"}
        )
    elif change in ("size", "leverage"):
        name = "requested_contracts" if change == "size" else "requested_leverage"
        inputs["risk_inputs"] = inputs["risk_inputs"].model_copy(
            update={name: getattr(inputs["risk_inputs"], name) + 1}
        )
    elif change == "expiry":
        inputs["intent"] = inputs["intent"].model_copy(
            update={"expires_at": inputs["intent"].expires_at + timedelta(seconds=1)}
        )
    elif change == "clock":
        inputs["evaluated_at"] += timedelta(microseconds=1)
    elif change == "ledger":
        inputs["consumed_event_keys"] = frozenset({"0" * 64})
    else:
        inputs["reference"] = inputs["reference"].model_copy(
            update={"bid": inputs["reference"].bid - Decimal("0.001")}
        )
    with pytest.raises(ValueError):
        replay_recheck_origin(origin, market, **inputs)


@pytest.mark.parametrize(
    "change", ("missing_receipt", "already_present", "missing_snapshot", "only_eleven")
)
def test_only_complete_written_record_can_be_pinned(origin, change):
    evidence = origin.evidence
    updates = (
        {"receipt": None}
        if change == "missing_receipt"
        else {"snapshot": None}
        if change == "missing_snapshot"
        else {
            "receipt": evidence.receipt.model_copy(update={"status": "already_present"})
        }
        if change == "already_present"
        else {"result": evidence.pre_evidence.result}
    )
    with pytest.raises(ValueError):
        freeze_recheck_origin(evidence.model_copy(update=updates))


def test_hostile_decimal_context_cannot_change_replay(source, origin):
    context = Context(prec=2)
    context.traps[Inexact] = True
    with localcontext(context):
        checked = replay_recheck_origin(
            origin, source.original_source.market, **source.original_inputs
        )
        assert checked.evaluation_sha256 == origin.evaluation_sha256
