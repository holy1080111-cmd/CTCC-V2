"""Create one private new rootfs from verified public final-image inventory.

Linux-only, root-owned tool preparation. Never read PG data/config, invoke an
image program, execute an entrypoint, install packages or start a server.
"""

import hashlib
import json
import os
import posixpath
import stat
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

PUBLIC = Path(__file__).resolve().parent
ASSETS = PUBLIC / "public-assets"
PIN = "sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9"
RECEIPT_SHA = "1aeb49a7ec1749a7d403fa5cbfebe0b44eefe550da34d7842afef63c78722074"
OUTER = Path("/root/ctcc-ci-python312-2f17-1aeb49a7-20261121")
O_DIRECTORY = os.O_DIRECTORY
O_NOFOLLOW = os.O_NOFOLLOW
stage = "initial_metadata"


def need(value):
    if value is not True:
        raise ValueError("private_ci_rootfs_materialization_refused")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def hash_file(path):
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode))
    fd = os.open(path, os.O_RDONLY | O_NOFOLLOW)
    digestor, count = hashlib.sha256(), 0
    try:
        opened = os.fstat(fd)
        need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns))
        while chunk := os.read(fd, 1024 * 1024):
            count += len(chunk)
            digestor.update(chunk)
        after = os.fstat(fd)
        need((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns))
        need(count == before.st_size)
        return digestor.hexdigest(), count
    finally:
        os.close(fd)


def read_json(path, expected=None):
    digest, size = hash_file(path)
    need(size <= 32 * 1024 * 1024 and (expected is None or digest == expected))
    descriptor = os.open(path, os.O_RDONLY | O_NOFOLLOW)
    try:
        raw = b""
        while chunk := os.read(descriptor, 1024 * 1024):
            raw += chunk
            need(len(raw) <= 32 * 1024 * 1024)
    finally:
        os.close(descriptor)
    need(sha(raw) == digest)
    return json.loads(raw)


def relative(name):
    need(type(name) is str and 0 < len(name) <= 1024 and "\\" not in name and not any(ord(character) < 32 or ord(character) == 127 for character in name))
    path = PurePosixPath(name)
    need(not path.is_absolute() and path.as_posix() == name and all(part not in ("", ".", "..") for part in path.parts))
    return path


def guest_link(path, target, *, hard):
    need(type(target) is str and 0 < len(target) <= 1024 and "\\" not in target and not any(ord(character) < 32 or ord(character) == 127 for character in target))
    candidate = target.lstrip("/") if target.startswith("/") or hard else posixpath.join(path.parent.as_posix(), target)
    normalized = posixpath.normpath(candidate)
    need(normalized not in ("", ".", "..") and not normalized.startswith("../") and not normalized.startswith("/"))
    return normalized


def parent_fd(root_fd, path):
    current = os.dup(root_fd)
    try:
        for component in path.parts[:-1]:
            nxt = os.open(component, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = nxt
        return current
    except BaseException:
        os.close(current)
        raise


def signature(row, *, expected):
    fields = {key: row[key] for key in ("path", "kind", "mode", "uid", "gid")}
    if row["kind"] in ("file", "hardlink"):
        source = expected[guest_link(relative(row["path"]), row["link_target"], hard=True)] if row["kind"] == "hardlink" else row
        fields.update(size=source["size"], sha256=source["sha256"])
    if row["kind"] in ("symlink", "hardlink"):
        fields["link_target"] = row["link_target"]
    return fields


def run():
    global stage
    need(os.geteuid() == 0 and os.name == "posix")
    need(not OUTER.exists() and not OUTER.is_symlink())
    parent = OUTER.parent.lstat()
    need(stat.S_ISDIR(parent.st_mode) and parent.st_uid == 0 and parent.st_mode & 0o022 == 0)
    receipt = read_json(PUBLIC / "PINNED_OCI_ALL_DIFFIDS_VERIFIED.json", RECEIPT_SHA)
    need(receipt["source_pinned_index"] == PIN and receipt["rootfs_directory_created"] is False)
    inventory = read_json(PUBLIC / "PINNED_OCI_VIRTUAL_ROOTFS_INVENTORY.json", receipt["virtual_rootfs_inventory_sha256"])
    need(type(inventory) is list and 0 < len(inventory) <= 100000)
    rows = {row["path"]: row for row in inventory}
    need(len(rows) == len(inventory))
    archives, members = {}, {}
    root_fd = None
    outer_fd = None
    try:
        stage = "public_layer_integrity"
        for layer in receipt["layers"]:
            need(hash_file(ASSETS / (layer["compressed_digest"].replace(":", "-") + ".blob"))[0] == layer["compressed_digest"].split(":")[1])
            path = ASSETS / layer["tar_file"]
            digest, size = hash_file(path)
            need("sha256:" + digest == layer["diff_id"] and size == layer["uncompressed_bytes"])
            archive = tarfile.open(path, mode="r:")
            archives[layer["layer"]] = archive
            for member in archive:
                name = member.name.removeprefix("./")
                if name not in ("", "."):
                    relative(name)
                    need((layer["layer"], name) not in members)
                    members[layer["layer"], name] = member
        stage = "final_inventory_admission"
        for row in inventory:
            path = relative(row["path"])
            need(row["kind"] in {"directory", "file", "symlink", "hardlink"} and not path.name.startswith(".wh."))
            need(all(type(row[key]) is int and 0 <= row[key] <= 2**32 - 1 for key in ("mode", "uid", "gid", "size", "layer")))
            need(row["mode"] <= 0o7777)
            for parent_path in path.parents:
                if parent_path.as_posix() != ".":
                    need(parent_path.as_posix() in rows and rows[parent_path.as_posix()]["kind"] == "directory")
            member = members[row["layer"], row["path"]]
            need({"directory": member.isdir, "file": member.isfile, "symlink": member.issym, "hardlink": member.islnk}[row["kind"]]())
            need((member.mode, member.uid, member.gid, member.size) == (row["mode"], row["uid"], row["gid"], row["size"]))
            need(set(member.pax_headers) <= {"atime", "ctime", "mtime", "path", "linkpath"})
            if row["kind"] in {"hardlink", "symlink"}:
                need(member.linkname == row["link_target"])
                target = guest_link(path, row["link_target"], hard=row["kind"] == "hardlink")
                if row["kind"] == "hardlink":
                    need(target in rows and rows[target]["kind"] == "file")
                    need(all(row[key] == rows[target][key] for key in ("mode", "uid", "gid")))
                else:
                    need(row["mode"] == 0o777)
        required = sum(row["size"] for row in inventory if row["kind"] == "file")
        capacity = os.statvfs(OUTER.parent)
        need(capacity.f_bavail * capacity.f_frsize > required + 64 * 1024 * 1024)
        os.umask(0o077)
        os.mkdir(OUTER, 0o700)
        outer_fd = os.open(OUTER, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        need(os.fstat(outer_fd).st_uid == 0 and stat.S_IMODE(os.fstat(outer_fd).st_mode) == 0o700)
        os.mkdir("rootfs", 0o700, dir_fd=outer_fd)
        root_fd = os.open("rootfs", os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=outer_fd)
        stage = "exclusive_directory_and_regular_files"
        for row in sorted((row for row in inventory if row["kind"] == "directory"), key=lambda row: (len(relative(row["path"]).parts), row["path"])):
            path = relative(row["path"])
            fd = parent_fd(root_fd, path)
            try:
                os.mkdir(path.name, 0o700, dir_fd=fd)
            finally:
                os.close(fd)
        for row in inventory:
            if row["kind"] != "file":
                continue
            path = relative(row["path"])
            fd = parent_fd(root_fd, path)
            output = None
            try:
                output = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW, 0o600, dir_fd=fd)
                need(stat.S_ISREG(os.fstat(output).st_mode))
                source = archives[row["layer"]].extractfile(members[row["layer"], row["path"]])
                digestor, count = hashlib.sha256(), 0
                while chunk := source.read(1024 * 1024):
                    count += len(chunk)
                    need(count <= row["size"])
                    digestor.update(chunk)
                    offset = 0
                    while offset < len(chunk):
                        written = os.write(output, chunk[offset:])
                        need(written > 0)
                        offset += written
                need(count == row["size"] and digestor.hexdigest() == row["sha256"])
                os.fchown(output, row["uid"], row["gid"])
                os.fchmod(output, row["mode"])
                os.fsync(output)
            finally:
                if output is not None:
                    os.close(output)
                os.close(fd)
        stage = "links_last"
        for row in inventory:
            if row["kind"] != "hardlink":
                continue
            path = relative(row["path"])
            target = relative(guest_link(path, row["link_target"], hard=True))
            destination_fd, source_fd = parent_fd(root_fd, path), parent_fd(root_fd, target)
            try:
                os.link(target.name, path.name, src_dir_fd=source_fd, dst_dir_fd=destination_fd, follow_symlinks=False)
            finally:
                os.close(source_fd)
                os.close(destination_fd)
        for row in inventory:
            if row["kind"] != "symlink":
                continue
            path = relative(row["path"])
            fd = parent_fd(root_fd, path)
            try:
                os.symlink(row["link_target"], path.name, dir_fd=fd)
                os.chown(path.name, row["uid"], row["gid"], dir_fd=fd, follow_symlinks=False)
            finally:
                os.close(fd)
        stage = "directory_metadata"
        for row in sorted((row for row in inventory if row["kind"] == "directory"), key=lambda row: len(relative(row["path"]).parts), reverse=True):
            path = relative(row["path"])
            parent_descriptor = parent_fd(root_fd, path)
            fd = os.open(path.name, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=parent_descriptor)
            try:
                os.fchown(fd, row["uid"], row["gid"])
                os.fchmod(fd, row["mode"])
            finally:
                os.close(fd)
                os.close(parent_descriptor)
        os.fchown(root_fd, 0, 0)
        os.fchmod(root_fd, 0o755)
        stage = "independent_full_tree_readback"
        actual = []
        expected_children = {}
        for name in rows:
            path = relative(name)
            expected_children.setdefault(path.parent.as_posix(), set()).add(path.name)
        for directory in [".", *(row["path"] for row in inventory if row["kind"] == "directory")]:
            if directory == ".":
                fd = os.dup(root_fd)
            else:
                path = relative(directory)
                parent_descriptor = parent_fd(root_fd, path)
                try:
                    fd = os.open(path.name, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=parent_descriptor)
                finally:
                    os.close(parent_descriptor)
            try:
                need(set(os.listdir(fd)) == expected_children.get(directory, set()))
            finally:
                os.close(fd)
        for row in inventory:
            path = relative(row["path"])
            fd = parent_fd(root_fd, path)
            try:
                observed = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
                need((stat.S_IMODE(observed.st_mode), observed.st_uid, observed.st_gid) == (row["mode"], row["uid"], row["gid"]))
                output = signature(row, expected=rows)
                if row["kind"] == "directory":
                    need(stat.S_ISDIR(observed.st_mode))
                elif row["kind"] == "symlink":
                    need(stat.S_ISLNK(observed.st_mode) and os.readlink(path.name, dir_fd=fd) == row["link_target"])
                else:
                    need(stat.S_ISREG(observed.st_mode) and observed.st_size == output["size"])
                    reader = os.open(path.name, os.O_RDONLY | O_NOFOLLOW, dir_fd=fd)
                    digestor, prefix = hashlib.sha256(), b""
                    try:
                        while chunk := os.read(reader, 1024 * 1024):
                            digestor.update(chunk)
                            if len(prefix) < 64:
                                prefix += chunk[:64 - len(prefix)]
                    finally:
                        os.close(reader)
                    need(digestor.hexdigest() == output["sha256"])
                    if row["path"] in receipt["selected_image_elf_files"]:
                        need(prefix.startswith(b"\x7fELF") and prefix[4:6] == b"\x02\x01" and int.from_bytes(prefix[18:20], "little") == 62)
                        need(output["sha256"] == receipt["selected_image_elf_files"][row["path"]]["sha256"])
                actual.append(output)
            finally:
                os.close(fd)
        expected = [signature(row, expected=rows) for row in inventory]
        need(actual == expected)
        tree_sha = sha(canonical(actual))
        stage = "child_namespace_capability"
        names = ("net", "mnt", "ipc")
        before = {name: os.readlink("/proc/self/ns/" + name) for name in names}
        code = "import json,os;print(json.dumps({n:os.readlink('/proc/self/ns/'+n) for n in ('net','mnt','ipc')}))"
        child = subprocess.run(["/usr/bin/unshare", "-n", "-m", "-i", "/usr/bin/python3", "-I", "-S", "-c", code], env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}, stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        after = {name: os.readlink("/proc/self/ns/" + name) for name in names}
        need(child.returncode == 0 and before == after and child.stderr == b"")
        child_names = json.loads(child.stdout)
        need(set(child_names) == set(names) and all(child_names[name] != before[name] for name in names))
        locator = {"schema": "ctcc.private_ci_python312.rootfs_locator.v1", "outer": str(OUTER), "rootfs": str(OUTER / "rootfs"), "tree_sha256": tree_sha, "source_index": PIN, "source_content_receipt_sha256": RECEIPT_SHA, "server_started": False}
        locator_raw = canonical(locator)
        locator_fd = os.open("ROOTFS_LOCATOR.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW, 0o600, dir_fd=outer_fd)
        try:
            need(os.write(locator_fd, locator_raw) == len(locator_raw))
            os.fsync(locator_fd)
        finally:
            os.close(locator_fd)
        result = {"schema": "ctcc.private_ci_python312.rootfs_materialization.v1", "status": "PASS", "source_index": PIN, "source_content_receipt_sha256": RECEIPT_SHA, "source_virtual_inventory_sha256": receipt["virtual_rootfs_inventory_sha256"], "physical_tree_sha256": tree_sha, "verified_entries": len(actual), "verified_regular_files": sum(row["kind"] == "file" for row in inventory), "verified_links": sum(row["kind"] in {"hardlink", "symlink"} for row in inventory), "private_locator_sha256": sha(locator_raw), "outer_mode": "0700", "rootfs_mode": "0755", "root_owned": True, "all_bytes_modes_uid_gid_independently_verified": True, "child_namespaces": child_names, "parent_namespaces_before": before, "parent_namespaces_after": after, "private_pg_data_or_config_reads": 0, "existing_path_overlays_or_deletions": 0, "image_python_programs_or_entrypoint_executed": False, "server_or_daemon_started": False, "xattrs_installed": False, "mtimes_are_not_an_admission_input": True}
        raw = canonical(result)
        with (PUBLIC / "PRIVATE_CI_ROOTFS_MATERIALIZED_VERIFIED.json").open("xb") as output:
            output.write(raw)
        print(json.dumps({"status": "PASS", "receipt_sha256": sha(raw), "private_locator_sha256": sha(locator_raw), "tree_sha256": tree_sha}))
    finally:
        for archive in archives.values():
            archive.close()
        if root_fd is not None:
            os.close(root_fd)
        if outer_fd is not None:
            os.close(outer_fd)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        value = {"schema": "ctcc.private_ci_python312.rootfs_materialization_failure.v1", "status": "FAIL", "stage": stage, "safe_error_type": type(error).__name__, "partial_leaf_preserved": True, "no_server_or_entrypoint_execution": True}
        with (PUBLIC / "PRIVATE_CI_ROOTFS_MATERIALIZATION_SAFE_FAILURE.json").open("xb") as output:
            output.write(canonical(value))
        print(json.dumps(value))
        raise SystemExit(1) from None
