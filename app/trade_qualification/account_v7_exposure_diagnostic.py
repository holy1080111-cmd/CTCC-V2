"""DENY-only diagnostic of nonempty exposure in an original V7 account chain.

This does not revive the revoked V7 flat native issuer. The exact B1 page chain
is replayed under the V7 current-source and gateway/row policies. A receipt
only says that rows were observed in the retained current inventory; it does
not establish current account completeness or any execution permission.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_current_use_revocation as revoked
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope

POLICY_BYTES = canonical(
    {
        "version": "ctcc.demo_v7_exposure_diagnostic_policy.v1",
        "source": "exact_original_v7_B1_page_chain",
        "current_source_policy_sha256": current.V7_POLICY_SHA256,
        "current_use_revocation_policy_sha256": revoked.POLICY_SHA256,
        "required": "one_or_more_retained_current_inventory_rows",
        "gateway": "all_original_page_pairs_present_and_ordered",
        "known_row_times": "no_later_than_same_page_gateway_outTime",
        "validated_at": "caller_supplied_replay_cutoff_not_owned_live_clock",
        "source_clock_coverage_complete": False,
        "native_clock_proof_verified": False,
        "source_authenticity_verified": False,
        "history_and_local_exposure": "unknown",
        "account_complete": False,
        "flat_start_permission": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)
SCHEMA_VERSION = "ctcc.demo_v7_exposure_diagnostic.v1"
_ISSUER = object()
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_EXPOSURE_BLOCKER = "current_exposure_requires_protection_and_local_join"
_EXPECTED_REVOCATION_BLOCKERS = [
    "account_revision_unverified",
    _EXPOSURE_BLOCKER,
    "source_authenticity_unverified",
    "v7_current_flat_policy_revoked",
]
_FIELDS = frozenset(
    {
        "schema_version",
        "policy_sha256",
        "scope_sha256",
        "original_source_reference_sha256",
        "recorded_current_chain_head_sha256",
        "current_source_receipt_sha256",
        "current_use_revocation_receipt_sha256",
        "row_gateway_audit_receipt_sha256",
        "page_evidence_sha256",
        "row_evidence_sha256",
        "page_count",
        "inventory_row_counts",
        "exposure_row_count",
        "observed_nonempty_current_inventory",
        "gateway_causal_coverage_complete",
        "known_source_row_times_ordered",
        "source_clock_coverage_complete",
        "native_clock_proof_verified",
        "source_authenticity_verified",
        "history_complete",
        "local_exposure_complete",
        "active_protection_complete",
        "account_atomic_revision_verified",
        "snapshot",
        "account_complete",
        "flat_start_permission",
        "execution_authority",
        "admission",
    }
)


class V7ExposureDiagnosticError(ValueError):
    """A fixed code, never a private row, UID, or credential value."""


def _deny(code: str) -> None:
    raise V7ExposureDiagnosticError(code)


def _digest(value: object) -> bool:
    return type(value) is str and _HEX64.fullmatch(value) is not None


@dataclass(frozen=True, slots=True, repr=False, init=False)
class V7ExposureDiagnostic:
    receipt_json: bytes

    def __init__(self, receipt_json: bytes, *, _issuer=None) -> None:
        if _issuer is not _ISSUER:
            _deny("account_v7_exposure_receipt_invalid")
        object.__setattr__(self, "receipt_json", receipt_json)
        self._check()

    def _check(self) -> None:
        try:
            raw = self.receipt_json
            if type(raw) is not bytes or not 1 <= len(raw) <= 8192:
                _deny("account_v7_exposure_receipt_invalid")
            value = json.loads(raw)
            counts = value["inventory_row_counts"]
            if (
                type(value) is not dict
                or set(value) != _FIELDS
                or canonical(value) != raw
                or value["schema_version"] != SCHEMA_VERSION
                or value["policy_sha256"] != POLICY_SHA256
                or any(
                    not _digest(value[name])
                    for name in (
                        "scope_sha256",
                        "original_source_reference_sha256",
                        "recorded_current_chain_head_sha256",
                        "current_source_receipt_sha256",
                        "current_use_revocation_receipt_sha256",
                        "row_gateway_audit_receipt_sha256",
                        "page_evidence_sha256",
                        "row_evidence_sha256",
                    )
                )
                or type(value["page_count"]) is not int
                or not len(capture.V7_CURRENT_STREAMS) <= value["page_count"] <= 256
                or type(counts) is not dict
                or set(counts) != set(current.V7_INVENTORY_STREAMS)
                or any(
                    type(count) is not int or not 0 <= count <= 65536
                    for count in counts.values()
                )
                or type(value["exposure_row_count"]) is not int
                or value["exposure_row_count"] != sum(counts.values())
                or not 1 <= value["exposure_row_count"] <= 65536
                or any(
                    value[name] is not True
                    for name in (
                        "observed_nonempty_current_inventory",
                        "gateway_causal_coverage_complete",
                        "known_source_row_times_ordered",
                    )
                )
                or any(
                    value[name] is not False
                    for name in (
                        "native_clock_proof_verified",
                        "source_clock_coverage_complete",
                        "source_authenticity_verified",
                        "account_atomic_revision_verified",
                        "account_complete",
                        "flat_start_permission",
                        "execution_authority",
                    )
                )
                or any(
                    value[name] is not None
                    for name in (
                        "history_complete",
                        "local_exposure_complete",
                        "active_protection_complete",
                        "snapshot",
                    )
                )
                or value["admission"] != "DENY"
            ):
                _deny("account_v7_exposure_receipt_invalid")
        except V7ExposureDiagnosticError:
            raise
        except Exception:  # noqa: BLE001 -- never echo a private receipt
            _deny("account_v7_exposure_receipt_invalid")

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def snapshot(self) -> None:
        return None

    @property
    def account_complete(self) -> bool:
        return False

    @property
    def flat_start_permission(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False


def diagnose_v7_recorded_exposure(
    *,
    chain: tuple,
    reference: observed.CaptureReference,
    scope: LedgerScope,
    validated_at: datetime,
    expected_policy_sha256: str = POLICY_SHA256,
) -> V7ExposureDiagnostic:
    """Replay exact source; `validated_at` is a caller-supplied replay cutoff.

    A historical chain can be fresh relative to that cutoff. This function
    neither measures the current clock nor grants current account authority.
    """
    if (
        type(chain) is not tuple
        or type(reference) is not observed.CaptureReference
        or type(scope) is not LedgerScope
        or type(validated_at) is not datetime
        or type(expected_policy_sha256) is not str
        or expected_policy_sha256 != POLICY_SHA256
    ):
        _deny("account_v7_exposure_inputs_invalid")
    try:
        source_receipt = current.verify_current_account_sources(
            chain,
            reference=reference,
            scope=scope,
            validated_at=validated_at,
            expected_policy_sha256=current.V7_POLICY_SHA256,
        )
        current_source = json.loads(source_receipt.receipt_json)
        counts = current_source["inventory_row_counts"]
        if (
            current_source["blocking_reasons"] != [_EXPOSURE_BLOCKER]
            or current_source["observed_flat"] is not False
            or current_source["observed_inventory_state"] != "incomplete_or_exposed"
            or set(counts) != set(current.V7_INVENTORY_STREAMS)
            or not any(counts.values())
        ):
            _deny("account_v7_exposure_source_incomplete_or_empty")
        revocation = revoked.audit_v7_current_use_revocation(
            chain=chain,
            reference=reference,
            scope=scope,
            validated_at=validated_at,
            expected_policy_sha256=revoked.POLICY_SHA256,
        )
        current_use = json.loads(revocation.receipt_json)
        if (
            current_use["historical_current_receipt_sha256"]
            != source_receipt.receipt_sha256
            or current_use["gateway_causal_coverage_complete"] is not True
            or current_use["known_source_row_times_ordered"] is not True
            or current_use["blocking_reasons"] != _EXPECTED_REVOCATION_BLOCKERS
        ):
            _deny("account_v7_exposure_gateway_or_source_incomplete")
        result = canonical(
            {
                "schema_version": SCHEMA_VERSION,
                "policy_sha256": POLICY_SHA256,
                "scope_sha256": current_source["scope_sha256"],
                "original_source_reference_sha256": current_use[
                    "original_source_reference_sha256"
                ],
                "recorded_current_chain_head_sha256": current_source[
                    "recorded_current_chain_head_sha256"
                ],
                "current_source_receipt_sha256": source_receipt.receipt_sha256,
                "current_use_revocation_receipt_sha256": revocation.receipt_sha256,
                "row_gateway_audit_receipt_sha256": current_use[
                    "row_gateway_audit_receipt_sha256"
                ],
                "page_evidence_sha256": sha(canonical(current_source["current_pages"])),
                "row_evidence_sha256": sha(canonical(current_source["current_rows"])),
                "page_count": len(current_source["current_pages"]),
                "inventory_row_counts": counts,
                "exposure_row_count": sum(counts.values()),
                "observed_nonempty_current_inventory": True,
                "gateway_causal_coverage_complete": True,
                "known_source_row_times_ordered": True,
                "source_clock_coverage_complete": False,
                "native_clock_proof_verified": False,
                "source_authenticity_verified": False,
                "history_complete": None,
                "local_exposure_complete": None,
                "active_protection_complete": None,
                "account_atomic_revision_verified": False,
                "snapshot": None,
                "account_complete": False,
                "flat_start_permission": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
        return V7ExposureDiagnostic(result, _issuer=_ISSUER)
    except V7ExposureDiagnosticError:
        raise
    except Exception:  # noqa: BLE001 -- raw account evidence stays private
        _deny("account_v7_exposure_diagnostic_unavailable")


def verify_v7_recorded_exposure(
    receipt: V7ExposureDiagnostic,
    *,
    chain: tuple,
    reference: observed.CaptureReference,
    scope: LedgerScope,
    validated_at: datetime,
    expected_receipt_sha256: str,
) -> V7ExposureDiagnostic:
    """Independent SHA pin and complete original-chain replay; no lease issued."""
    if type(receipt) is not V7ExposureDiagnostic or not _digest(
        expected_receipt_sha256
    ):
        _deny("account_v7_exposure_readback_invalid")
    receipt._check()
    replay = diagnose_v7_recorded_exposure(
        chain=chain,
        reference=reference,
        scope=scope,
        validated_at=validated_at,
    )
    if (
        receipt.receipt_sha256 != expected_receipt_sha256
        or receipt.receipt_json != replay.receipt_json
    ):
        _deny("account_v7_exposure_readback_mismatch")
    return replay
