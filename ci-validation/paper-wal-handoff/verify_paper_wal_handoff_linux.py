"""Prove a disposable PG17 WAL recovery handoff against native Linux."""

import argparse
import hashlib
import json
import os
import socket
import stat
import sys
from pathlib import Path
from types import ModuleType

SCHEMA = "ctcc.validation.paper_wal_handoff_native.v1"
CUSTODY_SHA256 = "51453d2edcaf7734d9569b2d0504960b239f8f0ceddf3861ce24a8a182e15af1"
SOURCE_COMMIT = "d847b9976048c5eca8d2a0e803b0c29c5fcbef2e"
IMAGE_ROOT = Path("/root/ctcc-ci-pg17-f02121de-a07a7048-20261121/rootfs")
PINS = {
    "verify_paper_wal_copy_linux": "bfd8e93b639c36cccd7621e6e96f874dc13dec6b11160032edbdb39b24246bb0",
    "paper_wal_cold_copy": "e078f2409c1b24f0d520aca7813d2597d6f30458d196b755f3971b1fb4ab7785",
    "paper_wal_control": "16d3d5165ef01e545b3c1c6c6bcdc6c2ceb40df178f5494948d1ff53377aa4e3",
    "paper_wal_exchange": "fe4e6ec88e1f824638dd8e7f0d3305ef529acb6eff9180a2376b086cab0946ae",
    "paper_wal_handoff_journal": "be585eee3750ca7d76d7ed84012fc87bbadbe7f1a48896ce95a4e098225f2f41",
    "paper_wal_handoff": "fc9c3ad4f16cffb84da3dfffffe7a0692bee3414ae49dbda74badc81ce442e84",
}
PHASE = "environment"


def need(condition):
    if condition is not True:
        raise ValueError("paper_wal_handoff_acceptance_refused") from None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def measured_driver(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= 65536)
        raw = stream.read(65537)
        after = os.fstat(stream.fileno())
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    need(all(getattr(before, field) == getattr(after, field) for field in fields))
    need(len(raw) == before.st_size and sha(raw) == PINS[path.stem])
    module = ModuleType(path.stem)
    module.__file__ = str(path)
    sys.modules[path.stem] = module
    exec(compile(raw, str(path), "exec"), vars(module))  # noqa: S102 - Exact hashed bytes only.
    return module


def prove_handoff(parent, anchor, target, operation, api, helper):
    """Let an already synced exchange survive the loss of its terminal writer."""
    global PHASE
    handoff, exchange, journal, copy_tool = api
    names = handoff._names(operation)
    plan = handoff._plan(exchange, anchor, target, operation)
    kwargs = {"journal": journal, "exchange": exchange, "copy_tool": copy_tool}

    PHASE = "prepare"
    with (
        helper.directory_fd(parent) as fd,
        handoff._operator_lock(fd) as lock,
        handoff._held_roles(fd, plan, target) as (source, candidate),
    ):
        intent = handoff._reserve(
            fd,
            source,
            candidate,
            names=names,
            plan=plan,
            anchor=anchor,
            target=target,
            operation_id=operation,
            **kwargs,
        )
        observed = journal.inspect_handoff(
            handoff._read(fd, names[0]), None, anchor=anchor
        )
        need(
            observed.intent_sha256 == sha(intent)
            and observed.phase == journal.RECONCILIATION_REQUIRED
        )
        need(handoff._read(fd, names[1], missing_ok=True) is None)
        need(handoff._read(fd, names[2], missing_ok=True) is None)
        need(
            exchange.inspect_exchange(fd, source, candidate, plan=plan)
            is exchange.ExchangeState.PRE
        )

        PHASE = "exchange"
        result = exchange.exchange_pgdata(
            fd, source, candidate, plan=plan, witness_name=names[1], lock_fd=lock
        )
        need(
            result.state is exchange.ExchangeState.POST
            and result.kernel_called
            and result.parent_fsynced
        )
        need(handoff._read(fd, names[2], missing_ok=True) is None)

    PHASE = "reconcile"
    with helper.directory_fd(parent) as fd:
        try:
            handoff.publish_new(
                fd, anchor=anchor, target=target, operation_id=operation, **kwargs
            )
        except handoff.HandoffRefused:
            need(handoff._read(fd, names[0]) == intent)
            need(handoff._read(fd, names[2], missing_ok=True) is None)
        else:
            need(False)
        reconciled = handoff.reconcile(
            fd, anchor=anchor, operation_id=operation, **kwargs
        )
    with helper.directory_fd(parent) as fd:
        replay = handoff.reconcile(fd, anchor=anchor, operation_id=operation, **kwargs)
        need(replay.status == "ALREADY_COMPLETED" and reconciled.status == "COMPLETED")
        need(replay.intent_sha256 == reconciled.intent_sha256 == sha(intent))
        need(
            replay.terminal_sha256
            == reconciled.terminal_sha256
            == sha(handoff._read(fd, names[2]))
        )
    return reconciled


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
    children = Path("/proc/self/task/1/children")
    need(socket.if_nameindex() == [(1, "lo")] and children.read_bytes().strip() == b"")
    need(args.pg_root == IMAGE_ROOT)
    os.umask(0o077)

    PHASE = "load"
    support = Path(__file__).resolve().parent
    helper = measured_driver(support / "verify_paper_wal_copy_linux.py")
    modules = {
        name: helper.module(name, support / f"{name}.py", digest, 65536)
        for name, digest in PINS.items()
        if name != "verify_paper_wal_copy_linux"
    }
    control, copy_tool = modules["paper_wal_control"], modules["paper_wal_cold_copy"]
    journal, exchange, handoff = (
        modules[key]
        for key in (
            "paper_wal_handoff_journal",
            "paper_wal_exchange",
            "paper_wal_handoff",
        )
    )
    custody = helper.module(
        "_paper_wal_custody", args.custody, CUSTODY_SHA256, 137370, 137370
    )
    helper.measured(args.inventory, helper.INVENTORY_SHA256, 2 * 1024 * 1024)
    need(custody.PG_INVENTORY_SHA256 == helper.INVENTORY_SHA256)
    custody._verify_image(args.pg_root, args.inventory)

    PHASE = "fixture"
    fixture = helper.FIXTURE
    need(fixture.is_dir() and fixture.resolve(strict=True) == fixture)
    custody._mount("tmpfs", fixture, "tmpfs", 2 | 4 | 8 | 1024, "mode=0700,size=256m")
    need(not any(fixture.iterdir()) and os.statvfs(fixture).f_flag & os.ST_NOATIME != 0)
    parent = fixture / "synthetic-paper-operator"
    parent.mkdir(mode=0o700)
    custody._write_private(
        parent / "active.lock", b"offline disposable fixture\n", uid=0, gid=0
    )
    operation = "54100000000000000000000000000001"
    origin, candidate = parent / "data", parent / f"wal-recovered-{operation}"
    for directory in (origin, candidate):
        directory.mkdir(mode=0o700)
        os.chown(directory, 70, 70)
    system, committed, raw_control = helper.native_immediate_fixture(
        custody, args.pg_root, origin
    )
    dirty = control.admit_dirty_primary(raw_control, expected_system_sha256=system)
    need(helper.lsn(committed) > helper.lsn(dirty.redo_lsn))
    need(children.read_bytes().strip() == b"")

    PHASE = "replay"
    with (
        helper.directory_fd(origin) as source,
        helper.directory_fd(candidate) as target,
    ):
        copied = copy_tool.copy_cold_pgdata(source, target)
    need(copied.source_before.tree_sha256 == copied.destination_after.tree_sha256)
    shutdown, rows = helper.observe_recovered(
        custody,
        control,
        args.pg_root,
        candidate,
        dirty,
        name="qualified-replay",
        expect_redo=True,
    )
    clean, confirmed_rows = helper.observe_recovered(
        custody,
        control,
        args.pg_root,
        candidate,
        dirty,
        name="qualified-reopen",
        expect_redo=False,
    )
    need(
        shutdown.cluster_state == clean.cluster_state == "shut down"
        and rows == confirmed_rows
    )
    with (
        helper.directory_fd(origin) as source,
        helper.directory_fd(candidate) as target,
    ):
        original_tree = copy_tool.inspect_cold_pgdata(source)
        candidate_tree = copy_tool.inspect_cold_pgdata(target)
    need(original_tree == copied.source_before)
    need(
        original_tree.tree_sha256 != candidate_tree.tree_sha256
        and original_tree.inode != candidate_tree.inode
    )
    qualification = sha(
        json.dumps(
            {
                "system_sha256": system,
                "row_sha256": sha(rows.encode("ascii")),
                "origin": original_tree.tree_sha256,
                "candidate": candidate_tree.tree_sha256,
                "device": candidate_tree.device,
                "inode": candidate_tree.inode,
                "state": clean.cluster_state,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    )
    anchor = journal.WalHandoffAnchor(
        sandbox="54100000000000000000000000000011",
        counter=2,
        pending_open_sha256=sha(b"disposable pending open 2"),
        previous_close_sha256=sha(b"disposable close 1"),
        descriptor_sha256=sha(b"disposable native fixture descriptor"),
        source_commit=SOURCE_COMMIT,
        source_tree=journal.SOURCE_TREE,
        manifest_sha256=journal.MANIFEST_SHA256,
        package_identity_sha256=journal.PACKAGE_IDENTITY_SHA256,
        tls_generation_id="54100000000000000000000000000022",
        tls_profile_sha256=sha(b"fixture TLS profile"),
        tls_ca_sha256=sha(b"fixture CA identity"),
        system_sha256=system,
    )
    target = journal.WalHandoffTarget(
        original_tree.tree_sha256,
        candidate_tree.tree_sha256,
        original_tree.device,
        original_tree.inode,
        candidate_tree.device,
        candidate_tree.inode,
        qualification,
    )
    reconciled = prove_handoff(
        parent,
        anchor,
        target,
        operation,
        (handoff, exchange, journal, copy_tool),
        helper,
    )

    PHASE = "reopen"
    with helper.directory_fd(origin) as fd:
        active = copy_tool.inspect_cold_pgdata(fd)
    need(
        (active.device, active.inode, active.tree_sha256)
        == (candidate_tree.device, candidate_tree.inode, candidate_tree.tree_sha256)
    )
    final, active_rows = helper.observe_recovered(
        custody,
        control,
        args.pg_root,
        origin,
        dirty,
        name="published-reopen",
        expect_redo=False,
    )
    need(final.cluster_state == "shut down" and active_rows == rows)
    with helper.directory_fd(candidate) as fd:
        preserved = copy_tool.inspect_cold_pgdata(fd)
    need(
        (preserved.device, preserved.inode, preserved.tree_sha256)
        == (original_tree.device, original_tree.inode, original_tree.tree_sha256)
    )
    need(children.read_bytes().strip() == b"")
    return {
        "schema": SCHEMA,
        "status": "PASS_DISPOSABLE_PG17_WAL_ATOMIC_HANDOFF",
        "source_commit": SOURCE_COMMIT,
        "custody_sha256": CUSTODY_SHA256,
        "pg_image": custody.PG_IMAGE,
        "pg_inventory_sha256": custody.PG_INVENTORY_SHA256,
        "pg_tree_sha256": custody.PG_TREE_SHA256,
        "origin_tree_sha256": original_tree.tree_sha256,
        "preserved_origin_tree_sha256": preserved.tree_sha256,
        "candidate_tree_sha256": candidate_tree.tree_sha256,
        "published_intent_sha256": reconciled.intent_sha256,
        "reconciled_terminal_sha256": reconciled.terminal_sha256,
        "origin_and_active_distinct": True,
        "intent_before_exchange_verified": True,
        "interrupted_post_reconciled": True,
        "terminal_replay_idempotent": True,
        "duplicate_apply_refused": True,
        "native_reopen_verified": True,
        "committed_rows_verified": 2,
        "preexisting_operator_data_read": False,
        "execution_authority": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--custody", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--pg-root", required=True, type=Path)
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
