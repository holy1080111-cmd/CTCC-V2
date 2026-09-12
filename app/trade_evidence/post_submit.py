"""Post-submit reporting handoff, separate from every order wait path.

This module has no order client, order callback, scheduler or environment lookup.
An acknowledged Demo result can enqueue metadata using the existing durable
outbox; a separately invoked one-pass worker uses the existing Notion adapter.
Acknowledgement means neither a fill nor confirmed protection or profitability.

The original candidate and a consumed pre-submit reservation receipt are checked
against external pins. These are consistency checks, NOT authenticated exchange
or disk evidence. The caller must persist report/client-order identity BEFORE
submission and retain unresolved intents. A post-submit-only function cannot
repair a crash before it is called. Unknown submissions never enter the confirmed
submission outbox; report bytes can be retained in that existing upstream ledger.
No new store, uncertain-order queue, implicit retry or order authority is added.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.domain.okx_demo import OkxDemoOrderAcknowledgement, OkxDemoWriteResult
from app.trade_evidence import forensics, outbox, outbox_worker, storage
from app.trade_evidence.notion_adapter import NotionDeliveryAdapter
from app.trade_qualification import reservations

MAX_REPORT_BYTES = 32768
SubmissionStatus = Literal["acknowledged", "rejected", "uncertain"]
ClientOrderId = Annotated[str, Field(pattern=r"^[A-Za-z0-9]{1,32}$")]
OrderId = Annotated[str, Field(pattern=r"^[A-Za-z0-9]{1,64}$")]
ExchangeCode = Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]{0,15})$")]


class PostSubmitError(ValueError):
    """Static error codes; the known report ID is preserved where available."""

    def __init__(self, code: str, report_id: str | None = None):
        self.code = code
        self.report_id = report_id
        super().__init__(code)


def _utc(value):
    if type(value) is not datetime or (
        type(value.tzinfo) is not timezone and type(value.tzinfo) is not ZoneInfo
    ):
        raise ValueError("post_submit_clock_invalid")
    try:
        # A nonexistent local time must not become a fabricated UTC observation.
        result = value.astimezone(UTC)
        if result.astimezone(value.tzinfo).replace(tzinfo=None) != value.replace(
            tzinfo=None
        ):
            raise ValueError("post_submit_clock_invalid")
        return result
    except (ValueError, OverflowError):
        raise ValueError("post_submit_clock_invalid") from None


def _exact(value, expected):
    if type(value) is not expected:
        raise ValueError("post_submit_exact_model_required")
    raw = object.__getattribute__(value, "__dict__")
    supplied = object.__getattribute__(value, "__pydantic_fields_set__")
    if (
        type(raw) is not dict
        or type(supplied) is not set
        or object.__getattribute__(value, "__pydantic_extra__") is not None
        or object.__getattribute__(value, "__pydantic_private__") is not None
        or len(raw) != len(expected.model_fields)
        or len(supplied) > len(raw)
        or any(type(key) is not str for key in (*raw, *supplied))
        or set(raw) != set(expected.model_fields)
        or not supplied <= set(raw)
    ):
        raise ValueError("post_submit_dirty_model")
    return raw


def _guard(value, depth=0):
    if depth > 8:
        raise ValueError("post_submit_record_bound")
    kind = type(value)
    allowed = (
        forensics.TradeCandidate,
        reservations.ReservationReceipt,
        reservations.LedgerScope,
        reservations.RiskCoverage,
        reservations.ScenarioOperands,
        DemoSubmissionReport,
        PostSubmitReportingResult,
    )
    if any(kind is item for item in allowed):
        for name, part in _exact(value, kind).items():
            if name in {"durable_record_only", "all_fill_prices_covered"} and (
                type(part) is not bool
            ):
                raise ValueError("post_submit_exact_bool_required")
            _guard(part, depth + 1)
    elif kind is str:
        if len(value) > 512:
            raise ValueError("post_submit_record_bound")
    elif kind is Decimal:
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 400
            or abs(value.as_tuple().exponent) > 400
        ):
            raise ValueError("post_submit_decimal_bound")
    elif kind is datetime:
        _utc(value)
    elif kind is int:
        if not 0 <= value <= 2**63 - 1:
            raise ValueError("post_submit_integer_bound")
    elif kind is not bool and value is not None:
        raise ValueError("post_submit_record_type")


def _json_flags(value, depth=0):
    if depth > 8:
        raise ValueError("post_submit_record_bound")
    if type(value) is dict:
        for name, part in value.items():
            if (
                name
                in {
                    "execution_authority",
                    "source_authenticity_verified",
                    "account_evidence_authenticated",
                    "execution_recheck_performed",
                    "durability_not_verified",
                    "order_retry_authority",
                    "notion_delivery_verified",
                    "durable_outbox_readback",
                    "requires_upstream_reconciliation",
                    "durable_record_only",
                    "all_fill_prices_covered",
                }
                and type(part) is not bool
            ):
                raise ValueError("post_submit_exact_bool_required")
            _json_flags(part, depth + 1)
    elif type(value) is list:
        raise ValueError("post_submit_record_type")


class _Model(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        str_strip_whitespace=False,
    )

    @classmethod
    def model_validate(cls, obj, **kwargs):
        if type(obj) is not dict:
            _exact(obj, cls)
            _guard(obj)
            obj = dict(obj.__dict__)
        return super().model_validate(obj, **kwargs)

    @classmethod
    def model_validate_json(cls, json_data, **kwargs):
        if type(json_data) is not bytes and type(json_data) is not str:
            raise ValueError("post_submit_record_type")
        if not 0 < len(json_data) <= MAX_REPORT_BYTES:
            raise ValueError("post_submit_record_bound")
        raw = json_data.encode() if type(json_data) is str else json_data
        if len(raw) > MAX_REPORT_BYTES:
            raise ValueError("post_submit_record_bound")
        try:
            parsed = json.loads(
                raw,
                object_pairs_hook=storage._unique_object,
                parse_constant=storage._json_constant,
            )
            _json_flags(parsed)
        except (ValueError, UnicodeError, RecursionError):
            raise ValueError("post_submit_json_invalid") from None
        return super().model_validate_json(raw, **kwargs)

    @model_validator(mode="before")
    @classmethod
    def exact_input(cls, value):
        if type(value) is not dict:
            value = dict(_exact(value, cls))
        if len(value) > len(cls.model_fields) or any(
            type(key) is not str or key not in cls.model_fields for key in value
        ):
            raise ValueError("post_submit_fields_invalid")
        for part in value.values():
            _guard(part)
        return value

    @field_validator("*", mode="before")
    @classmethod
    def exact_flags(cls, value, info):
        if (
            info.field_name
            in {
                "execution_authority",
                "source_authenticity_verified",
                "durability_not_verified",
                "order_retry_authority",
                "notion_delivery_verified",
                "durable_outbox_readback",
                "requires_upstream_reconciliation",
            }
            and type(value) is not bool
        ):
            raise ValueError("post_submit_exact_bool_required")
        return value


class DemoSubmissionReport(_Model):
    """Allowlisted local claim, not a filled order or authenticated receipt."""

    candidate: forensics.TradeCandidate
    candidate_sha256: outbox.Digest
    reservation_before_submit: reservations.ReservationReceipt
    reservation_sha256: outbox.Digest
    original_event_key: outbox.Digest
    strategy: outbox.Strategy
    client_order_id: ClientOrderId
    evidence_completed_at: datetime
    submit_started_at: datetime
    completed_at: datetime
    status: SubmissionStatus
    exchange_order_id: OrderId | None
    exchange_code: ExchangeCode | None
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    durability_not_verified: Literal[True] = True

    @model_validator(mode="before")
    @classmethod
    def json_transport(cls, value, info: ValidationInfo):
        if info.mode == "json" and type(value) is dict:
            value = dict(value)
            for name in (
                "evidence_completed_at",
                "submit_started_at",
                "completed_at",
            ):
                if name in value:
                    stamp = TypeAdapter(datetime).validate_json(
                        json.dumps(value[name]), strict=True
                    )
                    if stamp.tzinfo is None or stamp.utcoffset() is None:
                        raise ValueError("post_submit_clock_invalid")
                    value[name] = stamp.astimezone(UTC)
            for name, kind in (
                ("candidate", forensics.TradeCandidate),
                ("reservation_before_submit", reservations.ReservationReceipt),
            ):
                if name in value:
                    value[name] = kind.model_validate_json(
                        json.dumps(value[name]), strict=True
                    )
        return value

    @field_validator("candidate", "reservation_before_submit", mode="before")
    @classmethod
    def nested(cls, value, info):
        expected = (
            forensics.TradeCandidate
            if info.field_name == "candidate"
            else reservations.ReservationReceipt
        )
        _exact(value, expected)
        _guard(value)
        if expected is forensics.TradeCandidate:
            return forensics._copy(value, expected)
        return reservations.checked(value, expected)

    _times = field_validator(
        "evidence_completed_at", "submit_started_at", "completed_at"
    )(_utc)

    @model_validator(mode="after")
    def pinned(self):
        _guard(self)
        candidate = self.candidate
        receipt = self.reservation_before_submit
        if (
            forensics.candidate_sha256(candidate) != self.candidate_sha256
            or reservations.digest(receipt) != self.reservation_sha256
            or receipt.original_event_key != self.original_event_key
        ):
            raise ValueError("post_submit_pin_mismatch")
        if (
            receipt.report_id != candidate.report_id
            or receipt.instrument_id != candidate.instrument_id
            or receipt.direction != candidate.direction
            or receipt.scope.environment != candidate.environment
            or receipt.scope.account_id != candidate.account_id
            or receipt.scope.settlement_currency != candidate.settlement_currency
            or receipt.state != "consumed"
        ):
            raise ValueError("post_submit_reservation_mismatch")
        geometry = receipt.coverage.candidate
        if (
            geometry.entry != candidate.entry
            or geometry.stop_loss != candidate.stop_loss
            or geometry.contracts != candidate.planned_contracts
            or geometry.contract_value != candidate.contract_value_base
        ):
            raise ValueError("post_submit_candidate_mismatch")
        if (
            not (
                candidate.recorded_at
                <= self.evidence_completed_at
                <= self.submit_started_at
                <= self.completed_at
            )
            or receipt.updated_at > self.submit_started_at
        ):
            raise ValueError("post_submit_clock_order")
        outbox._name(candidate.report_id)
        if self.status == "acknowledged":
            if self.exchange_order_id is None or self.exchange_code != "0":
                raise ValueError("post_submit_acknowledgement_incomplete")
        elif self.status == "rejected":
            if self.exchange_order_id is not None or self.exchange_code in {None, "0"}:
                raise ValueError("post_submit_rejection_incomplete")
        elif self.exchange_order_id is not None or self.exchange_code is not None:
            raise ValueError("post_submit_uncertain_has_no_confirmed_ack")
        return self

    @property
    def report_id(self) -> str:
        return self.candidate.report_id

    @property
    def requires_upstream_reconciliation(self) -> bool:
        return self.status == "uncertain"


class PostSubmitReportingResult(_Model):
    """Local enqueue/readback result; never a reason to repeat submission."""

    report_id: outbox.ReportId
    submission_status: SubmissionStatus
    report_sha256: outbox.Digest
    status: Literal["enqueued", "deferred", "failed"]
    code: Literal[
        "post_submit_enqueued",
        "post_submit_rejected",
        "post_submit_uncertain",
        "post_submit_enqueue_failed",
    ]
    outbox_status: outbox.Status | None
    outbox_envelope_sha256: outbox.Digest | None
    durable_outbox_readback: bool
    requires_upstream_reconciliation: bool
    durability_not_verified: Literal[True] = True
    execution_authority: Literal[False] = False
    order_retry_authority: Literal[False] = False
    notion_delivery_verified: Literal[False] = False

    @model_validator(mode="after")
    def consistent(self):
        _guard(self)
        outbox._name(self.report_id)
        if self.requires_upstream_reconciliation != (
            self.submission_status == "uncertain"
        ):
            raise ValueError("post_submit_result_inconsistent")
        if self.status == "enqueued":
            valid = (
                self.submission_status == "acknowledged"
                and self.code == "post_submit_enqueued"
                and self.durable_outbox_readback
                and self.outbox_status is not None
                and self.outbox_envelope_sha256 is not None
            )
        else:
            valid = (
                not self.durable_outbox_readback
                and self.outbox_status is None
                and self.outbox_envelope_sha256 is None
                and (
                    self.status == "deferred"
                    and self.submission_status in {"rejected", "uncertain"}
                    and self.code == "post_submit_" + self.submission_status
                    or self.status == "failed"
                    and self.submission_status == "acknowledged"
                    and self.code == "post_submit_enqueue_failed"
                )
            )
        if not valid:
            raise ValueError("post_submit_result_inconsistent")
        return self


def _projection(result, client_order_id, submit_started_at, completed_at):
    if result is None:
        return "uncertain", None, None
    raw = _exact(result, OkxDemoWriteResult)
    # Do not iterate, stringify, hash or serialize raw exchange_data/order/warnings.
    if type(raw["action"]) is not str or raw["action"] != "place_order":
        raise ValueError("post_submit_place_order_required")
    if type(raw["acknowledged"]) is not bool:
        raise ValueError("post_submit_exact_acknowledgement_required")
    if not submit_started_at <= _utc(raw["completed_at"]) <= completed_at:
        raise ValueError("post_submit_clock_order")
    ack = raw["acknowledgement"]
    if ack is None:
        return "uncertain", None, None
    ack = _exact(ack, OkxDemoOrderAcknowledgement)
    order_id, client_id, code = (
        ack["order_id"],
        ack["client_order_id"],
        ack["exchange_code"],
    )
    if any(type(value) is not str for value in (order_id, client_id, code)):
        return "uncertain", None, None
    if (
        len(order_id) > 64
        or len(client_id) > 32
        or len(code) > 16
        or client_id != client_order_id
        or re.fullmatch(r"0|[1-9][0-9]{0,15}", code) is None
    ):
        return "uncertain", None, None
    if (
        raw["acknowledged"]
        and code == "0"
        and re.fullmatch(r"[A-Za-z0-9]{1,64}", order_id)
    ):
        return "acknowledged", order_id, code
    if not raw["acknowledged"] and code != "0" and order_id == "":
        return "rejected", None, code
    return "uncertain", None, None


def build_submission_report(
    candidate: forensics.TradeCandidate,
    *,
    expected_candidate_sha256: str,
    reservation_before_submit: reservations.ReservationReceipt,
    expected_reservation_sha256: str,
    expected_original_event_key: str,
    strategy: outbox.Strategy,
    client_order_id: str,
    evidence_completed_at: datetime,
    submit_started_at: datetime,
    completed_at: datetime,
    write_result: OkxDemoWriteResult | None,
) -> DemoSubmissionReport:
    """Project a completed call or unknown outcome; never accepts an exception.

    An interrupted/raised submit has ``write_result=None``. Even a valid exchange
    acknowledgement says nothing about fills/protection. No callback is invoked.
    The returned report is NOT persisted; the caller retains it in its ledger.
    """
    report_id = None
    try:
        _exact(candidate, forensics.TradeCandidate)
        _guard(candidate)
        candidate = forensics._copy(candidate, forensics.TradeCandidate)
        report_id = candidate.report_id
        outbox._name(report_id)
        if (
            type(client_order_id) is not str
            or re.fullmatch(r"[A-Za-z0-9]{1,32}", client_order_id) is None
        ):
            raise ValueError("post_submit_client_order_id_invalid")
        started = _utc(submit_started_at)
        completed = _utc(completed_at)
        status, order_id, code = _projection(
            write_result, client_order_id, started, completed
        )
        return DemoSubmissionReport(
            candidate=candidate,
            candidate_sha256=expected_candidate_sha256,
            reservation_before_submit=reservation_before_submit,
            reservation_sha256=expected_reservation_sha256,
            original_event_key=expected_original_event_key,
            strategy=strategy,
            client_order_id=client_order_id,
            evidence_completed_at=evidence_completed_at,
            submit_started_at=submit_started_at,
            completed_at=completed,
            status=status,
            exchange_order_id=order_id,
            exchange_code=code,
        )
    except Exception:  # noqa: BLE001 - Never echo raw exchange or model errors.
        raise PostSubmitError("post_submit_report_invalid", report_id) from None


def validate_submission_report(value: DemoSubmissionReport) -> DemoSubmissionReport:
    report_id = None
    try:
        _exact(value, DemoSubmissionReport)
        candidate = value.__dict__["candidate"]
        if type(candidate) is forensics.TradeCandidate:
            raw = _exact(candidate, forensics.TradeCandidate)
            name = raw["report_id"]
            if type(name) is str:
                outbox._name(name)
                report_id = name
        _guard(value)
        return DemoSubmissionReport.model_validate(value, strict=True)
    except Exception:  # noqa: BLE001 - Public input boundary uses static codes.
        raise PostSubmitError("post_submit_report_invalid", report_id) from None


def freeze_submission_report(value: DemoSubmissionReport) -> bytes:
    """Canonical bytes for the existing caller ledger, not a new persistence API."""
    checked = validate_submission_report(value)
    raw = json.dumps(
        checked.model_dump(mode="json", round_trip=True),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    if len(raw) > MAX_REPORT_BYTES:
        raise PostSubmitError("post_submit_report_bound", checked.report_id)
    return raw


def verify_submission_report(raw: bytes, expected_sha256: str) -> DemoSubmissionReport:
    """Verify canonical record integrity only, never its source/disk authority."""
    try:
        if (
            type(raw) is not bytes
            or not 0 < len(raw) <= MAX_REPORT_BYTES
            or type(expected_sha256) is not str
            or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None
            or hashlib.sha256(raw).hexdigest() != expected_sha256
        ):
            raise ValueError("pin")
        json.loads(
            raw,
            object_pairs_hook=storage._unique_object,
            parse_constant=storage._json_constant,
        )
        result = DemoSubmissionReport.model_validate_json(raw, strict=True)
        if freeze_submission_report(result) != raw:
            raise ValueError("canonical")
        return result
    except Exception:  # noqa: BLE001 - No raw source or exception text escapes.
        raise PostSubmitError("post_submit_record_invalid") from None


def enqueue_post_submit(
    root: Path,
    report: DemoSubmissionReport,
    *,
    outbox_policy: outbox.OutboxPolicy | None = None,
    clock: Callable[[], datetime] = outbox.actual_utc,
) -> PostSubmitReportingResult:
    """Local only. Never wait for Notion here or repeat an exchange operation.

    Enqueue failures preserve the report ID and do not assert zero writes: a
    marker may have been committed before readback failed. Retry only this local
    enqueue with the SAME immutable report/policy, never reconstruct timestamps.
    All unknown submissions remain the upstream ledger's reconciliation duty.
    """
    report = validate_submission_report(report)
    pin = hashlib.sha256(freeze_submission_report(report)).hexdigest()
    values = {
        "report_id": report.report_id,
        "submission_status": report.status,
        "report_sha256": pin,
        "status": "deferred",
        "code": "post_submit_" + report.status,
        "outbox_status": None,
        "outbox_envelope_sha256": None,
        "durable_outbox_readback": False,
        "requires_upstream_reconciliation": report.requires_upstream_reconciliation,
    }
    if report.status != "acknowledged":
        return PostSubmitReportingResult(**values)
    try:
        payload = outbox.OutboxPayload(
            report_id=report.report_id,
            instrument_id=report.candidate.instrument_id,
            strategy=report.strategy,
            direction=report.candidate.direction,
            order_reference=report.client_order_id,
            submitted_at=report.completed_at,
            evidence_completed_at=report.evidence_completed_at,
            source_sha256=report.candidate.original_source_sha256,
            evidence_report_sha256=report.candidate.original_evidence_sha256,
            submission_receipt_sha256=pin,
        )
        view = outbox.validate_outbox_view(
            outbox.enqueue(root, payload, policy=outbox_policy, clock=clock)
        )
        if view.envelope.payload != payload:
            raise ValueError("post_submit_outbox_pin_mismatch")
        values.update(
            status="enqueued",
            code="post_submit_enqueued",
            outbox_status=view.status,
            outbox_envelope_sha256=view.envelope.envelope_sha256,
            durable_outbox_readback=True,
        )
    except Exception:  # noqa: BLE001 - Reporting failure cannot become an order retry.
        values.update(status="failed", code="post_submit_enqueue_failed")
    return PostSubmitReportingResult(**values)


async def run_post_submit_pass(
    root: Path,
    *,
    report_ids: tuple[str, ...],
    worker_id: str,
    adapter: NotionDeliveryAdapter,
    policy: outbox_worker.OutboxWorkerPolicy,
    clock: Callable[[], datetime] = outbox.actual_utc,
) -> outbox_worker.OutboxPassResult:
    """Explicit separate reporting pass; no order callback or credential lookup.

    Construct the existing adapter with an explicitly supplied client/token and
    four destination property pins. Caller owns the HTTP client. This function
    neither enqueues nor accepts submit results and never starts a scheduler.
    Cancel/timeout recovery and remote ambiguity retain the existing outbox rules.
    """
    if type(adapter) is not NotionDeliveryAdapter:
        raise PostSubmitError("post_submit_notion_adapter_required")
    return await outbox_worker.run_outbox_pass(
        root,
        report_ids=report_ids,
        worker_id=worker_id,
        adapter=adapter,
        policy=policy,
        clock=clock,
    )
