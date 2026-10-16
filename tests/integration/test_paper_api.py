from fastapi.testclient import TestClient

from app.api.security import require_ctcc_token
from app.main import app


def test_paper_market_order_api() -> None:
    app.dependency_overrides[require_ctcc_token] = lambda: None
    try:
        with TestClient(app) as client:
            assert client.post("/api/paper/reset").status_code == 200
            response = client.post(
                "/api/paper/orders",
                json={
                    "symbol": "BTC-USDT-SWAP",
                    "side": "long",
                    "quantity": "0.1",
                    "reference_price": "100",
                    "stop_loss": "95",
                    "take_profit": "110",
                    "order_type": "market",
                    "risk_decision": "approved",
                    "strategy": "integration_test",
                    "score": 80,
                },
            )
            assert response.status_code == 201
            assert response.json()["status"] == "filled"
            account = client.get("/api/paper/account")
            assert account.status_code == 200
            assert account.json()["open_positions"] == 1
    finally:
        app.dependency_overrides.pop(require_ctcc_token, None)
