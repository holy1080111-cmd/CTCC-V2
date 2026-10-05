"""Raw original V2 location arithmetic; same candidate, no qualifier/issuer."""

from decimal import Context, localcontext

from app.trade_qualification.full_public_numeric_v2_guard import (
    deny,
    encoded_record,
    record_document,
)
from app.trade_qualification.full_public_numeric_v2_models import (
    FullPublicLocationDiagnosticV2,
)
from app.trade_qualification.full_public_numeric_v2_profile import (
    PROFILE_ID,
    PROFILE_SHA256,
)
from app.trade_qualification.full_public_numeric_v2_source import (
    BASE_STRATEGIES,
    diagnostic_document,
    replay_source,
)
from app.trade_qualification.location import _INVALID, _evaluate_location_numeric_tail


def _location(source):
    original, zone, quote = source["original"], source["zone"], source["quote"]
    if original.intent is None:
        return None, source["precursor"]["code"]
    if original.intent.strategy not in BASE_STRATEGIES:
        return None, "history_numeric_slice_not_integrated"
    reference = (
        quote.ticker.ask if original.intent.direction == "long" else quote.ticker.bid
    )
    audit = {}
    if source["created_at"] < zone.created_at:
        code, reason = "entry_zone_not_open", "The source entry zone has not opened."
    elif source["created_at"] >= zone.expires_at:
        code, reason = "entry_zone_expired", "The source entry zone has expired."
    else:
        try:
            with localcontext(Context(prec=100)):
                code, reason = _evaluate_location_numeric_tail(
                    entry=original.intent.candidate_entry,
                    direction=original.intent.direction,
                    zone=zone,
                    reference=reference,
                    bid=quote.ticker.bid,
                    ask=quote.ticker.ask,
                    mark_price=quote.mark.price,
                    audit=audit,
                )
        except _INVALID:
            code, reason = (
                "candidate_entry_invalid",
                "Entry-location arithmetic failed validation.",
            )
    return {
        "code": code,
        "reason": reason,
        "math_checks_passed": code == "passed",
        "reference_price": str(reference),
        "drift_bps": str(audit["drift_bps"]) if "drift_bps" in audit else None,
    }, code


def diagnose_full_public_location_v2(
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
    document = diagnostic_document(source, "location")
    document["location"], document["code"] = _location(source)
    document["math_checks_passed"] = (
        document["location"] is not None and document["location"]["math_checks_passed"]
    )
    if document["code"] == "history_numeric_slice_not_integrated":
        document["action"] = "WAIT"
    return encoded_record(document, FullPublicLocationDiagnosticV2)


def verify_full_public_location_v2(result, public_packet, account_packet, **inputs):
    record_document(result, FullPublicLocationDiagnosticV2)
    replayed = diagnose_full_public_location_v2(public_packet, account_packet, **inputs)
    if result.receipt_json != replayed.receipt_json:
        deny("numeric_v2_location_raw_replay_mismatch")
    return replayed
