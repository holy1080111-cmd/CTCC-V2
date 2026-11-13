"""Exercise installed DB0056 submission and terminal-report guards without venue IO."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import native_role_identity_0056_v2 as identity
from app.database.models.qualification_ledger import (
    QualificationAccountScope,
    QualificationReservation,
    QualificationReservationTransition,
)
from app.database.models.submission_reporting import QualificationSubmissionOutcome
from app.database.repositories import native_terminal_report_resume_0056_v1 as resume
from app.database.repositories.demo_control import DemoControlRepository
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification.demo_control import ControlScope
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _actual_0056_role_observation_lab,  # noqa: F401
)
from tests.unit.test_native_ledger_contracts_v2 import synthetic  # noqa: F401
from tests.unit.test_native_reconciliation_facts_v1 import target  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_actual0056_report_resume_refuses_missing_cleanup_no_new_rows(
    _actual_0056_role_observation_lab,  # noqa: F811
    monkeypatch,
):
    _, owner, selected, _ = _actual_0056_role_observation_lab
    roles = await identity.observe_restricted_role_identity_0056_v2(
        selected, source=resume.report56._SOURCE
    )
    settings = SimpleNamespace(submission_outbox_queue_namespace="disposable_test")
    monkeypatch.setattr(resume, "get_settings", lambda: settings)
    counters = text("""
        SELECT (SELECT count(*) FROM public.qualification_submission_outcomes),
               (SELECT count(*) FROM public.qualification_report_spool),
               (SELECT count(*) FROM public.qualification_report_projection_receipts),
               (SELECT coalesce(sum(ledger_revision), 0) FROM public.qualification_account_scopes)
    """)
    async with owner.begin() as session:
        before = (await session.execute(counters)).one()
    cleanup_id = sha(b"ctcc-isolated-missing-cleanup")
    reads = []
    get = AsyncSession.get

    async def checked_get(session, model, key, **options):
        value = await get(session, model, key, **options)
        if model is QualificationSubmissionOutcome and key == cleanup_id:
            assert session.bind is selected.writer_factory.kw["bind"]
            assert session.in_transaction() and value is None
            reads.append(session)
        return value

    monkeypatch.setattr(AsyncSession, "get", checked_get)
    with pytest.raises(
        resume.report56.NativeTerminalReport0056Error,
        match="^native_terminal_report_0056_unavailable$",
    ):
        await resume._resume_native_terminal_report_0056_v1(
            selected,
            cleanup_id=cleanup_id,
            expected_roles=roles,
        )
    assert len(reads) == 1 and not reads[0].in_transaction()
    async with owner.begin() as session:
        assert (await session.execute(counters)).one() == before


async def _seed_consumed_intent(owner, source):
    """Seed only the pre-existing guard inputs in the disposable database."""
    request = source.request
    wrapper = decode(source.intent_json.encode())
    intent = decode(wrapper["intent_json"].encode())
    consumed = intent["reservation_receipt"]
    created = datetime.fromisoformat(consumed["created_at"])
    consumed_at = datetime.fromisoformat(consumed["updated_at"])
    scope = request.scope.model_dump(
        mode="python", include={"environment", "account_id", "settlement_currency"}
    )
    control_scope = ControlScope(request.scope.environment, request.scope.account_id)
    await DemoControlRepository(owner, clock=lambda: created).latch_stop(
        control_scope, command_id=sha(b"ctcc-isolated-initial-transport-stopped")
    )
    async with owner.begin() as session:
        session.add(
            QualificationAccountScope(
                **scope,
                account_revision=consumed["account_revision"],
                ledger_revision=consumed["ledger_revision"],
                claims_json="{}",
                claims_sha256=sha(b"{}"),
                created_at=created,
                updated_at=consumed_at,
            )
        )
        await session.flush()
        session.add(
            QualificationReservation(
                **scope,
                reservation_id=source.reservation_id,
                original_event_key=request.origin.original_event_key,
                report_id=consumed["report_id"],
                instrument_id=consumed["instrument_id"],
                direction=consumed["direction"],
                correlation_group=consumed["correlation_group"],
                request_json=source.request_json,
                request_sha256=consumed["request_sha256"],
                coverage_json=canonical(consumed["coverage"]).decode(),
                risk_amount=request.coverage.risk_amount,
                margin_amount=request.coverage.margin_amount,
                notional_amount=request.coverage.notional_amount,
                deadline=request.origin.deadline,
                state="consumed",
                state_revision=2,
                created_at=created,
                updated_at=consumed_at,
            )
        )
        await session.flush()
        reserved, consumed_transition = (
            QualificationReservationTransition(
                reservation_id=source.reservation_id,
                state_revision=1,
                from_state=None,
                to_state="reserved",
                reason_code="risk_reserved_control_bound_v1",
                evidence_json=wrapper["reservation_binding_json"],
                occurred_at=created,
            ),
            QualificationReservationTransition(
                reservation_id=source.reservation_id,
                state_revision=2,
                from_state="reserved",
                to_state="consumed",
                reason_code="consumed_with_control_bound_intent_v1",
                evidence_json=source.intent_json,
                occurred_at=consumed_at,
            ),
        )
        session.add_all((reserved, consumed_transition))
        await session.flush()
        return intent, consumed_transition.id, consumed_at


async def _append_initial(
    session, source, intent, transition_id, recorded_at, capture, *, intent_digest
):
    request = source.request
    scope = request.scope
    # Use production's UID/Control lock ordering before the scope row.
    control = await DemoControlRepository._locked(
        session, ControlScope(scope.environment, scope.account_id)
    )
    assert control is not None and control.emergency_stop is True
    scope_row = await session.scalar(
        select(QualificationAccountScope)
        .filter_by(
            **scope.model_dump(
                mode="python",
                include={"environment", "account_id", "settlement_currency"},
            )
        )
        .with_for_update()
    )
    reservation = await session.get(QualificationReservation, source.reservation_id)
    assert reservation.state == "consumed" and reservation.state_revision == 2
    reservation.state, reservation.state_revision, reservation.updated_at = (
        "uncertain",
        3,
        recorded_at,
    )
    scope_row.ledger_revision += 1
    scope_row.updated_at = recorded_at
    capture_sha = sha(capture)
    session.add(
        QualificationReservationTransition(
            reservation_id=source.reservation_id,
            state_revision=3,
            from_state="consumed",
            to_state="uncertain",
            reason_code="post_submit_native_uncertain_v1",
            occurred_at=recorded_at,
            evidence_json=canonical(
                {
                    "version": "ctcc.native_uncertain_observation.v1",
                    "intent_sha256": intent_digest,
                    "capture_sha256": capture_sha,
                }
            ).decode(),
        )
    )
    await session.flush()
    facts = {
        "reservation_id": source.reservation_id,
        "intent_transition_id": transition_id,
        "sequence": 1,
        "previous_sha256": None,
        "observation_kind": "initial",
        "status": "uncertain",
        "intent_sha256": intent_digest,
        "exchange_request_sha256": sha(canonical(intent["exact_order"])),
        "capture_sha256": capture_sha,
        "ledger_revision": scope_row.ledger_revision,
    }
    outcome = canonical(
        {
            **facts,
            "version": "ctcc.native_submission_outcome.v1",
            "request_sha256": reservation.request_sha256,
            "observed_at": recorded_at.isoformat(),
            "recorded_at": recorded_at.isoformat(),
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    outcome_id = sha(outcome)
    session.add(
        QualificationSubmissionOutcome(
            **facts,
            outcome_id=outcome_id,
            capture_json=capture.decode(),
            outcome_json=outcome.decode(),
            observed_at=recorded_at,
            recorded_at=recorded_at,
        )
    )
    await session.flush()
    return outcome_id


@pytest.mark.parametrize(
    "synthetic", [("long", "net_mode")], indirect=True, scope="module", ids=["long-net"]
)
async def test_actual0056_report_initial_transport_appends_atomically_without_eligibility(
    _actual_0056_role_observation_lab,  # noqa: F811
    target,  # noqa: F811
):
    _, owner, selected, _ = _actual_0056_role_observation_lab
    report56 = resume.report56
    roles = await report56.witness56.observe_restricted_native_roles_0056_witness_v1(
        selected, source=report56._SOURCE
    )
    factory, engine, names, _ = report56._factory_pins(selected)
    route = report56.locked._direct_tls_route(engine)
    intent, transition_id, consumed_at = await _seed_consumed_intent(owner, target)
    at = consumed_at + timedelta(microseconds=1)
    body = canonical(
        {
            "code": "0",
            "msg": "",
            "data": [
                {
                    "clOrdId": intent["exact_order"]["clOrdId"],
                    "ordId": "100001",
                    "sCode": "0",
                    "sMsg": "",
                }
            ],
        }
    )
    capture = canonical(
        {
            "version": "ctcc.native_initial_transport_data.v1",
            "kind": "current",
            "reservation_id": target.reservation_id,
            "transport_response_missing_after_restart": False,
            "execution_authority": False,
            "order_retry_authority": False,
            "raw_body_hex": body.hex(),
            "raw_body_sha256": sha(body),
            "received_http_status": 200,
            "received_eof": True,
            "received_overflow": False,
            "received_faults": [],
            "response_observation_sha256": sha(body),
        }
    )

    with pytest.raises(IntegrityError) as rejected:
        async with (
            factory() as session,
            session.begin(),
            report56._verified_terminal_writer(session, engine, roles, names, route),
        ):
            await _append_initial(
                session,
                target,
                intent,
                transition_id,
                at,
                capture,
                intent_digest="0" * 64,
            )
    assert "submission_outcome_binding_denied" in str(rejected.value.orig)
    async with (
        factory() as writer,
        writer.begin(),
        report56._verified_terminal_writer(writer, engine, roles, names, route),
    ):
        assert (
            await writer.scalar(
                select(QualificationSubmissionOutcome).filter_by(
                    reservation_id=target.reservation_id
                )
            )
            is None
        )
        assert (
            await writer.scalar(
                select(QualificationReservationTransition).filter_by(
                    reservation_id=target.reservation_id, state_revision=3
                )
            )
            is None
        )
        outcome_id = await _append_initial(
            writer,
            target,
            intent,
            transition_id,
            at,
            capture,
            intent_digest=target.intent_sha256,
        )
        write_xid = await writer.scalar(resume._XID)

    async with (
        factory() as reader,
        reader.begin(),
        report56._verified_terminal_writer(reader, engine, roles, names, route),
    ):
        assert reader is not writer and not writer.in_transaction()
        assert await reader.scalar(resume._XID) != write_xid
        outcome = await reader.get(QualificationSubmissionOutcome, outcome_id)
        control = await DemoControlRepository._locked(
            reader,
            ControlScope(
                target.request.scope.environment,
                target.request.scope.account_id,
            ),
        )
        reservation = await reader.get(QualificationReservation, target.reservation_id)
        transition = await reader.scalar(
            select(QualificationReservationTransition).filter_by(
                reservation_id=target.reservation_id,
                state_revision=3,
            )
        )
        scope = await reader.get(
            QualificationAccountScope,
            (
                target.request.scope.environment,
                target.request.scope.account_id,
                target.request.scope.settlement_currency,
            ),
        )
        assert outcome.capture_json.encode() == capture
        observed_capture = decode(outcome.capture_json.encode())
        assert bytes.fromhex(observed_capture["raw_body_hex"]) == body
        assert (
            observed_capture["raw_body_sha256"]
            == observed_capture["response_observation_sha256"]
            == sha(body)
        )
        assert sha(outcome.outcome_json.encode()) == outcome.outcome_id == outcome_id
        assert (
            outcome.capture_sha256 == sha(capture)
            and outcome.sequence == 1
            and outcome.status == "uncertain"
        )
        assert (
            reservation.state == transition.to_state == "uncertain"
            and reservation.state_revision == 3
        )
        assert (
            control.emergency_stop is True
            and control.arm_request_id is None
            and control.control_revision == 1
        )
        assert (
            transition.from_state == "consumed"
            and scope.ledger_revision
            == outcome.ledger_revision
            == target.request.expected_ledger_revision + 3
            and scope.account_revision == target.request.expected_account_revision
            and scope.updated_at
            == reservation.updated_at
            == transition.occurred_at
            == outcome.recorded_at
            == at
        )
        assert (
            await reader.scalar(
                text(
                    "SELECT public.qualification_native_report_eligible_v55(:oid,:rid,:report,:policy)"
                ),
                {
                    "oid": outcome_id,
                    "rid": target.reservation_id,
                    "report": sha(capture),
                    "policy": sha(b"synthetic"),
                },
            )
            is False
        )
