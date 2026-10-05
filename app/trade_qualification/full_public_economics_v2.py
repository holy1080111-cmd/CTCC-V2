"""Original and executable projected costs from actual full raw V2 sources."""

import json
from decimal import Context, localcontext

from app.strategies.structural_protection import select_structural_protection
from app.trade_qualification.economics import _INVALID, _evaluate_economics_numeric_tail
from app.trade_qualification.executable_economics import _compare_economics_numeric_tail
from app.trade_qualification.full_public_location_v2 import _location
from app.trade_qualification.full_public_numeric_v2_guard import (
    deny,
    encoded_record,
    record_document,
)
from app.trade_qualification.full_public_numeric_v2_models import (
    FullPublicEconomicsDiagnosticV2,
)
from app.trade_qualification.full_public_numeric_v2_profile import (
    PROFILE_ID,
    PROFILE_SHA256,
)
from app.trade_qualification.full_public_numeric_v2_source import (
    diagnostic_document,
    replay_source,
)


def _cost(source, entry, stop, target):
    quote, policy = source["quote"], source["costs"]
    audit = {}
    try:
        with localcontext(Context(prec=100)):
            code, reason = _evaluate_economics_numeric_tail(
                entry=entry,
                direction=source["original"].intent.direction,
                stop=stop,
                target=target,
                bid=quote.ticker.bid,
                ask=quote.ticker.ask,
                funding_rate=quote.funding.forecast.rate,
                checked_policy=policy,
                evidence=audit,
            )
    except _INVALID:
        for key in ("cost_per_base", "gross_rr", "net_rr"):
            audit.pop(key, None)
        code, reason = (
            "economics_arithmetic_invalid",
            "Bounded cost arithmetic could not be validated.",
        )
    return {
        "code": code,
        "reason": reason,
        "math_checks_passed": code == "passed",
        "entry": str(entry),
        "stop_loss": str(stop),
        "take_profit": str(target),
        "audit": {key: str(value) for key, value in audit.items()},
    }, audit


def _comparison(source, candidate, execution, original_entry, reference, stop):
    values = {
        "entry_delta_per_base": None,
        "adverse_entry_delta_per_base": None,
        "adverse_entry_delta_bps": None,
        "candidate_cost_adjusted_risk_per_base": None,
        "execution_cost_adjusted_risk_per_base": None,
        "worst_cost_adjusted_risk_per_base": None,
        "net_rr_delta": None,
        "worst_net_rr": None,
    }
    with localcontext(Context(prec=100)):
        values.update(
            _compare_economics_numeric_tail(
                "entry",
                reference=reference,
                entry=original_entry,
                direction=source["original"].intent.direction,
            )
        )
        for label, audit, entry in (
            ("candidate", candidate, original_entry),
            ("execution", execution, reference),
        ):
            if "cost_per_base" in audit:
                values[f"{label}_cost_adjusted_risk_per_base"] = (
                    _compare_economics_numeric_tail(
                        "cost", entry=entry, stop=stop, cost=audit["cost_per_base"]
                    )
                )
        candidate_risk, execution_risk = (
            values[f"{label}_cost_adjusted_risk_per_base"]
            for label in ("candidate", "execution")
        )
        if candidate_risk is not None and execution_risk is not None:
            values["worst_cost_adjusted_risk_per_base"] = (
                _compare_economics_numeric_tail(
                    "risk", candidate_risk=candidate_risk, execution_risk=execution_risk
                )
            )
        if "net_rr" in candidate and "net_rr" in execution:
            values.update(
                _compare_economics_numeric_tail(
                    "rr",
                    candidate_net=candidate["net_rr"],
                    execution_net=execution["net_rr"],
                )
            )
    return {
        key: str(value) if value is not None else None for key, value in values.items()
    }


def diagnose_full_public_economics_v2(
    public_packet,
    account_packet,
    *,
    strategy,
    expected_public_bundle_sha256,
    expected_account_plan_sha256,
    expected_account_packet_sha256,
    created_at,
    service_deadline,
    profile_id=PROFILE_ID,
    expected_profile_sha256=PROFILE_SHA256,
):
    source = replay_source(
        public_packet,
        account_packet,
        strategy=strategy,
        expected_public_bundle_sha256=expected_public_bundle_sha256,
        expected_account_plan_sha256=expected_account_plan_sha256,
        expected_account_packet_sha256=expected_account_packet_sha256,
        profile_id=profile_id,
        expected_profile_sha256=expected_profile_sha256,
        created_at=created_at,
        service_deadline=service_deadline,
    )
    document = diagnostic_document(source, "economics")
    document["location"], document["code"] = _location(source)
    if document["location"] is None or not document["location"]["math_checks_passed"]:
        if document["code"] == "history_numeric_slice_not_integrated":
            document["action"] = "WAIT"
        return encoded_record(document, FullPublicEconomicsDiagnosticV2)
    intent = source["original"].intent
    with localcontext(Context(prec=100)):
        selection = select_structural_protection(
            source["detection"],
            source["market"],
            source["analysis"],
            observed_at=source["created_at"],
            entry=intent.candidate_entry,
            tick_size=source["rules"].tick_size,
            **source["protection"],
        )
    document["selection"] = json.loads(selection.to_audit_json())
    if not selection.protection_valid:
        document["code"] = "original_structural_selection_rejected"
        return encoded_record(document, FullPublicEconomicsDiagnosticV2)
    if (
        selection.source_sha256 != source["g1"].source_sha256
        or selection.report_id != intent.report_id
        or selection.instrument_id != intent.instrument_id
        or selection.direction != intent.direction
        or selection.reference_entry != intent.candidate_entry
    ):
        deny("numeric_v2_original_selection_binding_mismatch")
    stop, target = (
        selection.selected.stop.final_stop,
        selection.selected.target.final_target,
    )
    reference = (
        source["quote"].ticker.ask
        if intent.direction == "long"
        else source["quote"].ticker.bid
    )
    candidate, candidate_audit = _cost(source, intent.candidate_entry, stop, target)
    execution, execution_audit = _cost(source, reference, stop, target)
    document["economics"] = {
        "candidate": candidate,
        "execution": execution,
        "comparison": _comparison(
            source,
            candidate_audit,
            execution_audit,
            intent.candidate_entry,
            reference,
            stop,
        ),
        "projected_policy": json.loads(
            source["costs"].model_dump_json(round_trip=True)
        ),
        "actual_fee_authenticity": None,
    }
    document["math_checks_passed"] = (
        candidate["math_checks_passed"] and execution["math_checks_passed"]
    )
    document["code"] = (
        candidate["code"] if not candidate["math_checks_passed"] else execution["code"]
    )
    return encoded_record(document, FullPublicEconomicsDiagnosticV2)


def verify_full_public_economics_v2(result, public_packet, account_packet, **inputs):
    record_document(result, FullPublicEconomicsDiagnosticV2)
    replayed = diagnose_full_public_economics_v2(
        public_packet, account_packet, **inputs
    )
    if result.receipt_json != replayed.receipt_json:
        deny("numeric_v2_economics_raw_replay_mismatch")
    return replayed
