from fastapi.testclient import TestClient

from app.main import app


def test_liveness() -> None:
    with TestClient(app) as client:
        response = client.get("/liveness")
    assert response.status_code == 200
    assert response.json()["version"] == "1.6.8"


def test_capabilities_are_honest() -> None:
    with TestClient(app) as client:
        response = client.get("/api/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert "trade_lifecycle_state_machine" in body["completed"]
    assert "deterministic_risk_engine" in body["completed"]
    assert "paper_execution" in body["completed"]
    assert "auto_paper_orchestrator" in body["completed"]
    assert "postgresql_paper_state_persistence" in body["completed"]
    assert "paper_restart_recovery" in body["completed"]
    assert "okx_demo_authenticated_rest" in body["completed"]
    assert "okx_demo_explicitly_armed_automation" in body["completed"]
    assert "okx_demo_observability_watchdog" in body["completed"]
    assert "okx_demo_durable_soak_sessions" in body["completed"]
    assert "okx_demo_execute_soak_preflight" in body["completed"]
    assert "okx_demo_execute_soak_protection_verification" in body["completed"]
    assert "okx_demo_daily_performance_reports" in body["completed"]
    assert "okx_demo_operator_strategy_controls" in body["completed"]
    assert "live_execution" in body["completed"]
    assert "okx_live_protected_real_position_execution" in body["completed"]
    assert "okx_live_real_account_operator_acceptance" in body["not_yet_available"]


def test_lifecycle_transition_map() -> None:
    with TestClient(app) as client:
        response = client.get("/api/lifecycle/transitions")
    assert response.status_code == 200
    assert "risk_approved" in response.json()["candidate"]


def test_recovery_status_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/recovery/status")
    assert response.status_code == 200
    body = response.json()
    assert "memory_checksum" in body
    assert "consistent" in body


def test_okx_demo_status_never_exposes_credentials() -> None:
    with TestClient(app) as client:
        response = client.get("/api/okx-demo/status")
    assert response.status_code == 200
    body = response.json()
    assert body["simulated_trading_header"] == "1"
    rendered = response.text.lower()
    assert "api_secret" not in rendered
    assert "passphrase" not in rendered
    assert "ok-access-key" not in rendered


def test_okx_demo_private_routes_require_ctcc_token() -> None:
    with TestClient(app) as client:
        response = client.post("/api/okx-demo/connectivity-check")
        assert response.status_code in {401, 503}


def test_okx_live_routes_require_ctcc_token_including_status() -> None:
    with TestClient(app) as client:
        for path in (
            "/api/okx-live/status",
            "/api/okx-live/balance",
            "/api/okx-live/automation/status",
        ):
            response = client.get(path)
            assert response.status_code in {401, 503}


def test_demo_automation_routes_require_ctcc_token() -> None:
    with TestClient(app) as client:
        response = client.get("/api/demo-automation/status")
    assert response.status_code in {401, 503}


def test_observability_routes_require_ctcc_token() -> None:
    with TestClient(app) as client:
        response = client.get("/api/demo-observability/summary")
    assert response.status_code in {401, 503}


def test_execute_soak_preflight_requires_ctcc_token() -> None:
    with TestClient(app) as client:
        response = client.get("/api/demo-observability/soak/preflight")
    assert response.status_code in {401, 503}


def test_performance_routes_require_ctcc_token() -> None:
    with TestClient(app) as client:
        response = client.get("/api/demo-performance/summary")
    assert response.status_code in {401, 503}
