"""Pure local checks for the opt-in original PG timing observer."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.original_pg_timing_probe import OriginalPgTimingProbe, _kind


def test_timing_probe_is_opt_in_and_does_not_attach_by_default(monkeypatch):
    monkeypatch.delenv("CTCC_ORIGINAL_PG_TIMING_V1", raising=False)
    engine = create_engine("sqlite://")
    probe = OriginalPgTimingProbe(SimpleNamespace(sync_engine=engine))
    with probe, engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    assert probe.receipt_json() is None
    assert not event.contains(engine, "before_cursor_execute", probe._before)
    engine.dispose()


def test_timing_probe_records_only_bounded_roundtrip_numbers(monkeypatch):
    monkeypatch.setenv("CTCC_ORIGINAL_PG_TIMING_V1", "1")
    engine = create_engine("sqlite://")
    probe = OriginalPgTimingProbe(SimpleNamespace(sync_engine=engine))
    with probe, engine.connect() as connection:
        connection.execute(text("SELECT 1 AS synthetic_private_value"))
    result = json.loads(probe.receipt_json())
    assert result["dbapi"]["other"]["count"] == 1
    assert result["dbapi"]["frame"]["count"] == 0
    assert result["unfinished"] == 0
    assert result["overflow"] is False
    assert result["account_complete"] is result["execution_authority"] is False
    assert "synthetic_private_value" not in probe.receipt_json()
    assert not event.contains(engine, "before_cursor_execute", probe._before)
    engine.dispose()


def test_timing_probe_classifies_only_fixed_sql_shapes():
    assert _kind("SELECT public.account_history_original_frame_v1_read($1)") == "frame"
    assert _kind("\nSELECT jsonb_build_object('fixed',1)") == "catalog"
    assert _kind("SELECT private_column FROM private_table") == "other"


@pytest.mark.asyncio
async def test_timing_probe_attaches_to_async_engine_without_connecting(monkeypatch):
    monkeypatch.setenv("CTCC_ORIGINAL_PG_TIMING_V1", "1")
    engine = create_async_engine("postgresql+asyncpg://synthetic@127.0.0.1/ctcc")
    probe = OriginalPgTimingProbe(engine)
    with probe:
        assert event.contains(
            engine.sync_engine, "before_cursor_execute", probe._before
        )
    result = json.loads(probe.receipt_json())
    assert result["dbapi"]["other"]["count"] == 0
    assert not event.contains(
        engine.sync_engine, "before_cursor_execute", probe._before
    )
    await engine.dispose()
