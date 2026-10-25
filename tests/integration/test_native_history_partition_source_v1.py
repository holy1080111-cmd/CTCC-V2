"""Real isolated PG17 mechanisms, never native exchange-source acceptance.

These tests use real restricted login roles, original B1/B3 rows, transactions,
concurrency and independent readback. The linkage is labelled synthetic by its
fixture. No baseline/role/catalog/SQL readback is patched; no direct SQL fallback
can turn a pending native gate into successful V3 or trading authority.
"""

import asyncio
import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.repositories import account_history_partition as repository
from app.domain import account_history_partition_catalog_v1 as policy
from app.trade_qualification import native_account_risk_assembly_v1 as assembly
from tests.integration.test_account_history_partition_repository import (
    _member,
    _now,
    _restricted_role,
    _sources,
    partition_child,  # noqa: F401 - pytest fixture dependency.
    partition_primary,  # noqa: F401 - pytest fixture dependency.
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_formal_partition_native_baseline_gate_cannot_mint_from_rows(
    monkeypatch,
    partition_child,  # noqa: F811 -- explicit imported pytest fixture injection.
):
    scope, documents, _fences, _expiry = await _sources(monkeypatch, partition_child)
    async with _restricted_role(partition_child) as restricted:
        factory = async_sessionmaker(
            restricted, expire_on_commit=False, autoflush=False
        )
        reader = repository.AccountHistoryPartitionRepository(factory, clock=_now)
        if policy.PG17_DEPARSED_BASELINE is None:
            with pytest.raises(policy.AccountHistoryPartitionError, match="native"):
                await reader.verify_deployment()
            with pytest.raises(policy.AccountHistoryPartitionError, match="native"):
                await reader.read(scope)
        else:
            await reader.verify_deployment()
            observed = await reader.read(scope)
            assert observed.b3_documents == documents
            assert observed.members == observed.checkpoints == ()
            assert not observed.execution_authority and not observed.account_complete
            with pytest.raises(
                assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
            ):
                assembly.consume_owned_native_account_financial_operands_v3(observed)
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
    ):
        assembly.consume_owned_native_account_financial_operands_v3(
            {"b3_documents": documents, "passed": True}
        )


async def test_real_partition_unique_latest_append_and_independent_readback_cannot_remint(
    monkeypatch,
    partition_child,  # noqa: F811 -- explicit imported pytest fixture injection.
):
    if policy.PG17_DEPARSED_BASELINE is None:
        pytest.skip(
            "actual native PG17 catalog seal pending; positive formal append is NOT accepted"
        )
    scope, documents, fences, expiry = await _sources(monkeypatch, partition_child)
    _registration, linkage = _member(scope, documents[-1])
    async with _restricted_role(partition_child) as restricted:
        factory = async_sessionmaker(
            restricted, expire_on_commit=False, autoflush=False
        )
        first = repository.AccountHistoryPartitionRepository(factory, clock=_now)
        second = repository.AccountHistoryPartitionRepository(factory, clock=_now)
        baseline = await first.read(scope)
        assert not baseline.members
        kwargs = {
            "b3_document_raw": documents[-1],
            "native_linkage_raw": linkage,
            "expected_b3_policy_sha256": json.loads(documents[-1])["policy_sha256"],
            "expected_b3_event_sha256": baseline.head_sha256,
            "expected_previous_checkpoint_sha256": None,
            "expected_control_revision": fences[0],
            "expected_account_revision": fences[1],
            "expected_ledger_revision": fences[2],
            "expires_at": expiry,
        }
        results = await asyncio.gather(
            first.append_member_and_checkpoint(scope, **kwargs),
            second.append_member_and_checkpoint(scope, **kwargs),
            return_exceptions=True,
        )
        successes = [
            value
            for value in results
            if type(value) is repository.HistoryPartitionReadbackV1
        ]
        assert len(successes) == 1
        assert all(
            type(value) is repository.HistoryPartitionReadbackV1
            or isinstance(value, policy.AccountHistoryPartitionError)
            for value in results
        )
        after = await repository.AccountHistoryPartitionRepository(
            factory, clock=_now
        ).read(scope)
        assert len(after.members) == 1
        assert after.members == successes[0].members
        assert after.transaction_id != successes[0].transaction_id
        assert after.head_sha256 == baseline.head_sha256
        assert after.b3_documents == documents
        assert (
            after.control_revision,
            after.account_revision,
            after.ledger_revision,
        ) == fences
        assert first._poisoned is True and second._poisoned is True
        assert not after.account_complete and not after.execution_authority
        for value in (successes[0], after, after.members[0]):
            with pytest.raises(
                assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
            ):
                assembly.consume_owned_native_account_financial_operands_v3(value)


async def test_real_partition_wrong_original_control_revision_denies_without_registration(
    monkeypatch,
    partition_child,  # noqa: F811 -- explicit imported pytest fixture injection.
):
    if policy.PG17_DEPARSED_BASELINE is None:
        pytest.skip(
            "actual native PG17 catalog seal pending; positive formal fence mechanism NOT accepted"
        )
    scope, documents, fences, expiry = await _sources(monkeypatch, partition_child)
    _registration, linkage = _member(scope, documents[-1])
    async with _restricted_role(partition_child) as restricted:
        factory = async_sessionmaker(
            restricted, expire_on_commit=False, autoflush=False
        )
        writer = repository.AccountHistoryPartitionRepository(factory, clock=_now)
        before = await writer.read(scope)
        with pytest.raises(policy.AccountHistoryPartitionError, match="fence"):
            await writer.append_member_and_checkpoint(
                scope,
                b3_document_raw=documents[-1],
                native_linkage_raw=linkage,
                expected_b3_policy_sha256=json.loads(documents[-1])["policy_sha256"],
                expected_b3_event_sha256=before.head_sha256,
                expected_previous_checkpoint_sha256=None,
                expected_control_revision=fences[0] + 1,
                expected_account_revision=fences[1],
                expected_ledger_revision=fences[2],
                expires_at=expiry,
            )
        after = await repository.AccountHistoryPartitionRepository(
            factory, clock=_now
        ).read(scope)
        assert after.members == before.members == ()
        assert after.b3_documents == before.b3_documents
        assert writer._poisoned is True


def _final_fence_role_with_original_B1_read(admin, restricted):
    """Match original native main factory's B1 SELECT, never owner/SET ROLE."""
    role = restricted.url.username
    import re
    from contextlib import asynccontextmanager

    from sqlalchemy import text

    assert re.fullmatch(r"ctcc_hp50_[a-f0-9]{32}", role)

    @asynccontextmanager
    async def binding():
        async with admin.begin() as connection:
            await connection.execute(
                text(f'GRANT SELECT ON public.demo_account_capture_events TO "{role}"')
            )
        try:
            yield
        finally:
            async with admin.begin() as connection:
                await connection.execute(
                    text(
                        f'REVOKE SELECT ON public.demo_account_capture_events FROM "{role}"'
                    )
                )

    return binding()


async def test_real_final_main_fence_observes_legal_B3_append_after_original_read(
    monkeypatch,
    partition_child,  # noqa: F811 -- explicit imported pytest fixture injection.
):
    """Real B3 repository growth; not native coverage/acquisition acceptance."""
    if policy.PG17_DEPARSED_BASELINE is None:
        pytest.skip(
            "actual native PG17 catalog seal pending; final main fence mechanism NOT accepted"
        )
    from app.database.repositories.account_observation_index import (
        AccountObservationIndexRepository,
    )
    from app.trade_qualification.reservations import LedgerScope
    from tests.integration.test_account_observation_index_repository import (
        additional_capture,
        now,
    )

    scope, documents, fences, _expiry = await _sources(monkeypatch, partition_child)
    admin_factory = async_sessionmaker(
        partition_child, expire_on_commit=False, autoflush=False
    )
    index = AccountObservationIndexRepository(admin_factory, clock=now)
    ledger_scope = LedgerScope(
        environment=scope.environment,
        account_id=scope.account_id,
        settlement_currency=scope.settlement_currency,
    )
    reference, window = await additional_capture(monkeypatch, index, ledger_scope, [])
    async with (
        _restricted_role(partition_child) as restricted,
        _final_fence_role_with_original_B1_read(partition_child, restricted),
    ):
        factory = async_sessionmaker(
            restricted, expire_on_commit=False, autoflush=False
        )
        reader = repository.AccountHistoryPartitionRepository(factory, clock=_now)
        before = await reader.read(scope)

        async def last_coverage_await_with_legitimate_history_writer():
            # This is an explicit PG scheduling provider, not a native source.
            return await index.append(
                ledger_scope, reference, window, expected_revision=1
            )

        appended = await last_coverage_await_with_legitimate_history_writer()
        capture_ids = tuple(
            json.loads(raw)["reference"]["capture_id"] for raw in documents
        )
        final = await reader.read_final_source_fence(scope, capture_ids=capture_ids)
        assert (
            before.head_sequence == 1
            and appended.sequence == final.partition.head_sequence == 2
        )
        assert (
            final.partition.head_sha256 == appended.event_sha256 != before.head_sha256
        )
        assert final.partition.b3_documents == (*documents, appended.document_json)
        assert (
            final.partition.control_revision,
            final.partition.account_revision,
            final.partition.ledger_revision,
        ) == fences
        assert final.b1_chains[0][0] == capture_ids[0] and final.b1_chains[0][1]
        assert final.execution_authority is final.account_complete is False


async def test_real_final_main_fence_keeps_exact_UID_lock_through_original_B1_and_B3_reads(
    monkeypatch,
    partition_child,  # noqa: F811 -- explicit imported pytest fixture injection.
):
    """Real shared-key contention and snapshot semantics through session close."""
    if policy.PG17_DEPARSED_BASELINE is None:
        pytest.skip(
            "actual native PG17 catalog seal pending; final main lock mechanism NOT accepted"
        )
    from sqlalchemy import text

    from app.database.repositories.account_capture_journal import (
        AccountCaptureJournalRepository,
    )
    from app.database.repositories.account_observation_index import (
        AccountObservationIndexRepository,
    )
    from app.trade_qualification.reservations import LedgerScope
    from tests.integration.test_account_observation_index_repository import (
        additional_capture,
        now,
    )

    scope, documents, fences, _expiry = await _sources(monkeypatch, partition_child)
    admin_factory = async_sessionmaker(
        partition_child, expire_on_commit=False, autoflush=False
    )
    index = AccountObservationIndexRepository(admin_factory, clock=now)
    ledger_scope = LedgerScope(
        environment=scope.environment,
        account_id=scope.account_id,
        settlement_currency=scope.settlement_currency,
    )
    reference, window = await additional_capture(monkeypatch, index, ledger_scope, [])
    capture_ids = tuple(json.loads(raw)["reference"]["capture_id"] for raw in documents)
    entered, release = asyncio.Event(), asyncio.Event()
    owner_pid = []
    original = AccountCaptureJournalRepository._read_chain_locked
    async with (
        _restricted_role(partition_child) as restricted,
        _final_fence_role_with_original_B1_read(partition_child, restricted),
    ):
        factory = async_sessionmaker(
            restricted, expire_on_commit=False, autoflush=False
        )

        async def paused_original_B1(self, session, selected_scope, capture_id):
            result = await original(self, session, selected_scope, capture_id)
            if self.session_factory is factory:
                owner_pid.append(await session.scalar(text("SELECT pg_backend_pid()")))
                entered.set()
                await release.wait()
            return result

        monkeypatch.setattr(
            AccountCaptureJournalRepository, "_read_chain_locked", paused_original_B1
        )
        reader = repository.AccountHistoryPartitionRepository(factory, clock=_now)
        fenced = asyncio.create_task(
            reader.read_final_source_fence(scope, capture_ids=capture_ids)
        )
        writer = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            writer = asyncio.create_task(
                index.append(ledger_scope, reference, window, expected_revision=1)
            )

            async def observe_actual_advisory_wait():
                while True:
                    async with partition_child.connect() as connection:
                        waiting = await connection.scalar(
                            text(
                                "SELECT count(*) FROM pg_catalog.pg_stat_activity "
                                "WHERE datname=current_database() AND wait_event='advisory' "
                                "AND :owner_pid=ANY(pg_catalog.pg_blocking_pids(pid))"
                            ),
                            {"owner_pid": owner_pid[0]},
                        )
                    if waiting:
                        return waiting
                    if writer.done():
                        raise AssertionError(
                            "legitimate B3 writer did not wait on held final UID lock"
                        )
                    await asyncio.sleep(0.01)

            assert (
                await asyncio.wait_for(observe_actual_advisory_wait(), timeout=3) >= 1
            )
            release.set()
            observed = await asyncio.wait_for(fenced, timeout=5)
            appended = await asyncio.wait_for(writer, timeout=5)
            assert (
                observed.partition.head_sequence == 1
                and observed.partition.b3_documents == documents
            )
            assert appended.sequence == 2
            assert (
                observed.partition.control_revision,
                observed.partition.account_revision,
                observed.partition.ledger_revision,
            ) == fences
            assert observed.execution_authority is observed.account_complete is False
            # A real later read sees growth after release. The original DTO is
            # an immutable transaction observation, never freshness renewal.
            current = await reader.read(scope)
            assert (
                current.head_sequence == 2
                and current.head_sha256 != observed.partition.head_sha256
            )
        finally:
            release.set()
            for task in (fenced, writer):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (fenced, writer) if task is not None),
                return_exceptions=True,
            )
