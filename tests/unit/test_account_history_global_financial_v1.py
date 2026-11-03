"""HELD original raw-fixture tests; no native/source-owner/risk acceptance.

All rows are declared before original capture. The 33 case really acquires33
new B1 chains. B3 wire documents use the existing original-index fixture with
only CURRENT+previous witness to preserve the oldcap32; no checkpoint or native
stream acceptance is claimed by this separate reusable accumulator slice.
"""

import asyncio
import json
from datetime import timedelta

import pytest

from app.database.repositories.account_observation_index import (
    ObservationBatchReadback,
    _membership,
)
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import (
    account_history_global_financial_v1 as global_history,
)
from app.trade_qualification import account_observation_index as observed
from tests.unit import test_account_lifecycle_history as original
from tests.unit.test_account_financial_operands_v2 import native_position_source
from tests.unit.test_qualification_account_capture import NOW, UID

SCOPE = original.SCOPE
WINDOW = observed.ExecutionWindow(
    NOW - timedelta(hours=1), NOW + timedelta(seconds=1200)
)
CHECKED = NOW + timedelta(seconds=2010)


def original_batches(sources, window):
    """Declared synthetic DB0021 export derived from original raw sources."""
    batches, documents = [], []
    counts = {"source": 0, "coverage": 0, "finding": 0}
    scope_sha = journal.digest(journal.canonical(["demo", UID, "USDT"]))
    previous = anchor_sha = None
    windows = window if type(window) is tuple else (window,) * len(sources)
    assert len(windows) == len(sources)
    for sequence, source in enumerate(sources, 1):
        window = windows[sequence - 1]
        single = observed.replay_observation_index(
            (source,), scope=SCOPE, window=window
        )
        facts, coverage = _membership(single)
        replay = observed._replay(
            tuple(sources[max(0, sequence - 2) : sequence]),
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
                for b, d in zip(batches[-1:], documents[-1:], strict=True)
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


async def acquired(monkeypatch, *, opens=1, case="A01"):
    chains = [
        await original.original_capture(
            monkeypatch, NOW, base=NOW, filled=False, case="A01"
        )
    ]
    for _ in range(opens):
        chains.append(
            await original.original_capture(
                monkeypatch,
                NOW + timedelta(seconds=600),
                base=NOW,
                filled=True,
                case="A03",
            )
        )
    chains.append(
        await original.original_capture(
            monkeypatch, NOW + timedelta(seconds=1800), base=NOW, filled=True, case=case
        )
    )
    sources = tuple((chain, observed.source_reference(chain)) for chain in chains)
    assert len({reference.capture_id for _, reference in sources}) == len(sources)
    return sources, original_batches(sources, WINDOW)


def original_witnesses(sources, batch):
    anchor = json.loads(json.loads(batch.document_json)["anchor_json"])
    return tuple(
        global_history.stream.OriginalHistoryWitnessV1(
            item["sequence"], sources[item["sequence"] - 1][0]
        )
        for item in anchor["verified_witnesses"]
    )


def consume(sources, batches):
    result = global_history.OriginalHistoryFinancialAccumulatorV1(scope=SCOPE)
    for (chain, reference), batch in zip(sources, batches, strict=True):
        result.consume_current(
            chain=chain,
            reference=reference,
            batch=batch,
            witnesses=original_witnesses(sources, batch),
        )
    return result


def finish(result, sources, batches, *, position=True):
    return json.loads(
        result.finish(
            expected_through=len(sources),
            expected_head=batches[-1].event_sha256,
            checked_at=CHECKED,
            position_source=native_position_source(sources[-1][1])
            if position
            else None,
        ).receipt_json
    )


@pytest.mark.asyncio
async def test_actual_original_open32_close33_global_fifo_and_signed_fees(monkeypatch):
    sources, batches = await acquired(monkeypatch, opens=31)
    assert len(sources) == 33
    data = finish(consume(sources, batches), sources, batches)
    run = data["closed_runs"][0]
    assert data["aggregate_generation_blocking_reasons"] == []
    assert (
        "generation_observation_tail_below_policy"
        in data["original_source_blocking_reasons"]
    )
    assert run["fill_gross_pnl"] == {"numerator": "2", "denominator": "5"}
    assert run["signed_fill_fees"] == {"numerator": "-1", "denominator": "50"}
    assert run["reconciled_closed_run_net_pnl"] == {
        "numerator": "7",
        "denominator": "20",
    }
    assert len(data["original_current_sources"]) == 33
    assert (
        data["account_complete"]
        is data["execution_authority"]
        is data["original_stream_prefix_verified"]
        is False
    )


@pytest.mark.asyncio
async def test_previous_witness_does_not_book_its_fills_again_and_all_locators_survive(
    monkeypatch,
):
    sources, batches = await acquired(monkeypatch)
    data = finish(consume(sources, batches), sources, batches)
    assert len(data["observed_lifecycles"]) == 1
    assert len(data["observed_lifecycles"][0]["original_source_rows"]) == 2
    fills = [row for row in data["retained_source_index"] if row["family"] == "fills"]
    assert len(fills) == 2
    assert max(len(row["locators"]) for row in fills) >= 2
    assert all(
        set(locator)
        == {
            "capture_id",
            "request_index",
            "row_ordinal",
            "raw_event_sequence",
            "page_receipt_sha256",
            "raw_sha256",
            "stream",
        }
        for row in fills
        for locator in row["locators"]
    )


@pytest.mark.asyncio
async def test_original_same_identity_conflicting_bytes_poison_whole_accumulator(
    monkeypatch,
):
    sources, _ = await acquired(monkeypatch)
    changed = await original.original_capture(
        monkeypatch,
        NOW + timedelta(seconds=1900),
        base=NOW,
        filled=True,
        case="A21",
        variant=True,
    )
    sources = (*sources, (changed, observed.source_reference(changed)))
    batches = original_batches(sources, WINDOW)
    accumulator = consume(sources[:-1], batches[:-1])
    with pytest.raises(
        global_history.GlobalFinancialHistoryError,
        match="^global_financial_conflicting_row_identity$",
    ):
        accumulator.consume_current(
            chain=sources[-1][0],
            reference=sources[-1][1],
            batch=batches[-1],
            witnesses=original_witnesses(sources, batches[-1]),
        )
    with pytest.raises(
        global_history.GlobalFinancialHistoryError,
        match="^global_financial_closed_or_failed$",
    ):
        accumulator.finish(
            expected_through=4,
            expected_head=batches[-1].event_sha256,
            checked_at=CHECKED,
        )


def test_position_total_reuse_guard_rejects_two_run_claims_without_native_credit():
    # A direct negative guard vector, never a synthetic native-positive fixture.
    run = {
        "position_history_row": {"row_sha256": "a" * 64},
        "net_outcome_arithmetic_reconciled": True,
    }
    with pytest.raises(
        global_history.GlobalFinancialHistoryError,
        match="^global_financial_position_total_reused$",
    ):
        global_history._position_totals_once([run, dict(run)])


@pytest.mark.asyncio
async def test_fee_currency_unknown_cannot_become_closed_net(monkeypatch):
    sources, batches = await acquired(monkeypatch, case="A20")
    data = finish(consume(sources, batches), sources, batches)
    assert all(
        run["reconciled_closed_run_net_pnl"] is None for run in data["closed_runs"]
    )
    assert (
        data["observed_lifecycles"][0]["source_observed_signed_fill_fee_subtotal"]
        is None
    )


@pytest.mark.asyncio
async def test_funding_accrual_windows_seed_and_hwm_remain_unknown_even_with_closed_net(
    monkeypatch,
):
    sources, batches = await acquired(monkeypatch)
    data = finish(consume(sources, batches), sources, batches)
    for key in (
        "funding_accrual_events",
        "daily_net_pnl",
        "weekly_net_pnl",
        "loss_streak_seed",
        "loss_streak",
        "historical_native_hwm",
        "current_history_tail_finality",
        "portfolio_revision",
    ):
        assert data[key] is None
    assert data["native_owner"] is None
    assert data["admission"] == "DENY"


@pytest.mark.asyncio
async def test_whole_current_membership_cannot_be_trimmed_by_a_new_document(
    monkeypatch,
):
    sources, batches = await acquired(monkeypatch)
    from dataclasses import replace

    document = json.loads(batches[1].document_json)
    document["source_membership_count"] = 0
    document["source_membership_sha256"] = journal.digest(journal.canonical([]))
    anchor = json.loads(document["anchor_json"])
    anchor["current_source_membership_sha256"] = document["source_membership_sha256"]
    anchor["source_facts_through_sequence"] = json.loads(batches[0].document_json)[
        "source_membership_count"
    ]
    document["anchor_json"] = journal.canonical(anchor).decode()
    document["anchor_sha256"] = journal.digest(journal.canonical(anchor))
    receipt = json.loads(batches[1].replay.receipt_json)
    receipt["continuation_anchor"] = anchor
    raw_receipt = journal.canonical(receipt)
    document["receipt_sha256"] = journal.digest(raw_receipt)
    raw = journal.canonical(document)
    damaged = replace(
        batches[1],
        event_sha256=journal.digest(raw),
        document_json=raw,
        replay=observed.ObservationReplay(raw_receipt),
    )
    accumulator = consume(sources[:1], batches[:1])
    with pytest.raises(
        global_history.GlobalFinancialHistoryError,
        match="^global_financial_whole_current_membership_changed$",
    ):
        accumulator.consume_current(
            chain=sources[1][0],
            reference=sources[1][1],
            batch=damaged,
            witnesses=original_witnesses(sources, batches[1]),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["anchor", "findings"])
async def test_rehashed_receipt_cannot_unbind_original_anchor_or_finding_membership(
    monkeypatch, damage
):
    from dataclasses import replace

    sources, batches = await acquired(monkeypatch)
    receipt = json.loads(batches[1].replay.receipt_json)
    if damage == "anchor":
        receipt["continuation_anchor"]["previous_sequence"] = 0
    else:
        receipt["findings"].append({"kind": "declared_fixture_unbound_finding"})
    raw_receipt = journal.canonical(receipt)
    document = json.loads(batches[1].document_json)
    document["receipt_sha256"] = journal.digest(raw_receipt)
    raw = journal.canonical(document)
    damaged = replace(
        batches[1],
        event_sha256=journal.digest(raw),
        document_json=raw,
        replay=observed.ObservationReplay(raw_receipt),
    )
    accumulator = consume(sources[:1], batches[:1])
    with pytest.raises(
        global_history.GlobalFinancialHistoryError,
        match="^global_financial_original_receipt_changed$",
    ):
        accumulator.consume_current(
            chain=sources[1][0],
            reference=sources[1][1],
            batch=damaged,
            witnesses=original_witnesses(sources, batches[1]),
        )


@pytest.mark.asyncio
async def test_compact_overflow_and_explicit_cancel_leave_no_partial_receipt(
    monkeypatch,
):
    sources, batches = await acquired(monkeypatch)
    accumulator = global_history.OriginalHistoryFinancialAccumulatorV1(scope=SCOPE)
    with monkeypatch.context() as overflow:
        overflow.setattr(global_history, "MAX_COMPACT_BYTES", 1)
        with pytest.raises(
            global_history.GlobalFinancialHistoryError,
            match="^global_financial_compact_byte_bound$",
        ):
            accumulator.consume_current(
                chain=sources[0][0],
                reference=sources[0][1],
                batch=batches[0],
                witnesses=original_witnesses(sources, batches[0]),
            )
        with pytest.raises(
            global_history.GlobalFinancialHistoryError,
            match="^global_financial_closed_or_failed$",
        ):
            accumulator.finish(
                expected_through=1,
                expected_head=batches[0].event_sha256,
                checked_at=CHECKED,
            )
        with pytest.raises(
            global_history.GlobalFinancialHistoryError,
            match="^global_financial_compact_byte_bound$",
        ):
            global_history.OriginalHistoryFinancialAccumulatorV1(scope=SCOPE)
    cancelled = global_history.OriginalHistoryFinancialAccumulatorV1(scope=SCOPE)
    cancelled.cancel()
    with pytest.raises(
        global_history.GlobalFinancialHistoryError,
        match="^global_financial_closed_or_failed$",
    ):
        cancelled.finish(
            expected_through=1,
            expected_head=batches[0].event_sha256,
            checked_at=CHECKED,
        )


@pytest.mark.asyncio
async def test_same_millisecond_fills_fail_without_a_partial_result(monkeypatch):
    sources, batches = await acquired(monkeypatch, case="A17")
    with pytest.raises(global_history.GlobalFinancialHistoryError):
        consume(sources, batches)


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", ["pending", "suppressed"])
async def test_real_task_cancellation_denies_before_any_source_read_and_keeps_no_receipt(
    delivery,
):
    async def scenario():
        task = asyncio.current_task()
        accumulator = global_history.OriginalHistoryFinancialAccumulatorV1(scope=SCOPE)
        task.cancel()
        if delivery == "suppressed":
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
        try:
            # None/empty inputs are explicit rejection controls, never owner or
            # source proofs. Cancellation must be checked before any are read.
            with pytest.raises(
                global_history.GlobalFinancialHistoryError,
                match="^global_financial_cancelled$",
            ):
                accumulator.consume_current(chain=(), reference=None, batch=None)
        finally:
            # Drain only this test-owned child before clearing its cancel count.
            # Python3.12 uncancel alone need not rescind scheduled delivery.
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
            while task.cancelling():
                task.uncancel()
        with pytest.raises(
            global_history.GlobalFinancialHistoryError,
            match="^global_financial_closed_or_failed$",
        ):
            accumulator.finish(
                expected_through=1, expected_head="a" * 64, checked_at=CHECKED
            )

    await asyncio.create_task(scenario())


async def acquired_without_repeated_opening(monkeypatch):
    """Every packet/plan is freshly declared before actual fixture acquisition."""
    sources = []
    first = await original.original_capture(
        monkeypatch, NOW, base=NOW, filled=False, case="A01"
    )
    opening = await original.original_capture(
        monkeypatch, NOW + timedelta(seconds=600), base=NOW, filled=True, case="A03"
    )
    sources.extend(
        (chain, observed.source_reference(chain)) for chain in (first, opening)
    )
    original_pins = tuple(
        global_history.stream._chain_measure(chain)[1] for chain in (first, opening)
    )
    original_readbacks = tuple(
        tuple(point.readback_at for point in chain) for chain in (first, opening)
    )
    real_regional, real_script = original.regional, original.script

    def narrowed_plan(**values):
        # Source-generation ts of the opening is NOW+301, outside these new queries.
        values["history_start"] = NOW + timedelta(seconds=302)
        return real_regional(**values)

    def declared_packet(*, pages):
        # These are new fixture response bytes, not edits to any captured event.
        for name in ("fills_recent", "fills_history"):
            pages[name] = [
                [
                    row
                    for page in pages[name]
                    for row in page
                    if row.get("ordId") != "801"
                ],
                [],
            ]
            if not pages[name][0]:
                pages[name] = [[]]
        for name in ("orders_history_recent", "orders_history_archive"):
            pages[name] = [
                [
                    row
                    for page in pages[name]
                    for row in page
                    if row.get("ordId") != "801"
                ],
                [],
            ]
            if not pages[name][0]:
                pages[name] = [[]]
        return real_script(pages=pages)

    with monkeypatch.context() as current:
        current.setattr(original, "regional", narrowed_plan)
        current.setattr(original, "script", declared_packet)
        for number in range(31):
            chain = await original.original_capture(
                monkeypatch,
                NOW + timedelta(seconds=601 + number),
                base=NOW,
                filled=True,
                case="A03",
            )
            sources.append((chain, observed.source_reference(chain)))
        closing = await original.original_capture(
            monkeypatch,
            NOW + timedelta(seconds=1800),
            base=NOW,
            filled=True,
            case="A01",
        )
        sources.append((closing, observed.source_reference(closing)))
    assert (
        tuple(
            global_history.stream._chain_measure(chain)[1] for chain in (first, opening)
        )
        == original_pins
    )
    assert (
        tuple(tuple(point.readback_at for point in chain) for chain in (first, opening))
        == original_readbacks
    )
    sources = tuple(sources)
    assert len(sources) == 34 and len({ref.capture_id for _, ref in sources}) == 34
    return sources, original_batches(sources, WINDOW)


@pytest.mark.asyncio
async def test_global_closed_run_keeps_opening_absent_from_every_last32_current(
    monkeypatch,
):
    sources, batches = await acquired_without_repeated_opening(monkeypatch)
    early = sources[1][1].capture_id
    tail_ids = {reference.capture_id for _, reference in sources[-32:]}
    assert early not in tail_ids
    from app.trade_qualification import account_lifecycle_history as lifecycle

    for chain, reference in sources[-32:]:
        packet = lifecycle._packet(chain, reference)
        assert all(
            json.loads(row.canonical_json).get("ordId") != "801"
            for page in packet.observations
            if page.request.stream in {"fills_recent", "fills_history"}
            for row in page.rows
        )
    accumulator = consume(sources, batches)
    assert accumulator._state_bytes == global_history._canonical_size(
        accumulator._state
    )
    data = finish(accumulator, sources, batches)
    run = data["closed_runs"][0]
    assert data["aggregate_generation_blocking_reasons"] == []
    assert (
        "generation_observation_tail_below_policy"
        in data["original_source_blocking_reasons"]
    )
    assert run["fill_gross_pnl"] == {"numerator": "2", "denominator": "5"}
    assert run["signed_fill_fees"] == {"numerator": "-1", "denominator": "50"}
    assert run["reconciled_closed_run_net_pnl"] == {
        "numerator": "7",
        "denominator": "20",
    }
    assert any(
        locator["capture_id"] == early
        for row in run["original_fill_rows"]
        for locator in row["locators"]
    )
    assert len(data["original_current_sources"]) == 34
    tail = sources[-32:]
    tail_data = finish(
        consume(tail, original_batches(tail, WINDOW)),
        tail,
        original_batches(tail, WINDOW),
        position=False,
    )
    assert all(
        row["reconciled_closed_run_net_pnl"] is None for row in tail_data["closed_runs"]
    )
    assert "generation_window_gap" in tail_data["aggregate_generation_blocking_reasons"]
    assert all(
        locator["capture_id"] != early
        for row in tail_data["retained_source_index"]
        for locator in row["locators"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["through", "head", "cutoff", "policy"])
async def test_incomplete_or_rebound_finish_poison_without_receipt(monkeypatch, damage):
    sources, batches = await acquired(monkeypatch)
    accumulator = consume(sources, batches)
    arguments = {
        "expected_through": len(sources),
        "expected_head": batches[-1].event_sha256,
        "checked_at": CHECKED,
    }
    if damage == "through":
        arguments["expected_through"] += 1
    elif damage == "head":
        arguments["expected_head"] = "f" * 64
    elif damage == "cutoff":
        arguments["checked_at"] = NOW
    else:
        arguments["expected_policy_sha256"] = "f" * 64
    with pytest.raises(global_history.GlobalFinancialHistoryError):
        accumulator.finish(**arguments)
    assert (
        accumulator._state is None
        and not accumulator._known
        and not accumulator._captures
    )
    with pytest.raises(
        global_history.GlobalFinancialHistoryError,
        match="^global_financial_closed_or_failed$",
    ):
        accumulator.finish(
            expected_through=len(sources),
            expected_head=batches[-1].event_sha256,
            checked_at=CHECKED,
        )


@pytest.mark.asyncio
async def test_scope_policy_cutoff_and_all_original_sources_are_bound(monkeypatch):
    sources, batches = await acquired(monkeypatch)
    data = finish(consume(sources, batches), sources, batches)
    assert data["scope_sha256"] == journal.digest(
        journal.canonical(["demo", UID, "USDT"])
    )
    assert data["policy_sha256"] == global_history.POLICY_SHA256
    assert data["component_policy_sha256"] == global_history._component_policies()
    assert data["checked_at"] == CHECKED.isoformat()
    assert (
        data["checked_at_semantics"]
        == "DECLARED_VALIDATION_CUTOFF_NOT_NATIVE_OWNERSHIP"
    )
    assert data["source_available_at"] <= data["checked_at"]
    assert [
        row["reference"]["capture_id"] for row in data["original_current_sources"]
    ] == [reference.capture_id for _, reference in sources]
    assert (
        data["original_stream_prefix_verified"]
        is data["account_complete"]
        is data["execution_authority"]
        is False
    )


@pytest.mark.asyncio
async def test_external_scope_mutation_does_not_rebind_owned_snapshot(monkeypatch):
    from app.trade_qualification.reservations import LedgerScope

    sources, batches = await acquired(monkeypatch)
    supplied = LedgerScope(account_id=UID, settlement_currency="USDT")
    accumulator = global_history.OriginalHistoryFinancialAccumulatorV1(scope=supplied)
    object.__setattr__(supplied, "account_id", "999")
    for (chain, reference), batch in zip(sources, batches, strict=True):
        accumulator.consume_current(
            chain=chain,
            reference=reference,
            batch=batch,
            witnesses=original_witnesses(sources, batch),
        )
    data = finish(accumulator, sources, batches, position=False)
    assert data["scope_sha256"] == journal.digest(
        journal.canonical(["demo", UID, "USDT"])
    )


@pytest.mark.asyncio
async def test_nested_locator_budget_drift_is_rejected_by_final_exact_accounting(
    monkeypatch,
):
    sources, batches = await acquired(monkeypatch)
    accumulator = consume(sources, batches)
    # Deliberate private state corruption is a negative accounting vector only.
    accumulator._state["members"][0]["locators"].append(
        dict(accumulator._state["members"][0]["locators"][0])
    )
    with pytest.raises(
        global_history.GlobalFinancialHistoryError,
        match="^global_financial_compact_accounting_changed$",
    ):
        accumulator.finish(
            expected_through=len(sources),
            expected_head=batches[-1].event_sha256,
            checked_at=CHECKED,
        )
    assert accumulator._state is None


@pytest.mark.asyncio
async def test_real_cancel_after_valid_prefix_before_finish_erases_all_state(
    monkeypatch,
):
    sources, batches = await acquired(monkeypatch)

    async def scenario():
        accumulator = consume(sources, batches)
        task = asyncio.current_task()
        task.cancel()
        try:
            with pytest.raises(
                global_history.GlobalFinancialHistoryError,
                match="^global_financial_cancelled$",
            ):
                accumulator.finish(
                    expected_through=len(sources),
                    expected_head=batches[-1].event_sha256,
                    checked_at=CHECKED,
                )
        finally:
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
            while task.cancelling():
                task.uncancel()
        assert (
            accumulator._state is None
            and not accumulator._known
            and not accumulator._captures
        )

    await asyncio.create_task(scenario())


@pytest.mark.asyncio
async def test_different_task_cannot_resume_partial_accumulator(monkeypatch):
    sources, batches = await acquired(monkeypatch)
    accumulator = consume(sources[:1], batches[:1])

    async def different_task():
        with pytest.raises(
            global_history.GlobalFinancialHistoryError,
            match="^global_financial_task_changed$",
        ):
            accumulator.consume_current(
                chain=sources[1][0],
                reference=sources[1][1],
                batch=batches[1],
                witnesses=original_witnesses(sources, batches[1]),
            )

    await asyncio.create_task(different_task())
    assert accumulator._state is None


@pytest.mark.asyncio
async def test_source_cancellation_preserves_type_without_private_args_and_poison(
    monkeypatch,
):
    sources, batches = await acquired(monkeypatch)
    accumulator = global_history.OriginalHistoryFinancialAccumulatorV1(scope=SCOPE)

    def interrupted(*_args, **_kwargs):
        raise asyncio.CancelledError("DECLARED_SYNTHETIC_ARGUMENT_WITHHELD")

    with monkeypatch.context() as current:
        current.setattr(observed, "replay_observation_index", interrupted)
        with pytest.raises(asyncio.CancelledError) as rejected:
            accumulator.consume_current(
                chain=sources[0][0],
                reference=sources[0][1],
                batch=batches[0],
                witnesses=original_witnesses(sources, batches[0]),
            )
    assert rejected.value.args == () and accumulator._state is None


@pytest.mark.asyncio
async def test_incremental_budget_matches_exact_state_after_each_nested_append(
    monkeypatch,
):
    sources, batches = await acquired(monkeypatch, opens=4)
    accumulator = global_history.OriginalHistoryFinancialAccumulatorV1(scope=SCOPE)
    for (chain, reference), batch in zip(sources, batches, strict=True):
        accumulator.consume_current(
            chain=chain,
            reference=reference,
            batch=batch,
            witnesses=original_witnesses(sources, batch),
        )
        exact = len(journal.canonical(global_history._wire(accumulator._state)))
        assert accumulator._state_bytes == exact <= global_history.MAX_COMPACT_BYTES
    retained = accumulator._state_bytes
    result = accumulator.finish(
        expected_through=len(sources),
        expected_head=batches[-1].event_sha256,
        checked_at=CHECKED,
        position_source=native_position_source(sources[-1][1]),
    )
    assert retained + len(result.receipt_json) <= global_history.MAX_COMPACT_BYTES
    assert (
        accumulator._state is None
        and not accumulator._known
        and not accumulator._captures
    )


def rehashed_batch(batch, *, receipt=None, document=None):
    from dataclasses import replace

    receipt = json.loads(batch.replay.receipt_json) if receipt is None else receipt
    document = json.loads(batch.document_json) if document is None else document
    raw_receipt = journal.canonical(receipt)
    document["receipt_sha256"] = journal.digest(raw_receipt)
    raw = journal.canonical(document)
    return replace(
        batch,
        event_sha256=journal.digest(raw),
        document_json=raw,
        replay=observed.ObservationReplay(raw_receipt),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("gap", ["coverage", "tail", "stale"])
async def test_unresolved_real_aggregate_obligation_keeps_closed_net_unknown(
    monkeypatch, gap
):
    if gap == "coverage":
        sources, _ = await acquired_without_repeated_opening(monkeypatch)
        sources = sources[-32:]
        window = WINDOW
        expected = "generation_window_gap"
    elif gap == "tail":
        sources, _ = await acquired(monkeypatch)
        window = observed.ExecutionWindow(
            WINDOW.started_at, NOW + timedelta(seconds=1700)
        )
        expected = "generation_observation_tail_below_policy"
    else:
        real_regional = original.regional

        def stale_cutoff(**values):
            values["history_end"] = values["created_at"] - timedelta(seconds=121)
            return real_regional(**values)

        with monkeypatch.context() as selected:
            selected.setattr(original, "regional", stale_cutoff)
            sources, _ = await acquired(monkeypatch)
        window = WINDOW
        expected = "generation_query_cutoff_stale"
    batches = original_batches(sources, window)
    data = finish(consume(sources, batches), sources, batches)
    assert expected in data["aggregate_generation_blocking_reasons"]
    assert expected in data["missing_obligations"]
    assert all(
        run["reconciled_closed_run_net_pnl"] is None for run in data["closed_runs"]
    )
    assert data["account_complete"] is data["execution_authority"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["blocking_reasons", "source_index", "cashflows"])
async def test_rehashed_joint_receipt_must_match_actual_witness_query(
    monkeypatch, field
):
    sources, batches = await acquired(monkeypatch)
    receipt = json.loads(batches[1].replay.receipt_json)
    if field == "blocking_reasons":
        assert "generation_observation_tail_below_policy" in receipt[field]
        receipt[field].remove("generation_observation_tail_below_policy")
    else:
        assert receipt[field]
        receipt[field] = []
    damaged = rehashed_batch(batches[1], receipt=receipt)
    accumulator = consume(sources[:1], batches[:1])
    with pytest.raises(
        global_history.GlobalFinancialHistoryError,
        match="^global_financial_original_joint_receipt_changed$",
    ):
        accumulator.consume_current(
            chain=sources[1][0],
            reference=sources[1][1],
            batch=damaged,
            witnesses=original_witnesses(sources, batches[1]),
        )
    assert accumulator._state is None and not accumulator._source_index


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["scope", "window"])
async def test_rehashed_different_scope_or_window_cannot_rebind_prefix(
    monkeypatch, field
):
    sources, batches = await acquired(monkeypatch)
    receipt = json.loads(batches[1].replay.receipt_json)
    document = json.loads(batches[1].document_json)
    if field == "scope":
        receipt["scope_sha256"] = document["scope_sha256"] = "e" * 64
    else:
        receipt["window"]["ended_at"] = (
            WINDOW.ended_at - timedelta(seconds=1)
        ).isoformat()
        document["window"] = receipt["window"]
    damaged = rehashed_batch(batches[1], receipt=receipt, document=document)
    accumulator = consume(sources[:1], batches[:1])
    with pytest.raises(global_history.GlobalFinancialHistoryError):
        accumulator.consume_current(
            chain=sources[1][0],
            reference=sources[1][1],
            batch=damaged,
            witnesses=original_witnesses(sources, batches[1]),
        )
    assert accumulator._state is None and not accumulator._source_index


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "changed"])
async def test_exact_original_witness_bytes_are_required_without_saved_receipt_authority(
    monkeypatch, damage
):
    from dataclasses import replace

    sources, batches = await acquired(monkeypatch)
    witnesses = original_witnesses(sources, batches[1])
    assert len(witnesses) == 1
    if damage == "missing":
        witnesses = ()
    else:
        chain = witnesses[0].chain
        point = replace(
            chain[0], db_recorded_at=chain[0].db_recorded_at + timedelta(microseconds=1)
        )
        witnesses = (replace(witnesses[0], chain=(point, *chain[1:])),)
    accumulator = consume(sources[:1], batches[:1])
    with pytest.raises(global_history.GlobalFinancialHistoryError):
        accumulator.consume_current(
            chain=sources[1][0],
            reference=sources[1][1],
            batch=batches[1],
            witnesses=witnesses,
        )
    assert accumulator._state is None and not accumulator._source_index


@pytest.mark.asyncio
async def test_missing_original_raw_page_cannot_enter_aggregate_coverage(monkeypatch):
    sources, batches = await acquired(monkeypatch)
    chain, reference = sources[0]
    removed = next(
        index
        for index, point in enumerate(chain)
        if journal.checked_event(point.event)["kind"] == "raw_finalized"
    )
    incomplete = chain[:removed] + chain[removed + 1 :]
    accumulator = global_history.OriginalHistoryFinancialAccumulatorV1(scope=SCOPE)
    with pytest.raises(global_history.GlobalFinancialHistoryError):
        accumulator.consume_current(
            chain=incomplete, reference=reference, batch=batches[0]
        )
    assert accumulator._state is None and not accumulator._source_index


@pytest.mark.asyncio
async def test_actual_rolling33_original_windows_select_final_pinned_b3_window(
    monkeypatch,
):
    from app.trade_qualification import account_lifecycle_history as lifecycle

    sources, _ = await acquired(monkeypatch, opens=31)
    # Every window is derived from that already captured original plan. These
    # are declared synthetic windows, not a claim of native issuer ownership.
    windows = tuple(
        observed.ExecutionWindow(
            packet.plan.history_start, packet.plan.history_end - timedelta(seconds=300)
        )
        for chain, reference in sources
        for packet in (lifecycle._packet(chain, reference),)
    )
    assert (
        len(windows) == 33
        and len({journal.canonical(observed.window_document(item)) for item in windows})
        > 1
    )
    batches = original_batches(sources, windows)
    data = finish(consume(sources, batches), sources, batches)
    assert data["aggregate_generation_window"] == observed.window_document(windows[-1])
    assert [item["window"] for item in data["original_current_sources"]] == [
        observed.window_document(item) for item in windows
    ]
    assert data["aggregate_generation_blocking_reasons"] == []
    assert data["closed_runs"][0]["reconciled_closed_run_net_pnl"] == {
        "numerator": "7",
        "denominator": "20",
    }
    assert data["account_complete"] is data["execution_authority"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["start_regressed", "span_over_seven_days", "submillisecond"]
)
async def test_original_window_order_and_shape_cannot_be_rehashed_away(
    monkeypatch, damage
):
    sources, batches = await acquired(monkeypatch)
    receipt = json.loads(batches[1].replay.receipt_json)
    document = json.loads(batches[1].document_json)
    if damage == "start_regressed":
        receipt["window"]["started_at"] = (
            WINDOW.started_at - timedelta(seconds=1)
        ).isoformat()
    elif damage == "span_over_seven_days":
        receipt["window"]["started_at"] = (
            WINDOW.ended_at - timedelta(days=7, seconds=1)
        ).isoformat()
    else:
        receipt["window"]["ended_at"] = (
            WINDOW.ended_at + timedelta(microseconds=1)
        ).isoformat()
    document["window"] = receipt["window"]
    damaged = rehashed_batch(batches[1], receipt=receipt, document=document)
    accumulator = consume(sources[:1], batches[:1])
    with pytest.raises(global_history.GlobalFinancialHistoryError):
        accumulator.consume_current(
            chain=sources[1][0],
            reference=sources[1][1],
            batch=damaged,
            witnesses=original_witnesses(sources, batches[1]),
        )
    assert accumulator._state is None and not accumulator._source_index
