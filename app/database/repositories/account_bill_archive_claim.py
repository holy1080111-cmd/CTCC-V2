"""DB0022 private quarterly archive claim; no OKX request or account authority.

The one-attempt row is immutable and unique across sessions/currencies for the
same Demo UID and calendar quarter. A commit/readback error is uncertain, never
an invitation to reapply. This repository has no signer or network client.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select

from app.database.models.account_bill_archive_claim import DemoAccountBillArchiveClaim
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_bill_archive_acquisition as archive
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MAX_CLAIM_TO_DB = timedelta(minutes=2)


class AccountBillArchiveClaimError(ValueError):
    """Fixed public-safe DB claim reason, without account/SQL content."""


def _fail(code):
    raise AccountBillArchiveClaimError(code)


def _checked(scope, plan):
    checked_bootstrap(scope, LedgerScope)
    if (
        type(plan) is not archive.DiagnosticArchivePlan
        or scope.environment != "demo"
        or plan.expected_uid != scope.account_id
        or len(plan.expected_uid) > 32
        or len(plan.expected_main_uid) > 32
        or plan.bill_types != "all"
    ):
        _fail("archive_claim_scope_or_plan_invalid")
    return scope, plan


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class ArchiveClaimReadback:
    """Separate DB observation of an unresolved archive apply tombstone."""

    receipt_json: bytes

    @property
    def receipt_sha256(self) -> str:
        return _sha(self.receipt_json)

    @property
    def account_complete(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def admission(self) -> str:
        return "DENY"


class AccountBillArchiveClaimRepository:
    def __init__(self, session_factory, *, clock):
        self.session_factory, self.clock = session_factory, clock
        self.ledger = QualificationLedgerRepository(session_factory, clock=clock)

    async def _lock(self, session, scope):
        checked_bootstrap(scope, LedgerScope)
        if session.get_bind().dialect.name != "postgresql":
            _fail("archive_claim_postgresql_required")
        return await self.ledger._locked(session, scope)

    async def claim_once(self, scope, plan) -> ArchiveClaimReadback:
        """Commit only a pre-send unknown tombstone, then independently reread it.

        There is no automatic or manual-request dispatch method here. A second
        caller receives a fixed conflict even if the original commit succeeded
        but its readback failed or a different credential session is used.
        """
        _checked(scope, plan)
        clock_source_invalid = False
        try:
            recorded_at = archive._utc(self.clock())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- injected clock errors may contain private data
            clock_source_invalid = True
        if clock_source_invalid:
            _fail("archive_claim_clock_source_invalid")
        journal = archive.DiagnosticArchiveJournal(plan).claim_one_apply(
            recorded_at=recorded_at
        )
        raw = archive.encode_journal(journal)
        claim_sha = _sha(raw)
        keys = (scope.environment, scope.account_id, plan.year, plan.quarter)
        storage_uncertain = False
        try:
            async with self.session_factory() as session, session.begin():
                await self._lock(session, scope)
                if await session.get(DemoAccountBillArchiveClaim, keys) is not None:
                    _fail("archive_claim_already_exists")
                session.add(
                    DemoAccountBillArchiveClaim(
                        environment=scope.environment,
                        account_id=scope.account_id,
                        year=plan.year,
                        quarter=plan.quarter,
                        settlement_currency=scope.settlement_currency,
                        bill_types="all",
                        expected_main_uid=plan.expected_main_uid,
                        session_binding_id=plan.session_binding_id,
                        registration_region=plan.registration_region,
                        origin=plan.origin,
                        registration_evidence_sha256=plan.registration_evidence_sha256,
                        plan_sha256=plan.plan_sha256,
                        apply_scope_sha256=plan.apply_scope_sha256,
                        claim_sha256=claim_sha,
                        claim_json=raw.decode("utf-8"),
                    )
                )
                await session.flush()
        except asyncio.CancelledError:
            raise
        except AccountBillArchiveClaimError:
            raise
        except Exception:  # noqa: BLE001 -- SQL errors may contain private bind parameters
            # The commit may have succeeded even when its acknowledgement failed.
            # Raise outside this handler so even __context__ cannot expose the
            # SQL/claim JSON. Never retry the one-attempt insert.
            storage_uncertain = True
        if storage_uncertain:
            _fail("archive_claim_storage_uncertain")
        # This is deliberately a new session after commit, not the ORM row.
        return await self.read_claim(
            scope,
            year=plan.year,
            quarter=plan.quarter,
            expected_plan_sha256=plan.plan_sha256,
            expected_claim_sha256=claim_sha,
        )

    async def read_claim(
        self,
        scope,
        *,
        year: int,
        quarter: int,
        expected_plan_sha256: str,
        expected_claim_sha256: str,
    ) -> ArchiveClaimReadback:
        checked_bootstrap(scope, LedgerScope)
        if (
            scope.environment != "demo"
            or type(year) is not int
            or type(quarter) is not int
            or not 2021 <= year <= 9998
            or quarter not in {1, 2, 3, 4}
            or type(expected_plan_sha256) is not str
            or _DIGEST.fullmatch(expected_plan_sha256) is None
            or type(expected_claim_sha256) is not str
            or _DIGEST.fullmatch(expected_claim_sha256) is None
        ):
            _fail("archive_claim_read_scope_invalid")
        storage_uncertain = False
        try:
            async with self.session_factory() as session, session.begin():
                await self._lock(session, scope)
                row = await session.scalar(
                    select(DemoAccountBillArchiveClaim)
                    .filter_by(
                        environment=scope.environment,
                        account_id=scope.account_id,
                        year=year,
                        quarter=quarter,
                    )
                    .with_for_update()
                )
                if row is None:
                    _fail("archive_claim_missing")
                raw = row.claim_json.encode("utf-8")
                if (
                    row.settlement_currency != scope.settlement_currency
                    or row.bill_types != "all"
                    or row.plan_sha256 != expected_plan_sha256
                    or row.claim_sha256 != expected_claim_sha256
                    or _sha(raw) != row.claim_sha256
                ):
                    _fail("archive_claim_readback_mismatch")
                replay_invalid = False
                try:
                    replayed = archive.replay_journal(
                        raw, expected_sha256=row.claim_sha256
                    )
                except archive.ArchiveAcquisitionError:
                    replay_invalid = True
                if replay_invalid:
                    _fail("archive_claim_readback_mismatch")
                plan = replayed.plan
                if (
                    replayed.state != "apply_uncertain"
                    or len(replayed.events) != 1
                    or plan.expected_uid != row.account_id
                    or plan.expected_main_uid != row.expected_main_uid
                    or plan.session_binding_id != row.session_binding_id
                    or plan.registration_region != row.registration_region
                    or plan.origin != row.origin
                    or plan.registration_evidence_sha256
                    != row.registration_evidence_sha256
                    or plan.plan_sha256 != row.plan_sha256
                    or plan.apply_scope_sha256 != row.apply_scope_sha256
                    or plan.year != row.year
                    or plan.quarter != row.quarter
                ):
                    _fail("archive_claim_readback_mismatch")
                clock_invalid = False
                try:
                    readback_at = archive._utc(self.clock())
                    db_at = archive._utc(row.db_recorded_at)
                    claim_at = archive._read_stamp(replayed.events[0]["completed_at"])
                except (archive.ArchiveAcquisitionError, TypeError, ValueError):
                    clock_invalid = True
                if clock_invalid:
                    _fail("archive_claim_readback_clock_invalid")
                if (
                    claim_at > db_at
                    or db_at - claim_at > _MAX_CLAIM_TO_DB
                    or db_at > readback_at
                ):
                    _fail("archive_claim_readback_clock_invalid")
                receipt = archive._canonical(
                    {
                        "schema_version": "ctcc.demo_bill_archive_claim_readback.v1",
                        "plan_sha256": row.plan_sha256,
                        "claim_sha256": row.claim_sha256,
                        "apply_scope_sha256": row.apply_scope_sha256,
                        "db_recorded_at": db_at.isoformat(),
                        "readback_at": readback_at.isoformat(),
                        "apply_state": "unknown_one_attempt_tombstone",
                        "exchange_apply_status": "unknown_not_inferred",
                        "source_authenticity_verified": False,
                        "account_complete": False,
                        "execution_authority": False,
                        "admission": "DENY",
                    }
                )
        except asyncio.CancelledError:
            raise
        except AccountBillArchiveClaimError:
            raise
        except Exception:  # noqa: BLE001 -- DB/readback errors may contain private data
            storage_uncertain = True
        if storage_uncertain:
            _fail("archive_claim_readback_storage_uncertain")
        return ArchiveClaimReadback(receipt)
