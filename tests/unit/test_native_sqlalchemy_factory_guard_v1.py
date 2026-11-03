"""Inspect installed factory source/closures; never call it or access a DB."""

from types import FunctionType

import pytest
from sqlalchemy import create_engine

from app.trade_qualification import native_continuation_guard_v1 as guard
from app.trade_qualification import native_sqlalchemy_factory_guard_v1 as factory
from tests.unit import native_sqlalchemy_factory_guard_fixture_v1 as fixture


def target_cells():
    target = create_engine.__globals__["target"]
    return dict(zip(target.__code__.co_freevars, target.__closure__, strict=True))


def test_exact_genuine_generated_factory_is_data_pinned_without_invocation():
    pin = factory.capture_exact_create_engine_v1(create_engine)
    factory.require_exact_create_engine_v1(pin)
    files = {}
    actual = guard._function_pin(create_engine, files)
    guard._check_function(actual)
    assert actual[-1][0] == factory.TAG and len(files) == 3
    with pytest.raises(ValueError, match="Cell is empty"):
        _ = target_cells()["defaults"].cell_contents


def test_exact_code_clone_in_foreign_namespace_does_not_become_factory():
    copied = FunctionType(
        create_engine.__code__, dict(create_engine.__globals__), "create_engine"
    )
    copied.__wrapped__ = create_engine.__wrapped__
    with pytest.raises(factory.SqlAlchemyFactoryClosureError):
        factory.capture_exact_create_engine_v1(copied)


@pytest.mark.parametrize(
    "name", ("messages", "versions", "version_warnings", "check_kw", "defaults")
)
def test_mutable_target_cells_and_empty_default_are_rechecked(monkeypatch, name):
    pin = factory.capture_exact_create_engine_v1(create_engine)
    cell = target_cells()[name]
    if name == "defaults":
        cell.cell_contents = {}
        try:
            with pytest.raises(factory.SqlAlchemyFactoryClosureError):
                factory.require_exact_create_engine_v1(pin)
        finally:
            del cell.cell_contents
    elif name == "check_kw":
        values = cell.cell_contents
        values.add("foreign_parameter")
        try:
            with pytest.raises(factory.SqlAlchemyFactoryClosureError):
                factory.require_exact_create_engine_v1(pin)
        finally:
            values.remove("foreign_parameter")
    else:
        with monkeypatch.context() as change:
            change.setitem(
                cell.cell_contents,
                "strategy",
                "changed" if name != "version_warnings" else object(),
            )
            with pytest.raises(factory.SqlAlchemyFactoryClosureError):
                factory.require_exact_create_engine_v1(pin)


def test_generated_global_target_replacement_never_runs_foreign(monkeypatch):
    pin = factory.capture_exact_create_engine_v1(create_engine)
    called = []

    def foreign(*args, **kwargs):
        called.append(True)

    monkeypatch.setitem(create_engine.__globals__, "target", foreign)
    with pytest.raises(factory.SqlAlchemyFactoryClosureError):
        factory.require_exact_create_engine_v1(pin)
    assert called == []


def test_actual_module_name_remains_pinned_as_global_not_builtin(monkeypatch):
    pin = factory.capture_exact_create_engine_v1(create_engine)
    namespace = create_engine.__globals__["fn"].__globals__
    assert namespace["__name__"] == "sqlalchemy.engine.create"
    monkeypatch.setitem(namespace, "__name__", "changed.sqlalchemy.engine.create")
    with pytest.raises(factory.SqlAlchemyFactoryClosureError):
        factory.require_exact_create_engine_v1(pin)


def test_new_shadow_of_actual_builtin_remains_denied(monkeypatch):
    pin = factory.capture_exact_create_engine_v1(create_engine)
    namespace = create_engine.__globals__["fn"].__globals__
    assert "hasattr" not in namespace
    monkeypatch.setitem(namespace, "hasattr", object())
    with pytest.raises(factory.SqlAlchemyFactoryClosureError):
        factory.require_exact_create_engine_v1(pin)


def test_factory_cell_mutation_after_actual_await_stops_resume(monkeypatch):
    # Pytest rewrites assertion code in this module. Pin the separate ordinary
    # source module, whose actual create_engine alias reaches the special pin.
    module = fixture
    original = module._guarded_factory_fixture
    prior_guard = getattr(module, "_NATIVE_GUARD", None)
    installed = guard.install_native_guards_v1(module, ("_guarded_factory_fixture",))
    pending = module._guarded_factory_fixture()
    assert pending.send(None) is None
    try:
        with monkeypatch.context() as change:
            change.setitem(
                target_cells()["versions"].cell_contents,
                "strategy",
                "changed_after_await",
            )
            with pytest.raises(guard.NativeSourceClosureError):
                pending.send(None)
    finally:
        pending.close()
        module._guarded_factory_fixture = original
        if prior_guard is None:
            vars(module).pop("_NATIVE_GUARD", None)
        else:
            module._NATIVE_GUARD = prior_guard
    assert callable(installed)
