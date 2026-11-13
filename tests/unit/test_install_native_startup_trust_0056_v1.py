"""DB0056 administrator producer and dedicated runtime reader interoperability."""

import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.core import native_startup_trust_0056_v1 as trust
from app.domain.source_primitives import decode, sha
from scripts import install_native_startup_trust_0056_v1 as subject
from scripts import native_deployment_descriptor_0056_v1 as deployment
from tests.unit.test_native_deployment_descriptor_0056_v1 import _descriptor


def test_reference_is_exact_reader_contract_and_excludes_owner_details():
    descriptor = _descriptor()
    digest = sha(descriptor.payload)
    raw = subject._reference_document(descriptor, digest)
    reference = trust._parse(raw)
    assert reference.descriptor_sha256 == digest
    assert reference.source_commit == descriptor.source_commit
    assert reference.source_tree == descriptor.source_tree
    assert (
        reference.restricted_role_identity_sha256
        == descriptor.restricted_role_identity_sha256
    )
    document = decode(raw, trust._LIMIT)
    assert set(document) == {
        "schema",
        "database_head",
        "startup_mode",
        *trust._REFERENCE_FIELDS,
    }
    assert "owner_observed_cluster_sha256" not in document
    assert "owner_grant_plan_sha256" not in document
    with pytest.raises(subject.NativeTrustInstallRefusal):
        subject._reference_document(descriptor, "7" * 64)


@pytest.mark.parametrize("bad", ["owner", "archive", "descriptor", "readback", None])
def test_installer_preserves_existing_boundary_on_failed_check(monkeypatch, bad):
    descriptor = _descriptor()
    digest = sha(descriptor.payload)
    payload = subject._reference_document(descriptor, digest)
    if bad == "owner":
        _error = subject.NativeTrustInstallRefusal("private owner error")
        monkeypatch.setattr(subject, "_require_administrator", Mock(side_effect=_error))
    else:
        monkeypatch.setattr(subject, "_require_administrator", lambda: None)
    monkeypatch.setattr(subject, "_require_review_owned_imports", lambda: None)
    monkeypatch.setattr(subject, "_review_owned_path", lambda *_, **__: None)
    read_descriptor = Mock(return_value=descriptor)
    verify_release = Mock()
    if bad == "archive":
        verify_release.side_effect = ValueError("private archive mismatch")
    elif bad == "descriptor":
        read_descriptor.side_effect = ValueError("private descriptor mismatch")
    monkeypatch.setattr(deployment, "_check_installed_release", verify_release)
    monkeypatch.setattr(deployment, "_read_private_descriptor", read_descriptor)

    @contextmanager
    def destination():
        yield 17

    monkeypatch.setattr(subject, "_destination", destination)
    publish = Mock(return_value=True)
    monkeypatch.setattr(subject, "_publish_reference", publish)
    readback = (
        subject._reference_document(
            replace(descriptor, source_commit="f" * 40),
            sha(replace(descriptor, source_commit="f" * 40).payload),
        )
        if bad == "readback"
        else payload
    )
    monkeypatch.setattr(subject, "_read_reference_at", Mock(return_value=readback))
    arguments = {
        "descriptor_file": Path("descriptor"),
        "descriptor_sha256": digest,
        "source_archive_file": Path("archive"),
    }
    if bad:
        with pytest.raises(ValueError):
            subject.install_native_startup_trust_0056_v1(**arguments)
        if bad == "readback":
            publish.assert_called_once_with(17, payload)
        else:
            publish.assert_not_called()
    else:
        receipt = subject.install_native_startup_trust_0056_v1(**arguments)
        assert receipt["status"] == "INSTALLED"
        assert receipt["trust_sha256"] == sha(payload)
        assert receipt["descriptor_sha256"] == digest
        publish.assert_called_once_with(17, payload)


def test_cli_refusal_hides_input_and_has_no_startup_or_execution_authority(
    capsys, monkeypatch
):
    secret = "hidden descriptor message"
    monkeypatch.setattr(
        subject,
        "install_native_startup_trust_0056_v1",
        Mock(side_effect=RuntimeError(secret)),
    )
    assert (
        subject.main(
            [
                "--descriptor-file",
                "private-descriptor",
                "--descriptor-sha256",
                "7" * 64,
                "--source-archive-file",
                "private-archive",
            ]
        )
        == 1
    )
    content = capsys.readouterr().out
    assert secret not in content and "private-archive" not in content
    receipt = decode(content.encode(), 4096)
    assert receipt["status"] == "REFUSED"
    assert receipt["admission"] == "DENY"
    assert receipt["startup_admission"] is receipt["execution_authority"] is False
    assert receipt["exchange_requests"] == receipt["database_writes"] == 0


@pytest.mark.skipif(
    sys.platform != "linux" or getattr(os, "geteuid", lambda: -1)() != 0,
    reason="requires Linux and administrator to prepare a disposable filesystem",
)
def test_linux_install_is_single_link_and_reopens_as_nonroot_without_replacement(
    tmp_path,
):
    tmp_path.chmod(0o755)
    (tmp_path / "run").mkdir(mode=0o755)
    descriptor = _descriptor()
    expected = subject._reference_document(descriptor, sha(descriptor.payload))
    other = replace(descriptor, restricted_role_identity_sha256="e" * 64)
    conflicting = subject._reference_document(other, sha(other.payload))
    child = """
import os
import sys
import stat
from app.core import native_startup_trust_0056_v1 as trust
from app.domain.source_primitives import sha
from app.trade_evidence.storage import _write_fd
from scripts import install_native_startup_trust_0056_v1 as installer

with open("/proc/self/status", "rb") as status:
    def read_status():
        status.seek(0)
        return status.read(16385)

    installer._require_administrator()
    # Load the Linux C API before changing root. It operates on held fds below.
    library = installer.ctypes.CDLL(None, use_errno=True)
    installer.ctypes.CDLL = lambda *_args, **_kwargs: library
    os.chroot(sys.argv[1])
    os.chdir("/")
    raw, mismatch = (value.encode("ascii") for value in sys.argv[2:])
    with installer._destination() as directory:
        with open("/run/ctcc-native/no-clobber-test", "xb") as existing:
            existing.write(b"old")
        with open("/run/ctcc-native/rename-source-test", "xb") as candidate:
            candidate.write(b"new")
        try:
            installer._rename_no_replace(directory, "rename-source-test", "no-clobber-test")
        except FileExistsError:
            pass
        else:
            raise AssertionError("atomic rename replaced an existing file")
        with open("/run/ctcc-native/no-clobber-test", "rb") as existing:
            assert existing.read() == b"old"
        os.unlink("no-clobber-test", dir_fd=directory)
        os.unlink("rename-source-test", dir_fd=directory)
        assert installer._publish_reference(directory, raw) is True
        assert installer._publish_reference(directory, raw) is False
        try:
            installer._publish_reference(directory, mismatch)
        except installer.NativeTrustInstallRefusal:
            pass
        else:
            raise AssertionError("existing trust was replaced")
        assert installer._read_reference_at(directory, trust._FILENAME) == raw
        assert os.listdir(directory) == [trust._FILENAME]
        info = os.stat(trust._FILENAME, dir_fd=directory, follow_symlinks=False)
        assert info.st_uid == 0
        assert info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o444
    os.setgroups([])
    os.setresgid(65534, 65534, 65534)
    os.setresuid(65534, 65534, 65534)
    trust._linux_status = read_status
    assert trust.read_native_startup_trust_0056_v1().trust_sha256 == sha(raw)
"""
    subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            child,
            str(tmp_path),
            expected.decode("ascii"),
            conflicting.decode("ascii"),
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        timeout=10,
    )
