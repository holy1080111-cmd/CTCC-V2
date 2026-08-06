from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.enums import LifecycleState


@dataclass(frozen=True, slots=True)
class LifecycleRecord:
    id: UUID
    candidate_id: UUID
    state: LifecycleState
    version: int
    failure_code: str | None
    created_at: datetime
    updated_at: datetime
