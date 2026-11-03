"""Committed PostgreSQL recovery with seeded history and the installed 0055 trigger.

The fixture schema carries synthetic history. Normal application source/target
admission is deliberately outside this test. The repository and installed
submission trigger handle all journal writes and independent readbacks.
"""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models.qualification_ledger import (
    QualificationAccountScope as Scope,
)
from app.database.repositories import native_postsubmit_v1 as repo
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain import native_submission_reporting_schema_v55 as submission
from app.domain import native_zero_fok_closure_schema_v55 as closure
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import native_postsubmit_reconciliation_v1 as native
from app.trade_qualification import native_reconciliation_facts_v1 as facts
from tests.controlled_current55_fixtures import (
    controlled_current55_database as controlled_current55_database,  # noqa: PLC0414 -- register the shared fixture.
)
from tests.integration.test_account_history_original_frame_repository_v1 import (
    collector,
)
from tests.unit.test_native_ledger_contracts_v2 import (
    synthetic as synthetic,  # noqa: PLC0414 -- register the exact shared fixture.
)
from tests.unit.test_native_reconciliation_facts_v1 import data
from tests.unit.test_native_reconciliation_facts_v1 import (
    target as target,  # noqa: PLC0414 -- register the exact shared fixture.
)
from tests.unit.test_native_zero_fok_restart_facts_v1 import pages

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

TABLES = (
    (Scope.__tablename__, "SELECT,UPDATE"),
    (repo.Reservation.__tablename__, "SELECT"),
    (repo.Transition.__tablename__, "SELECT"),
    (repo.Outcome.__tablename__, "SELECT,INSERT"),
)
PURPOSE = "native_previously_closed_zero_recovery.v1"
REASON = "post_submit_native_zero_fok_flat_v1"


def document(value):
    return canonical(value).decode()


async def _seed_scope_and_intent(engine, name, fixture, historical, intent):
    scope = fixture.request.scope
    fields = {
        "environment": scope.environment,
        "account_id": scope.account_id,
        "settlement_currency": scope.settlement_currency,
    }
    order = intent["inner"]["exact_order"]
    consumed = intent["inner"]["reservation_receipt"]
    at = consumed["updated_at"]
    reserved = consumed["created_at"]
    at, reserved = datetime.fromisoformat(at), datetime.fromisoformat(reserved)
    async with engine.begin() as connection:
        await connection.execute(
            text(f'SET LOCAL search_path TO "{name}",pg_catalog,public')
        )
        await connection.execute(
            insert(Scope).values(
                **fields,
                account_revision=fixture.request.expected_account_revision,
                ledger_revision=consumed["ledger_revision"],
                claims_json="{}",
                claims_sha256=sha(b"{}"),
                created_at=reserved,
                updated_at=at,
            )
        )
        await connection.execute(
            insert(repo.Reservation).values(
                **fields,
                reservation_id=fixture.reservation_id,
                original_event_key=fixture.request.origin.original_event_key,
                report_id=fixture.request.origin.candidate.report_id,
                instrument_id=order["instId"],
                direction=fixture.request.origin.candidate.direction,
                correlation_group=fixture.request.risk_inputs.instrument.correlation_group,
                request_json=fixture.request_json,
                request_sha256=sha(fixture.request_json.encode()),
                coverage_json="{}",
                risk_amount=1,
                margin_amount=1,
                notional_amount=1,
                state="uncertain",
                state_revision=3,
                deadline=fixture.request.origin.deadline,
                created_at=reserved,
                updated_at=at,
            )
        )
        await connection.execute(
            insert(repo.Transition),
            [
                {
                    "id": 1,
                    "reservation_id": fixture.reservation_id,
                    "state_revision": 1,
                    "from_state": None,
                    "to_state": "reserved",
                    "reason_code": "risk_reserved_control_bound_v1",
                    "evidence_json": intent["outer"]["reservation_binding_json"],
                    "occurred_at": reserved,
                },
                {
                    "id": 2,
                    "reservation_id": fixture.reservation_id,
                    "state_revision": 2,
                    "from_state": "reserved",
                    "to_state": "consumed",
                    "reason_code": "consumed_with_control_bound_intent_v1",
                    "evidence_json": fixture.intent_json,
                    "occurred_at": at,
                },
                {
                    "id": 3,
                    "reservation_id": fixture.reservation_id,
                    "state_revision": 3,
                    "from_state": "consumed",
                    "to_state": "uncertain",
                    "reason_code": "post_submit_native_uncertain_v1",
                    "evidence_json": None,
                    "occurred_at": max(page.eof_at for _, page in historical),
                },
            ],
        )


def _fresh(fixture):
    seconds = 20
    wall, monotonic = timedelta(seconds=seconds), seconds * 1_000_000_000
    return tuple(
        (
            role,
            replace(
                page,
                read_session_id="f" * 64,
                started_at=page.started_at + wall,
                headers_at=page.headers_at + wall,
                eof_at=page.eof_at + wall,
                started_monotonic_ns=page.started_monotonic_ns + monotonic,
                headers_monotonic_ns=page.headers_monotonic_ns + monotonic,
                eof_monotonic_ns=page.eof_monotonic_ns + monotonic,
            ),
        )
        for role, page in pages(fixture)
    )


@pytest.mark.parametrize(
    "late_rejection", (False, True), ids=("committed", "late_rejection")
)
async def test_recovery_repository_commits_and_replays_as_restricted_writer(
    controlled_current55_database,
    target,
    monkeypatch,
    request,
    late_rejection,
):
    admin = controlled_current55_database
    name = "recovery_fixture_" + uuid4().hex
    binding = object()
    historical = data(target)
    fresh = _fresh(target)
    close_at = max(page.eof_at for _, page in historical) + timedelta(seconds=1)
    verified_at = max(page.eof_at for _, page in fresh) + timedelta(seconds=1)
    outer = decode(target.intent_json.encode())
    inner = decode(outer["intent_json"].encode())
    wrapped = SimpleNamespace(
        facts=target,
        reservation_id=target.reservation_id,
        request=target.request,
        request_json=target.request_json,
        intent=SimpleNamespace(
            canonical_json=target.intent_json, sha256=sha(target.intent_json.encode())
        ),
        consumed=SimpleNamespace(request_sha256=sha(target.request_json.encode())),
        current_state="uncertain",
        current_state_revision=3,
        core_json=target.request_json,
    )
    state = {
        "kind": "recovery",
        "reservation_id": target.reservation_id,
        "target": wrapped,
        "observations": list(historical),
    }
    source = SimpleNamespace(credential_session_sha256="e" * 64)
    clock = [close_at]
    transactions = []

    async with collector(admin) as (writer, writer_oid):
        role = writer.url.username
        sessions = async_sessionmaker(writer, expire_on_commit=False, autoflush=False)
        repository = QualificationLedgerRepository(sessions, clock=lambda: clock[0])

        @asynccontextmanager
        async def fixture_transaction(cap):
            assert cap is binding
            async with sessions() as session, session.begin():
                await session.execute(
                    text(f'SET LOCAL search_path TO "{name}",pg_catalog,public')
                )
                await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
                await session.execute(text("SET LOCAL synchronous_commit=on"))
                scope = await session.scalar(select(Scope).with_for_update())
                metadata = tuple((await session.execute(repo._META)).one())
                assert metadata[:7] == (
                    role,
                    role,
                    "none",
                    writer_oid,
                    False,
                    False,
                    False,
                )
                transactions.append(metadata[7:9])
                yield state, source, repository, session, scope, None, None, metadata

        def fixture_source(cap):
            assert cap is binding
            return state, source

        async def fixture_target(cap, _source, _repository, session, scope, _metadata):
            assert (
                cap is binding and scope.account_id == target.request.scope.account_id
            )
            row = await session.get(
                repo.Reservation, target.reservation_id, populate_existing=True
            )
            assert (
                row.state == wrapped.current_state
                and row.state_revision == wrapped.current_state_revision
            )
            assert row.request_json == wrapped.core_json
            return wrapped

        async def fixture_scan(cap, raw):
            assert cap is binding and type(raw) is bytes

        try:
            async with admin.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{name}"'))
                for table, rights in TABLES:
                    await connection.execute(
                        text(
                            f'CREATE TABLE "{name}".{table} (LIKE public.{table} INCLUDING ALL)'
                        )
                    )
                    await connection.execute(
                        text(f'GRANT {rights} ON "{name}".{table} TO "{role}"')
                    )
                await connection.execute(
                    text(f'GRANT USAGE ON SCHEMA "{name}" TO "{role}"')
                )
                await connection.execute(
                    text(
                        f'CREATE TRIGGER recovery_binding BEFORE INSERT ON "{name}".qualification_submission_outcomes '
                        "FOR EACH ROW EXECUTE FUNCTION public.qualification_submission_binding()"
                    )
                )
                await connection.execute(
                    text(
                        f'CREATE TRIGGER recovery_immutable BEFORE UPDATE OR DELETE ON "{name}".qualification_submission_outcomes '
                        "FOR EACH ROW EXECUTE FUNCTION public.qualification_ledger_immutable()"
                    )
                )
                actual = await connection.scalar(
                    text(
                        "SELECT prosrc FROM pg_proc WHERE oid='public.qualification_submission_binding()'::regprocedure"
                    )
                )
                assert sha(actual.encode()) == submission.CURRENT_BODY_SHA256
                assert (
                    await connection.scalar(
                        text(
                            "SELECT tgfoid = 'public.qualification_submission_binding()'::regprocedure "
                            "FROM pg_trigger WHERE tgrelid=CAST(:name AS regclass) AND tgname='recovery_binding'"
                        ),
                        {"name": f"{name}.qualification_submission_outcomes"},
                    )
                    is True
                )
                if late_rejection:
                    await connection.execute(
                        text(
                            f'ALTER TABLE "{name}".qualification_submission_outcomes ADD CONSTRAINT fixture_late_rejection '
                            f"CHECK (capture_json::jsonb->>'purpose' IS DISTINCT FROM '{PURPOSE}')"
                        )
                    )
            await _seed_scope_and_intent(
                admin, name, target, historical, {"outer": outer, "inner": inner}
            )
            with monkeypatch.context() as patches:
                patches.setattr(repo, "_transaction", fixture_transaction)
                patches.setattr(repo, "_source", fixture_source)
                patches.setattr(repo, "_target_locked", fixture_target)
                patches.setattr(repo, "_scan", fixture_scan)
                async with fixture_transaction(binding) as parts:
                    await repo._append(
                        binding, parts, {"version": "fixture.initial"}, kind="initial"
                    )
                await repo.retain_native_readback_v1(binding)
                state["assessment"] = facts.assess_native_reconciliation_v1(
                    target, historical
                )
                await repo.record_native_assessment_v1(binding)
                assessment = state["assessment"].receipt_json
                manifest_id = state["durable_readback_manifest_id"]
                witness = {
                    "version": "ctcc.native_zero_fok_flat_closure.v1",
                    "reservation_id": target.reservation_id,
                    "request_sha256": wrapped.consumed.request_sha256,
                    "intent_sha256": wrapped.intent.sha256,
                    "issued_row_sha256": target.request.issued_row_sha256,
                    "original_credential_session_sha256": target.request.credential_session_sha256,
                    "original_main_uid": target.main_uid,
                    "original_origin": target.origin,
                    "exact_order_json": document(inner["exact_order"]),
                    "manifest_outcome_id": manifest_id,
                    "assessment_json": assessment.decode(),
                    "assessment_sha256": sha(assessment),
                    "closure_body_sha256": closure.CURRENT_BODY_SHA256,
                    "execution_authority": False,
                    "order_retry_authority": False,
                }
                async with fixture_transaction(binding) as parts:
                    original_id = await repo._append(
                        binding,
                        parts,
                        witness,
                        kind="reconciliation",
                        status="rejected",
                    )
                    historical_count = len(await repo._outcomes(parts[3], wrapped))
                    historical_revision = parts[4].ledger_revision
                async with admin.begin() as connection:
                    await connection.execute(
                        text(f'SET LOCAL search_path TO "{name}",pg_catalog,public')
                    )
                    await connection.execute(
                        repo.Reservation.__table__.update().values(
                            state="reconciled_flat",
                            state_revision=4,
                            updated_at=close_at,
                        )
                    )
                    await connection.execute(
                        insert(repo.Transition).values(
                            id=4,
                            reservation_id=target.reservation_id,
                            state_revision=4,
                            from_state="uncertain",
                            to_state="reconciled_flat",
                            reason_code=REASON,
                            evidence_json=document(
                                {
                                    "version": "ctcc.native_zero_fok_flat_transition.v1",
                                    "outcome_id": original_id,
                                    "manifest_outcome_id": manifest_id,
                                    "assessment_sha256": sha(assessment),
                                    "closure_body_sha256": closure.CURRENT_BODY_SHA256,
                                    "execution_authority": False,
                                    "order_retry_authority": False,
                                }
                            ),
                            occurred_at=close_at,
                        )
                    )
                wrapped.current_state, wrapped.current_state_revision = (
                    "reconciled_flat",
                    4,
                )
                prior = await repo.read_native_flat_closure_v1(binding)
                assert prior[0] == original_id and prior[2] == historical
                clock[0] = verified_at
                state["observations"] = list(fresh)
                before = len(transactions)
                if late_rejection:
                    with pytest.raises(IntegrityError, match="fixture_late_rejection"):
                        await repo.finish_previously_closed_recovery_v1(binding)
                    assert "verified_previously_closed_zero_recovery" not in state
                    assert "owned_flat_result" not in state
                else:
                    result = await repo.finish_previously_closed_recovery_v1(binding)
                    assert type(result) is native.NativeReconciliationResultV1
                    assert (
                        result.outcome_id == original_id
                        and result.risk_hold_retained is False
                    )
                    assert (
                        result.execution_authority
                        is result.order_retry_authority
                        is False
                    )
                    assert state["verified_previously_closed_zero_recovery"][2] == fresh
                assert state["durable_observations"] == fresh
                attempts = transactions[before:]
                assert len(attempts) == (3 if late_rejection else 4)
                assert (
                    len({pid for pid, _ in attempts})
                    == len({xid for _, xid in attempts})
                    == len(attempts)
                )
                async with fixture_transaction(binding) as parts:
                    rows = await repo._outcomes(parts[3], wrapped)
                    raw_appends = len(fresh) * 2 + 1
                    assert len(rows) == historical_count + raw_appends + (
                        not late_rejection
                    )
                    assert parts[
                        4
                    ].ledger_revision == historical_revision + raw_appends + (
                        not late_rejection
                    )
                    added = rows[historical_count:]
                    captures = [
                        decode(row.capture_json.encode(), 140000) for row in added
                    ]
                    assert all(row.status == "uncertain" for row in added)
                    assert all(
                        capture["recovery_closed_outcome_id"] == original_id
                        and capture["execution_authority"]
                        is capture["order_retry_authority"]
                        is False
                        for capture in captures
                    )
                    assert sum(
                        capture.get("purpose") == PURPOSE for capture in captures
                    ) == (not late_rejection)
                    assert (await repo._flat_readback_locked(binding, parts))[
                        :3
                    ] == prior[:3]
                    assert (
                        await parts[3].scalar(
                            text(
                                "SELECT has_table_privilege(current_user,"
                                "'qualification_submission_outcomes','UPDATE,DELETE')"
                            )
                        )
                        is False
                    )
            receipt = {
                "late_rejection": late_rejection,
                "independent_writer_transactions": len(attempts),
                "fresh_pages": len(fresh),
                "raw_retained_after_late_rejection": late_rejection,
                "completed_recovery_committed": not late_rejection,
                "original_closure_unchanged": True,
                "installed_trigger_body_sha256": submission.CURRENT_BODY_SHA256,
                "synthetic_history": True,
                "execution_authority": False,
                "order_retry_authority": False,
            }
            request.node.user_properties.append(
                ("ctcc_pg55_recovery_repository_observation_v1", document(receipt))
            )
        finally:
            async with admin.begin() as connection:
                await connection.execute(
                    text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
                )
