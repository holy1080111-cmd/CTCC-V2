"""Synthetic checks only; these do not establish actual PostgreSQL role proof."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core import native_role_preflight_v1 as preflight


def _row(name, oid, *, database_oid=100, started=None):
    return (
        name,
        name,
        "none",
        oid,
        database_oid,
        started or datetime(2026, 1, 1, tzinfo=UTC),
        170006,
        "127.0.0.1",
        5432,
    )


def test_identity_join_requires_distinct_roles_same_actual_server_and_repeat_writer():
    rows = (
        _row("writer", 11),
        _row("custody", 12),
        _row("frame", 13),
        _row("writer", 11),
    )
    preflight._same_server(*rows)
    for bad in (
        (rows[0], rows[0], rows[2], rows[3]),
        (rows[0], _row("custody", 12, database_oid=101), rows[2], rows[3]),
        (rows[0], rows[1], rows[2], _row("writer", 11, database_oid=101)),
    ):
        with pytest.raises(preflight.NativeRolePreflightError):
            preflight._same_server(*bad)


@pytest.mark.asyncio
async def test_configured_preflight_disposes_both_role_engines_on_fixed_refusal(
    monkeypatch,
):
    calls = []

    class Engine:
        async def dispose(self):
            calls.append("dispose")

    writer = object()
    selected = SimpleNamespace(
        writer_factory=writer,
        custody_engine=Engine(),
        frame_reader_engine=Engine(),
    )
    monkeypatch.setattr(
        preflight.selectors,
        "select_configured_native_demo_factories_v2",
        lambda: selected,
    )

    async def fail(_):
        raise preflight.NativeRolePreflightError("native_role_preflight_denied")

    monkeypatch.setattr(preflight, "verify_selected_native_roles_v1", fail)
    with pytest.raises(
        preflight.NativeRolePreflightError, match="native_role_preflight_denied"
    ):
        await preflight.preflight_configured_native_roles_v1(writer)
    assert calls == ["dispose", "dispose"]


@pytest.mark.asyncio
async def test_configured_preflight_refuses_writer_factory_substitution(monkeypatch):
    calls = []

    class Engine:
        async def dispose(self):
            calls.append("dispose")

    selected = SimpleNamespace(
        writer_factory=object(),
        custody_engine=Engine(),
        frame_reader_engine=Engine(),
    )
    monkeypatch.setattr(
        preflight.selectors,
        "select_configured_native_demo_factories_v2",
        lambda: selected,
    )
    with pytest.raises(preflight.NativeRolePreflightError):
        await preflight.preflight_configured_native_roles_v1(object())
    assert calls == ["dispose", "dispose"]
