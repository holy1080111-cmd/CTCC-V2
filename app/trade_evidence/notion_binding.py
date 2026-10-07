"""Read-only destination binding and private local credential-file handling."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path

import httpx
from pydantic import SecretStr

from app.trade_evidence import notion_adapter as notion
from app.trade_evidence import outbox, storage
from app.trade_qualification.quote_collector import _close_response


class NotionBindingError(ValueError):
    def __init__(self, code: str, *, retry_after_seconds: int | None = 0):
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code)


def external_secret_path(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise NotionBindingError("notion_secret_path_invalid")
    for ancestor in (path, *path.parents):
        if ancestor.exists() and (
            ancestor.is_symlink()
            or getattr(ancestor.stat(), "st_file_attributes", 0) & 0x400
        ):
            raise NotionBindingError("notion_secret_path_reparse")
        if (ancestor / ".git").exists():
            raise NotionBindingError("notion_secret_inside_git_source")
    source = Path(__file__).resolve().parents[2]
    workspace = source.parent
    # A managed checkout can be a sibling of validation-results and release
    # archives. A checkout directly in the user's home must not make all of
    # AppData (or a POSIX home) an excluded workspace.
    managed_workspace = (
        workspace != Path(source.anchor) and (workspace / "validation-results").is_dir()
    )
    if path.is_relative_to(source) or (
        managed_workspace and path.is_relative_to(workspace)
    ):
        raise NotionBindingError("notion_secret_inside_source_or_workspace")
    if any(
        part.casefold()
        in {
            "reports",
            "artifacts",
            "backups",
            "validation-results",
            "evidence",
            "release-archives",
            "source-archives",
            "final-evidence",
            "final-evidence-bundle",
            "checkpoints",
        }
        for part in path.parts
    ):
        raise NotionBindingError("notion_secret_inside_evidence")
    return path


def _windows_private_acl(path: Path, *, create: bool = False):
    # Only this exact file is affected; no machine policy/directory ACL changes.
    setup = (
        """
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = New-Object System.Security.AccessControl.FileSecurity
$acl.SetOwner($sid)
$acl.SetAccessRuleProtection($true, $false)
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'Allow')
$acl.AddAccessRule($rule)
[System.IO.File]::SetAccessControl($env:CTCC_PRIVATE_FILE, $acl)
"""
        if create
        else ""
    )
    check = """
$ErrorActionPreference = 'Stop'
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$acl = [System.IO.File]::GetAccessControl($env:CTCC_PRIVATE_FILE)
if (-not $acl.AreAccessRulesProtected) { exit 2 }
if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $sid) { exit 4 }
$rules = $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])
if ($rules.Count -ne 1 -or $rules[0].IdentityReference.Value -ne $sid -or $rules[0].AccessControlType -ne 'Allow') { exit 3 }
"""
    env = os.environ.copy()
    env["CTCC_PRIVATE_FILE"] = str(path)
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference = 'Stop'\n" + setup + check,
        ],
        env=env,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        raise NotionBindingError("notion_private_file_permissions_invalid")


def private_permissions(path: Path, *, fd: int):
    info = os.fstat(fd)
    current = path.stat(follow_symlinks=False)
    if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
        raise NotionBindingError("notion_private_file_identity_invalid")
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise NotionBindingError("notion_private_file_identity_invalid")
    if os.name == "nt":
        # Native open denies replacement and write sharing while all ancestors
        # remain pinned. The path ACL therefore belongs to this exact open fd.
        _windows_private_acl(path)
    elif stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.geteuid():
        raise NotionBindingError("notion_private_file_permissions_invalid")


@contextmanager
def private_file(path: Path, *, create=False):
    path = external_secret_path(path)
    with outbox._root_context(storage._root_path(path.parent)) as directory:
        if os.name == "nt":
            import msvcrt

            handle = directory.api._open(
                path,
                0xC0000000 if create else 0x80000000,
                0x1,
                1 if create else 3,
                0x00200000,
            )
            try:
                fd = msvcrt.open_osfhandle(
                    handle, (os.O_RDWR if create else os.O_RDONLY) | os.O_BINARY
                )
            except BaseException:
                directory.api.close(handle)
                raise
        else:
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY
            fd = os.open(
                path.name,
                flags | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=directory.fd,
            )
        try:
            if create and os.name == "nt":
                _windows_private_acl(path, create=True)
            private_permissions(path, fd=fd)
            yield fd
            private_permissions(path, fd=fd)
        finally:
            os.close(fd)


def save_private_token(path: Path, token: SecretStr):
    raw = notion._secret(token).encode("ascii")
    with private_file(path, create=True) as fd:
        storage._write_fd(fd, raw)
        os.lseek(fd, 0, os.SEEK_SET)
        if storage._read_fd(fd, 512) != raw:
            raise NotionBindingError("notion_private_file_readback_failed")


def load_private_token(path: Path) -> SecretStr:
    with private_file(path) as fd:
        raw = storage._read_fd(fd, 512)
    token = SecretStr(raw.decode("ascii"))
    notion._secret(token)
    return token


def load_destination(path: Path, expected_sha256: str) -> notion.NotionDestination:
    if not path.is_absolute() or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None:
        raise NotionBindingError("notion_destination_pin_missing")
    with outbox._root_context(storage._root_path(path.parent)) as directory:
        raw = directory.read(path.name, 16384)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise NotionBindingError("notion_destination_pin_mismatch")
    return notion.NotionDestination.model_validate_json(raw)


async def read_notion_resource(client, token, *, kind: str, identity: str):
    """A bounded GET only; no automatic HTTP retry, proxy, redirect or cookie merge."""
    if kind not in {"databases", "data_sources"}:
        raise NotionBindingError("notion_binding_endpoint_invalid")
    identity = notion._uuid(identity)
    notion._client(client, initial=True)
    request = httpx.Request(
        "GET",
        f"{notion.ORIGIN}/v1/{kind}/{identity}",
        headers={
            "Authorization": "Bearer " + notion._secret(token),
            "Notion-Version": notion.API_VERSION,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
        extensions={"timeout": httpx.Timeout(3).as_dict()},
    )
    response = None
    cancelled = False
    try:
        async with asyncio.timeout(4):
            response = await client.send(
                request, stream=True, auth=None, follow_redirects=False
            )
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            if response.status_code == 429:
                values = response.headers.get_list("retry-after")
                retry = values[0] if len(values) == 1 else ""
                # No trusted retry bound means stay paused until restart/config repair.
                seconds = int(retry) if re.fullmatch(r"[0-9]{1,5}", retry) else None
                raise NotionBindingError(
                    "notion_binding_rate_limited",
                    retry_after_seconds=None if seconds is None else max(1, seconds),
                )
            if response.status_code != 200:
                raise NotionBindingError("notion_binding_http_rejected")
            if (
                len(response.headers.raw) > 128
                or sum(len(k) + len(v) for k, v in response.headers.raw) > 32768
                or len(response.headers.get_list("content-type")) != 1
                or response.headers["content-type"].split(";", 1)[0].strip().lower()
                != "application/json"
                or response.headers.get("content-encoding", "identity").lower()
                != "identity"
                or len(response.headers.get_list("content-encoding")) > 1
                or len(response.headers.get_list("content-length")) > 1
            ):
                raise NotionBindingError("notion_binding_headers_invalid")
            length = response.headers.get("content-length")
            if length is not None and (
                re.fullmatch(r"[0-9]{1,6}", length) is None or int(length) > 131072
            ):
                raise NotionBindingError("notion_binding_headers_invalid")
            raw = bytearray()
            count = 0
            async for chunk in response.stream:
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
                count += 1
                if (
                    type(chunk) is not bytes
                    or count > 1024
                    or len(raw) + len(chunk) > 131072
                ):
                    raise NotionBindingError("notion_binding_response_bound")
                raw.extend(chunk)
            if length is not None and len(raw) != int(length):
                raise NotionBindingError("notion_binding_length_mismatch")
            data = notion._bounded_json(bytes(raw))
            return data
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        if response is not None:
            await _close_response(response, pending_cancel=cancelled)


def database_sources(data, database_id):
    if data.get("object") != "database" or notion._uuid(data.get("id")) != database_id:
        raise NotionBindingError("notion_database_identity_mismatch")
    notion._active(data)
    sources = data.get("data_sources")
    if type(sources) is not list or not 1 <= len(sources) <= 100:
        raise NotionBindingError("notion_database_sources_incomplete")
    identities = [notion._uuid(row.get("id")) for row in sources if type(row) is dict]
    if len(identities) != len(sources) or len(set(identities)) != len(identities):
        raise NotionBindingError("notion_database_sources_invalid")
    return tuple(identities)


async def verify_binding(client, token, destination):
    database = await read_notion_resource(
        client, token, kind="databases", identity=destination.database_id
    )
    if destination.data_source_id not in database_sources(
        database, destination.database_id
    ):
        raise NotionBindingError("notion_database_source_mismatch")
    schema = await read_notion_resource(
        client, token, kind="data_sources", identity=destination.data_source_id
    )
    notion._schema(schema, destination)
    return {
        "database_sha256": notion._sha(notion._json(database)),
        "schema_sha256": notion._sha(notion._json(schema)),
        "destination_sha256": notion.destination_sha256(destination),
    }
