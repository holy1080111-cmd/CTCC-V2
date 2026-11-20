"""Source/closed-profile stop-only DATA contracts; no DB/API/Arm execution."""

from datetime import UTC, datetime

import pytest

from app.core.native_role_witness_0056_v1 import runtime_witness_0056
from app.database.repositories import native_legacy_control_genesis_0056_v1 as legacy
from app.domain import native_database_profile_v1 as profiles
from scripts import initialize_demo_stopped_control_0058 as cli
from scripts import native_owner_postgrant_readback_0056_v1 as old


@pytest.mark.parametrize("profile", (profiles.DB0056, profiles.DB0057, profiles.DB0058))
def test_owner_incarnation_matches_actual_profile_witness_without_head_projection(
    profile,
):
    actor = (
        "writer",
        "writer",
        "none",
        1,
        2,
        datetime(2026, 11, 20, tzinfo=UTC),
        170010,
        "127.0.0.1",
        5432,
    )
    server, _ = runtime_witness_0056(
        actor,
        ("writer", "custody", "frame"),
        (("postgresql+asyncpg", "localhost", 5432, "db"),) * 3,
        **profiles.arguments(profile),
    )
    assert legacy._owner_incarnation_sha256(actor, profile) == server
    original = old._owner_server_sha256(actor)
    assert (server == original) is (profile is profiles.DB0056)


@pytest.mark.parametrize("profile", (None, "0058", True, {}))
def test_incarnation_requires_closed_profile(profile):
    with pytest.raises(ValueError):
        legacy._owner_incarnation_sha256((), profile)


def test_exact_human_stop_phrase_binds_all_reviewed_source_and_scope_inputs():
    parts = [character * 64 for character in "abcd"]
    phrase = cli._confirmation(*parts)
    assert phrase.startswith("CTCC_DB0058_CREATE_STOPPED_STATE ")
    for offset in range(4):
        changed = parts.copy()
        changed[offset] = "e" * 64
        assert cli._confirmation(*changed) != phrase
    with pytest.raises(ValueError):
        cli._confirmation("private_dsn", *parts[1:])


def test_cli_failure_and_uncertainty_are_redacted_and_never_claim_ready(
    monkeypatch, capsys
):
    arguments = []
    for name in ("profile", "reviewed-pins", "owner-dsn", "ca", "source-archive"):
        arguments += ["--" + name + "-file", "synthetic_private_path"]
    arguments += [
        "--reviewed-pins-sha256",
        "a" * 64,
        "--instrument",
        "BTC-USDT-SWAP",
        "--reconcile-only",
    ]

    async def failed(**values):
        assert values["reconcile_only"] is True
        raise legacy.LegacyControlGenesis0056Uncertain("synthetic_secret_do_not_echo")

    monkeypatch.setattr(cli, "_run", failed)
    assert cli.main(arguments) == 1
    output = capsys.readouterr()
    assert "synthetic_secret" not in output.out + output.err
    assert "synthetic_private_path" not in output.out + output.err
    assert '"execution_authority":false' in output.out
    assert "STOPPED_SETUP_UNCONFIRMED_RECONCILE_ONLY" in output.out
