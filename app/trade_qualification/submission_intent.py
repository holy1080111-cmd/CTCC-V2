"""Canonical pre-submit journal facts, not a transferable execution permit.

The repository constructs this record from its locked persisted reservation,
atomically with consumption. No caller order payload, acknowledgement, PASS or
callback is accepted. Reading a committed intent after a crash is audit only:
it must never cause an order retry, even if no acknowledgement was recorded.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Literal

from app.trade_evidence import gates, storage
from app.trade_evidence import models as evidence_models
from app.trade_qualification import (
    account_capture,
    data,
    economics,
    engine,
    event_models,
    location,
    models,
    portfolio,
    quote_collector,
    recheck_models,
    reservations,
    service,
    timing,
)
from app.trade_qualification.reservations import (
    QualificationLedgerError,
    ReservationReceipt,
    ReservationRequest,
    checked,
    digest,
    reservation_id,
)

VERSION = "ctcc-demo-submit-intent-v1"
MAX_INTENT_BYTES = 32768
_MODEL_TYPES = (
    ReservationRequest,
    ReservationReceipt,
    reservations.LedgerScope,
    reservations.RiskCoverage,
    reservations.ScenarioOperands,
    recheck_models.RecheckOrigin,
    gates.EvidenceGateRun,
    evidence_models.EvidenceCandle,
    evidence_models.EvidenceLevel,
    evidence_models.EvidencePanel,
    evidence_models.EvidenceSnapshot,
    storage.PublicationReceipt,
    storage.PublishedFile,
    data.DataQualificationPolicy,
    data.DataQualificationResult,
    data.WSReferenceObservation,
    economics.EconomicsPolicy,
    economics.EconomicsResult,
    engine.PortfolioInputs,
    engine.PreEvidencePolicy,
    engine.PreEvidenceRun,
    engine.ProtectionPolicy,
    event_models.TriggerDetection,
    location.ExecutableQuote,
    location.LocationResult,
    models.EntryQualificationResult,
    models.EntryTrigger,
    models.EntryZone,
    models.GateAssessment,
    portfolio.ContractRiskSpec,
    portfolio.DemoRiskAuthority,
    portfolio.EvidenceStamp,
    portfolio.PortfolioRiskPolicy,
    portfolio.PortfolioRiskResult,
    portfolio.PortfolioRiskSnapshot,
    portfolio.PositionExposure,
    portfolio.PendingReservation,
    portfolio.RealizedOutcome,
    service.QualificationIntent,
    service.QualificationPrefixPolicy,
    service.QualificationPrefixRun,
    timing.TimingPolicy,
    timing.TimingResult,
    quote_collector.CollectedQuote,
    quote_collector.EndpointObservation,
    quote_collector.QuoteCollectionPolicy,
)


def _guard(value, depth=0, budget=None):
    """Guard exact raw types before legacy helpers can inspect foreign callbacks."""
    if budget is None:
        budget = [300000, 64 * 1024 * 1024]
    budget[0] -= 1
    if depth > 32 or budget[0] < 0:
        raise QualificationLedgerError("submit_intent_input_invalid")
    kind = type(value)
    if any(kind is allowed for allowed in _MODEL_TYPES):
        raw = object.__getattribute__(value, "__dict__")
        supplied = object.__getattribute__(value, "__pydantic_fields_set__")
        if (
            type(raw) is not dict
            or type(supplied) is not set
            or object.__getattribute__(value, "__pydantic_extra__") is not None
            or object.__getattribute__(value, "__pydantic_private__") is not None
            or any(type(key) is not str for key in (*raw, *supplied))
            or set(raw) != set(kind.model_fields)
            or not supplied <= set(raw)
        ):
            raise QualificationLedgerError("submit_intent_input_invalid")
        for part in raw.values():
            _guard(part, depth + 1, budget)
    elif kind is dict or kind is MappingProxyType:
        if len(value) > 2048 or any(type(key) is not str for key in value):
            raise QualificationLedgerError("submit_intent_input_invalid")
        for key, part in value.items():
            _guard(key, depth + 1, budget)
            _guard(part, depth + 1, budget)
    elif kind is tuple or kind is list or kind is frozenset:
        if len(value) > 10000:
            raise QualificationLedgerError("submit_intent_input_invalid")
        for part in value:
            _guard(part, depth + 1, budget)
    elif kind is datetime:
        account_capture._utc(value)
    elif kind is Decimal:
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 128
            or abs(value.as_tuple().exponent) > 200
        ):
            raise QualificationLedgerError("submit_intent_input_invalid")
    elif kind is str or kind is bytes:
        budget[1] -= len(value)
        if len(value) > 16 * 1024 * 1024 or budget[1] < 0:
            raise QualificationLedgerError("submit_intent_input_invalid")
    elif (
        value is None
        or kind is bool
        or kind is int
        and abs(value) <= 10**40
        or any(
            kind is allowed
            for allowed in (
                models.MarketRegime,
                models.QualificationGate,
                models.QualificationState,
            )
        )
    ):
        pass
    else:
        raise QualificationLedgerError("submit_intent_input_invalid")


@dataclass(frozen=True, slots=True)
class SubmissionIntentRecord:
    """A bounded journal representation. Constructor is NOT a trust boundary."""

    canonical_json: str = field(repr=False)
    sha256: str
    execution_authority: Literal[False] = field(default=False, init=False)
    order_retry_authority: Literal[False] = field(default=False, init=False)


def _json(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def build_submission_intent(request, consumed):
    """Derive all facts from exact reservation records; performs no IO."""
    _guard(request)
    _guard(consumed)
    request = checked(request, ReservationRequest)
    consumed = checked(consumed, ReservationReceipt)
    origin = request.origin
    candidate = origin.candidate
    risk = request.risk_inputs
    if (
        consumed.state != "consumed"
        or consumed.state_revision != 2
        or consumed.scope != request.scope
        or consumed.reservation_id
        != reservation_id(request.scope, origin.original_event_key)
        or consumed.original_event_key != origin.original_event_key
        or consumed.request_sha256 != digest(request)
        or consumed.report_id != candidate.report_id
        or consumed.instrument_id != candidate.symbol
        or consumed.direction != candidate.direction
        or consumed.deadline != origin.deadline
        or risk.instrument is None
        or consumed.correlation_group != risk.instrument.correlation_group
        or not origin.publication_completed_at < consumed.updated_at < origin.deadline
        or consumed.account_revision < request.expected_account_revision
        or consumed.ledger_revision <= request.expected_ledger_revision
    ):
        raise QualificationLedgerError("submit_intent_reservation_mismatch")
    geometry = consumed.coverage.candidate
    if (
        geometry.entry != candidate.candidate_entry
        or geometry.stop_loss != candidate.stop_loss
        or geometry.contracts != risk.requested_contracts
        or geometry.leverage != risk.requested_leverage
        or geometry.contract_value != risk.instrument.contract_value
        or consumed.coverage.execution.contracts != geometry.contracts
        or consumed.coverage.execution.leverage != geometry.leverage
        or consumed.coverage.execution.stop_loss != geometry.stop_loss
        or consumed.coverage.execution.contract_value != geometry.contract_value
    ):
        raise QualificationLedgerError("submit_intent_geometry_mismatch")
    # Scope + original event determine an irreversible reservation; no fresh ID
    # can be chosen to retry an unknown submission under a renamed report.
    client_order_id = "CTQ" + consumed.reservation_id[:29]
    body = {
        "version": VERSION,
        "request_sha256": consumed.request_sha256,
        "origin_sha256": origin.evaluation_sha256,
        "consumed_receipt": consumed.model_dump(mode="json", round_trip=True),
        "client_order_id": client_order_id,
        "protection_client_order_id": "CTA" + consumed.reservation_id[:29],
        "candidate_entry": str(candidate.candidate_entry),
        "stop_loss": str(candidate.stop_loss),
        "take_profit": str(candidate.take_profit),
        "contracts": str(geometry.contracts),
        "leverage": geometry.leverage,
        "evidence_completed_at": origin.publication_completed_at.isoformat(),
        "record_kind": "durable_intent_not_execution_permission",
        "execution_authority": False,
        "order_retry_authority": False,
        "all_fill_prices_covered": False,
        "order_submitted": False,
    }
    raw = _json(body)
    if len(raw.encode("utf-8")) > MAX_INTENT_BYTES:
        raise QualificationLedgerError("submit_intent_too_large")
    return SubmissionIntentRecord(raw, hashlib.sha256(raw.encode()).hexdigest())


def replay_submission_intent(raw, request, *, expected_sha256):
    """Reconstruct against persisted request. Hash is integrity, not authority."""
    try:
        encoded = raw.encode("utf-8") if type(raw) is str else b""
    except UnicodeError:
        raise QualificationLedgerError("submit_intent_integrity_mismatch") from None
    if (
        type(raw) is not str
        or not 0 < len(raw) <= MAX_INTENT_BYTES
        or len(encoded) > MAX_INTENT_BYTES
        or type(expected_sha256) is not str
        or len(expected_sha256) != 64
        or any(char not in "0123456789abcdef" for char in expected_sha256)
        or hashlib.sha256(encoded).hexdigest() != expected_sha256
    ):
        raise QualificationLedgerError("submit_intent_integrity_mismatch")
    _guard(request)
    try:
        body = json.loads(raw)
        if type(body) is not dict or _json(body) != raw:
            raise ValueError("canonical")
        receipt = ReservationReceipt.model_validate_json(
            _json(body["consumed_receipt"]), strict=True
        )
        rebuilt = build_submission_intent(request, receipt)
        if rebuilt.canonical_json != raw:
            raise ValueError("changed")
        return rebuilt, checked(receipt, ReservationReceipt)
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise QualificationLedgerError("submit_intent_record_invalid") from None
