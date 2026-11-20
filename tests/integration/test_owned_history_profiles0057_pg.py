"""Actual isolated successor locks/read/append; native handoff is synthetic."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.native_role_witness_0056_v1 import (
    observe_restricted_native_roles_0056_witness_v1,
)
from app.database.repositories import (
    account_history_partition_current56_source_v1 as source,
)
from app.database.repositories import (
    account_history_partition_current56_v2 as partition,
)
from app.database.repositories import qualification_native_checkpoint_v2 as checkpoint
from app.database.repositories.demo_control import (
    ControlObservation,
    DemoControlRepository,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)
from app.domain import native_database_profile_v1 as profiles
from app.trade_qualification import demo_control as control
from app.trade_qualification import native_controlled_account_source_v6 as native
from app.trade_qualification.reservations import LedgerScope
from tests.controlled_writer_coverage_fixtures import _store_control_fixture
from tests.integration import test_g12_restricted_writer_0057_pg as lower
from tests.integration.test_account_history_partition_repository import _member
from tests.integration.test_account_observation_index_repository import stored
from tests.integration.test_gate3_fitted_candidate_custody_pg import _delta56_identifier

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]
# Register the same immutable disposable TLS fixture under a local name.
_owned57_lab = lower._isolated_0057_g12_lab


async def _selected(engines):
    factories = tuple(
        async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        for engine in engines
    )
    selected = ConfiguredNativeDemoFactoriesV2(*factories, *engines[1:])
    roles = await observe_restricted_native_roles_0056_witness_v1(
        selected, source=ROOT, profile=profiles.DB0057
    )
    return selected, roles


async def test_actual0057_held_checkpoint_and_source_partition_keep_actual_head_and_locks(
    _owned57_lab,
):
    owner, engines, names = _owned57_lab
    selected, roles = await _selected(engines)
    owner_factory = async_sessionmaker(owner, autoflush=False, expire_on_commit=False)
    scope = LedgerScope(
        environment="demo", account_id="730061", settlement_currency="USDT"
    )
    control_scope = control.ControlScope(scope.environment, scope.account_id)
    stopped = await DemoControlRepository(
        owner_factory, clock=lambda: datetime.now(UTC)
    ).latch_stop(control_scope, command_id="a" * 64)
    ledger = QualificationLedgerRepository(
        owner_factory, clock=lambda: datetime.now(UTC)
    )
    await ledger.initialize_capture_scope(scope)
    async with (
        selected.writer_factory() as session,
        session.begin(),
        checkpoint.held_qualification_checkpoint_0056_v1(
            selected, session, control_scope, roles=roles
        ) as held,
    ):
        assert held.profile is profiles.DB0057
        assert (
            held.control.database_head
            == held.control.writer_catalog.database_head
            == "0057"
        )
        assert held.control.control.state == stopped.state
        local = QualificationLedgerRepository(
            selected.writer_factory, clock=lambda: datetime.now(UTC)
        )
        row = await local._locked(session, scope)
        observed = await local._bootstrap_checkpoint(
            session, scope, row, _held0056=held
        )
        assert observed.state.account_revision == observed.state.ledger_revision == 0
        xid = await session.scalar(text("SELECT pg_catalog.pg_current_xact_id()::text"))
        async with owner.begin() as competing:
            with pytest.raises(DBAPIError) as denied:
                await competing.exec_driver_sql(
                    "LOCK TABLE public.demo_collector_issued_account_revisions IN ACCESS EXCLUSIVE MODE NOWAIT"
                )
            assert denied.value.orig.sqlstate == "55P03"
    reader = source.Db0056SourcePartitionReaderV1(
        selected, clock=lambda: datetime.now(UTC), expected_roles=roles
    )
    partition_scope = source.HistoryPartitionScopeV1("demo", scope.account_id, "USDT")
    actual = await reader.read(partition_scope)
    assert (
        actual.transaction_id != xid
        and actual.account_complete is actual.execution_authority is False
    )
    signature = source.original.policy.FUNCTION_SIGNATURES[
        "account_history_partition_v1_append"
    ]
    role = _delta56_identifier(names[0])
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            f"GRANT EXECUTE ON FUNCTION public.account_history_partition_v1_append({signature}) TO {role}"
        )
    try:
        with pytest.raises(source.Db0056SourcePartitionError):
            await reader.read(partition_scope)
    finally:
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                f"REVOKE EXECUTE ON FUNCTION public.account_history_partition_v1_append({signature}) FROM {role}"
            )
    assert (await reader.read(partition_scope)).account_complete is False


async def test_actual0057_controlled_partition_append_reopens_under_original_arm(
    monkeypatch, _owned57_lab
):
    owner, engines, _ = _owned57_lab
    selected, roles = await _selected(engines)
    # Close fixture parser substitutions before invoking the original native
    # repository. The writer/catalog/Control/RPC/CAS/readback are all actual.
    with monkeypatch.context() as setup:
        _, ledger_scope, _, _, batch = await stored(
            setup, (owner, selected.writer_factory)
        )
    scope = partition.previous.records.HistoryPartitionScopeV1(
        "demo", ledger_scope.account_id, "USDT"
    )
    now = datetime.now(UTC)
    control_scope = control.ControlScope("demo", scope.account_id)
    acquired = control.acquire(
        None,
        scope=control_scope,
        owner_sha256="a" * 64,
        pins=control.ControlPins("1" * 64, "2" * 64, "3" * 64),
        now=now,
    )
    armed = control.advance(
        acquired,
        owner_sha256=acquired.owner_sha256,
        owner_epoch=acquired.owner_epoch,
        expected_revision=acquired.revision,
        action="arm_requested",
        command_id="6" * 64,
        now=now,
        arm_expires_at=now + timedelta(seconds=27),
    )
    async with owner.begin() as connection:
        previous = await _store_control_fixture(
            connection,
            acquired,
            command_id="5" * 64,
            action="acquire",
            previous_sha256=None,
        )
        event = await _store_control_fixture(
            connection,
            armed,
            command_id="6" * 64,
            action="arm_requested",
            previous_sha256=previous,
        )
    expected = ControlObservation(armed, event, now)
    expires = now + timedelta(seconds=25)
    parent = object()

    def replay(actual, repository, *, opening=False, append=False):
        assert (
            actual is parent
            and type(repository) is partition.Db0056ControlledPartitionRepositoryV2
        )
        return selected, roles, scope, expected, datetime.now(UTC), expires

    monkeypatch.setattr(native, "_replay_owned_partition_repository_v6", replay)
    repository = partition.Db0056ControlledPartitionRepositoryV2(parent)
    before = await repository.read(scope)
    assert (
        repository._profile is profiles.DB0057
        and before.head_sha256 == batch.event_sha256
    )
    record, linkage = _member(scope, batch.document_json)
    arguments = {
        "b3_document_raw": batch.document_json,
        "native_linkage_raw": linkage,
        "expected_b3_policy_sha256": json.loads(batch.document_json)["policy_sha256"],
        "expected_b3_event_sha256": batch.event_sha256,
        "expected_previous_checkpoint_sha256": None,
        "expected_control_revision": before.control_revision,
        "expected_account_revision": before.account_revision,
        "expected_ledger_revision": before.ledger_revision,
        "expires_at": expires,
    }
    appended = await repository.append_member_and_checkpoint(scope, **arguments)
    reopened = await repository.read(scope)
    assert len(reopened.members) == len(reopened.checkpoints) == 1
    assert reopened.members == appended.members
    assert reopened.members[0].record_json == record
    assert (
        len(
            {
                before.transaction_id,
                appended.transaction_id,
                reopened.transaction_id,
            }
        )
        == 3
    )
