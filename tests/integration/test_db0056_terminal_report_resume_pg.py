"""Exercise installed DB0056 submission and terminal-report guards without venue IO."""

from datetime import datetime, timedelta
from itertools import pairwise
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import native_role_identity_0056_v2 as identity
from app.database.models.qualification_ledger import (
    QualificationAccountScope,
    QualificationReservation,
    QualificationReservationTransition,
)
from app.database.models.submission_reporting import QualificationSubmissionOutcome
from app.database.repositories import native_postsubmit_v1 as postsubmit
from app.database.repositories import native_terminal_report_resume_0056_v1 as resume
from app.database.repositories.demo_control import DemoControlRepository
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification.demo_control import ControlScope
from app.trade_qualification.native_reconciliation_facts_v1 import (
    assess_native_reconciliation_v1,
)
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _actual_0056_role_observation_lab,  # noqa: F401
)
from tests.unit.test_native_ledger_contracts_v2 import synthetic  # noqa: F401
from tests.unit.test_native_reconciliation_facts_v1 import (
    data,
    target,  # noqa: F401
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_MANIFEST_SQL = text("""
    SELECT public.qualification_native_manifest_v55(:rid,:mid,:origin,:credential,:cutoff)
""")
_FILLS_SQL = text("""
    SELECT public.qualification_native_rows_v55(
        CAST(:pages AS jsonb), 'fills_history', '/api/v5/trade/fills-history',
        CAST(:query AS jsonb), :cursor
    )
""")


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
    row = await _append_transport_data(
        session,
        reservation,
        scope_row,
        transition_id,
        recorded_at,
        capture,
        intent_digest,
        sha(canonical(intent["exact_order"])),
        previous=None,
    )
    return row.outcome_id


async def _append_transport_data(
    session,
    reservation,
    scope_row,
    transition_id,
    recorded_at,
    capture,
    intent_digest,
    request_digest,
    *,
    previous,
):
    scope_row.ledger_revision += 1
    scope_row.updated_at = recorded_at
    await session.flush()
    facts = {
        "reservation_id": reservation.reservation_id,
        "intent_transition_id": transition_id,
        "sequence": previous.sequence + 1 if previous else 1,
        "previous_sha256": previous.outcome_id if previous else None,
        "observation_kind": "reconciliation" if previous else "initial",
        "status": "uncertain",
        "intent_sha256": intent_digest,
        "exchange_request_sha256": request_digest,
        "capture_sha256": sha(capture),
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
    row = QualificationSubmissionOutcome(
        **facts,
        outcome_id=outcome_id,
        capture_json=capture.decode(),
        outcome_json=outcome.decode(),
        observed_at=recorded_at,
        recorded_at=recorded_at,
    )
    session.add(row)
    await session.flush()
    return row


async def _append_original_raw_manifest(session, source, first, pages):
    coordinates = source.request.scope
    control = await DemoControlRepository._locked(
        session, ControlScope(coordinates.environment, coordinates.account_id)
    )
    assert control.emergency_stop is True and control.arm_request_id is None
    scope = await session.scalar(
        select(QualificationAccountScope)
        .filter_by(
            environment=coordinates.environment,
            account_id=coordinates.account_id,
            settlement_currency=coordinates.settlement_currency,
        )
        .with_for_update()
    )
    reservation = await session.get(QualificationReservation, source.reservation_id)
    assert reservation.state == "uncertain" and reservation.state_revision == 3
    pivot = pages[0][1]
    capture_id = sha(
        canonical(
            {
                "first_session": pivot.read_session_id,
                "first_started_ns": pivot.started_monotonic_ns,
                "reservation_id": source.reservation_id,
            }
        )
    )
    credential = sha(b"isolated synthetic original post-submit reader")
    previous = first
    recorded_at = pages[-1][1].eof_at

    async def append(document):
        nonlocal previous, recorded_at
        recorded_at += timedelta(microseconds=1)
        previous = await _append_transport_data(
            session,
            reservation,
            scope,
            first.intent_transition_id,
            recorded_at,
            canonical(document),
            first.intent_sha256,
            first.exchange_request_sha256,
            previous=previous,
        )
        return previous.outcome_id

    linked = []
    for index, (role, page) in enumerate(pages):
        body_sha = sha(page.raw_body)
        metadata = await append(
            {
                "version": "ctcc.native_read_page_metadata.v1",
                "capture_id": capture_id,
                "page_index": index,
                "role": role,
                "path": page.path,
                "parameters": list(page.parameters),
                "request_url": page.request_url,
                "request_signature_sha256": page.request_signature_sha256,
                "read_session_id": page.read_session_id,
                "credential_session_sha256": credential,
                "original_credential_session_sha256": source.request.credential_session_sha256,
                "started_at": page.started_at.isoformat(),
                "started_monotonic_ns": page.started_monotonic_ns,
                "headers_at": page.headers_at.isoformat(),
                "headers_monotonic_ns": page.headers_monotonic_ns,
                "eof_at": page.eof_at.isoformat(),
                "eof_monotonic_ns": page.eof_monotonic_ns,
                "status": page.http_status,
                "raw_headers_hex": [
                    [key.hex(), value.hex()] for key, value in page.raw_headers
                ],
                "certificate_sha256": page.certificate_sha256,
                "tls_hostname": page.tls_hostname,
                "body_sha256": body_sha,
                "body_bytes": len(page.raw_body),
            }
        )
        parts = []
        for offset in range(0, len(page.raw_body), postsubmit._CHUNK):
            chunk = page.raw_body[offset : offset + postsubmit._CHUNK]
            parts.append(
                await append(
                    {
                        "version": "ctcc.native_read_raw_part.v1",
                        "capture_id": capture_id,
                        "page_index": index,
                        "metadata_outcome_id": metadata,
                        "offset": offset,
                        "raw_hex": chunk.hex(),
                        "raw_sha256": sha(chunk),
                    }
                )
            )
        linked.append(
            {
                "role": role,
                "metadata": metadata,
                "parts": parts,
                "body_sha256": body_sha,
            }
        )
    raw = {
        "version": "ctcc.native_read_manifest.v1",
        "capture_id": capture_id,
        "reservation_id": source.reservation_id,
        "pages": linked,
        "assessment_sha256": None,
        "computation": "not_evaluated",
        "durable_lineage_sha256": source.durable_lineage_sha256,
        "execution_authority": False,
        "order_retry_authority": False,
    }
    return await append(raw)


async def _complete_original_manifest(session, source, raw_id, assessment):
    coordinates = source.request.scope
    control = await DemoControlRepository._locked(
        session, ControlScope(coordinates.environment, coordinates.account_id)
    )
    assert control.emergency_stop is True and control.arm_request_id is None
    scope = await session.scalar(
        select(QualificationAccountScope)
        .filter_by(
            environment=coordinates.environment,
            account_id=coordinates.account_id,
            settlement_currency=coordinates.settlement_currency,
        )
        .with_for_update()
    )
    reservation = await session.get(QualificationReservation, source.reservation_id)
    assert reservation.state == "uncertain" and reservation.state_revision == 3
    raw = await session.get(QualificationSubmissionOutcome, raw_id)
    assert (
        raw.reservation_id == reservation.reservation_id
        and raw.ledger_revision == scope.ledger_revision
    )
    raw_doc = decode(raw.capture_json.encode())
    assert (
        raw_doc["computation"] == "not_evaluated"
        and raw_doc["assessment_sha256"] is None
    )
    complete = await _append_transport_data(
        session,
        reservation,
        scope,
        raw.intent_transition_id,
        raw.recorded_at + timedelta(microseconds=1),
        canonical(
            {
                **raw_doc,
                "raw_manifest_outcome_id": raw_id,
                "computation": "completed",
                "assessment_sha256": sha(assessment.receipt_json),
            }
        ),
        raw.intent_sha256,
        raw.exchange_request_sha256,
        previous=raw,
    )
    return complete.sequence


async def _verify_original_readback(
    session, source, pages, assessment, raw_id, sequence
):
    stored = tuple(
        (
            await session.scalars(
                select(QualificationSubmissionOutcome)
                .filter_by(reservation_id=source.reservation_id)
                .order_by(QualificationSubmissionOutcome.sequence)
            )
        ).all()
    )
    assert len(stored) == sequence and stored[-1].sequence == sequence
    completed = stored[-1]
    prior = stored[-2]
    assert prior.outcome_id == completed.previous_sha256 == raw_id
    assert decode(prior.capture_json.encode())["computation"] == "not_evaluated"
    manifest, reconstructed = postsubmit._read_manifest(stored, completed.outcome_id)
    assert reconstructed == pages
    assert manifest["raw_manifest_outcome_id"] == raw_id
    assert manifest["assessment_sha256"] == sha(assessment.receipt_json)
    assert (
        assess_native_reconciliation_v1(source, reconstructed).receipt_json
        == assessment.receipt_json
    )
    assert all(
        sha(row.capture_json.encode()) == row.capture_sha256
        and sha(row.outcome_json.encode()) == row.outcome_id
        for row in stored
    )
    assert all(
        before.outcome_id == after.previous_sha256
        and before.ledger_revision + 1 == after.ledger_revision
        for before, after in pairwise(stored)
    )

    args = {
        "rid": source.reservation_id,
        "mid": completed.outcome_id,
        "origin": source.origin,
        "credential": source.request.credential_session_sha256,
        "cutoff": pages[-1][1].eof_at,
    }
    replay = await session.scalar(_MANIFEST_SQL, args)
    assert replay["manifest"] == manifest and len(replay["pages"]) == len(pages)
    for item, (role, page) in zip(replay["pages"], pages, strict=True):
        assert item["role"] == role and item["meta"]["body_sha256"] == sha(
            page.raw_body
        )
        assert item["data"] == decode(page.raw_body)["data"]
    fillings = [item for item in replay["pages"] if item["role"] == "fills_history"]
    assert len(fillings) == 2 and fillings[-1]["data"] == []
    query = {
        key: value for key, value in fillings[0]["query"].items() if key != "limit"
    }
    wire_query = canonical(query).decode()
    assert (
        await session.scalar(
            _FILLS_SQL,
            {
                "pages": canonical(replay["pages"]).decode(),
                "query": wire_query,
                "cursor": "billId",
            },
        )
        == fillings[0]["data"]
    )

    for changes, message in (
        ({"mid": raw_id}, "native_filled_complete_manifest"),
        (
            {"credential": replay["credential"]},
            "native_filled_actual_new_read_chronology",
        ),
        (
            {"cutoff": pages[-1][1].eof_at - timedelta(microseconds=1)},
            "native_filled_actual_new_read_chronology",
        ),
    ):
        with pytest.raises(DBAPIError, match=message):
            async with session.begin_nested():
                await session.scalar(_MANIFEST_SQL, args | changes)
    with pytest.raises(DBAPIError, match="native_filled_terminal_empty_page_required"):
        async with session.begin_nested():
            await session.scalar(
                _FILLS_SQL,
                {
                    "pages": canonical(fillings[:1]).decode(),
                    "query": wire_query,
                    "cursor": "billId",
                },
            )
    with pytest.raises(DBAPIError, match="native_filled_exact_page_query"):
        async with session.begin_nested():
            await session.scalar(
                _FILLS_SQL,
                {
                    "pages": canonical(fillings).decode(),
                    "query": wire_query,
                    "cursor": "tradeId",
                },
            )
    assert (
        await session.scalar(
            text("""
        SELECT public.qualification_native_report_eligible_v55(:oid,:rid,:report,:policy)
    """),
            {
                "oid": completed.outcome_id,
                "rid": source.reservation_id,
                "report": sha(assessment.receipt_json),
                "policy": sha(b"synthetic"),
            },
        )
        is False
    )


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
                    "ordId": "10001",
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

    with pytest.raises(DBAPIError) as rejected:
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
    assert rejected.value.orig.sqlstate == "P0001"
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
    pages = data(target, filled=True, exact_outgoing_query=True)
    original_order = decode(pages[1][1].raw_body)["data"][0]
    assert original_order["ordId"] == decode(body)["data"][0]["ordId"]
    assert original_order["clOrdId"] == intent["exact_order"]["clOrdId"]
    assessment = assess_native_reconciliation_v1(target, pages)
    assert assessment.classification == "filled_and_protected_hold_retained"
    assert assessment.filled_and_protected and not assessment.execution_authority
    async with (
        factory() as raw_writer,
        raw_writer.begin(),
        report56._verified_terminal_writer(raw_writer, engine, roles, names, route),
    ):
        first = await raw_writer.get(QualificationSubmissionOutcome, outcome_id)
        raw_id = await _append_original_raw_manifest(raw_writer, target, first, pages)
        raw_xid = await raw_writer.scalar(resume._XID)
    async with (
        factory() as computed_writer,
        computed_writer.begin(),
        report56._verified_terminal_writer(
            computed_writer, engine, roles, names, route
        ),
    ):
        computed_xid = await computed_writer.scalar(resume._XID)
        assert computed_xid not in {write_xid, raw_xid}
        sequence = await _complete_original_manifest(
            computed_writer, target, raw_id, assessment
        )
    async with (
        factory() as completed_reader,
        completed_reader.begin(),
        report56._verified_terminal_writer(
            completed_reader, engine, roles, names, route
        ),
    ):
        assert await completed_reader.scalar(resume._XID) not in {
            write_xid,
            raw_xid,
            computed_xid,
        }
        await _verify_original_readback(
            completed_reader, target, pages, assessment, raw_id, sequence
        )
