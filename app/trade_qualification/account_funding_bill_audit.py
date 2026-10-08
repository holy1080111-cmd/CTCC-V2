"""Pinned B1 account-bill funding candidates, never funding accrual or risk authority.

OKX Trading Account bill type/subtype semantics are endpoint-specific.  A bill
``ts`` is a balance-update/record-generation time, not a proven settlement or
accrual time.  This additive diagnostic replays the original V5 page chain and
leaves historical cashflow completeness and source authenticity unverified.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_query_verifier as history
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.demo_account_funding_bill_candidate_policy.v1",
        "source": "externally_pinned_original_global_v5_B1_account_bill_pages",
        "account_endpoints": [
            "/api/v5/account/bills",
            "/api/v5/account/bills-archive",
        ],
        "asset_bill_semantics": "distinct_never_reused",
        "funding_type": "8",
        "funding_subtypes": {"expense": "173", "income": "174"},
        "candidate_payment_field": "pnl",
        "bill_time": "source_balance_update_not_funding_accrual",
        "account_complete": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
        "admission": "DENY",
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)
CONSISTENCY_POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.demo_account_funding_bill_consistency_policy.v2",
        "predecessor_policy_sha256": POLICY_SHA256,
        "source": "replayed_original_v5_B1_account_bill_pages",
        "comparison": "exact_decimal_pnl_balChg_and_fee_fields_without_accrual_inference",
        "empty_candidates": "unknown_not_zero_funding",
        "numeric_agreement": "observed_source_field_agreement_not_cashflow_attribution",
        "account_complete": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
        "admission": "DENY",
    }
)
CONSISTENCY_POLICY_SHA256 = journal.digest(CONSISTENCY_POLICY_BYTES)
_ENDPOINTS = {
    "bills_recent": "/api/v5/account/bills",
    "bills_archive": "/api/v5/account/bills-archive",
}
_ISSUER = object()


class FundingBillAuditError(ValueError):
    """Fixed source-safe denial code; original account rows stay private."""


def _deny(code="funding_bill_source_invalid"):
    raise FundingBillAuditError(code)


@dataclass(frozen=True, slots=True, repr=False, init=False)
class FundingBillAudit:
    receipt_json: bytes

    def __init__(self, receipt_json: bytes, *, _issuer=None):
        # This ordinary Python construction fence is not exchange authenticity.
        if _issuer is not _ISSUER:
            _deny("funding_bill_receipt_invalid")
        object.__setattr__(self, "receipt_json", receipt_json)
        self.__post_init__()

    def __post_init__(self):
        raw = self.receipt_json
        if type(raw) is not bytes or not 1 <= len(raw) <= observed.MAX_RECEIPT_BYTES:
            _deny("funding_bill_receipt_invalid")
        try:
            value = json.loads(raw)
            if (
                type(value) is not dict
                or journal.canonical(value) != raw
                or value.get("schema_version")
                != "ctcc.demo_account_funding_bill_candidate_audit.v1"
                or value.get("policy_sha256") != POLICY_SHA256
                or value.get("funding_accrual_at") is not None
                or value.get("net_funding_cashflow") is not None
                or value.get("account_complete") is not False
                or value.get("source_authenticity_verified") is not False
                or value.get("execution_authority") is not False
                or value.get("admission") != "DENY"
            ):
                _deny("funding_bill_receipt_invalid")
        except (TypeError, ValueError, KeyError):
            _deny("funding_bill_receipt_invalid")

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def account_complete(self):
        return False

    @property
    def execution_authority(self):
        return False

    @property
    def admission(self):
        return "DENY"


@dataclass(frozen=True, slots=True, repr=False, init=False)
class FundingBillConsistency:
    """Source-field comparisons only; even exact agreement grants no authority."""

    receipt_json: bytes

    def __init__(self, receipt_json: bytes, *, _issuer=None):
        if _issuer is not _ISSUER:
            _deny("funding_bill_consistency_receipt_invalid")
        object.__setattr__(self, "receipt_json", receipt_json)
        self.__post_init__()

    def __post_init__(self):
        raw = self.receipt_json
        if type(raw) is not bytes or not 1 <= len(raw) <= observed.MAX_RECEIPT_BYTES:
            _deny("funding_bill_consistency_receipt_invalid")
        try:
            value = json.loads(raw)
            if (
                type(value) is not dict
                or journal.canonical(value) != raw
                or value.get("schema_version")
                != "ctcc.demo_account_funding_bill_consistency.v2"
                or value.get("policy_sha256") != CONSISTENCY_POLICY_SHA256
                or value.get("funding_accrual_at") is not None
                or value.get("net_funding_cashflow") is not None
                or value.get("account_complete") is not False
                or value.get("source_authenticity_verified") is not False
                or value.get("execution_authority") is not False
                or value.get("admission") != "DENY"
            ):
                _deny("funding_bill_consistency_receipt_invalid")
        except (TypeError, ValueError, KeyError):
            _deny("funding_bill_consistency_receipt_invalid")

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def account_complete(self):
        return False

    @property
    def execution_authority(self):
        return False

    @property
    def admission(self):
        return "DENY"


def _bounded_decimal(value):
    if type(value) is not str or not value or len(value) > 128:
        return None
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    if (
        not result.is_finite()
        or abs(result.as_tuple().exponent) > 100
        or len(result.as_tuple().digits) > 100
    ):
        return None
    return result


def audit_funding_bill_consistency(chain, *, reference, scope):
    """Reopen original pages and compare candidate source fields exactly.

    A matching balance change is only an observation about two fields in one
    record. It cannot establish settlement time, a complete cashflow or loss.
    """
    try:
        return _audit_consistency(chain, reference, scope)
    except FundingBillAuditError:
        raise
    except Exception:  # noqa: BLE001 -- redact private source failures
        raise FundingBillAuditError("funding_bill_consistency_source_invalid") from None


def verify_funding_bill_consistency(chain, *, reference, scope, expected_receipt_json):
    if (
        type(expected_receipt_json) is not bytes
        or not 1 <= len(expected_receipt_json) <= observed.MAX_RECEIPT_BYTES
    ):
        _deny("funding_bill_consistency_receipt_mismatch")
    replay = audit_funding_bill_consistency(chain, reference=reference, scope=scope)
    if replay.receipt_json != expected_receipt_json:
        _deny("funding_bill_consistency_receipt_mismatch")
    return replay


def _audit_consistency(chain, reference, scope):
    prior = audit_funding_bills(chain, reference=reference, scope=scope)
    prior_value = json.loads(prior.receipt_json)
    payloads = [
        item.event.packet_payload
        for item in chain
        if item.event.packet_payload is not None
    ]
    if len(payloads) != 1:
        _deny("funding_bill_consistency_source_invalid")
    packet = capture.verify_demo_account_packet(
        payloads[0],
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )
    observations = []
    blockers = set(prior_value["blocking_reasons"])
    for entry in prior_value["account_bill_rows"]:
        if entry["classification"] != "account_funding_payment_candidate":
            continue
        locator = entry["locators"][0]
        page = packet.observations[locator["request_index"]]
        row = page.rows[locator["row_ordinal"]]
        if (
            page.request.stream not in _ENDPOINTS
            or journal.digest(row.canonical_json.encode("utf-8")) != entry["row_sha256"]
        ):
            _deny("funding_bill_consistency_source_invalid")
        raw = json.loads(row.canonical_json)
        payment = _bounded_decimal(raw.get("pnl"))
        balance_change = _bounded_decimal(raw.get("balChg"))
        fee = _bounded_decimal(raw.get("fee"))
        agreement = (
            None
            if payment is None or balance_change is None
            else payment == balance_change
        )
        fee_zero = None if fee is None else fee == 0
        if agreement is not True:
            blockers.add("funding_bill_balance_change_payment_unverified")
        if fee_zero is not True:
            blockers.add("funding_bill_fee_field_requires_attribution")
        observations.append(
            {
                "identity_sha256": entry["identity_sha256"],
                "row_sha256": entry["row_sha256"],
                "source_locators": entry["locators"],
                "payment_balance_change_numeric_agreement": agreement,
                "fee_field_observed_zero": fee_zero,
                "effective_accrual_at": None,
            }
        )
    receipt = journal.canonical(
        {
            "schema_version": "ctcc.demo_account_funding_bill_consistency.v2",
            "policy_sha256": CONSISTENCY_POLICY_SHA256,
            "source_reference": prior_value["source_reference"],
            "funding_bill_audit_receipt_sha256": prior.receipt_sha256,
            "candidate_field_observations": observations,
            "funding_candidates_observed": len(observations),
            "blocking_reasons": sorted(blockers),
            "funding_accrual_at": None,
            "net_funding_cashflow": None,
            "account_complete": False,
            "source_authenticity_verified": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    return FundingBillConsistency(receipt, _issuer=_ISSUER)


def _payment_sign(value):
    if type(value) is not str or not value or len(value) > 128:
        return "unknown"
    try:
        number = Decimal(value)
    except InvalidOperation:
        return "unknown"
    if not number.is_finite():
        return "unknown"
    if number < 0:
        return "negative"
    if number > 0:
        return "positive"
    return "zero"


def _classify_account_bill(endpoint, raw, settlement_currency):
    """Classification is never a cashflow/accrual measurement or authority."""
    if endpoint not in _ENDPOINTS.values():
        _deny("funding_bill_account_endpoint_required")
    if type(raw) is not dict:
        _deny()
    bill_type, subtype = raw.get("type"), raw.get("subType")
    funding_pair = bill_type == "8" and subtype in {"173", "174"}
    if not funding_pair:
        if bill_type == "8" or subtype in {"173", "174"}:
            return "funding_type_subtype_conflict", "unknown"
        return "other_account_movement", "unknown"
    sign = _payment_sign(raw.get("pnl"))
    expected_sign = "negative" if subtype == "173" else "positive"
    if (
        raw.get("ccy") != settlement_currency
        or raw.get("instType") != "SWAP"
        or type(raw.get("instId")) is not str
        or not raw["instId"].endswith(f"-{settlement_currency}-SWAP")
        or sign != expected_sign
    ):
        return "funding_payment_fields_unverified", sign
    return "account_funding_payment_candidate", sign


def audit_funding_bills(chain, *, reference, scope):
    """Recompute from pinned original B1 readbacks; never accept caller claim DTOs."""
    try:
        return _audit(chain, reference, scope)
    except FundingBillAuditError:
        raise
    except Exception:  # noqa: BLE001, S110 -- private source errors stay private
        pass
    _deny()


def verify_funding_bill_audit(chain, *, reference, scope, expected_receipt_json):
    """Require exact recomputation against original B1 bytes, not a hash claim.

    This rejects forged classifications and locators even when fixed DENY fields
    remain intact. It does not authenticate the original exchange transport.
    """
    if (
        type(expected_receipt_json) is not bytes
        or not 1 <= len(expected_receipt_json) <= observed.MAX_RECEIPT_BYTES
    ):
        _deny("funding_bill_receipt_mismatch")
    replay = audit_funding_bills(chain, reference=reference, scope=scope)
    if replay.receipt_json != expected_receipt_json:
        _deny("funding_bill_receipt_mismatch")
    return replay


def _audit(chain, reference, scope):
    checked_bootstrap(scope, LedgerScope)
    observed.reference_document(reference)
    if (
        type(chain) is not tuple
        or not 1 <= len(chain) <= journal.MAX_EVENTS
        or observed.source_reference(chain) != reference
    ):
        _deny()
    source = history.verify_history_query_chain(
        chain, **observed._pins(reference, scope)
    )
    history_receipt = json.loads(source.receipt_json)
    packet_payloads = [
        item.event.packet_payload
        for item in chain
        if item.event.packet_payload is not None
    ]
    if len(packet_payloads) != 1:
        _deny()
    packet = capture.verify_demo_account_packet(
        packet_payloads[0],
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )
    if (
        type(packet.plan) is not capture.AllProductDemoAccountCapturePlan
        or packet.plan.registration_region != "global"
        or packet.plan.expected_uid != scope.account_id
        or packet.plan.settlement_currency != scope.settlement_currency
    ):
        _deny()
    indexed = {
        item["identity_sha256"]: item
        for item in history_receipt["rows"]
        if item["family"] == "bills"
    }
    entries = {}
    for request_index, page in enumerate(packet.observations):
        stream = page.request.stream
        if stream not in _ENDPOINTS:
            continue
        if page.request.endpoint != _ENDPOINTS[stream]:
            _deny("funding_bill_account_endpoint_required")
        for ordinal, row in enumerate(page.rows):
            identity = journal.digest(journal.canonical(("bills", row.row_id)))
            row_sha = journal.digest(row.canonical_json.encode("utf-8"))
            matched = indexed.get(identity)
            if matched is None or matched["row_sha256"] != row_sha:
                _deny()
            matching_locators = [
                item
                for item in matched["locators"]
                if item["request_index"] == request_index
                and item["row_ordinal"] == ordinal
            ]
            if len(matching_locators) != 1:
                _deny()
            locator = {
                "request_index": request_index,
                "row_ordinal": ordinal,
                "raw_event_sequence": matching_locators[0]["raw_event_sequence"],
                "page_receipt_sha256": page.receipt_sha256,
                "raw_sha256": journal.digest(page.response_body),
                "stream": stream,
            }
            if locator != matching_locators[0]:
                _deny()
            if identity in entries:
                continue
            raw = json.loads(row.canonical_json)
            kind, sign = _classify_account_bill(
                page.request.endpoint, raw, scope.settlement_currency
            )
            times = {item.path: item.value for item in row.source_times}
            if (
                times.get("ts") is None
                or times["ts"].isoformat() != matched["generation_at"]
            ):
                _deny()
            entries[identity] = {
                "identity_sha256": identity,
                "row_sha256": row_sha,
                "locators": matched["locators"],
                "classification": kind,
                "account_bill_type": raw.get("type"),
                "account_bill_subtype": raw.get("subType"),
                "payment_field": "pnl"
                if kind == "account_funding_payment_candidate"
                else None,
                "payment_sign": sign,
                "source_balance_update_at": matched["generation_at"],
                "effective_accrual_at": None,
            }
    if set(entries) != set(indexed):
        _deny()
    queries = [
        item for item in history_receipt["queries"] if item["stream"] in _ENDPOINTS
    ]
    if {item["stream"] for item in queries} != set(_ENDPOINTS):
        _deny()
    classifications = {entry["classification"] for entry in entries.values()}
    blocking = {
        "funding_accrual_provenance_missing",
        "complete_account_cashflow_history_unproven",
        "source_authenticity_unverified",
    }
    if (
        "funding_type_subtype_conflict" in classifications
        or "funding_payment_fields_unverified" in classifications
    ):
        blocking.add("funding_bill_semantics_unverified")
    result = {
        "schema_version": "ctcc.demo_account_funding_bill_candidate_audit.v1",
        "policy_sha256": POLICY_SHA256,
        "source_reference": observed.reference_document(reference),
        "history_query_receipt_sha256": source.receipt_sha256,
        "requested_generation_start": history_receipt["requested_start"],
        "requested_generation_end": history_receipt["requested_end"],
        "account_bill_queries": queries,
        "account_bill_rows": list(entries.values()),
        "blocking_reasons": sorted(blocking),
        "funding_accrual_at": None,
        "net_funding_cashflow": None,
        "account_complete": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    return FundingBillAudit(journal.canonical(result), _issuer=_ISSUER)
