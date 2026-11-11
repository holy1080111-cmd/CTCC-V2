"""Pure CLI admission controls; no database or provider is contacted."""

from __future__ import annotations

import pytest

from scripts.run_gate3_fitted_custody import (
    FittedCustodyCommandError,
    _arguments,
    _database_url,
)


def _argv(root, mode="publish"):
    values = [mode]
    for name in (
        "source-archive",
        "binding",
        "batch",
        "study",
        "trials",
        "legacy",
        "preregistration",
        "ca-file",
        "output",
    ):
        values.extend((f"--{name}", str(root / name)))
    for name in (
        "source-archive-sha256",
        "binding-sha256",
        "batch-sha256",
        "study-sha256",
        "trials-sha256",
        "legacy-sha256",
        "preregistration-sha256",
        "ca-sha256",
    ):
        values.extend((f"--{name}", "a" * 64))
    for name, value in (
        ("database-oid", 101),
        ("login-oid", {"publish": 103, "acknowledge": 104}.get(mode, 102)),
        ("publisher-oid", 103),
        ("observer-oid", 104),
        ("evaluator-oid", 105),
        ("database-host", "custody.example"),
        ("database-port", 5432),
        ("database-name", "evidence"),
        ("login-name", "custodian"),
    ):
        values.extend((f"--{name}", str(value)))
    if mode == "observe":
        values.extend(("--ack-sha256", "b" * 64))
    return values


def test_only_three_explicit_custody_modes_are_admitted(tmp_path):
    for mode in ("publish", "acknowledge", "observe"):
        assert _arguments(_argv(tmp_path, mode)).mode == mode
    with pytest.raises(
        FittedCustodyCommandError, match="^gate3_fitted_command_invalid$"
    ):
        _arguments(_argv(tmp_path, "reserve"))


def test_observe_requires_independent_ack_pin(tmp_path):
    values = _argv(tmp_path, "observe")
    del values[-2:]
    with pytest.raises(
        FittedCustodyCommandError, match="^gate3_fitted_command_invalid$"
    ):
        _arguments(values)
    with pytest.raises(
        FittedCustodyCommandError, match="^gate3_fitted_command_invalid$"
    ):
        _arguments(_argv(tmp_path) + ["--ack-sha256", "b" * 64])


def test_bad_digest_and_role_oid_are_refused_before_any_io(tmp_path):
    values = _argv(tmp_path)
    values[values.index("--binding-sha256") + 1] = "not-a-digest"
    with pytest.raises(
        FittedCustodyCommandError, match="^gate3_fitted_command_invalid$"
    ):
        _arguments(values)
    values = _argv(tmp_path)
    values[values.index("--observer-oid") + 1] = "0"
    with pytest.raises(
        FittedCustodyCommandError, match="^gate3_fitted_command_invalid$"
    ):
        _arguments(values)


def test_output_may_not_alias_any_input(tmp_path):
    values = _argv(tmp_path)
    values[values.index("--output") + 1] = str(tmp_path / "binding")
    with pytest.raises(
        FittedCustodyCommandError, match="^gate3_fitted_command_path_conflict$"
    ):
        _arguments(values)


def test_private_url_must_match_the_independently_pinned_endpoint(
    tmp_path, monkeypatch
):
    args = _arguments(_argv(tmp_path))
    environment = "CTCC_GATE3_FITTED_CUSTODY_DATABASE_URL"
    monkeypatch.setenv(
        environment, "postgresql+asyncpg://custodian@wrong.example/evidence"
    )
    with pytest.raises(FittedCustodyCommandError, match="database_settings_invalid"):
        _database_url(args)
    monkeypatch.setenv(
        environment, "postgresql+asyncpg://custodian@custody.example/evidence"
    )
    assert _database_url(args).host == args.database_host
