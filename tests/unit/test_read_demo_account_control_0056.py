"""The readback refuses a stale or ambiguous Control event and rolls back."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.trade_qualification.demo_control import ControlScope
from scripts import read_demo_account_control_0056 as reader

_EVENT = "1" * 64
_SCOPE = ControlScope("demo", "730005")


@pytest.fixture
def sample(monkeypatch):
    tx = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    state = SimpleNamespace(
        scope=_SCOPE, revision=8, emergency_stop=True, arm_request_id=None
    )
    proof = SimpleNamespace(
        control=SimpleNamespace(state=state, event_sha256=_EVENT),
        execution_authority=False,
        issuance_authority=False,
    )
    checkpoints = Mock()

    class Session:
        begin = AsyncMock(return_value=tx)
        scalars = AsyncMock(
            return_value=SimpleNamespace(all=lambda: [_SCOPE.account_id])
        )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    roles = object()

    @asynccontextmanager
    async def held(selected, session, scope, **kwargs):
        assert selected.writer_factory is Session
        assert type(session) is Session
        assert scope == _SCOPE
        assert kwargs == {
            "roles": roles,
            "purpose": reader.NativeWriterControlPurpose0056.PREPARE_OBSERVATION,
        }
        try:
            yield SimpleNamespace(observation=proof, check_held=checkpoints)
        except reader.startup.deployment.NativeDeploymentDescriptor0056Error as exc:
            raise RuntimeError(
                "held control context normalized an internal error"
            ) from exc

    monkeypatch.setattr(reader, "held_native_writer_control_0056_v1", held)
    selected = SimpleNamespace(writer_factory=Session)
    return SimpleNamespace(
        selected=selected,
        roles=roles,
        session=Session,
        proof=proof,
        checkpoints=checkpoints,
        transaction=tx,
    )


async def test_account_readback_discloses_no_account_identity_and_releases_lock(sample):
    result = await reader._read_selected_control(sample.selected, sample.roles, _EVENT)

    assert result == {
        "control_event_sha256": _EVENT,
        "control_revision": 8,
        "emergency_stop": True,
        "arm_request_recorded": False,
    }
    assert sample.session.scalars.await_args.args[1] == {"event": _EVENT}
    sample.checkpoints.assert_called_once()
    sample.transaction.rollback.assert_awaited_once()
    sample.transaction.commit.assert_not_awaited()


@pytest.mark.parametrize("matches", [[], ["730005", "730006"]])
async def test_account_readback_refuses_unknown_or_ambiguous_scopes(sample, matches):
    sample.session.scalars.return_value = SimpleNamespace(all=lambda: matches)

    with pytest.raises(reader.startup.deployment.NativeDeploymentDescriptor0056Error):
        await reader._read_selected_control(sample.selected, sample.roles, _EVENT)
    sample.checkpoints.assert_not_called()
    sample.transaction.rollback.assert_awaited_once()


async def test_account_readback_refuses_historical_event_as_current(sample):
    sample.proof.control.event_sha256 = "2" * 64

    with pytest.raises(reader.startup.deployment.NativeDeploymentDescriptor0056Error):
        await reader._read_selected_control(sample.selected, sample.roles, _EVENT)
    sample.checkpoints.assert_called_once()
    sample.transaction.rollback.assert_awaited_once()


def test_cli_bad_event_is_redacted_and_no_operation_runs(monkeypatch, capsys):
    operation = AsyncMock()
    monkeypatch.setattr(reader, "read_configured_account_control", operation)
    result = reader.main(
        [
            "--source-archive-file",
            "private-path-1",
            "--descriptor-file",
            "private-path-2",
            "--control-event-sha256",
            "private-uid",
        ]
    )
    assert result == 1
    operation.assert_not_awaited()
    stdout = capsys.readouterr().out
    assert '"status":"CONTROL_UNCONFIRMED"' in stdout
    assert "private" not in stdout


async def test_configured_readback_refuses_changed_second_transaction(monkeypatch):
    deploy = reader.startup.deployment
    installed = SimpleNamespace(trust_sha256="a" * 64)
    descriptor = object()
    settings = object()
    selected = object()
    roles = object()
    release = Mock(return_value=(installed, descriptor))
    check_settings = Mock()
    check_roles = Mock()
    dispose = AsyncMock()
    compare = AsyncMock(side_effect=[{"control_revision": 8}, {"control_revision": 9}])
    monkeypatch.setattr(reader.startup, "_read_bound_release", release)
    monkeypatch.setattr(deploy, "get_settings", lambda: settings)
    monkeypatch.setattr(deploy, "_check_runtime_settings", check_settings)
    monkeypatch.setattr(deploy, "_check_restricted_role_observation", check_roles)
    monkeypatch.setattr(
        deploy.selectors, "_dispose_failed_selection_factories_v2", dispose
    )
    monkeypatch.setattr(
        deploy.selectors, "select_configured_native_demo_factories_v2", lambda: selected
    )
    monkeypatch.setattr(
        deploy.identity,
        "observe_restricted_role_identity_0056_v2",
        AsyncMock(return_value=roles),
    )
    monkeypatch.setattr(reader, "_read_selected_control", compare)

    with pytest.raises(reader.startup.deployment.NativeDeploymentDescriptor0056Error):
        await reader.read_configured_account_control(
            descriptor_file=object(),
            source_archive_file=object(),
            control_event_sha256=_EVENT,
        )

    check_roles.assert_called_once_with(roles, descriptor)
    check_settings.assert_called_once_with(settings, descriptor)
    assert compare.await_count == 2
    dispose.assert_awaited_once_with(selected)
