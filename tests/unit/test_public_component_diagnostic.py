"""Offline timestamp semantics for the non-authoritative component probe."""

from __future__ import annotations

import json

import pytest

from scripts import diagnose_public_components as probe


def _stamps(ts_ms: int):
    return tuple(
        {
            "utc_ns": ts_ms * 1_000_000 + offset,
            "monotonic_ns": 10_000_000_000 + offset,
        }
        for offset in (0, 100_000_000, 150_000_000, 160_000_000)
    )


def _response(data):
    return json.dumps({"code": "0", "msg": "", "data": data}).encode()


def _candle(ts_ms, confirm):
    return [str(ts_ms), "1", "2", "1", "2", "3", "4", "5", confirm]


def test_fixed_anonymous_scope_and_no_demo_or_order_route():
    assert probe.base.ORIGIN == "https://openapi.okx.com"
    assert probe.WS_ENDPOINT == "wss://ws.okx.com:443/ws/v5/public"
    assert len(probe.REQUESTS) == 11
    assert all(
        path.startswith(("/api/v5/market/", "/api/v5/public/"))
        for _, path, _ in probe.REQUESTS
    )
    assert all("trade/order" not in path for _, path, _ in probe.REQUESTS)
    assert b"login" not in probe.WS_SUBSCRIPTION
    assert b"x-simulated-trading" not in probe.WS_SUBSCRIPTION


def test_latest_candle_open_is_not_misreported_as_response_generation():
    latest = 1_791_698_400_000
    raw = _response([_candle(latest, "0"), _candle(latest - 14_400_000, "1")])
    result = probe.inspect_rest("candle_4h", raw, _stamps(latest + 100_000))
    assert result["timestamp_semantics"] == "bar_open_not_http_response_time"
    assert result["latest_confirm"] == "0"
    assert result["latest_bar_closed_by_headers"] is False
    assert result["source_within_request_interval"] is False
    assert result["response_instrument_verified"] is False
    assert result["raw_body_sha256"] == probe.base._sha(raw)
    assert "execution_authority" not in result


@pytest.mark.parametrize(
    "rows,error",
    [
        ([_candle(1_791_698_400_000, "1")], "source_candle_shape_invalid"),
        (
            [_candle(1_791_698_400_000, "1"), _candle(1_791_698_400_000, "1")],
            "source_candle_order_invalid",
        ),
        (
            [_candle(1_791_698_400_000, "1"), _candle(1_791_694_800_000, "1")],
            "source_candle_order_invalid",
        ),
    ],
)
def test_missing_or_conflicting_candle_rows_reject_without_sort_or_fill(rows, error):
    with pytest.raises(ValueError, match=error):
        probe.inspect_rest("candle_4h", _response(rows), _stamps(1_791_698_500_000))


def test_ticker_requires_exact_instrument_and_preserves_future_component_time():
    ts = 1_791_700_000_150
    raw = _response([{"instId": probe.base.INSTRUMENT, "ts": str(ts)}])
    result = probe.inspect_rest("ticker", raw, _stamps(ts - 150))
    assert result["response_instrument_verified"] is True
    assert result["source_minus_headers_received_ns"] == 50_000_000
    assert result["source_not_future_at_headers"] is False
    other = _response([{"instId": "ETH-USDT-SWAP", "ts": str(ts)}])
    with pytest.raises(ValueError, match="source_instrument_mismatch"):
        probe.inspect_rest("ticker", other, _stamps(ts))


def test_ws_application_messages_require_exact_channel_instrument_id_and_raw_time():
    received = _stamps(1_791_700_000_000)[1]
    ack = json.dumps(
        {
            "id": "ctccpublicdiagnostic1",
            "event": "subscribe",
            "arg": {"channel": "tickers", "instId": probe.base.INSTRUMENT},
            "connId": "a4d3ae55",
        }
    ).encode()
    result = probe.inspect_ws(ack, kind="ack", received=received)
    assert result["kind"] == "ack"
    ticker = json.dumps(
        {
            "arg": {"channel": "tickers", "instId": probe.base.INSTRUMENT},
            "data": [{"instId": probe.base.INSTRUMENT, "ts": "1791700000200"}],
        }
    ).encode()
    result = probe.inspect_ws(ticker, kind="ticker", received=received)
    assert result["source_not_future_at_receive"] is False
    assert result["source_minus_received_ns"] == 100_000_000
    wrong = ticker.replace(b"BTC-USDT-SWAP", b"ETH-USDT-SWAP", 1)
    with pytest.raises(ValueError, match="ws_source_scope_invalid"):
        probe.inspect_ws(wrong, kind="ticker", received=received)


def test_duplicate_json_key_rejected_before_interpretation():
    raw = b'{"code":"0","code":"0","msg":"","data":[{"ts":"1791700000000"}]}'
    with pytest.raises(ValueError, match="source_json_invalid"):
        probe.inspect_rest("time_pre", raw, _stamps(1_791_700_000_000))


@pytest.mark.parametrize("invalid", [b"NaN", b"Infinity", b"-Infinity", b"1e999"])
def test_non_json_or_overflowed_numbers_rejected_before_canonical_hash(invalid):
    raw = (
        b'{"code":"0","msg":"","data":[{"ts":"1791700000000","unexpected":'
        + invalid
        + b"}]}"
    )
    with pytest.raises(ValueError, match="source_json_invalid"):
        probe.inspect_rest("time_pre", raw, _stamps(1_791_700_000_000))
    ws = (
        b'{"id":"ctccpublicdiagnostic1","event":"subscribe",'
        b'"arg":{"channel":"tickers","instId":"BTC-USDT-SWAP"},'
        b'"connId":"a4d3ae55","unexpected":' + invalid + b"}"
    )
    with pytest.raises(ValueError, match="source_json_invalid"):
        probe.inspect_ws(ws, kind="ack", received=_stamps(1_791_700_000_000)[1])


@pytest.mark.parametrize(
    "extra",
    [{"code": "60012"}, {"code": "0"}, {"msg": ""}],
)
def test_ws_ack_rejects_error_and_noncanonical_success_envelopes(extra):
    ack = {
        "id": "ctccpublicdiagnostic1",
        "event": "subscribe",
        "arg": {"channel": "tickers", "instId": probe.base.INSTRUMENT},
        "connId": "a4d3ae55",
        **extra,
    }
    with pytest.raises(ValueError, match="ws_ack_invalid"):
        probe.inspect_ws(
            json.dumps(ack).encode(),
            kind="ack",
            received=_stamps(1_791_700_000_000)[1],
        )
