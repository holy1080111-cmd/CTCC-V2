"""Ephemeral TLS inputs for the isolated D/V PostgreSQL integration test.

This only exercises the native TLS repository path in a trust-auth test lab.
It does not establish independent custody, SCRAM identity, Gate 3, or OOS.
The caller owns Docker execution and must use its exact internal network,
pinned postgres image, and ordinary no-credential test container controls.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_HOST = re.compile(r"ctcc-final-[a-f0-9]{12}-postgres\Z")
_OPENSSL = Path("/usr/bin/openssl")
_HOST_INPUT = "/ctcc-devval-tls-input"
_GUEST_PRIVATE = "/ctcc-devval-tls"
_PYTEST_CA = "/ctcc-devval-test-ca.pem"
_POSTGRES_BOOT = """\
umask 077
mkdir -p /ctcc-devval-tls
cp /ctcc-devval-tls-input/server.key /ctcc-devval-tls/server.key
cp /ctcc-devval-tls-input/server.crt /ctcc-devval-tls/server.crt
chown postgres:postgres /ctcc-devval-tls /ctcc-devval-tls/server.key /ctcc-devval-tls/server.crt
chmod 0700 /ctcc-devval-tls
chmod 0600 /ctcc-devval-tls/server.key
chmod 0644 /ctcc-devval-tls/server.crt
exec /usr/local/bin/docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/ctcc-devval-tls/server.crt -c ssl_key_file=/ctcc-devval-tls/server.key
"""


class DevvalTlsError(ValueError):
    """Fixed refusal without host path, certificate, or key contents."""


@dataclass(frozen=True, slots=True)
class DevvalTlsConfig:
    """Docker argv fragments; no private key bytes or credential values."""

    postgres_mount: tuple[str, ...]
    postgres_command: tuple[str, ...]
    wrong_hostname_alias: str
    pytest_mount: tuple[str, ...]
    pytest_environment: tuple[str, ...]


def _regular(path: Path, *, maximum: int) -> bool:
    try:
        mode = path.lstat().st_mode
        return stat.S_ISREG(mode) and 0 < path.stat().st_size <= maximum
    except OSError:
        return False


def _openssl(*arguments: str) -> None:
    try:
        result = subprocess.run(
            (str(_OPENSSL), *arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise DevvalTlsError("devval_tls_generation_failed") from None
    if result.returncode != 0:
        raise DevvalTlsError("devval_tls_generation_failed")


@contextmanager
def devval_tls(needed: bool, postgres_host: str) -> Iterator[DevvalTlsConfig | None]:
    """Yield no-clobber ephemeral test TLS mounts, then remove owned leaves.

    The caller inserts ``postgres_mount`` before the pinned postgres image and
    ``postgres_command`` after it. The official image entrypoint runs ``sh``;
    the fixed wrapper copies the key into private tmpfs and re-enters the
    original postgres entrypoint for normal initdb and privilege drop.
    ``pytest_mount`` binds only the public certificate, never the key.
    """
    if type(needed) is not bool:
        raise DevvalTlsError("devval_tls_selection_invalid")
    if not needed:
        yield None
        return
    if (
        os.name != "posix"
        or type(postgres_host) is not str
        or _HOST.fullmatch(postgres_host) is None
        or not _OPENSSL.is_file()
        or _OPENSSL.is_symlink()
        or not Path("/tmp").is_dir()
    ):
        raise DevvalTlsError("devval_tls_host_profile_invalid")

    root = Path(tempfile.mkdtemp(prefix="ctcc-devval-tls-", dir="/tmp"))
    key = root / "server.key"
    certificate = root / "server.crt"
    try:
        os.chmod(root, 0o700)
        _openssl(
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-sha256",
            "-days",
            "1",
            "-batch",
            "-config",
            "/dev/null",
            "-subj",
            f"/CN={postgres_host}",
            "-addext",
            f"subjectAltName=DNS:{postgres_host}",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        )
        if not _regular(key, maximum=16_384) or not _regular(
            certificate, maximum=16_384
        ):
            raise DevvalTlsError("devval_tls_material_invalid")
        os.chmod(key, 0o600)
        os.chmod(certificate, 0o644)
        _openssl("x509", "-in", str(certificate), "-noout", "-checkhost", postgres_host)
        if {path.name for path in root.iterdir()} != {"server.key", "server.crt"}:
            raise DevvalTlsError("devval_tls_material_invalid")
        certificate_sha256 = hashlib.sha256(certificate.read_bytes()).hexdigest()
        yield DevvalTlsConfig(
            postgres_mount=(
                "--mount",
                f"type=bind,source={root},target={_HOST_INPUT},readonly",
                "--tmpfs",
                f"{_GUEST_PRIVATE}:rw,nosuid,nodev,noexec,size=64k,mode=0700",
            ),
            postgres_command=("sh", "-ceu", _POSTGRES_BOOT),
            wrong_hostname_alias=f"{postgres_host}-wrong-host",
            pytest_mount=(
                "--mount",
                f"type=bind,source={certificate},target={_PYTEST_CA},readonly",
            ),
            pytest_environment=(
                "-e",
                f"CTCC_DEVVAL_CUSTODY_TEST_DATABASE_URL=postgresql+asyncpg://ctcc@{postgres_host}:5432/ctcc",
                "-e",
                f"CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE={_PYTEST_CA}",
                "-e",
                f"CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256={certificate_sha256}",
            ),
        )
    finally:
        # Only this newly owned temporary leaf is removed. Unknown extra files
        # cause a refusal instead of a recursive delete outside our inventory.
        for leaf in (key, certificate):
            if leaf.exists():
                if leaf.is_symlink() or not leaf.is_file():
                    raise DevvalTlsError("devval_tls_cleanup_invalid")
                leaf.unlink()
        try:
            root.rmdir()
        except OSError:
            raise DevvalTlsError("devval_tls_cleanup_invalid") from None
