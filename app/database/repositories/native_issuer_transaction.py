"""Pin the native issuer's actual PostgreSQL transaction and installed schema."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import native_locked_demo_control_0056_v1 as locked
from app.core import native_writer_observation_0056_v1 as writer56
from app.database.repositories import account_revision_collector_lineage as lineage
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as catalog55,
)
from app.database.repositories import public_runtime_bundle_custody as custody
from app.database.repositories.demo_control import DemoControlRepository
from app.database.repositories.demo_control_current55_v2 import DemoControlRepositoryV2
from app.domain.source_primitives import canonical, sha
from app.trade_qualification.demo_control import ControlScope

_HEAD = text("SELECT version_num FROM public.alembic_version LIMIT 2")


class NativeIssuerTransactionError(ValueError):
    """Fixed refusal without endpoint, account, or SQL details."""


@dataclass(frozen=True, slots=True)
class NativeIssuerCatalogIdentity:
    head: str
    role_oid: int
    observation_sha256: str


def _require(condition: bool) -> None:
    if condition is not True:
        raise NativeIssuerTransactionError("native_issuer_database_refused")


async def read_actual_native_issuer_transaction(
    session: AsyncSession,
) -> tuple[NativeIssuerCatalogIdentity, tuple]:
    """Return SQL-measured catalog and transaction facts, without granting issuance."""
    try:
        _require(type(session) is AsyncSession and custody._native_engine(session.bind))
        engine = session.bind
        transaction = session.sync_session.get_transaction()

        def require_same_transaction() -> None:
            writer56._require_same_transaction(session, engine, transaction)
            _require(
                session.get_bind(clause=_HEAD) is engine.sync_engine
                and session.get_bind(clause=lineage._META_SQL) is engine.sync_engine
            )

        require_same_transaction()
        rows = (await session.scalars(_HEAD)).all()
        require_same_transaction()
        if rows == ["0055"]:
            observed = await catalog55.verify_actual_issuer_catalog_v2(session)
            _require(
                observed["database_head"] == "0055"
                and observed["native_baseline_admission"] is True
            )
            require_same_transaction()
            metadata = await lineage._metadata(session)
            _require(observed["role_oid"] == metadata[3])
            result = NativeIssuerCatalogIdentity(
                "0055", metadata[3], observed["catalog_sha256"]
            )
        elif rows == ["0056"]:
            route = locked._direct_tls_route(engine)
            before = await writer56._session_identity(session, engine)
            require_same_transaction()
            observed = await writer56.observe_restricted_readwrite_writer_0056_v1(
                session
            )
            require_same_transaction()
            metadata = await lineage._metadata(session)
            require_same_transaction()
            after = await writer56._session_identity(session, engine)
            _require(
                type(observed) is writer56.NativeWriter0056ObservationV1
                and observed.database_head == "0056"
                and observed.execution_authority is False
                and before == after
                and locked._direct_tls_route(engine) == route
                and metadata[:4] == (before[1], before[2], before[3], before[5])
                and metadata[7] == before[7]
                and type(metadata[9]) is datetime
                and metadata[9].tzinfo is UTC
                and metadata[9] >= before[9]
            )
            result = NativeIssuerCatalogIdentity(
                "0056",
                metadata[3],
                sha(
                    canonical(
                        [
                            observed.native_definitions_sha256,
                            observed.actor_catalog_sha256,
                        ]
                    )
                ),
            )
        else:
            _require(False)
        require_same_transaction()
        return result, metadata
    except (asyncio.CancelledError, NativeIssuerTransactionError):
        raise
    except Exception:  # noqa: BLE001 -- never expose environment or SQL values.
        _require(False)


async def read_actual_issuer_control_0056(session: AsyncSession, scope: ControlScope):
    """Replay the inherited Control journal with the exact DB0056 writer."""
    catalog, before = await read_actual_native_issuer_transaction(session)
    _require(catalog.head == "0056" and type(scope) is ControlScope)
    row = await DemoControlRepository._locked(session, scope)
    state, event = await DemoControlRepositoryV2._snapshot(session, row)
    checked, after = await read_actual_native_issuer_transaction(session)
    _require(checked == catalog and before[:9] == after[:9] and before[9] <= after[9])
    _require(state.scope == scope)
    return state, event
