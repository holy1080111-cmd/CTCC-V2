"""HELD real-PG synthetic-wire mechanics, no native account/TLS authority credit.

Reuse the current original0052 pooled fresh-database/real-login fixture. Every
new test must run on its own actual stage; existing43 results cannot be reused.
"""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.repositories import (
    account_history_original_financial_read_v3 as native,
)
from app.database.repositories import account_history_original_frame_v1 as wire
from app.database.repositories import (
    account_history_original_native_read_v1 as source_native,
)
from app.domain import account_history_original_source_catalog_v1 as source_guard
from app.domain import account_history_partition_catalog_v1 as parent
from app.trade_qualification import account_history_original_stream_v1 as stream
from app.trade_qualification import account_native_proof as proof
from tests.integration import test_account_history_original_native_read_v1 as original
from tests.integration.test_account_history_original_frame_repository_v1 import (
    original_frame_database as original_frame_database,  # noqa: PLC0414
)
from tests.integration.test_account_history_original_native_read_v1 import (
    INSTRUMENT,
    MAIN_UID,
    NOW,
    UID,
    AccountCaptureJournalRepository,
    ControlledDemoAccountSession,
    _closed_original_source_leaf,
    all_product_script,
    bootstrap,
    capture,
    credentials,
    hashlib,
    journal,
    memory_setup,
    ms,
    now,
    observed,
    original_proof,
    source_pages,
    synthetic_tls_chain,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_pg_financial_distinct_sessions_original33_uses_same_expiry_and_measured_finish(
    monkeypatch,
    original_frame_database,
):
    admin = original_frame_database
    (
        scope,
        documents,
        revisions,
        lease,
        checkpoint,
    ) = await _distinct_prefix33(monkeypatch, admin)
    async with original.collector(admin, grant=True) as (reader, oid):
        # FIRST construction follows real role setup, exactly as the existing
        # native acceptance. No existing request expiry or Control lease resets.
        request = original._request(scope, documents, revisions, lease)
        result = await native.replay_original_history_financial_from_pg_v3(
            async_sessionmaker(reader, expire_on_commit=False, autoflush=False),
            genesis_request=request,
            expected_checkpoint_sha256=checkpoint,
            approved_collector_role_oid=oid,
            expected_financial_policy_sha256=stream.original_history_financial_stream_policy_sha256_v3(),
        )
        await original._assert_reader_gone(admin, reader)
        acquisition = json.loads(result.acquisition_receipt_json)
        joined = json.loads(result.replay.receipt_json)
        facts = json.loads(result.replay.financial_facts.receipt_json)
        original_receipt = json.loads(result.replay.original_replay.receipt_json)
        assert (
            result.observed_frames
            == acquisition["observed_frames"]
            == joined["through_sequence"]
            == 33
        )
        assert len(facts["original_current_sources"]) == 33
        assert (
            joined["checkpoint_sha256"]
            == checkpoint
            == original_receipt["checkpoint_sha256"]
        )
        assert acquisition["original_expires_at"] == request.expires_at.isoformat()
        assert acquisition["composite_receipt_sha256"] == result.replay.receipt_sha256
        assert (
            joined["checked_at"]
            == facts["checked_at"]
            == result.measured_validation_at.isoformat()
        )
        assert (
            result.first_request_started_at
            <= result.last_rpc_received_at
            <= result.measured_validation_at
            <= result.session_closed_at
            < request.expires_at
        )
        assert "native_acquisition_not_owned" in joined["remaining_obligations"]
        assert facts["native_position_archive_sha256"] is None
        for value in (acquisition, joined, facts):
            for key in (
                "account_complete",
                "account_revision_published",
                "execution_authority",
            ):
                assert value[key] is False
            assert value["admission"] == "DENY"
        assert (
            result.native_history_prefix_verified
            is result.account_complete
            is result.execution_authority
            is False
        )
        assert original_receipt["native_registration_missing_count"] == 32
        # The new arithmetic must not acquire a direct source/control table grant.
        async with reader.begin() as connection:
            for table in (
                *source_guard.TABLES,
                *parent.TABLES,
                "qualification_account_scopes",
                "demo_account_controls",
            ):
                assert (
                    await connection.scalar(
                        text(
                            "SELECT has_table_privilege(current_user,:table,'SELECT')"
                        ),
                        {"table": "public." + table},
                    )
                    is False
                )


@pytest.mark.parametrize("source_kind", ["regional", "all_product_unannotated"])
async def test_pg_financial_default_source_refusals_are_not_requalified(
    monkeypatch,
    original_frame_database,
    source_kind,
):
    admin = original_frame_database
    if source_kind == "regional":
        scope, documents, revisions, lease = await original._sources(monkeypatch, admin)
        expected = "native_account_source_scope_unsupported"
    else:
        scope, documents, revisions, lease = await original._new_all_product_sources(
            monkeypatch,
            admin,
            count=1,
            declared_synthetic_wire=False,
        )
        expected = "native_account_original_tls_required"
    async with original.collector(admin, grant=True) as (reader, oid):
        request = original._request(scope, documents, revisions, lease)
        with pytest.raises(native.OriginalHistoryFinancialPgReadError) as rejected:
            await native.replay_original_history_financial_from_pg_v3(
                async_sessionmaker(reader, expire_on_commit=False, autoflush=False),
                genesis_request=request,
                expected_checkpoint_sha256=None,
                approved_collector_role_oid=oid,
                expected_financial_policy_sha256=stream.original_history_financial_stream_policy_sha256_v3(),
            )
        error = rejected.value
        found = False
        for _ in range(4):
            if type(error) is proof.NativeAccountProofError:
                found = error.args == (expected,)
                break
            if type(error) not in {
                native.OriginalHistoryFinancialPgReadError,
                source_native.OriginalHistoryPgReadError,
                wire.OriginalFrameWireError,
                stream.OriginalHistoryStreamError,
            }:
                break
            error = error.__context__
        assert found
        await original._assert_reader_gone(admin, reader)


@pytest.mark.parametrize("change", ["head", "control", "cursor"])
async def test_pg_financial_changed_original_request_cannot_return_a_composite(
    monkeypatch,
    original_frame_database,
    change,
):
    admin = original_frame_database
    scope, documents, revisions, lease = await original._new_all_product_sources(
        monkeypatch,
        admin,
        count=2,
        declared_synthetic_wire=True,
    )
    async with original.collector(admin, grant=True) as (reader, oid):
        request = original._request(scope, documents, revisions, lease)
        if change == "head":
            request = replace(request, head="f" * 64)
        elif change == "control":
            request = replace(request, control=request.control + 1)
        else:
            request = replace(request, after=1, previous=request.head)
        with pytest.raises(native.OriginalHistoryFinancialPgReadError):
            await native.replay_original_history_financial_from_pg_v3(
                async_sessionmaker(reader, expire_on_commit=False, autoflush=False),
                genesis_request=request,
                expected_checkpoint_sha256=None,
                approved_collector_role_oid=oid,
                expected_financial_policy_sha256=stream.original_history_financial_stream_policy_sha256_v3(),
            )
        await original._assert_reader_gone(admin, reader)


async def test_pg_financial_cancelled_real_uid_wait_closes_without_partial_result(
    monkeypatch,
    original_frame_database,
):
    admin = original_frame_database
    scope, documents, revisions, lease = await original._new_all_product_sources(
        monkeypatch,
        admin,
        count=1,
        declared_synthetic_wire=True,
    )
    uid_sql = parent.UID_SQL.replace("p_environment", "'demo'").replace(
        "p_account_id", ":uid"
    )
    async with original.collector(admin, grant=True) as (reader, oid):
        request = original._request(scope, documents, revisions, lease)
        async with admin.connect() as holder, holder.begin():
            await holder.execute(
                text("SELECT pg_advisory_xact_lock(" + uid_sql + ")"),
                {"uid": scope.account_id},
            )
            task = asyncio.create_task(
                native.replay_original_history_financial_from_pg_v3(
                    async_sessionmaker(reader, expire_on_commit=False, autoflush=False),
                    genesis_request=request,
                    expected_checkpoint_sha256=None,
                    approved_collector_role_oid=oid,
                    expected_financial_policy_sha256=stream.original_history_financial_stream_policy_sha256_v3(),
                )
            )
            try:
                found = False
                for _ in range(50):
                    async with admin.connect() as observer:
                        found = bool(
                            await observer.scalar(
                                text(
                                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                                    "WHERE usename=:role AND wait_event_type='Lock')"
                                ),
                                {"role": reader.url.username},
                            )
                        )
                    if found:
                        break
                    await asyncio.sleep(0.01)
                assert found
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await original._assert_reader_gone(admin, reader)
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        async with admin.connect() as probe, probe.begin():
            assert (
                await probe.scalar(
                    text("SELECT pg_try_advisory_xact_lock(" + uid_sql + ")"),
                    {"uid": scope.account_id},
                )
                is True
            )


# NEW source construction only; every original B1 is unchanged after its first append.
async def _new_distinct_session_original_before_storage(
    monkeypatch, admin, index, scope, *, declared_synthetic_wire
):
    """Create new AllProduct wire bytes; NEVER alter a stored journal/packet.

    The existing memory helper supplies only a scoped pre-storage emitter sink.
    No verifier is mocked. Its temporary journal/checkpoint methods are restored
    before any actual B1 append. The TLS annotation is an explicit synthetic
    wire-format fixture, never observed TLS or native source ownership.
    """
    before = await index.journal.ledger.initialize_capture_scope(scope)
    captured_now = now()
    anchor = captured_now.replace(
        microsecond=captured_now.microsecond // 1000 * 1000
    ) - timedelta(seconds=10)
    # Direct construction: no Regional object conversion, model_copy or retag.
    selected = capture.AllProductDemoAccountCapturePlan(
        plan_id="synthetic_wire_all_product_history",
        created_at=anchor,
        expected_uid=UID,
        expected_main_uid=MAIN_UID,
        session_binding_id="declared_fresh_wire_" + uuid4().hex,
        settlement_currency="USDT",
        leverage_instrument_ids=(INSTRUMENT,),
        history_start=anchor - timedelta(days=6),
        history_end=anchor - timedelta(seconds=1),
        registration_region="global",
        origin="https://openapi.okx.com",
        registration_evidence_sha256=hashlib.sha256(
            b"DECLARED_SYNTHETIC_WIRE_REGISTRATION_NOT_AUTHENTICATED"
        ).hexdigest(),
        contract_version="ctcc.demo_account_plan.v5",
        capture_scope="all_standard_products_v5_documented_algos",
    )
    assert type(selected) is capture.AllProductDemoAccountCapturePlan
    delta = int(ms(anchor)) - int(ms(NOW))

    def shift(value):
        if type(value) is list:
            return [shift(item) for item in value]
        if type(value) is dict:
            return {
                key: str(int(item) + delta)
                if key in {"ts", "cTime", "uTime", "fillTime"}
                and type(item) is str
                and item.isdigit()
                else shift(item)
                for key, item in value.items()
            }
        return value

    pages = shift(source_pages())
    # Explicit declared empty historical populations, with every required V5
    # query still issued. Missing responses are never changed into empty rows.
    for family in (
        "fills_recent",
        "fills_history",
        "bills_recent",
        "bills_archive",
        "orders_history_recent",
        "orders_history_archive",
    ):
        pages[family] = [[]]
    real_append = AccountCaptureJournalRepository._append
    real_read = AccountCaptureJournalRepository.read_chain
    ledger_type = type(index.journal.ledger)
    real_initialize = ledger_type.initialize_capture_scope
    real_checkpoint = ledger_type.read_bootstrap_checkpoint
    real_capture_checkpoint = ledger_type.read_capture_checkpoint
    with monkeypatch.context() as memory:
        _, harness, _, arguments, events = memory_setup(
            memory, states=[before.state, before.state]
        )
        harness.clock = now
        harness.script = all_product_script(pages=pages, source=pages)
        arguments["clock"] = now
        arguments["barrier_completed_at"] = anchor - timedelta(seconds=2)
        arguments["repository"].clock = now
        arguments["journal_repository"].clock = now
        session = ControlledDemoAccountSession(
            credentials=credentials(session_binding_id=selected.session_binding_id),
            plan=selected,
            expected_plan_sha256=capture.plan_sha256(selected),
        )
        captured = await bootstrap.collect_bootstrap_recorded(session, **arguments)
        assert captured.account_complete is captured.execution_authority is False
        harness.assert_closed()
        assert harness.requests and all(
            request.method == "GET" and request.headers["x-simulated-trading"] == "1"
            for request in harness.requests
        )
        original_unpersisted = tuple(events)
        # Explicit wire annotation occurs while these NEW events exist only in
        # memory. It does not represent a real TLS/network observation.
        construction = (
            synthetic_tls_chain(original_unpersisted)
            if declared_synthetic_wire
            else original_unpersisted
        )
    assert AccountCaptureJournalRepository._append is real_append
    assert AccountCaptureJournalRepository.read_chain is real_read
    assert ledger_type.initialize_capture_scope is real_initialize
    assert ledger_type.read_bootstrap_checkpoint is real_checkpoint
    assert ledger_type.read_capture_checkpoint is real_capture_checkpoint
    if declared_synthetic_wire:
        construction_diagnostic = None
        try:
            original_proof._source(construction, scope)
        except Exception as error:  # noqa: BLE001 -- fixed-code-only TEST diagnostic, never exception text.
            construction_diagnostic = _closed_original_source_leaf(error)
        # Outside except: do not chain original source-bearing failure context.
        if construction_diagnostic is not None:
            pytest.fail(construction_diagnostic, pytrace=False)
    capture_id = journal.checked_event(construction[0].event)["capture_id"]
    async with admin.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM public.demo_account_capture_events WHERE capture_id=:id"
                ),
                {"id": capture_id},
            )
            == 0
        )
    for point in construction:
        stored = await index.journal._append(scope, point.event)
        assert stored.event == point.event
    chain = await index.journal.read_chain(scope, capture_id)
    assert tuple(point.event for point in chain) == tuple(
        point.event for point in construction
    )
    return chain, observed.ExecutionWindow(
        selected.history_start, selected.history_end - timedelta(seconds=600)
    )


async def _distinct_prefix33(monkeypatch, admin):
    real = original._new_all_product_original_before_storage
    try:
        with monkeypatch.context() as construction:
            construction.setattr(
                original,
                "_new_all_product_original_before_storage",
                _new_distinct_session_original_before_storage,
            )
            result = await original._prefix33_with_original_checkpoint(
                construction, admin
            )
    finally:
        assert original._new_all_product_original_before_storage is real
    documents = result[1]
    assert (
        len(
            {
                json.loads(raw)["reference"]["session_binding_sha256"]
                for raw in documents
            }
        )
        == 33
    )
    return result
