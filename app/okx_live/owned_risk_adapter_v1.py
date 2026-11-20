"""Exact Live source-to-shared-kernel arithmetic, with no positive authority.

The owned coordinator must authenticate every argument. These ordinary DATA
values cannot create its private operator, qualification or transport owners.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from app.database.repositories.native_live_financial_epoch_v1 import (
    LiveFinancialWindowFactsV1,
)
from app.domain import native_live_financial_epoch_schema_v1 as epoch_schema
from app.domain.source_primitives import canonical, sha, utc_from_ns
from app.exchange.okx.live_account_current_chain_v1 import _rows
from app.okx_live import finite_portfolio_v1 as finite
from app.trade_qualification import engine
from app.trade_qualification.account_ledger_claims_v2 import ExactRatioV1
from app.trade_qualification.economics import EconomicsPolicy
from app.trade_qualification.native_original_bracket_policy_v1 import (
    fixed_native_original_bracket_policy,
)
from app.trade_qualification.native_original_g1_policy_v1 import (
    fixed_native_original_g1_policy,
)
from app.trade_qualification.portfolio import (
    ContractRiskSpec,
    LiveEvidenceStamp,
    LiveRiskAuthority,
    PortfolioRiskPolicy,
)
from app.trade_qualification.service import QualificationPrefixPolicy


def _need(value):
    if value is not True:
        raise ValueError("native_live_risk_source_adapter_refused") from None


def _number(row, name):
    raw = row[name]
    _need(type(raw) is str and len(raw) <= 80)
    value = Decimal(raw)
    _need(value.is_finite() and value > 0)
    return value


def _exact_decimal(value):
    from decimal import Context, localcontext

    _need(type(value) is Fraction)
    with localcontext(Context(prec=256)):
        result = Decimal(value.numerator) / Decimal(value.denominator)
    _need(Fraction(result) == value)
    return result


def _ratio(value):
    _need(type(value) is Fraction)
    return ExactRatioV1(
        numerator=str(value.numerator), denominator=str(value.denominator)
    )


@dataclass(frozen=True, slots=True)
class LiveInstrumentDataV1:
    spec: ContractRiskSpec
    tick_size: Decimal
    contracts: Decimal
    leverage: int
    position_side: str
    execution_authority: bool = False


def live_instrument_data_v1(view, *, instrument_id, direction, settings):
    """Only supported linear SWAP/ctMult1; never change venue leverage here."""
    pages = [
        p
        for p in view["current_pages"]
        if p.selector.stream == "account_instruments"
        and p.selector.instrument_id == instrument_id
    ]
    _need(len(pages) == 1 and direction in ("long", "short"))
    rows = _rows(pages[0].raw_body)
    _need(len(rows) == 1)
    row = rows[0]
    base = instrument_id.split("-", 1)[0]
    _need(
        row["instId"] == instrument_id
        and row["instType"] == "SWAP"
        and row["ctType"] == "linear"
        and row["settleCcy"] == "USDT"
        and row["state"] == "live"
        and _number(row, "ctMult") == 1
        and row["ctValCcy"] == base
    )
    side = "net" if view["account_config"].position_mode == "net_mode" else direction
    leverage_rows = [
        r
        for p in view["current_pages"]
        if p.selector.stream == "leverage_isolated"
        for r in _rows(p.raw_body)
        if r.get("instId") == instrument_id
        and r.get("mgnMode") == "isolated"
        and r.get("posSide") in (("", "net") if side == "net" else (side,))
    ]
    _need(len(leverage_rows) == 1)
    leverage = _number(leverage_rows[0], "lever")
    _need(
        leverage == int(leverage)
        and 1 <= leverage <= min(3, settings.okx_live_max_leverage)
    )
    lot, minimum, maximum = (
        _number(row, key) for key in ("lotSz", "minSz", "maxLmtSz")
    )
    _need(
        (Fraction(minimum) / Fraction(lot)).denominator == 1
        and minimum <= maximum
        and minimum <= settings.okx_live_max_order_size_contracts
    )
    spec = ContractRiskSpec(
        instrument_id=instrument_id,
        base_currency=base,
        settlement_currency="USDT",
        contract_kind="linear_base",
        contract_value_currency=row["ctValCcy"],
        contract_value=_number(row, "ctVal"),
        lot_size=lot,
        min_contracts=minimum,
        max_contracts=maximum,
        max_leverage=int(leverage),
        correlation_group="crypto_usdt_swap",
        source_sha256=sha(canonical([pages[0].raw_sha256, pages[0].page_sha256])),
        observed_at=utc_from_ns(pages[0].stamps[3].utc_ns),
        received_at=utc_from_ns(view["current_completed"].utc_ns),
    )
    return LiveInstrumentDataV1(
        spec, _number(row, "tickSz"), minimum, int(leverage), side
    )


def fixed_live_qualification_policy_v1(instrument, *, epoch_started_at, settings):
    """Canonical conservative kernel policies, fixed before the candidate."""
    _need(
        type(instrument) is LiveInstrumentDataV1
        and settings.okx_live_require_protection is True
        and settings.okx_live_require_ip_bound_key is True
        and settings.okx_live_forbid_withdraw_permission is True
        and settings.okx_live_require_flat_start is True
        and settings.okx_live_auto_disarm is True
    )
    risk = PortfolioRiskPolicy(
        max_age_seconds=5,
        drawdown_window_started_at=epoch_started_at,
        risk_per_trade_pct=Decimal("0.005"),
        max_daily_loss_pct=min(
            Decimal("0.01"), settings.okx_live_session_loss_limit_pct
        ),
        max_weekly_loss_pct=Decimal("0.02"),
        max_drawdown_pct=Decimal("0.03"),
        max_consecutive_losses=3,
        max_open_positions=1,
        max_same_direction_positions=1,
        max_correlated_positions=1,
        max_order_notional=min(Decimal(25), settings.okx_live_max_notional_usdt),
        max_order_contracts=min(
            instrument.spec.max_contracts, settings.okx_live_max_order_size_contracts
        ),
        max_portfolio_notional=Decimal(25),
        max_same_direction_notional=Decimal(25),
        max_correlated_notional=Decimal(25),
        max_portfolio_risk_pct=Decimal("0.01"),
        max_portfolio_margin_pct=Decimal("0.60"),
        max_leverage=min(3, settings.okx_live_max_leverage),
    )
    return engine.PreEvidencePolicy(
        policy_id="ctcc-native-live-micro-fixed-v1",
        prefix=QualificationPrefixPolicy(
            policy_id="ctcc-native-live-micro-prefix-v1",
            data=fixed_native_original_g1_policy(),
            minimum_score=max(85, settings.strategy_min_score),
            tick_size=instrument.tick_size,
            max_allowed_drift_bps=min(
                Decimal(30), settings.okx_live_scan_max_entry_drift_bps
            ),
            maximum_strategy_spread_bps=Decimal(3),
            maximum_adverse_funding_bps=Decimal(5),
        ),
        protection=engine.ProtectionPolicy(
            policy_id="ctcc-native-live-micro-bracket-v1",
            **fixed_native_original_bracket_policy(),
        ),
        economics=EconomicsPolicy(
            policy_id="ctcc-native-live-micro-economics-v1",
            round_trip_fee_bps=Decimal(12),
            round_trip_slippage_bps=Decimal(13),
            funding_buffer_bps=Decimal(5),
            funding_periods=1,
            minimum_net_rr=Decimal(2),
            maximum_spread_bps=Decimal(3),
            maximum_funding_bps=Decimal(5),
            maximum_quote_age_seconds=5,
        ),
        portfolio=risk,
    )


def live_finite_inputs_data_v1(
    *,
    facts,
    instrument,
    policy,
    control_row,
    evaluated_at,
    deadline,
    local_stamp,
    local_active_count,
):
    """Rewindow actual signed cash flows at G11 time; never invent an HWM seed."""
    _need(
        type(facts) is LiveFinancialWindowFactsV1
        and facts.missing_obligations == ()
        and type(evaluated_at) is datetime
        and evaluated_at.tzinfo is UTC
        and facts.history_start <= evaluated_at - timedelta(days=7)
        and facts.source_cutoff_at
        <= facts.observed_at
        <= facts.available_at
        <= evaluated_at
        < deadline
        and local_active_count == 0
        and type(local_active_count) is int
        and type(local_stamp) is LiveEvidenceStamp
        and policy.portfolio.drawdown_window_started_at == facts.epoch_started_at
    )
    _need(
        (
            control_row["environment"],
            control_row["account_uid"],
            control_row["main_uid"],
            control_row["credential_session_sha256"],
            control_row["source_revision_sha256"],
        )
        == (
            "live",
            facts.account_uid,
            facts.main_uid,
            facts.credential_session_sha256,
            facts.source_revision_sha256,
        )
        and control_row["armed"] is True
        and control_row["emergency_stop"] is False
        and control_row["arm_expires_at"] >= deadline
    )
    stamp = LiveEvidenceStamp(
        environment="live",
        account_id=facts.account_uid,
        complete=True,
        source_sha256=sha(facts.receipt_json),
        observed_at=facts.observed_at,
        received_at=facts.available_at,
    )
    account = finite.LiveFinitePortfolioSnapshotV1(
        account_id=facts.account_uid,
        settlement_currency="USDT",
        balance_stamp=stamp,
        positions_stamp=stamp,
        history_stamp=stamp,
        reservations_stamp=local_stamp,
        equity=_exact_decimal(facts.measured_current),
        available_margin=_exact_decimal(facts.available_margin),
        positions=(),
        pending_reservations=(),
        position_count=0,
        pending_reservation_count=0,
        history_start=facts.history_start,
        history_end=facts.observed_at,
        original_start_seed=None,
    )
    day = evaluated_at.replace(hour=0, minute=0, second=0, microsecond=0)
    week = evaluated_at - timedelta(days=7)
    _need(
        all(
            facts.history_start <= c.at <= facts.source_cutoff_at
            for c in facts.cashflows
        )
    )
    financial = finite.LiveFinancialOperandsV1(
        account_uid=facts.account_uid,
        main_uid=facts.main_uid,
        credential_session_sha256=facts.credential_session_sha256,
        source_revision_sha256=facts.source_revision_sha256,
        epoch_id=facts.epoch_id,
        epoch_policy_sha256=epoch_schema.POLICY_SHA256,
        epoch_started_at=facts.epoch_started_at,
        sample_sequence=facts.sequence,
        source_as_of_at=facts.observed_at,
        available_at=facts.available_at,
        evaluated_at=evaluated_at,
        daily_window_started_at=day,
        weekly_window_started_at=week,
        daily_net_pnl=_ratio(
            sum(
                (c.net for c in facts.cashflows if day <= c.at <= evaluated_at),
                Fraction(),
            )
        ),
        weekly_net_pnl=_ratio(
            sum(
                (c.net for c in facts.cashflows if week <= c.at <= evaluated_at),
                Fraction(),
            )
        ),
        prospective_loss_streak=facts.loss_streak,
        original_start_seed=None,
        measured_peak_equity=_ratio(facts.measured_peak),
        measured_current_equity=_ratio(facts.measured_current),
        measured_peak_observed_at=facts.peak_observed_at,
        drawdown_basis="all_durable_original_samples_in_prospective_live_epoch",
        actual_continuous_peak=None,
        position_margin_bucket_usdt=Decimal(300),
        original_expires_at=deadline,
        source_receipt_sha256=sha(facts.receipt_json),
        missing_obligations=(),
    )
    authority = LiveRiskAuthority(
        stamp=local_stamp,
        environment="live",
        live_enabled=True,
        armed=True,
        order_writes_allowed=True,
        one_shot=True,
        emergency_stop=False,
        demo_enabled=False,
        automation_enabled=False,
        simulated_trading_header=None,
        credential_session_sha256=facts.credential_session_sha256,
        arm_id=control_row["arm_id"],
    )
    return finite.LiveFinitePortfolioInputsV1(
        requested_contracts=instrument.contracts,
        requested_leverage=instrument.leverage,
        instrument=instrument.spec,
        claims=finite.LiveFinitePortfolioClaimsV1(
            scope=finite.LiveFinancialScopeV1(
                environment="live",
                account_id=facts.account_uid,
                settlement_currency="USDT",
            ),
            account=account,
            financial=financial,
            risk_policy=policy.portfolio,
            authority=authority,
        ),
    )
