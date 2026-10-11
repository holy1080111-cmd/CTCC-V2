"""V7 Demo metadata coverage for plan-declared SWAPs, never account authority.

An empty position/order inventory says nothing about missing instrument or
leverage metadata. Replay the original B1 current chain before inspecting the
exact account-instruments and leverage rows; preserve every missing side as
unknown rather than inventing a default leverage or contract specification.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_RECEIPT_BYTES = 256 * 1024
SCHEMA_VERSION = "ctcc.demo_v7_declared_metadata_coverage.v1"
POLICY_BYTES = journal.canonical(
    {
        "version": SCHEMA_VERSION,
        "capture_plan": "ctcc.demo_current_account_plan.v7",
        "current_source_policy_sha256": current.V7_POLICY_SHA256,
        "scope": "plan_declared_SWAP_instruments_only",
        "position_sides": "net_or_both_long_and_short_from_account_config",
        "source": "one_original_B1_raw_page_chain_with_exact_external_reference",
        "account_complete": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class V7MetadataCoverageError(ValueError):
    """Fixed codes only; account rows and credentials never enter errors."""


def _deny(code):
    raise V7MetadataCoverageError(code)


@dataclass(frozen=True, slots=True, repr=False)
class V7MetadataCoverage:
    receipt_json: bytes

    def __post_init__(self):
        if (
            type(self.receipt_json) is not bytes
            or not 1 <= len(self.receipt_json) <= MAX_RECEIPT_BYTES
        ):
            _deny("v7_metadata_receipt_invalid")
        try:
            value = json.loads(self.receipt_json)
            if (
                type(value) is not dict
                or journal.canonical(value) != self.receipt_json
                or value.get("schema_version") != SCHEMA_VERSION
                or value.get("policy_sha256") != POLICY_SHA256
                or value.get("account_complete") is not False
                or value.get("source_authenticity_verified") is not False
                or value.get("execution_authority") is not False
                or value.get("snapshot", object()) is not None
                or value.get("admission") != "DENY"
            ):
                _deny("v7_metadata_receipt_invalid")
        except V7MetadataCoverageError:
            raise
        except Exception:  # noqa: BLE001 -- direct construction grants no authority
            _deny("v7_metadata_receipt_invalid")

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


def verify_v7_declared_metadata(
    chain,
    *,
    reference,
    scope,
    validated_at,
    expected_policy_sha256=POLICY_SHA256,
):
    """Recompute the V7 current source, then describe only declared metadata."""
    try:
        return _verify(chain, reference, scope, validated_at, expected_policy_sha256)
    except V7MetadataCoverageError:
        raise
    except Exception:  # noqa: BLE001 -- redact source and account exceptions
        raise V7MetadataCoverageError("v7_metadata_source_invalid") from None


def verify_v7_declared_metadata_receipt(receipt, **original_inputs):
    """Compare saved bytes with a fresh replay of the externally pinned B1 chain."""
    if type(receipt) is not V7MetadataCoverage:
        _deny("v7_metadata_exact_receipt_required")
    checked = V7MetadataCoverage(receipt.receipt_json)
    replayed = verify_v7_declared_metadata(**original_inputs)
    if checked.receipt_json != replayed.receipt_json:
        _deny("v7_metadata_receipt_changed")
    return replayed


def _positive(row, key):
    raw = row.get(key)
    return type(raw) is str and bool(raw) and Decimal(raw) > 0


def _verify(chain, reference, scope, validated_at, policy_pin):
    checked_bootstrap(scope, LedgerScope)
    if (
        type(policy_pin) is not str
        or policy_pin != POLICY_SHA256
        or type(validated_at) is not datetime
        or type(chain) is not tuple
        or not 1 <= len(chain) <= journal.MAX_EVENTS
        or any(type(item) is not journal.JournalReadback for item in chain)
        or sum(
            len(item.event.event_json)
            + len(item.event.raw_body or b"")
            + len(item.event.packet_payload or b"")
            for item in chain
        )
        > MAX_SOURCE_BYTES
    ):
        _deny("v7_metadata_inputs_invalid")
    reference_document = observed.reference_document(reference)
    if (
        observed.reference_document(observed.source_reference(chain))
        != reference_document
    ):
        _deny("v7_metadata_source_reference_mismatch")
    proof = current.verify_current_account_sources(
        chain,
        reference=reference,
        scope=scope,
        validated_at=validated_at,
        expected_policy_sha256=current.V7_POLICY_SHA256,
    )
    payloads = [
        item.event.packet_payload for item in chain if item.event.packet_payload
    ]
    if len(payloads) != 1:
        _deny("v7_metadata_original_packet_required")
    packet = capture.verify_demo_account_packet(
        payloads[0],
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )
    if type(packet.plan) is not capture.CurrentDemoAccountCapturePlanV7:
        _deny("v7_metadata_plan_required")
    source = json.loads(proof.receipt_json)
    if (
        packet.plan.expected_uid != scope.account_id
        or packet.plan.settlement_currency != scope.settlement_currency
        or source["identity"]["uid"] != scope.account_id
        or source["identity"]["main_uid"] != packet.plan.expected_main_uid
        or source["source_reference"] != reference_document
    ):
        _deny("v7_metadata_account_identity_mismatch")
    sides = (
        ("net",)
        if source["identity"]["position_mode"] == "net_mode"
        else ("long", "short")
    )
    groups = {
        name: [page for page in packet.observations if page.request.stream == name]
        for name in ("account_instruments", "leverage_cross", "leverage_isolated")
    }
    if any(len(pages) != 1 for pages in groups.values()):
        _deny("v7_metadata_source_page_invalid")
    specs = {row.instrument_id: row for row in groups["account_instruments"][0].rows}
    leverage = {
        mode: {
            (row.instrument_id, json.loads(row.canonical_json)["posSide"]): row
            for row in groups[f"leverage_{mode}"][0].rows
        }
        for mode in ("cross", "isolated")
    }
    declared = []
    blockers = set(source["blocking_reasons"])
    for instrument_id in packet.plan.leverage_instrument_ids:
        missing = []
        spec_record = specs.get(instrument_id)
        spec_sha = None
        if spec_record is None:
            missing.append("account_instrument_row_missing")
        else:
            spec = json.loads(spec_record.canonical_json)
            spec_sha = journal.digest(spec_record.canonical_json.encode("utf-8"))
            if (
                spec.get("instType") != "SWAP"
                or spec.get("ctType") != "linear"
                or spec.get("settleCcy") != scope.settlement_currency
                or spec.get("state") != "live"
                or spec_record.missing_fields
                or any(
                    not _positive(spec, key)
                    for key in ("ctVal", "ctMult", "lotSz", "minSz", "maxLmtSz")
                )
            ):
                missing.append("account_instrument_terms_unusable")
        mode_rows = {}
        for mode in ("cross", "isolated"):
            selected = {
                side: row
                for (name, side), row in leverage[mode].items()
                if name == instrument_id
            }
            mode_rows[mode] = {
                side: (
                    None
                    if side not in selected
                    else journal.digest(selected[side].canonical_json.encode("utf-8"))
                )
                for side in sides
            }
            if set(selected) != set(sides):
                missing.append(f"{mode}_leverage_side_coverage_missing_or_conflicting")
            if any(
                side not in selected
                or selected[side].missing_fields
                or not _positive(json.loads(selected[side].canonical_json), "lever")
                for side in sides
            ):
                missing.append(f"{mode}_leverage_value_unknown")
        if missing:
            blockers.add("declared_instrument_metadata_incomplete")
        declared.append(
            {
                "instrument_id": instrument_id,
                "instrument_row_sha256": spec_sha,
                "leverage_row_sha256_by_mode_and_side": mode_rows,
                "missing_or_unusable": missing,
            }
        )
    blockers.update(
        {
            "account_wide_instrument_scope_unverified",
            "region_registration_authority_unverified",
            "history_local_exposure_and_protection_unverified",
        }
    )
    receipt = journal.canonical(
        {
            "schema_version": SCHEMA_VERSION,
            "policy_sha256": POLICY_SHA256,
            "source_reference": reference_document,
            "current_source_receipt_sha256": proof.receipt_sha256,
            "scope_sha256": source["scope_sha256"],
            "metadata_page_receipt_sha256s": {
                name: pages[0].receipt_sha256 for name, pages in groups.items()
            },
            "expected_position_sides": list(sides),
            "declared_instruments": declared,
            "declared_metadata_coverage_observed": not any(
                item["missing_or_unusable"] for item in declared
            ),
            "current_observed_flat": source["observed_flat"],
            "blocking_reasons": sorted(blockers),
            "snapshot": None,
            "account_complete": False,
            "source_authenticity_verified": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    return V7MetadataCoverage(receipt)
