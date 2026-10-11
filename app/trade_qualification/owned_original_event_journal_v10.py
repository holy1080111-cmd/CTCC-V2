"""Same-frame original G5 and controlled-journal observation, always DENY.

The private coordinator retains raw market/account packets across both locked
journal reads. This diagnostic never establishes complete consumed-event history,
G6, candidate, G12, risk reservation, or order authority.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import original_source_coordinator_v2 as original
from app.trade_qualification.owned_event_journal_validation import _utc

_SCHEMA = "ctcc.owned_original_event_journal_in_frame.v10"
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_MAX_BYTES = 3072
_CODES = frozenset(
    {
        "g5_unavailable",
        "ledger_unavailable",
        "ledger_changed",
        "event_seen_in_controlled_journal",
        "event_absent_from_controlled_journal_only",
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
_DIGEST_FIELDS = (
    "original_diagnostic_sha256",
    "public_packet_sha256",
    "account_packet_sha256",
    "precursor_receipt_sha256",
    "precursor_intent_sha256",
    "base_prefix_receipt_sha256",
    "event_receipt_sha256",
    "account_plan_sha256",
    "scope_sha256",
    "session_binding_sha256",
    "event_key_sha256",
    "ledger_first_sha256",
    "ledger_second_sha256",
)
_FIELDS = frozenset(
    {
        "schema_version",
        "code",
        "ledger_revision",
        "ledger_observed_at",
        "event_seen_in_controlled_journal",
        "g1_g5_replayed",
        "admission",
        *_DIGEST_FIELDS,
        *_FALSE_FIELDS,
    }
)


class OwnedOriginalEventJournalError(ValueError):
    """Static refusal; never prints source or account values."""


@dataclass(frozen=True, slots=True, repr=False)
class OwnedOriginalEventJournalDiagnosticV10:
    receipt_json: bytes

    def __post_init__(self):
        try:
            if type(self.receipt_json) is not bytes:
                raise ValueError
            raw = decode(self.receipt_json, _MAX_BYTES)
            if (
                type(raw) is not dict
                or set(raw) != _FIELDS
                or canonical(raw) != self.receipt_json
                or raw["schema_version"] != _SCHEMA
                or raw["code"] not in _CODES
                or raw["admission"] != "DENY"
                or any(raw[name] is not False for name in _FALSE_FIELDS)
                or type(raw["g1_g5_replayed"]) is not bool
            ):
                raise ValueError
            for name in _DIGEST_FIELDS:
                value = raw[name]
                if value is not None and (
                    type(value) is not str or _HEX.fullmatch(value) is None
                ):
                    raise ValueError
            if any(
                raw[name] is None
                for name in (
                    "original_diagnostic_sha256",
                    "account_plan_sha256",
                    "scope_sha256",
                    "session_binding_sha256",
                )
            ):
                raise ValueError
            event = raw["g1_g5_replayed"]
            if event is (raw["code"] == "g5_unavailable"):
                raise ValueError
            source_fields = (
                "public_packet_sha256",
                "account_packet_sha256",
                "precursor_receipt_sha256",
                "precursor_intent_sha256",
                "base_prefix_receipt_sha256",
                "event_receipt_sha256",
                "event_key_sha256",
            )
            if event and any(raw[name] is None for name in source_fields):
                raise ValueError
            if not event and any(
                raw[name] is not None
                for name in ("event_receipt_sha256", "event_key_sha256")
            ):
                raise ValueError
            first, second = raw["ledger_first_sha256"], raw["ledger_second_sha256"]
            revision, observed = raw["ledger_revision"], raw["ledger_observed_at"]
            seen = raw["event_seen_in_controlled_journal"]
            stable = raw["code"] in {
                "event_seen_in_controlled_journal",
                "event_absent_from_controlled_journal_only",
            }
            if stable:
                if (
                    first is None
                    or second is None
                    or type(revision) is not int
                    or revision < 1
                    or type(seen) is not bool
                    or seen is not (raw["code"] == "event_seen_in_controlled_journal")
                ):
                    raise ValueError
                _utc(observed)
            elif raw["code"] == "ledger_changed":
                if (
                    first is None
                    or second is None
                    or any(value is not None for value in (revision, observed, seen))
                ):
                    raise ValueError
            elif any(
                value is not None for value in (first, second, revision, observed, seen)
            ):
                raise ValueError
        except Exception:  # noqa: BLE001 -- bounded untrusted receipt denial
            raise OwnedOriginalEventJournalError(
                "owned_event_journal_v10_receipt_invalid"
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


async def inspect_owned_original_event_journal_v10(
    public_root,
    account_root,
    *,
    instrument_id,
    strategy,
    market_policy,
    account_session,
    session_factory,
) -> OwnedOriginalEventJournalDiagnosticV10:
    """Own G1--G5 and the DB scans in one task; accept no caller proof or PASS."""
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    receipt = await original._capture_owned_original_event_journal_for_boundary_v10(
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
    return OwnedOriginalEventJournalDiagnosticV10(receipt)
