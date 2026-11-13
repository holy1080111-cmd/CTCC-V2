"""Verify manual authentication and safe cleanup before any configured Demo call."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from scripts import clear_native_demo_stop_0056 as cli


@pytest.mark.asyncio
async def test_exact_confirmation_and_token_before_selecting_any_runtime(monkeypatch):
    auth = AsyncMock(side_effect=ValueError("private configured value"))
    bindings = Mock()
    monkeypatch.setattr(cli, "require_ctcc_token", auth)
    monkeypatch.setattr(cli, "_bindings", bindings)
    with pytest.raises(ValueError, match="db0056_demo_stop_clear_refused"):
        await cli.clear_configured_demo_stop_0056(
            instrument="BTC-USDT-SWAP", token="secret", confirmation="wrong"
        )
    auth.assert_not_awaited()
    with pytest.raises(ValueError):
        await cli.clear_configured_demo_stop_0056(
            instrument="BTC-USDT-SWAP",
            token="secret",
            confirmation=cli.CLEAR_CONFIRMATION_V2,
        )
    auth.assert_awaited_once_with("secret")
    bindings.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", [None, "before", "select", "clear", "arm", "release", "lease"]
)
async def test_only_demo_owner_clears_then_releases_or_retightens(monkeypatch, fault):
    events = []
    settings, roles, factories = object(), object(), object()
    record = SimpleNamespace(scope_config=SimpleNamespace(expected_uid="12345"))
    selection = SimpleNamespace(factories=factories)
    scope = cli.ControlScope("demo", "12345")

    def value(revision, *, stopped=False, armed=False):
        return SimpleNamespace(
            execution_authority=False,
            state=SimpleNamespace(
                scope=scope,
                revision=revision,
                emergency_stop=stopped,
                arm_request_id="arm" if armed else None,
                arm_expires_at=None,
            ),
        )

    async def auth(token):
        assert token == "secret"
        events.append("authenticate")

    def lease_check():
        events.append("lease")
        if fault == "lease":
            raise RuntimeError("private lease")

    lease = SimpleNamespace(verify_current=lease_check)

    @contextmanager
    def pinned():
        events.append("enter")
        try:
            yield lease
        finally:
            events.append("exit")

    async def select(*, private_client, read_session, instrument_ids):
        assert private_client is client and read_session is lease
        assert instrument_ids == ("BTC-USDT-SWAP",)
        assert events == ["authenticate", "enter"]
        events.append("select")
        return selection

    async def dispose(actual, chosen):
        assert actual is selection and chosen is factories
        events.append("dispose")

    async def prepare(actual):
        assert actual is selection and events[-1] == "authenticate"
        events.append("prepare")
        return owner

    async def clear(**kwargs):
        assert kwargs == {
            "confirmation": cli.CLEAR_CONFIRMATION_V2,
            "private_proof_root": "raw",
            "native_proof_root": "native",
        }
        events.append("clear")
        if fault == "clear":
            raise RuntimeError("private outcome")
        return value(4, armed=fault == "arm")

    async def release():
        events.append("release")
        if fault == "release":
            raise RuntimeError("private release")
        return value(5)

    async def stop():
        events.append("retighten")

    owner = SimpleNamespace(clear_stop=clear, release=release, emergency_stop=stop)
    client = SimpleNamespace(pinned_read_session=pinned)
    repository = SimpleNamespace(
        read=AsyncMock(return_value=value(2, stopped=fault != "before"))
    )
    monkeypatch.setattr(cli, "require_ctcc_token", auth)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "_bindings",
        lambda: (
            lambda _: settings,
            dispose,
            SimpleNamespace(
                _NOW=object(), prepare_selected_demo_control_owner_0056=prepare
            ),
        ),
    )
    monkeypatch.setattr(cli, "_proof_roots", lambda _: ("raw", "native"))
    monkeypatch.setattr(cli, "OkxDemoPrivateRestClient", lambda **kwargs: client)
    monkeypatch.setattr(
        cli, "DemoControlRepository0056V1", lambda *args, **kwargs: repository
    )
    monkeypatch.setattr(cli, "ControlObservation", SimpleNamespace)
    monkeypatch.setattr(
        cli.selectors, "_select_configured_demo_controlled_identity_0056", select
    )
    monkeypatch.setattr(
        cli.selectors,
        "_controlled_identity_0056",
        lambda _: (record, object() if fault == "select" else factories, roles),
    )
    operation = cli.clear_configured_demo_stop_0056(
        instrument="BTC-USDT-SWAP",
        token="secret",
        confirmation=cli.CLEAR_CONFIRMATION_V2,
    )
    if fault is None:
        assert (await operation).state.revision == 5
        assert events == [
            "authenticate",
            "enter",
            "select",
            "authenticate",
            "prepare",
            "clear",
            "lease",
            "release",
            "exit",
            "dispose",
        ]
    else:
        with pytest.raises((ValueError, RuntimeError)):
            await operation
        assert events[-2:] == ["exit", "dispose"]
        if fault in {"before", "select"}:
            assert "prepare" not in events and "retighten" not in events
        else:
            assert events[-3] == "retighten"


def test_uncertain_cli_never_prints_token_or_zero_control_mutations(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        cli, "_operator_input", lambda: ("secret-value", cli.CLEAR_CONFIRMATION_V2)
    )
    monkeypatch.setattr(
        cli,
        "clear_configured_demo_stop_0056",
        AsyncMock(side_effect=RuntimeError("secret-value")),
    )
    assert cli.main(["--instrument", "BTC-USDT-SWAP"]) == 1
    result = capsys.readouterr().out
    assert '"status":"CONTROL_CLEAR_UNCONFIRMED"' in result
    assert '"order_writes":0' in result and "secret-value" not in result
    assert "control_writes" not in result
