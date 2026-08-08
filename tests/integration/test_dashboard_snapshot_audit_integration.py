from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.database.models.operations import SystemEvent
from app.database.repositories.persistence import PersistenceRepository


EXPECTED_PAYLOAD_KEYS = {
    "snapshot_id",
    "contract_version",
    "generated_at",
    "duration_ms",
    "complete",
    "failed_sources",
    "timed_out_sources",
}

FORBIDDEN_PAYLOAD_TERMS = {
    "balance",
    "positions",
    "orders",
    "api_token",
    "secret",
    "passphrase",
    "exception",
    "traceback",
}


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("complete", "expected_severity", "failed_sources", "timed_out_sources"),
    [
        (True, "info", [], []),
        (False, "warning", ["events"], ["events"]),
    ],
)
async def test_dashboard_snapshot_audit_persists_minimal_system_event(
    complete: bool,
    expected_severity: str,
    failed_sources: list[str],
    timed_out_sources: list[str],
) -> None:
    settings = get_settings()
    test_engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
    )
    Session = async_sessionmaker(
        test_engine,
        expire_on_commit=False,
        autoflush=False,
    )
    repository = PersistenceRepository(Session)

    snapshot_id = f"integration-{uuid4()}"
    generated_at = datetime.now(timezone.utc)

    async def cleanup() -> None:
        async with Session() as session:
            async with session.begin():
                await session.execute(
                    delete(SystemEvent).where(
                        SystemEvent.event_type
                        == "dashboard_snapshot_generated",
                        SystemEvent.payload["snapshot_id"].astext
                        == snapshot_id,
                    )
                )

    try:
        await cleanup()

        await repository.record_dashboard_snapshot_audit(
            snapshot_id=snapshot_id,
            contract_version="1.0",
            generated_at=generated_at,
            duration_ms=321,
            complete=complete,
            failed_sources=failed_sources,
            timed_out_sources=timed_out_sources,
        )

        async with Session() as session:
            event = (
                await session.scalars(
                    select(SystemEvent).where(
                        SystemEvent.event_type
                        == "dashboard_snapshot_generated",
                        SystemEvent.payload["snapshot_id"].astext
                        == snapshot_id,
                    )
                )
            ).one()

        assert event.aggregate_type == "dashboard_snapshot"
        assert event.severity == expected_severity
        assert event.aggregate_id is None
        assert event.correlation_id is None
        assert event.causation_id is None

        assert set(event.payload) == EXPECTED_PAYLOAD_KEYS
        assert event.payload == {
            "snapshot_id": snapshot_id,
            "contract_version": "1.0",
            "generated_at": generated_at.isoformat(),
            "duration_ms": 321,
            "complete": complete,
            "failed_sources": sorted(set(failed_sources)),
            "timed_out_sources": sorted(set(timed_out_sources)),
        }

        serialized_payload = json.dumps(
            event.payload,
            sort_keys=True,
        ).lower()

        for term in FORBIDDEN_PAYLOAD_TERMS:
            assert term not in serialized_payload

    finally:
        await cleanup()
        await test_engine.dispose()
