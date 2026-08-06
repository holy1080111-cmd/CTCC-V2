from fastapi.testclient import TestClient

from app.main import app


def test_paper_market_order_api() -> None:
    with TestClient(app) as client:
        client.post("/api/paper/reset")
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
