"""Actual foreign-module negative; no synthetic native Source positive."""

from types import ModuleType

import pytest

from app.exchange.okx import native_demo_settlement_v1 as settlement
from app.exchange.okx import native_demo_write_v2 as write
from app.trade_qualification import native_continuation_guard_v1 as guard


def test_missing_captured_class_never_invokes_foreign_module_getattr():
    called = []
    foreign = ModuleType("foreign_post_namespace")

    def foreign_attribute(name):
        called.append(name)
        raise RuntimeError("foreign callback must not execute")

    write._NATIVE_GUARD()
    foreign.__getattr__ = foreign_attribute
    saved = {}
    guard.save_native_guard_v1(saved, write._NATIVE_GUARD)
    guard.check_saved_native_guard_v1(saved)
    pin = saved["native_source_guard_pin"]
    saved["native_source_guard_pin"] = (object(), *pin[1:])
    record = {"settlement_api": (foreign, object, object, object(), (), saved)}
    with pytest.raises(
        guard.NativeSourceClosureError, match="^native_continuation_source_changed$"
    ):
        settlement._api(record)
    assert called == []
