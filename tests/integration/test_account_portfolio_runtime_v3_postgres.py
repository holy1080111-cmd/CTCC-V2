"""Real PostgreSQL B1/B3/B5 diagnostic orchestration from synthetic account bytes.

These cases require an explicit isolated, migrated DATABASE_URL. The HTTP
transport is mocked; no OKX credential, network request or order is involved.
They prove a durable sampled-balance join, not account or trading authority.
"""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.database.models.account_capture_journal import DemoAccountCaptureEvent
from app.database.models.account_measured_hwm_v2 import DemoAccountMeasuredHWMV2
from app.database.models.account_observation_index import DemoAccountObservationBatch
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.account_measured_hwm_v2 import (
    AccountMeasuredHWMRepository,
)
from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
)
from app.database.repositories.qualification_ledger import (
    QualificationLedgerRepository,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_materializer as mapping
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_portfolio_components as components
from app.trade_qualification import account_portfolio_runtime as runtime
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_account_history_query_verifier_repository import now
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- isolated PostgreSQL fixture
)
from tests.unit.test_qualification_account_capture import NOW, ms, row
from tests.unit.test_qualification_account_collector import (
    SECRETS,
    Harness,
    credentials,
)
from tests.unit.test_qualification_account_materializer import inputs, source_pages
from tests.unit.test_qualification_account_v4 import plan as v5_plan
from tests.unit.test_qualification_account_v4 import script as v5_script

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _invocation(monkeypatch, database):
    uid = f"123456789{uuid4().int % 10**12:012d}"
    scope = LedgerScope(account_id=uid, settlement_currency="USDT")
    current = now()
    anchor = current.replace(
        microsecond=current.microsecond // 1000 * 1000
    ) - timedelta(seconds=10)
    offset = int(ms(anchor)) - int(ms(NOW))

    def shift(value):
        if type(value) is list:
            return [shift(item) for item in value]
        if type(value) is dict:
            return {
                key: str(int(item) + offset)
                if key in {"ts", "cTime", "uTime", "fillTime"}
                and type(item) is str
                and item.isdigit()
                else uid
                if key == "uid"
                else shift(item)
                for key, item in value.items()
            }
        return value

    pages = source_pages()
    pages["account_instruments"] = [[row("account_instruments", tickSz="0.1")]]
    harness = Harness(
        monkeypatch,
        pages=pages,
        change=lambda stream, index, data: shift(data),
    )
    harness.script = v5_script(pages=pages)
    harness.clock = now
    plan = v5_plan(
        expected_uid=uid,
        created_at=anchor,
        history_start=anchor - timedelta(days=7),
        history_end=anchor - timedelta(seconds=1),
    )
    controlled = ControlledDemoAccountSession(
        credentials=credentials(),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )
    factory = database[1]
    ledger = QualificationLedgerRepository(factory, clock=now)
    journal = AccountCaptureJournalRepository(factory, clock=now)
    observation = AccountObservationIndexRepository(factory, clock=now)
    measured = AccountMeasuredHWMRepository(factory, clock=now)
    profile = inputs(
        account_id=uid, instruments=(), costs=(), ledger=None, history=None, peak=None
    )
    arguments = {
        "repository": ledger,
        "journal_repository": journal,
        "observation_repository": observation,
        "hwm_repository": measured,
        "clock": now,
        "barrier_completed_at": anchor - timedelta(seconds=2),
        "window": observed.ExecutionWindow(
            plan.history_start, plan.history_end - timedelta(seconds=600)
        ),
        "expected_revision": 0,
        "profile": profile,
        "expected_profile_sha256": mapping.materialization_inputs_sha256(profile),
    }
    return controlled, harness, scope, arguments


async def _counts(database, scope):
    filters = {
        "environment": scope.environment,
        "account_id": scope.account_id,
        "settlement_currency": scope.settlement_currency,
    }
    async with database[1]() as session:
        counts = []
        for model in (
            DemoAccountCaptureEvent,
            DemoAccountObservationBatch,
            DemoAccountMeasuredHWMV2,
        ):
            counts.append(
                await session.scalar(
                    select(func.count()).select_from(model).filter_by(**filters)
                )
            )
        return tuple(counts)


async def test_v3_real_postgresql_same_invocation_commits_and_replays_deny_only(
    monkeypatch, database
):
    controlled, harness, scope, arguments = _invocation(monkeypatch, database)
    result = await runtime.collect_owned_portfolio_components_v3(
        controlled, **arguments
    )
    receipt = json.loads(result.receipt_json)
    assert receipt["schema_version"] == "ctcc.portfolio_components_diagnostic.v3"
    assert receipt["admission"] == "DENY"
    assert receipt["account_complete"] is receipt["execution_authority"] is False
    assert result.owner is result.snapshot is receipt["snapshot"] is None
    assert result.execution_authority is False
    assert receipt["completed_components"]["sampled_hwm_population_verified"]
    assert receipt["durable_measured_hwm"]["db_readback_matched"] is True
    assert receipt["durable_measured_hwm"]["execution_authority"] is False
    assert receipt["durable_measured_hwm"]["native_clock_verified"] is False
    assert all(secret.encode() not in result.receipt_json for secret in SECRETS)

    source = observed.CaptureReference(**receipt["source_reference"])
    restarted_journal = AccountCaptureJournalRepository(database[1], clock=now)
    restarted_b3 = AccountObservationIndexRepository(database[1], clock=now)
    restarted_b5 = AccountMeasuredHWMRepository(database[1], clock=now)
    chain = await restarted_journal.read_chain(scope, source.capture_id)
    assert observed.source_reference(chain) == source
    batches = await restarted_b3.read(scope)
    assert len(batches) == 1 and batches[0].sequence == 1
    assert batches[0].event_sha256 != source.head_sha256
    measured = await restarted_b5.readback(
        scope, expected_sequence=1, expected_head_sha256=batches[0].event_sha256
    )
    assert (
        measured.projection_sha256
        == receipt["durable_measured_hwm"]["projection_sha256"]
    )
    projection = json.loads(measured.projection_json)
    folded = receipt["measured_hwm"]
    assert projection["peak_equity"] == folded["peak_equity"]
    assert projection["peak_measurement_sha256"] == folded["peak_source_sha256"]
    assert projection["last_source_reference"] == receipt["source_reference"]
    assert measured.admission == "DENY" and not measured.execution_authority
    assert (await restarted_b5.preflight_prefix(scope)).sequence == 1
    b1_count, b3_count, b5_count = await _counts(database, scope)
    assert b1_count == len(chain) > 1 and b3_count == b5_count == 1
    assert harness.requests and all(req.method == "GET" for req in harness.requests)
    assert all(req.headers["x-simulated-trading"] == "1" for req in harness.requests)
    harness.assert_closed()


async def test_v3_late_v1_fold_failure_preserves_committed_b1_b3_b5_after_restart(
    monkeypatch, database
):
    controlled, harness, scope, arguments = _invocation(monkeypatch, database)

    async def fail_after_b5_commit(*args, **kwargs):
        raise RuntimeError("synthetic late population read failure")

    monkeypatch.setattr(
        arguments["observation_repository"],
        "read_balance_source_page",
        fail_after_b5_commit,
    )
    with pytest.raises(
        components.PortfolioComponentError,
        match="owned_portfolio_component_capture_failed",
    ):
        await runtime.collect_owned_portfolio_components_v3(controlled, **arguments)
    b1_count, b3_count, b5_count = await _counts(database, scope)
    assert b1_count > 1 and b3_count == b5_count == 1
    restarted_b3 = AccountObservationIndexRepository(database[1], clock=now)
    restarted_b5 = AccountMeasuredHWMRepository(database[1], clock=now)
    (batch,) = await restarted_b3.read(scope)
    measured = await restarted_b5.readback(
        scope, expected_sequence=1, expected_head_sha256=batch.event_sha256
    )
    assert measured.admission == "DENY" and not measured.execution_authority
    assert (await restarted_b5.preflight_prefix(scope)).sequence == 1
    assert harness.requests and all(req.method == "GET" for req in harness.requests)
    harness.assert_closed()
