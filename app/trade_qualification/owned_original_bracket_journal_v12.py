"""One-invocation original bracket and UID event-journal inspection, always DENY.

Both nested records are made before the one-use native source frame closes. A
stable absence in the controlled journal is not complete external event history
or a G6 pass. Neither record can be replayed into G12 or order permission.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import original_source_coordinator_v2 as original
from app.trade_qualification.owned_original_bracket_v11 import (
    OwnedOriginalBracketDiagnosticV11,
)
from app.trade_qualification.owned_original_event_journal_v10 import (
    OwnedOriginalEventJournalDiagnosticV10,
)

_SCHEMA = "ctcc.owned_original_bracket_journal.v12"
_MAX_BYTES = 12288
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_CODES = frozenset(
    {
        "source_unavailable",
        "precursor_no_intent",
        "g5_unavailable",
        "ledger_unavailable",
        "ledger_changed",
        "event_seen_in_controlled_journal",
        "bracket_unavailable",
        "joined_inspected_unqualified",
    }
)
_FALSE_FIELDS = (
    "source_authenticity_verified",
    "historical_first_availability_verified",
    "event_ledger_authenticated",
    "g6_evaluated",
    "g7_evaluated",
    "g8_evaluated",
    "g9_evaluated",
    "candidate_created",
    "g1_g11_complete",
    "g12_published",
    "account_complete",
    "execution_recheck_performed",
    "atomic_risk_reserved",
    "durable_intent_created",
    "execution_authority",
    "order_submitted",
)
_FIELDS = frozenset(
    {
        "schema_version",
        "code",
        "bracket_receipt_json",
        "bracket_receipt_sha256",
        "journal_receipt_json",
        "journal_receipt_sha256",
        "admission",
        *_FALSE_FIELDS,
    }
)
_SHARED = (
    "original_diagnostic_sha256",
    "public_packet_sha256",
    "account_packet_sha256",
    "account_plan_sha256",
    "precursor_receipt_sha256",
    "precursor_intent_sha256",
    "base_prefix_receipt_sha256",
    "event_key_sha256",
    "g1_g5_replayed",
)


class OwnedOriginalBracketJournalError(ValueError):
    """Fixed refusal without account, source, SQL, or credential values."""


def _code(bracket: dict, journal: dict) -> str:
    bracket_code, journal_code = bracket["code"], journal["code"]
    if bracket_code in {"source_unavailable", "precursor_no_intent"}:
        return bracket_code
    if journal_code in {
        "g5_unavailable",
        "ledger_unavailable",
        "ledger_changed",
        "event_seen_in_controlled_journal",
    }:
        return journal_code
    if bracket_code == "bracket_unavailable":
        return bracket_code
    return "joined_inspected_unqualified"


@dataclass(frozen=True, slots=True, repr=False)
class OwnedOriginalBracketJournalDiagnosticV12:
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
            ):
                raise ValueError
            nested = {}
            for prefix, model in (
                ("bracket", OwnedOriginalBracketDiagnosticV11),
                ("journal", OwnedOriginalEventJournalDiagnosticV10),
            ):
                item = raw[f"{prefix}_receipt_json"]
                digest = raw[f"{prefix}_receipt_sha256"]
                if (
                    type(item) is not str
                    or type(digest) is not str
                    or _HEX.fullmatch(digest) is None
                ):
                    raise ValueError
                encoded = item.encode("ascii")
                checked = model(encoded)
                if checked.receipt_sha256 != digest:
                    raise ValueError
                nested[prefix] = decode(checked.receipt_json)
            bracket, journal = nested["bracket"], nested["journal"]
            if (
                raw["code"] != _code(bracket, journal)
                or any(bracket[name] != journal[name] for name in _SHARED)
                or bracket["base_event_receipt_sha256"]
                != journal["event_receipt_sha256"]
                or bracket["bracket_inspected"] is not journal["g1_g5_replayed"]
                or bracket["bracket_inspected"]
                and journal["code"] == "g5_unavailable"
                or not bracket["bracket_inspected"]
                and journal["code"] != "g5_unavailable"
            ):
                raise ValueError
        except Exception:  # noqa: BLE001 -- nested untrusted records stay private
            raise OwnedOriginalBracketJournalError(
                "owned_bracket_journal_receipt_invalid"
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


async def inspect_owned_original_bracket_journal_v12(
    public_root,
    account_root,
    *,
    instrument_id,
    strategy,
    market_policy,
    account_session,
    session_factory,
) -> OwnedOriginalBracketJournalDiagnosticV12:
    """Accept no caller packet, run, historical receipt, bracket, or ledger."""
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    receipt = await original._capture_owned_original_bracket_journal_for_boundary_v12(
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
    checked = OwnedOriginalBracketJournalDiagnosticV12(receipt)
    record = decode(checked.receipt_json, _MAX_BYTES)
    bracket = decode(record["bracket_receipt_json"].encode("ascii"))
    if bracket["account_plan_sha256"] != account_session._pin or (
        bracket["bracket_inspected"] and account_session._used is not True
    ):
        raise OwnedOriginalBracketJournalError("owned_bracket_journal_session_mismatch")
    return checked
