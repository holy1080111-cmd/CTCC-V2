"""Install the reviewed DB0056 monitoring reference as the Linux administrator."""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
import stat
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4

_SOURCE = Path(__file__).resolve().parents[1]
_RECEIPT_SCHEMA = "ctcc.native_only.db0056_admin_startup_trust_install.v1"


class NativeTrustInstallRefusal(ValueError):
    """Fixed refusal without private paths or descriptor contents."""


def _require(condition: bool) -> None:
    if condition is not True:
        raise NativeTrustInstallRefusal(
            "native_startup_trust_install_refused"
        ) from None


def _require_administrator() -> None:
    _require(os.name == "posix" and sys.platform == "linux")
    _require(os.getresuid() == (0, 0, 0))


def _review_owned_path(path: Path, *, regular=False) -> None:
    _require(path.anchor == "/" and ".." not in path.parts)
    for parent in reversed(path.parents):
        info = parent.lstat()
        _require(
            stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
        )
    info = path.lstat()
    _require(info.st_uid == 0 and not info.st_mode & 0o022)
    if regular:
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    else:
        _require(stat.S_ISDIR(info.st_mode))


def _require_review_owned_imports() -> None:
    # This command itself and the Python runtime must already be trusted by
    # the administrator. Check the remaining application import trees before
    # loading the code that parses and validates the deployment descriptor.
    _review_owned_path(_SOURCE)

    def refused(_):
        _require(False)

    for package in (_SOURCE / "app", _SOURCE / "scripts"):
        _review_owned_path(package)
        for directory, subdirectories, files in os.walk(
            package, onerror=refused, followlinks=False
        ):
            root = Path(directory)
            for name in subdirectories:
                _review_owned_path(root / name)
            for name in files:
                _review_owned_path(root / name, regular=True)


def _reference_document(descriptor, expected_sha256: str) -> bytes:
    from app.core import native_startup_trust_0056_v1 as trust
    from app.domain.source_primitives import canonical, sha

    _require(sha(descriptor.payload) == expected_sha256)
    raw = canonical(
        {
            "schema": trust._SCHEMA,
            "database_head": "0056",
            "startup_mode": trust._MODE,
            **{
                name: (
                    expected_sha256
                    if name == "descriptor_sha256"
                    else getattr(descriptor, name)
                )
                for name in trust._REFERENCE_FIELDS
            },
        }
    )
    _require(trust._parse(raw).descriptor_sha256 == expected_sha256)
    return raw


@contextmanager
def _destination():
    from app.core import native_startup_trust_0056_v1 as trust

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    with ExitStack() as stack:
        root = os.open("/", flags)
        stack.callback(os.close, root)
        root_identity = trust._directory_identity(os.fstat(root))
        run_name, directory_name = trust._DIRECTORIES
        run = os.open(run_name, flags, dir_fd=root)
        stack.callback(os.close, run)
        run_identity = trust._directory_identity(os.fstat(run))
        try:
            directory = os.open(directory_name, flags, dir_fd=run)
        except FileNotFoundError:
            mask = os.umask(0o022)
            try:
                try:
                    os.mkdir(directory_name, mode=0o755, dir_fd=run)
                except FileExistsError:
                    pass
            finally:
                os.umask(mask)
            directory = os.open(directory_name, flags, dir_fd=run)
        stack.callback(os.close, directory)
        identity = trust._directory_identity(os.fstat(directory))
        _require(identity[2] & 0o001 != 0)
        os.fsync(run)
        yield directory
        _require(
            trust._directory_identity(os.stat("/", follow_symlinks=False))
            == root_identity
            and trust._directory_identity(
                os.stat(run_name, dir_fd=root, follow_symlinks=False)
            )
            == run_identity
            and trust._directory_identity(
                os.stat(directory_name, dir_fd=run, follow_symlinks=False)
            )
            == identity
        )


def _read_reference_at(directory: int, name: str) -> bytes:
    from app.core import native_startup_trust_0056_v1 as trust
    from app.trade_evidence.storage import _read_fd

    identity = trust._file_identity(
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    )
    file = os.open(
        name,
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=directory,
    )
    try:
        _require(trust._file_identity(os.fstat(file)) == identity)
        raw = _read_fd(file, trust._LIMIT)
        _require(
            trust._file_identity(os.fstat(file)) == identity
            and trust._file_identity(
                os.stat(name, dir_fd=directory, follow_symlinks=False)
            )
            == identity
        )
        return raw
    finally:
        os.close(file)


def _rename_no_replace(directory: int, temporary: str, leaf: str) -> None:
    # Atomic no-clobber publication keeps st_nlink=1 across interruption.
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    if rename(directory, os.fsencode(temporary), directory, os.fsencode(leaf), 1) != 0:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise FileExistsError(code, "trust reference exists")
        raise OSError(code, "no-clobber trust publication failed")


def _publish_reference(directory: int, payload: bytes) -> bool:
    from app.core import native_startup_trust_0056_v1 as trust
    from app.trade_evidence.storage import _write_fd

    leaf = trust._FILENAME
    try:
        _require(_read_reference_at(directory, leaf) == payload)
    except FileNotFoundError:
        pass
    else:
        return False

    temporary = f".{leaf}.{uuid4().hex}.partial"
    file = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        mode=0o600,
        dir_fd=directory,
    )
    published = False
    try:
        _write_fd(file, payload)
        os.fchmod(file, 0o444)
        os.fsync(file)
        _require(_read_reference_at(directory, temporary) == payload)
        try:
            _rename_no_replace(directory, temporary, leaf)
            published = True
        except FileExistsError:
            # Another administrator may have won publication. The final
            # single-link exact-byte read verifies that installation below.
            pass
    finally:
        os.close(file)
        if not published:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.fsync(directory)
    _require(_read_reference_at(directory, leaf) == payload)
    return published


def install_native_startup_trust_0056_v1(
    *, descriptor_file: Path, descriptor_sha256: str, source_archive_file: Path
) -> dict[str, object]:
    _require_administrator()
    _require_review_owned_imports()
    for path in (descriptor_file, source_archive_file):
        _review_owned_path(path, regular=True)

    from app.core import native_startup_trust_0056_v1 as trust
    from scripts import native_deployment_descriptor_0056_v1 as deployment

    descriptor = deployment._read_private_descriptor(descriptor_file, descriptor_sha256)
    deployment._check_installed_release(descriptor, source_archive_file)
    payload = _reference_document(descriptor, descriptor_sha256)
    with _destination() as destination:
        published = _publish_reference(destination, payload)
    with _destination() as destination:
        reference = trust._parse(_read_reference_at(destination, trust._FILENAME))
        _require(reference == trust._parse(payload))
    return {
        "status": "INSTALLED" if published else "ALREADY_INSTALLED",
        "database_head": reference.database_head,
        "startup_mode": reference.startup_mode,
        "trust_sha256": reference.trust_sha256,
        "descriptor_sha256": reference.descriptor_sha256,
        "source_commit": reference.source_commit,
        "source_tree": reference.source_tree,
    }


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _require(False)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(allow_abbrev=False, description=__doc__)
    parser.add_argument("--descriptor-file", required=True, type=Path)
    parser.add_argument("--descriptor-sha256", required=True)
    parser.add_argument("--source-archive-file", required=True, type=Path)
    receipt = {
        "schema": _RECEIPT_SCHEMA,
        "admission": "DENY",
        "startup_admission": False,
        "execution_authority": False,
        "database_writes": 0,
        "exchange_requests": 0,
        "order_writes": 0,
    }
    try:
        receipt.update(
            install_native_startup_trust_0056_v1(**vars(parser.parse_args(argv)))
        )
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- no private input escapes.
        receipt["status"] = "REFUSED"
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 1
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
