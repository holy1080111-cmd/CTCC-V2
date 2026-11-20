"""Retain root/ancestor identity, WITHOUT retaining a publisher transaction.

This DATA pin grants no publication, delivery, claim, Arm or order authority.
Each existing outbox transaction still takes its original exclusive lease.
"""

import ctypes
import os
import stat
from contextlib import ExitStack, contextmanager

from app.trade_evidence import native_private_root_v2 as private_root
from app.trade_evidence import storage


def _need(value):
    if value is not True:
        raise ValueError("native_outbox_root_identity_unavailable") from None


def _posix_identity(info):
    _need(stat.S_ISDIR(info.st_mode))
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid


def _windows_identity(api, handle):
    info = api.info_type()
    _need(bool(api.info(handle, ctypes.byref(info))))
    _need(bool(info.attributes & 0x10) and not info.attributes & storage._REPARSE_POINT)
    return info.volume, info.index_high, info.index_low


class _RootIdentityPin:
    __slots__ = ("api", "closed", "records", "root")

    def __init__(self, root, records, api):
        self.root, self.records, self.api, self.closed = root, records, api, False

    def check(self):
        _need(self.closed is False)
        private_root.require_private_evidence_base_v2(str(self.root))
        if self.api is None:
            for fd, parent, name, expected in self.records:
                _need(_posix_identity(os.fstat(fd)) == expected)
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                _need(_posix_identity(current) == expected)
        else:
            for path, handle, expected in self.records:
                _need(_windows_identity(self.api, handle) == expected)
                fresh = self.api.open_directory(path, publisher=False)
                try:
                    _need(_windows_identity(self.api, fresh) == expected)
                finally:
                    self.api.close(fresh)


@contextmanager
def pin_outbox_root_v1(root):
    """Pin every ancestor and reject substitution without taking publisher flock."""
    root = storage._root_path(root)
    private_root.require_private_evidence_base_v2(str(root))
    pin = None
    with ExitStack() as stack:
        records = []
        if os.name == "nt":
            api = storage._WindowsAPI()
            for path in (*reversed(root.parents), root):
                handle = api.open_directory(path, publisher=False)
                stack.callback(api.close, handle)
                records.append((path, handle, _windows_identity(api, handle)))
        else:
            api = None
            parent = None
            fd = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, fd)
            records.append((fd, parent, root.anchor, _posix_identity(os.fstat(fd))))
            for part in root.parts[1:]:
                parent = fd
                fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                )
                stack.callback(os.close, fd)
                records.append((fd, parent, part, _posix_identity(os.fstat(fd))))
        pin = _RootIdentityPin(root, tuple(records), api)
        pin.check()
        try:
            yield pin
            pin.check()
        finally:
            pin.closed = True
