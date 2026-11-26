"""Verify the held Paper PGDATA selector with disposable native Linux objects."""

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import socket
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

HELD_SELECTOR_SHA256 = "610b9bc0e3275bb2826f9c47bf44c38a0b4a48be1dc583b080217a2f24a5cf69"
EXCHANGE_SHA256 = "fe4e6ec88e1f824638dd8e7f0d3305ef529acb6eff9180a2376b086cab0946ae"
APPROVED_HELD_SELECTOR = (
    "610b9bc0e3275bb2826f9c47bf44c38a0b4a48be1dc583b080217a2f24a5cf69"
)
APPROVED_EXCHANGE = "fe4e6ec88e1f824638dd8e7f0d3305ef529acb6eff9180a2376b086cab0946ae"
SCHEMA = "ctcc.validation.paper_held_pgdata_selector_native.v1"
PHASE = "environment"
SCRATCH = Path("/var/tmp")
DATA_BYTES = (
    ("PG_VERSION", b"17\n"),
    ("global/pg_control", b"mocked control bytes for selector fixture\n"),
    ("pg_wal/fixture-segment", b"mocked WAL bytes for selector fixture\n"),
)


def need(condition):
    if not condition:
        raise ValueError("paper_held_pgdata_selector_native_refused") from None


def measured(path, digest):
    need(path.is_absolute() and path.resolve(strict=True) == path)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        need(
            stat.S_ISREG(info.st_mode)
            and 0 < info.st_size <= 65536
            and info.st_nlink == 1
        )
        raw = stream.read(65537)
        after = os.fstat(stream.fileno())
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    need(all(getattr(info, field) == getattr(after, field) for field in fields))
    need(len(raw) == info.st_size and hashlib.sha256(raw).hexdigest() == digest)
    module = ModuleType(path.stem)
    module.__file__ = str(path)
    sys.modules[path.stem] = module
    exec(compile(raw, str(path), "exec"), vars(module))  # noqa: S102 - Exact measured bytes only.
    return module


class Mounts:
    def __init__(self):
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.unshare.argtypes = [ctypes.c_int]
        self.libc.unshare.restype = ctypes.c_int
        self.libc.mount.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_ulong,
            ctypes.c_char_p,
        ]
        self.libc.mount.restype = ctypes.c_int

    def establish_scratch(self, exchange):
        initial = os.readlink("/proc/self/ns/mnt")
        need(self.libc.unshare(0x00020000) == 0)
        need(os.readlink("/proc/self/ns/mnt") != initial)
        need(self.libc.mount(None, b"/", None, 16384 | 262144, None) == 0)
        need(SCRATCH.resolve(strict=True) == SCRATCH)
        with directory(SCRATCH) as previous:
            mount = exchange._mount_id(previous)
            need(
                self.libc.mount(
                    b"tmpfs",
                    os.fsencode(SCRATCH),
                    b"tmpfs",
                    2 | 4 | 8,
                    b"mode=0700,size=16m,nr_inodes=1024",
                )
                == 0
            )
            with directory(SCRATCH) as selected:
                observed = os.fstat(selected)
                need(exchange._mount_id(selected) != mount)
                need(
                    (observed.st_uid, observed.st_gid, stat.S_IMODE(observed.st_mode))
                    == (0, 0, 0o700)
                )
        flags = os.ST_NOSUID | os.ST_NODEV | os.ST_NOEXEC
        sizes = os.statvfs(SCRATCH)
        need(
            sizes.f_flag & flags == flags
            and sizes.f_blocks * sizes.f_frsize <= 16 * 1024**2
        )
        need(not any(SCRATCH.iterdir()))

    def bind(self, source, target):
        need(
            self.libc.mount(os.fsencode(source), os.fsencode(target), None, 4096, None)
            == 0
        )


@contextmanager
def directory(path, *, parent=None):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path, flags, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


@contextmanager
def lock_file(parent, name):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    fd = os.open(name, flags, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def fixture_data(path):
    path.mkdir(mode=0o700)
    os.chown(path, 70, 70)
    for name in ("global", "pg_wal"):
        directory_path = path / name
        directory_path.mkdir(mode=0o700)
        os.chown(directory_path, 70, 70)
    for name, raw in DATA_BYTES:
        file = path / name
        with file.open("xb") as stream:
            need(stream.write(raw) == len(raw))
            os.fchown(stream.fileno(), 70, 70)
            os.fchmod(stream.fileno(), 0o600)


def same_mock_data(path):
    return all((path / name).read_bytes() == raw for name, raw in DATA_BYTES)


def refusal(action, expected):
    try:
        action()
    except expected as error:
        return error
    raise ValueError("paper_held_pgdata_expected_refusal_missing") from None


def run():
    global PHASE
    need(sys.argv[1:] == [])
    need(
        sys.platform == "linux"
        and os.getpid() == 1
        and os.geteuid() == 0
        and os.getegid() == 0
    )
    need(sys.flags.isolated == sys.flags.no_site == sys.flags.dont_write_bytecode == 1)
    need(
        os.uname().machine == "x86_64"
        and "microsoft" not in os.uname().release.casefold()
    )
    need(
        os.environ
        == {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"}
    )
    need(socket.if_nameindex() == [(1, "lo")])
    children = Path("/proc/self/task/1/children")
    need(children.read_bytes().strip() == b"")
    os.umask(0o077)

    PHASE = "pins"
    need(
        HELD_SELECTOR_SHA256 == APPROVED_HELD_SELECTOR
        and EXCHANGE_SHA256 == APPROVED_EXCHANGE
    )
    support = Path(__file__).resolve().parent
    exchange = measured(support / "paper_wal_exchange.py", EXCHANGE_SHA256)
    selector = measured(support / "paper_wal_held_selector.py", HELD_SELECTOR_SHA256)
    need(selector.EXCHANGE_SHA256 == EXCHANGE_SHA256)

    PHASE = "disposable_mount"
    mounts = Mounts()
    mounts.establish_scratch(exchange)
    parent = SCRATCH / "operator"
    parent.mkdir(mode=0o700)
    data = parent / "data"
    alternate = parent / "alternate"
    fixture_data(data)
    fixture_data(alternate)
    need(same_mock_data(data) and same_mock_data(alternate))
    for name in ("active.lock", "foreign.lock"):
        with (parent / name).open("xb") as stream:
            stream.write(b"synthetic lock\n")
    alias = SCRATCH / "bound"
    foreign_alias = SCRATCH / "bound-foreign"
    for path in (alias, foreign_alias):
        path.mkdir(mode=0o700)

    def identity(info):
        return exchange.DirectoryIdentity(info.st_dev, info.st_ino)

    with (
        directory(parent) as root_fd,
        lock_file(root_fd, b"active.lock") as active,
        lock_file(root_fd, b"foreign.lock") as foreign,
        directory(b"alternate", parent=root_fd) as alternate_fd,
    ):
        pin = identity(data.lstat())
        wrong_pin = identity(os.fstat(alternate_fd))
        need(pin.device == wrong_pin.device and pin.inode != wrong_pin.inode)

        def selected(lock=active, expected=pin):
            return selector.hold_selected_pgdata(
                root_fd, lock, expected=expected, exchange=exchange
            )

        def enter(lock=active, expected=pin):
            with selected(lock, expected):
                pass

        PHASE = "locks"
        refusal(enter, exchange.ExchangeRefused)
        fcntl.flock(foreign, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(active, fcntl.LOCK_EX | fcntl.LOCK_NB)
        refusal(lambda: enter(foreign), exchange.ExchangeRefused)

        PHASE = "positive_bind"
        with selected() as held:
            need(identity(os.fstat(held.fd)) == pin and held.recheck() == pin)
            mounts.bind(held.bind_source, alias)
            with directory(alias) as bound:
                need(held.verify_bound(bound) == pin and same_mock_data(alias))
                need(exchange._mount_id(bound) != exchange._mount_id(held.fd))

                PHASE = "negative_binds"
                refusal(lambda: enter(expected=wrong_pin), selector.SelectorRefused)
                with directory(b"data", parent=root_fd) as direct:
                    need(identity(os.fstat(direct)) == pin)
                    refusal(lambda: held.verify_bound(direct), selector.SelectorRefused)
                mounts.bind(f"/proc/self/fd/{alternate_fd}", foreign_alias)
                with directory(foreign_alias) as foreign_bound:
                    need(
                        identity(os.fstat(foreign_bound)) == wrong_pin
                        and same_mock_data(foreign_alias)
                    )
                    refusal(
                        lambda: held.verify_bound(foreign_bound),
                        selector.SelectorRefused,
                    )

                PHASE = "name_swap"
                os.rename(b"data", b"saved", src_dir_fd=root_fd, dst_dir_fd=root_fd)
                os.rename(b"alternate", b"data", src_dir_fd=root_fd, dst_dir_fd=root_fd)
                need(identity(data.lstat()) == wrong_pin and same_mock_data(data))
                refusal(held.recheck, selector.SelectorRefused)
                refusal(lambda: held.verify_bound(bound), selector.SelectorRefused)
                os.rename(b"data", b"alternate", src_dir_fd=root_fd, dst_dir_fd=root_fd)
                os.rename(b"saved", b"data", src_dir_fd=root_fd, dst_dir_fd=root_fd)
                need(held.verify_bound(bound) == pin)

        PHASE = "locks"
        with lock_file(root_fd, b"active.lock") as contender:
            error = refusal(
                lambda: fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB),
                BlockingIOError,
            )
            need(error.errno in (errno.EAGAIN, errno.EWOULDBLOCK))

        PHASE = "symlink"
        os.rename(b"data", b"saved", src_dir_fd=root_fd, dst_dir_fd=root_fd)
        os.symlink(b"saved", b"data", dir_fd=root_fd, target_is_directory=True)
        error = refusal(enter, OSError)
        need(error.errno in (errno.ELOOP, errno.ENOTDIR))

    need(children.read_bytes().strip() == b"" and socket.if_nameindex() == [(1, "lo")])
    return {
        "schema": SCHEMA,
        "status": "PASS_DISPOSABLE_HELD_PGDATA_SELECTOR",
        "held_selector_sha256": HELD_SELECTOR_SHA256,
        "exchange_sha256": EXCHANGE_SHA256,
        "positive_bound_alias_verified": True,
        "wrong_inode_with_identical_bytes_refused": True,
        "direct_view_refused": True,
        "foreign_bind_refused": True,
        "unlocked_refused": True,
        "foreign_lock_refused": True,
        "symlink_refused": True,
        "swapped_name_refused": True,
        "borrowed_lock_preserved": True,
        "synthetic_only": True,
        "postgres_started": False,
        "execution_authority": False,
    }


def emit(document, stream):
    print(
        json.dumps(document, sort_keys=True, separators=(",", ":")),
        file=stream,
        flush=True,
    )


if __name__ == "__main__":
    try:
        emit(run(), sys.stdout)
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 - No native paths or identities in diagnostics.
        emit(
            {
                "schema": SCHEMA,
                "status": "REFUSED",
                "phase": PHASE,
                "execution_authority": False,
            },
            sys.stderr,
        )
        raise SystemExit(2) from None
