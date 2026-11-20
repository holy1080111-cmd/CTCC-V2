"""Synthetic source-boundary tampering checks; no Docker/DB execution."""

import io
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import manifest
from scripts import run_db0058_migration_probe_v1 as probe

ROOT = Path(__file__).resolve().parents[2]


def _archive(tmp_path, *, change=None):
    paths = {
        path.relative_to(ROOT).as_posix(): path.read_bytes()
        for path in manifest.source_files(ROOT)
    }
    paths["MANIFEST.sha256"] = (ROOT / "MANIFEST.sha256").read_bytes()
    if change == "migration":
        paths[probe.pins.MIGRATION_PATH] += (
            b"\n# DECLARED_SYNTHETIC_CHANGED_MIGRATION\n"
        )
    elif change == "runner":
        paths[probe.RUNNER] += b"\n# DECLARED_SYNTHETIC_CHANGED_RUNNER\n"
    elif change == "private_env":
        paths[".env"] = b"DECLARED_SYNTHETIC_DATA=1\n"
    elif change == "extra_migration":
        paths["migrations/versions/9999_declared_synthetic.py"] = b"# synthetic\n"
    output = io.BytesIO()
    with tarfile.open(
        fileobj=output, mode="w", pax_headers={"comment": "a" * 40}
    ) as stream:
        for name, raw in sorted(paths.items()):
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mode = 0o644
            stream.addfile(info, io.BytesIO(raw))
    raw = output.getvalue()
    with tarfile.open(fileobj=io.BytesIO(raw)) as stream:
        tree = probe.shared.archive_tree(stream)
    path = tmp_path / "DECLARED_SYNTHETIC_SOURCE.tar"
    path.write_bytes(raw)
    return SimpleNamespace(
        archive=path, archive_sha256=probe.sha(raw), tree=tree, commit="a" * 40
    )


def test_probe_admits_actual58_prefix_without_claiming_full_acceptance(tmp_path):
    args = _archive(tmp_path)
    raw, files = probe.admit_archive(args)
    assert probe.sha(raw) == args.archive_sha256
    assert files[probe.pins.MIGRATION_PATH]["sha256"] == probe.pins.MIGRATION_SHA256
    assert len(probe.REQUIRED_CASES) == 28
    assert len(probe.REQUIRED_FINANCIAL_CASES) == 11


@pytest.mark.parametrize("change", ["migration", "runner", "extra_migration"])
def test_rehashed_source_changes_cannot_become_migration_proof(tmp_path, change):
    args = _archive(tmp_path, change=change)
    with pytest.raises(ValueError):
        probe.admit_archive(args)


def test_private_environment_is_rejected_by_the_shared_archive_reader(tmp_path):
    with pytest.raises(ValueError, match="^archive_non_source_entry$"):
        _archive(tmp_path, change="private_env")


@pytest.mark.parametrize("field", ["tree", "commit", "archive_sha256"])
def test_probe_rejects_changed_external_source_pins(tmp_path, field):
    args = _archive(tmp_path)
    setattr(args, field, "f" * (40 if field in ("tree", "commit") else 64))
    with pytest.raises(ValueError):
        probe.admit_archive(args)


@pytest.mark.parametrize("omitted", (None, *range(4, 13), *range(17, 28)))
def test_parameterized_caps_each_require_their_own_passed_junit_case(tmp_path, omitted):
    cases = tuple(
        case for index, case in enumerate(probe.REQUIRED_CASES) if index != omitted
    )
    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root, "testsuite", tests=str(len(cases)), failures="0", errors="0", skipped="0"
    )
    for module, name in cases:
        ET.SubElement(suite, "testcase", classname=module, name=name)
    path = tmp_path / "DECLARED_SYNTHETIC_CAP_RESULTS.xml"
    path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    if omitted is None:
        checked = probe.shared.verify_pytest_report(
            path, required_cases=probe.REQUIRED_CASES
        )
        assert checked["tests"] == checked["passed"] == 28
    else:
        # All retained cases pass; a missing single NULL/NaN/cap case still fails.
        with pytest.raises(
            ValueError, match="^pytest_required_execution_not_verified$"
        ):
            probe.shared.verify_pytest_report(path, required_cases=probe.REQUIRED_CASES)
