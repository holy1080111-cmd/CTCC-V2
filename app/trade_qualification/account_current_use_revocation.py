"""Versioned DENY-only current-use interpretation of historical V7 account reads.

The original B1 page chain, V7 source receipt, and native proof stay replayable.
This receipt records the later gateway/source-row audit without upgrading an old
``observed_flat`` diagnostic into a current flat-start qualification.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_row_gateway_causality as causal
from app.trade_qualification.reservations import LedgerScope

POLICY_BYTES = canonical(
    {
        "version": "ctcc.demo_v7_current_use_revocation.v1",
        "historical_current_policy_sha256": current.V7_POLICY_SHA256,
        "row_gateway_policy_sha256": causal.POLICY_SHA256,
        "source": "exact_original_v7_B1_page_chain",
        "gateway_time": "REST_inTime_and_outTime_unix_microseconds_required_per_page",
        "source_time": "ordered_only_if_all_gateway_pairs_present_and_every_known_row_time_le_same_page_outTime",
        "missing_gateway_time": "incomplete_never_host_receipt_substitution",
        "historical_v7_flat": "replayable_but_revoked_for_current_use",
        "current_flat_qualified": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)
SCHEMA_VERSION = "ctcc.demo_v7_current_use_revocation.v1"
_ISSUER = object()
_FIELDS = frozenset(
    {
        "schema_version",
        "policy_sha256",
        "original_source_reference_sha256",
        "historical_current_receipt_sha256",
        "row_gateway_audit_receipt_sha256",
        "page_count",
        "gateway_causal_coverage_complete",
        "known_source_row_times_ordered",
        "blocking_reasons",
        "current_flat_qualified",
        "flat_start_permission",
        "account_complete",
        "source_authenticity_verified",
        "execution_authority",
        "admission",
    }
)


class AccountCurrentUseRevocationError(ValueError):
    """Fixed local rejection; no private source values enter errors."""


def _deny(code: str) -> None:
    raise AccountCurrentUseRevocationError(code)


@dataclass(frozen=True, slots=True, repr=False, init=False)
class V7CurrentUseRevocation:
    receipt_json: bytes

    def __init__(self, receipt_json: bytes, *, _issuer=None):
        if _issuer is not _ISSUER:
            _deny("account_current_use_receipt_invalid")
        object.__setattr__(self, "receipt_json", receipt_json)
        self._check()

    def _check(self) -> None:
        try:
            raw = self.receipt_json
            value = json.loads(raw)
            if (
                type(raw) is not bytes
                or not 1 <= len(raw) <= 8192
                or canonical(value) != raw
                or type(value) is not dict
                or set(value) != _FIELDS
                or value["schema_version"] != SCHEMA_VERSION
                or value["policy_sha256"] != POLICY_SHA256
                or any(
                    type(value[name]) is not str
                    or len(value[name]) != 64
                    or any(ch not in "0123456789abcdef" for ch in value[name])
                    for name in (
                        "original_source_reference_sha256",
                        "historical_current_receipt_sha256",
                        "row_gateway_audit_receipt_sha256",
                    )
                )
                or type(value["page_count"]) is not int
                or not 1 <= value["page_count"] <= 256
                or type(value["gateway_causal_coverage_complete"]) is not bool
                or type(value["known_source_row_times_ordered"]) is not bool
                or (
                    value["known_source_row_times_ordered"]
                    and not value["gateway_causal_coverage_complete"]
                )
                or type(value["blocking_reasons"]) is not list
                or value["blocking_reasons"] != sorted(set(value["blocking_reasons"]))
                or value["gateway_causal_coverage_complete"]
                == ("exchange_gateway_time_missing" in value["blocking_reasons"])
                or (
                    value["known_source_row_times_ordered"]
                    and "source_timestamp_after_gateway_response"
                    in value["blocking_reasons"]
                )
                or "v7_current_flat_policy_revoked" not in value["blocking_reasons"]
                or value["current_flat_qualified"] is not False
                or value["flat_start_permission"] is not False
                or value["account_complete"] is not False
                or value["source_authenticity_verified"] is not False
                or value["execution_authority"] is not False
                or value["admission"] != "DENY"
            ):
                _deny("account_current_use_receipt_invalid")
        except AccountCurrentUseRevocationError:
            raise
        except Exception:  # noqa: BLE001 -- do not expose source or parser details
            _deny("account_current_use_receipt_invalid")

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def current_flat_qualified(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False


def audit_v7_current_use_revocation(
    *,
    chain,
    reference,
    scope,
    validated_at,
    expected_policy_sha256=POLICY_SHA256,
) -> V7CurrentUseRevocation:
    """Replay original V7 pages; every outcome remains current-use DENY."""
    if (
        type(chain) is not tuple
        or type(reference) is not observed.CaptureReference
        or type(scope) is not LedgerScope
        or type(validated_at) is not datetime
        or type(expected_policy_sha256) is not str
        or expected_policy_sha256 != POLICY_SHA256
    ):
        _deny("account_current_use_inputs_invalid")
    try:
        row_audit = causal.audit_v7_account_row_gateway_causality(
            chain=chain,
            reference=reference,
            scope=scope,
            validated_at=validated_at,
            expected_policy_sha256=causal.POLICY_SHA256,
        )
        audit = json.loads(row_audit.receipt_json)
        missing_page = any(
            page["status"] == "gateway_missing" for page in audit["pages"]
        )
        if missing_page != (
            "exchange_gateway_time_missing" in audit["blocking_reasons"]
        ):
            _deny("account_current_use_gateway_status_inconsistent")
        gateway_complete = not missing_page
        # No outTime means the row comparison was not performed for that page.
        # An absence of observed conflicts cannot certify causal ordering.
        row_times_ordered = (
            gateway_complete
            and not audit["source_timestamp_after_gateway_response_request_indices"]
        )
        # The row audit replays the original V7 source verifier and binds its
        # receipt through the gateway audit. This second exact replay obtains
        # the historical receipt hash without accepting caller text.
        historical = current.verify_current_account_sources(
            chain,
            reference=reference,
            scope=scope,
            validated_at=validated_at,
            expected_policy_sha256=current.V7_POLICY_SHA256,
        )
        receipt = canonical(
            {
                "schema_version": SCHEMA_VERSION,
                "policy_sha256": POLICY_SHA256,
                "original_source_reference_sha256": sha(
                    canonical(observed.reference_document(reference))
                ),
                "historical_current_receipt_sha256": historical.receipt_sha256,
                "row_gateway_audit_receipt_sha256": row_audit.receipt_sha256,
                "page_count": len(audit["pages"]),
                "gateway_causal_coverage_complete": gateway_complete,
                "known_source_row_times_ordered": row_times_ordered,
                "blocking_reasons": sorted(
                    set(audit["blocking_reasons"]) | {"v7_current_flat_policy_revoked"}
                ),
                "current_flat_qualified": False,
                "flat_start_permission": False,
                "account_complete": False,
                "source_authenticity_verified": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
        return V7CurrentUseRevocation(receipt, _issuer=_ISSUER)
    except AccountCurrentUseRevocationError:
        raise
    except Exception:  # noqa: BLE001 -- never expose private source values
        _deny("account_current_use_audit_invalid")


def verify_v7_current_use_revocation(
    receipt: V7CurrentUseRevocation,
    *,
    chain,
    reference,
    scope,
    validated_at,
    expected_receipt_sha256: str,
) -> V7CurrentUseRevocation:
    """External SHA pin and full original-chain replay, with no current lease."""
    if (
        type(receipt) is not V7CurrentUseRevocation
        or type(expected_receipt_sha256) is not str
        or len(expected_receipt_sha256) != 64
    ):
        _deny("account_current_use_readback_invalid")
    receipt._check()
    replay = audit_v7_current_use_revocation(
        chain=chain, reference=reference, scope=scope, validated_at=validated_at
    )
    if (
        receipt.receipt_sha256 != expected_receipt_sha256
        or receipt.receipt_json != replay.receipt_json
    ):
        _deny("account_current_use_readback_mismatch")
    return replay
