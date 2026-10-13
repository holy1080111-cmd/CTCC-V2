"""Bounded replay of a supplied OKX Demo USDT-SWAP fee response.

This is a source-derived diagnostic, not an authenticated fee collector.  The
account-instruments mapping comes from an externally pinned original V7 packet;
fee rates come only from externally pinned raw ``trade-fee`` bytes.  A caller can
forge both portable inputs, so the receipt always DENIES execution.  In
particular an API host, successful HTTP status, or registration-evidence hash
cannot prove this account's original registration site.

OKX V5 fee and instruments contracts reviewed 2026-10-13:
https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-fee-rates
https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-instruments
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import captured_instrument_rules as rules

MAX_FEE_BYTES = 262144
MAX_FEE_GROUPS = 32
MAX_FEE_REQUEST_SECONDS = 5
MAX_AFTER_ACCOUNT_SECONDS = 30
FEE_ENDPOINT = "/api/v5/account/trade-fee"
_DIGEST = re.compile(r"[a-f0-9]{64}")
_GROUP = re.compile(r"[1-9][0-9]{0,5}")
_FAMILY = re.compile(r"[A-Z0-9]{1,20}-USDT")
_SIGNED_RATE = re.compile(r"-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,20})?")
_MS = re.compile(r"[1-9][0-9]{12}")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ROW_KEYS = frozenset(
    {
        "category",
        "delivery",
        "exercise",
        "feeGroup",
        "fiat",
        "instType",
        "level",
        "maker",
        "makerU",
        "makerUSDC",
        "ruleType",
        "settle",
        "taker",
        "takerU",
        "takerUSDC",
        "ts",
    }
)
_GROUP_KEYS = frozenset({"groupId", "maker", "taker", "elpMaker", "rpiMaker"})
POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.demo_swap_fee_source_policy.v1",
        "account_packet": "ctcc.demo_current_account_capture.v7",
        "instrument_scope": "exact_live_linear_USDT_SWAP_original_account_instruments_row",
        "fee_request": "GET_instType_SWAP_plus_exact_original_instFamily_only",
        "mapping": "unique_instrument_groupId_to_unique_feeGroup_groupId",
        "timestamp": "fee_ts_is_data_return_ms_not_fill_or_availability_time",
        "signed_rates": "positive_rebate_negative_commission_exact_decimal",
        "deprecated_top_level_rates": "retained_in_raw_never_used",
        "zero_fee_exception": "API_does_not_reflect_zero_fee_pairs_unverified",
        "max_fee_bytes": MAX_FEE_BYTES,
        "max_fee_groups": MAX_FEE_GROUPS,
        "max_fee_request_seconds": MAX_FEE_REQUEST_SECONDS,
        "max_after_account_seconds": MAX_AFTER_ACCOUNT_SECONDS,
        "registered_site_proven": False,
        "source_authenticity_verified": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class SwapFeeSourceError(ValueError):
    """Only stable codes, never source bodies, credentials, or account IDs."""


def _deny(code: str) -> None:
    raise SwapFeeSourceError(code)


@dataclass(frozen=True, slots=True, repr=False)
class SuppliedSwapFeeObservation:
    """Portable raw evidence, explicitly not an owned authenticated GET."""

    raw_body: bytes = field(repr=False)
    origin: str
    method: str
    endpoint: str
    parameters: tuple[tuple[str, str], ...]
    request_started_at: datetime
    headers_received_at: datetime
    body_completed_at: datetime
    barrier_completed_at: datetime
    status_code: int
    content_type: str
    content_encoding: str
    redirected: bool


@dataclass(frozen=True, slots=True, repr=False)
class SwapFeeSourceDiagnostic:
    instrument_id: str
    instrument_family: str
    group_id: str
    signed_maker_rate: Decimal
    signed_taker_rate: Decimal
    raw_fee_body: bytes = field(repr=False)
    receipt_json: bytes = field(repr=False)

    @property
    def receipt_sha256(self) -> str:
        return journal.digest(self.receipt_json)

    @property
    def registered_site_proven(self) -> bool:
        return False

    @property
    def source_authenticity_verified(self) -> bool:
        return False

    @property
    def account_complete(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False


def _pin(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _deny("swap_fee_pin_invalid")
    return value


def _observation_document(observation: SuppliedSwapFeeObservation) -> bytes:
    if type(observation) is not SuppliedSwapFeeObservation:
        _deny("swap_fee_observation_type_invalid")
    raw = observation.raw_body
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_FEE_BYTES:
        _deny("swap_fee_response_bytes_invalid")
    try:
        clocks = tuple(
            capture._utc(value)
            for value in (
                observation.request_started_at,
                observation.headers_received_at,
                observation.body_completed_at,
                observation.barrier_completed_at,
            )
        )
    except capture.AccountCaptureError:
        _deny("swap_fee_clock_invalid")
    if any(
        type(value) is not str or len(value) > maximum
        for value, maximum in (
            (observation.origin, 64),
            (observation.method, 8),
            (observation.endpoint, 96),
            (observation.content_type, 96),
            (observation.content_encoding, 32),
        )
    ):
        _deny("swap_fee_observation_invalid")
    if (
        type(observation.parameters) is not tuple
        or len(observation.parameters) != 2
        or any(
            type(pair) is not tuple
            or len(pair) != 2
            or any(type(part) is not str or len(part) > 96 for part in pair)
            for pair in observation.parameters
        )
        or type(observation.status_code) is not int
        or not 100 <= observation.status_code <= 599
        or type(observation.redirected) is not bool
    ):
        _deny("swap_fee_observation_invalid")
    return journal.canonical(
        {
            "schema_version": "ctcc.supplied_swap_fee_observation.v1",
            "raw_body_sha256": journal.digest(raw),
            "raw_body_size": len(raw),
            "origin": observation.origin,
            "method": observation.method,
            "endpoint": observation.endpoint,
            "parameters": [list(pair) for pair in observation.parameters],
            "request_started_at": clocks[0].isoformat(),
            "headers_received_at": clocks[1].isoformat(),
            "body_completed_at": clocks[2].isoformat(),
            "barrier_completed_at": clocks[3].isoformat(),
            "status_code": observation.status_code,
            "content_type": observation.content_type,
            "content_encoding": observation.content_encoding,
            "redirected": observation.redirected,
            "source_authenticity_verified": False,
        }
    )


def supplied_observation_sha256(observation: SuppliedSwapFeeObservation) -> str:
    """Pin all portable request/receipt metadata as well as raw body identity."""
    return journal.digest(_observation_document(observation))


def _rate(raw: object) -> tuple[Decimal, dict]:
    if type(raw) is not str or len(raw) > 42 or _SIGNED_RATE.fullmatch(raw) is None:
        _deny("swap_fee_rate_invalid")
    value = Decimal(raw)
    # This is an explicit uncalibrated diagnostic bound, not a fee-risk limit.
    if (value.is_zero() and value.is_signed()) or value.copy_abs() > Decimal("0.01"):
        _deny("swap_fee_rate_invalid")
    exact = Fraction(value)
    meaning = (
        "rebate"
        if value > 0
        else "commission"
        if value < 0
        else "zero_reported_unverified"
    )
    return value, {
        "raw": raw,
        "exact": {
            "numerator": str(exact.numerator),
            "denominator": str(exact.denominator),
        },
        "meaning": meaning,
    }


def derive_supplied_swap_fee_source(
    original_account_packet: bytes,
    fee_observation: SuppliedSwapFeeObservation,
    *,
    instrument_id: str,
    expected_account_packet_sha256: str,
    expected_account_plan_sha256: str,
    expected_fee_raw_sha256: str,
    expected_fee_observation_sha256: str,
    expected_policy_sha256: str = POLICY_SHA256,
) -> SwapFeeSourceDiagnostic:
    """Recompute an exact DENY-only rate mapping from the two supplied raw sources."""
    try:
        return _derive(
            original_account_packet,
            fee_observation,
            instrument_id=instrument_id,
            account_pin=expected_account_packet_sha256,
            plan_pin=expected_account_plan_sha256,
            fee_pin=expected_fee_raw_sha256,
            observation_pin=expected_fee_observation_sha256,
            policy_pin=expected_policy_sha256,
        )
    except SwapFeeSourceError:
        raise
    except Exception:  # noqa: BLE001 -- never expose raw account/fee exceptions
        raise SwapFeeSourceError("swap_fee_source_invalid") from None


def _derive(
    packet_bytes,
    observation,
    *,
    instrument_id,
    account_pin,
    plan_pin,
    fee_pin,
    observation_pin,
    policy_pin,
):
    if policy_pin != POLICY_SHA256 or type(policy_pin) is not str:
        _deny("swap_fee_policy_mismatch")
    for pin in (account_pin, plan_pin, fee_pin, observation_pin):
        _pin(pin)
    if journal.digest(_observation_document(observation)) != observation_pin:
        _deny("swap_fee_observation_pin_mismatch")
    if journal.digest(observation.raw_body) != fee_pin:
        _deny("swap_fee_raw_pin_mismatch")
    packet = capture.verify_demo_account_packet(
        packet_bytes,
        expected_sha256=account_pin,
        expected_plan_sha256=plan_pin,
    )
    if (
        type(packet.plan) is not capture.CurrentDemoAccountCapturePlanV7
        or packet.schema_version != "ctcc.demo_current_account_capture.v7"
        or packet.plan.environment != "demo"
        or packet.plan.settlement_currency != "USDT"
    ):
        _deny("swap_fee_account_scope_invalid")
    captured = rules.derive_captured_instrument_rules(
        packet,
        instrument_id=instrument_id,
        expected_plan_sha256=plan_pin,
        expected_packet_sha256=account_pin,
    )
    # The original row was already replayed and checked by the account packet.
    # Its documented instIdCode may be a large JSON integer, which the generic
    # non-wire packet decoder deliberately does not accept in isolation.
    instrument_row = json.loads(captured.raw_row_json)
    instrument_receipt = json.loads(captured.receipt_json)
    instrument_page = packet.observations[
        instrument_receipt["source_binding"]["request_index"]
    ]
    family, group_id = instrument_row.get("instFamily"), instrument_row.get("groupId")
    if (
        type(family) is not str
        or _FAMILY.fullmatch(family) is None
        or family != captured.base_currency + "-USDT"
        or type(group_id) is not str
        or _GROUP.fullmatch(group_id) is None
    ):
        _deny("swap_fee_instrument_mapping_invalid")
    if (
        observation.origin != packet.plan.origin
        or observation.method != "GET"
        or observation.endpoint != FEE_ENDPOINT
        or observation.parameters != (("instFamily", family), ("instType", "SWAP"))
        or observation.status_code != 200
        or observation.content_type.split(";", 1)[0].strip().lower()
        != "application/json"
        or observation.content_encoding.lower() != "identity"
        or observation.redirected is not False
    ):
        _deny("swap_fee_request_or_response_invalid")
    start, headers, complete, barrier = (
        capture._utc(moment)
        for moment in (
            observation.request_started_at,
            observation.headers_received_at,
            observation.body_completed_at,
            observation.barrier_completed_at,
        )
    )
    if (
        not barrier < start <= headers <= complete
        or start <= packet.completed_at
        or start - packet.completed_at > timedelta(seconds=MAX_AFTER_ACCOUNT_SECONDS)
        or start - instrument_page.body_completed_at
        > timedelta(seconds=MAX_AFTER_ACCOUNT_SECONDS)
        or complete - start > timedelta(seconds=MAX_FEE_REQUEST_SECONDS)
    ):
        _deny("swap_fee_causal_clock_invalid")
    body, canonical_text = capture._decode_json(
        observation.raw_body, limit=MAX_FEE_BYTES, wire=True
    )
    if (
        type(body) is not dict
        or set(body) - {"code", "msg", "data", "inTime", "outTime"}
        or body.get("code") != "0"
        or type(body.get("msg")) is not str
        or type(body.get("data")) is not list
        or len(body["data"]) != 1
        or ("inTime" in body) != ("outTime" in body)
    ):
        _deny("swap_fee_envelope_invalid")
    if "inTime" in body:
        gateway_in = capture._gateway_time(body["inTime"])
        gateway_out = capture._gateway_time(body["outTime"])
        if not start <= gateway_in <= gateway_out <= headers:
            _deny("swap_fee_gateway_clock_invalid")
    fee_row = body["data"][0]
    if (
        type(fee_row) is not dict
        or set(fee_row) - _ROW_KEYS
        or fee_row.get("instType") != "SWAP"
        or type(fee_row.get("feeGroup")) is not list
        or not 1 <= len(fee_row["feeGroup"]) <= MAX_FEE_GROUPS
        or type(fee_row.get("ts")) is not str
        or _MS.fullmatch(fee_row["ts"]) is None
    ):
        _deny("swap_fee_row_invalid")
    try:
        source_time = _EPOCH + timedelta(milliseconds=int(fee_row["ts"]))
    except (ValueError, OverflowError):
        _deny("swap_fee_data_return_clock_invalid")
    if not start <= source_time <= headers:
        _deny("swap_fee_data_return_clock_invalid")
    seen: set[str] = set()
    selected = None
    for entry in fee_row["feeGroup"]:
        if type(entry) is not dict or set(entry) - _GROUP_KEYS:
            _deny("swap_fee_group_invalid")
        identity = entry.get("groupId")
        if (
            type(identity) is not str
            or _GROUP.fullmatch(identity) is None
            or identity in seen
        ):
            _deny("swap_fee_group_duplicate_or_invalid")
        seen.add(identity)
        _rate(entry.get("maker"))
        _rate(entry.get("taker"))
        for optional in ("elpMaker", "rpiMaker"):
            if optional in entry and entry[optional] != "":
                _rate(entry[optional])
            elif optional in entry and type(entry[optional]) is not str:
                _deny("swap_fee_group_invalid")
        if identity == group_id:
            selected = entry
    if selected is None:
        _deny("swap_fee_group_mapping_missing")
    maker, maker_record = _rate(selected["maker"])
    taker, taker_record = _rate(selected["taker"])
    receipt = journal.canonical(
        {
            "schema_version": "ctcc.demo_swap_fee_source_diagnostic.v1",
            "policy_sha256": POLICY_SHA256,
            "original_account_packet_sha256": account_pin,
            "original_account_plan_sha256": plan_pin,
            "instrument_id": instrument_id,
            "instrument_family": family,
            "instrument_group_id": group_id,
            "instrument_rules_receipt_sha256": captured.receipt_sha256,
            "account_instruments_row_sha256": journal.digest(captured.raw_row_json),
            "account_instruments_page_receipt_sha256": instrument_page.receipt_sha256,
            "fee_raw_body_sha256": fee_pin,
            "fee_canonical_body_sha256": journal.digest(canonical_text.encode("utf-8")),
            "fee_observation_sha256": observation_pin,
            "fee_request": {
                "origin": observation.origin,
                "method": "GET",
                "endpoint": FEE_ENDPOINT,
                "parameters": [list(pair) for pair in observation.parameters],
            },
            "fee_group_count": len(fee_row["feeGroup"]),
            "signed_maker_rate": maker_record,
            "signed_taker_rate": taker_record,
            "fee_ts": {
                "raw": fee_row["ts"],
                "semantics": "data_return_time_not_trade_match_or_asof",
            },
            "measured_receipt": {
                "request_started_at": start.isoformat(),
                "headers_received_at": headers.isoformat(),
                "body_completed_at": complete.isoformat(),
                "publication_barrier": barrier.isoformat(),
                "instrument_body_completed_at": instrument_page.body_completed_at.isoformat(),
            },
            "unverified": [
                "original_registration_site",
                "native_owned_signed_GET_and_TLS",
                "current_fee_freshness",
                "zero_fee_override",
                "maker_execution_mode",
                "complete_account_risk",
                "execution_cost_authority",
            ],
            "registered_site_proven": False,
            "source_authenticity_verified": False,
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    return SwapFeeSourceDiagnostic(
        instrument_id=instrument_id,
        instrument_family=family,
        group_id=group_id,
        signed_maker_rate=maker,
        signed_taker_rate=taker,
        raw_fee_body=observation.raw_body,
        receipt_json=receipt,
    )


def verify_supplied_swap_fee_receipt(
    receipt_json: bytes,
    *,
    expected_receipt_sha256: str,
    original_account_packet: bytes,
    fee_observation: SuppliedSwapFeeObservation,
    instrument_id: str,
    expected_account_packet_sha256: str,
    expected_account_plan_sha256: str,
    expected_fee_raw_sha256: str,
    expected_fee_observation_sha256: str,
    expected_policy_sha256: str = POLICY_SHA256,
) -> SwapFeeSourceDiagnostic:
    """Read back the portable receipt by rebuilding it from pinned raw sources."""
    _pin(expected_receipt_sha256)
    rebuilt = derive_supplied_swap_fee_source(
        original_account_packet,
        fee_observation,
        instrument_id=instrument_id,
        expected_account_packet_sha256=expected_account_packet_sha256,
        expected_account_plan_sha256=expected_account_plan_sha256,
        expected_fee_raw_sha256=expected_fee_raw_sha256,
        expected_fee_observation_sha256=expected_fee_observation_sha256,
        expected_policy_sha256=expected_policy_sha256,
    )
    if (
        type(receipt_json) is not bytes
        or journal.digest(receipt_json) != expected_receipt_sha256
        or receipt_json != rebuilt.receipt_json
    ):
        _deny("swap_fee_receipt_mismatch")
    return rebuilt
