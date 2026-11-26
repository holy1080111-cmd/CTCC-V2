"""Static source predicates only; no runtime, credentials, DB or trading IO."""

import asyncio
import subprocess
import sys
from pathlib import Path
from types import FunctionType

import pytest

from app.trade_qualification import native_continuation_guard_v1 as guard
from tests.unit import native_guard_fixture_v1 as fixture
from tests.unit.test_native_continuation_guard_v1 import (
    ORIGINAL_ASYNC_ENTRY,
    ORIGINAL_ENTRY,
)


def _pin(function, *, compiled=None):
    closure = function.__closure__
    return (
        function,
        function.__code__,
        function.__globals__,
        function.__builtins__,
        function.__defaults__,
        function.__kwdefaults__,
        guard._keyword_defaults_pin_v2(function.__kwdefaults__),
        closure,
        tuple(cell.cell_contents for cell in closure or ()),
        compiled,
    )


def _function(value=None):
    return value


def _replace(pin, index, value):
    return pin[:index] + (value,) + pin[index + 1 :]


def _ordinary(function, lookups=()):
    return function.__globals__, function.__builtins__, lookups


def test_first_occurrence_order_is_retained_without_merging_distinct_functions():
    first = _pin(_function)
    clone = FunctionType(
        _function.__code__, _function.__globals__, argdefs=_function.__defaults__
    )
    other = _pin(clone)
    result = guard._unique_static_function_pins_v3(
        [first, other, tuple(item for item in first)]
    )
    assert len(result) == 2 and result[0] is first and result[1] is other
    assert result[0][0] is not result[1][0]


def test_equivalent_ordinary_source_obligations_are_checked_once():
    names = (("len", len), ("str", str))
    first = _pin(_function, compiled=_ordinary(_function, names))
    second = _pin(
        _function,
        compiled=_ordinary(
            _function, tuple(("".join(name), value) for name, value in names)
        ),
    )
    result = guard._unique_static_function_pins_v3([first, second])
    assert len(result) == 1 and result[0] is first
    guard._check_function(result[0])


@pytest.mark.parametrize("slot", [1, 2, 3, 4, 5, 7])
def test_inconsistent_identity_slot_never_selects_a_favorable_pin(slot):
    first = _pin(_function)
    changed = {
        1: first[1].replace(co_name="changed_source_code"),
        2: dict(first[2]),
        3: dict(first[3]),
        4: (object(),),
        5: {},
        7: (),
    }[slot]
    with pytest.raises(
        guard.NativeSourceClosureError, match="duplicate_pin_inconsistent"
    ):
        guard._unique_static_function_pins_v3([first, _replace(first, slot, changed)])


def test_keyword_snapshot_values_use_identity_without_foreign_equality():
    calls = []

    class ForeignEquality:
        def __eq__(self, _other):
            calls.append(True)
            return True

    initial, changed = ForeignEquality(), ForeignEquality()

    def function(*, value=initial):
        return value

    first = _pin(function)
    function.__kwdefaults__["value"] = changed
    second = _pin(function)
    with pytest.raises(guard.NativeSourceClosureError):
        guard._unique_static_function_pins_v3([first, second])
    assert calls == []


@pytest.mark.parametrize(
    "kind", ["foreign_key", "foreign_row", "duplicate_key", "changed_name"]
)
def test_malformed_or_different_keyword_snapshots_are_rejected_without_callbacks(kind):
    calls = []

    class ForeignString(str):
        def __eq__(self, _other):
            calls.append(True)
            return True

    value = object()

    def function(*, value=value):
        return value

    first = _pin(function)
    changed = {
        "foreign_key": ((ForeignString("value"), value),),
        "foreign_row": (["value", value],),
        "duplicate_key": (("value", value), ("value", value)),
        "changed_name": (("wrong", value),),
    }[kind]
    with pytest.raises(guard.NativeSourceClosureError):
        guard._unique_static_function_pins_v3([first, _replace(first, 6, changed)])
    assert calls == []


def test_changed_cell_snapshot_is_not_hidden_by_restored_current_cell():
    value = object()

    def function():
        return value

    first = _pin(function)
    changed = _replace(first, 8, (object(),))
    with pytest.raises(guard.NativeSourceClosureError):
        guard._unique_static_function_pins_v3([first, changed, first])


@pytest.mark.parametrize(
    "part", ["globals", "builtins", "name", "value", "order", "foreign_key", "none"]
)
def test_every_ordinary_compiled_obligation_must_match(part):
    items = (("len", len), ("str", str))
    original = _ordinary(_function, items)
    first = _pin(_function, compiled=original)

    class ForeignString(str):
        def __eq__(self, _other):
            raise AssertionError("foreign_lookup_equality_forbidden")

    changed = {
        "globals": (dict(original[0]), original[1], items),
        "builtins": (original[0], dict(original[1]), items),
        "name": (original[0], original[1], (("abs", len), ("str", str))),
        "value": (original[0], original[1], (("len", object()), ("str", str))),
        "order": (original[0], original[1], tuple(reversed(items))),
        "foreign_key": (
            original[0],
            original[1],
            ((ForeignString("len"), len), ("str", str)),
        ),
        "none": None,
    }[part]
    with pytest.raises(guard.NativeSourceClosureError):
        guard._unique_static_function_pins_v3([first, _replace(first, 9, changed)])


def test_factory_checks_are_all_retained_without_comparing_or_caching_payloads(
    monkeypatch,
):
    class Payload:
        def __eq__(self, _other):
            raise AssertionError("factory_payload_equality_forbidden")

    payloads = (Payload(), Payload())
    first = _pin(_function, compiled=(guard._sqlalchemy_factory.TAG, payloads[0]))
    second = _pin(_function, compiled=(guard._sqlalchemy_factory.TAG, payloads[1]))
    result = guard._unique_static_function_pins_v3([first, second, first])
    assert (
        len(result) == 3
        and result[0] is first
        and result[1] is second
        and result[2] is first
    )
    calls = []
    monkeypatch.setattr(
        guard._sqlalchemy_factory, "require_exact_create_engine_v1", calls.append
    )
    for pin in result:
        guard._check_function(pin)
    assert (
        len(calls) == 3
        and calls[0] is payloads[0]
        and calls[1] is payloads[1]
        and calls[2] is payloads[0]
    )


def test_factory_duplicate_common_mismatch_is_still_denied():
    first = _pin(_function, compiled=(guard._sqlalchemy_factory.TAG, object()))
    with pytest.raises(guard.NativeSourceClosureError):
        guard._unique_static_function_pins_v3([first, _replace(first, 4, (99,))])


def _install_alias_fixture(monkeypatch):
    monkeypatch.setattr(fixture, "entry", ORIGINAL_ENTRY)
    monkeypatch.setattr(fixture, "async_entry", ORIGINAL_ASYNC_ENTRY)
    monkeypatch.setattr(fixture, "_NATIVE_GUARD", None, raising=False)
    monkeypatch.setattr(fixture, "leaf_alias", fixture.leaf, raising=False)
    fixture.closed.clear()
    guard.install_native_guards_v1(fixture, ("entry", "async_entry"))
    return fixture


def test_alias_records_are_not_removed_with_duplicate_function_predicates(monkeypatch):
    module = _install_alias_fixture(monkeypatch)
    captured = dict(
        zip(
            module._NATIVE_GUARD.__code__.co_freevars,
            (cell.cell_contents for cell in module._NATIVE_GUARD.__closure__),
            strict=True,
        )
    )
    assert sum(pin[0] is fixture.leaf for pin in captured["functions"]) == 1
    assert {
        name
        for owner, name, _ in captured["records"]
        if owner is module and name in ("leaf", "leaf_alias")
    } == {"leaf", "leaf_alias"}
    called = []
    monkeypatch.setattr(module, "leaf_alias", lambda: called.append(True))
    with pytest.raises(guard.NativeSourceClosureError):
        module.entry()
    assert called == []


def test_same_code_mutation_still_rejects_after_static_dedup(monkeypatch):
    module = _install_alias_fixture(monkeypatch)
    monkeypatch.setattr(
        module.leaf, "__code__", module.leaf.__code__.replace(co_name="changed_leaf")
    )
    with pytest.raises(guard.NativeSourceClosureError):
        module.entry()


def test_changed_bytes_are_reread_after_real_yield_without_touching_source(tmp_path):
    # The guard compiles its exact defining source and insists that each pinned
    # module is under its source root. Import the original bytes in a writable
    # isolated tree so the sentinel can change even when /app is read-only.
    sandbox = tmp_path / "source"
    source = Path(guard.__file__).resolve().parents[2]
    for relative in (
        "app/__init__.py",
        "app/domain/__init__.py",
        "app/domain/source_primitives.py",
        "app/trade_qualification/__init__.py",
        "app/trade_qualification/native_continuation_guard_v1.py",
        "app/trade_qualification/native_sqlalchemy_factory_guard_v1.py",
        "tests/unit/native_guard_fixture_v1.py",
    ):
        destination = sandbox / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source / relative).read_bytes())
    # Force this fixture package to win over any installed package named tests.
    (sandbox / "tests" / "__init__.py").touch()
    program = r"""
import sys
from pathlib import Path

root = Path.cwd().resolve()
sys.path.insert(0, str(root))
from app.trade_qualification import native_continuation_guard_v1 as guard
from tests.unit import native_guard_fixture_v1 as fixture

assert guard._ROOT == root
assert Path(fixture.__file__).resolve() == root / "tests/unit/native_guard_fixture_v1.py"
sentinel = root / "source-sentinel.py"
sentinel.write_bytes(b"# original immutable-source sentinel\n")
fixture.__file__ = str(sentinel)
guard.install_native_guards_v1(fixture, ("entry", "async_entry"))
pending = fixture.async_entry()
assert pending.send(None) is None
sentinel.write_bytes(b"# changed source sentinel\n")
for _ in range(2):
    try:
        pending.send(None)
    except guard.NativeSourceClosureError as error:
        assert str(error) == "native_continuation_source_changed"
    else:
        raise AssertionError("modified source or retired coroutine was admitted")
    assert fixture.closed == ["closed"]
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program],
        cwd=sandbox,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_real_await_does_not_cache_execution_liveness(monkeypatch):
    from app.exchange.okx import native_live_write_v1 as write
    from tests.unit.test_native_live_preparation_scope_v2 import _scope

    async with _scope(monkeypatch) as scope:
        write._require_preparation_scope_v2(
            scope.gate, key=write.transport._TRANSPORT_KEY
        )
        await asyncio.sleep(0)
        scope.qualification["revoked"] = True
        with pytest.raises(ValueError):
            write._require_preparation_scope_v2(
                scope.gate, key=write.transport._TRANSPORT_KEY
            )
