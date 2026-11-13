"""Canonical evidence for one cold legacy singleton transition."""

import json
from datetime import datetime

from app.database.models.demo_automation import DemoAutomationState
from app.database.repositories import native_legacy_control_genesis_0056_v1 as genesis
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import demo_control as control

ACTION = "clear_exact_stopped_legacy_0056"
RESOURCE = "demo_automation_state"
ACTOR = "ctcc_native_control_operator_0056"
_BLOCKER = "legacy_inflight_or_uncertain"
_PREFIX = "native_legacy_genesis_control_"


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
