"""B5 isolated PostgreSQL only; original account responses remain synthetic.

The legacy-singleton case requires a fresh dedicated migrated B5 database. It
leaves its explicitly synthetic records in that database; no deployed fallback.
"""

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.demo_automation import DemoAutomationState
from app.database.models.okx_demo import (
    OkxDemoAlgoOrderState,
    OkxDemoBalanceState,
    OkxDemoOrderState,
    OkxDemoPositionState,
    OkxDemoSyncCheckpoint,
)
from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
)
from app.database.repositories.okx_demo import OkxDemoRepository
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.okx_demo import (
    OkxDemoAccountConfig,
    OkxDemoBalanceDetail,
    OkxDemoBalanceSnapshot,
    OkxDemoOrderView,
)
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


def _safe_first_append_failure(exc: Exception) -> str:
    """Keep the synthetic SQL diagnostic free of source and account values."""
    kind = type(exc).__name__
    if kind not in {
        "AccountObservationError",
        "DBAPIError",
        "IntegrityError",
        "ProgrammingError",
    }:
        kind = "other_exception"
    fields = [f"class={kind}"]
    if (
        type(exc) is AccountObservationError
        and len(exc.args) == 1
        and type(exc.args[0]) is str
        and exc.args[0]
        in {
            "observation_policy_epoch_mismatch",
            "observation_source_replay_invalid",
            "observation_predecessor_missing",
            "observation_continuation_prefix_incomplete",
            "observation_commit_readback_unknown",
            "observation_read_failed",
        }
    ):
        fields.append(f"code={exc.args[0]}")
    if isinstance(exc, DBAPIError):
        # SQLAlchemy asyncpg wrapping may keep PostgreSQL diagnostics on the
        # original error or its cause. Read only fixed fields and allowlists.
        for origin in (exc.orig, getattr(exc.orig, "__cause__", None)):
            if origin is None:
                continue
            state = getattr(origin, "sqlstate", None)
            if (
                type(state) is str
                and len(state) == 5
                and state.isascii()
                and state.isalnum()
                and not any(field.startswith("sqlstate=") for field in fields)
            ):
                fields.append(f"sqlstate={state}")
            diag = getattr(origin, "diag", None)
            constraint = getattr(diag, "constraint_name", None) or getattr(
                origin, "constraint_name", None
            )
            if (
                type(constraint) is str
                and constraint
                in {
                    "fk_account_observation_source",
                    "uq_account_observation_capture",
                    "fk_observation_fact_batch",
                    "fk_observation_coverage_batch",
                    "fk_observation_finding_batch",
                    "scope",
                    "sequence_bound",
                    "chain",
                    "byte_bound",
                    "bound",
                    "digests",
                }
                and not any(field.startswith("constraint=") for field in fields)
            ):
                fields.append(f"constraint={constraint}")
            primary = getattr(diag, "message_primary", None) or getattr(
                origin, "message", None
            )
            if (
                type(primary) is str
                and primary
                in {
                    "account_observation_append_denied",
                    "account_observation_membership_shape_denied",
                }
                and not any(field.startswith("db_guard=") for field in fields)
            ):
                fields.append(f"db_guard={primary}")
    return ",".join(fields)


async def _stored_with_first_append_diagnostic(monkeypatch, database):
    """Classify the one synthetic append without a second ambiguous write."""
    original_append = AccountObservationIndexRepository.append

    async def observed_append(self, scope, reference, window, *, expected_revision):
        if expected_revision != 0:
            return await original_append(
                self, scope, reference, window, expected_revision=expected_revision
            )
        try:
            return await self._append(scope, reference, window, expected_revision)
        except Exception as internal:  # noqa: BLE001 -- synthetic first-failure only
            classification = _safe_first_append_failure(internal)
            raise AssertionError(
                f"first_observation_append_failed:{classification}"
            ) from None

    monkeypatch.setattr(AccountObservationIndexRepository, "append", observed_append)
    try:
        return await stored(monkeypatch, database)
    finally:
        monkeypatch.setattr(
            AccountObservationIndexRepository, "append", original_append
        )


async def test_first_append_diagnostic_never_retries_ambiguous_failure(monkeypatch):
    attempts = []

    async def failed_append(self, scope, reference, window, expected_revision):
        attempts.append(expected_revision)
        raise AccountObservationError("observation_source_replay_invalid")

    async def synthetic_stored(_monkeypatch, _database):
        repo = AccountObservationIndexRepository(lambda: None, clock=now)
        return await repo.append(None, None, None, expected_revision=0)

    monkeypatch.setattr(AccountObservationIndexRepository, "_append", failed_append)
    monkeypatch.setitem(globals(), "stored", synthetic_stored)
    with pytest.raises(
        AssertionError,
        match="first_observation_append_failed:class=AccountObservationError,code=observation_source_replay_invalid",
    ):
        await _stored_with_first_append_diagnostic(monkeypatch, None)
    assert attempts == [0]


async def test_original_balance_pages_survive_restart_and_reject_prefix_or_head_changes(
    monkeypatch, database
):
    repo, scope, reference, _, first = await _stored_with_first_append_diagnostic(
        monkeypatch, database
    )
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
                details={
                    "account_uid_sha256": hashlib.sha256(
                        scope.account_id.encode("ascii")
                    ).hexdigest()
                },
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
    assert initial["legacy"]["sync_checkpoint"]["exact_uid_bound"] is True
    async with database[1]() as session, session.begin():
        checkpoint = await session.get(OkxDemoSyncCheckpoint, 1)
        assert checkpoint is not None
        checkpoint.details = {"account_uid_sha256": "0" * 64}
    wrong_uid_mirror = await repo.read_portfolio_checkpoint(scope)
    assert not wrong_uid_mirror.known_flat
    assert (
        "legacy_reconciliation_uid_unbound"
        in json.loads(wrong_uid_mirror.document_json)["blocking_reasons"]
    )
    async with database[1]() as session, session.begin():
        checkpoint = await session.get(OkxDemoSyncCheckpoint, 1)
        assert checkpoint is not None
        checkpoint.details = {
            "account_uid_sha256": hashlib.sha256(
                scope.account_id.encode("ascii")
            ).hexdigest()
        }
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


async def test_demo_mirror_terminal_history_is_locally_flat_but_pending_is_not(
    database,
):
    """Real SQL mirror and B5 readback; all account inputs are synthetic."""
    uid = f"123456789{uuid4().int % 10**12:012d}"
    scope = LedgerScope(account_id=uid, settlement_currency="USDT")
    order_id = f"synthetic-demo-order-{uuid4().hex}"
    pending_id = f"synthetic-demo-pending-{uuid4().hex}"
    historical_time = now() - timedelta(minutes=3)
    pending_time = historical_time + timedelta(minutes=1)
    terminal_time = pending_time + timedelta(minutes=1)
    account_config = OkxDemoAccountConfig(
        uid=uid,
        account_level="2",
        position_mode="net_mode",
        raw={"uid": uid, "acctLv": "2", "posMode": "net_mode"},
    )
    balance = OkxDemoBalanceSnapshot(
        total_equity=Decimal(100),
        isolated_equity=Decimal(0),
        adjusted_equity=Decimal(100),
        available_equity=Decimal(100),
        details=[
            OkxDemoBalanceDetail(
                currency="USDT",
                equity=Decimal(100),
                available_equity=Decimal(100),
                cash_balance=Decimal(100),
                available_balance=Decimal(100),
                frozen_balance=Decimal(0),
                unrealized_pnl=Decimal(0),
            )
        ],
    )

    def order(
        identity: str, state: str, updated_at: datetime | None
    ) -> OkxDemoOrderView:
        raw = {"ordId": identity, "state": state}
        if updated_at is not None:
            raw["uTime"] = str(int(updated_at.timestamp() * 1000))
        return OkxDemoOrderView(
            order_id=identity,
            instrument_id="BTC-USDT-SWAP",
            side="buy",
            position_side="net",
            order_type="limit",
            state=state,
            size=Decimal(1),
            accumulated_fill_size=Decimal("1" if state == "filled" else "0"),
            price=Decimal(60000),
            updated_at=updated_at,
            raw=raw,
        )

    # Roll back the whole SQL exercise, including singleton replacement, so
    # this test neither relies on nor mutates any prior synthetic B5 case.
    async with database[0].connect() as connection:
        outer = await connection.begin()
        try:
            for model in (
                OkxDemoSyncCheckpoint,
                OkxDemoOrderState,
                OkxDemoPositionState,
                OkxDemoAlgoOrderState,
                OkxDemoBalanceState,
                DemoAutomationState,
            ):
                await connection.execute(delete(model))
            sessions = async_sessionmaker(
                bind=connection,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
                join_transaction_mode="create_savepoint",
            )
            ledger = QualificationLedgerRepository(sessions, clock=now)
            mirror = OkxDemoRepository(sessions)
            await ledger.initialize_capture_scope(scope)
            unknown = json.loads(
                (await ledger.read_portfolio_checkpoint(scope)).document_json
            )
            assert set(unknown["blocking_reasons"]) == {
                "legacy_automation_initialization_unknown",
                "legacy_reconciliation_initialization_unknown",
            }

            async with sessions() as session, session.begin():
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
            without_mirror = json.loads(
                (await ledger.read_portfolio_checkpoint(scope)).document_json
            )
            assert without_mirror["blocking_reasons"] == [
                "legacy_reconciliation_initialization_unknown"
            ]

            async def sync(orders: list[OkxDemoOrderView]) -> str:
                binding_id = uuid4().hex + uuid4().hex
                await mirror.sync_snapshot(
                    account_config=account_config,
                    balance=balance,
                    positions=[],
                    orders=orders,
                    algo_orders=[],
                    credential_session_binding_id=binding_id,
                )
                return binding_id

            first_binding_id = await sync([order(order_id, "filled", historical_time)])
            async with sessions() as session:
                checkpoint = await session.get(OkxDemoSyncCheckpoint, 1)
                historical = await session.get(OkxDemoOrderState, order_id)
                assert checkpoint is not None and checkpoint.order_count == 0
                assert checkpoint.details["orders_observed_in_snapshot"] == 1
                assert historical is not None and historical.state == "filled"
            assert (await mirror.mirror_status()).order_count == 1
            flat = await ledger.read_portfolio_checkpoint(scope)
            assert flat.known_flat
            flat_document = json.loads(flat.document_json)
            assert flat_document["legacy"]["orders"] == []
            assert (
                flat_document["legacy"]["sync_checkpoint"][
                    "credential_session_binding_id"
                ]
                == first_binding_id
            )
            assert not flat_document["execution_authority"]
            assert not flat_document["future_writer_exclusion"]

            await sync(
                [
                    order(order_id, "filled", historical_time),
                    order(pending_id, "live", pending_time),
                ]
            )
            async with sessions() as session:
                checkpoint = await session.get(OkxDemoSyncCheckpoint, 1)
                assert checkpoint is not None and checkpoint.order_count == 1
                assert checkpoint.details["orders_observed_in_snapshot"] == 2
            pending = json.loads(
                (await ledger.read_portfolio_checkpoint(scope)).document_json
            )
            assert set(pending["blocking_reasons"]) == {
                "legacy_reconciliation_incomplete_or_exposed",
                "legacy_unattributed_active_orders",
            }
            assert pending["legacy"]["orders"] == [pending_id]

            # A malformed duplicate, an older terminal, an untime-stamped
            # terminal and an equal-time conflict all leave the active row and
            # checkpoint intact. The transaction must roll back every write.
            with pytest.raises(ValueError, match="order_identity_duplicate"):
                await sync(
                    [
                        order(order_id, "filled", historical_time),
                        order(pending_id, "live", pending_time),
                        order(pending_id, "live", pending_time),
                    ]
                )
            for rejected in (
                order(pending_id, "canceled", historical_time),
                order(pending_id, "canceled", None),
                order(pending_id, "canceled", pending_time),
            ):
                with pytest.raises(ValueError, match="order_revision_unproven"):
                    await sync([order(order_id, "filled", historical_time), rejected])
                async with sessions() as session:
                    checkpoint = await session.get(OkxDemoSyncCheckpoint, 1)
                    still_active = await session.get(OkxDemoOrderState, pending_id)
                    assert checkpoint is not None and checkpoint.order_count == 1
                    assert still_active is not None
                    assert still_active.state == "live"
                    assert still_active.exchange_updated_at == pending_time
                assert not (await ledger.read_portfolio_checkpoint(scope)).known_flat

            # A direct order mirror write has the same chronology guard. Its
            # rejection additionally latches the singleton error state,
            # because the exchange may already have acknowledged a submit.
            for rejected in (
                order(pending_id, "canceled", historical_time),
                order(pending_id, "canceled", None),
                order(pending_id, "canceled", pending_time),
            ):
                with pytest.raises(ValueError, match="order_revision_unproven"):
                    await mirror.upsert_orders(
                        [rejected], action="synthetic_order_update"
                    )
                async with sessions() as session:
                    checkpoint = await session.get(OkxDemoSyncCheckpoint, 1)
                    still_active = await session.get(OkxDemoOrderState, pending_id)
                    assert checkpoint is not None and checkpoint.status == "error"
                    assert still_active is not None and still_active.state == "live"
                assert not (await ledger.read_portfolio_checkpoint(scope)).known_flat
            with pytest.raises(ValueError, match="order_identity_duplicate"):
                await mirror.upsert_orders(
                    [
                        order(pending_id, "live", pending_time),
                        order(pending_id, "live", pending_time),
                    ],
                    action="synthetic_order_duplicate",
                )
            # A complete successful reconcile can restore the checkpoint,
            # while the row remains blocked until a strictly newer terminal.
            await sync(
                [
                    order(order_id, "filled", historical_time),
                    order(pending_id, "live", pending_time),
                ]
            )

            # A later bounded snapshot omits that pending order: neither the
            # persisted row nor the checkpoint can silently become zero.
            await sync([order(order_id, "filled", historical_time)])
            async with sessions() as session:
                checkpoint = await session.get(OkxDemoSyncCheckpoint, 1)
                assert checkpoint is not None and checkpoint.order_count == 1
                assert checkpoint.details["orders_observed_in_snapshot"] == 1
            assert not (await ledger.read_portfolio_checkpoint(scope)).known_flat

            # Only an explicit terminal state transition releases that local
            # blocker; account completeness and execution still need proof.
            await mirror.upsert_orders(
                [order(pending_id, "canceled", terminal_time)],
                action="synthetic_newer_terminal",
            )
            async with sessions() as session:
                checkpoint = await session.get(OkxDemoSyncCheckpoint, 1)
                closed = await session.get(OkxDemoOrderState, pending_id)
                assert checkpoint is not None and checkpoint.order_count == 1
                assert closed is not None and closed.state == "canceled"
            assert not (await ledger.read_portfolio_checkpoint(scope)).known_flat
            await sync(
                [
                    order(order_id, "filled", historical_time),
                    order(pending_id, "canceled", terminal_time),
                ]
            )
            async with sessions() as session:
                checkpoint = await session.get(OkxDemoSyncCheckpoint, 1)
                closed = await session.get(OkxDemoOrderState, pending_id)
                assert checkpoint is not None and checkpoint.order_count == 0
                assert closed is not None and closed.state == "canceled"
            assert (await mirror.mirror_status()).order_count == 2
            assert (await ledger.read_portfolio_checkpoint(scope)).known_flat

            # A caller-created config without the original raw UID cannot
            # authenticate the singleton mirror, even with no active rows.
            await mirror.sync_snapshot(
                account_config=account_config.model_copy(update={"raw": {}}),
                balance=balance,
                positions=[],
                orders=[
                    order(order_id, "filled", historical_time),
                    order(pending_id, "canceled", terminal_time),
                ],
                algo_orders=[],
            )
            unbound = await ledger.read_portfolio_checkpoint(scope)
            assert not unbound.known_flat
            assert (
                "legacy_reconciliation_uid_unbound"
                in json.loads(unbound.document_json)["blocking_reasons"]
            )
        finally:
            await outer.rollback()


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
