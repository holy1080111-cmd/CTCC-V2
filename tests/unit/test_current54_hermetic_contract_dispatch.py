"""Offline dispatch/archive denial mechanics; no schema or account admission."""

import hashlib
import importlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import current0054_union_archive_v1 as archived
from scripts import current_hermetic_contract_dispatch_v1 as dispatch
from scripts import verify_final_hermetic as generic


def _archive(path, head, *, commit="a" * 40):
    with tarfile.open(
        path, "w", format=tarfile.PAX_FORMAT, pax_headers={"comment": commit}
    ) as stream:
        raw = b"DECLARED_SYNTHETIC_SOURCE_ONLY\n"
        member = tarfile.TarInfo(f"migrations/versions/{head}_declared_synthetic.py")
        member.size = len(raw)
        member.mode = 0o644
        stream.addfile(member, io.BytesIO(raw))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_changed_archive_denies_before_importing_a_new_runner(tmp_path):
    path = tmp_path / "source.tar"
    _archive(path, "0054")
    with pytest.raises(
        ValueError, match="^hermetic_contract_dispatch_archive_changed$"
    ):
        dispatch.maybe_dispatch_hermetic(
            ["--archive", str(path), "--archive-sha256", "0" * 64]
        )


@pytest.mark.parametrize("head", ["0054", "0055"])
@pytest.mark.parametrize("inline", [False, True])
def test_reviewed_head_routes_to_matching_runner_and_preserves_its_rejection(
    tmp_path, monkeypatch, head, inline
):
    path = tmp_path / "source.tar"
    pin = _archive(path, head)
    admitted = path.read_bytes()
    replacement = tmp_path / "replacement.tar"
    _archive(replacement, "0054" if head == "0055" else "0055")
    replacement_bytes = replacement.read_bytes()
    read_bytes = Path.read_bytes
    calls = []

    def replace_after_read(candidate):
        raw = read_bytes(candidate)
        if candidate == path:
            path.write_bytes(replacement_bytes)
        return raw

    def reject(*, archive_bytes):
        calls.append(archive_bytes)
        raise ValueError("DECLARED_MATCHED_RUNNER_REJECTION")

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    monkeypatch.setitem(
        sys.modules,
        f"scripts.verify_current{head}_hermetic",
        SimpleNamespace(main=reject),
    )
    argv = (
        [
            f"--archive={path}",
            f"--archive-sha256={pin}",
            "--suite=postgres",
            "--postgres-component-index=1",
        ]
        if inline
        else [
            "--archive",
            str(path),
            "--archive-sha256",
            pin,
            "--suite",
            "shard",
            "--shard-index",
            "7",
        ]
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_final_hermetic.py",
            *argv,
            "--commit",
            "a" * 40,
            "--tree",
            "b" * 40,
            "--output",
            str(tmp_path / "results"),
        ],
    )
    with pytest.raises(ValueError, match="^DECLARED_MATCHED_RUNNER_REJECTION$"):
        generic.main()
    assert calls == [admitted]
    assert read_bytes(path) == replacement_bytes


def test_unknown_archive_head_is_rejected_before_runner_selection(tmp_path):
    path = tmp_path / "source.tar"
    pin = _archive(path, "0099")
    with pytest.raises(
        ValueError, match="^hermetic_contract_dispatch_migration_unsupported$"
    ):
        dispatch.maybe_dispatch_hermetic(
            ["--archive", str(path), "--archive-sha256", pin]
        )


def test_historical52_routes_to_unchanged_historical_runner(tmp_path):
    path = tmp_path / "source.tar"
    pin = _archive(path, "0052")
    assert (
        dispatch.maybe_dispatch_hermetic(
            ["--archive", str(path), "--archive-sha256", pin]
        )
        is None
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["--archive"],
        ["--archive="],
        ["--archive", "one", "--archive", "two"],
        ["--archive=one", "--archive", "two"],
    ],
)
def test_ambiguous_archive_arguments_are_denied(argv):
    with pytest.raises(
        ValueError, match="^hermetic_contract_dispatch_argument_invalid$"
    ):
        dispatch.maybe_dispatch_hermetic(argv)


def test_generic_rejects_mixed_duplicate_archive_options_before_capture(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.tar"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_final_hermetic.py",
            "--archive",
            str(path),
            f"--archive={path}",
            "--archive-sha256",
            "a" * 64,
            "--commit",
            "a" * 40,
            "--tree",
            "b" * 40,
            "--output",
            str(tmp_path / "results"),
        ],
    )

    def unexpected_capture(*_args, **_kwargs):
        pytest.fail("duplicate archive arguments reached capture")

    monkeypatch.setattr(dispatch, "capture_hermetic_archive", unexpected_capture)
    with pytest.raises(
        ValueError, match="^hermetic_contract_dispatch_argument_invalid$"
    ):
        generic.main()


@pytest.mark.parametrize(
    "runner_name",
    [
        "verify_final_hermetic",
        "verify_current0054_hermetic",
        "verify_current0055_hermetic",
    ],
)
def test_archive_pin_abbreviation_refuses_before_build(
    tmp_path, monkeypatch, runner_name
):
    runner = importlib.import_module("scripts." + runner_name)
    archive_path = tmp_path / "source.tar"
    pin = _archive(archive_path, "0055")
    output = tmp_path / "results"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            runner_name,
            f"--archive={archive_path}",
            f"--archive-sh={pin}",
            "--commit",
            "a" * 40,
            "--tree",
            "b" * 40,
            "--output",
            str(output),
        ],
    )

    def unexpected_build(*_args, **_kwargs):
        pytest.fail("abbreviated archive pin reached a subprocess")

    monkeypatch.setattr(subprocess, "run", unexpected_build)
    with pytest.raises(SystemExit) as refused:
        runner.main()
    assert refused.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize("changed", ["archive_sha256", "commit", "tree", "file_map"])
def test_union_rejects_unadmitted_archive_before_catalog_replay(
    tmp_path, monkeypatch, changed
):
    path = tmp_path / "source.tar"
    pin = _archive(path, "0054")
    with tarfile.open(path) as stream:
        tree = archived.archive_tree(stream)
        files = archived.archive_files(stream)
    identity = {"archive_sha256": pin, "commit": "a" * 40, "tree": tree}
    if changed == "file_map":
        files = {}
    else:
        identity[changed] = "0" * (64 if changed == "archive_sha256" else 40)
    called = []
    monkeypatch.setattr(
        archived.current54,
        "verify_current0054_owner_capture",
        lambda *_: called.append(True),
    )
    with pytest.raises(
        ValueError, match="^current54_union_archive_(changed|identity_changed)$"
    ):
        archived.verify_archived_current54_capture(path, identity, files, {})
    assert called == []


@pytest.mark.parametrize("head", ["0054", "0055"])
@pytest.mark.parametrize("markers", ["removed", "relabeled52"])
def test_union_cannot_fall_back_to52_by_changing_identity_markers(
    tmp_path, monkeypatch, head, markers
):
    artifacts = tmp_path / "artifacts"
    attachment = artifacts / "DECLARED_SYNTHETIC_ATTACHMENT"
    evidence = attachment / "ctcc-validation"
    evidence.mkdir(parents=True)
    path = attachment / "ctcc-source.tar"
    pin = _archive(path, head)
    with tarfile.open(path) as stream:
        tree = archived.archive_tree(stream)
    identity = {"archive_sha256": pin}
    if markers == "relabeled52":
        identity.update(
            runner_contract="ctcc.LEGACY52",
            source_contract_head="0052",
            migration={"source_head": "0052"},
        )
    (evidence / "identity.json").write_text(json.dumps(identity))
    calls = []

    def matched_union():
        calls.append(head)
        raise ValueError("DECLARED_MATCHED_UNION_REJECTION")

    monkeypatch.setitem(
        sys.modules,
        f"scripts.verify_current{head}_linux_union",
        SimpleNamespace(main=matched_union),
    )
    with pytest.raises(ValueError, match="^DECLARED_MATCHED_UNION_REJECTION$"):
        dispatch.maybe_dispatch_union(
            ["--artifacts", str(artifacts), "--commit", "a" * 40, "--tree", tree]
        )
    assert calls == [head]


def test_union_rejects_replaced52_archive_with_current54_claimed_tree(
    tmp_path, monkeypatch
):
    artifacts = tmp_path / "artifacts"
    attachment = artifacts / "DECLARED_SYNTHETIC_ATTACHMENT"
    evidence = attachment / "ctcc-validation"
    evidence.mkdir(parents=True)
    current = tmp_path / "current54.tar"
    _archive(current, "0054")
    with tarfile.open(current) as stream:
        current_tree = archived.archive_tree(stream)
    path = attachment / "ctcc-source.tar"
    pin = _archive(path, "0052")
    (evidence / "identity.json").write_text(
        json.dumps({"archive_sha256": pin, "source_contract_head": "0052"})
    )
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "scripts.verify_current0054_linux_union",
        SimpleNamespace(main=lambda: calls.append(True)),
    )
    with pytest.raises(
        ValueError, match="^hermetic_contract_dispatch_union_archive_identity_changed$"
    ):
        dispatch.maybe_dispatch_union(
            [
                "--artifacts",
                str(artifacts),
                "--commit",
                "a" * 40,
                "--tree",
                current_tree,
            ]
        )
    assert calls == []


def test_genuine52_union_archive_retains_the_historical_route(tmp_path):
    artifacts = tmp_path / "artifacts"
    attachment = artifacts / "DECLARED_SYNTHETIC_ATTACHMENT"
    evidence = attachment / "ctcc-validation"
    evidence.mkdir(parents=True)
    path = attachment / "ctcc-source.tar"
    pin = _archive(path, "0052")
    with tarfile.open(path) as stream:
        tree = archived.archive_tree(stream)
    (evidence / "identity.json").write_text(json.dumps({"archive_sha256": pin}))
    assert (
        dispatch.maybe_dispatch_union(
            ["--artifacts", str(artifacts), "--commit", "a" * 40, "--tree", tree]
        )
        is None
    )
