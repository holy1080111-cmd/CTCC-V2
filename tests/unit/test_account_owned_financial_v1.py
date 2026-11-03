"""Finite financial/source-value components only; no native/DB/G11 authority.

Original packet/position fixtures exercise the existing source validators.
Explicit fictional finite claims below test the shared algebra and its DENYs.
They never stand in for an acquired owner, durable Control or issued revision.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Context, Decimal, localcontext
from fractions import Fraction

import pytest

from app.config.settings import Settings
from app.trade_qualification import account_ledger_claims_v2 as codec
from app.trade_qualification import account_original_raw_proof_bank_v1 as bank
from app.trade_qualification import account_owned_financial_v1 as owned
from app.trade_qualification import portfolio
from tests.unit import test_account_ledger_claims_finite_v1 as finite_fixture_source
from tests.unit import test_account_original_api_window_operands_v3 as original
from tests.unit import test_qualification_portfolio as caps

finite_claims = finite_fixture_source.finite_claims


def _copy(value, **changes):
    return type(value).model_validate(
        value.model_dump(mode="python", round_trip=True) | changes, strict=True
    )


def _complete_DATA(claims):
    account = claims.account
    stamps = {
        name: _copy(getattr(account, name), complete=True)
        for name in (
            "balance_stamp",
            "positions_stamp",
            "history_stamp",
            "reservations_stamp",
        )
    }
    financial = _copy(
        claims.financial,
        bank_window_policy_sha256=bank.FINITE_EVALUATED_WINDOWS_POLICY_SHA256_V1,
        measured_hwm_policy_sha256=owned.measurement.POLICY_SHA256,
    )
    authority = _copy(
        claims.authority,
        stamp=_copy(claims.authority.stamp, complete=True),
        demo_enabled=True,
        armed=True,
        order_writes_allowed=True,
        emergency_stop=False,
    )
    return _copy(
        claims,
        account=_copy(account, **stamps),
        financial=financial,
        authority=authority,
    )


def _evaluate(claims, **changes):
    return portfolio.evaluate_finite_account_portfolio_v1(
        **(
            {
                "claims": claims,
                "report_id": "fictional-finite-risk",
                "instrument_id": caps.INSTRUMENT,
                "direction": "long",
                "candidate_entry": Decimal(100),
                "stop_loss": Decimal(95),
                "round_trip_cost_per_base": Decimal("0.1"),
                "requested_contracts": Decimal(1),
                "requested_leverage": 3,
                "instrument": caps.instrument(
                    observed_at=claims.financial.evaluated_at,
                    received_at=claims.financial.evaluated_at,
                ),
                "current_time": claims.financial.evaluated_at,
            }
            | changes
        )
    )


def test_finite_DATA_uses_same_caps_without_constructing_legacy_seed(finite_claims):
    claims = _complete_DATA(finite_claims)
    result = _evaluate(claims)
    assert result.passed is True
    assert result.execution_authority is False
    assert claims.account.original_start_seed is None
    assert claims.financial.original_start_seed is None
    assert claims.financial.current_loss_streak_upper_bound == 2
    assert not hasattr(claims.account, "loss_streak_at_history_start")
    assert Fraction(result.max_loss_amount) == Fraction(51, 1000)


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
def test_required_unknown_finite_operand_never_becomes_zero(finite_claims, field):
    claims = _complete_DATA(finite_claims)
    claims = _copy(claims, financial=_copy(claims.financial, **{field: None}))
    result = _evaluate(claims)
    assert result.passed is False
    assert result.execution_authority is False
    assert getattr(claims.financial, field) is None


def test_current_bound_reaches_limit_without_an_initial_seed(finite_claims):
    claims = _complete_DATA(finite_claims)
    claims = _copy(
        claims,
        financial=_copy(
            claims.financial,
            current_loss_streak_upper_bound=claims.risk_policy.max_consecutive_losses,
        ),
    )
    assert "consecutive_loss_limit_reached" in _evaluate(claims).causes


def test_finite_drawdown_uses_original_measured_current_not_risk_anchor(finite_claims):
    claims = _complete_DATA(finite_claims)
    # These are explicitly fictional separately timed source values. The test
    # verifies which operand the algebra uses, not their real authenticity.
    claims = _copy(claims, account=_copy(claims.account, equity=Decimal(1000)))
    result = _evaluate(claims)
    assert result.passed is True
    assert result.drawdown_pct > 0
    expected = Fraction(10, 110)
    assert abs(Fraction(result.drawdown_pct) - expected) < Fraction(1, 10**90)


def test_configured_lower_bucket_applies_to_aggregate_same_instrument(finite_claims):
    claims = _complete_DATA(finite_claims)
    position = caps.position(
        notional=Decimal(150), margin=Decimal("249.8"), risk_amount=Decimal("0.1")
    )
    claims = _copy(
        claims,
        account=_copy(
            claims.account,
            equity=Decimal(1000),
            positions=(position,),
            position_count=1,
        ),
        financial=_copy(claims.financial, position_margin_bucket_usdt=Decimal(250)),
    )
    result = _evaluate(claims)
    assert "position_margin_bucket_exceeded" in result.causes
    assert result.execution_authority is False


def test_finite_type_never_relaxes_legacy_portfolio_validator(finite_claims):
    claims = _complete_DATA(finite_claims)
    result = caps.evaluate(account=claims.account)
    assert "account_invalid" in result.causes
    assert result.passed is False


def test_G11_evaluation_cannot_reuse_a_precomputed_other_window(finite_claims):
    claims = _complete_DATA(finite_claims)
    with pytest.raises(owned.OwnedFinancialValuesError):
        _evaluate(
            claims,
            current_time=claims.financial.evaluated_at + timedelta(microseconds=1),
        )


def test_configured_selector_uses_existing_settings_and_hard_ceiling():
    settings = Settings(
        _env_file=None,
        risk_per_trade_pct=0.02,
        okx_demo_position_margin_bucket_usdt=Decimal(250),
    )
    window = caps.NOW - timedelta(days=30)
    value = owned._configured_policy_from_settings_v1(
        settings, drawdown_window_started_at=window
    )
    assert value.risk_policy.risk_per_trade_pct == Decimal("0.005")
    assert value.risk_policy.max_portfolio_risk_pct <= Decimal("0.01")
    assert value.risk_policy.max_portfolio_margin_pct <= Decimal("0.60")
    assert value.risk_policy.max_weekly_loss_pct == Decimal(
        str(settings.max_weekly_loss_pct)
    )
    assert (
        value.risk_policy.max_daily_loss_pct == settings.okx_demo_daily_loss_limit_pct
    )
    assert value.risk_policy.drawdown_window_started_at == window
    assert value.position_margin_bucket_usdt == Decimal(250)
    assert value.execution_authority is False


@pytest.mark.asyncio
async def test_actual_original_source_windows_recompute_week_at_G11_time(monkeypatch):
    population, current, checked, positions = await original._asof_source_values(
        monkeypatch
    )
    now = checked + timedelta(seconds=1)
    value = bank._derive_evaluated_api_asof_windows_v1(
        population,
        scope=original.source.original.SCOPE,
        original_validated_at=checked,
        evaluated_at=now,
        held_current_record=current,
        original_position_source=positions,
    )
    result = bank.decode(value.receipt_json)
    assert result["checked_at"] == checked.isoformat()
    assert result["window_evaluated_at"] == now.isoformat()
    assert (
        result["source_windows"]["rolling_7d"]["started_at"]
        == (now - timedelta(days=7)).isoformat()
    )
    assert result["as_of_original_risk_source_at"] != now.isoformat()
    old = bank._derive_original_api_window_operands_v3(
        population,
        scope=original.source.original.SCOPE,
        checked_at=checked,
        _held_current_record=current,
        _original_position_source=positions,
    )
    old_document = bank.decode(old.receipt_json)
    assert old_document["schema_version"] == bank.WINDOW_OPERANDS_SCHEMA_V3
    assert "window_evaluated_at" not in old_document
    assert old.account_complete is old.execution_authority is False


@pytest.mark.asyncio
async def test_full_original_measured_prefix_adoption_keeps_empty_streak_unknown(
    monkeypatch,
):
    population, current, checked, positions = await original._asof_source_values(
        monkeypatch
    )
    core = bank.decode(population.original_raw_bank.financial_facts.receipt_json)
    window = owned._utc(
        datetime.fromisoformat(core["sampled_balance_peak"]["first_measured_at"])
    )
    selected = owned._configured_policy_from_settings_v1(
        Settings(_env_file=None), drawdown_window_started_at=window
    )
    value = owned.derive_owned_finite_financial_values_v1(
        population,
        scope=original.source.original.SCOPE,
        original_validated_at=checked,
        evaluated_at=checked,
        held_current_record=current,
        original_position_source=positions,
        configured_policy=selected,
        original_expires_at=current.expires_at,
        original_prefix_sha256=core["index_head_sha256"],
    )
    assert value.financial.original_start_seed is None
    assert value.financial.current_loss_streak_upper_bound is None
    assert value.financial.daily_net_pnl == codec.ExactRatioV1(
        numerator="0", denominator="1"
    )
    assert value.financial.measured_current_equity is not None
    assert value.financial.measured_peak_equity is not None
    assert value.financial.actual_continuous_peak is None
    assert "finite_API_asof_complete_reset_or_original_seed_missing" in (
        value.financial.missing_obligations
    )
    assert "continuous_peak_or_dominating_source_bound_missing" not in (
        value.financial.missing_obligations
    )
    assert value.account_complete is value.account_revision_issued is False
    assert value.execution_authority is False
    changed = replace(
        selected,
        risk_policy=_copy(
            selected.risk_policy,
            drawdown_window_started_at=window + timedelta(microseconds=1),
        ),
    )
    with pytest.raises(owned.OwnedFinancialValuesError):
        owned.derive_owned_finite_financial_values_v1(
            population,
            scope=original.source.original.SCOPE,
            original_validated_at=checked,
            evaluated_at=checked,
            held_current_record=current,
            original_position_source=positions,
            configured_policy=changed,
            original_expires_at=current.expires_at,
            original_prefix_sha256=core["index_head_sha256"],
        )


def test_configured_policy_products_and_receipt_ignore_ambient_precision():
    settings = Settings(
        _env_file=None,
        order_size_cap_usdt=1000.123456789,
        max_open_positions=3,
        okx_demo_max_open_positions=3,
        max_same_direction_positions=2,
        max_correlated_positions=2,
    )
    window = caps.NOW - timedelta(days=30)
    with localcontext(Context(prec=28)):
        normal = owned._configured_policy_from_settings_v1(
            settings, drawdown_window_started_at=window
        )
    with localcontext(Context(prec=2)):
        low = owned._configured_policy_from_settings_v1(
            settings, drawdown_window_started_at=window
        )
    assert normal.risk_policy == low.risk_policy
    assert normal.selected_settings_json == low.selected_settings_json
    assert normal.selected_settings_sha256 == low.selected_settings_sha256
    assert Fraction(low.risk_policy.max_portfolio_notional) == (
        Fraction(Decimal(str(settings.order_size_cap_usdt))) * 3
    )


def _counter_initialization_DATA(scope, selected, genesis_raw, *, initialized_at):
    """Fictional immutable-row transport; never a native/operator capability."""
    from app.trade_qualification import demo_policy_counter_initialization_v1 as counter
    from app.trade_qualification.demo_control import ControlScope

    sampled = initialized_at - timedelta(microseconds=1)
    expiry = initialized_at + timedelta(seconds=20)
    policy_json = selected.selected_settings_json.decode("ascii")
    genesis_json = genesis_raw.decode("ascii")
    operator = counter.canonical(
        {
            "schema_version": counter.COUNTER_OPERATOR_SCHEMA_V1,
            "purpose": "policy_counter_initialize",
            "scope": [scope.environment, scope.account_id, scope.settlement_currency],
            "command_id": "1" * 64,
            "control_revision": 2,
            "control_event_sha256": "2" * 64,
            "source_lineage_sha256": "4" * 64,
            "configured_policy_sha256": counter.digest(policy_json),
            "measured_genesis_sha256": counter.digest(genesis_json),
            "original_expires_at": expiry.isoformat(),
            "confirmation": counter.COUNTER_CONFIRMATION_V1,
        }
    )
    hashes = {
        "operator_ack": counter.digest(operator),
        "configured_policy": counter.digest(policy_json),
        "measured_genesis": counter.digest(genesis_json),
    }
    ack = counter.PolicyCounterInitializationAckV1(
        ControlScope(scope.environment, scope.account_id),
        scope.settlement_currency,
        "1" * 64,
        2,
        "2" * 64,
        "3" * 64,
        "4" * 64,
        0,
        0,
        None,
        policy_json,
        genesis_json,
        sampled,
        expiry,
        100,
        200,
        "300",
        tuple(
            (name, hashes.get(name, "5" * 64))
            for name in counter.COUNTER_PROOF_NAMES_V1
        ),
        operator,
    )
    row = {
        "initialization_sha256": ack.initialization_sha256,
        "environment": scope.environment,
        "account_id": scope.account_id,
        "settlement_currency": scope.settlement_currency,
        "command_id": ack.command_id,
        "control_revision": ack.control_revision,
        "control_event_sha256": ack.control_event_sha256,
        "control_state_sha256": ack.control_state_sha256,
        "source_lineage_sha256": ack.source_lineage_sha256,
        "account_revision": ack.account_revision,
        "ledger_revision": ack.ledger_revision,
        "claims_sha256": ack.claims_sha256,
        "configured_policy_sha256": counter.digest(policy_json),
        "configured_policy_json": policy_json,
        "measured_genesis_sha256": counter.digest(genesis_json),
        "measured_genesis_json": genesis_json,
        "owner_sampled_at": sampled.isoformat(),
        "original_expires_at": expiry.isoformat(),
        "ack_json": counter.canonical(counter.policy_counter_ack_document_v1(ack)),
        "initialized_at": initialized_at.isoformat(),
        "db_recorded_at": initialized_at.isoformat(),
    }
    return counter.PolicyCounterInitializationReadbackV1(
        ack, initialized_at, initialized_at, owned.canonical(row), (), ()
    )


def _first_measurement(population, scope):
    anchor = population.coupled_anchors[0]
    ref = owned.measurement.observed.CaptureReference(
        **bank.decode(anchor.source_reference_json)
    )
    packet = owned.capture._verify_original_page_packet(
        anchor.original_packet,
        expected_sha256=ref.packet_sha256,
        expected_plan_sha256=ref.plan_sha256,
    )
    return owned.measurement._measure_verified_packet_v1(packet, ref, scope)


@pytest.mark.asyncio
async def test_explicit_counter_DATA_derives_zero_only_from_complete_original_outcomes(
    monkeypatch,
):
    population, current, checked, positions = await original._asof_source_values(
        monkeypatch
    )
    scope = original.source.original.SCOPE
    genesis = _first_measurement(population, scope)
    started = datetime.fromisoformat(
        owned.measurement._read(genesis)["body_completed_at"]
    )
    selected = owned._configured_policy_from_settings_v1(
        Settings(_env_file=None), drawdown_window_started_at=started
    )
    initialized = _counter_initialization_DATA(
        scope,
        selected,
        genesis,
        initialized_at=original.source.original.NOW - timedelta(seconds=90),
    )
    core = bank.decode(population.original_raw_bank.financial_facts.receipt_json)
    value = owned.derive_owned_finite_financial_values_v1(
        population,
        scope=scope,
        original_validated_at=checked,
        evaluated_at=checked,
        held_current_record=current,
        original_position_source=positions,
        configured_policy=selected,
        original_expires_at=current.expires_at,
        original_prefix_sha256=core["index_head_sha256"],
        policy_counter_initialization=initialized,
    )
    assert value.financial.loss_streak_basis == "explicit_operator_policy_counter"
    assert value.financial.init_ack_sha256 == initialized.ack.initialization_sha256
    assert value.financial.init_at == initialized.initialized_at
    assert value.financial.current_loss_streak_upper_bound == 0
    assert value.financial.original_start_seed is None
    assert value.financial.measured_hwm_window_started_at == started
    assert (
        value.financial.daily_net_pnl
        == value.financial.weekly_net_pnl
        == codec.ExactRatioV1(numerator="0", denominator="1")
    )
    hwm = bank.decode(value.measured_hwm_receipt_json)
    assert hwm["measured_genesis_json"].encode("ascii") == genesis
    assert hwm["measured_genesis_sha256"] == bank.sha(genesis)
    assert value.source_authenticity_verified is value.execution_authority is False


@pytest.mark.asyncio
async def test_counter_DATA_cannot_change_original_immutable_row_column(monkeypatch):
    population, current, checked, positions = await original._asof_source_values(
        monkeypatch
    )
    scope = original.source.original.SCOPE
    genesis = _first_measurement(population, scope)
    started = datetime.fromisoformat(
        owned.measurement._read(genesis)["body_completed_at"]
    )
    selected = owned._configured_policy_from_settings_v1(
        Settings(_env_file=None), drawdown_window_started_at=started
    )
    initialized = _counter_initialization_DATA(
        scope,
        selected,
        genesis,
        initialized_at=original.source.original.NOW - timedelta(seconds=90),
    )
    row = bank.decode(initialized.row_json)
    row["ledger_revision"] += 1
    changed = replace(initialized, row_json=bank.canonical(row))
    core = bank.decode(population.original_raw_bank.financial_facts.receipt_json)
    with pytest.raises(owned.OwnedFinancialValuesError, match="row_bytes_changed"):
        owned.derive_owned_finite_financial_values_v1(
            population,
            scope=scope,
            original_validated_at=checked,
            evaluated_at=checked,
            held_current_record=current,
            original_position_source=positions,
            configured_policy=selected,
            original_expires_at=current.expires_at,
            original_prefix_sha256=core["index_head_sha256"],
            policy_counter_initialization=changed,
        )


@pytest.mark.asyncio
async def test_inert_counter_vector_preserves_pre_epoch_daily_and_weekly_loss(
    monkeypatch,
):
    # Explicit arithmetic component after original source fixtures; these
    # replaced numerical rows/core receipts are NOT native source evidence.
    population, current, checked, positions = await original._asof_source_values(
        monkeypatch
    )
    at = original.source.original.NOW
    opened, closed = at - timedelta(seconds=240), at - timedelta(seconds=180)
    members = original._complete_trade_members(opened, closed)
    for member in members:
        row = member["raw"]
        if member["family"] == "fills":
            row["fillSz"] = "100"
            if row["side"] == "sell":
                row["fillPx"], row["fillPnl"] = "97", "-3"
        elif row["type"] == "2":
            row["sz"] = "100"
            if row["subType"] == "4":
                row["px"], row["pnl"], row["balChg"] = "97", "-3", "-3"
        else:
            row["balChg"] = "-1"
        member["row_sha256"] = bank.sha(bank.canonical(row))
    changed = original._inert_vector(population, members)
    core = bank.decode(changed.original_raw_bank.financial_facts.receipt_json)
    run = original._run(1, closed, -4, members[0]["identity_sha256"])
    run["opened_at_execution"] = opened.isoformat()
    run["position_funding_total"] = {"numerator": "-1", "denominator": "1"}
    run["original_fill_rows"] = [
        {
            "identity_sha256": member["identity_sha256"],
            "row_sha256": member["row_sha256"],
        }
        for member in members
        if member["family"] == "fills"
    ]
    core["closed_runs"] = [run]
    facts = replace(
        changed.original_raw_bank.financial_facts, receipt_json=bank.canonical(core)
    )
    old_receipt = bank.decode(changed.original_raw_bank.receipt_json)
    old_receipt["financial_facts_sha256"] = bank.sha(facts.receipt_json)
    raw_bank = replace(
        changed.original_raw_bank,
        financial_facts=facts,
        receipt_json=bank.canonical(old_receipt),
    )
    pop_receipt = bank.decode(changed.receipt_json)
    pop_receipt["original_raw_bank_receipt_sha256"] = bank.sha(raw_bank.receipt_json)
    changed = replace(
        changed, original_raw_bank=raw_bank, receipt_json=bank.canonical(pop_receipt)
    )
    scope = original.source.original.SCOPE
    genesis = _first_measurement(changed, scope)
    started = datetime.fromisoformat(
        owned.measurement._read(genesis)["body_completed_at"]
    )
    selected = owned._configured_policy_from_settings_v1(
        Settings(_env_file=None), drawdown_window_started_at=started
    )
    initialized = _counter_initialization_DATA(
        scope, selected, genesis, initialized_at=at - timedelta(seconds=90)
    )
    value = owned.derive_owned_finite_financial_values_v1(
        changed,
        scope=scope,
        original_validated_at=checked,
        evaluated_at=checked,
        held_current_record=current,
        original_position_source=positions,
        configured_policy=selected,
        original_expires_at=current.expires_at,
        original_prefix_sha256=core["index_head_sha256"],
        policy_counter_initialization=initialized,
    )
    assert value.financial.current_loss_streak_upper_bound == 0
    assert value.financial.original_start_seed is None
    assert (
        value.financial.daily_net_pnl
        == value.financial.weekly_net_pnl
        == codec.ExactRatioV1(numerator="-4", denominator="1")
    )
    assert value.financial.measured_hwm_window_started_at == started
    assert value.account_complete is value.execution_authority is False


@pytest.mark.asyncio
async def test_controlled_HWM_v2_DATA_has_explicit_policy_without_V4_relabel(
    monkeypatch,
):
    from app.trade_qualification import account_controlled_measured_hwm_v2 as hwm

    population, current, checked, positions = await original._asof_source_values(
        monkeypatch
    )
    scope = original.source.original.SCOPE
    genesis = _first_measurement(population, scope)
    started = datetime.fromisoformat(
        owned.measurement._read(genesis)["body_completed_at"]
    )
    selected = owned._configured_policy_from_settings_v1(
        Settings(_env_file=None), drawdown_window_started_at=started
    )
    core = bank.decode(population.original_raw_bank.financial_facts.receipt_json)
    values = owned.derive_owned_finite_financial_values_v1(
        population,
        scope=scope,
        original_validated_at=checked,
        evaluated_at=checked,
        held_current_record=current,
        original_position_source=positions,
        configured_policy=selected,
        original_expires_at=current.expires_at,
        original_prefix_sha256=core["index_head_sha256"],
    )
    raw = hwm.build_controlled_hwm_record_v2(values, current, owner_sampled_at=checked)
    doc = bank.decode(raw)
    assert (
        doc["schema_version"] == "ctcc.native_owned_controlled_balance_peak_record.v2"
    )
    assert doc["policy_sha256"] == hwm.POLICY_SHA256
    assert bank.canonical(bank.decode(hwm.POLICY_BYTES)) == hwm.POLICY_BYTES
    assert len(doc) == 11 and len(doc["observed_source"]) == 17
    assert doc["observed_source"]["current_source_reference"] == (
        owned.measurement.observed.reference_document(current.reference)
    )
    assert doc["observed_source"]["measured_hwm_receipt_json"].encode("ascii") == (
        values.measured_hwm_receipt_json
    )
    assert doc["account_complete"] is doc["execution_authority"] is False
    assert doc["historical_account_peak"] is doc["initial_account_peak"] is None
    assert values.financial.original_start_seed is None
