"""Read-only V6 exposure observation from an original recorded source chain.

This is deliberately separate from the flat-only native companion proof.  It
preserves the exact current page and row identities while leaving native clock
replay, history, protection, local liabilities and risk authority unproved.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope


class NativeExposedObservationError(ValueError):
    """Fixed error codes; private row bodies never enter an exception."""


POLICY_BYTES = canonical(
    {
        "version": "ctcc.native_demo_exposed_observation.v1",
        "source": "original_v6_recorded_chain_replay_and_exact_packet",
        "required": "one_or_more_observed_current_exposure_rows",
        "native_companion_proof_verified": False,
        "history_protection_and_local_join": "unknown",
        "account_complete": False,
        "flat_start_permission": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)
V2_POLICY_BYTES = canonical(
    {
        "version": "ctcc.native_demo_exposed_observation.v2",
        "recorded_observation_policy_sha256": POLICY_SHA256,
        "native_companion_policy_sha256": proof.EXPOSED_V4_POLICY_SHA256,
        "native_companion_readback": "separate_no_clobber_original_root_readback",
        "source_authenticity_verified": False,
        "history_protection_and_local_join": "unknown",
        "account_complete": False,
        "flat_start_permission": False,
        "execution_authority": False,
    }
)
V2_POLICY_SHA256 = sha(V2_POLICY_BYTES)

_UNKNOWN = (
    "native_clock_companion_proof_missing",
    "registration_region_authority_unknown",
    "source_authenticity_unverified",
    "exchange_global_atomic_revision_unknown",
    "complete_history_and_realized_outcome_unknown",
    "funding_accrual_and_fees_unknown",
    "daily_rolling_loss_and_streak_unknown",
    "measured_high_water_mark_unknown",
    "local_reservations_intents_and_uncertain_unknown",
    "active_protection_coverage_unknown",
    "correlation_and_cost_mapping_unknown",
)


@dataclass(frozen=True, slots=True, repr=False)
class NativeExposedAccountObservation:
    receipt_json: bytes

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
    def flat_start_permission(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def admission(self) -> str:
        return "DENY"


def observe_recorded_native_v6_exposure(
    *,
    chain,
    reference,
    packet,
    scope,
    validated_at,
) -> NativeExposedAccountObservation:
    """Replay original bytes and publish hashes/counts only, never a permit.

    The runtime calls this only after its owned collector closes and it rereads
    the original account journal under the exact account lock. A standalone
    replay has no way to attest that native acquisition or clock proof occurred;
    the receipt says so explicitly even when its recorded chain is valid.
    """
    try:
        if (
            type(scope) is not LedgerScope
            or type(validated_at) is not datetime
            or type(packet) is not capture.DemoAccountPacket
            or type(packet.plan) is not capture.CurrentDemoAccountCapturePlanV6
            or type(reference) is not observed.CaptureReference
        ):
            raise NativeExposedObservationError("native_exposure_inputs_invalid")
        replay_reference, replay_packet, _records, _joins = proof._source(
            chain, scope, proof_schema=proof.V3_SCHEMA
        )
        if replay_reference != reference or replay_packet != packet:
            raise NativeExposedObservationError("native_exposure_source_changed")
        verification = current.verify_current_account_sources(
            chain,
            reference=reference,
            scope=scope,
            validated_at=validated_at,
            expected_policy_sha256=current.V6_POLICY_SHA256,
        )
        source = json.loads(verification.receipt_json)
        counts = source["inventory_row_counts"]
        if (
            set(counts) != set(current.INVENTORY_STREAMS)
            or any(type(value) is not int or value < 0 for value in counts.values())
            or not any(counts.values())
            or source["observed_flat"] is not False
            or "current_exposure_requires_protection_and_local_join"
            not in source["blocking_reasons"]
        ):
            raise NativeExposedObservationError("native_exposure_not_observed")
        pages = [
            {
                "stream": page["stream"],
                "request_index": page["request_index"],
                "page_index": page["page_index"],
                "previous_page_sha256": page["previous_page_sha256"],
                "body_sha256": page["body_sha256"],
                "receipt_sha256": page["receipt_sha256"],
                "row_count": page["row_count"],
                "terminal": page["terminal"],
            }
            for page in source["current_pages"]
        ]
        rows = [
            {
                "stream": row["stream"],
                "request_index": row["request_index"],
                "row_ordinal": row["row_ordinal"],
                "row_sha256": row["row_sha256"],
                "page_receipt_sha256": row["page_receipt_sha256"],
            }
            for row in source["current_rows"]
        ]
        receipt = canonical(
            {
                "schema_version": "ctcc.native_demo_exposed_observation.v1",
                "policy_sha256": POLICY_SHA256,
                "account_scope_sha256": sha(
                    canonical(
                        [
                            packet.plan.environment,
                            packet.plan.expected_uid,
                            packet.plan.expected_main_uid,
                            packet.plan.settlement_currency,
                            packet.plan.session_binding_id,
                        ]
                    )
                ),
                "source_reference": observed.reference_document(reference),
                "recorded_current_source_receipt_sha256": verification.receipt_sha256,
                "recorded_source_chain_replayed": True,
                "source_interval": source["observation_interval"],
                "current_inventory_row_counts": counts,
                "current_pages": pages,
                "current_rows": rows,
                "blocking_reasons": sorted(
                    set(source["blocking_reasons"]) | set(_UNKNOWN)
                ),
                "current_exposure_rows_observed": True,
                "native_companion_proof_verified": False,
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
        return NativeExposedAccountObservation(receipt)
    except NativeExposedObservationError:
        raise
    except Exception:  # noqa: BLE001 -- private source and chain errors remain private
        raise NativeExposedObservationError(
            "native_exposure_observation_unavailable"
        ) from None
