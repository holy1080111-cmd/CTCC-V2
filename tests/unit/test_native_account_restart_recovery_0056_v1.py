"""Recovery commits only a stop and reopens the account independently."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core import native_account_restart_recovery_0056_v1 as subject
from app.database.repositories.demo_control import ControlObservation
from app.trade_qualification import demo_control as control

_SCOPE = control.ControlScope("demo", "730005")
_AT = datetime(2026, 11, 14, tzinfo=UTC)
_PINS = control.ControlPins("1" * 64, "2" * 64, "3" * 64)


def _armed():
    acquired = control.acquire(
        None, scope=_SCOPE, owner_sha256="a" * 64, pins=_PINS, now=_AT
    )
    return control.advance(
        acquired,
        owner_sha256=acquired.owner_sha256,
        owner_epoch=acquired.owner_epoch,
        expected_revision=acquired.revision,
        action="arm_requested",
        command_id="b" * 64,
        now=_AT + timedelta(seconds=1),
        arm_expires_at=_AT + timedelta(seconds=10),
    )


async def test_expired_arm_is_durably_cleared_without_rebinding(monkeypatch):
    armed = _armed()
    now = _AT + timedelta(seconds=65)
    assert not armed.arm_intent_current(now)
    session = SimpleNamespace(scalar=AsyncMock(return_value=now))
    check_held = Mock()
    row = object()

    @asynccontextmanager
    async def verified(*args):
        yield row, object(), object(), now, check_held

    monkeypatch.setattr(subject.locked, "_verified_locked_demo_control_0056", verified)
    monkeypatch.setattr(subject, "_require_role_witness", Mock())
    monkeypatch.setattr(
        subject.locked.DemoControlRepositoryV2,
        "_snapshot",
        AsyncMock(return_value=(armed, "c" * 64)),
    )
    verify_lineage = AsyncMock()
    monkeypatch.setattr(
        subject.locked.DemoControlRepositoryV2,
        "verify_historical_binding",
        verify_lineage,
    )
    store = AsyncMock(return_value="d" * 64)
    monkeypatch.setattr(subject.locked.DemoControlRepository, "_store", store)
    changed, state, event = await subject._stop_locked(
        session, object(), object(), _SCOPE
    )
    assert changed and state.emergency_stop
    assert state.arm_request_id is state.arm_expires_at is None
    assert state.revision == armed.revision + 1 and state.lease_until == now
    assert (state.owner_epoch, state.owner_sha256, state.pins) == (
        armed.owner_epoch,
        armed.owner_sha256,
        armed.pins,
    )
    assert (
        control._lineage_transition(armed, state, action="estop", command_id="e" * 64)
        == state
    )
    assert store.await_args.kwargs["previous_sha256"] == "c" * 64
    verify_lineage.assert_awaited_once_with(session, state, event)


@pytest.fixture
def operation(monkeypatch):
    trace = []
    tx = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    class Session:
        begin = AsyncMock(return_value=tx)

        async def __aenter__(self):
            trace.append("open")
            return self

        async def __aexit__(self, *_):
            trace.append("close")

    selection = SimpleNamespace(
        source_roles_0056=object(),
        scope_config=SimpleNamespace(expected_uid=_SCOPE.account_id),
    )
    selected = {"factories": SimpleNamespace(writer_factory=Session)}
    state = control.stopped_genesis(_SCOPE, _AT)
    monkeypatch.setattr(subject.selectors, "_record", Mock(return_value=selected))
    stage = AsyncMock(return_value=(True, state, "e" * 64))
    monkeypatch.setattr(subject, "_stop_locked", stage)

    async def read(*_):
        trace.append("readback")
        return ControlObservation(state, "e" * 64, _AT)

    observation = AsyncMock(side_effect=read)
    monkeypatch.setattr(subject, "_observe_stop", observation)
    return SimpleNamespace(**locals())


@pytest.mark.parametrize(
    "changed,reconcile,status,trace",
    (
        (True, False, "STOP_PERSISTED", ["open", "close", "readback"]),
        (False, False, "STOP_OBSERVED", ["open", "close", "readback"]),
        (False, True, "STOP_OBSERVED", ["readback"]),
    ),
)
async def test_reopens_committed_stop_and_stays_deny(
    operation, changed, reconcile, status, trace
):
    operation.stage.return_value = changed, operation.state, "e" * 64
    result = await subject._recover_selected(
        operation.selection, reconcile_only=reconcile
    )
    assert result.status == status and operation.trace == trace
    assert result.admission == "DENY"
    assert (
        result.armed is result.positions_reconciled is result.startup_admission is False
    )
    assert operation.tx.commit.await_count == changed
    assert operation.tx.rollback.await_count == (not changed and not reconcile)


@pytest.mark.parametrize("phase", ["commit", "readback"])
async def test_commit_unknown_never_retries(operation, phase):
    failure = operation.tx.commit if phase == "commit" else operation.observation
    failure.side_effect = OSError("private data")
    with pytest.raises(
        subject.NativeAccountRestartRecovery0056Uncertain, match="reconcile_only"
    ):
        await subject._recover_selected(operation.selection, reconcile_only=False)
    operation.stage.assert_awaited_once()
    operation.tx.commit.assert_awaited_once()
    assert operation.observation.await_count == (phase == "readback")
