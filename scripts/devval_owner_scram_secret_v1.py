"""Ephemeral synthetic SCRAM secret for the isolated owner route lab only.

The generated value is never an application credential, command argument,
environment value, source artifact, or validation output. The official
PostgreSQL entrypoint receives only a private tmpfs copy of the file.
"""

from __future__ import annotations

import os
import secrets
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from scripts.devval_hermetic_tls import _POSTGRES_BOOT

_SOURCE = "/ctcc-owner-scram-input/password"
_POSTGRES_PRIVATE = "/ctcc-devval-tls/postgres-password"
_PYTEST_SECRET = "/ctcc-owner-scram-secret"
_ENTRYPOINT = "exec /usr/local/bin/docker-entrypoint.sh"
_INSERT = f"""\
cp {_SOURCE} {_POSTGRES_PRIVATE}
chown postgres:postgres {_POSTGRES_PRIVATE}
chmod 0600 {_POSTGRES_PRIVATE}
export POSTGRES_PASSWORD_FILE={_POSTGRES_PRIVATE}
"""


class OwnerScramLabSecretError(ValueError):
    """Fixed denial with no path or secret value."""


@dataclass(frozen=True, slots=True)
class OwnerScramLabSecretV1:
    postgres_mount: tuple[str, str]
    pytest_mount: tuple[str, str]
    postgres_command: tuple[str, str, str]
    pytest_secret_file: str = _PYTEST_SECRET


def _boot_command(tls_command: tuple[str, ...]) -> tuple[str, str, str]:
    if (
        type(tls_command) is not tuple
        or tls_command != ("sh", "-ceu", _POSTGRES_BOOT)
        or _POSTGRES_BOOT.count(_ENTRYPOINT) != 1
        or not _POSTGRES_BOOT.startswith("umask 077\n")
    ):
        raise OwnerScramLabSecretError("owner_scram_lab_secret_unavailable")
    return (
        "sh",
        "-ceu",
        _POSTGRES_BOOT.replace(_ENTRYPOINT, _INSERT + _ENTRYPOINT),
    )


@contextmanager
def owner_scram_lab_secret_v1(
    tls_command: tuple[str, ...],
) -> Iterator[OwnerScramLabSecretV1]:
    """Yield mount paths and fixed boot script, never the generated password."""
    if os.name != "posix" or not Path("/tmp").is_dir():
        raise OwnerScramLabSecretError("owner_scram_lab_secret_unavailable")
    boot = _boot_command(tls_command)
    directory = Path(tempfile.mkdtemp(prefix="ctcc-owner-scram-", dir="/tmp"))
    secret = directory / "password"
    recorded: os.stat_result | None = None
    try:
        directory.chmod(0o700)
        descriptor = os.open(
            secret,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            recorded = os.fstat(descriptor)
            payload = secrets.token_hex(32).encode("ascii")
            if os.write(descriptor, payload) != len(payload):
                raise OwnerScramLabSecretError("owner_scram_lab_secret_unavailable")
            os.fsync(descriptor)
            recorded = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if (
            not stat.S_ISREG(recorded.st_mode)
            or stat.S_IMODE(recorded.st_mode) != 0o600
            or recorded.st_size != 64
            or secret.lstat().st_ino != recorded.st_ino
        ):
            raise OwnerScramLabSecretError("owner_scram_lab_secret_unavailable")
        yield OwnerScramLabSecretV1(
            postgres_mount=(
                "--mount",
                f"type=bind,source={secret},target={_SOURCE},readonly",
            ),
            pytest_mount=(
                "--mount",
                f"type=bind,source={secret},target={_PYTEST_SECRET},readonly",
            ),
            postgres_command=boot,
        )
    finally:
        if os.path.lexists(secret):
            current = secret.lstat()
            if (
                recorded is None
                or not stat.S_ISREG(current.st_mode)
                or current.st_ino != recorded.st_ino
                or current.st_dev != recorded.st_dev
            ):
                raise OwnerScramLabSecretError("owner_scram_lab_secret_cleanup_invalid")
            secret.unlink()
        try:
            directory.rmdir()
        except OSError:
            raise OwnerScramLabSecretError(
                "owner_scram_lab_secret_cleanup_invalid"
            ) from None
