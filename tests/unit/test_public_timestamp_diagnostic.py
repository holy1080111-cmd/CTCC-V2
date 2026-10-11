"""Offline checks for the intentionally non-authoritative public probe."""

from __future__ import annotations

import json

import pytest

from scripts import diagnose_public_timestamp as diagnostic


def _stamps(ts_ms: int):
    base = ts_ms * 1_000_000
    return tuple(
        {"utc_ns": base + offset, "monotonic_ns": 10_000_000_000 + offset}
        for offset in (0, 100_000_000, 150_000_000, 160_000_000)
    )


def _raw(ts_ms: int, *, inst_id: str | None = diagnostic.INSTRUMENT) -> bytes:
    row = {"ts": str(ts_ms)}
    if inst_id is not None:
        row["instId"] = inst_id
    return json.dumps({"code": "0", "msg": "", "data": [row]}).encode()


def test_future_book_generation_is_reported_without_rewriting_raw_or_granting_authority():
    ts_ms = 1_791_700_000_150
    raw = _raw(ts_ms, inst_id=None)
    result = diagnostic.inspect_raw("books5", raw, _stamps(ts_ms - 150))
    assert result["source_ts_raw"] == str(ts_ms)
    assert result["source_minus_headers_received_ns"] == 50_000_000
    assert result["source_not_future_at_headers"] is False
    assert result["body_sha256"] == diagnostic._sha(raw)
    assert result["requested_instrument_id"] == diagnostic.INSTRUMENT
    assert result["response_instrument_id"] is None
    assert result["response_instrument_verified"] is False
    assert "execution_authority" not in result


def test_stale_funding_return_time_remains_original_and_distinct_from_book_generation():
    current_ms = 1_791_700_000_000
    raw = _raw(current_ms - 12_000)
    result = diagnostic.inspect_raw("funding", raw, _stamps(current_ms))
    assert result["timestamp_semantics"] == "exchange_data_return"
    assert result["source_minus_headers_received_ns"] == -12_100_000_000
    assert result["source_ts_raw"] == str(current_ms - 12_000)
    assert result["response_instrument_verified"] is True


@pytest.mark.parametrize(
    ("role", "raw", "error"),
    [
        (
            "open_interest",
            b'{"code":"0","msg":"","data":[{"ts":"1791700000000","ts":"1791700000001","instId":"BTC-USDT-SWAP"}]}',
            "source_json_invalid",
        ),
        (
            "funding",
            _raw(1_791_700_000_000, inst_id="ETH-USDT-SWAP"),
            "source_instrument_mismatch",
        ),
    ],
)
def test_duplicate_json_key_and_other_instrument_are_rejected(role, raw, error):
    with pytest.raises(ValueError, match=error):
        diagnostic.inspect_raw(role, raw, _stamps(1_791_700_000_000))


@pytest.mark.asyncio
async def test_client_has_no_proxy_redirect_auth_or_retries():
    client = diagnostic._client()
    try:
        assert diagnostic._assert_isolated(client)
        assert client.trust_env is False
        assert client.follow_redirects is False
        assert client.auth is None
        assert len(diagnostic.REQUESTS) == 5
        assert all(path.startswith("/api/v5/") for _, path, _ in diagnostic.REQUESTS)
    finally:
        await client.aclose()
