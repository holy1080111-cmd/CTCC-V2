from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import native_owner_grant_confirmation_0056_v1 as approval

_DIGEST = "3a" * 32
_ACKNOWLEDGEMENT = f"CTCC_DB0056_GRANT_42 {_DIGEST}"


@pytest.fixture
def terminal(monkeypatch):
    streams = SimpleNamespace(
        stdin=SimpleNamespace(fileno=lambda: 10),
        stdout=SimpleNamespace(fileno=lambda: 11),
    )
    monkeypatch.setattr(approval, "sys", streams)
    descriptors = SimpleNamespace(isatty=Mock(return_value=True))
    monkeypatch.setattr(approval, "os", descriptors)
    prompt = Mock(return_value=_ACKNOWLEDGEMENT)
    monkeypatch.setattr("builtins.input", prompt)
    return SimpleNamespace(streams=streams, descriptors=descriptors, prompt=prompt)


def test_confirms_only_the_exact_reviewed_plan(terminal):
    assert approval.confirm_owner_grant_0056_v1(_DIGEST) is None
    assert [call.args for call in terminal.descriptors.isatty.call_args_list] == [
        (10,),
        (11,),
    ]
    assert _ACKNOWLEDGEMENT in terminal.prompt.call_args.args[0]


@pytest.mark.parametrize("not_terminal", [10, 11])
def test_redirected_terminal_refuses_before_reading_stdin(terminal, not_terminal):
    terminal.descriptors.isatty.side_effect = lambda descriptor: (
        descriptor != not_terminal
    )
    with pytest.raises(approval.OwnerGrantConfirmationRefusal, match=approval._REFUSAL):
        approval.confirm_owner_grant_0056_v1(_DIGEST)
    terminal.prompt.assert_not_called()


def test_missing_terminal_refuses_before_reading_stdin(terminal):
    terminal.streams.stdin = None
    with pytest.raises(approval.OwnerGrantConfirmationRefusal, match=approval._REFUSAL):
        approval.confirm_owner_grant_0056_v1(_DIGEST)
    terminal.prompt.assert_not_called()


@pytest.mark.parametrize(
    "digest", [None, b"3a" * 32, "G" * 64, _DIGEST.upper(), _DIGEST + "\n"]
)
def test_invalid_digest_refuses_before_terminal_or_prompt(terminal, digest):
    with pytest.raises(approval.OwnerGrantConfirmationRefusal, match=approval._REFUSAL):
        approval.confirm_owner_grant_0056_v1(digest)
    terminal.descriptors.isatty.assert_not_called()
    terminal.prompt.assert_not_called()


@pytest.mark.parametrize(
    "response",
    [
        "private response",
        _ACKNOWLEDGEMENT + " ",
        f"CTCC_DB0056_GRANT_42 {'0' * 64}",
        _ACKNOWLEDGEMENT.replace(" ", "\t"),
    ],
)
def test_wrong_response_refuses_without_echo(terminal, response):
    terminal.prompt.return_value = response
    with pytest.raises(approval.OwnerGrantConfirmationRefusal) as refusal:
        approval.confirm_owner_grant_0056_v1(_DIGEST)
    assert str(refusal.value) == approval._REFUSAL


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt, OSError, ValueError])
def test_failed_or_interrupted_prompt_refuses(terminal, error):
    terminal.prompt.side_effect = error("private stream error")
    with pytest.raises(approval.OwnerGrantConfirmationRefusal) as refusal:
        approval.confirm_owner_grant_0056_v1(_DIGEST)
    assert str(refusal.value) == approval._REFUSAL
    assert refusal.value.__suppress_context__ is True
