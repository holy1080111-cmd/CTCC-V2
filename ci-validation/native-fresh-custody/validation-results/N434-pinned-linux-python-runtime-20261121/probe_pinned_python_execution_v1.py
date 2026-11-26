"""Linux-only, one-shot verified-image execution probe; never opens PG data.

This runs only three fixed --version commands, as an unprivileged user in
private NET/MOUNT/IPC namespaces and a read-only image chroot. It cannot start
a server, entrypoint, service, initialize a cluster, or read private PG inputs.
All newly created files/directories remain; no cleanup deletes are attempted.
"""

import ctypes
import hashlib
import importlib.util
import json
import os
import resource
import signal
import stat
import subprocess
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCATOR = Path("/root/ctcc-ci-python312-2f17-1aeb49a7-20261121/ROOTFS_LOCATOR.json")
LOCATOR_SHA = "35aee90d84c0b5181bcd43b1773a3fa0dd9d8b0922ce4bcb28dfffc6e731ee1a"
INVENTORY_SHA = "9b79bfd725a69bbf9ae2d386c3219f5110ffbc2efa5e4b7435552043c87c0a6a"
MATERIALIZER_SHA = "da5f4d976f4a961bce6b7adc8c108b0e5f66f8ec48487770494fd50d018d77ba"
EXPECTED_TREE = "a601e5ccc5a8a43752448bf394ee481e18b8048b085300dc448b5192186439f4"
PUBLIC_OUTPUT = HERE / "PINNED_PYTHON_EXECUTION_PROBE.json"
stage = "initial_metadata"
programs_executed = 0


def need(value):
    if value is not True:
        raise ValueError("private_ci_sandbox_probe_refused") from None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_regular(path, expected, limit):
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 <= before.st_size <= limit)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
             (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns))
        raw = bytearray()
        while chunk := os.read(descriptor, 1024 * 1024):
            raw.extend(chunk)
            need(len(raw) <= limit)
        after = os.fstat(descriptor)
        need((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) ==
             (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns))
    finally:
        os.close(descriptor)
    need(len(raw) == before.st_size and digest(raw) == expected)
    return bytes(raw)


def verify_complete_tree(rootfs, inventory, helper):
    root_fd = os.open(rootfs, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        root_info = os.fstat(root_fd)
        need((root_info.st_uid, root_info.st_gid, stat.S_IMODE(root_info.st_mode)) == (0, 0, 0o755))
        rows = {row["path"]: row for row in inventory}
        need(len(rows) == len(inventory) == 5579)
        children = {}
        for row in inventory:
            relative = helper.relative(row["path"])
            children.setdefault(relative.parent.as_posix(), set()).add(relative.name)
        for directory in [".", *(row["path"] for row in inventory if row["kind"] == "directory")]:
            if directory == ".":
                descriptor = os.dup(root_fd)
            else:
                relative = helper.relative(directory)
                parent = helper.parent_fd(root_fd, relative)
                try:
                    descriptor = os.open(relative.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                finally:
                    os.close(parent)
            try:
                need(set(os.listdir(descriptor)) == children.get(directory, set()))
            finally:
                os.close(descriptor)
        actual = []
        for row in inventory:
            relative = helper.relative(row["path"])
            parent = helper.parent_fd(root_fd, relative)
            try:
                before = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
                need((before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)) ==
                     (row["uid"], row["gid"], row["mode"]))
                result = helper.signature(row, expected=rows)
                if row["kind"] == "directory":
                    need(stat.S_ISDIR(before.st_mode))
                elif row["kind"] == "symlink":
                    need(stat.S_ISLNK(before.st_mode) and os.readlink(relative.name, dir_fd=parent) == row["link_target"])
                else:
                    need(row["kind"] in ("file", "hardlink") and stat.S_ISREG(before.st_mode) and before.st_size == result["size"])
                    descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        opened = os.fstat(descriptor)
                        need((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) ==
                             (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns))
                        hasher = hashlib.sha256()
                        while chunk := os.read(descriptor, 1024 * 1024):
                            hasher.update(chunk)
                        after = os.fstat(descriptor)
                        need((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) ==
                             (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns))
                        need(hasher.hexdigest() == result["sha256"])
                    finally:
                        os.close(descriptor)
                actual.append(result)
            finally:
                os.close(parent)
        need(digest(canonical(actual)) == EXPECTED_TREE)
        return rows
    finally:
        os.close(root_fd)


def libc_function(name, arguments):
    library = ctypes.CDLL(None, use_errno=True)
    fn = getattr(library, name)
    fn.argtypes = arguments
    fn.restype = ctypes.c_int
    return fn


def mount(source, target, kind, flags, data=None):
    call = libc_function("mount", [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                                   ctypes.c_ulong, ctypes.c_char_p])
    encoded = lambda value: None if value is None else os.fsencode(value)
    if call(encoded(source), encoded(target), encoded(kind), flags, encoded(data)) != 0:
        raise OSError(ctypes.get_errno(), "sandbox_mount_refused") from None


def ns_ids(pid):
    return {name: os.readlink(f"/proc/{pid}/ns/{name}") for name in ("net", "mnt", "ipc")}


def child_preexec(rootfs, controller_pid):
    # Never expose a host cwd, inherited groups, capabilities or credentials.
    os.chroot(rootfs)
    os.chdir("/")
    os.setgroups([])
    os.setgid(70)
    os.setuid(70)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    class CapabilityHeader(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]

    class CapabilityData(ctypes.Structure):
        _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32),
                    ("inheritable", ctypes.c_uint32)]

    header = CapabilityHeader(0x20080522, 0)
    data = (CapabilityData * 2)()
    capset = libc_function("capset", [ctypes.POINTER(CapabilityHeader), ctypes.POINTER(CapabilityData)])
    capget = libc_function("capget", [ctypes.POINTER(CapabilityHeader), ctypes.POINTER(CapabilityData)])
    need(capset(ctypes.byref(header), data) == 0)
    need(capget(ctypes.byref(header), data) == 0)
    need(all(item.effective == item.permitted == item.inheritable == 0 for item in data))
    prctl = libc_function("prctl", [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                                   ctypes.c_ulong, ctypes.c_ulong])
    need(prctl(38, 1, 0, 0, 0) == 0)  # PR_SET_NO_NEW_PRIVS
    need(prctl(4, 0, 0, 0, 0) == 0)   # PR_SET_DUMPABLE
    need(prctl(1, signal.SIGKILL, 0, 0, 0) == 0)  # PR_SET_PDEATHSIG
    need(os.getppid() == controller_pid)
    need(os.getuid() == 70 and os.getgid() == 70 and os.getgroups() == [])


def run():
    global stage, programs_executed
    need(os.geteuid() == 0 and not PUBLIC_OUTPUT.exists())
    need(set(os.environ) <= {"PATH", "LC_ALL", "LANG"})
    os.umask(0o077)
    locator_raw = read_regular(LOCATOR, LOCATOR_SHA, 4096)
    locator = json.loads(locator_raw)
    need(locator["schema"] == "ctcc.private_ci_python312.rootfs_locator.v1" and locator["server_started"] is False)
    outer, rootfs = Path(locator["outer"]), Path(locator["rootfs"])
    need(LOCATOR.parent == outer and rootfs == outer / "rootfs" and rootfs.resolve(strict=True) == rootfs)
    info = outer.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
    need(locator["tree_sha256"] == EXPECTED_TREE)
    producer = HERE / "materialize_private_pinned_python_rootfs_v1.py"
    producer_raw = read_regular(producer, MATERIALIZER_SHA, 32000)
    spec = importlib.util.spec_from_file_location("reviewed_materializer_helpers", producer)
    helper = importlib.util.module_from_spec(spec)
    # Compile the already measured bytes; do not reopen a different module body.
    exec(compile(producer_raw, str(producer), "exec"), helper.__dict__)
    inventory = json.loads(read_regular(HERE / "PINNED_OCI_VIRTUAL_ROOTFS_INVENTORY.json", INVENTORY_SHA, 2 * 1024 * 1024))
    stage = "repeat_complete_rootfs_readback"
    rows = verify_complete_tree(rootfs, inventory, helper)
    for target in ("dev", "run", "proc"):
        need(rows[target]["kind"] == "directory")
    parent_pid = os.getppid()
    parent_before = ns_ids(parent_pid)
    own_before = ns_ids("self")
    unshare = libc_function("unshare", [ctypes.c_int])
    stage = "child_only_namespaces"
    need(unshare(0x40000000 | 0x00020000 | 0x08000000) == 0)
    child_namespaces = ns_ids("self")
    need(all(child_namespaces[key] != own_before[key] for key in own_before))
    mount(None, "/", None, 16384 | 262144)  # MS_REC | MS_PRIVATE
    sandbox = Path("/root") / ("ctcc-pg-sandbox-probe-" + uuid.uuid4().hex)
    need(not sandbox.exists() and sandbox.parent.resolve(strict=True) == Path("/root"))
    sandbox.mkdir(mode=0o700)
    mounted_root = sandbox / "rootfs"
    mounted_root.mkdir(mode=0o700)
    stage = "private_readonly_chroot"
    mount(rootfs, mounted_root, None, 4096)  # MS_BIND
    mount(None, mounted_root, None, 4096 | 32 | 1 | 2 | 4)  # remount bind RO,NOSUID,NODEV
    # Only newly mounted private /dev is writable, with three known devices.
    mount("tmpfs", mounted_root / "dev", "tmpfs", 2 | 8, "mode=0755,size=1m")
    for name, major, minor in (("null", 1, 3), ("random", 1, 8), ("urandom", 1, 9)):
        os.mknod(mounted_root / "dev" / name, stat.S_IFCHR | 0o666, os.makedev(major, minor))
        os.chmod(mounted_root / "dev" / name, 0o666)
    verified_versions = {}
    stage = "fixed_genuine_python_metadata_only"
    controller_pid = os.getpid()
    probe_code = 'import json,os,ssl,sys,sysconfig\nassert sys.version_info[:3] == (3,12,14)\nassert sys.executable == "/usr/local/bin/python3.12"\nassert sys.prefix == sys.base_prefix == "/usr/local"\nassert sys.flags.isolated == sys.flags.no_user_site == sys.flags.ignore_environment == 1\nassert sys.dont_write_bytecode and sys.flags.no_site == 1\nassert os.getuid() == os.getgid() == 70 and os.getgroups() == []\nassert set(os.environ) <= {"PATH","LC_ALL","LANG","HOME"}\nassert sysconfig.get_config_var("SOABI") == "cpython-312-x86_64-linux-gnu"\nprint(json.dumps({"python_version":sys.version,"version_tuple":list(sys.version_info[:3]),\n"executable":sys.executable,"prefix":sys.prefix,"base_prefix":sys.base_prefix,\n"openssl_version":ssl.OPENSSL_VERSION,"soabi":sysconfig.get_config_var("SOABI"),\n"uid":os.getuid(),"gid":os.getgid(),"groups":os.getgroups(),"isolated":sys.flags.isolated,\n"no_site":sys.flags.no_site,"dont_write_bytecode":sys.dont_write_bytecode,\n"site_packages_or_ctcc_imported":False,"runtime_acceptance":False},sort_keys=True))\n'
    result = subprocess.run(["/usr/local/bin/python3.12", "-I", "-S", "-B", "-c", probe_code],
        preexec_fn=lambda: child_preexec(mounted_root, controller_pid),
        env={"PATH":"/usr/local/bin:/usr/bin:/bin","LC_ALL":"C","LANG":"C","HOME":"/nonexistent"},
        close_fds=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=8)
    programs_executed += 1
    need(result.returncode == 0 and result.stderr == b"" and 0 < len(result.stdout) <= 8192)
    metadata = json.loads(result.stdout)
    need(metadata["version_tuple"] == [3,12,14] and metadata["uid"] == metadata["gid"] == 70
         and metadata["runtime_acceptance"] is False and metadata["site_packages_or_ctcc_imported"] is False)
    verified_versions["python3.12"] = dict(metadata,
        source_program_sha256=rows["usr/local/bin/python3.12"]["sha256"])
    stage = "independent_complete_image_readback_after_execution"
    verify_complete_tree(rootfs, inventory, helper)
    need(ns_ids(parent_pid) == parent_before)
    report = {"schema": "ctcc.private_ci_python312.version_sandbox_probe.v1", "status": "PASS",
              "rootfs_tree_sha256": EXPECTED_TREE, "rootfs_locator_sha256": LOCATOR_SHA,
              "probe_source_sha256": digest(Path(__file__).read_bytes()),
              "producer_source_sha256": MATERIALIZER_SHA, "inventory_sha256": INVENTORY_SHA,
              "complete_rootfs_reverified": True, "programs": verified_versions,
              "child_namespaces": child_namespaces, "parent_namespaces_unchanged": True,
              "read_only_image_mount": True, "chroot_uid": 70, "chroot_gid": 70,
              "no_new_privileges": True, "inherited_groups": 0, "host_environment_cleared": True,
              "server_starts": 0, "sql_connections": 0, "entrypoint_runs": 0,
              "pg_private_data_or_configuration_reads": 0, "original_service_starts": 0,
              "existing_paths_overlaid_in_parent_namespace": 0, "deletions": 0}
    raw = canonical(report)
    with PUBLIC_OUTPUT.open("xb") as output:
        need(output.write(raw) == len(raw))
    print(json.dumps({"status": "PASS_GENUINE_PYTHON_METADATA_ONLY", "receipt_sha256": digest(raw)}))


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        report = {"schema": "ctcc.private_ci_python312.version_sandbox_failure.v1", "status": "FAIL",
                  "stage": stage, "safe_error_type": type(error).__name__,
                  "safe_errno": error.errno if type(error) is OSError else None,
                  "fixed_version_programs_attempted": programs_executed,
                  "server_starts": 0, "sql_connections": 0,
                  "private_pg_input_reads": 0, "partial_leaf_preserved": True}
        target = HERE / "PINNED_PYTHON_EXECUTION_PROBE_SAFE_FAILURE.json"
        with target.open("xb") as output:
            output.write(canonical(report))
        print(json.dumps(report))
        raise SystemExit(1) from None
