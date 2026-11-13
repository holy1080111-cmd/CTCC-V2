"""Verified PostgreSQL CA context for a future native-only transport.

This helper has no settings, database, startup, or execution integration. The
expected CA digest must come from a separately reviewed source or deployment
record; a digest computed from the same untrusted file is not an authority.
Call once and pass the *same returned context object* to all three restricted
role engines. The existing native role observer checks that identity.
The exact CA digest protects trust if an ancestor is renamed concurrently;
this is not a general proof of stable filesystem path identity.
"""

from __future__ import annotations

import hashlib
import os
import re
import ssl
import stat
from pathlib import Path

_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MAX_CA_BYTES = 1_048_576
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
_PATH_TYPE = type(Path())


class NativePgTlsError(ValueError):
    """Fixed denial; never includes a path, certificate, or private setting."""


def _deny() -> None:
    raise NativePgTlsError("native_pg_tls_unavailable")


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )


def _is_reparse(value: os.stat_result) -> bool:
    return bool(getattr(value, "st_file_attributes", 0) & _REPARSE_POINT)


def _read_ca(path: Path) -> bytes:
    if type(path) is not _PATH_TYPE or not path.is_absolute() or ".." in path.parts:
        _deny()
    # Reject symlink/reparse ancestors at acquisition. Ancestor renames remain
    # possible; only the exact digest and opened-leaf checks bind trust bytes.
    for parent in reversed(path.parents):
        entry = parent.lstat()
        if not stat.S_ISDIR(entry.st_mode) or _is_reparse(entry):
            _deny()
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or _is_reparse(before)
        or not 0 < before.st_size <= _MAX_CA_BYTES
    ):
        _deny()
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _file_identity(opened) != _file_identity(
            before
        ):
            _deny()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(_MAX_CA_BYTES + 1)
        after_open = os.fstat(descriptor)
        after_path = path.lstat()
        if (
            not 0 < len(payload) <= _MAX_CA_BYTES
            or len(payload) != opened.st_size
            or _file_identity(opened) != _file_identity(after_open)
            or _file_identity(opened) != _file_identity(after_path)
            or _is_reparse(after_path)
        ):
            _deny()
        return payload
    finally:
        os.close(descriptor)


def verified_native_pg_tls_context_v1(
    ca_file: Path, expected_sha256: str
) -> ssl.SSLContext:
    """Load only exact reviewed CA bytes; make no database connection."""
    try:
        if (
            type(expected_sha256) is not str
            or _SHA256.fullmatch(expected_sha256) is None
        ):
            _deny()
        payload = _read_ca(ca_file)
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            _deny()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_verify_locations(cadata=payload.decode("ascii"))
        if (
            type(context) is not ssl.SSLContext
            or context.verify_mode != ssl.CERT_REQUIRED
            or context.check_hostname is not True
            or context.cert_store_stats()["x509_ca"] < 1
            or context._msg_callback is not None
            or context.sni_callback is not None
        ):
            _deny()
        return context
    except NativePgTlsError:
        raise
    except Exception:  # noqa: BLE001 -- fixed denial hides private path and PEM
        raise NativePgTlsError("native_pg_tls_unavailable") from None
