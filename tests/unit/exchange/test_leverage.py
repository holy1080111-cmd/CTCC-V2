from app.exchange.okx.leverage import leverage_response_matches


def test_leverage_response_requires_all_effective_fields() -> None:
    assert leverage_response_matches(
        [
            {
                "instId": "BTC-USDT-SWAP",
                "mgnMode": "isolated",
                "posSide": "long",
                "lever": "20",
            }
        ],
        instrument_id="BTC-USDT-SWAP",
        margin_mode="isolated",
        leverage=20,
        position_side="long",
    )

    assert not leverage_response_matches(
        [
            {
                "instId": "BTC-USDT-SWAP",
                "mgnMode": "isolated",
                "posSide": "long",
                "lever": "10",
            }
        ],
        instrument_id="BTC-USDT-SWAP",
        margin_mode="isolated",
        leverage=20,
        position_side="long",
    )


def test_net_position_side_accepts_okx_empty_serialization() -> None:
    assert leverage_response_matches(
        [
            {
                "instId": "ETH-USDT-SWAP",
                "mgnMode": "cross",
                "posSide": "",
                "lever": "3",
            }
        ],
        instrument_id="ETH-USDT-SWAP",
        margin_mode="cross",
        leverage=3,
        position_side="net",
    )
