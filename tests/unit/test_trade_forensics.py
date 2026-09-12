"""Synthetic exact fills and price paths; no order, shadow or market samples."""

import ast
import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.trade_evidence.forensics import (
    MAX_BYTES,
    AttributionFact,
    CashFlowEvent,
    FillEvent,
    ForensicsError,
    ForensicsInput,
    PriceInterval,
    StreamCoverage,
    TradeCandidate,
    TradeForensicsResult,
    analyze_trade,
    candidate_sha256,
    freeze_forensics,
    validate_forensics,
    verify_forensics,
)

D = Decimal
AT = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
CATEGORIES = (
    "Wrong Direction",
    "Early Entry",
    "Late Entry",
    "Chasing Entry",
    "Stop Too Tight",
    "Liquidity Sweep",
    "Target Unrealistic",
    "Regime Mismatch",
    "Execution Error",
    "Strategy Edge Failure",
    "Good Execution",
)


def at(seconds):
    return AT + timedelta(seconds=seconds)


def fixture(direction="long", *, closed=True):
    def price(value):
        return D(value) if direction == "long" else D(200) - D(value)

    candidate = TradeCandidate(
        report_id="forensics_synthetic",
        instrument_id="BTC-USDT-SWAP",
        account_id="123456789",
        direction=direction,
        settlement_currency="USDT",
        contract_value_base=D("0.1"),
        planned_contracts=D("10"),
        entry=price("100"),
        stop_loss=price("95"),
        take_profit=price("110"),
        recorded_at=AT,
        original_evidence_sha256="1" * 64,
        original_source_sha256="2" * 64,
        entry_window_start=at(5),
        entry_deadline=at(25),
        entry_zone_low=price("99") if direction == "long" else price("103"),
        entry_zone_high=price("103") if direction == "long" else price("99"),
        max_adverse_slippage_bps=D("250"),
    )
    pin = candidate_sha256(candidate)
    observed = at(60 if closed else 40)

    def common(identifier):
        return {
            "evidence_id": identifier,
            "report_id": candidate.report_id,
            "candidate_sha256": pin,
            "instrument_id": candidate.instrument_id,
            "account_id": candidate.account_id,
            "source_sha256": hashlib.sha256(identifier.encode()).hexdigest(),
            "recorded_at": observed,
        }

    fills = []
    for number, (when, role, quantity, level) in enumerate(
        (
            (10, "entry", "4", "100"),
            (20, "entry", "6", "102"),
            (30, "exit", "5", "106"),
            (40, "exit", "5", "108"),
        )[: 4 if closed else 3]
    ):
        fills.append(
            FillEvent(
                **common(f"fill_{number}"),
                order_id=f"order_{number}",
                occurred_at=at(when),
                role=role,
                side="buy" if (direction == "long") == (role == "entry") else "sell",
                contracts=D(quantity),
                price=price(level),
                exit_reason="take_profit" if role == "exit" else None,
                reference_price=price("110") if role == "exit" else None,
                reference_source_sha256="3" * 64 if role == "exit" else None,
            )
        )
    cashflows = []
    for index, fill in enumerate(fills):
        cashflows.append(
            CashFlowEvent(
                **common(f"fee_{index}"),
                occurred_at=fill.occurred_at + timedelta(seconds=1),
                kind="fee",
                amount=D(("-.04", "-.06", "-.05", ".01")[index]),
                currency="USDT",
                fill_id=fill.evidence_id,
            )
        )
    cashflows.append(
        CashFlowEvent(
            **common("funding_0"),
            occurred_at=at(25),
            kind="funding",
            amount=D(".02"),
            currency="USDT",
        )
    )
    cashflows.sort(
        key=lambda item: item.occurred_at
    )  # Synthetic source creation, not repair by evaluator.
    rows = []
    for index, (start, end, opening, high, low, close) in enumerate(
        (
            (10, 20, "100", "103", "99", "102"),
            (20, 30, "102", "107", "100", "106"),
            (30, 40, "106", "109", "104", "108"),
        )
    ):
        rows.append(
            PriceInterval(
                **common(f"path_{index}"),
                started_at=at(start),
                ended_at=at(end),
                open=price(opening),
                high=price(high if direction == "long" else low),
                low=price(low if direction == "long" else high),
                close=price(close),
            )
        )
    coverage = tuple(
        StreamCoverage(
            **common(f"coverage_{stream}"),
            stream=stream,
            status="complete",
            started_at=at(10) if stream == "path" else AT,
            ended_at=at(40) if stream == "path" else observed,
        )
        for stream in ("fills", "fees", "funding", "path")
    )
    packet = ForensicsInput(
        candidate=candidate,
        fills=tuple(fills),
        cashflows=tuple(cashflows),
        path=tuple(rows),
        coverage=coverage,
        observed_at=observed,
        purpose="synthetic_test",
    )
    return packet, pin


def run(packet, pin):
    return analyze_trade(packet, expected_candidate_sha256=pin)


def rebind(packet, **changes):
    candidate = TradeCandidate.model_validate(
        {**packet.candidate.model_dump(), **changes}, strict=True
    )
    pin = candidate_sha256(candidate)
    updates = {"candidate": candidate}
    for key in ("fills", "cashflows", "path", "coverage", "facts"):
        updates[key] = tuple(
            item.model_copy(
                update={"candidate_sha256": pin, "report_id": candidate.report_id}
            )
            for item in getattr(packet, key)
        )
    return packet.model_copy(update=updates), pin


@pytest.fixture(params=("long", "short"))
def trade(request):
    return fixture(request.param)


def test_genuine_partial_fills_close_and_exact_cash_path(trade):
    packet, pin = trade
    result = run(packet, pin)
    assert result.position_status == "flat"
    assert result.metrics_complete
    assert result.actual_fill.value == (
        D("101.2") if packet.candidate.direction == "long" else D("98.8")
    )
    assert result.exit_price.value == (
        D("107") if packet.candidate.direction == "long" else D("93")
    )
    assert result.entry_contracts.value == result.exit_contracts.value == D(10)
    assert result.remaining_contracts.value == 0
    assert result.entry_slippage_bps.value == 120
    assert result.fees.value == D("-.14")
    assert result.funding.value == D(".02")
    assert result.gross_realized_pnl.value == D("5.8")
    assert result.pnl.value == result.net_cash_pnl_to_date.value == D("5.68")
    assert result.mfe.value == D("6.3")
    assert result.mae.value == D("1.2")
    assert result.max_favorable_r.value == D("1.26")
    assert result.max_adverse_r.value == D(".24")
    assert result.original_risk.value == D("5")
    assert result.exit_reason == "take_profit"
    assert result.primary_cause == "unknown" and not result.observations


@pytest.mark.parametrize("direction", ("long", "short"))
def test_partial_close_has_realized_cash_but_no_final_pnl(direction):
    packet, pin = fixture(direction, closed=False)
    result = run(packet, pin)
    assert result.position_status == "partially_open"
    assert result.remaining_contracts.value == 5
    assert result.gross_realized_pnl.value == D("2.8")
    assert result.net_cash_pnl_to_date.value == D("2.67")
    assert (
        result.pnl.value is None and result.pnl.unknown_reason == "position_not_closed"
    )
    assert result.mfe.value == D("6.3") and result.mae.value == D("1.2")
    assert not result.metrics_complete


def test_repeating_ratios_keep_exact_operands_and_hostile_context(trade):
    packet, pin = trade
    expected = run(packet, pin)
    slip = expected.exit_slippage_bps
    exact = Fraction(int(slip.numerator), int(slip.denominator))
    assert exact == (
        Fraction(3000, 11)
        if packet.candidate.direction == "long"
        else Fraction(1000, 3)
    )
    with localcontext() as context:
        context.prec = 3
        context.Emax = 9
        context.Emin = -9
        assert run(packet, pin) == expected


def test_json_freeze_external_hash_and_replay(trade):
    packet, pin = trade
    result = run(packet, pin)
    decoded = TradeForensicsResult.model_validate_json(
        result.model_dump_json(round_trip=True), strict=True
    )
    assert validate_forensics(decoded, expected_candidate_sha256=pin) == result
    raw = freeze_forensics(result, expected_candidate_sha256=pin)
    assert (
        verify_forensics(
            raw, hashlib.sha256(raw).hexdigest(), expected_candidate_sha256=pin
        )
        == result
    )
    with pytest.raises(ForensicsError):
        verify_forensics(raw, "0" * 64, expected_candidate_sha256=pin)


@pytest.mark.parametrize("stream", ("fills", "fees", "funding", "path"))
def test_unknown_coverage_never_fills_unknown_with_zero(trade, stream):
    packet, pin = trade
    covers = tuple(
        item.model_copy(update={"status": "unknown", "reason": "source not available"})
        if item.stream == stream
        else item
        for item in packet.coverage
    )
    result = run(packet.model_copy(update={"coverage": covers}), pin)
    assert not result.metrics_complete
    if stream == "fills":
        assert result.actual_fill.value is None and result.position_status == "unknown"
        assert result.pnl.value is None
    elif stream == "path":
        assert (
            result.mfe.value is result.mae.value is result.max_favorable_r.value is None
        )
        assert result.pnl.value == D("5.68")
    else:
        assert getattr(result, stream).value is None
        assert result.pnl.value is None
        assert result.gross_realized_pnl.value == D("5.8")


@pytest.mark.parametrize("stream", ("fees", "funding"))
def test_foreign_currency_is_retained_without_invented_conversion(trade, stream):
    packet, pin = trade
    kind = "fee" if stream == "fees" else "funding"
    flows = tuple(
        item.model_copy(update={"currency": "BTC"}) if item.kind == kind else item
        for item in packet.cashflows
    )
    result = run(packet.model_copy(update={"cashflows": flows}), pin)
    assert getattr(result, stream).value is None
    assert getattr(result, stream).unknown_reason == f"{stream}_currency_unconverted"
    assert result.pnl.value is None


def test_funding_debit_and_fee_rebate_signs_are_not_absolute_values(trade):
    packet, pin = trade
    flows = tuple(
        item.model_copy(update={"amount": -item.amount}) for item in packet.cashflows
    )
    result = run(packet.model_copy(update={"cashflows": flows}), pin)
    assert result.fees.value == D(".14") and result.funding.value == D("-.02")
    assert result.pnl.value == D("5.92")


def test_complete_empty_cash_ledger_is_explicit_known_zero(trade):
    packet, pin = trade
    result = run(packet.model_copy(update={"cashflows": ()}), pin)
    assert result.fees.value == result.funding.value == 0
    assert result.pnl.value == D("5.8")
    covers = tuple(
        item.model_copy(
            update={"status": "partial", "reason": "no completed bill capture"}
        )
        if item.stream != "fills"
        else item
        for item in packet.coverage
    )
    result = run(packet.model_copy(update={"cashflows": (), "coverage": covers}), pin)
    assert result.fees.value is result.funding.value is result.pnl.value is None


@pytest.mark.parametrize(
    "mode", ("gap", "cross_fill", "cropped", "missing", "wrong_end")
)
def test_price_coverage_limitations_produce_unknown_not_interpolation(trade, mode):
    packet, pin = trade
    rows = list(packet.path)
    if mode == "gap":
        rows[1] = rows[1].model_copy(update={"started_at": at(21)})
    elif mode == "cross_fill":
        rows[0] = rows[0].model_copy(update={"ended_at": at(21)})
        rows[1] = rows[1].model_copy(update={"started_at": at(21)})
    elif mode == "cropped":
        rows = rows[1:]
    elif mode == "missing":
        rows = []
    else:
        rows[-1] = rows[-1].model_copy(update={"ended_at": at(41)})
    result = run(packet.model_copy(update={"path": tuple(rows)}), pin)
    assert result.mfe.value is result.mae.value is result.max_adverse_r.value is None
    assert result.pnl.value == D("5.68")


@pytest.mark.parametrize(
    "mode", ("overlap", "out_of_order", "bad_high", "zero_interval")
)
def test_invalid_path_rejected(trade, mode):
    packet, pin = trade
    rows = list(packet.path)
    if mode == "overlap":
        rows[1] = rows[1].model_copy(update={"started_at": at(19)})
    elif mode == "out_of_order":
        rows.reverse()
    elif mode == "bad_high":
        rows[0] = rows[0].model_copy(update={"high": D(1)})
    else:
        rows[0] = rows[0].model_copy(update={"ended_at": rows[0].started_at})
    with pytest.raises(ForensicsError):
        run(packet.model_copy(update={"path": tuple(rows)}), pin)


@pytest.mark.parametrize(
    "field,value",
    (
        ("report_id", "other_report"),
        ("account_id", "987654321"),
        ("instrument_id", "ETH-USDT-SWAP"),
        ("candidate_sha256", "0" * 64),
        ("environment", "live"),
        ("source_sha256", " " + "0" * 64),
        ("contracts", D("0")),
        ("price", D("NaN")),
        ("occurred_at", at(61)),
        ("recorded_at", at(61)),
        ("occurred_at", AT - timedelta(seconds=1)),
        ("occurred_at", AT.replace(tzinfo=None)),
    ),
)
def test_fill_scope_decimal_and_time_fail_closed(trade, field, value):
    packet, pin = trade
    fill = packet.fills[0].model_copy(update={field: value})
    with pytest.raises(ForensicsError):
        run(packet.model_copy(update={"fills": (fill, *packet.fills[1:])}), pin)


@pytest.mark.parametrize(
    "collection", ("fills", "cashflows", "path", "coverage", "facts")
)
def test_duplicates_and_conflicts_not_silently_deduplicated(trade, collection):
    packet, pin = trade
    if collection == "facts":
        packet = with_fact(packet, "Wrong Direction")
    values = getattr(packet, collection)
    with pytest.raises(ForensicsError):
        run(packet.model_copy(update={collection: (values[0], *values)}), pin)


@pytest.mark.parametrize(
    "mode",
    ("wrong_side", "overclose", "exit_first", "reopen", "reorder", "missing_fee_fill"),
)
def test_inventory_and_fee_references_are_not_guessed(trade, mode):
    packet, pin = trade
    fills = list(packet.fills)
    if mode == "wrong_side":
        fills[0] = fills[0].model_copy(
            update={"side": "sell" if fills[0].side == "buy" else "buy"}
        )
    elif mode == "overclose":
        fills[-1] = fills[-1].model_copy(update={"contracts": D("6")})
    elif mode == "exit_first":
        fills[0] = fills[0].model_copy(
            update={"role": "exit", "exit_reason": "manual", "side": fills[-1].side}
        )
    elif mode == "reopen":
        fills.append(
            fills[0].model_copy(
                update={"evidence_id": "reopened", "occurred_at": at(45)}
            )
        )
    elif mode == "reorder":
        fills[0], fills[1] = fills[1], fills[0]
    else:
        flows = (
            packet.cashflows[0].model_copy(update={"fill_id": "missing_fill"}),
            *packet.cashflows[1:],
        )
        with pytest.raises(ForensicsError):
            run(packet.model_copy(update={"cashflows": flows}), pin)
        return
    with pytest.raises(ForensicsError):
        run(packet.model_copy(update={"fills": tuple(fills)}), pin)


def test_same_time_mixed_entry_exit_is_ambiguous_not_tuple_chronology(trade):
    packet, pin = trade
    fills = (
        *packet.fills[:2],
        packet.fills[2].model_copy(update={"occurred_at": at(20)}),
        packet.fills[3],
    )
    with pytest.raises(ForensicsError, match="equal_time_mixed_fill_roles_ambiguous"):
        run(packet.model_copy(update={"fills": fills}), pin)


def test_same_time_same_role_requires_explicit_lexical_fifo_accounting_order(trade):
    packet, pin = trade
    fills = (
        packet.fills[0],
        packet.fills[1].model_copy(update={"occurred_at": at(10)}),
        *packet.fills[2:],
    )
    packet = packet.model_copy(update={"fills": fills})
    result = run(packet, pin)
    assert result.pnl.value == D("5.68")
    assert result.mfe.value is None
    assert result.mfe.unknown_reason == "equal_time_fills_have_unknown_path_order"
    assert (
        "equal_time_same_role_fifo_uses_fill_id_not_exchange_chronology"
        in result.limitations
    )
    with pytest.raises(ForensicsError, match="equal_time_fill_id_order_required"):
        run(packet.model_copy(update={"fills": (fills[1], fills[0], *fills[2:])}), pin)


@pytest.mark.parametrize("when", (5, 10, 20, 40, 45))
def test_funding_requires_known_effective_holding_time(trade, when):
    packet, pin = trade
    flows = [
        item.model_copy(update={"occurred_at": at(when)})
        if item.kind == "funding"
        else item
        for item in packet.cashflows
    ]
    flows.sort(key=lambda item: item.occurred_at)
    with pytest.raises(ForensicsError, match="funding_"):
        run(packet.model_copy(update={"cashflows": tuple(flows)}), pin)


def test_complete_path_cannot_contradict_actual_fill_at_interval_start(trade):
    packet, pin = trade
    row = packet.path[0].model_copy(
        update={"open": D("90"), "high": D("91"), "low": D("89"), "close": D("90")}
    )
    with pytest.raises(ForensicsError, match="path_conflicts_with_boundary_fill_price"):
        run(packet.model_copy(update={"path": (row, *packet.path[1:])}), pin)


def with_fact(packet, category):
    fill = packet.fills[0]
    fact = AttributionFact(
        **{
            key: getattr(fill, key)
            for key in (
                "report_id",
                "candidate_sha256",
                "instrument_id",
                "account_id",
                "environment",
                "source_sha256",
                "recorded_at",
            )
        },
        evidence_id="fact_0",
        category=category,
        statement="Recorded source assessment; it is not independently verified.",
        evidence_ids=(fill.evidence_id,),
    )
    return packet.model_copy(update={"facts": (fact,)})


@pytest.mark.parametrize("category", CATEGORIES)
def test_exact_eleven_categories_require_traceable_evidence_not_causal_claim(
    trade, category
):
    packet, pin = trade
    result = run(with_fact(packet, category), pin)
    observation = result.observations[0]
    assert observation.category == category
    assert observation.evidence_ids == ("fact_0", "fill_0")
    assert observation.status == (
        "insufficient_single_trade_evidence"
        if category == "Strategy Edge Failure"
        else "source_claim"
    )
    assert not observation.causality_established
    assert result.primary_cause == "unknown"
    assert not result.strategy_edge_established


def test_missing_fact_reference_rejected(trade):
    packet, pin = trade
    packet = with_fact(packet, "Wrong Direction")
    fact = packet.facts[0].model_copy(update={"evidence_ids": ("missing",)})
    with pytest.raises(ForensicsError):
        run(packet.model_copy(update={"facts": (fact,)}), pin)


def test_fact_cannot_precede_cited_source_capture(trade):
    packet, pin = trade
    packet = with_fact(packet, "Regime Mismatch")
    fact = packet.facts[0].model_copy(update={"recorded_at": at(59)})
    with pytest.raises(ForensicsError, match="fact_precedes_referenced_evidence"):
        run(packet.model_copy(update={"facts": (fact,)}), pin)


def test_plan_deviations_are_observations_not_loss_based_root_causes(trade):
    packet, _ = trade
    packet, pin = rebind(
        packet,
        entry_window_start=at(15),
        entry_deadline=at(20),
        entry_zone_low=D("99.5"),
        entry_zone_high=D("100.5"),
        max_adverse_slippage_bps=D(10),
    )
    result = run(packet, pin)
    assert result.pnl.value > 0
    assert {item.category for item in result.observations} == {
        "Early Entry",
        "Late Entry",
        "Chasing Entry",
        "Execution Error",
    }
    assert all(
        item.status == "computed_fact" and not item.causality_established
        for item in result.observations
    )


def test_loss_alone_never_implies_wrong_direction_or_edge_failure(trade):
    packet, pin = trade
    flows = tuple(
        item.model_copy(update={"amount": D("-100")})
        if item.kind == "funding"
        else item
        for item in packet.cashflows
    )
    result = run(packet.model_copy(update={"cashflows": flows}), pin)
    assert result.pnl.value < 0
    assert result.primary_cause == "unknown" and result.observations == ()


def test_candidate_rewrite_rejected_by_external_original_pin(trade):
    packet, original_pin = trade
    packet, new_pin = rebind(packet, planned_contracts=D(20))
    assert new_pin != original_pin
    with pytest.raises(ForensicsError, match="original_candidate_hash_mismatch"):
        run(packet, original_pin)
    packet, _ = rebind(packet, report_id="renamed_report")
    with pytest.raises(ForensicsError):
        run(packet, original_pin)


def test_missing_exit_reference_does_not_assume_target_as_benchmark(trade):
    packet, pin = trade
    fills = tuple(
        fill.model_copy(
            update={"reference_price": None, "reference_source_sha256": None}
        )
        for fill in packet.fills
    )
    result = run(packet.model_copy(update={"fills": fills}), pin)
    assert result.exit_slippage_bps.value is None and not result.metrics_complete
    assert result.pnl.value == D("5.68")


@pytest.mark.parametrize(
    "field",
    (
        "source_authenticity_verified",
        "account_evidence_authenticated",
        "execution_authority",
        "strategy_edge_established",
        "calibration_sample_sufficient",
    ),
)
@pytest.mark.parametrize("value", (True, 0))
def test_no_authority_even_after_all_metrics_complete(trade, field, value):
    packet, pin = trade
    result = run(packet, pin).model_copy(update={field: value})
    with pytest.raises(ForensicsError):
        validate_forensics(result, expected_candidate_sha256=pin)


def test_utc_offset_equivalent_source_has_same_hash(trade):
    packet, pin = trade
    candidate = packet.candidate.model_copy(
        update={
            "recorded_at": packet.candidate.recorded_at.astimezone(
                timezone(timedelta(hours=8))
            )
        }
    )
    assert candidate_sha256(candidate) == pin
    later = packet.model_copy(
        update={
            "candidate": candidate,
            "observed_at": packet.observed_at.astimezone(timezone(timedelta(hours=8))),
        }
    )
    assert run(later, pin) == run(packet, pin)


def test_opaque_values_and_custom_timezone_reject_without_callbacks(trade):
    packet, pin = trade
    calls = []

    class Opaque:
        @property
        def __class__(self):
            calls.append("class")
            return Decimal

    class EvilTimezone(tzinfo):
        def utcoffset(self, dt):
            calls.append("offset")
            return timedelta(0)

    for bad in (Opaque(), datetime(2026, 9, 12, tzinfo=EvilTimezone())):
        fill = packet.fills[0].model_copy(update={"price": bad})
        with pytest.raises(ForensicsError):
            run(packet.model_copy(update={"fills": (fill, *packet.fills[1:])}), pin)
    assert calls == []


@pytest.mark.parametrize(
    "mode", ("subclass", "extra", "private", "iterator", "long_tuple", "wrong_nested")
)
def test_exact_nested_model_and_bounded_shape_before_dump(trade, mode):
    packet, pin = trade
    if mode == "subclass":

        class OtherFill(FillEvent):
            pass

        fill = OtherFill.model_validate(packet.fills[0].model_dump(), strict=True)
        packet = packet.model_copy(update={"fills": (fill, *packet.fills[1:])})
    elif mode in ("extra", "private"):
        packet = packet.model_copy()
        object.__setattr__(
            packet,
            "__pydantic_extra__" if mode == "extra" else "__pydantic_private__",
            {"unexpected": "secret"},
        )
    elif mode == "iterator":
        packet = packet.model_copy(update={"fills": iter(packet.fills)})
    elif mode == "long_tuple":
        packet = packet.model_copy(update={"path": (packet.path[0],) * 4097})
    else:
        packet = packet.model_copy(update={"fills": (packet.coverage[0],)})
    with pytest.raises(ForensicsError):
        run(packet, pin)


@pytest.mark.parametrize(
    "mode", ("changed_metric", "changed_hash", "hidden", "wrong_candidate")
)
def test_result_model_copy_cannot_create_verified_calculation(trade, mode):
    packet, pin = trade
    result = run(packet, pin)
    if mode == "changed_metric":
        result = result.model_copy(
            update={"pnl": result.pnl.model_copy(update={"value": D(999)})}
        )
    elif mode == "changed_hash":
        result = result.model_copy(update={"result_sha256": "0" * 64})
    elif mode == "hidden":
        result = result.model_copy(update={"invisible_permission": True})
    else:
        pin = "0" * 64
    with pytest.raises(ForensicsError):
        validate_forensics(result, expected_candidate_sha256=pin)


@pytest.mark.parametrize(
    "payload",
    (
        b'{"a":1,"a":2}',
        b'{"x":NaN}',
        b' {"x":1}',
        b"[" * 1500 + b"0" + b"]" * 1500,
        b'{"x":' + b"1" * 5000 + b"}",
        b"{}",
    ),
)
def test_hostile_bytes_fail_closed(payload):
    with pytest.raises(ForensicsError):
        verify_forensics(
            payload,
            hashlib.sha256(payload).hexdigest(),
            expected_candidate_sha256="0" * 64,
        )


def test_max_bytes_checked_before_json_parse():
    payload = b" " * (MAX_BYTES + 1)
    with pytest.raises(ForensicsError, match="bounded_forensics_bytes_required"):
        verify_forensics(payload, "0" * 64, expected_candidate_sha256="0" * 64)


def test_unknown_exit_reason_not_guessed_from_price(trade):
    packet, pin = trade
    fills = tuple(
        fill.model_copy(update={"exit_reason": "unknown"})
        if fill.role == "exit"
        else fill
        for fill in packet.fills
    )
    result = run(packet.model_copy(update={"fills": fills}), pin)
    assert result.exit_reason == "unknown" and result.pnl.value == D("5.68")
    assert not result.metrics_complete


def test_mixed_exit_reasons_cannot_hide_one_unknown_reason(trade):
    packet, pin = trade
    fills = (
        *packet.fills[:-1],
        packet.fills[-1].model_copy(update={"exit_reason": "unknown"}),
    )
    result = run(packet.model_copy(update={"fills": fills}), pin)
    assert result.exit_reason == "mixed" and not result.metrics_complete
    assert "exit_reason_unconfirmed" in result.limitations


def test_no_observed_fills_with_complete_ledger_does_not_create_a_trade(trade):
    packet, pin = trade
    result = run(
        packet.model_copy(update={"fills": (), "cashflows": (), "path": ()}), pin
    )
    assert result.position_status == "unfilled" and result.entry_contracts.value == 0
    assert result.actual_fill.value is result.pnl.value is result.mfe.value is None
    assert not result.metrics_complete


def test_inverse_or_mismatched_currency_candidate_is_rejected(trade):
    packet, pin = trade
    for field, value in (("contract_kind", "inverse"), ("settlement_currency", "BTC")):
        candidate = packet.candidate.model_copy(update={field: value})
        with pytest.raises(ForensicsError):
            run(packet.model_copy(update={"candidate": candidate}), pin)


def test_frozen_records_and_unknown_values_must_have_reason(trade):
    packet, pin = trade
    result = run(packet, pin)
    with pytest.raises(ValidationError):
        result.inputs.candidate.entry = D(101)
    broken = result.pnl.model_copy(
        update={"value": None, "numerator": None, "denominator": None}
    )
    with pytest.raises(ForensicsError):
        validate_forensics(
            result.model_copy(update={"pnl": broken}), expected_candidate_sha256=pin
        )


def test_module_has_no_io_settings_now_or_execution_calls():
    source = Path(__file__).parents[2] / "app" / "trade_evidence" / "forensics.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    forbidden = {
        "httpx",
        "requests",
        "asyncio",
        "sqlalchemy",
        "app.config",
        "app.database",
        "app.okx_demo",
        "app.exchange",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(item.name in forbidden for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module not in forbidden
        elif isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            assert name not in {
                "now",
                "utcnow",
                "get_settings",
                "open",
                "execute",
                "submit_order",
                "eval",
                "exec",
            }


def test_json_replay_detects_valid_but_changed_metric(trade):
    packet, pin = trade
    raw = freeze_forensics(run(packet, pin), expected_candidate_sha256=pin)
    parsed = json.loads(raw)
    parsed["pnl"].update(value="999", numerator="999", denominator="1")
    modified = json.dumps(
        parsed, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode()
    with pytest.raises(ForensicsError, match="forensics_replay_mismatch"):
        verify_forensics(
            modified,
            hashlib.sha256(modified).hexdigest(),
            expected_candidate_sha256=pin,
        )
