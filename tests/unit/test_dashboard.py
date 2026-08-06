import pytest

from app.dashboard.router import dashboard


@pytest.mark.asyncio
async def test_dashboard_is_read_only() -> None:
    response = await dashboard()
    html = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "CTCC Control Center" in html

    assert "/api/dashboard/snapshot" in html

    old_read_endpoints = [
        "/api/okx-demo/balance",
        "/api/okx-demo/positions",
        "/api/okx-demo/algo-orders/pending",
        "/api/demo-automation/status",
        "/api/demo-performance/summary",
        "/api/demo-performance/validation",
        "/api/demo-observability/events",
    ]

    for path in old_read_endpoints:
        assert path not in html

    forbidden_write_paths = [
        "/api/demo-automation/arm",
        "/api/demo-automation/disarm",
        "/api/demo-automation/start",
        "/api/demo-automation/stop",
        "/api/demo-automation/run-once",
        "/api/demo-automation/emergency-stop",
        "/api/demo-automation/clear-emergency-stop",
        "/api/okx-demo/positions/close",
        "/api/okx-demo/orders/cancel",
        "/api/demo-observability/soak/start",
    ]

    for path in forbidden_write_paths:
        assert path not in html

    assert 'method: "POST"' not in html
    assert 'method: "DELETE"' not in html
    assert 'method: "PATCH"' not in html
    assert 'method: "PUT"' not in html


@pytest.mark.asyncio
async def test_dashboard_has_security_headers() -> None:
    response = await dashboard()

    assert response.headers["cache-control"] == (
        "no-store, max-age=0"
    )
    assert response.headers["x-content-type-options"] == (
        "nosniff"
    )
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cross-origin-opener-policy"] == (
        "same-origin"
    )

    policy = response.headers["content-security-policy"]

    assert "default-src 'self'" in policy
    assert "connect-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "object-src 'none'" in policy


@pytest.mark.asyncio
async def test_dashboard_uses_single_snapshot_request() -> None:
    response = await dashboard()
    html = response.body.decode("utf-8")

    assert (
        'apiGet(\n'
        '            "/api/dashboard/snapshot",\n'
        '            token\n'
        '        )'
    ) in html

    assert "new AbortController" in html
    assert "REQUEST_TIMEOUT_MS = 12000" in html
    assert "if (refreshInProgress)" in html
    assert "refreshInProgress = false" in html
    assert "sessionStorage.removeItem(TOKEN_KEY)" in html

    assert "Promise.allSettled" not in html


@pytest.mark.asyncio
async def test_dashboard_uses_snapshot_integrity() -> None:
    response = await dashboard()
    html = response.body.decode("utf-8")

    assert 'id="dataIntegrityState"' in html
    assert 'id="dataIntegrityNote"' in html

    assert "DATA_STALE_AFTER_MS = 90000" in html
    assert "DATA_CONSISTENCY_WINDOW_MS = 5000" in html

    assert (
        "snapshot.source_status" in html
        or "snapshot?.source_status" in html
    )
    assert (
        "snapshot.generated_at" in html
        or "snapshot?.generated_at" in html
    )
    assert "snapshot.snapshot_id" in html
    assert "snapshot.duration_ms" in html
    assert "snapshot.complete" in html

    assert "updateDataIntegrityFromSnapshot" in html
    assert "sourceSucceeded" in html
    assert "sourceErrorCode" in html

    assert "資料年齡" in html
    assert "來源時間差" in html
