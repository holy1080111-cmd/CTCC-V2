"""Retained pure math must remain the original compiled source at dispatch."""

from importlib import import_module

import pytest

from app.okx_live import entry_proof_inputs_v1 as inputs
from app.okx_live import owned_qualification_v1 as subject
from app.trade_qualification.native_continuation_guard_v1 import (
    NativeSourceClosureError,
)


@pytest.mark.parametrize(
    "module,name",
    (
        ("app.analysis.service", "analyze_timeframe"),
        ("app.indicators.core", "ema"),
        ("app.trade_qualification.data", "_prepare_market_data"),
        ("app.trade_qualification.current_conditions", "_evaluate_base_g2_g4"),
        ("app.trade_qualification.portfolio", "_evaluate_portfolio"),
        ("app.strategies.structural_protection", "select_structural_protection"),
        ("app.trade_evidence.storage", "publish_evidence"),
    ),
)
def test_changed_helper_cannot_reuse_original_entry_proof_math(module, name):
    call = getattr(import_module(module), name)
    original = call.__code__

    def forged(*_args, **_kwargs):
        return True

    call.__code__ = forged.__code__
    try:
        with pytest.raises(NativeSourceClosureError):
            subject._NATIVE_GUARD()
    finally:
        call.__code__ = original
    subject._NATIVE_GUARD()


def test_changed_mutable_calculation_constant_changes_exact_input_digest():
    invocation = object()
    data = subject.entry_math_closure.original_entry_math_data_v1
    original = inputs.exact_input_digest_v1(data(), invocation=invocation)
    math_core = import_module("app.analysis.mathematical_core")
    before = math_core.EXECUTION_METHOD_WEIGHTS["derivative"]
    try:
        math_core.EXECUTION_METHOD_WEIGHTS["derivative"] = before / 2
        assert inputs.exact_input_digest_v1(data(), invocation=invocation) != original
    finally:
        math_core.EXECUTION_METHOD_WEIGHTS["derivative"] = before
    assert inputs.exact_input_digest_v1(data(), invocation=invocation) == original
