"""Private temporary files only; no runtime key or source authority is installed."""

import ctypes
import os

import pytest

from app.domain.source_primitives import canonical, sha
from app.okx_live import lifecycle_custody_v1 as custody
from app.okx_live import lifecycle_key_setup_v1 as setup
from app.okx_live import lifecycle_key_v1 as key_source
from tests.unit.test_native_live_lifecycle_foundation_v1 import _key


def _private_root_path(tmp_path):
    """Only this pytest-owned child receives a private, inheritable Windows DACL."""
    root = tmp_path / "lifecycle-private"
    root.mkdir(mode=0o700)
    assert root.parent == tmp_path and root.is_dir()
    if os.name != "nt":
        return root
    from ctypes import wintypes as w

    from app.trade_evidence.native_private_root_v2 import _windows_token_user

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    sid_buffer, sid = _windows_token_user(adv, kernel)
    pointer = ctypes.c_void_p
    adv.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(pointer)]
    adv.ConvertSidToStringSidW.restype = w.BOOL
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    sid_string = pointer()
    assert sid_buffer and adv.ConvertSidToStringSidW(sid, ctypes.byref(sid_string))
    try:
        owner = ctypes.wstring_at(sid_string)
    finally:
        kernel.LocalFree(sid_string)
    sddl = f"O:{owner}D:P(A;OICI;FA;;;{owner})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        w.LPCWSTR,
        w.DWORD,
        ctypes.POINTER(pointer),
        ctypes.POINTER(w.DWORD),
    ]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    adv.GetSecurityDescriptorDacl.argtypes = [
        pointer,
        ctypes.POINTER(w.BOOL),
        ctypes.POINTER(pointer),
        ctypes.POINTER(w.BOOL),
    ]
    adv.GetSecurityDescriptorDacl.restype = w.BOOL
    adv.GetSecurityDescriptorOwner.argtypes = [
        pointer,
        ctypes.POINTER(pointer),
        ctypes.POINTER(w.BOOL),
    ]
    adv.GetSecurityDescriptorOwner.restype = w.BOOL
    adv.SetNamedSecurityInfoW.argtypes = [
        w.LPWSTR,
        w.DWORD,
        w.DWORD,
        pointer,
        pointer,
        pointer,
        pointer,
    ]
    adv.SetNamedSecurityInfoW.restype = w.DWORD
    descriptor = pointer()
    assert adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    )
    try:
        present, defaulted = w.BOOL(), w.BOOL()
        dacl, owner_pointer = pointer(), pointer()
        assert (
            adv.GetSecurityDescriptorDacl(
                descriptor,
                ctypes.byref(present),
                ctypes.byref(dacl),
                ctypes.byref(defaulted),
            )
            and present.value
            and dacl.value
        )
        assert adv.GetSecurityDescriptorOwner(
            descriptor, ctypes.byref(owner_pointer), ctypes.byref(defaulted)
        )
        assert (
            adv.SetNamedSecurityInfoW(
                str(root), 1, 0x80000005, owner_pointer, None, dacl, None
            )
            == 0
        )
    finally:
        kernel.LocalFree(descriptor)
    return root


def test_content_addressed_private_journal_is_idempotent_and_read_back(tmp_path):
    root = _private_root_path(tmp_path)
    raw = canonical({"declared_synthetic_data": True})
    path = custody.publish_live_lifecycle_blob_v1(root, raw, domain="proofs")
    assert path.name == sha(raw) + ".json" and path.read_bytes() == raw
    assert custody.publish_live_lifecycle_blob_v1(root, raw, domain="proofs") == path
    assert custody.read_live_lifecycle_blob_v1(root, sha(raw), domain="proofs") == raw


def test_late_journal_readback_rejection_preserves_accepted_file(tmp_path, monkeypatch):
    root = _private_root_path(tmp_path)
    raw = canonical({"declared_synthetic_data": True})
    original = custody._read

    def reject(*args):
        raise custody.LiveLifecycleCustodyError("synthetic_late_readback_rejection")

    monkeypatch.setattr(custody, "_read", reject)
    with pytest.raises(custody.LiveLifecycleCustodyError):
        custody.publish_live_lifecycle_blob_v1(root, raw, domain="proofs")
    path = root / "live-lifecycle-v1" / "proofs" / (sha(raw) + ".json")
    assert path.read_bytes() == raw
    monkeypatch.setattr(custody, "_read", original)
    assert custody.read_live_lifecycle_blob_v1(root, sha(raw), domain="proofs") == raw


def test_changed_existing_journal_is_never_repaired_or_overwritten(tmp_path):
    root = _private_root_path(tmp_path)
    raw = canonical({"declared_synthetic_data": True})
    path = custody.publish_live_lifecycle_blob_v1(root, raw, domain="proofs")
    path.write_bytes(b"changed")
    with pytest.raises(custody.LiveLifecycleCustodyError):
        custody.publish_live_lifecycle_blob_v1(root, raw, domain="proofs")
    assert path.read_bytes() == b"changed"


def test_key_file_creation_flushes_and_refuses_overwrite(tmp_path):
    root = _private_root_path(tmp_path)
    path = root / "synthetic-journal-key.json"
    raw = canonical(_key())
    setup._create_file(path, raw)
    assert setup._existing_file(path) == (raw, _key())
    with pytest.raises((OSError, ValueError)):
        setup._create_file(path, canonical({**_key(), "key_hex": "ff" * 32}))
    assert path.read_bytes() == raw


def test_key_file_hardlink_is_refused(tmp_path):
    root = _private_root_path(tmp_path)
    path = root / "synthetic-journal-key.json"
    setup._create_file(path, canonical(_key()))
    os.link(path, root / "unexpected-link.json")
    with pytest.raises(key_source.LiveLifecycleKeyError):
        key_source._private_file(path)


@pytest.mark.parametrize("domain", ("../proofs", "keys", "", "proofs/other"))
def test_unknown_or_traversing_journal_domains_are_rejected(tmp_path, domain):
    root = _private_root_path(tmp_path)
    with pytest.raises(custody.LiveLifecycleCustodyError):
        custody.publish_live_lifecycle_blob_v1(root, b"{}", domain=domain)
