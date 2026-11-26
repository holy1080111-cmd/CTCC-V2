"""Hold the journal-selected Paper PGDATA relative to a locked native root.

The caller supplies the N541 exchange module loaded from exactly the bytes
identified by EXCHANGE_SHA256. The caller owns the root and lock descriptors,
the mount and PostgreSQL lifetime. The device number is kernel-local and may
change across WSL restarts. A mismatch refuses without deriving a new pin.
"""

import os
import stat
import sys
from contextlib import contextmanager

EXCHANGE_SHA256 = "fe4e6ec88e1f824638dd8e7f0d3305ef529acb6eff9180a2376b086cab0946ae"
_DATA = b"data"
_DATA_OWNER = 70


class SelectorRefused(ValueError):
    """The held native data or its bind view differs from the journal selector."""


def _need(condition):
    if not condition:
        raise SelectorRefused("paper_wal_held_selector_refused") from None


def _descriptor(fd):
    _need(type(fd) is int and fd >= 0)
    return fd


class _HeldSelector:
    def __init__(self, parent, lock, data, expected, exchange):
        self._parent = parent
        self._lock = lock
        self._data = data
        self._expected = expected
        self._exchange = exchange
        self._mount = exchange._mount_id(parent)

    @property
    def fd(self):
        return _descriptor(self._data)

    @property
    def bind_source(self):
        """A bind source reference valid only within this context and process."""
        return f"/proc/self/fd/{self.fd}"

    def _verify_data(self, info):
        _need(
            stat.S_ISDIR(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o700
            and (info.st_uid, info.st_gid) == (_DATA_OWNER, _DATA_OWNER)
            and (info.st_dev, info.st_ino)
            == (self._expected.device, self._expected.inode)
        )

    def recheck(self):
        """Recheck the held identity, the direct child name and the borrowed lock."""
        data = self.fd
        exchange = self._exchange
        exchange._lock_identity_verified(self._parent, self._lock)
        self._verify_data(os.fstat(data))
        _need(
            exchange._mount_id(data) == exchange._mount_id(self._parent) == self._mount
        )
        with exchange._directory(self._parent, _DATA) as named:
            self._verify_data(os.fstat(named))
            _need(exchange._mount_id(named) == self._mount)
            self._verify_data(
                os.stat(_DATA, dir_fd=self._parent, follow_symlinks=False)
            )
        return self._expected

    def verify_bound(self, bind_fd):
        """Require the caller's held bind view to expose the same selected inode."""
        bind_fd = _descriptor(bind_fd)
        self.recheck()
        self._verify_data(os.fstat(bind_fd))
        mount = self._exchange._mount_id(bind_fd)
        _need(mount != self._mount)
        self.recheck()
        self._verify_data(os.fstat(bind_fd))
        _need(self._exchange._mount_id(bind_fd) == mount)
        return self._expected


@contextmanager
def hold_selected_pgdata(root_fd, lock_fd, *, expected, exchange):
    """Borrow the existing lock and hold only the exact selected native data FD."""
    _need(sys.platform == "linux")
    _descriptor(root_fd)
    _descriptor(lock_fd)
    _need(type(expected) is exchange.DirectoryIdentity)
    _need(type(expected.device) is type(expected.inode) is int)
    _need(expected.device >= 0 and expected.inode > 0)
    with exchange._parent(root_fd) as parent:
        exchange._lock_identity_verified(parent, lock_fd)
        with exchange._directory(parent, _DATA) as data:
            held = _HeldSelector(parent, lock_fd, data, expected, exchange)
            try:
                held.recheck()
                yield held
            finally:
                held._data = None
