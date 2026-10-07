"""Native parser and fail-closed package identity; installer code never executes."""

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
VERIFIER = Path(__file__).resolve().parents[2] / "scripts/verify_highvol_installer.ps1"
NAMES = (
    "Install-CTCC-HighVol-Momentum-V2.ps1",
    "README.md",
    "controller.py",
    "patch.py",
    "test_demo_high_volatility_v2.py",
    "test_installer_restart_disarm.py",
    "update_manifest.py",
)
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="Native PowerShell required")


def package(tmp_path, *, malformed=False):
    marker = tmp_path / "MUST_NOT_EXECUTE"
    script = f"Set-Content -LiteralPath '{marker}' -Value 'executed'\n"
    if malformed:
        script += 'throw "CTCC_STOP:$Mode:$($result.code)"\n'
    else:
        script += 'throw "CTCC_STOP:${Mode}:$($result.code)"\n'
    rows = []
    for name in NAMES:
        data = script.encode() if name.endswith(".ps1") else b"fixture\n"
        (tmp_path / name).write_bytes(data)
        rows.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
    identity = tmp_path / "identity.json"
    identity.write_text(
        json.dumps({"schema": "ctcc_offline_installer_identity_v1", "files": rows}),
        encoding="utf-8",
    )
    return identity, marker


def validate(tmp_path, identity, *, expected_identity_sha256=None):
    identity_sha256 = (
        expected_identity_sha256 or hashlib.sha256(identity.read_bytes()).hexdigest()
    )
    command = [
        POWERSHELL,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "RemoteSigned",
        "-File",
        str(VERIFIER),
        "-PackageRoot",
        str(tmp_path),
        "-IdentityPath",
        str(identity),
        "-ExpectedIdentitySha256",
        identity_sha256,
        "-DryRun",
        "-StageTrace",
    ]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # TimeoutExpired.stderr may be bytes even with text=True. Report only
        # fixed stage names; never copy source, paths, or PowerShell output.
        stderr = exc.stderr or b""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        stages = re.findall(r"INSTALLER_VERIFY_STAGE:([a-z_]+)", stderr)
        raise AssertionError(
            f"INSTALLER_VERIFIER_TIMEOUT:last_stage={stages[-1] if stages else 'startup'}"
        ) from exc


def test_native_parse_and_repeated_dry_run_do_not_execute_installer(tmp_path):
    identity, marker = package(tmp_path)
    results = [validate(tmp_path, identity) for _ in range(2)]
    assert all(item.returncode == 0 for item in results), results[0].stderr
    records = [json.loads(item.stdout) for item in results]
    assert records[0] == records[1]
    expected_stages = [
        "start",
        "identity_hash",
        "source_hashes",
        "parser",
        "analyzer_discovery_start",
        "analyzer_discovery_end",
        "complete",
    ]
    for item in results:
        assert (
            re.findall(r"INSTALLER_VERIFY_STAGE:([a-z_]+)", item.stderr)
            == expected_stages
        )
    assert records[0]["external_calls"] == 0
    assert records[0]["deployment_performed"] is False
    assert records[0]["canonical_qualification_integration"] == "NOT_ACCEPTED"
    assert not marker.exists()


def test_original_scope_interpolation_rejects_before_execution(tmp_path):
    identity, marker = package(tmp_path, malformed=True)
    result = validate(tmp_path, identity)
    assert result.returncode != 0
    assert "INSTALLER_PARSER_REJECTED" in result.stderr
    assert not marker.exists()


def test_changed_source_bytes_reject_before_execution(tmp_path):
    identity, marker = package(tmp_path)
    (tmp_path / "controller.py").write_bytes(b"drift\n")
    result = validate(tmp_path, identity)
    assert result.returncode != 0
    assert "INSTALLER_SOURCE_IDENTITY_MISMATCH" in result.stderr
    assert not marker.exists()


def test_rewritten_identity_rejects_before_execution(tmp_path):
    identity, marker = package(tmp_path)
    trusted_sha256 = hashlib.sha256(identity.read_bytes()).hexdigest()
    value = json.loads(identity.read_text())
    value["files"][0]["sha256"] = "0" * 64
    identity.write_text(json.dumps(value), encoding="utf-8")
    result = validate(tmp_path, identity, expected_identity_sha256=trusted_sha256)
    assert result.returncode != 0
    assert "INSTALLER_IDENTITY_MISMATCH" in result.stderr
    assert not marker.exists()


def test_invalid_utf8_identity_rejects_before_execution(tmp_path):
    identity, marker = package(tmp_path)
    identity.write_bytes(identity.read_bytes() + b"\xff")
    result = validate(tmp_path, identity)
    assert result.returncode != 0
    assert "INSTALLER_IDENTITY_UTF8_INVALID" in result.stderr
    assert not marker.exists()


def test_invalid_utf8_installer_rejects_before_execution(tmp_path):
    identity, marker = package(tmp_path)
    installer = tmp_path / NAMES[0]
    installer.write_bytes(installer.read_bytes() + b"\xff")
    value = json.loads(identity.read_text())
    value["files"][0]["sha256"] = hashlib.sha256(installer.read_bytes()).hexdigest()
    identity.write_text(json.dumps(value), encoding="utf-8")
    result = validate(tmp_path, identity)
    assert result.returncode != 0
    assert "INSTALLER_SCRIPT_UTF8_INVALID" in result.stderr
    assert not marker.exists()


def test_utf8_bom_identity_and_installer_parse_from_pinned_bytes(tmp_path):
    identity, marker = package(tmp_path)
    installer = tmp_path / NAMES[0]
    installer.write_bytes(b"\xef\xbb\xbf" + installer.read_bytes())
    value = json.loads(identity.read_text())
    value["files"][0]["sha256"] = hashlib.sha256(installer.read_bytes()).hexdigest()
    identity.write_bytes(b"\xef\xbb\xbf" + json.dumps(value).encode())
    result = validate(tmp_path, identity)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["powershell_parser"] == "PASS"
    assert not marker.exists()


def test_missing_source_rejects_before_execution(tmp_path):
    identity, marker = package(tmp_path)
    (tmp_path / "patch.py").unlink()
    result = validate(tmp_path, identity)
    assert result.returncode != 0
    assert "INSTALLER_SOURCE_MISSING" in result.stderr
    assert not marker.exists()


def test_duplicate_identity_rejects_before_execution(tmp_path):
    identity, marker = package(tmp_path)
    value = json.loads(identity.read_text())
    value["files"][-1] = value["files"][0]
    identity.write_text(json.dumps(value), encoding="utf-8")
    result = validate(tmp_path, identity)
    assert result.returncode != 0
    assert "INSTALLER_IDENTITY_FILE_SET_INVALID" in result.stderr
    assert not marker.exists()
