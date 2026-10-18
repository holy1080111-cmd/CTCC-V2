"""DENY-only metadata coverage for instruments in original V7 exposure rows.

The older declared-instrument observation does not cover a position or pending
order on another instrument. This separate policy replays the same original B1
chain and reports that gap without expanding the plan's leverage query.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_v7_metadata_coverage as declared
from app.trade_qualification.reservations import LedgerScope

SCHEMA_VERSION = "ctcc.demo_v7_inventory_metadata_coverage.v1"
POLICY_BYTES = journal.canonical(
    {
        "version": SCHEMA_VERSION,
        "current_source_policy_sha256": current.V7_POLICY_SHA256,
        "declared_metadata_policy_sha256": declared.POLICY_SHA256,
        "source": "one_original_V7_B1_raw_page_chain",
        "scope": "all_observed_positions_ordinary_and_eight_algo_pending_instruments",
        "undeclared_leverage": "not_queried_never_inferred",
        "empty_inventory": "no_positive_inventory_metadata_claim",
        "source_authenticity_verified": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)
MAX_INSTRUMENTS = 128
MAX_RECEIPT_BYTES = 128 * 1024
_ISSUER = object()
_EXPOSURE_ONLY = ["current_exposure_requires_protection_and_local_join"]
_ALWAYS_BLOCKED = frozenset(
    {
        "account_wide_product_scope_unverified",
        "history_local_exposure_and_protection_unverified",
        "source_authenticity_unverified",
    }
)
_MISSING_REASONS = frozenset(
    {
        "account_instrument_row_missing",
        "account_instrument_terms_unusable",
        "cross_leverage_side_coverage_missing_or_conflicting",
        "cross_leverage_value_unknown",
        "isolated_leverage_side_coverage_missing_or_conflicting",
        "isolated_leverage_value_unknown",
        "leverage_instrument_not_declared_or_queried",
    }
)
_INSTRUMENT_FIELDS = frozenset(
    {
        "instrument_id",
        "exposure_row_count",
        "exposure_rows_sha256",
        "instrument_row_sha256",
        "declared_leverage_query",
        "missing_or_unusable",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "policy_sha256",
        "source_reference_sha256",
        "current_source_receipt_sha256",
        "declared_metadata_receipt_sha256",
        "metadata_page_receipt_sha256",
        "inventory_row_count",
        "instruments",
        "observed_inventory_metadata_coverage",
        "blocking_reasons",
        "snapshot",
        "source_authenticity_verified",
        "account_complete",
        "flat_start_permission",
        "execution_authority",
        "admission",
    }
)


class V7InventoryMetadataError(ValueError):
    """Fixed error codes only; raw account values never enter exceptions."""


def _deny(code: str) -> None:
    raise V7InventoryMetadataError(code)


def _sha(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def _instrument_valid(item: object) -> bool:
    if type(item) is not dict or set(item) != _INSTRUMENT_FIELDS:
        return False
    missing = item["missing_or_unusable"]
    if (
        type(item["instrument_id"]) is not str
        or not 1 <= len(item["instrument_id"]) <= 96
        or type(item["exposure_row_count"]) is not int
        or item["exposure_row_count"] < 1
        or not _sha(item["exposure_rows_sha256"])
        or (
            item["instrument_row_sha256"] is not None
            and not _sha(item["instrument_row_sha256"])
        )
        or type(item["declared_leverage_query"]) is not bool
        or type(missing) is not list
        or any(
            type(reason) is not str or reason not in _MISSING_REASONS
            for reason in missing
        )
        or len(missing) != len(set(missing))
    ):
        return False
    return ("account_instrument_row_missing" in missing) == (
        item["instrument_row_sha256"] is None
    ) and ("leverage_instrument_not_declared_or_queried" in missing) == (
        not item["declared_leverage_query"]
    )


@dataclass(frozen=True, slots=True, repr=False, init=False)
class V7InventoryMetadata:
    receipt_json: bytes

    def __init__(self, receipt_json: bytes, *, _issuer=None) -> None:
        if _issuer is not _ISSUER:
            _deny("v7_inventory_metadata_receipt_invalid")
        object.__setattr__(self, "receipt_json", receipt_json)
        self._check()

    def _check(self) -> None:
        try:
            raw = self.receipt_json
            if type(raw) is not bytes or not 1 <= len(raw) <= MAX_RECEIPT_BYTES:
                _deny("v7_inventory_metadata_receipt_invalid")
            value = json.loads(raw)
            if (
                type(value) is not dict
                or set(value) != _RECEIPT_FIELDS
                or journal.canonical(value) != raw
                or value["schema_version"] != SCHEMA_VERSION
                or value["policy_sha256"] != POLICY_SHA256
                or any(
                    not _sha(value[name])
                    for name in (
                        "source_reference_sha256",
                        "current_source_receipt_sha256",
                        "declared_metadata_receipt_sha256",
                        "metadata_page_receipt_sha256",
                    )
                )
                or value["snapshot"] is not None
                or value["source_authenticity_verified"] is not False
                or value["account_complete"] is not False
                or value["flat_start_permission"] is not False
                or value["execution_authority"] is not False
                or value["admission"] != "DENY"
                or type(value["observed_inventory_metadata_coverage"]) is not bool
                or type(value["inventory_row_count"]) is not int
                or value["inventory_row_count"] < 0
                or type(value["instruments"]) is not list
                or len(value["instruments"]) > MAX_INSTRUMENTS
                or any(not _instrument_valid(item) for item in value["instruments"])
                or len({item["instrument_id"] for item in value["instruments"]})
                != len(value["instruments"])
                or sum(item["exposure_row_count"] for item in value["instruments"])
                != value["inventory_row_count"]
                or (
                    value["observed_inventory_metadata_coverage"]
                    and not value["instruments"]
                )
                or type(value["blocking_reasons"]) is not list
                or sorted(set(value["blocking_reasons"])) != value["blocking_reasons"]
                or any(type(item) is not str for item in value["blocking_reasons"])
                or not _ALWAYS_BLOCKED <= set(value["blocking_reasons"])
                or (
                    not value["instruments"]
                    and "no_observed_inventory_instrument"
                    not in value["blocking_reasons"]
                )
                or (
                    value["instruments"]
                    and "current_exposure_requires_protection_and_local_join"
                    not in value["blocking_reasons"]
                )
                or (
                    any(item["missing_or_unusable"] for item in value["instruments"])
                    and "inventory_instrument_metadata_incomplete"
                    not in value["blocking_reasons"]
                )
                or (
                    value["observed_inventory_metadata_coverage"]
                    and (
                        any(
                            item["missing_or_unusable"] for item in value["instruments"]
                        )
                        or set(value["blocking_reasons"])
                        != _ALWAYS_BLOCKED | set(_EXPOSURE_ONLY)
                    )
                )
            ):
                _deny("v7_inventory_metadata_receipt_invalid")
        except V7InventoryMetadataError:
            raise
        except Exception:  # noqa: BLE001 -- do not echo private receipt contents
            _deny("v7_inventory_metadata_receipt_invalid")

    @property
    def receipt_sha256(self) -> str:
        return journal.digest(self.receipt_json)

    @property
    def account_complete(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def admission(self) -> str:
        return "DENY"


def observe_v7_inventory_metadata(
    chain: tuple,
    *,
    reference: observed.CaptureReference,
    scope: LedgerScope,
    validated_at: datetime,
    expected_policy_sha256: str = POLICY_SHA256,
) -> V7InventoryMetadata:
    """Replay source, then compare observed exposure to queried metadata scope."""
    if (
        type(expected_policy_sha256) is not str
        or expected_policy_sha256 != POLICY_SHA256
    ):
        _deny("v7_inventory_metadata_policy_invalid")
    try:
        return _observe(chain, reference, scope, validated_at)
    except V7InventoryMetadataError:
        raise
    except Exception:  # noqa: BLE001 -- raw source and account errors are private
        _deny("v7_inventory_metadata_source_invalid")


def verify_v7_inventory_metadata_receipt(
    receipt: V7InventoryMetadata,
    *,
    expected_receipt_sha256: str,
    chain: tuple,
    reference: observed.CaptureReference,
    scope: LedgerScope,
    validated_at: datetime,
) -> V7InventoryMetadata:
    """Read back a saved receipt against the complete original raw chain."""
    if (
        type(receipt) is not V7InventoryMetadata
        or type(expected_receipt_sha256) is not str
        or len(expected_receipt_sha256) != 64
        or any(c not in "0123456789abcdef" for c in expected_receipt_sha256)
    ):
        _deny("v7_inventory_metadata_readback_invalid")
    receipt._check()
    replay = observe_v7_inventory_metadata(
        chain, reference=reference, scope=scope, validated_at=validated_at
    )
    if (
        receipt.receipt_sha256 != expected_receipt_sha256
        or receipt.receipt_json != replay.receipt_json
    ):
        _deny("v7_inventory_metadata_readback_changed")
    return replay


def _observe(chain, reference, scope, validated_at):
    declared_result = declared.verify_v7_declared_metadata(
        chain, reference=reference, scope=scope, validated_at=validated_at
    )
    declared_value = json.loads(declared_result.receipt_json)
    current_result = current.verify_current_account_sources(
        chain,
        reference=reference,
        scope=scope,
        validated_at=validated_at,
        expected_policy_sha256=current.V7_POLICY_SHA256,
    )
    if current_result.receipt_sha256 != declared_value["current_source_receipt_sha256"]:
        _deny("v7_inventory_metadata_source_changed")
    source = json.loads(current_result.receipt_json)
    payloads = [
        item.event.packet_payload for item in chain if item.event.packet_payload
    ]
    if len(payloads) != 1:
        _deny("v7_inventory_metadata_original_packet_required")
    packet = capture.verify_demo_account_packet(
        payloads[0],
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )
    if type(packet.plan) is not capture.CurrentDemoAccountCapturePlanV7:
        _deny("v7_inventory_metadata_plan_invalid")
    metadata_pages = [
        item
        for item in packet.observations
        if item.request.stream == "account_instruments"
    ]
    if len(metadata_pages) != 1:
        _deny("v7_inventory_metadata_page_invalid")
    specs = {row.instrument_id: row for row in metadata_pages[0].rows}
    declared_rows = {
        item["instrument_id"]: item for item in declared_value["declared_instruments"]
    }
    inventory_rows = [
        item
        for item in source["current_rows"]
        if item["stream"] in current.V7_INVENTORY_STREAMS
    ]
    if len(inventory_rows) != sum(source["inventory_row_counts"].values()):
        _deny("v7_inventory_metadata_row_count_invalid")
    groups = {}
    for item in inventory_rows:
        instrument_id = item["instrument_id"]
        if type(instrument_id) is not str or not instrument_id:
            _deny("v7_inventory_metadata_instrument_invalid")
        group = groups.setdefault(instrument_id, [])
        group.append([item["stream"], item["row_sha256"], item["page_receipt_sha256"]])
        if len(groups) > MAX_INSTRUMENTS:
            _deny("v7_inventory_metadata_instrument_limit")
    instruments = []
    for instrument_id, witnesses in groups.items():
        declared_row = declared_rows.get(instrument_id)
        spec = specs.get(instrument_id)
        missing = (
            list(declared_row["missing_or_unusable"])
            if declared_row is not None
            else ["leverage_instrument_not_declared_or_queried"]
        )
        if declared_row is None and spec is None:
            missing.append("account_instrument_row_missing")
        instruments.append(
            {
                "instrument_id": instrument_id,
                "exposure_row_count": len(witnesses),
                "exposure_rows_sha256": journal.digest(journal.canonical(witnesses)),
                "instrument_row_sha256": (
                    None
                    if spec is None
                    else journal.digest(spec.canonical_json.encode("utf-8"))
                ),
                "declared_leverage_query": declared_row is not None,
                "missing_or_unusable": missing,
            }
        )
    source_blockers = source["blocking_reasons"]
    covered = (
        bool(instruments)
        and source_blockers == _EXPOSURE_ONLY
        and all(not item["missing_or_unusable"] for item in instruments)
    )
    blockers = set(source_blockers)
    if not instruments:
        blockers.add("no_observed_inventory_instrument")
    if any(item["missing_or_unusable"] for item in instruments):
        blockers.add("inventory_instrument_metadata_incomplete")
    blockers.update(_ALWAYS_BLOCKED)
    receipt = journal.canonical(
        {
            "schema_version": SCHEMA_VERSION,
            "policy_sha256": POLICY_SHA256,
            "source_reference_sha256": journal.digest(
                journal.canonical(observed.reference_document(reference))
            ),
            "current_source_receipt_sha256": current_result.receipt_sha256,
            "declared_metadata_receipt_sha256": declared_result.receipt_sha256,
            "metadata_page_receipt_sha256": metadata_pages[0].receipt_sha256,
            "inventory_row_count": len(inventory_rows),
            "instruments": instruments,
            "observed_inventory_metadata_coverage": covered,
            "blocking_reasons": sorted(blockers),
            "snapshot": None,
            "source_authenticity_verified": False,
            "account_complete": False,
            "flat_start_permission": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    return V7InventoryMetadata(receipt, _issuer=_ISSUER)
