"""Exact per-call graph, edge and capacity contracts; no execution owner."""

from datetime import datetime, tzinfo
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType

import pytest

from app.domain.source_primitives import ClockStamp
from app.okx_live import entry_input_dag_v2 as subject


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
