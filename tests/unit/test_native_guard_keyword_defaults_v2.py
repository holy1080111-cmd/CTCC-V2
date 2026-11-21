"""Source metadata must reject changed defaults without foreign callbacks."""

import pytest

from app.trade_qualification import native_continuation_guard_v1 as subject
from tests.unit import native_guard_keyword_fixture_v2 as keyword_fixture
from tests.unit.native_guard_keyword_fixture_v2 import leaf as _leaf


@pytest.fixture
def source_leaf(monkeypatch):
    first, second = object(), object()
    monkeypatch.setattr(_leaf, "__kwdefaults__", {"first": first, "second": second})
    return _leaf


def test_unchanged_keyword_defaults_accept_order_change_without_value_equality(
    source_leaf,
):
    pin = subject._function_pin(source_leaf, {})
    values = source_leaf.__kwdefaults__
    first = values.pop("first")
    values["first"] = first
    subject._check_function(pin)


@pytest.mark.parametrize("forged_equal", [False, True])
def test_changed_keyword_value_never_executes_foreign_equality(
    source_leaf, forged_equal
):
    pin = subject._function_pin(source_leaf, {})
    calls = []

    class Foreign:
        def __eq__(self, other):
            calls.append("foreign-equality")
            return forged_equal

    source_leaf.__kwdefaults__["first"] = Foreign()
    with pytest.raises(subject.NativeSourceClosureError):
        subject._check_function(pin)
    assert calls == []


def test_equal_but_distinct_keyword_value_is_not_original_identity(source_leaf):
    source_leaf.__kwdefaults__["first"] = [1]
    pin = subject._function_pin(source_leaf, {})
    source_leaf.__kwdefaults__["first"] = [1]
    with pytest.raises(subject.NativeSourceClosureError):
        subject._check_function(pin)


@pytest.mark.parametrize("at_capture", [False, True])
def test_keyword_key_subclass_is_refused_without_sort_or_comparison(
    source_leaf, at_capture
):
    pin = None if at_capture else subject._function_pin(source_leaf, {})
    calls = []

    class ForeignKey(str):
        def __lt__(self, other):
            calls.append("foreign-sort")
            return False

        def __eq__(self, other):
            calls.append("foreign-equality")
            return False

        __hash__ = str.__hash__

    values = source_leaf.__kwdefaults__
    values.clear()
    values[ForeignKey("first")] = object()
    values[ForeignKey("second")] = object()
    calls.clear()
    with pytest.raises(subject.NativeSourceClosureError):
        if at_capture:
            subject._function_pin(source_leaf, {})
        else:
            subject._check_function(pin)
    assert calls == []


@pytest.mark.parametrize("at_capture", [False, True])
def test_keyword_mapping_subclass_is_refused_without_items_callback(
    source_leaf, monkeypatch, at_capture
):
    pin = None if at_capture else subject._function_pin(source_leaf, {})
    calls = []

    class ForeignMapping(dict):
        def items(self):
            calls.append("foreign-items")
            return super().items()

    monkeypatch.setattr(
        source_leaf, "__kwdefaults__", ForeignMapping(source_leaf.__kwdefaults__)
    )
    with pytest.raises(subject.NativeSourceClosureError):
        if at_capture:
            subject._function_pin(source_leaf, {})
        else:
            subject._check_function(pin)
    assert calls == []


def test_removed_or_extra_keyword_names_refuse(source_leaf):
    pin = subject._function_pin(source_leaf, {})
    original = source_leaf.__kwdefaults__.pop("first")
    source_leaf.__kwdefaults__["extra"] = original
    with pytest.raises(subject.NativeSourceClosureError):
        subject._check_function(pin)


def test_keyword_helper_code_is_verified_before_export_default_check(
    monkeypatch, capsys
):
    # Keep normally imported source and the same helper object; only its live
    # code changes after genuine guard assembly. No trade/IO authority exists.
    original = keyword_fixture.entry
    monkeypatch.setattr(keyword_fixture, "entry", original)
    monkeypatch.setattr(keyword_fixture, "_NATIVE_GUARD", None, raising=False)
    subject.install_native_guards_v1(keyword_fixture, ("entry",))
    foreign = lambda values, pinned: (print("foreign-keyword-helper"), True)[1]
    monkeypatch.setattr(
        subject._keyword_defaults_match_v2, "__code__", foreign.__code__
    )
    with pytest.raises(subject.NativeSourceClosureError):
        keyword_fixture.entry()
    assert capsys.readouterr().out == ""
