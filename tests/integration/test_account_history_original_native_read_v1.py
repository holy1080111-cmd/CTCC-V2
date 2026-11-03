"""UNRUN PG17 transport/replay mechanisms with declared synthetic wire B1/B3.

These reuse the exact R10 fresh-database/real-login fixture. They do not assert
native source ownership, account completeness, economics or order authority.
No private credentials, external account transport or order endpoint is used.
Synthetic wire construction gives ZERO R5/Demo/native account acceptance credit.
"""

import asyncio
import hashlib
import json
import re
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.repositories import account_history_original_frame_v1 as wire
from app.database.repositories import account_history_original_native_read_v1 as native
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
)
from app.domain import account_history_original_source_catalog_v1 as source_guard
from app.domain import account_history_partition_catalog_v1 as parent
from app.domain import account_history_partition_v1 as records
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import (
    account_history_original_stream_v1 as original_stream,
)
from app.trade_qualification import account_history_query_verifier as history
from app.trade_qualification import account_native_proof as original_proof
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope
from tests.controlled_writer_coverage_fixtures import seed_controlled_writer_control
from tests.integration.test_account_history_original_frame_repository_v1 import (
    collector,
    settings,
)
from tests.integration.test_account_history_original_frame_repository_v1 import (
    original_frame_database as original_frame_database,  # noqa: PLC0414
)
from tests.integration.test_account_history_partition_repository import (
    _append,
    _member,
    _sources,
)
from tests.integration.test_account_observation_index_repository import (
    now,
)
from tests.unit.test_account_ingestion_journal_contracts import setup as memory_setup
from tests.unit.test_account_native_proof import synthetic_tls_chain
from tests.unit.test_qualification_account_capture import (
    INSTRUMENT,
    MAIN_UID,
    NOW,
    UID,
    ms,
)
from tests.unit.test_qualification_account_collector import credentials
from tests.unit.test_qualification_account_materializer import source_pages
from tests.unit.test_qualification_account_v4 import script as all_product_script

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _request(scope, documents, revisions, lease):
    return wire.OriginalFrameRequestV1(
        scope,
        len(documents),
        records.digest(documents[-1]),
        0,
        None,
        *revisions,
        min(lease, datetime.now(UTC) + timedelta(seconds=20)),
    )


class _ClosedOriginalTiming:
    """TEST observation only: call each real function once, preserve return/raise.

    Phase times are inclusive and MUST NOT be added together. Only fixed tags,
    bounded page numbers, call counts and monotonic durations leave this object.
    No argument, SQL, source bytes, UID, exception or dynamic class is recorded.
    """

    PHASES = (
        "INITIAL_CATALOG",
        "FINAL_CATALOG",
        "CATALOG_OTHER",
        "RPC",
        "DECODE",
        "SOURCE_PROOF",
        "QUERY_VERIFY",
        "PACKET_VERIFY",
        "STREAM_RECEIPT",
        "FENCE",
    )

    def __init__(self):
        self.phase, self.page = "PIPELINE", 0
        self.setup_grant_ns = self.pipeline_ns = self.catalog_calls = 0
        self.initial_request_remaining_us = None
        self.total = {name: [0, 0] for name in self.PHASES}
        self.pages = {}
        self.first_failure = None

    def _page(self, value):
        # Page identifiers come only from the declared positive request fields.
        if type(value) is int and 1 <= value <= 33:
            self.page = value
        else:
            self.page = 0

    def _done(self, phase, started):
        elapsed = time.monotonic_ns() - started
        self.total[phase][0] += 1
        self.total[phase][1] += elapsed
        if self.page:
            values = self.pages.setdefault(self.page, {})
            item = values.setdefault(phase, [0, 0])
            item[0] += 1
            item[1] += elapsed

    def _failed(self):
        if self.first_failure is None:
            self.first_failure = {"phase": self.phase, "page": self.page}

    def _sync(self, phase, function, *args, **kwargs):
        previous, self.phase = self.phase, phase
        started = time.monotonic_ns()
        try:
            return function(*args, **kwargs)
        except BaseException:
            self._failed()
            raise
        finally:
            self._done(phase, started)
            self.phase = previous

    def install(self, measured):
        """Install observation wrappers only in this owned test context."""
        originals = (
            (native, "_catalog", native._catalog),
            (native, "_execute_one", native._execute_one),
            (wire, "decode_original_frame_wire_v1", wire.decode_original_frame_wire_v1),
            (original_proof, "_source", original_proof._source),
            (original_stream, "_original_receipt", original_stream._original_receipt),
            (native._Fence, "check", native._Fence.check),
        )
        original_catalog, original_execute, original_decode, original_source = (
            item[2] for item in originals[:4]
        )
        original_receipt, original_fence = (item[2] for item in originals[4:])

        async def catalog(*args, **kwargs):
            self.catalog_calls += 1
            phase = (
                "INITIAL_CATALOG"
                if self.catalog_calls == 1
                else "FINAL_CATALOG"
                if self.catalog_calls == 2
                else "CATALOG_OTHER"
            )
            previous, self.phase = self.phase, phase
            started = time.monotonic_ns()
            try:
                return await original_catalog(*args, **kwargs)
            except BaseException:
                self._failed()
                raise
            finally:
                self._done(phase, started)
                self.phase = previous

        async def execute(session, statement, fence, parameters=None):
            if statement != wire.SQL:
                return await original_execute(session, statement, fence, parameters)
            self._page(
                parameters.get("after") + 1
                if type(parameters) is dict and type(parameters.get("after")) is int
                else None
            )
            previous, self.phase = self.phase, "RPC"
            started = time.monotonic_ns()
            try:
                return await original_execute(session, statement, fence, parameters)
            except BaseException:
                self._failed()
                raise
            finally:
                self._done("RPC", started)
                self.phase = previous

        def decode(*args, **kwargs):
            return self._sync("DECODE", original_decode, *args, **kwargs)

        def source(*args, **kwargs):
            return self._sync("SOURCE_PROOF", original_source, *args, **kwargs)

        def receipt(*args, **kwargs):
            return self._sync("STREAM_RECEIPT", original_receipt, *args, **kwargs)

        def check(fence):
            # Preserve the surrounding phase when the real expiry guard raises.
            started = time.monotonic_ns()
            try:
                return original_fence(fence)
            except BaseException:
                self._failed()
                raise
            finally:
                self._done("FENCE", started)

        for (owner, name, _), wrapper in zip(
            originals,
            (catalog, execute, decode, source, receipt, check),
            strict=True,
        ):
            measured.setattr(owner, name, wrapper)
        return originals

    @staticmethod
    def assert_restored(originals):
        assert all(
            getattr(owner, name) is function for owner, name, function in originals
        )

    def serialized(self):
        value = {
            "schema": "ctcc.synthetic_wire.original_pg33.closed_timing.v2",
            "inclusive_nested_times_not_additive": True,
            "guarded_query_packet_callees_wrapped": False,
            "initial_request_remaining_wall_us": self.initial_request_remaining_us,
            "setup_grant_ns": self.setup_grant_ns,
            "pipeline_ns": self.pipeline_ns,
            "phases": [
                {"phase": name, "calls": values[0], "elapsed_ns": values[1]}
                for name, values in self.total.items()
            ],
            "pages": [
                {
                    "page": number,
                    "phases": [
                        {"phase": name, "calls": values[0], "elapsed_ns": values[1]}
                        for name, values in phases.items()
                    ],
                }
                for number, phases in self.pages.items()
            ],
            "first_failure": self.first_failure,
            "R5_Demo_native_account_credit": 0,
            "complete_producer_acceptance_credit": 0,
        }
        assert (
            type(self.initial_request_remaining_us) is int
            and -(2**63) < self.initial_request_remaining_us < 2**63
        )
        numbers = [self.setup_grant_ns, self.pipeline_ns]
        numbers.extend(item for values in self.total.values() for item in values)
        numbers.extend(
            item
            for phases in self.pages.values()
            for values in phases.values()
            for item in values
        )
        assert all(type(item) is int and 0 <= item < 2**63 for item in numbers)
        assert len(self.pages) <= 33
        raw = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        assert len(raw.encode("ascii")) <= 65536
        return raw


async def _assert_reader_gone(admin, reader):
    # A joined close can precede the server observing TCP EOF by a small amount.
    # This is test observation only; it extends no acquisition or Control lease.
    for _ in range(50):
        async with admin.connect() as observer:
            count = await observer.scalar(
                text("SELECT count(*) FROM pg_stat_activity WHERE usename=:role"),
                {"role": reader.url.username},
            )
        if count == 0:
            return
        await asyncio.sleep(0.01)
    pytest.fail("reader_session_cleanup_not_observed")


async def _new_all_product_original_before_storage(
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
        session_binding_id="synthetic_wire_demo_session",
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


async def _new_all_product_sources(
    monkeypatch, admin, *, count, declared_synthetic_wire
):
    factory = async_sessionmaker(admin, expire_on_commit=False, autoflush=False)
    index = AccountObservationIndexRepository(factory, clock=now)
    scope = LedgerScope(account_id=UID, settlement_currency="USDT")
    documents = []
    for revision in range(count):
        chain, window = await _new_all_product_original_before_storage(
            monkeypatch,
            admin,
            index,
            scope,
            declared_synthetic_wire=declared_synthetic_wire,
        )
        reference = observed.source_reference(chain)
        current = await index.append(
            scope, reference, window, expected_revision=revision
        )
        documents.append(current.document_json)
    async with admin.begin() as connection:
        control, _ = await seed_controlled_writer_control(
            connection, uid=scope.account_id
        )
        state = (
            await connection.execute(
                text(
                    "SELECT account_revision,ledger_revision FROM public.qualification_account_scopes "
                    "WHERE environment='demo' AND account_id=:uid AND settlement_currency='USDT'"
                ),
                {"uid": scope.account_id},
            )
        ).one()
    return (
        records.HistoryPartitionScopeV1("demo", UID, "USDT"),
        tuple(documents),
        (control.revision, state.account_revision, state.ledger_revision),
        control.lease_until,
    )


async def _prefix33_with_original_checkpoint(monkeypatch, admin):
    scope, documents, revisions, lease = await _new_all_product_sources(
        monkeypatch, admin, count=32, declared_synthetic_wire=True
    )
    member, _ = _member(scope, documents[-1])
    checkpoint = records.build_partition_checkpoint_v1(
        scope=scope,
        b3_documents=documents,
        native_registrations=(None,) * 31 + (member,),
        previous_checkpoints=(),
        expected_b3_policy_sha256=json.loads(documents[-1])["policy_sha256"],
        expected_b3_head_sha256=records.digest(documents[-1]),
    ).record_json
    # Persist the original32 checkpoint BEFORE original33 is created. The
    # synthetic linkage keeps 31 missing registrations explicitly missing.
    # The actual DB0050 boundary must continue rejecting a superuser session.
    # This negative is a real SQL call, not a projected restricted-role DTO.
    with pytest.raises(DBAPIError) as denied:
        async with admin.begin() as connection:
            await settings(connection)
            await _append(
                connection,
                scope,
                documents[-1],
                member,
                revisions,
                lease,
                checkpoint=checkpoint,
            )
    assert denied.value.orig.sqlstate == "P0001"
    assert (
        denied.value.orig.__cause__.message == "account_history_partition_role_denied"
    )
    # A separate genuine LOGIN gets only the exact append capability. Its
    # portable password and connection are created by the existing fixture.
    # Revoke append before collector cleanup and before creating original33.
    signature = parent.FUNCTION_SIGNATURES["account_history_partition_v1_append"]
    async with collector(admin, grant=False) as (writer, _):
        role = writer.url.username
        assert re.fullmatch(r"ctcc_raw_frame_[a-f0-9]{32}", role)
        function = f"public.account_history_partition_v1_append({signature})"
        try:
            async with admin.begin() as connection:
                await connection.execute(
                    text(f'GRANT EXECUTE ON FUNCTION {function} TO "{role}"')
                )
            async with writer.begin() as connection:
                await settings(connection)
                actual = (
                    await connection.execute(
                        text("SELECT current_user,session_user,current_setting('role')")
                    )
                ).one()
                assert tuple(actual) == (role, role, "none")
                result = await _append(
                    connection,
                    scope,
                    documents[-1],
                    member,
                    revisions,
                    lease,
                    checkpoint=checkpoint,
                )
                assert result == records.digest(member)
        finally:
            async with admin.begin() as connection:
                await connection.execute(
                    text(f'REVOKE EXECUTE ON FUNCTION {function} FROM "{role}"')
                )
    factory = async_sessionmaker(admin, expire_on_commit=False, autoflush=False)
    index = AccountObservationIndexRepository(factory, clock=now)
    ledger_scope = LedgerScope(
        account_id=scope.account_id, settlement_currency=scope.settlement_currency
    )
    chain, window = await _new_all_product_original_before_storage(
        monkeypatch, admin, index, ledger_scope, declared_synthetic_wire=True
    )
    reference = observed.source_reference(chain)
    final = await index.append(ledger_scope, reference, window, expected_revision=32)
    documents = (*documents, final.document_json)
    async with admin.begin() as connection:
        current = (
            await connection.execute(
                text(
                    "SELECT c.control_revision AS revision,c.lease_until,"
                    "s.account_revision,s.ledger_revision "
                    "FROM public.demo_account_controls c "
                    "JOIN public.qualification_account_scopes s "
                    "ON s.environment=c.environment AND s.account_id=c.account_id "
                    "WHERE c.environment='demo' AND c.account_id=:uid "
                    "AND s.settlement_currency='USDT'"
                ),
                {"uid": scope.account_id},
            )
        ).one()
    return (
        scope,
        documents,
        (current.revision, current.account_revision, current.ledger_revision),
        current.lease_until,
        records.digest(checkpoint),
    )


# TEST ONLY: complete fixed literal codes from the pinned public source files.
# Never return exception text, repr, arbitrary type names, source rows or values.
_DIAGNOSTIC_PARENT_CODES = frozenset(
    {
        "account_history_partition_acl_denied",
        "account_history_partition_catalog_duplicate",
        "account_history_partition_catalog_shape",
        "account_history_partition_column_drift",
        "account_history_partition_constraint_drift",
        "account_history_partition_foreign_key_drift",
        "account_history_partition_function_drift",
        "account_history_partition_index_drift",
        "account_history_partition_native_baseline_drift",
        "account_history_partition_native_baseline_pending",
        "account_history_partition_oid_type",
        "account_history_partition_owner_drift",
        "account_history_partition_parent_drift",
        "account_history_partition_pg17_required",
        "account_history_partition_role_denied",
        "account_history_partition_table_drift",
        "account_history_partition_trigger_drift",
        "account_history_partition_trigger_identity_drift",
        "original_frame_acl_drift",
        "original_frame_approved_collector_invalid",
        "original_frame_catalog_shape",
        "original_frame_function_drift",
        "original_frame_installer_role_denied",
        "original_frame_native_baseline_pending",
        "original_frame_runtime_role_denied",
        "original_frame_source_catalog_duplicate",
        "original_frame_source_catalog_shape",
        "original_frame_source_deparser_drift",
        "original_frame_source_identity_drift",
        "original_frame_source_native_baseline_pending",
        "original_frame_source_schema_drift",
    }
)

_DIAGNOSTIC_WIRE_CODES = frozenset(
    {
        "original_frame_b3_precedes_original_source",
        "original_frame_byte_accounting_changed",
        "original_frame_chain_count_or_gap",
        "original_frame_checkpoint_changed",
        "original_frame_current_source_changed",
        "original_frame_decoded_byte_count_changed",
        "original_frame_exact_observation_required",
        "original_frame_exact_request_required",
        "original_frame_exact_utc_required",
        "original_frame_final_head_changed",
        "original_frame_hex_bound_or_type",
        "original_frame_hex_noncanonical",
        "original_frame_native_registration_changed",
        "original_frame_original_b3_changed",
        "original_frame_original_chain_changed",
        "original_frame_original_chain_reference_changed",
        "original_frame_original_lease_expired",
        "original_frame_original_witnesses_changed",
        "original_frame_raw_budget",
        "original_frame_recorded_time_future",
        "original_frame_recorded_time_invalid",
        "original_frame_reference_shape",
        "original_frame_request_cursor",
        "original_frame_request_or_authority_invalid",
        "original_frame_request_pin_changed",
        "original_frame_scope_changed",
        "original_frame_source_count_bound",
        "original_frame_wire_budget",
        "original_frame_wire_invalid",
        "original_frame_wire_shape",
        "original_frame_witness_order",
    }
)

_DIAGNOSTIC_STREAM_CODES = frozenset(
    {
        "stream_active_partition_byte_bound",
        "stream_checkpoint_head_changed",
        "stream_checkpoint_outside_original_boundary",
        "stream_compact_index_byte_bound",
        "stream_cross_partition_native_identity_conflict",
        "stream_duplicate_original_capture",
        "stream_exact_frame_required",
        "stream_exact_page_required",
        "stream_exact_witness_required",
        "stream_extra_source_after_pinned_head",
        "stream_final_receipt_byte_bound",
        "stream_frame_byte_bound",
        "stream_incomplete_or_changed_pinned_head",
        "stream_native_registration_changed",
        "stream_original_blocker_bound_or_shape",
        "stream_original_bytes_required",
        "stream_original_chain_required",
        "stream_original_checkpoint_members_changed",
        "stream_original_cumulative_counts_changed",
        "stream_original_findings_or_blockers_removed",
        "stream_original_genesis_or_chain_gap",
        "stream_original_membership_changed",
        "stream_original_partition_checkpoint_missing_or_changed",
        "stream_original_readback_required",
        "stream_original_receipt_binding_invalid",
        "stream_original_replay_failed",
        "stream_original_row_query_replay_mismatch",
        "stream_original_source_reference_changed",
        "stream_original_witness_binding_invalid",
        "stream_original_witness_order_invalid",
        "stream_original_witness_pin_changed",
        "stream_original_witness_source_changed",
        "stream_page_byte_bound",
        "stream_page_gap_or_reordered",
        "stream_policy_mismatch",
        "stream_retained_blocker_byte_bound",
        "stream_unexpected_checkpoint_pin",
    }
)

_DIAGNOSTIC_NATIVE_CODES = frozenset(
    {
        "native_account_algo_history_contract_invalid",
        "native_account_clock_domain_changed",
        "native_account_clock_inventory_invalid",
        "native_account_clock_profile_invalid",
        "native_account_current_sources_incomplete_or_exposed",
        "native_account_exact_clock_stamp_required",
        "native_account_exact_source_chain_required",
        "native_account_exchange_plan_invalid",
        "native_account_exchange_probe_invalid",
        "native_account_exchange_time_reversed",
        "native_account_exposure_scope_unsupported",
        "native_account_exposure_source_missing",
        "native_account_finalization_clock_invalid",
        "native_account_finalization_inventory_invalid",
        "native_account_host_clock_rejected",
        "native_account_initial_barrier_not_crossed",
        "native_account_initial_stage_invalid",
        "native_account_original_expiry_invalid",
        "native_account_original_index_invalid",
        "native_account_original_phase_missing",
        "native_account_original_tls_hostname_invalid",
        "native_account_original_tls_required",
        "native_account_original_utc_changed",
        "native_account_phase_binding_invalid",
        "native_account_phase_contract_invalid",
        "native_account_phase_inventory_invalid",
        "native_account_proof_authority_forbidden",
        "native_account_proof_contract_invalid",
        "native_account_proof_noncanonical",
        "native_account_proof_pin_mismatch",
        "native_account_proof_replay_invalid",
        "native_account_source_binding_invalid",
        "native_account_source_scope_unsupported",
        "native_account_time_trace_binding_invalid",
        "native_account_time_trace_chunk_invalid",
        "native_account_time_trace_chunk_missing",
        "native_account_time_trace_inventory_invalid",
        "native_account_time_trace_phase_join_invalid",
        "native_account_v7_chronology_incomplete",
        "native_account_v7_diagnostic_scope_unsupported",
        "native_account_v7_flat_current_use_revoked",
        "native_account_v7_inventory_incomplete",
    }
)

_DIAGNOSTIC_PG_CODES = frozenset(
    {
        "original_pg_backend_changed",
        "original_pg_catalog_changed",
        "original_pg_cleanup_uncertain",
        "original_pg_clock_or_task_changed",
        "original_pg_factory_invalid",
        "original_pg_incomplete",
        "original_pg_original_expiry",
        "original_pg_read_uncertain",
        "original_pg_request_causality",
        "original_pg_request_invalid",
        "original_pg_session_invalid",
    }
)

_DIAGNOSTIC_HISTORY_CODES = frozenset(
    {
        "history_query_algo_inventory_incomplete",
        "history_query_body_incomplete",
        "history_query_capture_binding_mismatch",
        "history_query_capture_incomplete",
        "history_query_chain_bound",
        "history_query_chain_invalid",
        "history_query_checkpoint_mismatch",
        "history_query_clock_invalid",
        "history_query_clock_or_prefix_reversed",
        "history_query_comparison_clock_reversed",
        "history_query_comparison_invalid",
        "history_query_comparison_policy_mismatch",
        "history_query_comparison_scope_mismatch",
        "history_query_comparison_time_domain_mismatch",
        "history_query_finalization_incomplete",
        "history_query_finalization_order_invalid",
        "history_query_generation_time_missing",
        "history_query_head_mismatch",
        "history_query_headers_mismatch",
        "history_query_lookback_unsupported",
        "history_query_original_packet_required",
        "history_query_overlap_conflict",
        "history_query_page_incomplete",
        "history_query_page_join_mismatch",
        "history_query_policy_mismatch",
        "history_query_prefix_invalid",
        "history_query_raw_join_mismatch",
        "history_query_raw_missing",
        "history_query_readback_required",
        "history_query_request_mismatch",
        "history_query_scope_unsupported",
        "history_query_source_clock_join_mismatch",
        "history_query_stage_order_invalid",
        "history_query_validated_join_mismatch",
        "history_query_verification_invalid",
    }
)

_DIAGNOSTIC_JOURNAL_CODES = frozenset(
    {
        "journal_acquisition_incomplete",
        "journal_buffer_limit",
        "journal_bytes_invalid",
        "journal_chunk_invalid",
        "journal_closed_or_bound",
        "journal_conflicting_source_rows",
        "journal_digest_invalid",
        "journal_event_bound",
        "journal_event_invalid",
        "journal_original_owned_packet_required",
        "journal_owned_capture_mismatch",
        "journal_owned_capture_required",
        "journal_owned_packet_required",
        "journal_readback_invalid",
        "journal_receipt_clock_order",
        "journal_request_bound",
        "journal_secret_scan_incomplete",
        "journal_source_clock_order",
        "journal_tls_hostname_invalid",
        "journal_token_binding_invalid",
        "owned_journal_event_required",
        "owned_journal_repository_required",
        "owned_journal_required",
    }
)

_DIAGNOSTIC_CAPTURE_CODES = frozenset(
    {
        "account_capture_cannot_grant_authority",
        "account_capture_invalid",
        "account_identity_mismatch",
        "account_mode_changed",
        "account_mode_unsupported",
        "algo_history_state_mismatch",
        "algo_query_scope_mismatch",
        "algo_state_invalid",
        "batch_deadline_exceeded",
        "canonical_bytes_limit",
        "clock_invalid",
        "conflicting_algo_type_identity",
        "conflicting_history_product_identity",
        "cross_request_clock_reversed",
        "cursor_invalid",
        "cursor_not_supported",
        "duplicate_json_key",
        "duplicate_or_conflicting_page_identity",
        "duplicate_source_identity",
        "empty_terminal_page_required",
        "external_packet_pin_invalid",
        "external_packet_pin_mismatch",
        "external_plan_pin_invalid",
        "external_plan_pin_mismatch",
        "fill_identifier_context_invalid",
        "filled_quantity_exceeds_order",
        "future_source_timestamp",
        "history_instrument_scope_mismatch",
        "history_overlap_conflict",
        "history_row_outside_query",
        "history_window_requires_milliseconds",
        "identity_chain_mismatch",
        "identity_receipt_required",
        "inventory_bytes_limit",
        "inventory_order_invalid",
        "inventory_page_count_invalid",
        "inventory_rows_limit",
        "inventory_stream_missing_or_limit",
        "json_array_limit",
        "json_key_limit",
        "json_number_invalid",
        "json_object_limit",
        "json_scalar_invalid",
        "json_text_limit",
        "json_traversal_limit",
        "json_unicode_invalid",
        "leverage_instrument_scope_mismatch",
        "margin_mode_invalid",
        "nonpaginated_inventory_duplicate",
        "nonpaginated_page_invalid",
        "observation_replay_mismatch",
        "order_side_invalid",
        "order_state_invalid",
        "packet_bytes_invalid",
        "packet_bytes_limit",
        "packet_noncanonical",
        "packet_replay_mismatch",
        "packet_schema_invalid",
        "page_after_terminal",
        "page_chain_metadata_invalid",
        "page_chain_mismatch",
        "page_index_gap",
        "page_index_invalid",
        "plan_history_window_invalid",
        "plan_inventory_budget_invalid",
        "plan_leverage_scope_invalid",
        "position_side_invalid",
        "receipt_pin_invalid",
        "record_bytes_limit",
        "record_decimal_invalid",
        "record_fields_invalid",
        "record_integer_limit",
        "record_invalid",
        "record_scalar_invalid",
        "record_sequence_limit",
        "record_text_limit",
        "record_traversal_limit",
        "record_type_invalid",
        "registration_origin_mismatch",
        "request_clock_invalid",
        "request_deadline_exceeded",
        "response_bytes_invalid",
        "response_cardinality_invalid",
        "response_envelope_fields_invalid",
        "response_envelope_invalid",
        "response_gateway_time_invalid",
        "response_json_invalid",
        "response_row_limit",
        "secret_field_forbidden",
        "source_bill_time_order_invalid",
        "source_cursor_not_exclusive",
        "source_cursor_order_invalid",
        "source_decimal_invalid",
        "source_identifier_invalid",
        "source_identity_field_invalid",
        "source_instrument_code_invalid",
        "source_instrument_type_invalid",
        "source_inventory_invalid",
        "source_lifecycle_reversed",
        "source_position_order_invalid",
        "source_quantity_negative",
        "source_quantity_unit_invalid",
        "source_row_invalid",
        "source_timestamp_invalid",
        "stream_invalid",
    }
)

_DIAGNOSTIC_OBSERVATION_CODES = frozenset(
    {
        "observation_capture_bound",
        "observation_capture_identity_invalid",
        "observation_cashflow_operand_invalid",
        "observation_cashflow_operand_missing",
        "observation_duplicate_or_invalid_capture",
        "observation_exact_reference_required",
        "observation_exact_window_required",
        "observation_original_packet_required",
        "observation_pin_invalid",
        "observation_policy_mismatch",
        "observation_receipt_byte_bound",
        "observation_row_bound",
        "observation_source_byte_bound",
        "observation_source_replay_invalid",
        "observation_source_row_mismatch",
        "observation_source_session_or_pin_mismatch",
        "observation_typed_domain_invalid",
        "observation_typed_query_required",
        "observation_typed_source_replay_invalid",
        "observation_window_invalid",
        "observation_window_milliseconds_required",
    }
)


def _closed_original_source_leaf(error):
    """Closed leaf diagnostics for NEW synthetic construction before B1 storage."""
    for exact_type, label, allowed in (
        (parent.AccountHistoryPartitionError, "PARENT", _DIAGNOSTIC_PARENT_CODES),
        (wire.OriginalFrameWireError, "WIRE", _DIAGNOSTIC_WIRE_CODES),
        (
            original_stream.OriginalHistoryStreamError,
            "STREAM",
            _DIAGNOSTIC_STREAM_CODES,
        ),
        (original_proof.NativeAccountProofError, "NATIVE", _DIAGNOSTIC_NATIVE_CODES),
        (history.HistoryQueryVerificationError, "HISTORY", _DIAGNOSTIC_HISTORY_CODES),
        (journal.AccountJournalError, "JOURNAL", _DIAGNOSTIC_JOURNAL_CODES),
        (capture.AccountCaptureError, "CAPTURE", _DIAGNOSTIC_CAPTURE_CODES),
        (
            observed.AccountObservationError,
            "OBSERVATION",
            _DIAGNOSTIC_OBSERVATION_CODES,
        ),
    ):
        if type(error) is exact_type:
            arguments = error.args
            if (
                type(arguments) is tuple
                and len(arguments) == 1
                and type(arguments[0]) is str
                and arguments[0] in allowed
            ):
                return "CTCC_SOURCE_CONSTRUCTION_" + label + "_" + arguments[0]
            break
    return "CTCC_SOURCE_CONSTRUCTION_UNCLASSIFIED"


_DIAGNOSTIC_SOURCE_LOCATORS = (
    ("PAGE_MEMO", "app/trade_qualification/account_original_page_memo_v1.py", 332),
    ("STREAM", "app/trade_qualification/account_history_original_stream_v1.py", 1073),
    ("WIRE", "app/database/repositories/account_history_original_frame_v1.py", 489),
    (
        "PG_NATIVE",
        "app/database/repositories/account_history_original_native_read_v1.py",
        374,
    ),
    ("NATIVE_PROOF", "app/trade_qualification/account_native_proof.py", 1145),
    ("OBSERVED", "app/trade_qualification/account_observation_index.py", 786),
    ("LIFECYCLE", "app/trade_qualification/account_lifecycle_history.py", 865),
    ("PARTITION", "app/domain/account_history_partition_v1.py", 723),
    ("B3_MEMBERSHIP", "app/database/repositories/account_observation_index.py", 873),
    ("CAPTURE", "app/trade_qualification/account_capture.py", 2003),
    ("JOURNAL", "app/trade_qualification/account_capture_journal.py", 821),
    (
        "HISTORY_QUERY",
        "app/trade_qualification/account_history_query_verifier.py",
        1178,
    ),
    ("PARTITION_CATALOG", "app/domain/account_history_partition_catalog_v1.py", 988),
    ("FRAME_CATALOG", "app/domain/account_history_original_frame_v1.py", 618),
    ("SOURCE_CATALOG", "app/domain/account_history_original_source_catalog_v1.py", 696),
    ("RESERVATIONS", "app/trade_qualification/reservations.py", 855),
)


def _closed_builtin_family(error):
    """No dynamic class names or args; only these static builtin/family labels."""
    kinds = (
        (UnboundLocalError, "UNBOUND_LOCAL_ERROR"),
        (NameError, "NAME_ERROR"),
        (AttributeError, "ATTRIBUTE_ERROR"),
        (KeyError, "KEY_ERROR"),
        (IndexError, "INDEX_ERROR"),
        (TypeError, "TYPE_ERROR"),
        (ValueError, "VALUE_ERROR"),
        (AssertionError, "ASSERTION_ERROR"),
        (TimeoutError, "TIMEOUT_ERROR"),
        (RecursionError, "RECURSION_ERROR"),
        (MemoryError, "MEMORY_ERROR"),
        (OverflowError, "OVERFLOW_ERROR"),
        (ZeroDivisionError, "ZERO_DIVISION_ERROR"),
        (NotImplementedError, "NOT_IMPLEMENTED_ERROR"),
        (RuntimeError, "RUNTIME_ERROR"),
        (OSError, "OS_ERROR"),
    )
    for exact, label in kinds:
        if type(error) is exact:
            return "BUILTIN_" + label
    for base, label in kinds:
        if issubclass(type(error), base):
            return label + "_FAMILY"
    return "OTHER_EXCEPTION_FAMILY"


def _closed_source_locators(errors):
    """Last <=6 whitelisted frames; no locals, source text, args or paths.

    All sixteen source bytes/pins are in the Root stage before/after inventory.
    This function only compares lexical exact filenames and returns fixed tags.
    It issues no custody, source-owner or execution authority.
    """
    source_root = Path(__file__).parents[2]
    allowed = {
        str(source_root.joinpath(*relative.split("/"))): (tag, last_line)
        for tag, relative, last_line in _DIAGNOSTIC_SOURCE_LOCATORS
    }
    tail, scanned, overflow = [], 0, False
    # Tracebacks and contexts arrive outer -> inner. Retain the last six so
    # wrappers cannot consume the allowance before the actual raise location.
    for error in errors:
        trace = error.__traceback__
        while trace is not None:
            if scanned >= 64:
                overflow = True
                break
            scanned += 1
            source = allowed.get(trace.tb_frame.f_code.co_filename)
            line = trace.tb_lineno
            if source is not None and type(line) is int and 1 <= line <= source[1]:
                tail.append((source[0], line))
                if len(tail) > 6:
                    tail.pop(0)
            trace = trace.tb_next
        if overflow:
            break
    status = "SCAN_OVERFLOW" if overflow else "TAIL" if tail else "NONE"
    # Each returned line is a traceback integer from an exact pinned public
    # filename. No filename, function name or value is returned or formatted.
    return (
        "LOCATORS_"
        + status
        + "".join("_" + tag + ":" + str(line) for tag, line in tail)
    )


def _closed_original_pg_context(error):
    """Project only reviewed fixed codes; unknown classes/args remain closed."""
    if type(error) is not native.OriginalHistoryPgReadError:
        return "CTCC_ORIGINAL_REPLAY_CONTEXT_UNCLASSIFIED"
    arguments = error.args
    if (
        type(arguments) is not tuple
        or len(arguments) != 1
        or type(arguments[0]) is not str
        or arguments[0] not in _DIAGNOSTIC_PG_CODES
    ):
        return "CTCC_ORIGINAL_REPLAY_CONTEXT_UNCLASSIFIED"
    parts = ["PG_" + arguments[0]]
    errors, seen = [error], {id(error)}
    context = error.__context__
    for _ in range(3):
        if context is None or id(context) in seen:
            break
        seen.add(id(context))
        errors.append(context)
        if type(context) is parent.AccountHistoryPartitionError:
            label, allowed = "PARENT", _DIAGNOSTIC_PARENT_CODES
        elif type(context) is wire.OriginalFrameWireError:
            label, allowed = "WIRE", _DIAGNOSTIC_WIRE_CODES
        elif type(context) is original_stream.OriginalHistoryStreamError:
            label, allowed = "STREAM", _DIAGNOSTIC_STREAM_CODES
        elif type(context) is original_proof.NativeAccountProofError:
            label, allowed = "NATIVE", _DIAGNOSTIC_NATIVE_CODES
        elif type(context) is history.HistoryQueryVerificationError:
            label, allowed = "HISTORY", _DIAGNOSTIC_HISTORY_CODES
        elif type(context) is journal.AccountJournalError:
            label, allowed = "JOURNAL", _DIAGNOSTIC_JOURNAL_CODES
        elif type(context) is capture.AccountCaptureError:
            label, allowed = "CAPTURE", _DIAGNOSTIC_CAPTURE_CODES
        elif type(context) is observed.AccountObservationError:
            label, allowed = "OBSERVATION", _DIAGNOSTIC_OBSERVATION_CODES
        else:
            parts.append(_closed_builtin_family(context))
            context = context.__context__
            continue
        arguments = context.args
        if (
            type(arguments) is not tuple
            or len(arguments) != 1
            or type(arguments[0]) is not str
            or arguments[0] not in allowed
        ):
            parts.append(_closed_builtin_family(context))
            context = context.__context__
            continue
        parts.append(label + "_" + arguments[0])
        context = context.__context__
    return (
        "CTCC_ORIGINAL_REPLAY_CONTEXT_"
        + "__".join(parts)
        + "__"
        + _closed_source_locators(errors)
    )


async def test_synthetic_wire_pg_original33_replays_original32_checkpoint_then33_and_remains_inert(
    monkeypatch, original_frame_database, record_property
):
    admin = original_frame_database
    (
        scope,
        documents,
        revisions,
        lease,
        checkpoint,
    ) = await _prefix33_with_original_checkpoint(monkeypatch, admin)
    timing = _ClosedOriginalTiming()
    grant_started = time.monotonic_ns()
    async with collector(admin, grant=True) as (reader, oid):
        timing.setup_grant_ns = time.monotonic_ns() - grant_started
        # This is the FIRST request construction, after role setup. Neither the
        # Control lease nor an already constructed request expiry is renewed.
        request = _request(scope, documents, revisions, lease)
        timing.initial_request_remaining_us = int(
            (request.expires_at - datetime.now(UTC)).total_seconds() * 1_000_000
        )
        diagnostic = None
        with monkeypatch.context() as measured:
            originals = timing.install(measured)
            pipeline_started = time.monotonic_ns()
            try:
                result = await native.replay_original_history_from_pg_v1(
                    async_sessionmaker(reader, expire_on_commit=False, autoflush=False),
                    genesis_request=request,
                    expected_checkpoint_sha256=checkpoint,
                    approved_collector_role_oid=oid,
                )
            except native.OriginalHistoryPgReadError as error:
                diagnostic = _closed_original_pg_context(error)
            finally:
                timing.pipeline_ns = time.monotonic_ns() - pipeline_started
                record_property("ctcc_closed_phase_timing_v1", timing.serialized())
        timing.assert_restored(originals)
        # Leave except before raising, so original private context is not chained.
        if diagnostic is not None:
            pytest.fail(diagnostic, pytrace=False)
        await _assert_reader_gone(admin, reader)
        receipt = json.loads(result.replay.receipt_json)
        assert result.observed_frames == receipt["through_sequence"] == 33
        assert receipt["page_count"] == 33
        assert receipt["original_b3_head_sha256"] == request.head
        assert receipt["checkpoint_sha256"] == checkpoint
        assert receipt["original_partition_count"] == 1
        assert receipt["active_partition_member_count"] == 1
        assert receipt["full_original_genesis_sources_replayed"] is True
        assert receipt["native_registration_missing_count"] == 32
        assert {
            "native_acquisition_not_owned",
            "original_native_registration_missing",
            "economic_stream_reducer_unimplemented",
            "history_tail_finality_unknown",
        } <= set(receipt["blocking_reasons"])
        for key in (
            "native_history_prefix_verified",
            "account_complete",
            "account_revision_published",
            "execution_authority",
        ):
            assert getattr(result, key) is False
            assert receipt[key] is False
        assert receipt["admission"] == "DENY"
        assert (
            result.first_request_started_at
            <= result.last_rpc_received_at
            <= result.session_closed_at
            < request.expires_at
        )
        # Real login role has ONLY the new explicitly granted RPC capability.
        # The native producer does not acquire any direct-table permission.
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
                            "SELECT has_table_privilege(current_user,:table,"
                            "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')"
                            " OR has_any_column_privilege(current_user,:table,"
                            "'SELECT,INSERT,UPDATE,REFERENCES')"
                        ),
                        {"table": "public." + table},
                    )
                    is False
                )


def _contains_exact_native_source_refusal(error, expected_code):
    if expected_code not in {
        "native_account_source_scope_unsupported",
        "native_account_original_tls_required",
    }:
        return False
    current = error
    for _ in range(4):
        if type(current) is original_proof.NativeAccountProofError:
            arguments = current.args
            return (
                type(arguments) is tuple
                and len(arguments) == 1
                and type(arguments[0]) is str
                and arguments[0] == expected_code
            )
        if type(current) not in {
            native.OriginalHistoryPgReadError,
            original_stream.OriginalHistoryStreamError,
            wire.OriginalFrameWireError,
        }:
            return False
        current = current.__context__
    return False


async def _assert_actual_default_source_refusal(
    admin, scope, documents, revisions, lease, expected_code
):
    request = _request(scope, documents, revisions, lease)
    async with collector(admin, grant=True) as (reader, oid):
        with pytest.raises(native.OriginalHistoryPgReadError) as caught:
            await native.replay_original_history_from_pg_v1(
                async_sessionmaker(reader, expire_on_commit=False, autoflush=False),
                genesis_request=request,
                expected_checkpoint_sha256=None,
                approved_collector_role_oid=oid,
            )
        assert _contains_exact_native_source_refusal(caught.value, expected_code)
        await _assert_reader_gone(admin, reader)


async def test_pg_default_regional_original_source_cannot_pass_native_read_bridge(
    monkeypatch, original_frame_database
):
    # Actual stored legacy Regional capture remains unmodified and unsupported.
    admin = original_frame_database
    scope, documents, revisions, lease = await _sources(monkeypatch, admin)
    await _assert_actual_default_source_refusal(
        admin,
        scope,
        documents,
        revisions,
        lease,
        "native_account_source_scope_unsupported",
    )


async def test_pg_unannotated_all_product_original_source_cannot_pass_native_read_bridge(
    monkeypatch, original_frame_database
):
    # A true new AllProduct plan alone cannot turn synthetic transport into TLS.
    admin = original_frame_database
    scope, documents, revisions, lease = await _new_all_product_sources(
        monkeypatch, admin, count=1, declared_synthetic_wire=False
    )
    await _assert_actual_default_source_refusal(
        admin,
        scope,
        documents,
        revisions,
        lease,
        "native_account_original_tls_required",
    )


@pytest.mark.parametrize("change", ["cursor", "head", "control", "expired"])
async def test_pg_original_prefix_wrong_pin_denies_and_closes(
    monkeypatch, original_frame_database, change
):
    admin = original_frame_database
    scope, documents, revisions, lease = await _sources(monkeypatch, admin, count=2)
    request = _request(scope, documents, revisions, lease)
    expected = "original_pg_read_uncertain"
    if change == "cursor":
        request = replace(request, after=1, previous=records.digest(documents[0]))
        expected = "original_pg_request_invalid"
    elif change == "head":
        request = replace(request, head="0" * 64)
    elif change == "control":
        request = replace(request, control=request.control + 1)
    else:
        request = replace(request, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        expected = "original_pg_original_expiry"
    async with collector(admin, grant=True) as (reader, oid):
        with pytest.raises(
            native.OriginalHistoryPgReadError, match="^" + expected + "$"
        ):
            await native.replay_original_history_from_pg_v1(
                async_sessionmaker(reader, expire_on_commit=False, autoflush=False),
                genesis_request=request,
                expected_checkpoint_sha256=None,
                approved_collector_role_oid=oid,
            )
        await _assert_reader_gone(admin, reader)


async def test_pg_original_prefix_cancelled_real_uid_wait_has_no_receipt_or_session(
    monkeypatch, original_frame_database
):
    admin = original_frame_database
    scope, documents, revisions, lease = await _sources(monkeypatch, admin)
    request = _request(scope, documents, revisions, lease)
    uid_sql = parent.UID_SQL.replace("p_environment", "'demo'").replace(
        "p_account_id", ":uid"
    )
    async with collector(admin, grant=True) as (reader, oid):
        async with admin.connect() as holder, holder.begin():
            await holder.execute(
                text("SELECT pg_advisory_xact_lock(" + uid_sql + ")"),
                {"uid": scope.account_id},
            )
            task = asyncio.create_task(
                native.replay_original_history_from_pg_v1(
                    async_sessionmaker(reader, expire_on_commit=False, autoflush=False),
                    genesis_request=request,
                    expected_checkpoint_sha256=None,
                    approved_collector_role_oid=oid,
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
                await _assert_reader_gone(admin, reader)
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        # Original transaction lock is released, not retained by an abandoned
        # producer task/session. No second source RPC or submit is attempted.
        async with admin.connect() as probe, probe.begin():
            assert (
                await probe.scalar(
                    text("SELECT pg_try_advisory_xact_lock(" + uid_sql + ")"),
                    {"uid": scope.account_id},
                )
                is True
            )
