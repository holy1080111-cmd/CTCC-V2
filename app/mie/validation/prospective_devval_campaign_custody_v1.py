"""Publish and observe a D/V campaign on separately restricted custody logins."""

from __future__ import annotations

import asyncio
import re
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Literal, TypedDict, Unpack

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.database.repositories.public_runtime_bundle_custody import (
    _closure,
    _native_engine,
)
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import validate_stamps
from app.mie.validation.prospective_development_validation_plan import _ns
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    ProspectiveDevvalCampaignManifestV1,
    verify_prospective_devval_campaign_v1,
)

_ROLE = Literal["publisher", "observer"]
_XID = re.compile(r"[1-9][0-9]{0,19}")
_READ = text("SELECT * FROM ctcc_devval_custody.read_campaign(:sha)")
_PUBLISH = text("SELECT ctcc_devval_custody.publish_campaign(:sha, :payload)")
_ACK = text("SELECT ctcc_devval_custody.acknowledge_campaign(:sha)")
_SESSION = text("""
    SELECT d.oid::bigint AS database_oid, r.oid::bigint AS login_oid,
           SESSION_USER::text AS login_name,
           SESSION_USER = CURRENT_USER AS direct_login,
           pg_catalog.pg_current_xact_id()::text AS xid,
           pg_catalog.current_setting('fsync') = 'on'
             AND pg_catalog.current_setting('full_page_writes') = 'on'
             AND pg_catalog.current_setting('synchronous_commit') = 'on'
             AND pg_catalog.current_setting('session_replication_role') = 'origin'
             AND NOT pg_catalog.pg_is_in_recovery() AS durable_primary,
           EXISTS (
             SELECT 1 FROM pg_catalog.pg_stat_ssl
             WHERE pid = pg_catalog.pg_backend_pid() AND ssl
           ) AS tls
    FROM pg_catalog.pg_database AS d
    CROSS JOIN pg_catalog.pg_roles AS r
    WHERE d.datname = pg_catalog.current_database() AND r.rolname = SESSION_USER
""")


class DevvalCampaignCustodyError(ValueError):
    """Fixed refusals without raw SQL, connection strings, or payload data."""


@dataclass(frozen=True, slots=True)
class _Transaction:
    database_oid: int
    login_oid: int
    xid: str


class _SourceCheckpoint(TypedDict):
    expected_source_commit: str
    expected_source_tree: str
    expected_source_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class DevvalCampaignCommitV1:
    """Publisher transaction locator for the separate observation login."""

    campaign_sha256: str
    database_oid: int
    publisher_role_oid: int
    publication_xid: str


@dataclass(frozen=True, slots=True)
class DevvalCampaignPublicationV1:
    campaign_sha256: str
    plan_sha256: str
    campaign_id: str
    database_oid: int
    recorded_at: datetime
    publisher_role_oid: int
    publication_xid: str
    acknowledged_at: datetime | None
    observer_role_oid: int | None
    acknowledgement_xid: str | None
    database_read_at: datetime
    readback_xid: str
    independently_protected: ClassVar[Literal[False]] = False
    trusted_clock_verified: ClassVar[Literal[False]] = False
    predictive_oos_eligible: ClassVar[Literal[False]] = False
    execution_authority: ClassVar[Literal[False]] = False


def _oid(value) -> bool:
    return type(value) is int and 0 < value < 2**32


def _xid(value) -> bool:
    return (
        type(value) is str
        and _XID.fullmatch(value) is not None
        and 2 < int(value) < 2**64
    )


def _utc(value) -> datetime:
    if type(value) is not datetime or value.utcoffset() is None:
        raise DevvalCampaignCustodyError("devval_custody_server_time_invalid")
    return value.astimezone(UTC)


def _readback(row, *, campaign, payload, pin, transaction, stamps):
    first_open = campaign.plan.development.start_at
    recorded = _utc(row["recorded_at"])
    observed = _utc(row["database_read_at"])
    publisher = row["publisher_role_oid"]
    write_xid = row["publication_xid"]
    acknowledged, observer, ack_xid = (
        row["acknowledged_at"],
        row["observer_role_oid"],
        row["acknowledgement_xid"],
    )
    if (
        row["campaign_sha256"] != pin
        or row["campaign_id"] != campaign.plan.campaign_id
        or type(row["campaign_json"]) is not str
        or row["campaign_json"].encode("utf-8") != payload
        or not _oid(publisher)
        or not _xid(write_xid)
        or write_xid == transaction.xid
        or not campaign.plan.planned_at <= recorded < first_open
        or observed < recorded
        or not stamps[0].utc_ns <= _ns(observed) <= stamps[1].utc_ns
    ):
        raise DevvalCampaignCustodyError("devval_custody_publication_readback_changed")
    if acknowledged is not None:
        acknowledged = _utc(acknowledged)
        if (
            not _oid(observer)
            or observer == publisher
            or not _xid(ack_xid)
            or ack_xid in (write_xid, transaction.xid)
            or not recorded <= acknowledged < first_open
            or observed < acknowledged
        ):
            raise DevvalCampaignCustodyError("devval_custody_ack_readback_changed")
    elif observer is not None or ack_xid is not None:
        raise DevvalCampaignCustodyError("devval_custody_ack_readback_changed")
    return DevvalCampaignPublicationV1(
        campaign_sha256=pin,
        plan_sha256=campaign.plan_sha256,
        campaign_id=campaign.plan.campaign_id,
        database_oid=transaction.database_oid,
        recorded_at=recorded,
        publisher_role_oid=publisher,
        publication_xid=write_xid,
        acknowledged_at=acknowledged,
        observer_role_oid=observer,
        acknowledgement_xid=ack_xid,
        database_read_at=observed,
        readback_xid=transaction.xid,
    )


class DevvalCampaignCustodyRepositoryV1:
    """One publisher or observer login, with separate commit and readback."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        role: _ROLE,
        host: str,
        port: int,
        database: str,
        login: str,
        expected_database_oid: int,
        expected_login_oid: int,
        expected_publisher_role_oid: int,
    ):
        if (
            type(role) is not str
            or role not in ("publisher", "observer")
            or any(
                type(value) is not str or not value for value in (host, database, login)
            )
            or type(port) is not int
            or not 1 <= port <= 65535
            or not _oid(expected_database_oid)
            or not _oid(expected_login_oid)
            or not _oid(expected_publisher_role_oid)
            or (expected_login_oid == expected_publisher_role_oid)
            != (role == "publisher")
            or type(session_factory) is not async_sessionmaker
            or session_factory.class_ is not AsyncSession
            or not set(session_factory.kw) <= {"bind", "autoflush", "expire_on_commit"}
        ):
            raise DevvalCampaignCustodyError("devval_custody_factory_invalid")
        self._role = role
        self._identity = (host, database, login)
        self._port = port
        self._database_oid = expected_database_oid
        self._login_oid = expected_login_oid
        self._publisher_oid = expected_publisher_role_oid
        self._engine = session_factory.kw.get("bind")
        self._require_native_engine()
        self._factory = async_sessionmaker(
            self._engine, expire_on_commit=False, autoflush=False
        )

    def _require_native_engine(self) -> None:
        engine = self._engine
        if type(engine) is not AsyncEngine or not _native_engine(engine):
            raise DevvalCampaignCustodyError("devval_custody_factory_invalid")
        parameters = _closure(engine.sync_engine.pool._creator)["cparams"]
        tls = parameters.get("ssl")
        if (
            (parameters.get("host"), parameters.get("database"), parameters.get("user"))
            != self._identity
            or (engine.url.host, engine.url.database, engine.url.username)
            != self._identity
            or parameters.get("port", 5432) != self._port
            or (engine.url.port or 5432) != self._port
            or type(tls) is not ssl.SSLContext
            or tls.verify_mode != ssl.CERT_REQUIRED
            or tls.check_hostname is not True
        ):
            raise DevvalCampaignCustodyError("devval_custody_tls_identity_invalid")

    async def _transaction(self, statement, parameters):
        try:
            self._require_native_engine()
            async with asyncio.timeout(12), self._factory() as session, session.begin():
                await session.execute(
                    text("SET LOCAL search_path = pg_catalog, pg_temp")
                )
                await session.execute(text("SET LOCAL synchronous_commit = on"))
                await session.execute(text("SET LOCAL statement_timeout = '8s'"))
                await session.execute(text("SET LOCAL lock_timeout = '2s'"))
                facts = (await session.execute(_SESSION)).mappings().one()
                if (
                    facts["direct_login"] is not True
                    or facts["login_name"] != self._identity[2]
                    or facts["tls"] is not True
                    or facts["durable_primary"] is not True
                    or facts["database_oid"] != self._database_oid
                    or facts["login_oid"] != self._login_oid
                    or not _xid(facts["xid"])
                ):
                    raise DevvalCampaignCustodyError("devval_custody_session_invalid")
                transaction = _Transaction(
                    facts["database_oid"], facts["login_oid"], facts["xid"]
                )
                before = native_stamp()
                result = await session.execute(statement, parameters)
                row = result.mappings().one_or_none() if statement is _READ else None
                after = native_stamp()
                stamps = validate_stamps((before, after))
                self._require_native_engine()
            return row, transaction, stamps
        except asyncio.CancelledError:
            raise
        except DevvalCampaignCustodyError:
            raise
        except Exception:  # noqa: BLE001 - never expose SQL, payload, or a DSN
            raise DevvalCampaignCustodyError(
                "devval_custody_transaction_unresolved"
            ) from None

    async def _read(self, campaign, payload, pin):
        row, transaction, stamps = await self._transaction(_READ, {"sha": pin})
        if row is None:
            raise DevvalCampaignCustodyError("devval_custody_publication_missing")
        try:
            result = _readback(
                row,
                campaign=campaign,
                payload=payload,
                pin=pin,
                transaction=transaction,
                stamps=stamps,
            )
        except DevvalCampaignCustodyError:
            raise
        except Exception:  # noqa: BLE001 - Invalid database results stay private.
            raise DevvalCampaignCustodyError(
                "devval_custody_readback_invalid"
            ) from None
        if result.publisher_role_oid != self._publisher_oid:
            raise DevvalCampaignCustodyError("devval_custody_publisher_changed")
        return result, transaction

    @staticmethod
    def _manifest(
        payload,
        *,
        expected_campaign_sha256,
        expected_source_commit,
        expected_source_tree,
        expected_source_manifest_sha256,
    ) -> ProspectiveDevvalCampaignManifestV1:
        try:
            return verify_prospective_devval_campaign_v1(
                payload,
                expected_campaign_sha256=expected_campaign_sha256,
                expected_source_commit=expected_source_commit,
                expected_source_tree=expected_source_tree,
                expected_source_manifest_sha256=expected_source_manifest_sha256,
            )
        except Exception:  # noqa: BLE001 - Validation text may contain the supplied payload.
            raise DevvalCampaignCustodyError(
                "devval_custody_manifest_invalid"
            ) from None

    async def read(
        self,
        payload: bytes,
        *,
        expected_campaign_sha256: str,
        **source_checkpoint: Unpack[_SourceCheckpoint],
    ) -> DevvalCampaignPublicationV1:
        """Read-only resolution with no write or qualification side effect."""
        if self._role != "observer":
            raise DevvalCampaignCustodyError("devval_custody_wrong_login_role")
        campaign = self._manifest(
            payload,
            expected_campaign_sha256=expected_campaign_sha256,
            **source_checkpoint,
        )
        result, _ = await self._read(campaign, payload, expected_campaign_sha256)
        return result

    async def publish(
        self,
        payload: bytes,
        *,
        expected_campaign_sha256: str,
        **source_checkpoint: Unpack[_SourceCheckpoint],
    ) -> DevvalCampaignCommitV1:
        """Commit once. A separate observer login must read and acknowledge it."""
        if self._role != "publisher":
            raise DevvalCampaignCustodyError("devval_custody_wrong_login_role")
        campaign = self._manifest(
            payload,
            expected_campaign_sha256=expected_campaign_sha256,
            **source_checkpoint,
        )
        first_open = campaign.plan.development.start_at
        if native_stamp()["utc_ns"] >= _ns(first_open):
            raise DevvalCampaignCustodyError("devval_custody_pre_window_deadline")
        _, written, _ = await self._transaction(
            _PUBLISH,
            {"sha": expected_campaign_sha256, "payload": payload.decode("utf-8")},
        )
        if native_stamp()["utc_ns"] >= _ns(first_open):
            raise DevvalCampaignCustodyError("devval_custody_publication_unresolved")
        return DevvalCampaignCommitV1(
            expected_campaign_sha256,
            written.database_oid,
            written.login_oid,
            written.xid,
        )

    async def acknowledge(
        self,
        payload: bytes,
        *,
        expected_campaign_sha256: str,
        **source_checkpoint: Unpack[_SourceCheckpoint],
    ) -> DevvalCampaignPublicationV1:
        """Replay the committed publication and append one independent observation."""
        if self._role != "observer":
            raise DevvalCampaignCustodyError("devval_custody_wrong_login_role")
        campaign = self._manifest(
            payload,
            expected_campaign_sha256=expected_campaign_sha256,
            **source_checkpoint,
        )
        before, reader = await self._read(campaign, payload, expected_campaign_sha256)
        first_open = campaign.plan.development.start_at
        if (
            before.acknowledged_at is not None
            or before.publisher_role_oid == reader.login_oid
            or native_stamp()["utc_ns"] >= _ns(first_open)
        ):
            raise DevvalCampaignCustodyError("devval_custody_ack_not_admitted")
        _, written, _ = await self._transaction(_ACK, {"sha": expected_campaign_sha256})
        after, final_reader = await self._read(
            campaign, payload, expected_campaign_sha256
        )
        if (
            after.publisher_role_oid != before.publisher_role_oid
            or after.publication_xid != before.publication_xid
            or after.recorded_at != before.recorded_at
            or reader.login_oid != written.login_oid
            or final_reader.login_oid != written.login_oid
            or reader.database_oid != written.database_oid
            or final_reader.database_oid != written.database_oid
            or after.observer_role_oid != written.login_oid
            or after.acknowledgement_xid != written.xid
            or after.database_read_at >= first_open
            or native_stamp()["utc_ns"] >= _ns(first_open)
        ):
            raise DevvalCampaignCustodyError("devval_custody_ack_unresolved")
        return after
