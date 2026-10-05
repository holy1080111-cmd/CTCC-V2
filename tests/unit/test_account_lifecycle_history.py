"""Synthetic original B1/index mechanics only; no net-risk/native acceptance."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest

from app.database.repositories.account_observation_index import (
    ObservationBatchReadback,
    _membership,
)
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_lifecycle_history as history
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope
from tests.unit.test_account_current_source_verifier import flat_pages
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_qualification_account_capture import NOW, UID, ms, row
from tests.unit.test_qualification_account_collector import credentials, script
from tests.unit.test_qualification_account_runtime import regional

SCOPE = LedgerScope(account_id=UID, settlement_currency="USDT")
CASES = tuple(f"A{i:02d}" for i in range(1, 25))


async def original_capture(monkeypatch, at, *, base, filled, case, variant=False):
    session, harness, _, args, events = setup(monkeypatch)
    selected = regional(
        created_at=at,
        history_start=at - timedelta(days=7),
        history_end=at - timedelta(seconds=20),
    )
    session = type(session)(
        credentials=credentials(),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    # Entire synthetic source and clock are declared before acquisition.
    harness.clock.overrides = {
        i: at + timedelta(milliseconds=i + 1) for i in range(2500)
    }
    args["barrier_completed_at"] = at - timedelta(seconds=2)
    pages = flat_pages()
    stamp = ms(at - timedelta(milliseconds=1))
    pages["account_position_risk"][0][0]["ts"] = stamp
    balance = pages["balance"][0][0]
    balance["uTime"] = stamp
    for detail in balance["details"]:
        detail["uTime"] = stamp
    if filled:
        enter, leave = base + timedelta(seconds=300), base + timedelta(seconds=900)
        if case == "A04":
            enter -= timedelta(days=7)
        opening = row(
            "fills_history",
            "901",
            ordId="801",
            tradeId="701",
            clOrdId="originalOpen",
            side="buy",
            fillSz="4",
            fillPx="100",
            fillPnl="0",
            fee="-0.01",
            fillTime=ms(enter),
            ts=ms(enter + timedelta(seconds=1)),
        )
        closing = row(
            "fills_history",
            "902",
            ordId="802",
            tradeId="702",
            clOrdId="originalClose",
            side="sell",
            fillSz="4",
            fillPx="110",
            fillPnl="0.4",
            fee="-0.01",
            fillTime=ms(leave),
            ts=ms(leave + timedelta(seconds=1)),
        )
        if case == "A05":
            opening["fee"] = closing["fee"] = "-0.3"
        if case == "A06":
            closing["fee"] = "0.01"
        if case == "A13":
            opening["fee"] = closing["fee"] = "-0.2"
        if case == "A16":
            opening = None
        if case == "A17":
            closing["fillTime"] = ms(enter)
        if case == "A18":
            closing["fillSz"] = "5"
        if case == "A20":
            closing["feeCcy"] = "BTC"
        if case == "A21" and variant:
            closing["fee"] = "-0.02"  # New original response, not captured-row editing.
        fills = [closing, opening] if opening is not None else [closing]
        if case == "A03":
            fills = [opening]
        order_rows = [
            row(
                "orders_history_archive",
                "802",
                side="sell",
                clOrdId="originalClose",
                reduceOnly=True,
                cTime=ms(leave - timedelta(seconds=1)),
                uTime=ms(leave),
                sz="4",
                accFillSz="4",
            ),
            row(
                "orders_history_archive",
                "801",
                side="buy",
                clOrdId="originalOpen",
                reduceOnly=False,
                cTime=ms(enter - timedelta(seconds=1)),
                uTime=ms(enter),
                sz="4",
                accFillSz="4",
            ),
        ]
        if case == "A03":
            order_rows = [order_rows[1]]
            pages["positions"] = [
                [
                    row(
                        "positions",
                        "950",
                        pos="4",
                        avgPx="100",
                        cTime=ms(enter),
                        uTime=stamp,
                        margin="5",
                    )
                ]
            ]
            pages["account_position_risk"][0][0]["posData"] = [
                {
                    "posId": "950",
                    "instId": opening["instId"],
                    "instType": "SWAP",
                    "posSide": "net",
                    "mgnMode": "cross",
                    "pos": "4",
                    "ccy": "USDT",
                }
            ]
        if case == "A19_ROLE_MISSING":
            order_rows = []
        if case == "A02":
            closing["fillSz"] = "2"
            closing["fillPnl"] = "0.2"
            closing["billId"], closing["tradeId"], closing["ordId"] = (
                "903",
                "703",
                "803",
            )
            partial_at = base + timedelta(seconds=330)
            partial = row(
                "fills_history",
                "902",
                ordId="802",
                tradeId="702",
                clOrdId="originalPartial",
                side="sell",
                fillSz="2",
                fillPx="105",
                fillPnl="0.1",
                fee="-0.01",
                fillTime=ms(partial_at),
                ts=ms(partial_at + timedelta(seconds=1)),
            )
            fills = [closing, partial, opening]
            order_rows[0]["ordId"] = "803"
            order_rows[0]["sz"] = order_rows[0]["accFillSz"] = "2"
            order_rows.insert(
                1,
                row(
                    "orders_history_archive",
                    "802",
                    side="sell",
                    clOrdId="originalPartial",
                    reduceOnly=True,
                    cTime=ms(partial_at - timedelta(seconds=1)),
                    uTime=ms(partial_at),
                    sz="2",
                    accFillSz="2",
                ),
            )
        if case == "A04":
            fills = [opening] if at < base else [closing]
            order_rows = [order_rows[1]] if at < base else [order_rows[0]]
        if case == "A19":
            other_enter, other_leave = (
                base + timedelta(seconds=400),
                base + timedelta(seconds=800),
            )
            other_name = "ETH-USDT-SWAP"
            closing["billId"], closing["tradeId"], closing["ordId"] = (
                "904",
                "704",
                "804",
            )
            order_rows[0]["ordId"] = "804"
            fills = [
                closing,
                row(
                    "fills_history",
                    "903",
                    instId=other_name,
                    ordId="803",
                    tradeId="703",
                    clOrdId="originalOtherClose",
                    side="sell",
                    fillSz="2",
                    fillPx="220",
                    fillPnl="4",
                    fee="-0.02",
                    fillTime=ms(other_leave),
                    ts=ms(other_leave + timedelta(seconds=1)),
                ),
                row(
                    "fills_history",
                    "902",
                    instId=other_name,
                    ordId="802",
                    tradeId="702",
                    clOrdId="originalOtherOpen",
                    side="buy",
                    fillSz="2",
                    fillPx="200",
                    fillPnl="0",
                    fee="-0.02",
                    fillTime=ms(other_enter),
                    ts=ms(other_enter + timedelta(seconds=1)),
                ),
                opening,
            ]
            order_rows = [
                order_rows[0],
                row(
                    "orders_history_archive",
                    "803",
                    instId=other_name,
                    side="sell",
                    clOrdId="originalOtherClose",
                    reduceOnly=True,
                    cTime=ms(other_leave - timedelta(seconds=1)),
                    uTime=ms(other_leave),
                    sz="2",
                    accFillSz="2",
                ),
                row(
                    "orders_history_archive",
                    "802",
                    instId=other_name,
                    side="buy",
                    clOrdId="originalOtherOpen",
                    reduceOnly=False,
                    cTime=ms(other_enter - timedelta(seconds=1)),
                    uTime=ms(other_enter),
                    sz="2",
                    accFillSz="2",
                ),
                order_rows[1],
            ]
        pages["fills_recent"] = [fills, []]
        pages["fills_history"] = [fills, []]
        pages["orders_history_archive"] = [order_rows, []] if order_rows else [[]]
        pages["orders_history_recent"] = [order_rows, []] if order_rows else [[]]
        if case == "A06":
            mirrors = [
                row(
                    "bills_archive",
                    "902",
                    type="2",
                    subType="1",
                    instId=closing["instId"],
                    fee=closing["fee"],
                    pnl=closing["fillPnl"],
                    ts=closing["ts"],
                )
            ]
            pages["bills_archive"] = [mirrors, []]
            pages["bills_recent"] = [mirrors, []]
        if case in {"A07", "A10", "A11"} or (case == "A09" and variant):
            generation = (
                leave
                if case == "A11"
                else base + timedelta(seconds=3000)
                if case == "A09"
                else leave + timedelta(seconds=200)
            )
            bill = row(
                "bills_archive",
                "990",
                instId=closing["instId"],
                balChg="-0.03",
                ts=ms(generation),
            )
            pages["bills_archive"] = [[bill], []]
            pages["bills_recent"] = [[bill], []]
        if case == "A20":
            unknown = row(
                "bills_archive", "990", type="99", ts=ms(leave + timedelta(seconds=200))
            )
            pages["bills_archive"] = [[unknown], []]
            pages["bills_recent"] = [[unknown], []]
    if case == "A19":
        pages["account_instruments"] = [
            [
                row("account_instruments"),
                row(
                    "account_instruments",
                    instId="ETH-USDT-SWAP",
                    baseCcy="ETH",
                    ctValCcy="ETH",
                    ctVal="0.1",
                ),
            ]
        ]
    harness.script = script(pages=pages)
    await bootstrap.collect_bootstrap_recorded(session, **args)
    harness.assert_closed()
    return tuple(events)


def original_index(sources, window):
    """Declared synthetic DB0021 export derived from original raw sources."""
    batches, documents = [], []
    counts = {"source": 0, "coverage": 0, "finding": 0}
    scope_sha = journal.digest(journal.canonical(["demo", UID, "USDT"]))
    previous = anchor_sha = None
    for sequence, source in enumerate(sources, 1):
        single = observed.replay_observation_index(
            (source,), scope=SCOPE, window=window
        )
        facts, coverage = _membership(single)
        replay = observed._replay(
            tuple(sources[:sequence]),
            SCOPE,
            window,
            observed.POLICY_SHA256,
            adjacent=False,
        )
        receipt = json.loads(replay.receipt_json)
        findings = receipt["findings"]
        memberships = {"source": facts, "coverage": coverage, "finding": findings}
        for name, values in memberships.items():
            counts[name] += len(values)
        anchor = {
            "schema_version": "ctcc.account_observation_continuation.v1",
            "policy_sha256": observed.POLICY_SHA256,
            "scope_sha256": scope_sha,
            "through_sequence": sequence,
            "previous_sequence": sequence - 1,
            "previous_event_sha256": previous,
            "previous_anchor_sha256": anchor_sha,
            "source_facts_through_sequence": counts["source"],
            "coverage_through_sequence": counts["coverage"],
            "unresolved_findings_through_sequence": counts["finding"],
            "current_source_membership_sha256": journal.digest(
                journal.canonical(facts)
            ),
            "current_coverage_membership_sha256": journal.digest(
                journal.canonical(coverage)
            ),
            "verified_witnesses": [
                {
                    "sequence": b.sequence,
                    "event_sha256": b.event_sha256,
                    "source_membership_sha256": d["source_membership_sha256"],
                    "coverage_membership_sha256": d["coverage_membership_sha256"],
                }
                for b, d in zip(batches, documents, strict=True)
            ],
            "required_proof_budget_exceeded": False,
        }
        receipt["continuation_anchor"] = anchor
        receipt_raw = journal.canonical(receipt)
        document = {
            "schema_version": "ctcc.account_observation_batch.v1",
            "scope_sha256": scope_sha,
            "sequence": sequence,
            "previous_sha256": previous,
            "reference": observed.reference_document(source[1]),
            "window": observed.window_document(window),
            "policy_sha256": observed.POLICY_SHA256,
            "receipt_sha256": journal.digest(receipt_raw),
            "anchor_json": journal.canonical(anchor).decode(),
            "anchor_sha256": journal.digest(journal.canonical(anchor)),
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
        for name, values in memberships.items():
            document[name + "_membership_sha256"] = journal.digest(
                journal.canonical(values)
            )
            document[name + "_membership_count"] = len(values)
        raw = journal.canonical(document)
        previous, anchor_sha = journal.digest(raw), document["anchor_sha256"]
        batches.append(
            ObservationBatchReadback(
                sequence, previous, raw, observed.ObservationReplay(receipt_raw)
            )
        )
        documents.append(document)
    return tuple(batches)


async def fixture(monkeypatch, case="A01"):
    base = NOW
    if case == "A02":
        base = datetime(2026, 9, 1, 23, 54, tzinfo=UTC)
    anchor_at = base - timedelta(days=7) if case == "A04" else base
    first = await original_capture(
        monkeypatch, anchor_at, base=base, filled=False, case=case
    )
    last_at = base + timedelta(seconds=4000 if case == "A22" else 1800)
    last = await original_capture(
        monkeypatch, last_at, base=base, filled=True, case=case
    )
    chains = [first, last]
    if case == "A04":
        middle = await original_capture(
            monkeypatch,
            base - timedelta(days=7) + timedelta(seconds=900),
            base=base,
            filled=True,
            case=case,
        )
        chains.insert(1, middle)
    if case in {"A09", "A21"}:
        chains.append(
            await original_capture(
                monkeypatch,
                base + timedelta(seconds=3600),
                base=base,
                filled=True,
                case=case,
                variant=True,
            )
        )
    sources = tuple((chain, observed.source_reference(chain)) for chain in chains)
    end = base + timedelta(seconds=1800 if case == "A23" else 1200)
    start = (
        end - timedelta(days=7)
        if case == "A04"
        else base + timedelta(seconds=1000)
        if case == "A12"
        else base - timedelta(hours=1)
    )
    window = observed.ExecutionWindow(start, end)
    batches = original_index(sources, window)
    return sources, {
        "scope": SCOPE,
        "window": window,
        "index_batches": batches,
        "expected_index_head_sha256": batches[-1].event_sha256,
        "expected_source_set_sha256": history.source_set_sha256(sources),
    }


def replay(value):
    result = history.replay_account_lifecycle_history(value[0], **value[1])
    data = json.loads(result.receipt_json)
    assert data["snapshot"] is data["owner"] is data["net_loss_window"] is None
    assert data["loss_streak_at_history_start"] is None
    assert data["historical_native_hwm_verified"] is False
    assert data["current_tail_complete"] is False
    assert not data["account_complete"] and not data["account_revision_published"]
    assert not result.execution_authority and result.admission == "DENY"
    return data


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES)
async def test_preregistered_original_source_projection(monkeypatch, case):
    baseline = await fixture(monkeypatch)
    baseline_data = replay(baseline)
    assert baseline_data["observed_fill_cashflow_total"] == {
        "numerator": "19",
        "denominator": "50",
    }
    assert baseline_data["observed_lifecycles"][0]["source_observed_closed_gross"] == {
        "numerator": "2",
        "denominator": "5",
    }
    assert (
        baseline_data["observed_lifecycles"][0]["source_observed_zeroing_fill_at"]
        is not None
    )
    value = await fixture(monkeypatch, case)
    data = replay(value)
    outcome = data["observed_lifecycles"][0]
    assert outcome["net_pnl"] is outcome["funding_amount"] is None
    assert outcome["positive_net_reset_proven"] is False
    if case == "A02":
        times = sorted(
            datetime.fromisoformat(m["execution_at"])
            for m in data["observed_fill_movements"]
        )
        assert len(times) == 3 and times[0].date() == times[1].date() != times[2].date()
        assert outcome["source_observed_closed_gross"] == {
            "numerator": "3",
            "denominator": "10",
        }
    elif case == "A03":
        assert "history_inventory_remains_open" in outcome["reasons"]
        assert data["observed_fill_cashflow_total"] == {
            "numerator": "-1",
            "denominator": "100",
        }
    elif case == "A04":
        assert len(data["observed_fill_movements"]) == 1
        assert len(outcome["original_source_rows"]) == 2
        assert value[1]["window"].ended_at - value[1]["window"].started_at == timedelta(
            days=7
        )
        assert "source_observations_require_reconciliation" in data["blocking_reasons"]
        assert any(f["kind"] == "measurement_gap" for f in data["findings"])
    elif case == "A05":
        assert data["observed_fill_cashflow_total"] == {
            "numerator": "-1",
            "denominator": "5",
        }
    elif case == "A06":
        assert data["observed_fill_cashflow_total"] == {
            "numerator": "2",
            "denominator": "5",
        }
        assert data["bills"][0]["kind"] == "reconciled_fee_pnl_mirror_not_summed"
    elif case in {"A07", "A09", "A10", "A11"}:
        assert data["bills"][0]["effective_accrual_at"] is None
        assert data["bills"][0]["kind"] == "funding_unknown"
        if case == "A09":
            assert len(value[0]) == 3
            assert datetime.fromisoformat(
                data["bills"][0]["generation_at"]
            ) > NOW + timedelta(seconds=1780)
        if case in {"A10", "A11"}:
            with pytest.raises(TypeError):
                history.replay_account_lifecycle_history(
                    value[0],
                    **value[1],
                    funding_accrual_at=data["bills"][0]["generation_at"],
                )
    elif case == "A08":
        assert data["bills"] == []
        assert "funding_accrual_provenance_missing" in outcome["reasons"]
    elif case == "A12":
        assert (
            datetime.fromisoformat(outcome["source_observed_zeroing_fill_at"])
            < value[1]["window"].started_at
        )
        assert data["observed_fill_movements"] == []
        assert outcome["positive_net_reset_proven"] is False
    elif case == "A13":
        assert data["observed_fill_cashflow_total"] == {
            "numerator": "0",
            "denominator": "1",
        }
        assert data["loss_streak_at_history_start"] is None
    elif case in {"A14", "A15"}:
        assert (
            outcome["source_observed_starting_inventory"]["meaning"]
            == "bounded_observed_flat_not_exchange_atomic_or_account_genesis"
        )
        with pytest.raises(TypeError):
            history.replay_account_lifecycle_history(
                value[0], **value[1], genesis_proven=True
            )
    elif case == "A16":
        assert "exit_exceeds_known_inventory" in outcome["reasons"]
    elif case == "A17":
        assert "history_equal_time_inventory_ambiguous" in outcome["reasons"]
    elif case == "A18":
        assert "exit_exceeds_known_inventory" in outcome["reasons"]
    elif case == "A19":
        assert len(data["observed_lifecycles"]) == 2
        assert {m["instrument_id"] for m in data["observed_lifecycles"]} == {
            "BTC-USDT-SWAP",
            "ETH-USDT-SWAP",
        }
        assert all(
            m["source_observed_zeroing_fill_at"] is not None
            for m in data["observed_lifecycles"]
        )
        assert data["loss_streak_at_history_start"] is None
        missing_role = replay(await fixture(monkeypatch, "A19_ROLE_MISSING"))[
            "observed_lifecycles"
        ][0]
        assert missing_role["source_observed_zeroing_fill_at"] is None
        assert "history_fill_role_or_time_unknown" in missing_role["reasons"]
    elif case == "A20":
        assert data["observed_fill_cashflow_total"] is None
        assert "cashflow_product_or_currency_unsupported" in data["blocking_reasons"]
        assert "history_account_movement_unclassified" in data["blocking_reasons"]
    elif case == "A21":
        assert any(f["kind"] == "conflicting_overlap" for f in data["findings"])
        assert data["observed_fill_cashflow_total"] is None
        assert len(outcome["original_source_rows"]) == 3
    elif case == "A22":
        assert any(f["kind"] == "measurement_gap" for f in data["findings"])
        assert data["observed_fill_cashflow_total"] is None
    elif case == "A23":
        assert "generation_observation_tail_below_policy" in data["blocking_reasons"]
        assert data["current_tail_complete"] is False
    else:
        assert "funding_accrual_provenance_missing" in data["blocking_reasons"]
    # These are successful DENY diagnostics, not full A07/A08/net/reset/genesis.
    assert (
        history.replay_account_lifecycle_history(value[0], **value[1]).receipt_json
        == history.replay_account_lifecycle_history(value[0], **value[1]).receipt_json
    )


COUNTERCASES = (
    "sequence_bool",
    "missing_prefix",
    "head_changed",
    "source_pin_changed",
    "source_foreign",
    "index_foreign",
    "receipt_row_changed",
    "membership_count_bool",
    "witness_index_bool",
    "funding_DTO",
    "completeness_override",
    "streak_override",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", COUNTERCASES)
async def test_ingress_denies_authority_and_exact_original_membership(
    monkeypatch, case
):
    sources, args = await fixture(monkeypatch)
    assert replay((sources, args))["observed_fill_cashflow_total"] is not None
    changed = dict(args)
    batches = list(args["index_batches"])
    expected = "history_index_prefix_invalid"
    if case == "sequence_bool":
        batches[0] = replace(batches[0], sequence=True)
    elif case == "missing_prefix":
        batches = batches[1:]
        expected = "history_index_prefix_missing"
    elif case == "head_changed":
        changed["expected_index_head_sha256"] = "0" * 64
        expected = "history_index_head_mismatch"
    elif case == "source_pin_changed":
        changed["expected_source_set_sha256"] = "0" * 64
        expected = "history_source_set_pin_mismatch"
    elif case == "source_foreign":
        sources = ((object(), sources[0][1]), *sources[1:])
        expected = "history_original_chain_required"
    elif case == "index_foreign":
        batches[0] = object()
    elif case in {"funding_DTO", "completeness_override", "streak_override"}:
        key = {
            "funding_DTO": "funding_event",
            "completeness_override": "complete",
            "streak_override": "loss_streak_at_history_start",
        }[case]
        changed[key] = {"complete": True} if case == "funding_DTO" else True
        with pytest.raises(TypeError):
            history.replay_account_lifecycle_history(sources, **changed)
        return
    else:
        item = batches[-1]
        receipt = json.loads(item.replay.receipt_json)
        document = json.loads(item.document_json)
        if case == "receipt_row_changed":
            receipt["source_index"][0]["fill_at"] = NOW.isoformat()
            expected = "history_index_row_query_replay_mismatch"
        elif case == "membership_count_bool":
            document["source_membership_count"] = True
            expected = "history_index_original_membership_mismatch"
        elif case == "witness_index_bool":
            receipt["continuation_anchor"]["verified_witnesses"][0]["sequence"] = True
            expected = "history_index_witness_invalid"
        raw_receipt = journal.canonical(receipt)
        document["receipt_sha256"] = journal.digest(raw_receipt)
        document["anchor_json"] = journal.canonical(
            receipt["continuation_anchor"]
        ).decode()
        document["anchor_sha256"] = journal.digest(
            journal.canonical(receipt["continuation_anchor"])
        )
        raw_document = journal.canonical(document)
        batches[-1] = replace(
            item,
            document_json=raw_document,
            event_sha256=journal.digest(raw_document),
            replay=observed.ObservationReplay(raw_receipt),
        )
        changed["expected_index_head_sha256"] = batches[-1].event_sha256
    changed["index_batches"] = tuple(batches)
    with pytest.raises(history.AccountLifecycleHistoryError, match=expected):
        history.replay_account_lifecycle_history(sources, **changed)


def test_extracted_math_rejects_foreign_scalar_before_callbacks():
    class Foreign:
        def __gt__(self, other):
            raise AssertionError("foreign callback")

    from app.trade_evidence.inventory_math import InventoryMathError, reduce_inventory

    baseline = reduce_inventory(
        direction="long",
        unit=Fraction(1),
        fills=(("entry", "buy", Fraction(1), Fraction(1), NOW),),
        funding_times=(),
    )
    assert baseline[:2] == (Fraction(1), Fraction(0))
    assert baseline[-1] == [(NOW, Fraction(0), ((Fraction(1), Fraction(1)),))]
    with pytest.raises(InventoryMathError, match="inventory_math_input_invalid"):
        reduce_inventory(direction="long", unit=Foreign(), fills=(), funding_times=())
    with pytest.raises(InventoryMathError, match="inventory_math_input_invalid"):
        reduce_inventory(
            direction="long",
            unit=Fraction(1),
            fills=(("entry", "buy", Fraction(1), Fraction(1), True),),
            funding_times=(),
        )
