"""Atomic private submission observations; separate, order-free file projection."""

from dataclasses import dataclass

from sqlalchemy import select

from app.database.models.qualification_ledger import (
    QualificationReservation,
    QualificationReservationTransition,
)
from app.database.models.submission_reporting import (
    QualificationReportProjectionReceipt,
    QualificationReportSpool,
    QualificationSubmissionOutcome,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_evidence import outbox, post_submit
from app.trade_evidence.submission_reporting import (
    SubmissionReportingError,
    checked_capture,
    decode_capture,
    freeze_capture,
    pin,
    policy_record,
    prepare_submission,
    queue,
    sha,
    wire,
)
from app.trade_qualification import reservations
from app.trade_qualification.models import require_aware


@dataclass(frozen=True, slots=True)
class SubmissionObservationReceipt:
    outcome_id: str
    spool_id: str
    status: str
    report_sha256: str
    intent_sha256: str
    capture_sha256: str
    execution_authority: bool = False
    order_retry_authority: bool = False


@dataclass(frozen=True, slots=True)
class SubmissionProjectionReceipt:
    spool_id: str
    envelope_sha256: str
    report_sha256: str
    policy_sha256: str


def outcome_document(
    prepared, *, transition_id, sequence, previous, revision, recorded_at
):
    return wire(
        {
            "version": "ctcc.private.submission-outcome.v1",
            "reservation_id": prepared.consumed.reservation_id,
            "intent_transition_id": transition_id,
            "intent_sha256": prepared.intent.sha256,
            "sequence": sequence,
            "previous_sha256": previous,
            "observation_kind": "initial",
            "status": prepared.status,
            "reason": prepared.reason,
            "capture_sha256": prepared.capture_sha256,
            "report_sha256": prepared.report_sha256,
            "ledger_revision": revision,
            "recorded_at": require_aware(recorded_at).isoformat(),
            "binding": prepared.binding,
            "execution_authority": False,
            "order_retry_authority": False,
            "transport_authenticity_verified": False,
            "fill_verified": False,
            "protection_verified": False,
        }
    )


def spool_identity(outcome_id, report_sha256, policy_sha256, namespace):
    return sha(
        wire(
            {
                "outcome_id": outcome_id,
                "report_sha256": report_sha256,
                "policy_sha256": policy_sha256,
                "queue_namespace": namespace,
            }
        ).encode()
    )


class SubmissionReportingRepository(QualificationLedgerRepository):
    async def _lineage(self, session, rid, *, intent_sha256, capture):
        record = await session.get(QualificationReservation, pin(rid))
        if record is None or record.state == "reserved":
            raise SubmissionReportingError("submission_consumed_reservation_missing")
        entries = (
            await session.scalars(
                select(QualificationReservationTransition)
                .filter_by(reservation_id=rid, to_state="consumed")
                .limit(2)
            )
        ).all()
        if len(entries) != 1:
            raise SubmissionReportingError("submission_intent_transition_missing")
        entry = entries[0]
        if (
            entry.reason_code != "consumed_with_submit_intent"
            or entry.from_state != "reserved"
            or entry.state_revision != 2
            or entry.evidence_json is None
        ):
            raise SubmissionReportingError("submission_intent_transition_invalid")
        request = reservations.decode(
            record.request_json, reservations.ReservationRequest
        )
        if reservations.digest(request) != record.request_sha256:
            raise SubmissionReportingError("submission_reservation_digest_mismatch")
        prepared = prepare_submission(
            request, entry.evidence_json, intent_sha256=intent_sha256, capture=capture
        )
        consumed = prepared.consumed
        if (
            any(
                getattr(consumed, name) != getattr(record, name)
                for name in (
                    "reservation_id",
                    "original_event_key",
                    "report_id",
                    "instrument_id",
                    "direction",
                    "correlation_group",
                    "request_sha256",
                    "deadline",
                    "created_at",
                )
            )
            or consumed.updated_at != require_aware(entry.occurred_at)
            or consumed.scope != reservations.LedgerScope(**self._keys(request.scope))
        ):
            raise SubmissionReportingError("submission_persisted_lineage_mismatch")
        if (
            (record.environment, record.account_id, record.settlement_currency)
            != (
                consumed.scope.environment,
                consumed.scope.account_id,
                consumed.scope.settlement_currency,
            )
            or reservations.decode(record.coverage_json, reservations.RiskCoverage)
            != consumed.coverage
            or (record.risk_amount, record.margin_amount, record.notional_amount)
            != (
                consumed.coverage.risk_amount,
                consumed.coverage.margin_amount,
                consumed.coverage.notional_amount,
            )
        ):
            raise SubmissionReportingError("submission_persisted_coverage_mismatch")
        return record, entry, prepared

    async def record_observation(
        self,
        scope,
        event_key,
        *,
        expected_revision,
        intent_sha256,
        capture,
        queue_namespace,
        outbox_policy,
    ):
        """Persist the first actual return/unknown result; never invoke transport.

        An identical persistence retry reads its existing row even after a lost
        commit acknowledgement. A conflicting initial observation is refused.
        Later reconciliation is not manufactured from an initial-submit capture.
        """
        scope = reservations.checked(scope, reservations.LedgerScope)
        rid = reservations.reservation_id(scope, event_key)
        capture = checked_capture(capture)
        namespace = queue(queue_namespace)
        _, policy_json, policy_sha256 = policy_record(outbox_policy)
        captured_pin = sha(freeze_capture(capture).encode())
        async with self.session_factory() as session, session.begin():
            account = await self._locked(session, scope)
            existing = await session.scalar(
                select(QualificationSubmissionOutcome).filter_by(
                    reservation_id=rid, observation_kind="initial"
                )
            )
            if existing is not None:
                spool = await session.scalar(
                    select(QualificationReportSpool).filter_by(
                        outcome_id=existing.outcome_id
                    )
                )
                if spool is None or (
                    existing.capture_sha256,
                    existing.intent_sha256,
                    spool.queue_namespace,
                    spool.policy_sha256,
                ) != (captured_pin, pin(intent_sha256), namespace, policy_sha256):
                    raise SubmissionReportingError(
                        "submission_initial_observation_conflict"
                    )
                outcome_id = existing.outcome_id
                await self._verify_spool(session, spool)
            else:
                self._revision(account.ledger_revision, expected_revision)
                record, entry, prepared = await self._lineage(
                    session, rid, intent_sha256=intent_sha256, capture=capture
                )
                if record.state not in {"consumed", "uncertain"}:
                    raise SubmissionReportingError(
                        "submission_initial_after_reconciliation_refused"
                    )
                now = self._now(account)
                if now < capture.completed_at:
                    raise SubmissionReportingError("submission_observation_from_future")
                account.ledger_revision += 1
                account.updated_at = now
                if prepared.status == "uncertain" and record.state != "uncertain":
                    previous = record.state
                    record.state, record.state_revision, record.updated_at = (
                        "uncertain",
                        record.state_revision + 1,
                        now,
                    )
                    self._journal(
                        session,
                        record,
                        previous,
                        "post_submit_uncertain",
                        now,
                        wire(
                            {
                                "capture_sha256": captured_pin,
                                "intent_sha256": prepared.intent.sha256,
                            }
                        ),
                    )
                await session.flush()
                document = outcome_document(
                    prepared,
                    transition_id=entry.id,
                    sequence=1,
                    previous=None,
                    revision=account.ledger_revision,
                    recorded_at=now,
                )
                outcome_id = sha(document.encode())
                outcome = QualificationSubmissionOutcome(
                    outcome_id=outcome_id,
                    reservation_id=rid,
                    intent_transition_id=entry.id,
                    sequence=1,
                    previous_sha256=None,
                    observation_kind="initial",
                    status=prepared.status,
                    intent_sha256=prepared.intent.sha256,
                    exchange_request_sha256=capture.exchange_request_sha256,
                    capture_json=prepared.capture_json,
                    capture_sha256=prepared.capture_sha256,
                    outcome_json=document,
                    ledger_revision=account.ledger_revision,
                    observed_at=capture.completed_at,
                    recorded_at=now,
                )
                session.add(outcome)
                await session.flush()
                session.add(
                    QualificationReportSpool(
                        spool_id=spool_identity(
                            outcome_id, prepared.report_sha256, policy_sha256, namespace
                        ),
                        outcome_id=outcome_id,
                        reservation_id=rid,
                        report_id=record.report_id,
                        report_bytes=prepared.report_bytes,
                        report_sha256=prepared.report_sha256,
                        policy_json=policy_json,
                        policy_sha256=policy_sha256,
                        queue_namespace=namespace,
                        eligible=prepared.status == "acknowledged",
                        created_at=now,
                    )
                )
                await session.flush()
        # A separate committed session is mandatory. Failure never rolls back an
        # exchange observation or permits a second submit.
        return await self.read_observation(
            scope, event_key, expected_outcome_id=outcome_id
        )

    async def _verify_spool(self, session, spool):
        outcome = await session.get(QualificationSubmissionOutcome, spool.outcome_id)
        if outcome is None or outcome.reservation_id != spool.reservation_id:
            raise SubmissionReportingError("submission_outcome_missing")
        capture = decode_capture(outcome.capture_json, outcome.capture_sha256)
        _, entry, prepared = await self._lineage(
            session,
            spool.reservation_id,
            intent_sha256=outcome.intent_sha256,
            capture=capture,
        )
        if (
            outcome.observation_kind != "initial"
            or outcome.sequence != 1
            or outcome.previous_sha256 is not None
        ):
            raise SubmissionReportingError("submission_observation_version_unsupported")
        rebuilt = outcome_document(
            prepared,
            transition_id=entry.id,
            sequence=outcome.sequence,
            previous=outcome.previous_sha256,
            revision=outcome.ledger_revision,
            recorded_at=outcome.recorded_at,
        )
        if (
            rebuilt != outcome.outcome_json
            or sha(rebuilt.encode()) != outcome.outcome_id
            or outcome.intent_transition_id != entry.id
            or outcome.exchange_request_sha256 != capture.exchange_request_sha256
            or outcome.observed_at != capture.completed_at
            or outcome.status != prepared.status
        ):
            raise SubmissionReportingError("submission_outcome_integrity_mismatch")
        try:
            policy = outbox.OutboxPolicy.model_validate_json(
                spool.policy_json, strict=True
            )
            policy, policy_json, policy_sha256 = policy_record(policy)
            report = post_submit.verify_submission_report(
                spool.report_bytes, spool.report_sha256
            )
        except (ValueError, TypeError):
            raise SubmissionReportingError("submission_spool_record_invalid") from None
        if (
            policy_json != spool.policy_json
            or policy_sha256 != spool.policy_sha256
            or prepared.report_bytes != spool.report_bytes
            or prepared.report_sha256 != spool.report_sha256
            or spool.spool_id
            != spool_identity(
                outcome.outcome_id,
                spool.report_sha256,
                policy_sha256,
                queue(spool.queue_namespace),
            )
            or spool.report_id != report.report_id
            or spool.created_at != outcome.recorded_at
            or spool.eligible != (prepared.status == "acknowledged")
        ):
            raise SubmissionReportingError("submission_spool_integrity_mismatch")
        return outcome, report, policy

    async def read_observation(self, scope, event_key, *, expected_outcome_id):
        scope = reservations.checked(scope, reservations.LedgerScope)
        rid = reservations.reservation_id(scope, event_key)
        async with self.session_factory() as session, session.begin():
            account = await self._locked(session, scope)
            outcome = await session.get(
                QualificationSubmissionOutcome, pin(expected_outcome_id)
            )
            if (
                outcome is None
                or outcome.reservation_id != rid
                or outcome.ledger_revision > account.ledger_revision
            ):
                raise SubmissionReportingError(
                    "submission_committed_observation_missing"
                )
            spool = await session.scalar(
                select(QualificationReportSpool).filter_by(
                    outcome_id=outcome.outcome_id
                )
            )
            if spool is None:
                raise SubmissionReportingError("submission_committed_spool_missing")
            await self._verify_spool(session, spool)
            return SubmissionObservationReceipt(
                outcome.outcome_id,
                spool.spool_id,
                outcome.status,
                spool.report_sha256,
                outcome.intent_sha256,
                outcome.capture_sha256,
            )

    async def pending_ids(self, namespace, *, limit=16, after=""):
        namespace = queue(namespace)
        if (
            type(limit) is not int
            or not 1 <= limit <= 16
            or (after and pin(after) != after)
        ):
            raise SubmissionReportingError("submission_projection_query_bound")
        async with self.session_factory() as session:
            statement = (
                select(QualificationReportSpool.spool_id)
                .outerjoin(QualificationReportProjectionReceipt)
                .where(
                    QualificationReportSpool.queue_namespace == namespace,
                    QualificationReportSpool.eligible.is_(True),
                    QualificationReportProjectionReceipt.spool_id.is_(None),
                )
                .order_by(QualificationReportSpool.spool_id)
                .limit(limit)
            )
            rows = tuple(
                (
                    await session.scalars(
                        statement.where(QualificationReportSpool.spool_id > after)
                    )
                ).all()
            )
            return rows or tuple((await session.scalars(statement)).all())

    async def project_one(self, spool_id, *, root, queue_namespace):
        """Only DB→local durable outbox. No token, HTTP, order or callback."""
        namespace = queue(queue_namespace)
        async with self.session_factory() as session, session.begin():
            spool = await session.scalar(
                select(QualificationReportSpool)
                .where(QualificationReportSpool.spool_id == pin(spool_id))
                .with_for_update(skip_locked=True)
            )
            if spool is None:
                return None
            if not spool.eligible or spool.queue_namespace != namespace:
                raise SubmissionReportingError(
                    "submission_spool_not_eligible_for_queue"
                )
            _, report, policy = await self._verify_spool(session, spool)
            existing = await session.get(QualificationReportProjectionReceipt, spool_id)
            result = post_submit.enqueue_post_submit(
                root, report, outbox_policy=policy, clock=self.clock
            )
            if result.status != "enqueued" or not result.durable_outbox_readback:
                raise SubmissionReportingError("submission_local_enqueue_pending")
            view = outbox.validate_outbox_view(
                outbox.read_job(root, report.report_id, clock=self.clock)
            )
            if (
                view.envelope.envelope_sha256 != result.outbox_envelope_sha256
                or view.envelope.payload.submission_receipt_sha256
                != spool.report_sha256
                or view.envelope.policy != policy
            ):
                raise SubmissionReportingError("submission_local_readback_mismatch")
            envelope_sha256 = view.envelope.envelope_sha256
            if existing is not None:
                if (
                    existing.envelope_sha256,
                    existing.report_sha256,
                    existing.policy_sha256,
                    existing.queue_namespace,
                ) != (
                    envelope_sha256,
                    spool.report_sha256,
                    spool.policy_sha256,
                    namespace,
                ):
                    raise SubmissionReportingError(
                        "submission_projection_receipt_conflict"
                    )
            else:
                now = require_aware(self.clock())
                if now < spool.created_at:
                    raise SubmissionReportingError(
                        "submission_projection_clock_regressed"
                    )
                session.add(
                    QualificationReportProjectionReceipt(
                        spool_id=spool_id,
                        report_sha256=spool.report_sha256,
                        policy_sha256=spool.policy_sha256,
                        envelope_sha256=envelope_sha256,
                        queue_namespace=namespace,
                        verified_at=now,
                    )
                )
                await session.flush()
        return await self.read_projection(
            spool_id, expected_envelope_sha256=envelope_sha256
        )

    async def read_projection(self, spool_id, *, expected_envelope_sha256):
        async with self.session_factory() as session:
            row = await session.get(QualificationReportProjectionReceipt, pin(spool_id))
            spool = await session.get(QualificationReportSpool, spool_id)
            if (
                row is None
                or spool is None
                or row.envelope_sha256 != pin(expected_envelope_sha256)
                or (row.report_sha256, row.policy_sha256, row.queue_namespace)
                != (spool.report_sha256, spool.policy_sha256, spool.queue_namespace)
                or row.verified_at < spool.created_at
            ):
                raise SubmissionReportingError("submission_projection_readback_missing")
            return SubmissionProjectionReceipt(
                spool_id, row.envelope_sha256, row.report_sha256, row.policy_sha256
            )
