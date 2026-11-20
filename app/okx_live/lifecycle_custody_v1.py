"""Content-addressed private lifecycle bytes: no clobber, flush, separate readback.

Accepted files survive every later failure. POSIX flushes directory entries;
Windows promises flushed file/readback, never atomic power-loss directories.
"""

import os
import re
from pathlib import Path

from app.domain.source_primitives import sha
from app.exchange.okx.live_account_config_custody_v1 import (
    _check_directory,
    _check_file,
)
from app.trade_evidence.native_private_root_v2 import (
    _open_new_windows_private_evidence_file,
    mkdir_private_evidence_child_v2,
)
from app.trade_evidence.storage import _read_fd, _WindowsDirectory, _write_fd
from app.trade_qualification.account_native_proof_storage import _root_context

_MAXIMUM = 12 * 1024 * 1024
_DOMAINS = ("raw", "submissions", "proofs")


class LiveLifecycleCustodyError(ValueError):
    pass


def _need(condition):
    if condition is not True:
        raise LiveLifecycleCustodyError(
            "native_live_lifecycle_custody_unavailable"
        ) from None


def _read(directory, name):
    if type(directory) is _WindowsDirectory:
        fd = directory.api.open_file(directory.path / name)
    else:
        fd = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory.fd
        )
    try:
        _check_file(directory, name, fd)
        raw = _read_fd(fd, _MAXIMUM)
        _check_file(directory, name, fd)
        return raw
    finally:
        os.close(fd)


def publish_live_lifecycle_blob_v1(root, raw, *, domain):
    _need(
        type(root) is type(Path())
        and root.is_absolute()
        and type(raw) is bytes
        and 0 < len(raw) <= _MAXIMUM
        and domain in _DOMAINS
    )
    name = sha(raw) + ".json"
    with _root_context(root) as base:
        identity = _check_directory(base)
        if "live-lifecycle-v1" not in base.names():
            mkdir_private_evidence_child_v2(base, "live-lifecycle-v1")
        with base.child("live-lifecycle-v1") as lifecycle:
            _check_directory(lifecycle)
            if domain not in lifecycle.names():
                mkdir_private_evidence_child_v2(lifecycle, domain)
            with lifecycle.child(domain) as directory:
                child_identity = _check_directory(directory)
                if name in directory.names():
                    _need(_read(directory, name) == raw)
                elif type(directory) is _WindowsDirectory:
                    fd = _open_new_windows_private_evidence_file(directory, name)
                    try:
                        _check_file(directory, name, fd)
                        _write_fd(fd, raw)
                    finally:
                        os.close(fd)
                else:
                    directory.publish(name, raw)
    with (
        _root_context(root) as base,
        base.child("live-lifecycle-v1") as lifecycle,
        lifecycle.child(domain) as directory,
    ):
        _need(
            _check_directory(base) == identity
            and _check_directory(directory) == child_identity
        )
        _need(_read(directory, name) == raw)
    return root / "live-lifecycle-v1" / domain / name


def read_live_lifecycle_blob_v1(root, digest, *, domain):
    _need(
        type(root) is type(Path())
        and root.is_absolute()
        and domain in _DOMAINS
        and type(digest) is str
        and re.fullmatch(r"[a-f0-9]{64}", digest) is not None
    )
    name = digest + ".json"
    with (
        _root_context(root) as base,
        base.child("live-lifecycle-v1") as lifecycle,
        lifecycle.child(domain) as directory,
    ):
        _check_directory(base)
        _check_directory(lifecycle)
        _check_directory(directory)
        raw = _read(directory, name)
    _need(sha(raw) == digest)
    return raw
