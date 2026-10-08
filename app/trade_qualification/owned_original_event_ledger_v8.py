"""Read-only G5-to-controlled-journal observation; never a G6 or R7 permit.

The V7 issuer owns the source and account capture in this same task. A separate
PostgreSQL inspection can reject a known consumed event, but an absent row cannot
prove that legacy or exchange history is complete. Every outcome stays DENY.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.database.repositories.qualification_ledger import (
    QualificationLedgerRepository,
    _ConsumedEventJournalInspection,
)
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.trade_qualification import account_capture, account_native_clock
from app.trade_qualification import owned_original_base_event_v7 as owned_g5
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope, digest

_SCHEMA = "ctcc.owned_original_event_ledger_observation.v8"
_JOURNAL_SCHEMA = "ctcc.consumed_event_journal_inspection.v1"
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_MAX_RECEIPT = 2048
_MAX_JOURNAL = 4096
_CODES = frozenset(
    {
        "g5_unavailable",
        "ledger_unavailable",
        "ledger_changed",
        "event_seen_in_controlled_journal",
        "event_absent_from_controlled_journal_only",
    }
)
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
_FALSE_FIELDS = (
    "historical_first_availability_verified",
    "event_ledger_authenticated",
    "db_journal_complete",
    "external_event_history_complete",
    "g6_evaluated",
    "g7_evaluated",
    "candidate_created",
    "g1_g11_complete",
    "g12_published",
    "account_complete",
    "execution_recheck_performed",
    "atomic_risk_reserved",
    "execution_authority",
    "order_submitted",
)
_FIELDS = frozenset(
    {
        "schema_version",
        "code",
        "v7_receipt_sha256",
        "account_plan_sha256",
        "scope_sha256",
        "session_binding_sha256",
        "event_key_sha256",
        "ledger_first_sha256",
        "ledger_second_sha256",
        "ledger_revision",
        "ledger_observed_at",
        "event_seen_in_controlled_journal",
        "g1_g5_replayed",
        "admission",
        *_FALSE_FIELDS,
    }
)


class OwnedOriginalEventLedgerError(ValueError):
    """Fixed rejection code; never includes UID, credentials or SQL details."""


def _utc(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError
    at = datetime.fromisoformat(value)
    if at.utcoffset() is None or at.utcoffset().total_seconds() != 0:
        raise ValueError
    return at


@dataclass(frozen=True, slots=True, repr=False)
class OwnedOriginalEventLedgerDiagnosticV8:
    receipt_json: bytes

    def __post_init__(self):
        try:
            raw = decode(self.receipt_json, _MAX_RECEIPT)
            if (
                type(raw) is not dict
                or set(raw) != _FIELDS
                or canonical(raw) != self.receipt_json
                or raw["schema_version"] != _SCHEMA
                or raw["code"] not in _CODES
                or raw["admission"] != "DENY"
                or any(raw[name] is not False for name in _FALSE_FIELDS)
                or type(raw["g1_g5_replayed"]) is not bool
                or raw["g1_g5_replayed"] is (raw["code"] == "g5_unavailable")
            ):
                raise ValueError
            for name in (
                "v7_receipt_sha256",
                "account_plan_sha256",
                "scope_sha256",
                "session_binding_sha256",
            ):
                if type(raw[name]) is not str or _HEX.fullmatch(raw[name]) is None:
                    raise ValueError
            for name in (
                "event_key_sha256",
                "ledger_first_sha256",
                "ledger_second_sha256",
            ):
                if raw[name] is not None and (
                    type(raw[name]) is not str or _HEX.fullmatch(raw[name]) is None
                ):
                    raise ValueError
            stable = raw["code"] in {
                "event_seen_in_controlled_journal",
                "event_absent_from_controlled_journal_only",
            }
            if (
                (raw["event_key_sha256"] is None) != (raw["code"] == "g5_unavailable")
                or (raw["ledger_first_sha256"] is None)
                != (raw["ledger_second_sha256"] is None)
                or (raw["ledger_first_sha256"] is not None)
                != (stable or raw["code"] == "ledger_changed")
                or (raw["ledger_revision"] is None) != (not stable)
                or (raw["ledger_observed_at"] is None) != (not stable)
                or (raw["event_seen_in_controlled_journal"] is None) != (not stable)
            ):
                raise ValueError
            if stable:
                if (
                    type(raw["ledger_revision"]) is not int
                    or raw["ledger_revision"] < 1
                    or type(raw["event_seen_in_controlled_journal"]) is not bool
                    or raw["event_seen_in_controlled_journal"]
                    is not (raw["code"] == "event_seen_in_controlled_journal")
                ):
                    raise ValueError
                _utc(raw["ledger_observed_at"])
        except Exception:  # noqa: BLE001 -- bounded untrusted receipt denial
            raise OwnedOriginalEventLedgerError(
                "owned_event_ledger_receipt_invalid"
            ) from None

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def admission(self) -> Literal["DENY"]:
        return "DENY"

    @property
    def execution_authority(self) -> Literal[False]:
        return False


def _session_identity(session, *, used):
    if type(session) is not ControlledDemoAccountSession or session._used is not used:
        raise OwnedOriginalEventLedgerError("owned_event_session_invalid")
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
        raise OwnedOriginalEventLedgerError("owned_event_session_invalid") from None


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
        or any(type(key) is not str or _HEX.fullmatch(key) is None for key in keys)
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
            type(raw[name]) is not str or _HEX.fullmatch(raw[name]) is None
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


async def observe_owned_g5_event_ledger_v8(
    public_root,
    account_root,
    *,
    instrument_id,
    strategy,
    market_policy,
    account_session,
    session_factory,
) -> OwnedOriginalEventLedgerDiagnosticV8:
    """One same-task G5 replay plus two independent locked journal observations.

    Absence is explicitly limited to rows visible in the controlled DB journal.
    No caller gate, event, consumed-set, previous receipt or permit enters.
    """
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    pin, scope, binding = _session_identity(account_session, used=False)
    g5 = await owned_g5.preflight_owned_base_event_v7(
        public_root,
        account_root,
        instrument_id=instrument_id,
        strategy=strategy,
        market_policy=market_policy,
        account_session=account_session,
        session_factory=session_factory,
    )
    if task is not asyncio.current_task() or task.cancelling():
        raise asyncio.CancelledError
    if type(g5) is not owned_g5.OwnedOriginalBaseEventDiagnosticV7:
        raise OwnedOriginalEventLedgerError("owned_event_g5_handoff_invalid")
    owned_g5.OwnedOriginalBaseEventDiagnosticV7(g5.receipt_json)
    original = decode(g5.receipt_json, owned_g5._MAX_BYTES)
    inspected = original["code"] == "base_g1_g5_inspected"
    # An earlier source failure can occur before or after the one-use account
    # capture. Only a completed G5 is required to have consumed that session.
    expected_used = True if inspected else account_session._used
    if type(expected_used) is not bool:
        raise OwnedOriginalEventLedgerError("owned_event_session_changed")
    if _session_identity(account_session, used=expected_used) != (pin, scope, binding):
        raise OwnedOriginalEventLedgerError("owned_event_session_changed")
    event_key = original["event_key_sha256"]
    code = "g5_unavailable"
    first_sha = second_sha = revision = observed = seen = None

    def finish():
        return OwnedOriginalEventLedgerDiagnosticV8(
            canonical(
                {
                    "schema_version": _SCHEMA,
                    "code": code,
                    "v7_receipt_sha256": g5.receipt_sha256,
                    "account_plan_sha256": pin,
                    "scope_sha256": digest(scope),
                    "session_binding_sha256": binding,
                    "event_key_sha256": event_key,
                    "ledger_first_sha256": first_sha,
                    "ledger_second_sha256": second_sha,
                    "ledger_revision": revision,
                    "ledger_observed_at": observed,
                    "event_seen_in_controlled_journal": seen,
                    "g1_g5_replayed": event_key is not None,
                    "admission": "DENY",
                    **{name: False for name in _FALSE_FIELDS},
                }
            )
        )

    if not inspected:
        return finish()
    code = "ledger_unavailable"
    stamps = []

    def stamp():
        item = native_stamp()
        stamps.append(item)
        if len(stamps) >= 2:
            validate_stamps(stamps)
        return utc_from_ns(item["utc_ns"])

    repository = QualificationLedgerRepository(session_factory, clock=stamp)
    try:
        async with asyncio.timeout(30):
            first_start = stamp()
            first = await repository.inspect_consumed_event_journal(scope)
            first_finish = stamp()
            first_doc = _journal_document(first, scope, first_start, first_finish)
            if task.cancelling():
                raise asyncio.CancelledError
            second_start = stamp()
            second = await repository.inspect_consumed_event_journal(scope)
            second_finish = stamp()
            second_doc = _journal_document(second, scope, second_start, second_finish)
        if task is not asyncio.current_task() or task.cancelling():
            raise asyncio.CancelledError
        if _session_identity(account_session, used=True) != (pin, scope, binding):
            raise ValueError
        first_sha, second_sha = first.sha256, second.sha256
        if (
            first.received_at > second.observed_at
            or first.consumed_event_keys != second.consumed_event_keys
            or any(
                first_doc[name] != second_doc[name] for name in _STABLE_JOURNAL_FIELDS
            )
        ):
            code = "ledger_changed"
            return finish()
        revision = second_doc["ledger_revision"]
        observed = second_doc["observed_at"]
        seen = event_key in second.consumed_event_keys
        code = (
            "event_seen_in_controlled_journal"
            if seen
            else "event_absent_from_controlled_journal_only"
        )
        return finish()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- no SQL/source/credential exception in receipt
        if task.cancelling():
            raise asyncio.CancelledError from None
        first_sha = second_sha = None
        return finish()
