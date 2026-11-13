"""Prepare a persistent Windows localhost analysis installation without Docker.

The new directory has a verified owner/System-only NTFS ACL. Only local DB and
API tokens are generated; no OKX or Notion credential is read or created.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import secrets
import stat
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

from scripts import local_analysis_private_dir as private_dir
from scripts import manifest

_SOURCE = Path(__file__).resolve().parents[1]
_TEMPLATE = _SOURCE / "config/compose.local-analysis.template.yaml"
_SECRET_NAMES = ("postgres_password", "database_url", "api_token")
_MEMBERS = frozenset(
    (*_SECRET_NAMES, "compose.env", "compose.yaml", "installation.json")
)
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_PLACEHOLDER = re.compile(r"__[A-Z_]+__", re.ASCII)


class LocalAnalysisInstallRefusal(ValueError):
    """Fixed refusal; secret values and private paths never enter exceptions."""


def _need(value: bool) -> None:
    if value is not True:
        raise LocalAnalysisInstallRefusal(
            "local_analysis_installation_refused"
        ) from None


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_regular(path: Path, maximum: int) -> bytes:
    before = path.lstat()
    _need(
        stat.S_ISREG(before.st_mode)
        and not path.is_symlink()
        and not path.is_junction()
        and 0 < before.st_size <= maximum
    )
    raw = path.read_bytes()
    after = path.lstat()
    _need(
        len(raw) == before.st_size
        and (before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_ino, after.st_size, after.st_mtime_ns)
    )
    return raw


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=_SOURCE,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocalAnalysisInstallRefusal(
            "local_analysis_source_identity_refused"
        ) from exc
    _need(result.returncode == 0 and len(result.stdout) <= 4096)
    return result.stdout.decode("utf-8").strip()


def _source_identity() -> tuple[str, bytes, str, str]:
    _need(_SOURCE.is_dir() and not _SOURCE.is_symlink() and not _SOURCE.is_junction())
    _need(Path(_git("rev-parse", "--show-toplevel")).resolve(strict=True) == _SOURCE)
    _need(_git("status", "--porcelain=v1", "--untracked-files=all") == "")
    commit = _git("rev-parse", "--verify", "HEAD")
    tree = _git("rev-parse", "--verify", "HEAD^{tree}")
    _need(
        _GIT_SHA.fullmatch(commit) is not None and _GIT_SHA.fullmatch(tree) is not None
    )
    with redirect_stdout(io.StringIO()):
        _need(manifest.check_manifest(_SOURCE, _SOURCE / "MANIFEST.sha256"))
    manifest_raw = _read_regular(_SOURCE / "MANIFEST.sha256", 2_000_000)
    template = _read_regular(_TEMPLATE, 24_000)
    return _sha(manifest_raw), template, commit, tree


def _render(
    template: bytes,
    *,
    root: Path,
    install_id: str,
    source_manifest_sha256: str,
    source_commit: str,
    source_tree: str,
    host_port: int,
) -> bytes:
    _need(
        re.fullmatch(r"[0-9a-f]{32}", install_id, re.ASCII) is not None
        and _SHA.fullmatch(source_manifest_sha256) is not None
        and _GIT_SHA.fullmatch(source_commit) is not None
        and _GIT_SHA.fullmatch(source_tree) is not None
        and type(host_port) is int
        and 1024 <= host_port <= 65535
    )
    text = template.decode("utf-8")
    replacements = {
        "__INSTALL_ID__": install_id,
        "__SOURCE_SHORT__": source_manifest_sha256[:12],
        "__SOURCE_COMMIT__": source_commit,
        "__SOURCE_TREE__": source_tree,
        "__SOURCE_ROOT_JSON__": json.dumps(_SOURCE.as_posix()),
        "__LOOPBACK_PORT_JSON__": json.dumps(f"127.0.0.1:{host_port}:8000"),
        "__POSTGRES_PASSWORD_PATH_JSON__": json.dumps(
            (root / "postgres_password").as_posix()
        ),
        "__DATABASE_URL_PATH_JSON__": json.dumps((root / "database_url").as_posix()),
        "__API_TOKEN_PATH_JSON__": json.dumps((root / "api_token").as_posix()),
    }
    _need(set(_PLACEHOLDER.findall(text)) == set(replacements))
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    _need(_PLACEHOLDER.search(text) is None and "${" not in text)
    rendered = text.encode("utf-8")
    _need(0 < len(rendered) <= 32_768)
    return rendered


def _new_file(path: Path, raw: bytes, *, secret: bool) -> str:
    _need(type(raw) is bytes and 0 < len(raw) <= 32_768)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if fd >= 0:
            os.close(fd)
    # Compose file secrets are bind mounts: uid/gid/mode remapping is ignored.
    # The protected parent ACL restricts host traversal; 0644 lets the image's
    # nonroot `ctcc` user read its two mounted secrets in the Linux container.
    if secret:
        os.chmod(path, 0o644)
    _need(_read_regular(path, 32_768) == raw)
    return _sha(raw)


def _new_empty_file(path: Path) -> str:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    _need(
        path.lstat().st_size == 0
        and path.is_file()
        and not path.is_symlink()
        and not path.is_junction()
    )
    return _sha(b"")


def _checked_root(root: Path) -> None:
    base = (Path.home() / "AppData/Local").absolute()
    _need(
        root.is_absolute()
        and root.parent == base
        and private_dir.LEAF.fullmatch(root.name) is not None
        and root.is_dir()
        and not root.is_symlink()
        and not root.is_junction()
        and root.resolve(strict=True) == root
    )
    kernel, security = private_dir.api()
    user = private_dir.current_user(kernel, security)
    private_dir.verify_acl(kernel, security, root, user)


def prepare(*, host_port: int = 8100) -> dict[str, object]:
    manifest_sha, template, commit, tree = _source_identity()
    _need(type(host_port) is int and 1024 <= host_port <= 65535)
    root = private_dir.create_private_work()
    _checked_root(root)
    install_id = root.name[-32:]
    password = secrets.token_hex(32).encode("ascii")
    token = secrets.token_urlsafe(48).encode("ascii")
    database_url = b"postgresql+asyncpg://ctcc:" + password + b"@postgres:5432/ctcc"
    secret_bytes = {
        "postgres_password": password,
        "database_url": database_url,
        "api_token": token,
    }
    secret_hashes = {
        name: _new_file(root / name, raw, secret=True)
        for name, raw in secret_bytes.items()
    }
    compose_env_sha = _new_empty_file(root / "compose.env")
    compose = _render(
        template,
        root=root,
        install_id=install_id,
        source_manifest_sha256=manifest_sha,
        source_commit=commit,
        source_tree=tree,
        host_port=host_port,
    )
    compose_sha = _new_file(root / "compose.yaml", compose, secret=False)
    metadata = {
        "schema": "ctcc.local_analysis.installation.v1",
        "installation_id": install_id,
        "host_port": host_port,
        "source_root": _SOURCE.as_posix(),
        "source_manifest_sha256": manifest_sha,
        "source_commit": commit,
        "source_tree": tree,
        "template_sha256": _sha(template),
        "compose_env_sha256": compose_env_sha,
        "compose_sha256": compose_sha,
        "secret_sha256": secret_hashes,
        "exchange_credentials_present": False,
        "trading_enabled": False,
    }
    _new_file(
        root / "installation.json",
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("ascii"),
        secret=False,
    )
    checked = verify(root)
    _need(checked["status"] == "READY_TO_START_ANALYSIS_ONLY")
    return checked


def verify(root: Path) -> dict[str, object]:
    _checked_root(root)
    _need({path.name for path in root.iterdir()} == _MEMBERS)
    metadata = json.loads(_read_regular(root / "installation.json", 4096))
    _need(
        type(metadata) is dict
        and set(metadata)
        == {
            "schema",
            "installation_id",
            "host_port",
            "source_root",
            "source_manifest_sha256",
            "source_commit",
            "source_tree",
            "template_sha256",
            "compose_env_sha256",
            "compose_sha256",
            "secret_sha256",
            "exchange_credentials_present",
            "trading_enabled",
        }
        and metadata["schema"] == "ctcc.local_analysis.installation.v1"
        and metadata["exchange_credentials_present"] is False
        and metadata["trading_enabled"] is False
        and metadata["installation_id"] == root.name[-32:]
        and metadata["source_root"] == _SOURCE.as_posix()
        and type(metadata["secret_sha256"]) is dict
        and set(metadata["secret_sha256"]) == set(_SECRET_NAMES)
    )
    manifest_sha, template, commit, tree = _source_identity()
    _need(
        metadata["source_manifest_sha256"] == manifest_sha
        and metadata["template_sha256"] == _sha(template)
        and metadata["source_commit"] == commit
        and metadata["source_tree"] == tree
        and metadata["compose_env_sha256"] == _sha(b"")
    )
    empty = root / "compose.env"
    _need(
        empty.is_file()
        and not empty.is_symlink()
        and not empty.is_junction()
        and empty.lstat().st_size == 0
    )
    secrets_raw = {name: _read_regular(root / name, 4096) for name in _SECRET_NAMES}
    _need(
        all(
            _sha(secrets_raw[name]) == metadata["secret_sha256"][name]
            for name in _SECRET_NAMES
        )
        and re.fullmatch(rb"[0-9a-f]{64}", secrets_raw["postgres_password"]) is not None
        and len(secrets_raw["api_token"]) >= 48
        and secrets_raw["database_url"]
        == b"postgresql+asyncpg://ctcc:"
        + secrets_raw["postgres_password"]
        + b"@postgres:5432/ctcc"
    )
    expected_compose = _render(
        template,
        root=root,
        install_id=metadata["installation_id"],
        source_manifest_sha256=manifest_sha,
        source_commit=commit,
        source_tree=tree,
        host_port=metadata["host_port"],
    )
    _need(
        _sha(expected_compose) == metadata["compose_sha256"]
        and _read_regular(root / "compose.yaml", 32_768) == expected_compose
    )
    return {
        "schema": "ctcc.local_analysis.installation_check.v1",
        "status": "READY_TO_START_ANALYSIS_ONLY",
        "installation_root": str(root),
        "installation_id": metadata["installation_id"],
        "project_name": "ctcc-analysis-" + metadata["installation_id"],
        "compose_file": str(root / "compose.yaml"),
        "dashboard_url": f"http://127.0.0.1:{metadata['host_port']}/dashboard",
        "readiness_url": f"http://127.0.0.1:{metadata['host_port']}/readiness",
        "exchange_credentials_present": False,
        "trading_enabled": False,
        "docker_started": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", action="store_true")
    action.add_argument("--check", type=Path)
    parser.add_argument("--host-port", type=int, default=8100)
    try:
        args = parser.parse_args(argv)
        result = (
            prepare(host_port=args.host_port)
            if args.prepare
            else verify(args.check.absolute())
        )
    except Exception:  # noqa: BLE001 -- fixed refusal, no private values or paths
        print('{"status":"REFUSED","code":"local_analysis_installation_refused"}')
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
