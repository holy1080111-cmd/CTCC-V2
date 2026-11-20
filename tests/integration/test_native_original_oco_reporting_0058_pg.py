"""Actual public58 reporting SQL, with explicitly synthetic raw/cleanup ingress.

The test DTOs never attest to actual exchange/SDK cleanup or mint a report
owner. Public migration, exact restricted TLS roles and all SQL guards remain
on. Local collection is not PostgreSQL or venue acceptance.
"""

import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core import native_manual_demo_lifespan_0056_v1 as manual
from app.core.native_role_witness_0056_v1 import (
    observe_restricted_native_roles_0056_witness_v1,
)
from app.database.models.submission_reporting import QualificationReportSpool as Spool
from app.database.repositories import native_closed_reporting_0056_v1 as writer
from app.database.repositories import native_closed_reporting_v55 as shared
from app.database.repositories.submission_reporting import spool_identity
from app.domain import native_database_profile_v1 as profiles
from app.domain import native_protective_report_schema_v58 as schema
from app.domain.source_primitives import canonical, decode, sha
from app.trade_evidence.outbox import OutboxPolicy
from app.trade_evidence.submission_reporting import policy_record
from tests.integration.native_public_protective_0058_fixture_v1 import (
    prepare_public_reservation,
    prepare_public_source,
    wait_until,
)
from tests.integration.test_native_demo_profile_0058_pg import (
    _actual58_demo_lab as _actual58_demo_lab,  # noqa: PLC0414
)
from tests.integration.test_native_original_oco_public_0058_pg import (
    _guards,
)
from tests.integration.test_native_original_oco_public_0058_pg import (
    _template as _template,  # noqa: PLC0414
)
from tests.unit.test_trade_evidence_notion_adapter import destination

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]


def _utc_ns(value):
    return int(value.timestamp()) * 1_000_000_000 + value.microsecond * 1000


def _declared_synthetic_retirement(journal, terminal, closed, roles):
    """SQL fixture DATA, never an observation of real SDK/socket retirement."""
    started = max(datetime.now(UTC), closed.at, terminal[-1][1].eof_at)
    end = started + timedelta(milliseconds=1)
    mono = time.monotonic_ns()
    reads = []
    for role, page in (("identity_selector", terminal[0][1]), *terminal):
        reads.append(
            {
                "role": role,
                "path": page.path,
                "parameters": list(page.parameters),
                "body_sha256": sha(page.raw_body),
                "request_signature_sha256": page.request_signature_sha256,
                "read_session_id": page.read_session_id,
                "closed_observation": [
                    "ctcc.native_socket_closed_observation.v1",
                    _utc_ns(started) + 1,
                    mono + 1,
                    -1,
                    "native_callback_tail_and_sdk_pool_closed",
                ],
            }
        )
    target = journal.target
    return {
        "version": "ctcc.native_original_oco_read_resources_retired.v1",
        "reservation_id": target.reservation_id,
        "closure_outcome_id": closed.closure,
        "request_sha256": sha(target.request_json.encode()),
        "intent_sha256": journal.intent.sha256,
        "forensics_outcome_id": closed.forensic_id,
        "forensics_sha256": sha(closed.report.receipt_json),
        "manifest_outcome_id": closed.manifest,
        "read_only_purpose": True,
        "original_selection_retired": True,
        "started_at": started.isoformat(),
        "completed_at": end.isoformat(),
        "started_monotonic_ns": mono,
        "completed_monotonic_ns": mono + 1_000_000,
        "started_utc_ns": _utc_ns(started),
        "completed_utc_ns": _utc_ns(end),
        "original_role_route_sha256": roles.role_route_sha256,
        "original_server_incarnation_sha256": roles.server_incarnation_sha256,
        "closed_gets": reads,
        "retired_pools": [
            {
                "role": role,
                "kind": "sqlalchemy.pool.NullPool",
                "original_pool_sha256": sha((role + "-synthetic-before").encode()),
                "retired_pool_sha256": sha((role + "-synthetic-after").encode()),
            }
            for role in ("frame_reader", "custody")
        ],
        "execution_authority": False,
        "order_retry_authority": False,
    }


async def _records(journal, terminal, closed, roles, *, foreign=False):
    receipt = _declared_synthetic_retirement(journal, terminal, closed, roles)
    await wait_until(datetime.fromisoformat(receipt["completed_at"]))
    target = journal.target
    _, policy_json, policy_sha = policy_record(OutboxPolicy())
    async with journal.selected.writer_factory() as session, session.begin():
        cleanup, _ = await journal.append(
            session,
            {
                "version": schema.CLEANUP_VERSION,
                "reservation_id": target.reservation_id,
                "request_sha256": sha(target.request_json.encode()),
                "intent_sha256": journal.intent.sha256,
                "closure_outcome_id": closed.closure,
                "closure_receipt_sha256": sha(closed.assessment.receipt_json),
                "forensics_outcome_id": "0" * 64 if foreign else closed.forensic_id,
                "forensics_sha256": sha(closed.report.receipt_json),
                "manifest_outcome_id": closed.manifest,
                "cleanup_receipt_json": canonical(receipt).decode(),
                "cleanup_receipt_sha256": sha(canonical(receipt)),
                "read_resources_retired": True,
            },
            status="acknowledged",
        )
        ready, at = await journal.append(
            session,
            {
                "version": schema.READY_VERSION,
                "reservation_id": target.reservation_id,
                "request_sha256": sha(target.request_json.encode()),
                "intent_sha256": journal.intent.sha256,
                "closure_outcome_id": closed.closure,
                "cleanup_outcome_id": cleanup,
                "forensics_outcome_id": closed.forensic_id,
                "report_sha256": sha(closed.report.receipt_json),
                "policy_sha256": policy_sha,
                "report_policy_version": schema.REPORT_POLICY_VERSION,
                "report_policy_sha256": schema.REPORT_POLICY_SHA256,
            },
            status="acknowledged",
        )
        xid = await session.scalar(text("SELECT pg_current_xact_id()::text"))
    return {
        "outcome_id": ready,
        "reservation_id": target.reservation_id,
        "report_id": target.request.origin.candidate.report_id,
        "report_bytes": closed.report.receipt_json,
        "report_sha256": sha(closed.report.receipt_json),
        "policy_json": policy_json,
        "policy_sha256": policy_sha,
        "queue_namespace": "synthetic_original_oco",
        "eligible": True,
        "created_at": at,
    }, xid


async def _prepared(monkeypatch, lab, template):
    owner, selected, _ = lab
    before = await _guards(owner)
    roles = await observe_restricted_native_roles_0056_witness_v1(
        selected, source=ROOT, profile=profiles.DB0058
    )
    _, item, recorded, issued, plan, _ = await prepare_public_source(
        monkeypatch, owner, selected, template
    )
    journal, _ = await prepare_public_reservation(
        selected, item, recorded, issued, plan, template
    )
    original, terminal, hold = await journal.seed_hold(recorded)
    closed = await journal.close(original, terminal, hold, retain_forensics=True)
    return owner, selected, before, roles, journal, terminal, closed


async def test_actual0058_original_oco_report_spool_replays_without_own_close_intent(
    monkeypatch, _actual58_demo_lab, _template, tmp_path
):
    owner, selected, before, roles, journal, terminal, closed = await _prepared(
        monkeypatch, _actual58_demo_lab, _template
    )
    values, xid = await _records(journal, terminal, closed, roles)
    spool = Spool(
        spool_id=spool_identity(
            values["outcome_id"],
            values["report_sha256"],
            values["policy_sha256"],
            values["queue_namespace"],
        ),
        **values,
    )
    async with selected.writer_factory() as session, session.begin():
        session.add(spool)
        await session.flush()
        write_xid = await session.scalar(text("SELECT pg_current_xact_id()::text"))
    factory, engine, names, _ = writer._factory_pins(selected)
    route = writer.locked._direct_tls_route(engine)
    async with (
        factory() as session,
        session.begin(),
        writer._verified_terminal_writer(
            session, engine, roles, names, route, profile=profiles.DB0058
        ),
    ):
        current = await session.get(Spool, spool.spool_id)
        ready, view, policy = await shared._replay_native_terminal_spool(
            session, current
        )
        assert ready.outcome_id == values["outcome_id"]
        assert view.report_id == journal.target.request.origin.candidate.report_id
        assert policy == OutboxPolicy()
        assert await session.scalar(text("SELECT pg_current_xact_id()::text")) not in (
            xid,
            write_xid,
            closed.write_xid,
        )
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM public.qualification_submission_outcomes WHERE reservation_id=:rid AND capture_json::jsonb->>'version'='ctcc.native_controlled_close_intent.v1'"
                ),
                {"rid": journal.target.reservation_id},
            )
            == 0
        )
        assert (
            await session.scalar(
                select(Spool.spool_id).where(
                    Spool.reservation_id == journal.target.reservation_id
                )
            )
            == spool.spool_id
        )
    # A fresh configured process builds its own three TLS engines. No product
    # factory/guard/catalog is patched and no external Notion request is sent.
    await _configured_reporting_child(selected, spool, tmp_path)
    assert await _guards(owner) == before


async def _configured_reporting_child(selected, spool, tmp_path):
    private = tmp_path / "current58_reporting"
    private.mkdir(mode=0o700)
    root = private / "outbox"
    root.mkdir(mode=0o700)
    destination_file = private / "declared_synthetic_destination.json"
    destination_bytes = destination().model_dump_json(round_trip=True).encode()
    destination_file.write_bytes(destination_bytes)
    destination_file.chmod(0o600)
    urls = tuple(
        factory.kw["bind"].url
        for factory in (
            selected.writer_factory,
            selected.custody_factory,
            selected.frame_reader_factory,
        )
    )
    assert all(url.password is None and not url.query for url in urls)
    expected = hashlib.sha256((ROOT / "MANIFEST.sha256").read_bytes()).hexdigest()
    env = {
        **{name.upper(): "false" for name in manual._DISABLED},
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PYTHONPATH": str(ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "ENVIRONMENT": "production",
        "TRADING_MODE": "okx_demo",
        "WEB_CONCURRENCY": "1",
        "API_TOKEN": hashlib.sha256(b"declared-current58-reporting-only").hexdigest(),
        "CTCC_NATIVE_DEMO_MANUAL_LIFESPAN_0056_V1": "true",
        "OKX_DEMO_ENABLED": "true",
        "OKX_DEMO_ALLOW_ORDER_WRITES": "false",
        "OKX_DEMO_AUTO_EXECUTION": "false",
        "DATABASE_URL": urls[0].render_as_string(hide_password=False),
        "OKX_DEMO_NATIVE_CUSTODY_DATABASE_URL": urls[1].render_as_string(
            hide_password=False
        ),
        "OKX_DEMO_NATIVE_FRAME_READER_DATABASE_URL": urls[2].render_as_string(
            hide_password=False
        ),
        "CTCC_NATIVE_PG_TLS_CA_FILE": os.environ[
            "CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE"
        ],
        "CTCC_NATIVE_PG_TLS_CA_SHA256": os.environ[
            "CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"
        ],
        "SUBMISSION_OUTBOX_ENABLED": "true",
        "SUBMISSION_OUTBOX_ROOT": str(root),
        "SUBMISSION_OUTBOX_QUEUE_NAMESPACE": spool.queue_namespace,
        "NOTION_OUTBOX_ENABLED": "true",
        "NOTION_OUTBOX_ROOT": str(root),
        "NOTION_OUTBOX_DESTINATION_FILE": str(destination_file),
        "NOTION_OUTBOX_DESTINATION_SHA256": hashlib.sha256(
            destination_bytes
        ).hexdigest(),
        "CTCC_CURRENT58_REPORTING_HERMETIC_CHILD": "1",
        "CTCC_EXPECTED_REPORTING_MANIFEST_SHA256": expected,
        "CTCC_CURRENT58_REPORTING_SPOOL_SHA256": spool.spool_id,
        "CTCC_CURRENT58_REPORTING_REPORT_ID": spool.report_id,
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-B",
        "-m",
        "tests.integration.native_current58_reporting_child_v1",
        cwd=str(private),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(240):
            stdout, stderr = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert len(stdout) <= 8192
    result = json.loads(stdout.strip())
    assert process.returncode == 0 and result["status"] == "PASS", result
    assert stderr == b""
    assert result["database_head"] == "0058"
    assert result["source_manifest_sha256"] == expected
    assert result["report_spool_sha256"] == spool.spool_id
    assert result["projection_and_local_pair_readback"] is True
    assert result["notion_source_distinct_committed_readbacks"] == 2
    assert result["notion_writes"] == result["order_writes"] == 0
    assert result["execution_authority"] is False


async def test_actual0058_original_oco_reporting_guard_refuses_foreign_forensics(
    monkeypatch, _actual58_demo_lab, _template
):
    owner, selected, before, roles, journal, terminal, closed = await _prepared(
        monkeypatch, _actual58_demo_lab, _template
    )
    with pytest.raises(DBAPIError, match="submission_outcome_binding_denied"):
        await _records(journal, terminal, closed, roles, foreign=True)
    async with selected.writer_factory() as session, session.begin():
        assert (
            await session.scalar(
                select(Spool.spool_id).where(
                    Spool.reservation_id == journal.target.reservation_id
                )
            )
            is None
        )
        assert (
            await session.scalar(
                text(
                    "SELECT state FROM public.qualification_reservations WHERE reservation_id=:rid"
                ),
                {"rid": journal.target.reservation_id},
            )
            == "reconciled_flat"
        )
    assert await _guards(owner) == before


async def test_actual0058_original_oco_report_policy_refuses_rehashed_changed_analysis(
    monkeypatch, _actual58_demo_lab, _template
):
    owner, selected, before, roles, journal, terminal, closed = await _prepared(
        monkeypatch, _actual58_demo_lab, _template
    )
    values, _ = await _records(journal, terminal, closed, roles)
    changed = decode(values["report_bytes"], 140000)
    changed["analysis"]["metrics_complete"] = True
    values.update(
        report_bytes=canonical(changed), report_sha256=sha(canonical(changed))
    )
    spool = Spool(
        spool_id=spool_identity(
            values["outcome_id"],
            values["report_sha256"],
            values["policy_sha256"],
            values["queue_namespace"],
        ),
        **values,
    )
    async with selected.writer_factory() as session, session.begin():
        assert (
            await session.scalar(
                text(
                    "SELECT public.qualification_native_report_eligible_v55(:oid,:rid,:report,:policy)"
                ),
                {
                    "oid": values["outcome_id"],
                    "rid": values["reservation_id"],
                    "report": values["report_sha256"],
                    "policy": values["policy_sha256"],
                },
            )
            is False
        )
        async with session.begin_nested() as savepoint:
            session.add(spool)
            with pytest.raises(DBAPIError, match="native_terminal_spool_denied"):
                await session.flush()
            await savepoint.rollback()
        assert (
            await session.scalar(
                select(Spool.spool_id).where(
                    Spool.reservation_id == journal.target.reservation_id
                )
            )
            is None
        )
    assert await _guards(owner) == before
