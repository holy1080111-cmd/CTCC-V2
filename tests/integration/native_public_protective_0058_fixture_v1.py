"""Declared synthetic ingress, original public0058 SQL/locks/triggers only.

Nothing here runs a native Source/Arm/order transport. All source and market
inputs are explicitly fictional test DATA. Existing public schema guards remain
installed, enabled and unmodified, including issuer/source and intent lineage.
"""

import asyncio
import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlencode

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.database.models.account_capture_journal import DemoAccountCaptureEvent
from app.database.models.account_issued_revision import (
    DemoCollectorIssuedAccountRevision as Issued,
)
from app.database.models.qualification_ledger import (
    QualificationAccountScope as ScopeRow,
)
from app.database.models.qualification_ledger import (
    QualificationReservation as Reservation,
)
from app.database.models.qualification_ledger import (
    QualificationReservationTransition as Transition,
)
from app.database.models.submission_reporting import (
    QualificationSubmissionOutcome as Outcome,
)
from app.database.repositories import (
    account_issued_revision_current55_v1 as issued_codec,
)
from app.database.repositories import native_postsubmit_v1 as journal
from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain import native_protective_hold_schema_v58 as schema
from app.domain.source_primitives import canonical, decode, sha
from app.exchange.okx import native_reconciliation_observation_v1 as read
from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification import demo_control as control
from app.trade_qualification import native_ledger_contracts_v2 as wire
from app.trade_qualification import native_protective_exit_facts_v1 as facts
from app.trade_qualification import reservations as ledger
from tests.controlled_writer_coverage_fixtures import _store_control_fixture
from tests.integration import (
    test_collector_issued_revision_sql_component_pg as issuer_fixture,
)
from tests.unit.test_native_ledger_contracts_v2 import native_binding
from tests.unit.test_native_protective_exit_facts_v1 import protective_terminal
from tests.unit.test_native_reconciliation_facts_v1 import data as original_pages

COMPONENT = "DECLARED_SYNTHETIC_PUBLIC0058_PROTECTIVE_SQL_ONLY"


def _shift(value, offset):
    if type(value) is dict:
        return {key: _shift(item, offset) for key, item in value.items()}
    if type(value) is list:
        return [_shift(item, offset) for item in value]
    if (
        type(value) is str
        and len(value) >= 20
        and value[4:5] == "-"
        and value[10:11] == "T"
        and value.endswith(("+00:00", "Z"))
    ):
        try:
            return (datetime.fromisoformat(value) + offset).isoformat()
        except ValueError:
            pass
    return value


def request_for_issued(
    template, *, scope, issued_row, credential, deadline, checked_at
):
    """Predeclare a NEW synthetic fixture before retaining any execution page."""
    original = template.request
    offset = deadline - original.origin.deadline
    raw = _shift(original.model_dump(mode="json", round_trip=True), offset)
    fixed = _shift(decode(original.origin.original_candidate_json.encode()), offset)
    fixed["scope"] = scope.model_dump(mode="json", round_trip=True)
    fixed["credential_session_sha256"] = credential
    detection = wire.TriggerDetection.model_validate_json(
        canonical(fixed["detection"]), strict=True
    )
    event = wire.event_identity(detection)
    fixed["original_event_key"] = event
    fixed_json = canonical(fixed).decode()
    proof = _shift(decode(original.r7_proof_json.encode()), offset)
    snapshot = issued_codec._snapshot(issued_row, Issued, issued_codec._ISSUED_COLUMNS)
    proof.update(
        original_event_key=event,
        original_candidate_sha256=sha(fixed_json.encode()),
        new_issuer_sha256=issued_row.issuer_sha256,
        new_issued_row_sha256=sha(snapshot),
        new_account_revision=issued_row.account_revision,
        new_ledger_revision=issued_row.ledger_revision,
        observed_at=(checked_at - timedelta(microseconds=1)).isoformat(),
    )
    raw.update(
        scope=scope.model_dump(mode="json", round_trip=True),
        issuer_sha256=issued_row.issuer_sha256,
        issued_row_sha256=sha(snapshot),
        expected_account_revision=issued_row.account_revision,
        expected_ledger_revision=issued_row.ledger_revision,
        credential_session_sha256=credential,
        r7_proof_json=canonical(proof).decode(),
        r7_proof_sha256=sha(canonical(proof)),
    )
    raw["origin"].update(
        original_event_key=event,
        original_candidate_json=fixed_json,
        original_candidate_sha256=sha(fixed_json.encode()),
    )
    return wire.NativeReservationRequestV4.model_validate_json(
        canonical(raw), strict=True
    )


async def _settings(session):
    for statement in (
        "SET LOCAL search_path=pg_catalog,public,pg_temp",
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL synchronous_commit=on",
        "SET LOCAL statement_timeout='1500ms'",
        "SET LOCAL lock_timeout='1500ms'",
    ):
        await session.execute(text(statement))


async def _lock(session, scope):
    await _settings(session)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": journal.ledger._account_uid_lock_key(scope)},
    )
    await session.execute(
        text(
            "SELECT 1 FROM public.demo_account_controls WHERE environment=:env AND account_id=:uid FOR UPDATE"
        ),
        {"env": scope.environment, "uid": scope.account_id},
    )
    return await session.get(
        ScopeRow,
        (scope.environment, scope.account_id, scope.settlement_currency),
        with_for_update=True,
    )


async def prepare_public_source(monkeypatch, owner, selected, template):
    """Reuse real public lineage/issuer CAS with labeled synthetic raw ingress."""
    plan = issuer_fixture.seed.wire_fixture.current_plan_v7()
    credential = sha(plan.session_binding_id.encode())
    recorded = {}

    async def seed_control(session, *, uid):
        now = datetime.now(UTC)
        pins = control.ControlPins(
            "1" * 64, template.request.origin.original_policy_sha256, credential
        )
        acquired = control.acquire(
            None,
            scope=control.ControlScope("demo", uid),
            owner_sha256="4" * 64,
            pins=pins,
            now=now,
        )
        previous = await _store_control_fixture(
            session,
            acquired,
            command_id="5" * 64,
            action="acquire",
            previous_sha256=None,
        )
        armed = control.advance(
            acquired,
            owner_sha256=acquired.owner_sha256,
            owner_epoch=1,
            expected_revision=1,
            action="arm_requested",
            command_id="6" * 64,
            now=now + timedelta(microseconds=1),
            arm_expires_at=now + timedelta(seconds=25),
        )
        event = await _store_control_fixture(
            session,
            armed,
            command_id="6" * 64,
            action="arm_requested",
            previous_sha256=previous,
        )
        recorded.update(control=armed, event=event)

    lab = SimpleNamespace(
        admin=owner,
        app=selected.writer_factory.kw["bind"],
        factory=selected.writer_factory,
        custody=custody.RuntimeBundleCustodyRepository(selected.custody_factory),
    )
    # Only a TEST fixture callback is replaced. No production admission, SQL
    # function, trigger, source owner or safety-kernel callback is substituted.
    with monkeypatch.context() as ingress:
        ingress.setattr(issuer_fixture, "seed_controlled_writer_control", seed_control)
        item = await issuer_fixture._manual_parents(ingress, lab)
    _, values, _ = await issuer_fixture._write(
        lab, item, expires=min(item.expires, recorded["control"].arm_expires_at)
    )
    async with selected.writer_factory() as session, session.begin():
        await _settings(session)
        issued_row = await session.get(Issued, values["issuer_sha256"])
        first = await session.scalar(
            select(DemoAccountCaptureEvent).where(
                DemoAccountCaptureEvent.capture_id
                == (
                    await session.scalar(
                        select(issuer_fixture.Lineage).where(
                            issuer_fixture.Lineage.lineage_sha256 == item.lineage_sha
                        )
                    )
                ).current_capture_id,
                DemoAccountCaptureEvent.sequence == 1,
            )
        )
        source_plan = decode(first.event_json.encode())["data"]["plan"]
        assert sha(source_plan["session_binding_id"].encode()) == credential
        assert source_plan["expected_uid"] == item.scope.account_id
        snapshot = issued_codec._snapshot(
            issued_row, Issued, issued_codec._ISSUED_COLUMNS
        )
    return lab, item, recorded, issued_row, source_plan, snapshot


async def prepare_public_reservation(
    selected, item, recorded, issued_row, source_plan, template
):
    at = datetime.now(UTC)
    request = request_for_issued(
        template,
        scope=item.scope,
        issued_row=issued_row,
        credential=recorded["control"].pins.credential_session_sha256,
        deadline=min(at + timedelta(seconds=3), issued_row.original_expires_at),
        checked_at=at,
    )
    reserved = ledger.ReservationReceipt(
        scope=request.scope,
        reservation_id=ledger.reservation_id(
            request.scope, request.origin.original_event_key
        ),
        original_event_key=request.origin.original_event_key,
        report_id=request.origin.candidate.report_id,
        instrument_id=request.risk_inputs.instrument.instrument_id,
        direction=request.origin.candidate.direction,
        correlation_group=request.risk_inputs.instrument.correlation_group,
        request_sha256=ledger.digest(request),
        coverage=request.coverage,
        state="reserved",
        state_revision=1,
        account_revision=request.expected_account_revision,
        ledger_revision=request.expected_ledger_revision + 1,
        created_at=at,
        updated_at=at,
        deadline=request.origin.deadline,
    )
    consumed = reserved.model_copy(
        update={
            "state": "consumed",
            "state_revision": 2,
            "ledger_revision": reserved.ledger_revision + 1,
            "updated_at": at + timedelta(microseconds=1),
        }
    )
    reserved_raw = bound.build_reserved_evidence(
        request, reserved, recorded["control"], recorded["event"], checked_at=at
    )
    inner = wire.build_native_intent(
        request, consumed, execution_binding=native_binding(request)
    )
    intent = bound.build_control_bound_intent(
        request, inner, reserved_raw, recorded["control"], recorded["event"]
    )
    target = facts.facts.NativeReconciliationTargetV1(
        request,
        wire.canonical_request(request),
        intent.canonical_json,
        intent.sha256,
        reserved.reservation_id,
        source_plan["expected_main_uid"],
        source_plan["origin"],
        request.credential_session_sha256,
        item.lineage_sha,
    )
    async with selected.writer_factory() as session, session.begin():
        scope_row = await _lock(session, item.scope)
        scope_row.ledger_revision += 1
        scope_row.updated_at = at
        session.add(
            Reservation(
                reservation_id=target.reservation_id,
                environment=item.scope.environment,
                account_id=item.scope.account_id,
                settlement_currency=item.scope.settlement_currency,
                original_event_key=request.origin.original_event_key,
                report_id=reserved.report_id,
                instrument_id=reserved.instrument_id,
                direction=reserved.direction,
                correlation_group=reserved.correlation_group,
                request_json=target.request_json,
                request_sha256=ledger.digest(request),
                coverage_json=ledger.canonical(request.coverage),
                risk_amount=request.coverage.risk_amount,
                margin_amount=request.coverage.margin_amount,
                notional_amount=request.coverage.notional_amount,
                state="reserved",
                state_revision=1,
                deadline=request.origin.deadline,
                created_at=at,
                updated_at=at,
            )
        )
        await session.flush()
        session.add(
            Transition(
                reservation_id=target.reservation_id,
                state_revision=1,
                from_state=None,
                to_state="reserved",
                reason_code=bound.RESERVED_REASON,
                evidence_json=reserved_raw,
                occurred_at=at,
            )
        )
        await session.flush()
        row = await session.get(Reservation, target.reservation_id)
        row.state = "consumed"
        row.state_revision = 2
        row.updated_at = consumed.updated_at
        scope_row.ledger_revision += 1
        scope_row.updated_at = consumed.updated_at
        await session.flush()
        transition = Transition(
            reservation_id=target.reservation_id,
            state_revision=2,
            from_state="reserved",
            to_state="consumed",
            reason_code=bound.CONSUMED_REASON,
            evidence_json=intent.canonical_json,
            occurred_at=consumed.updated_at,
        )
        session.add(transition)
        await session.flush()
        intent_id = transition.id
        row.state = "uncertain"
        row.state_revision = 3
        row.updated_at = at + timedelta(microseconds=2)
        scope_row.ledger_revision += 1
        scope_row.updated_at = row.updated_at
        session.add(
            Transition(
                reservation_id=target.reservation_id,
                state_revision=3,
                from_state="consumed",
                to_state="uncertain",
                reason_code="post_submit_native_uncertain_v1",
                evidence_json=canonical(
                    {"component": COMPONENT, "execution_authority": False}
                ).decode(),
                occurred_at=row.updated_at,
            )
        )
    return PublicJournal0058(selected, target, intent, intent_id), recorded


async def wait_until(observed):
    remaining = (observed - datetime.now(UTC)).total_seconds()
    if remaining > 15:
        raise AssertionError("synthetic_fixture_dispatch_window_exceeded")
    if remaining > 0:
        await asyncio.sleep(remaining + 0.01)


def _sessions(pages, label):
    return tuple(
        (
            role,
            replace(
                page,
                read_session_id=sha((label + "-read").encode()),
                request_url=page.request_url.split("?", 1)[0]
                + ("?" + urlencode(dict(page.parameters)) if page.parameters else ""),
            ),
        )
        for role, page in pages
    )


def declared_public_original_oco(target):
    """NEW fictional pages use the exact declared source origin and cadence.

    None of these pages have been captured or retained yet. The default shared
    fixture remains unchanged; this lab explicitly declares serial10ms GETs.
    """
    original = original_pages(
        target, filled=True, exact_outgoing_query=True, dispatch_interval_ms=10
    )
    return protective_terminal(target, original=original)


def _fresh_terminal(pages, *, after, alter_exit):
    start = max(pages[0][1].started_at, after + timedelta(milliseconds=10))
    offset = start - pages[0][1].started_at
    ms = int(offset.total_seconds() * 1000)
    output = []
    for role, page in pages:
        rows = copy.deepcopy(facts.facts.rows(page))
        if alter_exit:
            for row in rows:
                if row.get("ordId") == "10002":
                    for name in ("cTime", "uTime", "fillTime", "ts"):
                        if row.get(name):
                            row[name] = str(int(row[name]) + ms)
                if role in (
                    "protection_before",
                    "protection_after",
                    "protected_selector_algo",
                ):
                    for name in ("triggerTime", "uTime"):
                        if row.get(name):
                            row[name] = str(int(row[name]) + ms)
                if role == "balance":
                    row["uTime"] = str(int(row["uTime"]) + ms)
                    for detail in row["details"]:
                        detail["uTime"] = str(int(detail["uTime"]) + ms)
                if role == "account_risk":
                    row["ts"] = str(int(row["ts"]) + ms)
        query = dict(page.parameters)
        if "end" in query:
            query["end"] = str(int(query["end"]) + ms)
        ordered = read.checked_query(page.path, query)
        output.append(
            (
                role,
                replace(
                    page,
                    parameters=ordered,
                    request_url=page.request_url.split("?", 1)[0]
                    + ("?" + urlencode(dict(ordered)) if ordered else ""),
                    started_at=page.started_at + offset,
                    headers_at=page.headers_at + offset,
                    eof_at=page.eof_at + offset,
                    started_monotonic_ns=page.started_monotonic_ns
                    + int(offset.total_seconds() * 1e9),
                    headers_monotonic_ns=page.headers_monotonic_ns
                    + int(offset.total_seconds() * 1e9),
                    eof_monotonic_ns=page.eof_monotonic_ns
                    + int(offset.total_seconds() * 1e9),
                    raw_body=canonical({"code": "0", "data": rows}),
                ),
            )
        )
    return tuple(output)


class PublicJournal0058:
    """DATA-only test writer; all actual public constraints/triggers stay on."""

    def __init__(self, selected, target, intent, intent_id):
        self.selected, self.target, self.intent, self.intent_id = (
            selected,
            target,
            intent,
            intent_id,
        )

    async def append(
        self,
        session,
        capture,
        *,
        kind="reconciliation",
        status="uncertain",
        observed=None,
        closure=None,
    ):
        target = self.target
        scope = await _lock(session, target.request.scope)
        previous = await session.scalar(
            select(Outcome)
            .where(Outcome.reservation_id == target.reservation_id)
            .order_by(Outcome.sequence.desc())
            .limit(1)
        )
        at = datetime.now(UTC)
        observed = at if observed is None else observed
        assert observed <= at
        scope.ledger_revision += 1
        scope.updated_at = at
        if closure:
            capture = {**capture, "recovery_closed_outcome_id": closure}
        capture = {
            **capture,
            "execution_authority": False,
            "order_retry_authority": False,
        }
        raw = canonical(capture).decode()
        inner = decode(
            decode(self.intent.canonical_json.encode())["intent_json"].encode()
        )
        document = {
            "version": "ctcc.native_submission_outcome.v1",
            "reservation_id": target.reservation_id,
            "intent_transition_id": self.intent_id,
            "intent_sha256": self.intent.sha256,
            "exchange_request_sha256": sha(canonical(inner["exact_order"])),
            "request_sha256": sha(target.request_json.encode()),
            "sequence": 1 if previous is None else previous.sequence + 1,
            "previous_sha256": None if previous is None else previous.outcome_id,
            "observation_kind": kind,
            "status": status,
            "capture_sha256": sha(raw.encode()),
            "ledger_revision": scope.ledger_revision,
            "observed_at": observed.isoformat(),
            "recorded_at": at.isoformat(),
            "execution_authority": False,
            "order_retry_authority": False,
        }
        encoded = canonical(document).decode()
        identity = sha(encoded.encode())
        values = {
            k: document[k]
            for k in (
                "reservation_id",
                "intent_transition_id",
                "sequence",
                "previous_sha256",
                "observation_kind",
                "status",
                "intent_sha256",
                "exchange_request_sha256",
                "capture_sha256",
                "ledger_revision",
            )
        }
        session.add(
            Outcome(
                **values,
                outcome_id=identity,
                capture_json=raw,
                outcome_json=encoded,
                observed_at=observed,
                recorded_at=at,
            )
        )
        await session.flush()
        return identity, at

    async def manifest(self, session, pages, assessment, *, label, closure=None):
        capture_id = sha((label + self.target.reservation_id).encode())
        members = []
        for index, (role, page) in enumerate(pages):
            metadata = {
                "version": "ctcc.native_read_page_metadata.v1",
                "capture_id": capture_id,
                "page_index": index,
                "role": role,
                "path": page.path,
                "parameters": list(page.parameters),
                "request_url": page.request_url,
                "request_signature_sha256": page.request_signature_sha256,
                "read_session_id": page.read_session_id,
                "credential_session_sha256": sha((label + "-credential").encode()),
                "original_credential_session_sha256": self.target.request.credential_session_sha256,
                "started_at": page.started_at.isoformat(),
                "started_monotonic_ns": page.started_monotonic_ns,
                "headers_at": page.headers_at.isoformat(),
                "headers_monotonic_ns": page.headers_monotonic_ns,
                "eof_at": page.eof_at.isoformat(),
                "eof_monotonic_ns": page.eof_monotonic_ns,
                "status": 200,
                "raw_headers_hex": [
                    [key.hex(), value.hex()] for key, value in page.raw_headers
                ],
                "certificate_sha256": page.certificate_sha256,
                "tls_hostname": page.tls_hostname,
                "body_bytes": len(page.raw_body),
                "body_sha256": sha(page.raw_body),
            }
            meta, _ = await self.append(
                session, metadata, observed=page.eof_at, closure=closure
            )
            part, _ = await self.append(
                session,
                {
                    "version": "ctcc.native_read_raw_part.v1",
                    "capture_id": capture_id,
                    "page_index": index,
                    "metadata_outcome_id": meta,
                    "offset": 0,
                    "raw_hex": page.raw_body.hex(),
                    "raw_sha256": sha(page.raw_body),
                },
                observed=page.eof_at,
                closure=closure,
            )
            members.append(
                {
                    "role": role,
                    "metadata": meta,
                    "parts": [part],
                    "body_sha256": sha(page.raw_body),
                }
            )
        return await self.append(
            session,
            {
                "version": "ctcc.native_read_manifest.v1",
                "capture_id": capture_id,
                "reservation_id": self.target.reservation_id,
                "pages": members,
                "computation": "completed",
                "assessment_sha256": sha(assessment.receipt_json),
            },
            observed=pages[-1][1].eof_at,
            closure=closure,
        )

    async def seed_hold(self, recorded):
        original, terminal = declared_public_original_oco(self.target)
        original = _sessions(original, "public58-original")
        await wait_until(original[-1][1].eof_at)
        assessment = facts.facts.assess_native_reconciliation_v1(self.target, original)
        async with self.selected.writer_factory() as session, session.begin():
            await self.append(
                session,
                {
                    "version": "ctcc.declared_public58_initial.v1",
                    "component": COMPONENT,
                },
                kind="initial",
            )
            manifest, _ = await self.manifest(
                session, original, assessment, label="original"
            )
            filled, _ = await self.append(
                session,
                {
                    "version": "ctcc.native_verified_reconciliation_data.v1",
                    "manifest_outcome_id": manifest,
                    "assessment": decode(assessment.receipt_json),
                    "risk_hold_retained": True,
                    "filled_lifecycle_pending": True,
                },
            )
            hold, held_at = await self.append(
                session,
                {
                    "version": schema.HOLD_VERSION,
                    "reservation_id": self.target.reservation_id,
                    "request_sha256": sha(self.target.request_json.encode()),
                    "intent_sha256": self.intent.sha256,
                    "original_filled_outcome_id": filled,
                    "original_manifest_outcome_id": manifest,
                    "original_assessment_sha256": sha(assessment.receipt_json),
                    "risk_hold_retained": True,
                },
            )
        async with self.selected.writer_factory() as session, session.begin():
            await _lock(session, self.target.request.scope)
            stopped = control.advance(
                recorded["control"],
                owner_sha256=recorded["control"].owner_sha256,
                owner_epoch=1,
                expected_revision=recorded["control"].revision,
                action="disarm",
                command_id="7" * 64,
                now=datetime.now(UTC),
            )
            await _store_control_fixture(
                session,
                stopped,
                command_id="7" * 64,
                action="disarm",
                previous_sha256=recorded["event"],
            )
            assert stopped.arm_request_id is None
        terminal = _sessions(
            _fresh_terminal(terminal, after=held_at, alter_exit=True),
            "public58-terminal",
        )
        return original, terminal, hold

    async def close(
        self, original, terminal, hold, *, mutation=None, retain_forensics=False
    ):
        """Actual predicate/UPDATE guard; a rehashed DATA claim cannot release."""
        expected = facts.assess_original_protective_exit_v1(
            self.target, original, terminal
        )
        observed = terminal
        if mutation is not None:
            updated = []
            for role, page in terminal:
                rows = copy.deepcopy(facts.facts.rows(page))
                if mutation == "changed_protection" and role in (
                    "protected_selector_algo",
                    "protection_before",
                    "protection_after",
                ):
                    for row in rows:
                        row["slTriggerPx"] = str(Decimal(row["slTriggerPx"]) + 1)
                elif mutation == "partial_fill" and role in (
                    "exit_order_before",
                    "exit_order_after",
                ):
                    for row in rows:
                        row["accFillSz"] = str(Decimal(row["sz"]) / 2)
                elif mutation == "ambiguous_child" and role in (
                    "protected_selector_algo",
                    "protection_before",
                    "protection_after",
                ):
                    for row in rows:
                        row["ordIdList"] = ["10002", "10003"]
                updated.append(
                    (
                        role,
                        replace(page, raw_body=canonical({"code": "0", "data": rows})),
                    )
                )
            observed = tuple(updated)
        await wait_until(observed[-1][1].eof_at)
        async with self.selected.writer_factory() as session, session.begin():
            manifest, _ = await self.manifest(
                session, observed, expected, label="terminal"
            )
            forensic_id, report = None, None
            if retain_forensics:
                # Declared synthetic venue ingress, with genuine public SQL
                # linkage and immutable pre-flat reporting rows. No native
                # source or actual SDK-cleanup authority is minted here.
                from app.domain import native_protective_report_schema_v58 as reporting
                from app.trade_evidence import native_protective_forensics_v58 as mapper

                report = mapper.reconstruct_native_protective_forensics_v58(
                    self.target, original, observed
                )
                held = await session.get(Outcome, hold)
                held_capture = decode(held.capture_json.encode())
                forensic_id, _ = await self.append(
                    session,
                    {
                        "version": reporting.FORENSICS_VERSION,
                        "reservation_id": self.target.reservation_id,
                        "request_sha256": sha(self.target.request_json.encode()),
                        "intent_sha256": self.intent.sha256,
                        "hold_outcome_id": hold,
                        "original_manifest_outcome_id": held_capture[
                            "original_manifest_outcome_id"
                        ],
                        "final_manifest_outcome_id": manifest,
                        "assessment_sha256": sha(expected.receipt_json),
                        "forensics_json": report.receipt_json.decode(),
                        "forensics_sha256": sha(report.receipt_json),
                        "risk_hold_retained": True,
                    },
                )
            inner = decode(
                decode(self.intent.canonical_json.encode())["intent_json"].encode()
            )
            witness = {
                "version": schema.TERMINAL_VERSION,
                "reservation_id": self.target.reservation_id,
                "request_sha256": sha(self.target.request_json.encode()),
                "intent_sha256": self.intent.sha256,
                "issued_row_sha256": self.target.request.issued_row_sha256,
                "original_credential_session_sha256": self.target.request.credential_session_sha256,
                "original_main_uid": self.target.main_uid,
                "original_origin": self.target.origin,
                "exact_order_json": canonical(inner["exact_order"]).decode(),
                "hold_outcome_id": hold,
                "manifest_outcome_id": manifest,
                "assessment_sha256": sha(expected.receipt_json),
                "assessment_json": expected.receipt_json.decode(),
                "closure_body_sha256": schema.CURRENT_BODY_SHA256,
                "risk_hold_retained": False,
                "metrics_complete": False,
            }
            if retain_forensics:
                witness.update(
                    forensics_outcome_id=forensic_id,
                    forensics_sha256=sha(report.receipt_json),
                )
            closure, at = await self.append(session, witness, status="acknowledged")
            allowed = await session.scalar(
                text(
                    "SELECT public.qualification_native_protective_flat_data_v58(:rid,:at)"
                ),
                {"rid": self.target.reservation_id, "at": at},
            )
            if mutation is not None:
                assert allowed is False
                async with session.begin_nested() as savepoint:
                    try:
                        await session.execute(
                            text(
                                "UPDATE public.qualification_reservations SET state='reconciled_flat',state_revision=4,updated_at=:at WHERE reservation_id=:rid"
                            ),
                            {"rid": self.target.reservation_id, "at": at},
                        )
                    except DBAPIError as error:
                        assert "qualification_reservation_update_denied" in str(error)
                    else:
                        raise AssertionError(
                            "changed/partial/ambiguous raw DATA resolved risk"
                        )
                    await savepoint.rollback()
                row = await session.get(
                    Reservation, self.target.reservation_id, populate_existing=True
                )
                assert (row.state, row.state_revision) == ("uncertain", 3)
            else:
                assert allowed is True
                await session.execute(
                    text(
                        "UPDATE public.qualification_reservations SET state='reconciled_flat',state_revision=4,updated_at=:at WHERE reservation_id=:rid"
                    ),
                    {"rid": self.target.reservation_id, "at": at},
                )
                session.add(
                    Transition(
                        reservation_id=self.target.reservation_id,
                        state_revision=4,
                        from_state="uncertain",
                        to_state="reconciled_flat",
                        reason_code=schema.TRANSITION_REASON,
                        evidence_json=canonical(
                            {
                                "version": schema.TERMINAL_VERSION,
                                "outcome_id": closure,
                                "closure_body_sha256": schema.CURRENT_BODY_SHA256,
                                "execution_authority": False,
                                "order_retry_authority": False,
                            }
                        ).decode(),
                        occurred_at=at,
                    )
                )
            write_xid = await session.scalar(text("SELECT pg_current_xact_id()::text"))
        return SimpleNamespace(
            closure=closure,
            at=at,
            manifest=manifest,
            assessment=expected,
            write_xid=write_xid,
            forensic_id=forensic_id,
            report=report,
        )

    async def readback_held(self):
        """A different transaction proves rejected closure cannot lose risk."""
        async with self.selected.writer_factory() as session, session.begin():
            await _lock(session, self.target.request.scope)
            row = await session.get(Reservation, self.target.reservation_id)
            assert (row.state, row.state_revision) == ("uncertain", 3)
            assert row.request_json == self.target.request_json
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Transition)
                    .where(
                        Transition.reservation_id == self.target.reservation_id,
                        Transition.state_revision == 4,
                    )
                )
            ) == 0
            assert (
                await session.scalar(
                    text(
                        "SELECT arm_request_id IS NULL AND arm_expires_at IS NULL FROM public.demo_account_controls WHERE environment=:env AND account_id=:uid"
                    ),
                    {
                        "env": self.target.request.scope.environment,
                        "uid": self.target.request.scope.account_id,
                    },
                )
            ) is True
            await journal._outcomes(
                session,
                SimpleNamespace(
                    intent=self.intent,
                    request_json=self.target.request_json,
                    reservation_id=self.target.reservation_id,
                ),
            )
            return await session.scalar(text("SELECT pg_current_xact_id()::text"))

    async def journal_data_readback(self, expected_state):
        """Actual query/projection smoke only; no signed selector is replaced."""
        from app.core.native_role_witness_0056_v1 import (
            observe_restricted_native_roles_0056_witness_v1,
        )
        from app.demo_automation import native_journal_readout_v1 as readout
        from app.domain import native_database_profile_v1 as profiles

        roles = await observe_restricted_native_roles_0056_witness_v1(
            self.selected, source=readout.database._SOURCE, profile=profiles.DB0058
        )
        factory, engine, names, route = readout._local_writer_pins_v1(self.selected)
        async with (
            factory() as session,
            session.begin(),
            readout.database._verified_terminal_writer(
                session, engine, roles, names, route, profile=profiles.DB0058
            ),
        ):
            head = await session.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            assert head == "0058"
            views = []
            for uid in (self.target.request.scope.account_id, "999999999999999"):
                raw = dict(
                    (await session.execute(readout._QUERY, {"uid": uid, "limit": 100}))
                    .mappings()
                    .one()
                )
                view = readout._readout(raw, uid=uid, head=head, limit=100)
                assert (
                    view.execution_authority
                    is view.order_retry_authority
                    is view.account_complete
                    is False
                )
                views.append(view)
            actual, foreign = views
            assert actual.total_native == len(actual.entries) == 1
            assert actual.unresolved_native == (expected_state != "reconciled_flat")
            assert actual.unresolved_other == 0 and actual.truncated is False
            row = actual.entries[0]
            assert (row.reservation_id, row.local_state) == (
                self.target.reservation_id,
                expected_state,
            )
            assert row.protection_status == "NOT_RECHECKED"
            assert (
                foreign.total_native
                == foreign.unresolved_native
                == foreign.unresolved_other
                == 0
            )
            assert foreign.entries == () and foreign.truncated is False

    async def replay_and_append_after_flat(self, original, terminal, closed):
        # A NEW invocation/read envelope is declared after the committed flat
        # witness. Original entry/exit source clocks and raw facts stay fixed.
        current = _sessions(
            _fresh_terminal(terminal, after=closed.at, alter_exit=False),
            "public58-postflat",
        )
        assert current[0][1].started_at > closed.at
        actual = facts.assess_original_protective_exit_v1(
            self.target, original, current
        )
        assert actual.receipt_json == closed.assessment.receipt_json
        await wait_until(current[-1][1].eof_at)
        async with self.selected.writer_factory() as session, session.begin():
            await _lock(session, self.target.request.scope)
            assert (
                await session.scalar(
                    text(
                        "SELECT public.qualification_native_protective_flat_data_v58(:rid,:at)"
                    ),
                    {"rid": self.target.reservation_id, "at": closed.at},
                )
                is True
            )
            # The new raw branch must require the exact committed closure.
            # Rehashed DATA with a foreign link must still fail the real
            # submission binding trigger, leaving all real rows intact.
            async with session.begin_nested() as savepoint:
                try:
                    await self.append(
                        session,
                        {"version": "ctcc.native_read_manifest.v1"},
                        closure="0" * 64,
                    )
                except DBAPIError as error:
                    assert "submission_outcome_binding_denied" in str(error)
                else:
                    raise AssertionError("foreign closure link appended postflat DATA")
                await savepoint.rollback()
            manifest, _ = await self.manifest(
                session, current, actual, label="postflat", closure=closed.closure
            )
            write_xid = await session.scalar(text("SELECT pg_current_xact_id()::text"))
        async with self.selected.writer_factory() as session, session.begin():
            await _lock(session, self.target.request.scope)
            read_xid = await session.scalar(text("SELECT pg_current_xact_id()::text"))
            assert read_xid not in (closed.write_xid, write_xid)
            row = await session.get(Reservation, self.target.reservation_id)
            assert (row.state, row.state_revision, row.updated_at) == (
                "reconciled_flat",
                4,
                closed.at,
            )
            # The production DATA decoder reopens exact canonical hashes and
            # every retained raw part. This object is not a source capability.
            data_target = SimpleNamespace(
                intent=self.intent,
                request_json=self.target.request_json,
                reservation_id=self.target.reservation_id,
            )
            outcomes = await journal._outcomes(session, data_target)
            raw_manifest, pages = journal._read_manifest(outcomes, manifest)
            assert raw_manifest["recovery_closed_outcome_id"] == closed.closure
            assert (
                facts.assess_original_protective_exit_v1(
                    self.target, original, pages
                ).receipt_json
                == actual.receipt_json
            )
            assert (
                await session.scalar(
                    text(
                        "SELECT public.qualification_native_protective_flat_data_v58(:rid,:at)"
                    ),
                    {"rid": self.target.reservation_id, "at": closed.at},
                )
                is True
            )
            assert (
                sum(
                    decode(outcome.capture_json.encode()).get("version")
                    == schema.HOLD_VERSION
                    for outcome in outcomes
                )
                == 1
            )
            assert (
                sum(
                    decode(outcome.capture_json.encode()).get("version")
                    == schema.TERMINAL_VERSION
                    for outcome in outcomes
                )
                == 1
            )
        return read_xid
