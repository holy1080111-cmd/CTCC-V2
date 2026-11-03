"""Original coupled CURRENT values and finite unit/number helpers.

Initial D integration deliberately exposes only the actually used coupled
source slice. The independent V1 inventory/mark-path proposal remains frozen
in its own archive; it is not imported or relabeled as native account proof.
All returned values lack account, revision and execution authority.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from fractions import Fraction

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_original_raw_proof_bank_v1 as bank

MAX_RATIONAL_BITS = 4096
MAX_ORDERS = 128
MAX_CONTEXT_ROWS = 4096
MAX_RECEIPT_BYTES = 1024 * 1024
_SHA = re.compile(r"[a-f0-9]{64}")
_SYMBOL = re.compile(r"[A-Z0-9]{1,20}-USDT-SWAP")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,30})?")
_ID = re.compile(r"[1-9][0-9]{0,31}")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _checked(value):
    if type(value) is not Fraction:
        raise OriginalPositionPathError(
            "position_path_exact_fraction_required"
        ) from None
    if (
        value.numerator.bit_length() > MAX_RATIONAL_BITS
        or value.denominator.bit_length() > MAX_RATIONAL_BITS
    ):
        raise OriginalPositionPathError("position_path_rational_bit_bound") from None
    return value


class OriginalPositionPathError(ValueError):
    """Fixed source-safe errors only."""


def _need(value, code):
    if value is not True:
        raise OriginalPositionPathError(code) from None


def _utc(value):
    _need(
        type(value) is datetime and value.tzinfo is UTC and 1970 <= value.year <= 3000,
        "position_path_original_UTC_required",
    )
    return value


def _time(value):
    _need(type(value) is str, "position_path_original_time_required")
    return _utc(datetime.fromisoformat(value))


def _ms(value):
    _need(
        type(value) is str
        and value.isascii()
        and value.isdigit()
        and len(value) <= 17
        and str(int(value)) == value,
        "position_path_original_milliseconds_invalid",
    )
    number = int(value)
    _need(0 <= number <= 32503680000000, "position_path_original_milliseconds_invalid")
    return _EPOCH + timedelta(milliseconds=number)


def _digest(value):
    _need(
        type(value) is str and _SHA.fullmatch(value) is not None,
        "position_path_source_digest_invalid",
    )
    return value


def _id(value):
    _need(
        type(value) is str and _ID.fullmatch(value) is not None,
        "position_path_original_identity_invalid",
    )
    return value


def _number(value, *, positive=False, nonnegative=False):
    _need(
        type(value) is str and _NUMBER.fullmatch(value) is not None,
        "position_path_original_number_invalid",
    )
    result = _checked(Fraction(value))
    _need(
        (not positive or result > 0) and (not nonnegative or result >= 0),
        "position_path_original_number_out_of_range",
    )
    return result


def _fraction(value):
    checked = _checked(value)
    return [str(checked.numerator), str(checked.denominator)]


def _specifications(document, names):
    specs = document["original_instrument_specs"]
    _need(
        type(specs) is dict and len(specs) <= MAX_ORDERS,
        "position_path_original_metadata_population_bound",
    )
    result, rows = {}, 0
    for name in names:
        choices = specs.get(name)
        _need(
            type(choices) is list and len(choices) > 0,
            "position_path_contract_metadata_missing",
        )
        rows += len(choices)
        _need(
            rows <= MAX_CONTEXT_ROWS, "position_path_original_context_population_bound"
        )
        first, pin = choices[0], sha(canonical(choices[0]))
        _need(
            all(type(item) is dict and sha(canonical(item)) == pin for item in choices),
            "position_path_contract_metadata_changed",
        )
        _need(
            first.get("instId") == name
            and first.get("instType") == "SWAP"
            and first.get("ctType") == "linear"
            and first.get("settleCcy") == "USDT"
            and first.get("ctValCcy") == name.split("-")[0]
            and first.get("ctMult") == "1",
            "position_path_contract_unit_unsupported",
        )
        result[name] = (_number(first.get("ctVal"), positive=True), pin)
    return result


COUPLED_ANCHOR_SCHEMA_V2 = "ctcc.original_coupled_risk_anchor_values.v2"
MAX_COUPLED_ROWS_V2 = 4096
COUPLED_ANCHOR_POLICY_BYTES_V2 = canonical(
    {
        "version": "ctcc.original_coupled_risk_anchor.policy.v2",
        "input": "original_chain_reference_scope_and_original_packet_replay",
        "equity": "USDT_balData_eq_in_single_account_position_risk_response",
        "positions": "same_response_posData_contracts_original_order_no_unit_conversion",
        "corroboration": "original_balance_and_positions_values_no_time_relabel",
        "available_margin": "separate_balance_observation_not_coupled_anchor_operand",
        "population": "exact_requested_packet_streams_filters_pages_rows_and_receipts",
        "time": "source_update_and_request_receipt_times_remain_distinct",
        "source_authentication": "retained_native_owner_required_not_issued_by_values",
        "genesis_seed_funding_tail_and_unobserved_peak": "not_inferred",
        "maximum_rows": MAX_COUPLED_ROWS_V2,
    }
)
COUPLED_ANCHOR_POLICY_SHA256_V2 = sha(COUPLED_ANCHOR_POLICY_BYTES_V2)


@dataclass(frozen=True, slots=True, repr=False)
class OriginalCoupledPositionValuesV2:
    position_id: str
    instrument_id: str
    position_side: str
    margin_mode: str
    contracts: Fraction
    original_risk_row_sha256: str
    original_position_row_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class OriginalCoupledRiskAnchorValuesV2:
    original_packet: bytes = field(repr=False)
    source_reference_json: bytes = field(repr=False)
    account_equity_usdt: Fraction
    positions: tuple[OriginalCoupledPositionValuesV2, ...] = field(repr=False)
    account_source_updated_at: datetime
    risk_request_started_at: datetime
    risk_headers_received_at: datetime
    risk_body_completed_at: datetime
    original_packet_completed_at: datetime
    separately_observed_available_margin_usdt: Fraction
    separate_balance_measurement_json: bytes = field(repr=False)
    receipt_json: bytes = field(repr=False)
    source_authenticity_verified: bool = field(default=False, init=False)
    account_complete: bool = field(default=False, init=False)
    account_revision_issued: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


def _coupled_packet_population_v2(packet):
    # The original whole-packet verifier owns cursor/terminal semantics. These
    # are exact original witnesses of its requested population, not a second
    # invented pagination rule or a claim about future exchange revisions.
    result, row_count = [], 0
    for index, page in enumerate(packet.observations):
        row_count += len(page.rows)
        _need(
            row_count <= MAX_COUPLED_ROWS_V2, "coupled_anchor_original_population_bound"
        )
        result.append(
            {
                "request_index": index,
                "request": bank.capture._json_value(page.request),
                "page_index": page.page_index,
                "cursor": page.after,
                "previous_page_sha256": page.previous_page_sha256,
                "page_receipt_sha256": page.receipt_sha256,
                "body_sha256": page.body_sha256,
                "body_size_bytes": page.body_size_bytes,
                "request_started_at": page.request_started_at.isoformat(),
                "headers_received_at": page.headers_received_at.isoformat(),
                "body_completed_at": page.body_completed_at.isoformat(),
                "terminal": page.terminal,
                "original_row_sha256": [
                    sha(row.canonical_json.encode("utf-8")) for row in page.rows
                ],
            }
        )
    return result


def _derive_original_coupled_risk_anchor_v2(chain, *, reference, scope, checked_at):
    from app.trade_qualification import (
        account_original_balance_measurement_v1 as measured,
    )

    # This existing public verifier reopens original bytes and the complete
    # V5/V6 history query or V7 CURRENT chain. It also checks exact identity,
    # source clock semantics and original risk/balance/position consistency.
    balance_raw = measured.measure_original_balance_v1(
        chain, reference=reference, scope=scope, checked_at=_utc(checked_at)
    )
    payloads = [
        point.event.packet_payload for point in chain if point.event.packet_payload
    ]
    _need(len(payloads) == 1, "coupled_anchor_original_packet_required")
    original = payloads[0]
    packet = bank.capture._verify_original_page_packet(
        original,
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )
    return _coupled_anchor_from_original_packet_v3(
        packet,
        original=original,
        reference=reference,
        scope=scope,
        balance_raw=balance_raw,
        checked_at=checked_at,
    )


def _coupled_anchor_from_original_packet_v3(
    packet,
    *,
    original,
    reference,
    scope,
    balance_raw,
    checked_at,
):
    from app.trade_qualification import (
        account_original_balance_measurement_v1 as measured,
    )

    balance = measured._read(balance_raw)
    groups = {}
    for index, page in enumerate(packet.observations):
        groups.setdefault(page.request.stream, []).append((index, page))
    _need(
        all(
            len(groups.get(name, ())) == 1
            for name in ("account_position_risk", "positions", "balance")
        ),
        "coupled_anchor_original_snapshot_cardinality",
    )
    risk_index, risk_page = groups["account_position_risk"][0]
    _need(len(risk_page.rows) == 1, "coupled_anchor_original_snapshot_cardinality")
    risk = decode(
        risk_page.rows[0].canonical_json.encode("utf-8"),
        bank.capture.MAX_RESPONSE_BYTES,
    )
    balances, inventory = risk["balData"], risk["posData"]
    _need(
        type(balances) is list
        and 0 < len(balances) <= MAX_COUPLED_ROWS_V2
        and type(inventory) is list
        and len(inventory) <= MAX_COUPLED_ROWS_V2,
        "coupled_anchor_original_nested_population_invalid",
    )
    currencies = set()
    settlement = None
    for item in balances:
        _need(
            type(item) is dict
            and type(item.get("ccy")) is str
            and item["ccy"] not in currencies,
            "coupled_anchor_original_currency_duplicate_or_missing",
        )
        currencies.add(item["ccy"])
        if item["ccy"] == scope.settlement_currency:
            settlement = item
    _need(settlement is not None, "coupled_anchor_original_USDT_equity_missing")
    equity = _number(settlement.get("eq"), positive=True)
    _need(
        equity == measured._rational(balance["equity"], positive=True),
        "coupled_anchor_original_equity_conflict",
    )
    source_at = _ms(risk.get("ts"))
    _need(
        packet.barrier_completed_at
        <= source_at
        <= risk_page.body_completed_at
        <= packet.completed_at
        <= checked_at,
        "coupled_anchor_original_source_clock_conflict",
    )
    position_page = groups["positions"][0][1]
    corroboration = {}
    for row in position_page.rows:
        raw = decode(
            row.canonical_json.encode("utf-8"), bank.capture.MAX_RESPONSE_BYTES
        )
        identity = _id(raw.get("posId"))
        _need(
            identity not in corroboration, "coupled_anchor_original_position_duplicate"
        )
        corroboration[identity] = (raw, sha(row.canonical_json.encode("utf-8")))
    positions, identities = [], set()
    for row in inventory:
        _need(type(row) is dict, "coupled_anchor_original_position_invalid")
        identity = _id(row.get("posId"))
        _need(
            identity not in identities and identity in corroboration,
            "coupled_anchor_original_position_duplicate_or_unjoined",
        )
        identities.add(identity)
        other, other_pin = corroboration[identity]
        _need(
            type(row.get("instId")) is str
            and _SYMBOL.fullmatch(row["instId"]) is not None
            and row.get("instType") == "SWAP"
            and row.get("posSide") == "net"
            and row.get("mgnMode") in {"cross", "isolated"}
            and other.get("ccy") == "USDT"
            and all(
                row.get(key) == other.get(key)
                for key in ("instId", "instType", "posSide", "mgnMode")
            ),
            "coupled_anchor_original_position_scope_unsupported_or_conflicting",
        )
        contracts = _number(row.get("pos"))
        _need(
            contracts == _number(other.get("pos")),
            "coupled_anchor_original_position_quantity_conflict",
        )
        positions.append(
            OriginalCoupledPositionValuesV2(
                identity,
                row["instId"],
                row["posSide"],
                row["mgnMode"],
                contracts,
                sha(journal.canonical(row)),
                other_pin,
            )
        )
    _need(
        identities == set(corroboration),
        "coupled_anchor_original_position_population_conflict",
    )
    population = _coupled_packet_population_v2(packet)
    reference_raw = canonical(measured.observed.reference_document(reference))
    receipt = canonical(
        {
            "schema_version": COUPLED_ANCHOR_SCHEMA_V2,
            "policy_sha256": COUPLED_ANCHOR_POLICY_SHA256_V2,
            "scope_sha256": sha(
                canonical(
                    [scope.environment, scope.account_id, scope.settlement_currency]
                )
            ),
            "source_reference_sha256": sha(reference_raw),
            "original_packet_sha256": sha(original),
            "packet_plan_sha256": packet.plan_sha256,
            "packet_contract": packet.plan.contract_version,
            "original_packet_incomplete_reasons": list(packet.incomplete_reasons),
            "history_window_as_requested": [
                packet.plan.history_start.isoformat(),
                packet.plan.history_end.isoformat(),
            ],
            "requested_api_population": population,
            "risk_request_index": risk_index,
            "risk_page_receipt_sha256": risk_page.receipt_sha256,
            "risk_body_sha256": risk_page.body_sha256,
            "risk_source_updated_at": source_at.isoformat(),
            "original_packet_completed_at": packet.completed_at.isoformat(),
            "coupled_equity_usdt": _fraction(equity),
            "original_coupled_position_rows": [
                item.original_risk_row_sha256 for item in positions
            ],
            "separate_balance_measurement_sha256": sha(balance_raw),
            "available_margin_is_same_response_anchor_operand": False,
            "source_time_is_packet_completion_time": False,
            "local_prefix_genesis_is_exchange_account_genesis": False,
            "exchange_wide_atomicity_verified": False,
            "cashflow_and_funding_effective_time_finality": None,
            "loss_streak_seed": None,
            "unobserved_peak": None,
            "current_history_tail_finality": None,
            "source_authenticity_verified": False,
            "account_complete": False,
            "account_revision_issued": False,
            "execution_authority": False,
        }
    )
    _need(len(receipt) <= MAX_RECEIPT_BYTES, "coupled_anchor_original_receipt_bound")
    return OriginalCoupledRiskAnchorValuesV2(
        original,
        reference_raw,
        equity,
        tuple(positions),
        source_at,
        risk_page.request_started_at,
        risk_page.headers_received_at,
        risk_page.body_completed_at,
        packet.completed_at,
        measured._rational(balance["available_margin"]),
        balance_raw,
        receipt,
    )


def derive_original_coupled_risk_anchor_v2(chain, *, reference, scope, checked_at):
    """Verify a useful original source slice; never mint account authority."""
    try:
        return _derive_original_coupled_risk_anchor_v2(
            chain, reference=reference, scope=scope, checked_at=checked_at
        )
    except OriginalPositionPathError:
        raise
    except Exception:  # noqa: BLE001 -- original source/account details stay private.
        raise OriginalPositionPathError(
            "coupled_anchor_original_source_invalid"
        ) from None


def _coupled_anchor_from_held_current_record_v3(record, *, scope, checked_at):
    """Private held-owner seam; this value never authenticates its caller.

    The assembly must replay its SAME original native CURRENT before/after
    using this seam. A caller-created Recorded DTO has no authority here.
    """
    from app.trade_qualification import account_current_source_v4 as current
    from app.trade_qualification import (
        account_original_balance_measurement_v1 as measured,
    )

    _utc(checked_at)
    _need(
        type(record) is current.RecordedCurrentAccountBundleV4
        and type(record.packet) is bank.capture.DemoAccountPacket
        and type(record.packet.plan) is bank.capture.CurrentDemoAccountCapturePlanV7,
        "coupled_anchor_held_CURRENT_record_required",
    )
    frozen = bank.capture.freeze_demo_account_packet(
        record.packet, expected_plan_sha256=record.reference.plan_sha256
    )
    _need(
        frozen.sha256 == record.reference.packet_sha256,
        "coupled_anchor_held_CURRENT_original_bytes_changed",
    )
    packet = bank.capture._verify_original_page_packet(
        frozen.payload,
        expected_sha256=record.reference.packet_sha256,
        expected_plan_sha256=record.reference.plan_sha256,
    )
    _need(
        packet.completed_at <= checked_at
        and packet.plan.expected_uid == scope.account_id
        and packet.plan.environment == scope.environment
        and packet.plan.settlement_currency == scope.settlement_currency,
        "coupled_anchor_held_CURRENT_scope_or_availability_changed",
    )
    balance_raw = measured._measure_verified_packet_v1(packet, record.reference, scope)
    return _coupled_anchor_from_original_packet_v3(
        packet,
        original=frozen.payload,
        reference=record.reference,
        scope=scope,
        balance_raw=balance_raw,
        checked_at=checked_at,
    )
