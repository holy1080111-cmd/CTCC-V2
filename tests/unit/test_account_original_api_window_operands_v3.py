"""Finite arithmetic/source-value tests; no native/account/G11 acceptance.

The first test runs the original synthetic wire/prefix validators. Numerical
vectors deliberately construct inert values after that fixture; they test
arithmetic and rejection, and do not authenticate altered rows or packets.
"""

from dataclasses import replace
from datetime import timedelta
from fractions import Fraction

import pytest

from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import account_financial_operands_v2 as financial
from app.trade_qualification import account_original_raw_proof_bank_v1 as bank
from tests.unit import test_account_original_api_population_v2 as source

pytestmark = pytest.mark.asyncio


async def _population(monkeypatch):
    sources, batches, _, _ = await source.original.population(monkeypatch)
    producer = source._consume(sources, batches)
    result = producer.finish_window_operands_v3(
        expected_through=len(batches),
        expected_head=batches[-1].event_sha256,
        checked_at=source.original.CHECKED,
    )
    return result


def _derive(population):
    return bank.derive_original_api_window_operands_v3(
        population, scope=source.original.SCOPE, checked_at=source.original.CHECKED
    )


def _inert_vector(population, rows):
    """Explicit synthetic value seam, never an owned source or passed flag."""
    old = population.original_raw_bank
    document = bank.decode(old.raw_source_json)
    document["members"] = rows
    if any(member["family"] == "fills" for member in rows):
        document["original_instrument_specs"]["BTC-USDT-SWAP"] = [
            {
                "instId": "BTC-USDT-SWAP",
                "instType": "SWAP",
                "ctType": "linear",
                "settleCcy": "USDT",
                "ctValCcy": "BTC",
                "ctMult": "1",
                "ctVal": "0.01",
            }
        ]
    raw = bank.canonical(document)
    old_receipt = bank.decode(old.receipt_json)
    old_receipt["raw_source_sha256"] = bank.sha(raw)
    old_receipt["raw_source_bytes"] = len(raw)
    changed = replace(
        old,
        raw_source_json=raw,
        raw_source_sha256=bank.sha(raw),
        receipt_json=bank.canonical(old_receipt),
    )
    receipt = bank.decode(population.receipt_json)
    receipt["original_raw_bank_sha256"] = changed.raw_source_sha256
    receipt["original_raw_bank_receipt_sha256"] = bank.sha(changed.receipt_json)
    return replace(
        population, original_raw_bank=changed, receipt_json=bank.canonical(receipt)
    )


def _member(family, number, at, **fields):
    stamp = source.ms(at)
    row = (
        {
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "feeCcy": "USDT",
            "fillTime": stamp,
            "billId": str(number),
            "fillPnl": "0",
            "fee": "0",
        }
        if family == "fills"
        else {
            "billId": str(number),
            "ccy": "USDT",
            "ts": stamp,
            "balChg": "0",
            "type": "2",
        }
    )
    row.update(fields)
    value = {
        "family": family,
        "raw": row,
        "row_sha256": journal.digest(journal.canonical(row)),
        "identity_sha256": bank.sha(bank.canonical([family, number])),
        "locators": [{"synthetic_arithmetic_vector": number}],
    }
    if family == "fills":
        value["fill_at"] = at.isoformat()
    return value


def _fraction(value):
    return Fraction(int(value[0]), int(value[1]))


async def test_actual_original_finish_invokes_new_numeric_producer_without_renewing_current(
    monkeypatch,
):
    result = await _population(monkeypatch)
    value = bank.decode(result.receipt_json)
    anchor = result.original_population.coupled_anchors[-1]
    assert (
        value["as_of_original_risk_source_at"]
        == anchor.account_source_updated_at.isoformat()
    )
    assert value["checked_at"] == source.original.CHECKED.isoformat()
    assert value["as_of_original_risk_source_at"] != value["checked_at"]
    assert value["original_population_receipt_sha256"] == bank.sha(
        result.original_population.receipt_json
    )
    assert value["source_windows"]["UTC_day"]["fill_gross"] == ["0", "1"]
    assert value["observed_closed_run_reset"]["original_start_loss_streak_seed"] is None
    assert value["actual_continuous_peak"] is None
    assert result.account_complete is result.execution_authority is False


async def test_signed_fees_fill_time_windows_and_posted_debits_are_separate(
    monkeypatch,
):
    population = (await _population(monkeypatch)).original_population
    as_of = population.coupled_anchors[-1].account_source_updated_at
    rows = [
        _member("fills", 1, as_of - timedelta(seconds=2), fillPnl="12.5", fee="-0.25"),
        _member("fills", 2, as_of - timedelta(days=1), fillPnl="-4", fee="0.1"),
        _member(
            "bills",
            3,
            as_of - timedelta(seconds=1),
            balChg="-3",
            type="8",
            subType="173",
        ),
        _member("bills", 4, as_of, balChg="9"),
    ]
    value = bank.decode(_derive(_inert_vector(population, rows)).receipt_json)
    day, week = (value["source_windows"][name] for name in ("UTC_day", "rolling_7d"))
    assert _fraction(day["fill_gross"]) == Fraction("12.5")
    assert _fraction(day["signed_fill_fee"]) == Fraction("-0.25")
    assert _fraction(week["fill_gross"]) == Fraction("8.5")
    assert _fraction(week["signed_fill_fee"]) == Fraction("-0.15")
    assert _fraction(day["posted_bill_debit"]) == 3
    assert day["exact_full_net_PnL"] is day["account_loss_upper_bound"] is None
    assert (
        value["posted_bill_ts_semantics"]
        == "balance_update_completion_not_funding_accrual"
    )


async def test_future_activity_is_retained_as_original_index_and_never_restamps_current(
    monkeypatch,
):
    population = (await _population(monkeypatch)).original_population
    as_of = population.coupled_anchors[-1].account_source_updated_at
    rows = [_member("fills", 1, as_of + timedelta(milliseconds=1), fillPnl="-8")]
    value = bank.decode(_derive(_inert_vector(population, rows)).receipt_json)
    assert value["observed_activity_after_asof_original_indices"] == [0]
    assert value["source_windows"]["rolling_7d"]["fill_gross"] == ["0", "1"]
    assert (
        "observed_activity_after_original_CURRENT_requires_fresh_capture"
        in value["missing_obligations"]
    )


@pytest.mark.parametrize(
    "damage", ["hash", "duplicate", "missing_fee", "currency", "fill_time"]
)
async def test_missing_conflicting_or_unsupported_original_operands_are_rejected(
    monkeypatch, damage
):
    population = (await _population(monkeypatch)).original_population
    as_of = population.coupled_anchors[-1].account_source_updated_at
    row = _member("fills", 1, as_of, fillPnl="-1", fee="-0.1")
    rows = [row]
    if damage == "duplicate":
        rows.append(row)
    elif damage == "fill_time":
        row["fill_at"] = (as_of - timedelta(seconds=1)).isoformat()
    else:
        if damage == "hash":
            row["raw"]["fillPnl"] = "-999"
        elif damage == "missing_fee":
            del row["raw"]["fee"]
        elif damage == "currency":
            row["raw"]["feeCcy"] = "BTC"
        if damage != "hash":
            row["row_sha256"] = journal.digest(journal.canonical(row["raw"]))
    with pytest.raises(bank.OriginalRawProofBankError):
        _derive(_inert_vector(population, rows))


def _run(number, at, net, fill_id):
    return {
        "original_fill_rows": [{"identity_sha256": fill_id, "row_sha256": fill_id}],
        "net_outcome_arithmetic_reconciled": True,
        "reconciled_closed_run_net_pnl": {"numerator": str(net), "denominator": "1"},
        "closed_at_execution": at.isoformat(),
        "lifecycle_source_sha256": f"{number:064x}",
    }


async def test_realized_positive_reset_can_erase_unknown_seed_for_observed_population_only():
    at = source.original.NOW
    ids = [f"{number:064x}" for number in range(1, 5)]
    runs = [
        _run(1, at - timedelta(seconds=2), -3, ids[0]),
        _run(2, at - timedelta(seconds=1), 4, ids[1]),
        _run(3, at, -1, ids[2]),
        _run(4, at, 0, ids[3]),
    ]
    result = bank._observed_outcome_suffix_v3(
        runs, dict(zip(ids, ids, strict=True)), at
    )
    assert result["observed_suffix_loss_streak_upper_bound"] == 1
    assert result["original_start_loss_streak_seed"] is None
    assert result["account_current_loss_streak_upper_bound"] is None
    assert result["API_asof_tail_coverage_still_required"] is True


async def test_equal_time_loss_is_counted_adversely_after_positive_reset():
    at = source.original.NOW
    ids = [f"{number:064x}" for number in (1, 2)]
    result = bank._observed_outcome_suffix_v3(
        [_run(1, at, 4, ids[0]), _run(2, at, -2, ids[1])],
        dict(zip(ids, ids, strict=True)),
        at,
    )
    assert result["observed_suffix_loss_streak_upper_bound"] == 1


async def test_unjoined_fill_or_unknown_net_never_becomes_zero_seed():
    at = source.original.NOW
    identity = "1" * 64
    run = _run(1, at, 1, identity)
    run["net_outcome_arithmetic_reconciled"] = False
    result = bank._observed_outcome_suffix_v3(
        [run], {identity: identity, "2" * 64: "2" * 64}, at
    )
    assert result["observed_suffix_loss_streak_upper_bound"] is None
    assert "closed_run_net_after_all_costs_unknown" in result["missing_obligations"]
    assert (
        "original_fill_population_not_fully_closed_reconciled"
        in result["missing_obligations"]
    )


@pytest.mark.parametrize("net", ["00", "-0", "1" * 1236])
async def test_noncanonical_or_unbounded_outcome_numbers_are_rejected(net):
    at = source.original.NOW
    identity = "1" * 64
    run = _run(1, at, 1, identity)
    run["reconciled_closed_run_net_pnl"]["numerator"] = net
    with pytest.raises(bank.OriginalRawProofBankError):
        bank._observed_outcome_suffix_v3([run], {identity: identity}, at)


def _coverage(left, right):
    return [
        {
            "family": family,
            "product": product,
            "intervals": [[left.isoformat(), right.isoformat()]],
        }
        for family, product in [
            *(("fills", kind) for kind in bank.capture.INSTRUMENT_TYPES),
            ("bills", "*"),
        ]
    ]


async def test_finite_API_generation_coverage_can_pass_without_global_watermark_claim():
    at = source.original.NOW
    left = at - timedelta(days=7)
    value = bank._generation_coverage_operands_v3(
        _coverage(left, at), started_at=left, as_of=at
    )
    assert value["all_requested_API_generation_domains_covered"] is True
    assert value["missing_domains"] == []
    assert value["generation_coverage_is_execution_or_accrual_finality"] is False


async def test_real_uncovered_tail_and_missing_domain_are_measured_not_zero_filled():
    at = source.original.NOW
    left = at - timedelta(days=7)
    coverage = _coverage(left, at - timedelta(seconds=1))
    removed = coverage.pop()
    value = bank._generation_coverage_operands_v3(coverage, started_at=left, as_of=at)
    assert value["all_requested_API_generation_domains_covered"] is False
    assert value["missing_domains"] == [[removed["family"], removed["product"]]]
    assert all(
        item["requested_generation_window_covered"] is False
        for item in value["domains"]
    )


@pytest.mark.parametrize("damage", ["duplicate", "reverse", "overlap"])
async def test_conflicting_generation_population_or_interval_is_rejected(damage):
    at = source.original.NOW
    left = at - timedelta(days=7)
    coverage = _coverage(left, at)
    if damage == "duplicate":
        coverage.append(coverage[0])
    elif damage == "reverse":
        coverage[0]["intervals"][0].reverse()
    else:
        coverage[0]["intervals"].append([left.isoformat(), at.isoformat()])
    with pytest.raises(bank.OriginalRawProofBankError):
        bank._generation_coverage_operands_v3(coverage, started_at=left, as_of=at)


async def _asof_source_values(monkeypatch, *, changed_equity=False, settlement="USDT"):
    """Original source-value component; the Recorded DTO grants no authority."""
    chain, reference = await source.original.fresh_v6_or_v7(monkeypatch, "V7")
    packet = source.original.lifecycle._packet(chain, reference)
    record = current.RecordedCurrentAccountBundleV4(
        packet,
        reference,
        b"{}",
        "1" * 64,
        "2" * 64,
        "3" * 64,
        source.original.NOW + timedelta(seconds=30),
        10**12,
    )
    sources = []
    for delta in (-121, 21):
        sources.append(
            await source.original.fresh_v5(
                monkeypatch,
                at=source.original.NOW + timedelta(seconds=delta),
                equity="900" if changed_equity and delta == 21 else "1000",
            )
        )
    windows = tuple(
        source.original.observed.ExecutionWindow(
            source.original.lifecycle._packet(part, ref).plan.history_start,
            source.original.lifecycle._packet(part, ref).plan.history_end
            - timedelta(seconds=300),
        )
        for part, ref in sources
    )
    batches = source.original.batches_fixture.original_batches(tuple(sources), windows)
    checked = source.original.NOW + timedelta(seconds=25)
    position_source = _empty_position_archive(
        sources[-1][1],
        checked,
        source.original.lifecycle._packet(*sources[-1]).plan.expected_main_uid,
        settlement=settlement,
    )
    population = source._consume(tuple(sources), batches).finish(
        expected_through=len(batches),
        expected_head=batches[-1].event_sha256,
        checked_at=checked,
        position_source=position_source,
    )
    return population, record, checked, position_source


def _empty_position_archive(reference, checked, main_uid, *, settlement="USDT"):
    """Declared original empty page/archive, never a native account owner."""
    pages, positions = financial.position_pages, financial.positions
    identity = positions._NativePositionIdentityV1(
        source.original.SCOPE.account_id, main_uid, "2", "net_mode"
    )
    start = checked - timedelta(seconds=1)
    plan = pages.PositionHistoryPlanV1(
        "global",
        "https://openapi.okx.com",
        identity.sha256,
        reference.session_binding_sha256,
        start,
        int(source.ms(source.original.NOW - timedelta(days=7, seconds=120))),
    )
    config = bank.canonical(
        {
            "code": "0",
            "msg": "",
            "data": [
                {
                    "uid": identity.uid,
                    "mainUid": identity.main_uid,
                    "acctLv": "2",
                    "posMode": "net_mode",
                    "settleCcy": settlement,
                }
            ],
        }
    )
    page = pages.parse_page(
        plan,
        bank.canonical({"code": "0", "msg": "", "data": []}),
        page_index=0,
        after=None,
        previous_page_sha256=None,
        request_started_at=start + timedelta(milliseconds=4),
        headers_received_at=start + timedelta(milliseconds=5),
        body_completed_at=start + timedelta(milliseconds=6),
    )
    before = positions._RawPage(
        config,
        start + timedelta(milliseconds=1),
        start + timedelta(milliseconds=2),
        start + timedelta(milliseconds=3),
        "c" * 64,
    )
    after = positions._RawPage(
        config,
        start + timedelta(milliseconds=7),
        start + timedelta(milliseconds=8),
        start + timedelta(milliseconds=9),
        "c" * 64,
    )
    raw = positions._freeze_native_source_archive(
        plan, identity, before, (page,), after, ("c" * 64,)
    )
    return financial.NativePositionSourceV2(plan, identity, raw, bank.sha(raw))


async def test_original_current_first_later_complete_API_population_has_finite_positive_window(
    monkeypatch,
):
    population, record, checked, position_source = await _asof_source_values(
        monkeypatch
    )
    result = bank._derive_original_api_window_operands_v3(
        population,
        scope=source.original.SCOPE,
        checked_at=checked,
        _held_current_record=record,
        _original_position_source=position_source,
    )
    value = bank.decode(result.receipt_json)
    join = value["same_original_CURRENT_history_asof_join"]
    assert join["CURRENT_packet_sha256"] == record.reference.packet_sha256
    assert join["CURRENT_completed_at"] == record.packet.completed_at.isoformat()
    assert join["later_history_risk_time_is_CURRENT_time"] is False
    assert join["observed_account_state_changed_after_CURRENT"] is False
    assert (
        value["as_of_original_risk_source_at"]
        != population.coupled_anchors[-1].account_source_updated_at.isoformat()
    )
    for window in value["source_windows"].values():
        assert (
            window["original_API_generation_coverage"][
                "all_requested_API_generation_domains_covered"
            ]
            is True
        )
        assert window["complete_closed_run_window"]["missing_obligations"] == []
        assert window["exact_full_net_PnL"] == ["0", "1"]
    # Empty history is not account genesis or a numeric loss-streak seed.
    assert value["observed_closed_run_reset"]["original_start_loss_streak_seed"] is None
    assert (
        value["observed_closed_run_reset"]["account_current_loss_streak_upper_bound"]
        is None
    )
    assert result.account_complete is result.execution_authority is False


async def test_later_changed_account_requires_fresh_current_without_relabeling_T0(
    monkeypatch,
):
    population, record, checked, position_source = await _asof_source_values(
        monkeypatch, changed_equity=True
    )
    result = bank._derive_original_api_window_operands_v3(
        population,
        scope=source.original.SCOPE,
        checked_at=checked,
        _held_current_record=record,
        _original_position_source=position_source,
    )
    value = bank.decode(result.receipt_json)
    assert (
        value["same_original_CURRENT_history_asof_join"][
            "observed_account_state_changed_after_CURRENT"
        ]
        is True
    )
    assert all(
        window["exact_full_net_PnL"] is None
        for window in value["source_windows"].values()
    )
    assert (
        "observed_account_state_after_original_CURRENT_changed_requires_fresh_capture"
        in value["missing_obligations"]
    )


async def test_old_or_modified_current_record_cannot_replace_original_packet_bytes(
    monkeypatch,
):
    population, record, checked, _position_source = await _asof_source_values(
        monkeypatch
    )
    record = replace(
        record, reference=replace(record.reference, packet_sha256="f" * 64)
    )
    with pytest.raises(
        source.position.OriginalPositionPathError,
        match="coupled_anchor_held_CURRENT_original_bytes_changed",
    ):
        bank._derive_original_api_window_operands_v3(
            population,
            scope=source.original.SCOPE,
            checked_at=checked,
            _held_current_record=record,
        )


async def test_new_receipt_is_charged_to_total_original_retained_budget(monkeypatch):
    population = (await _population(monkeypatch)).original_population
    old = population.original_raw_bank
    total = (
        len(old.raw_source_json)
        + len(old.receipt_json)
        + len(old.financial_facts.receipt_json)
    )
    total += len(population.receipt_json)
    total += sum(
        len(raw)
        for anchor in population.coupled_anchors
        for raw in (
            anchor.original_packet,
            anchor.source_reference_json,
            anchor.separate_balance_measurement_json,
            anchor.receipt_json,
        )
    )
    monkeypatch.setattr(bank, "MAX_API_RETAINED_BYTES_V2", total)
    with pytest.raises(
        bank.OriginalRawProofBankError, match="original_window_result_byte_bound"
    ):
        _derive(population)


@pytest.mark.parametrize("settlement", ["BTC", "", None])
async def test_fee_currency_never_substitutes_for_original_gross_settlement_metadata(
    monkeypatch, settlement
):
    population = (await _population(monkeypatch)).original_population
    as_of = population.coupled_anchors[-1].account_source_updated_at
    vector = _inert_vector(population, [_member("fills", 1, as_of, fillPnl="3")])
    old = vector.original_raw_bank
    document = bank.decode(old.raw_source_json)
    document["original_instrument_specs"]["BTC-USDT-SWAP"][0]["settleCcy"] = settlement
    raw = bank.canonical(document)
    receipt = bank.decode(old.receipt_json)
    receipt["raw_source_sha256"] = bank.sha(raw)
    updated = replace(
        old,
        raw_source_json=raw,
        raw_source_sha256=bank.sha(raw),
        receipt_json=bank.canonical(receipt),
    )
    joined = bank.decode(vector.receipt_json)
    joined["original_raw_bank_sha256"] = updated.raw_source_sha256
    joined["original_raw_bank_receipt_sha256"] = bank.sha(updated.receipt_json)
    with pytest.raises(bank.OriginalRawProofBankError):
        _derive(
            replace(
                vector, original_raw_bank=updated, receipt_json=bank.canonical(joined)
            )
        )


def _complete_trade_members(opened, closed):
    fills = [
        _member(
            "fills",
            1,
            opened,
            side="buy",
            subType="3",
            posSide="net",
            ordId="101",
            tradeId="201",
            fillSz="1",
            fillPx="100",
        ),
        _member(
            "fills",
            2,
            closed,
            side="sell",
            subType="4",
            posSide="net",
            ordId="102",
            tradeId="202",
            fillSz="1",
            fillPx="103",
            fillPnl="3",
        ),
    ]
    bills = []
    for fill in fills:
        raw = fill["raw"]
        bills.append(
            _member(
                "bills",
                int(raw["billId"]),
                closed,
                instType="SWAP",
                instId=raw["instId"],
                posSide="net",
                type="2",
                subType=raw["subType"],
                ordId=raw["ordId"],
                tradeId=raw["tradeId"],
                px=raw["fillPx"],
                sz=raw["fillSz"],
                fee=raw["fee"],
                pnl=raw["fillPnl"],
                balChg=raw["fillPnl"],
                posBalChg="0",
            )
        )
    bills.append(_member("bills", 3, closed, type="8", subType="174", balChg="1"))
    return fills + bills


async def test_complete_holding_inside_window_uses_cumulative_funding_net_without_bill_time_guess():
    at = source.original.NOW
    opened, closed = at - timedelta(seconds=180), at - timedelta(seconds=120)
    members = _complete_trade_members(opened, closed)
    ids = [member["identity_sha256"] for member in members]
    run = _run(1, closed, 4, ids[0])
    run["opened_at_execution"] = opened.isoformat()
    run["original_fill_rows"] = [
        {
            "identity_sha256": member["identity_sha256"],
            "row_sha256": member["row_sha256"],
        }
        for member in members
        if member["family"] == "fills"
    ]
    run["position_funding_total"] = {"numerator": "1", "denominator": "1"}
    result = bank._closed_run_window_net_v3(
        [run],
        members,
        started_at=at - timedelta(days=1),
        as_of=at,
        joined_current=True,
        current_flat=True,
        later_flat=True,
        coverage_complete=True,
        activity_after_asof=False,
        position_archive_complete=True,
    )
    assert result["exact_net_PnL"] == ["4", "1"]
    assert result["individual_funding_accrual_events"] is None
    assert result["bill_ts_used_as_accrual"] is False
    assert result["missing_obligations"] == []


async def test_crossing_holding_does_not_allocate_net_funding_into_daily_window():
    at = source.original.NOW
    opened, closed = at - timedelta(days=2), at - timedelta(seconds=120)
    members = _complete_trade_members(opened, closed)
    run = _run(1, closed, 4, members[0]["identity_sha256"])
    run["opened_at_execution"] = opened.isoformat()
    run["original_fill_rows"] = [
        {
            "identity_sha256": member["identity_sha256"],
            "row_sha256": member["row_sha256"],
        }
        for member in members
        if member["family"] == "fills"
    ]
    result = bank._closed_run_window_net_v3(
        [run],
        members,
        started_at=at - timedelta(days=1),
        as_of=at,
        joined_current=True,
        current_flat=True,
        later_flat=True,
        coverage_complete=True,
        activity_after_asof=False,
        position_archive_complete=True,
    )
    assert result["exact_net_PnL"] is None
    assert (
        "holding_crosses_window_funding_allocation_unknown"
        in result["missing_obligations"]
    )


@pytest.mark.parametrize("seconds,known", [(59, False), (60, False), (61, True)])
async def test_recent_close_requires_documented_assessment_to_finish_with_strict_causal_order(
    seconds, known
):
    at = source.original.NOW
    closed = at - timedelta(seconds=seconds)
    opened = closed - timedelta(seconds=120)
    members = _complete_trade_members(opened, closed)
    run = _run(1, closed, 4, members[0]["identity_sha256"])
    run["opened_at_execution"] = opened.isoformat()
    run["original_fill_rows"] = [
        {
            "identity_sha256": member["identity_sha256"],
            "row_sha256": member["row_sha256"],
        }
        for member in members
        if member["family"] == "fills"
    ]
    result = bank._closed_run_window_net_v3(
        [run],
        members,
        started_at=at - timedelta(days=1),
        as_of=at,
        joined_current=True,
        current_flat=True,
        later_flat=True,
        coverage_complete=True,
        activity_after_asof=False,
        position_archive_complete=True,
    )
    assert (result["exact_net_PnL"] is not None) is known
    assert (
        "recent_close_funding_assessment_completion_or_bound_unknown"
        in result["missing_obligations"]
    ) is not known


@pytest.mark.parametrize("distance,known", [(59, False), (60, False), (61, True)])
async def test_window_start_does_not_silently_allocate_prior_assessment_to_new_holding(
    distance, known
):
    at = source.original.NOW
    left = at - timedelta(seconds=300)
    opened, closed = left + timedelta(seconds=distance), at - timedelta(seconds=120)
    members = _complete_trade_members(opened, closed)
    run = _run(1, closed, 4, members[0]["identity_sha256"])
    run["opened_at_execution"] = opened.isoformat()
    run["original_fill_rows"] = [
        {
            "identity_sha256": member["identity_sha256"],
            "row_sha256": member["row_sha256"],
        }
        for member in members
        if member["family"] == "fills"
    ]
    result = bank._closed_run_window_net_v3(
        [run],
        members,
        started_at=left,
        as_of=at,
        joined_current=True,
        current_flat=True,
        later_flat=True,
        coverage_complete=True,
        activity_after_asof=False,
        position_archive_complete=True,
    )
    assert (result["exact_net_PnL"] is not None) is known
    assert (
        "opening_may_overlap_prior_window_funding_assessment"
        in result["missing_obligations"]
    ) is not known


async def test_position_archive_hash_alone_cannot_complete_a_window(monkeypatch):
    population, record, checked, _position_source = await _asof_source_values(
        monkeypatch
    )
    result = bank._derive_original_api_window_operands_v3(
        population,
        scope=source.original.SCOPE,
        checked_at=checked,
        _held_current_record=record,
    )
    value = bank.decode(result.receipt_json)
    assert value["original_position_source_gaps"] == [
        "original_position_archive_not_replayed_in_this_derivation"
    ]
    assert all(
        window["exact_full_net_PnL"] is None
        for window in value["source_windows"].values()
    )


async def test_actual_position_config_unknown_currency_stays_unknown(monkeypatch):
    population, record, checked, position_source = await _asof_source_values(
        monkeypatch, settlement=None
    )
    result = bank._derive_original_api_window_operands_v3(
        population,
        scope=source.original.SCOPE,
        checked_at=checked,
        _held_current_record=record,
        _original_position_source=position_source,
    )
    value = bank.decode(result.receipt_json)
    assert value["original_position_source_gaps"] == [
        "native_position_account_settlement_setting_unknown_or_different"
    ]
    assert all(
        window["exact_full_net_PnL"] is None
        for window in value["source_windows"].values()
    )


@pytest.mark.parametrize("age,excluded", [(59, False), (60, False), (61, True)])
async def test_only_proven_old_normal_position_can_be_excluded_from_window(
    age, excluded
):
    left = source.original.NOW - timedelta(days=1)
    updated = left - timedelta(seconds=age)
    row = {
        "type": "2",
        "instType": "SWAP",
        "ccy": "USDT",
        "posSide": "net",
        "instId": "BTC-USDT-SWAP",
        "closeTotalPos": "1",
        "cTime": source.ms(updated - timedelta(seconds=10)),
        "uTime": source.ms(updated),
    }
    pin = journal.digest(journal.canonical(row))
    result = bank._unjoined_position_window_v3(
        [{"row_sha256": pin}], [{"raw": row, "row_sha256": pin}], started_at=left
    )
    assert result["excluded_original_row_sha256"] == ([pin] if excluded else [])
    assert result["unresolved_count"] == (0 if excluded else 1)
    assert "closed_at_execution" not in result


@pytest.mark.parametrize("damage", ["duplicate", "changed_bytes", "missing_membership"])
async def test_unjoined_position_exclusion_requires_exact_original_population(damage):
    at = source.original.NOW
    row = {
        "type": "2",
        "instType": "SWAP",
        "ccy": "USDT",
        "posSide": "net",
        "instId": "BTC-USDT-SWAP",
        "closeTotalPos": "1",
        "cTime": source.ms(at - timedelta(days=2)),
        "uTime": source.ms(at - timedelta(days=1)),
    }
    pin = journal.digest(journal.canonical(row))
    original = [{"raw": row, "row_sha256": pin}]
    unjoined = [{"row_sha256": pin}]
    if damage == "duplicate":
        unjoined.append(unjoined[0])
    elif damage == "changed_bytes":
        row["closeTotalPos"] = "2"
    else:
        original.clear()
    with pytest.raises(bank.OriginalRawProofBankError):
        bank._unjoined_position_window_v3(unjoined, original, started_at=at)
