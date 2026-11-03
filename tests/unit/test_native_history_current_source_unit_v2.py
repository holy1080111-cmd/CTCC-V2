"""Synthetic provider mechanics only; ZERO native/TLS/PG/account credit.

The original source owner is acquired through the real fixed entry. This file
does not inject production WeakKey owner registries or replace source verifiers.
The inherited fixture explicitly substitutes OS/TLS/custody/database providers.
"""

import asyncio
import copy
import json
import pickle
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain import account_history_partition_v1 as partition
from app.domain.source_primitives import PublicReceiptError, sha
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_current_bundle_v2 as bounded
from app.trade_qualification import account_history_source_bundle_v1 as original
from app.trade_qualification import account_history_source_v1 as source
from app.trade_qualification import native_account_risk_assembly_v1 as assembly
from tests.unit import test_account_history_partition_v1 as shape
from tests.unit.test_account_history_original_financial_stream_v2 import (
    SCOPE,
    acquired_wire,
    frames_for,
)
from tests.unit.test_account_history_original_financial_stream_v2 import (
    originals as original_financial_fixtures,
)
from tests.unit.test_account_history_source_v1 import acquisition_fixture


def _synthetic_v2_custody(monkeypatch, value):
    async def seal(repository, *, bundle_raw, expected_sha256, proof_envelope_sha256):
        assert value.saved["deployment"]
        facts = bounded.replay_current_history_source_bundle_v2(
            bundle_raw, expected_sha256=expected_sha256
        )
        assert facts.proof_sha256 == proof_envelope_sha256
        value.saved["raw"] = bundle_raw
        now = datetime.now(UTC)
        readback = custody.RetainedRuntimeBundleReadback(
            "b" * 32, expected_sha256, proof_envelope_sha256, now, now, bundle_raw
        )
        receipt = custody.OwnedRuntimeBundleCustodyReceipt(custody._ISSUER)
        custody._RECEIPTS[receipt] = (
            readback,
            custody._owner(),
            custody.time.monotonic_ns(),
        )
        return receipt

    monkeypatch.setattr(custody.RuntimeBundleCustodyRepository, "seal_bundle", seal)


@pytest.mark.asyncio
async def test_fresh_synthetic_current_entry_keeps_original_owner_and_native_proof(
    monkeypatch, tmp_path
):
    with acquisition_fixture(monkeypatch, tmp_path) as value:
        _synthetic_v2_custody(monkeypatch, value)
        owner = await source.acquire_native_history_current_source_v2(**value.args)
        assert type(owner) is source.OwnedNativeHistoryCurrentSourceV2
        # The original public V1 consumer must not adopt a V2 owner or DTO.
        with pytest.raises(source.NativeHistorySourceError, match="owner_unavailable"):
            source.consume_owned_native_history_source_facts_v1(owner)
        binding = source._take_financial_assembly_v2(owner, key=assembly._HOOK_KEY)
        try:
            state = source._financial_state_v1(binding, key=assembly._HOOK_KEY)
            facts = await source._replay_history_state_v1(state)
            assert type(facts) is bounded.CurrentHistorySourceFactsV2
            assert facts.frame.batch.sequence == 1
            assert facts.frame.chain == facts.sources[-1][0]
            assert json.loads(facts.bundle_raw)["schema_version"] == bounded.SCHEMA
            assert not facts.native_history_prefix_verified
            assert not facts.account_complete and not facts.execution_authority
            assert facts.account_revision is facts.funding_accrual_events is None
            assert len(facts.sources) <= source.observed.MAX_CAPTURES == 32
            with pytest.raises(original.HistorySourceBundleError):
                original.replay_history_source_bundle_v1(
                    facts.bundle_raw, expected_sha256=facts.bundle_sha256
                )
            with pytest.raises(
                source.NativeHistorySourceError, match="owner_unavailable"
            ):
                source._take_financial_assembly_v2(owner, key=assembly._HOOK_KEY)
        finally:
            source._release_financial_assembly_v1(binding, key=assembly._HOOK_KEY)


@pytest.mark.asyncio
async def test_original_current60_second_lifetime_cannot_be_reminted(
    monkeypatch, tmp_path
):
    with acquisition_fixture(monkeypatch, tmp_path) as value:
        _synthetic_v2_custody(monkeypatch, value)
        owner = await source.acquire_native_history_current_source_v2(**value.args)
        value.samples.calls += (
            60000  # Declared synthetic clock advances; no source timestamp changes.
        )
        with pytest.raises(
            (PublicReceiptError, source.NativeHistorySourceError)
        ) as rejected:
            source._take_financial_assembly_v2(owner, key=assembly._HOOK_KEY)
        assert (type(rejected.value), rejected.value.args) in (
            (PublicReceiptError, ("clock_lease_expired",)),
            (
                source.NativeHistorySourceError,
                ("native_history_current_original_expiry",),
            ),
        )
        with pytest.raises(source.NativeHistorySourceError, match="owner_unavailable"):
            source._take_financial_assembly_v2(owner, key=assembly._HOOK_KEY)


@pytest.mark.asyncio
async def test_suppressed_real_task_cancel_burns_current_owner(monkeypatch, tmp_path):
    with acquisition_fixture(monkeypatch, tmp_path) as value:
        _synthetic_v2_custody(monkeypatch, value)

        async def cancelled_owner():
            owner = await source.acquire_native_history_current_source_v2(**value.args)
            task = asyncio.current_task()
            task.cancel()
            try:
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    pass
                assert task.cancelling() == 1
                with pytest.raises(
                    source.NativeHistorySourceError, match="context_invalid"
                ):
                    source._take_financial_assembly_v2(owner, key=assembly._HOOK_KEY)
                with pytest.raises(
                    source.NativeHistorySourceError, match="owner_unavailable"
                ):
                    source._take_financial_assembly_v2(owner, key=assembly._HOOK_KEY)
            finally:
                task.uncancel()

        await asyncio.create_task(cancelled_owner())


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_current_owner_and_v4_owner_cannot_be_constructed_or_serialized(operation):
    for kind in (
        source.OwnedNativeHistoryCurrentSourceV2,
        assembly.OwnedNativeAccountHistoryFinancialV4,
    ):
        with pytest.raises(
            (source.NativeHistorySourceError, assembly.NativeAccountRiskAssemblyError)
        ):
            kind()
        # An unregistered shell remains powerless even when Python object.__new__
        # is available; this shell is not inserted into an issuer registry.
        shell = object.__new__(kind)
        with pytest.raises(
            (source.NativeHistorySourceError, assembly.NativeAccountRiskAssemblyError)
        ):
            operation(shell)
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
        ):
            assembly.consume_owned_native_account_history_financial_v4(shell)


@pytest.mark.asyncio
async def test_current_support33_keeps_true_sequence_and_every_original_witness(
    monkeypatch,
):
    sources, batches = await acquired_wire(monkeypatch, opens=31)
    assert len(sources) == len(batches) == 33
    support = sources[-2:]
    supporting_batches = batches[-2:]
    frame, reference, packet, _records, _joins = bounded._checked_sources(
        source.LedgerScope(account_id=SCOPE.account_id, settlement_currency="USDT"),
        support,
        supporting_batches,
    )
    assert frame.batch.sequence == 33
    assert frame.witnesses[0].sequence == 32
    assert reference == support[-1][1]
    assert packet.plan is not None
    assert frame.batch.document_json == batches[32].document_json
    assert (
        json.loads(frame.batch.document_json)["previous_sha256"]
        == batches[31].event_sha256
    )
    assert len(support) == 2 < source.observed.MAX_CAPTURES
    # Source32 is a required declared witness, never an optional cache page.
    with pytest.raises(
        bounded.stream.OriginalHistoryStreamError, match="witness_binding"
    ):
        bounded._checked_sources(
            source.LedgerScope(account_id=SCOPE.account_id, settlement_currency="USDT"),
            sources[-1:],
            batches[-1:],
        )
    # Keeping the original current head/receipt while swapping witness raw bytes
    # cannot be hidden by sorting, deduplication or a claimed passed flag.
    with pytest.raises(
        (bounded.CurrentHistoryBundleError, bounded.stream.OriginalHistoryStreamError)
    ):
        bounded._checked_sources(
            source.LedgerScope(account_id=SCOPE.account_id, settlement_currency="USDT"),
            (sources[0], sources[-1]),
            supporting_batches,
        )


@pytest.mark.asyncio
async def test_actual_current_entry33_uses_real_b3_derivation_over_synthetic_providers(
    monkeypatch, tmp_path
):
    """Native control flow and pure proof only; no actual PG/native/TLS credit.

    All earlier captures are freshly declared before the new acquisition. SQL
    storage providers are explicit in-memory substitutions. B1/query/native
    proof, B3 derivation and the new CURRENT-bundle verifier remain real.
    """
    # Declare the earlier captures and their own B3 window before their first
    # acquisition. No saved row, timestamp, plan or expiry is edited afterward.
    with monkeypatch.context() as earlier:
        earlier.setattr(
            original_financial_fixtures,
            "NOW",
            original_financial_fixtures.NOW - timedelta(hours=1),
        )
        prior_window = original_financial_fixtures.WINDOW
        earlier.setattr(
            original_financial_fixtures,
            "WINDOW",
            source.observed.ExecutionWindow(
                prior_window.started_at - timedelta(hours=1),
                prior_window.ended_at - timedelta(hours=1),
            ),
        )
        prior_sources, prior_batches = await acquired_wire(earlier, opens=30)
    assert len(prior_batches) == 32
    checkpoint = frames_for(prior_sources, prior_batches)[-1].checkpoint
    old_bytes = tuple(batch.document_json for batch in prior_batches)
    chain_by_id = {reference.capture_id: chain for chain, reference in prior_sources}
    with acquisition_fixture(monkeypatch, tmp_path) as value:
        _synthetic_v2_custody(monkeypatch, value)
        value.batches.extend(prior_batches)

        async def read_chain(repository, scope, capture_id):
            if capture_id in chain_by_id:
                return chain_by_id[capture_id]
            current = tuple(value.events)
            assert source.observed.source_reference(current).capture_id == capture_id
            return current

        async def stored_head(repository, scope, sequence=None):
            if not value.batches:
                return None
            batch = (
                value.batches[-1] if sequence is None else value.batches[sequence - 1]
            )
            data = json.loads(batch.document_json)
            return SimpleNamespace(
                sequence=batch.sequence,
                event_sha256=batch.event_sha256,
                document_json=batch.document_json.decode(),
                receipt_json=batch.replay.receipt_json.decode(),
                capture_id=data["reference"]["capture_id"],
                previous_sha256=data["previous_sha256"],
                environment=scope.environment,
                account_id=scope.account_id,
                settlement_currency=scope.settlement_currency,
            )

        async def selected_original_witness(repository, scope, upto, single, window):
            assert upto == 32
            anchor = json.loads(
                json.loads(prior_batches[-1].document_json)["anchor_json"]
            )
            return (
                [32],
                {
                    "facts": anchor["source_facts_through_sequence"],
                    "coverage": anchor["coverage_through_sequence"],
                    "findings": anchor["unresolved_findings_through_sequence"],
                },
                False,
            )

        async def original_membership(repository, scope, row, single):
            data = repository._checked_document(row, scope)
            facts, coverage = bounded.stream._membership(single)
            for label, rows in (("source", facts), ("coverage", coverage)):
                assert data[label + "_membership_count"] == len(rows)
                assert data[label + "_membership_sha256"] == journal.digest(
                    journal.canonical(rows)
                )

        async def append(repository, scope, reference, window, *, expected_revision):
            assert expected_revision == 32
            (
                raw,
                replay,
                _facts,
                _coverage,
                _findings,
                _chain,
            ) = await repository._derive(scope, reference, window, 33)
            batch = bounded.ObservationBatchReadback(33, sha(raw), raw, replay)
            value.batches.append(batch)
            return batch

        async def original_partition(repository, scope):
            assert len(value.batches) == 32  # Called before B3 append33, never after.
            return SimpleNamespace(
                head_sequence=32,
                head_sha256=prior_batches[-1].event_sha256,
                checkpoints=(
                    SimpleNamespace(partition_number=1, record_json=checkpoint),
                ),
            )

        monkeypatch.setattr(
            source.AccountCaptureJournalRepository, "read_chain", read_chain
        )
        monkeypatch.setattr(
            source.AccountObservationIndexRepository, "_batch", stored_head
        )
        monkeypatch.setattr(
            source.AccountObservationIndexRepository,
            "_witnesses",
            selected_original_witness,
        )
        monkeypatch.setattr(
            source.AccountObservationIndexRepository,
            "_stored_membership",
            original_membership,
        )
        monkeypatch.setattr(source.AccountObservationIndexRepository, "append", append)
        monkeypatch.setattr(
            source.AccountHistoryPartitionRepository, "read", original_partition
        )
        owner = await source.acquire_native_history_current_source_v2(**value.args)
        binding = source._take_financial_assembly_v2(owner, key=assembly._HOOK_KEY)
        try:
            state = source._financial_state_v1(binding, key=assembly._HOOK_KEY)
            facts = await source._replay_history_state_v1(state)
            assert facts.frame.batch.sequence == 33
            assert tuple(witness.sequence for witness in facts.frame.witnesses) == (32,)
            assert tuple(batch.sequence for batch in facts.batches) == (32, 33)
            assert (
                tuple(batch.document_json for batch in value.batches[:32]) == old_bytes
            )
            assert facts.batches[0].document_json == old_bytes[-1]
            assert (
                json.loads(facts.batches[-1].document_json)["previous_sha256"]
                == prior_batches[-1].event_sha256
            )
            assert not facts.native_history_prefix_verified
            assert not facts.account_complete and not facts.execution_authority
            assert source.observed.MAX_CAPTURES == 32
        finally:
            source._release_financial_assembly_v1(binding, key=assembly._HOOK_KEY)


@pytest.mark.asyncio
async def test_checkpoint32_cannot_be_backfilled_after_current33(monkeypatch):
    sources, batches = await acquired_wire(monkeypatch, opens=31)
    frames = frames_for(sources, batches)
    checkpoint = frames[31].checkpoint
    assert checkpoint is not None
    facts = SimpleNamespace(
        scope=source.LedgerScope(
            account_id=SCOPE.account_id, settlement_currency="USDT"
        ),
        batches=batches[-2:],
    )
    before = SimpleNamespace(
        members=(),
        checkpoints=(),
        b3_documents=tuple(batch.document_json for batch in batches),
    )
    registration = SimpleNamespace(record_json=b"unused_nonboundary")
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError, match="original_checkpoint_missing"
    ):
        assembly._partition_checkpoint_v4(
            facts, before, registration, source.observed.POLICY_SHA256
        )
    recorded = SimpleNamespace(
        record_json=checkpoint, record_sha256=sha(checkpoint), partition_number=1
    )
    accepted = SimpleNamespace(
        members=(), checkpoints=(recorded,), b3_documents=before.b3_documents
    )
    assert (
        assembly._partition_checkpoint_v4(
            facts, accepted, registration, source.observed.POLICY_SHA256
        )
        is None
    )
    assert accepted.checkpoints[0].record_json == checkpoint
    # Neither this helper nor a valid saved checkpoint is a real V4 owner.
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
    ):
        assembly.consume_owned_native_account_history_financial_v4(recorded)


def test_original_checkpoint64_extends32_without_changing_original_v3_budget():
    # Pure existing B3/linkage-shape vectors, explicitly not native source proof.
    documents = shape.original_documents(64)
    first = shape.checkpoint(documents[:32])
    registered = shape.registration(documents[-1])
    facts = SimpleNamespace(
        scope=source.LedgerScope(
            account_id=shape.SCOPE.account_id, settlement_currency="USDT"
        ),
        batches=(SimpleNamespace(sequence=64, event_sha256=sha(documents[-1])),),
    )
    before = SimpleNamespace(
        members=(),
        b3_documents=documents,
        checkpoints=(
            SimpleNamespace(
                record_json=first.record_json, record_sha256=first.record_sha256
            ),
        ),
    )
    registration = SimpleNamespace(record_json=registered)
    output = assembly._partition_checkpoint_v4(
        facts, before, registration, shape.SOURCE_POLICY
    )
    replay = partition.replay_partition_checkpoints_v1(
        scope=shape.SCOPE,
        checkpoints=(first.record_json, output),
        expected_checkpoint_sha256=sha(output),
        expected_b3_policy_sha256=shape.SOURCE_POLICY,
        expected_b3_head_sha256=sha(documents[-1]),
    )
    assert replay is not None
    value = json.loads(output)
    assert value["first_sequence"] == 33 and value["last_sequence"] == 64
    assert value["previous_checkpoint_sha256"] == first.record_sha256
    assert before.checkpoints[0].record_json == first.record_json
    # The old V3 source's full-prefix32 limitation is not silently increased.
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError, match="streaming_source_unavailable"
    ):
        assembly._partition_checkpoint_v3(
            facts, before, registration, shape.SOURCE_POLICY
        )


@pytest.mark.parametrize("value", [None, {}, {"passed": True}, b"saved_receipt"])
def test_saved_observation_or_caller_claim_never_enters_native_v4(value):
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
    ):
        assembly.consume_owned_native_account_history_financial_v4(value)


@pytest.mark.asyncio
async def test_database_route_uses_actual_creator_overrides_and_allows_only_role_difference():
    """Native factory construction only; no connection/SQL/credential read."""
    writer = create_async_engine(
        "postgresql+asyncpg://writer@127.0.0.1:25432/ctcc", poolclass=NullPool
    )
    reader = create_async_engine(
        "postgresql+asyncpg://reader@127.0.0.1:25432/ctcc", poolclass=NullPool
    )
    overridden = create_async_engine(
        "postgresql+asyncpg://reader@127.0.0.2:29999/other",
        poolclass=NullPool,
        connect_args={"host": "127.0.0.1", "port": 25432, "database": "ctcc"},
    )
    changed = create_async_engine(
        "postgresql+asyncpg://reader@127.0.0.1:25433/ctcc", poolclass=NullPool
    )
    try:
        factories = [
            async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
            for engine in (writer, reader, overridden, changed)
        ]
        pins = [assembly._database_connection_pin_v4(factory) for factory in factories]
        assert (
            pins[0][0] != pins[1][0]
        )  # Every complete credential-bearing pin is private and distinct.
        assert pins[0][1] == pins[1][1] == pins[2][1]
        assert pins[0][1] != pins[3][1]
        actual = custody._closure(reader.sync_engine.pool._creator)["cparams"]
        before = pins[1]
        assert type(actual) is custody.immutabledict
        with pytest.raises(TypeError):
            actual["host"] = "127.0.0.2"
        assert assembly._database_connection_pin_v4(factories[1]) == before
        creator = reader.sync_engine.pool._creator
        cells = dict(
            zip(creator.__code__.co_freevars, creator.__closure__, strict=True)
        )
        cell = cells["cparams"]
        assert cell.cell_contents is actual
        try:
            # Explicit synthetic closure mutation only. No engine connection,
            # DB/credential read or network operation is performed by this case.
            cell.cell_contents = dict(actual)
            converted = assembly._database_connection_pin_v4(factories[1])
            assert converted != before and converted[1] == before[1]
            assert converted[0][0] is dict and before[0][0] is custody.immutabledict
            changed_parameters = dict(actual)
            changed_parameters["host"] = "127.0.0.2"
            cell.cell_contents = changed_parameters
            changed_pin = assembly._database_connection_pin_v4(factories[1])
            assert changed_pin != before and changed_pin[1] != before[1]
        finally:
            cell.cell_contents = actual
        assert assembly._database_connection_pin_v4(factories[1]) == before
    finally:
        for engine in (writer, reader, overridden, changed):
            await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options", [{"host": ["127.0.0.1", "127.0.0.2"]}, {"dsn": "declared-unused"}]
)
async def test_database_route_rejects_multi_host_and_hidden_dsn_before_connection(
    options,
):
    engine = create_async_engine(
        "postgresql+asyncpg://reader@127.0.0.1:25432/ctcc",
        poolclass=NullPool,
        connect_args=options,
    )
    try:
        factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError,
            match="explicit_database_route_required",
        ):
            assembly._database_connection_pin_v4(factory)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_close_retains_one_task_through_repeated_caller_cancel():
    entered, release = asyncio.Event(), asyncio.Event()

    class CloseProbe:
        calls = 0
        closed = False

        async def close(self):
            self.calls += 1
            entered.set()
            await release.wait()
            self.closed = True

    probe = CloseProbe()
    before = set(asyncio.all_tasks())
    task = asyncio.create_task(assembly._close_database_session_v4(probe))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and probe.calls == 1 and not probe.closed
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and probe.calls == 1
    release.set()
    with pytest.raises(asyncio.CancelledError) as rejected:
        await task
    assert rejected.value.args == ()
    assert probe.calls == 1 and probe.closed
    assert not (set(asyncio.all_tasks()) - before)


@pytest.mark.asyncio
async def test_database_close_uncertainty_is_closed_once_and_never_retried():
    class CloseProbe:
        calls = 0

        async def close(self):
            self.calls += 1
            raise RuntimeError(
                "declared synthetic adapter error must not become receipt"
            )

    probe = CloseProbe()
    with pytest.raises(assembly.NativeAccountRiskAssemblyError) as rejected:
        await assembly._close_database_session_v4(probe)
    assert rejected.value.args == ("native_financial_v4_database_cleanup_uncertain",)
    assert probe.calls == 1
