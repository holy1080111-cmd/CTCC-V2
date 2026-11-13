"""Keep the operator connection test single-attempt and redacted."""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from scripts import check_okx_demo_connection as subject


def _demo_settings(retries):
    return SimpleNamespace(
        environment="production",
        okx_demo_enabled=True,
        okx_demo_read_max_retries=retries,
    )


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
    monkeypatch.setattr(subject, "get_settings", lambda: _demo_settings(retries))
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
    assert "api_key_policy" not in result
    assert result["account_complete"] is result["execution_authority"] is False
    if retries:
        build.assert_not_called()
        check.assert_not_awaited()
    else:
        check.assert_awaited_once()


@pytest.mark.parametrize(
    ("permissions", "ips", "permission_policy", "ip_policy"),
    [
        ("trade,read_only", "8.8.8.8", "EXACT_READ_TRADE", "SINGLE_PERMITTED_IPV4"),
        ("trade", "8.8.8.8,1.1.1.1", "READ_TRADE_MISMATCH", "MULTIPLE_PERMITTED_IPV4"),
        ("read_only,trade,withdraw", "", "WITHDRAW_ENABLED", "UNBOUND"),
        (
            "read_only,trade,trade",
            "10.0.0.1",
            "MISSING_OR_MALFORMED",
            "MISSING_OR_MALFORMED",
        ),
        ("read_only,trade", "8.8.8.8/32", "EXACT_READ_TRADE", "MISSING_OR_MALFORMED"),
        (
            "read_only,trade",
            "8.8.8.8,8.8.8.8",
            "EXACT_READ_TRADE",
            "MISSING_OR_MALFORMED",
        ),
        (
            "read_only,trade",
            "2606:4700:4700::1111",
            "EXACT_READ_TRADE",
            "MISSING_OR_MALFORMED",
        ),
        (None, None, "MISSING_OR_MALFORMED", "MISSING_OR_MALFORMED"),
    ],
)
def test_opt_in_key_policy_classifies_only_redacted_signed_row(
    monkeypatch, capsys, permissions, ips, permission_policy, ip_policy
):
    row = {
        "uid": "121212",
        "mainUid": "818181",
        "label": "private-demo-key-label",
        "perm": permissions,
        "ip": ips,
    }
    check = AsyncMock(return_value=SimpleNamespace(data=(row,)))
    build = Mock(
        return_value=SimpleNamespace(
            pinned_read_session=nullcontext, account_config_selector_v2=check
        )
    )
    monkeypatch.setattr(subject, "OkxDemoPrivateRestClient", build)
    monkeypatch.setattr(subject, "get_settings", lambda: _demo_settings(0))

    assert subject.main(["--key-policy"]) == 0

    raw = capsys.readouterr().out
    assert all(str(value) not in raw for value in row.values() if value)
    result = json.loads(raw)
    assert result["status"] == "SIGNED_CONFIG_OBSERVED"
    assert result["api_key_policy"] == {
        "permission_policy": permission_policy,
        "ip_binding_policy": ip_policy,
        "current_egress_ip_verified": False,
    }
    assert result["source_verified"] is result["registered_site_verified"] is False
    assert result["account_complete"] is result["execution_authority"] is False
    assert result["order_writes"] == 0
    check.assert_awaited_once()


def test_opt_in_key_policy_has_no_observation_for_whitelist_refusal(
    monkeypatch, capsys
):
    check = AsyncMock(
        side_effect=subject.OkxPrivateApiError("private provider IP", code="50110")
    )
    monkeypatch.setattr(
        subject,
        "OkxDemoPrivateRestClient",
        Mock(
            return_value=SimpleNamespace(
                pinned_read_session=nullcontext, account_config_selector_v2=check
            )
        ),
    )
    monkeypatch.setattr(subject, "get_settings", lambda: _demo_settings(0))

    assert subject.main(["--key-policy"]) == 1

    raw = capsys.readouterr().out
    assert "private provider IP" not in raw
    result = json.loads(raw)
    assert result["status"] == "IP_NOT_WHITELISTED"
    assert result["okx_code"] == "50110"
    assert "api_key_policy" not in result
    check.assert_awaited_once()
