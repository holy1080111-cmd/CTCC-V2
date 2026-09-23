"""Real PostgreSQL wiring for synthetic control crash-probe seed/readback.

These tests do not kill a process; the Docker harness separately does that.
"""

from uuid import uuid4

import pytest

from scripts.control_durability_probe import (
    confirm_ready_controls,
    seed_controls,
    verify_controls,
)
from tests.integration import test_qualification_ledger_repository as fixtures
from tests.unit.test_demo_control import Clock

database = fixtures.database
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_three_durable_scenarios_verify_with_independent_unarmed_owner(database):
    clock = Clock()
    active_uid = f"987654321{uuid4().int % 10**12:012d}"
    seeded = await seed_controls(
        database[1],
        clock=clock,
        active_uid=active_uid,
    )
    records = seeded.records
    assert [item["scenario"] for item in records] == [
        "arm_intent",
        "estop",
        "cold_estop",
    ]
    assert records[-1]["state"]["owner_sha256"] is None
    assert records[-1]["state"]["credential_session_sha256"] is None
    confirm_ready_controls(seeded, expected_active_uid=active_uid)
    await verify_controls(database[1], records, expected_active_uid=active_uid)
    # Evidence of the old seed cannot be replayed as a second successful restart.
    with pytest.raises(RuntimeError, match="state_changed_after_restart"):
        await verify_controls(database[1], records, expected_active_uid=active_uid)
