"""Future sealed55 repository role regression on real SQL, synthetic source DATA.

The existing test transport commits original B1/B3 through their real repositories.
Its new synthetic linkage grants no native/account authority. No source/custody,
native seal, clock, validator or function is replaced. This positive test MUST
wait for a genuine current55 seal; a pending seal raises, never skips or passes.
"""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.repositories import (
    account_history_partition_current55_v2 as repository,
)
from app.domain import collector_current_0055_catalog_v1 as profile
from app.domain import controlled_history_current55_schema_v2 as schema
from app.trade_qualification.demo_control import (
    ControlPins,
    ControlScope,
    acquire,
    advance,
)
from tests.controlled_current55_fixtures import (
    controlled_current55_database as controlled_current55_database,  # noqa: PLC0414 -- pytest registers this exact fixture reexport.
)
from tests.controlled_writer_coverage_fixtures import _store_control_fixture
from tests.integration.test_account_history_original_frame_repository_v1 import (
    collector,
)
from tests.integration.test_account_history_partition_repository import _member
from tests.integration.test_account_observation_index_repository import stored

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@asynccontextmanager
async def _roles(admin):
    # Reuse the real restricted login fixture; every added privilege is removed
    # before that fixture drops the role. No DROP OWNED or data deletion.
    async with (
        collector(admin, grant=True) as (raw, _),
        collector(admin) as (writer, _),
    ):
        raw_role, writer_role = raw.url.username, writer.url.username
        table_grants = (
            (writer_role, "SELECT,INSERT", "demo_collector_current_revision_lineages"),
            (writer_role, "SELECT,INSERT", "demo_collector_issued_account_revisions"),
            (writer_role, "SELECT,INSERT,UPDATE", "demo_account_controls"),
            (writer_role, "SELECT,INSERT", "demo_control_journal"),
            (writer_role, "SELECT,INSERT", "demo_control_clear_acknowledgements_v2"),
            (writer_role, "SELECT,INSERT", "demo_policy_counter_initializations_v1"),
            (writer_role, "SELECT,INSERT", "qualification_submission_outcomes"),
            (writer_role, "SELECT,INSERT", "qualification_report_spool"),
            (writer_role, "SELECT,INSERT", "qualification_report_projection_receipts"),
            (writer_role, "SELECT", "qualification_reservations"),
            (
                writer_role,
                "SELECT,INSERT,UPDATE",
                "qualification_leverage_configuration_intents_v1",
            ),
            (
                writer_role,
                "SELECT,INSERT",
                "qualification_leverage_configuration_attempts_v1",
            ),
            (
                writer_role,
                "SELECT,INSERT",
                "qualification_leverage_configuration_results_v1",
            ),
            (writer_role, "SELECT", "alembic_version"),
            (raw_role, "SELECT", "alembic_version"),
        )
        function_grants = (
            (writer_role, schema.APPEND, schema.SIGNATURES[schema.APPEND]),
            (raw_role, schema.FRAME, schema.SIGNATURES[schema.FRAME]),
            (
                writer_role,
                "account_history_partition_v1_read",
                repository.policy.FUNCTION_SIGNATURES[
                    "account_history_partition_v1_read"
                ],
            ),
        )
        try:
            async with admin.begin() as connection:
                for role, rights, table in table_grants:
                    await connection.execute(
                        text(f'GRANT {rights} ON public.{table} TO "{role}"')
                    )
                for role, name, signature in function_grants:
                    await connection.execute(
                        text(
                            f'GRANT EXECUTE ON FUNCTION public.{name}({signature}) TO "{role}"'
                        )
                    )
            yield writer
        finally:
            async with admin.begin() as connection:
                for role, name, signature in reversed(function_grants):
                    await connection.execute(
                        text(
                            f'REVOKE EXECUTE ON FUNCTION public.{name}({signature}) FROM "{role}"'
                        )
                    )
                for role, rights, table in reversed(table_grants):
                    await connection.execute(
                        text(f'REVOKE {rights} ON public.{table} FROM "{role}"')
                    )


async def _armed_source(monkeypatch, admin):
    # Existing synthetic transport only; no baseline/native-owner monkeypatch.
    factory = async_sessionmaker(admin, expire_on_commit=False, autoflush=False)
    _, ledger_scope, _, _, first = await stored(monkeypatch, (admin, factory))
    scope = repository.records.HistoryPartitionScopeV1(
        "demo", ledger_scope.account_id, "USDT"
    )
    async with admin.begin() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM demo_account_controls WHERE environment='demo' AND account_id=:uid"
                ),
                {"uid": scope.account_id},
            )
            == 0
        )
        now = datetime.now(UTC)
        state = acquire(
            None,
            scope=ControlScope("demo", scope.account_id),
            owner_sha256="a" * 64,
            pins=ControlPins("1" * 64, "2" * 64, "3" * 64),
            now=now,
        )
        previous = await _store_control_fixture(
            connection,
            state,
            command_id=uuid4().hex * 2,
            action="acquire",
            previous_sha256=None,
        )
        command = uuid4().hex * 2
        state = advance(
            state,
            owner_sha256=state.owner_sha256,
            owner_epoch=state.owner_epoch,
            expected_revision=state.revision,
            action="arm_requested",
            command_id=command,
            now=datetime.now(UTC),
            arm_expires_at=now + timedelta(seconds=25),
        )
        await _store_control_fixture(
            connection,
            state,
            command_id=command,
            action="arm_requested",
            previous_sha256=previous,
        )
        account_revision, ledger_revision = (
            await connection.execute(
                text(
                    "SELECT account_revision,ledger_revision FROM qualification_account_scopes "
                    "WHERE environment='demo' AND account_id=:uid AND settlement_currency='USDT'"
                ),
                {"uid": scope.account_id},
            )
        ).one()
    return scope, first.document_json, state, account_revision, ledger_revision


async def test_sealed55_repository_reads_and_appends_with_old_append_revoked(
    monkeypatch,
    controlled_current55_database,
):
    # This must be an actual installed55 seal, not a test-supplied digest.
    profile._required_baseline()
    admin = controlled_current55_database
    scope, document, control, account_revision, ledger_revision = await _armed_source(
        monkeypatch, admin
    )
    member, linkage = _member(scope, document)
    async with _roles(admin) as writer:
        factory = async_sessionmaker(writer, expire_on_commit=False, autoflush=False)
        role = writer.url.username
        old_append = "account_history_partition_v1_append"
        signature = repository.policy.FUNCTION_SIGNATURES[old_append]
        # Exercise the actual revocation, then use the untouched formal path.
        async with admin.begin() as connection:
            await connection.execute(
                text(
                    f'GRANT EXECUTE ON FUNCTION public.{old_append}({signature}) TO "{role}"'
                )
            )
            await connection.execute(
                text(
                    f'REVOKE EXECUTE ON FUNCTION public.{old_append}({signature}) FROM "{role}"'
                )
            )
        repo = repository.AccountHistoryPartitionRepository(
            factory, clock=lambda: datetime.now(UTC)
        )
        await repo.verify_deployment()
        before = await repo.read(scope)
        assert before.head_sha256 == repository.records.digest(document)
        assert not before.members
        async with factory() as session, session.begin():
            await repo._settings(session)
            captured = await repository.read_account_history_partition_catalog(session)
            old = next(
                row for row in captured["functions"] if row["name"] == old_append
            )
            assert old["can_execute"] is False
            with pytest.raises(
                repository.policy.AccountHistoryPartitionError,
                match="account_history_partition_role_denied",
            ):
                repository.policy.verify_runtime_role(captured)
        after = await repo.append_member_and_checkpoint(
            scope,
            b3_document_raw=document,
            native_linkage_raw=linkage,
            expected_b3_policy_sha256=json.loads(document)["policy_sha256"],
            expected_b3_event_sha256=repository.records.digest(document),
            expected_previous_checkpoint_sha256=None,
            expected_control_revision=control.revision,
            expected_account_revision=account_revision,
            expected_ledger_revision=ledger_revision,
            expires_at=min(
                control.lease_until,
                control.arm_expires_at,
                datetime.now(UTC) + timedelta(seconds=15),
            ),
        )
        assert after.transaction_id != before.transaction_id
        assert after.head_sha256 == before.head_sha256
        assert after.control_revision == control.revision
        assert (
            after.account_revision == account_revision
            and after.ledger_revision == ledger_revision
        )
        assert len(after.members) == 1 and after.members[0].record_json == member
        assert after.account_complete is after.execution_authority is False
        readback = await repo.read(scope)
        assert readback.transaction_id != after.transaction_id
        assert len(readback.members) == 1 and readback.members[0].record_json == member
        assert readback.account_complete is readback.execution_authority is False
