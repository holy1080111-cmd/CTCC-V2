"""Restricted-PG restart-clear primitive; source handoff remains synthetic."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.native_role_witness_0056_v1 import (
    observe_restricted_native_roles_0056_witness_v1,
)
from app.database.models.demo_automation import DemoAutomationState
from app.database.models.okx_demo import OkxDemoSyncCheckpoint
from app.database.models.operations import AuditLog
from app.database.repositories import demo_operator_acknowledgements_current55_v1 as ack
from app.database.repositories import (
    native_legacy_control_clear_0056_v1 as proof_policy,
)
from app.database.repositories import native_legacy_control_genesis_0056_v1 as genesis
from app.database.repositories.demo_control import DemoControlRepository
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.source_primitives import canonical, sha
from app.trade_qualification.demo_control import ControlScope
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _open_actual_0056_role_observation_lab,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_SOURCE_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def _restart0056_lab():
    async with _open_actual_0056_role_observation_lab(grant_demo=True) as lab:
        yield lab


async def test_actual0056_restart_clear_preserves_history_rolls_back_and_reopens_audit(
    _restart0056_lab,
):
    _, owner_factory, selected, _ = _restart0056_lab
    witness = await observe_restricted_native_roles_0056_witness_v1(
        selected, source=_SOURCE_ROOT
    )
    scope = LedgerScope(
        environment="demo", account_id="730059", settlement_currency="USDT"
    )
    control_scope = ControlScope(scope.environment, scope.account_id)
    seed_at = datetime.now(UTC) - timedelta(seconds=1)
    original = genesis._stopped_values(seed_at, "a" * 64)
    original.update(
        control_revision=7,
        lock_reasons=["emergency_stop_engaged", "restart_requires_explicit_clear_stop"],
        daily_pnl=Decimal("-1.25"),
        trades_today=3,
        consecutive_losses=1,
        realized_pnl_events=[{"net_pnl": "-1.25", "closed_at": seed_at.isoformat()}],
        symbol_cooldowns={"BTC-USDT-SWAP": seed_at.isoformat()},
        last_started_at=seed_at - timedelta(seconds=4),
        last_completed_at=seed_at - timedelta(seconds=2),
    )
    async with owner_factory.begin() as session:
        session.add(DemoAutomationState(**original))
        session.add(
            OkxDemoSyncCheckpoint(
                id=1,
                status="reconciled",
                order_count=0,
                position_count=0,
                algo_order_count=0,
                reconciled_at=seed_at,
                last_error=None,
                details={
                    "account_uid_sha256": sha(scope.account_id.encode("ascii")),
                    "credential_session_binding_id": "4" * 64,
                },
            )
        )
    stopped = await DemoControlRepository(
        owner_factory, clock=lambda: datetime.now(UTC)
    ).latch_stop(control_scope, command_id="b" * 64)
    await QualificationLedgerRepository(
        owner_factory, clock=lambda: datetime.now(UTC)
    ).initialize_capture_scope(scope)

    # The registry handoff is synthetic; all following writer/TLS/Control,
    # singleton CAS, checkpoint locks, audit INSERT and readback are real.
    replay = SimpleNamespace(
        purpose="clear_stop",
        session_factory=selected.writer_factory,
        _owned_database_0056=(selected, witness),
        previous_control=stopped.state,
        previous_control_event_sha256=stopped.event_sha256,
        scope=scope,
        expires_at=datetime.now(UTC) + timedelta(seconds=25),
        lineage=SimpleNamespace(lineage_sha256="c" * 64),
        current=SimpleNamespace(
            current_receipt_json=canonical(
                {
                    "observed_flat": True,
                    "blocking_reasons": [],
                    "observed_inventory_state": "observed_empty_supported_queries",
                    "identity": {
                        "environment": "demo",
                        "uid": scope.account_id,
                        "settlement_currency": "USDT",
                    },
                }
            ),
            packet=SimpleNamespace(plan=SimpleNamespace(session_binding_id="4" * 64)),
        ),
    )

    async def clear_once(*, abort=False):
        ledger = QualificationLedgerRepository(selected.writer_factory, clock=ack._time)
        async with (
            selected.writer_factory() as session,
            session.begin(),
            ack._transaction(session, lambda: replay) as held,
        ):
            await session.execute(ack._LEGACY_CLEAR_FENCE)
            scope_row = await ledger._locked(session, scope)
            point = await ledger._read_portfolio_checkpoint_locked(
                session, scope, scope_row, _held0056=held
            )
            replay._owned_mirror_checkpoint_0056 = point.document_json
            result, proof = await ack._clear_stopped_legacy(
                session, lambda: replay, ledger, held, scope_row, point
            )
            assert type(proof) is ack._RestartLegacyClear
            assert result.known_flat and held.control.control.state.emergency_stop
            if abort:
                raise RuntimeError("synthetic_crash_before_commit")
            before, after = ack._legacy_audit_documents(proof, replay)
            audit_id = "d" * 64
            session.add(
                AuditLog(
                    actor=proof_policy.ACTOR,
                    action=proof.action,
                    resource_type=proof_policy.RESOURCE,
                    resource_id=audit_id,
                    before=before,
                    after=after,
                    occurred_at=proof.cleared_at,
                )
            )
            await session.flush()
            metadata = await ack._metadata(session)
            marker = SimpleNamespace(
                ack_sha256=audit_id,
                owner_sampled_at=metadata[9],
                proofs=(("local_reconciliation", sha(proof.proof(replay))),),
            )
            await ack._verify_legacy_audit(session, replay, marker, proof, result)
        return proof, marker, metadata

    with pytest.raises(RuntimeError, match="^synthetic_crash_before_commit$"):
        await clear_once(abort=True)
    async with owner_factory() as session:
        rolled_back = await session.get(DemoAutomationState, 1)
        assert rolled_back.control_revision == 7 and rolled_back.emergency_stop
        assert rolled_back.realized_pnl_events == original["realized_pnl_events"]
        assert not (await session.scalars(select(AuditLog))).all()

    proof, marker, write_metadata = await clear_once()
    async with (
        selected.writer_factory() as session,
        session.begin(),
        ack._transaction(session, lambda: replay) as held,
    ):
        ledger = QualificationLedgerRepository(selected.writer_factory, clock=ack._time)
        scope_row = await ledger._locked(session, scope)
        current = await ledger._read_portfolio_checkpoint_locked(
            session, scope, scope_row, _held0056=held
        )
        await ack._verify_legacy_audit(session, replay, marker, proof, current)
        metadata = await ack._metadata(session)
        assert metadata[8] != write_metadata[8]
        assert metadata[9] >= write_metadata[9]
        assert current.document_json == proof.after
        with pytest.raises(ValueError):
            await ack._clear_stopped_legacy(
                session, lambda: replay, ledger, held, scope_row, current
            )
    async with owner_factory() as session:
        row = await session.get(DemoAutomationState, 1)
        assert row.control_revision == 8 and row.armed is False
        assert row.restart_latch_required is True
        assert row.emergency_stop is row.locked is False
        assert row.daily_pnl == Decimal("-1.25")
        assert row.trades_today == 3 and row.consecutive_losses == 1
        assert row.symbol_cooldowns == original["symbol_cooldowns"]
        assert row.realized_pnl_events == original["realized_pnl_events"]
