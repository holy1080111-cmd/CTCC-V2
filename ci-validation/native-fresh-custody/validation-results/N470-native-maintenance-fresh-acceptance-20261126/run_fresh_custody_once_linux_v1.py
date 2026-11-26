"""Reviewed-once staging for fresh canonical fixtures; never preserved PG DATA.

The external broker pins this file and the exact source archive before launch.
This staging program supplies only resource DATA to the canonical supervisor.
It cannot construct or adopt a cold-origin object, listener or live lease.
"""

import base64
import hashlib
import importlib
import io
import json
import os
import re
import stat
import sys
import tarfile
import uuid
from pathlib import Path, PurePosixPath

PG_TREE = "a4413022a5e18d5c8b4e3221be80bd7d21aa13c85093ae1adb7e01423da78556"
PG_INVENTORY = "603f68e5f1059ea01a25ecc0a79a0f7f041b4640a24f5f16fcc3b3b40524f382"
PY_TREE = "a601e5ccc5a8a43752448bf394ee481e18b8048b085300dc448b5192186439f4"
PY_INVENTORY = "9b79bfd725a69bbf9ae2d386c3219f5110ffbc2efa5e4b7435552043c87c0a6a"
PG_ROOT = Path("/root/ctcc-ci-pg17-f02121de-a07a7048-20261121/rootfs")
PY_ROOT = Path("/root/ctcc-ci-python312-2f17-1aeb49a7-20261121/rootfs")
PROVIDER = PY_ROOT.parent / "MAINTENANCE_VENDOR_LOCATOR_PRIVATE.json"
HEX64 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
HEX40 = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
PRIVATE_OPERATION = None


def need(value):
    if value is not True:
        raise ValueError("fresh_native_staging_refused") from None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_private(path, raw):
    need(type(raw) is bytes and len(raw) <= 128 * 1024 * 1024)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            need(count > 0)
            view = view[count:]
        os.fsync(fd)
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0)
        need(stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)
    finally:
        os.close(fd)


def read_measured(path, expected, limit):
    need(type(expected) is str and HEX64.fullmatch(expected) is not None)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit)
        chunks, used = [], 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, limit + 1 - used))
            if not chunk:
                break
            used += len(chunk)
            need(used <= limit)
            chunks.append(chunk)
        after = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid", "st_size", "st_mtime_ns", "st_ctime_ns")
        need(all(getattr(before, key) == getattr(after, key) for key in fields))
        raw = b"".join(chunks)
        need(len(raw) == before.st_size and sha(raw) == expected)
        return raw
    finally:
        os.close(fd)


def unpack_source(raw, output, source):
    """Extract into one new private directory; canonical code revalidates it."""
    output.mkdir(mode=0o700)
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        need(archive.pax_headers.get("comment") == source["commit"])
        for member in archive:
            name = member.name.rstrip("/") if member.isdir() else member.name
            path = PurePosixPath(name)
            need(0 < len(name) <= 1024 and name not in seen)
            need(not path.is_absolute() and path.as_posix() == name)
            need(not any(part in ("", ".", "..") for part in path.parts))
            need("\\" not in name and ":" not in name)
            need(not any(ord(char) < 32 or ord(char) == 127 for char in name))
            need(member.isdir() or (member.isfile() and 0 <= member.size <= 16 * 1024 * 1024))
            seen.add(name)
            target = output / path
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if member.isdir():
                target.mkdir(mode=0o700, exist_ok=True)
            else:
                stream = archive.extractfile(member)
                need(stream is not None)
                value = stream.read(member.size + 1)
                need(len(value) == member.size)
                write_private(target, value)
                # Git trees encode executability, not tar's configurable group
                # write bits. Stage the canonical tracked0644/0755 mode.
                os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)
    need(0 < len(seen) <= 10000)
    need(sha((output / "MANIFEST.sha256").read_bytes()) == source["manifest"])


def main():
    global PRIVATE_OPERATION
    need(sys.platform == "linux" and os.geteuid() == 0 and sys.flags.isolated == 1)
    raw_request = sys.stdin.buffer.read(131073)
    need(0 < len(raw_request) <= 131072)
    request = json.loads(raw_request)
    need(type(request) is dict and set(request) == {"schema", "source", "source_archive_file", "pg_inventory_file", "python_inventory_file", "tls_files_b64"})
    need(request["schema"] == "ctcc.fresh_native.external_resource_staging.v1")
    source = request["source"]
    need(type(source) is dict and set(source) == {"commit", "tree", "archive", "manifest"})
    for name, value in source.items():
        pattern = HEX40 if name in ("commit", "tree") else HEX64
        need(type(value) is str and pattern.fullmatch(value) is not None)
    source_raw = read_measured(request["source_archive_file"], source["archive"], 128 * 1024 * 1024)
    pg_inventory = read_measured(request["pg_inventory_file"], PG_INVENTORY, 4 * 1024 * 1024)
    python_inventory = read_measured(request["python_inventory_file"], PY_INVENTORY, 4 * 1024 * 1024)
    need(PG_ROOT.is_dir() and PY_ROOT.is_dir() and PROVIDER.is_file())
    outer = Path("/root") / ("ctcc-fresh-maintenance-" + uuid.uuid4().hex)
    outer.mkdir(mode=0o700)
    PRIVATE_OPERATION = outer
    source_directory = outer / "source"
    unpack_source(source_raw, source_directory, source)
    resources = outer / "resources"
    resources.mkdir(mode=0o700)
    write_private(resources / "source.tar", source_raw)
    write_private(resources / "pg-inventory.json", pg_inventory)
    write_private(resources / "python-inventory.json", python_inventory)
    tls = request["tls_files_b64"]
    need(type(tls) is dict and set(tls) == {"ca_certificate", "server_certificate", "server_private_key"})
    for key in tls:
        need(type(tls[key]) is str and len(tls[key]) <= 65536)
        pem = base64.b64decode(tls[key], validate=True)
        need(0 < len(pem) <= 32768)
        write_private(resources / (key + ".pem"), pem)
    operations = outer / "operations"
    operations.mkdir(mode=0o700)
    data = {
        "pg_rootfs": str(PG_ROOT), "pg_inventory_file": str(resources / "pg-inventory.json"),
        "python_rootfs": str(PY_ROOT), "python_inventory_file": str(resources / "python-inventory.json"),
        "private_output_parent": str(operations),
        "ca_certificate_file": str(resources / "ca_certificate.pem"),
        "server_certificate_file": str(resources / "server_certificate.pem"),
        "server_private_key_file": str(resources / "server_private_key.pem"),
        "runtime_provider_file": str(PROVIDER), "source_archive_file": str(resources / "source.tar"),
    }
    resource_request = {"schema": "ctcc.retained_pg.fresh_acceptance_request.v1", "resources": data, "source": source}
    private_request = outer / "fresh-request.private.json"
    write_private(private_request, json.dumps(resource_request, sort_keys=True, separators=(",", ":")).encode("ascii"))
    # This independently extracted canonical code still verifies its own source,
    # actual images/vendor bytes, namespaces, child/TLS/cold/transaction itself.
    sys.path.insert(0, str(source_directory))
    custody = importlib.import_module("scripts.retained_pg_bootstrap_custody_v1")
    try:
        custody.run_preserved_owner_supervisor_v1(private_request)
    except custody.RetainedBootstrapRefused:
        pass
    else:
        raise ValueError("fresh_request_adopted_by_preserved_entry") from None
    outcome = custody.run_fresh_canonical17_acceptance_supervisor_v1(private_request)
    check = importlib.import_module("tests.retained_custody_fresh_resource_v1")
    check.assert_fresh_acceptance_data_v1(outcome)
    encoded = (json.dumps(outcome, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    need(len(encoded) <= 65536)
    write_private(outer / "accepted-result.private.json", encoded)
    print(encoded.decode("ascii"), end="")


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:  # noqa: BLE001 -- private diagnostics and redacted refusal also on cancellation.
        # Never print resource paths, key material, connection state or SQL.
        if PRIVATE_OPERATION is not None:
            import traceback

            write_private(PRIVATE_OPERATION / "staging-failure.private.txt", traceback.format_exc().encode("utf-8"))
        status = "FRESH_NATIVE_ACCEPTANCE_REFUSED"
        approved = {
            "RetainedBootstrapReadbackRequired": "COMMITTED_READBACK_REQUIRED",
            "RetainedBootstrapCommitUnknown": "COMMIT_UNKNOWN_DO_NOT_RETRY",
        }
        if type(error).__module__ == "scripts.retained_pg_bootstrap_custody_v1":
            candidate = approved.get(type(error).__name__)
            if candidate is not None and type(error).status == candidate:
                status = candidate
        print(json.dumps({"status": status, "safe_error_type": type(error).__name__, "private_operation_id": None if PRIVATE_OPERATION is None else PRIVATE_OPERATION.name.removeprefix("ctcc-fresh-maintenance-"), "execution_authority": False}))
        raise SystemExit(2) from None
