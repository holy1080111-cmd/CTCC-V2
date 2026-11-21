"""Fixed old-column retention proof for the source0017 -> stopped0055 route.

This is SQL construction and DATA validation, not a migration permission. The
caller must first prove the sealed source17 catalog/native storage and retain
its real owner locks. SQL NULLs, JSON nulls, row duplicates and column order
remain distinct. No original value is read through a Python getter/callback.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import text

from scripts import native_owner_retained_rows_v2 as rows


class OldColumnProjectionV1(Enum):
    ORIGINAL = "original_values"
    AFTER_FIXED_0034 = "exact_source0034_negative_only_delta"


class Retained0017ProjectionRefused(ValueError):
    pass


REASON_0034 = "control_protocol_upgrade_requires_clear"
_STATE_CHANGES = frozenset({"armed", "emergency_stop", "locked", "lock_reasons"})
_NEW_STATE_COLUMNS = (
    ["control_revision", 20, -1, True, 0, "", ""],
    ["restart_latch_required", 16, -1, True, 0, "", ""],
)


def _need(value):
    if value is not True:
        raise Retained0017ProjectionRefused("retained0017_projection_refused") from None


def old_projection_sql_v1(name, columns, *, phase):
    """Always project before to_jsonb; its whole-row argument must stay old."""
    _need(type(phase) is OldColumnProjectionV1)
    _need(type(name) is str and rows._NAME.fullmatch(name) is not None)
    encoded = rows.row_expression_v2(columns)
    selected = []
    if name == "demo_automation_state":
        _need(_STATE_CHANGES | {"id"} <= set(columns))
    for column in columns:
        original = 's."' + column + '"'
        value = original
        if (
            phase is OldColumnProjectionV1.AFTER_FIXED_0034
            and name == "demo_automation_state"
            and column in _STATE_CHANGES
        ):
            replacement = {
                "armed": "false",
                "emergency_stop": "true",
                "locked": "true",
                "lock_reasons": original
                + " || '[\"control_protocol_upgrade_requires_clear\"]'::pg_catalog.jsonb",
            }[column]
            value = (
                'CASE WHEN s."id"=1 THEN ' + replacement + " ELSE " + original + " END"
            )
        selected.append(value + ' AS "' + column + '"')
    projected = "(SELECT " + ",".join(selected) + ' FROM public."' + name + '" s) r'
    size = text(
        "SELECT pg_catalog.count(*),COALESCE(pg_catalog.sum(pg_catalog.octet_length("
        + encoded
        + ")),0)::pg_catalog.int8 FROM "
        + projected
    )
    digest = text(
        "SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to("
        "COALESCE(pg_catalog.string_agg(row_sha,'' ORDER BY row_sha COLLATE pg_catalog.\"C\"),''),"
        "'UTF8')),'hex') FROM (SELECT pg_catalog.encode(pg_catalog.sha256("
        "pg_catalog.convert_to("
        + encoded
        + ",'UTF8')),'hex') row_sha FROM "
        + projected
        + ") q"
    )
    return size, digest


def validate_original_control_v1(automation):
    """Refuse malformed reasons before frozen0034 can apply COALESCE."""
    _need(type(automation) is list and len(automation) == 1)
    state = automation[0]
    _need(type(state) is dict and type(state.get("id")) is int and state["id"] == 1)
    _need(
        {
            "id",
            "armed",
            "emergency_stop",
            "locked",
            "lock_reasons",
            "active_trades",
            "active_instrument_id",
            "active_client_order_id",
            "active_start_equity",
            "active_started_at",
            "last_started_at",
            "last_completed_at",
        }
        <= set(state)
    )
    _need(
        all(
            type(state.get(name)) is bool
            for name in ("armed", "emergency_stop", "locked")
        )
    )
    reasons = state.get("lock_reasons")
    _need(
        type(reasons) is list
        and all(type(reason) is str and bool(reason) for reason in reasons)
    )
    # Existing unknown reasons and existing copies of the fixed reason stay in
    # order. 0034 appends exactly one more entry; nothing is deduplicated.
    _need(type(state.get("active_trades")) is dict and not state["active_trades"])
    _need(
        all(
            state.get(name) is None
            for name in (
                "active_instrument_id",
                "active_client_order_id",
                "active_start_equity",
                "active_started_at",
            )
        )
    )
    start, completed = state.get("last_started_at"), state.get("last_completed_at")
    if start is None and completed is None:
        return state
    _need(type(start) is str and type(completed) is str)
    try:
        start_time, complete_time = (
            datetime.fromisoformat(start),
            datetime.fromisoformat(completed),
        )
    except ValueError:
        _need(False)
    _need(
        start_time.utcoffset() is not None
        and complete_time.utcoffset() is not None
        and start_time <= complete_time
    )
    return state


def require_preserved_old_layouts_v1(before, after):
    """Physical old relation/column identities stay exact; only two columns append."""
    _need(type(before) is list and type(after) is list)
    old = {row[0]: row for row in before}
    new = {row[0]: row for row in after}
    _need(len(old) == len(before) and len(new) == len(after) and set(old) <= set(new))
    for name, old_row in old.items():
        current = new[name]
        _need(
            type(old_row) is list
            and type(current) is list
            and len(old_row) == len(current) == 4
        )
        _need(current[:3] == old_row[:3])
        expected = (
            old_row[3] + list(_NEW_STATE_COLUMNS)
            if name == "demo_automation_state"
            else old_row[3]
        )
        _need(current[3] == expected)


def require_preserved_projection_v1(expected, actual):
    _need(type(expected) is list and type(actual) is list)
    _need(len({row[0] for row in expected}) == len(expected))
    _need(len({row[0] for row in actual}) == len(actual))
    _need(expected == actual)
