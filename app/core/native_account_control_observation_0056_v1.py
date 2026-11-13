"""Read one exact Demo Control lineage under a restricted DB0056 snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import text

from app.core.native_startup_control_observation_0056_v1 import (
    _require,
    _restricted_read_snapshot_0056,
)
from app.database.models.demo_control import DemoAccountControl
from app.database.repositories.demo_control import ControlObservation
from app.database.repositories.demo_control_current55_v2 import DemoControlRepositoryV2
from app.trade_qualification.demo_control import ControlScope, checked

_CLOCK_SQL = text("SELECT pg_catalog.clock_timestamp()")


@dataclass(frozen=True, slots=True)
class NativeAccountControl0056ObservationV1:
    control: ControlObservation = field(repr=False)
    database_head: Literal["0056"] = field(default="0056", init=False)
    startup_admission: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


async def observe_account_control_0056_v1(selected, scope: ControlScope):
    """Replay the whole immutable prefix; the snapshot confers no held lock."""
    checked(scope, ControlScope)
    async with _restricted_read_snapshot_0056(selected) as session:
        # A transaction-owned snapshot, not the historical write-path `_locked`.
        row = await session.get(
            DemoAccountControl, (scope.environment, scope.account_id)
        )
        state, event_sha256 = await DemoControlRepositoryV2._snapshot(session, row)
        _require(state.scope == scope)
        at = await session.scalar(_CLOCK_SQL)
        _require(type(at) is datetime and at.utcoffset() is not None)
        observed = ControlObservation(state, event_sha256, at.astimezone(UTC))
    return NativeAccountControl0056ObservationV1(observed)
