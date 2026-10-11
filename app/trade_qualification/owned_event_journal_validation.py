"""Shared validation for a bounded, read-only controlled event-journal scan.

This module proves only the inspected PostgreSQL row chain. It cannot establish
that legacy or exchange event history is complete and grants no G6 authority.
"""

from __future__ import annotations

from datetime import datetime

from app.database.repositories.qualification_ledger import (
    _ConsumedEventJournalInspection,
)
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import account_capture, account_native_clock
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope, digest

_JOURNAL_SCHEMA = "ctcc.consumed_event_journal_inspection.v1"
_MAX_JOURNAL = 4096
_STABLE_JOURNAL_FIELDS = (
    "scope_sha256",
    "account_revision",
    "ledger_revision",
    "claims_sha256",
    "event_count",
    "transition_count",
    "state_counts",
    "event_keys_sha256",
    "rows_sha256",
    "transitions_sha256",
    "db_journal_row_chain_verified",
    "db_journal_complete",
    "external_event_history_complete",
    "source_authenticity_verified",
    "execution_authority",
    "admission",
)
_JOURNAL_FIELDS = frozenset(
    {
        "schema_version",
        "observed_at",
        "received_at",
        *_STABLE_JOURNAL_FIELDS,
    }
)
_HEX = frozenset("0123456789abcdef")


class OwnedEventJournalValidationError(ValueError):
    """Fixed refusal; never includes account identity or source bytes."""


def _utc(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError
    at = datetime.fromisoformat(value)
    if at.utcoffset() is None or at.utcoffset().total_seconds() != 0:
        raise ValueError
    return at


def _session_identity(session, *, used):
    if type(session) is not ControlledDemoAccountSession or session._used is not used:
        raise OwnedEventJournalValidationError("owned_event_session_invalid")
    try:
        plan = account_capture._checked_plan(session._plan, session._pin)
        account_native_clock._checked_credential_pin(session)
        if (
            type(plan) is not account_capture.CurrentDemoAccountCapturePlanV6
            or plan.environment != "demo"
            or plan.registration_region != "global"
            or plan.settlement_currency != "USDT"
        ):
            raise ValueError
        scope = LedgerScope(
            environment=plan.environment,
            account_id=plan.expected_uid,
            settlement_currency=plan.settlement_currency,
        )
        binding = sha(
            canonical(
                {
                    "account_plan_sha256": session._pin,
                    "session_binding_id": plan.session_binding_id,
                }
            )
        )
        return session._pin, scope, binding
    except Exception:  # noqa: BLE001 -- never echo private session values
        raise OwnedEventJournalValidationError("owned_event_session_invalid") from None


def _journal_document(inspection, scope, started, finished):
    if (
        type(inspection) is not _ConsumedEventJournalInspection
        or type(inspection.document_json) is not bytes
        or not 0 < len(inspection.document_json) <= _MAX_JOURNAL
        or type(inspection.consumed_event_keys) is not frozenset
        or len(inspection.consumed_event_keys) > 2048
    ):
        raise ValueError
    raw = decode(inspection.document_json, _MAX_JOURNAL)
    keys = inspection.consumed_event_keys
    if (
        type(raw) is not dict
        or set(raw) != _JOURNAL_FIELDS
        or canonical(raw) != inspection.document_json
        or raw["schema_version"] != _JOURNAL_SCHEMA
        or raw["scope_sha256"] != digest(scope)
        or raw["event_keys_sha256"] != sha(canonical(sorted(keys)))
        or any(
            type(key) is not str
            or len(key) != 64
            or any(char not in _HEX for char in key)
            for key in keys
        )
        or type(raw["event_count"]) is not int
        or raw["event_count"] != len(keys)
        or type(raw["transition_count"]) is not int
        or not len(keys) <= raw["transition_count"] <= 8192
        or type(raw["account_revision"]) is not int
        or raw["account_revision"] < 1
        or type(raw["ledger_revision"]) is not int
        or raw["ledger_revision"] < raw["account_revision"]
        or raw["state_counts"]
        != {
            state: raw["state_counts"].get(state)
            for state in ("reserved", "consumed", "uncertain", "reconciled_flat")
        }
        or any(
            type(count) is not int or count < 0
            for count in raw["state_counts"].values()
        )
        or sum(raw["state_counts"].values()) != len(keys)
        or raw["db_journal_row_chain_verified"] is not True
        or any(
            raw[name] is not False
            for name in (
                "db_journal_complete",
                "external_event_history_complete",
                "source_authenticity_verified",
                "execution_authority",
            )
        )
        or raw["admission"] != "DENY"
        or any(
            type(raw[name]) is not str
            or len(raw[name]) != 64
            or any(char not in _HEX for char in raw[name])
            for name in ("claims_sha256", "rows_sha256", "transitions_sha256")
        )
    ):
        raise ValueError
    observed, received = _utc(raw["observed_at"]), _utc(raw["received_at"])
    if (
        inspection.observed_at != observed
        or inspection.received_at != received
        or not started <= observed <= received <= finished
        or inspection.sha256 != sha(inspection.document_json)
    ):
        raise ValueError
    return raw
