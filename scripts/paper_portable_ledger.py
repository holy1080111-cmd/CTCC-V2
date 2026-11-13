"""Save or verify a read-only export of the existing local Paper ledger."""

from __future__ import annotations

import argparse
import hmac
import http.client
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

from app.domain.source_primitives import decode, sha
from app.paper import portable_state as portable
from app.trade_evidence import storage
from app.trade_evidence.native_private_root_v2 import (
    _windows_private,
    require_private_evidence_base_v2,
)

_HOST = "127.0.0.1"
_PORT = 8102
_VERSION = "/api/version"
_RUNTIME = "/api/paper/runtime/state"
_EXPORT = "paper-state.json"
_TOKEN = re.compile(rb"[A-Za-z0-9_-]{48,128}", re.ASCII)
_BINDING_LABEL = b"ctcc.paper.local_runtime_installation.v1"


def _get(path: str, *, token: bytes = b"") -> bytes:
    portable.require(
        (path == _VERSION and token == b"")
        or (path == _RUNTIME and _TOKEN.fullmatch(token) is not None)
    )
    headers = {"Accept-Encoding": "identity", "Connection": "close"}
    if token:
        headers["X-CTCC-Token"] = token.decode("ascii")
    connection = http.client.HTTPConnection(_HOST, _PORT, timeout=3)
    try:
        connection.request("GET", path, headers=headers)
        with connection.getresponse() as response:
            types = response.headers.get_all("Content-Type", ())
            lengths = response.headers.get_all("Content-Length", ())
            encodings = response.headers.get_all("Content-Encoding", ())
            limit = 8192 if path == _VERSION else portable.MAX_STATE_BYTES
            portable.require(
                response.status == 200
                and types == ["application/json"]
                and len(lengths) == 1
                and re.fullmatch(r"[1-9][0-9]{0,6}", lengths[0]) is not None
                and int(lengths[0]) <= limit
                and encodings in ((), ["identity"])
                and response.headers.get("Location") is None
                and response.headers.get("Transfer-Encoding") is None
            )
            raw = response.read(limit + 1)
            portable.require(len(raw) == int(lengths[0]))
    finally:
        connection.close()
    return raw


@contextmanager
def _directory(path: Path):
    root = require_private_evidence_base_v2(str(path))
    if os.name == "nt":
        with storage._windows_root(storage._root_path(root)) as directory:
            _windows_private(root)
            yield directory
    else:
        with storage._posix_root(storage._root_path(root)) as directory:
            info = os.fstat(directory.fd)
            portable.require(info.st_uid == os.geteuid() and not info.st_mode & 0o077)
            yield directory


def _load_token(path: Path) -> bytes:
    portable.require(path.name == "api_token")
    with _directory(path.parent) as directory:
        if os.name == "nt":
            fd = directory.api.open_file(path)
        else:
            fd = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory.fd,
            )
        try:
            if os.name == "nt":
                # The native file handle denies replacement and write sharing.
                _windows_private(path)
            else:
                info = os.fstat(fd)
                portable.require(
                    info.st_uid == os.geteuid() and not info.st_mode & 0o077
                )
            token = storage._read_fd(fd, 128)
            portable.require(_TOKEN.fullmatch(token) is not None)
            return token
        finally:
            os.close(fd)


def export(token_path: Path, output: Path) -> dict[str, object]:
    """Read one unchanged local runtime around independent version reads."""
    portable.require(token_path.is_absolute() and token_path.parent != output)
    with _directory(output) as directory:
        portable.require(_EXPORT not in directory.names())
        version_before = portable.verified_version(decode(_get(_VERSION), 8192))
        token = _load_token(token_path)
        binding = hmac.digest(token, _BINDING_LABEL, "sha256").hex()
        first = portable.read_runtime_state(_get(_RUNTIME, token=token), binding)
        second = portable.read_runtime_state(_get(_RUNTIME, token=token), binding)
        version_after = portable.verified_version(decode(_get(_VERSION), 8192))
        portable.require(first == second and version_before == version_after)
        raw = portable.make_export(second, software_version=version_after)
        digest = sha(raw)
        directory.publish(_EXPORT, raw)
    with _directory(output) as directory:
        published = directory.read(_EXPORT, portable.MAX_STATE_BYTES)
    portable.require(published == raw)
    return portable.inspect_export(published, expected_sha256=digest)


def inspect(directory: Path, *, expected_sha256: str) -> dict[str, object]:
    """Reopen a local artifact without reading credentials or contacting the API."""
    with _directory(directory) as held:
        raw = held.read(_EXPORT, portable.MAX_STATE_BYTES)
    return portable.inspect_export(raw, expected_sha256=expected_sha256)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    operations = parser.add_subparsers(dest="operation", required=True)
    capture = operations.add_parser("export", allow_abbrev=False)
    capture.add_argument("--token-file", required=True, type=Path)
    capture.add_argument("--directory", required=True, type=Path)
    review = operations.add_parser("inspect", allow_abbrev=False)
    review.add_argument("--directory", required=True, type=Path)
    review.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)
    try:
        result = (
            export(args.token_file, args.directory)
            if args.operation == "export"
            else inspect(args.directory, expected_sha256=args.sha256)
        )
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- private values stay local.
        print('{"status":"PAPER_EXPORT_REFUSED"}')
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
