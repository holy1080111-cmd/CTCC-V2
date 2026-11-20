from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.database.models.demo_automation import DemoAutomationState
from app.database.repositories import native_legacy_control_clear_0056_v1 as legacy
from app.database.repositories import native_legacy_control_genesis_0056_v1 as genesis
from app.domain.source_primitives import canonical, decode
from app.trade_qualification import demo_control as control

_BEFORE = datetime(2026, 11, 20, 1, 0, tzinfo=UTC)
_AFTER = _BEFORE + timedelta(seconds=2)


def _stopped():
    values = genesis._stopped_values(_BEFORE, "a" * 64)
    values.update(
        control_revision=7,
        lock_reasons=["emergency_stop_engaged", "restart_requires_explicit_clear_stop"],
        equity_basis="cashflow_adjusted",
        baseline_equity=Decimal("300.0000000000"),
        peak_equity=Decimal("320.0000000000"),
        risk_peak_equity=Decimal("315.0000000000"),
        daily_pnl=Decimal("-1.2500000000"),
        trades_today=3,
        consecutive_losses=1,
        symbol_cooldowns={
            "BTC-USDT-SWAP": (_BEFORE + timedelta(minutes=5)).isoformat()
        },
        realized_pnl_events=[{"net_pnl": "-1.25", "closed_at": _BEFORE.isoformat()}],
        last_trade_closed_at=_BEFORE - timedelta(seconds=10),
        last_started_at=_BEFORE - timedelta(seconds=20),
        last_completed_at=_BEFORE - timedelta(seconds=5),
    )
    return DemoAutomationState(**values)


def _pair(row):
    first = legacy.restart_snapshot(row, now=_AFTER)
    cleared = DemoAutomationState(
        **(
            legacy.restart_cas_values(row)
            | legacy.restart_cleared_values(first, at=_AFTER)
        )
    )
    last = legacy.exact_restart_cleared(cleared, before=first, clear_at=_AFTER)
    fields = (
        "armed",
        "emergency_stop",
        "locked",
        "lock_reasons",
        "active_instrument_id",
        "active_client_order_id",
        "active_start_equity",
        "active_started_at",
        "active_trades",
        "last_started_at",
        "last_completed_at",
        "last_error",
        "updated_at",
    )
    document = {
        "schema_version": "ctcc.portfolio_local_checkpoint.v2",
        "unresolved_intents": [],
        "currency_states": [
            {
                "account_revision": 7,
                "ledger_revision": 19,
                "claims_sha256": "c" * 64,
                "active": [],
            }
        ],
        "blocking_reasons": ["legacy_inflight_or_uncertain"],
        "legacy": {
            "positions": [],
            "orders": [],
            "algos": [],
            "sync_checkpoint": {"status": "reconciled", "session": "unchanged"},
            "automation": {name: decode(first)[name] for name in fields},
        },
    }
    final = deepcopy(document)
    final["blocking_reasons"] = []
    final["legacy"]["automation"] = {name: decode(last)[name] for name in fields}
    return first, last, canonical(document), canonical(final), cleared


def _receipt(first, last, before, after):
    return legacy.restart_receipt(
        before=before,
        after=after,
        singleton_before=first,
        singleton_after=last,
        clear_at=_AFTER,
        acquired_event="e" * 64,
        lineage="f" * 64,
    )


def test_restart_clear_preserves_nonzero_history_risk_and_all_currency_revisions():
    first, last, before, after, cleared = _pair(_stopped())
    receipt = decode(_receipt(first, last, before, after))
    assert receipt["legacy_revision_before"] == 7
    assert receipt["legacy_revision_after"] == 8
    assert receipt["restart_latch_preserved"] is True
    assert receipt["execution_authority"] is False
    assert cleared.armed is False and cleared.emergency_stop is cleared.locked is False
    assert cleared.restart_latch_required is True
    old, new = decode(first), decode(last)
    for field in set(old) - {
        "armed",
        "emergency_stop",
        "locked",
        "lock_reasons",
        "restart_latch_required",
        "control_revision",
        "updated_at",
    }:
        assert old[field] == new[field]
    assert decode(before)["currency_states"] == decode(after)["currency_states"]
    assert new["trades_today"] == 3 and new["consecutive_losses"] == 1
    assert new["daily_pnl"] == "-1.2500000000"
    assert new["realized_pnl_events"] and new["symbol_cooldowns"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("control_revision", 1),
        ("control_revision", True),
        ("control_revision", 2**63 - 1),
        ("armed", True),
        ("emergency_stop", False),
        ("locked", False),
        ("restart_latch_required", False),
        ("last_error", ""),
        ("last_error", "ambiguous_submit"),
        ("active_instrument_id", "BTC-USDT-SWAP"),
        ("active_client_order_id", "uncertain"),
        ("active_start_equity", Decimal(3)),
        ("active_started_at", _BEFORE),
        ("active_trades", {"unknown": {}}),
        ("active_trades", []),
        ("last_completed_at", None),
        ("last_started_at", _BEFORE + timedelta(seconds=1)),
        ("last_started_at", None),
        ("updated_at", _AFTER + timedelta(seconds=1)),
        ("daily_pnl", Decimal("NaN")),
        ("daily_pnl", True),
        ("trades_today", False),
        ("consecutive_losses", -1),
        ("symbol_cooldowns", []),
        ("realized_pnl_events", {}),
        ("lock_reasons", ["emergency_stop_engaged", "drawdown_breach"]),
        (
            "lock_reasons",
            [
                "emergency_stop_engaged",
                "restart_requires_explicit_clear_stop",
                "loss_limit",
            ],
        ),
    ],
)
def test_restart_clear_refuses_uncertain_or_nonrestart_state(field, value):
    row = _stopped()
    setattr(row, field, value)
    with pytest.raises(ValueError):
        legacy.restart_snapshot(row, now=_AFTER)


@pytest.mark.parametrize(
    "field",
    [
        "daily_pnl",
        "realized_pnl_events",
        "symbol_cooldowns",
        "control_revision",
        "restart_latch_required",
    ],
)
def test_restart_readback_detects_any_lost_history_or_changed_counter(field):
    first, _, _, _, cleared = _pair(_stopped())
    changes = {
        "daily_pnl": Decimal(0),
        "realized_pnl_events": [],
        "symbol_cooldowns": {},
        "control_revision": 9,
        "restart_latch_required": False,
    }
    setattr(cleared, field, changes[field])
    with pytest.raises(control.DemoControlError):
        legacy.exact_restart_cleared(cleared, before=first, clear_at=_AFTER)


@pytest.mark.parametrize(
    "change",
    ["intent", "position", "reservation", "blocker", "revision", "sync", "history"],
)
def test_restart_receipt_never_discards_other_local_evidence(change):
    first, last, before, after, _ = _pair(_stopped())
    a, b = decode(before), decode(after)
    if change == "intent":
        a["unresolved_intents"] = ["unknown"]
    elif change == "position":
        a["legacy"]["positions"] = ["position"]
    elif change == "reservation":
        a["currency_states"][0]["active"] = ["reserved"]
    elif change == "blocker":
        a["blocking_reasons"].append("incomplete")
    elif change == "revision":
        b["currency_states"][0]["ledger_revision"] += 1
    elif change == "sync":
        b["legacy"]["sync_checkpoint"]["session"] = "different"
    else:
        changed = decode(last)
        changed["realized_pnl_events"] = []
        last = canonical(changed)
    with pytest.raises(control.DemoControlError):
        _receipt(first, last, canonical(a), canonical(b))


def test_restart_genesis_marker_must_name_an_actual_stopped_control_member():
    row = _stopped()
    scope = control.ControlScope("demo", "730057")
    stopped = control.stopped_genesis(scope, _BEFORE - timedelta(minutes=1))
    event = control.canonical({"state": control.document(stopped)})
    marker = control.digest(event)
    row.lock_reasons += [
        "native_legacy_genesis_unreconciled",
        f"native_legacy_genesis_control_{marker}",
    ]
    raw = legacy.restart_snapshot(row, now=_AFTER)
    current = control.acquire(
        stopped,
        scope=scope,
        owner_sha256="a" * 64,
        pins=control.ControlPins("1" * 64, "2" * 64, "3" * 64),
        now=_AFTER,
    )
    lineage = SimpleNamespace(
        scope=scope,
        state=current,
        source_rows=(SimpleNamespace(event_sha256=marker, event_json=event),),
    )
    legacy.verify_restart_origin(lineage, raw)
    lineage.source_rows = ()
    with pytest.raises(control.DemoControlError):
        legacy.verify_restart_origin(lineage, raw)
