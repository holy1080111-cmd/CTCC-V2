"""Stage the sealed Linux wheel inventory for the offline vendor preparer."""

from __future__ import annotations

import ctypes
import hashlib
import http.client
import json
import os
import re
import ssl
import stat
import sys
import time
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

_INPUT_SHA256 = "cc4eec913361243481206d76a87d9f9b76296d2ab9df85ea018c4891792f2caf"
_INPUT_DIRECTORY = "N434-pinned-linux-python-runtime-20261121"
_INPUT_FILE = "OFFLINE_DEPENDENCY_INPUTS_VERIFIED_V1.json"
_HOST = "files.pythonhosted.org"
_DIGEST = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,239}\.whl\Z", re.ASCII)
_URL = re.compile(
    r"https://files\.pythonhosted\.org"
    r"(?P<path>/packages/[a-f0-9]{2}/[a-f0-9]{2}/[a-f0-9]{60}/"
    r"(?P<filename>[A-Za-z0-9][A-Za-z0-9_.-]{0,239}\.whl))\Z",
    re.ASCII,
)
_WHEEL_COUNT = 41
_MAX_WHEEL_BYTES = 64 * 1024 * 1024
_MAX_INPUT_BYTES = 128 * 1024
_CHUNK_BYTES = 64 * 1024
_SOCKET_TIMEOUT = 15
_WHEEL_DEADLINE = 120
_WHEELHOUSE = "linux-wheels"
_RENAME_NOREPLACE = 1
_PIN_FIELDS = {
    "name",
    "distribution_name",
    "version",
    "filename",
    "sha256",
    "byte_size",
    "metadata_sha256",
}


class WheelFetchRefused(ValueError):
    """The exact public wheel inventory could not be staged."""


@dataclass(frozen=True)
class Wheel:
    filename: str
    sha256: str
    byte_size: int
    request_path: str


def _require(condition: bool) -> None:
    if condition is not True:
        raise WheelFetchRefused("pinned_wheel_fetch_refused")


def _is_digest(value: object) -> bool:
    return type(value) is str and _DIGEST.fullmatch(value) is not None


@contextmanager
def _directory(parent: int | None, name: str):
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=parent,
    )
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _support_root(path: Path):
    with ExitStack() as opened:
        descriptor = opened.enter_context(_directory(None, "/"))
        for name in path.parts[1:]:
            descriptor = opened.enter_context(_directory(descriptor, name))
        yield descriptor


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read_pinned_file(
    directory: int,
    name: str,
    expected_sha256: str,
    maximum: int,
    *,
    retain: bool,
    exact_size: int | None = None,
) -> bytes:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=directory,
    )
    try:
        before = os.fstat(descriptor)
        _require(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and 0 < before.st_size <= maximum
            and (exact_size is None or before.st_size == exact_size)
        )
        digest, count, parts = hashlib.sha256(), 0, []
        while chunk := os.read(
            descriptor, min(_CHUNK_BYTES, before.st_size - count + 1)
        ):
            count += len(chunk)
            _require(count <= before.st_size)
            digest.update(chunk)
            if retain:
                parts.append(chunk)
        _require(
            count == before.st_size
            and digest.hexdigest() == expected_sha256
            and _file_identity(os.fstat(descriptor)) == _file_identity(before)
            and _file_identity(os.stat(name, dir_fd=directory, follow_symlinks=False))
            == _file_identity(before)
        )
        return b"".join(parts)
    finally:
        os.close(descriptor)


def _sealed_wheels(root: int) -> tuple[Wheel, ...]:
    with _directory(root, "validation-results") as validation:
        with _directory(validation, _INPUT_DIRECTORY) as source:
            raw = _read_pinned_file(
                source, _INPUT_FILE, _INPUT_SHA256, _MAX_INPUT_BYTES, retain=True
            )
        manifest = json.loads(raw)
        expected = manifest["packages"]
        metadata_pin, lock_pin = manifest["metadata_sha256"], manifest["lock_sha256"]
        _require(
            manifest["schema"] == "ctcc.pinned_python.offline_vendor_inputs.v1"
            and manifest["vendor_only_maintenance_provider"] is True
            and manifest["project_source_installed"] is False
            and manifest["full_validation_baseline"] is False
            and manifest["execution_authority"] is False
            and type(expected) is list
            and len(expected) == manifest["vendor_wheel_count"] == _WHEEL_COUNT
            and _is_digest(metadata_pin)
            and _is_digest(lock_pin)
        )
        with (
            _directory(validation, "n330runtime58") as checkout,
            _directory(checkout, "requirements") as requirements,
        ):
            raw_metadata = _read_pinned_file(
                requirements,
                "validation-linux-py312.json",
                metadata_pin,
                _MAX_INPUT_BYTES,
                retain=True,
            )
            _read_pinned_file(
                requirements,
                "validation-linux-py312.lock",
                lock_pin,
                _MAX_INPUT_BYTES,
                retain=False,
            )

    metadata = json.loads(raw_metadata)
    records = metadata["packages"]
    _require(
        metadata["lock_sha256"] == lock_pin
        and metadata["source_pyproject_sha256"] == manifest["pyproject_sha256"]
        and type(records) is list
        and len(records) == _WHEEL_COUNT
    )
    wheels = []
    for sealed, record in zip(expected, records, strict=True):
        _require(
            type(sealed) is dict
            and set(sealed) == _PIN_FIELDS
            and type(record) is dict
            and all(record[key] == value for key, value in sealed.items())
        )
        filename = sealed["filename"]
        size = sealed["byte_size"]
        digest = sealed["sha256"]
        match = _URL.fullmatch(record["url"])
        _require(
            type(filename) is str
            and _FILENAME.fullmatch(filename) is not None
            and type(size) is int
            and 0 < size < _MAX_WHEEL_BYTES
            and _is_digest(digest)
            and _is_digest(sealed["metadata_sha256"])
            and match is not None
            and match["filename"] == filename
        )
        wheels.append(Wheel(filename, digest, size, match["path"]))
    _require(
        len({wheel.filename for wheel in wheels}) == _WHEEL_COUNT
        and sum(wheel.byte_size for wheel in wheels) == manifest["wheel_bytes"]
    )
    return tuple(wheels)


def _fetch(
    wheel: Wheel, directory: int, created: list[str], context: ssl.SSLContext
) -> None:
    # http.client uses the exact host directly. It does not honor proxy variables
    # or follow redirects. No authentication or user-derived headers are sent.
    connection = http.client.HTTPSConnection(
        _HOST, timeout=_SOCKET_TIMEOUT, context=context
    )
    deadline = time.monotonic() + _WHEEL_DEADLINE
    try:
        connection.request(
            "GET", wheel.request_path, headers={"Accept-Encoding": "identity"}
        )
        with connection.getresponse() as response:
            lengths = response.headers.get_all("Content-Length", [])
            _require(
                response.status == 200
                and lengths in ([], [str(wheel.byte_size)])
                and response.getheader("Content-Encoding", "identity").lower()
                == "identity"
                and response.getheader("Content-Range") is None
            )
            descriptor = os.open(
                wheel.filename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory,
            )
            created.append(wheel.filename)
            with os.fdopen(descriptor, "wb") as output:
                digest, size = hashlib.sha256(), 0
                while True:
                    _require(time.monotonic() < deadline)
                    chunk = response.read1(
                        min(_CHUNK_BYTES, wheel.byte_size - size + 1)
                    )
                    if not chunk:
                        break
                    size += len(chunk)
                    _require(size <= wheel.byte_size)
                    digest.update(chunk)
                    output.write(chunk)
                _require(
                    time.monotonic() < deadline
                    and size == wheel.byte_size
                    and digest.hexdigest() == wheel.sha256
                )
                output.flush()
                os.fsync(output.fileno())
    finally:
        connection.close()


def _check_wheelhouse(directory: int, wheels: tuple[Wheel, ...]) -> None:
    _require(set(os.listdir(directory)) == {wheel.filename for wheel in wheels})
    for wheel in wheels:
        _read_pinned_file(
            directory,
            wheel.filename,
            wheel.sha256,
            wheel.byte_size,
            retain=False,
            exact_size=wheel.byte_size,
        )


def _publish(root: int, wheels: tuple[Wheel, ...]) -> None:
    # Ubuntu provides renameat2. Refuse publication if its no-replace operation
    # is unavailable, rather than allowing a regular rename to overwrite a leaf.
    renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_default_certs()

    try:
        os.mkdir("dependency-lock-tzdata", 0o700, dir_fd=root)
    except FileExistsError:
        pass
    with _directory(root, "dependency-lock-tzdata") as destination:
        _require(_WHEELHOUSE not in os.listdir(destination))
        scratch = ".ctcc-wheelhouse-" + os.urandom(16).hex()
        os.mkdir(scratch, 0o700, dir_fd=destination)
        published = False
        try:
            with _directory(destination, scratch) as staging:
                created: list[str] = []
                try:
                    for wheel in wheels:
                        _fetch(wheel, staging, created, context)
                    _check_wheelhouse(staging, wheels)
                    os.fsync(staging)
                    staged = os.fstat(staging)
                    entry = os.stat(scratch, dir_fd=destination, follow_symlinks=False)
                    _require(
                        stat.S_ISDIR(entry.st_mode)
                        and (entry.st_dev, entry.st_ino) == (staged.st_dev, staged.st_ino)
                    )
                    _require(
                        renameat2(
                            destination,
                            os.fsencode(scratch),
                            destination,
                            os.fsencode(_WHEELHOUSE),
                            _RENAME_NOREPLACE,
                        ) == 0
                    )
                    published = True
                    os.fsync(destination)
                    with _directory(destination, _WHEELHOUSE) as final:
                        installed = os.fstat(final)
                        _require(
                            (installed.st_dev, installed.st_ino)
                            == (staged.st_dev, staged.st_ino)
                        )
                        _check_wheelhouse(final, wheels)
                finally:
                    if not published:
                        for name in created:
                            with suppress(OSError):
                                os.unlink(name, dir_fd=staging)
        finally:
            if not published:
                with suppress(OSError):
                    os.rmdir(scratch, dir_fd=destination)


def main() -> int:
    try:
        _require(
            sys.platform == "linux" and sys.flags.isolated == 1 and len(sys.argv) == 2
        )
        path = Path(sys.argv[1]).absolute()
        _require(path.anchor == "/" and len(path.parts) > 1 and ".." not in path.parts)
        with _support_root(path) as root:
            wheels = _sealed_wheels(root)
            _publish(root, wheels)
    except (
        AttributeError,
        KeyError,
        OSError,
        ValueError,
        TypeError,
        http.client.HTTPException,
        KeyboardInterrupt,
    ):
        print('{"status":"PINNED_WHEEL_FETCH_REFUSED"}', file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "PINNED_WHEELS_VERIFIED",
                "input_sha256": _INPUT_SHA256,
                "wheel_count": len(wheels),
                "wheel_bytes": sum(wheel.byte_size for wheel in wheels),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
