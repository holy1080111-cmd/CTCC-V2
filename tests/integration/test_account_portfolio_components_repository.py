"""B5 isolated PostgreSQL only; original account responses remain synthetic.

The legacy-singleton case requires a fresh dedicated migrated B5 database. It
leaves its explicitly synthetic records in that database; no deployed fallback.
"""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.database.models.demo_automation import DemoAutomationState
from app.database.models.okx_demo import OkxDemoSyncCheckpoint
from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_portfolio_components as components
from app.trade_qualification.account_observation_index import AccountObservationError
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_account_history_query_verifier_repository import now
from tests.integration.test_account_observation_index_repository import (
    additional_capture,
    stored,
)
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- explicit isolated fixture
)
from tests.integration.test_qualification_ledger_repository import (
    fixture as fixture,  # noqa: PLC0414 -- genuine synthetic evaluator fixture
)
from tests.integration.test_qualification_ledger_repository import (
    initialize,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_original_balance_pages_survive_restart_and_reject_prefix_or_head_changes(
    monkeypatch, database
):
    repo, scope, reference, _, first = await stored(monkeypatch, database)
    original = await repo.journal.read_chain(scope, reference.capture_id)
    later_ref, later_window = await additional_capture(monkeypatch, repo, scope, [])
    second = await repo.append(scope, later_ref, later_window, expected_revision=1)
    restarted = AccountObservationIndexRepository(database[1], clock=now)
    page1 = await restarted.read_balance_source_page(
        scope, through_sequence=2, expected_head_sha256=second.event_sha256, limit=1
    )
    page2 = await restarted.read_balance_source_page(
        scope,
        through_sequence=2,
        expected_head_sha256=second.event_sha256,
        after=1,
        limit=1,
    )
    assert page1.items[0][:3] == (1, None, first.event_sha256)
    assert page2.items[0][:3] == (2, first.event_sha256, second.event_sha256)
    assert (
        json.loads(page1.items[0][3].source_json)["source_reference"]["capture_id"]
        == reference.capture_id
    )
    fold = components.MeasuredHWMFold(scope, 2, second.event_sha256)
    for item in (*page1.items, *page2.items):
        fold.add(*item)
    proof = json.loads(fold.finish())
    assert proof["sample_count"] == 2 and proof["measured_population_complete"]
    assert not proof["all_sources_recorded_native"] and not proof["execution_authority"]
    suffix = components.MeasuredHWMFold(scope, 2, second.event_sha256)
    with pytest.raises(components.PortfolioComponentError, match="prefix"):
        suffix.add(*page2.items[0])
    for arguments in (
        {"expected_head_sha256": "0" * 64},
        {"after": True},
        {"through_sequence": 3},
        {"scope": LedgerScope(account_id="999", settlement_currency="USDT")},
    ):
        with pytest.raises(AccountObservationError):
            await restarted.read_balance_source_page(
                **{
                    "scope": scope,
                    "through_sequence": 2,
                    "expected_head_sha256": second.event_sha256,
                    **arguments,
                }
            )
    assert tuple(point.event for point in original) == tuple(
        point.event
        for point in await repo.journal.read_chain(scope, reference.capture_id)
    )


async def test_unknown_legacy_initialization_and_persisted_latch_never_become_empty(
    database,
):
    scope = LedgerScope(
        account_id=f"123456789{uuid4().int % 10**12:012d}", settlement_currency="USDT"
    )
    repo = QualificationLedgerRepository(database[1], clock=now)
    await repo.initialize_capture_scope(scope)
    async with database[1]() as session:
        assert (await session.scalars(select(DemoAutomationState))).all() == [], (
            "fresh dedicated B5 database required"
        )
        assert (await session.scalars(select(OkxDemoSyncCheckpoint))).all() == []
    unknown = await repo.read_portfolio_checkpoint(scope)
    assert not unknown.known_flat
    assert set(json.loads(unknown.document_json)["blocking_reasons"]) == {
        "legacy_automation_initialization_unknown",
        "legacy_reconciliation_initialization_unknown",
    }
    async with database[1]() as session, session.begin():
        session.add(
            DemoAutomationState(
                id=1,
                session_date=now().date(),
                armed=False,
                emergency_stop=True,
                locked=False,
                lock_reasons=[],
                active_trades={},
                updated_at=now(),
            )
        )
        session.add(
            OkxDemoSyncCheckpoint(
                id=1,
                status="reconciled",
                order_count=0,
                position_count=0,
                algo_order_count=0,
                reconciled_at=now(),
                updated_at=now(),
            )
        )
    verified = await repo.read_portfolio_checkpoint(scope)
    assert verified.known_flat
    initial = json.loads(verified.document_json)
    # An EStop latch is retained; observed local flat never clears or authorizes it.
    assert initial["legacy"]["automation"]["emergency_stop"] is True
    assert not initial["execution_authority"] and not initial["future_writer_exclusion"]
    cases = (
        {"active_trades": {"synthetic": {"outcome": "unknown"}}},
        {
            "active_trades": {},
            "locked": True,
            "lock_reasons": ["unknown_exchange_result"],
        },
        {
            "locked": False,
            "lock_reasons": [],
            "last_started_at": now(),
            "last_completed_at": None,
        },
    )
    expected_control_revision = 0
    for changes in cases:
        async with database[1]() as session, session.begin():
            row = await session.get(DemoAutomationState, 1)
            assert row is not None
            assert row.control_revision == expected_control_revision
            for key, value in changes.items():
                setattr(row, key, value)
            # The synthetic legacy transition must satisfy the real control
            # revision trigger; bypassing it would mask stale-writer failures.
            expected_control_revision += 1
            row.control_revision = expected_control_revision
            row.updated_at = now()
        async with database[1]() as session:
            persisted = await session.get(DemoAutomationState, 1)
            assert persisted is not None
            assert persisted.control_revision == expected_control_revision
        changed = await QualificationLedgerRepository(
            database[1], clock=now
        ).read_portfolio_checkpoint(scope)
        assert not changed.known_flat and changed.state_sha256 != verified.state_sha256
        assert (
            json.loads(changed.document_json)["legacy"]["automation"]["emergency_stop"]
            is True
        )


async def test_local_read_includes_other_currency_expired_holds_and_consumed_intent(
    database, fixture
):
    repo, clock = await initialize(database, fixture)
    receipt = await repo.reserve(fixture.request)
    await repo.consume_once(
        fixture.request.scope, receipt.original_event_key, expected_revision=2
    )
    other = LedgerScope(
        account_id=fixture.request.scope.account_id, settlement_currency="USDC"
    )
    await repo.initialize_capture_scope(other)
    clock.value = max(now(), fixture.request.origin.deadline + timedelta(days=1))
    result = await repo.read_portfolio_checkpoint(other)
    data = json.loads(result.document_json)
    assert {
        state["scope"]["settlement_currency"] for state in data["currency_states"]
    } == {"USDT", "USDC"}
    assert "all_currency_local_holds_unresolved" in data["blocking_reasons"]
    assert "all_currency_local_intents_unresolved" in data["blocking_reasons"]
    assert data["unresolved_intents"][0][0] == receipt.reservation_id
    assert not result.known_flat
    restarted = QualificationLedgerRepository(database[1], clock=clock)
    again = await restarted.read_portfolio_checkpoint(other)
    assert again.document_json == result.document_json
    assert (await restarted.read_scope(fixture.request.scope)).active[
        0
    ].state == "consumed"
