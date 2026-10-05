"""Exact sampled-risk journal contracts, never source or order authorization.

The repository serializes these *claims* under a Demo account lock. It cannot
authenticate account IO, replay an actual publication or establish a fill price.
Expiry never releases a hold. Only explicit newer confirmed-flat claims do.
"""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.trade_qualification.current_risk import evaluate_current_risk
from app.trade_qualification.engine import PortfolioInputs, _copy, _preflight
from app.trade_qualification.history_engine import (
    HistoryPreEvidenceRunV4,
    HistoryPreEvidenceRunV5,
)
from app.trade_qualification.location import (
    ExecutableQuote,
    _revalidate,
    build_original_range_anchor_zone,
    evaluate_location,
)
from app.trade_qualification.models import Price, ReportId, require_aware
from app.trade_qualification.portfolio import (
    DemoRiskAuthority,
    PendingReservation,
    PortfolioRiskSnapshot,
)
from app.trade_qualification.recheck_models import RecheckOrigin, copy_recheck_origin
from app.trade_qualification.service import _plain

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Revision = Annotated[int, Field(ge=0, le=10**15)]
State = Literal["reserved", "consumed", "uncertain", "reconciled_flat"]
MAX_JSON_BYTES = 16 * 1024 * 1024
STAMP_FIELDS = (
    "balance_stamp",
    "positions_stamp",
    "history_stamp",
    "reservations_stamp",
)


class QualificationLedgerError(ValueError):
    """Local stable rejection code; no raw account or database error text."""


class LedgerModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        allow_inf_nan=False,
        str_strip_whitespace=False,
    )
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    account_evidence_authenticated: Literal[False] = False
    execution_recheck_performed: Literal[False] = False

    @field_validator(
        "execution_authority",
        "source_authenticity_verified",
        "account_evidence_authenticated",
        "execution_recheck_performed",
        mode="before",
    )
    @classmethod
    def false_means_false(cls, value, info):
        if info.field_name in LedgerModel.model_fields and value is not False:
            raise ValueError("ledger_cannot_grant_authority")
        return value


class LedgerScope(LedgerModel):
    environment: Literal["demo"] = "demo"
    account_id: Annotated[str, Field(pattern=r"^[0-9]{1,32}$")]
    settlement_currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{1,16}$")]


class AccountLedgerClaims(LedgerModel):
    scope: LedgerScope
    account: PortfolioRiskSnapshot
    authority: DemoRiskAuthority
    reconciliation_id: Digest


class ReservationRequest(LedgerModel):
    scope: LedgerScope
    origin: RecheckOrigin
    quote: ExecutableQuote
    risk_inputs: PortfolioInputs
    expected_account_revision: Revision
    expected_ledger_revision: Revision


class ReservationReplayBindingV2(LedgerModel):
    """Bounded raw replay inputs; serialization conveys no authority."""

    contract_version: Literal["ctcc-reservation-replay-v2"]
    original_market_json: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    current_market_json: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    original_inputs_json: str = Field(min_length=1, max_length=16 * 1024 * 1024)
    quote_json: str = Field(min_length=1, max_length=1024 * 1024)
    reference_json: str = Field(min_length=1, max_length=32768)
    recheck_json: str = Field(min_length=1, max_length=16 * 1024 * 1024)
    consumed_event_keys: tuple[Digest, ...] = Field(max_length=2048)


class ReservationRequestV2(ReservationRequest):
    contract_version: Literal["ctcc-reservation-request-v2"]
    replay_binding: ReservationReplayBindingV2

    @model_validator(mode="after")
    def explicit_range_v5(self):
        if type(self.origin.evidence.pre_evidence) is not HistoryPreEvidenceRunV5:
            raise ValueError("ledger_v2_requires_range_v5")
        return self


class ReservationReplayBindingV3(LedgerModel):
    """Range V5 replay inputs plus the account packet pinned before reserve."""

    contract_version: Literal["ctcc-reservation-replay-v3"]
    account_packet_json: str = Field(min_length=1, max_length=16 * 1024 * 1024)
    account_packet_sha256: Digest
    account_plan_sha256: Digest
    original_market_json: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    current_market_json: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    original_inputs_json: str = Field(min_length=1, max_length=16 * 1024 * 1024)
    quote_json: str = Field(min_length=1, max_length=1024 * 1024)
    reference_json: str = Field(min_length=1, max_length=32768)
    recheck_json: str = Field(min_length=1, max_length=16 * 1024 * 1024)
    consumed_event_keys: tuple[Digest, ...] = Field(max_length=2048)


class ReservationRequestV3(ReservationRequest):
    contract_version: Literal["ctcc-reservation-request-v3"]
    replay_binding: ReservationReplayBindingV3

    @model_validator(mode="after")
    def explicit_range_v5(self):
        if type(self.origin.evidence.pre_evidence) is not HistoryPreEvidenceRunV5:
            raise ValueError("ledger_v3_requires_range_v5")
        return self


class ScenarioOperands(LedgerModel):
    entry: Price
    stop_loss: Price
    cost_per_base: Price
    contracts: Price
    contract_value: Price
    leverage: Annotated[int, Field(ge=1, le=125)]


class RiskCoverage(LedgerModel):
    candidate: ScenarioOperands
    execution: ScenarioOperands
    risk_amount: Price
    margin_amount: Price
    notional_amount: Price
    rounding: Literal["ceil_once_to_1e-20_from_exact_fractions"] = (
        "ceil_once_to_1e-20_from_exact_fractions"
    )
    # These two samples do not bound all possible execution/fill prices.
    all_fill_prices_covered: Literal[False] = False

    @model_validator(mode="after")
    def coverage(self):
        if self.all_fill_prices_covered is not False:
            raise ValueError("sampled_prices_only")
        values = exact_coverage(self.candidate, self.execution)
        if any(getattr(self, key) != value for key, value in values.items()):
            raise ValueError("ledger_coverage_mismatch")
        return self


class ReservationReceipt(LedgerModel):
    scope: LedgerScope
    reservation_id: Digest
    original_event_key: Digest
    report_id: ReportId
    instrument_id: Annotated[str, Field(min_length=1, max_length=64)]
    direction: Literal["long", "short"]
    correlation_group: Annotated[str, Field(min_length=1, max_length=128)]
    request_sha256: Digest
    coverage: RiskCoverage
    state: State
    state_revision: Revision
    account_revision: Revision
    ledger_revision: Revision
    created_at: datetime
    updated_at: datetime
    deadline: datetime
    durable_record_only: Literal[True] = True

    @field_validator("created_at", "updated_at", "deadline")
    @classmethod
    def utc(cls, value):
        if type(value) is not datetime:
            raise ValueError("exact_ledger_clock_required")
        return require_aware(value)

    @model_validator(mode="after")
    def clocks(self):
        if not self.created_at < self.deadline or self.updated_at < self.created_at:
            raise ValueError("ledger_receipt_clock_order")
        if self.state_revision < 1 or self.account_revision < 1:
            raise ValueError("ledger_receipt_revision_missing")
        return self


class LedgerScopeState(LedgerModel):
    scope: LedgerScope
    account_revision: Revision
    ledger_revision: Revision
    claims_sha256: Digest | None
    active: Annotated[tuple[ReservationReceipt, ...], Field(max_length=2048)]


_CHILDREN = {
    AccountLedgerClaims: {
        "scope": LedgerScope,
        "account": PortfolioRiskSnapshot,
        "authority": DemoRiskAuthority,
    },
    ReservationRequest: {
        "scope": LedgerScope,
        "origin": RecheckOrigin,
        "quote": ExecutableQuote,
        "risk_inputs": PortfolioInputs,
    },
    ReservationRequestV2: {
        "scope": LedgerScope,
        "origin": RecheckOrigin,
        "quote": ExecutableQuote,
        "risk_inputs": PortfolioInputs,
        "replay_binding": ReservationReplayBindingV2,
    },
    ReservationRequestV3: {
        "scope": LedgerScope,
        "origin": RecheckOrigin,
        "quote": ExecutableQuote,
        "risk_inputs": PortfolioInputs,
        "replay_binding": ReservationReplayBindingV3,
    },
    RiskCoverage: {"candidate": ScenarioOperands, "execution": ScenarioOperands},
    ReservationReceipt: {"scope": LedgerScope, "coverage": RiskCoverage},
    LedgerScopeState: {"scope": LedgerScope},
}
_MODELS = {
    LedgerScope,
    AccountLedgerClaims,
    ReservationRequest,
    ReservationRequestV2,
    ReservationReplayBindingV2,
    ReservationRequestV3,
    ReservationReplayBindingV3,
    ScenarioOperands,
    RiskCoverage,
    ReservationReceipt,
    LedgerScopeState,
}


_QUOTE_SCALARS = {
    "report_id": str,
    "instrument_id": str,
    "source": str,
    "bid": Decimal,
    "ask": Decimal,
    "mark_price": Decimal,
    "bid_size": Decimal,
    "ask_size": Decimal,
    "funding_rate": Decimal,
    "quote_time": datetime,
    "mark_time": datetime,
    "funding_time": datetime,
    "received_at": datetime,
    "request_started_at": datetime,
}


def _quote_scalar_guard(quote):
    """This new quote junction rejects opaque callbacks before shared helpers.

    ``isinstance`` can consult a foreign object's ``__class__`` property. Type
    sets/membership can also invoke its metaclass equality/hash. Use identities
    only, including for timezone implementations before any utcoffset call.
    This is NOT a callback-free guarantee for unrelated pre-existing helpers.
    """
    try:
        raw = object.__getattribute__(quote, "__dict__")
        extra = object.__getattribute__(quote, "__pydantic_extra__")
        private = object.__getattribute__(quote, "__pydantic_private__")
        fields = object.__getattribute__(quote, "__pydantic_fields_set__")
    except AttributeError:
        raise QualificationLedgerError("dirty_quote_contract") from None
    if (
        type(raw) is not dict
        or len(raw) != len(_QUOTE_SCALARS)
        or extra is not None
        or private is not None
        or type(fields) is not set
        or len(fields) > len(_QUOTE_SCALARS)
    ):
        raise QualificationLedgerError("dirty_quote_contract")
    for name in fields:
        if type(name) is not str or name not in _QUOTE_SCALARS:
            raise QualificationLedgerError("dirty_quote_contract")
    for name, part in raw.items():
        if type(name) is not str or name not in _QUOTE_SCALARS:
            raise QualificationLedgerError("dirty_quote_contract")
        if name == "request_started_at" and part is None:
            continue
        expected = _QUOTE_SCALARS[name]
        if type(part) is not expected:
            raise QualificationLedgerError("exact_quote_scalars_required")
        if expected is datetime:
            zone = part.tzinfo
            if type(zone) is not timezone and type(zone) is not ZoneInfo:
                raise QualificationLedgerError("exact_quote_timezone_required")


def checked_bootstrap(value, expected):
    """Reject foreign Python callbacks at the new cold-start read junction.

    This guard is deliberately limited to the bootstrap scope/state tree. It
    does not alter existing receipt schemas or confer authority on DB records.
    """
    if expected is not LedgerScope and expected is not LedgerScopeState:
        raise QualificationLedgerError("exact_bootstrap_contract_required")
    if type(value) is not expected:
        raise QualificationLedgerError("exact_bootstrap_contract_required")

    def guard(part, depth=0):
        if depth > 8:
            raise QualificationLedgerError("dirty_bootstrap_contract")
        kind = type(part)
        if any(
            kind is model
            for model in (
                LedgerScope,
                LedgerScopeState,
                ReservationReceipt,
                RiskCoverage,
                ScenarioOperands,
            )
        ):
            raw = object.__getattribute__(part, "__dict__")
            fields = object.__getattribute__(part, "__pydantic_fields_set__")
            if (
                type(raw) is not dict
                or any(type(key) is not str for key in raw)
                or set(raw) != set(kind.model_fields)
                or type(fields) is not set
                or any(type(key) is not str for key in fields)
                or not fields <= set(kind.model_fields)
                or object.__getattribute__(part, "__pydantic_extra__") is not None
                or object.__getattribute__(part, "__pydantic_private__") is not None
            ):
                raise QualificationLedgerError("dirty_bootstrap_contract")
            for item in raw.values():
                guard(item, depth + 1)
            return
        if kind is tuple and len(part) <= 2048:
            for item in part:
                guard(item, depth + 1)
            return
        if kind is datetime:
            zone = part.tzinfo
            if type(zone) is not timezone and type(zone) is not ZoneInfo:
                raise QualificationLedgerError("exact_bootstrap_timezone_required")
            return
        if (
            part is None
            or kind is bool
            or kind is int
            or kind is Decimal
            or kind is str
        ):
            return
        raise QualificationLedgerError("exact_bootstrap_scalar_required")

    guard(value)
    return checked(value, expected)


def checked(value, expected):
    """Context-specific exact class guards BEFORE serialization/revalidation."""
    if type(value) is not expected or expected not in _MODELS:
        raise QualificationLedgerError("exact_ledger_contract_required")
    if expected in (
        ReservationRequestV2,
        ReservationReplayBindingV2,
        ReservationRequestV3,
        ReservationReplayBindingV3,
    ):
        from app.trade_qualification.submission_intent import _guard

        _guard(value)
    if set(value.__dict__) != set(expected.model_fields) or value.__pydantic_extra__:
        raise QualificationLedgerError("dirty_ledger_contract")
    for name, child in _CHILDREN.get(expected, {}).items():
        item = value.__dict__[name]
        if type(item) is not child:
            raise QualificationLedgerError("exact_ledger_child_required")
        if child in _MODELS:
            checked(item, child)
        elif child is RecheckOrigin:
            copy_recheck_origin(item)
        elif child is ExecutableQuote:
            _quote_scalar_guard(item)
            _preflight(item.__dict__)
            _revalidate(item, child)
        else:
            _copy(item, child)
    if expected is LedgerScopeState:
        active = value.__dict__["active"]
        if type(active) is not tuple or len(active) > 2048:
            raise QualificationLedgerError("bounded_ledger_holds_required")
        for item in active:
            checked(item, ReservationReceipt)
    for name, item in value.__dict__.items():
        if name not in _CHILDREN.get(expected, {}) and name != "active":
            _preflight(item)
    return expected.model_validate(_plain(value), strict=True)


def canonical(value) -> str:
    checked(value, type(value))
    raw = json.dumps(
        value.model_dump(mode="json", round_trip=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    if len(raw.encode()) > MAX_JSON_BYTES:
        raise QualificationLedgerError("ledger_payload_too_large")
    return raw


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decode(raw: str, expected):
    """Only stored bounded canonical JSON; public Python contracts stay strict."""
    if type(raw) is not str or len(raw) > MAX_JSON_BYTES:
        raise QualificationLedgerError("invalid_ledger_json")
    try:
        result = expected.model_validate_json(raw, strict=True)
        result = checked(result, expected)
        if canonical(result) != raw:
            raise ValueError("noncanonical")
        return result
    except (ValueError, TypeError, RecursionError) as exc:
        raise QualificationLedgerError("invalid_ledger_json") from exc


def checked_reservation_request(value):
    if type(value) is ReservationRequest:
        return checked(value, ReservationRequest)
    if type(value) is ReservationRequestV2:
        # New source-bearing envelopes receive the exact raw-tree preflight
        # before any legacy validation/serialization can inspect a callback.
        from app.trade_qualification.submission_intent import _guard

        _guard(value)
        return checked(value, ReservationRequestV2)
    if type(value) is ReservationRequestV3:
        from app.trade_qualification.submission_intent import _guard

        _guard(value)
        return checked(value, ReservationRequestV3)
    raise QualificationLedgerError("exact_reservation_request_required")


def decode_reservation_request(raw):
    if type(raw) is not str or not 0 < len(raw) <= MAX_JSON_BYTES:
        raise QualificationLedgerError("invalid_ledger_json")
    try:
        body = json.loads(raw)
        if type(body) is not dict:
            raise ValueError("shape")
        if "contract_version" not in body:
            kind = ReservationRequest
        elif body["contract_version"] == "ctcc-reservation-request-v2":
            kind = ReservationRequestV2
        elif body["contract_version"] == "ctcc-reservation-request-v3":
            kind = ReservationRequestV3
        else:
            raise ValueError("version")
        return checked_reservation_request(decode(raw, kind))
    except (ValueError, TypeError, RecursionError):
        raise QualificationLedgerError("invalid_ledger_json") from None


def _ceil(value: Fraction) -> Decimal:
    scaled = value * 10**20
    coefficient = -(-scaled.numerator // scaled.denominator)
    # Decimal tuple construction is independent of the caller's context/traps.
    if coefficient <= 0 or len(str(coefficient)) > 40:
        raise QualificationLedgerError("ledger_amount_out_of_bounds")
    return Decimal((0, tuple(map(int, str(coefficient))), -20))


def exact_coverage(candidate: ScenarioOperands, execution: ScenarioOperands):
    samples = []
    for sample in (candidate, execution):
        checked(sample, ScenarioOperands)
        quantity = Fraction(sample.contracts) * Fraction(sample.contract_value)
        notional = quantity * Fraction(sample.entry)
        risk = quantity * (
            abs(Fraction(sample.entry) - Fraction(sample.stop_loss))
            + Fraction(sample.cost_per_base)
        )
        samples.append((risk, notional / sample.leverage, notional))
    return {
        name: _ceil(max(row[index] for row in samples))
        for index, name in enumerate(
            ("risk_amount", "margin_amount", "notional_amount")
        )
    }


def claim_stamps(claims):
    return tuple(getattr(claims.account, name) for name in STAMP_FIELDS) + (
        claims.authority.stamp,
    )


def validate_claims(claims, now, *, newer_than=None):
    claims = checked(claims, AccountLedgerClaims)
    now = require_aware(now)
    scope, account = claims.scope, claims.account
    pending_ids = [item.reservation_id for item in account.pending_reservations]
    if len(set(pending_ids)) != len(pending_ids):
        raise QualificationLedgerError("duplicate_claimed_reservation")
    if (account.account_id, account.settlement_currency) != (
        scope.account_id,
        scope.settlement_currency,
    ) or claims.authority.environment != "demo":
        raise QualificationLedgerError("ledger_account_scope_mismatch")
    if account.position_count != len(
        account.positions
    ) or account.pending_reservation_count != len(account.pending_reservations):
        raise QualificationLedgerError("ledger_account_count_mismatch")
    for stamp in claim_stamps(claims):
        if stamp.account_id != scope.account_id or not stamp.complete:
            raise QualificationLedgerError("ledger_incomplete_account_claims")
        if not stamp.observed_at <= stamp.received_at <= now:
            raise QualificationLedgerError("ledger_claim_clock_order")
        if newer_than is not None and not newer_than < stamp.observed_at:
            raise QualificationLedgerError("ledger_reconciliation_not_newer")
    return claims


def reservation_id(scope, event_key):
    checked(scope, LedgerScope)
    if (
        type(event_key) is not str
        or len(event_key) != 64
        or any(c not in "0123456789abcdef" for c in event_key)
    ):
        raise QualificationLedgerError("invalid_ledger_event_key")
    return hashlib.sha256((canonical(scope) + ":" + event_key).encode()).hexdigest()


def _check_original_range_location(pre, quote, *, observed_at):
    """Rebuild the original V4 band; consistency is never qualification authority.

    Accepts honest G7/G8-denied diagnostics so this restriction can be tested
    without fabricating a passing G12 origin. No original fact is rewritten.
    """
    from app.trade_qualification.history_engine import _copy as copy_history
    from app.trade_qualification.one_shot import _guard_original

    if (
        not any(
            type(pre) is model
            for model in (HistoryPreEvidenceRunV4, HistoryPreEvidenceRunV5)
        )
        or type(quote) is not ExecutableQuote
    ):
        raise QualificationLedgerError("ledger_exact_range_contract_required")
    _guard_original(pre)
    _guard_original(observed_at)
    _quote_scalar_guard(quote)
    pre = copy_history(pre, type(pre))
    prefix = pre.prefix
    if not prefix.prefix_complete:
        raise QualificationLedgerError("ledger_original_range_prefix_incomplete")
    zone, code = build_original_range_anchor_zone(
        prefix.detection,
        tick_size=prefix.policy.tick_size,
        max_allowed_drift_bps=prefix.policy.max_allowed_drift_bps,
        expires_at=prefix.timing.latest_valid_entry_time,
    )
    if code != "passed" or zone != pre.result.entry_zone:
        raise QualificationLedgerError("ledger_original_range_zone_mismatch")
    location = evaluate_location(
        report_id=prefix.intent.report_id,
        instrument_id=prefix.intent.instrument_id,
        direction=prefix.intent.direction,
        zone=zone,
        candidate_entry=prefix.intent.candidate_entry,
        quote=quote,
        current_time=observed_at,
        max_quote_age_seconds=prefix.policy.data.maximum_quote_age_seconds,
    )
    if not location.passed:
        raise QualificationLedgerError("ledger_original_range_location_denied")
    return location


def _check_range_reservation_admission(pre, quote, *, observed_at):
    _check_original_range_location(pre, quote, observed_at=observed_at)
    # V4 currently specifies only an extra anchor restriction. Its complete
    # range protection/alignment policy remains unverified, and this request
    # lacks original quote/reference documents for full G1--G11 replay. Neither
    # a structurally consistent origin nor a passing band grants admission.
    raise QualificationLedgerError("ledger_range_policy_incomplete")


def replay_range_reservation(request, *, observed_at):
    """Recompute original G1--G12, recorded R7, and current locked-time R7.

    This checks supplied source mathematics, not source authenticity. A copied
    boolean or a new schema label cannot stand in for any replay document.
    """
    from app.domain.market import MarketSnapshot
    from app.trade_qualification import data, quote_collector
    from app.trade_qualification.recheck import (
        RecordedRecheckAssessment,
        evaluate_recorded_recheck,
        verify_recorded_recheck,
    )
    from app.trade_qualification.submission_intent import (
        _document,
        _original_inputs_document,
    )

    if type(request) not in (ReservationRequestV2, ReservationRequestV3):
        raise QualificationLedgerError("ledger_range_replay_binding_required")
    request = checked_reservation_request(request)
    binding = request.replay_binding
    try:
        recorded = _document(binding.recheck_json, RecordedRecheckAssessment)
        original = _original_inputs_document(binding.original_inputs_json)
        quote = _document(binding.quote_json, quote_collector.CollectedQuote)
        reference = _document(binding.reference_json, data.WSReferenceObservation)
        before = _document(binding.original_market_json, MarketSnapshot)
        after = _document(binding.current_market_json, MarketSnapshot)
        if (
            recorded.origin != request.origin
            or quote.quote != request.quote
            or not recorded.observed_at <= observed_at < request.origin.deadline
            or len(set(binding.consumed_event_keys)) != len(binding.consumed_event_keys)
        ):
            raise ValueError("binding_mismatch")
        inputs = {
            "origin": request.origin,
            "original_inputs": {
                name: getattr(original, name) for name in type(original).model_fields
            },
            "quote": quote,
            "reference": reference,
            "current_risk_inputs": request.risk_inputs,
            "consumed_event_keys": frozenset(binding.consumed_event_keys),
        }
        actual = verify_recorded_recheck(
            recorded, before, after, **inputs, observed_at=recorded.observed_at
        )
        current = evaluate_recorded_recheck(
            before, after, **inputs, observed_at=observed_at
        )
        if (
            not actual.computational_checks_passed
            or not current.computational_checks_passed
        ):
            raise ValueError("replay_denied")
        _check_original_range_location(
            request.origin.evidence.pre_evidence, request.quote, observed_at=observed_at
        )
        return current
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        ArithmeticError,
        RecursionError,
    ):
        raise QualificationLedgerError("ledger_range_replay_denied") from None


def prepare_reservation(request, claims, active, *, observed_at):
    """Called again under the account lock, not a caller-written PASS input."""
    request = checked_reservation_request(request)
    if type(request) in (ReservationRequestV2, ReservationRequestV3):
        from app.trade_qualification.submission_intent import _guard

        _guard(observed_at)
    claims = validate_claims(claims, observed_at)
    if (
        claims.scope != request.scope
        or request.risk_inputs.account != claims.account
        or request.risk_inputs.authority != claims.authority
    ):
        raise QualificationLedgerError("ledger_current_claims_mismatch")
    if type(request.origin.evidence.pre_evidence) is HistoryPreEvidenceRunV4:
        _check_range_reservation_admission(
            request.origin.evidence.pre_evidence,
            request.quote,
            observed_at=observed_at,
        )
    if type(request.origin.evidence.pre_evidence) is HistoryPreEvidenceRunV5:
        replay_range_reservation(request, observed_at=observed_at)
    if type(active) is not tuple or len(active) > 2048:
        raise QualificationLedgerError("bounded_ledger_holds_required")
    pending = {
        item.reservation_id: item for item in claims.account.pending_reservations
    }
    if len(pending) != len(claims.account.pending_reservations):
        raise QualificationLedgerError("duplicate_claimed_reservation")
    for hold in active:
        hold = checked(hold, ReservationReceipt)
        if hold.scope != request.scope or hold.state == "reconciled_flat":
            raise QualificationLedgerError("invalid_active_ledger_hold")
        item = PendingReservation(
            reservation_id=hold.reservation_id,
            instrument_id=hold.instrument_id,
            direction=hold.direction,
            settlement_currency=hold.scope.settlement_currency,
            notional=hold.coverage.notional_amount,
            margin=hold.coverage.margin_amount,
            risk_amount=hold.coverage.risk_amount,
            correlation_group=hold.correlation_group,
        )
        if item.reservation_id in pending and pending[item.reservation_id] != item:
            raise QualificationLedgerError("local_claimed_reservation_conflict")
        pending[item.reservation_id] = item
    if len(pending) > 2048:
        raise QualificationLedgerError("ledger_pending_limit")
    raw = _plain(request.risk_inputs)
    raw["account"]["pending_reservations"] = tuple(
        _plain(item) for item in pending.values()
    )
    raw["account"]["pending_reservation_count"] = len(pending)
    risk_inputs = PortfolioInputs.model_validate(raw, strict=True)
    result = evaluate_current_risk(
        request.origin,
        quote=request.quote,
        current_risk_inputs=risk_inputs,
        observed_at=observed_at,
    )
    if not result.passed:
        raise QualificationLedgerError("ledger_current_risk_denied")

    def operands(scenario):
        return ScenarioOperands(
            entry=scenario.candidate_entry,
            stop_loss=result.original_stop_loss,
            cost_per_base=scenario.cost_per_base,
            contracts=risk_inputs.requested_contracts,
            contract_value=risk_inputs.instrument.contract_value,
            leverage=risk_inputs.requested_leverage,
        )

    candidate, execution = (
        operands(result.economics.candidate_result),
        operands(result.economics.execution_result),
    )
    coverage = RiskCoverage(
        candidate=candidate, execution=execution, **exact_coverage(candidate, execution)
    )
    return coverage, result
