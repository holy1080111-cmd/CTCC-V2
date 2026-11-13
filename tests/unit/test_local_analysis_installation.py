"""Offline contract tests for a local-only, no-exchange installation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.config.settings import Settings
from scripts import prepare_local_analysis_installation as installer


def _fixture_install(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    root.mkdir()
    template = installer._TEMPLATE.read_bytes()
    monkeypatch.setattr(
        installer,
        "_source_identity",
        lambda: ("a" * 64, template, "b" * 40, "c" * 40),
    )
    monkeypatch.setattr(installer.private_dir, "create_private_work", lambda: root)

    def checked_root(candidate: Path) -> None:
        assert candidate == root
        assert candidate.is_dir()
        assert installer.private_dir.LEAF.fullmatch(candidate.name)

    monkeypatch.setattr(installer, "_checked_root", checked_root)


def test_rendered_compose_is_local_analysis_only(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / ("ctcc-analysis-" + "1" * 32)
    _fixture_install(monkeypatch, root)
    result = installer.prepare(host_port=18100)

    assert result["status"] == "READY_TO_START_ANALYSIS_ONLY"
    assert result["project_name"] == "ctcc-analysis-" + "1" * 32
    assert result["docker_started"] is False
    assert result["trading_enabled"] is False
    assert {path.name for path in root.iterdir()} == installer._MEMBERS
    compose_raw = (root / "compose.yaml").read_bytes()
    compose = yaml.safe_load(compose_raw)
    assert compose["name"] == "ctcc-analysis-" + "1" * 32
    assert set(compose["services"]) == {"api", "postgres", "redis"}
    api = compose["services"]["api"]
    assert api["ports"] == ["127.0.0.1:18100:8000"]
    assert api["networks"] == ["analysis_internal"]
    assert compose["networks"]["analysis_internal"]["internal"] is True
    assert api["environment"]["TRADING_MODE"] == "analysis_only"
    assert api["environment"]["CTCC_NATIVE_ONLY_LIFESPAN_V1"] == "false"
    assert api["environment"]["PAPER_PERSISTENCE_ENABLED"] == "true"
    assert api["build"]["args"] == {
        "CTCC_SOURCE_COMMIT": "b" * 40,
        "CTCC_SOURCE_TREE": "c" * 40,
    }
    for name in (
        "AUTO_TRADE",
        "LIVE_TRADING",
        "OKX_DEMO_ENABLED",
        "OKX_DEMO_ALLOW_ORDER_WRITES",
        "OKX_DEMO_AUTO_EXECUTION",
        "OKX_LIVE_ENABLED",
        "OKX_LIVE_ALLOW_ORDER_WRITES",
        "OKX_LIVE_AUTO_EXECUTION",
        "OKX_WS_ENABLED",
        "PAPER_AUTO_EXECUTION",
        "NOTION_OUTBOX_ENABLED",
        "SUBMISSION_OUTBOX_ENABLED",
    ):
        assert api["environment"][name] == "false"
    assert "env_file" not in api
    assert (root / "compose.env").read_bytes() == b""
    assert "API_TOKEN" not in api["environment"]
    assert "DATABASE_URL" not in api["environment"]
    assert "__" not in compose_raw.decode()
    assert "${" not in compose_raw.decode()
    assert b"postgresql+asyncpg://ctcc:" not in compose_raw
    assert (root / "api_token").read_bytes() not in compose_raw
    assert (root / "postgres_password").read_bytes() not in compose_raw
    assert compose["services"]["postgres"]["image"].startswith(
        "postgres:17-alpine@sha256:"
    )
    assert compose["services"]["redis"]["image"].startswith("redis:8-alpine@sha256:")
    assert set(compose["volumes"]) == {"postgres_data", "redis_data"}
    for name, value in api["environment"].items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://ctcc:synthetic@postgres:5432/ctcc"
    )
    monkeypatch.setenv(
        "API_TOKEN", "synthetic-offline-token-with-no-authority-123456789"
    )
    settings = Settings(_env_file=None)
    assert settings.trading_mode == "analysis_only"
    assert settings.api_token_is_safe
    assert not settings.okx_demo_enabled and not settings.okx_live_enabled
    assert installer.verify(root) == result


def test_recheck_refuses_secret_or_compose_tamper(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / ("ctcc-analysis-" + "2" * 32)
    _fixture_install(monkeypatch, root)
    installer.prepare()
    with (root / "api_token").open("ab") as handle:
        handle.write(b"x")
    with pytest.raises(installer.LocalAnalysisInstallRefusal):
        installer.verify(root)

    root2 = tmp_path / ("ctcc-analysis-" + "3" * 32)
    _fixture_install(monkeypatch, root2)
    installer.prepare()
    with (root2 / "compose.yaml").open("ab") as handle:
        handle.write(b"\n")
    with pytest.raises(installer.LocalAnalysisInstallRefusal):
        installer.verify(root2)


def test_recheck_refuses_extra_member_and_reinstall_clobber(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / ("ctcc-analysis-" + "4" * 32)
    _fixture_install(monkeypatch, root)
    installer.prepare()
    with pytest.raises(FileExistsError):
        installer.prepare()
    (root / "unexpected.txt").write_text("unreviewed", encoding="utf-8")
    with pytest.raises(installer.LocalAnalysisInstallRefusal):
        installer.verify(root)


def test_metadata_contains_hashes_and_no_secrets(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / ("ctcc-analysis-" + "5" * 32)
    _fixture_install(monkeypatch, root)
    installer.prepare()
    metadata = json.loads((root / "installation.json").read_bytes())
    assert metadata["source_manifest_sha256"] == "a" * 64
    assert metadata["source_commit"] == "b" * 40
    assert metadata["source_tree"] == "c" * 40
    assert metadata["secret_sha256"].keys() == set(installer._SECRET_NAMES)
    raw = (root / "installation.json").read_bytes()
    for name in installer._SECRET_NAMES:
        assert (root / name).read_bytes() not in raw
    assert metadata["exchange_credentials_present"] is False
    assert metadata["trading_enabled"] is False
