"""Explicit fictional DATA only: no native capability, SQL or account evidence."""

import copy
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_ledger_claims_v2 as codec
from app.trade_qualification import reservations as legacy
from app.trade_qualification.portfolio import (
    DemoRiskAuthority,
    EvidenceStamp,
    PortfolioRiskPolicy,
)


@pytest.fixture
def finite_claims():
    now = datetime(2026, 1, 12, 12, tzinfo=UTC)
    asof = now - timedelta(seconds=5)
    scope = legacy.LedgerScope(account_id="100001", settlement_currency="USDT")
    stamp = EvidenceStamp(
        environment="demo",
        account_id=scope.account_id,
        source_sha256="1" * 64,
        observed_at=asof,
        received_at=now,
        complete=False,
    )
    policy = PortfolioRiskPolicy(
        max_age_seconds=30,
        drawdown_window_started_at=now - timedelta(days=30),
        risk_per_trade_pct=Decimal("0.005"),
        max_daily_loss_pct=Decimal("0.02"),
        max_weekly_loss_pct=Decimal("0.05"),
        max_drawdown_pct=Decimal("0.1"),
        max_consecutive_losses=3,
        max_open_positions=3,
        max_same_direction_positions=2,
        max_correlated_positions=2,
        max_order_notional=Decimal(100),
        max_order_contracts=Decimal(10),
        max_portfolio_notional=Decimal(300),
        max_same_direction_notional=Decimal(200),
        max_correlated_notional=Decimal(200),
        max_portfolio_risk_pct=Decimal("0.01"),
        max_portfolio_margin_pct=Decimal("0.6"),
        max_leverage=3,
    )
    account = codec.FinitePortfolioSnapshotV1(
        account_id=scope.account_id,
        settlement_currency="USDT",
        balance_stamp=stamp,
        positions_stamp=stamp,
        history_stamp=stamp,
        reservations_stamp=stamp,
        equity=Decimal(100),
        available_margin=Decimal(100),
        positions=(),
        pending_reservations=(),
        position_count=0,
        pending_reservation_count=0,
        history_start=now - timedelta(days=7),
        history_end=asof,
        original_start_seed=None,
    )
    ratio = lambda n: codec.ExactRatioV1(numerator=str(n), denominator="1")
    financial = codec.FiniteFinancialOperandsV1(
        financial_schema=codec.FINITE_FINANCIAL_SCHEMA_V1,
        source_as_of_at=asof,
        available_at=now - timedelta(seconds=1),
        evaluated_at=now,
        daily_window_started_at=now.replace(hour=0, minute=0, second=0, microsecond=0),
        weekly_window_started_at=now - timedelta(days=7),
        daily_net_pnl=ratio(-1),
        weekly_net_pnl=ratio(-2),
        current_loss_streak_upper_bound=2,
        original_start_seed=None,
        loss_streak_basis="observed_positive_reset",
        init_ack_sha256=None,
        init_at=None,
        measured_hwm_window_started_at=policy.drawdown_window_started_at,
        measured_peak_equity=ratio(110),
        measured_peak_observed_at=now - timedelta(days=1),
        measured_current_equity=ratio(100),
        measured_hwm_policy_sha256="2" * 64,
        drawdown_basis="every_accepted_measured_balance_in_original_window",
        actual_continuous_peak=None,
        cashflow_adjusted_peak=None,
        original_expires_at=now + timedelta(seconds=20),
        bank_window_policy_sha256="3" * 64,
        current_source_reference=codec.FiniteCurrentReferenceV1(
            capture_id="4" * 32,
            head_sha256="5" * 64,
            plan_sha256="6" * 64,
            packet_sha256="7" * 64,
            session_binding_sha256="8" * 64,
        ),
        original_prefix_sha256="9" * 64,
        original_raw_bank_sha256="a" * 64,
        api_population_receipt_sha256="b" * 64,
        source_receipt_sha256="c" * 64,
        measured_hwm_receipt_sha256="d" * 64,
        position_margin_bucket_usdt=Decimal(250),
        original_risk_policy_sha256=codec.finite_risk_policy_sha256_v1(policy),
        missing_obligations=(),
    )
    authority = DemoRiskAuthority(
        stamp=stamp,
        environment="demo",
        demo_enabled=False,
        armed=False,
        order_writes_allowed=False,
        simulated_trading_header="1",
        emergency_stop=True,
        live_trading=False,
        live_order_writes=False,
        live_auto_execution=False,
    )
    return codec.FiniteAccountLedgerClaimsV1(
        payload_schema=codec.FINITE_PAYLOAD_SCHEMA_V1,
        scope=scope,
        account=account,
        financial=financial,
        risk_policy=policy,
        authority=authority,
        reconciliation_id="e" * 64,
    )


def _packed(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def test_finite_DATA_roundtrip_keeps_unknown_seed_separate_from_current_bound(
    finite_claims,
):
    raw = codec.encode_finite_account_ledger_claims_v1(finite_claims)
    document = json.loads(raw)
    assert document["scope"] == ["demo", "100001", "USDT"]
    assert document["claims"]["payload_schema"] == codec.FINITE_PAYLOAD_SCHEMA_V1
    assert all(document[name] is False for name in codec.FALSE_FIELDS)
    restored = codec.decode_finite_account_ledger_claims_v1(raw)
    assert restored == finite_claims and restored is not finite_claims
    assert (
        restored.account.original_start_seed
        is restored.financial.original_start_seed
        is None
    )
    assert restored.financial.current_loss_streak_upper_bound == 2
    assert restored.financial.position_margin_bucket_usdt == Decimal(250)
    assert restored.authority.armed is restored.authority.order_writes_allowed is False
    assert restored.account.balance_stamp.complete is False


def test_legacy_decoder_and_ledger_guard_cannot_promote_finite_DATA(finite_claims):
    raw = codec.encode_finite_account_ledger_claims_v1(finite_claims)
    with pytest.raises(codec.AccountLedgerClaimsV2CodecError):
        codec.decode_account_ledger_claims_v2(raw)
    with pytest.raises(legacy.QualificationLedgerError):
        legacy.decode(raw.decode("ascii"), legacy.AccountLedgerClaims)
    with pytest.raises(legacy.QualificationLedgerError):
        QualificationLedgerRepository._require_owned_account_revision(finite_claims)


@pytest.mark.parametrize(
    "field",
    [
        "daily_net_pnl",
        "weekly_net_pnl",
        "current_loss_streak_upper_bound",
        "measured_current_equity",
        "measured_hwm_receipt_sha256",
    ],
)
def test_explicit_unknown_stays_null_never_zero_in_finite_DATA(finite_claims, field):
    document = json.loads(codec.encode_finite_account_ledger_claims_v1(finite_claims))
    document["claims"]["financial"][field] = None
    restored = codec.decode_finite_account_ledger_claims_v1(_packed(document))
    assert getattr(restored.financial, field) is None
    assert restored.financial.original_start_seed is None


@pytest.mark.parametrize(
    "path,value",
    [
        (("account", "original_start_seed"), 0),
        (("financial", "original_start_seed"), 0),
        (("financial", "current_loss_streak_upper_bound"), True),
        (("financial", "current_loss_streak_upper_bound"), 4097),
        (("financial", "drawdown_basis"), "continuous_lifetime_peak"),
        (
            ("financial", "actual_continuous_peak"),
            {"numerator": "110", "denominator": "1"},
        ),
        (("financial", "original_risk_policy_sha256"), "0" * 64),
        (("financial", "measured_hwm_window_started_at"), "2026-01-01T00:00:00Z"),
        (("financial", "daily_window_started_at"), "2026-01-11T00:00:00Z"),
        (("financial", "weekly_window_started_at"), "2026-01-05T11:59:59Z"),
        (("financial", "current_source_reference"), {"capture_id": "0" * 32}),
        (("financial", "missing_obligations"), ["z", "a"]),
        (("scope", "account_id"), "100002"),
    ],
)
def test_finite_scope_time_policy_seed_and_type_mutants_reject(
    finite_claims, path, value
):
    document = json.loads(codec.encode_finite_account_ledger_claims_v1(finite_claims))
    document["claims"][path[0]][path[1]] = value
    with pytest.raises(codec.AccountLedgerClaimsV2CodecError):
        codec.decode_finite_account_ledger_claims_v1(_packed(document))


@pytest.mark.parametrize(
    "numerator,denominator",
    [("2", "2"), ("-0", "1"), ("01", "1"), ("1", "0"), ("1", "01")],
)
def test_exact_ratio_rejects_unreduced_or_noncanonical_DATA(numerator, denominator):
    with pytest.raises((ValidationError, codec.AccountLedgerClaimsV2CodecError)):
        codec.ExactRatioV1(numerator=numerator, denominator=denominator)


def test_exact_ratio_accepts_original4096bit_boundary_and_denies_one_over():
    value = str(2**4095)
    assert codec.ExactRatioV1(numerator=value, denominator="1").numerator == value
    with pytest.raises((ValidationError, codec.AccountLedgerClaimsV2CodecError)):
        codec.ExactRatioV1(numerator=str(2**4096), denominator="1")


def test_model_construct_cannot_smuggle_removed_fields_or_unknown_type(finite_claims):
    forged = copy.deepcopy(finite_claims)
    forged.__dict__["caller_passed"] = True
    with pytest.raises(codec.AccountLedgerClaimsV2CodecError):
        codec.encode_finite_account_ledger_claims_v1(forged)
    assert finite_claims.financial.original_start_seed is None


@pytest.mark.parametrize("value", ["0", "301", True, None])
def test_actual_configured_bucket_cannot_default_or_exceed_user300_ceiling(
    finite_claims, value
):
    document = json.loads(codec.encode_finite_account_ledger_claims_v1(finite_claims))
    document["claims"]["financial"]["position_margin_bucket_usdt"] = value
    with pytest.raises(codec.AccountLedgerClaimsV2CodecError):
        codec.decode_finite_account_ledger_claims_v1(_packed(document))


def test_missing_configured_bucket_cannot_default_to300(finite_claims):
    document = json.loads(codec.encode_finite_account_ledger_claims_v1(finite_claims))
    document["claims"]["financial"].pop("position_margin_bucket_usdt")
    with pytest.raises(codec.AccountLedgerClaimsV2CodecError):
        codec.decode_finite_account_ledger_claims_v1(_packed(document))


@pytest.mark.parametrize(
    "basis,bound,ack,at",
    [
        (None, 2, None, None),
        ("observed_positive_reset", 2, "1" * 64, None),
        ("observed_positive_reset", 2, None, "BEFORE"),
        ("explicit_operator_policy_counter", 2, None, "BEFORE"),
        ("explicit_operator_policy_counter", 2, "1" * 64, None),
        ("explicit_operator_policy_counter", 2, "1" * 64, "AFTER"),
        ("explicit_operator_policy_counter", True, "1" * 64, "BEFORE"),
        ("invented_global_seed", 0, None, None),
    ],
)
def test_finite_counter_basis_does_not_invent_reset_or_missing_reference(
    finite_claims, basis, bound, ack, at
):
    value = json.loads(codec.encode_finite_account_ledger_claims_v1(finite_claims))
    row = value["claims"]["financial"]
    row.update(
        loss_streak_basis=basis,
        current_loss_streak_upper_bound=bound,
        init_ack_sha256=ack,
    )
    row["init_at"] = (
        TypeAdapter(datetime).dump_python(
            finite_claims.financial.source_as_of_at
            + timedelta(seconds=1 if at == "AFTER" else -1),
            mode="json",
        )
        if at is not None
        else None
    )
    with pytest.raises(codec.AccountLedgerClaimsV2CodecError):
        codec.decode_finite_account_ledger_claims_v1(_packed(value))


def test_finite_counter_data_reference_is_distinct_from_original_seed_and_authority(
    finite_claims,
):
    value = json.loads(codec.encode_finite_account_ledger_claims_v1(finite_claims))
    row = value["claims"]["financial"]
    row.update(
        loss_streak_basis="explicit_operator_policy_counter",
        init_ack_sha256="1" * 64,
        init_at=TypeAdapter(datetime).dump_python(
            finite_claims.financial.source_as_of_at - timedelta(days=1),
            mode="json",
        ),
    )
    restored = codec.decode_finite_account_ledger_claims_v1(_packed(value))
    assert restored.financial.current_loss_streak_upper_bound == 2
    assert (
        restored.financial.original_start_seed
        is restored.account.original_start_seed
        is None
    )
    assert restored.authority.armed is restored.authority.order_writes_allowed is False
    assert restored.account.balance_stamp.complete is False
    assert all(value[field] is False for field in codec.FALSE_FIELDS)
    # A genuine initialization row may be known while its source outcome
    # coverage is incomplete. DATA must preserve that unknown, never counter0.
    row["current_loss_streak_upper_bound"] = None
    restored = codec.decode_finite_account_ledger_claims_v1(_packed(value))
    assert restored.financial.current_loss_streak_upper_bound is None
    assert restored.financial.init_ack_sha256 == "1" * 64
    assert restored.financial.original_start_seed is None


def test_unknown_finite_counter_has_no_silent_basis_or_zero_default(finite_claims):
    value = json.loads(codec.encode_finite_account_ledger_claims_v1(finite_claims))
    row = value["claims"]["financial"]
    row.update(
        loss_streak_basis=None,
        current_loss_streak_upper_bound=None,
        init_ack_sha256=None,
        init_at=None,
    )
    restored = codec.decode_finite_account_ledger_claims_v1(_packed(value))
    assert (
        restored.financial.loss_streak_basis
        is restored.financial.current_loss_streak_upper_bound
        is None
    )
    for field in ("loss_streak_basis", "init_ack_sha256", "init_at"):
        mutant = copy.deepcopy(value)
        mutant["claims"]["financial"].pop(field)
        with pytest.raises(codec.AccountLedgerClaimsV2CodecError):
            codec.decode_finite_account_ledger_claims_v1(_packed(mutant))


def test_noncanonical_counter_time_wire_stays_rejected_without_production_codec_change(
    finite_claims,
):
    value = json.loads(codec.encode_finite_account_ledger_claims_v1(finite_claims))
    instant = finite_claims.financial.source_as_of_at - timedelta(days=1)
    canonical_time = TypeAdapter(datetime).dump_python(instant, mode="json")
    assert canonical_time.endswith("Z")
    value["claims"]["financial"].update(
        loss_streak_basis="explicit_operator_policy_counter",
        init_ack_sha256="1" * 64,
        init_at=canonical_time,
    )
    accepted = codec.decode_finite_account_ledger_claims_v1(_packed(value))
    assert accepted.financial.init_at == instant
    assert accepted.financial.original_start_seed is None
    assert accepted.execution_authority is False
    value["claims"]["financial"]["init_at"] = instant.isoformat()
    assert value["claims"]["financial"]["init_at"].endswith("+00:00")
    with pytest.raises(codec.AccountLedgerClaimsV2CodecError):
        codec.decode_finite_account_ledger_claims_v1(_packed(value))
