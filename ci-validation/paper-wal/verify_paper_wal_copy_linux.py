"""Exercise stock PG17 WAL recovery on a new copy of a disposable cluster."""

import argparse
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

SOURCE_COMMIT = "d847b9976048c5eca8d2a0e803b0c29c5fcbef2e"
CUSTODY_SHA256 = "51453d2edcaf7734d9569b2d0504960b239f8f0ceddf3861ce24a8a182e15af1"
INVENTORY_SHA256 = "603f68e5f1059ea01a25ecc0a79a0f7f041b4640a24f5f16fcc3b3b40524f382"
IMAGE_ROOT = Path("/root/ctcc-ci-pg17-f02121de-a07a7048-20261121/rootfs")
FIXTURE = Path("/var/tmp")
CONTROL = ["/usr/local/bin/pg_controldata", "/run/data"]
PORT = "15443"
SCHEMA = "ctcc.validation.paper_wal_copy_native.v1"
PHASE = "admission"


def need(value):
    if value is not True:
        raise ValueError("paper_wal_copy_acceptance_refused") from None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def measured(path, expected, limit):
    need(path.is_absolute() and path.resolve(strict=True) == path)
    need(re.fullmatch(r"[0-9a-f]{64}", expected) is not None)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit)
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    need(all(getattr(before, key) == getattr(after, key) for key in fields))
    need(len(raw) == before.st_size and sha(raw) == expected)
    return raw


def module(name, path, expected, limit, exact_size=None):
    raw = measured(path, expected, limit)
    need(exact_size is None or len(raw) == exact_size)
    result = ModuleType(name)
    result.__file__ = str(path)
    sys.modules[name] = result
    exec(compile(raw, str(path), "exec"), vars(result))  # noqa: S102 - Bytes measured before loading.
    return result


@contextmanager
def directory_fd(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    try:
        need(os.path.samestat(os.fstat(fd), path.lstat()))
        yield fd
    finally:
        os.close(fd)


def stage_runtime(custody, pg_root, name, data):
    runtime, rootfs = FIXTURE / (name + "-runtime"), FIXTURE / (name + "-rootfs")
    runtime.mkdir(mode=0o700)
    rootfs.mkdir(mode=0o700)
    os.chown(runtime, 70, 70)
    (runtime / "data").mkdir(mode=0o700)
    (runtime / "socket").mkdir(mode=0o700)
    os.chown(runtime / "socket", 70, 70)
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
    for filename, raw in (
        (
            "maintenance.conf",
            b"# Exact runtime options are passed on the native command line.\n",
        ),
        (
            "hba.conf",
            b"local postgres postgres peer\nlocal all all reject\nhost all all 0.0.0.0/0 reject\nhost all all ::0/0 reject\n",
        ),
        ("ident.conf", b""),
    ):
        custody._write_private(runtime / filename, raw, uid=70, gid=70)
    return runtime, rootfs, common


def sql(custody, common, statement):
    result = custody._run_image(
        [
            "/usr/local/bin/psql",
            "-X",
            "-qAt",
            "--set=ON_ERROR_STOP=on",
            "-h",
            "/run/socket",
            "-p",
            PORT,
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-c",
            statement,
        ],
        common,
        15,
    )
    need(
        result.returncode == 0 and result.stderr == b"" and len(result.stdout) <= 65536
    )
    return result.stdout.decode("ascii")


def stock_settings(custody, *, read_only):
    return {
        "data_directory": "'/run/data'",
        "hba_file": "'/run/hba.conf'",
        "ident_file": "'/run/ident.conf'",
        "listen_addresses": "''",
        "port": PORT,
        "unix_socket_directories": "'/run/socket'",
        "unix_socket_permissions": "0700",
        "ssl": "off",
        "shared_buffers": "'16MB'",
        "default_transaction_read_only": "on" if read_only else "off",
        "synchronous_commit": "on",
        "fsync": "on",
        "full_page_writes": "on",
        "checkpoint_timeout": "'1h'",
        "archive_mode": "off",
        "archive_command": "''",
        "restore_command": "''",
        "primary_conninfo": "''",
        "autovacuum": "off",
        "shared_preload_libraries": "''",
        "session_preload_libraries": "''",
        "local_preload_libraries": "''",
        "log_min_messages": "log",
        "dynamic_shared_memory_type": "mmap",
        "huge_pages": "off",
        **dict(custody._MAINTENANCE_LOCK_GUCS),
    }


@contextmanager
def held_server(custody, runtime, rootfs, *, read_only):
    server, common = custody._new_server_v1(
        rootfs, runtime, stock_settings(custody, read_only=read_only)
    )
    try:
        deadline = time.monotonic() + 45
        args = [
            "/usr/local/bin/pg_isready",
            "-h",
            "/run/socket",
            "-p",
            PORT,
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-t",
            "1",
        ]
        while True:
            need(server.poll() is None and time.monotonic() < deadline)
            code = custody._run_image(args, common, 3).returncode
            if code == 0:
                break
            need(code in (1, 2))
            time.sleep(0.1)
        yield server, common
    finally:
        if server.poll() is None:
            need(custody._stop_held(server) == "FAST_SHUTDOWN_0")


def no_active_auto_conf(custody, data):
    raw = custody._private_bytes(data / "postgresql.auto.conf", 4096, owner=70)
    need(
        all(
            not line.strip() or line.lstrip().startswith(b"#")
            for line in raw.splitlines()
        )
    )


def native_immediate_fixture(custody, pg_root, data):
    global PHASE
    PHASE = "initializing_test_cluster"
    runtime, rootfs, common = stage_runtime(custody, pg_root, "origin", data)
    init = custody._run_image(
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
    need(init.returncode == 0)
    system = custody._control_state(custody._run_image(CONTROL, common, 5))
    no_active_auto_conf(custody, data)
    PHASE = "committing_test_rows"
    with held_server(custody, runtime, rootfs, read_only=False) as (server, backend):
        sql(
            custody,
            backend,
            "CREATE TABLE public.paper_wal_workload(id integer PRIMARY KEY, label text NOT NULL); INSERT INTO public.paper_wal_workload VALUES (1, 'before checkpoint')",
        )
        sql(custody, backend, "CHECKPOINT")
        sql(
            custody,
            backend,
            "BEGIN; INSERT INTO public.paper_wal_workload VALUES (2, 'after checkpoint'); COMMIT",
        )
        committed = sql(
            custody, backend, "SELECT pg_catalog.pg_current_wal_flush_lsn()::text"
        ).strip()
        need(
            sql(custody, backend, "SELECT count(*) FROM public.paper_wal_workload")
            == "2\n"
        )
        PHASE = "immediate_test_shutdown"
        pidfile = custody._private_bytes(data / "postmaster.pid", 4096, owner=70)
        need(pidfile.splitlines()[0] == str(server.pid).encode("ascii"))
        stopped = custody._run_image(
            [
                "/usr/local/bin/pg_ctl",
                "-D",
                "/run/data",
                "stop",
                "-m",
                "immediate",
                "-w",
                "-t",
                "15",
            ],
            backend,
            20,
        )
        need(stopped.returncode == 0 and server.wait(timeout=5) == 0)
    return system, committed, custody._run_image(CONTROL, common, 5)


def lsn(value):
    need(re.fullmatch(r"[0-9A-F]+/[0-9A-F]+", value) is not None)
    high, low = (int(part, 16) for part in value.split("/"))
    need(0 <= high < 2**32 and 0 <= low < 2**32)
    return high * 2**32 + low


def observe_recovered(custody, control, pg_root, copy, p0, *, name, expect_redo):
    runtime, rootfs, common = stage_runtime(custody, pg_root, name, copy)
    no_active_auto_conf(custody, copy)
    with held_server(custody, runtime, rootfs, read_only=True) as (_, backend):
        status = sql(
            custody,
            backend,
            "SELECT pg_catalog.pg_is_in_recovery(), pg_catalog.current_setting('transaction_read_only'), (SELECT system_identifier::text FROM pg_catalog.pg_control_system())",
        )
        recovery, read_only, system = status.strip().split("|")
        need((recovery, read_only) == ("f", "on") and system.isdecimal())
        need(sha(system.encode("ascii")) == p0.system_sha256)
        rows = sql(
            custody,
            backend,
            "SELECT id, label FROM public.paper_wal_workload ORDER BY id",
        )
        need(rows == "1|before checkpoint\n2|after checkpoint\n")
    shutdown = control.verify_clean_shutdown(
        custody._run_image(CONTROL, common, 5), prior=p0
    )
    log = custody._private_bytes(runtime / "server.private.log", 128000)
    redo = (
        b"redo starts at " in log
        and b"redo done at " in log
        and b"automatic recovery in progress" in log
    )
    need(redo is expect_redo)
    return shutdown, rows


def run(args):
    global PHASE
    need(sys.platform == "linux" and os.getpid() == 1 and os.geteuid() == 0)
    need(sys.flags.isolated == sys.flags.no_site == sys.flags.dont_write_bytecode == 1)
    need(
        os.uname().machine == "x86_64"
        and "microsoft" not in os.uname().release.casefold()
    )
    need(
        os.environ
        == {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"}
    )
    need(
        socket.if_nameindex() == [(1, "lo")]
        and Path("/proc/self/task/1/children").read_bytes().strip() == b""
    )
    need(
        args.pg_root == IMAGE_ROOT
        and FIXTURE.is_dir()
        and FIXTURE.resolve(strict=True) == FIXTURE
    )
    os.umask(0o077)
    PHASE = "measured_public_inputs"
    support = Path(__file__).resolve().parent
    custody = module("_paper_wal_custody", args.custody, CUSTODY_SHA256, 137370, 137370)
    copy_tool = module(
        "_paper_wal_cold_copy",
        support / "paper_wal_cold_copy.py",
        args.copy_sha256,
        65536,
    )
    control = module(
        "_paper_wal_control",
        support / "paper_wal_control.py",
        args.control_sha256,
        65536,
    )
    measured(args.inventory, INVENTORY_SHA256, 2 * 1024 * 1024)
    need(custody.PG_INVENTORY_SHA256 == INVENTORY_SHA256)
    custody._verify_image(args.pg_root, args.inventory)

    PHASE = "new_isolated_fixture"
    custody._mount("tmpfs", FIXTURE, "tmpfs", 2 | 4 | 8 | 1024, "mode=0700,size=256m")
    need(not any(FIXTURE.iterdir()) and os.statvfs(FIXTURE).f_flag & os.ST_NOATIME != 0)
    origin, working = FIXTURE / "origin", FIXTURE / "working"
    for path in (origin, working):
        path.mkdir(mode=0o700)
        os.chown(path, 70, 70)
    system, committed, observed = native_immediate_fixture(
        custody, args.pg_root, origin
    )
    PHASE = "confirming_dirty_control"
    native = control.admit_dirty_primary(observed, expected_system_sha256=system)
    need(lsn(committed) > lsn(native.redo_lsn))
    need(Path("/proc/self/task/1/children").read_bytes().strip() == b"")

    PHASE = "verifying_cold_copy"
    with directory_fd(origin) as source, directory_fd(working) as target:
        copied = copy_tool.copy_cold_pgdata(source, target)
        need(copied.source_before.tree_sha256 == copied.destination_after.tree_sha256)
        PHASE = "replaying_wal_on_copy"
        shutdown, initial_rows = observe_recovered(
            custody,
            control,
            args.pg_root,
            working,
            native,
            name="replay",
            expect_redo=True,
        )
        need(shutdown.cluster_state == "shut down")
        PHASE = "reopening_recovered_copy"
        final, reopened_rows = observe_recovered(
            custody,
            control,
            args.pg_root,
            working,
            native,
            name="reopen",
            expect_redo=False,
        )
        need(initial_rows == reopened_rows and final.cluster_state == "shut down")
        PHASE = "rechecking_unchanged_source"
        origin_after = copy_tool.inspect_cold_pgdata(source)
        need(origin_after == copied.source_before)
    need(Path("/proc/self/task/1/children").read_bytes().strip() == b"")
    return {
        "schema": SCHEMA,
        "status": "PASS_DISPOSABLE_PG17_WAL_REPLAY_ON_COPY",
        "source_commit": SOURCE_COMMIT,
        "custody_sha256": CUSTODY_SHA256,
        "copy_sha256": args.copy_sha256,
        "control_sha256": args.control_sha256,
        "pg_image": custody.PG_IMAGE,
        "pg_inventory_sha256": INVENTORY_SHA256,
        "pg_tree_sha256": custody.PG_TREE_SHA256,
        "origin_before_sha256": copied.source_before.tree_sha256,
        "origin_after_sha256": origin_after.tree_sha256,
        "working_copy_before_sha256": copied.destination_after.tree_sha256,
        "system_identifier_sha256": system,
        "initial_cluster_state": native.cluster_state,
        "final_cluster_state": final.cluster_state,
        "committed_rows_verified": 2,
        "independent_native_readbacks": 2,
        "wal_redo_observed": True,
        "preexisting_operator_data_read": False,
        "execution_authority": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--custody", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--pg-root", required=True, type=Path)
    parser.add_argument("--copy-sha256", required=True)
    parser.add_argument("--control-sha256", required=True)
    try:
        print(
            json.dumps(run(parser.parse_args()), sort_keys=True, separators=(",", ":")),
            flush=True,
        )
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 - Output only the bounded fixture phase.
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "status": "REFUSED",
                    "phase": PHASE,
                    "execution_authority": False,
                },
                separators=(",", ":"),
            ),
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2) from None
