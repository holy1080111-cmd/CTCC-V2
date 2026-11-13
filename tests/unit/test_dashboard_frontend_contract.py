import pytest

from app.api.router import api_router
from app.dashboard.router import dashboard


@pytest.mark.asyncio
async def test_frontend_declares_snapshot_contract() -> None:
    response = await dashboard()
    html = response.body.decode("utf-8")

    assert 'SUPPORTED_SNAPSHOT_CONTRACT_VERSION = "1.0"' in html
    assert "MAX_SNAPSHOT_FUTURE_SKEW_MS = 30000" in html
    assert "EXPECTED_SNAPSHOT_SOURCES" in html

    for source_name in (
        "balance",
        "positions",
        "algo_orders",
        "automation",
        "performance",
        "validation",
        "events",
    ):
        assert f'"{source_name}"' in html


@pytest.mark.asyncio
async def test_frontend_validates_snapshot_contract() -> None:
    response = await dashboard()
    html = response.body.decode("utf-8")

    required_markers = [
        "class SnapshotContractError extends Error",
        "function validateSnapshotContract(snapshot)",
        "unsupported_contract_version",
        "invalid_snapshot_id",
        "generated_at_timezone_missing",
        "generated_at_too_far_in_future",
        "source_set_mismatch",
        "invalid_source_status",
        "complete_source_status_mismatch",
        "successful_source_missing_value",
        "failed_source_contains_value",
        "list_source_not_array",
        "failed_source_contains_items",
    ]

    for marker in required_markers:
        assert marker in html


@pytest.mark.asyncio
async def test_contract_gate_runs_before_render() -> None:
    response = await dashboard()
    html = response.body.decode("utf-8")

    validation_index = html.index("validateSnapshotContract(snapshot);")

    first_render_index = html.index("renderBalance(snapshot.balance);")

    assert validation_index < first_render_index

    assert 'stateElement.textContent = "Contract Error"' in html
    assert "快照格式錯誤：" in html
    assert "lastContractError = null" in html
    assert "error.code" in html
    assert "SnapshotContractError" in html


@pytest.mark.asyncio
async def test_failed_snapshot_clears_previously_rendered_operator_values() -> None:
    html = (await dashboard()).body.decode("utf-8")
    refresh = html.split("async function refreshAll() {", 1)[1].split(
        "function setToken() {", 1
    )[0]
    failed = refresh.rsplit("} catch (error) {", 1)[1]
    assert failed.index("markSnapshotUnavailable();") < failed.index(
        "renderDataIntegrityStatus();"
    )
    reset = html.split("function markSnapshotUnavailable() {", 1)[1].split(
        "function formatDuration(", 1
    )[0]
    for source in (
        "markBalanceUnavailable();",
        "markAutomationUnavailable();",
        "markPositionsUnavailable();",
        "markPerformanceUnavailable(",
        "markEventsUnavailable();",
    ):
        assert source in reset
    assert 'byId("dailyPnl").textContent = "—";' in html
    assert '"activeDays",' in html and '"maxDrawdown"' in html


@pytest.mark.asyncio
async def test_auth_failure_is_clear_without_echoing_response_body() -> None:
    html = (await dashboard()).body.decode("utf-8")
    request = html.split("async function apiGet(path, token) {", 1)[1].split(
        "function renderAutomation(", 1
    )[0]
    assert request.index("if (!response.ok) {") < request.index(
        "const responseText = await response.text();"
    )
    assert "sessionStorage.removeItem(TOKEN_KEY);" in request
    assert "API Token 驗證失敗，請重新設定。" in request
    assert "JSON.stringify(payload)" not in request


@pytest.mark.asyncio
async def test_dashboard_links_existing_local_readiness_and_paper_tools() -> None:
    html = (await dashboard()).body.decode("utf-8")
    assert 'href="/readiness"' in html
    assert 'href="/docs#/analysis"' in html
    assert 'href="/docs#/paper"' in html
    assert "來源失敗時顯示「讀取失敗」，不代表零持倉或可交易" in html
    paths = {getattr(route, "path", None) for route in api_router.routes}
    assert {"/readiness", "/api/paper/account", "/api/paper/state"} <= paths
