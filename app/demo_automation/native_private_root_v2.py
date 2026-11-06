"""Fixed server evidence directory privacy checks; no trading permit."""

import ctypes
import os
import stat
from pathlib import Path


class NativeEvidenceRootErrorV2(ValueError):
    pass


def _need(value):
    if value is not True:
        raise NativeEvidenceRootErrorV2(
            "native_demo_private_evidence_root_unavailable"
        ) from None


def _windows_private(path):
    from ctypes import wintypes as w

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    adv.GetNamedSecurityInfoW.argtypes = [
        w.LPWSTR,
        w.DWORD,
        w.DWORD,
        ctypes.POINTER(pointer),
        ctypes.POINTER(pointer),
        ctypes.POINTER(pointer),
        ctypes.POINTER(pointer),
        ctypes.POINTER(pointer),
    ]
    adv.GetNamedSecurityInfoW.restype = w.DWORD
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    adv.OpenProcessToken.restype = w.BOOL
    adv.GetTokenInformation.argtypes = [
        w.HANDLE,
        ctypes.c_int,
        pointer,
        w.DWORD,
        ctypes.POINTER(w.DWORD),
    ]
    adv.GetTokenInformation.restype = w.BOOL
    adv.EqualSid.argtypes = [pointer, pointer]
    adv.EqualSid.restype = w.BOOL
    adv.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, ctypes.POINTER(pointer)]
    adv.ConvertStringSidToSidW.restype = w.BOOL
    adv.GetAce.argtypes = [pointer, w.DWORD, ctypes.POINTER(pointer)]
    adv.GetAce.restype = w.BOOL
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    token, descriptor, owner, dacl = w.HANDLE(), pointer(), pointer(), pointer()
    allocated = []
    try:
        _need(
            bool(
                adv.OpenProcessToken(
                    kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)
                )
            )
        )
        size = w.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        _need(0 < size.value <= 65536)
        user_buffer = ctypes.create_string_buffer(size.value)
        _need(
            bool(
                adv.GetTokenInformation(token, 1, user_buffer, size, ctypes.byref(size))
            )
        )
        user_sid = ctypes.cast(user_buffer, ctypes.POINTER(pointer))[0]
        _need(
            adv.GetNamedSecurityInfoW(
                str(path),
                1,
                0x00000001 | 0x00000004,
                ctypes.byref(owner),
                None,
                ctypes.byref(dacl),
                None,
                ctypes.byref(descriptor),
            )
            == 0
        )
        _need(
            bool(owner.value)
            and bool(dacl.value)
            and bool(adv.EqualSid(owner, user_sid))
        )
        allowed = [user_sid]
        for fixed in ("S-1-5-18", "S-1-5-32-544"):
            value = pointer()
            _need(bool(adv.ConvertStringSidToSidW(fixed, ctypes.byref(value))))
            allocated.append(value)
            allowed.append(value)
        # ACL header: revision/reserved/size/ACE count/reserved. Fixed allow ACE
        # has a four-byte header, four-byte mask, then the actual native SID.
        header = ctypes.string_at(dacl, 8)
        acl_size = int.from_bytes(header[2:4], "little")
        count = int.from_bytes(header[4:6], "little")
        _need(8 <= acl_size <= 65535 and 0 < count <= 4096)
        for index in range(count):
            ace = pointer()
            _need(bool(adv.GetAce(dacl, index, ctypes.byref(ace))))
            ace_header = ctypes.string_at(ace, 4)
            kind, length = ace_header[0], int.from_bytes(ace_header[2:4], "little")
            _need(16 <= length <= acl_size and kind in (0, 1))
            if kind == 0:
                sid = pointer(ace.value + 8)
                sid_header = ctypes.string_at(sid, 8)
                _need(sid_header[0] == 1 and 16 + 4 * sid_header[1] <= length)
                _need(any(bool(adv.EqualSid(sid, value)) for value in allowed))
    finally:
        for value in allocated:
            kernel.LocalFree(value)
        if descriptor.value:
            kernel.LocalFree(descriptor)
        if token.value:
            _need(bool(kernel.CloseHandle(token)))


def require_private_evidence_base_v2(raw):
    _need(type(raw) is str and bool(raw))
    path = Path(raw)
    _need(
        path.is_absolute()
        and path.anchor not in (str(path), "")
        and not path.anchor.startswith(("\\\\", "//"))
        and all(part not in (".", "..") for part in path.parts)
    )
    source = Path(__file__).absolute().parents[2]
    forbidden = {source}
    for parent in (source, *source.parents):
        if (parent / ".git").exists() or (parent / "Dockerfile").exists():
            forbidden.add(parent)
    for root in forbidden:
        _need(path != root and root not in path.parents)
    for item in (*reversed(path.parents), path):
        info = item.lstat()
        _need(
            stat.S_ISDIR(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and not getattr(info, "st_file_attributes", 0) & 1024
        )
    info = path.lstat()
    if os.name == "nt":
        _windows_private(path)
    else:
        _need(info.st_uid == os.geteuid() and not info.st_mode & 0o077)
    return path


def mkdir_private_evidence_child_v2(directory, name):
    """Create one child under the held, validated private evidence directory."""
    from app.trade_evidence.storage import _WindowsDirectory

    if type(directory) is _WindowsDirectory:
        # Python's Windows 0o700 ACL differs from this service's private ACL.
        require_private_evidence_base_v2(str(directory.path))
        directory.mkdir_inherited_acl(name)
    else:
        directory.mkdir(name)
