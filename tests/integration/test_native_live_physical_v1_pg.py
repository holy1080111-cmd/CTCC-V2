"""Isolated PostgreSQL negative-only Live physical cleanup; no venue or Arm.

Uses the existing owner-provisioned synthetic DB0058 fixture. These tests never
construct a physical permit or submit an exchange order.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.exchange.okx import native_live_write_v1 as physical
from tests.integration import test_native_live_ledger_v1_pg as fixture_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_live58_lab = fixture_source._live58_lab


async def test_actual58_physical_negative_stop_commits_and_independently_reads_back(
    _live58_lab,
):
    owner, repository, intent = _live58_lab
    facts = SimpleNamespace(
        session_factory=repository.factory,
        role_pins=repository.pins,
        account_uid=intent.account_uid,
        main_uid=intent.main_uid,
        arm_id=intent.arm_id,
        intent_data=intent,
    )
    await physical._negative_control(facts, stop=True)
    async with owner.connect() as session:
        row = (
            await session.execute(
                text(
                    "SELECT armed,emergency_stop,arm_id,arm_expires_at FROM public.native_live_controls_v1"
                )
            )
        ).one()
    assert tuple(row) == (False, True, None, None)


async def test_actual58_physical_disarm_preserves_stop_without_any_new_authority(
    _live58_lab,
):
    owner, repository, intent = _live58_lab
    facts = SimpleNamespace(
        session_factory=repository.factory,
        role_pins=repository.pins,
        account_uid=intent.account_uid,
        main_uid=intent.main_uid,
        arm_id=intent.arm_id,
        intent_data=intent,
    )
    await physical._negative_control(facts, stop=True)
    await physical._negative_control(facts, stop=False)
    async with owner.connect() as session:
        row = (
            await session.execute(
                text(
                    "SELECT armed,emergency_stop,arm_id,arm_expires_at FROM public.native_live_controls_v1"
                )
            )
        ).one()
    assert tuple(row) == (False, True, None, None)
