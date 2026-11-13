"""Keep the operator connection test single-attempt and redacted."""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from scripts import check_okx_demo_connection as subject


@pytest.mark.parametrize(
    ("retries", "uid", "code", "status"),
    [
        (0, "121212", None, "SIGNED_CONFIG_OBSERVED"),
        (0, "000", None, "ACCOUNT_IDENTITY_UNAVAILABLE"),
        (0, None, "50110", "IP_NOT_WHITELISTED"),
        (0, None, "unexpected_private_text", "PRIVATE_READ_REFUSED"),
        (1, None, None, "PROFILE_REFUSED"),
    ],
)
def test_operator_connection_reports_no_provider_message_or_identity(
    monkeypatch, capsys, retries, uid, code, status
):
    check = AsyncMock(
        return_value=SimpleNamespace(data=({"uid": uid, "mainUid": "818181"},)),
        side_effect=subject.OkxPrivateApiError("remote private text", code=code)
        if code
        else None,
    )
    client = SimpleNamespace(
        pinned_read_session=nullcontext, account_config_selector_v2=check
    )
    build = Mock(return_value=client)
    monkeypatch.setattr(subject, "OkxDemoPrivateRestClient", build)
    monkeypatch.setattr(
        subject,
        "get_settings",
        lambda: SimpleNamespace(
            environment="production",
            okx_demo_enabled=True,
            okx_demo_read_max_retries=retries,
        ),
    )
    assert subject.main() == (0 if status == "SIGNED_CONFIG_OBSERVED" else 1)
    raw = capsys.readouterr().out
    assert all(
        secret not in raw
        for secret in (
            "remote private text",
            "unexpected_private_text",
            "121212",
            "818181",
        )
    )
    result = json.loads(raw)
    assert result["status"] == status
    assert result["account_complete"] is result["execution_authority"] is False
    if retries:
        build.assert_not_called()
        check.assert_not_awaited()
    else:
        check.assert_awaited_once()
