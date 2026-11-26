"""Measure one stopped Paper58 cluster without starting PostgreSQL."""

import base64
import ctypes
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

BASIS_SHA256 = "95c9d4c68ffcb75c2074616e5a42b2b0b70e92372bf62d802f140acc5067f2b0"
REQUEST_SCHEMA = "ctcc.paper58.operator.clean_stop_probe_request.v1"
PROBE_SCHEMA = "ctcc.paper58.operator.clean_stop_probe.v1"
SCRATCH_MOUNT = Path("/tmp")
PORTS = {15443, 18159}
ABSENT = ("postmaster.pid", "standby.signal", "recovery.signal")
DIR_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
FILE_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME
HEX32 = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
HEX64 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def need(condition):
    if condition is not True:
        raise ValueError("paper_clean_stop_probe_refused") from None


def canonical(document):
    return json.dumps(
        document, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_request(stream):
    raw = stream.readline(131073)
    need(0 < len(raw) <= 131072 and raw.endswith(b"\n") and stream.read(1) == b"")
    request = json.loads(raw)
    need(type(request) is dict and canonical(request) + b"\n" == raw)
    need(
        set(request)
        == {
            "schema",
            "sandbox",
            "challenge",
            "system_sha256",
            "source",
            "basis",
            "source_archive_file",
            "pg_inventory_file",
        }
    )
    need(request["schema"] == REQUEST_SCHEMA)
    need(
        type(request["sandbox"]) is str
        and HEX32.fullmatch(request["sandbox"]) is not None
    )
    for name in ("challenge", "system_sha256"):
        need(type(request[name]) is str and HEX64.fullmatch(request[name]) is not None)
    encoded = request["basis"]
    need(type(encoded) is str and 0 < len(encoded) <= 65536)
    basis = base64.b64decode(encoded, validate=True)
    need(sha(basis) == BASIS_SHA256)
    framework = {"__name__": "ctcc_measured_d847_paper_broker"}
    exec(compile(basis, "<measured-d847-paper-broker>", "exec"), framework)  # noqa: S102
    return request, framework


def directory(path, owner):
    need(path.is_absolute() and path.resolve(strict=True) == path)
    info = path.lstat()
    need(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700)
    need((info.st_uid, info.st_gid) == (owner, owner))
    return info.st_dev, info.st_ino


def native_ports_unused(net_tables):
    for table, ip_width in zip(net_tables, (8, 32), strict=True):
        table.seek(0)
        raw = bytearray()
        while chunk := table.read(16_384):
            raw.extend(chunk)
            need(len(raw) <= 4 * 1024 * 1024)
        need(raw.endswith(b"\n"))
        header, *rows = raw.splitlines()
        fields = header.split()
        need(fields[:2] == [b"sl", b"local_address"] and len(fields) >= 4)
        need(fields[2] in (b"rem_address", b"remote_address") and fields[3] == b"st")
        for row in rows:
            fields = row.split()
            need(len(fields) >= 4)
            address = fields[1].split(b":")
            need(len(address) == 2 and len(address[0]) == ip_width)
            need(re.fullmatch(rb"[0-9A-F]+", address[0]) is not None)
            need(re.fullmatch(rb"[0-9A-F]{4}", address[1]) is not None)
            need(re.fullmatch(rb"[0-9A-F]{2}", fields[3]) is not None)
            need(not (int(address[1], 16) in PORTS and fields[3] == b"0A"))


def isolate_scratch():
    # Run before archive extraction so an interrupted guest leaves no scratch files on the VHD.
    namespaces = [Path(f"/proc/self/ns/{name}") for name in ("mnt", "net", "ipc")]
    before = [os.readlink(path) for path in namespaces]
    libc = ctypes.CDLL(None, use_errno=True)
    libc.unshare.argtypes = [ctypes.c_int]
    libc.unshare.restype = ctypes.c_int
    need(libc.unshare(0x00020000 | 0x40000000 | 0x08000000) == 0)
    need(
        all(
            os.readlink(path) != old
            for path, old in zip(namespaces, before, strict=True)
        )
    )
    libc.mount.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_ulong,
        ctypes.c_char_p,
    ]
    libc.mount.restype = ctypes.c_int
    need(libc.mount(None, b"/", None, 16384 | 262144, None) == 0)
    need(SCRATCH_MOUNT.resolve(strict=True) == SCRATCH_MOUNT and SCRATCH_MOUNT.is_dir())
    need(
        libc.mount(
            b"tmpfs",
            os.fsencode(SCRATCH_MOUNT),
            b"tmpfs",
            2 | 4 | 8,
            b"mode=0700,size=8m",
        )
        == 0
    )
    directory(SCRATCH_MOUNT, 0)


@contextmanager
def held_fd(path, flags, *, parent=None):
    descriptor = os.open(path, flags, dir_fd=parent)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def verify_file(custody, path, descriptor, pinned, owner, maximum):
    with held_fd(path, FILE_READ) as independent:
        current = os.fstat(independent)
        need(stat.S_ISREG(current.st_mode) and current.st_nlink == 1)
        need(
            (current.st_uid, current.st_gid, stat.S_IMODE(current.st_mode))
            == (owner, owner, 0o600)
        )
        need(0 < current.st_size <= maximum and custody._stable(current) == pinned)
        raw = os.pread(independent, maximum + 1, 0)
        need(
            len(raw) == current.st_size and raw == os.pread(descriptor, maximum + 1, 0)
        )
        need(custody._stable(os.fstat(independent)) == pinned)
    need(
        custody._stable(os.fstat(descriptor)) == custody._stable(path.lstat()) == pinned
    )
    return raw


def read_control(
    custody,
    path,
    expected_system,
    expected_identity,
    common,
    net_tables,
    control,
    pinned,
):
    native_ports_unused(net_tables)
    need(directory(path, 70) == expected_identity)
    required_flags = os.ST_RDONLY | os.ST_NOATIME
    need(os.statvfs(path).f_flag & required_flags == required_flags)
    need(not any(os.path.lexists(path / name) for name in ABSENT))
    directory(path / "global", 70)
    before = verify_file(custody, path / "global/pg_control", control, pinned, 70, 8192)
    need(len(before) == 8192)
    result = custody._run_image(
        ["/usr/local/bin/pg_controldata", "/run/data"], common, 5
    )
    need(custody._control_state(result) == expected_system)
    need(
        verify_file(custody, path / "global/pg_control", control, pinned, 70, 8192)
        == before
    )
    need(
        directory(path, 70) == expected_identity
        and not any(os.path.lexists(path / name) for name in ABSENT)
    )
    native_ports_unused(net_tables)
    return sha(before)


def probe_read_only(
    request, base, custody, outer, stored, net_tables, marker_fd, data_fd
):
    expected = (request["system_sha256"] + "\n").encode("ascii")
    marker = outer / "system.sha256"
    marker_identity = custody._stable(os.fstat(marker_fd))
    need(
        verify_file(custody, marker, marker_fd, marker_identity, 0, len(expected))
        == expected
    )
    identity = directory(stored, 70)
    need(os.path.samestat(os.fstat(data_fd), stored.lstat()))
    inventory = Path(request["pg_inventory_file"])
    need(inventory.name == "PINNED_OCI_VIRTUAL_ROOTFS_INVENTORY.json")
    inventory_bytes = base["checked_bytes"](
        inventory, base["PG_INVENTORY"], 4 * 1024 * 1024
    )

    inventory_copy = SCRATCH_MOUNT / "inventory.json"
    custody._write_private(inventory_copy, inventory_bytes)
    custody._verify_image(base["PG_ROOT"], inventory_copy)
    rootfs, runtime = SCRATCH_MOUNT / "rootfs", SCRATCH_MOUNT / "runtime"
    rootfs.mkdir(mode=0o700)
    runtime.mkdir(mode=0o700)
    os.chown(runtime, 70, 70)
    data = runtime / "data"
    data.mkdir(mode=0o700)
    custody._mount_pg_image(base["PG_ROOT"], rootfs, runtime)
    mounted = rootfs / "run/data"
    custody._mount(stored, mounted, None, 4096)
    custody._mount(None, mounted, None, 4096 | 32 | 1 | 2 | 4 | 8 | 1024)
    parent = os.getpid()
    common = {
        "preexec_fn": lambda: custody._child_preexec(rootfs, parent),
        "env": custody._fixed_environment(),
        "close_fds": True,
        "stdin": subprocess.DEVNULL,
    }

    frames = []
    with (
        held_fd(mounted, DIR_READ) as mounted_fd,
        held_fd("global", DIR_READ, parent=mounted_fd) as global_fd,
        held_fd("pg_control", FILE_READ, parent=global_fd) as control,
    ):
        need(os.path.samestat(os.fstat(mounted_fd), os.fstat(data_fd)))
        pinned = custody._stable(os.fstat(control))
        for index in (1, 2):
            need(os.path.samestat(os.fstat(global_fd), (mounted / "global").lstat()))
            digest = read_control(
                custody,
                mounted,
                request["system_sha256"],
                identity,
                common,
                net_tables,
                control,
                pinned,
            )
            need(
                directory(stored, 70) == identity
                and os.path.samestat(os.fstat(data_fd), stored.lstat())
            )
            need(os.path.samestat(os.fstat(global_fd), (stored / "global").lstat()))
            need(
                verify_file(
                    custody, marker, marker_fd, marker_identity, 0, len(expected)
                )
                == expected
            )
            frames.append(
                {
                    "schema": PROBE_SCHEMA,
                    "sandbox": request["sandbox"],
                    "challenge": request["challenge"],
                    "probe_index": index,
                    "system_identifier_sha256": request["system_sha256"],
                    "pg_control_sha256": digest,
                    "pg_control_state": "shut down",
                    "bound_data_read_only": True,
                    "postmaster_pid_present": False,
                    "standby_signal_present": False,
                    "recovery_signal_present": False,
                    "native_listener_present": False,
                    "execution_authority": False,
                }
            )
    need(frames[0]["pg_control_sha256"] == frames[1]["pg_control_sha256"])
    return frames


def run():
    need(
        sys.platform == "linux"
        and os.geteuid() == 0
        and sys.flags.isolated == sys.flags.dont_write_bytecode == 1
    )
    os.umask(0o077)
    request, framework = read_request(sys.stdin.buffer)
    outer = Path("/root") / ("ctcc-paper58-operator-" + request["sandbox"])
    directory(outer, 0)
    lock_path = outer / "active.lock"
    stored = outer / "data"
    lock = os.open(lock_path, FILE_READ)
    try:
        info = os.fstat(lock)
        need(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600)
        need((info.st_uid, info.st_gid, info.st_nlink) == (0, 0, 1))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        need(os.path.samestat(info, lock_path.lstat()))
        with (
            held_fd(outer / "system.sha256", FILE_READ) as marker_fd,
            held_fd(stored, DIR_READ) as data_fd,
            Path("/proc/self/net/tcp").open("rb", buffering=0) as tcp4,
            Path("/proc/self/net/tcp6").open("rb", buffering=0) as tcp6,
        ):
            native_ports_unused((tcp4, tcp6))
            isolate_scratch()
            custody = framework["custody_module"](request, SCRATCH_MOUNT)
            first, second = probe_read_only(
                request,
                framework,
                custody,
                outer,
                stored,
                (tcp4, tcp6),
                marker_fd,
                data_fd,
            )
            need(os.path.samestat(info, lock_path.lstat()))
        return {
            "status": "PAPER_CLEAN_PROBES",
            "first": first,
            "second": second,
            "execution_authority": False,
        }
    finally:
        os.close(lock)


if __name__ == "__main__":
    try:
        print(canonical(run()).decode("ascii"), flush=True)
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 - The process boundary emits only a fixed status.
        print(
            '{"status":"PAPER_CLEAN_PROBE_REFUSED","execution_authority":false}',
            flush=True,
        )
        raise SystemExit(2) from None
