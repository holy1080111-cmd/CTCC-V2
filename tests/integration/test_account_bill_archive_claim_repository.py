"""Real DB0022 claim tests; isolated PostgreSQL DATABASE_URL is mandatory.

No OKX request, account credential, order path, or account-completeness claim.
Synthetic UID tombstones are intentionally not cleaned up within the test DB.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError

from app.database.models.account_bill_archive_claim import DemoAccountBillArchiveClaim
from app.database.repositories.account_bill_archive_claim import (
    AccountBillArchiveClaimError,
    AccountBillArchiveClaimRepository,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification.account_bill_archive_acquisition import (
    DiagnosticArchivePlan,
)
from app.trade_qualification.reservations import LedgerScope
from tests.integration import test_qualification_ledger_repository as fixtures

database = fixtures.database
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def now():
    return datetime.now(UTC)


def plan(uid, *, session):
    return DiagnosticArchivePlan(
        expected_uid=uid,
        expected_main_uid=uid,
        session_binding_id=session,
        registration_region="us_au",
        origin="https://us.okx.com",
        registration_evidence_sha256="a" * 64,
        year=2024,
        quarter=2,
        created_at=now() - timedelta(minutes=1),
    )


async def setup(database):
    uid = f"123456789{uuid4().int % 10**12:012d}"
    scope = LedgerScope(account_id=uid, settlement_currency="USDT")
    await QualificationLedgerRepository(
        database[1], clock=now
    ).initialize_capture_scope(scope)
    return scope


async def test_claim_is_one_across_sessions_and_survives_repository_restart(database):
    scope = await setup(database)
    first = AccountBillArchiveClaimRepository(database[1], clock=now)
    second = AccountBillArchiveClaimRepository(database[1], clock=now)
    plan_a, plan_b = (
        plan(scope.account_id, session="session-a"),
        plan(scope.account_id, session="session-b"),
    )
    outcomes = await asyncio.gather(
        first.claim_once(scope, plan_a),
        second.claim_once(scope, plan_b),
        return_exceptions=True,
    )
    accepted = [item for item in outcomes if not isinstance(item, Exception)]
    rejected = [item for item in outcomes if isinstance(item, Exception)]
    assert len(accepted) == len(rejected) == 1
    assert isinstance(rejected[0], AccountBillArchiveClaimError)
    assert str(rejected[0]) == "archive_claim_already_exists"
    receipt = json.loads(accepted[0].receipt_json)
    assert receipt["apply_state"] == "unknown_one_attempt_tombstone"
    assert receipt["admission"] == "DENY"
    assert receipt["account_complete"] is False
    assert receipt["execution_authority"] is False
    restarted = AccountBillArchiveClaimRepository(database[1], clock=now)
    again = await restarted.read_claim(
        scope,
        year=2024,
        quarter=2,
        expected_plan_sha256=receipt["plan_sha256"],
        expected_claim_sha256=receipt["claim_sha256"],
    )
    assert json.loads(again.receipt_json)["claim_sha256"] == receipt["claim_sha256"]


async def test_future_host_clock_denies_readback_but_preserves_one_attempt_claim(
    database,
):
    scope = await setup(database)
    skewed = AccountBillArchiveClaimRepository(
        database[1], clock=lambda: now() + timedelta(minutes=5)
    )
    with pytest.raises(
        AccountBillArchiveClaimError, match="archive_claim_readback_clock_invalid"
    ):
        await skewed.claim_once(scope, plan(scope.account_id, session="session-a"))
    async with database[1]() as session:
        row = await session.get(
            DemoAccountBillArchiveClaim,
            (scope.environment, scope.account_id, 2024, 2),
        )
        assert row is not None
        assert row.claim_sha256
    restarted = AccountBillArchiveClaimRepository(database[1], clock=now)
    with pytest.raises(
        AccountBillArchiveClaimError, match="archive_claim_already_exists"
    ):
        await restarted.claim_once(scope, plan(scope.account_id, session="session-b"))


@pytest.mark.parametrize("kind", ["update", "delete", "truncate"])
async def test_immutable_archive_tombstone_rejects_mutation(database, kind):
    scope = await setup(database)
    repo = AccountBillArchiveClaimRepository(database[1], clock=now)
    claim = await repo.claim_once(scope, plan(scope.account_id, session="session-a"))
    statements = {
        "update": update(DemoAccountBillArchiveClaim)
        .where(DemoAccountBillArchiveClaim.account_id == scope.account_id)
        .values(session_binding_id="other-session"),
        "delete": delete(DemoAccountBillArchiveClaim).where(
            DemoAccountBillArchiveClaim.account_id == scope.account_id
        ),
        "truncate": text("TRUNCATE demo_account_bill_archive_claims"),
    }
    with pytest.raises(DBAPIError):
        async with database[1]() as session, session.begin():
            await session.execute(statements[kind])
    receipt = json.loads(claim.receipt_json)
    await repo.read_claim(
        scope,
        year=2024,
        quarter=2,
        expected_plan_sha256=receipt["plan_sha256"],
        expected_claim_sha256=receipt["claim_sha256"],
    )
