"""Explicit terminal.filled alias shares the genuine filled-schema owner."""

from types import ModuleType

import pytest

from app.trade_qualification import native_continuation_guard_v1 as guard
from app.trade_qualification import native_controlled_close_v1 as close
from app.trade_qualification import native_ledger_runtime_v2 as ledger
from app.trade_qualification import native_postsubmit_reconciliation_v1 as post


@pytest.mark.parametrize("module", (post, close, ledger))
def test_rebound_terminal_filled_alias_denies_before_catalog_use(module, monkeypatch):
    module._NATIVE_GUARD()
    profile = post._catalog55.policy
    assert profile.native_terminal.filled is profile.native_closure
    original = profile.native_terminal.filled
    with monkeypatch.context() as scoped:
        scoped.setattr(
            profile.native_terminal, "filled", ModuleType("foreign_filled_schema")
        )
        with pytest.raises(guard.NativeSourceClosureError):
            module._NATIVE_GUARD()
    assert profile.native_terminal.filled is original


@pytest.mark.parametrize("module", (post, close, ledger))
def test_in_place_same_filled_module_constant_change_denies(module, monkeypatch):
    module._NATIVE_GUARD()
    filled = post._catalog55.policy.native_terminal.filled
    with monkeypatch.context() as scoped:
        scoped.setattr(filled, "FILLED_BODY_SHA256", "0" * 64)
        with pytest.raises(guard.NativeSourceClosureError):
            module._NATIVE_GUARD()
