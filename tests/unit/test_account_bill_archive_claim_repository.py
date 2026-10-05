from __future__ import annotations

import asyncio
import hashlib
import traceback
from datetime import UTC, datetime, timedelta

import pytest

from app.database.models.account_bill_archive_claim import DemoAccountBillArchiveClaim
from app.database.repositories.account_bill_archive_claim import (
    AccountBillArchiveClaimError,
    AccountBillArchiveClaimRepository,
)
from app.trade_qualification.account_bill_archive_acquisition import (
    DiagnosticArchiveJournal,
    DiagnosticArchivePlan,
    encode_journal,
)
from app.trade_qualification.reservations import LedgerScope

NOW = datetime(2026, 10, 6, 1, tzinfo=UTC)


def _plan(uid="123456789", session="synthetic-session"):
    return DiagnosticArchivePlan(
        expected_uid=uid,
        expected_main_uid=uid,
        session_binding_id=session,
        registration_region="us_au",
        origin="https://us.okx.com",
        registration_evidence_sha256="a" * 64,
        year=2024,
        quarter=2,
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_wrong_exact_uid_is_rejected_before_opening_any_db_session():
    def no_session():
        raise AssertionError("database session must not open")

    repo = AccountBillArchiveClaimRepository(no_session, clock=lambda: NOW)
    scope = LedgerScope(account_id="123456789", settlement_currency="USDT")
    with pytest.raises(
        AccountBillArchiveClaimError, match="archive_claim_scope_or_plan_invalid"
    ):
        await repo.claim_once(scope, _plan(uid="987654321"))
    with pytest.raises(
        AccountBillArchiveClaimError, match="archive_claim_read_scope_invalid"
    ):
        await repo.read_claim(
            scope,
            year=2024,
            quarter=2,
            expected_plan_sha256="not-a-digest",
            expected_claim_sha256="b" * 64,
        )


def test_db_primary_key_excludes_session_and_currency():
    table = DemoAccountBillArchiveClaim.__table__
    assert {column.name for column in table.primary_key.columns} == {
        "environment",
        "account_id",
        "year",
        "quarter",
    }
    assert "session_binding_id" not in table.primary_key.columns
    assert "settlement_currency" not in table.primary_key.columns


def _assert_public_error(error, expected_code, private_marker):
    assert type(error) is AccountBillArchiveClaimError
    assert str(error) == expected_code
    assert error.__cause__ is None
    assert error.__context__ is None
    assert private_marker not in "".join(traceback.format_exception(error))


@pytest.mark.asyncio
async def test_claim_session_failure_redacts_private_storage_exception():
    marker = "PRIVATE_UID_AND_SQL_PARAMETERS_MUST_NOT_APPEAR"

    def failed_session():
        raise RuntimeError(marker)

    repo = AccountBillArchiveClaimRepository(failed_session, clock=lambda: NOW)
    scope = LedgerScope(account_id="123456789", settlement_currency="USDT")
    with pytest.raises(AccountBillArchiveClaimError) as caught:
        await repo.claim_once(scope, _plan())
    _assert_public_error(caught.value, "archive_claim_storage_uncertain", marker)


@pytest.mark.asyncio
async def test_claim_clock_failure_is_redacted_before_db_or_attempt():
    marker = "PRIVATE_CLOCK_PROVIDER_DETAIL_MUST_NOT_APPEAR"
    opened = 0

    def no_session():
        nonlocal opened
        opened += 1
        raise AssertionError("clock failure must precede database access")

    def failed_clock():
        raise RuntimeError(marker)

    repo = AccountBillArchiveClaimRepository(no_session, clock=failed_clock)
    scope = LedgerScope(account_id="123456789", settlement_currency="USDT")
    with pytest.raises(AccountBillArchiveClaimError) as caught:
        await repo.claim_once(scope, _plan())
    _assert_public_error(caught.value, "archive_claim_clock_source_invalid", marker)
    assert opened == 0


@pytest.mark.asyncio
async def test_claim_clock_cancellation_propagates_before_db_or_attempt():
    opened = 0

    def no_session():
        nonlocal opened
        opened += 1
        raise AssertionError("clock cancellation must precede database access")

    def cancelled_clock():
        raise asyncio.CancelledError

    repo = AccountBillArchiveClaimRepository(no_session, clock=cancelled_clock)
    scope = LedgerScope(account_id="123456789", settlement_currency="USDT")
    with pytest.raises(asyncio.CancelledError):
        await repo.claim_once(scope, _plan())
    assert opened == 0


@pytest.mark.asyncio
async def test_readback_query_failure_redacts_private_storage_exception():
    marker = "PRIVATE_CLAIM_JSON_AND_SQL_PARAMETERS_MUST_NOT_APPEAR"

    class FailedReadSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def begin(self):
            return self

        async def scalar(self, *_):
            raise RuntimeError(marker)

    repo = AccountBillArchiveClaimRepository(FailedReadSession, clock=lambda: NOW)

    async def no_lock(*_):
        return None

    repo._lock = no_lock
    scope = LedgerScope(account_id="123456789", settlement_currency="USDT")
    with pytest.raises(AccountBillArchiveClaimError) as caught:
        await repo.read_claim(
            scope,
            year=2024,
            quarter=2,
            expected_plan_sha256="a" * 64,
            expected_claim_sha256="b" * 64,
        )
    _assert_public_error(
        caught.value, "archive_claim_readback_storage_uncertain", marker
    )


@pytest.mark.asyncio
async def test_commit_acknowledgement_failure_keeps_uncertain_tombstone_and_denies_retry():
    marker = "PRIVATE_COMMIT_RESPONSE_MUST_NOT_APPEAR"
    state = {"row": None, "adds": 0}

    class Transaction:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self

        async def __aexit__(self, error_type, *_):
            if error_type is None:
                state["row"] = self.session.pending
                raise RuntimeError(marker)
            return False

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def begin(self):
            return Transaction(self)

        async def get(self, *_):
            return state["row"]

        def add(self, row):
            state["adds"] += 1
            self.pending = row

        async def flush(self):
            return None

    repo = AccountBillArchiveClaimRepository(Session, clock=lambda: NOW)

    async def no_lock(*_):
        return None

    repo._lock = no_lock
    scope = LedgerScope(account_id="123456789", settlement_currency="USDT")
    with pytest.raises(AccountBillArchiveClaimError) as caught:
        await repo.claim_once(scope, _plan())
    _assert_public_error(caught.value, "archive_claim_storage_uncertain", marker)
    assert state["row"] is not None
    assert state["adds"] == 1

    with pytest.raises(
        AccountBillArchiveClaimError, match="archive_claim_already_exists"
    ):
        await repo.claim_once(scope, _plan(session="another-session"))
    assert state["row"].session_binding_id == "synthetic-session"
    assert state["adds"] == 1


@pytest.mark.asyncio
async def test_storage_cancellation_is_not_reclassified_or_retried():
    calls = 0

    def cancelled_session():
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    repo = AccountBillArchiveClaimRepository(cancelled_session, clock=lambda: NOW)
    scope = LedgerScope(account_id="123456789", settlement_currency="USDT")
    with pytest.raises(asyncio.CancelledError):
        await repo.claim_once(scope, _plan())
    assert calls == 1


@pytest.mark.asyncio
async def test_readback_keeps_explicit_clock_denial_and_immutable_claim():
    plan = _plan()
    raw = encode_journal(
        DiagnosticArchiveJournal(plan).claim_one_apply(recorded_at=NOW)
    )
    row = DemoAccountBillArchiveClaim(
        environment="demo",
        account_id=plan.expected_uid,
        year=plan.year,
        quarter=plan.quarter,
        settlement_currency="USDT",
        bill_types="all",
        expected_main_uid=plan.expected_main_uid,
        session_binding_id=plan.session_binding_id,
        registration_region=plan.registration_region,
        origin=plan.origin,
        registration_evidence_sha256=plan.registration_evidence_sha256,
        plan_sha256=plan.plan_sha256,
        apply_scope_sha256=plan.apply_scope_sha256,
        claim_sha256=hashlib.sha256(raw).hexdigest(),
        claim_json=raw.decode("utf-8"),
        db_recorded_at=NOW - timedelta(seconds=1),
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def begin(self):
            return self

        async def scalar(self, *_):
            return row

    repo = AccountBillArchiveClaimRepository(Session, clock=lambda: NOW)

    async def no_lock(*_):
        return None

    repo._lock = no_lock
    scope = LedgerScope(account_id=plan.expected_uid, settlement_currency="USDT")
    original_json = row.claim_json
    with pytest.raises(
        AccountBillArchiveClaimError, match="archive_claim_readback_clock_invalid"
    ):
        await repo.read_claim(
            scope,
            year=plan.year,
            quarter=plan.quarter,
            expected_plan_sha256=plan.plan_sha256,
            expected_claim_sha256=row.claim_sha256,
        )
    assert row.claim_json == original_json
