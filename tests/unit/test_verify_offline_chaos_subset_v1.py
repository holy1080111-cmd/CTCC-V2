import hashlib
import subprocess
import sys

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
