"""Durable Demo account journal. No settings/global DB, exchange or order API.

All state mutations lock the same scope row BEFORE observing the supplied clock.
Successful persistence is not an authenticated account snapshot or order permit.
"""

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import defer

from app.database.models.qualification_ledger import (
    QualificationAccountScope,
    QualificationReservation,
    QualificationReservationTransition,
)
from app.database.repositories.demo_control import DemoControlRepository
from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification.demo_control import ControlScope
from app.trade_qualification.event_observation import (
    VERSION as EVENT_OBSERVATION_VERSION,
)
from app.trade_qualification.event_observation import LedgerEventObservation
from app.trade_qualification.models import require_aware
from app.trade_qualification.reservations import (
    AccountLedgerClaims,
    LedgerScope,
    LedgerScopeState,
    QualificationLedgerError,
    ReservationReceipt,
    ReservationRequestV2,
    ReservationRequestV3,
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


def _canonical_json_sha256(value: str) -> str:
    """Hash canonical JSON already emitted or verified by the caller."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


@dataclass(frozen=True, slots=True, repr=False)
class PortfolioLocalCheckpoint:
    """Read-only all-currency/local-storage observation; never a source owner."""

    document_json: bytes
    observed_at: datetime
    received_at: datetime

    @property
    def state_sha256(self):
        return hashlib.sha256(self.document_json).hexdigest()

    @property
    def known_flat(self):
        return not json.loads(self.document_json)["blocking_reasons"]


def _portfolio_checkpoint_wire(value, depth=0):
    if depth > 16:
        raise QualificationLedgerError("portfolio_checkpoint_depth_bound")
    kind = type(value)
    if kind is dict and len(value) <= 4096 and all(type(key) is str for key in value):
        return {
            key: _portfolio_checkpoint_wire(item, depth + 1)
            for key, item in value.items()
        }
    if kind in (tuple, list) and len(value) <= 4096:
        return [_portfolio_checkpoint_wire(item, depth + 1) for item in value]
    if kind is datetime:
        return require_aware(value).isoformat()
    if kind is Decimal and value.is_finite():
        return str(value)
    if value is None or kind in (str, int, bool):
        return value
    raise QualificationLedgerError("portfolio_checkpoint_scalar_invalid")


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

    @staticmethod
    async def _event_rows_locked(session, scope, event_key):
        """Caller holds the UID lock; only this key, every currency/state.

        Two rows are enough to reject a legacy cross-currency collision. Large
        request payloads and the account's lifetime event set are not loaded.
        """
        reservation_id(scope, event_key)  # Exact scope/key validation only.
        return (
            await session.scalars(
                select(QualificationReservation)
                .options(defer(QualificationReservation.request_json))
                .where(
                    QualificationReservation.environment == scope.environment,
                    QualificationReservation.account_id == scope.account_id,
                    QualificationReservation.original_event_key == event_key,
                )
                .order_by(
                    QualificationReservation.settlement_currency,
                    QualificationReservation.reservation_id,
                )
                .limit(2)
            )
        ).all()

    async def read_event_observation(self, scope, event_key):
        """Measured exact UID/event lookup, retaining terminal tombstones.

        Read-only diagnostic, never an absence permit or issuer input. A bound
        reserve independently repeats the same query inside its transaction.
        Missing scopes/conflicts are errors, never an invented empty result.
        """
        scope = checked_bootstrap(scope, LedgerScope)
        reservation_id(scope, event_key)
        monotonic_started = time.monotonic_ns()
        started = self.clock()
        if type(started) is not datetime:
            raise QualificationLedgerError("exact_ledger_clock_required")
        started = require_aware(started)
        async with self.session_factory() as session, session.begin():
            row = await self._locked(session, scope)
            observed = self._now(row)
            records = await self._event_rows_locked(session, scope, event_key)
            if len(records) > 1:
                raise QualificationLedgerError("ledger_uid_event_scope_collision")
            matched = None
            if records:
                record = records[0]
                if (
                    record.environment,
                    record.account_id,
                    record.original_event_key,
                ) != (scope.environment, scope.account_id, event_key):
                    raise QualificationLedgerError("ledger_event_row_scope_invalid")
                actual_scope = LedgerScope(
                    environment=record.environment,
                    account_id=record.account_id,
                    settlement_currency=record.settlement_currency,
                )
                actual_row = (
                    row
                    if actual_scope == scope
                    else await self._locked(session, actual_scope)
                )
                if (
                    actual_row.account_revision < 1
                    or actual_row.ledger_revision < actual_row.account_revision
                    or require_aware(actual_row.updated_at) > observed
                ):
                    raise QualificationLedgerError("ledger_event_actual_scope_invalid")
                matched = self._receipt(record, actual_row)
            received = self._now(row)
            monotonic_received = time.monotonic_ns()
            try:
                result = LedgerEventObservation.model_validate(
                    {
                        "contract_version": EVENT_OBSERVATION_VERSION,
                        "scope": _plain(scope),
                        "original_event_key": event_key,
                        "account_revision": row.account_revision,
                        "ledger_revision": row.ledger_revision,
                        "matched": None if matched is None else _plain(matched),
                        "request_started_at": started,
                        "observed_at": observed,
                        "received_at": received,
                        "monotonic_started_ns": monotonic_started,
                        "monotonic_received_ns": monotonic_received,
                    },
                    strict=True,
                )
                _ = result.canonical_json  # Enforce the bounded detached schema.
            except (ValueError, TypeError):
                raise QualificationLedgerError(
                    "ledger_event_observation_invalid"
                ) from None
        return result

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

    async def read_portfolio_checkpoint(self, scope):
        """B5: observe every currency and legacy unknown under the UID lock.

        Legacy tables have no UID and their old writers do not share this lock.
        Short SHARE table locks make this individual read consistent; before/
        after hashes detect observed changes, not exchange-wide atomicity or
        future writer exclusion. Missing legacy initialization remains unknown.
        """
        from app.database.models.demo_automation import DemoAutomationState
        from app.database.models.okx_demo import (
            OkxDemoAlgoOrderState,
            OkxDemoOrderState,
            OkxDemoPositionState,
            OkxDemoSyncCheckpoint,
        )

        scope = checked_bootstrap(scope, LedgerScope)
        async with self.session_factory() as session, session.begin():
            if session.get_bind().dialect.name != "postgresql":
                raise QualificationLedgerError(
                    "portfolio_checkpoint_postgresql_required"
                )
            selected = await self._locked(session, scope)
            started = self._now(selected)
            rows = (
                await session.scalars(
                    select(QualificationAccountScope)
                    .where(
                        QualificationAccountScope.environment == scope.environment,
                        QualificationAccountScope.account_id == scope.account_id,
                    )
                    .order_by(QualificationAccountScope.settlement_currency)
                    .limit(129)
                    .with_for_update()
                )
            ).all()
            if not rows or len(rows) > 128:
                raise QualificationLedgerError("portfolio_checkpoint_scope_bound")
            states = []
            for row in rows:
                row_scope = LedgerScope(
                    environment=row.environment,
                    account_id=row.account_id,
                    settlement_currency=row.settlement_currency,
                )
                point = await self._bootstrap_checkpoint(session, row_scope, row)
                states.append(_plain(point.state))
            # Every durable consumed intent remains unresolved until its own
            # reservation explicitly transitions to reconciled_flat. No TTL.
            intents = (
                await session.execute(
                    select(
                        QualificationReservation.reservation_id,
                        QualificationReservation.settlement_currency,
                        QualificationReservation.state,
                        QualificationReservationTransition.state_revision,
                        QualificationReservationTransition.reason_code,
                    )
                    .join(
                        QualificationReservationTransition,
                        QualificationReservationTransition.reservation_id
                        == QualificationReservation.reservation_id,
                    )
                    .where(
                        QualificationReservation.environment == scope.environment,
                        QualificationReservation.account_id == scope.account_id,
                        QualificationReservation.state != "reconciled_flat",
                        QualificationReservationTransition.to_state == "consumed",
                    )
                    .order_by(
                        QualificationReservation.reservation_id,
                        QualificationReservationTransition.state_revision,
                    )
                    .limit(2049)
                )
            ).all()
            if len(intents) > 2048:
                raise QualificationLedgerError("portfolio_checkpoint_intent_bound")
            await session.execute(
                text(
                    "LOCK TABLE demo_automation_state, okx_demo_sync_checkpoints, "
                    "okx_demo_order_state, okx_demo_position_state, okx_demo_algo_order_state IN SHARE MODE"
                )
            )
            automation_rows = (
                await session.scalars(select(DemoAutomationState).limit(2))
            ).all()
            sync_rows = (
                await session.scalars(select(OkxDemoSyncCheckpoint).limit(2))
            ).all()
            blocked = set()
            legacy = {"automation": None, "sync_checkpoint": None}
            if len(automation_rows) != 1 or automation_rows[0].id != 1:
                blocked.add("legacy_automation_initialization_unknown")
            else:
                row = automation_rows[0]
                legacy["automation"] = {
                    name: getattr(row, name)
                    for name in (
                        "armed",
                        "emergency_stop",
                        "locked",
                        "lock_reasons",
                        "active_instrument_id",
                        "active_client_order_id",
                        "active_start_equity",
                        "active_started_at",
                        "active_trades",
                        "last_started_at",
                        "last_completed_at",
                        "last_error",
                        "updated_at",
                    )
                }
                if (
                    type(row.active_trades) not in (dict, list)
                    or len(row.active_trades) != 0
                    or any(
                        getattr(row, name) is not None
                        for name in (
                            "active_instrument_id",
                            "active_client_order_id",
                            "active_start_equity",
                            "active_started_at",
                        )
                    )
                ):
                    blocked.add("legacy_tracked_exposure_unattributed")
                if (
                    row.armed is not False
                    or row.locked is not False
                    or type(row.lock_reasons) is not list
                    or row.lock_reasons
                    or row.last_error not in (None, "")
                    or (
                        row.last_started_at is not None
                        and (
                            row.last_completed_at is None
                            or row.last_started_at > row.last_completed_at
                        )
                    )
                ):
                    blocked.add("legacy_inflight_or_uncertain")
            if len(sync_rows) != 1 or sync_rows[0].id != 1:
                blocked.add("legacy_reconciliation_initialization_unknown")
            else:
                row = sync_rows[0]
                legacy["sync_checkpoint"] = {
                    name: getattr(row, name)
                    for name in (
                        "status",
                        "order_count",
                        "position_count",
                        "algo_order_count",
                        "last_error",
                        "reconciled_at",
                        "updated_at",
                    )
                }
                if (
                    row.status != "reconciled"
                    or row.reconciled_at is None
                    or row.last_error not in (None, "")
                    or any(
                        type(getattr(row, name)) is not int or getattr(row, name) != 0
                        for name in (
                            "order_count",
                            "position_count",
                            "algo_order_count",
                        )
                    )
                ):
                    blocked.add("legacy_reconciliation_incomplete_or_exposed")
            for name, model, key, condition in (
                (
                    "positions",
                    OkxDemoPositionState,
                    OkxDemoPositionState.position_key,
                    None,
                ),
                (
                    "orders",
                    OkxDemoOrderState,
                    OkxDemoOrderState.order_id,
                    OkxDemoOrderState.state.not_in(
                        ("filled", "canceled", "mmp_canceled")
                    ),
                ),
                (
                    "algos",
                    OkxDemoAlgoOrderState,
                    OkxDemoAlgoOrderState.algo_order_id,
                    OkxDemoAlgoOrderState.state.not_in(
                        ("canceled", "order_failed", "effective")
                    ),
                ),
            ):
                statement = select(key).select_from(model).order_by(key).limit(2049)
                if condition is not None:
                    statement = statement.where(condition)
                identities = (await session.scalars(statement)).all()
                if len(identities) > 2048:
                    raise QualificationLedgerError("portfolio_checkpoint_legacy_bound")
                legacy[name] = list(identities)
                if identities:
                    blocked.add("legacy_unattributed_active_" + name)
            if any(state["active"] for state in states):
                blocked.add("all_currency_local_holds_unresolved")
            if intents:
                blocked.add("all_currency_local_intents_unresolved")
            payload = json.dumps(
                _portfolio_checkpoint_wire(
                    {
                        "schema_version": "ctcc.portfolio_local_checkpoint.v1",
                        "environment": scope.environment,
                        "account_id": scope.account_id,
                        "requested_settlement_currency": scope.settlement_currency,
                        "currency_states": states,
                        "unresolved_intents": [list(item) for item in intents],
                        "legacy": legacy,
                        "blocking_reasons": sorted(blocked),
                        "future_writer_exclusion": False,
                        "execution_authority": False,
                    }
                ),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode()
            if len(payload) > 262144:
                raise QualificationLedgerError("portfolio_checkpoint_byte_bound")
            received = self._now(selected)
            if received < started:
                raise QualificationLedgerError("portfolio_checkpoint_clock_reversed")
            return PortfolioLocalCheckpoint(payload, started, received)

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
        return await self._reserve(request)

    async def reserve_control_bound(self, request, *, control_expectation):
        """Record current durable control with the hold; still DENY admission."""
        return await self._reserve(
            request, control_expectation=bound.freeze_expectation(control_expectation)
        )

    async def _control_locked(self, session, scope, expectation, *, request=None):
        state, event_sha = await DemoControlRepository.read_in_transaction(
            session, ControlScope(scope.environment, scope.account_id)
        )
        # Reentrant advisory lock in the SAME transaction; no nested session.
        # The shared order is UID -> control row -> qualification scope row.
        row = await self._locked(session, scope)
        bound.guard_current_control(
            state, event_sha, expectation, scope, now=self._now(row), request=request
        )
        return row, state, event_sha

    async def _reserve(self, request, *, control_expectation=None):
        request = checked_reservation_request(request)
        # Request models are frozen. Serialize and hash once before acquiring
        # the account-scoped database lock; canonical() already validates the
        # exact request contract and its size.
        request_json = canonical(request)
        request_sha256 = _canonical_json_sha256(request_json)
        async with self.session_factory() as session, session.begin():
            if control_expectation is None:
                row = await self._locked(session, request.scope)
            else:
                row, control_state, control_event = await self._control_locked(
                    session, request.scope, control_expectation, request=request
                )
                if await self._event_rows_locked(
                    session, request.scope, request.origin.original_event_key
                ):
                    raise QualificationLedgerError("ledger_uid_event_already_recorded")
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
                request_json=request_json,
                request_sha256=request_sha256,
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
            receipt = self._receipt(record, row)
            evidence = (
                None
                if control_expectation is None
                else bound.build_reserved_evidence(
                    request,
                    receipt,
                    control_state,
                    control_event,
                    checked_at=now,
                )
            )
            self._journal(
                session,
                record,
                None,
                "risk_reserved" if evidence is None else bound.RESERVED_REASON,
                now,
                evidence,
            )
            await session.flush()
            self._late_guard(row, request, claims)
            if control_expectation is not None:
                bound.guard_current_control(
                    control_state,
                    control_event,
                    control_expectation,
                    request.scope,
                    now=self._now(row),
                    request=request,
                )
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
        control_expectation=None,
    ):
        scope = checked(scope, LedgerScope)
        if claims is not None:
            claims = checked(claims, AccountLedgerClaims)
        async with self.session_factory() as session, session.begin():
            if control_expectation is None:
                row = await self._locked(session, scope)
            else:
                row, control_state, control_event = await self._control_locked(
                    session, scope, control_expectation
                )
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
            request_json = record.request_json
            request = decode_reservation_request(request_json)
            if _canonical_json_sha256(request_json) != record.request_sha256:
                raise QualificationLedgerError("ledger_request_digest_mismatch")
            if target == "consumed":
                reserved_entry = await session.scalar(
                    select(QualificationReservationTransition).filter_by(
                        reservation_id=record.reservation_id, state_revision=1
                    )
                )
                is_bound = reserved_entry is not None and (
                    reserved_entry.reason_code == bound.RESERVED_REASON
                    or reserved_entry.evidence_json is not None
                )
                if control_expectation is None and is_bound:
                    raise QualificationLedgerError("bound_control_consume_required")
                if control_expectation is not None:
                    if (
                        not is_bound
                        or reserved_entry.reason_code != bound.RESERVED_REASON
                        or reserved_entry.from_state is not None
                        or reserved_entry.to_state != "reserved"
                        or reserved_entry.occurred_at != record.created_at
                        or not record_intent
                        or execution_binding is None
                    ):
                        raise QualificationLedgerError(
                            "bound_control_reservation_required"
                        )
                    reserved_receipt, reserved_control, reserved_event = (
                        bound.replay_reserved_evidence(
                            reserved_entry.evidence_json, request
                        )
                    )
                    current_receipt = self._receipt(record, row)
                    if (
                        any(
                            getattr(reserved_receipt, name)
                            != getattr(current_receipt, name)
                            for name in ReservationReceipt.model_fields
                            if name != "ledger_revision"
                        )
                        or reserved_receipt.ledger_revision
                        > current_receipt.ledger_revision
                    ):
                        raise QualificationLedgerError(
                            "bound_control_reservation_changed"
                        )
                    bound.guard_current_control(
                        reserved_control,
                        reserved_event,
                        control_expectation,
                        scope,
                        now=now,
                        request=request,
                    )
                    bound.guard_current_control(
                        control_state,
                        control_event,
                        control_expectation,
                        scope,
                        now=now,
                        request=request,
                    )
                await self._require_compatible_currency(session, scope)
                current = self._claims(row)
                if type(request) in (ReservationRequestV2, ReservationRequestV3):
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
            if control_expectation is not None:
                intent = bound.build_control_bound_intent(
                    request,
                    intent,
                    reserved_entry.evidence_json,
                    control_state,
                    control_event,
                )
            self._journal(
                session,
                record,
                previous,
                bound.CONSUMED_REASON
                if control_expectation is not None
                else "consumed_with_submit_intent"
                if record_intent
                else target,
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
                if control_expectation is not None:
                    bound.guard_current_control(
                        control_state,
                        control_event,
                        control_expectation,
                        scope,
                        now=self._now(row),
                        request=request,
                    )
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
        return await self._read_submission_intent(
            scope, event_key, expected_sha256=expected_sha256
        )

    async def consume_with_control_bound_submission_intent(
        self,
        scope,
        event_key,
        *,
        expected_revision,
        control_expectation,
        execution_binding,
    ):
        """Atomic controlled consume plus separate-session replay; still DENY.

        Unknown commit/readback outcomes retain the consumed reservation and
        intent. No retry, issuer, source/config mapper or order IO exists here.
        """
        expectation = bound.freeze_expectation(control_expectation)
        intent = await self._transition(
            scope,
            event_key,
            expected_revision=expected_revision,
            target="consumed",
            record_intent=True,
            execution_binding=execution_binding,
            control_expectation=expectation,
        )
        return await self.read_control_bound_submission_intent(
            scope, event_key, expected_sha256=intent.sha256
        )

    async def read_control_bound_submission_intent(
        self, scope, event_key, *, expected_sha256
    ):
        return await self._read_submission_intent(
            scope, event_key, expected_sha256=expected_sha256, control_bound=True
        )

    async def _read_submission_intent(
        self, scope, event_key, *, expected_sha256, control_bound=False
    ):
        scope = checked(scope, LedgerScope)
        if (
            type(expected_sha256) is not str
            or len(expected_sha256) != 64
            or any(char not in "0123456789abcdef" for char in expected_sha256)
        ):
            raise QualificationLedgerError("submit_intent_integrity_mismatch")
        async with self.session_factory() as session, session.begin():
            if control_bound:
                await DemoControlRepository.read_in_transaction(
                    session, ControlScope(scope.environment, scope.account_id)
                )
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
                or entries[0].reason_code
                != (
                    bound.CONSUMED_REASON
                    if control_bound
                    else "consumed_with_submit_intent"
                )
                or entries[0].from_state != "reserved"
                or entries[0].state_revision != 2
                or entries[0].evidence_json is None
            ):
                raise QualificationLedgerError("submit_intent_missing")
            entry = entries[0]
            request_json = record.request_json
            request = decode_reservation_request(request_json)
            if _canonical_json_sha256(request_json) != record.request_sha256:
                raise QualificationLedgerError("ledger_request_digest_mismatch")
            if control_bound:
                intent, consumed = bound.replay_control_bound_intent(
                    entry.evidence_json, request, expected_sha256=expected_sha256
                )
                reserved_entry = await session.scalar(
                    select(QualificationReservationTransition).filter_by(
                        reservation_id=record.reservation_id, state_revision=1
                    )
                )
                envelope = bound._document(intent.canonical_json)
                if (
                    reserved_entry is None
                    or reserved_entry.from_state is not None
                    or reserved_entry.to_state != "reserved"
                    or reserved_entry.reason_code != bound.RESERVED_REASON
                    or reserved_entry.evidence_json
                    != envelope["reservation_binding_json"]
                    or reserved_entry.occurred_at != record.created_at
                ):
                    raise QualificationLedgerError(
                        "bound_control_reserved_journal_invalid"
                    )
                _, reserved_control, reserved_event = bound.replay_reserved_evidence(
                    reserved_entry.evidence_json, request
                )
                await DemoControlRepository.verify_historical_binding(
                    session, reserved_control, reserved_event
                )
            else:
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
