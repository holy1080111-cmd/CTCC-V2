"""Data-only canonical DB0054 claims envelope; no issuer or source authority.

The existing strict legacy claims payload remains unchanged. Unknown required
financial operands cannot be replaced with zero to fit it. A future genuine
native financial adopter/issuer is still required; decoding saved bytes never
creates an owned account revision, G11 qualification or an order capability.
"""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.trade_qualification import reservations as legacy
from app.trade_qualification.models import Price, QualificationModel
from app.trade_qualification.portfolio import (
    Amount,
    DemoRiskAuthority,
    EvidenceStamp,
    PendingReservation,
    PortfolioRiskPolicy,
    PositionExposure,
)
from app.trade_qualification.portfolio import (
    _canonical as _risk_policy_document,
)

SCHEMA_VERSION = "ctcc.account_ledger_claims.v2"
MAX_CLAIMS_BYTES = 8 * 1024 * 1024  # Exact DB0054 claims_json byte ceiling.
MAX_DEPTH = 24
MAX_VALUES = 100_000
MAX_CONTAINER_ITEMS = 4096
MAX_OBJECT_FIELDS = 128
MAX_INTEGER = 2**63 - 1
FALSE_FIELDS = (
    "execution_authority",
    "source_authenticity_verified",
    "account_evidence_authenticated",
    "execution_recheck_performed",
)
FIELDS = frozenset({"schema_version", "scope", "claims", *FALSE_FIELDS})
ISSUER_PROOF_KEYS = (
    "registration",
    "current_identity",
    "history_prefix",
    "product_scope",
    "history_coverage",
    "cashflow_and_funding_finality",
    "loss_streak_seed",
    "unchanged_window_drawdown",
    "inventory_metadata",
    "protection",
    "local_ledger",
    "current_portfolio",
)
_DIGEST = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_UID = re.compile(r"[0-9]{1,32}\Z", re.ASCII)


class AccountLedgerClaimsV2CodecError(ValueError):
    """Fixed nonprivate data-contract failure, never an admission result."""


def _deny():
    raise AccountLedgerClaimsV2CodecError("account_ledger_claims_v2_invalid") from None


def _plain(value, *, depth=0, budget=None):
    if budget is None:
        budget = [MAX_VALUES]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_DEPTH:
        _deny()
    kind = type(value)
    if value is None or kind is bool:
        return
    if kind is int:
        if not -MAX_INTEGER <= value <= MAX_INTEGER:
            _deny()
        return
    if kind is str:
        if len(value) > MAX_CLAIMS_BYTES or any(
            0xD800 <= ord(char) <= 0xDFFF for char in value
        ):
            _deny()
        return
    if kind is list:
        if len(value) > MAX_CONTAINER_ITEMS:
            _deny()
        for item in value:
            _plain(item, depth=depth + 1, budget=budget)
        return
    if kind is dict:
        if len(value) > MAX_OBJECT_FIELDS or any(type(key) is not str for key in value):
            _deny()
        for key, item in value.items():
            _plain(key, depth=depth + 1, budget=budget)
            _plain(item, depth=depth + 1, budget=budget)
        return
    # All existing Decimal amounts are canonical JSON strings, not floats.
    _deny()


def _canonical(document):
    _plain(document)
    raw = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    if not 1 <= len(raw) <= MAX_CLAIMS_BYTES:
        _deny()
    return raw


def _pairs(items):
    if len(items) > MAX_OBJECT_FIELDS:
        _deny()
    result = {}
    for key, value in items:
        if key in result:
            _deny()
        result[key] = value
    return result


def _integer(token):
    if len(token) > 20:
        _deny()
    value = int(token)
    if str(value) != token or not -MAX_INTEGER <= value <= MAX_INTEGER:
        _deny()
    return value


def _forbidden_number(_token):
    _deny()


def _scope(claims):
    scope = claims.scope
    if (
        scope.environment != "demo"
        or scope.settlement_currency != "USDT"
        or type(scope.account_id) is not str
        or _UID.fullmatch(scope.account_id) is None
    ):
        _deny()
    return [scope.environment, scope.account_id, scope.settlement_currency]


def _checked_claims(value):
    claims = legacy.checked(value, legacy.AccountLedgerClaims)
    scope = _scope(claims)
    account = claims.account
    if (
        account.account_id != scope[1]
        or account.settlement_currency != scope[2]
        or claims.authority.environment != scope[0]
        or account.position_count != len(account.positions)
        or account.pending_reservation_count != len(account.pending_reservations)
    ):
        _deny()
    for stamp in legacy.claim_stamps(claims):
        if (
            stamp.environment != scope[0]
            or stamp.account_id != scope[1]
            or stamp.observed_at > stamp.received_at
        ):
            _deny()
    # No complete/armed/clock/source boolean is promoted by this codec.
    return claims


def encode_account_ledger_claims_v2(claims):
    """Encode exact typed data only; required unknown seed/amounts reject."""
    try:
        checked = _checked_claims(claims)
        payload = json.loads(legacy.canonical(checked))
        return _canonical(
            {
                "schema_version": SCHEMA_VERSION,
                "scope": _scope(checked),
                "claims": payload,
                **dict.fromkeys(FALSE_FIELDS, False),
            }
        )
    except AccountLedgerClaimsV2CodecError:
        raise
    except (ValueError, TypeError, AttributeError, ArithmeticError, RecursionError):
        _deny()


def decode_account_ledger_claims_v2(raw):
    """Return legacy DATA, never a source-issued/owned revision capability."""
    try:
        if type(raw) is not bytes or not 1 <= len(raw) <= MAX_CLAIMS_BYTES:
            _deny()
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_int=_integer,
            parse_float=_forbidden_number,
            parse_constant=_forbidden_number,
        )
        if (
            type(document) is not dict
            or set(document) != FIELDS
            or document["schema_version"] != SCHEMA_VERSION
            or type(document["scope"]) is not list
            or len(document["scope"]) != 3
            or any(type(item) is not str for item in document["scope"])
            or any(document[key] is not False for key in FALSE_FIELDS)
            or type(document["claims"]) is not dict
            or _canonical(document) != raw
        ):
            _deny()
        payload = _canonical(document["claims"]).decode("ascii")
        claims = _checked_claims(legacy.decode(payload, legacy.AccountLedgerClaims))
        if (
            document["scope"] != _scope(claims)
            or encode_account_ledger_claims_v2(claims) != raw
        ):
            _deny()
        return claims
    except AccountLedgerClaimsV2CodecError:
        raise
    except (ValueError, TypeError, AttributeError, ArithmeticError, RecursionError):
        _deny()


def account_ledger_claims_v2_sha256(raw):
    """DB0054 hashes exact UTF8 text bytes; syntax validation is not trust."""
    decode_account_ledger_claims_v2(raw)
    return hashlib.sha256(raw).hexdigest()


def issuer_proof_digest_items_v1(proofs):
    """Check DB0054's exact12 digest shape only, never their source truth."""
    if (
        type(proofs) is not dict
        or len(proofs) != len(ISSUER_PROOF_KEYS)
        or any(type(key) is not str for key in proofs)
        or set(proofs) != set(ISSUER_PROOF_KEYS)
    ):
        _deny()
    if any(
        type(value) is not str or len(value) != 64 or _DIGEST.fullmatch(value) is None
        for value in proofs.values()
    ):
        _deny()
    return tuple((key, proofs[key]) for key in ISSUER_PROOF_KEYS)


# This additive inner payload is deliberately distinct from the strict legacy
# claims accepted by the original v2 encoder/decoder above. SQL DB0054 retains
# its outer v2 envelope; neither payload schema nor typed data grants ownership.
FINITE_PAYLOAD_SCHEMA_V1 = "ctcc.account_ledger_claims.finite_api_asof.v1"
FINITE_FINANCIAL_SCHEMA_V1 = "ctcc.account_financial_operands.finite_api_asof.v1"
MAX_RATIO_BITS_V1 = 4096
MAX_RATIO_CHARS_V1 = 1235


def _finite_utc(value):
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _deny()
    return value.astimezone(UTC)


class ExactRatioV1(QualificationModel):
    """Bounded exact DATA; a canonical ratio is never a financial proof."""

    numerator: Annotated[
        str, Field(min_length=1, max_length=1235, pattern=r"^(0|-?[1-9][0-9]{0,1234})$")
    ]
    denominator: Annotated[
        str, Field(min_length=1, max_length=1235, pattern=r"^[1-9][0-9]{0,1234}$")
    ]

    @model_validator(mode="after")
    def reduced(self):
        numerator, denominator = int(self.numerator), int(self.denominator)
        if (
            numerator.bit_length() > MAX_RATIO_BITS_V1
            or denominator.bit_length() > MAX_RATIO_BITS_V1
        ):
            _deny()
        value = Fraction(numerator, denominator)
        if (
            str(value.numerator) != self.numerator
            or str(value.denominator) != self.denominator
        ):
            _deny()
        return self


class FiniteCurrentReferenceV1(QualificationModel):
    capture_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    head_sha256: legacy.Digest
    plan_sha256: legacy.Digest
    packet_sha256: legacy.Digest
    session_binding_sha256: legacy.Digest


class FinitePortfolioSnapshotV1(legacy.LedgerModel):
    """Actual inventory/availability DATA without a fabricated history seed.

    The separately timed balance and coupled risk/position observations retain
    their source stamps. No sampled/continuous peak is inferred here.
    """

    account_id: Annotated[str, Field(pattern=r"^[0-9]{1,32}$")]
    settlement_currency: Literal["USDT"]
    balance_stamp: EvidenceStamp
    positions_stamp: EvidenceStamp
    history_stamp: EvidenceStamp
    reservations_stamp: EvidenceStamp
    equity: Price
    available_margin: Amount
    positions: Annotated[tuple[PositionExposure, ...], Field(max_length=2048)]
    pending_reservations: Annotated[
        tuple[PendingReservation, ...], Field(max_length=2048)
    ]
    position_count: Annotated[int, Field(ge=0, le=2048)]
    pending_reservation_count: Annotated[int, Field(ge=0, le=2048)]
    history_start: datetime
    history_end: datetime
    original_start_seed: None

    _utc_times = field_validator("history_start", "history_end")(_finite_utc)

    @model_validator(mode="after")
    def bindings(self):
        if (
            self.position_count != len(self.positions)
            or self.pending_reservation_count != len(self.pending_reservations)
            or self.history_start > self.history_end
        ):
            _deny()
        for stamp in (
            self.balance_stamp,
            self.positions_stamp,
            self.history_stamp,
            self.reservations_stamp,
        ):
            if (
                stamp.environment != "demo"
                or stamp.account_id != self.account_id
                or stamp.observed_at > stamp.received_at
            ):
                _deny()
        for item in (*self.positions, *self.pending_reservations):
            if item.settlement_currency != self.settlement_currency:
                _deny()
        if len({item.position_id for item in self.positions}) != len(
            self.positions
        ) or len({item.reservation_id for item in self.pending_reservations}) != len(
            self.pending_reservations
        ):
            _deny()
        return self


class FiniteFinancialOperandsV1(legacy.LedgerModel):
    """Exact amounts, a stated bound and measured HWM; unknown stays None.

    This type is deliberately data-only and may describe incomplete source
    facts. Native issuance must verify all required original operands itself.
    """

    financial_schema: Literal["ctcc.account_financial_operands.finite_api_asof.v1"]
    source_as_of_at: datetime
    available_at: datetime
    evaluated_at: datetime
    daily_window_started_at: datetime
    weekly_window_started_at: datetime
    daily_net_pnl: ExactRatioV1 | None
    weekly_net_pnl: ExactRatioV1 | None
    current_loss_streak_upper_bound: Annotated[int, Field(ge=0, le=4096)] | None
    loss_streak_basis: (
        Literal["observed_positive_reset", "explicit_operator_policy_counter"] | None
    )
    init_ack_sha256: legacy.Digest | None
    init_at: datetime | None
    original_start_seed: None
    measured_hwm_window_started_at: datetime
    measured_peak_equity: ExactRatioV1 | None
    measured_peak_observed_at: datetime | None
    measured_current_equity: ExactRatioV1 | None
    measured_hwm_policy_sha256: legacy.Digest
    drawdown_basis: Literal["every_accepted_measured_balance_in_original_window"]
    actual_continuous_peak: None
    cashflow_adjusted_peak: None
    original_expires_at: datetime
    bank_window_policy_sha256: legacy.Digest
    current_source_reference: FiniteCurrentReferenceV1
    original_prefix_sha256: legacy.Digest
    original_raw_bank_sha256: legacy.Digest
    api_population_receipt_sha256: legacy.Digest
    source_receipt_sha256: legacy.Digest
    measured_hwm_receipt_sha256: legacy.Digest | None
    position_margin_bucket_usdt: Annotated[Price, Field(le=Decimal(300))]
    original_risk_policy_sha256: legacy.Digest
    missing_obligations: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=128)], ...],
        Field(max_length=128),
    ]

    _utc_times = field_validator(
        "source_as_of_at",
        "available_at",
        "evaluated_at",
        "daily_window_started_at",
        "weekly_window_started_at",
        "measured_hwm_window_started_at",
        "original_expires_at",
    )(_finite_utc)

    @field_validator("measured_peak_observed_at", "init_at")
    @classmethod
    def nullable_utc(cls, value):
        return None if value is None else _finite_utc(value)

    @model_validator(mode="after")
    def chronology(self):
        if self.loss_streak_basis is None:
            if (
                self.current_loss_streak_upper_bound is not None
                or self.init_ack_sha256 is not None
                or self.init_at is not None
            ):
                _deny()
        elif self.loss_streak_basis == "observed_positive_reset":
            if self.init_ack_sha256 is not None or self.init_at is not None:
                _deny()
        elif (
            self.init_ack_sha256 is None
            or self.init_at is None
            or self.init_at > self.source_as_of_at
        ):
            _deny()
        if (
            not self.source_as_of_at
            <= self.available_at
            <= self.evaluated_at
            < self.original_expires_at
            or self.daily_window_started_at
            != self.evaluated_at.replace(hour=0, minute=0, second=0, microsecond=0)
            or self.weekly_window_started_at != self.evaluated_at - timedelta(days=7)
            or self.missing_obligations != tuple(sorted(set(self.missing_obligations)))
        ):
            _deny()
        if (
            self.daily_net_pnl is not None
            and self.daily_window_started_at > self.source_as_of_at
        ) or (
            self.weekly_net_pnl is not None
            and self.weekly_window_started_at >= self.source_as_of_at
        ):
            _deny()
        for amount in (self.measured_peak_equity, self.measured_current_equity):
            if amount is not None and int(amount.numerator) <= 0:
                _deny()
        if self.measured_peak_observed_at is not None and not (
            self.measured_hwm_window_started_at
            <= self.measured_peak_observed_at
            <= self.evaluated_at
        ):
            _deny()
        # The measured maximum and its original witness are a paired value.
        if (self.measured_peak_equity is None) != (
            self.measured_peak_observed_at is None
        ):
            _deny()
        if (
            self.measured_peak_equity is not None
            and self.measured_current_equity is not None
        ):
            peak = Fraction(
                int(self.measured_peak_equity.numerator),
                int(self.measured_peak_equity.denominator),
            )
            current_equity = Fraction(
                int(self.measured_current_equity.numerator),
                int(self.measured_current_equity.denominator),
            )
            if peak < current_equity:
                _deny()
        return self


class FiniteAccountLedgerClaimsV1(legacy.LedgerModel):
    payload_schema: Literal["ctcc.account_ledger_claims.finite_api_asof.v1"]
    scope: legacy.LedgerScope
    account: FinitePortfolioSnapshotV1
    financial: FiniteFinancialOperandsV1
    risk_policy: PortfolioRiskPolicy
    authority: DemoRiskAuthority
    reconciliation_id: legacy.Digest

    @model_validator(mode="after")
    def scoped(self):
        if (
            self.scope.environment != "demo"
            or self.scope.settlement_currency != "USDT"
            or self.account.account_id != self.scope.account_id
            or self.account.settlement_currency != self.scope.settlement_currency
            or self.authority.environment != self.scope.environment
            or self.authority.stamp.environment != self.scope.environment
            or self.authority.stamp.account_id != self.scope.account_id
            or self.authority.stamp.observed_at > self.authority.stamp.received_at
            or self.account.history_end > self.financial.source_as_of_at
            or self.financial.measured_hwm_window_started_at
            != self.risk_policy.drawdown_window_started_at
            or self.financial.original_risk_policy_sha256
            != finite_risk_policy_sha256_v1(self.risk_policy)
        ):
            _deny()
        stamps = (
            self.account.balance_stamp,
            self.account.positions_stamp,
            self.account.history_stamp,
            self.account.reservations_stamp,
            self.authority.stamp,
        )
        if any(stamp.received_at > self.financial.evaluated_at for stamp in stamps):
            _deny()
        return self


def finite_risk_policy_sha256_v1(policy):
    """Bind the full original policy/window, not only the Settings subset."""
    if type(policy) is not PortfolioRiskPolicy:
        _deny()
    checked = PortfolioRiskPolicy.model_validate(
        policy.model_dump(mode="python", round_trip=True), strict=True
    )
    return hashlib.sha256(_canonical(_risk_policy_document(checked))).hexdigest()


def _finite_model_tree_v1(value, *, depth=0, budget=None):
    if budget is None:
        budget = [MAX_VALUES]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_DEPTH:
        _deny()
    kind = type(value)
    allowed = (
        ExactRatioV1,
        FiniteCurrentReferenceV1,
        FinitePortfolioSnapshotV1,
        FiniteFinancialOperandsV1,
        FiniteAccountLedgerClaimsV1,
        legacy.LedgerScope,
        EvidenceStamp,
        PositionExposure,
        PendingReservation,
        PortfolioRiskPolicy,
        DemoRiskAuthority,
    )
    if isinstance(value, BaseModel):
        if (
            kind not in allowed
            or set(value.__dict__) != set(kind.model_fields)
            or value.__pydantic_extra__
        ):
            _deny()
        for child in value.__dict__.values():
            _finite_model_tree_v1(child, depth=depth + 1, budget=budget)
        return
    if kind in (tuple, list):
        if len(value) > MAX_CONTAINER_ITEMS:
            _deny()
        for child in value:
            _finite_model_tree_v1(child, depth=depth + 1, budget=budget)
        return
    if kind is dict:
        if len(value) > MAX_OBJECT_FIELDS or any(type(key) is not str for key in value):
            _deny()
        for key, child in value.items():
            _finite_model_tree_v1(key, depth=depth + 1, budget=budget)
            _finite_model_tree_v1(child, depth=depth + 1, budget=budget)
        return
    if kind is Decimal:
        parts = value.as_tuple()
        if (
            not value.is_finite()
            or len(parts.digits) > 40
            or not -20 <= parts.exponent <= 40
        ):
            _deny()
        return
    if kind is datetime:
        _finite_utc(value)
        return
    if value is None or kind in (bool, int, str):
        _plain(value)
        return
    _deny()


def _checked_finite_claims_v1(value):
    if type(value) is not FiniteAccountLedgerClaimsV1:
        _deny()
    _finite_model_tree_v1(value)
    result = FiniteAccountLedgerClaimsV1.model_validate(
        value.model_dump(mode="python", round_trip=True, serialize_as_any=True),
        strict=True,
    )
    _scope(result)
    return result


def encode_finite_account_ledger_claims_v1(claims):
    """Encode finite DATA and explicit unknowns; never authenticate or issue."""
    try:
        checked = _checked_finite_claims_v1(claims)
        return _canonical(
            {
                "schema_version": SCHEMA_VERSION,
                "scope": _scope(checked),
                "claims": checked.model_dump(mode="json", round_trip=True),
                **dict.fromkeys(FALSE_FIELDS, False),
            }
        )
    except AccountLedgerClaimsV2CodecError:
        raise
    except (ValueError, TypeError, AttributeError, ArithmeticError, RecursionError):
        _deny()


def decode_finite_account_ledger_claims_v1(raw):
    """Saved finite DATA cannot be consumed by the legacy claims decoder."""
    try:
        if type(raw) is not bytes or not 1 <= len(raw) <= MAX_CLAIMS_BYTES:
            _deny()
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_int=_integer,
            parse_float=_forbidden_number,
            parse_constant=_forbidden_number,
        )
        if (
            type(document) is not dict
            or set(document) != FIELDS
            or document["schema_version"] != SCHEMA_VERSION
            or type(document["scope"]) is not list
            or len(document["scope"]) != 3
            or any(type(item) is not str for item in document["scope"])
            or any(document[key] is not False for key in FALSE_FIELDS)
            or type(document["claims"]) is not dict
            or document["claims"].get("payload_schema") != FINITE_PAYLOAD_SCHEMA_V1
            or _canonical(document) != raw
        ):
            _deny()
        data = FiniteAccountLedgerClaimsV1.model_validate_json(
            _canonical(document["claims"]), strict=True
        )
        checked = _checked_finite_claims_v1(data)
        if (
            document["scope"] != _scope(checked)
            or encode_finite_account_ledger_claims_v1(checked) != raw
        ):
            _deny()
        return checked
    except AccountLedgerClaimsV2CodecError:
        raise
    except (ValueError, TypeError, AttributeError, ArithmeticError, RecursionError):
        _deny()
