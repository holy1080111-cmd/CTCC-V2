from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import re
from typing import AsyncIterator

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.okx_live import OkxLiveExecutionIntent
from app.domain.okx_live import (
    LiveIntentAction,
    LiveIntentStatus,
    OkxLiveExecutionIntentView,
)


class OkxLiveExecutionRepositoryError(RuntimeError):
    pass


class OkxLiveExecutionIntentConflict(OkxLiveExecutionRepositoryError):
    pass


class OkxLiveExecutionIntentReplay(OkxLiveExecutionRepositoryError):
    pass


class OkxLiveExecutionAuthorityBusy(OkxLiveExecutionRepositoryError):
    pass


_SAFE_CODE = re.compile(r"^[a-z0-9_]{1,80}$")
_TRANSITIONS: dict[str, frozenset[str]] = {
    "reserved": frozenset({"acknowledged", "ambiguous", "rejected"}),
    "acknowledged": frozenset({"confirmed", "ambiguous"}),
    "confirmed": frozenset(),
    "ambiguous": frozenset(),
    "rejected": frozenset(),
}
_EXECUTION_ADVISORY_LOCK_ID = 1680010


class OkxLiveExecutionRepository:
    """Persist production-write intent before contacting OKX.

    An existing key is never eligible for automatic re-submission, including
    when the previous process stopped after reserving it but before recording
    an acknowledgement.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    @asynccontextmanager
    async def execution_lock(self) -> AsyncIterator[None]:
        """Serialize Live writes across every API instance using this database."""

        async with self.session_factory() as session:
            acquired = bool(
                await session.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": _EXECUTION_ADVISORY_LOCK_ID},
                )
            )
            if not acquired:
                await session.rollback()
                raise OkxLiveExecutionAuthorityBusy(
                    "okx_live_global_execution_lock_busy"
                )
            try:
                yield
            finally:
                try:
                    await session.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": _EXECUTION_ADVISORY_LOCK_ID},
                    )
                finally:
                    await session.rollback()

    async def reserve_intent(
        self,
        *,
        idempotency_key: str,
        request_hash: str,
        action: LiveIntentAction,
        instrument_id: str,
        client_order_id: str | None = None,
    ) -> OkxLiveExecutionIntentView:
        now = datetime.now(timezone.utc)
        values = {
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "action": action,
            "status": "reserved",
            "instrument_id": instrument_id,
            "client_order_id": client_order_id,
            "exchange_order_id": None,
            "detail_codes": [],
            "created_at": now,
            "updated_at": now,
        }
        async with self.session_factory() as session:
            async with session.begin():
                statement = (
                    pg_insert(OkxLiveExecutionIntent)
                    .values(**values)
                    .on_conflict_do_nothing(
                        index_elements=[OkxLiveExecutionIntent.idempotency_key]
                    )
                    .returning(OkxLiveExecutionIntent.idempotency_key)
                )
                created = (await session.scalar(statement)) is not None
                row = await session.scalar(
                    select(OkxLiveExecutionIntent)
                    .where(OkxLiveExecutionIntent.idempotency_key == idempotency_key)
                    .with_for_update()
                )
                if row is None:
                    raise OkxLiveExecutionRepositoryError(
                        "okx_live_execution_intent_unavailable"
                    )
                if not created:
                    if (
                        row.request_hash != request_hash
                        or row.action != action
                        or row.instrument_id != instrument_id
                        or row.client_order_id != client_order_id
                    ):
                        raise OkxLiveExecutionIntentConflict(
                            "okx_live_idempotency_key_payload_mismatch"
                        )
                    raise OkxLiveExecutionIntentReplay(
                        f"okx_live_idempotency_key_already_used:{row.status}"
                    )
                return self._view(row)

    async def update_intent(
        self,
        idempotency_key: str,
        *,
        status: LiveIntentStatus,
        exchange_order_id: str | None = None,
        detail_codes: list[str] | None = None,
    ) -> OkxLiveExecutionIntentView:
        async with self.session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(OkxLiveExecutionIntent)
                    .where(OkxLiveExecutionIntent.idempotency_key == idempotency_key)
                    .with_for_update()
                )
                if row is None:
                    raise OkxLiveExecutionRepositoryError(
                        "okx_live_execution_intent_not_found"
                    )
                if status != row.status and status not in _TRANSITIONS.get(
                    row.status, frozenset()
                ):
                    raise OkxLiveExecutionRepositoryError(
                        f"okx_live_execution_intent_invalid_transition:{row.status}:{status}"
                    )
                row.status = status
                if exchange_order_id:
                    row.exchange_order_id = exchange_order_id[:100]
                row.detail_codes = self._safe_detail_codes(detail_codes or [])
                row.updated_at = datetime.now(timezone.utc)
                await session.flush()
                return self._view(row)

    async def load_intent(
        self, idempotency_key: str
    ) -> OkxLiveExecutionIntentView | None:
        async with self.session_factory() as session:
            row = await session.get(OkxLiveExecutionIntent, idempotency_key)
        return None if row is None else self._view(row)

    @staticmethod
    def _safe_detail_codes(values: list[str]) -> list[str]:
        safe = sorted({item.strip().lower() for item in values if _SAFE_CODE.fullmatch(item.strip().lower())})
        return safe[:20] or (["unspecified"] if values else [])

    @staticmethod
    def _view(row: OkxLiveExecutionIntent) -> OkxLiveExecutionIntentView:
        return OkxLiveExecutionIntentView(
            idempotency_key=row.idempotency_key,
            request_hash=row.request_hash,
            action=row.action,
            status=row.status,
            instrument_id=row.instrument_id,
            client_order_id=row.client_order_id,
            exchange_order_id=row.exchange_order_id,
            detail_codes=list(row.detail_codes or []),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
