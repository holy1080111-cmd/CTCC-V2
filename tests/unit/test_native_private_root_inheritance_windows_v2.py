"""Windows native evidence children inherit only a verified private parent ACL.

The DACL changes below are confined to pytest-owned temporary directories.
No database, exchange client, credential, or order route is used.
"""

from __future__ import annotations

import ctypes
import os
import uuid
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.demo_automation import native_caller_v2, native_source_only_v1
from app.demo_automation.native_private_root_v2 import (
    NativeEvidenceRootErrorV2,
    mkdir_private_evidence_child_v2,
    require_private_evidence_base_v2,
)
from app.trade_qualification import account_native_proof_storage as storage

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows DACL contract")


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


def _set_protected_directory_dacl(path: Path, *, extra_users: bool = False) -> None:
    """Protect one disposable directory with inheritable owner/SYSTEM/Admin ACEs."""
    owner_sid = _owner_sid_string()
    entries = f"(A;OICI;FA;;;{owner_sid})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
    if extra_users:
        entries += "(A;OICI;FR;;;BU)"
    sddl = "D:P" + entries
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
        # SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION.
        status = adv.SetNamedSecurityInfoW(
            str(path),
            1,
            0x00000004 | 0x80000000,
            None,
            None,
            dacl,
            None,
        )
        if status:
            raise ctypes.WinError(status)
    finally:
        kernel.LocalFree(descriptor)


def _protected_base(tmp_path: Path) -> Path:
    base = tmp_path / "native-private"
    base.mkdir()
    _set_protected_directory_dacl(base)
    assert require_private_evidence_base_v2(str(base)) == base
    return base


def test_source_only_children_inherit_verified_private_acl(tmp_path):
    base = _protected_base(tmp_path)
    roots = native_source_only_v1._roots(
        SimpleNamespace(okx_demo_native_evidence_root=str(base))
    )
    assert set(roots) == set(native_source_only_v1._ROOT_NAMES)
    attempt = next(iter(roots.values())).parent
    assert attempt.parent == base
    assert require_private_evidence_base_v2(str(attempt)) == attempt
    assert {path.parent for path in roots.values()} == {attempt}
    assert len({storage._root_identity(path) for path in roots.values()}) == len(roots)
    for path in roots.values():
        assert require_private_evidence_base_v2(str(path)) == path
        with storage._root_context(path) as directory:
            assert not directory.names()


def test_native_caller_children_inherit_verified_private_acl_and_no_clobber(tmp_path):
    base = _protected_base(tmp_path)
    invocation = "n121-" + uuid.uuid4().hex
    roots = native_caller_v2._roots(
        SimpleNamespace(okx_demo_native_evidence_root=str(base)), invocation
    )
    assert set(roots) == set(native_caller_v2._ROOT_NAMES)
    attempt = base / invocation
    assert require_private_evidence_base_v2(str(attempt)) == attempt
    for path in roots.values():
        assert path.parent == attempt
        assert require_private_evidence_base_v2(str(path)) == path
        with storage._root_context(path) as directory:
            assert not directory.names()
    with pytest.raises(OSError):
        native_caller_v2._roots(
            SimpleNamespace(okx_demo_native_evidence_root=str(base)), invocation
        )
    assert require_private_evidence_base_v2(str(attempt)) == attempt


def test_native_helper_rejects_untrusted_parent_before_child_creation(tmp_path):
    base = _protected_base(tmp_path)
    _set_protected_directory_dacl(base, extra_users=True)
    with (
        storage._root_context(base) as directory,
        pytest.raises(NativeEvidenceRootErrorV2),
    ):
        mkdir_private_evidence_child_v2(directory, "never-created")
    assert not (base / "never-created").exists()


def test_native_validator_rejects_new_unauthorized_child_ace(tmp_path):
    base = _protected_base(tmp_path)
    with storage._root_context(base) as directory:
        mkdir_private_evidence_child_v2(directory, "verified-child")
    child = base / "verified-child"
    assert require_private_evidence_base_v2(str(child)) == child
    _set_protected_directory_dacl(child, extra_users=True)
    with pytest.raises(NativeEvidenceRootErrorV2):
        require_private_evidence_base_v2(str(child))
