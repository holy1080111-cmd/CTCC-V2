"""Exact finite financial adoption from the original E population.

This module is a data reducer and the common portfolio algebra input. Source
ownership, durable Control, native clocks and issued revisions are checked by
the native acquisition/issuer boundary; a well-formed value here grants none.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from fractions import Fraction

from app.config import settings as configured
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import (
    account_original_balance_measurement_v1 as measurement,
)
from app.trade_qualification import account_original_position_path_v1 as position_path
from app.trade_qualification import account_original_raw_proof_bank_v1 as bank
from app.trade_qualification import portfolio
from app.trade_qualification.account_ledger_claims_v2 import (
    ExactRatioV1,
    FiniteAccountLedgerClaimsV1,
    FiniteCurrentReferenceV1,
    FiniteFinancialOperandsV1,
    finite_risk_policy_sha256_v1,
)
from app.trade_qualification.hard_risk_caps import (
    MAX_PORTFOLIO_MARGIN_PCT,
    MAX_PORTFOLIO_STOP_RISK_PCT,
    MAX_SINGLE_POSITION_MARGIN_USDT,
    MAX_TRADE_RISK_PCT,
)
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

SCHEMA = "ctcc.owned_finite_financial_adoption.values.v1"
MAX_RETAINED_BYTES = bank.MAX_API_RETAINED_BYTES_V2


class OwnedFinancialValuesError(ValueError):
    """Fixed codes only; original account bytes are never exception text."""


def _deny(code):
    raise OwnedFinancialValuesError(code) from None


def _utc(value):
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _deny("owned_financial_aware_utc_required")
    return value.astimezone(UTC)


def _ratio(value):
    if value is None:
        return None
    if type(value) is not Fraction:
        _deny("owned_financial_exact_ratio_required")
    if max(value.numerator.bit_length(), value.denominator.bit_length()) > 4096:
        _deny("owned_financial_ratio_bound")
    return ExactRatioV1(
        numerator=str(value.numerator), denominator=str(value.denominator)
    )


def _read_ratio(value):
    if value is None:
        return None
    if type(value) is not ExactRatioV1:
        _deny("owned_financial_exact_ratio_required")
    result = Fraction(int(value.numerator), int(value.denominator))
    if _ratio(result) != value:
        _deny("owned_financial_ratio_not_canonical")
    return result


def _wire_ratio(value):
    if value is None:
        return None
    if type(value) is not list or len(value) != 2:
        _deny("owned_financial_original_ratio_shape")
    return _read_ratio(ExactRatioV1(numerator=value[0], denominator=value[1]))


@dataclass(frozen=True, slots=True, repr=False)
class ConfiguredOwnedRiskPolicyV1:
    risk_policy: portfolio.PortfolioRiskPolicy
    position_margin_bucket_usdt: Decimal
    selected_settings_json: bytes = field(repr=False)
    selected_settings_sha256: str
    source_authenticity_verified: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


def _configured_policy_from_settings_v1(settings, *, drawdown_window_started_at):
    """Select the actual configured conservative limits; never enable a feature.

    The window must already be the immutable measured ledger genesis. The
    native caller verifies it before acquisition and rechecks this exact
    settings projection after every await. This pure selector does not prove
    that a supplied datetime belongs to that ledger.
    """
    if type(settings) is not configured.Settings:
        _deny("owned_financial_exact_settings_required")
    window = _utc(drawdown_window_started_at)

    def number(name):
        raw = getattr(settings, name)
        if type(raw) not in (int, float, Decimal) or type(raw) is bool:
            _deny("owned_financial_config_number_invalid")
        value = Decimal(str(raw))
        if not value.is_finite() or value <= 0:
            _deny("owned_financial_config_number_invalid")
        return value

    def count(name):
        raw = getattr(settings, name)
        if type(raw) is not int or not 1 <= raw <= 2048:
            _deny("owned_financial_config_count_invalid")
        return raw

    # This is a deliberate stricter intersection of existing generic/Demo
    # limits. Neither score tiers nor continuous mode can raise these bounds.
    open_count = min(count("max_open_positions"), count("okx_demo_max_open_positions"))
    same_count = min(open_count, count("max_same_direction_positions"))
    correlated_count = min(open_count, count("max_correlated_positions"))
    order_cap = number("order_size_cap_usdt")
    bucket = min(
        number("okx_demo_position_margin_bucket_usdt"),
        Decimal(300),
    )
    with localcontext(Context(prec=100)):
        portfolio_cap = order_cap * open_count
        same_cap = order_cap * same_count
        correlated_cap = order_cap * correlated_count
    if any(
        Fraction(product) != Fraction(order_cap) * count
        for product, count in (
            (portfolio_cap, open_count),
            (same_cap, same_count),
            (correlated_cap, correlated_count),
        )
    ):
        _deny("owned_financial_config_cap_product_not_exact")
    policy = portfolio.PortfolioRiskPolicy(
        max_age_seconds=min(30, count("okx_demo_scan_max_snapshot_age_seconds")),
        drawdown_window_started_at=window,
        risk_per_trade_pct=min(number("risk_per_trade_pct"), Decimal("0.005")),
        max_daily_loss_pct=number("okx_demo_daily_loss_limit_pct"),
        max_weekly_loss_pct=number("max_weekly_loss_pct"),
        max_drawdown_pct=number("max_drawdown_pct"),
        max_consecutive_losses=min(
            count("max_consecutive_losses"),
            count("okx_demo_automation_max_consecutive_losses"),
        ),
        max_open_positions=open_count,
        max_same_direction_positions=same_count,
        max_correlated_positions=correlated_count,
        max_order_notional=order_cap,
        max_order_contracts=number("okx_demo_max_order_size_contracts"),
        max_portfolio_notional=portfolio_cap,
        max_same_direction_notional=same_cap,
        max_correlated_notional=correlated_cap,
        max_portfolio_risk_pct=min(
            number("okx_demo_portfolio_max_risk_pct"), Decimal("0.01")
        ),
        max_portfolio_margin_pct=min(
            number("okx_demo_portfolio_max_margin_pct"), Decimal("0.60")
        ),
        max_leverage=count("okx_demo_max_leverage"),
    )
    if (
        Fraction(policy.risk_per_trade_pct) > MAX_TRADE_RISK_PCT
        or Fraction(policy.max_portfolio_risk_pct) > MAX_PORTFOLIO_STOP_RISK_PCT
        or Fraction(policy.max_portfolio_margin_pct) > MAX_PORTFOLIO_MARGIN_PCT
        or Fraction(bucket) > MAX_SINGLE_POSITION_MARGIN_USDT
    ):
        _deny("owned_financial_config_hard_cap_violation")
    raw = canonical(
        {
            "schema_version": "ctcc.configured_owned_risk_policy.v1",
            "portfolio_risk_policy_sha256": finite_risk_policy_sha256_v1(policy),
            "policy": policy.model_dump(mode="json"),
            "position_margin_bucket_usdt": str(bucket),
            "configured_risk_inputs": {
                name: (
                    str(getattr(settings, name))
                    if type(getattr(settings, name)) in (float, Decimal)
                    else getattr(settings, name)
                )
                for name in (
                    "risk_per_trade_pct",
                    "max_open_positions",
                    "okx_demo_max_open_positions",
                    "max_same_direction_positions",
                    "max_correlated_positions",
                    "order_size_cap_usdt",
                    "okx_demo_position_margin_bucket_usdt",
                    "okx_demo_scan_max_snapshot_age_seconds",
                    "okx_demo_daily_loss_limit_pct",
                    "max_weekly_loss_pct",
                    "max_drawdown_pct",
                    "max_consecutive_losses",
                    "okx_demo_automation_max_consecutive_losses",
                    "okx_demo_max_order_size_contracts",
                    "okx_demo_portfolio_max_risk_pct",
                    "okx_demo_portfolio_max_margin_pct",
                    "okx_demo_max_leverage",
                )
            },
            "selection": "intersection_of_existing_generic_Demo_limits_and_fixed_hard_caps",
            "new_authority": False,
        }
    )
    return ConfiguredOwnedRiskPolicyV1(policy, bucket, raw, sha(raw))


def capture_configured_owned_risk_policy_v1(*, drawdown_window_started_at):
    """Production selection calls the existing configured settings provider."""
    return _configured_policy_from_settings_v1(
        configured.get_settings(), drawdown_window_started_at=drawdown_window_started_at
    )


def _require_same_configured_policy_v1(value):
    if type(value) is not ConfiguredOwnedRiskPolicyV1:
        _deny("owned_financial_configured_policy_required")
    current = capture_configured_owned_risk_policy_v1(
        drawdown_window_started_at=value.risk_policy.drawdown_window_started_at
    )
    if (
        current.selected_settings_json != value.selected_settings_json
        or current.selected_settings_sha256 != value.selected_settings_sha256
        or current.position_margin_bucket_usdt != value.position_margin_bucket_usdt
        or current.risk_policy != value.risk_policy
    ):
        _deny("owned_financial_original_configured_policy_changed")


@dataclass(frozen=True, slots=True, repr=False)
class OwnedFiniteFinancialValuesV1:
    financial: FiniteFinancialOperandsV1
    source_receipt_json: bytes = field(repr=False)
    measured_hwm_receipt_json: bytes = field(repr=False)
    original_window_receipt_json: bytes = field(repr=False)
    source_authenticity_verified: bool = field(default=False, init=False)
    account_complete: bool = field(default=False, init=False)
    account_revision_issued: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


def derive_owned_finite_financial_values_v1(
    population,
    *,
    scope,
    original_validated_at,
    evaluated_at,
    held_current_record,
    original_position_source,
    configured_policy,
    original_expires_at,
    original_prefix_sha256,
    policy_counter_initialization=None,
):
    """Reopen the exact E population; derive G11 windows and recorded HWM.

    A missing positive-reset proof remains unknown. This function never creates
    an initial seed, a continuous peak, an unseen funding event or an account
    revision. Native ownership and readback remain separate prerequisites.
    """
    checked_bootstrap(scope, LedgerScope)
    if type(configured_policy) is not ConfiguredOwnedRiskPolicyV1:
        _deny("owned_financial_configured_policy_required")
    original_validated_at, now, expiry = (
        _utc(value)
        for value in (original_validated_at, evaluated_at, original_expires_at)
    )
    if not original_validated_at <= now < expiry:
        _deny("owned_financial_original_expiry")
    position_path._digest(original_prefix_sha256)
    windows = bank._derive_evaluated_api_asof_windows_v1(
        population,
        scope=scope,
        original_validated_at=original_validated_at,
        evaluated_at=now,
        held_current_record=held_current_record,
        original_position_source=original_position_source,
    )
    receipt = decode(windows.receipt_json, bank.financial.MAX_COMPACT_BYTES)
    core = decode(
        population.original_raw_bank.financial_facts.receipt_json,
        bank.financial.MAX_COMPACT_BYTES,
    )
    document = decode(population.original_raw_bank.raw_source_json, bank.MAX_RAW_BYTES)
    if (
        receipt["schema_version"] != bank.FINITE_EVALUATED_WINDOWS_SCHEMA_V1
        or receipt["policy_sha256"] != bank.FINITE_EVALUATED_WINDOWS_POLICY_SHA256_V1
        or receipt["window_evaluated_at"] != now.isoformat()
        or core["index_head_sha256"] != document["index_head_sha256"]
    ):
        _deny("owned_financial_original_source_binding_changed")
    measurements = []
    for anchor in population.coupled_anchors:
        original = decode(anchor.source_reference_json, bank.MAX_RAW_BYTES)
        reference = measurement.observed.CaptureReference(**original)
        packet = capture._verify_original_page_packet(
            anchor.original_packet,
            expected_sha256=reference.packet_sha256,
            expected_plan_sha256=reference.plan_sha256,
        )
        measurements.append(
            measurement._measure_verified_packet_v1(packet, reference, scope)
        )
    peak = measurement.sampled_peak_from_original_measurements_v1(
        measurements,
        core["original_current_sources"],
        scope=scope,
        expected_through=core["through_sequence"],
        expected_head=core["index_head_sha256"],
    )
    if peak != core["sampled_balance_peak"]:
        _deny("owned_financial_original_measured_population_changed")
    window_start = _utc(datetime.fromisoformat(peak["first_measured_at"]))
    if window_start != configured_policy.risk_policy.drawdown_window_started_at:
        _deny("owned_financial_measured_genesis_window_changed")
    peak_rows = [
        raw for raw in measurements if sha(raw) == peak["peak_measurement_sha256"]
    ]
    if len(peak_rows) != 1:
        _deny("owned_financial_peak_source_membership_invalid")
    peak_row = measurement._read(peak_rows[0])
    current_row = measurement._read(measurements[-1])
    peak_at = _utc(datetime.fromisoformat(peak_row["body_completed_at"]))
    measured_current = measurement._rational(current_row["equity"], positive=True)
    measured_peak = measurement._rational(peak["peak_equity"], positive=True)
    if measured_peak is None or measured_peak < measured_current:
        _deny("owned_financial_measured_peak_invalid")
    # The selected engineering policy is the original complete measured prefix.
    # It explicitly does not make the old diagnostic continuous-peak claim.
    missing = set(receipt["missing_obligations"])
    missing.discard("continuous_peak_or_dominating_source_bound_missing")
    streak = receipt["finite_API_asof_streak"]["current_loss_streak_upper_bound"]
    streak_basis = None if streak is None else "observed_positive_reset"
    init_sha = init_at = None
    counter_raw = b""
    counter_retained = 0
    if policy_counter_initialization is not None:
        from app.trade_qualification import (
            demo_policy_counter_initialization_v1 as counter,
        )

        initialized = policy_counter_initialization
        if type(initialized) is not counter.PolicyCounterInitializationReadbackV1:
            _deny("owned_financial_exact_counter_readback_required")
        initialized.__post_init__()
        ack = initialized.ack
        if (
            ack.scope.environment != scope.environment
            or ack.scope.account_id != scope.account_id
            or ack.settlement_currency != scope.settlement_currency
            or ack.configured_policy_json.encode("ascii")
            != configured_policy.selected_settings_json
            or ack.measured_genesis_json.encode("ascii") != measurements[0]
        ):
            _deny("owned_financial_counter_original_policy_or_genesis_changed")
        init_sha, init_at = ack.initialization_sha256, initialized.initialized_at
        row = {
            "initialization_sha256": init_sha,
            "environment": ack.scope.environment,
            "account_id": ack.scope.account_id,
            "settlement_currency": ack.settlement_currency,
            "command_id": ack.command_id,
            "control_revision": ack.control_revision,
            "control_event_sha256": ack.control_event_sha256,
            "control_state_sha256": ack.control_state_sha256,
            "source_lineage_sha256": ack.source_lineage_sha256,
            "account_revision": ack.account_revision,
            "ledger_revision": ack.ledger_revision,
            "claims_sha256": ack.claims_sha256,
            "configured_policy_sha256": sha(ack.configured_policy_json.encode("ascii")),
            "configured_policy_json": ack.configured_policy_json,
            "measured_genesis_sha256": sha(ack.measured_genesis_json.encode("ascii")),
            "measured_genesis_json": ack.measured_genesis_json,
            "owner_sampled_at": ack.owner_sampled_at.isoformat(),
            "original_expires_at": ack.original_expires_at.isoformat(),
            "ack_json": counter.canonical(counter.policy_counter_ack_document_v1(ack)),
            "initialized_at": init_at.isoformat(),
            "db_recorded_at": initialized.db_recorded_at.isoformat(),
        }
        if canonical(row) != initialized.row_json:
            _deny("owned_financial_counter_original_row_bytes_changed")
        streak, counter_raw, counter_missing = (
            bank._derive_original_policy_counter_bound_v1(
                windows,
                scope=scope,
                original_validated_at=original_validated_at,
                held_current_record=held_current_record,
                original_position_source=original_position_source,
                initialized_at=init_at,
                initialization_sha256=init_sha,
            )
        )
        streak_basis = "explicit_operator_policy_counter"
        # The old global seed/reset statements remain in original receipts;
        # this explicitly selected counter policy has its own exact obligations.
        missing.discard("positive_reset_or_original_streak_seed_missing")
        missing.discard("finite_API_asof_complete_reset_or_original_seed_missing")
        missing.update(counter_missing)
        counter_retained = (
            len(initialized.row_json)
            + len(ack.configured_policy_json.encode("ascii"))
            + len(ack.measured_genesis_json.encode("ascii"))
            + len(counter_raw)
        )
    hwm_raw = canonical(
        {
            "schema_version": "ctcc.owned_measured_prefix_HWM.values.v1",
            "policy_sha256": measurement.POLICY_SHA256,
            "original_peak_receipt": peak,
            "peak_measurement_sha256": sha(peak_rows[0]),
            "current_measurement_sha256": sha(measurements[-1]),
            "measured_genesis_sha256": sha(measurements[0]),
            "measured_genesis_json": measurements[0].decode("ascii"),
            "measured_current_equity": position_path._fraction(measured_current),
            "window_started_at": window_start.isoformat(),
            "configured_policy_sha256": configured_policy.selected_settings_sha256,
            "actual_continuous_peak": None,
            "cashflow_adjusted_peak": None,
            "native_custody_readback": False,
        }
    )
    source_raw = canonical(
        {
            "schema_version": SCHEMA,
            "evaluated_at": now.isoformat(),
            "original_validated_at": original_validated_at.isoformat(),
            "original_expires_at": expiry.isoformat(),
            "configured_policy_sha256": configured_policy.selected_settings_sha256,
            "original_native_prefix_sha256": original_prefix_sha256,
            "original_index_head_sha256": core["index_head_sha256"],
            "position_margin_bucket_usdt": str(
                configured_policy.position_margin_bucket_usdt
            ),
            "evaluated_window_receipt_sha256": sha(windows.receipt_json),
            "measured_hwm_receipt_sha256": sha(hwm_raw),
            "loss_streak_basis": streak_basis,
            "init_ack_sha256": init_sha,
            "init_at": None if init_at is None else init_at.isoformat(),
            "policy_counter_receipt_sha256": None
            if not counter_raw
            else sha(counter_raw),
            "account_complete": False,
            "account_revision_issued": False,
            "execution_authority": False,
        }
    )
    retained = receipt["derivation_retained_original_bytes_before_receipt"]
    # Charge the original bank's accounted population plus every new result
    # buffer and every reconstructed measurement. Never truncate a population.
    total = (
        retained
        + counter_retained
        + sum(
            len(raw)
            for raw in (windows.receipt_json, source_raw, hwm_raw, *measurements)
        )
    )
    if (
        type(retained) is not int
        or retained < 0
        or total > MAX_RETAINED_BYTES
        or len(source_raw) > bank.financial.MAX_COMPACT_BYTES
        or len(hwm_raw) > bank.financial.MAX_COMPACT_BYTES
    ):
        _deny("owned_financial_total_retained_byte_bound")
    reference = held_current_record.reference
    financial = FiniteFinancialOperandsV1(
        financial_schema="ctcc.account_financial_operands.finite_api_asof.v1",
        source_as_of_at=datetime.fromisoformat(
            receipt["as_of_original_risk_source_at"]
        ),
        available_at=datetime.fromisoformat(receipt["available_at"]),
        evaluated_at=now,
        daily_window_started_at=now.replace(hour=0, minute=0, second=0, microsecond=0),
        weekly_window_started_at=now - timedelta(days=7),
        daily_net_pnl=_ratio(
            _wire_ratio(receipt["source_windows"]["UTC_day"]["exact_full_net_PnL"])
        ),
        weekly_net_pnl=_ratio(
            _wire_ratio(receipt["source_windows"]["rolling_7d"]["exact_full_net_PnL"])
        ),
        current_loss_streak_upper_bound=streak,
        loss_streak_basis=streak_basis,
        init_ack_sha256=init_sha,
        init_at=init_at,
        original_start_seed=None,
        measured_hwm_window_started_at=window_start,
        measured_peak_equity=_ratio(measured_peak),
        measured_peak_observed_at=peak_at,
        measured_current_equity=_ratio(measured_current),
        measured_hwm_policy_sha256=measurement.POLICY_SHA256,
        position_margin_bucket_usdt=configured_policy.position_margin_bucket_usdt,
        drawdown_basis="every_accepted_measured_balance_in_original_window",
        actual_continuous_peak=None,
        cashflow_adjusted_peak=None,
        original_expires_at=expiry,
        bank_window_policy_sha256=bank.FINITE_EVALUATED_WINDOWS_POLICY_SHA256_V1,
        current_source_reference=FiniteCurrentReferenceV1(
            **measurement.observed.reference_document(reference)
        ),
        original_prefix_sha256=original_prefix_sha256,
        original_raw_bank_sha256=population.original_raw_bank.raw_source_sha256,
        api_population_receipt_sha256=sha(population.receipt_json),
        source_receipt_sha256=sha(source_raw),
        measured_hwm_receipt_sha256=sha(hwm_raw),
        original_risk_policy_sha256=finite_risk_policy_sha256_v1(
            configured_policy.risk_policy
        ),
        missing_obligations=tuple(sorted(missing)),
    )
    return OwnedFiniteFinancialValuesV1(
        financial, source_raw, hwm_raw, windows.receipt_json
    )


@dataclass(frozen=True, slots=True)
class _FiniteEvaluationV1:
    scope: LedgerScope
    checked_at: datetime
    started_at: datetime
    ended_at: datetime
    daily_net_pnl: Fraction | None
    weekly_net_pnl: Fraction | None
    loss_streak: int | None
    measured_peak: Fraction | None
    measured_current: Fraction | None
    position_margin_bucket: Fraction
    receipt_json: bytes = field(repr=False)
    missing_obligations: tuple[str, ...]


def _checked_finite_evaluation_v1(claims, *, evaluated_at):
    if type(claims) is not FiniteAccountLedgerClaimsV1:
        _deny("owned_financial_exact_finite_claims_required")
    financial = claims.financial
    now = _utc(evaluated_at)
    if (
        financial.evaluated_at != now
        or financial.daily_window_started_at
        != now.replace(hour=0, minute=0, second=0, microsecond=0)
        or financial.weekly_window_started_at != now - timedelta(days=7)
        or claims.account.history_end != financial.source_as_of_at
        or financial.original_risk_policy_sha256
        != finite_risk_policy_sha256_v1(claims.risk_policy)
        or not financial.available_at <= now < financial.original_expires_at
        or financial.bank_window_policy_sha256
        != bank.FINITE_EVALUATED_WINDOWS_POLICY_SHA256_V1
        or financial.measured_hwm_policy_sha256 != measurement.POLICY_SHA256
    ):
        _deny("owned_financial_evaluation_window_or_policy_changed")
    missing = set(financial.missing_obligations)
    if financial.measured_hwm_receipt_sha256 is None:
        missing.add("original_measured_hwm_readback_missing")
    return _FiniteEvaluationV1(
        claims.scope,
        now,
        claims.account.history_start,
        claims.account.history_end,
        _read_ratio(financial.daily_net_pnl),
        _read_ratio(financial.weekly_net_pnl),
        financial.current_loss_streak_upper_bound,
        _read_ratio(financial.measured_peak_equity),
        _read_ratio(financial.measured_current_equity),
        Fraction(financial.position_margin_bucket_usdt),
        canonical(financial.model_dump(mode="json")),
        tuple(sorted(missing)),
    )
