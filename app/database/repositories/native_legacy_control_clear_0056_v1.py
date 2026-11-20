"""Canonical evidence for one cold legacy singleton transition."""

import json
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal

from app.database.models.demo_automation import DemoAutomationState
from app.database.repositories import native_legacy_control_genesis_0056_v1 as genesis
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import demo_control as control

ACTION = "clear_exact_stopped_legacy_0056"
RESOURCE = "demo_automation_state"
ACTOR = "ctcc_native_control_operator_0056"
_BLOCKER = "legacy_inflight_or_uncertain"
_PREFIX = "native_legacy_genesis_control_"
RESTART_ACTION = "clear_exact_restarted_legacy_0056"
_RESTART_REQUIRED_REASONS = frozenset(
    {"emergency_stop_engaged", "restart_requires_explicit_clear_stop"}
)


def _deny():
    raise control.DemoControlError(
        "control56_cold_legacy_clear_proof_changed"
    ) from None


def origin(row):
    if (
        type(row) is not DemoAutomationState
        or type(row.lock_reasons) is not list
        or len(row.lock_reasons) != 3
        or type(row.lock_reasons[2]) is not str
        or not row.lock_reasons[2].startswith(_PREFIX)
    ):
        _deny()
    return control.sha(row.lock_reasons[2][len(_PREFIX) :])


def verify_origin(lineage, event, *, created_at):
    matches = [row for row in lineage.source_rows if row.event_sha256 == event]
    if len(matches) != 1:
        _deny()
    marker = matches[0]
    initial = control.decode(control.canonical(json.loads(marker.event_json)["state"]))
    if not (
        initial.scope == lineage.scope
        and initial.emergency_stop is True
        and initial.arm_request_id is initial.arm_expires_at is None
        and initial.lease_until <= created_at <= lineage.state.updated_at
        and all(
            row.action in {"acquire", "renew", "release", "estop", "disarm", "rebind"}
            for row in lineage.source_rows
        )
    ):
        _deny()


def cleared_values(at):
    return {
        "armed": False,
        "emergency_stop": False,
        "locked": False,
        "lock_reasons": [],
        "restart_latch_required": True,
        "control_revision": 2,
        "updated_at": control.utc(at),
    }


def exact_cleared(row, *, created_at, clear_at, event):
    if type(row) is not DemoAutomationState or not created_at <= clear_at:
        _deny()
    expected = genesis._stopped_values(created_at, event) | cleared_values(clear_at)
    if any(getattr(row, field) != value for field, value in expected.items()) or (
        row.armed is not False
        or row.locked is not False
        or row.emergency_stop is not False
        or row.restart_latch_required is not True
    ):
        _deny()


def _empty_mirrors_and_counters(point):
    legacy = point["legacy"]
    if (
        point["unresolved_intents"] != []
        or point["schema_version"] != "ctcc.portfolio_local_checkpoint.v2"
        or not 1 <= len(point["currency_states"]) <= 128
        or any(legacy[key] != [] for key in ("positions", "orders", "algos"))
        or any(
            type(row["account_revision"]) is not int
            or type(row["ledger_revision"]) is not int
            or row["account_revision"] != 0
            or row["ledger_revision"] != 0
            or row["claims_sha256"] is not None
            or row["active"] != []
            for row in point["currency_states"]
        )
    ):
        _deny()


def stopped_checkpoint(raw):
    point = decode(raw, 262144)
    if point["blocking_reasons"] != [_BLOCKER]:
        _deny()
    _empty_mirrors_and_counters(point)


def checkpoint_pair(before, after):
    a = decode(before, 262144)
    b = decode(after, 262144)
    if a["blocking_reasons"] != [_BLOCKER] or b["blocking_reasons"] != []:
        _deny()
    _empty_mirrors_and_counters(a)
    before_control = a["legacy"]["automation"]
    after_control = b["legacy"]["automation"]
    if (
        before_control["armed"] is not False
        or before_control["emergency_stop"] is not True
        or before_control["locked"] is not True
        or after_control["armed"] is not False
        or after_control["emergency_stop"] is not False
        or after_control["locked"] is not False
        or after_control["lock_reasons"] != []
    ):
        _deny()
    a["blocking_reasons"] = []
    before_control.update(
        armed=False,
        emergency_stop=False,
        locked=False,
        lock_reasons=[],
        updated_at=after_control["updated_at"],
    )
    if canonical(a) != canonical(b):
        _deny()


def receipt(*, event, acquired_event, lineage, created_at, clear_at, before, after):
    checkpoint_pair(before, after)
    if type(created_at) is not datetime or not control.utc(created_at) <= control.utc(
        clear_at
    ):
        _deny()
    original = decode(before, 262144)["legacy"]["automation"]
    final = decode(after, 262144)["legacy"]["automation"]
    if (
        original["updated_at"] != created_at.isoformat()
        or original["lock_reasons"] != [*genesis._REASONS, f"{_PREFIX}{event}"]
        or final["updated_at"] != clear_at.isoformat()
    ):
        _deny()
    return canonical(
        {
            "schema": "ctcc.native_only.db0056_legacy_control_cleared.v1",
            "origin_control_event_sha256": control.sha(event),
            "stopped_owner_control_event_sha256": control.sha(acquired_event),
            "source_lineage_sha256": control.sha(lineage),
            "legacy_revision_before": 1,
            "legacy_revision_after": 2,
            "legacy_created_at": created_at.isoformat(),
            "legacy_clear_at": clear_at.isoformat(),
            "before_checkpoint_sha256": sha(before),
            "after_checkpoint_sha256": sha(after),
        }
    )


def _restart_deny():
    raise control.DemoControlError(
        "control56_restart_legacy_clear_proof_changed"
    ) from None


def _singleton_document(row):
    if type(row) is not DemoAutomationState:
        _restart_deny()
    result = {}
    for column in DemoAutomationState.__table__.columns:
        value = getattr(row, column.name)
        if type(value) is datetime:
            value = control.utc(value).isoformat()
        elif type(value) is date:
            value = value.isoformat()
        elif type(value) is Decimal:
            if not value.is_finite():
                _restart_deny()
            value = str(value)
        result[column.name] = value
    raw = canonical(result)
    if not 1 <= len(raw) <= 262144:
        _restart_deny()
    return result


def _restart_reasons(reasons):
    if (
        type(reasons) is not list
        or any(type(reason) is not str for reason in reasons)
        or len(reasons) != len(set(reasons))
        or not _RESTART_REQUIRED_REASONS.issubset(reasons)
    ):
        _restart_deny()
    optional = set(reasons) - _RESTART_REQUIRED_REASONS
    if optional:
        markers = [reason for reason in optional if reason.startswith(_PREFIX)]
        if len(markers) != 1 or optional != {
            "native_legacy_genesis_unreconciled",
            markers[0],
        }:
            _restart_deny()
        control.sha(markers[0][len(_PREFIX) :])


def _restart_stopped_document(value, *, now):
    at = control.utc(now)
    if (
        type(value) is not dict
        or set(value) != set(DemoAutomationState.__table__.columns.keys())
        or type(value["id"]) is not int
        or value["id"] != 1
        or type(value["control_revision"]) is not int
        or not 2 <= value["control_revision"] < 2**63 - 1
        or value["armed"] is not False
        or value["emergency_stop"] is not True
        or value["locked"] is not True
        or value["restart_latch_required"] is not True
        or value["last_error"] is not None
        or value["active_trades"] != {}
        or type(value["active_trades"]) is not dict
        or any(
            value[name] is not None
            for name in (
                "active_instrument_id",
                "active_client_order_id",
                "active_start_equity",
                "active_started_at",
            )
        )
        or type(value["symbol_cooldowns"]) is not dict
        or type(value["realized_pnl_events"]) is not list
        or any(
            type(value[name]) is not int or not 0 <= value[name] < 2**31
            for name in ("trades_today", "consecutive_losses")
        )
    ):
        _restart_deny()
    _restart_reasons(value["lock_reasons"])
    try:
        updated = control.utc(datetime.fromisoformat(value["updated_at"]))
        if updated > at or date.fromisoformat(value["session_date"]) > at.date():
            _restart_deny()
        started, completed = value["last_started_at"], value["last_completed_at"]
        if (started is None) != (completed is None):
            _restart_deny()
        if started is not None and not (
            control.utc(datetime.fromisoformat(started))
            <= control.utc(datetime.fromisoformat(completed))
            <= updated
        ):
            _restart_deny()
        if value["last_trade_closed_at"] is not None and (
            control.utc(datetime.fromisoformat(value["last_trade_closed_at"])) > updated
        ):
            _restart_deny()
        if (
            type(value["daily_pnl"]) is not str
            or not Decimal(value["daily_pnl"]).is_finite()
        ):
            _restart_deny()
        for name in ("baseline_equity", "peak_equity", "risk_peak_equity"):
            number = value[name]
            if number is not None and (
                type(number) is not str
                or not Decimal(number).is_finite()
                or Decimal(number) < 0
            ):
                _restart_deny()
    except (TypeError, ValueError, ArithmeticError):
        _restart_deny()


def restart_snapshot(row, *, now):
    value = _singleton_document(row)
    _restart_stopped_document(value, now=now)
    return canonical(value)


def restart_origin(before):
    value = decode(before, 262144)
    markers = [reason for reason in value["lock_reasons"] if reason.startswith(_PREFIX)]
    return None if not markers else control.sha(markers[0][len(_PREFIX) :])


def verify_restart_origin(lineage, before):
    marker = restart_origin(before)
    if marker is None:
        return
    matches = [row for row in lineage.source_rows if row.event_sha256 == marker]
    if len(matches) != 1:
        _restart_deny()
    initial = control.decode(
        control.canonical(json.loads(matches[0].event_json)["state"])
    )
    previous_updated = control.utc(
        datetime.fromisoformat(decode(before, 262144)["updated_at"])
    )
    if (
        initial.scope != lineage.scope
        or initial.emergency_stop is not True
        or initial.arm_request_id is not None
        or initial.arm_expires_at is not None
        or not initial.lease_until <= previous_updated <= lineage.state.updated_at
    ):
        _restart_deny()


def restart_cas_values(row):
    # All original columns participate, including historical JSON and counters.
    return {
        column.name: deepcopy(getattr(row, column.name))
        for column in DemoAutomationState.__table__.columns
    }


def restart_cleared_values(before, *, at):
    value = decode(before, 262144)
    _restart_stopped_document(value, now=at)
    return {
        "armed": False,
        "emergency_stop": False,
        "locked": False,
        "lock_reasons": [],
        "restart_latch_required": True,
        "control_revision": value["control_revision"] + 1,
        "updated_at": control.utc(at),
    }


def exact_restart_cleared(row, *, before, clear_at):
    original = decode(before, 262144)
    changes = restart_cleared_values(before, at=clear_at)
    changes["updated_at"] = changes["updated_at"].isoformat()
    expected = original | changes
    actual = canonical(_singleton_document(row))
    if actual != canonical(expected):
        _restart_deny()
    return actual


def restart_stopped_checkpoint(raw):
    point = decode(raw, 262144)
    if (
        point["schema_version"] != "ctcc.portfolio_local_checkpoint.v2"
        or point["blocking_reasons"] != [_BLOCKER]
        or point["unresolved_intents"] != []
        or not 1 <= len(point["currency_states"]) <= 128
        or any(point["legacy"][key] != [] for key in ("positions", "orders", "algos"))
    ):
        _restart_deny()
    for row in point["currency_states"]:
        if (
            type(row["account_revision"]) is not int
            or type(row["ledger_revision"]) is not int
            or not 0 <= row["account_revision"] <= row["ledger_revision"] < 2**63
            or row["active"] != []
            or (row["account_revision"] == 0 and row["claims_sha256"] is not None)
        ):
            _restart_deny()
        if row["account_revision"] > 0:
            control.sha(row["claims_sha256"])


def restart_checkpoint_pair(before, after):
    restart_stopped_checkpoint(before)
    original, final = decode(before, 262144), decode(after, 262144)
    original["blocking_reasons"] = []
    original["legacy"]["automation"].update(
        armed=False,
        emergency_stop=False,
        locked=False,
        lock_reasons=[],
        updated_at=final["legacy"]["automation"]["updated_at"],
    )
    if canonical(original) != canonical(final):
        _restart_deny()


def restart_receipt(
    *,
    before,
    after,
    singleton_before,
    singleton_after,
    clear_at,
    acquired_event,
    lineage,
):
    restart_checkpoint_pair(before, after)
    first, last = decode(singleton_before, 262144), decode(singleton_after, 262144)
    changes = restart_cleared_values(singleton_before, at=clear_at)
    changes["updated_at"] = changes["updated_at"].isoformat()
    if last != first | changes:
        _restart_deny()
    for point_raw, singleton in ((before, first), (after, last)):
        automation = decode(point_raw, 262144)["legacy"]["automation"]
        if any(
            name not in singleton or singleton[name] != value
            for name, value in automation.items()
        ):
            _restart_deny()
    return canonical(
        {
            "schema": "ctcc.native_only.db0056_restarted_legacy_control_cleared.v1",
            "stopped_owner_control_event_sha256": control.sha(acquired_event),
            "source_lineage_sha256": control.sha(lineage),
            "legacy_origin_control_event_sha256": restart_origin(singleton_before),
            "legacy_revision_before": first["control_revision"],
            "legacy_revision_after": last["control_revision"],
            "legacy_previous_updated_at": first["updated_at"],
            "legacy_clear_at": control.utc(clear_at).isoformat(),
            "before_checkpoint_sha256": sha(before),
            "after_checkpoint_sha256": sha(after),
            "before_singleton_sha256": sha(singleton_before),
            "after_singleton_sha256": sha(singleton_after),
            "restart_latch_preserved": True,
            "execution_authority": False,
        }
    )
