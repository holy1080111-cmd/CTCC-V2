"""Versioned, DENY-only V7 native-account chronology proof.

The historical V7 native proof remains replayable but revoked for current use.
This separate proof replays its exact raw B1 chain and requires every account
page's OKX gateway clock pair and every recognized row timestamp to be present
and no later than that page's gateway ``outTime``. Rows with no recognized
source clock remain explicitly unknown. It never grants flat-start or
execution authority; account provenance and portfolio completeness remain
separate requirements.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

from app.domain.source_primitives import canonical, sha, utc_from_ns
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_gateway_causality as gateway
from app.trade_qualification import account_native_proof as native
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_row_gateway_causality as row_gateway
from app.trade_qualification.reservations import LedgerScope

POLICY_BYTES = canonical(
    {
        "version": "ctcc.demo_native_current_v7_chronology_policy.v1",
        "historical_native_schema": native.V7_FLAT_SCHEMA,
        "historical_native_policy_sha256": native.V7_FLAT_POLICY_SHA256,
        "current_source_policy_sha256": current.V7_POLICY_SHA256,
        "gateway_policy_sha256": gateway.V7_POLICY_SHA256,
        "row_gateway_policy_sha256": row_gateway.POLICY_SHA256,
        "page_source_hashes": "sha256_canonical_ordered_request_index_page_receipt_and_raw_body_hashes",
        "row_source_hashes": "sha256_canonical_ordered_request_index_row_ordinal_and_row_hashes",
        "required_gateway": "each_original_page_inTime_le_outTime_within_request_and_headers",
        "required_source_order": "each_recognized_present_row_time_le_own_page_outTime",
        "missing_recognized_timestamp": "reject_not_assumed_ordered",
        "row_without_recognized_clock": "count_unknown_no_account_qualification",
        "source_time_coverage_complete": False,
        "missing_gateway": "reject_no_host_time_substitution",
        "old_v7_current_use": "revoked",
        "flat_start_permission": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)
SCHEMA_VERSION = "ctcc.demo_native_current_v7_chronology_proof.v1"
MAX_RECEIPT_BYTES = 8192
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_ISSUER = object()
_FIELDS = frozenset(
    {
        "schema_version",
        "policy_sha256",
        "native_proof_sha256",
        "original_source_reference_sha256",
        "current_source_receipt_sha256",
        "gateway_audit_receipt_sha256",
        "row_gateway_audit_receipt_sha256",
        "page_source_hashes_sha256",
        "row_source_hashes_sha256",
        "page_count",
        "known_source_timestamp_count",
        "missing_source_timestamp_count",
        "zero_clock_row_count",
        "source_time_coverage_complete",
        "validated_at",
        "gateway_causal_coverage_complete",
        "known_source_row_times_ordered",
        "current_flat_qualified",
        "flat_start_permission",
        "account_complete",
        "source_authenticity_verified",
        "execution_authority",
        "admission",
    }
)


class NativeCurrentV7ProofError(ValueError):
    """Static rejection code; no private row, UID, or credential in errors."""


def _deny(code: str) -> None:
    raise NativeCurrentV7ProofError(code)


def _digest(value: object) -> bool:
    return type(value) is str and _HEX64.fullmatch(value) is not None


@dataclass(frozen=True, slots=True, repr=False, init=False)
class NativeCurrentV7ChronologyProof:
    receipt_json: bytes

    def __init__(self, receipt_json: bytes, *, _issuer=None) -> None:
        if _issuer is not _ISSUER:
            _deny("native_current_v7_receipt_invalid")
        object.__setattr__(self, "receipt_json", receipt_json)
        self._check()

    def _check(self) -> None:
        try:
            raw = self.receipt_json
            if type(raw) is not bytes or not 1 <= len(raw) <= MAX_RECEIPT_BYTES:
                _deny("native_current_v7_receipt_invalid")
            value = json.loads(raw)
            if (
                type(value) is not dict
                or set(value) != _FIELDS
                or canonical(value) != raw
                or value["schema_version"] != SCHEMA_VERSION
                or value["policy_sha256"] != POLICY_SHA256
                or any(
                    not _digest(value[name])
                    for name in (
                        "native_proof_sha256",
                        "original_source_reference_sha256",
                        "current_source_receipt_sha256",
                        "gateway_audit_receipt_sha256",
                        "row_gateway_audit_receipt_sha256",
                        "page_source_hashes_sha256",
                        "row_source_hashes_sha256",
                    )
                )
                or type(value["page_count"]) is not int
                or not len(capture.V7_CURRENT_STREAMS) <= value["page_count"] <= 256
                or type(value["known_source_timestamp_count"]) is not int
                or not 1 <= value["known_source_timestamp_count"] <= 65536
                or type(value["missing_source_timestamp_count"]) is not int
                or value["missing_source_timestamp_count"] != 0
                or type(value["zero_clock_row_count"]) is not int
                or not 0 <= value["zero_clock_row_count"] <= 65536
                or value["source_time_coverage_complete"] is not False
                or type(value["validated_at"]) is not str
                or capture._utc(
                    datetime.fromisoformat(value["validated_at"])
                ).isoformat()
                != value["validated_at"]
                or value["gateway_causal_coverage_complete"] is not True
                or value["known_source_row_times_ordered"] is not True
                or any(
                    value[name] is not False
                    for name in (
                        "current_flat_qualified",
                        "flat_start_permission",
                        "account_complete",
                        "source_authenticity_verified",
                        "execution_authority",
                    )
                )
                or value["admission"] != "DENY"
            ):
                _deny("native_current_v7_receipt_invalid")
        except NativeCurrentV7ProofError:
            raise
        except Exception:  # noqa: BLE001 -- never echo a private receipt
            _deny("native_current_v7_receipt_invalid")

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def account_complete(self) -> bool:
        return False


def prove_v7_native_current_chronology(
    *,
    native_proof_json: bytes,
    clock_files: dict[str, bytes],
    chain: tuple,
    scope: LedgerScope,
    validated_at: datetime,
    expected_native_proof_sha256: str,
    expected_policy_sha256: str = POLICY_SHA256,
) -> NativeCurrentV7ChronologyProof:
    """Replay the old native proof and demand new per-page causal evidence.

    The supplied proof SHA must come from an independent readback/pin. This
    pure function performs no network, clock acquisition, database, or write.
    """
    if (
        type(native_proof_json) is not bytes
        or type(clock_files) is not dict
        or type(chain) is not tuple
        or type(scope) is not LedgerScope
        or type(validated_at) is not datetime
        or not _digest(expected_native_proof_sha256)
        or type(expected_policy_sha256) is not str
        or expected_policy_sha256 != POLICY_SHA256
    ):
        _deny("native_current_v7_inputs_invalid")
    try:
        native_document = native._canonical_document(native_proof_json)
        if (
            native_document["schema_version"] != native.V7_FLAT_SCHEMA
            or native_document["policy_sha256"] != native.V7_FLAT_POLICY_SHA256
        ):
            _deny("native_current_v7_original_proof_required")
        replay = native.replay_native_account_proof(
            native_proof_json,
            files=clock_files,
            chain=chain,
            scope=scope,
            expected_proof_sha256=expected_native_proof_sha256,
        )
        at = capture._utc(validated_at)
        if (
            not utc_from_ns(native_document["proof_persist_start"]["utc_ns"])
            <= at
            < replay.expires_at
        ):
            _deny("native_current_v7_original_expired")
        reference = observed.source_reference(chain)
        gateway_audit = gateway.audit_recorded_demo_account_gateway_causality(
            chain=chain,
            reference=reference,
            scope=scope,
            validated_at=at,
            expected_policy_sha256=gateway.V7_POLICY_SHA256,
        )
        row_audit = row_gateway.audit_v7_account_row_gateway_causality(
            chain=chain,
            reference=reference,
            scope=scope,
            validated_at=at,
        )
        gateway_value = json.loads(gateway_audit.receipt_json)
        row_value = json.loads(row_audit.receipt_json)
        pages = replay.packet.observations
        if (
            len(pages) != len(gateway_value["pages"])
            or len(pages) != len(row_value["pages"])
            or not gateway_value["gateway_causal_coverage_complete"]
            or not all(
                page["gateway_causal_window_verified"]
                for page in gateway_value["pages"]
            )
            or not all(page["status"] == "checked" for page in row_value["pages"])
            or any(
                page["missing_source_timestamp_count"] for page in row_value["pages"]
            )
        ):
            _deny("native_current_v7_gateway_or_row_time_incomplete")
        for index, page in enumerate(pages):
            if (
                page.receipt_sha256
                != gateway_value["pages"][index]["page_receipt_sha256"]
                or page.body_sha256 != gateway_value["pages"][index]["raw_body_sha256"]
                or page.receipt_sha256
                != row_value["pages"][index]["page_receipt_sha256"]
            ):
                _deny("native_current_v7_source_page_mismatch")
        source = json.loads(replay.current_source_receipt_json)
        if (
            source["policy_sha256"] != current.V7_POLICY_SHA256
            or source["source_reference"] != observed.reference_document(reference)
            or row_value["gateway_audit_receipt_sha256"] != gateway_audit.receipt_sha256
        ):
            _deny("native_current_v7_source_binding_invalid")
        receipt = canonical(
            {
                "schema_version": SCHEMA_VERSION,
                "policy_sha256": POLICY_SHA256,
                "native_proof_sha256": replay.proof_sha256,
                "original_source_reference_sha256": sha(
                    canonical(observed.reference_document(reference))
                ),
                "current_source_receipt_sha256": sha(
                    replay.current_source_receipt_json
                ),
                "gateway_audit_receipt_sha256": gateway_audit.receipt_sha256,
                "row_gateway_audit_receipt_sha256": row_audit.receipt_sha256,
                "page_source_hashes_sha256": sha(
                    canonical(
                        [
                            [index, page.receipt_sha256, page.body_sha256]
                            for index, page in enumerate(pages)
                        ]
                    )
                ),
                "row_source_hashes_sha256": sha(
                    canonical(
                        [
                            [
                                row["request_index"],
                                row["row_ordinal"],
                                row["row_sha256"],
                            ]
                            for row in source["current_rows"]
                        ]
                    )
                ),
                "page_count": len(pages),
                "known_source_timestamp_count": sum(
                    page["known_source_timestamp_count"] for page in row_value["pages"]
                ),
                "missing_source_timestamp_count": sum(
                    page["missing_source_timestamp_count"]
                    for page in row_value["pages"]
                ),
                "zero_clock_row_count": sum(
                    not row["source_times"] for row in source["current_rows"]
                ),
                "source_time_coverage_complete": False,
                "validated_at": at.isoformat(),
                "gateway_causal_coverage_complete": True,
                "known_source_row_times_ordered": True,
                "current_flat_qualified": False,
                "flat_start_permission": False,
                "account_complete": False,
                "source_authenticity_verified": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
        return NativeCurrentV7ChronologyProof(receipt, _issuer=_ISSUER)
    except NativeCurrentV7ProofError:
        raise
    except Exception:  # noqa: BLE001 -- no private original source data in errors
        _deny("native_current_v7_proof_invalid")


def verify_v7_native_current_chronology(
    receipt: NativeCurrentV7ChronologyProof,
    *,
    native_proof_json: bytes,
    clock_files: dict[str, bytes],
    chain: tuple,
    scope: LedgerScope,
    validated_at: datetime,
    expected_native_proof_sha256: str,
    expected_receipt_sha256: str,
) -> NativeCurrentV7ChronologyProof:
    """Independent receipt SHA pin and full original-source replay/readback."""
    if type(receipt) is not NativeCurrentV7ChronologyProof or not _digest(
        expected_receipt_sha256
    ):
        _deny("native_current_v7_readback_invalid")
    receipt._check()
    replay = prove_v7_native_current_chronology(
        native_proof_json=native_proof_json,
        clock_files=clock_files,
        chain=chain,
        scope=scope,
        validated_at=validated_at,
        expected_native_proof_sha256=expected_native_proof_sha256,
    )
    if (
        receipt.receipt_sha256 != expected_receipt_sha256
        or receipt.receipt_json != replay.receipt_json
    ):
        _deny("native_current_v7_readback_mismatch")
    return replay


def prove_v7_native_current_from_companion(
    root,
    *,
    chain: tuple,
    scope: LedgerScope,
    validated_at: datetime,
    expected_native_proof_sha256: str,
    expected_native_readback_sha256: str,
) -> NativeCurrentV7ChronologyProof:
    """Use the already sealed native companion's separate filesystem readback."""
    if not _digest(expected_native_proof_sha256) or not _digest(
        expected_native_readback_sha256
    ):
        _deny("native_current_v7_stored_pins_invalid")
    try:
        _, readback_raw = storage.read_native_account_companion(
            root,
            chain=chain,
            scope=scope,
            expected_proof_sha256=expected_native_proof_sha256,
            expected_readback_sha256=expected_native_readback_sha256,
        )
        readback = json.loads(readback_raw)
        if readback["schema_version"] != storage.V7_FLAT_READBACK_SCHEMA:
            _deny("native_current_v7_original_readback_required")
        if capture._utc(validated_at) < utc_from_ns(
            readback["readback_complete"]["utc_ns"]
        ):
            _deny("native_current_v7_readback_after_validation")
        with storage._root_context(root) as directory:
            if set(directory.names()) != {
                *native.FILES,
                "proof.json",
                "readback.json",
            } or (
                readback["root_device"],
                readback["root_inode"],
            ) != storage._root_identity(directory.path):
                _deny("native_current_v7_companion_changed")
            raw = directory.read("proof.json", native.MAX_PROOF)
            files = {
                name: directory.read(name, storage.MAX_PART) for name in native.FILES
            }
            if (
                sha(raw) != expected_native_proof_sha256
                or readback["clock_file_sha256"]
                != {name: sha(files[name]) for name in native.FILES}
                or directory.read("readback.json", storage.MAX_PART) != readback_raw
            ):
                _deny("native_current_v7_companion_changed")
        return prove_v7_native_current_chronology(
            native_proof_json=raw,
            clock_files=files,
            chain=chain,
            scope=scope,
            validated_at=validated_at,
            expected_native_proof_sha256=expected_native_proof_sha256,
        )
    except NativeCurrentV7ProofError:
        raise
    except Exception:  # noqa: BLE001 -- never disclose path/private source data
        _deny("native_current_v7_companion_readback_invalid")


def verify_v7_native_current_from_companion(
    receipt: NativeCurrentV7ChronologyProof,
    root,
    *,
    chain: tuple,
    scope: LedgerScope,
    validated_at: datetime,
    expected_native_proof_sha256: str,
    expected_native_readback_sha256: str,
    expected_receipt_sha256: str,
) -> NativeCurrentV7ChronologyProof:
    """Reopen the companion, replay raw source, and compare an external pin."""
    if type(receipt) is not NativeCurrentV7ChronologyProof or not _digest(
        expected_receipt_sha256
    ):
        _deny("native_current_v7_readback_invalid")
    receipt._check()
    replay = prove_v7_native_current_from_companion(
        root,
        chain=chain,
        scope=scope,
        validated_at=validated_at,
        expected_native_proof_sha256=expected_native_proof_sha256,
        expected_native_readback_sha256=expected_native_readback_sha256,
    )
    if (
        receipt.receipt_sha256 != expected_receipt_sha256
        or receipt.receipt_json != replay.receipt_json
    ):
        _deny("native_current_v7_readback_mismatch")
    return replay
