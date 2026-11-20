"""Closed source58 function metadata and immutable ancestor verification."""

import pytest

from app.domain import native_live_function_specs_v58 as subject


def test_current_physical58_uses_only_reviewed_lifecycle_guard_override():
    actual = subject.live_function_statements_v58()
    original = {
        sql.split("public.", 1)[1].split("(", 1)[0]: sql
        for sql in subject.ancestor.UPGRADE_SQL
        if sql.startswith("CREATE FUNCTION public.")
    }
    assert set(actual) == set(original) and len(actual) == 5
    assert (
        actual["native_live_reservation_guard_v1"]
        == subject.lifecycle.RESERVATION_GUARD_SQL
    )
    assert all(
        actual[name] == sql
        for name, sql in original.items()
        if name != "native_live_reservation_guard_v1"
    )
    with pytest.raises(TypeError):
        actual["unknown"] = "SELECT true"


def test_exact_metadata_keeps_all_input_types_returns_and_execution_flags():
    rows = subject.live_function_metadata_v58(40001)
    assert rows["native_live_control_fence_v1"] == (
        False,
        "v",
        "u",
        0,
        "plpgsql",
        2,
        "25 25",
        True,
        40001,
        0,
        0,
        None,
        None,
    )
    assert rows["native_live_disarm_v1"][5:9] == (3, "25 25 25", False, 20)
    assert rows["native_live_reservation_guard_v1"][5:9] == (0, "", False, 2279)


@pytest.mark.parametrize("value", (None, True, 0, -1, "40001"))
def test_unknown_control_return_type_cannot_be_defaulted(value):
    with pytest.raises(ValueError):
        subject.live_function_metadata_v58(value)


def test_unreviewed_function_override_or_changed_ancestor_is_refused(monkeypatch):
    monkeypatch.setattr(
        subject.lifecycle,
        "live_lifecycle_function_overrides_v1",
        lambda: {"other": "SELECT true"},
    )
    with pytest.raises(ValueError):
        subject.live_function_statements_v58()
