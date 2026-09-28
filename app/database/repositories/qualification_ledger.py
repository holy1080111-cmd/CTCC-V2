"""Durable Demo account journal. No settings/global DB, exchange or order API.

All state mutations lock the same scope row BEFORE observing the supplied clock.
Successful persistence is not an authenticated account snapshot or order permit.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import defer

from app.database.models.qualification_ledger import (
    QualificationAccountScope,
    QualificationReservation,
    QualificationReservationTransition,
)
from app.trade_qualification.models import require_aware
from app.trade_qualification.reservations import (
    AccountLedgerClaims,
    LedgerScope,
    LedgerScopeState,
    QualificationLedgerError,
    ReservationReceipt,
    ReservationRequestV2,
    RiskCoverage,
    canonical,
    checked,
    checked_bootstrap,
    checked_reservation_request,
    claim_stamps,
    decode,
    decode_reservation_request,
    digest,
    prepare_reservation,
    reservation_id,
    validate_claims,
)
from app.trade_qualification.service import _plain
from app.trade_qualification.submission_intent import (
    SubmissionExecutionBinding,
    build_submission_intent,
    replay_submission_intent,
)


@dataclass(frozen=True, slots=True, repr=False)
class LedgerCaptureCheckpoint:
    """Observed DB read under the existing account lock; not account authority."""

    state: LedgerScopeState
    observed_at: datetime
    received_at: datetime
    state_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class LedgerBootstrapCheckpoint:
    """Real local journal observation, including explicit uninitialized state.

    A zero local revision is not a zero balance or a complete account. This
    separate type is never accepted by the existing portfolio materializer.
    """

    state: LedgerScopeState
    observed_at: datetime
    received_at: datetime
    state_sha256: str

    @property
    def account_initialized(self):
        return self.state.account_revision > 0

    @property
    def execution_authority(self):
        return False


class QualificationLedgerRepository:
    def __init__(self, session_factory, *, clock):
        self.session_factory = session_factory
        self.clock = clock

    @staticmethod
    def _keys(scope):
        scope = checked(scope, LedgerScope)
        return {
            "environment": scope.environment,
            "account_id": scope.account_id,
            "settlement_currency": scope.settlement_currency,
        }

    async def _locked(self, session, scope, *, create=False):
        keys = self._keys(scope)
        # Account UID, not settlement currency, is the concurrency boundary.
        # A deterministic signed bigint avoids Python's process-randomized hash.
        # Acquire before every scope row lock, including reconciliation/readback.
        lock_bytes = hashlib.sha256(
            (
                "ctcc-qualification-account-v1\0"
                + keys["environment"]
                + "\0"
                + keys["account_id"]
            ).encode("ascii")
        ).digest()[:8]
        await session.execute(
            select(
                func.pg_advisory_xact_lock(
                    int.from_bytes(lock_bytes, "big", signed=True)
                )
            )
        )
        if create:
            await session.execute(
                pg_insert(QualificationAccountScope)
                .values(
                    **keys,
                    account_revision=0,
                    ledger_revision=0,
                )
                .on_conflict_do_nothing()
            )
        row = await session.scalar(
            select(QualificationAccountScope)
            .filter_by(
                **keys,
            )
            .with_for_update()
        )
        if row is None:
            raise QualificationLedgerError("ledger_scope_missing")
        return row

    def _now(self, row):
        now = self.clock()
        if type(now) is not datetime:
            raise QualificationLedgerError("exact_ledger_clock_required")
        now = require_aware(now)
        # The first insert uses a DB default timestamp; replace with explicit
        # injected logical clock before publishing its first claims revision.
        if row.ledger_revision and now < require_aware(row.updated_at):
            raise QualificationLedgerError("ledger_clock_regressed")
        return now

    def _late_guard(self, row, request, claims):
        """Last sampled pre-commit clock, not a promise about commit/fill time.

        Called after receipt construction and SQL flush while still holding the
        scope lock. Failure rolls back every reservation/consume/journal write.
        All objects here were already strictly copied and economically replayed.
        """
        now = self._now(row)
        origin = request.origin
        if not origin.publication_completed_at < now < origin.deadline:
            raise QualificationLedgerError("ledger_late_expiry")
        policy = origin.evidence.pre_evidence.policy
        quote = request.quote
        quote_age = timedelta(seconds=policy.economics.maximum_quote_age_seconds)
        if any(
            now - value > quote_age
            for value in (
                quote.quote_time,
                quote.mark_time,
                quote.funding_time,
                quote.received_at,
            )
        ):
            raise QualificationLedgerError("ledger_late_quote_stale")
        account_age = timedelta(seconds=policy.portfolio.max_age_seconds)
        instrument = request.risk_inputs.instrument
        sources = (*claim_stamps(claims), instrument)
        if any(
            now - item.observed_at > account_age or now - item.received_at > account_age
            for item in sources
        ):
            raise QualificationLedgerError("ledger_late_account_stale")

    @staticmethod
    def _revision(actual, expected):
        if type(expected) is not int or expected < 0 or actual != expected:
            raise QualificationLedgerError("ledger_revision_conflict")

    @staticmethod
    def _claims(row):
        if row.claims_json is None:
            raise QualificationLedgerError("ledger_account_claims_missing")
        claims = decode(row.claims_json, AccountLedgerClaims)
        if digest(claims) != row.claims_sha256:
            raise QualificationLedgerError("ledger_claim_digest_mismatch")
        return claims

    @staticmethod
    def _set_claims(row, claims, now):
        if row.claims_json is not None:
            previous = QualificationLedgerRepository._claims(row)
            if claims.reconciliation_id == previous.reconciliation_id:
                raise QualificationLedgerError("ledger_reconciliation_reused")
            for old, new in zip(
                claim_stamps(previous), claim_stamps(claims), strict=True
            ):
                if (
                    new.observed_at < old.observed_at
                    or new.received_at < old.received_at
                ):
                    raise QualificationLedgerError("ledger_claim_clock_regressed")
        else:
            row.created_at = now
        row.claims_json = canonical(claims)
        row.claims_sha256 = digest(claims)
        row.account_revision += 1
        row.ledger_revision += 1
        row.updated_at = now

    @staticmethod
    def _receipt(row, scope_row):
        scope = LedgerScope(
            environment=row.environment,
            account_id=row.account_id,
            settlement_currency=row.settlement_currency,
        )
        coverage = decode(row.coverage_json, RiskCoverage)
        if (coverage.risk_amount, coverage.margin_amount, coverage.notional_amount) != (
            row.risk_amount,
            row.margin_amount,
            row.notional_amount,
        ):
            raise QualificationLedgerError("ledger_persisted_amount_mismatch")
        return ReservationReceipt(
            scope=scope,
            reservation_id=row.reservation_id,
            original_event_key=row.original_event_key,
            report_id=row.report_id,
            instrument_id=row.instrument_id,
            direction=row.direction,
            correlation_group=row.correlation_group,
            request_sha256=row.request_sha256,
            coverage=coverage,
            state=row.state,
            state_revision=row.state_revision,
            account_revision=scope_row.account_revision,
            ledger_revision=scope_row.ledger_revision,
            created_at=row.created_at,
            updated_at=row.updated_at,
            deadline=row.deadline,
        )

    async def _active(self, session, scope, scope_row):
        rows = (
            await session.scalars(
                select(QualificationReservation)
                .options(defer(QualificationReservation.request_json))
                .filter_by(
                    **self._keys(scope),
                )
                .where(QualificationReservation.state != "reconciled_flat")
                .order_by(
                    QualificationReservation.reservation_id,
                )
                .limit(2049)
            )
        ).all()
        if len(rows) > 2048:
            raise QualificationLedgerError("ledger_active_limit")
        return tuple(self._receipt(row, scope_row) for row in rows)

    async def _state(self, session, scope, row):
        return LedgerScopeState(
            scope=scope,
            account_revision=row.account_revision,
            ledger_revision=row.ledger_revision,
            claims_sha256=row.claims_sha256,
            active=await self._active(session, scope, row),
        )

    async def reconcile_scope(self, claims, *, expected_revision):
        claims = checked(claims, AccountLedgerClaims)
        async with self.session_factory() as session, session.begin():
            row = await self._locked(session, claims.scope, create=True)
            self._revision(row.ledger_revision, expected_revision)
            now = self._now(row)
            validate_claims(claims, now)
            self._set_claims(row, claims, now)
            await session.flush()
            return await self._state(session, claims.scope, row)

    async def read_scope(self, scope):
        scope = checked(scope, LedgerScope)
        async with self.session_factory() as session, session.begin():
            row = await self._locked(session, scope)
            return await self._state(session, scope, row)

    async def read_capture_checkpoint(self, scope):
        """Read DB-owned revision and holds, with actual bounded receipt times.

        No caller claims, revision increments, reconciliation, hold release or
        execution permission. The account lock covers every query in the read.
        A later checkpoint must match before runtime mapping may use this one.
        """
        scope = checked(scope, LedgerScope)
        async with self.session_factory() as session, session.begin():
            row = await self._locked(session, scope)
            observed = self._now(row)
            # Validate persisted claims as well as their externally visible hash.
            if self._claims(row).scope != scope:
                raise QualificationLedgerError("ledger_claim_scope_mismatch")
            state = checked(await self._state(session, scope, row), LedgerScopeState)
            received = self._now(row)
            if received < observed:
                raise QualificationLedgerError("ledger_clock_regressed")
        return LedgerCaptureCheckpoint(
            state=state,
            observed_at=observed,
            received_at=received,
            state_sha256=digest(state),
        )

    async def _bootstrap_checkpoint(self, session, scope, row):
        observed = self._now(row)
        state = checked_bootstrap(
            await self._state(session, scope, row), LedgerScopeState
        )
        if row.account_revision == 0:
            if (
                row.ledger_revision != 0
                or row.claims_json is not None
                or row.claims_sha256 is not None
                or state.active
            ):
                raise QualificationLedgerError("ledger_uninitialized_state_invalid")
        elif self._claims(row).scope != scope:
            raise QualificationLedgerError("ledger_claim_scope_mismatch")
        received = self._now(row)
        if received < observed:
            raise QualificationLedgerError("ledger_clock_regressed")
        return LedgerBootstrapCheckpoint(state, observed, received, digest(state))

    async def initialize_capture_scope(self, scope):
        """Ensure only an unknown local scope, then read it in another session.

        The existing DB0017 zero-revision/null-claims state breaks the cold-start
        dependency without fabricating portfolio claims. Existing claims, holds,
        revisions and tombstones are never overwritten. Commit/readback failure
        gives the caller no successful checkpoint and never grants execution.
        """
        scope = checked_bootstrap(scope, LedgerScope)
        async with self.session_factory() as session, session.begin():
            row = await self._locked(session, scope, create=True)
            before = await self._bootstrap_checkpoint(session, scope, row)
        after = await self.read_bootstrap_checkpoint(scope)
        if before.received_at > after.observed_at:
            raise QualificationLedgerError("ledger_clock_regressed")
        if before.state_sha256 != after.state_sha256:
            raise QualificationLedgerError("ledger_bootstrap_revision_changed")
        return after

    async def read_bootstrap_checkpoint(self, scope):
        """Observe unknown or initialized local state under the existing UID lock."""
        scope = checked_bootstrap(scope, LedgerScope)
        async with self.session_factory() as session, session.begin():
            row = await self._locked(session, scope)
            return await self._bootstrap_checkpoint(session, scope, row)

    @staticmethod
    def _coverage_caps(request, claims, active, coverage):
        # Portfolio computations compare exact unrounded operands. Upward
        # ledger rounding must ALSO fit caps; it cannot oversubscribe an epsilon.
        policy = request.origin.evidence.pre_evidence.policy.portfolio
        equity = Fraction(claims.account.equity)
        pending = {
            item.reservation_id: item for item in claims.account.pending_reservations
        }
        for item in active:
            pending.pop(item.reservation_id, None)
        exposures = [*claims.account.positions, *pending.values()]
        risks = [Fraction(item.risk_amount) for item in exposures]
        margins = [Fraction(item.margin) for item in exposures]
        risks += [Fraction(item.coverage.risk_amount) for item in active]
        margins += [Fraction(item.coverage.margin_amount) for item in active]
        risk, margin = Fraction(coverage.risk_amount), Fraction(coverage.margin_amount)
        if (
            risk > equity * Fraction(policy.risk_per_trade_pct)
            or sum(risks) + risk > equity * Fraction(policy.max_portfolio_risk_pct)
            or sum(margins) + margin
            > equity * Fraction(policy.max_portfolio_margin_pct)
            or margin
            + sum(Fraction(item.margin) for item in pending.values())
            + sum(Fraction(item.coverage.margin_amount) for item in active)
            > Fraction(claims.account.available_margin)
        ):
            raise QualificationLedgerError("ledger_rounded_coverage_exceeds_caps")
        notionals = [
            (
                item.instrument_id,
                item.direction,
                item.correlation_group,
                Fraction(item.notional),
            )
            for item in exposures
        ]
        notionals += [
            (
                item.instrument_id,
                item.direction,
                item.correlation_group,
                Fraction(item.coverage.notional_amount),
            )
            for item in active
        ]
        notional = Fraction(coverage.notional_amount)
        direction = request.origin.candidate.direction
        group = request.risk_inputs.instrument.correlation_group
        if (
            notional > Fraction(policy.max_order_notional)
            or sum(item[3] for item in notionals) + notional
            > Fraction(policy.max_portfolio_notional)
            or sum(item[3] for item in notionals if item[1] == direction) + notional
            > Fraction(policy.max_same_direction_notional)
            or sum(item[3] for item in notionals if item[2] == group) + notional
            > Fraction(policy.max_correlated_notional)
        ):
            raise QualificationLedgerError("ledger_rounded_coverage_exceeds_caps")

    async def reserve(self, request):
        request = checked_reservation_request(request)
        async with self.session_factory() as session, session.begin():
            row = await self._locked(session, request.scope)
            await self._require_compatible_currency(session, request.scope)
            self._revision(row.ledger_revision, request.expected_ledger_revision)
            self._revision(row.account_revision, request.expected_account_revision)
            now = self._now(row)
            key = request.origin.original_event_key
            rid = reservation_id(request.scope, key)
            if await session.get(QualificationReservation, rid) is not None:
                raise QualificationLedgerError("ledger_event_already_recorded")
            claims = self._claims(row)
            active = await self._active(session, request.scope, row)
            self._require_no_uncertain(active)
            await self._require_resolved_submissions(session, active)
            coverage, result = prepare_reservation(
                request, claims, active, observed_at=now
            )
            self._coverage_caps(request, claims, active, coverage)
            record = QualificationReservation(
                **self._keys(request.scope),
                reservation_id=rid,
                original_event_key=key,
                report_id=result.report_id,
                instrument_id=result.instrument_id,
                direction=result.direction,
                correlation_group=request.risk_inputs.instrument.correlation_group,
                request_json=canonical(request),
                request_sha256=digest(request),
                coverage_json=canonical(coverage),
                risk_amount=coverage.risk_amount,
                margin_amount=coverage.margin_amount,
                notional_amount=coverage.notional_amount,
                state="reserved",
                state_revision=1,
                deadline=request.origin.deadline,
                created_at=now,
                updated_at=now,
            )
            row.ledger_revision += 1
            row.updated_at = now
            session.add(record)
            await session.flush()
            self._journal(session, record, None, "risk_reserved", now)
            receipt = self._receipt(record, row)
            await session.flush()
            self._late_guard(row, request, claims)
            return receipt

    @staticmethod
    def _journal(session, record, previous, reason, now, evidence=None):
        session.add(
            QualificationReservationTransition(
                reservation_id=record.reservation_id,
                state_revision=record.state_revision,
                from_state=previous,
                to_state=record.state,
                reason_code=reason,
                evidence_json=evidence,
                occurred_at=now,
            )
        )

    async def _transition(
        self,
        scope,
        key,
        *,
        expected_revision,
        target,
        claims=None,
        record_intent=False,
        execution_binding: SubmissionExecutionBinding | None = None,
    ):
        scope = checked(scope, LedgerScope)
        if claims is not None:
            claims = checked(claims, AccountLedgerClaims)
        async with self.session_factory() as session, session.begin():
            row = await self._locked(session, scope)
            self._revision(row.ledger_revision, expected_revision)
            now = self._now(row)
            record = await session.get(
                QualificationReservation, reservation_id(scope, key)
            )
            if record is None:
                raise QualificationLedgerError("ledger_reservation_missing")
            previous = record.state
            allowed = {
                "consumed": {"reserved"},
                "uncertain": {"reserved", "consumed"},
                "reconciled_flat": {"reserved", "consumed", "uncertain"},
            }
            if previous not in allowed[target]:
                raise QualificationLedgerError("ledger_transition_denied")
            request = decode_reservation_request(record.request_json)
            if digest(request) != record.request_sha256:
                raise QualificationLedgerError("ledger_request_digest_mismatch")
            if target == "consumed":
                await self._require_compatible_currency(session, scope)
                current = self._claims(row)
                if type(request) is ReservationRequestV2:
                    self._revision(
                        row.account_revision, request.expected_account_revision
                    )
                raw = _plain(request)
                raw["risk_inputs"]["account"] = _plain(current.account)
                raw["risk_inputs"]["authority"] = _plain(current.authority)
                all_active = await self._active(session, scope, row)
                self._require_no_uncertain(all_active)
                await self._require_resolved_submissions(session, all_active)
                active = tuple(
                    item
                    for item in all_active
                    if item.reservation_id != record.reservation_id
                )
                # A collector already includes this hold? Verify it then remove
                # it only for candidate re-evaluation (not from durable state).
                current = self._without_self(current, self._receipt(record, row))
                raw["risk_inputs"]["account"] = _plain(current.account)
                fresh_request = type(request).model_validate(raw, strict=True)
                coverage, _ = prepare_reservation(
                    fresh_request, current, active, observed_at=now
                )
                stored = decode(record.coverage_json, RiskCoverage)
                if any(
                    getattr(coverage, name) > getattr(stored, name)
                    for name in ("risk_amount", "margin_amount", "notional_amount")
                ):
                    raise QualificationLedgerError(
                        "ledger_reservation_no_longer_covers_risk"
                    )
                self._coverage_caps(fresh_request, current, active, stored)
            if target == "reconciled_flat":
                if claims is None or claims.scope != scope:
                    raise QualificationLedgerError(
                        "ledger_reconciliation_scope_mismatch"
                    )
                validate_claims(
                    claims, now, newer_than=require_aware(record.updated_at)
                )
                account = claims.account
                if account.positions or account.pending_reservations:
                    raise QualificationLedgerError(
                        "ledger_reconciliation_requires_flat_no_pending"
                    )
                policy = request.origin.evidence.pre_evidence.policy.portfolio
                if any(
                    (now - stamp.observed_at).total_seconds() > policy.max_age_seconds
                    for stamp in claim_stamps(claims)
                ):
                    raise QualificationLedgerError("ledger_reconciliation_stale")
                self._set_claims(row, claims, now)
            else:
                row.ledger_revision += 1
                row.updated_at = now
            record.state = target
            record.state_revision += 1
            record.updated_at = now
            receipt = self._receipt(record, row)
            intent = (
                build_submission_intent(
                    request, receipt, execution_binding=execution_binding
                )
                if record_intent
                else None
            )
            self._journal(
                session,
                record,
                previous,
                "consumed_with_submit_intent" if record_intent else target,
                now,
                intent.canonical_json
                if intent is not None
                else canonical(claims)
                if claims is not None
                else None,
            )
            await session.flush()
            if target == "consumed":
                self._late_guard(row, fresh_request, current)
            return intent if intent is not None else receipt

    @staticmethod
    def _without_self(claims, receipt):
        ids = [item.reservation_id for item in claims.account.pending_reservations]
        if len(set(ids)) != len(ids):
            raise QualificationLedgerError("duplicate_claimed_reservation")
        pending = []
        for item in claims.account.pending_reservations:
            if item.reservation_id != receipt.reservation_id:
                pending.append(item)
                continue
            expected = (
                receipt.instrument_id,
                receipt.direction,
                receipt.scope.settlement_currency,
                receipt.coverage.notional_amount,
                receipt.coverage.margin_amount,
                receipt.coverage.risk_amount,
                receipt.correlation_group,
            )
            if (
                item.instrument_id,
                item.direction,
                item.settlement_currency,
                item.notional,
                item.margin,
                item.risk_amount,
                item.correlation_group,
            ) != expected:
                raise QualificationLedgerError("local_claimed_reservation_conflict")
        raw = _plain(claims)
        raw["account"]["pending_reservations"] = tuple(_plain(item) for item in pending)
        raw["account"]["pending_reservation_count"] = len(pending)
        return AccountLedgerClaims.model_validate(raw, strict=True)

    async def consume_once(self, scope, event_key, *, expected_revision):
        return await self._transition(
            scope, event_key, expected_revision=expected_revision, target="consumed"
        )

    async def consume_with_submission_intent(
        self,
        scope,
        event_key,
        *,
        expected_revision,
        execution_binding: SubmissionExecutionBinding | None = None,
    ):
        """Consume and record derived intent atomically; then independently read.

        No order API is called. A commit/readback exception has an unknown
        outcome; callers must inspect/reconcile, not consume or submit again.
        The existing consume_once API is retained and cannot manufacture this
        newer record retroactively.

        An explicit replayable binding records a v2 exact FOK body in the same
        transition journal. Absence retains v1; invalid bindings roll back and
        never fall back to v1. Neither version grants submission authority.
        """
        intent = await self._transition(
            scope,
            event_key,
            expected_revision=expected_revision,
            target="consumed",
            record_intent=True,
            execution_binding=execution_binding,
        )
        return await self.read_submission_intent(
            scope, event_key, expected_sha256=intent.sha256
        )

    async def read_submission_intent(self, scope, event_key, *, expected_sha256):
        """Historical committed readback only, never crash-recovery dispatch."""
        scope = checked(scope, LedgerScope)
        if (
            type(expected_sha256) is not str
            or len(expected_sha256) != 64
            or any(char not in "0123456789abcdef" for char in expected_sha256)
        ):
            raise QualificationLedgerError("submit_intent_integrity_mismatch")
        async with self.session_factory() as session, session.begin():
            scope_row = await self._locked(session, scope)
            record = await session.get(
                QualificationReservation, reservation_id(scope, event_key)
            )
            if record is None:
                raise QualificationLedgerError("ledger_reservation_missing")
            entries = (
                await session.scalars(
                    select(QualificationReservationTransition)
                    .filter_by(
                        reservation_id=record.reservation_id, to_state="consumed"
                    )
                    .limit(2)
                )
            ).all()
            if (
                len(entries) != 1
                or entries[0].reason_code != "consumed_with_submit_intent"
                or entries[0].from_state != "reserved"
                or entries[0].state_revision != 2
                or entries[0].evidence_json is None
            ):
                raise QualificationLedgerError("submit_intent_missing")
            entry = entries[0]
            request = decode_reservation_request(record.request_json)
            if digest(request) != record.request_sha256:
                raise QualificationLedgerError("ledger_request_digest_mismatch")
            intent, consumed = replay_submission_intent(
                entry.evidence_json, request, expected_sha256=expected_sha256
            )
            current = self._receipt(record, scope_row)
            if (
                any(
                    getattr(consumed, name) != getattr(current, name)
                    for name in (
                        "scope",
                        "reservation_id",
                        "original_event_key",
                        "report_id",
                        "instrument_id",
                        "direction",
                        "correlation_group",
                        "request_sha256",
                        "deadline",
                    )
                )
                or consumed.created_at != current.created_at
                or consumed.updated_at != require_aware(entry.occurred_at)
                or consumed.coverage != current.coverage
                or consumed.ledger_revision > current.ledger_revision
                or consumed.account_revision > current.account_revision
                or current.state == "reserved"
                or consumed.state_revision > current.state_revision
            ):
                raise QualificationLedgerError("submit_intent_journal_mismatch")
            return intent

    async def mark_uncertain(self, scope, event_key, *, expected_revision):
        return await self._transition(
            scope, event_key, expected_revision=expected_revision, target="uncertain"
        )

    @staticmethod
    def _require_no_uncertain(active):
        # A durable unknown result inhibits every event in this exact account
        # scope. It survives restart and only explicit verified reconciliation
        # can retire the existing tombstone; expiry cannot release the hold.
        if any(item.state == "uncertain" for item in active):
            raise QualificationLedgerError("ledger_uncertain_exposure_inhibits_entry")

    @staticmethod
    async def _require_resolved_submissions(session, active):
        from app.database.models.submission_reporting import (
            QualificationSubmissionOutcome,
        )

        consumed = {item.reservation_id for item in active if item.state == "consumed"}
        if not consumed:
            return
        observed = set(
            (
                await session.scalars(
                    select(QualificationSubmissionOutcome.reservation_id).where(
                        QualificationSubmissionOutcome.reservation_id.in_(consumed),
                        QualificationSubmissionOutcome.observation_kind == "initial",
                    )
                )
            ).all()
        )
        # This also covers legacy consumed holds with no DB0018 observation. A
        # commit/response failure cannot depend on a separate EStop transaction.
        if consumed != observed:
            raise QualificationLedgerError(
                "ledger_unresolved_submission_inhibits_entry"
            )

    @staticmethod
    async def _require_compatible_currency(session, scope):
        other = await session.scalar(
            select(QualificationReservation.reservation_id)
            .where(
                QualificationReservation.environment == scope.environment,
                QualificationReservation.account_id == scope.account_id,
                QualificationReservation.settlement_currency
                != scope.settlement_currency,
                QualificationReservation.state != "reconciled_flat",
            )
            .limit(1)
        )
        if other is not None:
            # No conversion or cross-currency portfolio-cap claim is made.
            # Unknown/consumed/reserved exposure in another currency all deny.
            raise QualificationLedgerError("ledger_cross_currency_exposure_unknown")

    async def record_submission_observation(self, scope, event_key, **kwargs):
        """DB0018 observation/spool transaction; never sends an exchange order."""
        from app.database.repositories.submission_reporting import (
            SubmissionReportingRepository,
        )

        return await SubmissionReportingRepository(
            self.session_factory, clock=self.clock
        ).record_observation(scope, event_key, **kwargs)

    async def reconcile_reservation(
        self, scope, event_key, *, claims, expected_revision
    ):
        return await self._transition(
            scope,
            event_key,
            expected_revision=expected_revision,
            target="reconciled_flat",
            claims=claims,
        )
