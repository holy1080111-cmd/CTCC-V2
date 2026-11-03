"""Versioned controlled-bank measured HWM record DATA, never an owner.

The native current55 collector replays its SAME original source before and
after actual append/readback. This builder cannot authenticate a supplied DTO
or promote the original V4 schema/policy. Unknown account peaks stay unknown.
"""

from datetime import datetime

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import (
    account_original_balance_measurement_v1 as measurement,
)
from app.trade_qualification import account_owned_financial_v1 as financial
from app.trade_qualification import portfolio

SCHEMA = "ctcc.native_owned_controlled_balance_peak_record.v2"
POLICY_BYTES = '{"authority":"never_account_revision_or_trade","cashflow_adjusted_peak":null,"continuous_peak":null,"current_equity":"latest_accepted_original_measured_balance_separate_from_coupled_risk_anchor","measurement":"ctcc.original_v5_v6_v7.balance_measurement.v1","original_expiry":"min_original_CURRENT_history_Control_UTC_and_monotonic","population":"every_original_index_sequence_one_through_exact_head","source":"same_original_controlled_CURRENT_first_API_population_v3","version":"ctcc.native_owned_controlled_balance_peak.policy.v2"}'.encode(
    "ascii"
)
POLICY_SHA256 = "f82775c69bef1e9c70ec4607b1497ea7b3264817970879c825ff76d18b78b047"
MAX_RECORD_BYTES = 2 * 1024 * 1024


def _deny():
    raise ValueError("controlled_hwm_original_values_invalid") from None


def build_controlled_hwm_record_v2(values, record, *, owner_sampled_at):
    """Finite inert bytes; native owner/custody/source checks are independent."""
    if (
        type(values) is not financial.OwnedFiniteFinancialValuesV1
        or type(record) is not current.RecordedCurrentAccountBundleV4
        or sha(POLICY_BYTES) != POLICY_SHA256
        or canonical(decode(POLICY_BYTES)) != POLICY_BYTES
    ):
        _deny()
    facts = values.financial
    at = portfolio._utc(owner_sampled_at)
    if (
        type(owner_sampled_at) is not datetime
        or not facts.evaluated_at <= at < facts.original_expires_at <= record.expires_at
        or facts.current_source_reference.model_dump(mode="python")
        != observed.reference_document(record.reference)
        or facts.measured_hwm_policy_sha256 != measurement.POLICY_SHA256
        or facts.measured_hwm_receipt_sha256 != sha(values.measured_hwm_receipt_json)
        or facts.source_receipt_sha256 != sha(values.source_receipt_json)
        or any(
            value is None
            for value in (
                facts.measured_peak_equity,
                facts.measured_current_equity,
                facts.measured_peak_observed_at,
                facts.measured_hwm_receipt_sha256,
            )
        )
    ):
        _deny()
    # Retain exact canonical receipt strings; JSONB serialization is never used
    # to reconstruct their original bytes or hashes.
    for raw in (values.measured_hwm_receipt_json, values.source_receipt_json):
        if type(raw) is not bytes or canonical(decode(raw)) != raw:
            _deny()
    source = {
        "scope": [
            record.packet.plan.environment,
            record.packet.plan.expected_uid,
            record.packet.plan.settlement_currency,
        ],
        "current_source_reference": observed.reference_document(record.reference),
        "current_proof_envelope_sha256": record.proof_envelope_sha256,
        "original_request_expires_at": facts.original_expires_at.isoformat(),
        "original_prefix_sha256": facts.original_prefix_sha256,
        "original_raw_bank_sha256": facts.original_raw_bank_sha256,
        "api_population_receipt_sha256": facts.api_population_receipt_sha256,
        "measured_hwm_policy_sha256": facts.measured_hwm_policy_sha256,
        "measured_hwm_receipt_json": values.measured_hwm_receipt_json.decode("ascii"),
        "measured_hwm_receipt_sha256": facts.measured_hwm_receipt_sha256,
        "financial_source_receipt_json": values.source_receipt_json.decode("ascii"),
        "financial_source_receipt_sha256": facts.source_receipt_sha256,
        "measured_peak_equity": facts.measured_peak_equity.model_dump(mode="python"),
        "measured_current_equity": facts.measured_current_equity.model_dump(
            mode="python"
        ),
        "measured_peak_observed_at": facts.measured_peak_observed_at.isoformat(),
        "drawdown_window_started_at": facts.measured_hwm_window_started_at.isoformat(),
        "current_balance_basis": "latest_measured_original_index_member",
    }
    raw = canonical(
        {
            "schema_version": SCHEMA,
            "policy_sha256": POLICY_SHA256,
            "observed_source": source,
            "owner_sampled_at": at.isoformat(),
            "initial_account_peak": None,
            "historical_account_peak": None,
            "external_cashflow_adjustment": None,
            "cashflow_adjusted_drawdown": None,
            "account_complete": False,
            "account_revision_issued": False,
            "execution_authority": False,
        }
    )
    if not 0 < len(raw) <= MAX_RECORD_BYTES:
        _deny()
    return raw
