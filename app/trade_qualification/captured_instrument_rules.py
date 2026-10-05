"""Exact captured USDT-SWAP price/quantity rules, without source authority."""

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal

_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,20})?")
_CURRENCY = re.compile(r"[A-Z0-9]{1,20}")
_INSTRUMENT = re.compile(r"[A-Z0-9]{1,20}-USDT-SWAP")
POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.captured_instrument_rules.v1",
        "environment": "demo",
        "product": "SWAP",
        "contract_type": "linear",
        "settlement_currency": "USDT",
        "state": "live",
        "required_captured_ctMult": "1",
        "decimal_syntax": "positive_plain_decimal_maximum_20_integer_20_fraction_digits_no_exponent",
        "price_unit": "USDT_per_base_currency",
        "quantity_unit": "contracts",
        "contract_value_unit": "base_currency_per_contract",
        "min_size_policy": "independent_lower_bound_no_rounding_or_lot_multiple_assumption",
        "instrument_timestamp_semantics": "undocumented_optional_ts_is_retained_not_exchange_asof",
        "source_authenticity_verified": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class CapturedInstrumentRulesError(ValueError):
    """Static failure codes only; no account or source data in exception text."""


@dataclass(frozen=True, slots=True, repr=False)
class CapturedInstrumentRules:
    instrument_id: str
    base_currency: str
    settlement_currency: str
    tick_size: Decimal
    lot_size: Decimal
    min_contracts: Decimal
    contract_value: Decimal
    contract_multiplier: Decimal
    raw_row_json: bytes
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def source_authenticity_verified(self):
        return False

    @property
    def current_owned_session_authority(self):
        return False

    @property
    def execution_authority(self):
        return False


def _deny(code):
    raise CapturedInstrumentRulesError(code)


def _positive_decimal(raw):
    # Decimal construction is exact and context-independent; no conversion from
    # float, truthy bool, scientific notation, rounded arithmetic or fallback.
    if type(raw) is not str or len(raw) > 41 or _DECIMAL.fullmatch(raw) is None:
        _deny("captured_instrument_rule_decimal_invalid")
    value = Decimal(raw)
    if value <= 0:
        _deny("captured_instrument_rule_decimal_invalid")
    return value


def _fraction(value):
    exact = Fraction(value)
    return {"numerator": str(exact.numerator), "denominator": str(exact.denominator)}


def derive_captured_instrument_rules(
    packet,
    *,
    instrument_id,
    expected_plan_sha256,
    expected_packet_sha256,
    expected_policy_sha256=POLICY_SHA256,
):
    """Rebuild exact rules from a complete packet; caller specifications are absent.

    A future owner must call this on its actual acquired packet and keep the
    acquisition/session/UID binding. A copied diagnostic never becomes that owner.
    """
    try:
        return _derive(
            packet,
            instrument_id,
            expected_plan_sha256,
            expected_packet_sha256,
            expected_policy_sha256,
        )
    except CapturedInstrumentRulesError:
        raise
    except Exception:  # noqa: BLE001 -- raw replay errors may contain private data
        raise CapturedInstrumentRulesError(
            "captured_instrument_rules_invalid"
        ) from None


def _derive(packet, instrument_id, plan_pin, packet_pin, policy_pin):
    if type(instrument_id) is not str or _INSTRUMENT.fullmatch(instrument_id) is None:
        _deny("captured_instrument_scope_unsupported")
    if type(policy_pin) is not str or policy_pin != POLICY_SHA256:
        _deny("captured_instrument_rule_policy_mismatch")
    frozen = capture.freeze_demo_account_packet(packet, expected_plan_sha256=plan_pin)
    replay = capture.verify_demo_account_packet(
        frozen.payload, expected_sha256=packet_pin, expected_plan_sha256=plan_pin
    )
    if replay.plan.settlement_currency != "USDT":
        _deny("captured_instrument_scope_unsupported")
    seen, matched = set(), []
    for request_index, page in enumerate(replay.observations):
        if page.request.stream != "account_instruments":
            continue
        for row_ordinal, record in enumerate(page.rows):
            raw = record.canonical_json.encode("utf-8")
            row = json.loads(raw)
            name = row.get("instId")
            if type(name) is not str or name in seen:
                _deny("captured_instrument_duplicate_or_invalid_identity")
            seen.add(name)
            if name == instrument_id:
                matched.append((request_index, row_ordinal, page, raw, row))
    if len(matched) != 1:
        _deny("captured_instrument_exact_row_missing")
    request_index, row_ordinal, page, raw, row = matched[0]
    base = row.get("ctValCcy")
    if (
        row.get("instType") != "SWAP"
        or row.get("ctType") != "linear"
        or row.get("settleCcy") != "USDT"
        or row.get("state") != "live"
        or type(base) is not str
        or _CURRENCY.fullmatch(base) is None
        or base == "USDT"
        or instrument_id != base + "-USDT-SWAP"
        or row.get("baseCcy") not in (None, "", base)
        or row.get("quoteCcy") not in (None, "", "USDT")
    ):
        _deny("captured_instrument_contract_unsupported")
    values = {
        field: _positive_decimal(row.get(field))
        for field in ("tickSz", "lotSz", "minSz", "ctVal", "ctMult")
    }
    if values["ctMult"] != 1:
        _deny("captured_instrument_multiplier_unsupported")
    # minSz need not itself lie on the lot grid. Preserve both independent
    # source constraints exactly; a future sizing policy must satisfy both.
    receipt = {
        "schema_version": "ctcc.captured_instrument_rules_receipt.v1",
        "policy_sha256": POLICY_SHA256,
        "plan_sha256": plan_pin,
        "packet_sha256": packet_pin,
        "packet_schema_version": replay.schema_version,
        "account_identity": {
            "environment": replay.plan.environment,
            "uid": replay.plan.expected_uid,
            "main_uid": replay.plan.expected_main_uid,
            "session_binding_sha256": journal.digest(
                replay.plan.session_binding_id.encode()
            ),
        },
        "instrument_id": instrument_id,
        "base_currency": base,
        "settlement_currency": "USDT",
        "contract_type": "linear",
        "state": "live",
        "rules": {
            field: {"raw": row[field], "exact": _fraction(value)}
            for field, value in values.items()
        },
        "units": {
            "tickSz": "USDT_per_" + base,
            "lotSz": "contracts",
            "minSz": "contracts",
            "ctVal": base + "_per_contract",
            "ctMult": "dimensionless",
        },
        "source_binding": {
            "stream": "account_instruments",
            "request_index": request_index,
            "row_ordinal": row_ordinal,
            "page_index": page.page_index,
            "row_sha256": journal.digest(raw),
            "page_receipt_sha256": page.receipt_sha256,
            "response_body_sha256": page.body_sha256,
            "page_canonical_sha256": page.canonical_sha256,
            "request_query": list(page.request.parameters),
            "terminal": page.terminal,
        },
        "measured_receipt": {
            "request_started_at": page.request_started_at.isoformat(),
            "headers_received_at": page.headers_received_at.isoformat(),
            "body_completed_at": page.body_completed_at.isoformat(),
            "publication_barrier": page.barrier_completed_at.isoformat(),
        },
        "source_ts": {
            "field_present": "ts" in row,
            "raw": row.get("ts"),
            "semantics": "unknown_for_this_endpoint",
            "exchange_asof": None,
        },
        "unverified": [
            "native_source_ownership",
            "current_rule_freshness",
            "historical_contract_validity",
            "risk_caps",
            "sizing_policy",
        ],
        "source_authenticity_verified": False,
        "current_owned_session_authority": False,
        "account_complete": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    return CapturedInstrumentRules(
        instrument_id,
        base,
        "USDT",
        values["tickSz"],
        values["lotSz"],
        values["minSz"],
        values["ctVal"],
        values["ctMult"],
        raw,
        journal.canonical(receipt),
    )
