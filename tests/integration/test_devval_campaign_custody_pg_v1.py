"""Opt-in PostgreSQL acceptance of the isolated campaign custody schema."""

from __future__ import annotations

import os
import re
import ssl
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.mie.validation.contracts import DatasetPartition, PartitionWindow
from app.mie.validation.prospective_devval_campaign_custody_v1 import (
    DevvalCampaignCustodyError,
    DevvalCampaignCustodyRepositoryV1,
    _dispatch_document,
    _require_dispatch_claim,
)
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    ProspectiveDevvalCampaignPlanV1,
    freeze_prospective_devval_campaign_v1,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_SQL = (
    Path(__file__).resolve().parents[2]
    / "scripts/sql/gate3_devval_campaign_custody_v1.sql"
)
_DISPATCH_SQL = _SQL.with_name("gate3_devval_campaign_dispatch_claim_v1.sql")
_SCHEMA = "ctcc_devval_custody"
_PUBLISH = text("SELECT ctcc_devval_custody.publish_campaign(:sha, :payload)")
_ACK = text("SELECT ctcc_devval_custody.acknowledge_campaign(:sha)")
_READ = text("SELECT * FROM ctcc_devval_custody.read_campaign(:sha)")
_CLAIM = text("SELECT ctcc_devval_custody.claim_dispatch(:sha,:binding)")
_CLAIM_READ = text("SELECT * FROM ctcc_devval_custody.read_dispatch_claim(:sha)")


@asynccontextmanager
async def _isolated_evidence_db(tls_context=None):
    raw = os.environ.get("CTCC_DEVVAL_CUSTODY_TEST_DATABASE_URL")
    if raw is None:
        pytest.skip("explicit isolated D/V custody PostgreSQL test URL required")
    url = make_url(raw)
    if (
        url.drivername != "postgresql+asyncpg"
        or not url.username
        or not url.database
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
    collector = f"ctcc_dvc_{nonce}"
    connection_options = {"ssl": tls_context} if tls_context is not None else {}
    admin = create_async_engine(
        url, poolclass=NullPool, connect_args=connection_options
    )
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
            await connection.execute(text(f"CREATE ROLE {collector} LOGIN"))
        roles_created = True
        db_url = url.set(database=database)
        owner = create_async_engine(
            db_url, poolclass=NullPool, connect_args=connection_options
        )
        engines.append(owner)
        async with owner.connect() as connection:
            connection_raw = await connection.get_raw_connection()
            await connection_raw.driver_connection.execute(
                _SQL.read_text(encoding="utf-8")
            )
            await connection_raw.driver_connection.execute(
                _DISPATCH_SQL.read_text(encoding="utf-8")
            )
        async with owner.begin() as connection:
            await connection.execute(
                text(
                    f"GRANT USAGE ON SCHEMA {_SCHEMA} TO {publisher}, {observer}, {collector}"
                )
            )
            for function, actor in (
                ("publish_campaign(text,text)", publisher),
                ("acknowledge_campaign(text)", observer),
                ("read_campaign(text)", observer),
                ("read_campaign(text)", collector),
                ("claim_dispatch(text,text)", collector),
                ("read_dispatch_claim(text)", collector),
            ):
                await connection.execute(
                    text(f"GRANT EXECUTE ON FUNCTION {_SCHEMA}.{function} TO {actor}")
                )
        for actor in (publisher, observer, collector):
            engines.append(
                create_async_engine(
                    db_url.set(username=actor),
                    poolclass=NullPool,
                    connect_args=connection_options,
                )
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
                await connection.execute(
                    text(f"DROP ROLE {publisher}, {observer}, {collector}")
                )
        await admin.dispose()


@pytest.fixture
async def evidence_db():
    async with _isolated_evidence_db() as engines:
        yield engines


@pytest.fixture
async def tls_evidence_db():
    ca_file = os.environ.get("CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE")
    if ca_file is None:
        pytest.skip("explicit isolated D/V custody PostgreSQL CA required")
    if not ca_file or not os.environ.get("CTCC_DEVVAL_CUSTODY_TEST_DATABASE_URL"):
        pytest.fail("D/V custody TLS lab requires both a CA and an isolated URL")
    context = ssl.create_default_context(cafile=ca_file)
    async with _isolated_evidence_db(context) as engines:
        yield engines


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
    _, publisher, observer, _ = evidence_db
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
    owner, publisher, observer, _ = evidence_db
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


async def test_sql_campaign_dispatch_has_one_committed_collector_root(
    evidence_db, scheduled_campaign
):
    _, publisher, observer, collector = evidence_db
    pin = {"sha": scheduled_campaign.sha256}
    binding = {**pin, "binding": "4" * 64}
    async with publisher.begin() as write:
        await write.execute(
            _PUBLISH, {**pin, "payload": scheduled_campaign.payload.decode()}
        )
    with pytest.raises(DBAPIError):
        async with collector.begin() as write:
            await write.execute(_CLAIM, binding)
    async with observer.begin() as write:
        await write.execute(_ACK, pin)
    with pytest.raises(DBAPIError):
        async with observer.begin() as wrong_role:
            await wrong_role.execute(_CLAIM, binding)
    async with collector.begin() as write:
        xid = await write.scalar(text("SELECT pg_current_xact_id()::text"))
        await write.execute(_CLAIM, binding)
        async with collector.begin() as read:
            assert (await read.execute(_CLAIM_READ, pin)).one_or_none() is None
    async with collector.begin() as read:
        result = (await read.execute(_CLAIM_READ, pin)).mappings().one()
    assert result["root_binding_sha256"] == binding["binding"]
    assert result["dispatch_xid"] == xid
    assert result["claimed_at"] <= result["database_read_at"]
    with pytest.raises(DBAPIError):
        async with collector.begin() as duplicate:
            await duplicate.execute(_CLAIM, {**pin, "binding": "5" * 64})


async def test_native_tls_repository_observes_dispatch_without_owning_it(
    tls_evidence_db, scheduled_campaign
):
    owner, publisher, observer, collector = tls_evidence_db
    async with owner.connect() as connection:
        database_oid = await connection.scalar(
            text(
                "SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database()"
            )
        )
        role_rows = await connection.execute(
            text("SELECT rolname, oid FROM pg_catalog.pg_roles")
        )
        role_oids = {name: oid for name, oid in role_rows.tuples()}
    assert len({role_oids[engine.url.username] for engine in tls_evidence_db}) == 4

    def repository(engine, role):
        url = engine.url
        return DevvalCampaignCustodyRepositoryV1(
            async_sessionmaker(engine, expire_on_commit=False, autoflush=False),
            role=role,
            host=url.host,
            port=url.port or 5432,
            database=url.database,
            login=url.username,
            expected_database_oid=database_oid,
            expected_login_oid=role_oids[url.username],
            expected_publisher_role_oid=role_oids[publisher.url.username],
            expected_observer_role_oid=role_oids[observer.url.username],
        )

    payload, pin = scheduled_campaign.payload, scheduled_campaign.sha256
    plan = scheduled_campaign.contract.plan
    checkpoint = {
        "expected_campaign_sha256": pin,
        "expected_source_commit": plan.declared_source_commit,
        "expected_source_tree": plan.declared_source_tree,
        "expected_source_manifest_sha256": plan.manifest_sha256,
    }
    binding = {"root_binding_sha256": "4" * 64}
    publication = await repository(publisher, "publisher").publish(
        payload, **checkpoint
    )
    acknowledgement = await repository(observer, "observer").acknowledge(
        payload, **checkpoint
    )
    assert publication.publication_xid == acknowledgement.publication_xid
    assert publication.database_oid == acknowledgement.database_oid == database_oid
    collection = repository(collector, "collector")
    await collection.verify_undispatched(expected_campaign_sha256=pin)
    with pytest.raises(
        DevvalCampaignCustodyError, match="devval_custody_dispatch_missing"
    ):
        await collection.observe_dispatch(payload, **binding, **checkpoint)
    original = await collection.claim_dispatch(
        payload, expected_publication=acknowledgement, **binding, **checkpoint
    )
    _require_dispatch_claim(original, pin)
    _dispatch_document(original)

    fresh = repository(collector, "collector")
    with pytest.raises(
        DevvalCampaignCustodyError, match="devval_custody_dispatch_already_claimed"
    ):
        await fresh.verify_undispatched(expected_campaign_sha256=pin)
    read_publication, observed = await fresh.observe_dispatch(
        payload, **binding, **checkpoint
    )
    assert read_publication.publication_xid == publication.publication_xid
    assert read_publication.acknowledgement_xid == acknowledgement.acknowledgement_xid
    assert observed.dispatch_xid == original.dispatch_xid
    assert observed.claimed_at == original.claimed_at
    assert observed.root_binding_sha256 == original.root_binding_sha256
    assert observed.collector_role_oid == role_oids[collector.url.username]
    assert (
        original.database_read_at
        <= read_publication.database_read_at
        <= observed.database_read_at
    )
    assert (
        len(
            {
                publication.publication_xid,
                acknowledgement.acknowledgement_xid,
                original.dispatch_xid,
                original.readback_xid,
                read_publication.readback_xid,
                observed.readback_xid,
            }
        )
        == 6
    )
    _dispatch_document(observed)
    with pytest.raises(
        DevvalCampaignCustodyError, match="devval_custody_dispatch_owner_required"
    ):
        _require_dispatch_claim(observed, pin)
    with pytest.raises(
        DevvalCampaignCustodyError, match="devval_custody_dispatch_observation_changed"
    ):
        await fresh.observe_dispatch(
            payload, root_binding_sha256="5" * 64, **checkpoint
        )
    async with owner.connect() as connection:
        assert (
            await connection.scalar(
                text(f"SELECT count(*) FROM {_SCHEMA}.dispatch_claims")
            )
            == 1
        )
