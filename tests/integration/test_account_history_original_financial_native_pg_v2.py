"""HELD real-PG synthetic-wire mechanics, no native account/TLS authority credit.

Reuse the current original0052 pooled fresh-database/real-login fixture. Every
new test must run on its own actual stage; existing43 results cannot be reused.
"""

import asyncio
import json
import os
from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.repositories import (
    account_history_original_financial_read_v2 as native,
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

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_pg_financial_synthetic_wire_original33_uses_same_expiry_and_measured_finish(
    monkeypatch,
    original_frame_database,
    request,
):
    admin = original_frame_database
    (
        scope,
        documents,
        revisions,
        lease,
        checkpoint,
    ) = await original._prefix33_with_original_checkpoint(monkeypatch, admin)
    async with original.collector(admin, grant=True) as (reader, oid):
        # FIRST construction follows real role setup, exactly as the existing
        # native acceptance. No existing request expiry or Control lease resets.
        genesis = original._request(scope, documents, revisions, lease)
        try:
            result = await native.replay_original_history_financial_from_pg_v2(
                async_sessionmaker(reader, expire_on_commit=False, autoflush=False),
                genesis_request=genesis,
                expected_checkpoint_sha256=checkpoint,
                approved_collector_role_oid=oid,
                expected_financial_policy_sha256=stream.original_history_financial_stream_policy_sha256_v2(),
            )
        except native.OriginalHistoryFinancialPgReadError as error:
            if os.environ.get("CTCC_ORIGINAL_FINANCIAL_DIAGNOSTIC") != "1":
                raise
            code = (
                error.args[0]
                if len(error.args) == 1
                and type(error.args[0]) is str
                and error.args[0] in native.ERROR_CODES
                else "original_pg_read_uncertain"
            )
            request.node.user_properties.append(
                ("ctcc_pg52_original_financial_closed_code_v1", code)
            )
            pytest.fail(
                "ctcc_pg52_original_financial_closed_code_v1=" + code, pytrace=False
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
        assert acquisition["original_expires_at"] == genesis.expires_at.isoformat()
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
            < genesis.expires_at
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
            await native.replay_original_history_financial_from_pg_v2(
                async_sessionmaker(reader, expire_on_commit=False, autoflush=False),
                genesis_request=request,
                expected_checkpoint_sha256=None,
                approved_collector_role_oid=oid,
                expected_financial_policy_sha256=stream.original_history_financial_stream_policy_sha256_v2(),
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
            await native.replay_original_history_financial_from_pg_v2(
                async_sessionmaker(reader, expire_on_commit=False, autoflush=False),
                genesis_request=request,
                expected_checkpoint_sha256=None,
                approved_collector_role_oid=oid,
                expected_financial_policy_sha256=stream.original_history_financial_stream_policy_sha256_v2(),
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
                native.replay_original_history_financial_from_pg_v2(
                    async_sessionmaker(reader, expire_on_commit=False, autoflush=False),
                    genesis_request=request,
                    expected_checkpoint_sha256=None,
                    approved_collector_role_oid=oid,
                    expected_financial_policy_sha256=stream.original_history_financial_stream_policy_sha256_v2(),
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
