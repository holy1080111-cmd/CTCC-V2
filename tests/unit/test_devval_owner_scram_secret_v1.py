"""Offline synthetic SCRAM secret lifecycle; no container or database."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from scripts.devval_hermetic_tls import _POSTGRES_BOOT
from scripts.devval_owner_scram_secret_v1 import (
    OwnerScramLabSecretError,
    _boot_command,
    owner_scram_lab_secret_v1,
)
from scripts.verify_owner_scram_0056_hermetic import _arguments


def test_postgres_boot_uses_private_password_file_without_value():
    command = _boot_command(("sh", "-ceu", _POSTGRES_BOOT))
    assert command[:2] == ("sh", "-ceu")
    assert command[2].count("exec /usr/local/bin/docker-entrypoint.sh") == 1
    assert "POSTGRES_PASSWORD_FILE=/ctcc-devval-tls/postgres-password" in command[2]
    assert "chown postgres:postgres /ctcc-devval-tls/postgres-password" in command[2]
    assert "POSTGRES_PASSWORD=" not in command[2]
    with pytest.raises(OwnerScramLabSecretError):
        _boot_command(("sh", "-ceu", "exec something else"))


def test_owner_scram_runner_requires_explicit_fixed_source_args():
    arguments = [
        "--archive",
        "source.tar",
        "--archive-sha256",
        "a" * 64,
        "--commit",
        "b" * 40,
        "--tree",
        "c" * 40,
        "--output",
        "evidence",
    ]
    with pytest.raises(ValueError, match="owner_scram_lab_arguments_invalid"):
        _arguments(arguments)
    args = _arguments([*arguments, "--owner-scram-lab-only"])
    assert args.owner_scram_lab_only is True
    with pytest.raises(ValueError, match="owner_scram_lab_arguments_invalid"):
        _arguments([*arguments, "--owner-scram-lab-only", "--commit", "invalid"])


def test_secret_is_private_and_cleaned_or_posix_only():
    if os.name != "posix":
        with (
            pytest.raises(OwnerScramLabSecretError),
            owner_scram_lab_secret_v1(("sh", "-ceu", _POSTGRES_BOOT)),
        ):
            pytest.fail("Windows must refuse the Linux-only lab")
        return
    secret_path = None
    with owner_scram_lab_secret_v1(("sh", "-ceu", _POSTGRES_BOOT)) as lab:
        mount = lab.postgres_mount[1]
        secret_path = Path(mount.split(",", 2)[1].removeprefix("source="))
        assert secret_path.is_file()
        assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(secret_path.parent.stat().st_mode) == 0o700
        assert lab.pytest_secret_file == "/ctcc-owner-scram-secret"
        assert secret_path.read_bytes() not in repr(lab).encode("utf-8")
        assert secret_path.read_bytes() not in lab.postgres_command[2].encode()
    assert secret_path is not None and not secret_path.exists()
    assert not secret_path.parent.exists()
