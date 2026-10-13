"""DENY-only V7 audit of source-row clocks against OKX REST gateway send time.

The original raw account packet and its sealed policies are never modified.
This comparison can disprove a recorded causal ordering; it cannot prove an
authenticated, complete, or current account when no contradiction is found.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_gateway_causality as gateway
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope

POLICY_BYTES = canonical(
    {
        "version": "ctcc.demo_account_row_gateway_causality.v1",
        "source": "exact_original_v7_B1_page_chain",
        "gateway_policy_sha256": gateway.V7_POLICY_SHA256,
        "current_source_policy_sha256": current.V7_POLICY_SHA256,
        "comparison": "each_retained_nonnull_row_source_time_le_rest_gateway_outTime",
        "gateway_outTime": "response_send_unix_microseconds_not_body_completion",
        "missing_gateway_time": "unknown_not_host_receipt_substitution",
        "source_timestamp_after_gateway_response": "blocking_conflict_no_tolerance",
        "account_complete": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)
SCHEMA_VERSION = "ctcc.demo_account_row_gateway_causality_audit.v1"
MAX_RECEIPT_BYTES = 65536
_ISSUER = object()
_FIELDS = frozenset(
    {
        "schema_version",
        "policy_sha256",
        "source_reference",
        "gateway_audit_receipt_sha256",
        "pages",
        "source_timestamp_after_gateway_response_request_indices",
        "blocking_reasons",
        "snapshot",
        "account_complete",
        "source_authenticity_verified",
        "execution_authority",
        "admission",
    }
)


class AccountRowGatewayCausalityError(ValueError):
    """Fixed local error code; source timestamps and private rows stay private."""


def _deny(code: str) -> None:
    raise AccountRowGatewayCausalityError(code)


@dataclass(frozen=True, slots=True, repr=False, init=False)
class AccountRowGatewayCausalityAudit:
    receipt_json: bytes

    def __init__(self, receipt_json: bytes, *, _issuer=None):
        if _issuer is not _ISSUER:
            _deny("account_row_gateway_receipt_invalid")
        object.__setattr__(self, "receipt_json", receipt_json)
        self.__post_init__()

    def __post_init__(self) -> None:
        try:
            raw = self.receipt_json
            if type(raw) is not bytes or not 1 <= len(raw) <= MAX_RECEIPT_BYTES:
                _deny("account_row_gateway_receipt_invalid")
            value = json.loads(raw)
            if (
                type(value) is not dict
                or set(value) != _FIELDS
                or canonical(value) != raw
                or value["schema_version"] != SCHEMA_VERSION
                or value["policy_sha256"] != POLICY_SHA256
                or type(value["source_reference"]) is not dict
                or type(value["gateway_audit_receipt_sha256"]) is not str
                or len(value["gateway_audit_receipt_sha256"]) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in value["gateway_audit_receipt_sha256"]
                )
                or value["snapshot"] is not None
                or value["account_complete"] is not False
                or value["source_authenticity_verified"] is not False
                or value["execution_authority"] is not False
                or value["admission"] != "DENY"
            ):
                _deny("account_row_gateway_receipt_invalid")
            pages = value["pages"]
            if (
                type(pages) is not list
                or not len(capture.V7_CURRENT_STREAMS) <= len(pages) <= 256
                or type(value["blocking_reasons"]) is not list
                or value["blocking_reasons"] != sorted(set(value["blocking_reasons"]))
            ):
                _deny("account_row_gateway_receipt_invalid")
            conflicts = []
            for index, page in enumerate(pages):
                if (
                    type(page) is not dict
                    or set(page)
                    != {
                        "request_index",
                        "page_receipt_sha256",
                        "known_source_timestamp_count",
                        "missing_source_timestamp_count",
                        "source_after_gateway_count",
                        "status",
                    }
                    or type(page["request_index"]) is not int
                    or page["request_index"] != index
                    or type(page["page_receipt_sha256"]) is not str
                    or len(page["page_receipt_sha256"]) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in page["page_receipt_sha256"]
                    )
                    or any(
                        type(page[key]) is not int or not 0 <= page[key] <= 65536
                        for key in (
                            "known_source_timestamp_count",
                            "missing_source_timestamp_count",
                        )
                    )
                    or type(page["source_after_gateway_count"]) is not int
                    or not 0
                    <= page["source_after_gateway_count"]
                    <= page["known_source_timestamp_count"]
                    or page["status"]
                    not in {"gateway_missing", "checked", "source_after_gateway"}
                    or (page["source_after_gateway_count"] > 0)
                    != (page["status"] == "source_after_gateway")
                ):
                    _deny("account_row_gateway_receipt_invalid")
                if page["source_after_gateway_count"]:
                    conflicts.append(index)
            if (
                value["source_timestamp_after_gateway_response_request_indices"]
                != conflicts
            ):
                _deny("account_row_gateway_receipt_invalid")
            if (
                "source_timestamp_after_gateway_response" in value["blocking_reasons"]
            ) != bool(conflicts):
                _deny("account_row_gateway_receipt_invalid")
        except AccountRowGatewayCausalityError:
            raise
        except Exception:  # noqa: BLE001 -- never include private source data
            _deny("account_row_gateway_receipt_invalid")

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def account_complete(self) -> bool:
        return False


def audit_v7_account_row_gateway_causality(
    *,
    chain,
    reference,
    scope,
    validated_at,
    expected_policy_sha256=POLICY_SHA256,
) -> AccountRowGatewayCausalityAudit:
    """Replay exact B1 and expose impossible row/gateway ordering only."""
    if (
        type(chain) is not tuple
        or type(reference) is not observed.CaptureReference
        or type(scope) is not LedgerScope
        or type(validated_at) is not datetime
        or expected_policy_sha256 != POLICY_SHA256
        or type(expected_policy_sha256) is not str
    ):
        _deny("account_row_gateway_inputs_invalid")
    try:
        gateway_audit = gateway.audit_recorded_demo_account_gateway_causality(
            chain=chain,
            reference=reference,
            scope=scope,
            validated_at=validated_at,
            expected_policy_sha256=gateway.V7_POLICY_SHA256,
        )
        packet = current._verify_current_only_chain(
            chain, reference, scope, policy=current.V7_POLICY_SHA256
        )
        pages = []
        conflicts = []
        for index, page in enumerate(packet.observations):
            envelope = json.loads(page.response_body)
            out_raw = envelope.get("outTime")
            gateway_out = None if out_raw is None else capture._gateway_time(out_raw)
            stamps = (stamp.value for row in page.rows for stamp in row.source_times)
            known = missing = after = 0
            for stamp in stamps:
                if stamp is None:
                    missing += 1
                else:
                    known += 1
                    if gateway_out is not None and stamp > gateway_out:
                        after += 1
            if gateway_out is None:
                status = "gateway_missing"
            elif after:
                status = "source_after_gateway"
                conflicts.append(index)
            else:
                status = "checked"
            pages.append(
                {
                    "request_index": index,
                    "page_receipt_sha256": page.receipt_sha256,
                    "known_source_timestamp_count": known,
                    "missing_source_timestamp_count": missing,
                    "source_after_gateway_count": after,
                    "status": status,
                }
            )
        prior = json.loads(gateway_audit.receipt_json)
        result = canonical(
            {
                "schema_version": SCHEMA_VERSION,
                "policy_sha256": POLICY_SHA256,
                "source_reference": observed.reference_document(reference),
                "gateway_audit_receipt_sha256": gateway_audit.receipt_sha256,
                "pages": pages,
                "source_timestamp_after_gateway_response_request_indices": conflicts,
                "blocking_reasons": sorted(
                    set(prior["blocking_reasons"])
                    | (
                        {"source_timestamp_after_gateway_response"}
                        if conflicts
                        else set()
                    )
                ),
                "snapshot": None,
                "account_complete": False,
                "source_authenticity_verified": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
        return AccountRowGatewayCausalityAudit(result, _issuer=_ISSUER)
    except AccountRowGatewayCausalityError:
        raise
    except Exception:  # noqa: BLE001 -- foreign raw/account details cannot escape
        _deny("account_row_gateway_audit_invalid")


def verify_v7_account_row_gateway_causality(
    receipt: AccountRowGatewayCausalityAudit,
    *,
    chain,
    reference,
    scope,
    validated_at,
    expected_receipt_sha256: str,
) -> AccountRowGatewayCausalityAudit:
    """Independent caller pin plus full source replay, not a self-signed receipt."""
    if (
        type(receipt) is not AccountRowGatewayCausalityAudit
        or type(expected_receipt_sha256) is not str
        or len(expected_receipt_sha256) != 64
        or any(
            character not in "0123456789abcdef" for character in expected_receipt_sha256
        )
    ):
        _deny("account_row_gateway_readback_invalid")
    replay = audit_v7_account_row_gateway_causality(
        chain=chain, reference=reference, scope=scope, validated_at=validated_at
    )
    if (
        receipt.receipt_json != replay.receipt_json
        or receipt.receipt_sha256 != expected_receipt_sha256
    ):
        _deny("account_row_gateway_readback_mismatch")
    return replay
