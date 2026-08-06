from typing import Protocol
from uuid import UUID

from app.domain.entities import LifecycleRecord
from app.domain.enums import LifecycleState


class LifecycleRepository(Protocol):
    async def get(self, lifecycle_id: UUID, *, for_update: bool = False) -> LifecycleRecord | None: ...
    async def add(self, candidate_id: UUID) -> LifecycleRecord: ...
    async def transition(
        self,
        lifecycle_id: UUID,
        expected_version: int,
        target: LifecycleState,
        failure_code: str | None = None,
    ) -> LifecycleRecord: ...
