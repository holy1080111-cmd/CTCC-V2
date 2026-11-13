"""Browser entry for disconnected imported-snapshot analysis."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.router import api_router


def test_local_analysis_page_is_served_with_no_external_assets() -> None:
    app = FastAPI(openapi_url=None, docs_url=None)
    app.include_router(api_router)
    with TestClient(app) as client:
        response = client.get("/dashboard/local-analysis")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    html = response.text
    assert "<script>" in html and "<style>" in html
    assert "<script src=" not in html and "<link rel=" not in html
    assert "https://" not in html and "http://" not in html
    assert "匯入檔案的來源未經驗證" in html
    assert 'href="/readiness"' in html
    assert 'href="#paper-section"' in html
    assert 'href="/docs' not in html


def test_local_analysis_page_only_posts_exact_file_to_existing_offline_api() -> None:
    app = FastAPI(openapi_url=None, docs_url=None)
    app.include_router(api_router)
    paths = {getattr(route, "path", None): route for route in api_router.routes}
    assert paths["/api/analysis/snapshot"].methods == {"POST"}
    html = TestClient(app).get("/dashboard/local-analysis").text
    assert 'fetch("/api/analysis/snapshot", {' in html
    assert 'method: "POST"' in html
    assert "body: file" in html
    assert 'credentials: "omit"' in html
    assert 'redirect: "error"' in html
    assert "sessionStorage" not in html and "localStorage" not in html
    assert 'tokenInput.value = "";' in html
    assert "response.text()" not in html
    assert "result.textContent = JSON.stringify(value, null, 2);" in html
    assert '["/api/paper/account", "/api/paper/state"]' in html
    assert 'method: "GET"' in html
    assert "OKX 實際持倉仍未知" in html
    assert paths["/api/paper/orders"].methods == {"POST"}
    assert paths["/api/paper/ticks"].methods == {"POST"}
    assert 'paperPost("/api/paper/orders", token' in html
    assert 'paperPost("/api/paper/ticks", token' in html
    assert 'paperLimitWrap.hidden = paperMode.value !== "limit";' in html
    assert "client_order_id: pendingPaperOrder.id" in html
    assert "if (pendingPaperOrder.uncertain)" in html
    assert "paperConfirm.checked = false;" in html
    assert "/api/paper/reset" not in html and "/api/okx" not in html
    for denied in (
        "source_authenticity_verified === false",
        "execution_authority === false",
        "analysis.trade_ready === false",
        'includes("imported_snapshot_unverified")',
    ):
        assert denied in html


def test_dashboard_links_self_contained_local_analysis_page() -> None:
    app = FastAPI(openapi_url=None, docs_url=None)
    app.include_router(api_router)
    html = TestClient(app).get("/dashboard").text
    assert 'href="/dashboard/local-analysis"' in html
