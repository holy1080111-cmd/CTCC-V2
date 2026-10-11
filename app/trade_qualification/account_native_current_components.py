"""Source-owned V6/V7 current Demo components, without portfolio authority.

The one-use native packet carrier is consumed before deriving anything.  This
receipt records measured current operands and explicit missing dependencies;
it cannot become a PortfolioRiskSnapshot or an account revision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_api_key_policy as key_policy
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_consistency as consistency
from app.trade_qualification import account_materializer as materializer
from app.trade_qualification import account_native_runtime as native
from app.trade_qualification import account_observation_index as observed


class NativeCurrentComponentsError(ValueError):
    """Fixed denial only; raw account records never enter exception text."""


POLICY_BYTES = canonical(
    {
        "version": "ctcc.native_demo_current_components.v1",
        "source": "one_use_native_v6_raw_packet_with_original_proof_readback",
        "balance": "settlement_currency_eq_and_availEq_not_total_USD",
        "current_inventory": "all_exact_unfiltered_terminal_v6_streams",
        "missing": "null_and_explicit_blocker_never_zero_or_empty",
        "history_and_local_state": "unknown_until_separately_source_owned",
        "portfolio_snapshot": False,
        "account_revision_published": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)
V7_POLICY_BYTES = canonical(
    {
        "version": "ctcc.native_demo_current_components.v2",
        "source": "requires_one_use_native_v7_raw_packet_with_original_proof_readback",
        "current_capture_contract": "ctcc.demo_current_account_plan.v7",
        "current_streams": list(capture.V7_CURRENT_STREAMS),
        "algo_order_types": list(capture.CURRENT_ALGO_ORDER_TYPES_V7),
        "balance": "settlement_currency_eq_and_availEq_not_total_USD",
        "current_inventory": "all_exact_unfiltered_terminal_v7_streams",
        "missing": "null_and_explicit_blocker_never_zero_or_empty",
        "history_and_local_state": "unknown_until_separately_source_owned",
        "portfolio_snapshot": False,
        "account_revision_published": False,
        "execution_authority": False,
    }
)
V7_POLICY_SHA256 = sha(V7_POLICY_BYTES)
V7_KEY_POLICY_BYTES = canonical(
    {
        "version": "ctcc.native_demo_current_components.v3",
        "base_policy_sha256": V7_POLICY_SHA256,
        "api_key_policy_sha256": key_policy.POLICY_SHA256,
        "source": "same_one_use_native_v7_packet_and_original_proof_readback",
        "api_key_fields": "replayed_original_config_before_and_after_without_raw_values",
        "unverified": [
            "registration_region",
            "provider_credential_session",
            "current_egress_ip",
        ],
        "portfolio_snapshot": False,
        "account_revision_published": False,
        "execution_authority": False,
    }
)
V7_KEY_POLICY_SHA256 = sha(V7_KEY_POLICY_BYTES)

_UNKNOWN = (
    "complete_realized_outcome_window_unknown",
    "daily_and_rolling_loss_unknown",
    "loss_streak_seed_unknown",
    "funding_accrual_unknown",
    "fees_and_rebates_unknown",
    "measured_high_water_mark_unknown",
    "local_reservations_unknown",
    "local_inflight_and_uncertain_unknown",
    "active_protection_coverage_unknown",
    "correlation_and_cost_mapping_unknown",
    "account_atomic_revision_unknown",
    "registration_region_authority_unknown",
)


@dataclass(frozen=True, slots=True, repr=False)
class NativeCurrentComponents:
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
    def execution_authority(self) -> bool:
        return False


def _fraction(value):
    if value is None:
        return None
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def observe_native_demo_current_components(
    diagnostic, session
) -> NativeCurrentComponents:
    """Burn the source-owned lease and project only current measured operands.

    No caller-supplied packet, receipt, balance or completeness flag is trusted.
    Even a fully empty current inventory is only a bounded observation, not a
    flat-start permission; missing history/local state is never set to zero.
    """
    try:
        source = native._consume_native_demo_raw_packet(diagnostic, session)
        return _project(source)
    except NativeCurrentComponentsError:
        raise
    except Exception:  # noqa: BLE001 -- never expose source body, UID or credentials
        raise NativeCurrentComponentsError(
            "native_current_components_unavailable"
        ) from None


def observe_native_demo_current_components_v3(
    diagnostic, session
) -> NativeCurrentComponents:
    """Burn the V7 native lease and bind key policy to that same source.

    The earlier V2 receipt contract stays byte-for-byte unchanged. This new
    observation records only redacted config policy; it cannot verify the
    account's registration site, provider credential session, or egress IP.
    """
    try:
        source = native._consume_native_demo_raw_packet(diagnostic, session)
        return _project_v3(source)
    except NativeCurrentComponentsError:
        raise
    except Exception:  # noqa: BLE001 -- never expose source body, UID or credentials
        raise NativeCurrentComponentsError(
            "native_current_components_unavailable"
        ) from None


def _project_v3(source) -> NativeCurrentComponents:
    if (
        type(source) is not native._ObservedNativeDemoAccountRawPacket
        or type(source.packet.plan) is not capture.CurrentDemoAccountCapturePlanV7
        or source.packet.schema_version != "ctcc.demo_current_account_capture.v7"
    ):
        raise NativeCurrentComponentsError("native_current_components_v7_required")
    base = _project(source)
    base_value = json.loads(base.receipt_json)
    if (
        base_value["schema_version"] != "ctcc.native_demo_current_components.v2"
        or base_value["policy_sha256"] != V7_POLICY_SHA256
        or base_value["native_receipt_sha256"] != source.receipt_sha256
        or base_value["native_proof_sha256"] != source.proof_sha256
        or base_value["native_readback_sha256"] != source.readback_sha256
    ):
        raise NativeCurrentComponentsError("native_current_components_source_invalid")
    frozen = capture.freeze_demo_account_packet(
        source.packet, expected_plan_sha256=source.reference.plan_sha256
    )
    if frozen.sha256 != source.reference.packet_sha256:
        raise NativeCurrentComponentsError("native_current_components_source_invalid")
    observed = key_policy.observe_v7_demo_api_key_policy(
        frozen.payload,
        expected_packet_sha256=frozen.sha256,
        expected_plan_sha256=source.reference.plan_sha256,
        expected_policy_sha256=key_policy.POLICY_SHA256,
    )
    policy_value = json.loads(observed.receipt_json)
    reasons = policy_value["blocking_reasons"]
    base_value.update(
        {
            "schema_version": "ctcc.native_demo_current_components.v3",
            "policy_sha256": V7_KEY_POLICY_SHA256,
            "base_v2_receipt_sha256": base.receipt_sha256,
            "api_key_policy": {
                "observation_scope": "same_one_use_native_v7_packet_and_proof_readback",
                "policy_sha256": key_policy.POLICY_SHA256,
                "original_policy_receipt_sha256": observed.receipt_sha256,
                "permissions_observed": policy_value["permissions_observed"],
                "ip_binding_list_observed": policy_value["ip_binding_list_observed"],
                "config_policy_consistent": policy_value["config_policy_consistent"],
                "blocking_reasons": reasons,
                "registration_region_verified": False,
                "provider_credential_session_verified": False,
                "current_egress_ip_verified": False,
            },
            "blocking_reasons": sorted(
                set(base_value["blocking_reasons"])
                | {"api_key_" + reason for reason in reasons}
                | {
                    "api_key_registration_region_unverified",
                    "api_key_provider_credential_session_unverified",
                    "api_key_current_egress_ip_unverified",
                }
            ),
        }
    )
    return NativeCurrentComponents(canonical(base_value))


def _project(source) -> NativeCurrentComponents:
    if type(source) is not native._ObservedNativeDemoAccountRawPacket:
        raise NativeCurrentComponentsError("native_current_components_source_invalid")
    packet, reference = source.packet, source.reference
    if type(packet) is not capture.DemoAccountPacket:
        raise NativeCurrentComponentsError("native_current_components_source_invalid")
    contract = {
        (
            capture.CurrentDemoAccountCapturePlanV6,
            "ctcc.demo_current_account_capture.v6",
        ): (
            capture.V6_CURRENT_STREAMS,
            "ctcc.native_demo_current_components.v1",
            POLICY_SHA256,
        ),
        (
            capture.CurrentDemoAccountCapturePlanV7,
            "ctcc.demo_current_account_capture.v7",
        ): (
            capture.V7_CURRENT_STREAMS,
            "ctcc.native_demo_current_components.v2",
            V7_POLICY_SHA256,
        ),
    }.get((type(packet.plan), packet.schema_version))
    if (
        contract is None
        or type(reference) is not observed.CaptureReference
        or packet.plan_sha256 != reference.plan_sha256
        or capture.freeze_demo_account_packet(
            packet, expected_plan_sha256=reference.plan_sha256
        ).sha256
        != reference.packet_sha256
        or packet.account_complete is not False
        or packet.execution_authority is not False
        or packet.source_authenticity_verified is not False
        or source.admission != "DENY"
        or source.account_complete is not False
        or source.execution_authority is not False
        or source.source_authenticity_verified is not False
    ):
        raise NativeCurrentComponentsError("native_current_components_source_invalid")
    streams, receipt_schema, policy_sha256 = contract
    groups = {stream: [] for stream in streams}
    for page in packet.observations:
        if page.request.stream not in groups:
            raise NativeCurrentComponentsError(
                "native_current_components_inventory_incomplete"
            )
        groups[page.request.stream].append(page)
    if any(not pages or pages[-1].terminal is not True for pages in groups.values()):
        raise NativeCurrentComponentsError(
            "native_current_components_inventory_incomplete"
        )
    if any(
        any(
            name in {"instId", "instType", "ccy"} for name, _ in page.request.parameters
        )
        for stream, pages in groups.items()
        if stream in {"positions", "orders_pending"} or stream.startswith("algo_")
        for page in pages
    ):
        raise NativeCurrentComponentsError(
            "native_current_components_filtered_inventory"
        )
    config_pages = groups["config_before"] + groups["config_after"]
    if any(len(page.rows) != 1 for page in config_pages):
        raise NativeCurrentComponentsError(
            "native_current_components_identity_incomplete"
        )
    before, after = (json.loads(page.rows[0].canonical_json) for page in config_pages)
    config_changed = (
        config_pages[0].rows[0].canonical_json != config_pages[1].rows[0].canonical_json
    )
    identity = ("uid", "mainUid", "acctLv", "posMode")
    if (
        any(before.get(name) != after.get(name) for name in identity)
        or after.get("uid") != packet.plan.expected_uid
        or after.get("mainUid") != packet.plan.expected_main_uid
    ):
        raise NativeCurrentComponentsError(
            "native_current_components_identity_conflict"
        )
    balance_pages = groups["balance"]
    if len(balance_pages) != 1 or len(balance_pages[0].rows) != 1:
        raise NativeCurrentComponentsError(
            "native_current_components_balance_incomplete"
        )
    balance = json.loads(balance_pages[0].rows[0].canonical_json)
    equity, available, balance_gap = materializer._balance(
        balance, packet.plan.settlement_currency
    )
    cross = consistency._reconcile_verified_packet(packet, reference.packet_sha256)
    counts = {
        stream: sum(len(page.rows) for page in groups[stream])
        for stream in (
            "positions",
            "orders_pending",
            *(name for name in streams if name.startswith("algo_")),
        )
    }
    missing_fields = any(
        set(row.missing_fields)
        - (
            {"availEq"}
            if after["acctLv"] == "2" and stream == "balance"
            else {"adjEq"}
            if after["acctLv"] == "2" and stream == "account_position_risk"
            else set()
        )
        for stream, pages in groups.items()
        for page in pages
        for row in page.rows
    )
    blockers = set(_UNKNOWN)
    if config_changed:
        blockers.add("current_account_config_changed")
    if balance_gap is not None:
        blockers.add(balance_gap)
    blockers.update(cross.blocking_reasons)
    if after.get("acctLv") != "2" or after.get("posMode") not in {
        "net_mode",
        "long_short_mode",
    }:
        blockers.add("current_account_mode_unsupported")
    if any(counts.values()):
        blockers.add("current_exchange_exposure_present")
    if missing_fields:
        blockers.add("current_source_fields_missing_or_inapplicable")
    inventory_empty = (
        None
        if config_changed or missing_fields or cross.blocking_reasons
        else not any(counts.values())
    )
    receipt = canonical(
        {
            "schema_version": receipt_schema,
            "policy_sha256": policy_sha256,
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
            "native_receipt_sha256": source.receipt_sha256,
            "native_proof_sha256": source.proof_sha256,
            "native_readback_sha256": source.readback_sha256,
            "observed_at": source.observed_at.isoformat(),
            "expires_at": source.expires_at.isoformat(),
            "account_mode": after["acctLv"],
            "position_mode": after["posMode"],
            "current_balance": {
                "settlement_currency": packet.plan.settlement_currency,
                "equity": _fraction(equity),
                "available_equity": _fraction(available),
                "source_page_receipt_sha256": balance_pages[0].receipt_sha256,
            },
            "current_inventory_row_counts": counts,
            "current_inventory_page_receipts": {
                stream: [page.receipt_sha256 for page in groups[stream]]
                for stream in counts
            },
            "current_inventory_row_sha256": {
                stream: [
                    sha(row.canonical_json.encode("utf-8"))
                    for page in groups[stream]
                    for row in page.rows
                ]
                for stream in counts
            },
            "current_inventory_observed_empty": inventory_empty,
            "consistency_findings": len(cross.findings),
            "blocking_reasons": sorted(blockers),
            "history_complete": None,
            "local_exposure_complete": None,
            "active_protection_complete": None,
            "snapshot": None,
            "account_complete": False,
            "account_revision_published": False,
            "source_authenticity_verified": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    return NativeCurrentComponents(receipt)
