"""Read-only identity DATA for the five reviewed native Demo bridge sources.

These literal pins grant no execution permission. The independent native
account inventory pins this descriptor itself; it is deliberately absent from
its own returned rows, so neither a self hash nor a mutable success flag can
stand in for the original source closure.
"""

from __future__ import annotations

import hashlib
import re
import stat
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[3]
_ROWS = (
    (
        "app/exchange/okx/private_rest.py",
        "34d66da9949e18e926b29b54115e7d7ae904dde12b4809e3bb79ac0c6ed447ee",
    ),
    (
        "app/trade_qualification/private_write_authority.py",
        "d03380dbc779c23a67756a330ffc8d461bb748bed250a4127fcb0199debfd8d2",
    ),
    (
        "app/exchange/okx/native_demo_write_v2.py",
        "92c90d2aa471544b78a4d70687e1ef358ec140d95645ac6b4e1b50a217b93610",
    ),
    (
        "app/exchange/okx/native_demo_transport_v2.py",
        "106d47c55e5aab2dc12429d3a3ac100d6b6bf2a8d37c6ccd7c767efd47ddbfdf",
    ),
    (
        "app/exchange/okx/native_demo_vendor_integrity_v2.py",
        "12949b1466f58cae80e46bc817848dbec9041d1c06074041077d4eaab0d57287",
    ),
)


class NativeDemoSourceIdentityError(ValueError):
    """Fixed public rejection; never includes a private path or payload."""


def _need(value):
    if value is not True:
        raise NativeDemoSourceIdentityError("native_demo_source_identity_unproven")


def _plain_bytes(relative, limit):
    path = _SOURCE_ROOT.joinpath(*relative.split("/"))
    _need(path.is_relative_to(_SOURCE_ROOT) and path.resolve(strict=True) == path)
    for directory in (path.parent, *path.parent.parents):
        info = directory.lstat()
        _need(
            stat.S_ISDIR(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and not getattr(info, "st_file_attributes", 0) & 1024
        )
    before = path.lstat()
    _need(
        stat.S_ISREG(before.st_mode)
        and not stat.S_ISLNK(before.st_mode)
        and not getattr(before, "st_file_attributes", 0) & 1024
        and before.st_nlink == 1
        and 0 < before.st_size <= limit
    )
    raw = path.read_bytes()
    after = path.lstat()
    _need(
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        and len(raw) == before.st_size
    )
    return raw


def read_native_demo_source_contract_v2():
    """Return exactly five original path/digest pairs after physical readback."""
    from app.exchange.okx import native_demo_write_v2 as authority

    authority._NATIVE_GUARD()
    manifest = _plain_bytes("MANIFEST.sha256", 2 * 1024 * 1024)
    _need(manifest.endswith(b"\n") and b"\r" not in manifest)
    identities = {}
    for line in manifest.splitlines():
        _need(re.fullmatch(rb"[a-f0-9]{64}  [!-~]+", line) is not None)
        digest, name = line.split(b"  ", 1)
        _need(name not in identities and b"\\" not in name and b":" not in name)
        identities[name] = digest
    for name, digest in _ROWS:
        _need(identities.get(name.encode("ascii")) == digest.encode("ascii"))
        _need(hashlib.sha256(_plain_bytes(name, 1024 * 1024)).hexdigest() == digest)
    _need(_plain_bytes("MANIFEST.sha256", 2 * 1024 * 1024) == manifest)
    authority._NATIVE_GUARD()
    return _ROWS
