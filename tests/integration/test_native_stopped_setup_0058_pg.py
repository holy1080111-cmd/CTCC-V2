"""Actual58/TLS stop SQL with declared synthetic UID; no signed account claim.

No public trigger/role/schema is patched. The human/OKX handoff is outside this
test. Local collection is not actual PostgreSQL or deployment acceptance.
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import native_stopped_control_genesis_0056_v1 as account
from app.core.native_role_witness_0056_v1 import (
    observe_restricted_native_roles_0056_witness_v1,
)
from app.database.repositories import native_legacy_control_genesis_0056_v1 as legacy
from app.domain import native_database_profile_v1 as profiles
from app.domain.source_primitives import sha
from app.trade_qualification.demo_control import ControlScope
from scripts.native_owner_route_preflight_0056_v1 import OwnerRoutePinsV1
from tests.integration.test_native_demo_profile_0058_pg import (
    _actual58_demo_lab as _actual58_demo_lab,  # noqa: PLC0414
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]


async def _prepared(lab):
    owner, selected, _ = lab
    roles = await observe_restricted_native_roles_0056_witness_v1(
        selected, source=ROOT, profile=profiles.DB0058
    )
    # Explicit synthetic ingress only. No row below claims that this UID has
    # been read from OKX, has no exposure, or is a verified flat account.
    scope = ControlScope("demo", "730058")
    async with selected.writer_factory() as session, session.begin():
        created, state, event = await account._stage_genesis(
            session, selected, roles, scope, profile=profiles.DB0058
        )
        assert created is True and state.emergency_stop is True
        assert state.arm_request_id is None
    observed = await account._read_committed_genesis(
        selected, roles, scope, profile=profiles.DB0058
    )
    assert observed.event_sha256 == event
    async with owner.connect() as connection:
        row = (
            await connection.execute(
                text("""SELECT (SELECT oid::bigint FROM pg_database WHERE datname=current_database()),
 (SELECT oid::bigint FROM pg_roles WHERE rolname=current_user),
 (SELECT system_identifier::text FROM pg_control_system())""")
            )
        ).one()
    route = OwnerRoutePinsV1(
        sha((ROOT / "MANIFEST.sha256").read_bytes()),
        os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"],
        sha(row[2].encode()),
        owner.url.host,
        owner.url.port or 5432,
        owner.url.database,
        owner.url.username,
        row[0],
        row[1],
        "0058",
    )
    return owner, selected, roles, event, route


async def test_actual0058_stopped_genesis_and_owner_legacy_seed_replay_same_real_incarnation(
    _actual58_demo_lab,
):
    owner, selected, roles, event, route = await _prepared(_actual58_demo_lab)
    factory = async_sessionmaker(owner, autoflush=False, expire_on_commit=False)
    first = await legacy.seed_owner_legacy_control_genesis_0056_v1(
        factory,
        event_sha256=event,
        pins=route,
        server_incarnation_sha256=roles.server_incarnation_sha256,
        reconcile_only=False,
        profile=profiles.DB0058,
    )
    assert first.database_head == "0058" and first.status == "STOPPED_CREATED"
    assert first.armed is first.startup_admission is first.execution_authority is False
    await legacy.read_restricted_legacy_control_genesis_0056_v1(
        selected, roles, event_sha256=event, expected_at=first.updated_at
    )
    second = await legacy.seed_owner_legacy_control_genesis_0056_v1(
        factory,
        event_sha256=event,
        pins=route,
        server_incarnation_sha256=roles.server_incarnation_sha256,
        reconcile_only=True,
        profile=profiles.DB0058,
    )
    assert second.status == "STOPPED_OBSERVED" and second.updated_at == first.updated_at
    async with selected.writer_factory() as session, session.begin():
        row = (
            await session.execute(
                text(
                    "SELECT armed, emergency_stop, baseline_equity, peak_equity, risk_peak_equity, control_revision FROM demo_automation_state WHERE id=1"
                )
            )
        ).one()
        assert tuple(row) == (False, True, None, None, None, 1)
        assert (
            await session.scalar(
                text("SELECT count(*) FROM demo_policy_counter_initializations_v1")
            )
            == 0
        )
        assert (
            await session.scalar(
                text("SELECT count(*) FROM qualification_reservations")
            )
            == 0
        )


async def test_actual0058_wrong_owner_incarnation_refuses_without_legacy_write(
    _actual58_demo_lab,
):
    owner, selected, _roles, event, route = await _prepared(_actual58_demo_lab)
    factory = async_sessionmaker(owner, autoflush=False, expire_on_commit=False)
    with pytest.raises(legacy.LegacyControlGenesis0056Error):
        await legacy.seed_owner_legacy_control_genesis_0056_v1(
            factory,
            event_sha256=event,
            pins=route,
            server_incarnation_sha256="0" * 64,
            reconcile_only=False,
            profile=profiles.DB0058,
        )
    async with selected.writer_factory() as session, session.begin():
        assert (
            await session.scalar(text("SELECT count(*) FROM demo_automation_state"))
            == 0
        )
        assert (
            await session.scalar(
                text("SELECT emergency_stop FROM demo_account_controls")
            )
            is True
        )
