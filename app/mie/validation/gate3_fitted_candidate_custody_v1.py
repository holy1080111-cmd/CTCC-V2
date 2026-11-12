"""Restricted PostgreSQL custody for one fitted prospective Gate 3 candidate.

Every instance holds one direct login. The binding is fully replayed before
any write, and a successful commit is observed through a new session. These
records do not establish OOS validity or grant execution authority.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.database.repositories.public_runtime_bundle_custody import (
    _closure,
    _native_engine,
)
from app.domain.source_primitives import canonical, sha
from app.mie.validation.artifact import FrozenGate3Artifact
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_devval_campaign_batch_v1 import (
    DevvalCampaignBatchV1,
)
from app.mie.validation.prospective_devval_campaign_study_v1 import (
    DevvalCampaignStudyV1,
)
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateTrialsV1,
    DevvalLegacyScoresV1,
)
from app.mie.validation.prospective_devval_fitted_binding_v1 import (
    DevvalFittedCandidateBindingV1,
    verify_devval_candidate_binding,
)
from app.mie.validation.prospective_devval_research_blueprint_v1 import (
    DevvalResearchBlueprintV1,
)

_ROLE = Literal["publisher", "observer", "reader", "evaluator"]
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_XID = re.compile(r"[1-9][0-9]{0,19}\Z")
_MAX_BINDING_BYTES = 16_384
_READ_BINDING = text(
    "SELECT b.*, pg_catalog.clock_timestamp() AS database_read_at "
    "FROM public.gate3_fitted_binding_read(:seal) AS b"
)
_APPEND_BINDING = text(
    "SELECT public.gate3_fitted_binding_append(:seal, :binding, :payload)"
)
_READ_ACK = text(
    "SELECT a.*, pg_catalog.clock_timestamp() AS database_read_at "
    "FROM public.gate3_fitted_binding_ack_read(:seal) AS a"
)
_NON_READER_SIGNATURES = (
    "gate3_fitted_binding_append(text,text,text)",
    "gate3_fitted_binding_ack_append(text,text)",
    "gate3_fitted_evaluation_append(text,text,text,text,text,text)",
    "gate3_fitted_evaluation_read(text)",
)
_READER_ONLY = (
    "NOT ("
    + " OR ".join(
        f"pg_catalog.has_function_privilege(SESSION_USER, 'public.{signature}', 'EXECUTE')"
        for signature in _NON_READER_SIGNATURES
    )
    + ")"
)
_RECOVERY_BINDING = text(
    f"SELECT observed.*, {_READER_ONLY} AS fitted_reader_only "
    f"FROM ({_READ_BINDING.text}) AS observed"
)
_RECOVERY_ACK = text(
    f"SELECT observed.*, {_READER_ONLY} AS fitted_reader_only "
    f"FROM ({_READ_ACK.text}) AS observed"
)
_APPEND_ACK = text("SELECT public.gate3_fitted_binding_ack_append(:seal, :binding)")
_READ_EVALUATION = text(
    "SELECT e.*, pg_catalog.clock_timestamp() AS database_read_at "
    "FROM public.gate3_fitted_evaluation_read(:seal) AS e"
)
_APPEND_EVALUATION = text(
    "SELECT public.gate3_fitted_evaluation_append("
    ":seal, :binding, :ack_xid, :receipt, :dataset, :attempt)"
)
_APPEND_EVALUATION_NOT_BEFORE = text(
    "SELECT public.gate3_fitted_evaluation_append("
    ":seal, :binding, :ack_xid, :receipt, :dataset, :attempt) "
    "WHERE pg_catalog.clock_timestamp() >= CAST(:minimum AS timestamptz)"
)
_SESSION = text(
    "SELECT d.oid::bigint AS database_oid, r.oid::bigint AS login_oid, "
    "pg_catalog.current_database() AS database_name, "
    "SESSION_USER::text AS login_name, "
    "SESSION_USER = CURRENT_USER AS direct_login, "
    "pg_catalog.pg_current_xact_id()::text AS xid, "
    "pg_catalog.current_setting('fsync') = 'on' "
    "AND pg_catalog.current_setting('full_page_writes') = 'on' "
    "AND pg_catalog.current_setting('synchronous_commit') = 'on' "
    "AND pg_catalog.current_setting('session_replication_role') = 'origin' "
    "AND NOT pg_catalog.pg_is_in_recovery() AS durable_primary, "
    "EXISTS (SELECT 1 FROM pg_catalog.pg_stat_ssl "
    "WHERE pid = pg_catalog.pg_backend_pid() AND ssl) AS tls "
    "FROM pg_catalog.pg_database AS d CROSS JOIN pg_catalog.pg_roles AS r "
    "WHERE d.datname = pg_catalog.current_database() AND r.rolname = SESSION_USER"
)


class Gate3FittedCustodyError(ValueError):
    """Fixed refusal that never includes SQL, connection or payload bytes."""


@dataclass(frozen=True, slots=True, repr=False)
class FittedCandidateInputsV1:
    binding: FrozenGate3Artifact[DevvalFittedCandidateBindingV1]
    expected_binding_sha256: str
    batch: FrozenGate3Artifact[DevvalCampaignBatchV1]
    study: FrozenGate3Artifact[DevvalCampaignStudyV1]
    trial_result: FrozenGate3Artifact[DevvalCandidateTrialsV1]
    legacy_scores: DevvalLegacyScoresV1
    preregistration: Gate3ProspectivePreregistration
    expected_preregistration_sha256: str
    source_archive_sha256: str
    research_blueprint: DevvalResearchBlueprintV1 | None = None


@dataclass(frozen=True, slots=True)
class FittedCandidatePublicationCommitV1:
    seal_sha256: str
    binding_sha256: str
    database_oid: int
    publisher_oid: int
    publication_xid: str
    predictive_oos_eligible: ClassVar[Literal[False]] = False
    execution_authority: ClassVar[Literal[False]] = False


@dataclass(frozen=True, slots=True)
class FittedCandidateAcknowledgedV1:
    seal_sha256: str
    binding_sha256: str
    database_oid: int
    publisher_oid: int
    publication_xid: str
    recorded_at: datetime
    observer_oid: int
    acknowledgement_xid: str
    acknowledged_at: datetime
    database_read_at: datetime
    ack_sha256: str
    binding: DevvalFittedCandidateBindingV1 = field(repr=False)
    predictive_oos_eligible: ClassVar[Literal[False]] = False
    execution_authority: ClassVar[Literal[False]] = False


@dataclass(frozen=True, slots=True)
class FittedCandidateAckRecoveryV1:
    """Observation of a committed ACK before independently pinning its digest."""

    acknowledgement: FittedCandidateAcknowledgedV1
    independent_ack_pin_verified: ClassVar[Literal[False]] = False
    predictive_oos_eligible: ClassVar[Literal[False]] = False
    execution_authority: ClassVar[Literal[False]] = False


@dataclass(frozen=True, slots=True)
class FittedEvaluationReservationV1:
    seal_sha256: str
    binding_sha256: str
    acknowledgement_xid: str
    receipt_sha256: str
    dataset_sha256: str
    attempt_sha256: str
    database_oid: int
    evaluator_oid: int
    reservation_xid: str
    reserved_at: datetime
    database_read_at: datetime
    predictive_oos_eligible: ClassVar[Literal[False]] = False
    execution_authority: ClassVar[Literal[False]] = False


def _pin(value: object) -> bool:
    return type(value) is str and _SHA.fullmatch(value) is not None


def _oid(value: object) -> bool:
    return type(value) is int and 0 < value <= 4_294_967_295


def _xid(value: object) -> bool:
    return type(value) is str and _XID.fullmatch(value) is not None


def _time(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise Gate3FittedCustodyError("FITTED_CUSTODY_TIME_INVALID")
    return value.astimezone(UTC)


def _verified(inputs: FittedCandidateInputsV1) -> DevvalFittedCandidateBindingV1:
    if (
        type(inputs) is not FittedCandidateInputsV1
        or not _pin(inputs.expected_preregistration_sha256)
        or not _pin(inputs.expected_binding_sha256)
        or not _pin(inputs.source_archive_sha256)
    ):
        raise Gate3FittedCustodyError("FITTED_CUSTODY_INPUT_INVALID")
    binding = inputs.binding
    if (
        type(binding) is not FrozenGate3Artifact
        or type(binding.payload) is not bytes
        or not 1 <= len(binding.payload) <= _MAX_BINDING_BYTES
        or not _pin(binding.sha256)
        or binding.sha256 != inputs.expected_binding_sha256
    ):
        raise Gate3FittedCustodyError("FITTED_CUSTODY_INPUT_INVALID")
    try:
        checked = verify_devval_candidate_binding(
            binding.payload,
            expected_binding_sha256=binding.sha256,
            batch=inputs.batch,
            study=inputs.study,
            trial_result=inputs.trial_result,
            legacy_scores=inputs.legacy_scores,
            preregistration=inputs.preregistration,
            expected_preregistration_sha256=inputs.expected_preregistration_sha256,
            source_archive_sha256=inputs.source_archive_sha256,
            research_blueprint=inputs.research_blueprint,
        )
    except Exception:  # noqa: BLE001 - Original evidence details stay private.
        raise Gate3FittedCustodyError("FITTED_CUSTODY_REPLAY_REFUSED") from None
    if checked != binding.contract:
        raise Gate3FittedCustodyError("FITTED_CUSTODY_REPLAY_REFUSED")
    return checked


class Gate3FittedCandidateCustodyV1:
    """Use one pinned direct PostgreSQL login and no implicit cross-role access."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        role: _ROLE,
        expected_host: str,
        expected_port: int,
        expected_database_name: str,
        expected_login_name: str,
        expected_database_oid: int,
        expected_login_oid: int,
        expected_publisher_oid: int,
        expected_observer_oid: int,
        expected_evaluator_oid: int,
    ) -> None:
        if type(role) is not str or role not in (
            "publisher",
            "observer",
            "reader",
            "evaluator",
        ):
            raise Gate3FittedCustodyError("FITTED_CUSTODY_FACTORY_INVALID")
        role_oid = {
            "publisher": expected_publisher_oid,
            "observer": expected_observer_oid,
            "evaluator": expected_evaluator_oid,
        }
        if (
            any(
                type(value) is not str or not value
                for value in (
                    expected_host,
                    expected_database_name,
                    expected_login_name,
                )
            )
            or type(expected_port) is not int
            or not 1 <= expected_port <= 65535
            or not all(
                _oid(value)
                for value in (
                    expected_database_oid,
                    expected_login_oid,
                    expected_publisher_oid,
                    expected_observer_oid,
                    expected_evaluator_oid,
                )
            )
            or len(
                {expected_publisher_oid, expected_observer_oid, expected_evaluator_oid}
            )
            != 3
            or (role == "reader" and expected_login_oid in role_oid.values())
            or (role != "reader" and expected_login_oid != role_oid[role])
            or type(session_factory) is not async_sessionmaker
            or session_factory.class_ is not AsyncSession
            or not set(session_factory.kw) <= {"bind", "autoflush", "expire_on_commit"}
        ):
            raise Gate3FittedCustodyError("FITTED_CUSTODY_FACTORY_INVALID")
        self._role = role
        self._expected = (
            expected_host,
            expected_port,
            expected_database_name,
            expected_login_name,
            expected_database_oid,
            expected_login_oid,
            expected_publisher_oid,
            expected_observer_oid,
            expected_evaluator_oid,
        )
        self._engine = session_factory.kw.get("bind")
        self._require_engine()
        self._factory = async_sessionmaker(
            self._engine, expire_on_commit=False, autoflush=False
        )

    def _require_engine(self) -> None:
        engine = self._engine
        if type(engine) is not AsyncEngine or not _native_engine(engine):
            raise Gate3FittedCustodyError("FITTED_CUSTODY_ENGINE_INVALID")
        host, port, database, login, *_ = self._expected
        try:
            parameters = _closure(engine.sync_engine.pool._creator)["cparams"]
            tls = parameters.get("ssl")
            if (
                (
                    parameters.get("host"),
                    parameters.get("database"),
                    parameters.get("user"),
                )
                != (host, database, login)
                or parameters.get("port", 5432) != port
                or (
                    engine.url.host,
                    engine.url.port or 5432,
                    engine.url.database,
                    engine.url.username,
                )
                != (host, port, database, login)
                or type(tls) is not ssl.SSLContext
                or tls.verify_mode != ssl.CERT_REQUIRED
                or tls.check_hostname is not True
            ):
                raise Gate3FittedCustodyError("FITTED_CUSTODY_ENGINE_INVALID")
        except Gate3FittedCustodyError:
            raise
        except Exception:  # noqa: BLE001 - Never reveal engine internals.
            raise Gate3FittedCustodyError("FITTED_CUSTODY_ENGINE_INVALID") from None

    async def _transaction(
        self, statement, parameters, *, read: bool, require_write_row: bool = False
    ):
        self._require_engine()
        try:
            async with asyncio.timeout(12), self._factory() as session, session.begin():
                await session.execute(
                    text("SET LOCAL search_path = pg_catalog, pg_temp")
                )
                await session.execute(text("SET LOCAL synchronous_commit = on"))
                await session.execute(text("SET LOCAL statement_timeout = '8s'"))
                await session.execute(text("SET LOCAL lock_timeout = '2s'"))
                if read:
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                facts = (await session.execute(_SESSION)).mappings().one()
                _, _, database, login, db_oid, login_oid, *_ = self._expected
                if (
                    facts["direct_login"] is not True
                    or facts["durable_primary"] is not True
                    or facts["tls"] is not True
                    or facts["database_name"] != database
                    or facts["login_name"] != login
                    or facts["database_oid"] != db_oid
                    or facts["login_oid"] != login_oid
                    or not _xid(facts["xid"])
                ):
                    raise Gate3FittedCustodyError("FITTED_CUSTODY_SESSION_INVALID")
                result = await session.execute(statement, parameters)
                if require_write_row and (read or result.one_or_none() is None):
                    raise Gate3FittedCustodyError(
                        "FITTED_CUSTODY_GLOBAL_TIME_NOT_REACHED"
                    )
                row = result.mappings().one_or_none() if read else None
                self._require_engine()
                xid = facts["xid"]
            return row, xid
        except asyncio.CancelledError:
            raise
        except Gate3FittedCustodyError:
            raise
        except Exception:  # noqa: BLE001 - Ambiguous commits never trigger a retry.
            raise Gate3FittedCustodyError(
                "FITTED_CUSTODY_TRANSACTION_UNRESOLVED"
            ) from None

    def _require_role(self, *allowed: _ROLE) -> None:
        if self._role not in allowed:
            raise Gate3FittedCustodyError("FITTED_CUSTODY_ROLE_REFUSED")

    async def _read_binding(self, inputs, checked, *, reader_only=False):
        row, readback_xid = await self._transaction(
            _RECOVERY_BINDING if reader_only else _READ_BINDING,
            {"seal": inputs.expected_preregistration_sha256},
            read=True,
        )
        if row is None:
            raise Gate3FittedCustodyError("FITTED_CUSTODY_BINDING_MISSING")
        if reader_only and row.get("fitted_reader_only") is not True:
            raise Gate3FittedCustodyError("FITTED_CUSTODY_RECOVERY_ROLE_REFUSED")
        try:
            raw = row["binding_json"].encode("utf-8")
            recorded = _time(row["recorded_at"])
            read_at = _time(row["database_read_at"])
            if (
                not 1 <= len(raw) <= _MAX_BINDING_BYTES
                or raw != inputs.binding.payload
                or row["seal_sha256"] != inputs.expected_preregistration_sha256
                or row["binding_sha256"] != inputs.binding.sha256
                or row["publisher_oid"] != self._expected[6]
                or not _xid(row["publication_xid"])
                or readback_xid == row["publication_xid"]
                or checked.preregistration_sha256 != row["seal_sha256"]
                or not inputs.preregistration.created_at
                <= recorded
                < inputs.preregistration.prospective_holdout.start_at
                or read_at < recorded
            ):
                raise Gate3FittedCustodyError("FITTED_CUSTODY_BINDING_MISMATCH")
            return recorded, read_at, row["publication_xid"], readback_xid
        except Gate3FittedCustodyError:
            raise
        except Exception:  # noqa: BLE001 - SQL types and payload are private.
            raise Gate3FittedCustodyError("FITTED_CUSTODY_BINDING_MISMATCH") from None

    async def _read_acknowledged(self, inputs, checked, *, reader_only=False):
        (
            recorded,
            _,
            publication_xid,
            publication_readback_xid,
        ) = await self._read_binding(inputs, checked, reader_only=reader_only)
        row, ack_readback_xid = await self._transaction(
            _RECOVERY_ACK if reader_only else _READ_ACK,
            {"seal": inputs.expected_preregistration_sha256},
            read=True,
        )
        if row is None:
            raise Gate3FittedCustodyError("FITTED_CUSTODY_ACK_MISSING")
        if reader_only and row.get("fitted_reader_only") is not True:
            raise Gate3FittedCustodyError("FITTED_CUSTODY_RECOVERY_ROLE_REFUSED")
        try:
            acknowledged = _time(row["acknowledged_at"])
            read_at = _time(row["database_read_at"])
            if (
                row["seal_sha256"] != inputs.expected_preregistration_sha256
                or row["binding_sha256"] != inputs.binding.sha256
                or row["observer_oid"] != self._expected[7]
                or not _xid(row["acknowledgement_xid"])
                or publication_xid == row["acknowledgement_xid"]
                or ack_readback_xid
                in (
                    publication_xid,
                    publication_readback_xid,
                    row["acknowledgement_xid"],
                )
                or not recorded
                <= acknowledged
                < inputs.preregistration.prospective_holdout.start_at
                or read_at < acknowledged
            ):
                raise Gate3FittedCustodyError("FITTED_CUSTODY_ACK_MISMATCH")
            document = {
                "schema": "ctcc.mie.fitted_candidate_ack.v1",
                "seal_sha256": inputs.expected_preregistration_sha256,
                "binding_sha256": inputs.binding.sha256,
                "publisher_oid": self._expected[6],
                "publication_xid": publication_xid,
                "recorded_at": recorded.isoformat(),
                "observer_oid": self._expected[7],
                "acknowledgement_xid": row["acknowledgement_xid"],
                "acknowledged_at": acknowledged.isoformat(),
            }
            return FittedCandidateAcknowledgedV1(
                seal_sha256=inputs.expected_preregistration_sha256,
                binding_sha256=inputs.binding.sha256,
                database_oid=self._expected[4],
                publisher_oid=self._expected[6],
                publication_xid=publication_xid,
                recorded_at=recorded,
                observer_oid=self._expected[7],
                acknowledgement_xid=row["acknowledgement_xid"],
                acknowledged_at=acknowledged,
                database_read_at=read_at,
                ack_sha256=sha(canonical(document)),
                binding=checked,
            )
        except Gate3FittedCustodyError:
            raise
        except Exception:  # noqa: BLE001 - SQL types stay private.
            raise Gate3FittedCustodyError("FITTED_CUSTODY_ACK_MISMATCH") from None

    async def publish(
        self, inputs: FittedCandidateInputsV1
    ) -> FittedCandidatePublicationCommitV1:
        self._require_role("publisher")
        checked = _verified(inputs)
        _, xid = await self._transaction(
            _APPEND_BINDING,
            {
                "seal": inputs.expected_preregistration_sha256,
                "binding": inputs.binding.sha256,
                "payload": inputs.binding.payload.decode("utf-8"),
            },
            read=False,
        )
        _, _, observed_xid, _ = await self._read_binding(inputs, checked)
        if observed_xid != xid:
            raise Gate3FittedCustodyError(
                "FITTED_CUSTODY_PUBLICATION_READBACK_MISMATCH"
            )
        return FittedCandidatePublicationCommitV1(
            seal_sha256=inputs.expected_preregistration_sha256,
            binding_sha256=inputs.binding.sha256,
            database_oid=self._expected[4],
            publisher_oid=self._expected[6],
            publication_xid=xid,
        )

    async def acknowledge(
        self, inputs: FittedCandidateInputsV1
    ) -> FittedCandidateAcknowledgedV1:
        self._require_role("observer")
        checked = _verified(inputs)
        await self._read_binding(inputs, checked)
        _, written_xid = await self._transaction(
            _APPEND_ACK,
            {
                "seal": inputs.expected_preregistration_sha256,
                "binding": inputs.binding.sha256,
            },
            read=False,
        )
        observed = await self._read_acknowledged(inputs, checked)
        if observed.acknowledgement_xid != written_xid:
            raise Gate3FittedCustodyError("FITTED_CUSTODY_ACK_READBACK_MISMATCH")
        return observed

    async def read_acknowledged(
        self, inputs: FittedCandidateInputsV1, *, expected_ack_sha256: str
    ) -> FittedCandidateAcknowledgedV1:
        self._require_role("reader", "observer", "evaluator")
        if not _pin(expected_ack_sha256):
            raise Gate3FittedCustodyError("FITTED_CUSTODY_ACK_PIN_INVALID")
        checked = _verified(inputs)
        result = await self._read_acknowledged(inputs, checked)
        if result.ack_sha256 != expected_ack_sha256:
            raise Gate3FittedCustodyError("FITTED_CUSTODY_ACK_PIN_MISMATCH")
        return result

    async def recover_acknowledgement(
        self, inputs: FittedCandidateInputsV1
    ) -> FittedCandidateAckRecoveryV1:
        """Observe a lost ACK receipt using independently pinned inputs and a reader login."""
        self._require_role("reader")
        checked = _verified(inputs)
        acknowledged = await self._read_acknowledged(inputs, checked, reader_only=True)
        return FittedCandidateAckRecoveryV1(acknowledgement=acknowledged)

    async def reserve_evaluation(
        self,
        inputs: FittedCandidateInputsV1,
        *,
        expected_ack_sha256: str,
        receipt_sha256: str,
        dataset_sha256: str,
        not_before: datetime | None = None,
    ) -> FittedEvaluationReservationV1:
        self._require_role("evaluator")
        if not _pin(receipt_sha256) or not _pin(dataset_sha256):
            raise Gate3FittedCustodyError("FITTED_CUSTODY_EVALUATION_PIN_INVALID")
        if not_before is not None and (
            type(not_before) is not datetime
            or not_before.tzinfo is None
            or not_before.utcoffset() != timedelta(0)
        ):
            raise Gate3FittedCustodyError("FITTED_CUSTODY_GLOBAL_TIME_INVALID")
        minimum = _time(not_before) if not_before is not None else None
        acknowledged = await self.read_acknowledged(
            inputs, expected_ack_sha256=expected_ack_sha256
        )
        attempt = sha(secrets.token_bytes(32))
        parameters = {
            "seal": acknowledged.seal_sha256,
            "binding": acknowledged.binding_sha256,
            "ack_xid": acknowledged.acknowledgement_xid,
            "receipt": receipt_sha256,
            "dataset": dataset_sha256,
            "attempt": attempt,
        }
        if minimum is None:
            # Preserve the legacy single-child call and its exact SQL path.
            _, written_xid = await self._transaction(
                _APPEND_EVALUATION, parameters, read=False
            )
        else:
            parameters["minimum"] = minimum
            _, written_xid = await self._transaction(
                _APPEND_EVALUATION_NOT_BEFORE,
                parameters,
                read=False,
                require_write_row=True,
            )
        row, reservation_readback_xid = await self._transaction(
            _READ_EVALUATION, {"seal": acknowledged.seal_sha256}, read=True
        )
        if row is None:
            raise Gate3FittedCustodyError("FITTED_CUSTODY_EVALUATION_MISSING")
        try:
            reserved = _time(row["reserved_at"])
            read_at = _time(row["database_read_at"])
            if (
                row["seal_sha256"] != acknowledged.seal_sha256
                or row["binding_sha256"] != acknowledged.binding_sha256
                or row["acknowledgement_xid"] != acknowledged.acknowledgement_xid
                or row["receipt_sha256"] != receipt_sha256
                or row["dataset_sha256"] != dataset_sha256
                or row["attempt_sha256"] != attempt
                or row["evaluator_oid"] != self._expected[8]
                or not _xid(row["reservation_xid"])
                or row["reservation_xid"] != written_xid
                or reservation_readback_xid == row["reservation_xid"]
                or row["reservation_xid"]
                in (
                    acknowledged.publication_xid,
                    acknowledged.acknowledgement_xid,
                )
                or reserved < acknowledged.acknowledged_at
                or reserved
                < inputs.preregistration.prospective_holdout.first_permitted_access_at
                or (minimum is not None and reserved < minimum)
                or read_at < reserved
            ):
                raise Gate3FittedCustodyError("FITTED_CUSTODY_EVALUATION_MISMATCH")
            return FittedEvaluationReservationV1(
                seal_sha256=acknowledged.seal_sha256,
                binding_sha256=acknowledged.binding_sha256,
                acknowledgement_xid=acknowledged.acknowledgement_xid,
                receipt_sha256=receipt_sha256,
                dataset_sha256=dataset_sha256,
                attempt_sha256=attempt,
                database_oid=self._expected[4],
                evaluator_oid=self._expected[8],
                reservation_xid=row["reservation_xid"],
                reserved_at=reserved,
                database_read_at=read_at,
            )
        except Gate3FittedCustodyError:
            raise
        except Exception:  # noqa: BLE001 - SQL types stay private.
            raise Gate3FittedCustodyError(
                "FITTED_CUSTODY_EVALUATION_MISMATCH"
            ) from None


__all__ = (
    "FittedCandidateAcknowledgedV1",
    "FittedCandidateInputsV1",
    "FittedCandidatePublicationCommitV1",
    "FittedEvaluationReservationV1",
    "Gate3FittedCandidateCustodyV1",
    "Gate3FittedCustodyError",
)
