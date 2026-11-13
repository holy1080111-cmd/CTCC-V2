"""The explicit source preflight reports redacted stages without using OKX."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr

from app.demo_automation import native_source_profile_0056_v1 as subject
from scripts import inspect_demo_source_profile_0056 as cli

_INSTRUMENT = "BTC-USDT-SWAP"


def _valid_profile(monkeypatch, *, demo_credentials_configured):
    settings = SimpleNamespace(
        okx_demo_native_evidence_root="synthetic private path",
        okx_demo_credentials_configured=demo_credentials_configured,
        okx_demo_rest_base_url="https://openapi.okx.com",
        okx_demo_read_max_retries=0,
        okx_demo_allowed_symbol_list=(_INSTRUMENT,),
    )
    monkeypatch.setattr(subject, "get_settings", lambda: settings)
    monkeypatch.setattr(subject, "_settings_profile", Mock())
    monkeypatch.setattr(subject, "require_private_evidence_base_v2", Mock())
    monkeypatch.setattr(subject, "_tls_configuration", Mock())
    roles = AsyncMock()
    monkeypatch.setattr(subject, "_observe_configured_roles", roles)
    return roles


async def test_missing_credentials_does_not_block_independent_role_observation(
    monkeypatch,
):
    roles = _valid_profile(monkeypatch, demo_credentials_configured=False)
    result = await subject.inspect_configured_demo_source_profile_0056_v1(_INSTRUMENT)
    roles.assert_awaited_once_with()
    assert result.settings == result.native_profile == "PASS"
    assert result.private_evidence_root == result.postgres_tls_configuration == "PASS"
    assert result.restricted_postgres_roles == "PASS"
    assert result.demo_source_configuration == "FAIL"
    assert result.exchange_requests == 0
    assert result.checks_passed is False


async def test_default_profile_denied_without_opening_secrets_or_database(monkeypatch):
    settings = subject.Settings(_env_file=None, ctcc_native_only_lifespan_v1=False)
    monkeypatch.setattr(subject, "get_settings", lambda: settings)
    root = Mock()
    tls = Mock()
    roles = AsyncMock()
    monkeypatch.setattr(subject, "require_private_evidence_base_v2", root)
    monkeypatch.setattr(subject, "_tls_configuration", tls)
    monkeypatch.setattr(subject, "_observe_configured_roles", roles)
    result = await subject.inspect_configured_demo_source_profile_0056_v1(_INSTRUMENT)
    assert result.settings == "PASS"
    assert result.native_profile == "FAIL"
    assert (
        result.restricted_postgres_roles
        == result.demo_source_configuration
        == "NOT_RUN"
    )
    root.assert_not_called()
    tls.assert_not_called()
    roles.assert_not_awaited()


def test_duplicate_roles_and_missing_tls_reject_before_connection():
    shared = "postgresql+asyncpg://synthetic@localhost:5432/ctcc"
    settings = SimpleNamespace(
        database_url=shared,
        okx_demo_native_custody_database_url=SecretStr(shared),
        okx_demo_native_frame_reader_database_url=SecretStr(shared),
        ctcc_native_pg_tls_ca_file="",
        ctcc_native_pg_tls_ca_sha256="",
    )
    with pytest.raises(ValueError, match="roles_not_distinct"):
        subject._tls_configuration(settings)

    settings.okx_demo_native_custody_database_url = SecretStr(
        "postgresql+asyncpg://synthetic_custody@localhost:5432/ctcc"
    )
    settings.okx_demo_native_frame_reader_database_url = SecretStr(
        "postgresql+asyncpg://synthetic_frame@localhost:5432/ctcc"
    )
    with pytest.raises(ValueError, match="tls_required"):
        subject._tls_configuration(settings)


def test_cli_redacts_input_and_cached_settings_failure(monkeypatch, capsys):
    read_settings = Mock(side_effect=ValueError("PRIVATE_INPUT"))
    monkeypatch.setattr(subject, "get_settings", read_settings)
    assert cli.main(["--unknown", "PRIVATE_ARG"]) == 1
    read_settings.assert_not_called()
    assert "PRIVATE_ARG" not in capsys.readouterr().out
    assert cli.main(["--preflight-only", "--instrument", _INSTRUMENT]) == 1
    output = capsys.readouterr()
    assert "PRIVATE_INPUT" not in output.out + output.err
    assert json.loads(output.out)["settings"] == "FAIL"


def test_matching_cli_always_denies_authority(monkeypatch, capsys):
    _valid_profile(monkeypatch, demo_credentials_configured=True)
    result = cli.main(["--preflight-only", "--instrument", _INSTRUMENT])
    out = json.loads(capsys.readouterr().out)
    assert result == 2
    assert out["admission"] == "DENY" and out["status"] == "OBSERVED_DENY"
    assert out["execution_authority"] is out["startup_admission"] is False
    assert out["account_complete"] is False and out["exchange_requests"] == 0


async def test_tls_and_role_failures_never_allow_the_profile(monkeypatch):
    roles = _valid_profile(monkeypatch, demo_credentials_configured=True)
    roles.side_effect = ValueError("synthetic private role")
    result = await subject.inspect_configured_demo_source_profile_0056_v1(_INSTRUMENT)
    assert result.restricted_postgres_roles == "FAIL"
    assert result.checks_passed is False

    roles.reset_mock()
    monkeypatch.setattr(
        subject,
        "_tls_configuration",
        Mock(side_effect=ValueError("synthetic private CA")),
    )
    result = await subject.inspect_configured_demo_source_profile_0056_v1(_INSTRUMENT)
    assert result.postgres_tls_configuration == "FAIL"
    assert result.restricted_postgres_roles == "NOT_RUN"
    roles.assert_not_awaited()
    assert result.checks_passed is False
