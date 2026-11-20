"""Exact input graph and original-frame negatives; no source/Arm/order claim."""

from dataclasses import replace
from decimal import Decimal
from time import perf_counter

import pytest

from app.okx_live import entry_owned_seal_v1 as seal
from app.okx_live import entry_proof_inputs_v1 as subject
from app.okx_live import owned_qualification_v1 as q
from tests.unit.test_live_qualification_math_finite_v1 import (
    _declared_finite_options,
)
from tests.unit.test_live_qualification_math_v1 import (
    initial,
)
from tests.unit.test_live_qualification_math_v1 import (
    live_source as live_source,  # noqa: PLC0414 -- declared synthetic transport fixture
)
from tests.unit.test_live_qualification_math_v1 import (
    market as market,  # noqa: PLC0414 -- shared deterministic four-frame raw tape
)
from tests.unit.test_native_live_source_ledger_v1 import _value


def test_actual_intent_inputs_are_resampled_and_zero_never_becomes_missing():
    invocation = object()
    value = _value()
    first = subject.exact_input_digest_v1(value, invocation=invocation)
    assert first == subject.exact_input_digest_v1(value, invocation=invocation)
    object.__setattr__(value, "leverage", 2)
    assert subject.exact_input_digest_v1(value, invocation=invocation) != first
    assert subject.exact_input_digest_v1(
        0, invocation=invocation
    ) != subject.exact_input_digest_v1(None, invocation=invocation)
    assert subject.exact_input_digest_v1(
        Decimal(0), invocation=invocation
    ) != subject.exact_input_digest_v1(Decimal(1), invocation=invocation)


def test_extra_model_keys_unsupported_objects_and_cycles_fail_closed():
    invocation = object()
    cyclic = []
    cyclic.append(cyclic)
    for value in (object(), cyclic, float("nan")):
        with pytest.raises(ValueError):
            subject.exact_input_digest_v1(value, invocation=invocation)
    value = _value()
    value.__dict__["passed"] = True
    with pytest.raises(ValueError):
        subject.exact_input_digest_v1(value, invocation=invocation)


def test_foreign_model_with_same_app_name_fields_and_receipt_is_not_original_class():
    value = _value()
    actual = type(value)
    counterfeit = type(actual.__name__, (actual,), {"__module__": actual.__module__})
    counterfeit.__qualname__ = actual.__qualname__
    copied = counterfeit.model_construct(**value.__dict__)
    assert copied.model_dump() == value.model_dump()
    with pytest.raises(ValueError):
        subject.exact_input_digest_v1(copied, invocation=object())


@pytest.mark.asyncio
async def test_complete_real_raw_math_graph_is_hashable_without_receipt_only_promotion(
    live_source, market, record_property
):
    from app.okx_live import qualification_math_v1 as math

    _, source, options, _ = await initial(live_source, market)
    args = _declared_finite_options(source, options)
    result = math.evaluate_live_qualification_math_v1(source, **args)
    assert result.pre_evidence_complete
    started = perf_counter()
    first = subject.exact_input_digest_v1(result, invocation=source.invocation)
    record_property("exact_input_digest_seconds", perf_counter() - started)
    assert first == subject.exact_input_digest_v1(result, invocation=source.invocation)
    changed = replace(result, risk_operands_json=result.risk_operands_json + b" ")
    assert changed.receipt_json == result.receipt_json
    assert subject.exact_input_digest_v1(changed, invocation=source.invocation) != first
    object.__setattr__(result.result, "stop_loss", result.result.stop_loss - Decimal(1))
    assert subject.exact_input_digest_v1(result, invocation=source.invocation) != first


@pytest.mark.asyncio
async def test_constructor_or_saved_receipt_cannot_create_pending_or_native_proof():
    with pytest.raises(ValueError):
        seal._OriginalEntryProofV1()
    session = object.__new__(q.OwnedLiveOperatorSessionV1)
    owner = object.__new__(q.OwnedLiveQualificationV1)
    for key in (object(), q._ENTRY_PROOF_KEY_V1):
        with pytest.raises(ValueError):
            seal.prepare_pending_entry_v1(session, key=key)
        with pytest.raises(ValueError):
            seal.complete_native_entry_v1(session, owner, key=key)
    assert session not in seal._PENDING and owner not in seal._SEALED


def test_original_coordinator_frame_cannot_be_replaced_by_a_caller_callback():
    with pytest.raises(ValueError):
        seal._source_frame(object(), value=_value())
