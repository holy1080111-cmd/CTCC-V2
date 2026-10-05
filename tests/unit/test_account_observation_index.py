"""B3 real source semantics over synthetic owned captures; never real acceptance."""

import json
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest

from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
)
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_observation_runtime as runtime
from app.trade_qualification.reservations import LedgerScope
from tests.unit.test_account_history_query_verifier_contracts import (
    captured_history,
    rewrite,
)
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_qualification_account_capture import NOW, UID, ms, row
from tests.unit.test_qualification_account_collector import credentials, script
from tests.unit.test_qualification_account_materializer import source_pages
from tests.unit.test_qualification_account_runtime import regional

SCOPE = LedgerScope(account_id=UID, settlement_currency="USDT")
WINDOW = observed.ExecutionWindow(NOW - timedelta(days=7), NOW - timedelta(seconds=600))


def replay(*chains, window=WINDOW, scope=SCOPE):
    return observed.replay_observation_index(
        tuple((chain, observed.source_reference(chain)) for chain in chains),
        scope=scope,
        window=window,
    )


async def capture_options(monkeypatch, rows, *, tick=0, metadata=None, end=None):
    session, harness, _, args, events = setup(monkeypatch)
    if end is not None:
        plan = regional(history_end=end)
        session = type(session)(
            credentials=credentials(),
            plan=plan,
            expected_plan_sha256=capture.plan_sha256(plan),
        )
    pages = source_pages()
    pages["fills_history"] = [rows, []] if rows else [[]]
    if metadata is not None:
        pages["account_instruments"] = [metadata]
    harness.script = script(pages=pages)
    harness.clock.calls = tick
    await bootstrap.collect_bootstrap_recorded(session, **args)
    harness.assert_closed()
    return tuple(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [None, ""])
async def test_unknown_execution_time_is_retained_not_zero(monkeypatch, missing):
    sample = row("fills_history", fillPnl="-3", fillTime=missing)
    if missing is None:
        del sample["fillTime"]
    chain = await captured_history(monkeypatch, [sample])
    data = json.loads(replay(chain).receipt_json)
    assert len(data["source_index"]) == 1
    assert data["source_index"][0]["fill_at"] is None
    assert data["observed_fill_cashflow_total"] is None
    assert "execution_time_unknown" in data["blocking_reasons"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata", [[], [row("account_instruments", ctType="inverse", settleCcy="BTC")]]
)
async def test_later_metadata_change_cannot_reuse_first_supported_flag(
    monkeypatch, metadata
):
    sample = row(
        "fills_history", fillPnl="-3", fillTime=ms(NOW - timedelta(seconds=900))
    )
    first = await capture_options(monkeypatch, [sample])
    second = await capture_options(monkeypatch, [sample], tick=5000, metadata=metadata)
    data = json.loads(replay(first, second).receipt_json)
    assert len(data["source_index"][0]["metadata_observations"]) == 2
    assert data["observed_fill_cashflow_total"] is None
    assert "instrument_metadata_observation_conflict" in data["blocking_reasons"]
    assert (
        json.loads(replay(first).receipt_json)["observed_fill_cashflow_total"]
        is not None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("retain", [False, True])
async def test_generation_cutoff_cannot_regress_even_inside_freshness_policy(
    monkeypatch, retain
):
    sample = row(
        "fills_history",
        fillPnl="-3",
        fillTime=ms(NOW - timedelta(seconds=900)),
        ts=ms(NOW - timedelta(seconds=40 if retain else 10)),
    )
    first = await capture_options(monkeypatch, [sample])
    second = await capture_options(
        monkeypatch,
        [sample] if retain else [],
        tick=5000,
        end=NOW - timedelta(seconds=30),
    )
    data = json.loads(replay(first, second).receipt_json)
    assert data["observed_fill_cashflow_total"] is None
    assert "generation_cutoff_regressed" in {item["kind"] for item in data["findings"]}


@pytest.mark.asyncio
async def test_generation_after_cutoff_preserves_actual_partial_loss(monkeypatch):
    chain = await captured_history(
        monkeypatch,
        [
            row(
                "fills_history",
                fillTime=ms(NOW - timedelta(seconds=900)),
                ts=ms(NOW - timedelta(seconds=1)),
                fillPnl="-3",
                fee="-0.1",
            )
        ],
    )
    result = replay(chain)
    data = json.loads(result.receipt_json)
    assert data["window_state"] == "observed_fill_cashflows"
    cash = data["cashflows"][0]
    assert cash["execution_at"] < data["window"]["ended_at"] < cash["generation_at"]
    assert cash["lifecycle_completion"] == "unknown"
    assert data["observed_fill_cashflow_total"] == {
        "numerator": "-31",
        "denominator": "10",
    }
    assert "funding_accrual" in data["unverified"]
    assert not result.account_complete and not result.execution_authority
    assert result.admission == "DENY"


@pytest.mark.asyncio
async def test_source_execution_outside_window_never_moves_to_generation_time(
    monkeypatch,
):
    chain = await captured_history(
        monkeypatch, [row("fills_history", fillTime=ms(NOW - timedelta(days=8)))]
    )
    data = json.loads(replay(chain).receipt_json)
    assert len(data["source_index"]) == 1
    assert data["cashflows"] == []
    assert data["observed_fill_cashflow_total"] == {
        "numerator": "0",
        "denominator": "1",
    }
    assert "complete_net_loss_window" in data["unverified"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["late", "conflict", "missing"])
async def test_late_conflict_missing_invalidate_and_survive_complete_replay(
    monkeypatch, kind
):
    sample = row(
        "fills_history", fillTime=ms(NOW - timedelta(seconds=900)), fillPnl="0"
    )
    before = await captured_history(monkeypatch, [] if kind == "late" else [sample])
    after = await captured_history(
        monkeypatch,
        []
        if kind == "missing"
        else [{**sample, "fee": "-0.2"}]
        if kind == "conflict"
        else [sample],
        tick=5000,
    )
    before_bytes, after_bytes = (
        tuple(item.event for item in before),
        tuple(item.event for item in after),
    )
    accepted = replay(before)
    current = replay(before, after)
    data = json.loads(current.receipt_json)
    assert data["window_state"] == "incomplete_observation"
    assert data["observed_fill_cashflow_total"] is None
    assert data["findings"][0]["invalidates_capture_ids"] == [
        observed.source_reference(before).capture_id
    ]
    assert current == replay(before, after)
    assert accepted == replay(before)
    assert before_bytes == tuple(item.event for item in before)
    assert after_bytes == tuple(item.event for item in after)
    if kind == "conflict":
        assert len(data["source_index"]) == 2


@pytest.mark.asyncio
async def test_identical_overlap_one_cashflow_with_all_source_locators(monkeypatch):
    sample = row(
        "fills_history",
        fillTime=ms(NOW - timedelta(seconds=900)),
        fillPnl="-1",
        fee="0.01",
    )
    first = await captured_history(monkeypatch, [sample])
    second = await captured_history(monkeypatch, [sample], tick=5000)
    data = json.loads(replay(first, second).receipt_json)
    assert len(data["cashflows"]) == 1 and len(data["source_index"]) == 1
    assert len(data["cashflows"][0]["locators"]) == 2
    assert data["observed_fill_cashflow_total"] == {
        "numerator": "-99",
        "denominator": "100",
    }
    assert not data["findings"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["matching", "late", "missing", "conflict"])
async def test_local_comparison_preserves_b2_bytes_and_verifies_each_original_once(
    monkeypatch, kind
):
    sample = row(
        "fills_history", fillPnl="-3", fillTime=ms(NOW - timedelta(seconds=900))
    )
    first = await captured_history(monkeypatch, [] if kind == "late" else [sample])
    second = await captured_history(
        monkeypatch,
        []
        if kind == "missing"
        else [{**sample, "fee": "-0.2"}]
        if kind == "conflict"
        else [sample],
        tick=5000,
    )
    pins = [
        observed._pins(observed.source_reference(chain), SCOPE)
        for chain in (first, second)
    ]
    verified = [
        json.loads(observed.query.verify_history_query_chain(chain, **pin).receipt_json)
        for chain, pin in zip((first, second), pins, strict=True)
    ]
    assert observed._compare_verified(
        *verified
    ) == observed.query.compare_history_query_chains(
        previous_chain=first,
        previous_pins=pins[0],
        current_chain=second,
        current_pins=pins[1],
    )
    original = observed.query.verify_history_query_chain
    calls = []

    def counting(chain, **kwargs):
        calls.append(kwargs["expected_head_sha256"])
        return original(chain, **kwargs)

    monkeypatch.setattr(observed.query, "verify_history_query_chain", counting)
    result = replay(first, second)
    assert calls == [pin["expected_head_sha256"] for pin in pins]
    assert result.execution_authority is False


@pytest.mark.asyncio
async def test_new_generation_interval_can_reveal_old_execution_and_invalidate(
    monkeypatch,
):
    before = await captured_history(monkeypatch, [])
    session, harness, _, args, events = setup(monkeypatch)
    selected = regional(history_end=NOW)
    session = type(session)(
        credentials=credentials(),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    pages = source_pages()
    pages["fills_history"] = [
        [
            row(
                "fills_history",
                fillTime=ms(NOW - timedelta(seconds=900)),
                ts=ms(NOW),
                fillPnl="-2",
            )
        ],
        [],
    ]
    harness.script = script(pages=pages)
    harness.clock.calls = 5000
    await bootstrap.collect_bootstrap_recorded(session, **args)
    data = json.loads(replay(before, tuple(events)).receipt_json)
    assert "late_execution_discovered" in {item["kind"] for item in data["findings"]}
    assert data["observed_fill_cashflow_total"] is None
    assert len(data["cashflows"]) == 1


@pytest.mark.asyncio
async def test_nonadjacent_original_variant_conflict_cannot_disappear(monkeypatch):
    sample = row(
        "fills_history", fillTime=ms(NOW - timedelta(seconds=900)), fillPnl="-2"
    )
    first = await captured_history(monkeypatch, [sample])
    second = await captured_history(monkeypatch, [], tick=5000)
    third = await captured_history(monkeypatch, [{**sample, "fee": "-0.2"}], tick=10000)
    data = json.loads(replay(first, second, third).receipt_json)
    assert "conflicting_overlap" in {item["kind"] for item in data["findings"]}
    assert len(data["source_index"]) == 2
    assert data["observed_fill_cashflow_total"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "head_sha256",
        "plan_sha256",
        "packet_sha256",
        "session_binding_sha256",
        "capture_id",
    ],
)
async def test_references_require_exact_original_capture_and_session(
    monkeypatch, field
):
    chain = await captured_history(monkeypatch, [])
    reference = replace(
        observed.source_reference(chain),
        **{field: "0" * (32 if field == "capture_id" else 64)},
    )
    with pytest.raises(observed.AccountObservationError):
        observed.replay_observation_index(
            ((chain, reference),), scope=SCOPE, window=WINDOW
        )


@pytest.mark.asyncio
async def test_wrong_scope_policy_and_duplicate_capture_deny(monkeypatch):
    chain = await captured_history(monkeypatch, [])
    with pytest.raises(observed.AccountObservationError):
        replay(
            chain, scope=LedgerScope(account_id="999999", settlement_currency="USDT")
        )
    with pytest.raises(observed.AccountObservationError):
        replay(chain, chain)
    with pytest.raises(observed.AccountObservationError):
        observed.replay_observation_index(
            ((chain, observed.source_reference(chain)),),
            scope=SCOPE,
            window=WINDOW,
            expected_policy_sha256="0" * 64,
        )


@pytest.mark.asyncio
async def test_rehashed_source_type_mutation_still_denied(monkeypatch):
    chain = await captured_history(monkeypatch, [row("fills_history")])

    def mutate(record, raw, packet):
        if record["kind"] in {"raw_finalized", "page_validated"} and record["data"].get(
            "rows"
        ):
            record["data"]["rows"][0]["ordinal"] = False

    changed = tuple(rewrite(chain, mutate))
    with pytest.raises(observed.AccountObservationError):
        replay(changed)


@pytest.mark.asyncio
async def test_maturity_tail_is_named_incomplete_not_fake_current_window(monkeypatch):
    chain = await captured_history(monkeypatch, [])
    data = json.loads(
        replay(
            chain,
            window=observed.ExecutionWindow(
                NOW - timedelta(days=7), NOW - timedelta(seconds=1)
            ),
        ).receipt_json
    )
    assert "generation_observation_tail_below_policy" in data["blocking_reasons"]
    assert data["observed_fill_cashflow_total"] is None


@pytest.mark.asyncio
async def test_unsupported_fee_currency_retained_unknown(monkeypatch):
    chain = await captured_history(
        monkeypatch,
        [row("fills_history", fillTime=ms(NOW - timedelta(seconds=900)), feeCcy="BTC")],
    )
    data = json.loads(replay(chain).receipt_json)
    assert len(data["source_index"]) == 1
    assert "cashflow_product_or_currency_unsupported" in data["blocking_reasons"]
    assert data["observed_fill_cashflow_total"] is None


@pytest.mark.asyncio
async def test_missing_cash_operand_keeps_source_and_does_not_default_zero(monkeypatch):
    chain = await captured_history(
        monkeypatch, [row("fills_history", fillTime=ms(NOW - timedelta(seconds=900)))]
    )
    data = json.loads(replay(chain).receipt_json)
    assert len(data["source_index"]) == 1
    assert "cashflow_operand_missing" in data["blocking_reasons"]
    assert data["observed_fill_cashflow_total"] is None


@pytest.mark.asyncio
async def test_new_owned_route_preserves_old_b1_bytes_and_rejects_receipt_as_owner(
    monkeypatch,
):
    monkeypatch.setattr(journal.uuid, "uuid4", lambda: UUID(hex="a" * 32))
    session, _, _, args, old_events = setup(monkeypatch)
    old = await bootstrap.collect_bootstrap_recorded(session, **args)
    session, harness, _, args, new_events = setup(monkeypatch)
    repo = AccountObservationIndexRepository(None, clock=harness.clock)

    async def append(self, scope, reference, window, *, expected_revision):
        assert expected_revision == 0
        return observed.replay_observation_index(
            ((tuple(new_events), reference),), scope=scope, window=window
        )

    monkeypatch.setattr(AccountObservationIndexRepository, "append", append)
    result = await runtime.collect_observed_execution_window(
        session, **args, observation_repository=repo, window=WINDOW, expected_revision=0
    )
    assert [item.event for item in old_events] == [item.event for item in new_events]
    assert not result.execution_authority
    count = len(harness.requests)
    with pytest.raises(observed.AccountObservationError):
        await runtime.collect_observed_execution_window(
            old, **args, observation_repository=repo, window=WINDOW, expected_revision=0
        )
    assert len(harness.requests) == count
