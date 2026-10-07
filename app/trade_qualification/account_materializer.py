"""Recorded Demo account mapping, not authenticated account completeness.

No IO, clock, credentials, settings or order authority. Supplied raw records and
supplemental ledger/metadata claims are replayed; hashes bind, not authenticate.
Amounts use exact fractions and conservative 1e-20 rounding only at DTO boundaries.
Unknown inventory is never replaced with zero or a fabricated observation time.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from functools import wraps
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_consistency, reservations
from app.trade_qualification.models import Price, QualificationModel
from app.trade_qualification.portfolio import (
    Amount,
    ContractRiskSpec,
    EvidenceStamp,
    ObservedSource,
    PendingReservation,
    PortfolioRiskSnapshot,
    PositionExposure,
    RealizedOutcome,
)

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")]
Currency = Annotated[str, Field(pattern=r"^[A-Z0-9]{1,20}$")]
Identifier = Annotated[str, Field(pattern=r"^[1-9][0-9]{0,39}$")]
MAX_BYTES = 8 * 1024 * 1024
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,20})?")
_FALSE_FIELDS = (
    "account_complete",
    "source_authenticity_verified",
    "execution_authority",
)


class AccountMaterializationError(ValueError):
    """Only static local failure codes, never private response contents."""


def _fail(code):
    raise AccountMaterializationError(code)


def _bounded(function):
    @wraps(function)
    def checked(*args, **kwargs):
        code = None
        try:
            return function(*args, **kwargs)
        except AccountMaterializationError as exc:
            code = (
                exc.args[0]
                if type(exc) is AccountMaterializationError
                else "materialization_invalid"
            )
        except Exception:  # noqa: BLE001 -- no raw parser/validation error leakage
            code = "materialization_invalid"
        raise AccountMaterializationError(code)

    return checked


class _Model(QualificationModel):
    model_config = ConfigDict(
        str_strip_whitespace=False, ser_json_bytes="hex", val_json_bytes="hex"
    )


class InstrumentEvidence(_Model):
    """A single instrument JSON row plus recorded (not authenticated) provenance."""

    raw_response: bytes = Field(min_length=1, max_length=262144)
    expected_sha256: Digest
    observed_at: datetime
    received_at: datetime

    _times = field_validator("observed_at", "received_at")(capture._utc)


class CorrelationEntry(_Model):
    instrument_id: Name
    group: Name


class ExposureCost(_Model):
    instrument_id: Name
    cost_per_base: Amount
    source_sha256: Digest
    observed_at: datetime
    received_at: datetime

    _times = field_validator("observed_at", "received_at")(capture._utc)


class LedgerOrderBinding(_Model):
    reservation_id: Digest
    order_id: Identifier
    client_order_id: Name


class LedgerEvidence(_Model):
    source: ObservedSource
    state: reservations.LedgerScopeState
    order_bindings: tuple[LedgerOrderBinding, ...] = Field(default=(), max_length=2048)


class OutcomeGroup(_Model):
    outcome_id: Name
    sequence: int = Field(ge=0, le=10**15)
    fill_ids: tuple[Identifier, ...] = Field(min_length=2, max_length=2048)
    funding_bill_ids: tuple[Identifier, ...] = Field(default=(), max_length=2048)


class HistoryEvidence(_Model):
    """Recorded local grouping/seed, never a claim of exchange ingestion coverage."""

    account_id: Identifier
    settlement_currency: Currency
    source: ObservedSource
    history_start: datetime
    history_end: datetime
    loss_streak_at_history_start: int = Field(ge=0, le=2048)
    groups: tuple[OutcomeGroup, ...] = Field(default=(), max_length=2048)

    _times = field_validator("history_start", "history_end")(capture._utc)


class PeakEvidence(_Model):
    """Finite raw balance samples; not continuous peak-window coverage."""

    account_id: Identifier
    settlement_currency: Currency
    window_started_at: datetime
    samples: tuple[InstrumentEvidence, ...] = Field(min_length=1, max_length=64)

    _times = field_validator("window_started_at")(capture._utc)


class AccountMaterializationInputs(_Model):
    account_id: Identifier
    settlement_currency: Currency
    correlation_version: Name
    instruments: tuple[InstrumentEvidence, ...] = Field(default=(), max_length=128)
    correlations: tuple[CorrelationEntry, ...] = Field(default=(), max_length=128)
    costs: tuple[ExposureCost, ...] = Field(default=(), max_length=128)
    ledger: LedgerEvidence | None = None
    history: HistoryEvidence | None = None
    peak: PeakEvidence | None = None
    environment: Literal["demo"] = "demo"


class ExposureProjection(_Model):
    stream: Name
    row_id: Name
    instrument_id: Name | None
    kind: Literal[
        "position",
        "opening_order",
        "protection",
        "reducing_order",
        "unsupported_algo",
        "local_hold",
        "zero_position",
    ]
    direction: Literal["long", "short"] | None = None
    contracts: Amount | None = None
    base_quantity: Amount | None = None
    notional: Amount | None = None
    margin: Amount | None = None
    risk_amount: Amount | None = None
    reference_price: Price | None = None
    stop_loss: Price | None = None
    protection_id: Name | None = None
    source_sha256: Digest
    reasons: tuple[Name, ...] = Field(default=(), max_length=64)


class AccountMaterializationResult(_Model):
    schema_version: Literal["ctcc.account_materialization.v1"] = (
        "ctcc.account_materialization.v1"
    )
    account_id: Identifier
    settlement_currency: Currency
    packet_sha256: Digest
    inputs_sha256: Digest
    instruments: tuple[ContractRiskSpec, ...] = Field(max_length=128)
    equity: Price | None
    available_margin: Amount | None
    positions: tuple[PositionExposure, ...] = Field(max_length=2048)
    pending_reservations: tuple[PendingReservation, ...] = Field(max_length=2048)
    loss_history: tuple[RealizedOutcome, ...] = Field(max_length=2048)
    projections: tuple[ExposureProjection, ...] = Field(max_length=8192)
    snapshot: PortfolioRiskSnapshot | None
    incomplete_reasons: tuple[Name, ...] = Field(min_length=1, max_length=256)
    state: Literal["recorded_mapping_incomplete_account"] = (
        "recorded_mapping_incomplete_account"
    )
    account_complete: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    execution_authority: Literal[False] = False
    evaluation_sha256: Digest

    @field_validator(*_FALSE_FIELDS, mode="before")
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            _fail("materialization_cannot_grant_authority")
        return value


@dataclass(frozen=True, slots=True)
class FrozenAccountMaterialization:
    payload: bytes
    sha256: str


_MODELS = {
    InstrumentEvidence,
    CorrelationEntry,
    ExposureCost,
    LedgerOrderBinding,
    LedgerEvidence,
    OutcomeGroup,
    HistoryEvidence,
    PeakEvidence,
    AccountMaterializationInputs,
    ExposureProjection,
    AccountMaterializationResult,
    ObservedSource,
    ContractRiskSpec,
    EvidenceStamp,
    PositionExposure,
    PendingReservation,
    RealizedOutcome,
    PortfolioRiskSnapshot,
    reservations.LedgerScope,
    reservations.LedgerScopeState,
    reservations.ReservationReceipt,
    reservations.RiskCoverage,
    reservations.ScenarioOperands,
}


def _plain(value, depth=0, budget=None):
    if budget is None:
        budget = [100000, MAX_BYTES]
    budget[0] -= 1
    if depth > 16 or budget[0] < 0:
        _fail("materialization_input_limit")
    kind = type(value)
    if any(kind is model for model in _MODELS):
        fields = object.__getattribute__(value, "__dict__")
        extra = object.__getattribute__(value, "__pydantic_extra__")
        private = object.__getattribute__(value, "__pydantic_private__")
        if (
            type(fields) is not dict
            or any(type(key) is not str for key in fields)
            or set(fields) != set(kind.model_fields)
            or extra is not None
            or private is not None
        ):
            _fail("materialization_fields_invalid")
        return {key: _plain(item, depth + 1, budget) for key, item in fields.items()}
    if kind is tuple:
        if len(value) > 8192:
            _fail("materialization_input_limit")
        return tuple(_plain(item, depth + 1, budget) for item in value)
    if kind is str or kind is bytes:
        budget[1] -= len(value)
        if len(value) > 262144 or budget[1] < 0:
            _fail("materialization_input_limit")
        return value
    if kind is Decimal:
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 80
            or abs(value.as_tuple().exponent) > 80
        ):
            _fail("materialization_scalar_invalid")
        return value
    if kind is datetime:
        return capture._utc(value)
    if kind is int:
        if abs(value) > 10**15:
            _fail("materialization_scalar_invalid")
        return value
    if kind is bool or value is None:
        return value
    _fail("materialization_scalar_invalid")


def _copy(value, expected):
    if type(value) is not expected:
        _fail("materialization_type_invalid")
    return expected.model_validate(_plain(value), strict=True)


def _wire(value):
    kind = type(value)
    if any(kind is model for model in _MODELS):
        return {key: _wire(item) for key, item in value.__dict__.items()}
    if kind is dict:
        return {key: _wire(item) for key, item in value.items()}
    if kind is tuple:
        return [_wire(item) for item in value]
    if kind is bytes:
        return value.hex()
    if kind is Decimal:
        text = format(value, "f")
        return (
            "0"
            if value.is_zero()
            else text.rstrip("0").rstrip(".")
            if "." in text
            else text
        )
    if kind is datetime:
        return capture._utc(value).isoformat()
    return value


def _canonical(value):
    raw = json.dumps(
        _wire(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(raw) > MAX_BYTES:
        _fail("materialization_bytes_limit")
    return raw


def _sha(value):
    return hashlib.sha256(value).hexdigest()


@_bounded
def copy_materialization_inputs(
    inputs: AccountMaterializationInputs,
) -> AccountMaterializationInputs:
    checked = _copy(inputs, AccountMaterializationInputs)
    _canonical(checked)
    for collection in (checked.correlations, checked.costs):
        if len({item.instrument_id for item in collection}) != len(collection):
            _fail("duplicate_materialization_input_identity")
    source_records = list(checked.instruments)
    if checked.peak is not None:
        source_records.extend(checked.peak.samples)
    for item in source_records:
        _raw(item)
        if item.observed_at > item.received_at:
            _fail("materialization_source_time_invalid")
    for item in checked.costs:
        if item.observed_at > item.received_at:
            _fail("materialization_source_time_invalid")
    for item in (checked.history, checked.peak):
        if item is not None:
            _scope(item.account_id, item.settlement_currency, checked)
    if (
        checked.history is not None
        and checked.history.source.observed_at > checked.history.source.received_at
    ):
        _fail("materialization_source_time_invalid")
    if checked.ledger is not None:
        evidence = checked.ledger
        _scope(
            evidence.state.scope.account_id,
            evidence.state.scope.settlement_currency,
            checked,
        )
        if evidence.source.observed_at > evidence.source.received_at:
            _fail("materialization_source_time_invalid")
        if evidence.source.source_sha256 != reservations.digest(evidence.state):
            _fail("local_ledger_source_pin_mismatch")
    return checked


@_bounded
def materialization_inputs_sha256(inputs: AccountMaterializationInputs) -> str:
    return _sha(_canonical(copy_materialization_inputs(inputs)))


def _fraction(raw, *, positive=False, nonnegative=False):
    if type(raw) is not str or _NUMBER.fullmatch(raw) is None:
        return None
    value = Fraction(Decimal(raw))
    if positive and value <= 0 or nonnegative and value < 0:
        return None
    return value


def _amount(value, *, round_up=True):
    if value is None:
        return None
    scaled = value * 10**20
    numerator, denominator = scaled.numerator, scaled.denominator
    integer = -(-numerator // denominator) if round_up else numerator // denominator
    sign = "-" if integer < 0 else ""
    digits = str(abs(integer)).rjust(21, "0")
    return Decimal(f"{sign}{digits[:-20]}.{digits[-20:]}")


def _raw(evidence):
    if _sha(evidence.raw_response) != evidence.expected_sha256:
        _fail("materialization_source_pin_mismatch")
    row, _ = capture._decode_json(evidence.raw_response, limit=262144, wire=True)
    if type(row) is not dict:
        _fail("materialization_source_shape_invalid")
    return row


def _time(raw):
    record = capture._time_record("time", raw, "source_update")
    return record.value


def _scope(account_id, currency, inputs):
    if account_id != inputs.account_id or currency != inputs.settlement_currency:
        _fail("materialization_scope_mismatch")


def _source_time(source, completed):
    return source.observed_at <= source.received_at <= completed


def _records(packet):
    return {
        stream: [
            (row, json.loads(row.canonical_json), observed)
            for observed in packet.observations
            if capture.stream_family(observed.request.stream) == stream
            for row in observed.rows
        ]
        for stream in capture.STREAMS
    }


def _balance(row, currency):
    # top-level totalEq/availEq are USD, never relabelled as a stablecoin.
    details = row.get("details")
    if (
        type(details) is not list
        or len(details) != 1
        or details[0].get("ccy") != currency
    ):
        return None, None, "single_currency_balance_required"
    item = details[0]
    equity = _fraction(item.get("eq"), positive=True)
    available = _fraction(item.get("availEq"), nonnegative=True)
    if equity is None or available is None or available > equity:
        return None, None, "settlement_equity_or_margin_missing"
    if any(_fraction(item.get(key)) != 0 for key in ("liab", "borrowFroz")):
        return equity, available, "balance_liability_scope_unsupported"
    return equity, available, None


def _instruments(inputs, completed, gaps):
    groups = {}
    for item in inputs.correlations:
        if item.instrument_id in groups:
            _fail("duplicate_correlation_identity")
        if item.group.casefold() in {"unknown", "unavailable", "unclassified"}:
            _fail("correlation_unknown")
        groups[item.instrument_id] = item.group
    mapped = {}
    seen = set()
    for evidence in inputs.instruments:
        row = _raw(evidence)
        name = row.get("instId")
        if (
            type(name) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", name) is None
        ):
            _fail("instrument_identity_invalid")
        if name in seen:
            _fail("duplicate_instrument_identity")
        seen.add(name)
        values = {
            key: _fraction(row.get(key), positive=True)
            for key in ("ctVal", "lotSz", "minSz", "maxLmtSz", "lever")
        }
        if (
            row.get("instType") != "SWAP"
            or row.get("ctType") != "linear"
            or row.get("state") != "live"
            or row.get("settleCcy") != inputs.settlement_currency
            # For linear derivatives ctVal is denominated in the base currency.
            # baseCcy/quoteCcy are documented SPOT/MARGIN fields and may be empty.
            or type(row.get("ctValCcy")) is not str
            or re.fullmatch(r"[A-Z0-9]{1,20}", row.get("ctValCcy", "")) is None
            or row.get("baseCcy") not in (None, "", row.get("ctValCcy"))
            or row.get("quoteCcy") not in (None, "", inputs.settlement_currency)
            or row.get("ctValCcy") == inputs.settlement_currency
            or _fraction(row.get("ctMult")) != 1
            or any(value is None for value in values.values())
            or values["lever"].denominator != 1
            or not 1 <= values["lever"] <= 125
            or values["minSz"] > values["maxLmtSz"]
            or name not in groups
            or not _source_time(evidence, completed)
        ):
            gaps.add("instrument_mapping_incomplete")
            continue
        mapped[name] = ContractRiskSpec(
            instrument_id=name,
            base_currency=row["ctValCcy"],
            settlement_currency=inputs.settlement_currency,
            contract_kind="linear_base",
            contract_value_currency=row["ctValCcy"],
            contract_value=_amount(values["ctVal"]),
            lot_size=_amount(values["lotSz"]),
            min_contracts=_amount(values["minSz"]),
            max_contracts=_amount(values["maxLmtSz"]),
            max_leverage=int(values["lever"]),
            correlation_group=groups[name],
            source_sha256=evidence.expected_sha256,
            observed_at=evidence.observed_at,
            received_at=evidence.received_at,
        )
    return mapped


def _direction(row, mode, quantity):
    if mode == "net_mode" and row.get("posSide") == "net":
        return "long" if quantity > 0 else "short"
    if (
        mode == "long_short_mode"
        and row.get("posSide") in {"long", "short"}
        and quantity > 0
    ):
        return row["posSide"]
    return None


def _stop(row, reference, direction):
    stop = _fraction(row.get("slTriggerPx"), positive=True)
    if (
        stop is None
        or row.get("slOrdPx") != "-1"
        or row.get("slTriggerPxType") != "mark"
        or row.get("closeFraction") not in (None, "")
        or row.get("amendPxOnTriggerType") not in (None, "", "0")
        or direction == "long"
        and stop >= reference
        or direction == "short"
        and stop <= reference
    ):
        return None
    if row.get("ordType") == "oco":
        target = _fraction(row.get("tpTriggerPx"), positive=True)
        if (
            target is None
            or row.get("tpOrdPx") != "-1"
            or row.get("tpTriggerPxType") != "mark"
            or direction == "long"
            and target <= reference
            or direction == "short"
            and target >= reference
        ):
            return None
    return stop


def _positions_and_orders(
    records, inputs, specs, completed, gaps, mode, *, all_product_scope=False
):
    costs = {}
    for item in inputs.costs:
        if item.instrument_id in costs:
            _fail("duplicate_cost_identity")
        if _source_time(item, completed):
            costs[item.instrument_id] = Fraction(item.cost_per_base)
        else:
            gaps.add("cost_source_time_invalid")
    all_algos = [
        item
        for stream in capture.STREAMS
        if stream.startswith("algo_")
        for item in records[stream]
    ]
    matched = {}
    # Only actual unique inventory geometry can classify a reducing algo as a
    # position's protection. Outstanding algos are NOT all opening reservations.
    for algo_record, algo, _ in all_algos:
        choices = []
        for position_record, position, _ in records["positions"]:
            quantity = _fraction(position.get("pos"))
            mark = _fraction(position.get("markPx"), positive=True)
            direction = _direction(position, mode, quantity) if quantity else None
            if (
                direction is None
                or mark is None
                or algo.get("instType") != "SWAP"
                or position.get("instType") != "SWAP"
                or type(algo.get("reduceOnly")) is not bool
                or not algo["reduceOnly"]
                or algo.get("state") != "live"
                or algo.get("ordType") not in {"conditional", "oco"}
                or algo.get("instId") != position.get("instId")
                or algo.get("posSide") != position.get("posSide")
                or algo.get("side") != ("sell" if direction == "long" else "buy")
                or algo.get("tdMode") != position.get("mgnMode")
                or _fraction(algo.get("sz"), positive=True) != abs(quantity)
                or algo.get("posId") not in (None, "", position_record.row_id)
            ):
                continue
            stop = _stop(algo, mark, direction)
            if stop is not None:
                choices.append((position_record.row_id, stop))
        if len(choices) == 1:
            position_id, stop = choices[0]
            matched.setdefault(position_id, []).append((algo_record.row_id, stop))
    projections, positions, pending, used_algos = [], [], [], set()
    for record, row, observed in records["positions"]:
        reasons = set()
        instrument_id = row.get("instId")
        quantity = _fraction(row.get("pos"))
        fields = {
            "stream": "positions",
            "row_id": record.row_id,
            "instrument_id": instrument_id,
            "kind": "position",
            "source_sha256": observed.receipt_sha256,
        }
        if all_product_scope and row.get("instType") != "SWAP":
            reason = "position_instrument_scope_unsupported"
            gaps.add(reason)
            projections.append(ExposureProjection(**fields, reasons=(reason,)))
            continue
        if quantity == 0:
            projections.append(
                ExposureProjection(
                    **(fields | {"kind": "zero_position", "contracts": Decimal(0)})
                )
            )
            continue
        direction = _direction(row, mode, quantity) if quantity is not None else None
        spec = specs.get(instrument_id)
        mark = _fraction(row.get("markPx"), positive=True)
        margin = _fraction(row.get("margin"), nonnegative=True)
        if direction is None:
            reasons.add("position_direction_or_quantity_invalid")
        if spec is None:
            reasons.add("instrument_mapping_missing")
        if row.get("instType") != "SWAP":
            reasons.add("position_instrument_scope_unsupported")
        if row.get("ccy") != inputs.settlement_currency or row.get("mgnMode") not in {
            "cross",
            "isolated",
        }:
            reasons.add("position_margin_scope_unsupported")
        if mark is None:
            reasons.add("position_mark_missing")
        if margin is None:
            reasons.add("position_margin_missing")
        if len(matched.get(record.row_id, ())) != 1:
            reasons.add("unique_full_position_protection_missing")
            stop = protection_id = None
        else:
            protection_id, stop = matched[record.row_id][0]
            used_algos.add(protection_id)
        cost = costs.get(instrument_id)
        if cost is None:
            reasons.add("position_cost_missing")
        contracts = abs(quantity) if quantity is not None else None
        base = (
            contracts * Fraction(spec.contract_value)
            if contracts is not None and spec
            else None
        )
        notional = base * mark if base is not None and mark is not None else None
        risk = (
            base * (abs(mark - stop) + cost)
            if base is not None
            and mark is not None
            and stop is not None
            and cost is not None
            else None
        )
        if (
            spec
            and contracts is not None
            and (contracts / Fraction(spec.lot_size)).denominator != 1
        ):
            reasons.add("position_quantity_off_lot")
        if _time(row.get("uTime")) is None:
            reasons.add("position_source_time_missing")
        projection = ExposureProjection(
            **fields,
            direction=direction,
            contracts=_amount(contracts),
            base_quantity=_amount(base),
            notional=_amount(notional),
            margin=_amount(margin),
            risk_amount=_amount(risk),
            reference_price=_amount(mark),
            stop_loss=_amount(stop),
            protection_id=protection_id,
            reasons=tuple(sorted(reasons)),
        )
        projections.append(projection)
        gaps.update(reasons)
        if not reasons:
            positions.append(
                PositionExposure(
                    position_id=record.row_id,
                    instrument_id=instrument_id,
                    direction=direction,
                    settlement_currency=inputs.settlement_currency,
                    notional=projection.notional,
                    margin=projection.margin,
                    risk_amount=projection.risk_amount,
                    correlation_group=spec.correlation_group,
                )
            )
    for record, row, observed in records["orders_pending"]:
        reasons = set()
        name = row.get("instId")
        fields = {
            "stream": "orders_pending",
            "row_id": record.row_id,
            "instrument_id": name,
            "kind": "opening_order",
            "source_sha256": observed.receipt_sha256,
        }
        if all_product_scope and row.get("instType") != "SWAP":
            reason = "pending_order_scope_unsupported"
            gaps.add(reason)
            projections.append(ExposureProjection(**fields, reasons=(reason,)))
            continue
        size = _fraction(row.get("sz"), positive=True)
        filled = _fraction(row.get("accFillSz"), nonnegative=True)
        remaining = (
            size - filled
            if size is not None and filled is not None and 0 <= filled <= size
            else None
        )
        if row.get("reduceOnly") is True:
            reducing_reasons = set()
            if (
                row.get("instType") != "SWAP"
                or row.get("tdMode") not in {"cross", "isolated"}
                or mode == "net_mode"
                and row.get("posSide") != "net"
                or mode == "long_short_mode"
                and (
                    row.get("posSide") not in {"long", "short"}
                    or row.get("side")
                    != ("sell" if row.get("posSide") == "long" else "buy")
                )
            ):
                reducing_reasons.add("reducing_order_scope_unsupported")
            if remaining is None or remaining <= 0:
                reducing_reasons.add("reducing_remaining_quantity_invalid")
            if _time(row.get("uTime")) is None:
                reducing_reasons.add("reducing_source_time_missing")
            gaps.update(reducing_reasons)
            projections.append(
                ExposureProjection(
                    **(fields | {"kind": "reducing_order"}),
                    contracts=_amount(remaining),
                    reasons=tuple(sorted(reducing_reasons)),
                )
            )
            continue
        side = row.get("side")
        direction = "long" if side == "buy" else "short" if side == "sell" else None
        if (
            type(row.get("reduceOnly")) is not bool
            or row.get("instType") != "SWAP"
            or row.get("ordType") != "limit"
            or row.get("tdMode") not in {"cross", "isolated"}
            or mode == "net_mode"
            and row.get("posSide") != "net"
            or mode == "long_short_mode"
            and row.get("posSide") != direction
        ):
            reasons.add("pending_order_scope_unsupported")
        spec, cost = specs.get(name), costs.get(name)
        price = _fraction(row.get("px"), positive=True)
        leverage = _fraction(row.get("lever"), positive=True)
        if spec is None:
            reasons.add("instrument_mapping_missing")
        if cost is None:
            reasons.add("pending_cost_missing")
        if remaining is None or remaining <= 0:
            reasons.add("pending_remaining_quantity_invalid")
        if price is None:
            reasons.add("pending_limit_price_missing")
        if (
            leverage is None
            or leverage.denominator != 1
            or not 1 <= leverage <= (spec.max_leverage if spec else 125)
        ):
            reasons.add("pending_leverage_missing_or_unsupported")
            leverage = None
        children = row.get("attachAlgoOrds")
        stop = None
        if (
            type(children) is list
            and len(children) == 1
            and type(children[0]) is dict
            and price is not None
            and direction
        ):
            stop = _stop(children[0], price, direction)
        if stop is None:
            reasons.add("pending_structural_stop_missing")
        if _time(row.get("uTime")) is None:
            reasons.add("pending_source_time_missing")
        base = (
            remaining * Fraction(spec.contract_value)
            if remaining is not None and remaining > 0 and spec
            else None
        )
        notional = base * price if base is not None and price is not None else None
        margin = notional / leverage if notional is not None and leverage else None
        risk = (
            base * (abs(price - stop) + cost)
            if base is not None
            and price is not None
            and stop is not None
            and cost is not None
            else None
        )
        if (
            spec
            and remaining is not None
            and (remaining / Fraction(spec.lot_size)).denominator != 1
        ):
            reasons.add("pending_quantity_off_lot")
        projection = ExposureProjection(
            **fields,
            direction=direction,
            contracts=_amount(remaining),
            base_quantity=_amount(base),
            notional=_amount(notional),
            margin=_amount(margin),
            risk_amount=_amount(risk),
            reference_price=_amount(price),
            stop_loss=_amount(stop),
            reasons=tuple(sorted(reasons)),
        )
        projections.append(projection)
        gaps.update(reasons)
        if not reasons:
            pending.append(
                PendingReservation(
                    reservation_id="order:" + record.row_id,
                    instrument_id=name,
                    direction=direction,
                    settlement_currency=inputs.settlement_currency,
                    notional=projection.notional,
                    margin=projection.margin,
                    risk_amount=projection.risk_amount,
                    correlation_group=spec.correlation_group,
                )
            )
    for record, row, observed in all_algos:
        if record.row_id in used_algos:
            kind, reasons = "protection", ()
        else:
            kind, reasons = "unsupported_algo", ("outstanding_algo_unclassified",)
            gaps.update(reasons)
        projections.append(
            ExposureProjection(
                stream=observed.request.stream,
                row_id=record.row_id,
                instrument_id=row.get("instId"),
                kind=kind,
                source_sha256=observed.receipt_sha256,
                reasons=reasons,
            )
        )
    return positions, pending, projections


def _local_holds(inputs, specs, records, pending, projections, completed, gaps):
    evidence = inputs.ledger
    if evidence is None:
        gaps.add("local_ledger_missing")
        return None
    state = evidence.state
    _scope(state.scope.account_id, state.scope.settlement_currency, inputs)
    if not _source_time(evidence.source, completed):
        gaps.add("local_ledger_source_time_invalid")
        return None
    if evidence.source.source_sha256 != reservations.digest(state):
        _fail("local_ledger_source_pin_mismatch")
    if state.account_revision < 1 or state.ledger_revision < 1:
        gaps.add("local_ledger_revision_missing")
        return None
    bindings, order_ids = {}, set()
    for item in evidence.order_bindings:
        if item.reservation_id in bindings or item.order_id in order_ids:
            _fail("local_order_binding_duplicate")
        bindings[item.reservation_id] = item
        order_ids.add(item.order_id)
    rows = {record.row_id: row for record, row, _ in records["orders_pending"]}
    active_ids = set()
    for receipt in state.active:
        if receipt.reservation_id in active_ids:
            _fail("local_reservation_duplicate")
        active_ids.add(receipt.reservation_id)
        _scope(receipt.scope.account_id, receipt.scope.settlement_currency, inputs)
        spec = specs.get(receipt.instrument_id)
        if (
            receipt.state == "reconciled_flat"
            or receipt.updated_at > evidence.source.observed_at
            or receipt.ledger_revision > state.ledger_revision
            or receipt.account_revision > state.account_revision
            or spec is None
            or receipt.correlation_group != spec.correlation_group
            or any(
                operand.contract_value != spec.contract_value
                for operand in (receipt.coverage.candidate, receipt.coverage.execution)
            )
        ):
            gaps.add("local_reservation_mapping_incomplete")
            continue
        hold = PendingReservation(
            reservation_id="local:" + receipt.reservation_id,
            instrument_id=receipt.instrument_id,
            direction=receipt.direction,
            settlement_currency=inputs.settlement_currency,
            notional=receipt.coverage.notional_amount,
            margin=receipt.coverage.margin_amount,
            risk_amount=receipt.coverage.risk_amount,
            correlation_group=receipt.correlation_group,
        )
        binding = bindings.get(receipt.reservation_id)
        if binding is not None:
            row = rows.get(binding.order_id)
            existing = next(
                (
                    item
                    for item in pending
                    if item.reservation_id == "order:" + binding.order_id
                ),
                None,
            )
            if (
                row is None
                or row.get("clOrdId") != binding.client_order_id
                or existing is None
                or existing.instrument_id != hold.instrument_id
                or existing.direction != hold.direction
                or existing.correlation_group != hold.correlation_group
            ):
                gaps.add("local_exchange_binding_unresolved")
            else:
                # Explicit matching IDs deduplicate recorded holds conservatively;
                # do not subtract filled quantity from a still-uncertain local hold.
                pending.remove(existing)
                hold = PendingReservation(
                    **(
                        hold.model_dump()
                        | {
                            "notional": max(hold.notional, existing.notional),
                            "margin": max(hold.margin, existing.margin),
                            "risk_amount": max(hold.risk_amount, existing.risk_amount),
                        }
                    )
                )
                if _fraction(row.get("accFillSz")) != 0:
                    gaps.add("partial_fill_local_union_conservative")
        # Deadline/TTL is deliberately never a release condition.
        pending.append(hold)
        projections.append(
            ExposureProjection(
                stream="local_ledger",
                row_id=receipt.reservation_id,
                instrument_id=receipt.instrument_id,
                kind="local_hold",
                direction=receipt.direction,
                notional=hold.notional,
                margin=hold.margin,
                risk_amount=hold.risk_amount,
                source_sha256=evidence.source.source_sha256,
            )
        )
    if set(bindings) - active_ids:
        _fail("local_order_binding_dangling")
    return evidence.source


def _history_union(records, streams, gaps):
    """Keep raw receipts; coalesce only identical rows by endpoint identity."""
    combined = {}
    canonical = {}
    for stream in streams:
        for record, row, _ in records[stream]:
            if (
                record.row_id in canonical
                and canonical[record.row_id] != record.canonical_json
            ):
                gaps.add("history_overlapping_source_conflict")
                return None
            canonical[record.row_id] = record.canonical_json
            combined[record.row_id] = row
    return combined


def _account_metadata(records, packet, inputs, gaps):
    """Check captured specifications and both pinned leverage query inventories."""
    captured = {record.row_id: row for record, row, _ in records["account_instruments"]}
    requested = set(packet.plan.leverage_instrument_ids)
    exposed = {
        row.get("instId")
        for stream in (
            "positions",
            "orders_pending",
            *(name for name in capture.STREAMS if name.startswith("algo_")),
        )
        for _, row, _ in records[stream]
    }
    if not requested <= set(captured) or not exposed <= requested:
        gaps.add("account_metadata_instrument_coverage_incomplete")
    for evidence in inputs.instruments:
        supplied = _raw(evidence)
        current = captured.get(supplied.get("instId"))
        if current is None:
            gaps.add("account_instrument_source_missing")
            continue
        if any(
            supplied.get(key) != current.get(key)
            for key in ("instType", "ctType", "settleCcy", "ctValCcy", "state")
        ) or any(
            _fraction(supplied.get(key)) != _fraction(current.get(key))
            for key in ("ctVal", "ctMult", "lotSz", "minSz", "maxLmtSz", "lever")
        ):
            gaps.add("account_instrument_source_conflict")
    mode = records["config_before"][0][1]["posMode"]
    sides = {"net"} if mode == "net_mode" else {"long", "short"}
    expected = {(name, side) for name in requested for side in sides}
    for stream in ("leverage_cross", "leverage_isolated"):
        rows = records[stream]
        actual = {(row.get("instId"), row.get("posSide")) for _, row, _ in rows}
        if actual != expected or any(
            _fraction(row.get("lever"), positive=True) is None for _, row, _ in rows
        ):
            gaps.add("account_leverage_coverage_incomplete")


def _history(inputs, records, specs, packet, gaps):
    fills = _history_union(records, ("fills_recent", "fills_history"), gaps)
    bills = _history_union(records, ("bills_recent", "bills_archive"), gaps)
    orders = _history_union(
        records, ("orders_history_recent", "orders_history_archive"), gaps
    )
    if fills is None or bills is None or orders is None:
        return (), None
    if any(
        row.get("instType") != "SWAP" for row in (*fills.values(), *orders.values())
    ):
        gaps.add("history_product_mapping_unsupported")
        return (), None
    evidence = inputs.history
    if evidence is None:
        gaps.add("history_grouping_and_seed_missing")
        return (), None
    _scope(evidence.account_id, evidence.settlement_currency, inputs)
    if (
        not packet.plan.history_start
        <= evidence.history_start
        <= evidence.history_end
        <= packet.plan.history_end
        or evidence.history_end != evidence.source.observed_at
        or not _source_time(evidence.source, packet.completed_at)
    ):
        gaps.add("history_recorded_window_invalid")
        return (), None
    if (
        evidence.history_start != packet.plan.history_start
        or evidence.history_end != packet.plan.history_end
    ):
        # A proper subset cannot seed the packet's requested rolling loss
        # window, even if every row inside that subset maps successfully.
        gaps.add("history_recorded_window_incomplete")
        return (), None
    used_fills, used_funding, outcomes = set(), set(), []
    groups = sorted(evidence.groups, key=lambda item: item.sequence)
    if len({group.outcome_id for group in groups}) != len(groups):
        _fail("outcome_identity_duplicate")
    for index, group in enumerate(groups):
        if index and group.sequence != groups[index - 1].sequence + 1:
            _fail("outcome_sequence_gap")
        if (
            len(set(group.fill_ids)) != len(group.fill_ids)
            or used_fills.intersection(group.fill_ids)
            or len(set(group.funding_bill_ids)) != len(group.funding_bill_ids)
            or used_funding.intersection(group.funding_bill_ids)
        ):
            _fail("outcome_source_reused")
        used_fills.update(group.fill_ids)
        used_funding.update(group.funding_bill_ids)
        if any(value not in fills for value in group.fill_ids) or any(
            value not in bills for value in group.funding_bill_ids
        ):
            gaps.add("outcome_source_missing")
            continue
        selected = [fills[value] for value in group.fill_ids]
        if any(_time(row.get("fillTime")) is None for row in selected):
            gaps.add("outcome_fill_time_missing")
            continue
        selected.sort(key=lambda row: _time(row["fillTime"]))
        name = selected[0].get("instId")
        direction = "long" if selected[0].get("side") == "buy" else "short"
        opening_side = "buy" if direction == "long" else "sell"
        spec = specs.get(name)
        inventory, net = Fraction(0), Fraction(0)
        invalid = spec is None
        previous_time = None
        for row in selected:
            time = _time(row["fillTime"])
            quantity = _fraction(row.get("fillSz"), positive=True)
            pnl = _fraction(row.get("fillPnl"))
            fee = _fraction(row.get("fee"))
            is_open = row.get("side") == opening_side
            if (
                row.get("instId") != name
                or row.get("feeCcy") != inputs.settlement_currency
                or row.get("posSide") not in {"net", direction}
                or not evidence.history_start <= time <= evidence.history_end
                or previous_time is not None
                and time <= previous_time
                or quantity is None
                or pnl is None
                or fee is None
                or is_open
                and pnl != 0
            ):
                invalid = True
                break
            inventory += quantity if is_open else -quantity
            if inventory < 0 or inventory == 0 and row is not selected[-1]:
                invalid = True
                break
            net += pnl + fee
            previous_time = time
        closed_at = _time(selected[-1]["fillTime"])
        if group.funding_bill_ids:
            # A bill is evidence of a cash movement, not the accrual interval or
            # its attribution to this holding. Preserve every raw bill receipt,
            # but this recorded grouping has no independent accrual provenance.
            gaps.add("funding_accrual_provenance_missing")
            invalid = True
        if invalid or inventory != 0:
            gaps.add("closed_outcome_mapping_incomplete")
            continue
        if outcomes and closed_at < outcomes[-1].closed_at:
            _fail("outcome_sequence_time_reversed")
        outcomes.append(
            RealizedOutcome(
                outcome_id=group.outcome_id,
                sequence=group.sequence,
                instrument_id=name,
                closed_at=closed_at,
                realized_pnl=_amount(net, round_up=False),
            )
        )
    if used_fills != set(fills):
        gaps.add("history_fill_inventory_unmapped")
    if any(
        key not in used_funding and not (key in used_fills and row.get("type") == "2")
        for key, row in bills.items()
    ):
        gaps.add("history_cashflow_inventory_unmapped")
    if len(outcomes) != len(groups):
        return tuple(outcomes), None
    return tuple(outcomes), evidence


def _peak(inputs, current_equity, packet, gaps):
    evidence = inputs.peak
    if evidence is None:
        gaps.add("peak_samples_missing")
        return None
    _scope(evidence.account_id, evidence.settlement_currency, inputs)
    points = []
    for sample in evidence.samples:
        row = _raw(sample)
        if row.get("uid") not in (None, inputs.account_id):
            _fail("peak_account_identity_mismatch")
        value, _, reason = _balance(row, inputs.settlement_currency)
        top_time = _time(row.get("uTime"))
        currency_time = (
            _time(row["details"][0].get("uTime"))
            if len(row.get("details", ())) == 1
            else None
        )
        time = (
            min(top_time, currency_time)
            if top_time is not None and currency_time is not None
            else None
        )
        if (
            reason
            or time is None
            or time != sample.observed_at
            or not evidence.window_started_at <= top_time <= sample.received_at
            or not evidence.window_started_at <= currency_time <= sample.received_at
            or not _source_time(sample, packet.completed_at)
        ):
            gaps.add("peak_sample_mapping_incomplete")
            continue
        points.append((value, time))
    if len(points) != len(evidence.samples) or len({time for _, time in points}) != len(
        points
    ):
        gaps.add("peak_sample_mapping_incomplete")
        return None
    peak, observed = max(
        sorted(points, key=lambda item: item[1]), key=lambda item: item[0]
    )
    if current_equity is None or peak < current_equity:
        gaps.add("sampled_peak_below_current_equity")
        return None
    gaps.add("continuous_peak_window_unverified")
    return _amount(peak, round_up=False), observed, evidence.window_started_at


def _stamp(inputs, source_sha256, observed_at, received_at):
    return EvidenceStamp(
        environment="demo",
        account_id=inputs.account_id,
        source_sha256=source_sha256,
        observed_at=observed_at,
        received_at=received_at,
        complete=False,
    )


@_bounded
def materialize_demo_portfolio_snapshot(
    packet: capture.DemoAccountPacket,
    *,
    expected_plan_sha256: str,
    expected_packet_sha256: str,
    inputs: AccountMaterializationInputs,
    expected_inputs_sha256: str | None = None,
) -> AccountMaterializationResult:
    inputs = copy_materialization_inputs(inputs)
    inputs_sha = materialization_inputs_sha256(inputs)
    if expected_inputs_sha256 is not None and (
        type(expected_inputs_sha256) is not str or expected_inputs_sha256 != inputs_sha
    ):
        _fail("external_materialization_inputs_pin_mismatch")
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=expected_plan_sha256
    )
    packet = capture.verify_demo_account_packet(
        frozen.payload,
        expected_sha256=expected_packet_sha256,
        expected_plan_sha256=expected_plan_sha256,
    )
    _scope(packet.plan.expected_uid, packet.plan.settlement_currency, inputs)
    records = _records(packet)
    gaps = set(packet.incomplete_reasons)
    metadata_gaps = set()
    _account_metadata(records, packet, inputs, metadata_gaps)
    gaps.update(metadata_gaps)
    consistency = account_consistency._reconcile_verified_packet(
        packet, expected_packet_sha256
    )
    gaps.update(consistency.blocking_reasons)
    specs = _instruments(inputs, packet.completed_at, gaps)
    config = records["config_before"][0][1]
    if config.get("acctLv") != "2":
        gaps.add("account_mode_mapping_unsupported")
    _, balance, balance_receipt = records["balance"][0]
    equity, available, balance_reason = _balance(balance, inputs.settlement_currency)
    if balance_reason:
        gaps.add(balance_reason)
    balance_time = _time(balance.get("uTime"))
    detail_time = (
        _time(balance["details"][0].get("uTime"))
        if len(balance.get("details", ())) == 1
        else None
    )
    if balance_time is None or detail_time is None:
        gaps.add("balance_source_time_missing")
        balance_stamp = None
    else:
        balance_stamp = _stamp(
            inputs,
            balance_receipt.receipt_sha256,
            min(balance_time, detail_time),
            balance_receipt.headers_received_at,
        )
    positions, pending, projections = _positions_and_orders(
        records,
        inputs,
        specs,
        packet.completed_at,
        gaps,
        config["posMode"],
        all_product_scope=type(packet.plan) is capture.AllProductDemoAccountCapturePlan,
    )
    _, anchor, anchor_receipt = records["account_position_risk"][0]
    anchor_time = _time(anchor.get("ts"))
    anchor_positions = {
        row.get("posId"): (row.get("instId"), _fraction(row.get("pos")))
        for row in anchor.get("posData", ())
    }
    actual_positions = {
        record.row_id: (row.get("instId"), _fraction(row.get("pos")))
        for record, row, _ in records["positions"]
    }
    if anchor_positions != actual_positions or anchor_time is None:
        gaps.add("position_anchor_inconsistent")
        positions_stamp = None
    else:
        times = [_time(row.get("uTime")) for _, row, _ in records["positions"]]
        positions_stamp = (
            None
            if any(time is None for time in times)
            else _stamp(
                inputs,
                _sha(
                    _canonical(
                        tuple(
                            observed.receipt_sha256
                            for observed in packet.observations
                            if observed.request.stream
                            in {"account_position_risk", "positions"}
                        )
                    )
                ),
                min((anchor_time, *times)),
                max(
                    anchor_receipt.headers_received_at,
                    *(
                        observed.headers_received_at
                        for observed in packet.observations
                        if observed.request.stream == "positions"
                    ),
                ),
            )
        )
    ledger_source = _local_holds(
        inputs, specs, records, pending, projections, packet.completed_at, gaps
    )
    outcomes, history = _history(inputs, records, specs, packet, gaps)
    peak = _peak(inputs, equity, packet, gaps)
    snapshot = None
    mapped_positions = sum(
        item.kind == "position" and not item.reasons for item in projections
    )
    if (
        # A well-shaped projection is still diagnostic when its packet or any
        # mapping source reports incomplete coverage. Do not issue the formal
        # portfolio DTO until every recorded gap has been resolved.
        not packet.incomplete_reasons
        and not gaps
        and config.get("acctLv") == "2"
        and not metadata_gaps
        and not consistency.blocking_reasons
        and balance_reason is None
        and equity is not None
        and available is not None
        and balance_stamp is not None
        and positions_stamp is not None
        and ledger_source is not None
        and history is not None
        and peak is not None
        and mapped_positions == sum(item.kind == "position" for item in projections)
        and not any(item.reasons for item in projections)
        and not any(
            reason in gaps
            for reason in (
                "history_fill_inventory_unmapped",
                "history_cashflow_inventory_unmapped",
                "local_reservation_mapping_incomplete",
                "local_exchange_binding_unresolved",
            )
        )
        and peak[1] <= balance_stamp.observed_at
    ):
        snapshot = PortfolioRiskSnapshot(
            account_id=inputs.account_id,
            settlement_currency=inputs.settlement_currency,
            balance_stamp=balance_stamp,
            positions_stamp=positions_stamp,
            reservations_stamp=_stamp(
                inputs,
                ledger_source.source_sha256,
                ledger_source.observed_at,
                ledger_source.received_at,
            ),
            history_stamp=_stamp(
                inputs,
                history.source.source_sha256,
                history.history_end,
                history.source.received_at,
            ),
            equity=_amount(equity, round_up=False),
            available_margin=_amount(available, round_up=False),
            peak_equity=peak[0],
            peak_observed_at=peak[1],
            peak_window_started_at=peak[2],
            history_start=history.history_start,
            history_end=history.history_end,
            loss_streak_at_history_start=history.loss_streak_at_history_start,
            positions=tuple(sorted(positions, key=lambda item: item.position_id)),
            pending_reservations=tuple(
                sorted(pending, key=lambda item: item.reservation_id)
            ),
            loss_history=outcomes,
            position_count=len(positions),
            pending_reservation_count=len(pending),
        )
    fields = {
        "account_id": inputs.account_id,
        "settlement_currency": inputs.settlement_currency,
        "packet_sha256": expected_packet_sha256,
        "inputs_sha256": inputs_sha,
        "instruments": tuple(specs[key] for key in sorted(specs)),
        "equity": _amount(equity, round_up=False),
        "available_margin": _amount(available, round_up=False),
        "positions": tuple(sorted(positions, key=lambda item: item.position_id)),
        "pending_reservations": tuple(
            sorted(pending, key=lambda item: item.reservation_id)
        ),
        "loss_history": outcomes,
        "projections": tuple(
            sorted(projections, key=lambda item: (item.stream, item.row_id))
        ),
        "snapshot": snapshot,
        "incomplete_reasons": tuple(sorted(gaps)),
    }
    result = AccountMaterializationResult(**fields, evaluation_sha256="0" * 64)
    return AccountMaterializationResult(
        **(result.__dict__ | {"evaluation_sha256": _result_sha(result)})
    )


def _result_sha(result):
    return _sha(
        _canonical(
            {
                key: value
                for key, value in result.__dict__.items()
                if key != "evaluation_sha256"
            }
        )
    )


@_bounded
def copy_account_materialization_result(
    result: AccountMaterializationResult,
) -> AccountMaterializationResult:
    checked = _copy(result, AccountMaterializationResult)
    if checked.evaluation_sha256 != _result_sha(checked):
        _fail("materialization_result_pin_mismatch")
    if checked.snapshot is not None and any(
        getattr(checked.snapshot, name).complete is not False
        for name in (
            "balance_stamp",
            "positions_stamp",
            "history_stamp",
            "reservations_stamp",
        )
    ):
        _fail("materialization_cannot_grant_authority")
    return checked


@_bounded
def get_materialized_instrument(
    result: AccountMaterializationResult, instrument_id: str
) -> ContractRiskSpec | None:
    checked = copy_account_materialization_result(result)
    if type(instrument_id) is not str or not 1 <= len(instrument_id) <= 96:
        _fail("instrument_identity_invalid")
    return next(
        (item for item in checked.instruments if item.instrument_id == instrument_id),
        None,
    )


@_bounded
def verify_account_materialization(
    result: AccountMaterializationResult,
    *,
    packet: capture.DemoAccountPacket,
    expected_plan_sha256: str,
    expected_packet_sha256: str,
    inputs: AccountMaterializationInputs,
    expected_inputs_sha256: str | None = None,
) -> AccountMaterializationResult:
    checked = copy_account_materialization_result(result)
    replay = materialize_demo_portfolio_snapshot(
        packet,
        expected_plan_sha256=expected_plan_sha256,
        expected_packet_sha256=expected_packet_sha256,
        inputs=inputs,
        expected_inputs_sha256=expected_inputs_sha256,
    )
    if _canonical(checked) != _canonical(replay):
        _fail("materialization_replay_mismatch")
    return replay


@_bounded
def freeze_account_materialization(
    result: AccountMaterializationResult, **replay_inputs
) -> FrozenAccountMaterialization:
    checked = verify_account_materialization(result, **replay_inputs)
    payload = _canonical(checked)
    return FrozenAccountMaterialization(payload=payload, sha256=_sha(payload))


def _json_tree(value, depth=0, budget=None):
    if budget is None:
        budget = [100000]
    budget[0] -= 1
    if depth > 16 or budget[0] < 0:
        _fail("materialization_json_limit")
    kind = type(value)
    if kind is dict:
        if len(value) > 128 or any(
            type(key) is not str or len(key) > 96 for key in value
        ):
            _fail("materialization_json_limit")
        for item in value.values():
            _json_tree(item, depth + 1, budget)
    elif kind is list:
        if len(value) > 8192:
            _fail("materialization_json_limit")
        for item in value:
            _json_tree(item, depth + 1, budget)
    elif kind is str:
        if len(value) > 262144:
            _fail("materialization_json_limit")
    elif kind is int:
        if abs(value) > 10**15:
            _fail("materialization_json_limit")
    elif kind is not bool and value is not None:
        _fail("materialization_json_scalar_invalid")


@_bounded
def verify_frozen_account_materialization(
    payload: bytes, *, expected_sha256: str, **replay_inputs
) -> AccountMaterializationResult:
    if (
        type(payload) is not bytes
        or not 1 <= len(payload) <= MAX_BYTES
        or type(expected_sha256) is not str
        or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None
        or _sha(payload) != expected_sha256
    ):
        _fail("external_materialization_pin_mismatch")
    decoded = json.loads(
        payload,
        object_pairs_hook=capture._pairs,
        parse_float=capture._invalid_json_number,
        parse_constant=capture._invalid_json_number,
    )
    _json_tree(decoded)
    if _canonical(decoded) != payload:
        _fail("materialization_noncanonical")
    result = AccountMaterializationResult.model_validate_json(payload, strict=True)
    return verify_account_materialization(result, **replay_inputs)
