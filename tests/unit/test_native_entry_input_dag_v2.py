"""Exact per-call graph, edge and capacity contracts; no execution owner."""

from datetime import datetime, tzinfo
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType
from weakref import ref

import pytest

from app.domain.source_primitives import ClockStamp
from app.indicators.causal_trend import CausalTrendEstimate
from app.okx_live import entry_input_dag_v2 as subject
from app.trade_qualification.native_live_public_source_v1 import LivePublicRawFileV1


def digest(value):
    return subject.exact_input_dag_digest_v2(value, invocation=_INVOCATION)


_INVOCATION = object()


def test_repeated_calls_reobserve_actual_values_and_keep_all_field_types():
    value = ClockStamp(utc_ns=100, monotonic_ns=100)
    first = digest({"left": value, "right": value})
    assert digest({"left": value, "right": value}) == first
    object.__setattr__(value, "utc_ns", 101)
    assert digest({"left": value, "right": value}) != first
    assert (
        len({digest(v) for v in (None, 0, False, "0", Decimal(0), Fraction(0), 0.0)})
        == 7
    )


def test_completed_traversal_does_not_retain_models_through_a_recursive_closure():
    value = ClockStamp(utc_ns=100, monotonic_ns=100)
    original = ref(value)
    digest(value)
    del value
    # No gc.collect(), altered thresholds or cross-call memo is needed.
    assert original() is None


def test_binary_wire_overhead_is_bounded_as_well_as_expanded_payload(monkeypatch):
    monkeypatch.setattr(subject, "_MAX_BYTES", 64)
    with pytest.raises(ValueError):
        digest(0)


def test_rejected_traversal_releases_its_private_memo_without_gc():
    value = ClockStamp(utc_ns=100, monotonic_ns=100)
    original = ref(value)
    caught = False
    try:
        digest([value, object()])
    except ValueError:
        caught = True
    assert caught
    del value
    assert original() is None


def test_original_raw_field_map_does_not_invoke_an_inherited_descriptor():
    value = ClockStamp(utc_ns=100, monotonic_ns=100)
    expected = digest(value)
    base = subject.BaseModel
    original = vars(base)["model_fields"]
    calls = []

    class ForeignDescriptor:
        def __get__(self, instance, owner):
            calls.append("descriptor")
            raise AssertionError("foreign inherited descriptor")

    try:
        base.model_fields = ForeignDescriptor()
        assert digest(value) == expected
        assert calls == []
    finally:
        base.model_fields = original


def test_dataclass_dictionary_fields_never_invoke_a_shadowing_property():
    value = CausalTrendEstimate(1, *(Decimal(0) for _ in range(8)), "flat")
    expected = digest(value)
    calls = []
    assert "window" not in vars(CausalTrendEstimate)

    def foreign(instance):
        calls.append("window")
        raise AssertionError("foreign dataclass property")

    try:
        CausalTrendEstimate.window = property(foreign)
        assert digest(value) == expected
        assert calls == []
    finally:
        del CausalTrendEstimate.window


def test_dataclass_slots_require_the_actual_native_member_not_a_property():
    value = LivePublicRawFileV1("source.raw", b"{}", "a" * 64)
    expected = digest(value)
    original = vars(LivePublicRawFileV1)["name"]
    calls = []

    def foreign(instance):
        calls.append("name")
        raise AssertionError("foreign slot property")

    try:
        LivePublicRawFileV1.name = property(foreign)
        with pytest.raises(ValueError):
            digest(value)
        assert calls == []
    finally:
        LivePublicRawFileV1.name = original
    assert digest(value) == expected


def test_every_reference_multiplicity_path_and_distinct_object_is_encoded():
    first = bytes(bytearray(b"same"))
    second = bytes(bytearray(b"same"))
    assert first == second and first is not second
    assert digest([first, first]) != digest([first, second])
    assert digest([first]) != digest([first, first])
    assert digest({"a": [first], "b": first}) != digest({"a": first, "b": [first]})
    assert digest((first,)) != digest([first])


def test_shared_subtree_expanded_node_cost_is_charged_on_every_edge():
    subtree = [1] * 1000
    with pytest.raises(ValueError):
        digest([subtree] * 600)


def test_shared_subtree_expanded_raw_byte_cost_is_charged_on_every_edge():
    raw = b"x" * (1024 * 1024)
    with pytest.raises(ValueError):
        digest([raw] * 300)


def test_cached_subtree_cannot_hide_excessive_expanded_depth_or_cycles():
    tail = 1
    for _ in range(8):
        tail = [tail]
    deep = tail
    for _ in range(35):
        deep = [deep]
    with pytest.raises(ValueError):
        digest([tail, deep])
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError):
        digest(cycle)


def test_proxy_over_exact_dict_is_observed_but_foreign_mapping_is_never_called():
    original = {"zero": 0}
    proxy = MappingProxyType(original)
    before = digest(proxy)
    original["zero"] = 1
    assert digest(proxy) != before
    calls = []

    class ForeignMapping:
        def __getitem__(self, name):
            calls.append(name)
            raise AssertionError("foreign mapping callback")

        def __iter__(self):
            calls.append("iter")
            raise AssertionError("foreign mapping callback")

        def __len__(self):
            calls.append("len")
            raise AssertionError("foreign mapping callback")

    with pytest.raises(ValueError):
        digest(MappingProxyType(ForeignMapping()))
    assert calls == []


def test_unknown_instance_and_timezone_callbacks_are_not_invoked():
    calls = []

    class Foreign:
        @property
        def __class__(self):
            calls.append("class")
            raise AssertionError("foreign class callback")

        def __getattr__(self, name):
            calls.append(name)
            raise AssertionError("foreign attribute callback")

    class ForeignTimezone(tzinfo):
        def utcoffset(self, value):
            calls.append("utc")
            raise AssertionError("foreign timezone callback")

    for value in (Foreign(), datetime(2026, 1, 1, tzinfo=ForeignTimezone())):
        with pytest.raises(ValueError):
            digest(value)
    assert calls == []


def test_extra_model_fields_and_same_named_foreign_classes_stay_rejected():
    original = ClockStamp(utc_ns=1, monotonic_ns=1)
    fake = type("ClockStamp", (ClockStamp,), {"__module__": ClockStamp.__module__})
    fake.__qualname__ = ClockStamp.__qualname__
    with pytest.raises(ValueError):
        digest(fake.model_construct(**original.__dict__))
    original.__dict__["passed"] = True
    with pytest.raises(ValueError):
        digest(original)
