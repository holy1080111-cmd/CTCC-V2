"""DB0041→0042 PostgreSQL transition preserves private evidence and denies IO."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.database.models.submission_reporting import (
    QualificationReportProjectionReceipt,
    QualificationReportSpool,
    QualificationSubmissionOutcome,
)
from app.database.repositories import submission_reporting as reporting_module
from tests.durable_migration_fixtures import load_migration
from tests.integration import test_submission_reporting_repository as reports
from tests.integration.test_durable_migration_downgrade import migrate, shape
from tests.integration.test_durable_migration_downgrade import (
    sandbox as sandbox,  # noqa: PLC0414
)
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414
)
from tests.unit.qualification_ledger_fixtures import ledger_fixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def synthetic():
    return ledger_fixture(
        account_id=f"123456789{uuid4().int % 10**12:012d}",
        report_id="synthetic-transport-migration-" + uuid4().hex,
    )


async def _private_rows(sessions, outcome_id, spool_id):
    async with sessions() as session:
        outcome = await session.get(QualificationSubmissionOutcome, outcome_id)
        spool = await session.get(QualificationReportSpool, spool_id)
        receipt = await session.get(QualificationReportProjectionReceipt, spool_id)
        return (
            outcome.capture_json,
            outcome.outcome_json,
            spool.report_bytes,
            spool.policy_json,
            spool.eligible,
            receipt.envelope_sha256 if receipt else None,
        )


async def _assert_db0042_catalog(engine):
    migration = load_migration("0042")
    expected = {
        "qualification_spool_binding": migration._spool_guard(),
        "qualification_projection_binding": migration._projection_guard(),
    }
    async with engine.connect() as connection:
        functions = (
            await connection.execute(
                text("""
                  SELECT p.proname,p.prosrc,p.proconfig
                  FROM pg_catalog.pg_proc p
                  JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                  WHERE n.nspname='public'
                    AND p.proname IN (
                      'qualification_spool_binding',
                      'qualification_projection_binding')
                """)
            )
        ).all()
        assert len(functions) == 2
        for name, body, settings in functions:
            assert (
                body == expected[name].split("AS $$", 1)[1].split("$$ LANGUAGE", 1)[0]
            )
            assert type(settings) in (list, tuple) and len(settings) == 1
            name, separator, values = settings[0].partition("=")
            assert name == "search_path" and separator == "="
            assert tuple(part.strip() for part in values.split(",")) == (
                "pg_catalog",
                "pg_temp",
            )
        checks = {
            name: validated
            for name, validated in (
                await connection.execute(
                    text("""
                      SELECT conname,convalidated FROM pg_catalog.pg_constraint
                      WHERE conname IN (
                        'ck_qualification_report_spool_transport_unverified',
                        'ck_qualification_report_projection_receipts_transport_unverified')
                    """)
                )
            ).all()
        }
        assert checks == {
            "ck_qualification_report_spool_transport_unverified": True,
            "ck_qualification_report_projection_receipts_transport_unverified": True,
        }
        triggers = {
            name: enabled
            for name, enabled in (
                await connection.execute(
                    text("""
                      SELECT tgname,tgenabled FROM pg_catalog.pg_trigger
                      WHERE tgname IN (
                        'qualification_spool_binding_guard',
                        'qualification_projection_binding_guard')
                    """)
                )
            ).all()
        }
        assert triggers == {
            "qualification_spool_binding_guard": "O",
            "qualification_projection_binding_guard": "O",
        }


@pytest.mark.parametrize("revision", ["0041"])
async def test_upgrade_refuses_legacy_eligible_and_receipt_without_erasing_bytes(
    sandbox, synthetic, monkeypatch
):
    engine, sessions = sandbox
    _, reporter, clock, hold, args = await reports.setup(sandbox, synthetic)
    # DB0041 retains DB0018's status-only trigger. Reproduce the historical
    # unsafe row, then restore the current application rule before upgrading.
    with monkeypatch.context() as patcher:
        patcher.setattr(reporting_module, "spool_eligible", lambda _doc: True)
        created = await reporter.record_observation(
            hold.scope, hold.original_event_key, **args
        )
    async with sessions() as session, session.begin():
        spool = await session.get(QualificationReportSpool, created.spool_id)
        assert spool.eligible is True
        assert (
            json.loads(
                (
                    await session.get(
                        QualificationSubmissionOutcome, created.outcome_id
                    )
                ).outcome_json
            )["transport_authenticity_verified"]
            is False
        )
        session.add(
            QualificationReportProjectionReceipt(
                spool_id=spool.spool_id,
                report_sha256=spool.report_sha256,
                policy_sha256=spool.policy_sha256,
                envelope_sha256="e" * 64,
                queue_namespace=spool.queue_namespace,
                verified_at=clock.value + timedelta(milliseconds=1),
            )
        )
    before = await shape(engine)
    private_before = await _private_rows(sessions, created.outcome_id, created.spool_id)
    with pytest.raises(
        DBAPIError,
        match="submission_transport_legacy_rows_require_forensic_resolution",
    ):
        async with engine.begin() as connection:
            await connection.run_sync(migrate, "0042", "upgrade")
    assert await shape(engine) == before
    assert (
        await _private_rows(sessions, created.outcome_id, created.spool_id)
        == private_before
    )


@pytest.mark.parametrize("revision", ["0041"])
async def test_upgrade_retains_ineligible_private_spool_and_denies_projection(
    sandbox, synthetic
):
    engine, sessions = sandbox
    _, reporter, clock, hold, args = await reports.setup(
        sandbox, synthetic, status="rejected"
    )
    created = await reporter.record_observation(
        hold.scope, hold.original_event_key, **args
    )
    before = await _private_rows(sessions, created.outcome_id, created.spool_id)
    assert before[4] is False and before[5] is None
    prior_shape = await shape(engine)
    async with engine.begin() as connection:
        await connection.run_sync(migrate, "0042", "upgrade")
    current_shape = await shape(engine)
    assert current_shape != prior_shape
    await _assert_db0042_catalog(engine)
    assert await _private_rows(sessions, created.outcome_id, created.spool_id) == before
    async with sessions() as session:
        spool = await session.get(QualificationReportSpool, created.spool_id)
    with pytest.raises(DBAPIError, match="submission_projection_transport_unverified"):
        async with sessions() as session, session.begin():
            session.add(
                QualificationReportProjectionReceipt(
                    spool_id=spool.spool_id,
                    report_sha256=spool.report_sha256,
                    policy_sha256=spool.policy_sha256,
                    envelope_sha256="f" * 64,
                    queue_namespace=spool.queue_namespace,
                    verified_at=clock.value + timedelta(milliseconds=1),
                )
            )
            await session.flush()
    assert await _private_rows(sessions, created.outcome_id, created.spool_id) == before
    with pytest.raises(
        DBAPIError, match="submission_transport_authenticity_downgrade_denied"
    ):
        async with engine.begin() as connection:
            await connection.run_sync(migrate, "0042", "downgrade")
    assert await shape(engine) == current_shape


@pytest.mark.parametrize("revision", ["0041"])
@pytest.mark.parametrize("disable_guard", [False, True])
async def test_new_acknowledged_spool_cannot_be_forged_eligible_after_upgrade(
    sandbox, synthetic, monkeypatch, disable_guard
):
    engine, sessions = sandbox
    async with engine.begin() as connection:
        await connection.run_sync(migrate, "0042", "upgrade")
    if disable_guard:
        async with engine.begin() as connection:
            await connection.execute(
                text("""
                  ALTER TABLE public.qualification_report_spool
                  DISABLE TRIGGER qualification_spool_binding_guard
                """)
            )
    try:
        _, reporter, _clock, hold, args = await reports.setup(sandbox, synthetic)
        with monkeypatch.context() as patcher:
            patcher.setattr(reporting_module, "spool_eligible", lambda _doc: True)
            with pytest.raises(DBAPIError):
                await reporter.record_observation(
                    hold.scope, hold.original_event_key, **args
                )
    finally:
        if disable_guard:
            async with engine.begin() as connection:
                await connection.execute(
                    text("""
                      ALTER TABLE public.qualification_report_spool
                      ENABLE TRIGGER qualification_spool_binding_guard
                    """)
                )
    async with sessions() as session:
        outcomes = (
            await session.execute(
                text("SELECT count(*) FROM public.qualification_submission_outcomes")
            )
        ).scalar_one()
        spools = (
            await session.execute(
                text("SELECT count(*) FROM public.qualification_report_spool")
            )
        ).scalar_one()
        assert outcomes == spools == 0


@pytest.mark.parametrize("revision", ["0041"])
async def test_new_acknowledged_response_is_retained_only_as_ineligible_evidence(
    sandbox, synthetic
):
    engine, sessions = sandbox
    async with engine.begin() as connection:
        await connection.run_sync(migrate, "0042", "upgrade")
    _, reporter, _clock, hold, args = await reports.setup(sandbox, synthetic)
    created = await reporter.record_observation(
        hold.scope, hold.original_event_key, **args
    )
    private = await _private_rows(sessions, created.outcome_id, created.spool_id)
    assert json.loads(private[1])["status"] == "acknowledged"
    assert json.loads(private[1])["transport_authenticity_verified"] is False
    assert private[4] is False and private[5] is None


@pytest.mark.parametrize("revision", ["0041"])
async def test_active_writer_prevents_partial_upgrade(sandbox):
    engine, _ = sandbox
    before = await shape(engine)
    async with engine.begin() as writer:
        await writer.execute(
            text("LOCK TABLE public.qualification_report_spool IN ROW EXCLUSIVE MODE")
        )
        with pytest.raises(DBAPIError) as denied:
            async with engine.begin() as migration:
                await migration.run_sync(migrate, "0042", "upgrade")
        assert denied.value.orig.sqlstate == "55P03"
    assert await shape(engine) == before
