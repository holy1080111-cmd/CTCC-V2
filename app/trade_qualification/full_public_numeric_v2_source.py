"""Private full raw replay and original derivation; no caller market or candidate."""

import json
from decimal import Context, localcontext

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.source_primitives import decode, sha
from app.trade_qualification import data, data_v2
from app.trade_qualification import original_candidate_precursor_v2 as precursor
from app.trade_qualification import public_market_collector_v2 as public
from app.trade_qualification.captured_instrument_rules import (
    derive_captured_instrument_rules,
)
from app.trade_qualification.event_models import TriggerDetection
from app.trade_qualification.full_public_numeric_v2_guard import (
    FullPublicNumericV2Error,
    deny,
    exact_utc,
    pin,
    raw_inputs,
)
from app.trade_qualification.full_public_numeric_v2_profile import (
    PROFILE_SHA256,
    fixed_profile,
)
from app.trade_qualification.models import EntryZone

BASE_STRATEGIES = (
    "trend_pullback",
    "breakout_continuation",
    "fvg_return",
    "order_block_return",
)


def replay_source(
    public_packet,
    account_packet,
    *,
    strategy,
    expected_public_bundle_sha256,
    expected_account_plan_sha256,
    expected_account_packet_sha256,
    profile_id,
    expected_profile_sha256,
    created_at,
    service_deadline,
):
    """Every new public API reaches this replay; no supplied previous result."""
    raw_inputs(public_packet, account_packet)
    for value in (
        expected_public_bundle_sha256,
        expected_account_plan_sha256,
        expected_account_packet_sha256,
    ):
        pin(value)
    created, deadline = exact_utc(created_at), exact_utc(service_deadline)
    if type(strategy) is not str or strategy not in precursor.STRATEGY_CATALOG:
        deny("numeric_v2_strategy_invalid")
    try:
        with localcontext(Context(prec=100)):
            policy, costs, protection = fixed_profile(
                profile_id, expected_profile_sha256
            )
            checked, (_, quote, _, _, _, _) = public._parts(public_packet)
            packet = decode(checked.packet_json, public.MAX_PACKET_BYTES)
            if (
                packet["stage"] != "initial_public"
                or packet["barrier_completed_at"] is not None
                or packet["environment"] != "demo"
            ):
                deny("numeric_v2_initial_demo_raw_required")
            arguments = {
                "strategy": strategy,
                "expected_public_bundle_sha256": expected_public_bundle_sha256,
                "expected_account_plan_sha256": expected_account_plan_sha256,
                "expected_account_packet_sha256": expected_account_packet_sha256,
                "data_policy": policy,
                "expected_data_policy_sha256": data._sha(data._canonical(policy)),
                "created_at": created,
                "service_deadline": deadline,
            }
            original = precursor.derive_original_candidate_precursor_v2(
                checked, account_packet, **arguments
            )
            original = precursor.verify_original_candidate_precursor_v2(
                original, checked, account_packet, **arguments
            )
            record = decode(original.receipt_json)
            g1 = data_v2.evaluate_public_market_data_v2(
                checked,
                expected_bundle_sha256=expected_public_bundle_sha256,
                policy=policy,
                evaluated_at=created,
            )
            g1 = data_v2.verify_public_market_data_v2(
                g1,
                checked,
                expected_bundle_sha256=expected_public_bundle_sha256,
                policy=policy,
                evaluated_at=created,
            )
            if record["g1"]["evaluation_sha256"] != g1.evaluation_sha256:
                deny("numeric_v2_original_g1_binding_mismatch")
            rules = derive_captured_instrument_rules(
                account_packet,
                instrument_id=packet["instrument_id"],
                expected_plan_sha256=expected_account_plan_sha256,
                expected_packet_sha256=expected_account_packet_sha256,
            )
            if rules.receipt_sha256 != record["instrument_rules_sha256"]:
                deny("numeric_v2_original_instrument_binding_mismatch")
            projection = (
                decode(g1.source_json.encode(), data.MAX_SOURCE_BYTES)
                if g1.source_json is not None
                else None
            )
            market = (
                MarketSnapshot.model_validate_json(
                    json.dumps(projection["market"]), strict=True
                )
                if projection is not None
                else None
            )
            analysis = (
                MultiTimeframeAnalysis.model_validate_json(
                    json.dumps(projection["analysis"]), strict=True
                )
                if projection is not None
                else None
            )
            detection = (
                TriggerDetection.model_validate_json(
                    json.dumps(record["detection"]), strict=True
                )
                if record["detection"] is not None
                else None
            )
            zone = (
                EntryZone.model_validate_json(json.dumps(record["zone"]), strict=True)
                if record["zone"] is not None
                else None
            )
    except FullPublicNumericV2Error:
        raise
    except Exception:  # noqa: BLE001 -- replay errors must not disclose private source values
        raise FullPublicNumericV2Error("numeric_v2_raw_source_denied") from None
    return {
        "packet": packet,
        "quote": quote,
        "g1": g1,
        "original": original,
        "precursor": record,
        "rules": rules,
        "market": market,
        "analysis": analysis,
        "detection": detection,
        "zone": zone,
        "costs": costs,
        "protection": protection,
        "created_at": created,
        "deadline": deadline,
    }


def diagnostic_document(source, kind):
    original, g1 = source["precursor"], source["g1"]
    return {
        "schema_version": f"ctcc.full_public_{kind}_diagnostic.v2",
        "kind": kind,
        "profile_sha256": PROFILE_SHA256,
        "bindings": {
            "public_bundle_sha256": original["public_bundle_sha256"],
            "public_invocation_id": original["public_invocation_id"],
            "account_plan_sha256": original["account_plan_sha256"],
            "account_packet_sha256": original["account_packet_sha256"],
            "account_identity_sha256": original["account_identity_sha256"],
            "instrument_rules_sha256": original["instrument_rules_sha256"],
            "instrument_raw_row_sha256": original["instrument_raw_row_sha256"],
            "event_key": original["event_key"],
            "zone_sha256": sha(data._canonical(source["zone"]))
            if source["zone"] is not None
            else None,
            "created_at": original["created_at"],
            "service_deadline": original["service_deadline"],
            "fixed_expires_at": original["expires_at"],
            "strategy": original["strategy"],
            "initial_entry": original["initial_entry"],
            "direction": original["direction"],
            "quote_bundle_sha256": g1.quote_bundle_sha256,
            "quote_profile_sha256": g1.quote_profile_sha256,
            "quote_transport_policy_sha256": g1.quote_transport_policy_sha256,
            "projected_economics_policy_sha256": sha(
                source["costs"].model_dump_json(round_trip=True).encode()
            ),
            "quote_inspection_json": g1.quote_inspection_json,
            "quote_inspection_sha256": g1.quote_inspection_sha256,
            "funding_pair": decode(g1.funding_pair_json.encode()),
            "funding_pair_sha256": g1.funding_pair_sha256,
        },
        "precursor": original,
        "g1": json.loads(g1.model_dump_json(round_trip=True)),
        "location": None,
        "selection": None,
        "economics": None,
        "math_checks_passed": False,
        "action": original["action"],
        "code": original["code"],
        "admission": "DENY",
        "execution_authority": False,
        "qualification_performed": False,
        "execution_recheck_performed": False,
        "native_clock_verified": False,
        "original_source_verified": False,
        "account_complete": False,
        "atomic_risk_reserved": False,
        "metadata_current_owned": False,
        "unknown": {
            "quantity": None,
            "leverage": None,
            "portfolio_risk": None,
            "actual_exchange_fee_source": None,
            "fee_authenticity": None,
            "rate_generated_at": None,
            "historical_first_available_at": None,
            "native_production_profile": None,
        },
    }
