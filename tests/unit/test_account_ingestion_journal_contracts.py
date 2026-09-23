"""Synthetic transport and in-memory journal mechanics; no native/PG acceptance."""

import asyncio
import json
from datetime import datetime, timedelta

import httpx
import pytest

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_collector as collector
from app.trade_qualification.account_runtime import AccountRuntimeError
from tests.unit.test_qualification_account_collector import SECRETS, Stream
from tests.unit.test_qualification_bootstrap_runtime import setup as bootstrap_setup


def setup(monkeypatch, **options):
    session, harness, checkpoints, arguments = bootstrap_setup(monkeypatch, **options)
    repository = AccountCaptureJournalRepository(None, clock=harness.clock)
    events = []

    async def append(self, scope, event):
        record = journal.checked_event(event)
        assert record["sequence"] == len(events) + 1
        assert record["previous_sha256"] == (
            journal.digest(events[-1].event.event_json) if events else None
        )
        receipt = journal.JournalReadback(event, self.clock(), self.clock())
        events.append(receipt)
        return receipt

    async def read(self, scope, capture_id):
        assert all(
            journal.checked_event(item.event)["capture_id"] == capture_id
            for item in events
        )
        return tuple(events)

    monkeypatch.setattr(AccountCaptureJournalRepository, "_append", append)
    monkeypatch.setattr(AccountCaptureJournalRepository, "read_chain", read)
    arguments["journal_repository"] = repository
    return session, harness, checkpoints, arguments, events


def records(events, kind=None):
    values = [journal.checked_event(item.event) for item in events]
    return [value for value in values if kind is None or value["kind"] == kind]


def private_bytes(events):
    return b"".join(
        item.event.event_json
        + (item.event.raw_body or b"")
        + (item.event.packet_payload or b"")
        for item in events
    )


def assert_public(events, uid):
    for item in events:
        raw = item.receipt_json
        value = json.loads(raw)
        assert value["account_complete"] is value["execution_authority"] is False
        assert value["admission"] == "DENY"
        assert uid.encode() not in raw
        assert all(secret.encode() not in raw for secret in SECRETS)
        assert not set(value) & {
            "query",
            "account_id",
            "headers",
            "raw_body",
            "data",
            "balance",
            "position",
        }


@pytest.mark.asyncio
async def test_recorded_bootstrap_retains_private_raw_without_changing_old_result(
    monkeypatch,
):
    session, harness, checkpoints, args, events = setup(monkeypatch)
    result = await bootstrap.collect_bootstrap_recorded(session, **args)
    assert result.admission == "DENY" and not result.account_complete
    assert result.bootstrap.transport_provenance == "synthetic_transport"
    assert checkpoints[0].state == checkpoints[1].state
    assert records(events)[0]["kind"] == "capture_start"
    assert records(events)[-1]["outcome"] == "complete_recorded"
    raw_events = [item for item in events if item.event.raw_body is not None]
    assert len(raw_events) == len(result.bootstrap.packet.observations)
    assert [item.event.raw_body for item in raw_events] == [
        item.response_body for item in result.bootstrap.packet.observations
    ]
    packet = next(
        item.event.packet_payload for item in events if item.event.packet_payload
    )
    replay = capture.verify_demo_account_packet(
        packet,
        expected_sha256=journal.digest(packet),
        expected_plan_sha256=session._pin,
    )
    assert replay == result.bootstrap.packet
    assert session._plan.expected_uid.encode() in private_bytes(events)
    assert all(secret.encode() not in private_bytes(events) for secret in SECRETS)
    assert_public(events, session._plan.expected_uid)
    harness.assert_closed()


@pytest.mark.asyncio
async def test_capture_start_commit_or_readback_failure_prevents_any_network(
    monkeypatch,
):
    session, harness, _, args, events = setup(monkeypatch)

    async def fail(*args):
        raise RuntimeError("private failure must not leak")

    monkeypatch.setattr(AccountCaptureJournalRepository, "_append", fail)
    with pytest.raises(AccountRuntimeError, match="account_runtime_invalid"):
        await bootstrap.collect_bootstrap_recorded(session, **args)
    assert not harness.requests and not harness.factory_calls and not events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["before_headers", "status", "media", "partial", "cancel", "parse"]
)
async def test_failure_paths_keep_truthful_partial_states(monkeypatch, failure):
    options = {}
    if failure == "before_headers":
        options["handler_error"] = httpx.ReadTimeout("private request")
    if failure == "status":
        options["status"] = 503
    if failure == "media":
        options["response_headers"] = {"Content-Type": "text/plain"}
    if failure in {"partial", "cancel"}:
        options["chunks"] = [b'{"code":"0","data":']
        options["stream_error"] = (
            asyncio.CancelledError()
            if failure == "cancel"
            else httpx.ReadTimeout("private partial")
        )
    if failure == "parse":
        options["change"] = lambda *args: {"unknown": "safe actual JSON"}
    session, harness, _, args, events = setup(monkeypatch, **options)
    with pytest.raises(
        asyncio.CancelledError if failure == "cancel" else AccountRuntimeError
    ):
        await bootstrap.collect_bootstrap_recorded(session, **args)
    assert records(events)[-1]["outcome"] == (
        "cancelled" if failure == "cancel" else "failed"
    )
    retained = records(events, "raw_finalized")[0]["data"]
    if failure in {"before_headers", "status", "media"}:
        assert (
            retained["raw_retention"] == "not_read" and retained["observed_bytes"] == 0
        )
    elif failure in {"partial", "cancel"}:
        assert retained["raw_retention"] == "withheld_unverifiable_partial"
        assert retained["body_completed_at"] is None
        assert retained["observed_bytes"] > 0
    else:
        assert retained["raw_retention"] == "durable_secret_checked"
        assert any(item.event.raw_body for item in events)
    assert not any(item.event.packet_payload for item in events)
    assert len(harness.requests) == 1
    assert_public(events, session._plan.expected_uid)


@pytest.mark.asyncio
@pytest.mark.parametrize("escaped", [False, True])
async def test_cross_chunk_or_json_escaped_secret_never_reaches_plaintext_storage(
    monkeypatch, escaped
):
    token = SECRETS[0]
    encoded = "".join(f"\\u{ord(char):04x}" for char in token) if escaped else token
    raw = ('{"code":"0","data":[],"note":"' + encoded + '"}').encode()
    for boundary in range(1, len(raw)):
        session, _harness, _, args, events = setup(
            monkeypatch,
            change=lambda *args: raw,
            chunks=[raw[:boundary], raw[boundary:]],
        )
        with pytest.raises(AccountRuntimeError):
            await bootstrap.collect_bootstrap_recorded(session, **args)
        assert (
            records(events, "raw_finalized")[0]["data"]["raw_retention"]
            == "withheld_secret"
        )
        assert token.encode() not in private_bytes(events)
        assert not any(item.event.raw_body for item in events)
        assert_public(events, session._plan.expected_uid)


@pytest.mark.asyncio
async def test_later_signature_in_earlier_page_is_withheld_at_terminal_rescan(
    monkeypatch,
):
    marker = "SYNTHETIC_LATER_SIGNATURE_0000000000000000000="
    original = collector._signed_request
    count = 0

    def signed(*args):
        nonlocal count
        request, signature = original(*args)
        count += 1
        if count == 2:
            signature = marker
            request.headers["OK-ACCESS-SIGN"] = signature
        return request, signature

    monkeypatch.setattr(collector, "_signed_request", signed)

    def change(stream, index, data):
        return [dict(data[0], label=marker)] if index == 0 else None

    session, harness, _, args, events = setup(monkeypatch, change=change)
    with pytest.raises(AccountRuntimeError):
        await bootstrap.collect_bootstrap_recorded(session, **args)
    assert len(harness.requests) > 1
    assert marker.encode() not in private_bytes(events)
    assert (
        records(events, "raw_finalized")[0]["data"]["raw_retention"]
        == "withheld_secret"
    )
    assert any(item.event.raw_body for item in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("source_field", ["instrument_identity", "cursor_query"])
async def test_source_metadata_with_later_signature_has_no_early_plaintext(
    monkeypatch, source_field
):
    marker = (
        "SYNTHETIC_FUTURE_SIGNATURE_ROW_ID_00000000000="
        if source_field == "instrument_identity"
        else "984321098432109843210984321098432109"
    )
    selected_stream = (
        "account_instruments"
        if source_field == "instrument_identity"
        else "bills_recent"
    )
    ready = issued = False
    original = collector._signed_request

    def signed(*args):
        nonlocal issued
        request, signature = original(*args)
        if ready and not issued:
            signature = marker
            request.headers["OK-ACCESS-SIGN"] = signature
            issued = True
        return request, signature

    def change(stream, index, data):
        nonlocal ready
        if stream == selected_stream:
            if source_field == "instrument_identity":
                ready = True
                return [dict(data[0], instId=marker)]
            if data:
                return [dict(data[0], billId=marker)]
            ready = True  # issue marker after the source-derived cursor GET
        return None

    monkeypatch.setattr(collector, "_signed_request", signed)
    session, harness, _, args, events = setup(monkeypatch, change=change)
    if source_field == "cursor_query":
        from tests.unit.test_qualification_account_capture import row

        assert [
            data for stream, data in harness.script if stream == selected_stream
        ] == [[]]
        index = next(
            index
            for index, (stream, _) in enumerate(harness.script)
            if stream == selected_stream
        )
        harness.script.insert(index, (selected_stream, [row(selected_stream)]))
    with pytest.raises(AccountRuntimeError):
        await bootstrap.collect_bootstrap_recorded(session, **args)
    assert issued and len(harness.requests) > 1
    assert marker.encode() not in private_bytes(events)
    source_records = [
        item["data"]
        for item in records(events, "page_validated")
        if item["data"]["stream"] == selected_stream
    ]
    assert source_records
    for page in source_records:
        assert "query" not in page and "after" not in page
        for row in page["rows"]:
            assert "row_id" not in row and "instrument_id" not in row
            assert len(row["row_identity_sha256"]) == 64
    finalized = [
        item["data"]
        for item in records(events, "raw_finalized")
        if item["data"]["stream"] == selected_stream
    ]
    assert finalized[0]["raw_retention"] == "withheld_secret"
    if source_field == "cursor_query":
        assert finalized[1]["query_retention"] == "withheld_secret"
        assert "query" not in finalized[1] and "after" not in finalized[1]
    assert any(item.event.raw_body for item in events)
    assert not any(item.event.packet_payload for item in events)
    assert records(events)[-1]["outcome"] == "failed"
    harness.assert_closed()


@pytest.mark.asyncio
async def test_late_bootstrap_checkpoint_conflict_keeps_already_durable_safe_pages(
    monkeypatch,
):
    from tests.unit.test_qualification_bootstrap_runtime import unknown_state

    changed = unknown_state().model_copy(update={"ledger_revision": 1})
    session, harness, _, args, events = setup(
        monkeypatch, states=[unknown_state(), changed]
    )
    with pytest.raises(AccountRuntimeError):
        await bootstrap.collect_bootstrap_recorded(session, **args)
    assert any(item.event.raw_body for item in events)
    assert not any(item.event.packet_payload for item in events)
    assert records(events)[-1]["outcome"] == "failed"
    harness.assert_closed()


@pytest.mark.asyncio
async def test_expired_interrupted_capture_records_missing_payload_without_invented_time(
    monkeypatch,
):
    session, harness, _, args, events = setup(monkeypatch)

    async def death(self):
        yield b'{"unclosed":'
        raise SystemExit("synthetic abrupt process death")

    monkeypatch.setattr(Stream, "__aiter__", death)
    with pytest.raises(SystemExit):
        await bootstrap.collect_bootstrap_recorded(session, **args)
    assert not any(item.event.raw_body for item in events)
    assert records(events)[-1]["outcome"] == "in_progress"
    last = records(events)[-1]
    repository = args["journal_repository"]
    repository.clock = lambda: harness.clock() + timedelta(hours=2)
    receipt = await repository.recover_interrupted(
        checkpoints_scope(session),
        capture_id=last["capture_id"],
        expected_head_sha256=journal.digest(events[-1].event.event_json),
    )
    recovery = journal.checked_event(receipt.event)
    assert recovery["outcome"] == "interrupted_owner_unknown"
    assert (
        recovery["data"]["missing_raw"][0]["raw_retention"]
        == "unavailable_owner_unverified"
    )
    assert recovery["data"]["missing_raw"][0]["additional_unobserved_bytes"] is None
    assert recovery["data"]["missing_raw"][0]["original_body_completed_at"] is None
    assert len(harness.requests) == 1


def checkpoints_scope(session):
    from app.trade_qualification.reservations import LedgerScope

    return LedgerScope(
        account_id=session._plan.expected_uid,
        settlement_currency=session._plan.settlement_currency,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["future_source", "future_db"])
async def test_independent_db_receipt_clock_order_is_not_repaired(monkeypatch, change):
    session, _harness, _, args, events = setup(monkeypatch)
    await bootstrap.collect_bootstrap_recorded(session, **args)
    original = events[0]
    db = original.db_recorded_at
    readback = original.readback_at
    if change == "future_source":
        db -= timedelta(hours=1)
    else:
        db += timedelta(hours=1)
    with pytest.raises(journal.AccountJournalError, match="receipt_clock_order"):
        journal.JournalReadback(original.event, db, readback)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["raw_finalized", "packet_recorded", "terminal", "public_receipt"]
)
async def test_late_persistence_or_public_receipt_failure_preserves_prior_events(
    monkeypatch, stage
):
    session, harness, _, args, events = setup(monkeypatch)
    original = AccountCaptureJournalRepository._append
    failures = 0

    async def fail(self, scope, event):
        nonlocal failures
        kind = journal.checked_event(event)["kind"]
        if kind == stage and (
            kind != "raw_finalized" or any(item.event.raw_body for item in events)
        ):
            failures += 1
            raise RuntimeError(SECRETS[0])
        return await original(self, scope, event)

    if stage == "public_receipt":

        def render(self):
            raise RuntimeError(SECRETS[0])

        monkeypatch.setattr(journal.JournalReadback, "receipt_json", property(render))
    else:
        monkeypatch.setattr(AccountCaptureJournalRepository, "_append", fail)
    with pytest.raises(AccountRuntimeError) as error:
        await bootstrap.collect_bootstrap_recorded(session, **args)
    assert all(secret not in str(error.value) for secret in SECRETS)
    assert any(item.event.raw_body for item in events)
    assert all(secret.encode() not in private_bytes(events) for secret in SECRETS)
    assert len(harness.requests) == len(harness.script)
    if stage == "terminal":
        assert records(events)[-1]["kind"] == "packet_recorded"
    if stage == "public_receipt":
        assert records(events)[-1]["outcome"] == "complete_recorded"
    else:
        assert failures >= 1


@pytest.mark.asyncio
async def test_cleanup_failure_retains_actual_complete_body_but_denies_packet(
    monkeypatch,
):
    session, _, _, args, events = setup(monkeypatch)

    async def failed_close(self):
        self.close_count += 1
        raise OSError("private cleanup diagnostic")

    monkeypatch.setattr(Stream, "aclose", failed_close)
    with pytest.raises(AccountRuntimeError):
        await bootstrap.collect_bootstrap_recorded(session, **args)
    assert records(events, "body_complete")
    assert any(item.event.raw_body for item in events)
    assert not any(item.event.packet_payload for item in events)
    assert records(events)[-1]["outcome"] == "failed"


@pytest.mark.asyncio
async def test_bounded_finalization_stays_cancelled_and_has_fixed_timeout(monkeypatch):
    monkeypatch.setattr(journal, "FINALIZE_SECONDS", 0.02)

    async def never():
        await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        await journal.bounded_finalization(never())
    task = asyncio.create_task(journal.bounded_finalization(never()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 0.2)


@pytest.mark.asyncio
async def test_foreign_hook_or_runtime_dto_cannot_be_owned_input(monkeypatch):
    session, harness, _, args, events = setup(monkeypatch)

    class Foreign:
        def __getattribute__(self, name):
            pytest.fail("foreign hook callback invoked")

    with pytest.raises(collector.AccountCollectionError):
        await collector._collect_owned_demo_account_records(
            credentials=session._credentials,
            clock=harness.clock,
            plan=session._plan,
            expected_plan_sha256=session._pin,
            barrier_completed_at=args["barrier_completed_at"],
            _journal=Foreign(),
        )
    assert not harness.requests and not events
    with pytest.raises(AccountRuntimeError):
        await bootstrap.collect_bootstrap_recorded(Foreign(), **args)
    assert not harness.requests and not events


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [1023, 1024, 1025])
async def test_observed_and_buffered_body_limits_are_distinct(monkeypatch, size):
    session, harness, _, args, events = setup(monkeypatch)
    plan = session._plan.model_copy(
        update={"max_response_bytes": 1024, "max_total_bytes": 1024}
    )
    owned = await journal.start_owned_journal(
        repository=args["journal_repository"],
        scope=checkpoints_scope(session),
        plan=plan,
        checkpoint="a" * 64,
        clock=harness.clock,
        tokens=SECRETS,
    )
    spec = capture.account_request(plan, "config_before")
    await owned.request(
        stream="config_before",
        page_index=0,
        after=None,
        previous_sha=None,
        identity=None,
        spec=spec,
        started=harness.clock(),
    )
    await owned.headers(harness.clock(), 200)
    core = b'{"code":"0","data":[]}'
    raw = b" " * (size - len(core)) + core
    if size > 1024:
        with pytest.raises(journal.AccountJournalError, match="buffer_limit"):
            await owned.chunk(raw, harness.clock())
    else:
        await owned.chunk(raw, harness.clock())
        await owned.body_complete(harness.clock())
    await owned.close_acquisition(successful=False)
    finalized = records(events, "raw_finalized")[0]["data"]
    assert finalized["observed_bytes"] == size
    assert finalized["prefix_sha256"] == journal.digest(raw)
    assert finalized["buffer_complete"] is (size <= 1024)
    saved = [event.event.raw_body for event in events if event.event.raw_body]
    assert saved == ([raw] if size <= 1024 else [])


@pytest.mark.asyncio
async def test_one_byte_chunks_are_scanned_as_one_buffer(monkeypatch):
    raw = ('{"code":"0","data":[],"label":"' + SECRETS[1] + '"}').encode()
    session, _, _, args, events = setup(
        monkeypatch,
        change=lambda *args: raw,
        chunks=[raw[i : i + 1] for i in range(len(raw))],
    )
    with pytest.raises(AccountRuntimeError):
        await bootstrap.collect_bootstrap_recorded(session, **args)
    assert records(events, "raw_finalized")[0]["data"]["observed_bytes"] == len(raw)
    assert (
        records(events, "raw_finalized")[0]["data"]["raw_retention"]
        == "withheld_secret"
    )
    assert not any(item.event.raw_body for item in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("second_size", [424, 425])
async def test_total_buffer_limit_keeps_safe_earlier_page_without_truncation(
    monkeypatch, second_size
):
    session, harness, _, args, events = setup(monkeypatch)
    plan = session._plan.model_copy(
        update={"max_response_bytes": 1024, "max_total_bytes": 1024}
    )
    owned = await journal.start_owned_journal(
        repository=args["journal_repository"],
        scope=checkpoints_scope(session),
        plan=plan,
        checkpoint="a" * 64,
        clock=harness.clock,
        tokens=SECRETS,
    )
    buffers = []
    for index, size in enumerate((600, second_size)):
        stream = ("config_before", "balance")[index]
        await owned.request(
            stream=stream,
            page_index=0,
            after=None,
            previous_sha=None,
            identity=None,
            spec=capture.account_request(plan, stream),
            started=harness.clock(),
        )
        await owned.headers(harness.clock(), 200)
        core = b'{"code":"0","data":[]}'
        raw = b" " * (size - len(core)) + core
        if index == 1 and second_size > 424:
            with pytest.raises(journal.AccountJournalError, match="buffer_limit"):
                await owned.chunk(raw, harness.clock())
        else:
            await owned.chunk(raw, harness.clock())
            await owned.body_complete(harness.clock())
            buffers.append(raw)
    await owned.close_acquisition(successful=False)
    assert [item.event.raw_body for item in events if item.event.raw_body] == buffers
    assert owned.total == (1024 if second_size == 424 else 600)
    assert all(
        item["data"]["terminal_secret_set_closed"]
        for item in records(events, "raw_finalized")
    )


def test_db0020_downgrade_locks_before_empty_check_and_drop(monkeypatch):
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0020_account_capture_journal.py"
    )
    spec = importlib.util.spec_from_file_location("synthetic_b1_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    operations = []

    class Op:
        @staticmethod
        def execute(sql):
            operations.append(str(sql))

        @staticmethod
        def drop_table(name):
            operations.append("DROP " + name)

    monkeypatch.setattr(migration, "op", Op)
    migration.downgrade()
    assert operations[0] == (
        "LOCK TABLE qualification_account_scopes, demo_account_capture_events "
        "IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    assert "IF EXISTS(SELECT 1 FROM demo_account_capture_events)" in operations[1]
    assert operations[2] == "DROP demo_account_capture_events"


def test_db0020_model_constraint_names_match_declared_migration():
    from pathlib import Path

    from app.database.models.account_capture_journal import DemoAccountCaptureEvent

    sql = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0020_account_capture_journal.py"
    ).read_text()
    assert all(
        constraint.name in sql
        for constraint in DemoAccountCaptureEvent.__table__.constraints
    )
    assert "chain_bytes" in DemoAccountCaptureEvent.__table__.c
    assert "NEW.chain_bytes IS DISTINCT FROM" in sql
    assert "chr(0)" not in sql


@pytest.mark.asyncio
async def test_portable_result_cannot_replace_same_invocation_owned_packet(monkeypatch):
    session, _, _, args, events = setup(monkeypatch)
    original = journal._OwnedAccountJournal.finish

    async def replace_packet(self, *, result=None, error=None):
        if result is not None:
            self.owned_packet_sha256 = "f" * 64
        return await original(self, result=result, error=error)

    monkeypatch.setattr(journal._OwnedAccountJournal, "finish", replace_packet)
    with pytest.raises(AccountRuntimeError):
        await bootstrap.collect_bootstrap_recorded(session, **args)
    assert any(item.event.raw_body for item in events)
    assert not any(item.event.packet_payload for item in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["headers", "partial_chunk", "chunk"])
async def test_failed_clock_sample_does_not_erase_actual_received_observation(
    monkeypatch, stage
):
    session, harness, _, args, events = setup(monkeypatch)
    original = collector._read_clock
    iterator = Stream.__aiter__
    chunk_started = False
    failed = False

    async def observed_chunk(self):
        nonlocal chunk_started
        if stage == "chunk":
            chunk_started = True
            self.yield_count += 1
            yield self.body
        else:
            async for chunk in iterator(self):
                chunk_started = True
                yield chunk

    def clock(value):
        nonlocal failed
        if not failed and harness.requests and (stage == "headers" or chunk_started):
            failed = True
            raise collector.AccountCollectionError("clock_invalid")
        return original(value)

    monkeypatch.setattr(Stream, "__aiter__", observed_chunk)
    monkeypatch.setattr(collector, "_read_clock", clock)
    with pytest.raises(AccountRuntimeError):
        await bootstrap.collect_bootstrap_recorded(session, **args)
    assert failed
    if stage == "headers":
        header = records(events, "headers_received")[0]
        assert header["observed_at"] is None
        assert header["data"]["headers_received_at"] is None
        assert (
            records(events, "raw_finalized")[0]["data"]["raw_retention"] == "not_read"
        )
    else:
        progress = records(events, "body_progress")[0]
        assert progress["observed_at"] is None
        assert progress["data"]["observed_bytes"] > 0
        assert any(item.event.raw_body for item in events) is (stage == "chunk")
        if stage == "partial_chunk":
            assert (
                records(events, "raw_finalized")[0]["data"]["raw_retention"]
                == "withheld_unverifiable_partial"
            )
        assert records(events, "raw_finalized")[0]["data"]["body_completed_at"] is None
    assert not any(item.event.packet_payload for item in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("sample", [1, 2, 3, 4, 5])
async def test_transient_source_clock_reversal_retains_observation_but_denies_packet(
    monkeypatch, sample
):
    session, harness, _, args, events = setup(monkeypatch)
    original = collector._read_clock
    seen = 0
    injected = False

    def clock(value):
        nonlocal seen, injected
        actual = original(value)
        if len(harness.requests) == 1:
            seen += 1
            if seen == sample:
                injected = True
                if sample == 5:
                    eof = records(events, "body_complete")[0]["observed_at"]
                    return datetime.fromisoformat(eof) - timedelta(microseconds=1)
                return actual - timedelta(seconds=1)
        return actual

    monkeypatch.setattr(collector, "_read_clock", clock)
    with pytest.raises(AccountRuntimeError):
        await bootstrap.collect_bootstrap_recorded(session, **args)
    assert injected and len(harness.requests) == 1
    rejected = [
        item
        for item in records(events)
        if item["data"].get("clock_order") == "reversed"
        and item["kind"] != "raw_finalized"
    ]
    assert len(rejected) == 1
    record = rejected[0]
    assert record["data"]["source_observed_at"] == record["observed_at"]
    assert record["data"]["previous_source_observed_at"] > record["observed_at"]
    expected_bytes = (
        0 if sample == 1 else 11 if sample == 2 else len(harness.streams[0].body)
    )
    assert record["data"]["observed_bytes"] == expected_bytes
    assert any(item.event.raw_body for item in events) is (sample >= 3)
    assert not any(item.event.packet_payload for item in events)
    assert not records(events, "acquisition_closed")[0]["data"][
        "source_clock_order_valid"
    ]
    assert records(events)[-1]["outcome"] == "failed"
    harness.assert_closed()


@pytest.mark.asyncio
async def test_failed_eof_clock_preserves_observed_exhaustion_without_inventing_time(
    monkeypatch,
):
    session, harness, _, args, events = setup(monkeypatch)
    original = collector._read_clock
    seen = 0

    def clock(value):
        nonlocal seen
        if len(harness.requests) == 1:
            seen += 1
            if seen == 4:
                raise collector.AccountCollectionError("clock_invalid")
        return original(value)

    monkeypatch.setattr(collector, "_read_clock", clock)
    with pytest.raises(AccountRuntimeError):
        await bootstrap.collect_bootstrap_recorded(session, **args)
    eof = records(events, "body_complete")[0]
    assert eof["observed_at"] is None
    assert eof["data"]["body_completed_at"] is None
    assert eof["data"]["body_exhaustion_observed"]
    assert eof["data"]["clock_order"] == "missing_sample"
    assert any(item.event.raw_body == harness.streams[0].body for item in events)
    assert not any(item.event.packet_payload for item in events)
    assert records(events)[-1]["outcome"] == "failed"
