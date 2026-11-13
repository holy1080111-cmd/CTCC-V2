"""Verify DB0056 selects historical reporting only inside the owning transaction."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.database.repositories import native_closed_reporting_0056_v1 as subject
from scripts import native_report_lineage_status_0056_v1 as cli
from scripts import project_native_terminal_report_0056_v1 as projector
from tests.unit.test_native_startup_control_observation_0056_v1 import (
    scenario,  # noqa: F401
)

SPOOL = "a" * 64
REPORT = "b" * 64
POLICY = "c" * 64
ENVELOPE = "d" * 64
DEFINITIONS = "e" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        None,
        "cancelled",
        "catalog",
        "grant",
        "query",
        "missing",
        "projection",
        "rollback",
    ],
)
async def test_report_observation_replays_once_and_always_rolls_back(
    scenario,  # noqa: F811
    monkeypatch,
    failure,
):
    selected = scenario.selected
    writer = scenario.writer
    role = "ctcc_writer"
    initial_t = datetime(2026, 6, 1, tzinfo=UTC)
    actor = (role, role, "none", 91, 92, initial_t, 170011, "127.0.0.1", 5432)
    session_data = [None] * 12
    session_data[5], session_data[6], session_data[8] = actor[3:6]
    spool = subject.QualificationReportSpool(
        spool_id=SPOOL,
        report_sha256=REPORT,
        policy_sha256=POLICY,
        queue_namespace="test",
        created_at=initial_t,
    )
    projection = subject.QualificationReportProjectionReceipt(
        spool_id=SPOOL,
        report_sha256=REPORT,
        policy_sha256=POLICY,
        envelope_sha256="f" * 64 if failure == "projection" else ENVELOPE,
        queue_namespace="wrong" if failure == "projection" else "test",
        verified_at=initial_t,
    )
    names = ("ctcc_writer", "ctcc_custody", "ctcc_frame")
    route = subject.locked._direct_tls_route(writer)
    witness = subject.witness56.RestrictedNativeRoles0056WitnessV1(
        subject.roles56.RestrictedNativeRoles0056ObservationV1(
            DEFINITIONS, "1" * 64, "2" * 64, "3" * 64
        ),
        *subject.witness56.runtime_witness_0056(actor, names, (route,) * 3),
    )
    witness_probe = AsyncMock(return_value=witness)
    monkeypatch.setattr(
        subject.witness56,
        "observe_restricted_native_roles_0056_witness_v1",
        witness_probe,
    )
    identities = AsyncMock(return_value=session_data)
    monkeypatch.setattr(subject.writer56, "_session_identity", identities)
    monkeypatch.setattr(
        subject.roles56, "_identity_at_0056", AsyncMock(return_value=actor)
    )
    catalog = SimpleNamespace(
        native_definitions_sha256="0" * 64 if failure == "catalog" else DEFINITIONS
    )
    monkeypatch.setattr(
        subject.writer56,
        "observe_restricted_readwrite_writer_0056_v1",
        AsyncMock(return_value=catalog),
    )
    monkeypatch.setattr(subject, "_exact_existing_acl", AsyncMock())
    monkeypatch.setattr(
        subject.roles56, "_require_no_effective_demo_fitted_execute", AsyncMock()
    )
    sql = []

    async def execute(session, statement):
        assert session.bind is writer and session.in_transaction()
        sql.append(str(statement))

    async def read(session, model, key):
        assert session.bind is writer and session.in_transaction() and key == SPOOL
        if model is subject.QualificationReportSpool:
            return None if failure == "missing" else spool
        assert model is subject.QualificationReportProjectionReceipt
        return projection

    monkeypatch.setattr(AsyncSession, "execute", execute)
    monkeypatch.setattr(AsyncSession, "get", read)
    monkeypatch.setattr(
        AsyncSession, "scalar", AsyncMock(return_value=failure != "grant")
    )
    valid = subject.historical.NativeTerminalReportDataV55(
        object(),
        "private-report-id",
        "fixture",
        "private-client-id",
        initial_t,
        initial_t,
    )
    replay = AsyncMock(return_value=(object(), valid, object()))
    if failure == "cancelled":
        replay.side_effect = asyncio.CancelledError()
    elif failure == "query":
        replay.side_effect = RuntimeError("private connection or trade contents")
    monkeypatch.setattr(subject.historical, "_replay_native_terminal_spool", replay)
    rollbacks = []
    original_rollback = AsyncSessionTransaction.rollback

    async def rollback(transaction):
        await original_rollback(transaction)
        rollbacks.append(transaction)
        if failure == "rollback":
            raise RuntimeError("private rollback failure")

    with (
        patch.object(AsyncSessionTransaction, "rollback", rollback),
        patch.object(AsyncSessionTransaction, "commit", AsyncMock()) as commit,
    ):
        if failure is None:
            observed = await subject.observe_terminal_lineage_0056_v1(
                selected, spool_id=SPOOL
            )
            assert observed.spool_sha256 == SPOOL
            assert observed.report_sha256 == REPORT and observed.policy_sha256 == POLICY
            assert observed.projection_receipt_envelope_sha256 == ENVELOPE
            assert (
                observed.server_incarnation_sha256 == witness.server_incarnation_sha256
            )
            assert observed.role_route_sha256 == witness.role_route_sha256
            assert observed.admission == "DENY"
            assert not observed.local_outbox_verified
            assert not observed.delivery_authority and not observed.execution_authority
            assert "private-" not in repr(observed)
            assert identities.await_count == 2
        else:
            expected = (
                asyncio.CancelledError
                if failure == "cancelled"
                else subject.NativeTerminalReport0056Error
            )
            with pytest.raises(expected) as denied:
                await subject.observe_terminal_lineage_0056_v1(selected, spool_id=SPOOL)
            assert "private" not in str(denied.value)
        commit.assert_not_awaited()
    assert len(rollbacks) == 1
    assert not rollbacks[0].session.in_transaction()
    witness_probe.assert_awaited_once()
    assert sql[0] == "SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE"
    assert sql[-1] == "SET LOCAL synchronous_commit = 'on'"
    if failure in {"catalog", "grant", "missing"}:
        replay.assert_not_awaited()
    else:
        replay.assert_awaited_once()


def test_missing_projection_receipt_remains_unknown():
    assert subject._read_projection_digest(None, None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "different_role",
        "locked",
        "insert_refused",
        "commit_ack_lost",
        "file_lost",
        "namespace",
    ],
)
async def test_local_projection_requires_commit_and_fresh_readback(
    scenario,  # noqa: F811
    monkeypatch,
    tmp_path,
    fault,
):
    now = subject.outbox.actual_utc()
    report = subject.historical.NativeTerminalReportDataV55(
        SimpleNamespace(
            instrument_id="BTC-USDT-SWAP",
            direction="long",
            original_source_sha256="1" * 64,
            original_evidence_sha256="2" * 64,
        ),
        "synthetic-report",
        "trend_pullback",
        "synthetic-order-reference",
        now - timedelta(seconds=2),
        now - timedelta(seconds=1),
    )
    spool = subject.QualificationReportSpool(
        spool_id=SPOOL,
        report_sha256=REPORT,
        policy_sha256=POLICY,
        eligible=True,
        queue_namespace="wrong" if fault == "namespace" else "test",
        created_at=now,
    )
    policy = subject.outbox.OutboxPolicy()
    roles = subject.NativeRoleIdentity0056V2(
        91,
        92,
        ("writer", "custody", "frame"),
        (1, 2, 3),
        "8" * 64,
        "7" * 64,
        "6" * 64,
        DEFINITIONS,
        "5" * 64,
    )
    witness = subject.witness56.RestrictedNativeRoles0056WitnessV1(
        subject.roles56.RestrictedNativeRoles0056ObservationV1(
            DEFINITIONS, "4" * 64, "3" * 64, "5" * 64
        ),
        "9" * 64 if fault == "different_role" else roles.server_incarnation_sha256,
        roles.role_route_sha256,
    )
    monkeypatch.setattr(
        subject.witness56,
        "observe_restricted_native_roles_0056_witness_v1",
        AsyncMock(return_value=witness),
    )
    settings = object()
    monkeypatch.setattr(subject, "get_settings", lambda: settings)
    monkeypatch.setattr(
        subject, "_configured_projection_queue", lambda _: (tmp_path, "test")
    )
    calls, receipts, staged = [], [], {}
    committed = None

    async def read(session, _engine, _roles, _names, _route, requested, *, lock=False):
        assert requested == SPOOL and session.in_transaction()
        calls.append((session, lock))
        if lock and fault == "locked":
            subject._need(False)
        if not lock:
            assert calls[-2][0] is not session
            assert not calls[-2][0].in_transaction()
            assert committed is not None
        return spool, report, policy, subject._read_projection_digest(committed, spool)

    def add(session, receipt):
        assert session not in staged
        staged[session] = receipt

    async def flush(session):
        receipt = staged.pop(session)
        assert type(receipt) is subject.QualificationReportProjectionReceipt
        assert receipt.spool_id == SPOOL and receipt.queue_namespace == "test"
        assert receipt.report_sha256 == REPORT and receipt.policy_sha256 == POLICY
        if fault == "insert_refused" and len(calls) == 1:
            raise RuntimeError("private DB trigger refusal")
        receipts.append(receipt)

    original_exit = AsyncSessionTransaction.__aexit__

    async def commit(transaction, *args):
        nonlocal committed
        await original_exit(transaction, *args)
        if args == (None, None, None) and receipts:
            committed = receipts[-1]
            if fault == "commit_ack_lost" and len(calls) == 1:
                raise RuntimeError("private SQL driver error after the commit")

    monkeypatch.setattr(subject, "_read_held", read)
    monkeypatch.setattr(AsyncSession, "add", add)
    monkeypatch.setattr(AsyncSession, "flush", flush)
    monkeypatch.setattr(AsyncSessionTransaction, "__aexit__", commit)
    args = {
        "spool_id": SPOOL,
        "queue_namespace": "test",
        "root": tmp_path,
        "expected_roles": roles,
    }
    project = subject._project_terminal_report_0056_v1
    file = tmp_path / (report.report_id + ".json")
    journal = tmp_path / (report.report_id + ".state") / "00000001.json"
    original_pair = None
    if fault is not None and fault != "file_lost":
        with pytest.raises(
            subject.NativeTerminalReport0056Error,
            match="^native_terminal_report_0056_unavailable$",
        ):
            await project(scenario.selected, **args)
        if fault in {"different_role", "locked", "namespace"}:
            assert not file.exists() and not journal.exists() and not receipts
            return
        assert len(calls) == 1 and len(receipts) == (fault == "commit_ack_lost")
        original_pair = file.read_bytes(), journal.read_bytes()

    first = await project(scenario.selected, **args)
    before = file.read_bytes(), journal.read_bytes()
    if original_pair is not None:
        assert before == original_pair
    if fault == "file_lost":
        file.unlink()
        with pytest.raises(subject.NativeTerminalReport0056Error):
            await project(scenario.selected, **args)
        assert not file.exists() and journal.read_bytes() == before[1]
    else:
        second = await project(scenario.selected, **args)
        assert first == second
        assert (file.read_bytes(), journal.read_bytes()) == before
        assert [lock for _, lock in calls[-2:]] == [True, False]
    assert len(receipts) == 1
    assert first.projection_receipt_envelope_sha256 == receipts[0].envelope_sha256
    assert first.local_outbox_verified is True
    assert first.delivery_authority is first.execution_authority is False
    assert first.admission == "DENY"


def test_local_projection_cli_does_not_claim_zero_db_writes_on_failure(
    monkeypatch, capsys
):
    failure = AsyncMock(side_effect=RuntimeError("private report or settings"))
    monkeypatch.setattr(
        projector, "project_configured_native_terminal_report_0056_v1", failure
    )
    assert (
        projector.main(
            [
                "--descriptor-file",
                "private-descriptor",
                "--source-archive-file",
                "private-source",
                "--spool-sha256",
                SPOOL,
            ]
        )
        == 1
    )
    result = capsys.readouterr().out
    assert '"status":"PROJECTION_UNCONFIRMED"' in result
    assert '"order_writes":0' in result and '"notion_writes":0' in result
    assert "private" not in result and "database_mutations" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "trust", "incarnation", "readback"])
async def test_operator_requires_same_installed_release_and_server(monkeypatch, fault):
    deployment = cli.startup.deployment
    descriptor = object()
    anchor = SimpleNamespace(trust_sha256="f" * 64)
    release = (anchor, descriptor)
    selected = object()
    with (
        patch.object(cli.startup, "_read_bound_release") as read_release,
        patch.object(deployment, "get_settings", return_value=object()),
        patch.object(deployment, "_check_runtime_settings"),
        patch.object(deployment, "_check_restricted_role_observation"),
        patch.object(
            deployment.selectors,
            "select_configured_native_demo_factories_v2",
            return_value=selected,
        ) as select,
        patch.object(
            deployment.selectors, "_dispose_failed_selection_factories_v2", AsyncMock()
        ) as dispose,
        patch.object(
            deployment.identity, "observe_restricted_role_identity_0056_v2", AsyncMock()
        ) as roles,
        patch.object(
            subject, "observe_terminal_lineage_0056_v1", AsyncMock()
        ) as verify,
    ):
        read_release.side_effect = (
            ValueError("private trust failure")
            if fault == "trust"
            else [release, (object(), descriptor) if fault == "readback" else release]
        )
        roles.return_value = SimpleNamespace(
            server_incarnation_sha256="1" * 64,
            role_route_sha256="2" * 64,
        )
        verify.return_value = subject.NativeTerminalLineage0056V1(
            SPOOL,
            REPORT,
            POLICY,
            ENVELOPE,
            "9" * 64 if fault == "incarnation" else "1" * 64,
            "2" * 64,
        )
        options = {
            "descriptor_file": Path("descriptor"),
            "source_archive_file": Path("source"),
            "spool_sha256": SPOOL,
        }
        if fault is not None:
            with pytest.raises(ValueError):
                await cli.observe_configured_report_lineage_0056_v1(**options)
        else:
            result = await cli.observe_configured_report_lineage_0056_v1(**options)
            assert result["report_sha256"] == REPORT
            assert result["deployment_admin_anchor_sha256"] == anchor.trust_sha256
            assert (
                result["delivery_authority"] is result["execution_authority"] is False
            )
        if fault == "trust":
            select.assert_not_called()
            dispose.assert_not_awaited()
            verify.assert_not_awaited()
        else:
            verify.assert_awaited_once_with(selected, spool_id=SPOOL)
            dispose.assert_awaited_once_with(selected)


def test_cli_refusal_contains_no_private_inputs_or_contents(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "observe_configured_report_lineage_0056_v1",
        AsyncMock(side_effect=RuntimeError("private database details")),
    )
    arguments = [
        "--descriptor-file",
        "private-descriptor",
        "--source-archive-file",
        "private-source",
        "--spool-sha256",
        SPOOL,
    ]
    assert cli.main(arguments) == 1
    assert cli.main([*arguments, "--trust-file", "private-bypass"]) == 1
    output = capsys.readouterr().out
    assert output.count('"status":"REFUSED"') == 2
    assert output.count('"notion_writes":0') == 2
    assert "private" not in output and SPOOL not in output
