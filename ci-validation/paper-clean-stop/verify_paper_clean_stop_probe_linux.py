"""Run the Paper58 stopped-control observer on a disposable Linux PG17 fixture.

Run as PID 1 in new mount, network, IPC, and PID namespaces with private
propagation and a fresh proc mount. Pass an independently extracted d847
custody leaf and the verified public OCI PostgreSQL rootfs and inventory.
This entry point has no option to use an existing PostgreSQL data directory.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType

SOURCE_COMMIT = "d847b9976048c5eca8d2a0e803b0c29c5fcbef2e"
CUSTODY_SHA256 = "51453d2edcaf7734d9569b2d0504960b239f8f0ceddf3861ce24a8a182e15af1"
INVENTORY_SHA256 = "603f68e5f1059ea01a25ecc0a79a0f7f041b4640a24f5f16fcc3b3b40524f382"
FIXTURE_MOUNT = Path("/var/tmp")
CONTROL = ["/usr/local/bin/pg_controldata", "/run/data"]
FILE_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
STAGE = "admission"


def need(condition):
    if condition is not True:
        raise ValueError("paper58_native_probe_acceptance_refused")


def measured_bytes(path, expected, maximum):
    need(path.is_absolute() and path.resolve(strict=True) == path)
    need(re.fullmatch(r"[0-9a-f]{64}", expected) is not None)
    with os.fdopen(os.open(path, FILE_READ), "rb") as source:
        before = os.fstat(source.fileno())
        need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= maximum)
        raw = source.read(maximum + 1)
        after = os.fstat(source.fileno())
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    need(all(getattr(before, field) == getattr(after, field) for field in fields))
    need(len(raw) == before.st_size and hashlib.sha256(raw).hexdigest() == expected)
    return raw


def load_measured(name, path, digest, maximum, *, exact_size=None):
    raw = measured_bytes(path, digest, maximum)
    need(exact_size is None or len(raw) == exact_size)
    module = ModuleType(name)
    module.__file__ = str(path)
    need(name not in sys.modules)
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def metadata(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_atime_ns,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def fixture_tree_sha256(data_fd):
    records = []
    for path, directories, files, descriptor in os.fwalk(".", dir_fd=data_fd):
        directories.sort()
        directory = os.fstat(descriptor)
        need(
            (directory.st_uid, directory.st_gid, stat.S_IMODE(directory.st_mode))
            == (70, 70, 0o700)
        )
        records.append((path, metadata(directory)))
        for name in directories:
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            need(stat.S_ISDIR(info.st_mode))
        for name in sorted(files):
            before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
            need(
                (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode))
                == (70, 70, 0o600)
            )
            with os.fdopen(
                os.open(name, FILE_READ | os.O_NOATIME, dir_fd=descriptor), "rb"
            ) as file:
                need(metadata(os.fstat(file.fileno())) == metadata(before))
                digest = hashlib.file_digest(file, "sha256").hexdigest()
                need(metadata(os.fstat(file.fileno())) == metadata(before))
            records.append((f"{path}/{name}", metadata(before), digest))
    need(len(records) > 100)
    raw = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def fresh_fixture(custody, pg_root):
    need(FIXTURE_MOUNT.resolve(strict=True) == FIXTURE_MOUNT and FIXTURE_MOUNT.is_dir())
    custody._mount(
        "tmpfs", FIXTURE_MOUNT, "tmpfs", 2 | 4 | 8 | 1024, "mode=0700,size=160m"
    )
    need(os.statvfs(FIXTURE_MOUNT).f_flag & os.ST_NOATIME != 0)
    need(not any(FIXTURE_MOUNT.iterdir()))
    outer = FIXTURE_MOUNT / "fixture"
    rootfs = FIXTURE_MOUNT / "init-rootfs"
    runtime = FIXTURE_MOUNT / "init-runtime"
    for path in (outer, rootfs, runtime):
        path.mkdir(mode=0o700)
    os.chown(runtime, 70, 70)
    (runtime / "data").mkdir(mode=0o700)
    data = outer / "data"
    data.mkdir(mode=0o700)
    os.chown(data, 70, 70)
    custody._mount_pg_image(pg_root, rootfs, runtime)
    custody._mount(data, rootfs / "run/data", None, 4096)
    need(os.path.samestat(data.lstat(), (rootfs / "run/data").lstat()))
    parent = os.getpid()
    common = {
        "preexec_fn": lambda: custody._child_preexec(rootfs, parent),
        "env": custody._fixed_environment(),
        "close_fds": True,
        "stdin": subprocess.DEVNULL,
    }
    initialized = custody._run_image(
        [
            "/usr/local/bin/initdb",
            "-D",
            "/run/data",
            "--auth-local=peer",
            "--auth-host=reject",
            "--username=postgres",
            "--locale=en_US.utf8",
            "--encoding=UTF8",
        ],
        common,
        45,
    )
    need(initialized.returncode == 0)
    need(custody._private_bytes(data / "PG_VERSION", 3, owner=70) == b"17\n")
    system = custody._control_state(custody._run_image(CONTROL, common, 5))
    custody._write_private(outer / "system.sha256", (system + "\n").encode("ascii"))
    custody._write_private(outer / "active.lock", b"")
    return outer, data, system


def main(args):
    global STAGE
    need(sys.platform == "linux" and os.geteuid() == 0 and os.getpid() == 1)
    need(sys.flags.isolated == sys.flags.no_site == sys.flags.dont_write_bytecode == 1)
    need(
        os.uname().machine == "x86_64"
        and "microsoft" not in os.uname().release.casefold()
    )
    need(
        os.environ
        == {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
    )
    need(socket.if_nameindex() == [(1, "lo")])
    need(Path("/proc/self/task/1/children").read_bytes().strip() == b"")
    os.umask(0o077)
    probe_path = Path(__file__).resolve().with_name("paper_clean_stop_probe.py")
    for path in (probe_path, args.custody, args.inventory, args.pg_root):
        need(path.is_absolute() and path.resolve(strict=True) == path)
        need(
            not any(path.is_relative_to(root) for root in (Path("/tmp"), FIXTURE_MOUNT))
        )

    STAGE = "public_sources"
    custody = load_measured(
        "_d847_public_custody",
        args.custody,
        CUSTODY_SHA256,
        137370,
        exact_size=137370,
    )
    probe = load_measured(
        "_reviewed_paper58_clean_probe", probe_path, args.probe_sha256, 65536
    )
    need(custody.PG_INVENTORY_SHA256 == INVENTORY_SHA256)
    need(args.inventory.name == "PINNED_OCI_VIRTUAL_ROOTFS_INVENTORY.json")
    measured_bytes(args.inventory, INVENTORY_SHA256, 2 * 1024 * 1024)
    custody._verify_image(args.pg_root, args.inventory)
    base = {
        "PG_ROOT": args.pg_root,
        "PG_INVENTORY": INVENTORY_SHA256,
        "checked_bytes": measured_bytes,
    }

    STAGE = "fresh_fixture"
    probe.isolate_scratch()
    outer, data, system = fresh_fixture(custody, args.pg_root)
    nonce, challenge = secrets.token_hex(16), secrets.token_hex(32)
    request = {
        "sandbox": nonce,
        "challenge": challenge,
        "system_sha256": system,
        "pg_inventory_file": str(args.inventory),
    }
    commands = []

    def record_native_command(event, arguments):
        if event == "subprocess.Popen":
            executable, argv, _, _ = arguments
            need(executable == CONTROL[0] and argv == CONTROL)
            commands.append(tuple(argv))
        elif event in {"os.exec", "os.fork", "os.posix_spawn", "os.system"}:
            need(False)

    STAGE = "native_probe"
    with (
        probe.held_fd(outer / "active.lock", FILE_READ) as lock,
        probe.held_fd(outer / "system.sha256", FILE_READ) as marker,
        probe.held_fd(data, probe.DIR_READ) as data_fd,
        Path("/proc/self/net/tcp").open("rb", buffering=0) as tcp4,
        Path("/proc/self/net/tcp6").open("rb", buffering=0) as tcp6,
    ):
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_id, marker_id = (
            metadata(os.fstat(descriptor)) for descriptor in (lock, marker)
        )
        before = fixture_tree_sha256(data_fd)
        before_control = hashlib.sha256(
            custody._private_bytes(data / "global/pg_control", 8192, owner=70)
        ).hexdigest()
        probe.isolate_scratch()
        need(Path("/proc/self/task/1/children").read_bytes().strip() == b"")
        sys.addaudithook(record_native_command)
        frames = probe.probe_read_only(
            request, base, custody, outer, data, (tcp4, tcp6), marker, data_fd
        )
        need(commands == [tuple(CONTROL), tuple(CONTROL)])
        need(Path("/proc/self/task/1/children").read_bytes().strip() == b"")
        STAGE = "fixture_readback"
        after = fixture_tree_sha256(data_fd)
        need(after == before)
        need(
            metadata((outer / "active.lock").lstat())
            == metadata(os.fstat(lock))
            == lock_id
        )
        need(
            metadata((outer / "system.sha256").lstat())
            == metadata(os.fstat(marker))
            == marker_id
        )
        need(not any(os.path.lexists(data / name) for name in probe.ABSENT))

    expected = {
        "schema": "ctcc.paper58.operator.clean_stop_probe.v1",
        "sandbox": nonce,
        "challenge": challenge,
        "system_identifier_sha256": system,
        "pg_control_sha256": before_control,
        "pg_control_state": "shut down",
        "bound_data_read_only": True,
        "execution_authority": False,
        "postmaster_pid_present": False,
        "standby_signal_present": False,
        "recovery_signal_present": False,
        "native_listener_present": False,
    }
    need(frames == [dict(expected, probe_index=1), dict(expected, probe_index=2)])
    return {
        "schema": "ctcc.validation.paper58_clean_stop_native.v1",
        "status": "PASS_DISPOSABLE_PG17_CLEAN_STOP_PROBE",
        "source_commit": SOURCE_COMMIT,
        "custody_sha256": CUSTODY_SHA256,
        "probe_sha256": args.probe_sha256,
        "pg_image": custody.PG_IMAGE,
        "pg_inventory_sha256": INVENTORY_SHA256,
        "pg_tree_sha256": custody.PG_TREE_SHA256,
        "fixture_origin": "disposable_initdb",
        "pg_control_sha256": before_control,
        "before_tree_sha256": before,
        "after_tree_sha256": after,
        "independent_native_readbacks": len(commands),
        "database_sessions": 0,
        "postgres_started_by_probe": False,
        "windows_end_to_end": False,
        "execution_authority": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--custody", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--pg-root", required=True, type=Path)
    parser.add_argument("--probe-sha256", required=True)
    try:
        result = main(parser.parse_args())
        print(json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 - Only the bounded acceptance phase is published.
        print(
            json.dumps(
                {
                    "status": "DISPOSABLE_PG17_CLEAN_STOP_PROBE_REFUSED",
                    "stage": STAGE,
                    "execution_authority": False,
                },
                separators=(",", ":"),
            ),
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2) from None
