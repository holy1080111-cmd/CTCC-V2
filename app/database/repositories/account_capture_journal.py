"""B1 audit transactions under DB0017's UID lock; no claims/control mutation."""

from datetime import datetime

from sqlalchemy import select

from app.database.models.account_capture_journal import DemoAccountCaptureEvent
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_capture as capture
from app.trade_qualification.account_capture_journal import (
    _ISSUER,
    MAX_CHAIN_BYTES,
    MAX_EVENTS,
    AccountJournalError,
    JournalReadback,
    _JournalEvent,
    canonical,
    checked_event,
    digest,
)
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap


class AccountCaptureJournalRepository:
    def __init__(self, session_factory, *, clock):
        self.session_factory, self.clock = session_factory, clock
        self.ledger = QualificationLedgerRepository(session_factory, clock=clock)

    async def _lock(self, session, scope):
        checked_bootstrap(scope, LedgerScope)
        if session.get_bind().dialect.name != "postgresql":
            raise AccountJournalError("journal_postgresql_required")
        return await self.ledger._locked(session, scope)

    @staticmethod
    def _event(row, scope):
        if (row.environment, row.account_id, row.settlement_currency) != (
            scope.environment,
            scope.account_id,
            scope.settlement_currency,
        ):
            raise AccountJournalError("journal_scope_mismatch")
        event = _JournalEvent(
            _ISSUER, row.event_json.encode("utf-8"), row.raw_body, row.packet_payload
        )
        data = checked_event(event)
        if (row.capture_id, row.sequence, row.previous_sha256, row.event_sha256) != (
            data["capture_id"],
            data["sequence"],
            data["previous_sha256"],
            digest(event.event_json),
        ):
            raise AccountJournalError("journal_row_mismatch")
        return event

    async def read_chain(self, scope, capture_id):
        async with self.session_factory() as session, session.begin():
            await self._lock(session, scope)
            rows = (
                await session.scalars(
                    select(DemoAccountCaptureEvent)
                    .where(DemoAccountCaptureEvent.capture_id == capture_id)
                    .order_by(DemoAccountCaptureEvent.sequence)
                    .limit(MAX_EVENTS + 1)
                )
            ).all()
            if not rows or len(rows) > MAX_EVENTS:
                raise AccountJournalError("journal_capture_missing_or_bound")
            result, previous, total_bytes, previous_db = [], None, 0, None
            for index, row in enumerate(rows, 1):
                event = self._event(row, scope)
                record = checked_event(event)
                if record["sequence"] != index or record["previous_sha256"] != previous:
                    raise AccountJournalError("journal_chain_invalid")
                total_bytes += (
                    len(event.event_json)
                    + len(event.raw_body or b"")
                    + len(event.packet_payload or b"")
                )
                if (
                    row.chain_bytes != total_bytes
                    or total_bytes > MAX_CHAIN_BYTES
                    or (previous_db is not None and row.db_recorded_at < previous_db)
                ):
                    raise AccountJournalError("journal_chain_byte_or_clock_bound")
                previous_db = row.db_recorded_at
                previous = digest(event.event_json)
                result.append(
                    JournalReadback(
                        event, row.db_recorded_at, capture._utc(self.clock())
                    )
                )
            return tuple(result)

    async def _append(self, scope, event):
        record = checked_event(event)
        encoded, event_sha = event.event_json.decode("utf-8"), digest(event.event_json)
        async with self.session_factory() as session, session.begin():
            account = await self._lock(session, scope)
            existing = await session.get(
                DemoAccountCaptureEvent, (record["capture_id"], record["sequence"])
            )
            if existing is not None:
                if self._event(existing, scope) != event:
                    raise AccountJournalError("journal_event_conflict")
            else:
                prior = await session.scalar(
                    select(DemoAccountCaptureEvent)
                    .where(DemoAccountCaptureEvent.capture_id == record["capture_id"])
                    .order_by(DemoAccountCaptureEvent.sequence.desc())
                    .limit(1)
                )
                if prior is None:
                    if record["sequence"] != 1:
                        raise AccountJournalError("journal_predecessor_missing")
                    before = await self.ledger._bootstrap_checkpoint(
                        session, scope, account
                    )
                    if (
                        record["data"].get("local_checkpoint_sha256")
                        != before.state_sha256
                    ):
                        raise AccountJournalError("journal_local_checkpoint_changed")
                    if tuple(
                        record["data"].get(k)
                        for k in ("environment", "account_id", "settlement_currency")
                    ) != (
                        scope.environment,
                        scope.account_id,
                        scope.settlement_currency,
                    ):
                        raise AccountJournalError("journal_scope_mismatch")
                else:
                    old = checked_event(self._event(prior, scope))
                    if (
                        old["outcome"] != "in_progress"
                        or record["sequence"] != prior.sequence + 1
                        or record["previous_sha256"] != prior.event_sha256
                    ):
                        raise AccountJournalError("journal_predecessor_conflict")
                chain_bytes = (
                    (0 if prior is None else prior.chain_bytes)
                    + len(event.event_json)
                    + len(event.raw_body or b"")
                    + len(event.packet_payload or b"")
                )
                if chain_bytes > MAX_CHAIN_BYTES:
                    raise AccountJournalError("journal_chain_byte_bound")
                session.add(
                    DemoAccountCaptureEvent(
                        chain_bytes=chain_bytes,
                        capture_id=record["capture_id"],
                        sequence=record["sequence"],
                        environment=scope.environment,
                        account_id=scope.account_id,
                        settlement_currency=scope.settlement_currency,
                        previous_sha256=record["previous_sha256"],
                        event_sha256=event_sha,
                        event_json=encoded,
                        raw_body=event.raw_body,
                        packet_payload=event.packet_payload,
                    )
                )
                await session.flush()
        # A new session, not an identity-map reread inside the writer transaction.
        receipt = await self.read_event(scope, record["capture_id"], record["sequence"])
        if receipt.event != event:
            raise AccountJournalError("journal_commit_readback_unknown")
        return receipt

    async def read_event(self, scope, capture_id, sequence):
        """Bounded independent-session exact event/predecessor audit readback."""
        async with self.session_factory() as session, session.begin():
            await self._lock(session, scope)
            row = await session.get(DemoAccountCaptureEvent, (capture_id, sequence))
            if row is None:
                raise AccountJournalError("journal_event_missing")
            event = self._event(row, scope)
            own_size = (
                len(event.event_json)
                + len(event.raw_body or b"")
                + len(event.packet_payload or b"")
            )
            expected_bytes = own_size
            if sequence > 1:
                prior = await session.get(
                    DemoAccountCaptureEvent, (capture_id, sequence - 1)
                )
                if (
                    prior is None
                    or digest(self._event(prior, scope).event_json)
                    != row.previous_sha256
                    or prior.db_recorded_at > row.db_recorded_at
                ):
                    raise AccountJournalError("journal_predecessor_invalid")
                expected_bytes += prior.chain_bytes
            if row.chain_bytes != expected_bytes or row.chain_bytes > MAX_CHAIN_BYTES:
                raise AccountJournalError("journal_chain_byte_bound")
            return JournalReadback(
                event, row.db_recorded_at, capture._utc(self.clock())
            )

    async def recover_interrupted(self, scope, *, capture_id, expected_head_sha256):
        """Audit-only expiry recovery; never resume HTTP or reconstruct missing bytes."""
        chain = await self.read_chain(scope, capture_id)
        first, last = checked_event(chain[0].event), checked_event(chain[-1].event)
        now = capture._utc(self.clock())
        if (
            digest(chain[-1].event.event_json) != expected_head_sha256
            or last["outcome"] != "in_progress"
        ):
            raise AccountJournalError("journal_recovery_head_conflict")
        if now <= capture._utc(
            datetime.fromisoformat(first["data"]["recovery_not_before"])
        ):
            raise AccountJournalError("journal_recovery_deadline_not_reached")
        observed, durable = {}, set()
        for receipt in chain:
            record = checked_event(receipt.event)
            data = record["data"]
            if "request_index" in data:
                observed[data["request_index"]] = data
                if receipt.event.raw_body is not None:
                    durable.add(data["request_index"])
        missing = [
            {
                "request_index": index,
                "last_durable_observed_bytes": data["observed_bytes"],
                "last_durable_prefix_sha256": data["prefix_sha256"],
                "raw_retention": "unavailable_owner_unverified",
                "additional_unobserved_bytes": None,
                "original_body_completed_at": data["body_completed_at"],
            }
            for index, data in observed.items()
            if index not in durable
        ]
        event = _JournalEvent(
            _ISSUER,
            canonical(
                {
                    "version": "ctcc.demo_account_ingestion_event.v1",
                    "capture_id": capture_id,
                    "sequence": last["sequence"] + 1,
                    "previous_sha256": expected_head_sha256,
                    "kind": "recovery",
                    "outcome": "interrupted_owner_unknown",
                    "observed_at": now.isoformat(),
                    "data": {
                        "raw_retention": "unavailable_owner_unverified",
                        "missing_raw": missing,
                        "timestamp_semantics": "recovery_observation_only",
                        "source_state": "incomplete",
                        "process_death_confirmed": False,
                        "owner_liveness": "unverified",
                    },
                    "raw_sha256": None,
                    "packet_sha256": None,
                    "account_complete": False,
                    "execution_authority": False,
                    "admission": "DENY",
                }
            ),
        )
        return await self._append(scope, event)
