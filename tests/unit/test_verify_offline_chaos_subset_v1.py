import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from scripts import verify_offline_chaos_subset_v1 as chaos


def test_publisher_reads_back_and_refuses_to_replace_receipt(tmp_path):
    output = tmp_path / "receipt"
    files = {"report.json": b"original", "chaos.xml": b"report"}
    digest = chaos._publish(output, files)
    assert digest == hashlib.sha256(files["report.json"]).hexdigest()
    assert (output / "SHA256SUMS").read_text().splitlines() == [
        f"{hashlib.sha256(raw).hexdigest()}  {name}"
        for name, raw in sorted(files.items())
    ]
    with pytest.raises(FileExistsError):
        chaos._publish(output, {"report.json": b"changed"})
    assert (output / "report.json").read_bytes() == files["report.json"]


@pytest.mark.parametrize(
    ("case", "skipped", "expected_error"),
    [
        ("test_required", 0, None),
        ("test_substituted", 0, "linux_selected_junit_identity_mismatch"),
        ("test_required", 1, "pytest_required_execution_not_verified"),
    ],
)
def test_report_requires_exact_successful_case(tmp_path, case, skipped, expected_error):
    path = tmp_path / "junit.xml"
    child = "<skipped />" if skipped else ""
    path.write_text(
        '<testsuites><testsuite tests="1" failures="0" errors="0" '
        f'skipped="{skipped}"><testcase classname="tests.unit.test_offline" '
        f'name="{case}">{child}</testcase></testsuite></testsuites>',
        encoding="utf-8",
    )
    roster = ["tests/unit/test_offline.py::test_required"]
    if expected_error is None:
        assert chaos._verify_report(path, roster)["passed"] == 1
    else:
        with pytest.raises(ValueError, match=expected_error):
            chaos._verify_report(path, roster)


def _linux_roster():
    return [
        "tests/unit/" + case for case in (*chaos._COMMON, *chaos._OS_CASES["linux"])
    ]


def _roster_junit(roster):
    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root, "testsuite", tests=str(len(roster)), errors="0", failures="0", skipped="0"
    )
    for node in roster:
        source, name = node.split("::")
        ET.SubElement(
            suite,
            "testcase",
            classname=source.removesuffix(".py").replace("/", "."),
            name=name,
        )
    return root, suite


def test_linux_roster_replays_all_four_link_variants_with_exact_node_keys(tmp_path):
    roster = _linux_roster()
    expected = {
        "test_native_posix_linked_paths_are_refused_without_altering_outside_bytes"
        + f"[{variant}]"
        for variant in ("root", "ancestor", "state", "job_hardlink")
    }
    assert {
        node.split("::")[1] for node in roster if "linked_paths" in node
    } == expected
    assert len(roster) == 18
    assert len((*chaos._COMMON, *chaos._OS_CASES["windows"])) == 15
    root, _ = _roster_junit(roster)
    path = tmp_path / "linux.xml"
    path.write_bytes(ET.tostring(root))
    assert chaos._verify_report(path, roster)["passed"] == 18


def test_failure_summary_keeps_only_roster_outcomes_and_bounded_digest(tmp_path):
    roster = _linux_roster()
    xml, suite = _roster_junit(roster)
    known = list(suite)
    suite.remove(known[-1])
    ET.SubElement(known[1], "skipped", message="SECRET_SKIPPED_REASON")
    suite.append(known[2])
    ET.SubElement(
        suite,
        "testcase",
        classname="SECRET_UNEXPECTED_CLASS",
        name="SECRET_UNEXPECTED_NAME",
    )
    path = tmp_path / "linux.xml"
    path.write_bytes(ET.tostring(xml))
    raw = chaos._read_junit(path)
    summary_raw = chaos._failure_summary(raw, roster)
    assert b"SECRET" not in summary_raw
    summary = json.loads(summary_raw)
    assert summary["acceptance"] is False
    assert summary["junit_sha256"] == hashlib.sha256(raw).hexdigest()
    assert summary["unexpected_case_count"] == 1
    assert summary["selected_outcomes"][roster[0]] == "passed"
    assert summary["selected_outcomes"][roster[1]] == "skipped"
    assert summary["selected_outcomes"][roster[2]] == "duplicate"
    assert summary["selected_outcomes"][roster[-1]] == "missing"
    path.write_bytes(b"<!DOCTYPE unsafe><testsuites/>")
    with pytest.raises(ValueError, match="offline_chaos_junit_unavailable"):
        chaos._read_junit(path)


def test_non_loopback_refusal_remains_fatal_when_a_case_catches_it(tmp_path):
    code = chaos._LOOPBACK_PYTEST.partition("import pytest")[0]
    code += """
try:
    socket.getaddrinfo("192.0.2.1", 443)
except PermissionError:
    pass
else:
    raise AssertionError("network check did not refuse")
assert len(socket.getaddrinfo("127.0.0.1", 443)) > 0
raise SystemExit(91 if blocked else 0)
"""
    env = chaos._run_env(tmp_path)
    assert not {"HOME", "APPDATA", "TEMP"}.difference(env)
    assert all(env[name] == str(tmp_path) for name in ("HOME", "APPDATA", "TEMP"))
    assert "OKX_API_KEY" not in env and "HTTPS_PROXY" not in env
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 91, result.stderr
