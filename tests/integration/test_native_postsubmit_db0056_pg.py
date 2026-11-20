"""Actual restricted-PG retained DATA transactions, without venue access."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from app.core import native_locked_demo_control_0056_v1 as locked
from app.core.native_role_witness_0056_v1 import (
    observe_restricted_native_roles_0056_witness_v1,
)
from app.database.repositories import native_postsubmit_v1 as repository
from app.database.repositories.demo_control import DemoControlRepository
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification.demo_control import ControlPins, ControlScope
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _delta56_identifier,
    _open_actual_0056_role_observation_lab,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_SOURCE_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def _postsubmit0056_lab():
    async with _open_actual_0056_role_observation_lab(grant_demo=True) as lab:
        yield lab


async def test_actual0056_stopped_postsubmit_transactions_preserve_control_and_catalog(
    monkeypatch, _postsubmit0056_lab
):
    owner_engine, owner_factory, selected, _ = _postsubmit0056_lab
    witness = await observe_restricted_native_roles_0056_witness_v1(
        selected, source=_SOURCE_ROOT
    )
    scope = LedgerScope(
        environment="demo", account_id="730057", settlement_currency="USDT"
    )
    control_scope = ControlScope(scope.environment, scope.account_id)
    owner = DemoControlRepository(owner_factory, clock=lambda: datetime.now(UTC))
    await owner.acquire(
        control_scope,
        owner_token=b"p" * 32,
        pins=ControlPins("a" * 64, "b" * 64, "c" * 64),
        command_id="d" * 64,
    )
    stopped = await owner.latch_stop(control_scope, command_id="e" * 64)
    await QualificationLedgerRepository(
        owner_factory, clock=lambda: datetime.now(UTC)
    ).initialize_capture_scope(scope)

    # Only the native registry handoff is synthetic. The original production
    # transaction, TLS, exact restricted role/catalog, UID locks and SQL reads
    # are real; no selector/source/intent/venue permission is minted here.
    binding = object()
    view = SimpleNamespace(scope=scope, session_factory=selected.writer_factory)
    monkeypatch.setattr(repository, "_source", lambda actual: ({}, view))
    monkeypatch.setattr(
        repository, "_database_profile", lambda actual: (selected, witness)
    )
    sessions, transactions = [], []

    async def observe(*, abort=False):
        async with repository._transaction(binding) as parts:
            session, state, event, metadata = parts[3], parts[5], parts[6], parts[7]
            sessions.append(session)
            transactions.append(metadata[8])
            assert state == stopped.state and event == stopped.event_sha256
            assert state.emergency_stop is True and state.arm_request_id is None
            assert parts[4].account_revision == parts[4].ledger_revision == 0
            assert (
                await repository._checked_postsubmit_catalog(binding, session) is None
            )
            async with owner_engine.begin() as competing:
                with pytest.raises(DBAPIError) as blocked:
                    await competing.exec_driver_sql(
                        "LOCK TABLE public.demo_collector_issued_account_revisions "
                        "IN ACCESS EXCLUSIVE MODE NOWAIT"
                    )
                assert blocked.value.orig.sqlstate == "55P03"
            if abort:
                raise RuntimeError("synthetic_process_abort")
        assert session not in repository._HELD_POSTSUBMIT_0056

    await observe()
    with pytest.raises(RuntimeError, match="^synthetic_process_abort$"):
        await observe(abort=True)
    assert all(session not in repository._HELD_POSTSUBMIT_0056 for session in sessions)
    await observe()
    assert len(set(transactions)) == 3

    # An effective extra grant must fail the next exact catalog observation;
    # it must never select the permissive old55 branch or manufacture a seal.
    role = _delta56_identifier(selected.writer_factory.kw["bind"].url.username)
    async with owner_engine.begin() as connection:
        await connection.exec_driver_sql(
            f"GRANT SELECT ON public.gate3_fitted_candidate_bindings TO {role}"
        )
    try:
        with pytest.raises(locked.NativeLockedDemoControl0056Error):
            async with repository._transaction(binding):
                pytest.fail("catalog drift reached the retained DATA body")
    finally:
        async with owner_engine.begin() as connection:
            await connection.exec_driver_sql(
                f"REVOKE SELECT ON public.gate3_fitted_candidate_bindings FROM {role}"
            )
    await observe()
    actual = await owner.read(control_scope)
    assert actual.state == stopped.state and actual.event_sha256 == stopped.event_sha256
