"""Read-only exchange gateway-time coverage for pinned V6/V7 Demo chains.

An OKX envelope may omit its microsecond ``inTime``/``outTime`` fields.  The
original capture preserves that absence, so this diagnostic does not reinterpret
an HTTP response or a local receive time as an exchange processing timestamp.
It cannot establish account completeness, source authenticity or risk authority.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope

POLICY_BYTES = canonical(
    {
        "version": "ctcc.demo_account_gateway_causality.v1",
        "source": "externally_pinned_original_v6_B1_journal_and_full_page_replay",
        "gateway_timestamp_unit": "unix_microseconds",
        "causal_order": "request_start_le_gateway_in_le_gateway_out_le_headers_receipt",
        "missing_gateway_time": "incomplete_never_local_time_substitution",
        "account_complete": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)
V7_POLICY_BYTES = canonical(
    {
        "version": "ctcc.demo_account_gateway_causality.v2",
        "source": "externally_pinned_original_v7_B1_journal_and_full_page_replay",
        "current_source_policy_sha256": current.V7_POLICY_SHA256,
        "current_streams": list(capture.V7_CURRENT_STREAMS),
        "algo_order_types": list(capture.CURRENT_ALGO_ORDER_TYPES_V7),
        "gateway_timestamp_unit": "unix_microseconds",
        "causal_order": "request_start_le_gateway_in_le_gateway_out_le_headers_receipt",
        "missing_gateway_time": "incomplete_never_local_time_substitution",
        "account_complete": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
    }
)
V7_POLICY_SHA256 = sha(V7_POLICY_BYTES)
MAX_RECEIPT_BYTES = 524288
MAX_PAGES = 256
_ISSUER = object()
_HEX = re.compile(r"[a-f0-9]+\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{0,95}\Z")
_RECEIPT_FIELDS = {
    "schema_version",
    "policy_sha256",
    "source_reference",
    "current_source_receipt_sha256",
    "declared_validation_at",
    "pages",
    "gateway_causal_coverage_complete",
    "missing_gateway_time_request_indices",
    "current_source_blocking_reasons",
    "blocking_reasons",
    "snapshot",
    "account_complete",
    "source_authenticity_verified",
    "execution_authority",
    "admission",
}
_PAGE_FIELDS = {
    "request_index",
    "stream",
    "page_index",
    "page_receipt_sha256",
    "raw_body_sha256",
    "request_started_at",
    "headers_received_at",
    "gateway_in_time_raw",
    "gateway_out_time_raw",
    "gateway_causal_window_verified",
}


class AccountGatewayCausalityError(ValueError):
    """A fixed denial code; private source rows never enter exception text."""


def _invalid():
    raise AccountGatewayCausalityError("account_gateway_receipt_invalid")


def _digest(value, length=64):
    return type(value) is str and len(value) == length and _HEX.fullmatch(value)


def _time(value):
    if type(value) is not str or len(value) > 40:
        _invalid()
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is not UTC or stamp.isoformat() != value:
        _invalid()
    return stamp


def _reasons(value):
    if (
        type(value) is not list
        or len(value) > 64
        or any(
            type(item) is not str or _REASON.fullmatch(item) is None for item in value
        )
        or value != sorted(set(value))
    ):
        _invalid()
    return set(value)


@dataclass(frozen=True, slots=True, repr=False, init=False)
class AccountGatewayCausalityAudit:
    receipt_json: bytes

    def __init__(self, receipt_json: bytes, *, _issuer=None):
        # A coherent hash-only receipt is still not exchange authenticity.  The
        # fence prevents ordinary callers from minting this diagnostic object;
        # the fixed DENY fields remain the actual authority boundary.
        if _issuer is not _ISSUER:
            _invalid()
        object.__setattr__(self, "receipt_json", receipt_json)
        self.__post_init__()

    def __post_init__(self):
        try:
            raw = self.receipt_json
            if type(raw) is not bytes or not 1 <= len(raw) <= MAX_RECEIPT_BYTES:
                _invalid()
            value = json.loads(raw)
            if type(value) is not dict or set(value) != _RECEIPT_FIELDS:
                _invalid()
            if canonical(value) != raw:
                _invalid()
            contract = {
                ("ctcc.demo_account_gateway_causality_audit.v1", POLICY_SHA256): (
                    capture.V6_CURRENT_STREAMS
                ),
                ("ctcc.demo_account_gateway_causality_audit.v2", V7_POLICY_SHA256): (
                    capture.V7_CURRENT_STREAMS
                ),
            }.get((value["schema_version"], value["policy_sha256"]))
            if (
                contract is None
                or not _digest(value["current_source_receipt_sha256"])
                or value["snapshot"] is not None
                or value["account_complete"] is not False
                or value["source_authenticity_verified"] is not False
                or value["execution_authority"] is not False
                or value["admission"] != "DENY"
            ):
                _invalid()
            reference = value["source_reference"]
            if (
                type(reference) is not dict
                or set(reference)
                != {
                    "capture_id",
                    "head_sha256",
                    "plan_sha256",
                    "packet_sha256",
                    "session_binding_sha256",
                }
                or not _digest(reference["capture_id"], 32)
                or any(
                    not _digest(reference[key])
                    for key in (
                        "head_sha256",
                        "plan_sha256",
                        "packet_sha256",
                        "session_binding_sha256",
                    )
                )
            ):
                _invalid()
            validated_at = _time(value["declared_validation_at"])
            pages = value["pages"]
            if type(pages) is not list or not len(contract) <= len(pages) <= MAX_PAGES:
                _invalid()
            counts = {stream: 0 for stream in contract}
            missing = []
            last_stream = -1
            previous_headers = None
            for index, page in enumerate(pages):
                if type(page) is not dict or set(page) != _PAGE_FIELDS:
                    _invalid()
                stream = page["stream"]
                if type(stream) is not str or stream not in counts:
                    _invalid()
                stream_index = contract.index(stream)
                if (
                    type(page["request_index"]) is not int
                    or page["request_index"] != index
                    or type(page["page_index"]) is not int
                    or page["page_index"] != counts[stream]
                    or page["page_index"] >= 64
                    or stream_index < last_stream
                    or not _digest(page["page_receipt_sha256"])
                    or not _digest(page["raw_body_sha256"])
                    or type(page["gateway_causal_window_verified"]) is not bool
                ):
                    _invalid()
                counts[stream] += 1
                last_stream = stream_index
                started = _time(page["request_started_at"])
                headers = _time(page["headers_received_at"])
                if (
                    not started <= headers <= validated_at
                    or previous_headers is not None
                    and started < previous_headers
                ):
                    _invalid()
                previous_headers = headers
                gateway_in = page["gateway_in_time_raw"]
                gateway_out = page["gateway_out_time_raw"]
                present = gateway_in is not None and gateway_out is not None
                if (gateway_in is None) != (gateway_out is None):
                    _invalid()
                if present:
                    if not (
                        started
                        <= capture._gateway_time(gateway_in)
                        <= capture._gateway_time(gateway_out)
                        <= headers
                    ):
                        _invalid()
                else:
                    missing.append(index)
                if page["gateway_causal_window_verified"] is not present:
                    _invalid()
            if any(count == 0 for count in counts.values()):
                _invalid()
            if (
                type(value["missing_gateway_time_request_indices"]) is not list
                or any(
                    type(index) is not int
                    for index in value["missing_gateway_time_request_indices"]
                )
                or value["missing_gateway_time_request_indices"] != missing
                or type(value["gateway_causal_coverage_complete"]) is not bool
                or value["gateway_causal_coverage_complete"] is not (not missing)
            ):
                _invalid()
            current_reasons = _reasons(value["current_source_blocking_reasons"])
            reasons = _reasons(value["blocking_reasons"])
            expected = current_reasons | {
                "source_authenticity_unverified",
                "account_revision_unverified",
            }
            if missing:
                expected.add("exchange_gateway_time_missing")
            if reasons != expected:
                _invalid()
        except AccountGatewayCausalityError:
            raise
        except Exception:  # noqa: BLE001 -- no private receipt bytes in the error
            _invalid()

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def snapshot(self):
        return None

    @property
    def account_complete(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False


def audit_recorded_demo_account_gateway_causality(
    *,
    chain,
    reference,
    scope,
    validated_at,
    expected_policy_sha256=POLICY_SHA256,
) -> AccountGatewayCausalityAudit:
    """Replay original B1 evidence and disclose missing exchange time per page.

    This pure check takes a declared validation cutoff.  A separate owned native
    clock, exact credential/region proof, history and local risk state are still
    necessary before a current account claim can be made.
    """
    try:
        if (
            type(chain) is not tuple
            or not chain
            or type(reference) is not observed.CaptureReference
            or type(scope) is not LedgerScope
            or type(validated_at) is not datetime
            or type(expected_policy_sha256) is not str
            or expected_policy_sha256 not in {POLICY_SHA256, V7_POLICY_SHA256}
        ):
            raise AccountGatewayCausalityError("account_gateway_audit_inputs_invalid")
        v7 = expected_policy_sha256 == V7_POLICY_SHA256
        source_policy = current.V7_POLICY_SHA256 if v7 else current.V6_POLICY_SHA256
        expected_plan = (
            capture.CurrentDemoAccountCapturePlanV7
            if v7
            else capture.CurrentDemoAccountCapturePlanV6
        )
        source = current.verify_current_account_sources(
            chain,
            reference=reference,
            scope=scope,
            validated_at=validated_at,
            expected_policy_sha256=source_policy,
        )
        packet = current._verify_current_only_chain(
            chain, reference, scope, policy=source_policy
        )
        if type(packet.plan) is not expected_plan:
            raise AccountGatewayCausalityError(
                "account_gateway_v7_source_required"
                if v7
                else "account_gateway_v6_source_required"
            )
        pages = []
        missing = []
        for index, page in enumerate(packet.observations):
            payload = json.loads(page.response_body)
            gateway_in = payload.get("inTime")
            gateway_out = payload.get("outTime")
            present = gateway_in is not None and gateway_out is not None
            # The original parser has already rejected a partial pair, invalid
            # microseconds, reversal or timestamps outside this request/receipt.
            if not present:
                missing.append(index)
            pages.append(
                {
                    "request_index": index,
                    "stream": page.request.stream,
                    "page_index": page.page_index,
                    "page_receipt_sha256": page.receipt_sha256,
                    "raw_body_sha256": page.body_sha256,
                    "request_started_at": page.request_started_at.isoformat(),
                    "headers_received_at": page.headers_received_at.isoformat(),
                    "gateway_in_time_raw": gateway_in,
                    "gateway_out_time_raw": gateway_out,
                    "gateway_causal_window_verified": present,
                }
            )
        current_receipt = json.loads(source.receipt_json)
        result = canonical(
            {
                "schema_version": (
                    "ctcc.demo_account_gateway_causality_audit.v2"
                    if v7
                    else "ctcc.demo_account_gateway_causality_audit.v1"
                ),
                "policy_sha256": expected_policy_sha256,
                "source_reference": observed.reference_document(reference),
                "current_source_receipt_sha256": source.receipt_sha256,
                "declared_validation_at": capture._utc(validated_at).isoformat(),
                "pages": pages,
                "gateway_causal_coverage_complete": not missing,
                "missing_gateway_time_request_indices": missing,
                "current_source_blocking_reasons": current_receipt["blocking_reasons"],
                "blocking_reasons": sorted(
                    {"source_authenticity_unverified", "account_revision_unverified"}
                    | ({"exchange_gateway_time_missing"} if missing else set())
                    | set(current_receipt["blocking_reasons"])
                ),
                "snapshot": None,
                "account_complete": False,
                "source_authenticity_verified": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
        return AccountGatewayCausalityAudit(result, _issuer=_ISSUER)
    except AccountGatewayCausalityError:
        raise
    except Exception:  # noqa: BLE001 -- no private source content in an error
        raise AccountGatewayCausalityError("account_gateway_audit_invalid") from None
