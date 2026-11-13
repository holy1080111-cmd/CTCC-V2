"""Offline checks for explicit DB0056 stopped genesis and reconcile-only."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core import native_stopped_control_genesis_0056_v1 as subject
from app.database.repositories.demo_control import ControlObservation
from app.trade_qualification.demo_control import ControlScope, stopped_genesis
from scripts import initialize_demo_stopped_control_0056 as cli

_SCOPE = ControlScope("demo", "123")
_NOW = datetime(2026, 11, 14, tzinfo=UTC)


@pytest.fixture
def operation(monkeypatch):
    state = stopped_genesis(_SCOPE, _NOW)
    event = subject._exact_genesis_event(_SCOPE, state)
    calls = []
    transaction = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    class Session:
        begin = AsyncMock(return_value=transaction)

        async def __aenter__(self):
            calls.append("open")
            return self

        async def __aexit__(self, *_):
            calls.append("close")

    factories = SimpleNamespace(writer_factory=Mock(side_effect=Session))
    selection = SimpleNamespace(
        source_roles_0056=object(), scope_config=SimpleNamespace(expected_uid="123")
    )
    genuine = {"factories": factories}
    monkeypatch.setattr(subject.selectors, "_record", Mock(return_value=genuine))
    stage = AsyncMock(return_value=(True, state, event))

    async def read(*_):
        calls.append("readback")
        return ControlObservation(state, event, _NOW)

    readback = AsyncMock(side_effect=read)
    monkeypatch.setattr(subject, "_stage_genesis", stage)
    monkeypatch.setattr(subject, "_read_committed_genesis", readback)
    return SimpleNamespace(
        selection=selection,
        transaction=transaction,
        calls=calls,
        stage=stage,
        readback=readback,
        state=state,
        event=event,
        factories=factories,
    )


async def test_commit_then_independent_readback(operation):
    result = await subject._operate_selected_genesis(
        operation.selection, reconcile_only=False
    )
    operation.transaction.commit.assert_awaited_once()
    assert operation.calls == ["open", "close", "readback"]
    assert result.status == "CREATED_STOPPED"
    assert result.control_event_sha256 == operation.event
    assert result.emergency_stop is True
    assert result.armed is result.execution_authority is False


async def test_repeat_matching_genesis_rolls_back_then_reads(operation):
    operation.stage.return_value = (False, operation.state, operation.event)
    result = await subject._operate_selected_genesis(
        operation.selection, reconcile_only=False
    )
    operation.transaction.commit.assert_not_awaited()
    operation.transaction.rollback.assert_awaited_once()
    assert operation.calls == ["open", "close", "readback"]
    assert result.status == "EXISTING_STOPPED"


async def test_reconcile_only_cannot_enter_any_mutation_session(operation):
    result = await subject._operate_selected_genesis(
        operation.selection, reconcile_only=True
    )
    operation.factories.writer_factory.assert_not_called()
    operation.stage.assert_not_awaited()
    assert operation.calls == ["readback"]
    assert result.status == "EXISTING_STOPPED"


@pytest.mark.parametrize("point", ["commit", "readback"])
async def test_uncertain_result_does_not_retry(operation, point):
    if point == "commit":
        operation.transaction.commit.side_effect = OSError("private database route")
    else:
        operation.readback.side_effect = OSError("private database route")
    with pytest.raises(
        subject.NativeStoppedControlGenesis0056Error,
        match=f"^db0056_stopped_control_{point}_uncertain_reconcile_only$",
    ):
        await subject._operate_selected_genesis(
            operation.selection, reconcile_only=False
        )
    operation.stage.assert_awaited_once()
    operation.transaction.commit.assert_awaited_once()
    assert operation.readback.await_count == (0 if point == "commit" else 1)


async def test_wrong_uid_or_occupied_lineage_is_not_this_genesis():
    state = stopped_genesis(_SCOPE, _NOW)
    for wrong in (
        replace(state, revision=2),
        stopped_genesis(ControlScope("demo", "321"), _NOW),
    ):
        with pytest.raises(
            subject.NativeStoppedControlGenesis0056Error, match="genesis_refused"
        ):
            subject._exact_genesis_event(_SCOPE, wrong)


async def test_unauthenticated_selection_cannot_open_a_database_session(monkeypatch):
    monkeypatch.setattr(
        subject.selectors, "_record", Mock(side_effect=ValueError("untrusted scope"))
    )
    stage = AsyncMock()
    monkeypatch.setattr(subject, "_stage_genesis", stage)
    with pytest.raises(ValueError, match="untrusted scope"):
        await subject._operate_selected_genesis(object(), reconcile_only=False)
    stage.assert_not_awaited()


async def test_command_derives_uid_only_after_signed_selection(monkeypatch):
    from app.demo_automation import native_source_only_v1 as source_only

    settings = object()
    lease = object()
    selected = object()
    operation = AsyncMock(
        return_value=subject.StoppedControlGenesis0056V1("EXISTING_STOPPED", "b" * 64)
    )
    select = AsyncMock(return_value=selected)
    dispose = AsyncMock()
    factory_set = object()

    @contextmanager
    def pinned():
        yield lease

    client = SimpleNamespace(pinned_read_session=pinned)
    monkeypatch.setattr(source_only, "_settings", Mock(return_value=settings))
    monkeypatch.setattr(source_only, "_dispose", dispose)
    monkeypatch.setattr(subject, "OkxDemoPrivateRestClient", Mock(return_value=client))
    monkeypatch.setattr(subject, "_operate_selected_genesis", operation)
    monkeypatch.setattr(
        subject.selectors, "select_configured_demo_source_identity_0056_v1", select
    )
    monkeypatch.setattr(
        subject.selectors, "_SELECTIONS", {selected: {"factories": factory_set}}
    )
    result = await subject.initialize_configured_stopped_control_0056_v1(
        "BTC-USDT-SWAP", reconcile_only=True
    )
    select.assert_awaited_once_with(
        private_client=client, read_session=lease, instrument_ids=("BTC-USDT-SWAP",)
    )
    operation.assert_awaited_once_with(selected, reconcile_only=True)
    dispose.assert_awaited_once_with(selected, factory_set)
    assert result.admission == "DENY"


def test_cli_requires_explicit_mode_and_maps_uncertain_to_two(monkeypatch, capsys):
    operation = AsyncMock(
        side_effect=subject.NativeStoppedControlGenesis0056Error(
            "db0056_stopped_control_commit_uncertain_reconcile_only"
        )
    )
    monkeypatch.setattr(cli, "initialize_configured_stopped_control_0056_v1", operation)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--instrument", "BTC-USDT-SWAP"])
    assert exc.value.code == 2
    operation.assert_not_awaited()
    capsys.readouterr()

    assert cli.main(["--instrument", "BTC-USDT-SWAP", "--initialize-stopped"]) == 2
    operation.assert_awaited_once_with("BTC-USDT-SWAP", reconcile_only=False)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "db0056_stopped_control_commit_uncertain_reconcile_only\n"
