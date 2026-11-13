"""Test exact committed-cleanup selection and transaction boundaries."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.database.repositories import native_terminal_report_resume_0056_v1 as subject
from app.domain.source_primitives import canonical, decode, sha
from scripts import resume_native_terminal_report_0056_v1 as cli
from tests.unit.test_native_startup_control_observation_0056_v1 import (
    scenario,  # noqa: F401
)

NOW = datetime(2026, 11, 13, tzinfo=UTC)
RESERVATION, CLOSURE, FORENSICS, CLEANUP, INTENT = tuple(
    value * 64 for value in "abcde"
)


@pytest.fixture
def tail():
    reservation = subject.QualificationReservation(
        reservation_id=RESERVATION,
        request_sha256="f" * 64,
        updated_at=NOW,
    )
    transition = subject.QualificationReservationTransition(
        reservation_id=RESERVATION,
        state_revision=4,
        from_state="uncertain",
        to_state="reconciled_flat",
        reason_code=subject.TRANSITION_REASON,
        occurred_at=NOW,
        evidence_json=canonical({"outcome_id": CLOSURE}).decode(),
    )
    refs = {
        "terminal_data_outcome_id": "1" * 64,
        "close_intent_outcome_id": "2" * 64,
        "execution_authority": False,
        "order_retry_authority": False,
    }
    report = canonical({"kind": "synthetic"})
    entries = (
        (subject._CLOSURE, CLOSURE, {"assessment_sha256": "3" * 64}),
        (
            subject._FORENSICS,
            FORENSICS,
            {
                "closure_outcome_id": CLOSURE,
                "forensics_json": report.decode(),
                "forensics_sha256": sha(report),
            },
        ),
        (
            subject._CLEANUP,
            CLEANUP,
            {
                "closure_outcome_id": CLOSURE,
                "closure_receipt_sha256": "3" * 64,
                "reservation_id": RESERVATION,
                "request_sha256": reservation.request_sha256,
                "intent_sha256": INTENT,
            },
        ),
    )
    rows = []
    for index, (version, digest, fields) in enumerate(entries):
        capture = canonical({"version": version, **refs, **fields})
        rows.append(
            subject.QualificationSubmissionOutcome(
                outcome_id=digest,
                sequence=index + 5,
                intent_sha256=INTENT,
                intent_transition_id=13,
                exchange_request_sha256="4" * 64,
                observation_kind="reconciliation",
                status="acknowledged",
                capture_json=capture.decode(),
                capture_sha256=sha(capture),
                recorded_at=NOW + timedelta(minutes=index),
                ledger_revision=index + 13,
            )
        )
    return rows, reservation, transition, report


def test_report_ready_derives_original_version_and_policy_from_exact_rows(tail):
    rows, reservation, transition, report = tail
    cleanup, raw, policy_json, capture = subject._pending_report(
        rows, reservation, transition, CLEANUP
    )
    assert cleanup is rows[-1] and raw == report
    assert decode(capture.encode()) == {
        "version": "ctcc.native_closed_trade_report_ready.v1",
        "reservation_id": RESERVATION,
        "request_sha256": reservation.request_sha256,
        "intent_sha256": INTENT,
        "closure_outcome_id": CLOSURE,
        "cleanup_outcome_id": CLEANUP,
        "forensics_outcome_id": FORENSICS,
        "report_sha256": sha(report),
        "policy_sha256": sha(policy_json.encode()),
        "report_policy_version": subject.policy55.REPORT_POLICY_VERSION,
        "report_policy_sha256": subject.policy55.REPORT_POLICY_SHA256,
        "execution_authority": False,
        "order_retry_authority": False,
    }


@pytest.mark.parametrize(
    "fault",
    [
        "wrong",
        "missing",
        "duplicate",
        "digest",
        "active",
        "different_intent",
        "non_fill",
    ],
)
def test_report_resume_refuses_unproven_history(tail, fault):
    rows, reservation, transition, _ = tail
    expected = CLEANUP
    if fault == "wrong":
        expected = FORENSICS
    elif fault == "missing":
        rows.pop(1)
    elif fault == "duplicate":
        rows.insert(1, rows[1])
    elif fault == "digest":
        rows[1].capture_sha256 = "0" * 64
    elif fault == "active":
        doc = decode(rows[-1].capture_json.encode())
        doc["execution_authority"] = True
        changed = canonical(doc)
        rows[-1].capture_json, rows[-1].capture_sha256 = changed.decode(), sha(changed)
    elif fault == "different_intent":
        rows[1].intent_transition_id += 1
    elif fault == "non_fill":
        transition.reason_code = "post_submit_native_zero_fok_flat_v1"
    with pytest.raises((subject.report56.NativeTerminalReport0056Error, KeyError)):
        subject._pending_report(rows, reservation, transition, expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", [None, "sql", "roles", "commit_lost", "same_xid", "different"]
)
async def test_resume_confirms_commit_via_independent_rollback_transaction(
    scenario,  # noqa: F811
    monkeypatch,
    fault,
):
    role = SimpleNamespace(
        server_incarnation_sha256="1" * 64, role_route_sha256="2" * 64
    )
    settings = SimpleNamespace(submission_outbox_queue_namespace="test")
    monkeypatch.setattr(subject, "get_settings", lambda: settings)
    monkeypatch.setattr(
        subject.report56, "_require_matching_roles", AsyncMock(return_value=role)
    )
    result = subject.NativeTerminalReportResumed0056V1(
        CLEANUP,
        "3" * 64,
        "4" * 64,
        "5" * 64,
        "6" * 64,
        role.server_incarnation_sha256,
        role.role_route_sha256,
    )
    transactions = []
    commits = []
    original_exit = AsyncSessionTransaction.__aexit__
    catalog = object()
    monkeypatch.setattr(
        subject.report56.writer56,
        "observe_restricted_readwrite_writer_0056_v1",
        AsyncMock(return_value=catalog),
    )
    compare_roles = AsyncMock(
        side_effect=ValueError("changed measured role") if fault == "roles" else None
    )
    monkeypatch.setattr(
        subject.report56.locked, "_compare_held_role_pins", compare_roles
    )

    async def exit_transaction(transaction, *exc):
        await original_exit(transaction, *exc)
        if exc == (None, None, None):
            commits.append(transaction)
            if fault == "commit_lost":
                raise RuntimeError("private DB failure")

    @asynccontextmanager
    async def verified(session, *_):
        assert session.in_transaction()
        transactions.append(session)
        yield catalog, object()

    async def xid(session, statement):
        assert statement is subject._XID and session is transactions[-1]
        return "100" if len(transactions) == 1 or fault == "same_xid" else "101"

    async def staged(session, cleanup_id, namespace, roles):
        assert cleanup_id == CLEANUP and namespace == "test" and roles is role
        assert transactions == [session] and not commits
        if fault == "sql":
            raise RuntimeError("private SQL evidence refusal")
        return result

    async def lookup(session, model, key):
        assert session is transactions[-1] and session is not transactions[0]
        assert not transactions[0].in_transaction() and len(commits) == 1
        assert model is subject.QualificationReportSpool and key == result.spool_sha256
        return object()

    async def readback(session, spool, cleanup_id, namespace, roles):
        assert session is transactions[-1] and cleanup_id == CLEANUP
        return (
            replace(result, report_sha256="f" * 64) if fault == "different" else result
        )

    monkeypatch.setattr(AsyncSessionTransaction, "__aexit__", exit_transaction)
    monkeypatch.setattr(subject.report56, "_verified_terminal_writer", verified)
    monkeypatch.setattr(AsyncSession, "scalar", xid)
    monkeypatch.setattr(AsyncSession, "get", lookup)
    monkeypatch.setattr(subject, "_resume_held", staged)
    monkeypatch.setattr(subject, "_read_resumed_report", readback)
    pending = subject._resume_native_terminal_report_0056_v1(
        scenario.selected, cleanup_id=CLEANUP, expected_roles=role
    )
    if fault is None:
        assert await pending == result
        assert result.delivery_authority is result.execution_authority is False
    else:
        with pytest.raises(
            subject.report56.NativeTerminalReport0056Error,
            match="^native_terminal_report_0056_unavailable$",
        ):
            await pending
    assert len(transactions) == (1 if fault in {"sql", "roles", "commit_lost"} else 2)
    assert all(not session.in_transaction() for session in transactions)
    assert len(commits) == int(fault not in {"sql", "roles"})
    assert compare_roles.await_count == int(fault != "sql")


def test_resume_cli_redacts_uncertain_db_outcome(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "resume_configured_terminal_report_0056_v1",
        AsyncMock(side_effect=RuntimeError("private cleanup or SQL data")),
    )
    assert (
        cli.main(
            [
                "--descriptor-file",
                "private-descriptor",
                "--source-archive-file",
                "private-archive",
                "--cleanup-sha256",
                CLEANUP,
            ]
        )
        == 1
    )
    result = decode(capsys.readouterr().out.strip().encode())
    assert result["status"] == "REPORT_RESUME_UNCONFIRMED"
    assert (
        result["admission"] == "DENY"
        and result["notion_writes"] == result["order_writes"] == 0
    )
    assert "private" not in str(result) and "database_mutations" not in result
