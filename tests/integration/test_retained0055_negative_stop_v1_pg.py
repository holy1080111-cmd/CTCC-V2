"""Required real-source negative-stop cases; pending genuine copy lineage.

No actual-source or custody guard is stubbed. These tests may execute only once
the shared real clone fixture supplies the reviewed source-owned lineage; the
current pending boundary is a refusal, never an approved skip or SQL success.
"""

from dataclasses import asdict, replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncTransaction

from scripts import retained0055_negative_stop_reassert_v1 as subject
from tests.integration.test_retained0017_owner_upgrade_0055_v1_pg import (
    _canonical_upgrade17_lab as _canonical_upgrade17_lab,  # noqa: PLC0414 -- explicit real pytest fixture re-export.
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def _negative_stop55_lab(_canonical_upgrade17_lab):
    engine, pins, _prepared, arguments, _audit, _private = _canonical_upgrade17_lab
    # No monkeypatch, passed boolean or caller report can enable the current
    # pending real-copy prerequisite. The actual controlled clone fixture must
    # satisfy its reviewed successor before this positive suite can run.
    subject.owner._require_apply_lineage()
    await subject.owner.upgrade_preserved_owner_0017_to_0055_v1(
        pins, confirmation=subject.owner.CONFIRMATION, **arguments
    )
    route = subject.canonical.OwnerRoutePinsV1(
        **{**asdict(pins.retained.route), "database_head": "0055"}
    )
    reviewed = subject.canonical.OwnerUpgradePinsV1(
        pins.retained.source_commit,
        pins.retained.source_tree,
        pins.retained.source_archive_sha256,
        route,
    )
    stop_pins = subject.NegativeStopPinsV1(reviewed, pins.maintenance_settings_sha256)
    options = {
        "owner_dsn_file": arguments["owner_dsn_file"],
        "ca_file": arguments["ca_file"],
        "archive_file": arguments["source_archive_file"],
    }
    observed = await subject.reassert_retained_owner_0055_stop_v1(stop_pins, **options)
    assert observed["mutation_performed"] is False and observed["exchange_flat"] is None
    yield (
        engine,
        replace(stop_pins, reviewed_snapshot_sha256=observed["snapshot_sha256"]),
        options,
    )


async def test_actual0055_negative_stop_preserves_rows_reasons_counters_and_one_uuid_journal(
    _negative_stop55_lab,
):
    engine, pins, options = _negative_stop55_lab
    before = await subject._read(engine, pins)
    old = subject._control(before["control"], revision=0)
    result = await subject.reassert_retained_owner_0055_stop_v1(
        pins, confirmation=subject.CONFIRMATION, **options
    )
    assert result["control_revision"] == 1 and result["independent_readbacks"] == 2
    assert result["execution_authority"] is False and result["exchange_flat"] is None
    after = await subject._read(engine, pins)
    actual = subject._control(after["control"], revision=1)
    assert actual["lock_reasons"] == old["lock_reasons"] + [subject.guard.REASON]
    assert after["sequence_state"] == before["sequence_state"]
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT id,action,occurred_at FROM public.audit_logs WHERE id=:id"
                ),
                {"id": UUID(result["operation_id"])},
            )
        ).one()
        assert (
            str(row[0]) == result["operation_id"]
            and row[1] == subject.ACTION
            and row[2].utcoffset().total_seconds() == 0
        )
    with pytest.raises(subject.NegativeStopRefused):
        await subject.reassert_retained_owner_0055_stop_v1(
            pins, confirmation=subject.CONFIRMATION, **options
        )


async def test_actual0055_negative_stop_failure_after_real_journal_insert_rolls_everything_back(
    _negative_stop55_lab, monkeypatch
):
    engine, pins, options = _negative_stop55_lab
    before = await subject._read(engine, pins)
    original = subject._project
    inserted = []

    async def fail_after_real_insert(connection, layouts, phase, operation_id=None):
        value = await original(connection, layouts, phase, operation_id)
        if phase is subject._Projection.NEW_JOURNAL:
            assert value[0] == 1
            inserted.append(str(operation_id))
            raise RuntimeError("declared post-insert isolated failure")
        return value

    monkeypatch.setattr(subject, "_project", fail_after_real_insert)
    with pytest.raises(subject.NegativeStopRefused):
        await subject.reassert_retained_owner_0055_stop_v1(
            pins, confirmation=subject.CONFIRMATION, **options
        )
    assert len(inserted) == 1 and await subject._read(engine, pins) == before


async def test_actual0055_negative_stop_real_commit_lost_result_is_terminal_and_not_repeated(
    _negative_stop55_lab, monkeypatch
):
    engine, pins, options = _negative_stop55_lab
    original, commits = AsyncTransaction.commit, []

    async def lost(transaction):
        await original(transaction)
        commits.append(1)
        raise OSError("declared lost commit acknowledgement")

    monkeypatch.setattr(AsyncTransaction, "commit", lost)
    with pytest.raises(subject.NegativeStopCommitUnknown) as error:
        await subject.reassert_retained_owner_0055_stop_v1(
            pins, confirmation=subject.CONFIRMATION, **options
        )
    assert commits == [1]
    monkeypatch.setattr(AsyncTransaction, "commit", original)
    actual = await subject._read(engine, pins)
    subject._control(actual["control"], revision=1)
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT pg_catalog.count(*) FROM public.audit_logs WHERE id=:id AND action=:action"
                ),
                {"id": error.value.operation_id, "action": subject.ACTION},
            )
            == 1
        )
    with pytest.raises(subject.NegativeStopRefused):
        await subject.reassert_retained_owner_0055_stop_v1(
            pins, confirmation=subject.CONFIRMATION, **options
        )
    assert commits == [1]


@pytest.mark.parametrize("fault", ("control_trigger_body", "audit_default"))
async def test_actual0055_negative_stop_refuses_foreign_callback_without_invoking_it(
    _negative_stop55_lab, fault
):
    engine, pins, options = _negative_stop55_lab
    sequence = "stop_callback_canary_" + uuid4().hex[:12]
    async with engine.begin() as connection:
        await connection.exec_driver_sql('CREATE SEQUENCE public."' + sequence + '"')
        if fault == "control_trigger_body":
            await connection.exec_driver_sql(
                "CREATE OR REPLACE FUNCTION public.demo_automation_control_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_catalog.nextval('public."
                + sequence
                + "'::pg_catalog.regclass); RETURN NEW; END; $$"
            )
        else:
            await connection.exec_driver_sql(
                "CREATE FUNCTION public.stop_audit_default_canary() RETURNS timestamptz LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_catalog.nextval('public."
                + sequence
                + "'::pg_catalog.regclass); RETURN CURRENT_TIMESTAMP; END; $$"
            )
            await connection.exec_driver_sql(
                "ALTER TABLE public.audit_logs ALTER COLUMN occurred_at SET DEFAULT public.stop_audit_default_canary()"
            )
    with pytest.raises(subject.NegativeStopRefused):
        await subject.reassert_retained_owner_0055_stop_v1(
            pins, confirmation=subject.CONFIRMATION, **options
        )
    async with engine.connect() as connection:
        # nextval is nontransactional. False proves the foreign callback was
        # never executed; rollback cannot conceal an invocation.
        assert (
            await connection.scalar(
                text('SELECT is_called FROM public."' + sequence + '"')
            )
            is False
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT control_revision FROM public.demo_automation_state WHERE id=1"
                )
            )
            == 0
        )
