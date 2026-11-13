from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.database.models.demo_automation import DemoAutomationState
from app.database.repositories import native_legacy_control_clear_0056_v1 as legacy
from app.database.repositories import native_legacy_control_genesis_0056_v1 as genesis
from app.database.repositories.qualification_ledger import PortfolioLocalCheckpoint
from app.domain.source_primitives import canonical, decode
from app.trade_qualification import demo_control as control
from app.trade_qualification import demo_control_clear_v2 as clear

_STARTED = datetime(2026, 9, 16, 14, 0, tzinfo=UTC)
_CLEARED = _STARTED + timedelta(seconds=2)
_ORIGIN = "a" * 64


def _checkpoints():
    before = {
        "schema_version": "ctcc.portfolio_local_checkpoint.v2",
        "unresolved_intents": [],
        "currency_states": [
            {
                "account_revision": 0,
                "ledger_revision": 0,
                "claims_sha256": None,
                "active": [],
            }
        ],
        "blocking_reasons": ["legacy_inflight_or_uncertain"],
        "legacy": {
            "positions": [],
            "orders": [],
            "algos": [],
            "sync_checkpoint": {"status": "reconciled"},
            "automation": {
                "armed": False,
                "emergency_stop": True,
                "locked": True,
                "lock_reasons": [
                    *genesis._REASONS,
                    f"native_legacy_genesis_control_{_ORIGIN}",
                ],
                "updated_at": _STARTED.isoformat(),
                "active_trades": {},
            },
        },
    }
    after = deepcopy(before)
    after["blocking_reasons"] = []
    after["legacy"]["automation"].update(
        emergency_stop=False,
        locked=False,
        lock_reasons=[],
        updated_at=_CLEARED.isoformat(),
    )
    return before, after


def _receipt(before, after):
    return legacy.receipt(
        event=_ORIGIN,
        acquired_event="b" * 64,
        lineage="c" * 64,
        created_at=_STARTED,
        clear_at=_CLEARED,
        before=canonical(before),
        after=canonical(after),
    )


def test_recovery_revision_is_distinct_from_exact_cold_genesis():
    values = genesis._stopped_values(_STARTED, _ORIGIN)
    original = DemoAutomationState(**values)
    assert legacy.origin(original) == _ORIGIN
    genesis._exact_readback([original], _STARTED, _ORIGIN)
    original.control_revision += 1
    with pytest.raises(genesis.LegacyControlGenesis0056Error):
        genesis._exact_readback([original], _CLEARED, _ORIGIN)
    cleared = DemoAutomationState(**(values | legacy.cleared_values(_CLEARED)))
    legacy.exact_cleared(cleared, created_at=_STARTED, clear_at=_CLEARED, event=_ORIGIN)
    assert cleared.restart_latch_required is True and cleared.armed is False
    cleared.active_trades = {"unexpected": "exposure"}
    with pytest.raises(control.DemoControlError):
        legacy.exact_cleared(
            cleared, created_at=_STARTED, clear_at=_CLEARED, event=_ORIGIN
        )


@pytest.mark.parametrize(
    "changed",
    [
        "extra_blocker",
        "intent",
        "other_revision",
        "other_mirror",
        "after_changed",
        "false_revision",
        "bad_marker",
    ],
)
def test_cold_checkpoint_never_discards_other_local_evidence(changed):
    before, after = _checkpoints()
    assert (
        PortfolioLocalCheckpoint(canonical(before), _STARTED, _STARTED).known_flat
        is False
    )
    assert (
        PortfolioLocalCheckpoint(canonical(after), _CLEARED, _CLEARED).known_flat
        is True
    )
    report = decode(_receipt(before, after))
    assert report["origin_control_event_sha256"] == _ORIGIN
    if changed == "extra_blocker":
        before["blocking_reasons"].append(
            "legacy_reconciliation_initialization_unknown"
        )
    elif changed == "intent":
        before["unresolved_intents"].append("unknown-order")
    elif changed == "other_revision":
        before["currency_states"][0]["ledger_revision"] = 1
    elif changed == "other_mirror":
        before["legacy"]["positions"].append("position")
    elif changed == "after_changed":
        after["legacy"]["sync_checkpoint"]["status"] = "other"
    elif changed == "false_revision":
        before["currency_states"][0]["account_revision"] = False
    else:
        before["legacy"]["automation"]["lock_reasons"][2] = "wrong"
    with pytest.raises(control.DemoControlError):
        _receipt(before, after)


def test_cold_origin_must_be_a_previous_stopped_lineage_member():
    scope = control.ControlScope("demo", "700002")
    unlocked_at = _STARTED + timedelta(seconds=1)
    start = control.stopped_genesis(scope, _STARTED)
    current = control.acquire(
        start,
        scope=scope,
        owner_sha256="8" * 64,
        pins=control.ControlPins("1" * 64, "2" * 64, "3" * 64),
        now=unlocked_at,
    )
    rows = []
    previous = None
    for command, action, state in (
        ("5" * 64, "estop", start),
        ("6" * 64, "acquire", current),
    ):
        instead = {
            "version": "ctcc.demo_control_event.v1",
            "command_id": command,
            "action": action,
            "previous_sha256": previous,
            "state": control.document(state),
        }
        raw = control.canonical(instead)
        event = control.digest(raw)
        rows.append(
            control.ControlJournalSourceRowV1(
                scope,
                state.revision,
                command,
                action,
                previous,
                event,
                control.digest(control.canonical(control.document(state))),
                raw,
                state.updated_at,
            )
        )
        previous = event
    exact = clear.replay_control_journal_lineage_v2(
        tuple(rows),
        scope=scope,
        target_state=current,
        expected_head_sha256=previous,
        clear_ack_rows=(),
    )
    legacy.verify_origin(exact, rows[0].event_sha256, created_at=_STARTED)
    for digest in (rows[1].event_sha256, "9" * 64):
        with pytest.raises(control.DemoControlError):
            legacy.verify_origin(exact, digest, created_at=_STARTED)
    changed = SimpleNamespace(
        scope=scope,
        state=current,
        source_rows=(
            *rows,
            SimpleNamespace(
                action="arm_requested",
                event_sha256="c" * 64,
            ),
        ),
    )
    with pytest.raises(control.DemoControlError):
        legacy.verify_origin(changed, rows[0].event_sha256, created_at=_STARTED)
