"""Archive admission must reject source substitutions before Docker starts."""

import hashlib
import io
import json
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from scripts.verify_final_hermetic import (
    Run,
    archive_files,
    archive_tree,
    cleanup_resources,
    verify_copied_source,
)


def archive(entries):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as target:
        for name, raw, kind in entries:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.size = len(raw) if kind == tarfile.REGTYPE else 0
            item.linkname = "outside" if kind == tarfile.SYMTYPE else ""
            target.addfile(item, io.BytesIO(raw))
    data.seek(0)
    return tarfile.open(fileobj=data)


@pytest.mark.parametrize(
    "name",
    (
        "../escape",
        "/absolute",
        ".env",
        "config/.env.live",
        ".git/config",
        "reports/private.json",
        "backups/database",
        "config/runtime.token",
        "config/RUNTIME.TOKEN",
        "config/.ENV.live",
        "private-notion/destination.json",
        "PRIVATE-NOTION/nested/data.json",
    ),
)
def test_archive_refuses_non_source_paths(name):
    with (
        archive([(name, b"not-a-secret", tarfile.REGTYPE)]) as value,
        pytest.raises(ValueError),
    ):
        archive_tree(value)


def test_archive_refuses_symlink_and_duplicate_members():
    with (
        archive([("link", b"", tarfile.SYMTYPE)]) as value,
        pytest.raises(ValueError, match="non_source"),
    ):
        archive_tree(value)
    with (
        archive(
            [("a", b"first", tarfile.REGTYPE), ("a", b"second", tarfile.REGTYPE)]
        ) as value,
        pytest.raises(ValueError, match="duplicate"),
    ):
        archive_tree(value)


def test_archive_tree_changes_when_checkout_bytes_change():
    # Windows archive conversion must not masquerade as the original Git tree.
    with archive([("source.py", b"pass\n", tarfile.REGTYPE)]) as original:
        original_tree = archive_tree(original)
    with archive([("source.py", b"pass\r\n", tarfile.REGTYPE)]) as changed:
        assert archive_tree(changed) != original_tree


def test_empty_tree_matches_git_known_empty_tree_identity():
    with archive([]) as value:
        assert archive_tree(value) == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def test_actual_copied_bytes_cannot_rely_on_normalized_manifest(tmp_path):
    with archive([("source.py", b"pass\n", tarfile.REGTYPE)]) as original:
        files = archive_files(original)
    mapping = tmp_path / "source-files.json"
    mapping.write_text(json.dumps(files))
    digest = hashlib.sha256(mapping.read_bytes()).hexdigest()
    source = tmp_path / "source"
    source.mkdir()
    copied = source / "source.py"
    copied.write_bytes(b"pass\n")
    assert verify_copied_source(source, mapping, digest) == 1
    copied.write_bytes(b"pass\r\n")
    with pytest.raises(ValueError, match="copied_source_mismatch"):
        verify_copied_source(source, mapping, digest)
    copied.write_bytes(b"pass\n")
    mapping.write_text("{}")
    with pytest.raises(ValueError, match="source_file_map_digest_mismatch"):
        verify_copied_source(source, mapping, digest)


def test_command_timeout_is_durable_failed_step(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        Run(tmp_path).command("linux-full", ["docker", "run"], timeout=1)
    step = json.loads((tmp_path / "steps.json").read_text())[0]
    assert step["exit_code"] is None
    assert step["failure_type"] == "TimeoutExpired"
    assert step["name"] == "linux-full"


def test_cleanup_reconciles_attempted_owned_start_even_if_ack_was_lost(monkeypatch):
    calls = []

    def docker(args, **kwargs):
        calls.append(args)
        if "ls" in args:
            return SimpleNamespace(stdout="ctcc-final-run-linux-full\n")
        if "inspect" in args:
            return SimpleNamespace(
                stdout=json.dumps(
                    [{"Config": {"Labels": {"org.ctcc.validation.run": "run"}}}]
                )
            )
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(subprocess, "run", docker)
    result = cleanup_resources([("container", "ctcc-final-run-linux-full")], "run")
    assert result[0]["status"] == "REMOVED"
    assert calls[-1] == [
        "docker",
        "container",
        "rm",
        "-f",
        "-v",
        "ctcc-final-run-linux-full",
    ]


def test_cleanup_never_removes_other_owners_resource(monkeypatch):
    calls = []

    def docker(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            stdout="existing\n"
            if "ls" in args
            else json.dumps(
                [{"Config": {"Labels": {"org.ctcc.validation.run": "other"}}}]
            )
        )

    monkeypatch.setattr(subprocess, "run", docker)
    result = cleanup_resources([("container", "existing")], "run")
    assert result[0]["status"] == "FAIL"
    assert all("rm" not in args for args in calls)


def test_cleanup_does_not_treat_daemon_failure_as_resource_absence(monkeypatch):
    def unavailable(args, **kwargs):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(subprocess, "run", unavailable)
    assert cleanup_resources([("container", "new")], "run")[0]["status"] == "FAIL"
