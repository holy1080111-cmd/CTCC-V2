"""No-clobber publication of a bounded, complete six-file evidence packet.

The pre-existing absolute root must be a trusted, service-owned directory.
Windows pins every directory ancestor without delete sharing and takes an
exclusive publisher lease on the root's add-file access. Linux uses
directory descriptors, O_NOFOLLOW and a cooperative nonblocking root flock.
These controls do not defend against an administrator or malicious same-user
code that can mutate the publisher, ignore its lease, or change the trusted ACL.

report.json is published last as a logical commit marker, not a six-file
filesystem transaction. Interrupted/incomplete directories are retained and
rejected; only this invocation's private temporary files are cleaned up. A
receipt proves publication consistency, not source truth or execution authority.
Files are fsynced and read back on both platforms; only POSIX also fsyncs the
directory metadata. In particular, Windows publication does not promise that a
complete packet survives sudden power loss as an atomic, durable transaction.
Any unavailable ancestor pin or publisher lease fails closed; success of the
POSIX path does not establish native Windows chain or junction protection.
"""

from __future__ import annotations

import ctypes
import hashlib
import io
import json
import os
import re
import stat
import uuid
import zlib
from collections.abc import Callable, Mapping
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PNG_NAMES = ("4h.png", "1h.png", "15m.png", "5m.png", "summary.png")
FILE_NAMES = (*PNG_NAMES, "report.json")
MAX_PNG_BYTES = 8 * 1024 * 1024
MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_DIMENSION = 8192
_REPORT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}")
_DIGEST = re.compile(r"[a-f0-9]{64}")
_WINDOWS_DEVICE = re.compile(
    r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", re.IGNORECASE
)
_REPARSE_POINT = 0x400


class EvidencePublicationError(RuntimeError):
    pass


def actual_utc() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise EvidencePublicationError(
            "publication clock must return an aware datetime"
        )
    return value.astimezone(UTC)


class PublishedFile(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: Literal["4h.png", "1h.png", "15m.png", "5m.png", "summary.png", "report.json"]
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    size_bytes: Annotated[int, Field(gt=0, le=MAX_REPORT_BYTES)]


class PublicationReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    status: Literal["written", "already_present"]
    report_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")]
    report_directory: str
    report_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    files: tuple[PublishedFile, ...]
    completed_at: datetime
    execution_authority: Literal[False] = False

    _utc_complete = field_validator("completed_at")(_utc)

    @field_validator("execution_authority", mode="before")
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("publication cannot grant execution authority")
        return value

    @model_validator(mode="after")
    def complete_packet(self):
        if tuple(item.name for item in self.files) != FILE_NAMES:
            raise ValueError("receipt must describe the exact ordered six-file packet")
        if self.files[-1].sha256 != self.report_sha256:
            raise ValueError("receipt report digest differs from its file record")
        return self


def _report_name(report_id: str) -> None:
    if type(report_id) is not str or _REPORT_ID.fullmatch(report_id) is None:
        raise EvidencePublicationError("report_id is not a safe single path component")
    if _WINDOWS_DEVICE.fullmatch(report_id):
        raise EvidencePublicationError("report_id cannot be a Windows device name")


def _root_path(root: Path) -> Path:
    if not isinstance(root, Path) or not root.is_absolute():
        raise EvidencePublicationError(
            "evidence root must be an absolute pre-existing directory"
        )
    if root.anchor.startswith(("\\", "//")) or root.drive.startswith("\\"):
        raise EvidencePublicationError("UNC and device roots are not supported")
    if len(root.parts) < 2:
        raise EvidencePublicationError(
            "a filesystem or drive root is not an evidence directory"
        )
    for part in root.parts[1:]:
        if (
            part in {".", ".."}
            or ":" in part
            or "\\" in part
            or any(ord(character) < 32 for character in part)
            or part != part.rstrip(" .")
            or _WINDOWS_DEVICE.fullmatch(part)
        ):
            raise EvidencePublicationError(
                "evidence root contains an unsafe path component"
            )
    # Do not resolve here: that would hide a junction/symlink from the chain check.
    return root


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON keys are not allowed")
        result[key] = value
    return result


def _json_constant(value):
    raise ValueError(f"nonfinite JSON constant {value} is not allowed")


def _png_dimensions(payload: bytes) -> tuple[int, int]:
    if not 0 < len(payload) <= MAX_PNG_BYTES or payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise EvidencePublicationError("image is not a bounded PNG")
    offset, count, dimensions, saw_data = 8, 0, None, False
    while offset < len(payload):
        count += 1
        if count > 65536 or len(payload) - offset < 12:
            raise EvidencePublicationError("PNG chunk framing is invalid")
        size = int.from_bytes(payload[offset : offset + 4], "big")
        kind = payload[offset + 4 : offset + 8]
        end = offset + 12 + size
        if end > len(payload):
            raise EvidencePublicationError("PNG chunk exceeds its bounded payload")
        data = payload[offset + 8 : offset + 8 + size]
        expected_crc = int.from_bytes(payload[offset + 8 + size : end], "big")
        if zlib.crc32(kind + data) & 0xFFFFFFFF != expected_crc:
            raise EvidencePublicationError("PNG chunk checksum is invalid")
        if count == 1:
            if kind != b"IHDR" or size != 13:
                raise EvidencePublicationError(
                    "PNG must begin with a single valid IHDR"
                )
            width, height = (
                int.from_bytes(data[:4], "big"),
                int.from_bytes(data[4:8], "big"),
            )
            if (
                not (
                    0 < width <= MAX_IMAGE_DIMENSION
                    and 0 < height <= MAX_IMAGE_DIMENSION
                )
                or width * height > MAX_IMAGE_PIXELS
            ):
                raise EvidencePublicationError(
                    "PNG dimensions exceed the publication limit"
                )
            dimensions = width, height
        elif kind == b"IHDR":
            raise EvidencePublicationError("PNG contains duplicate IHDR chunks")
        if kind in {b"acTL", b"fcTL", b"fdAT"}:
            raise EvidencePublicationError("animated PNG evidence is not supported")
        if kind == b"IDAT":
            saw_data = True
        if kind == b"IEND":
            if size or end != len(payload) or not saw_data:
                raise EvidencePublicationError(
                    "PNG has an invalid end or trailing payload"
                )
            break
        offset = end
    else:
        raise EvidencePublicationError("PNG is missing its end chunk")
    try:
        with Image.open(io.BytesIO(payload), formats=("PNG",)) as decoded:
            if (
                decoded.format != "PNG"
                or decoded.size != dimensions
                or getattr(decoded, "n_frames", 1) != 1
            ):
                raise EvidencePublicationError("PNG decoder metadata is inconsistent")
            decoded.verify()
        with Image.open(io.BytesIO(payload), formats=("PNG",)) as decoded:
            decoded.load()
            if decoded.size != dimensions:
                raise EvidencePublicationError("PNG decoder dimensions changed")
    except EvidencePublicationError:
        raise
    except Exception as exc:
        raise EvidencePublicationError("PNG failed complete decoding") from exc
    return dimensions


def _packet(report_id: str, files: Mapping[str, bytes]) -> dict[str, bytes]:
    if not isinstance(files, Mapping) or set(files) != set(FILE_NAMES):
        raise EvidencePublicationError(
            "publication requires the exact six fixed filenames"
        )
    packet = {name: files[name] for name in FILE_NAMES}
    if any(type(value) is not bytes for value in packet.values()):
        raise EvidencePublicationError("publication payloads must be immutable bytes")
    dimensions = {name: _png_dimensions(packet[name]) for name in PNG_NAMES}
    if not 0 < len(packet["report.json"]) <= MAX_REPORT_BYTES:
        raise EvidencePublicationError("report JSON exceeds its byte limit")
    try:
        report = json.loads(
            packet["report.json"].decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_json_constant,
            parse_float=Decimal,
        )
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise EvidencePublicationError(
            "report JSON is not a valid unique-key object"
        ) from exc
    if (
        type(report) is not dict
        or report.get("schema_version") != "ctcc.trade_evidence.v1"
        or report.get("report_id") != report_id
        or type(report.get("images")) is not dict
        or set(report["images"]) != set(PNG_NAMES)
    ):
        raise EvidencePublicationError("report identity or image manifest is invalid")
    for name in PNG_NAMES:
        record = report["images"][name]
        if (
            type(record) is not dict
            or type(record.get("sha256")) is not str
            or _DIGEST.fullmatch(record["sha256"]) is None
            or record["sha256"] != hashlib.sha256(packet[name]).hexdigest()
            or type(record.get("size_bytes")) is not int
            or record["size_bytes"] != len(packet[name])
        ):
            raise EvidencePublicationError(
                "report image digest or size does not match its PNG"
            )
        for field, expected in zip(("width", "height"), dimensions[name], strict=True):
            if field in record and (
                type(record[field]) is not int or record[field] != expected
            ):
                raise EvidencePublicationError(
                    "report image dimensions do not match its PNG"
                )
    return packet


def _regular_file(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
        or info.st_nlink != 1
    ):
        raise EvidencePublicationError(
            "evidence must be a real, unaliased regular file"
        )


def _read_fd(fd: int, maximum: int) -> bytes:
    before = os.fstat(fd)
    _regular_file(before)
    if not 0 < before.st_size <= maximum:
        raise EvidencePublicationError("existing evidence size is invalid")
    parts, remaining = [], maximum + 1
    while remaining:
        part = os.read(fd, min(65536, remaining))
        if not part:
            break
        parts.append(part)
        remaining -= len(part)
    after = os.fstat(fd)
    if len(b"".join(parts)) != before.st_size or (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise EvidencePublicationError("evidence changed during bounded readback")
    return b"".join(parts)


def _write_fd(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise EvidencePublicationError("evidence write made no progress")
        view = view[written:]
    os.fsync(fd)


class _PosixDirectory:
    def __init__(self, path: Path, fd: int):
        self.path, self.fd = path, fd

    def names(self):
        return os.listdir(self.fd)

    def mkdir(self, name: str):
        os.mkdir(name, mode=0o700, dir_fd=self.fd)
        os.fsync(self.fd)

    @contextmanager
    def child(self, name: str):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            yield _PosixDirectory(self.path / name, fd)
        finally:
            os.close(fd)

    def read(self, name: str, maximum: int):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            return _read_fd(fd, maximum)
        finally:
            os.close(fd)

    def publish(self, name: str, payload: bytes):
        temporary = f".{name}.{uuid.uuid4().hex}.partial"
        owned = False
        try:
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode=0o600,
                dir_fd=self.fd,
            )
            owned = True
            try:
                _write_fd(fd, payload)
            finally:
                os.close(fd)
            os.link(
                temporary,
                name,
                src_dir_fd=self.fd,
                dst_dir_fd=self.fd,
                follow_symlinks=False,
            )
        finally:
            if owned:
                os.unlink(temporary, dir_fd=self.fd)
        os.fsync(self.fd)


@contextmanager
def _posix_root(root: Path):
    import fcntl

    with ExitStack() as stack:
        fd = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        for part in root.parts[1:]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stack.callback(fcntl.flock, fd, fcntl.LOCK_UN)
        yield _PosixDirectory(root, fd)


class _WindowsAPI:
    def __init__(self):
        from ctypes import wintypes

        class FileInformation(ctypes.Structure):
            _fields_ = [
                ("attributes", wintypes.DWORD),
                ("creation", wintypes.FILETIME),
                ("access", wintypes.FILETIME),
                ("write", wintypes.FILETIME),
                ("volume", wintypes.DWORD),
                ("size_high", wintypes.DWORD),
                ("size_low", wintypes.DWORD),
                ("links", wintypes.DWORD),
                ("index_high", wintypes.DWORD),
                ("index_low", wintypes.DWORD),
            ]

        self.info_type = FileInformation
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.create = self.kernel.CreateFileW
        self.create.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self.create.restype = wintypes.HANDLE
        self.close = self.kernel.CloseHandle
        self.close.argtypes, self.close.restype = [wintypes.HANDLE], wintypes.BOOL
        self.info = self.kernel.GetFileInformationByHandle
        self.info.argtypes, self.info.restype = (
            [wintypes.HANDLE, ctypes.POINTER(FileInformation)],
            wintypes.BOOL,
        )

    def open_directory(self, path: Path, *, publisher=False):
        # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES (+ FILE_ADD_FILE for lease).
        # Attribute-only access is exempt from sharing checks and cannot pin a
        # directory. LIST_DIRECTORY/ADD_FILE use the read/write data access bits.
        # Omitting FILE_SHARE_WRITE on the root serializes cooperating publishers;
        # no directory handle ever grants FILE_SHARE_DELETE to ancestor replacement.
        access = 0x81 | (0x2 if publisher else 0)
        share = 0x1 if publisher else 0x3
        return self._open(
            path, access, share, 3, 0x02000000 | 0x00200000, directory=True
        )

    def _open(self, path: Path, access, share, disposition, flags, *, directory=False):
        handle = self.create(str(path), access, share, None, disposition, flags, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = self.info_type()
            if not self.info(handle, ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if (
                info.attributes & _REPARSE_POINT
                or bool(info.attributes & 0x10) != directory
            ):
                raise EvidencePublicationError(
                    "publication path contains a reparse point or wrong file type"
                )
        except BaseException:
            self.close(handle)
            raise
        return handle

    def open_file(self, path: Path, *, create=False):
        import msvcrt

        handle = self._open(
            path,
            0x40000000 if create else 0x80000000,
            0x1,
            1 if create else 3,
            0x00200000,
        )
        try:
            return msvcrt.open_osfhandle(
                handle, (os.O_WRONLY if create else os.O_RDONLY) | os.O_BINARY
            )
        except BaseException:
            self.close(handle)
            raise


class _WindowsDirectory:
    def __init__(self, path: Path, api: _WindowsAPI):
        self.path, self.api = path, api

    def names(self):
        return os.listdir(self.path)

    def mkdir(self, name: str):
        os.mkdir(self.path / name, mode=0o700)

    @contextmanager
    def child(self, name: str):
        handle = self.api.open_directory(self.path / name)
        try:
            yield _WindowsDirectory(self.path / name, self.api)
        finally:
            self.api.close(handle)

    def read(self, name: str, maximum: int):
        fd = self.api.open_file(self.path / name)
        try:
            return _read_fd(fd, maximum)
        finally:
            os.close(fd)

    def publish(self, name: str, payload: bytes):
        temporary = self.path / f".{name}.{uuid.uuid4().hex}.partial"
        owned = False
        try:
            fd = self.api.open_file(temporary, create=True)
            owned = True
            try:
                _write_fd(fd, payload)
            finally:
                os.close(fd)
            os.link(temporary, self.path / name, follow_symlinks=False)
        finally:
            if owned:
                os.unlink(temporary)


@contextmanager
def _windows_root(root: Path):
    api = _WindowsAPI()
    with ExitStack() as stack:
        for path in (*reversed(root.parents), root):
            handle = api.open_directory(path, publisher=path == root)
            stack.callback(api.close, handle)
        yield _WindowsDirectory(root, api)


def _verify(directory, packet: dict[str, bytes]):
    if set(directory.names()) != set(FILE_NAMES):
        raise EvidencePublicationError(
            "existing report is incomplete or contains unexpected files"
        )
    for name in FILE_NAMES:
        maximum = MAX_REPORT_BYTES if name == "report.json" else MAX_PNG_BYTES
        if directory.read(name, maximum) != packet[name]:
            raise EvidencePublicationError(
                "existing evidence conflicts with the complete packet"
            )


def publish_evidence(
    root: Path,
    *,
    report_id: str,
    files: Mapping[str, bytes],
    clock: Callable[[], datetime] = actual_utc,
) -> PublicationReceipt:
    """Validate first, publish without replacement, read back, then timestamp.

    The injected clock is a trusted runtime dependency, not a verifiable source.
    It is sampled before publication and after all readback; backwards clocks
    cannot produce a receipt. Idempotent verification produces a new completion
    receipt but changes no packet bytes. Concurrent losers may fail closed.
    """
    try:
        started_at = _utc(clock())
        _report_name(report_id)
        root = _root_path(root)
        packet = _packet(report_id, files)
        root_context = _windows_root if os.name == "nt" else _posix_root
        with root_context(root) as directory:
            names = directory.names()
            if any(
                name.casefold() == report_id.casefold() and name != report_id
                for name in names
            ):
                raise EvidencePublicationError(
                    "report_id conflicts with an existing case alias"
                )
            created = False
            try:
                directory.mkdir(report_id)
                created = True
            except FileExistsError:
                pass
            with directory.child(report_id) as report_directory:
                if created:
                    for name in FILE_NAMES:
                        report_directory.publish(name, packet[name])
                _verify(report_directory, packet)
                completed_at = _utc(clock())
                if completed_at < started_at:
                    raise EvidencePublicationError("publication clock moved backwards")
                records = tuple(
                    PublishedFile(
                        name=name,
                        sha256=hashlib.sha256(packet[name]).hexdigest(),
                        size_bytes=len(packet[name]),
                    )
                    for name in FILE_NAMES
                )
                return PublicationReceipt(
                    status="written" if created else "already_present",
                    report_id=report_id,
                    report_directory=str(root / report_id),
                    report_sha256=records[-1].sha256,
                    files=records,
                    completed_at=completed_at,
                )
    except EvidencePublicationError:
        raise
    except Exception as exc:
        raise EvidencePublicationError(
            "evidence publication failed without a completion receipt"
        ) from exc
