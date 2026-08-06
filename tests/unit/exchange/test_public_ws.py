from app.config.settings import Settings
from app.exchange.okx.public_ws import OkxPublicWebSocket


async def _handler(_event):
    return None


def test_subscription_args_are_complete() -> None:
    settings = Settings(_env_file=None, okx_ws_symbols="BTC-USDT-SWAP")
    client = OkxPublicWebSocket(settings, _handler)
    args = client.subscription_args()
    channels = {item["channel"] for item in args}
    assert channels == {"tickers", "mark-price", "funding-rate", "open-interest", "trades", "books5"}
    assert all(item["instId"] == "BTC-USDT-SWAP" for item in args)
