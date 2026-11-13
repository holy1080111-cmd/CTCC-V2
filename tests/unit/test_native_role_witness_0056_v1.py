"""New witness tests; preserve the exact historical N234 observer bytes."""

# ruff: noqa: F401, F811 - pytest consumes an imported fixture by parameter name.

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core import native_role_witness_0056_v1 as subject
from tests.unit.test_native_role_preflight_0056_full_diagnostic_v1 import (
    scenario as historical_scenario,
)


@pytest.mark.asyncio
async def test_restricted_witness_uses_original_role_transactions(
    historical_scenario, monkeypatch
):
    state, selected, _owner = historical_scenario
    monkeypatch.setattr(
        subject.old.selectors,
        "_database_route",
        lambda url: ("postgresql+asyncpg", url, 5432, "synthetic"),
    )
    witnessed = await subject.observe_restricted_native_roles_0056_witness_v1(
        selected, source=Path("synthetic")
    )
    assert witnessed.observation.database_head == "0056"
    assert witnessed.observation.admission == "DENY"
    assert witnessed.startup_admission is witnessed.execution_authority is False
    assert subject.old._is_digest(witnessed.server_incarnation_sha256)
    assert subject.old._is_digest(witnessed.role_route_sha256)
    assert ("writer-final", "identity") in state["events"]
    assert "writer" not in repr(witnessed)


def test_runtime_witness_changes_on_restart_and_writer_role_change():
    started = datetime(2026, 1, 1, tzinfo=UTC)
    identity = ("writer", "writer", "none", 201, 93, started, 170011, "db", 5432)
    names = ("writer", "custody", "frame")
    routes = (("postgresql+asyncpg", "db", 5432, "ctcc"),) * 3
    first = subject.runtime_witness_0056(identity, names, routes)
    assert (
        first[0]
        != subject.runtime_witness_0056(
            (*identity[:5], datetime(2026, 1, 2, tzinfo=UTC), *identity[6:]),
            names,
            routes,
        )[0]
    )
    changed_role = subject.runtime_witness_0056(
        (*identity[:3], 202, *identity[4:]), names, routes
    )
    assert first[0] == changed_role[0]
    assert first[1] != changed_role[1]
