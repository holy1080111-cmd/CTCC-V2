"""Canonical pre-submit journal facts, not a transferable execution permit.

The repository constructs this record from its locked persisted reservation,
atomically with consumption. No caller order payload, acknowledgement, PASS or
callback is accepted. Reading a committed intent after a crash is audit only:
it must never cause an order retry, even if no acknowledgement was recorded.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType
from typing import Literal

from pydantic import Field

from app.domain.market import MarketSnapshot
from app.trade_evidence import gates, storage
from app.trade_evidence import models as evidence_models
from app.trade_qualification import (
    account_capture,
    data,
    economics,
    engine,
    event_models,
    history_engine,
    history_prefix,
    location,
    models,
    portfolio,
    quote_collector,
    recheck_models,
    reservations,
    service,
    timing,
)
from app.trade_qualification.regime_admission import RegimeAdmissionResult
from app.trade_qualification.reservations import (
    QualificationLedgerError,
    ReservationReceipt,
    ReservationRequest,
    checked,
    digest,
    reservation_id,
)

VERSION = "ctcc-demo-submit-intent-v1"
VERSION_V2 = "ctcc-demo-submit-intent-v2"
MAX_INTENT_BYTES = 32768
# DB0017 qualification_reservation_transitions.evidence_bound is 8 MiB.
# The complete escaped journal, including every replay document, must fit it.
MAX_V2_INTENT_BYTES = 8 * 1024 * 1024


class SubmissionExecutionBinding(reservations.LedgerModel):
    """Bounded replay documents, never authenticated source or a submit permit.

    The constructor and hashes grant no trust. Every v2 build/readback replays
    these inputs and the full account page chain against the reservation.
    """

    account_packet_json: str = Field(min_length=1, max_length=16 * 1024 * 1024)
    account_packet_sha256: reservations.Digest
    account_plan_sha256: reservations.Digest
    original_market_json: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    current_market_json: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    original_inputs_json: str = Field(min_length=1, max_length=16 * 1024 * 1024)
    quote_json: str = Field(min_length=1, max_length=1024 * 1024)
    reference_json: str = Field(min_length=1, max_length=32768)
    recheck_json: str = Field(min_length=1, max_length=16 * 1024 * 1024)
    consumed_event_keys: tuple[reservations.Digest, ...] = Field(max_length=2048)


class _OriginalReplayInputs(models.QualificationModel):
    intent: service.QualificationIntent
    quote: quote_collector.CollectedQuote
    reference: data.WSReferenceObservation
    policy: engine.PreEvidencePolicy
    risk_inputs: engine.PortfolioInputs
    consumed_event_keys: frozenset[reservations.Digest] = Field(max_length=2048)
    evaluated_at: datetime


class _HistoryReplayInputsV2(_OriginalReplayInputs):
    policy: history_engine.HistoryPreEvidencePolicyV2


class _HistoryReplayInputsV3(_OriginalReplayInputs):
    policy: history_engine.HistoryPreEvidencePolicyV3


_MODEL_TYPES = (
    gates.HistoryEvidenceGateRunV2,
    gates.HistoryEvidenceGateRunV3,
    history_engine.HistoryPreEvidencePolicyV2,
    history_engine.HistoryPreEvidencePolicyV3,
    history_engine.HistoryPreEvidenceRunV2,
    history_engine.HistoryPreEvidenceRunV3,
    history_prefix.HistoryEntryQualificationResultV2,
    history_prefix.HistoryEntryQualificationResultV3,
    history_prefix.HistoryQualificationPrefixPolicyV2,
    history_prefix.HistoryQualificationPrefixPolicyV3,
    history_prefix.HistoryQualificationPrefixRunV2,
    history_prefix.HistoryQualificationPrefixRunV3,
    RegimeAdmissionResult,
    SubmissionExecutionBinding,
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


def build_submission_intent(
    request, consumed, *, execution_binding: SubmissionExecutionBinding | None = None
):
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
        # Candidate.symbol is the domain display symbol (BTC/USDT:USDT).
        # The persisted reservation and exchange request use the exact OKX
        # instrument ID (BTC-USDT-SWAP), already bound by the prefix replay.
        or consumed.instrument_id
        != origin.evidence.pre_evidence.prefix.intent.instrument_id
        or consumed.direction != candidate.direction
        or consumed.deadline != origin.deadline
        or risk.instrument is None
        or consumed.instrument_id != risk.instrument.instrument_id
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
    maximum = MAX_INTENT_BYTES
    if execution_binding is not None:
        body.update(_execution_body(request, consumed, execution_binding))
        body["version"] = VERSION_V2
        maximum = MAX_V2_INTENT_BYTES
    raw = _json(body)
    if len(raw.encode("utf-8")) > maximum:
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
        or not 0 < len(raw) <= MAX_V2_INTENT_BYTES
        or len(encoded) > MAX_V2_INTENT_BYTES
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
        version = body.get("version")
        if version not in (VERSION, VERSION_V2):
            raise ValueError("version")
        if version == VERSION and len(encoded) > MAX_INTENT_BYTES:
            raise ValueError("legacy_size")
        receipt = ReservationReceipt.model_validate_json(
            _json(body["consumed_receipt"]), strict=True
        )
        binding = (
            SubmissionExecutionBinding.model_validate_json(
                _json(body["execution_binding"]), strict=True
            )
            if version == VERSION_V2
            else None
        )
        rebuilt = build_submission_intent(request, receipt, execution_binding=binding)
        if rebuilt.canonical_json != raw:
            raise ValueError("changed")
        return rebuilt, checked(receipt, ReservationReceipt)
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise QualificationLedgerError("submit_intent_record_invalid") from None


def _document(raw, model):
    if _json(json.loads(raw)) != raw:
        raise ValueError("noncanonical_binding_document")
    return model.model_validate_json(raw, strict=True)


def _original_inputs_document(raw):
    """Select only explicit supported policy families before Pydantic defaults.

    The outer intent schema is unchanged. History V1 is not admitted; missing,
    mixed or unknown history markers cannot silently become a newer policy.
    Full original source and recheck replay still follow this schema selection.
    """
    parsed = json.loads(raw)
    if _json(parsed) != raw or type(parsed) is not dict:
        raise ValueError("noncanonical_original_inputs")
    policy = parsed.get("policy")
    if type(policy) is not dict:
        raise ValueError("original_policy_required")
    if "contract_version" not in policy:
        model = _OriginalReplayInputs
    else:
        version = policy["contract_version"]
        variants = {
            "ctcc-history-pre-evidence-v2": (
                _HistoryReplayInputsV2,
                "ctcc-history-qualification-prefix-v2",
            ),
            "ctcc-history-pre-evidence-v3": (
                _HistoryReplayInputsV3,
                "ctcc-history-qualification-prefix-v3",
            ),
        }
        if type(version) is not str or version not in variants:
            raise ValueError("original_policy_version_unsupported")
        model, prefix_version = variants[version]
        prefix = policy.get("prefix")
        if type(prefix) is not dict or prefix.get("contract_version") != prefix_version:
            raise ValueError("original_policy_prefix_version_mismatch")
    return model.model_validate_json(raw, strict=True)


def _execution_body(request, consumed, supplied):
    # Keep this optional v2 path independent of unchanged historical v1 replay.
    from app.trade_qualification.executable_economics import (
        evaluate_executable_economics,
    )
    from app.trade_qualification.recheck import (
        RecordedRecheckAssessment,
        verify_recorded_recheck,
    )

    _guard(supplied)
    if type(supplied) is not SubmissionExecutionBinding:
        raise QualificationLedgerError("submit_execution_binding_required")
    try:
        binding = SubmissionExecutionBinding.model_validate(
            supplied.model_dump(mode="python", round_trip=True), strict=True
        )
        packet = account_capture.verify_demo_account_packet(
            binding.account_packet_json.encode("utf-8"),
            expected_sha256=binding.account_packet_sha256,
            expected_plan_sha256=binding.account_plan_sha256,
        )
        origin = request.origin
        recorded = _document(binding.recheck_json, RecordedRecheckAssessment)
        quote = _document(binding.quote_json, quote_collector.CollectedQuote)
        reference = _document(binding.reference_json, data.WSReferenceObservation)
        original_inputs = _original_inputs_document(binding.original_inputs_json)
        if (
            packet.plan.expected_uid != request.scope.account_id
            or packet.plan.settlement_currency != request.scope.settlement_currency
            or consumed.instrument_id not in packet.plan.leverage_instrument_ids
            or packet.barrier_completed_at != origin.publication_completed_at
            or not packet.completed_at <= recorded.observed_at <= consumed.updated_at
            or recorded.origin != origin
            or quote.quote != request.quote
            or consumed.account_revision != request.expected_account_revision
            or len(set(binding.consumed_event_keys)) != len(binding.consumed_event_keys)
        ):
            raise ValueError("binding_scope_or_time")
        actual = verify_recorded_recheck(
            recorded,
            _document(binding.original_market_json, MarketSnapshot),
            _document(binding.current_market_json, MarketSnapshot),
            origin=origin,
            original_inputs={
                name: getattr(original_inputs, name)
                for name in type(original_inputs).model_fields
            },
            quote=quote,
            reference=reference,
            current_risk_inputs=request.risk_inputs,
            consumed_event_keys=frozenset(binding.consumed_event_keys),
            observed_at=recorded.observed_at,
        )
        if not actual.computational_checks_passed:
            raise ValueError("recorded_recheck_denied")
        config = [
            json.loads(page.rows[0].canonical_json)
            for page in packet.observations
            if page.request.stream == "config_before"
        ]
        if len(config) != 1 or config[0]["acctLv"] != "2":
            raise ValueError("account_configuration_unsupported")
        max_account_age = origin.evidence.pre_evidence.policy.portfolio.max_age_seconds
        if any(
            (consumed.updated_at - page.headers_received_at).total_seconds()
            > max_account_age
            for page in packet.observations
        ):
            raise ValueError("account_binding_stale")
        mode = config[0]["posMode"]
        if mode not in ("net_mode", "long_short_mode"):
            raise ValueError("position_mode_unsupported")
        position_side = "net" if mode == "net_mode" else consumed.direction
        instruments = [
            json.loads(row.canonical_json)
            for page in packet.observations
            if page.request.stream == "account_instruments"
            for row in page.rows
            if row.instrument_id == consumed.instrument_id
        ]
        tick = origin.evidence.pre_evidence.policy.prefix.tick_size
        if (
            len(instruments) != 1
            or instruments[0].get("state") != "live"
            or type(instruments[0].get("tickSz")) is not str
            or Decimal(instruments[0]["tickSz"]) != tick
        ):
            raise ValueError("instrument_tick_binding_missing")
        instrument, spec = instruments[0], request.risk_inputs.instrument
        if (
            instrument.get("ctType") != "linear"
            or spec.contract_kind != "linear_base"
            or instrument.get("settleCcy") != spec.settlement_currency
            or instrument.get("ctValCcy") != spec.contract_value_currency
            or Decimal(instrument["ctVal"]) != spec.contract_value
            or Decimal(instrument["ctMult"]) != 1
            or Decimal(instrument["lotSz"]) != spec.lot_size
            or not Decimal(instrument["minSz"])
            <= request.risk_inputs.requested_contracts
            <= Decimal(instrument["maxLmtSz"])
            or Decimal(instrument["lever"]) < request.risk_inputs.requested_leverage
        ):
            raise ValueError("instrument_contract_binding_mismatch")
        leverage = [
            json.loads(row.canonical_json)
            for page in packet.observations
            if page.request.stream == "leverage_isolated"
            for row in page.rows
            if row.instrument_id == consumed.instrument_id
        ]
        matched = [item for item in leverage if item.get("posSide") == position_side]
        if (
            len(matched) != 1
            or matched[0].get("mgnMode") != "isolated"
            or type(matched[0].get("lever")) is not str
            or Decimal(matched[0]["lever"]) != consumed.coverage.candidate.leverage
        ):
            raise ValueError("isolated_leverage_binding_missing")
        candidate = origin.candidate
        costs = evaluate_executable_economics(
            report_id=candidate.report_id,
            instrument_id=consumed.instrument_id,
            direction=consumed.direction,
            candidate_entry=candidate.candidate_entry,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            quote=request.quote,
            policy=origin.evidence.pre_evidence.policy.economics,
            current_time=consumed.updated_at,
        )
        if not costs.passed:
            raise ValueError("current_sample_economics_denied")
        for scenario, coverage in (
            (costs.candidate_result, consumed.coverage.candidate),
            (costs.execution_result, consumed.coverage.execution),
        ):
            if (
                scenario.candidate_entry,
                scenario.stop_loss,
                scenario.cost_per_base,
            ) != (coverage.entry, coverage.stop_loss, coverage.cost_per_base):
                raise ValueError("reserved_sample_changed")
        limit = consumed.coverage.execution.entry
        for price in (
            candidate.candidate_entry,
            limit,
            candidate.stop_loss,
            candidate.take_profit,
        ):
            if (Fraction(price) / Fraction(tick)).denominator != 1:
                raise ValueError("fixed_price_off_tick")
        elapsed = consumed.deadline - datetime(1970, 1, 1, tzinfo=UTC)
        expiry_ms = (
            elapsed.days * 86400000
            + elapsed.seconds * 1000
            + elapsed.microseconds // 1000
        )
        now = consumed.updated_at - datetime(1970, 1, 1, tzinfo=UTC)
        if (
            now.days * 86400000000 + now.seconds * 1000000 + now.microseconds
            >= expiry_ms * 1000
        ):
            raise ValueError("rounded_expiry_elapsed")
        order = {
            "instId": consumed.instrument_id,
            "tdMode": "isolated",
            "clOrdId": "CTQ" + consumed.reservation_id[:29],
            "side": "buy" if consumed.direction == "long" else "sell",
            "posSide": position_side,
            "ordType": "fok",
            "sz": format(consumed.coverage.candidate.contracts, "f"),
            "px": format(limit, "f"),
            "attachAlgoOrds": [
                {
                    "attachAlgoClOrdId": "CTA" + consumed.reservation_id[:29],
                    "tpTriggerPx": format(candidate.take_profit, "f"),
                    "tpOrdPx": "-1",
                    "tpTriggerPxType": "mark",
                    "slTriggerPx": format(candidate.stop_loss, "f"),
                    "slOrdPx": "-1",
                    "slTriggerPxType": "mark",
                }
            ],
        }
        dispatch = {
            "method": "POST",
            "path": "/api/v5/trade/order",
            "environment": "demo",
            "account_uid": request.scope.account_id,
            "headers": {"x-simulated-trading": "1", "expTime": str(expiry_ms)},
            "body": order,
        }
        return {
            "execution_binding": binding.model_dump(mode="json", round_trip=True),
            "execution_binding_sha256": hashlib.sha256(
                _json(binding.model_dump(mode="json", round_trip=True)).encode()
            ).hexdigest(),
            "exchange_request": dispatch,
            "exchange_request_sha256": hashlib.sha256(
                _json(dispatch).encode()
            ).hexdigest(),
            "account_packet_sha256": binding.account_packet_sha256,
            "account_plan_sha256": binding.account_plan_sha256,
            "position_mode": mode,
            "margin_mode": "isolated",
            "evidence_sha256": origin.evidence_sha256,
            "recheck_sha256": actual.evaluation_sha256,
            "economics_sha256": hashlib.sha256(
                _json(costs.model_dump(mode="json", round_trip=True)).encode()
            ).hexdigest(),
            "executable_limit": str(limit),
            "limit_basis": "exact_reserved_executable_sample_no_widening",
            "source_authenticity_verified": False,
            "account_complete": False,
            "intrabar_path_verified": False,
        }
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        ArithmeticError,
        RecursionError,
    ):
        raise QualificationLedgerError("submit_execution_binding_invalid") from None
