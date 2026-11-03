"""Reject copied private state before any source callback; no native acceptance."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.trade_qualification import native_ledger_runtime_v2 as runtime
from app.trade_qualification import reservations as ledger


def test_shallow_state_copy_cannot_reach_a_registered_source_checker(monkeypatch):
    # This isolated table marker is deliberately not a native owner. A copied
    # dictionary must fail the identity gate even before a source can be read.
    module, purpose, binding = SimpleNamespace(), object(), object()
    original = {
        "transport_module": module,
        "transport_key": purpose,
        "binding": binding,
        "repository": object(),
    }
    copied = dict(original)
    called = []
    monkeypatch.setattr(runtime, "_ATTEMPTS", {binding: original})
    monkeypatch.setattr(runtime, "_transport_module_v2", lambda key: module)

    def forbidden_source(*args):
        called.append("source")
        raise AssertionError("Copied DATA cannot reach a native source")

    monkeypatch.setattr(runtime, "_record", forbidden_source)
    with pytest.raises(
        ledger.QualificationLedgerError,
        match="^native_ledger_transport_owner_required$",
    ):
        runtime._transport_record_v2(copied, key=purpose)
    assert not called


def settings_data():
    return SimpleNamespace(
        trading_mode="okx_demo",
        okx_demo_enabled=True,
        okx_demo_allow_order_writes=True,
        okx_demo_auto_execution=True,
        okx_demo_require_protection=True,
        okx_ws_enabled=True,
        paper_auto_execution=False,
        live_trading=False,
        okx_live_enabled=False,
        okx_live_allow_order_writes=False,
        okx_live_auto_execution=False,
        okx_demo_allowed_symbol_list=["BTC-USDT-SWAP"],
        okx_demo_max_leverage=3,
        okx_demo_max_order_size_contracts=Decimal(2),
    )


@pytest.mark.parametrize("target", ["captured", "current"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("okx_demo_allow_order_writes", False),
        ("okx_demo_auto_execution", False),
        ("okx_demo_require_protection", False),
        ("okx_demo_enabled", 1),
        ("okx_live_allow_order_writes", True),
        ("paper_auto_execution", True),
        ("okx_demo_max_leverage", 1),
        ("okx_demo_max_order_size_contracts", Decimal("0.5")),
    ],
)
def test_atomic_switch_and_cap_primitive_rejects_either_changed_source(
    monkeypatch, target, field, value
):
    # These are DATA fixtures for the primitive only. No registry, native owner,
    # reservation, database or transport is constructed by this test.
    captured, current = settings_data(), settings_data()
    setattr(captured if target == "captured" else current, field, value)
    monkeypatch.setattr(runtime._settings_module, "get_settings", lambda: current)
    issued = SimpleNamespace(private_client=SimpleNamespace(settings=captured))
    request = SimpleNamespace(
        risk_inputs=SimpleNamespace(
            instrument=SimpleNamespace(instrument_id="BTC-USDT-SWAP"),
            requested_leverage=3,
            requested_contracts=Decimal(1),
        )
    )
    with pytest.raises(
        ledger.QualificationLedgerError,
        match="^native_ledger_demo_execution_disabled_or_cap_changed$",
    ):
        runtime._require_execution_settings(issued, request)
