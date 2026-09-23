"""Independent bootstrap audit. Mocked journal/transport, never PostgreSQL/TLS."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_runtime as runtime
from app.trade_qualification.reservations import QualificationLedgerError
from tests.unit.test_qualification_account_capture import NOW
from tests.unit.test_qualification_account_materializer import (
    expired_hold,
    ledger_evidence,
)
from tests.unit.test_qualification_bootstrap_runtime import setup, unknown_state


class Context:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        return Context()


@pytest.mark.asyncio
async def test_initializer_rejects_clock_regression_between_commit_and_readback(
    monkeypatch,
):
    state = unknown_state()
    row = SimpleNamespace(
        account_revision=0, ledger_revision=0, claims_json=None, claims_sha256=None
    )
    times = iter(
        [
            NOW,
            NOW + timedelta(seconds=10),
            NOW + timedelta(seconds=5),
            NOW + timedelta(seconds=11),
        ]
    )

    async def locked(self, session, scope, **kwargs):
        return row

    async def current(self, session, scope, row):
        return state

    monkeypatch.setattr(QualificationLedgerRepository, "_locked", locked)
    monkeypatch.setattr(QualificationLedgerRepository, "_state", current)
    repo = QualificationLedgerRepository(Context, clock=lambda: next(times))
    with pytest.raises(QualificationLedgerError, match="clock"):
        await repo.initialize_capture_scope(state.scope)


@pytest.mark.asyncio
async def test_two_concurrent_consumers_share_one_use(monkeypatch):
    session, harness, seen, args = setup(monkeypatch)
    results = await asyncio.gather(
        session.collect_bootstrap(**args),
        session.collect_bootstrap(**args),
        return_exceptions=True,
    )
    good = [v for v in results if not isinstance(v, Exception)]
    errors = [v for v in results if isinstance(v, Exception)]
    assert len(good) == len(errors) == 1
    assert str(errors[0]) == "account_session_already_used"
    assert good[0].admission == "DENY"
    assert len(seen) == 2
    harness.assert_closed()


@pytest.mark.asyncio
async def test_bootstrap_use_prevents_materialization_reuse(monkeypatch):
    session, harness, _seen, args = setup(monkeypatch)
    await session.collect_bootstrap(**args)
    count = len(harness.requests)
    with pytest.raises(
        runtime.AccountRuntimeError, match="account_session_already_used"
    ):
        await session.collect_and_materialize(
            **args, inputs=None, expected_inputs_sha256=""
        )
    assert count == len(harness.requests)


@pytest.mark.asyncio
async def test_active_hold_disappearance_rejects_even_if_revisions_unchanged(
    monkeypatch,
):
    before = ledger_evidence(active=(expired_hold(),)).state
    after = before.model_copy(update={"active": ()})
    session, harness, _seen, args = setup(monkeypatch, states=[before, after])
    with pytest.raises(
        runtime.AccountRuntimeError, match="ledger_revision_changed_during_capture"
    ):
        await session.collect_bootstrap(**args)
    harness.assert_closed()


@pytest.mark.asyncio
async def test_unknown_repository_callback_is_not_invoked(monkeypatch):
    session, harness, seen, args = setup(monkeypatch)

    class Hostile:
        def __getattribute__(self, name):
            pytest.fail("foreign callback reached")

    args["repository"] = Hostile()
    with pytest.raises(
        runtime.AccountRuntimeError, match="owned_qualification_repository_required"
    ):
        await session.collect_bootstrap(**args)
    assert not harness.requests and not seen


@pytest.mark.asyncio
async def test_initialized_claims_still_cannot_become_complete_or_authorized(
    monkeypatch,
):
    state = ledger_evidence().state
    session, harness, _seen, args = setup(monkeypatch, states=[state, state])
    result = await session.collect_bootstrap(**args)
    assert result.ledger_checkpoint.account_initialized
    assert result.account_complete is result.execution_authority is False
    assert result.admission == "DENY"
    assert "registration_provenance_unverified" in result.blocking_reasons
    assert "bootstrap_source_verification_required" in result.blocking_reasons
    assert result.ledger_checkpoint.state.claims_sha256 == state.claims_sha256
    harness.assert_closed()


@pytest.mark.asyncio
async def test_cancelled_after_raw_capture_still_consumes_session(monkeypatch):
    session, harness, _seen, args = setup(monkeypatch)

    async def cancelled(self, scope):
        raise asyncio.CancelledError

    monkeypatch.setattr(
        QualificationLedgerRepository, "read_bootstrap_checkpoint", cancelled
    )
    with pytest.raises(asyncio.CancelledError):
        await session.collect_bootstrap(**args)
    assert harness.requests
    harness.assert_closed()
    with pytest.raises(
        runtime.AccountRuntimeError, match="account_session_already_used"
    ):
        await session.collect_bootstrap(**args)


@pytest.mark.parametrize(
    "field", ("account_revision", "ledger_revision", "claims_sha256")
)
def test_unknown_checkpoint_scalar_cannot_invoke_foreign_callback(field):
    from app.database.repositories.qualification_ledger import LedgerBootstrapCheckpoint

    class Hostile:
        def __getattribute__(self, name):
            pytest.fail("foreign checkpoint scalar callback")

    state = unknown_state().model_copy(update={field: Hostile()})
    value = LedgerBootstrapCheckpoint(state, NOW, NOW, "a" * 64)
    with pytest.raises((ValueError, TypeError)):
        runtime.ControlledDemoAccountSession._check_bootstrap_checkpoint(
            value, unknown_state().scope
        )


def test_unknown_checkpoint_timezone_cannot_invoke_utcoffset():
    from datetime import tzinfo

    from app.database.repositories.qualification_ledger import LedgerBootstrapCheckpoint
    from app.trade_qualification import reservations

    class HostileTZ(tzinfo):
        def utcoffset(self, dt):
            pytest.fail("foreign timezone callback")

    state = unknown_state()
    value = LedgerBootstrapCheckpoint(
        state, NOW.replace(tzinfo=HostileTZ()), NOW, reservations.digest(state)
    )
    with pytest.raises((ValueError, TypeError)):
        runtime.ControlledDemoAccountSession._check_bootstrap_checkpoint(
            value, state.scope
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method", ("initialize_capture_scope", "read_bootstrap_checkpoint")
)
@pytest.mark.parametrize("field", ("environment", "account_id", "settlement_currency"))
async def test_bootstrap_scope_rejects_foreign_scalar_before_database(method, field):
    class Hostile:
        def __getattribute__(self, name):
            pytest.fail("foreign scope callback")

    def no_session():
        pytest.fail("database touched for malformed scope")

    repo = QualificationLedgerRepository(no_session, clock=lambda: NOW)
    scope = unknown_state().scope.model_copy(update={field: Hostile()})
    with pytest.raises(QualificationLedgerError, match="bootstrap"):
        await getattr(repo, method)(scope)


@pytest.mark.parametrize(
    "junction", ("scope", "hold", "coverage", "operands", "fields")
)
def test_bootstrap_nested_foreign_values_rejected_without_callbacks(junction):
    from app.database.repositories.qualification_ledger import LedgerBootstrapCheckpoint

    class HostileMeta(type):
        def __eq__(cls, other):
            pytest.fail("foreign metaclass equality")

        def __hash__(cls):  # noqa: PLE0309 -- any invocation is a test failure
            pytest.fail("foreign metaclass hash")

    class Hostile(metaclass=HostileMeta):
        def __getattribute__(self, name):
            pytest.fail("foreign nested callback")

    hold = expired_hold()
    state = ledger_evidence(active=(hold,)).state
    if junction == "scope":
        state = state.model_copy(update={"scope": Hostile()})
    elif junction == "hold":
        state = state.model_copy(update={"active": (Hostile(),)})
    elif junction == "coverage":
        hold = hold.model_copy(update={"coverage": Hostile()})
        state = state.model_copy(update={"active": (hold,)})
    elif junction == "operands":
        operands = hold.coverage.candidate.model_copy(update={"entry": Hostile()})
        coverage = hold.coverage.model_copy(update={"candidate": operands})
        state = state.model_copy(
            update={"active": (hold.model_copy(update={"coverage": coverage}),)}
        )
    else:
        object.__setattr__(state, "__pydantic_fields_set__", Hostile())
    checkpoint = LedgerBootstrapCheckpoint(state, NOW, NOW, "a" * 64)
    with pytest.raises(QualificationLedgerError, match="bootstrap"):
        runtime.ControlledDemoAccountSession._check_bootstrap_checkpoint(
            checkpoint, unknown_state().scope
        )
