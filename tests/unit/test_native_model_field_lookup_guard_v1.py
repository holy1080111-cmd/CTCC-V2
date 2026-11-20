"""Same stored Pydantic inputs cannot acquire different field getter semantics."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import BaseModel

from app.domain import native_live_intent_v1 as live_intent
from app.domain import source_primitives
from app.okx_live import entry_input_dag_v2 as dag
from app.strategies import base as strategy_base
from app.trade_qualification import models
from app.trade_qualification import native_continuation_guard_v1 as guard
from tests.unit import native_guard_field_fixture_v1 as fixture
from tests.unit import native_guard_fixture_v1 as async_fixture
from tests.unit.test_native_live_source_ledger_v1 import _value as _intent

ORIGINAL_ENTRY = fixture.entry
ORIGINAL_ASYNC_ENTRY = async_fixture.async_entry


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setattr(fixture, "entry", ORIGINAL_ENTRY)
    monkeypatch.setattr(fixture, "_NATIVE_GUARD", None, raising=False)
    guard.install_native_guards_v1(
        fixture,
        ("entry",),
        dependencies=(models, source_primitives, strategy_base, live_intent),
    )
    assert fixture.entry() == 7
    return fixture


def _result():
    return models.EntryQualificationResult(
        report_id="field_lookup_guard",
        symbol="BTC-USDT-SWAP",
        strategy="trend_pullback",
        direction="long",
        evaluated_at=datetime(2026, 11, 21, tzinfo=UTC),
        raw_score=0,
        effective_score=0,
        stop_loss=Decimal(99),
    )


@pytest.mark.parametrize(
    "owner", [models.EntryQualificationResult, models.QualificationModel, BaseModel]
)
def test_new_property_cannot_change_genuine_field_with_same_stored_digest(
    installed, monkeypatch, owner
):
    result = _result()
    invocation = object()
    before = dag.exact_input_dag_digest_v2(result, invocation=invocation)
    called = []

    def foreign(instance):
        called.append(instance)
        return Decimal(1)

    assert "stop_loss" not in vars(owner)
    monkeypatch.setattr(owner, "stop_loss", property(foreign), raising=False)
    assert result.stop_loss == Decimal(1)
    assert result.__dict__["stop_loss"] == Decimal(99)
    assert dag.exact_input_dag_digest_v2(result, invocation=invocation) == before
    called.clear()
    with pytest.raises(guard.NativeSourceClosureError):
        installed.entry()
    assert not called


def test_new_clock_field_property_is_rejected_without_invoking_it(
    installed, monkeypatch
):
    stamp = source_primitives.ClockStamp(utc_ns=10, monotonic_ns=20)
    called = []

    def foreign(instance):
        called.append(instance)
        return 999

    assert "utc_ns" not in vars(source_primitives.ClockStamp)
    monkeypatch.setattr(
        source_primitives.ClockStamp, "utc_ns", property(foreign), raising=False
    )
    assert stamp.__dict__["utc_ns"] == 10
    with pytest.raises(guard.NativeSourceClosureError):
        installed.entry()
    assert not called


@pytest.mark.parametrize("name", ["__getattribute__", "__getattr__"])
def test_new_field_lookup_override_is_rejected_without_callback(
    installed, monkeypatch, name
):
    called = []

    def foreign(instance, *args):
        called.append(instance)
        raise AssertionError("foreign field callback executed")

    assert name not in vars(models.EntryQualificationResult)
    monkeypatch.setattr(models.EntryQualificationResult, name, foreign, raising=False)
    with pytest.raises(guard.NativeSourceClosureError):
        installed.entry()
    assert not called


def test_model_mro_is_pinned_without_freezing_unrelated_class_names(
    installed,
):
    original = fixture.FieldModel.__bases__
    type.__setattr__(fixture.FieldModel, "__bases__", (fixture.AlternateFieldParent,))
    try:
        with pytest.raises(guard.NativeSourceClosureError):
            installed.entry()
    finally:
        type.__setattr__(fixture.FieldModel, "__bases__", original)


def test_unrelated_pydantic_lazy_cache_remains_supported(installed, monkeypatch):
    handlers = models.EntryQualificationResult.__pydantic_setattr_handlers__
    monkeypatch.setitem(handlers, "_field_guard_lazy_probe", object())
    monkeypatch.setattr(
        models.EntryQualificationResult,
        "_field_guard_unrelated_lazy_state",
        object(),
        raising=False,
    )
    assert installed.entry() == 7


def test_untampered_model_and_clock_keep_original_values(installed):
    result = _result()
    stamp = source_primitives.ClockStamp(utc_ns=10, monotonic_ns=20)
    assert result.stop_loss == Decimal(99)
    assert stamp.utc_ns == 10 and stamp.monotonic_ns == 20
    assert installed.entry() == 7


def test_field_metadata_identity_change_does_not_invoke_foreign_equality(
    installed, monkeypatch
):
    called = []

    class ForeignEquality:
        def __eq__(self, other):
            called.append(other)
            return True

    monkeypatch.setitem(
        models.EntryQualificationResult.__pydantic_fields__,
        "stop_loss",
        ForeignEquality(),
    )
    with pytest.raises(guard.NativeSourceClosureError):
        installed.entry()
    assert not called


def test_field_descriptor_addition_after_actual_await_retires_attempt(monkeypatch):
    monkeypatch.setattr(async_fixture, "async_entry", ORIGINAL_ASYNC_ENTRY)
    monkeypatch.setattr(async_fixture, "_NATIVE_GUARD", None, raising=False)
    async_fixture.closed.clear()
    guard.install_native_guards_v1(
        async_fixture, ("async_entry",), dependencies=(models, source_primitives)
    )
    pending = async_fixture.async_entry()
    assert pending.send(None) is None
    called = []

    def foreign(instance):
        called.append(instance)
        return Decimal(1)

    monkeypatch.setattr(
        models.EntryQualificationResult, "stop_loss", property(foreign), raising=False
    )
    with pytest.raises(guard.NativeSourceClosureError):
        pending.send(None)
    assert not called and async_fixture.closed == ["closed"]
    with pytest.raises(guard.NativeSourceClosureError):
        pending.send(None)


@pytest.mark.parametrize("kind", ["clock", "intent"])
def test_genuine_live_guard_covers_actual_clock_and_intent_defining_modules(
    monkeypatch, kind
):
    from app.okx_live import owned_qualification_v1 as qualification

    if kind == "clock":
        value = source_primitives.ClockStamp(utc_ns=10, monotonic_ns=20)
        model, name, changed = source_primitives.ClockStamp, "utc_ns", 999
    else:
        value = _intent()
        model, name, changed = live_intent.LiveIntentDataV1, "stop_loss", Decimal(1)
    original = value.__dict__[name]
    assert getattr(value, name) == original
    qualification._NATIVE_GUARD()
    called = []

    def foreign(instance):
        called.append(instance)
        return changed

    monkeypatch.setattr(model, name, property(foreign), raising=False)
    assert value.__dict__[name] == original
    with pytest.raises(guard.NativeSourceClosureError):
        qualification._NATIVE_GUARD()
    assert not called


def test_required_dataclass_field_shadow_is_denied_without_getter(
    installed, monkeypatch
):
    condition = strategy_base.Condition("native", "test", 1, True, "synthetic")
    called = []

    def foreign(instance):
        called.append(instance)
        return False

    assert "passed" not in vars(strategy_base.Condition)
    monkeypatch.setattr(
        strategy_base.Condition, "passed", property(foreign), raising=False
    )
    assert condition.__dict__["passed"] is True
    with pytest.raises(guard.NativeSourceClosureError):
        installed.entry()
    assert not called


def test_untampered_existing_dataclass_retains_fields_defaults_and_methods(installed):
    condition = strategy_base.Condition("native", "test", 1, True, "synthetic")
    assert condition.passed is True
    assert condition.veto is condition.required is False
    assert condition.code == "native"
    assert installed.entry() == 7
