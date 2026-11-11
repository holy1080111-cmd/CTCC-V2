"""Opt-in PostgreSQL acceptance of the isolated campaign custody schema."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.mie.validation.contracts import DatasetPartition, PartitionWindow
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    ProspectiveDevvalCampaignPlanV1,
    freeze_prospective_devval_campaign_v1,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_SQL = (
    Path(__file__).resolve().parents[2]
    / "scripts/sql/gate3_devval_campaign_custody_v1.sql"
)
_SCHEMA = "ctcc_devval_custody"
_PUBLISH = text("SELECT ctcc_devval_custody.publish_campaign(:sha, :payload)")
_ACK = text("SELECT ctcc_devval_custody.acknowledge_campaign(:sha)")
_READ = text("SELECT * FROM ctcc_devval_custody.read_campaign(:sha)")


@pytest.fixture
async def evidence_db():
    raw = os.environ.get("CTCC_DEVVAL_CUSTODY_TEST_DATABASE_URL")
    if raw is None:
        pytest.skip("explicit isolated D/V custody PostgreSQL test URL required")
    url = make_url(raw)
    if (
        url.drivername != "postgresql+asyncpg"
        or url.password is not None
        or url.query
        or not re.fullmatch(
            r"127\.0\.0\.1|ctcc-final-[a-f0-9]{12}-postgres", url.host or ""
        )
    ):
        pytest.fail("D/V custody SQL acceptance requires an isolated trust-auth lab")
    nonce = uuid4().hex
    database = f"ctcc_dv_{nonce}"
    publisher = f"ctcc_dvp_{nonce}"
    observer = f"ctcc_dvo_{nonce}"
    admin = create_async_engine(url, poolclass=NullPool)
    database_created = False
    roles_created = False
    engines = []
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(text(f"CREATE DATABASE {database}"))
        database_created = True
        async with admin.begin() as connection:
            await connection.execute(text(f"CREATE ROLE {publisher} LOGIN"))
            await connection.execute(text(f"CREATE ROLE {observer} LOGIN"))
        roles_created = True
        db_url = url.set(database=database)
        owner = create_async_engine(db_url, poolclass=NullPool)
        engines.append(owner)
        async with owner.connect() as connection:
            connection_raw = await connection.get_raw_connection()
            await connection_raw.driver_connection.execute(
                _SQL.read_text(encoding="utf-8")
            )
        async with owner.begin() as connection:
            await connection.execute(
                text(f"GRANT USAGE ON SCHEMA {_SCHEMA} TO {publisher}, {observer}")
            )
            for function, actor in (
                ("publish_campaign(text,text)", publisher),
                ("acknowledge_campaign(text)", observer),
                ("read_campaign(text)", observer),
            ):
                await connection.execute(
                    text(f"GRANT EXECUTE ON FUNCTION {_SCHEMA}.{function} TO {actor}")
                )
        for actor in (publisher, observer):
            engines.append(
                create_async_engine(db_url.set(username=actor), poolclass=NullPool)
            )
        yield tuple(engines)
    finally:
        for engine in reversed(engines):
            await engine.dispose()
        if database_created:
            async with admin.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.execute(text(f"DROP DATABASE {database}"))
        if roles_created:
            async with admin.begin() as connection:
                await connection.execute(text(f"DROP ROLE {publisher}, {observer}"))
        await admin.dispose()


@pytest.fixture(scope="module")
def scheduled_campaign():
    planned = datetime.now(UTC) - timedelta(minutes=1)
    opening = planned.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        days=2
    )
    development_end = opening + timedelta(hours=84)
    validation_start = development_end + timedelta(hours=4)
    return freeze_prospective_devval_campaign_v1(
        ProspectiveDevvalCampaignPlanV1(
            campaign_id="isolated-postgres-commit",
            planned_at=planned,
            declared_source_commit="1" * 40,
            declared_source_tree="2" * 40,
            manifest_sha256="3" * 64,
            origin="https://openapi.okx.com",
            development=PartitionWindow(
                partition=DatasetPartition.DEVELOPMENT,
                start_at=opening,
                end_at=development_end,
            ),
            validation=PartitionWindow(
                partition=DatasetPartition.VALIDATION,
                start_at=validation_start,
                end_at=validation_start + timedelta(hours=84),
            ),
            expected_rows=20_160,
        )
    )


async def test_sql_observer_reads_only_committed_campaign_and_seals_ack(
    evidence_db, scheduled_campaign
):
    _, publisher, observer = evidence_db
    pin = {"sha": scheduled_campaign.sha256}
    payload = scheduled_campaign.payload.decode()
    async with publisher.begin() as write:
        xid = await write.scalar(text("SELECT pg_current_xact_id()::text"))
        await write.execute(_PUBLISH, {**pin, "payload": payload})
        async with observer.begin() as read:
            assert (await read.execute(_READ, pin)).one_or_none() is None
        with pytest.raises(DBAPIError):
            async with observer.begin() as premature:
                await premature.execute(_ACK, pin)
    async with observer.begin() as read:
        prior = (await read.execute(_READ, pin)).mappings().one()
    assert prior["campaign_json"] == payload and prior["publication_xid"] == xid
    assert prior["acknowledged_at"] is None
    async with observer.begin() as write:
        ack_xid = await write.scalar(text("SELECT pg_current_xact_id()::text"))
        await write.execute(_ACK, pin)
    async with observer.begin() as read:
        current = (await read.execute(_READ, pin)).mappings().one()
    assert (
        current["publication_xid"] == xid and current["acknowledgement_xid"] == ack_xid
    )
    assert xid != ack_xid
    assert current["publisher_role_oid"] != current["observer_role_oid"]
    assert (
        current["recorded_at"]
        <= current["acknowledged_at"]
        <= current["database_read_at"]
    )
    assert (
        current["database_read_at"]
        < scheduled_campaign.contract.plan.development.start_at
    )
    with pytest.raises(DBAPIError):
        async with observer.begin() as duplicate:
            await duplicate.execute(_ACK, pin)
    with pytest.raises(DBAPIError):
        async with publisher.begin() as duplicate:
            await duplicate.execute(_PUBLISH, {**pin, "payload": payload})


async def test_sql_custody_rejects_cross_role_and_owner_mutations(
    evidence_db, scheduled_campaign
):
    owner, publisher, observer = evidence_db
    pin = {"sha": scheduled_campaign.sha256}
    payload = scheduled_campaign.payload.decode()
    for engine, query, parameters in (
        (publisher, _READ, pin),
        (publisher, _ACK, pin),
        (observer, _PUBLISH, {**pin, "payload": payload}),
        (observer, text(f"SELECT * FROM {_SCHEMA}.campaigns"), {}),
        (publisher, _PUBLISH, {**pin, "payload": payload + " "}),
    ):
        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.execute(query, parameters)
    async with publisher.begin() as connection:
        await connection.execute(_PUBLISH, {**pin, "payload": payload})
    async with observer.begin() as connection:
        await connection.execute(_ACK, pin)
    for table, column in (
        ("campaigns", "recorded_at"),
        ("acknowledgements", "acknowledged_at"),
    ):
        for query in (
            f"UPDATE {_SCHEMA}.{table} SET {column} = {column}",
            f"DELETE FROM {_SCHEMA}.{table}",
            f"TRUNCATE {_SCHEMA}.{table} CASCADE",
        ):
            with pytest.raises(DBAPIError, match="immutable"):
                async with owner.begin() as connection:
                    await connection.execute(text(query))
