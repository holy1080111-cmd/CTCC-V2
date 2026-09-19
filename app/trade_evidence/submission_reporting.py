"""Deterministic post-submit facts, never permission to submit or retry.

Raw response bytes stay in the private database capture. Only the existing v1
allowlisted report reaches the file outbox. Response consistency does not prove
authenticated transport, fill, protection, or source truth.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from itertools import pairwise
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.trade_evidence import forensics, outbox, post_submit, storage
from app.trade_qualification import reservations, submission_intent


class SubmissionReportingError(ValueError):
    """Static, redacted reporting errors."""


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def wire(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def pin(value):
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise SubmissionReportingError("submission_reporting_invalid_pin")
    return value


def queue(value):
    if type(value) is not str or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None:
        raise SubmissionReportingError("submission_reporting_invalid_queue")
    return value


class CapturedSubmission(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", revalidate_instances="always"
    )
    exchange_request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_started_at: datetime
    completed_at: datetime
    headers_received_at: datetime | None = None
    body_completed_at: datetime | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    raw_body: bytes | None = Field(default=None, max_length=65536, repr=False)
    transport_complete: bool = False

    @model_validator(mode="before")
    @classmethod
    def exact_scalars(cls, value):
        if type(value) is not dict:
            value = dict(post_submit._exact(value, cls))
        for name, part in value.items():
            if type(name) is not str:
                raise ValueError("capture_exact_field_name_required")
            if name.endswith("_at") and part is not None:
                if type(part) is not datetime or (
                    type(part.tzinfo) is not timezone
                    and type(part.tzinfo) is not ZoneInfo
                ):
                    raise ValueError("capture_exact_known_datetime_required")
                post_submit._utc(part)
            elif name == "raw_body" and part is not None and type(part) is not bytes:
                raise ValueError("capture_exact_bytes_required")
            elif name == "http_status" and part is not None and type(part) is not int:
                raise ValueError("capture_exact_status_required")
            elif name == "transport_complete" and type(part) is not bool:
                raise ValueError("capture_exact_bool_required")
            elif name == "exchange_request_sha256" and type(part) is not str:
                raise ValueError("capture_exact_pin_required")
        return value

    @model_validator(mode="after")
    def clocks(self):
        stamps = [self.request_started_at]
        for value in (
            self.headers_received_at,
            self.body_completed_at,
            self.completed_at,
        ):
            if value is not None:
                stamps.append(value)
        if any(
            type(item) is not datetime
            or item.tzinfo is None
            or item.utcoffset() is None
            for item in stamps
        ):
            raise ValueError("capture_aware_clock_required")
        if any(a > b for a, b in pairwise(stamps)):
            raise ValueError("capture_clock_order")
        if self.body_completed_at is not None and self.headers_received_at is None:
            raise ValueError("capture_header_receipt_missing")
        if self.transport_complete and any(
            item is None
            for item in (
                self.headers_received_at,
                self.body_completed_at,
                self.http_status,
                self.raw_body,
            )
        ):
            raise ValueError("complete_capture_missing_body")
        return self


def checked_capture(value):
    try:
        return CapturedSubmission.model_validate(
            dict(post_submit._exact(value, CapturedSubmission)), strict=True
        )
    except (ValueError, TypeError):
        raise SubmissionReportingError("submission_capture_invalid") from None


def freeze_capture(value) -> str:
    value = checked_capture(value)
    body = {
        name: getattr(value, name)
        for name in CapturedSubmission.model_fields
        if name != "raw_body"
    }
    for name in (
        "request_started_at",
        "headers_received_at",
        "body_completed_at",
        "completed_at",
    ):
        if body[name] is not None:
            body[name] = body[name].astimezone(UTC).isoformat()
    body["raw_body_hex"] = None if value.raw_body is None else value.raw_body.hex()
    body["raw_body_sha256"] = None if value.raw_body is None else sha(value.raw_body)
    body["version"] = "ctcc.private.submit-capture.v1"
    return wire(body)


def decode_capture(raw, expected_sha256):
    try:
        if (
            type(raw) is not str
            or not 0 < len(raw.encode()) <= 140000
            or sha(raw.encode()) != pin(expected_sha256)
        ):
            raise ValueError("capture_pin")
        body = json.loads(
            raw,
            object_pairs_hook=storage._unique_object,
            parse_constant=storage._json_constant,
        )
        if body.pop("version") != "ctcc.private.submit-capture.v1":
            raise ValueError("version")
        encoded = body.pop("raw_body_hex")
        body_pin = body.pop("raw_body_sha256")
        body["raw_body"] = None if encoded is None else bytes.fromhex(encoded)
        if body_pin != (None if body["raw_body"] is None else sha(body["raw_body"])):
            raise ValueError("body_pin")
        for name in (
            "request_started_at",
            "headers_received_at",
            "body_completed_at",
            "completed_at",
        ):
            if body[name] is not None:
                body[name] = datetime.fromisoformat(body[name])
        result = CapturedSubmission.model_validate(body, strict=True)
        if freeze_capture(result) != raw:
            raise ValueError("canonical")
        return result
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise SubmissionReportingError("submission_capture_readback_invalid") from None


def policy_record(policy):
    try:
        checked = outbox._copy(policy, outbox.OutboxPolicy)
        raw = wire(checked.model_dump(mode="json"))
        return checked, raw, sha(raw.encode())
    except (ValueError, TypeError):
        raise SubmissionReportingError("submission_policy_invalid") from None


def classify(capture, client_id, deadline):
    if capture.request_started_at >= deadline:
        return "uncertain", None, None, "request_started_after_expiry"
    if not capture.transport_complete or capture.http_status != 200:
        return "uncertain", None, None, "transport_unresolved"
    try:
        value = json.loads(
            capture.raw_body,
            object_pairs_hook=storage._unique_object,
            parse_constant=storage._json_constant,
        )
        if (
            type(value) is not dict
            or type(value.get("data")) is not list
            or len(value["data"]) != 1
        ):
            raise ValueError("one_ack_required")
        row = value["data"][0]
        code, top, order = row["sCode"], value["code"], row["ordId"]
        if (
            type(row) is not dict
            or row["clOrdId"] != client_id
            or any(type(item) is not str for item in (code, top, order))
            or any(
                re.fullmatch(r"0|[1-9][0-9]{0,15}", item) is None
                for item in (code, top)
            )
        ):
            raise ValueError("ack_binding")
        if code == top == "0" and re.fullmatch(r"[0-9]{1,64}", order):
            return "acknowledged", order, "0", "exact_order_ack_observed"
        if code != "0" and top != "0" and order == "":
            return "rejected", None, code, "exact_order_rejection_observed"
    except (ValueError, TypeError, KeyError, IndexError, RecursionError, UnicodeError):
        pass
    return "uncertain", None, None, "ambiguous_or_mismatched_response"


@dataclass(frozen=True, slots=True, repr=False)
class PreparedSubmission:
    capture_json: str
    capture_sha256: str
    report_bytes: bytes
    report_sha256: str
    status: str
    reason: str
    consumed: reservations.ReservationReceipt
    intent: submission_intent.SubmissionIntentRecord
    binding: dict


def prepare_submission(request, intent_raw, *, intent_sha256, capture):
    """Reconstruct report only from independently replayed immutable v2 lineage."""
    try:
        capture = checked_capture(capture)
        intent, consumed = submission_intent.replay_submission_intent(
            intent_raw, request, expected_sha256=pin(intent_sha256)
        )
        body = json.loads(intent.canonical_json)
        if (
            body["version"] != "ctcc-demo-submit-intent-v2"
            or capture.exchange_request_sha256 != body["exchange_request_sha256"]
            or capture.request_started_at < consumed.updated_at
        ):
            raise ValueError("exact_v2_request_required")
        origin = request.origin
        candidate = origin.candidate
        zone = candidate.entry_zone
        forensic = forensics.TradeCandidate(
            report_id=candidate.report_id,
            instrument_id=consumed.instrument_id,
            account_id=consumed.scope.account_id,
            direction=consumed.direction,
            settlement_currency=consumed.scope.settlement_currency,
            contract_value_base=consumed.coverage.candidate.contract_value,
            planned_contracts=consumed.coverage.candidate.contracts,
            entry=candidate.candidate_entry,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            recorded_at=candidate.evaluated_at,
            original_evidence_sha256=origin.evidence.receipt.report_sha256,
            original_source_sha256=origin.original_source_sha256,
            entry_deadline=origin.deadline,
            entry_zone_low=zone.zone_low,
            entry_zone_high=zone.zone_high,
        )
        status, order, code, reason = classify(
            capture, body["client_order_id"], origin.deadline
        )
        report = post_submit.DemoSubmissionReport(
            candidate=forensic,
            candidate_sha256=forensics.candidate_sha256(forensic),
            reservation_before_submit=consumed,
            reservation_sha256=reservations.digest(consumed),
            original_event_key=origin.original_event_key,
            strategy=candidate.strategy,
            client_order_id=body["client_order_id"],
            evidence_completed_at=origin.publication_completed_at,
            submit_started_at=capture.request_started_at,
            completed_at=capture.completed_at,
            status=status,
            exchange_order_id=order,
            exchange_code=code,
        )
        raw = post_submit.freeze_submission_report(report)
        captured = freeze_capture(capture)
        binding = {
            name: body[name]
            for name in (
                "exchange_request_sha256",
                "execution_binding_sha256",
                "client_order_id",
                "protection_client_order_id",
                "evidence_sha256",
                "recheck_sha256",
                "account_packet_sha256",
                "account_plan_sha256",
                "economics_sha256",
                "margin_mode",
                "position_mode",
                "executable_limit",
                "candidate_entry",
                "stop_loss",
                "take_profit",
                "contracts",
                "leverage",
            )
        }
        binding.update(
            original_event_key=origin.original_event_key,
            original_source_sha256=origin.original_source_sha256,
            original_policy_sha256=origin.original_policy_sha256,
            evidence_report_sha256=origin.evidence.receipt.report_sha256,
            forensic_candidate_sha256=report.candidate_sha256,
            consumed_receipt_sha256=reservations.digest(consumed),
            reservation_request_sha256=consumed.request_sha256,
        )
        return PreparedSubmission(
            captured,
            sha(captured.encode()),
            raw,
            sha(raw),
            status,
            reason,
            consumed,
            intent,
            binding,
        )
    except Exception:  # noqa: BLE001 -- Never expose response bytes or account UID.
        raise SubmissionReportingError(
            "submission_lineage_or_capture_invalid"
        ) from None
