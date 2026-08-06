from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import asyncio
import pytest
import app.api.routers.dashboard_snapshot as router
import app.database.repositories.persistence as persistence
from app.database.repositories.persistence import PersistenceRepository

class AC:
    def __init__(self, value): self.value = value
    async def __aenter__(self): return self.value
    async def __aexit__(self, *args): return False

class Session:
    def __init__(self): self.added = []
    def begin(self): return AC(self)
    def add(self, value): self.added.append(value)

def snapshot(complete=True):
    status = {
        name: SimpleNamespace(ok=True, timed_out=False)
        for name in router.DASHBOARD_SOURCE_NAMES
    }
    if not complete:
        status["balance"] = SimpleNamespace(ok=False, timed_out=False)
        status["events"] = SimpleNamespace(ok=False, timed_out=True)
    return SimpleNamespace(
        snapshot_id=uuid4(),
        contract_version="1.0",
        generated_at=datetime.now(timezone.utc),
        duration_ms=123,
        complete=complete,
        source_status=status,
    )

@pytest.mark.asyncio
async def test_audit_collects_failed_and_timed_out(monkeypatch):
    captured = {}
    class Repo:
        async def record_dashboard_snapshot_audit(self, **kwargs):
            captured.update(kwargs)
    monkeypatch.setattr(router, "dashboard_snapshot_audit_repository", Repo())
    item = snapshot(False)
    await router._record_snapshot_audit(item)
    assert captured["snapshot_id"] == str(item.snapshot_id)
    assert captured["failed_sources"] == ["balance", "events"]
    assert captured["timed_out_sources"] == ["events"]

@pytest.mark.asyncio
async def test_audit_exception_is_isolated(monkeypatch):
    class Repo:
        async def record_dashboard_snapshot_audit(self, **kwargs):
            raise RuntimeError("audit failed")
    monkeypatch.setattr(router, "dashboard_snapshot_audit_repository", Repo())
    await router._record_snapshot_audit(snapshot())

@pytest.mark.asyncio
async def test_audit_timeout_is_isolated(monkeypatch):
    class Repo:
        async def record_dashboard_snapshot_audit(self, **kwargs):
            await asyncio.sleep(0.1)
    monkeypatch.setattr(router, "dashboard_snapshot_audit_repository", Repo())
    monkeypatch.setattr(router, "DASHBOARD_AUDIT_TIMEOUT_SECONDS", 0.01)
    await router._record_snapshot_audit(snapshot())

@pytest.mark.asyncio
@pytest.mark.parametrize("complete,severity", [(True, "info"), (False, "warning")])
async def test_repository_records_minimal_event(monkeypatch, complete, severity):
    session = Session()
    repo = PersistenceRepository(lambda: AC(session))
    monkeypatch.setattr(persistence, "SystemEvent", lambda **kwargs: kwargs)
    generated_at = datetime(2026, 8, 7, 3, 30, tzinfo=timezone.utc)
    await repo.record_dashboard_snapshot_audit(
        snapshot_id="snapshot-123",
        contract_version="1.0",
        generated_at=generated_at,
        duration_ms=321,
        complete=complete,
        failed_sources=["events", "balance", "events"],
        timed_out_sources=["events"],
    )
    event = session.added[0]
    assert event["event_type"] == "dashboard_snapshot_generated"
    assert event["aggregate_type"] == "dashboard_snapshot"
    assert event["severity"] == severity
    assert event["payload"] == {
        "snapshot_id": "snapshot-123",
        "contract_version": "1.0",
        "generated_at": generated_at.isoformat(),
        "duration_ms": 321,
        "complete": complete,
        "failed_sources": ["balance", "events"],
        "timed_out_sources": ["events"],
    }
