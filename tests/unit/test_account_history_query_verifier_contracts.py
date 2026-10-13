"""B2a source replay over owned synthetic B1 captures; no native/PG acceptance."""

import copy
import json
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_query_verifier as verifier
from app.trade_qualification.account_runtime import AccountRuntimeError
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_qualification_account_capture import NOW, ms, row
from tests.unit.test_qualification_account_collector import SECRETS, credentials, script
from tests.unit.test_qualification_account_materializer import source_pages
from tests.unit.test_qualification_account_runtime import regional


def pins(events):
    initial = journal.checked_event(events[0].event)["data"]
    packet = next(
        item.event.packet_payload for item in events if item.event.packet_payload
    )
    return {
        "expected_head_sha256": journal.digest(events[-1].event.event_json),
        "expected_plan_sha256": initial["plan_sha256"],
        "expected_packet_sha256": journal.digest(packet),
        "expected_account_id": initial["account_id"],
        "expected_settlement_currency": initial["settlement_currency"],
    }


def verify(events, **changes):
    return verifier.verify_history_query_chain(
        tuple(events), **(pins(events) | changes)
    )


def rewrite(events, change):
    """Adversarially recompute outer hashes; source join must still catch drift."""
    result, previous = [], None
    for item in events:
        record = copy.deepcopy(journal.checked_event(item.event))
        raw, packet = item.event.raw_body, item.event.packet_payload
        replacement = change(record, raw, packet)
        if replacement is not None:
            raw, packet = replacement
        record["previous_sha256"] = previous
        record["raw_sha256"] = None if raw is None else journal.digest(raw)
        record["packet_sha256"] = None if packet is None else journal.digest(packet)
        event = journal._JournalEvent(
            journal._ISSUER, journal.canonical(record), raw, packet
        )
        result.append(
            journal.JournalReadback(event, item.db_recorded_at, item.readback_at)
        )
        previous = journal.digest(event.event_json)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("v4", [False, True])
async def test_owned_capture_finalization_separate_read_and_exact_query_replay(
    monkeypatch, v4
):
    session, harness, checkpoints, args, events = setup(monkeypatch, v4=v4)
    reads = []
    original = AccountCaptureJournalRepository.read_chain

    async def read(self, scope, capture_id):
        assert journal.checked_event(events[-1].event)["outcome"] == "complete_recorded"
        reads.append(capture_id)
        return await original(self, scope, capture_id)

    monkeypatch.setattr(AccountCaptureJournalRepository, "read_chain", read)
    result = await bootstrap.collect_bootstrap_query_verified(session, **args)
    data = json.loads(result.query_verification.receipt_json)
    public = json.loads(result.receipt_json)
    old = verify(events)
    assert len(reads) == 1
    assert result.query_verification == verifier.verify_history_query_chain_v2(
        tuple(events), **pins(events)
    )
    assert data["schema_version"] == "ctcc.observed_account_history_query.v2"
    assert data["policy_sha256"] == verifier.V2_POLICY_SHA256
    assert data["legacy_v1_receipt_sha256"] == old.receipt_sha256
    assert data["state"] == "retained_typed_source_ts_query_verified"
    assert all(item["requested_source_ts_window_covered"] for item in data["coverage"])
    assert public["schema_version"] == "ctcc.demo_bootstrap_query_observation.v2"
    assert public["policy_sha256"] == verifier.V2_POLICY_SHA256
    assert (
        public["query_verification_sha256"] == result.query_verification.receipt_sha256
    )
    assert len(data["coverage"]) == (7 if v4 else 2)
    recent = next(item for item in data["queries"] if item["stream"] == "fills_recent")
    archive = next(
        item for item in data["queries"] if item["stream"].startswith("fills_history")
    )
    assert recent["covered_intervals"][0][0] > data["requested_start"]
    assert archive["covered_intervals"][0][0] == data["requested_start"]
    assert recent["supported_days"] == 3 and archive["supported_days"] == 28
    assert recent["time_domain"] == archive["time_domain"] == "fill_record_generated_at"
    assert all(
        item["time_domain"]
        == (
            "balance_update_completed_at"
            if item["family"] == "bills"
            else "fill_record_generated_at"
        )
        for item in data["coverage"]
    )
    assert data["unverified"] and "execution_time_loss_window" in data["unverified"]
    assert not result.account_complete and not result.execution_authority
    assert result.admission == "DENY"
    assert result.recorded.bootstrap.transport_provenance == "synthetic_transport"
    assert checkpoints[0].state == checkpoints[1].state
    assert session._plan.expected_uid.encode() not in result.receipt_json
    assert all(secret.encode() not in result.receipt_json for secret in SECRETS)
    if v4:
        assert any(len(row["locators"]) == 2 for row in data["rows"])
        assert {item["source_ts_semantics"] for item in data["rows"]} == {
            "fill_record_generated_at",
            "balance_update_completed_at",
        }
        assert all(
            "generation_at" not in item and "source_ts_at" in item
            for item in data["rows"]
        )
        fill = next(item for item in data["rows"] if item["family"] == "fills")
        bill = next(item for item in data["rows"] if item["family"] == "bills")
        assert (
            fill["source_ts_at"]
            == bill["source_ts_at"]
            == (NOW - timedelta(seconds=1)).isoformat()
        )
        assert fill["fill_at"] == (NOW - timedelta(seconds=3)).isoformat()
        assert bill["fill_at"] is None
    else:
        # Actual terminal-only fixture is an observed empty query, never a seed.
        assert data["rows"] == []
    assert all(request.method == "GET" for request in harness.requests)
    harness.assert_closed()


@pytest.mark.asyncio
async def test_v1_receipt_cannot_be_relabelled_as_v2(monkeypatch):
    session, _, _, args, events = setup(monkeypatch, v4=True)
    await bootstrap.collect_bootstrap_recorded(session, **args)
    original = verify(events)
    with pytest.raises(verifier.HistoryQueryVerificationError, match="policy_mismatch"):
        verifier.verify_history_query_chain_v2(
            tuple(events),
            **pins(events),
            expected_policy_sha256=verifier.POLICY_SHA256,
        )
    with pytest.raises(verifier.HistoryQueryVerificationError, match="policy_mismatch"):
        verifier.verify_history_query_chain(
            tuple(events),
            **pins(events),
            expected_policy_sha256=verifier.V2_POLICY_SHA256,
        )
    revised = verifier.verify_history_query_chain_v2(tuple(events), **pins(events))
    assert verify(events) == original
    assert revised.receipt_sha256 != original.receipt_sha256
    assert (
        json.loads(revised.receipt_json)["legacy_v1_receipt_sha256"]
        == original.receipt_sha256
    )
    assert original.account_complete is original.execution_authority is False
    assert revised.account_complete is revised.execution_authority is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_head_sha256", "0" * 64),
        ("expected_plan_sha256", "0" * 64),
        ("expected_packet_sha256", "0" * 64),
        ("expected_policy_sha256", "0" * 64),
        ("expected_account_id", "999999"),
        ("expected_settlement_currency", "USD"),
    ],
)
async def test_pin_and_exact_scope_mismatch_deny(monkeypatch, field, value):
    session, _, _, args, events = setup(monkeypatch)
    await bootstrap.collect_bootstrap_recorded(session, **args)
    with pytest.raises(verifier.HistoryQueryVerificationError):
        verify(events, **{field: value})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("raw_finalized", "after", "123"),
        ("raw_finalized", "query", [["limit", "1"]]),
        ("raw_finalized", "query_retention", "withheld_secret"),
        ("raw_finalized", "buffer_complete", False),
        ("raw_finalized", "terminal_secret_set_closed", False),
        ("raw_finalized", "retained_bytes", 1),
        ("raw_finalized", "prefix_sha256", "0" * 64),
        ("request_start", "endpoint", "/api/v5/trade/order"),
        ("request_start", "request_index", 1),
        ("page_validated", "receipt_sha256", "0" * 64),
        ("page_validated", "rows", []),
        ("acquisition_closed", "source_clock_order_valid", False),
        ("packet_recorded", "local_checkpoint_sha256", "0" * 64),
        ("terminal", "observed_pages", 1),
    ],
)
async def test_rehashed_journal_metadata_cannot_override_source(
    monkeypatch, kind, field, value
):
    session, _, _, args, events = setup(monkeypatch)
    await bootstrap.collect_bootstrap_recorded(session, **args)
    changed = False

    def mutate(record, raw, packet):
        nonlocal changed
        if record["kind"] == kind and not changed:
            record["data"][field] = value
            changed = True

    altered = rewrite(events, mutate)
    assert changed
    with pytest.raises(verifier.HistoryQueryVerificationError):
        verify(altered)


@pytest.mark.asyncio
async def test_raw_bytes_mismatch_and_missing_terminal_are_not_empty_complete(
    monkeypatch,
):
    session, _, _, args, events = setup(monkeypatch)
    await bootstrap.collect_bootstrap_recorded(session, **args)

    def mutate(record, raw, packet):
        if raw is not None:
            return raw + b" ", packet

    with pytest.raises(verifier.HistoryQueryVerificationError):
        verify(rewrite(events, mutate))
    with pytest.raises(verifier.HistoryQueryVerificationError):
        verify(events[:-1])
    with pytest.raises(verifier.HistoryQueryVerificationError):
        verifier.verify_history_query_chain(tuple(events[1:]), **pins(events))


@pytest.mark.asyncio
async def test_lost_separate_readback_preserves_b1_journal_and_never_recollects(
    monkeypatch,
):
    session, harness, _, args, events = setup(monkeypatch)

    async def fail(*args):
        raise RuntimeError(SECRETS[0])

    monkeypatch.setattr(AccountCaptureJournalRepository, "read_chain", fail)
    with pytest.raises(AccountRuntimeError) as caught:
        await bootstrap.collect_bootstrap_query_verified(session, **args)
    assert not any(secret in str(caught.value) for secret in SECRETS)
    assert journal.checked_event(events[-1].event)["outcome"] == "complete_recorded"
    assert any(item.event.raw_body is not None for item in events)
    request_count = len(harness.requests)
    with pytest.raises(AccountRuntimeError, match="account_session_already_used"):
        await bootstrap.collect_bootstrap_query_verified(session, **args)
    assert len(harness.requests) == request_count


@pytest.mark.asyncio
async def test_forged_readback_clock_is_revalidated(monkeypatch):
    session, _, _, args, events = setup(monkeypatch)
    await bootstrap.collect_bootstrap_recorded(session, **args)
    altered = list(events)
    first = replace(altered[0])
    object.__setattr__(
        first, "db_recorded_at", first.db_recorded_at - timedelta(days=1)
    )
    altered[0] = first
    with pytest.raises(verifier.HistoryQueryVerificationError):
        verify(altered)


@pytest.mark.asyncio
async def test_foreign_runtime_result_cannot_enter_owned_path(monkeypatch):
    session, harness, _, args, events = setup(monkeypatch)
    old = await bootstrap.collect_bootstrap_recorded(session, **args)
    count = len(harness.requests)
    with pytest.raises(AccountRuntimeError):
        await bootstrap.collect_bootstrap_query_verified(old, **args)
    assert len(harness.requests) == count
    value = verify(events)
    assert value.account_complete is value.execution_authority is False


@pytest.mark.asyncio
async def test_cross_capture_comparison_replays_both_sources(monkeypatch):
    session, _, _, args, events = setup(monkeypatch)
    await bootstrap.collect_bootstrap_recorded(session, **args)
    output = json.loads(
        verifier.compare_history_query_chains(
            previous_chain=tuple(events),
            previous_pins=pins(events),
            current_chain=tuple(events),
            current_pins=pins(events),
        )
    )
    assert all(item["kind"] == "matching_overlap" for item in output["findings"])
    assert not output["dependent_current_claims_require_reconciliation"]
    assert output["admission"] == "DENY"


def test_foreign_chain_and_coverage_dto_never_run_callbacks():
    class Foreign:
        def __iter__(self):
            pytest.fail("foreign callback")

    with pytest.raises(verifier.HistoryQueryVerificationError):
        verifier.verify_history_query_chain(
            Foreign(),
            expected_head_sha256="0" * 64,
            expected_plan_sha256="0" * 64,
            expected_packet_sha256="0" * 64,
            expected_account_id="1",
            expected_settlement_currency="USDT",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("days,accepted", [(27, True), (28, False), (29, False)])
async def test_actual_completion_retention_edge_not_plan_creation(
    monkeypatch, days, accepted
):
    session, harness, _, args, events = setup(monkeypatch)
    selected = regional(history_start=NOW - timedelta(days=days))
    session = type(session)(
        credentials=credentials(),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    if accepted:
        result = await bootstrap.collect_bootstrap_query_verified(session, **args)
        assert result.admission == "DENY"
    else:
        # Completion is later than NOW; exactly28 days at plan creation is outside.
        with pytest.raises(AccountRuntimeError):
            await bootstrap.collect_bootstrap_query_verified(session, **args)
        assert journal.checked_event(events[-1].event)["outcome"] == "complete_recorded"
        with pytest.raises(
            verifier.HistoryQueryVerificationError, match="lookback_unsupported"
        ):
            verify(events)
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "region,origin",
    [
        ("us_au", "https://us.okx.com"),
        ("eea", "https://eea.okx.com"),
        ("tr", "https://tr.okx.com"),
    ],
)
async def test_unsupported_region_rejected_before_any_acquisition(
    monkeypatch, region, origin
):
    session, harness, _, args, events = setup(monkeypatch)
    selected = regional(registration_region=region, origin=origin)
    session = type(session)(
        credentials=credentials(),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    with pytest.raises(AccountRuntimeError):
        await bootstrap.collect_bootstrap_query_verified(session, **args)
    assert not harness.requests and not events


@pytest.mark.asyncio
async def test_old_recorded_wire_bytes_unchanged_by_new_path(monkeypatch):
    monkeypatch.setattr(journal.uuid, "uuid4", lambda: UUID(hex="a" * 32))
    session, _, _, args, old_events = setup(monkeypatch)
    old = await bootstrap.collect_bootstrap_recorded(session, **args)
    session, _, _, args, new_events = setup(monkeypatch)
    new = await bootstrap.collect_bootstrap_query_verified(session, **args)
    assert old.bootstrap.receipt_json == new.recorded.bootstrap.receipt_json
    assert old.journal_receipt_json == new.recorded.journal_receipt_json
    assert [item.event for item in old_events] == [item.event for item in new_events]
    assert capture.freeze_demo_account_packet(
        old.bootstrap.packet, expected_plan_sha256=session._pin
    ) == capture.freeze_demo_account_packet(
        new.recorded.bootstrap.packet, expected_plan_sha256=session._pin
    )


async def captured_history(monkeypatch, rows, *, tick=0, bills=()):
    session, harness, _, args, events = setup(monkeypatch)
    pages = source_pages()
    pages["fills_history"] = [rows, []] if rows else [[]]
    pages["bills_archive"] = [list(bills), []] if bills else [[]]
    harness.script = script(pages=pages)
    harness.clock.calls = tick
    await bootstrap.collect_bootstrap_recorded(session, **args)
    return tuple(events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,expected",
    [
        ("late", "late_observation_in_prior_query"),
        ("conflict", "conflicting_overlap"),
        ("missing", "previous_row_missing_in_current_query"),
    ],
)
async def test_cross_capture_late_conflicting_missing_rows_preserve_both_sources(
    monkeypatch, change, expected
):
    sample = row("fills_history")
    before_rows = [] if change == "late" else [sample]
    after_rows = (
        []
        if change == "missing"
        else [{**sample, "fee": "-0.002"}]
        if change == "conflict"
        else [sample]
    )
    before = await captured_history(monkeypatch, before_rows)
    after = await captured_history(monkeypatch, after_rows, tick=5000)
    before_bytes = [item.event for item in before]
    after_bytes = [item.event for item in after]
    result = json.loads(
        verifier.compare_history_query_chains(
            previous_chain=before,
            previous_pins=pins(before),
            current_chain=after,
            current_pins=pins(after),
        )
    )
    assert expected in {item["kind"] for item in result["findings"]}
    assert result["dependent_current_claims_require_reconciliation"] is True
    assert [item.event for item in before] == before_bytes
    assert [item.event for item in after] == after_bytes
    assert not result["account_complete"] and result["admission"] == "DENY"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,expected",
    [
        ("late", "late_observation_in_prior_query"),
        ("conflict", "conflicting_overlap"),
        ("missing", "previous_row_missing_in_current_query"),
    ],
)
async def test_v2_comparison_replays_original_b1_under_typed_source_ts(
    monkeypatch, change, expected
):
    sample = row("fills_history", fillTime=ms(NOW - timedelta(days=8)))
    before = await captured_history(monkeypatch, [] if change == "late" else [sample])
    after = await captured_history(
        monkeypatch,
        []
        if change == "missing"
        else [{**sample, "fee": "-0.002"}]
        if change == "conflict"
        else [sample],
        tick=5000,
    )
    original_events = ([item.event for item in before], [item.event for item in after])
    output = json.loads(
        verifier.compare_history_query_chains_v2(
            previous_chain=before,
            previous_pins=pins(before),
            current_chain=after,
            current_pins=pins(after),
        )
    )
    assert output["schema_version"] == "ctcc.observed_history_query_comparison.v3"
    assert output["policy_sha256"] == verifier.V2_COMPARISON_POLICY_SHA256
    assert output["source_query_policy_sha256"] == verifier.V2_POLICY_SHA256
    assert output["source_time_domains"] == {
        "fills": "fill_record_generated_at",
        "bills": "balance_update_completed_at",
    }
    assert (
        output["previous_query_receipt_sha256"]
        == verifier.verify_history_query_chain_v2(before, **pins(before)).receipt_sha256
    )
    assert (
        output["current_query_receipt_sha256"]
        == verifier.verify_history_query_chain_v2(after, **pins(after)).receipt_sha256
    )
    assert expected in {item["kind"] for item in output["findings"]}
    if change == "late":
        observed = json.loads(
            verifier.verify_history_query_chain_v2(after, **pins(after)).receipt_json
        )
        fill = next(item for item in observed["rows"] if item["family"] == "fills")
        assert fill["fill_at"] < observed["requested_start"]
        assert fill["source_ts_at"] >= observed["requested_start"]
    assert output["dependent_current_claims_require_reconciliation"] is True
    assert [item.event for item in before] == original_events[0]
    assert [item.event for item in after] == original_events[1]
    assert output["account_complete"] is output["execution_authority"] is False
    assert output["admission"] == "DENY"


@pytest.mark.asyncio
async def test_v2_comparison_uses_bill_balance_update_domain(monkeypatch):
    before = await captured_history(monkeypatch, [], bills=[])
    after = await captured_history(
        monkeypatch, [], tick=5000, bills=[row("bills_archive", "902")]
    )
    query = json.loads(
        verifier.verify_history_query_chain_v2(after, **pins(after)).receipt_json
    )
    bill = next(item for item in query["rows"] if item["family"] == "bills")
    assert bill["source_ts_semantics"] == "balance_update_completed_at"
    assert bill["fill_at"] is None
    output = json.loads(
        verifier.compare_history_query_chains_v2(
            previous_chain=before,
            previous_pins=pins(before),
            current_chain=after,
            current_pins=pins(after),
        )
    )
    assert {item["kind"] for item in output["findings"]} == {
        "late_observation_in_prior_query"
    }
    assert output["dependent_current_claims_require_reconciliation"] is True
    assert output["account_complete"] is output["execution_authority"] is False


@pytest.mark.asyncio
async def test_v2_comparison_rejects_v1_pin_and_missing_b1_terminal(monkeypatch):
    events = await captured_history(monkeypatch, [])
    arguments = {
        "previous_chain": events,
        "previous_pins": pins(events),
        "current_chain": events,
        "current_pins": pins(events),
    }
    with pytest.raises(
        verifier.HistoryQueryVerificationError, match="comparison_policy_mismatch"
    ):
        verifier.compare_history_query_chains_v2(
            **arguments, expected_policy_sha256=verifier.POLICY_SHA256
        )
    with pytest.raises(
        verifier.HistoryQueryVerificationError, match="history_query_policy_mismatch"
    ):
        verifier.compare_history_query_chains_v2(
            **(
                arguments
                | {
                    "previous_pins": pins(events)
                    | {
                        "expected_policy_sha256": verifier.POLICY_SHA256,
                    }
                }
            )
        )
    with pytest.raises(verifier.HistoryQueryVerificationError):
        verifier.compare_history_query_chains_v2(
            **(arguments | {"current_chain": events[:-1]})
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current_start_days,requires_reconciliation", [(7, True), (15, False)]
)
async def test_empty_same_product_domains_require_measured_generation_overlap(
    monkeypatch, current_start_days, requires_reconciliation
):
    """Two empty terminal chains certify no discovery only in their overlap."""
    previous_plan = regional(
        history_start=NOW - timedelta(days=20),
        history_end=NOW - timedelta(days=14),
    )
    session, _, _, args, before = setup(monkeypatch)
    session = type(session)(
        credentials=credentials(),
        plan=previous_plan,
        expected_plan_sha256=capture.plan_sha256(previous_plan),
    )
    await bootstrap.collect_bootstrap_recorded(session, **args)
    previous = tuple(before)
    current_plan = regional(history_start=NOW - timedelta(days=current_start_days))
    session, harness, _, args, after = setup(monkeypatch)
    session = type(session)(
        credentials=credentials(),
        plan=current_plan,
        expected_plan_sha256=capture.plan_sha256(current_plan),
    )
    harness.clock.calls = 5000
    await bootstrap.collect_bootstrap_recorded(session, **args)
    current = tuple(after)

    old_receipt = json.loads(verify(previous).receipt_json)
    new_receipt = json.loads(verify(current).receipt_json)
    assert old_receipt["rows"] == new_receipt["rows"] == []
    assert {(item["family"], item["product"]) for item in old_receipt["coverage"]} == {
        (item["family"], item["product"]) for item in new_receipt["coverage"]
    }
    assert (old_receipt["requested_end"] < new_receipt["requested_start"]) is (
        requires_reconciliation
    )

    comparison = json.loads(
        verifier.compare_history_query_chains(
            previous_chain=previous,
            previous_pins=pins(previous),
            current_chain=current,
            current_pins=pins(current),
        )
    )
    assert comparison["schema_version"] == "ctcc.observed_history_query_comparison.v2"
    assert comparison["findings"] == comparison["uncompared_query_domains"] == []
    intervals = comparison["uncompared_generation_intervals"]
    assert bool(intervals) is requires_reconciliation
    assert comparison["dependent_current_claims_require_reconciliation"] is (
        requires_reconciliation
    )
    if requires_reconciliation:
        assert {(item["family"], item["product"]) for item in intervals} == {
            (item["family"], item["product"]) for item in old_receipt["coverage"]
        }
        assert all(
            item["previous_covered_intervals"]
            and item["current_covered_intervals"]
            and item["previous_covered_intervals"][-1][1]
            < item["current_covered_intervals"][0][0]
            for item in intervals
        )
    assert comparison["account_complete"] is False
    assert comparison["execution_authority"] is False
    assert comparison["admission"] == "DENY"

    typed = json.loads(
        verifier.compare_history_query_chains_v2(
            previous_chain=previous,
            previous_pins=pins(previous),
            current_chain=current,
            current_pins=pins(current),
        )
    )
    assert typed["schema_version"] == "ctcc.observed_history_query_comparison.v3"
    assert bool(typed["uncompared_source_ts_intervals"]) is requires_reconciliation
    assert typed["dependent_current_claims_require_reconciliation"] is (
        requires_reconciliation
    )
    assert typed["account_complete"] is typed["execution_authority"] is False
    assert typed["admission"] == "DENY"


@pytest.mark.asyncio
async def test_source_generation_window_never_claims_actual_fill_window(monkeypatch):
    events = await captured_history(
        monkeypatch, [row("fills_history", fillTime=ms(NOW - timedelta(days=8)))]
    )
    result = json.loads(verify(events).receipt_json)
    assert result["rows"][0]["fill_at"] < result["requested_start"]
    assert result["rows"][0]["generation_at"] >= result["requested_start"]
    assert "execution_time_loss_window" in result["unverified"]
    assert not result["account_complete"]


@pytest.mark.asyncio
async def test_raw_finalized_eof_cannot_disagree_with_original_observation(monkeypatch):
    session, _, _, args, events = setup(monkeypatch)
    await bootstrap.collect_bootstrap_recorded(session, **args)

    def mutate(record, raw, packet):
        if record["kind"] == "raw_finalized":
            record["data"]["body_completed_at"] = record["data"]["headers_received_at"]

    with pytest.raises(verifier.HistoryQueryVerificationError):
        verify(rewrite(events, mutate))


def test_fractional_retention_boundaries_never_bridge_an_integer_ms_gap():
    first = (NOW, NOW + timedelta(microseconds=100))
    second = (NOW + timedelta(microseconds=1100), NOW + timedelta(milliseconds=2))
    result = verifier._union([first, second])
    integer_ms = NOW + timedelta(milliseconds=1)
    assert len(result) == 2
    assert not any(a <= integer_ms <= b for a, b in result)
    assert verifier._union([(NOW, NOW + timedelta(seconds=1)), (NOW, NOW)]) == [
        (NOW, NOW + timedelta(seconds=1))
    ]


@pytest.mark.asyncio
async def test_different_product_plans_record_uncompared_domains(monkeypatch):
    session, _, _, args, before = setup(monkeypatch, v4=True)
    await bootstrap.collect_bootstrap_recorded(session, **args)
    session, harness, _, args, after = setup(monkeypatch)
    harness.clock.calls = 5000
    await bootstrap.collect_bootstrap_recorded(session, **args)
    result = json.loads(
        verifier.compare_history_query_chains(
            previous_chain=tuple(before),
            previous_pins=pins(before),
            current_chain=tuple(after),
            current_pins=pins(after),
        )
    )
    assert {item["product"] for item in result["uncompared_query_domains"]} == {
        "SPOT",
        "MARGIN",
        "FUTURES",
        "OPTION",
        "EVENTS",
    }
    assert all(
        item["observed_only_in"] == "previous"
        for item in result["uncompared_query_domains"]
    )
    assert result["dependent_current_claims_require_reconciliation"] is True
    assert not result["account_complete"]


async def two_row_history(monkeypatch):
    """Declare both ordinals in actual source pages before B1 acquisition."""
    session, harness, _, args, events = setup(monkeypatch, v4=True)
    for index, (stream, rows) in enumerate(harness.script):
        if stream == "fills_recent" and rows:
            second = {**rows[0], "billId": str(int(rows[0]["billId"]) - 1)}
            harness.script[index] = (stream, [rows[0], second])
            break
    await bootstrap.collect_bootstrap_recorded(session, **args)
    assert verify(events).admission == "DENY"
    harness.assert_closed()
    return tuple(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["page_validated", "raw_finalized"])
@pytest.mark.parametrize(
    "field,number", [("ordinal", 0), ("ordinal", 1), ("prior_row_ordinal", 0)]
)
async def test_rehashed_boolean_locators_cannot_alias_original_integer_lineage(
    monkeypatch, kind, field, number
):
    events = await two_row_history(monkeypatch)
    changed = False

    def mutate(record, raw, packet):
        nonlocal changed
        if record["kind"] != kind or changed:
            return
        for locator in record["data"].get("rows", []):
            if type(locator.get(field)) is int and locator[field] == number:
                locator[field] = bool(number)
                changed = True
                return

    altered = rewrite(events, mutate)
    assert changed
    assert [(item.event.raw_body, item.event.packet_payload) for item in altered] == [
        (item.event.raw_body, item.event.packet_payload) for item in events
    ]
    with pytest.raises(verifier.HistoryQueryVerificationError):
        verify(altered)
    assert verify(events).admission == "DENY"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kinds",
    [
        ("page_validated",),
        ("raw_finalized",),
        ("page_validated", "raw_finalized"),
    ],
)
async def test_rehashed_numeric_eof_cannot_replace_actual_boolean_observation(
    monkeypatch, kinds
):
    session, _, _, args, events = setup(monkeypatch)
    await bootstrap.collect_bootstrap_recorded(session, **args)

    def mutate(record, raw, packet):
        if record["kind"] in kinds:
            assert record["data"]["body_exhaustion_observed"] is True
            record["data"]["body_exhaustion_observed"] = 1

    with pytest.raises(verifier.HistoryQueryVerificationError):
        verify(rewrite(events, mutate))
    assert verify(events).admission == "DENY"


@pytest.mark.asyncio
async def test_rehashed_validated_and_raw_eof_must_match_observed_body_stage(
    monkeypatch,
):
    session, _, _, args, events = setup(monkeypatch)
    await bootstrap.collect_bootstrap_recorded(session, **args)

    def mutate(record, raw, packet):
        if record["kind"] in {"page_validated", "raw_finalized"}:
            record["data"]["body_completed_at"] = record["data"]["headers_received_at"]

    with pytest.raises(verifier.HistoryQueryVerificationError):
        verify(rewrite(events, mutate))


@pytest.mark.asyncio
async def test_rehashed_plan_boolean_cannot_alias_original_integer_limit(monkeypatch):
    session, harness, _, args, events = setup(monkeypatch)
    selected = regional(max_request_seconds=1)
    session = type(session)(
        credentials=credentials(),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    await bootstrap.collect_bootstrap_recorded(session, **args)
    assert verify(events).admission == "DENY"

    def mutate(record, raw, packet):
        if record["kind"] == "capture_start":
            assert record["data"]["plan"]["max_request_seconds"] == 1
            record["data"]["plan"]["max_request_seconds"] = True

    with pytest.raises(verifier.HistoryQueryVerificationError):
        verify(rewrite(events, mutate))
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["source_observed_at", "previous_source_observed_at"])
async def test_matching_late_stage_clocks_still_require_original_source_join(
    monkeypatch, field
):
    session, _, _, args, events = setup(monkeypatch)
    await bootstrap.collect_bootstrap_recorded(session, **args)

    def mutate(record, raw, packet):
        if record["kind"] in {"page_validated", "raw_finalized"}:
            record["data"][field] = record["data"]["headers_received_at"]

    with pytest.raises(verifier.HistoryQueryVerificationError):
        verify(rewrite(events, mutate))


@pytest.mark.asyncio
async def test_sparse_chunk_samples_are_bounded_without_invented_exact_timestamps(
    monkeypatch,
):
    session, harness, _, args, events = setup(monkeypatch)
    await bootstrap.collect_bootstrap_recorded(session, **args)
    first = [
        journal.checked_event(item.event)
        for item in events
        if journal.checked_event(item.event)["data"].get("request_index") == 0
    ]
    progress = [item for item in first if item["kind"] == "body_progress"]
    eof = next(item for item in first if item["kind"] == "body_complete")
    assert harness.streams[0].yield_count == 2 and len(progress) == 1
    # The second actual chunk is measured but not a separate durable progress
    # event. Requiring equality with the earlier progress timestamp would lie.
    assert progress[-1]["observed_at"] < eof["data"]["previous_source_observed_at"]
    assert eof["data"]["previous_source_observed_at"] <= eof["observed_at"]
    assert verify(events).admission == "DENY"
    harness.assert_closed()
