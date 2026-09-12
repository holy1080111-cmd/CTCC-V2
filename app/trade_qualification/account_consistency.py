"""Replayable cross-source account checks, never source authentication.

These checks find contradictions in recorded account-position-risk, balance and
position inventories. Matching separately read responses cannot establish a
shared exchange revision or an authenticated, complete account. No IO exists.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal
from functools import wraps
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator

from app.trade_qualification import account_capture as capture
from app.trade_qualification.models import QualificationModel

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Text = Annotated[str, Field(min_length=1, max_length=96)]
MAX_FINDINGS = 16384
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,20})?")
_BOUNDARIES = (
    "cross_source_atomicity_unverified",
    "source_authenticity_unverified",
    "account_scope_and_history_completeness_unverified",
)


class AccountConsistencyError(ValueError):
    """Static local failure only; raw account values never enter errors."""


class AccountConsistencyFinding(QualificationModel):
    model_config = ConfigDict(str_strip_whitespace=False)

    reason: Text
    status: Literal["missing", "conflict"]
    field: Text
    row_id: Text
    left_stream: Literal["account_position_risk"] = "account_position_risk"
    right_stream: Literal["balance", "positions", "account_position_risk"]
    left_receipt_sha256: Digest
    right_receipt_sha256: Digest


class AccountConsistencyReport(QualificationModel):
    model_config = ConfigDict(str_strip_whitespace=False)

    schema_version: Literal["ctcc.recorded_account_consistency.v1"] = (
        "ctcc.recorded_account_consistency.v1"
    )
    environment: Literal["demo"] = "demo"
    account_id: capture.Identifier
    settlement_currency: capture.Currency
    packet_sha256: Digest
    anchor_receipt_sha256: Digest
    balance_receipt_sha256: Digest
    position_receipt_sha256s: tuple[Digest, ...] = Field(min_length=1, max_length=1)
    anchor_observed_at: datetime | None
    findings: tuple[AccountConsistencyFinding, ...] = Field(max_length=MAX_FINDINGS)
    blocking_reasons: tuple[Text, ...] = Field(max_length=32)
    unverified_boundaries: tuple[
        Literal["cross_source_atomicity_unverified"],
        Literal["source_authenticity_unverified"],
        Literal["account_scope_and_history_completeness_unverified"],
    ] = _BOUNDARIES
    account_complete: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator(
        "account_complete",
        "source_authenticity_verified",
        "execution_authority",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise AccountConsistencyError("account_consistency_cannot_grant_authority")
        return value


def _redacted(function):
    @wraps(function)
    def checked(*args, **kwargs):
        failed = False
        try:
            return function(*args, **kwargs)
        except Exception:  # noqa: BLE001 -- do not expose source parser exceptions
            failed = True
        if failed:
            raise AccountConsistencyError("recorded_account_consistency_invalid")

    return checked


def _number(value):
    if type(value) is str and _NUMBER.fullmatch(value):
        return Decimal(value)
    return None


def _time(value):
    return capture._time_record("time", value, "source_update").value


def _reconcile_verified_packet(packet, packet_sha256):
    """Internal only: caller must first replay the entire externally pinned packet."""
    observed = {item.request.stream: item for item in packet.observations}
    anchor_receipt = observed["account_position_risk"]
    balance_receipt = observed["balance"]
    position_receipt = observed["positions"]
    anchor = json.loads(anchor_receipt.rows[0].canonical_json)
    balance = json.loads(balance_receipt.rows[0].canonical_json)
    positions = {
        item.row_id: json.loads(item.canonical_json) for item in position_receipt.rows
    }
    findings = []

    def add(reason, status, field, row_id, receipt):
        if len(findings) >= MAX_FINDINGS:
            raise AccountConsistencyError("account_consistency_findings_limit")
        findings.append(
            AccountConsistencyFinding(
                reason=reason,
                status=status,
                field=field,
                row_id=row_id,
                right_stream=receipt.request.stream,
                left_receipt_sha256=anchor_receipt.receipt_sha256,
                right_receipt_sha256=receipt.receipt_sha256,
            )
        )

    def compare(left, right, field, row_id, receipt, *, numeric=False):
        a, b = left.get(field), right.get(field)
        if numeric:
            a, b = _number(a), _number(b)
        else:
            a = a if type(a) is str and a else None
            b = b if type(b) is str and b else None
        prefix = (
            "account_anchor_balance"
            if receipt is balance_receipt
            else "account_anchor_position"
        )
        if a is None or b is None:
            add(f"{prefix}_field_missing", "missing", field, row_id, receipt)
        elif a != b:
            add(f"{prefix}_field_conflict", "conflict", field, row_id, receipt)

    anchor_at = _time(anchor.get("ts"))
    if anchor_at is None:
        add("account_anchor_clock_missing", "missing", "ts", "anchor", anchor_receipt)
    elif anchor_at < packet.barrier_completed_at:
        add(
            "account_anchor_predates_publication",
            "conflict",
            "ts",
            "anchor",
            anchor_receipt,
        )

    def clock(raw, field, row_id, receipt):
        at = _time(raw.get(field))
        if at is None:
            add(
                "account_anchor_source_clock_missing", "missing", field, row_id, receipt
            )
        elif anchor_at is not None and at > anchor_at:
            # A later update cannot be claimed to describe the older anchor.
            # Earlier updates may be unchanged records; equality is not required.
            add("account_anchor_cutoff_conflict", "conflict", field, row_id, receipt)

    clock(balance, "uTime", "balance", balance_receipt)
    anchor_balances = {item["ccy"]: item for item in anchor["balData"]}
    actual_balances = {item["ccy"]: item for item in balance["details"]}
    settlement = packet.plan.settlement_currency
    if settlement not in anchor_balances or settlement not in actual_balances:
        add(
            "account_anchor_settlement_currency_missing",
            "missing",
            "ccy",
            settlement,
            balance_receipt,
        )
    for currency in sorted(anchor_balances.keys() ^ actual_balances.keys()):
        add(
            "account_anchor_currency_inventory_conflict",
            "conflict",
            "ccy",
            currency,
            balance_receipt,
        )
    for currency in sorted(anchor_balances.keys() & actual_balances.keys()):
        left, right = anchor_balances[currency], actual_balances[currency]
        # These are per-currency operands. Never compare totalEq/adjEq USD with
        # settlement currency, nor relabel balances using instrument names.
        # The documented balData schema has ccy/eq/disEq, not cashBal. Do not
        # require or fabricate a balance-only field on account-position-risk.
        compare(left, right, "eq", currency, balance_receipt, numeric=True)
    for currency, item in sorted(actual_balances.items()):
        clock(item, "uTime", currency, balance_receipt)

    anchor_positions = {item["posId"]: item for item in anchor["posData"]}
    for identity in sorted(anchor_positions.keys() ^ positions.keys()):
        add(
            "account_anchor_position_inventory_conflict",
            "conflict",
            "posId",
            identity,
            position_receipt,
        )
    for identity in sorted(anchor_positions.keys() & positions.keys()):
        left, right = anchor_positions[identity], positions[identity]
        for field in ("instId", "instType", "posSide", "mgnMode"):
            compare(left, right, field, identity, position_receipt)
        compare(left, right, "pos", identity, position_receipt, numeric=True)
        # ccy is not assumed present on every anchor product. When supplied it
        # cannot contradict the position source; absence remains scope-unverified.
        if left.get("ccy") not in (None, ""):
            compare(left, right, "ccy", identity, position_receipt)
    for identity, item in sorted(positions.items()):
        clock(item, "uTime", identity, position_receipt)
        clock(item, "cTime", identity, position_receipt)

    return AccountConsistencyReport(
        account_id=packet.plan.expected_uid,
        settlement_currency=settlement,
        packet_sha256=packet_sha256,
        anchor_receipt_sha256=anchor_receipt.receipt_sha256,
        balance_receipt_sha256=balance_receipt.receipt_sha256,
        position_receipt_sha256s=(position_receipt.receipt_sha256,),
        anchor_observed_at=anchor_at,
        findings=tuple(findings),
        blocking_reasons=tuple(sorted({item.reason for item in findings})),
    )


@_redacted
def reconcile_recorded_account_sources(
    packet: capture.DemoAccountPacket,
    *,
    expected_plan_sha256: str,
    expected_packet_sha256: str,
) -> AccountConsistencyReport:
    """Recompute anchored field checks from the full raw packet and external pins.

    Output contains private inventory identities and receipt hashes. It is not
    automatically persisted or published and is never accepted as authority.
    Empty findings mean only no contradiction in the explicitly checked fields.
    """
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=expected_plan_sha256
    )
    replay = capture.verify_demo_account_packet(
        frozen.payload,
        expected_sha256=expected_packet_sha256,
        expected_plan_sha256=expected_plan_sha256,
    )
    return _reconcile_verified_packet(replay, expected_packet_sha256)
