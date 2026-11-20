"""Lightweight native private-directory setup for disposable test-owned paths.

Native SID/DACL functions are extracted without an AST/permission change.
No product code imports this module. No ACL is changed during import.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


def _owner_sid_string() -> str:
    """Read only the test process owner SID; never persist account identity."""
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    adv.OpenProcessToken.restype = wintypes.BOOL
    adv.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    adv.GetTokenInformation.restype = wintypes.BOOL
    adv.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    adv.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    token = wintypes.HANDLE()
    if not adv.OpenProcessToken(
        kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        assert 0 < size.value <= 65536
        buffer = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        string = ctypes.c_void_p()
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(string)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.wstring_at(string)
        finally:
            kernel.LocalFree(string)
    finally:
        kernel.CloseHandle(token)


def _set_protected_directory_dacl(
    path: Path, *, extra_users: bool = False, initialize_owner: bool = False
) -> None:
    """Protect one disposable directory with inheritable owner/SYSTEM/Admin ACEs."""
    owner_sid = _owner_sid_string()
    entries = f"(A;OICI;FA;;;{owner_sid})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
    if extra_users:
        entries += "(A;OICI;FR;;;BU)"
    sddl = (f"O:{owner_sid}" if initialize_owner else "") + "D:P" + entries
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    adv.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    adv.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    adv.GetSecurityDescriptorOwner.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    adv.GetSecurityDescriptorOwner.restype = wintypes.BOOL
    adv.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    adv.SetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        dacl = ctypes.c_void_p()
        if (
            not adv.GetSecurityDescriptorDacl(
                descriptor,
                ctypes.byref(present),
                ctypes.byref(dacl),
                ctypes.byref(defaulted),
            )
            or not present.value
            or not dacl.value
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        owner = ctypes.c_void_p()
        # DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION.
        flags = 0x00000004 | 0x80000000
        if initialize_owner:
            if (
                not adv.GetSecurityDescriptorOwner(
                    descriptor, ctypes.byref(owner), ctypes.byref(defaulted)
                )
                or not owner.value
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            flags |= 0x00000001  # OWNER_SECURITY_INFORMATION.
        status = adv.SetNamedSecurityInfoW(
            str(path),
            1,  # SE_FILE_OBJECT.
            flags,
            owner if initialize_owner else None,
            None,
            dacl,
            None,
        )
        if status:
            raise ctypes.WinError(status)
    finally:
        kernel.LocalFree(descriptor)


def _private_root_path(tmp_path):
    root = tmp_path / "private-custody"
    root.mkdir(mode=0o700)
    if os.name == "nt":
        _set_protected_directory_dacl(root, initialize_owner=True)
    return root
