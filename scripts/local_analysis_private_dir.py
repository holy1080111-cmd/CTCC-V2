"""Create one persistent owner-only Windows local-analysis installation root.

Adapted from the reviewed N120 private-work ACL primitive. No system ACL or
policy is changed; only a new owned leaf under AppData/Local is created.
"""

from __future__ import annotations

import ctypes
import re
import sys
from ctypes import wintypes
from pathlib import Path
from uuid import uuid4

LEAF = re.compile(r"ctcc-analysis-[a-f0-9]{32}\Z")
FILE_ALL_ACCESS = 0x001F01FF
OWNER_AND_DACL = 0x00000005
SE_DACL_PROTECTED = 0x1000


class PrivateWorkError(RuntimeError):
    """Fixed scope/ACL refusal, no Windows SID or pathname in error output."""


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]


class _AclSizeInformation(ctypes.Structure):
    _fields_ = [
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    ]


class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", wintypes.WORD),
    ]


class _AllowedAce(ctypes.Structure):
    _fields_ = [
        ("Header", _AceHeader),
        ("Mask", wintypes.DWORD),
        ("SidStart", wintypes.DWORD),
    ]


def need(ok, code):
    if ok is not True:
        raise PrivateWorkError(code) from None


def api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    handle_p = ctypes.POINTER(wintypes.HANDLE)
    pointer_p = ctypes.POINTER(wintypes.LPVOID)
    bool_p = ctypes.POINTER(wintypes.BOOL)
    dword_p = ctypes.POINTER(wintypes.DWORD)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    kernel.LocalFree.restype = wintypes.LPVOID
    kernel.CreateDirectoryW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(_SecurityAttributes),
    ]
    kernel.CreateDirectoryW.restype = wintypes.BOOL
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    kernel.GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        dword_p,
        dword_p,
        dword_p,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel.GetVolumeInformationW.restype = wintypes.BOOL
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, handle_p]
    security.OpenProcessToken.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        dword_p,
    ]
    security.GetTokenInformation.restype = wintypes.BOOL
    security.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    security.ConvertSidToStringSidW.restype = wintypes.BOOL
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        pointer_p,
        dword_p,
    ]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    security.GetFileSecurityW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        dword_p,
    ]
    security.GetFileSecurityW.restype = wintypes.BOOL
    security.GetSecurityDescriptorControl.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.WORD),
        dword_p,
    ]
    security.GetSecurityDescriptorControl.restype = wintypes.BOOL
    security.GetSecurityDescriptorOwner.argtypes = [wintypes.LPVOID, pointer_p, bool_p]
    security.GetSecurityDescriptorOwner.restype = wintypes.BOOL
    security.GetSecurityDescriptorDacl.argtypes = [
        wintypes.LPVOID,
        bool_p,
        pointer_p,
        bool_p,
    ]
    security.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    security.GetAclInformation.argtypes = [
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    security.GetAclInformation.restype = wintypes.BOOL
    security.GetAce.argtypes = [wintypes.LPVOID, wintypes.DWORD, pointer_p]
    security.GetAce.restype = wintypes.BOOL
    return kernel, security


def sid_text(kernel, security, pointer):
    string = wintypes.LPWSTR()
    need(
        bool(security.ConvertSidToStringSidW(pointer, ctypes.byref(string))),
        "private_work_sid_unavailable",
    )
    try:
        text = string.value
        need(
            type(text) is str and re.fullmatch(r"S-1-[0-9-]{3,110}", text) is not None,
            "private_work_sid_shape",
        )
        return text
    finally:
        kernel.LocalFree(ctypes.cast(string, wintypes.LPVOID))


def current_user(kernel, security):
    token = wintypes.HANDLE()
    need(
        bool(
            security.OpenProcessToken(
                kernel.GetCurrentProcess(), 8, ctypes.byref(token)
            )
        ),
        "private_work_user_scope",
    )
    try:
        size = wintypes.DWORD()
        need(
            not security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            and ctypes.get_last_error() == 122
            and 0 < size.value <= 4096,
            "private_work_user_scope",
        )
        raw = ctypes.create_string_buffer(size.value)
        need(
            bool(
                security.GetTokenInformation(
                    token, 1, raw, size.value, ctypes.byref(size)
                )
            ),
            "private_work_user_scope",
        )
        return sid_text(kernel, security, _SidAndAttributes.from_buffer(raw).Sid)
    finally:
        kernel.CloseHandle(token)


def verify_acl(kernel, security, path, user):
    size = wintypes.DWORD()
    need(
        not security.GetFileSecurityW(
            str(path), OWNER_AND_DACL, None, 0, ctypes.byref(size)
        )
        and ctypes.get_last_error() == 122
        and 0 < size.value <= 4096,
        "private_work_acl_size",
    )
    raw = ctypes.create_string_buffer(size.value)
    need(
        bool(
            security.GetFileSecurityW(
                str(path), OWNER_AND_DACL, raw, size.value, ctypes.byref(size)
            )
        ),
        "private_work_acl_readback",
    )
    control, revision = wintypes.WORD(), wintypes.DWORD()
    owner, dacl = wintypes.LPVOID(), wintypes.LPVOID()
    present, defaulted = wintypes.BOOL(), wintypes.BOOL()
    need(
        bool(
            security.GetSecurityDescriptorControl(
                raw, ctypes.byref(control), ctypes.byref(revision)
            )
        )
        and bool(control.value & SE_DACL_PROTECTED)
        and bool(
            security.GetSecurityDescriptorOwner(
                raw, ctypes.byref(owner), ctypes.byref(defaulted)
            )
        )
        and sid_text(kernel, security, owner) == user
        and bool(
            security.GetSecurityDescriptorDacl(
                raw, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
            )
        )
        and bool(present.value)
        and bool(dacl.value)
        and not defaulted.value,
        "private_work_acl_scope",
    )
    size_info = _AclSizeInformation()
    need(
        bool(
            security.GetAclInformation(
                dacl, ctypes.byref(size_info), ctypes.sizeof(size_info), 2
            )
        )
        and size_info.AceCount == 2,
        "private_work_ace_inventory",
    )
    observed = set()
    for index in range(2):
        pointer = wintypes.LPVOID()
        need(
            bool(security.GetAce(dacl, index, ctypes.byref(pointer)))
            and bool(pointer.value),
            "private_work_ace_identity",
        )
        ace = _AllowedAce.from_address(pointer.value)
        need(
            ace.Header.AceType == 0
            and ace.Header.AceFlags == 3
            and ace.Mask == FILE_ALL_ACCESS
            and ace.Header.AceSize >= ctypes.sizeof(_AllowedAce),
            "private_work_ace_authority",
        )
        observed.add(
            sid_text(kernel, security, pointer.value + _AllowedAce.SidStart.offset)
        )
    need(observed == {user, "S-1-5-18"}, "private_work_ace_authority")


def create_private_work():
    need(sys.platform == "win32", "private_work_platform")
    base = (Path.home() / "AppData/Local").absolute()
    info = base.lstat()
    need(
        base.resolve(strict=True) == base
        and base.is_dir()
        and not info.st_file_attributes & 1024,
        "private_work_parent_identity",
    )
    kernel, security = api()
    filesystem = ctypes.create_unicode_buffer(32)
    need(
        kernel.GetDriveTypeW(base.anchor) == 3
        and bool(
            kernel.GetVolumeInformationW(
                base.anchor, None, 0, None, None, None, filesystem, len(filesystem)
            )
        )
        and filesystem.value == "NTFS",
        "private_work_volume_identity",
    )
    user = current_user(kernel, security)
    need(user != "S-1-5-18", "private_work_user_scope")
    leaf = "ctcc-analysis-" + uuid4().hex
    path = base / leaf
    need(
        LEAF.fullmatch(leaf) is not None
        and path.parent == base
        and len(str(path)) < 240,
        "private_work_path_scope",
    )
    descriptor = wintypes.LPVOID()
    sddl = f"O:{user}D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{user})"
    need(
        bool(
            security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None
            )
        ),
        "private_work_descriptor_unavailable",
    )
    try:
        attributes = _SecurityAttributes(
            ctypes.sizeof(_SecurityAttributes), descriptor, False
        )
        need(
            bool(kernel.CreateDirectoryW(str(path), ctypes.byref(attributes))),
            "private_work_create_refused",
        )
    finally:
        kernel.LocalFree(descriptor)
    actual = path.lstat()
    need(
        path.resolve(strict=True) == path
        and path.is_dir()
        and not actual.st_file_attributes & 1024
        and not any(path.iterdir()),
        "private_work_created_identity",
    )
    verify_acl(kernel, security, path, user)
    return path
