"""Standalone localhost analysis never exposes Paper, DB or exchange routes."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.offline_snapshot_only import build_offline_snapshot_only_app
from app.api.routers import analysis
from app.domain.source_primitives import MAX_RAW, sha
from scripts import run_offline_snapshot_only as launcher
from tests.unit.test_qualification_data import market  # noqa: F401

TOKEN = b"a" * 64
COMMIT = "b" * 40


def _client() -> TestClient:
    app = build_offline_snapshot_only_app(token=TOKEN, source_commit=COMMIT, port=8100)
    return TestClient(app, base_url="http://127.0.0.1:8100")


def test_standalone_routes_are_local_and_analysis_only() -> None:
    with _client() as client:
        paths = client.app.openapi()["paths"]
        assert set(paths) == {"/dashboard", "/readiness", "/api/analysis/snapshot"}
        assert set(paths["/api/analysis/snapshot"]) == {"post"}
        page = client.get("/dashboard")
        assert page.status_code == 200
        assert "default-src 'none'" in page.headers["content-security-policy"]
        assert page.headers["cache-control"] == "no-store"
        assert "匯入資料來源未經驗證" in page.text
        assert 'id="analysisSection" hidden' in page.text
        assert "四個週期的分析" in page.text
        assert "完整 JSON 結果" in page.text
        assert "<script src=" not in page.text
        assert "https://" not in page.text and "http://" not in page.text
        assert "/api/paper" not in page.text and "/api/okx" not in page.text
        assert client.get("/docs").status_code == 404
        assert client.get("/api/paper/account").status_code == 404
        assert client.get("/api/market/snapshot/BTC-USDT-SWAP").status_code == 404
        status = client.get("/readiness").json()
        assert status["execution_authority"] is False
        assert status["source_authenticity_verified"] is False


def test_standalone_auth_origin_and_exact_source_are_enforced(
    market,  # noqa: F811
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        analysis,
        "get_settings",
        lambda: pytest.fail("standalone app read ambient settings"),
    )
    blocked = []

    async def deny_external(*args, **kwargs):
        blocked.append((args, kwargs))
        raise AssertionError("standalone app attempted external HTTP")

    monkeypatch.setattr(httpx.AsyncClient, "send", deny_external)
    body = json.dumps(market.model_dump(mode="json")).encode("utf-8")
    with _client() as client:
        path = "/api/analysis/snapshot"
        assert client.post(path, content=body).status_code == 401
        assert (
            client.post(
                path,
                content=body,
                headers={"X-CTCC-Token": TOKEN.decode(), "Origin": "https://elsewhere"},
            ).status_code
            == 403
        )
        assert (
            client.get("/readiness", headers={"Host": "localhost:8100"}).status_code
            == 403
        )
        accepted = client.post(
            path,
            content=body,
            headers={
                "X-CTCC-Token": TOKEN.decode(),
                "Content-Type": "application/json",
            },
        )
        assert accepted.status_code == 200, accepted.json()
        value = accepted.json()
        assert value["input_sha256"] == sha(body)
        assert value["source_authenticity_verified"] is False
        assert value["execution_authority"] is False
        assert value["analysis"]["trade_ready"] is False
        assert "imported_snapshot_unverified" in value["analysis"]["blockers"]
        assert isinstance(value["analysis"]["price"], str)
        assert value["analysis"]["generated_at"].endswith("Z")
        for interval in ("4H", "1H", "15m", "5m"):
            assert (
                value["analysis"]["timeframe_analyses"][interval]["timeframe"]
                == interval
            )
        oversized = client.post(
            path,
            content=b" " * (MAX_RAW + 1),
            headers={
                "X-CTCC-Token": TOKEN.decode(),
                "Content-Type": "application/json",
            },
        )
        assert oversized.status_code == 413
    assert blocked == []


def test_launcher_rechecks_private_token_without_printing_it(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    metadata = {
        "source_commit": COMMIT,
        "host_port": 8100,
        "secret_sha256": {"api_token": sha(TOKEN)},
    }
    contents = {
        "installation.json": json.dumps(metadata).encode("ascii"),
        "api_token": TOKEN,
    }
    monkeypatch.setattr(
        launcher.installation,
        "verify",
        lambda root: {
            "status": "READY_TO_START_ANALYSIS_ONLY",
            "installation_root": str(root),
            "dashboard_url": "http://127.0.0.1:8100/dashboard",
        },
    )
    monkeypatch.setattr(
        launcher.installation,
        "_read_regular",
        lambda path, maximum: contents[path.name],
    )
    assert launcher.verified_launch_inputs(tmp_path) == (TOKEN, COMMIT, 8100)
    assert launcher.main(["--check", str(tmp_path)]) == 0
    checked_output = capsys.readouterr().out
    assert "READY_TO_LAUNCH_OFFLINE_SNAPSHOT_ONLY" in checked_output
    assert TOKEN.decode() not in checked_output
    contents["api_token"] = b"changed" + TOKEN
    assert launcher.main(["--check", str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert output == '{"status":"REFUSED","code":"offline_snapshot_launcher_refused"}\n'
    assert TOKEN.decode() not in output


def test_launcher_serve_pins_loopback_without_starting_service(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    app = build_offline_snapshot_only_app(token=TOKEN, source_commit=COMMIT, port=8100)
    monkeypatch.setattr(
        launcher, "build_verified_app", lambda root: (app, COMMIT, 8100)
    )
    import uvicorn

    calls = []
    monkeypatch.setattr(
        uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    assert launcher.main(["--serve", str(tmp_path)]) == 0
    assert len(calls) == 1
    assert calls[0][0] == (app,)
    options = calls[0][1]
    assert options["host"] == "127.0.0.1"
    assert options["port"] == 8100
    assert options["reload"] is False
    assert options["access_log"] is False
    assert TOKEN.decode() not in capsys.readouterr().out


@pytest.mark.parametrize(
    "token", (b"short", b"a" * 47, b"a" * 129, b"a" * 48 + b"\xff")
)
def test_bad_explicit_token_configuration_refuses(token: bytes) -> None:
    with pytest.raises(ValueError, match="offline_snapshot_only_configuration_refused"):
        build_offline_snapshot_only_app(token=token, source_commit=COMMIT, port=8100)
