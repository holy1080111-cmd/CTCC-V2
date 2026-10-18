"""Bounded, DENY-only measured-HWM calculation for a future durable B5 index.

This v2 membership is deliberately separate from the v1 SHA256 of a complete
concatenated population. One append reads a predecessor projection, its genesis
and selected-peak witnesses, and the new source measurement. It does not attest
that those values came from the database or replace the v1 runtime replay.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_materializer as mapping
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_portfolio_components as components
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

SCHEMA_VERSION = "ctcc.measured_balance_hwm_continuation.v2"
POLICY_BYTES = journal.canonical(
    {
        "version": SCHEMA_VERSION,
        "v1_component_policy_sha256": components.POLICY_SHA256,
        "genesis": "unchanged_accepted_sequence_one_balance_body_completion",
        "population": "contiguous_accepted_observation_batch_sequence",
        "membership": "domain_separated_sha256_of_predecessor_and_exact_new_member",
        "prior_witnesses": "original_genesis_and_selected_peak_required_each_append",
        "durable_membership_proof": "unimplemented",
        "native_clock_proof": "unimplemented",
        "account_complete": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)
_MEMBERSHIP_DOMAIN = b"ctcc.measured_balance_hwm_continuation.v2\x00"
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_FIELDS = frozenset(
    {
        "schema_version",
        "policy_sha256",
        "environment",
        "account_id",
        "settlement_currency",
        "sample_count",
        "through_sequence",
        "head_sha256",
        "previous_head_sha256",
        "previous_projection_sha256",
        "last_measurement_sha256",
        "last_source_reference",
        "genesis_measurement_sha256",
        "genesis_source_reference",
        "peak_measurement_sha256",
        "peak_source_reference",
        "window_started_at",
        "last_measured_at",
        "peak_observed_at",
        "peak_equity",
        "membership_sha256",
        "all_sources_recorded_native",
        "unsampled_intermediate_peak",
        "durable_membership_verified",
        "native_clock_verified",
        "account_complete",
        "execution_authority",
        "admission",
    }
)


def _deny(code):
    raise components.PortfolioComponentError(code)


def _sha(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        _deny("component_hwm_v2_digest_invalid")
    return value


def _time(value):
    if type(value) is not str:
        _deny("component_hwm_v2_time_invalid")
    try:
        checked = capture._utc(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        _deny("component_hwm_v2_time_invalid")
    if checked.isoformat() != value:
        _deny("component_hwm_v2_time_invalid")
    return checked


def _equity(value):
    if (
        type(value) is not dict
        or set(value) != {"numerator", "denominator"}
        or any(
            type(value.get(key)) is not str or _DECIMAL.fullmatch(value[key]) is None
            for key in ("numerator", "denominator")
        )
    ):
        _deny("component_hwm_v2_equity_invalid")
    result = (
        Fraction(int(value["numerator"]), int(value["denominator"]))
        if value["denominator"] != "0"
        else None
    )
    if result is None or result <= 0 or _fraction(result) != value:
        _deny("component_hwm_v2_equity_invalid")
    return result


def _fraction(value):
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


@dataclass(frozen=True, slots=True, repr=False)
class HWMContinuation:
    """Calculation bytes only; an instance or its hash never grants ownership."""

    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def execution_authority(self):
        return False


def _measurement(value, scope):
    if type(value) is not components.BalanceMeasurement:
        _deny("component_hwm_v2_original_measurement_required")
    raw = value.source_json
    if type(raw) is not bytes or not 1 <= len(raw) <= 1048576:
        _deny("component_hwm_v2_measurement_bound")
    try:
        data = json.loads(raw)
        reference = observed.CaptureReference(**data["source_reference"])
        if (
            journal.canonical(data) != raw
            or data["schema_version"] != "ctcc.measured_balance.v1"
            or data["source_reference"] != observed.reference_document(reference)
            or (data["environment"], data["account_id"], data["settlement_currency"])
            != (scope.environment, scope.account_id, scope.settlement_currency)
            or data["current_source_authority"] is not False
            or type(data["recorded_native_transport"]) is not bool
            or type(data["raw_row_json"]) is not str
            or journal.digest(data["raw_row_json"].encode()) != data["raw_row_sha256"]
        ):
            _deny("component_hwm_v2_measurement_invalid")
        measured = _time(data["body_completed_at"])
        equity = _equity(data["equity"])
        requested, headers = (
            _time(data[key]) for key in ("request_started_at", "headers_received_at")
        )
        updates = [
            mapping._time(data[key]) for key in ("account_uTime", "settlement_uTime")
        ]
        if not requested <= headers <= measured or any(
            update is None or update > measured for update in updates
        ):
            _deny("component_hwm_v2_measurement_invalid")
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        _deny("component_hwm_v2_measurement_invalid")
    return data, journal.digest(raw), measured, equity


def _prior(value, scope):
    if (
        type(value) is not HWMContinuation
        or type(value.receipt_json) is not bytes
        or not 1 <= len(value.receipt_json) <= 1048576
    ):
        _deny("component_hwm_v2_predecessor_required")
    try:
        data = json.loads(value.receipt_json)
        if (
            journal.canonical(data) != value.receipt_json
            or set(data) != _FIELDS
            or data["schema_version"] != SCHEMA_VERSION
            or data["policy_sha256"] != POLICY_SHA256
            or (data["environment"], data["account_id"], data["settlement_currency"])
            != (scope.environment, scope.account_id, scope.settlement_currency)
            or type(data["sample_count"]) is not int
            or data["sample_count"] < 1
            or data["sample_count"] != data["through_sequence"]
            or type(data["through_sequence"]) is not int
            or data["through_sequence"] >= 9223372036854775807
            or data["unsampled_intermediate_peak"] != "unknown"
            or type(data["all_sources_recorded_native"]) is not bool
            or any(
                data[k] is not False
                for k in (
                    "durable_membership_verified",
                    "native_clock_verified",
                    "account_complete",
                    "execution_authority",
                )
            )
            or data["admission"] != "DENY"
        ):
            _deny("component_hwm_v2_predecessor_invalid")
        for key in (
            "head_sha256",
            "last_measurement_sha256",
            "genesis_measurement_sha256",
            "peak_measurement_sha256",
            "membership_sha256",
        ):
            _sha(data[key])
        for key in (
            "last_source_reference",
            "genesis_source_reference",
            "peak_source_reference",
        ):
            reference = observed.CaptureReference(**data[key])
            if data[key] != observed.reference_document(reference):
                _deny("component_hwm_v2_predecessor_invalid")
        if data["sample_count"] == 1:
            if (
                data["previous_head_sha256"] is not None
                or data["previous_projection_sha256"] is not None
            ):
                _deny("component_hwm_v2_predecessor_invalid")
            if (
                data["last_measurement_sha256"] != data["genesis_measurement_sha256"]
                or data["peak_measurement_sha256"] != data["genesis_measurement_sha256"]
                or data["last_source_reference"] != data["genesis_source_reference"]
                or data["peak_source_reference"] != data["genesis_source_reference"]
            ):
                _deny("component_hwm_v2_predecessor_invalid")
        else:
            _sha(data["previous_head_sha256"])
            _sha(data["previous_projection_sha256"])
        first, last, peak_at = (
            _time(data[k])
            for k in ("window_started_at", "last_measured_at", "peak_observed_at")
        )
        if not first <= peak_at <= last:
            _deny("component_hwm_v2_predecessor_invalid")
        _equity(data["peak_equity"])
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        _deny("component_hwm_v2_predecessor_invalid")
    return data


def advance_measured_hwm(
    scope,
    *,
    sequence,
    previous_event_sha256,
    event_sha256,
    measurement,
    expected_reference,
    predecessor=None,
    expected_predecessor_sha256=None,
    genesis_witness=None,
    selected_peak_witness=None,
):
    """O(1) calculation after externally verified, immutable source readback.

    Missing persistence/membership/native-clock proofs keep every output DENY.
    The witnesses must be replayed original B1 measurements by a future owned
    repository; supplying objects here does not make them trusted.
    """
    checked_bootstrap(scope, LedgerScope)
    if type(sequence) is not int or not 1 <= sequence <= 9223372036854775807:
        _deny("component_hwm_v2_sequence_invalid")
    _sha(event_sha256)
    if type(expected_reference) is not observed.CaptureReference:
        _deny("component_hwm_v2_original_reference_required")
    expected_reference_document = observed.reference_document(expected_reference)
    source, measurement_sha, measured, equity = _measurement(measurement, scope)
    if source["source_reference"] != expected_reference_document:
        _deny("component_hwm_v2_original_reference_mismatch")
    if sequence == 1:
        if (
            predecessor is not None
            or previous_event_sha256 is not None
            or expected_predecessor_sha256 is not None
            or genesis_witness is not None
            or selected_peak_witness is not None
        ):
            _deny("component_hwm_v2_genesis_invalid")
        prior_sha = prior_membership = None
        first, last, peak, peak_at = measured, measured, equity, measured
        genesis_sha = peak_sha = measurement_sha
        genesis_reference = peak_reference = expected_reference_document
        native = source["recorded_native_transport"]
    else:
        if type(previous_event_sha256) is not str:
            _deny("component_hwm_v2_predecessor_required")
        _sha(previous_event_sha256)
        _sha(expected_predecessor_sha256)
        prior = _prior(predecessor, scope)
        if (
            predecessor.receipt_sha256 != expected_predecessor_sha256
            or prior["through_sequence"] != sequence - 1
            or prior["head_sha256"] != previous_event_sha256
            or type(genesis_witness) is not components.BalanceMeasurement
            or type(selected_peak_witness) is not components.BalanceMeasurement
        ):
            _deny("component_hwm_v2_predecessor_mismatch")
        genesis_source, genesis_sha, first, _ = _measurement(genesis_witness, scope)
        peak_source, peak_sha, peak_at, peak = _measurement(
            selected_peak_witness, scope
        )
        if (
            prior["genesis_measurement_sha256"] != genesis_sha
            or prior["genesis_source_reference"] != genesis_source["source_reference"]
            or prior["window_started_at"] != first.isoformat()
            or prior["peak_measurement_sha256"] != peak_sha
            or prior["peak_source_reference"] != peak_source["source_reference"]
            or prior["peak_observed_at"] != peak_at.isoformat()
            or prior["peak_equity"] != _fraction(peak)
            or measured < _time(prior["last_measured_at"])
        ):
            _deny("component_hwm_v2_witness_or_chronology_mismatch")
        genesis_reference = prior["genesis_source_reference"]
        peak_reference = prior["peak_source_reference"]
        last = measured
        if equity > peak:
            peak, peak_at, peak_sha = equity, measured, measurement_sha
            peak_reference = expected_reference_document
        prior_sha = predecessor.receipt_sha256
        prior_membership = prior["membership_sha256"]
        native = (
            prior["all_sources_recorded_native"] and source["recorded_native_transport"]
        )
    member = journal.canonical(
        [sequence, previous_event_sha256, event_sha256, measurement_sha]
    )
    membership = journal.digest(
        _MEMBERSHIP_DOMAIN
        + (b"" if prior_membership is None else bytes.fromhex(prior_membership))
        + member
    )
    return HWMContinuation(
        journal.canonical(
            {
                "schema_version": SCHEMA_VERSION,
                "policy_sha256": POLICY_SHA256,
                "environment": scope.environment,
                "account_id": scope.account_id,
                "settlement_currency": scope.settlement_currency,
                "sample_count": sequence,
                "through_sequence": sequence,
                "head_sha256": event_sha256,
                "previous_head_sha256": previous_event_sha256,
                "previous_projection_sha256": prior_sha,
                "last_measurement_sha256": measurement_sha,
                "last_source_reference": expected_reference_document,
                "genesis_measurement_sha256": genesis_sha,
                "genesis_source_reference": genesis_reference,
                "peak_measurement_sha256": peak_sha,
                "peak_source_reference": peak_reference,
                "window_started_at": first.isoformat(),
                "last_measured_at": last.isoformat(),
                "peak_observed_at": peak_at.isoformat(),
                "peak_equity": _fraction(peak),
                "membership_sha256": membership,
                "all_sources_recorded_native": native,
                "unsampled_intermediate_peak": "unknown",
                "durable_membership_verified": False,
                "native_clock_verified": False,
                "account_complete": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
    )
