"""Verify that the native startup reference inherits OS custody."""

from __future__ import annotations

import io
import os
import stat
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core import native_startup_trust_0056_v1 as subject
from app.domain.source_primitives import canonical, sha


def _document():
    return {
        "schema": "ctcc.native_only.db0056_admin_startup_trust.v1",
        "database_head": "0056",
        "startup_mode": "read_only_control_monitoring",
        "descriptor_sha256": "1" * 64,
        "source_commit": "a" * 40,
        "source_tree": "b" * 40,
        "source_archive_sha256": "2" * 64,
        "source_manifest_sha256": "3" * 64,
        "tls_ca_sha256": "4" * 64,
        "restricted_role_identity_sha256": "5" * 64,
        "inherited_native_definitions_sha256": "6" * 64,
        "granted_inherited_acl_shape_sha256": "7" * 64,
        "reviewed_portable_reference_sha256": "8" * 64,
    }


def _status():
    return (
        b"CapPrm:\t"
        + b"0" * 16
        + b"\nCapEff:\t"
        + b"0" * 16
        + b"\nCapAmb:\t"
        + b"0" * 16
    )


@pytest.fixture
def fs(monkeypatch):
    raw = canonical(_document())
    paths = {
        (None, "/"): 100,
        (100, "run"): 101,
        (101, "ctcc-native"): 102,
        (102, "0056-startup-trust.json"): 103,
    }
    metadata = {
        fd: SimpleNamespace(
            st_mode=stat.S_IFREG | 0o444 if fd == 103 else stat.S_IFDIR | 0o755,
            st_uid=0,
            st_gid=0,
            st_nlink=1,
            st_dev=1,
            st_ino=fd,
            st_size=len(raw),
            st_mtime_ns=1,
            st_ctime_ns=1,
        )
        for fd in paths.values()
    }

    def acquire(path, flags, *, dir_fd=None):
        observed = paths[dir_fd, path]
        if stat.S_ISLNK(metadata[observed].st_mode) and flags & 8:
            raise OSError("internal private path")
        return observed

    stream = io.BytesIO(raw)
    system = SimpleNamespace(
        name="posix",
        getuid=Mock(return_value=3001),
        geteuid=Mock(return_value=3001),
        getresuid=Mock(return_value=(3001, 3001, 3001)),
        O_RDONLY=0,
        O_DIRECTORY=2,
        O_CLOEXEC=4,
        O_NOFOLLOW=8,
        O_NONBLOCK=16,
        W_OK=2,
        open=Mock(side_effect=acquire),
        close=Mock(),
        fstat=Mock(side_effect=metadata.__getitem__),
        stat=Mock(
            side_effect=lambda name, *, dir_fd=None, follow_symlinks: metadata[
                paths[dir_fd, name]
            ]
        ),
        access=Mock(return_value=False),
        fdopen=Mock(return_value=stream),
    )
    monkeypatch.setattr(subject, "os", system)
    monkeypatch.setattr(subject, "_LINUX", True)
    monkeypatch.setattr(subject, "_linux_status", _status)
    return system, metadata, stream


def test_exact_admin_reference_and_safe_resource_cleanup(fs):
    system, _, stream = fs
    ref = subject.read_native_startup_trust_0056_v1()
    for key, value in _document().items():
        if key != "schema":
            assert getattr(ref, key) == value
    assert ref.trust_sha256 == sha(canonical(_document()))
    with pytest.raises(FrozenInstanceError):
        ref.source_commit = "d" * 40
    assert [call.args for call in system.close.call_args_list] == [
        (103,),
        (102,),
        (101,),
        (100,),
    ]
    assert all(call.args[1] & system.O_NOFOLLOW for call in system.open.call_args_list)
    assert stream.closed


@pytest.mark.parametrize(
    ("fd", "field", "value"),
    [
        (100, "st_mode", stat.S_IFDIR | 0o775),
        (101, "st_mode", stat.S_IFLNK | 0o777),
        (101, "st_mode", stat.S_IFDIR | 0o777),
        (102, "st_uid", 3001),
        (102, "st_mode", stat.S_IFDIR | 0o757),
        (103, "st_mode", stat.S_IFLNK | 0o777),
        (103, "st_mode", stat.S_IFIFO | 0o444),
        (103, "st_mode", stat.S_IFREG | 0o644),
        (103, "st_uid", 3001),
        (103, "st_nlink", 2),
        (103, "st_size", 4097),
    ],
)
def test_refuses_unsafe_ancestry_or_leaf(fs, fd, field, value):
    system, metadata, _ = fs
    setattr(metadata[fd], field, value)
    with pytest.raises(
        subject.NativeStartupTrust0056Error, match="^native_startup_trust_0056_refused$"
    ):
        subject.read_native_startup_trust_0056_v1()
    assert system.close.call_args_list[-1].args == (100,)
    system.fdopen.assert_not_called()


@pytest.mark.parametrize("path", ["/", "run", "ctcc-native", "0056-startup-trust.json"])
def test_effective_filesystem_write_access_is_refused_even_with_safe_mode(fs, path):
    system, _, _ = fs
    system.access.side_effect = lambda name, *_, **__: name == path
    with pytest.raises(subject.NativeStartupTrust0056Error):
        subject.read_native_startup_trust_0056_v1()
    assert system.close.call_args_list[-1].args == (100,)


def test_rejects_path_replacement_after_descriptor_is_opened(fs):
    system, metadata, stream = fs
    readback = system.stat.side_effect
    system.stat.side_effect = lambda path, **kwargs: (
        SimpleNamespace(**{**vars(metadata[103]), "st_ino": 104})
        if path == "0056-startup-trust.json" and stream.closed
        else readback(path, **kwargs)
    )
    with pytest.raises(subject.NativeStartupTrust0056Error):
        subject.read_native_startup_trust_0056_v1()
    assert stream.closed
    assert system.close.call_args_list[-1].args == (100,)


@pytest.mark.parametrize(
    "defect",
    ["windows", "root", "effective_root", "saved_root", "capability", "missing_proc"],
)
def test_refuses_process_with_administrative_authority_before_opening(
    fs, monkeypatch, defect
):
    system, _, _ = fs
    if defect == "windows":
        system.name = "nt"
    elif defect == "root":
        system.getuid.return_value = 0
    elif defect == "effective_root":
        system.geteuid.return_value = 0
    elif defect == "saved_root":
        system.getresuid.return_value = (3001, 3001, 0)
    elif defect == "capability":
        monkeypatch.setattr(
            subject,
            "_linux_status",
            lambda: _status().replace(b"0\nCapAmb", b"2\nCapAmb"),
        )
    else:
        monkeypatch.setattr(subject, "_linux_status", lambda: b"")
    with pytest.raises(subject.NativeStartupTrust0056Error):
        subject.read_native_startup_trust_0056_v1()
    system.open.assert_not_called()


@pytest.mark.parametrize(
    "defect",
    [
        "unknown_key",
        "missing_key",
        "write_mode",
        "version",
        "uppercase",
        "append",
        "duplicate",
        "sha512",
    ],
)
def test_refuses_malformed_or_elevated_reference(fs, defect):
    system, metadata, _ = fs
    document = _document()
    if defect == "unknown_key":
        document["execution_authority"] = False
    elif defect == "missing_key":
        del document["tls_ca_sha256"]
    elif defect == "write_mode":
        document["startup_mode"] = "allow_exchange_writes"
    elif defect == "version":
        document["database_head"] = 56
    elif defect == "uppercase":
        document["source_tree"] = "A" * 40
    elif defect == "sha512":
        document["descriptor_sha256"] = "a" * 128
    raw = canonical(document)
    if defect == "append":
        raw += b"\n"
    elif defect == "duplicate":
        raw = b'{"database_head":"0056",' + raw[1:]
    stream = io.BytesIO(raw)
    metadata[103].st_size = len(raw)
    system.fdopen.return_value = stream
    with pytest.raises(
        subject.NativeStartupTrust0056Error, match="^native_startup_trust_0056_refused$"
    ):
        subject.read_native_startup_trust_0056_v1()
    assert stream.closed


@pytest.mark.skipif(
    sys.platform != "linux" or getattr(os, "geteuid", lambda: -1)() != 0,
    reason="requires Linux with an administrator to prepare a disposable filesystem",
)
def test_real_linux_directory_descriptors_with_nonroot_child(tmp_path):
    # The child uses an isolated root and the actual fixed path. No production
    # reference, permissions, mounts, or credentials are changed.
    tmp_path.chmod(0o755)
    root_path = tmp_path / "run" / "ctcc-native"
    root_path.mkdir(parents=True, mode=0o755)
    leaf = root_path / "0056-startup-trust.json"
    leaf.write_bytes(canonical(_document()))
    leaf.chmod(0o444)
    child = """
import os
import sys
from app.core import native_startup_trust_0056_v1 as subject
from app.domain.source_primitives import sha

with open("/proc/self/status", "rb") as status:
    def linux_status():
        status.seek(0)
        return status.read(16385)

    os.chroot(sys.argv[1])
    os.chdir("/")
    os.setgroups([])
    os.setresgid(65534, 65534, 65534)
    os.setresuid(65534, 65534, 65534)
    subject._linux_status = linux_status  # Read the same kernel file after chroot.
    assert subject.read_native_startup_trust_0056_v1().trust_sha256 == sys.argv[2]
    for path in ("/", "/run", "/run/ctcc-native", "/run/ctcc-native/0056-startup-trust.json"):
        assert not os.access(path, os.W_OK)
"""
    subprocess.run(
        [sys.executable, "-B", "-c", child, str(tmp_path), sha(leaf.read_bytes())],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        timeout=10,
    )
