from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.trading import TradeLifecycle
from app.domain.entities import LifecycleRecord
from app.domain.enums import LifecycleState
from app.domain.errors import DomainError
from app.domain.state_machine import ensure_transition


class SqlAlchemyLifecycleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _to_record(model: TradeLifecycle) -> LifecycleRecord:
        return LifecycleRecord(
            id=model.id,
            candidate_id=model.candidate_id,
            state=LifecycleState(model.state),
            version=model.version,
            failure_code=model.failure_code,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def _get_model(
        self, lifecycle_id: UUID, *, for_update: bool = False
    ) -> TradeLifecycle | None:
        statement = select(TradeLifecycle).where(TradeLifecycle.id == lifecycle_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get(
        self, lifecycle_id: UUID, *, for_update: bool = False
    ) -> LifecycleRecord | None:
        model = await self._get_model(lifecycle_id, for_update=for_update)
        return None if model is None else self._to_record(model)

    async def add(self, candidate_id: UUID) -> LifecycleRecord:
        model = TradeLifecycle(
            candidate_id=candidate_id,
            state=LifecycleState.CANDIDATE.value,
            version=1,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_record(model)

    async def transition(
        self,
        lifecycle_id: UUID,
        expected_version: int,
        target: LifecycleState,
        failure_code: str | None = None,
    ) -> LifecycleRecord:
        model = await self._get_model(lifecycle_id, for_update=True)
        if model is None:
            raise DomainError(f"lifecycle not found: {lifecycle_id}")
        if model.version != expected_version:
            raise DomainError(
                f"lifecycle version conflict: expected {expected_version}, actual {model.version}"
            )

        current = LifecycleState(model.state)
        ensure_transition(current, target)

        model.state = target.value
        model.version += 1
        model.failure_code = failure_code
        model.last_event_at = datetime.now(timezone.utc)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_record(model)
