"""Paper package/profile composition with synthetic secret files and no SQL."""

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn

from app.config import windows_tool_profile_0058_v1 as private_profile
from app.core import owned_paper58_runtime_v1 as runtime
from app.database import native_pg_tls_v1 as tls
from scripts import configure_windows_tool_0058_v1 as wizard
from scripts import launch_windows_tool_0058_v1 as existing_launcher
from scripts import run_paper58_owned_v1 as paper

_IDENTITY = "a" * 64
_COMMIT = "b" * 40
_CA_DIGEST = "c" * 64
_OWNER_URL = "postgresql+asyncpg://paper_owner:synthetic1@paper.invalid:5432/paper_db"
_ACTOR_URL = "postgresql+asyncpg://paper_actor:synthetic2@paper.invalid:5432/paper_db"


@pytest.fixture
def configured(tmp_path, monkeypatch, capsys):
    release = tmp_path / "exact-release"
    source = release / "source"
    private_root = tmp_path / "synthetic-private-only"
    source.mkdir(parents=True)
    private_root.mkdir()
    ca = tmp_path / "synthetic-ca.crt"
    arrivals = []

    def verified(root, expected):
        assert (root, expected) == (release, _IDENTITY)
        arrivals.append("package")
        return {"source_commit": _COMMIT}

    def ca_verified(path, digest):
        assert (path, digest) == (ca, _CA_DIGEST)
        arrivals.append("ca")

    monkeypatch.setattr(wizard.package, "_SOURCE", source)
    monkeypatch.setattr(wizard.package, "verify", verified)
    monkeypatch.setattr(
        wizard.package,
        "verify_dependencies",
        lambda root: arrivals.append("dependencies"),
    )
    monkeypatch.setattr(
        paper, "__file__", str(source / "scripts/run_paper58_owned_v1.py")
    )
    monkeypatch.setattr(
        paper.sys, "executable", str(release / "runtime/Scripts/python.exe")
    )
    monkeypatch.setattr(paper.sys, "prefix", str(release / "runtime"))
    monkeypatch.setattr(wizard, "create_private_root", lambda: private_root)
    monkeypatch.setattr(private_profile, "_private", Path.read_bytes)
    monkeypatch.setattr(tls, "verified_native_pg_tls_context_v1", ca_verified)
    entered = iter((_OWNER_URL, _ACTOR_URL))
    monkeypatch.setattr(wizard, "_secret", lambda _: next(entered))
    monkeypatch.setattr(wizard, "_path", lambda _: ca)
    monkeypatch.setattr("builtins.input", lambda _: _CA_DIGEST.upper())

    def forbidden(*_args, **_kwargs):
        pytest.fail("configuration attempted a PostgreSQL connection")

    monkeypatch.setattr(runtime, "create_async_engine", forbidden)
    terminal = SimpleNamespace(isatty=lambda: True)
    with monkeypatch.context() as tty:
        tty.setattr(wizard.sys, "stdin", terminal)
        tty.setattr(wizard.sys, "stderr", terminal)
        profile = wizard.configure(release, _IDENTITY, "paper", 8131)
    assert arrivals == ["package", "dependencies", "ca"]
    secret_paths = {
        key: private_root / name for key, name in paper._SECRET_FILES.items()
    }
    saved = json.loads(profile.read_bytes())
    assert profile == private_root / paper._PROFILE_FILE
    assert set(saved) == paper._PROFILE_KEYS
    assert saved["secret_files_sha256"] == {
        key: paper.package.sha(path.read_bytes()) for key, path in secret_paths.items()
    }
    stdout = capsys.readouterr().out
    assert json.loads(stdout)["database_observed"] is False
    for path in secret_paths.values():
        value = path.read_bytes().decode()
        assert value not in stdout and value not in profile.read_text(encoding="utf-8")
    return release, profile, secret_paths, arrivals


def test_paper_check_and_launch_use_same_package_lock(configured, monkeypatch, capsys):
    release, profile, _, arrivals = configured
    held = []

    @contextmanager
    def lock(root):
        assert root == release and held == []
        held.append(root)
        try:
            yield
        finally:
            held.pop()

    monkeypatch.setattr(existing_launcher, "runtime_lock", lock)

    class Server:
        started = False
        lifespan = SimpleNamespace(shutdown_failed=False)

        def __init__(self, config):
            assert (config.host, config.port, config.workers, config.lifespan) == (
                "127.0.0.1",
                8131,
                1,
                "on",
            )
            assert config.proxy_headers is config.access_log is False

        def run(self):
            assert held == [release]
            arrivals.append("server")
            self.started = True

    monkeypatch.setattr(uvicorn, "Server", Server)
    assert paper.main(["--profile", str(profile), "--check"]) == 0
    assert "server" not in arrivals and held == []
    check = json.loads(capsys.readouterr().out)
    assert check["dashboard_url"] == "http://127.0.0.1:8131/dashboard/local-analysis"
    assert check["source_commit"] == _COMMIT
    assert check["database_recovery_verified"] is check["execution_authority"] is False
    assert paper.main(["--profile", str(profile), "--serve"]) == 0
    assert arrivals.count("server") == 1 and held == []


@pytest.mark.parametrize("fault", ("content", "profile_pointer"))
def test_paper_refuses_secret_file_drift(configured, fault):
    _, profile, secret_paths, _ = configured
    if fault == "content":
        secret_paths["actor_database_url_file"].write_bytes(
            b"substituted synthetic actor"
        )
    else:
        mutated = json.loads(profile.read_bytes())
        mutated["actor_database_url_file"] = mutated["owner_database_url_file"]
        profile.write_bytes(paper.package.canonical(mutated))
    with pytest.raises(paper.package.WindowsToolRefused):
        paper.build_verified_app(profile)


@pytest.mark.parametrize(
    "name",
    (
        "OKX_LIVE_API_KEY",
        "OKX_DEMO_EXPECTED_UID",
        "CTCC_NATIVE_LIVE_RUNTIME_CONFIG_FILE",
    ),
)
def test_paper_route_refuses_inherited_venue_settings_before_profile_read(
    monkeypatch, name
):
    monkeypatch.setenv(name, "synthetic_ambient_value")
    with pytest.raises(paper.package.WindowsToolRefused):
        paper.build_verified_app(Path("profile-not-opened"))
