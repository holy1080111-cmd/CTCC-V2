"""Isolated PG journal tests; fabricated hashes here prove no source authenticity."""

import asyncio
import json
import os
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.source_primitives import canonical, sha
from app.trade_qualification.g12_invocation_custody import (
    G12InvocationClaim,
    G12InvocationCustodyError,
    G12InvocationCustodyRepository,
    G12InvocationPublication,
)
from tests.durable_migration_fixtures import load_migration

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_FILES = ("4h.png", "1h.png", "15m.png", "5m.png", "summary.png", "report.json")


def database():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("explicit isolated DATABASE_URL required")
    engine = create_async_engine(url, poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _claim(*, uid=None, event=None):
    uid = uid or sha(uuid4().hex.encode())
    event = event or sha(uuid4().hex.encode())
    invocation = uuid4().hex
    doc = canonical(
        {
            "schema_version": "ctcc.g12.invocation_claim.v1",
            "invocation_id": invocation,
            "environment": "demo",
            "account_uid_sha256": uid,
            "account_plan_sha256": "1" * 64,
            "event_key": event,
            "report_id": "synthetic-db-only",
            "instrument_id": "BTC-USDT-SWAP",
            "direction": "long",
            "entry": "100",
            "stop_loss": "99",
            "take_profit": "103",
            "deadline": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            "candidate_sha256": "2" * 64,
            "original_run_sha256": "3" * 64,
            "original_market_sha256": "4" * 64,
            "original_inputs_sha256": "5" * 64,
            "evidence_root_sha256": "6" * 64,
            "admission": "DENY",
            "source_authenticity_verified": False,
            "execution_authority": False,
        }
    )
    return G12InvocationClaim(invocation, doc, sha(doc))


def _publication(claim):
    doc = canonical(
        {
            "schema_version": "ctcc.g12.invocation_publication.v1",
            "invocation_id": claim.invocation_id,
            "claim_sha256": claim.claim_sha256,
            "candidate_sha256": claim.values["candidate_sha256"],
            "event_key": claim.values["event_key"],
            "evidence_root_sha256": claim.values["evidence_root_sha256"],
            "evidence_sha256": "7" * 64,
            "report_sha256": "8" * 64,
            "file_sha256": {name: "9" * 64 for name in _FILES},
            "publication_completed_at": (
                claim.db_claimed_at + timedelta(microseconds=1)
            ).isoformat(),
            "admission": "DENY",
            "publication_provenance_verified": False,
            "execution_authority": False,
        }
    )
    return G12InvocationPublication(claim.invocation_id, doc, sha(doc))


async def test_committed_claim_publication_restart_readback_and_immutability():
    engine, factory = database()
    try:
        repo = G12InvocationCustodyRepository(factory)
        claim = await repo.claim(_claim())
        assert claim.db_claimed_at is not None
        assert claim.admission == "DENY" and claim.execution_authority is False
        await asyncio.sleep(0.05)
        publication = await repo.publication(claim, _publication(claim))
        assert publication.db_recorded_at >= claim.db_claimed_at
        restarted = G12InvocationCustodyRepository(factory)
        assert await restarted._read_claim(claim) == claim
        assert (
            await restarted.read_publication(claim, publication.publication_sha256)
        ) == publication
        with pytest.raises(DBAPIError):
            async with factory() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE public.g12_invocation_claims SET claim_sha256=:digest "
                        "WHERE invocation_id=:id"
                    ),
                    {"digest": "0" * 64, "id": claim.invocation_id},
                )
        with pytest.raises(G12InvocationCustodyError):
            await restarted.publication(claim, _publication(claim))
    finally:
        await engine.dispose()


async def test_same_uid_event_concurrent_preclaims_at_most_one_commits():
    engine, factory = database()
    try:
        uid, event = sha(uuid4().hex.encode()), sha(uuid4().hex.encode())
        first, second = _claim(uid=uid, event=event), _claim(uid=uid, event=event)
        repo = G12InvocationCustodyRepository(factory)
        outcomes = await asyncio.gather(
            repo.claim(first), repo.claim(second), return_exceptions=True
        )
        assert sum(type(item) is G12InvocationClaim for item in outcomes) == 1
        assert sum(type(item) is G12InvocationCustodyError for item in outcomes) == 1
    finally:
        await engine.dispose()


async def test_direct_sql_rejects_missing_direction_and_outside_claim_db_interval():
    engine, factory = database()
    try:
        malformed = _claim()
        altered = malformed.values
        del altered["direction"]
        altered["unexpected_key"] = "long"
        malformed_json = canonical(altered)
        assert len(altered) == 21
        with pytest.raises(DBAPIError):
            async with factory() as session, session.begin():
                await session.execute(
                    text(
                        "INSERT INTO public.g12_invocation_claims "
                        "(invocation_id,environment,account_uid_sha256,event_key,claim_json,claim_sha256) "
                        "VALUES (:id,'demo',:uid,:event,:body,:digest)"
                    ),
                    {
                        "id": malformed.invocation_id,
                        "uid": altered["account_uid_sha256"],
                        "event": altered["event_key"],
                        "body": malformed_json.decode("utf-8"),
                        "digest": sha(malformed_json),
                    },
                )

        claim = await G12InvocationCustodyRepository(factory).claim(_claim())
        publication = _publication(claim)
        for completed_at in (
            claim.db_claimed_at - timedelta(microseconds=1),
            claim.db_claimed_at + timedelta(minutes=10),
        ):
            values = json.loads(publication.publication_json)
            values["publication_completed_at"] = completed_at.isoformat()
            body = canonical(values)
            with pytest.raises(DBAPIError):
                async with factory() as session, session.begin():
                    await session.execute(
                        text(
                            "INSERT INTO public.g12_invocation_publications "
                            "(invocation_id,claim_sha256,publication_json,publication_sha256) "
                            "VALUES (:id,:claim,:body,:digest)"
                        ),
                        {
                            "id": claim.invocation_id,
                            "claim": claim.claim_sha256,
                            "body": body.decode("utf-8"),
                            "digest": sha(body),
                        },
                    )
    finally:
        await engine.dispose()


def _migrate_0039(sync_connection, direction):
    migration = load_migration("0039")
    migration.op = Operations(MigrationContext.configure(sync_connection))
    getattr(migration, direction)()


async def test_0039_fresh_upgrade_empty_downgrade_reupgrade_and_nonempty_guard():
    """Only an exact hermetic Docker PG parent may create this disposable DB."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("explicit isolated DATABASE_URL required")
    admin = create_async_engine(url, poolclass=NullPool)
    parsed = admin.url
    if (
        parsed.drivername != "postgresql+asyncpg"
        or re.fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", parsed.host or "") is None
        or parsed.username != "ctcc"
        or parsed.password is not None
        or parsed.port != 5432
        or parsed.database != "ctcc"
        or bool(parsed.query)
    ):
        await admin.dispose()
        raise RuntimeError("g12_migration_hermetic_cluster_required")
    name = "ctcc_g12_migration_test_" + uuid4().hex
    if re.fullmatch(r"ctcc_g12_migration_test_[a-f0-9]{32}", name) is None:
        raise RuntimeError("g12_migration_target_invalid")
    target = None
    created = False
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            if await connection.scalar(text("SELECT current_database()")) != "ctcc":
                raise RuntimeError("g12_migration_parent_changed")
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
        target = create_async_engine(
            parsed.set(database=name),
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": "public,pg_catalog"}},
        )
        async with target.begin() as connection:
            await connection.execute(
                text("""
              CREATE FUNCTION public.qualification_ledger_immutable()
              RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'immutable'; END $$
              LANGUAGE plpgsql
            """)
            )
            await connection.run_sync(_migrate_0039, "upgrade")
        async with target.begin() as connection:
            found = (
                (
                    await connection.execute(
                        text("""
              SELECT tablename FROM pg_catalog.pg_tables
              WHERE schemaname='public' AND tablename LIKE 'g12_invocation_%'
            """)
                    )
                )
                .scalars()
                .all()
            )
            assert set(found) == {
                "g12_invocation_claims",
                "g12_invocation_publications",
            }
            await connection.run_sync(_migrate_0039, "downgrade")
            await connection.run_sync(_migrate_0039, "upgrade")
        factory = async_sessionmaker(target, expire_on_commit=False)
        claim = await G12InvocationCustodyRepository(factory).claim(_claim())
        with pytest.raises(DBAPIError):
            async with target.begin() as connection:
                await connection.run_sync(_migrate_0039, "downgrade")
        async with target.connect() as connection:
            retained = await connection.scalar(
                text(
                    "SELECT count(*) FROM public.g12_invocation_claims WHERE invocation_id=:id"
                ),
                {"id": claim.invocation_id},
            )
            assert retained == 1
    finally:
        if target is not None:
            await target.dispose()
        if created:
            async with admin.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                if await connection.scalar(text("SELECT current_database()")) != "ctcc":
                    raise RuntimeError("g12_migration_cleanup_origin_changed")
                await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()
