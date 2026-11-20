"""Synthetic package/configuration tests, no DB, exchange, Arm or services."""

import io
import json
import os
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings as configuration
from app.config import windows_tool_profile_0058_v1 as profile
from scripts import launch_windows_tool_0058_v1 as launcher
from scripts import manifest
from scripts import windows_tool_0058_v1 as tool

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def synthetic_git_source(tmp_path):
    """Real isolated Git/archive; actual migration bytes, synthetic history."""
    source = tmp_path / "synthetic-source"
    source.mkdir()
    names = list(tool._DEPENDENCIES)
    names.extend(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "migrations").rglob("*.py")
        if "__pycache__" not in path.parts
    )
    for name in names:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    (source / "empty.py").touch()
    manifest.write_manifest(source, source / "MANIFEST.sha256")
    for arguments in (
        ["init", "--quiet"],
        ["config", "core.autocrlf", "false"],
        ["add", "."],
        [
            "-c",
            "user.name=Synthetic fixture",
            "-c",
            "user.email=fixture@invalid.invalid",
            "commit",
            "--quiet",
            "-m",
            "Synthetic installer data only",
        ],
    ):
        result = subprocess.run(
            ["git", "-C", str(source), *arguments], capture_output=True, check=False
        )
        assert result.returncode == 0, result.stderr
    return source


def staged(tmp_path, source):
    destination = tmp_path / "release"
    document = tool.stage(destination, source=source)
    return destination, document, tool.sha(tool.canonical(document))


def test_real_git_stage_exact_archive_and_reinstall_are_idempotent(
    tmp_path, synthetic_git_source
):
    root, document, identity = staged(tmp_path, synthetic_git_source)
    assert tool.verify(root, identity) == document
    assert document["migration"]["head"] == "0058"
    assert document["migration"]["count"] == 59
    assert (
        document["application_version"]
        == configuration.Settings.model_fields["app_version"].default
    )
    assert document["execution_authority"] is document["startup_trust"] is False
    assert (root / "source/empty.py").read_bytes() == b""
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert tool.stage(root, source=synthetic_git_source) == document
    assert {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    } == before


def test_plan_does_not_create_any_installation(tmp_path, synthetic_git_source):
    root = tmp_path / "not-created"
    document = tool.stage(root, source=synthetic_git_source, dry_run=True)
    assert not root.exists()
    assert document["installed_database_modified"] is False


@pytest.mark.parametrize(
    "operation", ["source", "archive", "identity", "extra", "hardlink"]
)
def test_package_drift_refuses_without_repair_or_overwrite(
    tmp_path, synthetic_git_source, operation
):
    root, _, identity = staged(tmp_path, synthetic_git_source)
    if operation == "source":
        (root / "source/pyproject.toml").write_bytes(b"changed")
    elif operation == "archive":
        with (root / "source.zip").open("ab") as stream:
            stream.write(b"tamper")
    elif operation == "identity":
        identity = "0" * 64
    elif operation == "extra":
        (root / "source/.env").write_bytes(b"private=must_refuse")
    else:
        (root / "linked").hardlink_to(root / "source/pyproject.toml")
    with pytest.raises(tool.WindowsToolRefused):
        tool.verify(root, identity)


def test_dirty_source_and_existing_unknown_directory_refuse(
    tmp_path, synthetic_git_source
):
    (synthetic_git_source / "untracked.txt").write_text("do not discard")
    with pytest.raises(tool.WindowsToolRefused):
        tool.stage(tmp_path / "dirty-release", source=synthetic_git_source)
    assert (synthetic_git_source / "untracked.txt").read_text() == "do not discard"


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "C:/escape",
        "folder\\escape",
        ".env",
        "folder/token.key",
        "private/thing",
        "one:stream",
        "NUL.txt",
        "COM1.py",
        "folder./x",
        "trailing /x",
    ],
)
def test_archive_path_private_and_traversal_refusal(name):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        # ZipInfo normally normalizes Windows separators. Set the actual raw
        # member afterward to exercise an untrusted noncanonical archive.
        entry = zipfile.ZipInfo("placeholder")
        entry.filename = name
        archive.writestr(entry, b"synthetic")
    with pytest.raises(tool.WindowsToolRefused):
        tool._archive_members(stream.getvalue())


def test_case_conflict_and_symlink_archive_refuse():
    for symlink in (False, True):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            if symlink:
                entry = zipfile.ZipInfo("link")
                entry.external_attr = 0o120777 << 16
                archive.writestr(entry, b"target")
            else:
                archive.writestr("A.py", b"1")
                archive.writestr("a.py", b"2")
        with pytest.raises(tool.WindowsToolRefused):
            tool._archive_members(stream.getvalue())


def document(mode="analysis", **changes):
    return {
        "mode": mode,
        "port": 8128,
        "settings": {"api_token": "synthetic_token_" + "a" * 48},
        "live_runtime": None,
    } | changes


def test_every_field_explicitly_suppresses_ambient_environment_and_cwd_env(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "LIVE_TRADING=true\nOKX_DEMO_AUTO_EXECUTION=true\nAPI_TOKEN=secret_from_untrusted_cwd\n"
    )
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("DATABASE_URL", "not_a_database")
    monkeypatch.setenv("CTCC_DEMO_HIGH_VOL_MOMENTUM_V2", "true")
    values = profile._values(configuration.Settings, document())
    assert set(values) == set(configuration.Settings.model_fields)
    result = configuration.Settings(_env_file=None, **values)
    assert result.api_token_value == document()["settings"]["api_token"]
    assert (
        result.live_trading
        is result.okx_live_allow_order_writes
        is result.okx_demo_auto_execution
        is False
    )
    assert result.ctcc_demo_high_vol_momentum_v2 is False
    assert result.trading_mode == "analysis_only"
    assert result.database_url != "not_a_database"


def test_live_unconfigured_page_settings_default_off_and_legacy_fences_stay_off():
    result = configuration.Settings(
        _env_file=None, **profile._values(configuration.Settings, document("live"))
    )
    assert result.trading_mode == "analysis_only"
    assert (
        result.okx_live_enabled
        is result.live_trading
        is result.okx_live_allow_order_writes
        is False
    )
    assert result.okx_live_auto_execution is False


def test_demo_source_settings_use_only_request_owned_capabilities():
    settings = {
        "api_token": "synthetic_" + "a" * 48,
        "okx_demo_api_key": "synthetic_key",
        "okx_demo_api_secret": "synthetic_secret",
        "okx_demo_api_passphrase": "synthetic_passphrase",
        "ctcc_native_pg_tls_ca_file": "synthetic_ca_not_opened",
        "ctcc_native_pg_tls_ca_sha256": "a" * 64,
    }
    result = configuration.Settings(
        _env_file=None,
        **profile._values(configuration.Settings, document("demo", settings=settings)),
    )
    from app.core.native_manual_demo_lifespan_0056_v1 import (
        require_manual_demo_settings_0056_v1,
    )

    require_manual_demo_settings_0056_v1(result)
    assert result.ctcc_native_demo_manual_lifespan_0056_v1 is True
    assert result.okx_demo_auto_execution is result.okx_demo_allow_order_writes is True
    assert result.okx_demo_continuous_session_enabled is False
    assert result.okx_live_allow_order_writes is False
    assert result.okx_demo_read_max_retries == result.okx_public_max_retries == 0


def test_private_loader_failure_is_fixed_and_unset_get_settings_unchanged(monkeypatch):
    configuration.get_settings.cache_clear()
    monkeypatch.delenv(profile.ENVIRONMENT_NAME, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "test")
    assert configuration.get_settings().environment == "test"
    configuration.get_settings.cache_clear()
    monkeypatch.setenv(
        profile.ENVIRONMENT_NAME, "synthetic_secret_path_must_not_escape"
    )
    with pytest.raises(tool.WindowsToolRefused) as error:
        configuration.get_settings()
    assert str(error.value) == "windows_tool_0058_profile_refused"
    configuration.get_settings.cache_clear()


def test_launch_check_refuses_host_interpreter_and_never_starts_provider(
    tmp_path, monkeypatch, capsys
):
    doc = document(package_root=str(tmp_path), package_identity_sha256="a" * 64)
    monkeypatch.setattr(launcher, "configure_process", lambda _: doc)
    monkeypatch.setattr(
        launcher,
        "build_tool",
        lambda _: pytest.fail("host-interpreter check imported app"),
    )
    assert launcher.main(["--profile", str(tmp_path / "profile.json"), "--check"]) == 1
    assert "launch_refused" in capsys.readouterr().out


def test_runtime_operation_lock_excludes_second_process_and_recovers_after_exit(
    tmp_path,
):
    with (
        launcher.runtime_lock(tmp_path),
        pytest.raises(OSError),
        launcher.runtime_lock(tmp_path),
    ):
        pytest.fail("parallel runtime lock admitted")
    with launcher.runtime_lock(tmp_path):
        assert (tmp_path / "runtime-operation.lock").is_file()


def test_invalid_cli_refusal_does_not_echo_private_values(capsys):
    assert (
        tool.main(
            [
                "verify",
                "--package",
                "synthetic_secret_dsn",
                "--identity-sha256",
                "a" * 64,
            ]
        )
        == 1
    )
    value = capsys.readouterr().out
    assert "synthetic_secret_dsn" not in value
    assert json.loads(value)["status"] == "REFUSED"


def test_unknown_cli_secret_switch_does_not_echo_secret(capsys):
    with pytest.raises(SystemExit) as stopped:
        tool.main(
            [
                "verify",
                "--package",
                "synthetic",
                "--api-token",
                "secret_must_not_escape",
            ]
        )
    assert stopped.value.code == 2
    assert "secret_must_not_escape" not in capsys.readouterr().err


@pytest.mark.parametrize("failure", [False, True])
def test_offline_runtime_install_and_reinstall_preserve_state_with_synthetic_processes(
    tmp_path, monkeypatch, failure
):
    # Controlled process/venv mechanics only; this never installs packages or
    # attests to a usable interpreter, database, accepted release or account.
    import venv

    root = tmp_path / "release"
    (root / "source/requirements").mkdir(parents=True)
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / "synthetic.whl").write_bytes(b"synthetic wheel bytes")
    (root / "source" / tool._DEPENDENCIES[1]).write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "filename": "synthetic.whl",
                        "sha256": tool.sha(b"synthetic wheel bytes"),
                    }
                ]
            }
        )
    )
    sentinel = root / "retained-journal.json"
    sentinel.write_text("do not delete")
    monkeypatch.setattr(tool, "verify", lambda *_: {"execution_authority": False})
    monkeypatch.setattr(tool.sys, "platform", "win32")
    created = []

    def create(target):
        created.append(target)
        (target / "Scripts").mkdir(parents=True)
        (target / "Scripts/python.exe").write_bytes(
            b"synthetic interpreter, never executed"
        )

    monkeypatch.setattr(venv, "EnvBuilder", lambda **_: SimpleNamespace(create=create))
    calls = []

    def run(arguments, **keywords):
        calls.append((arguments, keywords))
        return SimpleNamespace(returncode=1 if failure else 0)

    monkeypatch.setattr(tool.subprocess, "run", run)
    monkeypatch.setenv("OKX_DEMO_API_SECRET", "synthetic_must_not_reach_installer")
    if failure:
        with pytest.raises(tool.WindowsToolRefused):
            tool.install_runtime(root, "a" * 64, wheels)
        assert len(calls) == 1
    else:
        assert (
            tool.install_runtime(root, "a" * 64, wheels)["execution_authority"] is False
        )
        assert (
            tool.install_runtime(root, "a" * 64, wheels)["execution_authority"] is False
        )
        assert len(created) == 1 and len(calls) == 4
        arguments, keywords = calls[0]
        assert all(
            flag in arguments
            for flag in ("--isolated", "--no-index", "--no-deps", "--require-hashes")
        )
        assert "OKX_DEMO_API_SECRET" not in keywords["env"]
        assert keywords["stdout"] is subprocess.DEVNULL
        assert "--no-index" in arguments and "--upgrade" not in arguments
    assert sentinel.read_text() == "do not delete"
    assert "synthetic_must_not_reach_installer" not in str(calls)
    assert os.environ["OKX_DEMO_API_SECRET"] == "synthetic_must_not_reach_installer"


@pytest.mark.parametrize(
    "fault", [None, "schema", "mode", "unknown_setting", "live_runtime", "token"]
)
def test_private_document_closed_field_parser_with_explicit_synthetic_permissions(
    monkeypatch, tmp_path, fault
):
    # Private ACL and source verification are separately negative-tested. This
    # controlled parser fixture supplies DATA only, no database/source owner.
    module_path = tmp_path / "package/source/app/config/windows_tool_profile_0058_v1.py"
    monkeypatch.setattr(profile, "__file__", str(module_path))
    data = {
        "schema": profile.SCHEMA,
        "package_root": str(tmp_path / "package"),
        "package_identity_sha256": "a" * 64,
        **document(),
    }
    if fault == "schema":
        data["schema"] = "ctcc.native_only.db0056_deployment_descriptor.inert.v1"
    elif fault == "mode":
        data["mode"] = "legacy_live"
    elif fault == "unknown_setting":
        data["settings"]["live_trading"] = True
    elif fault == "live_runtime":
        data["live_runtime"] = {"path": "synthetic", "sha256": "a" * 64}
    elif fault == "token":
        data["settings"]["api_token"] = "short"
    raw = tool.canonical(data)
    monkeypatch.setattr(profile, "_private", lambda _: raw)
    calls = []
    monkeypatch.setattr(tool, "verify", lambda *args: calls.append(args))
    if fault is not None:
        with pytest.raises(tool.WindowsToolRefused):
            profile.read_profile(tmp_path / "private/profile.json")
    else:
        assert profile.read_profile(tmp_path / "private/profile.json") == data
        assert calls == [(tmp_path / "package", "a" * 64)]
