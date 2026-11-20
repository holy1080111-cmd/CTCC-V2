"""Stage and verify an exact-source Windows tool; never migrate or arm it.

Each release is a separate directory. Private settings, exchange state and
databases are never copied into a release or changed by package installation.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path, PurePosixPath

from scripts import manifest

SCHEMA = "ctcc.windows_tool.physical0058.staged_release.v1"
_SOURCE = Path(__file__).resolve().parents[1]
_HEX = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_GIT = re.compile(r"[a-f0-9]{40}\Z", re.ASCII)
_DEPENDENCIES = (
    "pyproject.toml",
    "requirements/validation-windows-py312.json",
    "requirements/validation-windows-py312.lock",
)
_MAX_ARCHIVE = 200_000_000


class WindowsToolRefused(ValueError):
    """Fixed safe output; provider, pathname and secret values stay private."""


class PrivateParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "windows_tool_0058_arguments_refused\n")


def need(value):
    if value is not True:
        raise WindowsToolRefused("windows_tool_0058_refused") from None


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def regular(path, maximum):
    before = path.lstat()
    need(
        stat.S_ISREG(before.st_mode)
        and before.st_nlink == 1
        and not getattr(before, "st_file_attributes", 0) & 1024
        and not path.is_symlink()
        and 0 <= before.st_size <= maximum
    )
    raw = path.read_bytes()
    after = path.lstat()
    need(
        len(raw) == before.st_size
        and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    )
    return raw


def _new(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    need(regular(path, max(len(raw), 1)) == raw)


def _directory(path):
    need(path.is_absolute() and path.resolve(strict=True) == path)
    for item in (path, *path.parents):
        info = item.lstat()
        need(
            stat.S_ISDIR(info.st_mode)
            and not getattr(info, "st_file_attributes", 0) & 1024
        )


def _git(source, *arguments):
    result = subprocess.run(
        ["git", "-C", str(source), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    need(result.returncode == 0 and len(result.stdout) <= _MAX_ARCHIVE)
    return result.stdout


def _archive_members(raw):
    need(type(raw) is bytes and 0 < len(raw) <= _MAX_ARCHIVE)
    result = {}
    folded = set()
    total = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        need(len(archive.infolist()) <= 10000)
        for info in archive.infolist():
            name = info.filename
            parsed = PurePosixPath(name)
            need(
                info.orig_filename == name
                and name == parsed.as_posix() + ("/" if info.is_dir() else "")
                and not parsed.is_absolute()
                and all(
                    part not in ("", ".", "..")
                    and ":" not in part
                    and "\\" not in part
                    and part == part.rstrip(" .")
                    and not any(ord(character) < 32 for character in part)
                    and part.split(".", 1)[0].upper()
                    not in {
                        "CON",
                        "PRN",
                        "AUX",
                        "NUL",
                        *(f"COM{number}" for number in range(1, 10)),
                        *(f"LPT{number}" for number in range(1, 10)),
                    }
                    for part in parsed.parts
                )
                and not info.flag_bits & 1
                and stat.S_IFMT(info.external_attr >> 16)
                in (0, stat.S_IFREG, stat.S_IFDIR)
            )
            if info.is_dir():
                continue
            need(name.casefold() not in folded and 0 <= info.file_size <= 20_000_000)
            need(
                not any(
                    part.casefold()
                    in (
                        ".git",
                        ".venv",
                        "private",
                        "node_modules",
                        "validation-results",
                    )
                    for part in parsed.parts
                )
            )
            need(not parsed.name.startswith(".env") or parsed.name == ".env.example")
            need(
                parsed.suffix.lower() not in (".key", ".token", ".pfx", ".dump", ".log")
            )
            body = archive.read(info)
            need(len(body) == info.file_size)
            total += len(body)
            need(total <= _MAX_ARCHIVE)
            folded.add(name.casefold())
            result[name] = body
    need(bool(result))
    return result


def _migration_identity(members):
    inventory = {}
    revisions = {}
    parents = set()
    for name, raw in sorted(members.items()):
        if name.startswith("migrations/") and name.endswith(".py"):
            inventory[name] = sha(raw)
            if name.startswith("migrations/versions/"):
                values = {}
                for node in ast.parse(raw.decode("utf-8")).body:
                    targets = (
                        node.targets
                        if isinstance(node, ast.Assign)
                        else [node.target]
                        if isinstance(node, ast.AnnAssign)
                        else []
                    )
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id in (
                            "revision",
                            "down_revision",
                        ):
                            values[target.id] = ast.literal_eval(node.value)
                if "revision" in values:
                    revision, parent = values["revision"], values.get("down_revision")
                    need(
                        type(revision) is str
                        and revision not in revisions
                        and (parent is None or type(parent) is str)
                    )
                    revisions[revision] = parent
                    if parent:
                        parents.add(parent)
    need(len(inventory) == 59 and set(revisions) - parents == {"0058"})
    need(parents.issubset(revisions))
    return {
        "head": "0058",
        "count": len(inventory),
        "sha256": sha(canonical(inventory)),
    }


def _identity(members, commit, tree, archive_sha, zip_sha):
    need(_GIT.fullmatch(commit) is not None and _GIT.fullmatch(tree) is not None)
    need(_HEX.fullmatch(archive_sha) is not None)
    need(all(name in members for name in (*_DEPENDENCIES, "MANIFEST.sha256")))
    lock = json.loads(members[_DEPENDENCIES[1]])
    need(
        lock["schema"] == "ctcc.validation_dependency_lock.v1"
        and lock["target"] == "windows"
        and lock["python"] == "3.12"
        and lock["architecture"] == "x86_64"
        and lock["source_pyproject_sha256"] == sha(members["pyproject.toml"])
        and lock["lock_sha256"] == sha(members[_DEPENDENCIES[2]])
    )
    from scripts.verify_dependency_lock import PIN

    pins, options = {}, []
    for line in members[_DEPENDENCIES[2]].decode("ascii").splitlines():
        if not line or line.startswith("#"):
            continue
        if line.startswith("--"):
            options.append(line)
            continue
        match = PIN.fullmatch(line)
        need(match is not None and match[1] not in pins)
        pins[match[1]] = (match[2], match[3])
    need(options == ["--index-url https://pypi.org/simple", "--only-binary :all:"])
    need(
        type(lock["packages"]) is list
        and len(lock["packages"]) == len(pins)
        and bool(pins)
    )
    need(len({item["name"] for item in lock["packages"]}) == len(pins))
    for item in lock["packages"]:
        need(
            pins.get(item["name"]) == (item["version"], item["sha256"])
            and item["url"].startswith("https://files.pythonhosted.org/")
            and item["filename"] == PurePosixPath(item["filename"]).name
            and "\\" not in item["filename"]
            and item["filename"].endswith(".whl")
        )
    return {
        "schema": SCHEMA,
        "source_commit": commit,
        "source_tree": tree,
        "application_version": tomllib.loads(members["pyproject.toml"].decode())[
            "project"
        ]["version"],
        "source_archive_sha256": archive_sha,
        "source_zip_sha256": zip_sha,
        "source_manifest_sha256": sha(members["MANIFEST.sha256"]),
        "files": {name: sha(raw) for name, raw in sorted(members.items())},
        "dependency_sha256": sha(
            canonical({name: sha(members[name]) for name in _DEPENDENCIES})
        ),
        "migration": _migration_identity(members),
        "execution_authority": False,
        "installed_database_modified": False,
        "startup_trust": False,
    }


def stage(destination, *, source=_SOURCE, dry_run=False):
    source, destination = source.absolute(), destination.absolute()
    _directory(source)
    need(destination != source and not destination.is_relative_to(source))
    need(
        _git(source, "rev-parse", "--show-toplevel").decode().strip().replace("\\", "/")
        == source.as_posix()
    )
    need(_git(source, "status", "--porcelain=v1", "--untracked-files=all") == b"")
    with redirect_stdout(io.StringIO()):
        need(manifest.check_manifest(source, source / "MANIFEST.sha256"))
    commit = _git(source, "rev-parse", "HEAD").decode().strip()
    tree = _git(source, "rev-parse", "HEAD^{tree}").decode().strip()
    raw = _git(source, "archive", "--format=zip", "HEAD")
    members = _archive_members(raw)
    tar_raw = _git(source, "archive", "--format=tar", "HEAD")
    _verify_tar(tar_raw, members, commit, tree)
    expected = {}
    for item in _git(source, "ls-tree", "-r", "-z", "HEAD").split(b"\0"):
        if not item:
            continue
        header, path = item.split(b"\t", 1)
        mode, kind, object_sha = header.split()
        need(mode in (b"100644", b"100755") and kind == b"blob")
        expected[path.decode("utf-8")] = object_sha.decode("ascii")
    need(set(expected) == set(members))
    for name, body in members.items():
        need(
            hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
            == expected[name]
        )
    document = _identity(members, commit, tree, sha(tar_raw), sha(raw))
    if destination.exists():
        need(verify(destination, sha(canonical(document))) == document)
        return document
    _directory(destination.parent)
    if dry_run:
        return document
    destination.mkdir()
    (destination / "source").mkdir()
    for name, body in members.items():
        target = destination / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        _new(target, body)
    _new(destination / "source.zip", raw)
    _new(destination / "source.tar", tar_raw)
    _new(destination / "package.json", canonical(document))
    need(verify(destination, sha(canonical(document))) == document)
    return document


def _verify_tar(raw, members, commit, tree):
    from scripts.verify_final_hermetic import archive_tree

    need(0 < len(raw) <= _MAX_ARCHIVE)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        need(
            archive.pax_headers.get("comment") == commit
            and archive_tree(archive) == tree
        )
        files = {item.name: item for item in archive.getmembers() if item.isfile()}
        need(set(files) == set(members))
        for name, item in files.items():
            stream = archive.extractfile(item)
            need(stream is not None and stream.read() == members[name])


def verify(package, expected_sha256):
    package = package.absolute()
    _directory(package)
    need(type(expected_sha256) is str and _HEX.fullmatch(expected_sha256) is not None)
    raw = regular(package / "package.json", 3_000_000)
    need(sha(raw) == expected_sha256)
    document = json.loads(raw)
    archive_raw = regular(package / "source.zip", _MAX_ARCHIVE)
    tar_raw = regular(package / "source.tar", _MAX_ARCHIVE)
    members = _archive_members(archive_raw)
    _verify_tar(tar_raw, members, document["source_commit"], document["source_tree"])
    need(
        document
        == _identity(
            members,
            document["source_commit"],
            document["source_tree"],
            sha(tar_raw),
            sha(archive_raw),
        )
    )
    actual = set()
    for path in (package / "source").rglob("*"):
        need(not path.is_symlink() and not path.is_junction())
        if path.is_file():
            relative = path.relative_to(package / "source").as_posix()
            if "__pycache__" in path.parts and path.suffix == ".pyc":
                continue
            actual.add(relative)
            need(relative in members and regular(path, 20_000_000) == members[relative])
    need(actual == set(members))
    with redirect_stdout(io.StringIO()):
        need(
            manifest.check_manifest(
                package / "source", package / "source/MANIFEST.sha256"
            )
        )
    return document


def verify_dependencies(package):
    need(
        sys.platform == "win32"
        and sys.version_info[:2] == (3, 12)
        and sys.maxsize > 2**32
        and platform.machine().lower() in ("amd64", "x86_64")
    )
    lock = json.loads(regular(package / "source" / _DEPENDENCIES[1], 200_000))
    expected = Counter(
        (re.sub(r"[-_.]+", "-", entry["name"].lower()), entry["version"])
        for entry in lock["packages"]
    )
    actual = Counter(
        (re.sub(r"[-_.]+", "-", entry.metadata["Name"].lower()), entry.version)
        for entry in importlib.metadata.distributions()
    )
    need(expected == actual)
    return {"python": "3.12", "platform": "windows", "packages": len(lock["packages"])}


def install_runtime(package, expected_sha256, wheelhouse, *, dry_run=False):
    document = verify(package, expected_sha256)
    need(
        sys.platform == "win32"
        and sys.version_info[:2] == (3, 12)
        and sys.maxsize > 2**32
    )
    _directory(wheelhouse)
    lock = json.loads(regular(package / "source" / _DEPENDENCIES[1], 200_000))
    for entry in lock["packages"]:
        need(
            sha(regular(wheelhouse / entry["filename"], 100_000_000)) == entry["sha256"]
        )
    runtime = package / "runtime"
    interpreter = runtime / "Scripts/python.exe"
    if dry_run:
        return document
    from scripts.launch_windows_tool_0058_v1 import runtime_lock

    with runtime_lock(package):
        _install_locked_runtime(package, wheelhouse, runtime, interpreter)
    verify(package, expected_sha256)
    return document


def _install_locked_runtime(package, wheelhouse, runtime, interpreter):
    if not runtime.exists():
        import venv

        venv.EnvBuilder(with_pip=True, clear=False, upgrade=False).create(runtime)
    need(interpreter.is_file() and interpreter.resolve() == interpreter)
    result = subprocess.run(
        [
            str(interpreter),
            "-I",
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "install",
            "--no-index",
            "--no-deps",
            "--require-hashes",
            "--find-links",
            str(wheelhouse),
            "-r",
            str(package / "source" / _DEPENDENCIES[2]),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=600,
        check=False,
        env={
            name: value
            for name, value in os.environ.items()
            if name.upper()
            in {
                "SYSTEMROOT",
                "WINDIR",
                "PATH",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
            }
        },
    )
    need(result.returncode == 0)
    check = subprocess.run(
        [
            str(interpreter),
            "-I",
            "-B",
            "-c",
            "import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from scripts.windows_tool_0058_v1 import verify_dependencies;verify_dependencies(Path(sys.argv[2]))",
            str(package / "source"),
            str(package),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    need(check.returncode == 0)
    # Reinstall is the same exact locked operation; no existing runtime/source
    # is deleted and no database, configuration, service or Arm is touched.


def main(arguments=None):
    parser = PrivateParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "mode", choices=("plan", "stage", "verify", "install", "reinstall")
    )
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--identity-sha256")
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(arguments)
    try:
        if args.mode in ("plan", "stage"):
            document = stage(args.package, dry_run=args.mode == "plan")
        elif args.mode == "verify":
            document = verify(args.package, args.identity_sha256)
        else:
            need(args.wheelhouse is not None)
            document = install_runtime(
                args.package,
                args.identity_sha256,
                args.wheelhouse,
                dry_run=args.dry_run,
            )
        print(
            canonical(
                {
                    "status": "VERIFIED_STAGED_SOURCE",
                    "package_identity_sha256": sha(canonical(document)),
                    "source_commit": document["source_commit"],
                    "database_head": "NOT_OBSERVED",
                    "source_head": "0058",
                    "execution_authority": False,
                    "installed_database_modified": False,
                }
            ).decode()
        )
        return 0
    except Exception:  # noqa: BLE001 -- do not serialize private paths or configuration.
        print('{"status":"REFUSED","code":"windows_tool_0058_refused"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
