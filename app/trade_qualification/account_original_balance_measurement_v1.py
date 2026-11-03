"""Original V5/V6/V7 balance measurements and sampled-prefix peak arithmetic.

These bytes describe measured values only. They never establish a complete
account peak, cashflow-adjusted drawdown, current owner or execution authority.
The native collector binds them while its real source owners are still alive.
"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from fractions import Fraction

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_consistency as consistency
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_history_query_verifier as history
from app.trade_qualification import account_materializer as materializer
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

MAX_MEASUREMENT_BYTES = 1048576
SCHEMA = "ctcc.original_v5_v6_v7.balance_measurement.v1"
PEAK_SCHEMA = "ctcc.original_prefix.sampled_balance_peak.v1"
POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.original_v5_v6_v7.balance_measurement.policy.v1",
        "original_v5_query_policy_sha256": history.POLICY_SHA256,
        "history_query_policy_sha256": history.ALGO_V6_POLICY_SHA256,
        "current_source_policy_sha256": current.V7_POLICY_SHA256,
        "packet": "original_pinned_AllProduct_V5_V6_or_CURRENT_V7_only",
        "balance": "one_USDT_detail_exact_equity_and_available_margin",
        "identity": "config_before_after_UID_mainUID_acctLv_posMode_routing_scope",
        "time": "original_request_headers_body_and_source_update_times",
        "peak": "every_original_index_CURRENT_sequence_one_to_exact_final_head",
        "first_observed_sample_is_account_genesis": False,
        "unobserved_peak_and_cashflow_adjustment": "unknown",
        "native_owner_or_clock_authority": "not_issued_by_arithmetic",
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)
_MEMBER_DOMAIN = b"ctcc.original_prefix.sampled_balance_peak.v1\x00"
_FIELDS = frozenset(
    {
        "schema_version",
        "policy_sha256",
        "source_reference",
        "environment",
        "account_id",
        "main_uid",
        "settlement_currency",
        "account_level",
        "position_mode",
        "origin",
        "registration_region",
        "packet_contract",
        "session_binding_sha256",
        "equity",
        "available_margin",
        "request_index",
        "row_ordinal",
        "raw_row_sha256",
        "page_receipt_sha256",
        "body_sha256",
        "config_before_sha256",
        "config_after_sha256",
        "request_started_at",
        "headers_received_at",
        "body_completed_at",
        "packet_completed_at",
        "account_uTime",
        "settlement_uTime",
        "native_clock_issued",
        "current_owner_issued",
    }
)


class OriginalBalanceMeasurementError(ValueError):
    """Fixed codes only; no source rows or private/native diagnostics."""


def _deny(code="original_balance_measurement_invalid"):
    raise OriginalBalanceMeasurementError(code) from None


def _utc(value):
    if type(value) is not datetime or value.tzinfo is not UTC:
        _deny("original_balance_UTC_required")
    return value


def _fraction(value):
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _rational(value, *, positive=False):
    if type(value) is not dict or set(value) != {"numerator", "denominator"}:
        _deny()
    if any(
        type(item) is not str
        or not item.isascii()
        or not item.isdigit()
        or len(item) > 128
        for item in value.values()
    ):
        _deny()
    if int(value["denominator"]) <= 0:
        _deny()
    result = Fraction(int(value["numerator"]), int(value["denominator"]))
    if _fraction(result) != value or (result <= 0 if positive else result < 0):
        _deny()
    return result


def _read(value):
    if type(value) is not bytes or not 0 < len(value) <= MAX_MEASUREMENT_BYTES:
        _deny("original_balance_measurement_bound")
    data = json.loads(value)
    if (
        type(data) is not dict
        or set(data) != _FIELDS
        or journal.canonical(data) != value
        or data.get("schema_version") != SCHEMA
        or data["policy_sha256"] != POLICY_SHA256
        or type(data["request_index"]) is not int
        or not 0 <= data["request_index"] <= 255
        or type(data["row_ordinal"]) is not int
        or data["row_ordinal"] != 0
        or data["packet_contract"]
        not in (
            "ctcc.demo_account_plan.v5",
            "ctcc.demo_account_plan.v6",
            "ctcc.demo_current_account_plan.v7",
        )
        or data["account_level"] != "2"
        or data["position_mode"] != "net_mode"
        or data["origin"] != "https://openapi.okx.com"
        or data["registration_region"] != "global"
    ):
        _deny()
    for name in (
        "raw_row_sha256",
        "page_receipt_sha256",
        "body_sha256",
        "config_before_sha256",
        "config_after_sha256",
        "session_binding_sha256",
    ):
        journal._sha(data[name])
    times = [
        _utc(datetime.fromisoformat(data[name]))
        for name in (
            "request_started_at",
            "headers_received_at",
            "body_completed_at",
            "packet_completed_at",
        )
    ]
    if times != sorted(times) or any(
        materializer._time(data[name]) is None
        or materializer._time(data[name]) > times[2]
        for name in ("account_uTime", "settlement_uTime")
    ):
        _deny("original_balance_source_clock_changed")
    return data


def _measure_verified_packet_v1(packet, reference, scope):
    """Private arithmetic seam after the actual original-query/packet verifier.

    No native flag/capability is returned. The core calls this only after its
    complete original membership and actual witness replay. The public entry
    below additionally reopens the source chain itself; a supplied packet is
    never a public source-admission input.
    """
    checked_bootstrap(scope, LedgerScope)
    if (
        type(packet) is not capture.DemoAccountPacket
        or type(packet.plan)
        not in (
            capture.AllProductDemoAccountCapturePlan,
            capture.AllProductDemoAccountCapturePlanV6,
            capture.CurrentDemoAccountCapturePlanV7,
        )
        or type(reference) is not observed.CaptureReference
        or packet.plan_sha256 != reference.plan_sha256
        or packet.plan.environment != scope.environment
        or packet.plan.expected_uid != scope.account_id
        or scope.environment != "demo"
        or scope.settlement_currency != "USDT"
        or packet.plan.settlement_currency != scope.settlement_currency
        or packet.plan.registration_region != "global"
        or packet.plan.origin != "https://openapi.okx.com"
    ):
        _deny("original_balance_scope_or_packet_unsupported")
    observed.reference_document(reference)
    # Cheap exact-byte fingerprint after the real verifier; do not trust a
    # mutable/fabricated Pydantic DTO at this private arithmetic seam. This is
    # the same canonical encoding used by freeze_demo_account_packet, without
    # replaying every page a second time inside the active original-page scope.
    original_packet = capture._canonical(capture._json_value(packet)).encode("utf-8")
    if journal.digest(original_packet) != reference.packet_sha256:
        _deny("original_balance_packet_bytes_changed")
    configs = []
    for kind in ("config_before", "config_after"):
        pages = [page for page in packet.observations if page.request.stream == kind]
        if len(pages) != 1 or len(pages[0].rows) != 1:
            _deny("original_balance_config_required")
        raw = pages[0].rows[0].canonical_json
        config = json.loads(raw)
        identity = (
            config.get("uid"),
            config.get("mainUid"),
            config.get("acctLv"),
            config.get("posMode"),
        )
        if identity != (
            scope.account_id,
            packet.plan.expected_main_uid,
            "2",
            "net_mode",
        ):
            _deny("original_balance_config_identity_changed")
        if config.get("settleCcy") not in (None, "", scope.settlement_currency):
            _deny("original_balance_config_currency_changed")
        configs.append((raw, config))
    if any(
        configs[0][1].get(key) != configs[1][1].get(key)
        for key in ("uid", "mainUid", "acctLv", "posMode", "settleCcy")
    ):
        _deny("original_balance_config_changed")
    cross = consistency._reconcile_verified_packet(packet, reference.packet_sha256)
    if cross.blocking_reasons:
        _deny("original_balance_source_inconsistent")
    pages = [
        (index, page)
        for index, page in enumerate(packet.observations)
        if page.request.stream == "balance"
    ]
    if len(pages) != 1 or len(pages[0][1].rows) != 1:
        _deny("original_balance_cardinality_invalid")
    index, page = pages[0]
    row = json.loads(page.rows[0].canonical_json)
    materializer._supplemental_account_identity(row, packet)
    equity, available, gap = materializer._balance(row, scope.settlement_currency)
    if gap:
        _deny("original_balance_value_unknown")
    stamps = (row.get("uTime"), row["details"][0].get("uTime"))
    if any(
        materializer._time(stamp) is None
        or materializer._time(stamp) > page.body_completed_at
        for stamp in stamps
    ):
        _deny("original_balance_update_time_invalid")
    if not (
        _utc(page.request_started_at)
        <= _utc(page.headers_received_at)
        <= _utc(page.body_completed_at)
        <= _utc(packet.completed_at)
    ):
        _deny("original_balance_source_clock_changed")
    data = {
        "schema_version": SCHEMA,
        "policy_sha256": POLICY_SHA256,
        "source_reference": observed.reference_document(reference),
        "environment": scope.environment,
        "account_id": scope.account_id,
        "main_uid": packet.plan.expected_main_uid,
        "settlement_currency": scope.settlement_currency,
        "account_level": "2",
        "position_mode": "net_mode",
        "origin": packet.plan.origin,
        "registration_region": packet.plan.registration_region,
        "packet_contract": packet.plan.contract_version,
        "session_binding_sha256": reference.session_binding_sha256,
        "equity": _fraction(equity),
        "available_margin": _fraction(available),
        "request_index": index,
        "row_ordinal": 0,
        "raw_row_sha256": journal.digest(page.rows[0].canonical_json.encode()),
        "page_receipt_sha256": page.receipt_sha256,
        "body_sha256": page.body_sha256,
        "config_before_sha256": journal.digest(configs[0][0].encode()),
        "config_after_sha256": journal.digest(configs[1][0].encode()),
        "request_started_at": page.request_started_at.isoformat(),
        "headers_received_at": page.headers_received_at.isoformat(),
        "body_completed_at": page.body_completed_at.isoformat(),
        "packet_completed_at": packet.completed_at.isoformat(),
        "account_uTime": stamps[0],
        "settlement_uTime": stamps[1],
        "native_clock_issued": False,
        "current_owner_issued": False,
    }
    raw = journal.canonical(data)
    if len(raw) > MAX_MEASUREMENT_BYTES:
        _deny("original_balance_measurement_bound")
    return raw


def measure_original_balance_v1(chain, *, reference, scope, checked_at):
    """Reopen original bytes; checked_at is availability, not a caller owner."""
    try:
        _utc(checked_at)
        checked_bootstrap(scope, LedgerScope)
        if (
            type(chain) is not tuple
            or not chain
            or observed.source_reference(chain) != reference
        ):
            _deny("original_balance_reference_changed")
        payloads = [
            point.event.packet_payload for point in chain if point.event.packet_payload
        ]
        if len(payloads) != 1:
            _deny("original_balance_packet_required")
        packet = capture._verify_original_page_packet(
            payloads[0],
            expected_sha256=reference.packet_sha256,
            expected_plan_sha256=reference.plan_sha256,
        )
        if packet.completed_at > checked_at:
            _deny("original_balance_unavailable_at_cutoff")
        if type(packet.plan) is capture.AllProductDemoAccountCapturePlan:
            history.verify_history_query_chain(
                chain, **observed._pins(reference, scope)
            )
        elif type(packet.plan) is capture.AllProductDemoAccountCapturePlanV6:
            history.verify_algo_history_query_chain_v6(
                chain, **observed._pins(reference, scope)
            )
        elif type(packet.plan) is capture.CurrentDemoAccountCapturePlanV7:
            # Historical sample replay uses its original completion, and does
            # not renew that source's current/native lease at checked_at.
            current.verify_current_account_sources(
                chain,
                reference=reference,
                scope=scope,
                validated_at=packet.completed_at,
                expected_policy_sha256=current.V7_POLICY_SHA256,
            )
        else:
            _deny("original_balance_scope_or_packet_unsupported")
        return _measure_verified_packet_v1(packet, reference, scope)
    except OriginalBalanceMeasurementError:
        raise
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except (KeyboardInterrupt, SystemExit) as error:
        raise type(error)() from None
    except Exception:  # noqa: BLE001 -- original row/parser details stay private
        _deny()


def sampled_peak_from_original_measurements_v1(
    measurements, sources, *, scope, expected_through, expected_head
):
    """One final O(N) walk of the complete original-source membership."""
    try:
        checked_bootstrap(scope, LedgerScope)
        if (
            type(measurements) is not list
            or type(sources) is not list
            or type(expected_through) is not int
            or expected_through < 1
            or len(measurements) != len(sources)
            or len(sources) != expected_through
        ):
            _deny("original_balance_prefix_incomplete")
        journal._sha(expected_head)
        previous, first_at, last_at, identity, peak = None, None, None, None, None
        membership = hashlib.sha256()
        peak_source = peak_pin = last_available = None
        for number, (raw, source) in enumerate(
            zip(measurements, sources, strict=True), 1
        ):
            data = _read(raw)
            reference = observed.CaptureReference(**data["source_reference"])
            if (
                source["sequence"] != number
                or source["reference"] != observed.reference_document(reference)
                or data["policy_sha256"] != POLICY_SHA256
                or (
                    data["environment"],
                    data["account_id"],
                    data["settlement_currency"],
                )
                != (scope.environment, scope.account_id, scope.settlement_currency)
                or data["session_binding_sha256"] != reference.session_binding_sha256
                or data["native_clock_issued"] is not False
                or data["current_owner_issued"] is not False
            ):
                _deny("original_balance_prefix_membership_changed")
            current_identity = (
                data["account_id"],
                data["main_uid"],
                data["account_level"],
                data["position_mode"],
                data["origin"],
                data["registration_region"],
                data["environment"],
                data["settlement_currency"],
            )
            if identity is not None and current_identity != identity:
                _deny("original_balance_prefix_identity_changed")
            identity = current_identity
            at = _utc(datetime.fromisoformat(data["body_completed_at"]))
            if last_at is not None and at < last_at:
                _deny("original_balance_prefix_clock_regressed")
            first_at = at if first_at is None else first_at
            last_at = at
            equity = _rational(data["equity"], positive=True)
            available = _rational(data["available_margin"])
            if available > equity:
                _deny("original_balance_available_above_equity")
            pin = journal.digest(raw)
            if peak is None or equity > peak:
                peak, peak_source, peak_pin = equity, source["reference"], pin
            last_available = available
            membership.update(
                _MEMBER_DOMAIN
                + journal.canonical([number, previous, source["event_sha256"], pin])
                + b"\n"
            )
            previous = source["event_sha256"]
        if previous != expected_head or peak is None:
            _deny("original_balance_prefix_head_changed")
        return {
            "schema_version": PEAK_SCHEMA,
            "policy_sha256": POLICY_SHA256,
            "scope": [scope.environment, scope.account_id, scope.settlement_currency],
            "account_identity": list(identity),
            "sample_count": expected_through,
            "index_head_sha256": expected_head,
            "population": "original_index_sequence_one_through_exact_head",
            "first_measured_at": first_at.isoformat(),
            "last_measured_at": last_at.isoformat(),
            "peak_equity": _fraction(peak),
            "peak_measurement_sha256": peak_pin,
            "peak_source_reference": peak_source,
            "last_available_margin": _fraction(last_available),
            "measurement_membership_sha256": membership.hexdigest(),
            "measured_prefix_complete": True,
            "initial_account_peak": None,
            "unobserved_intermediate_peak": None,
            "historical_account_peak": None,
            "external_cashflow_adjustment": None,
            "cashflow_adjusted_drawdown": None,
            "native_clock_issued": False,
            "current_owner_issued": False,
        }
    except OriginalBalanceMeasurementError:
        raise
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except (KeyboardInterrupt, SystemExit) as error:
        raise type(error)() from None
    except Exception:  # noqa: BLE001 -- no raw source or malformed input details
        _deny("original_balance_prefix_invalid")
