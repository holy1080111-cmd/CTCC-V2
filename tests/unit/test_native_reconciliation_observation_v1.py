"""Engineering bounds and retained DATA; no native source owner is fabricated."""

import pytest

from app.exchange.okx import native_reconciliation_observation_v1 as data


@pytest.mark.parametrize(
    "path,query",
    (
        ("/api/v5/trade/order", {"instId": "BTC-USDT-SWAP", "clOrdId": "ctcc123"}),
        ("/api/v5/account/config", {}),
        ("/api/v5/account/balance", {}),
        ("/api/v5/trade/orders-pending", {"limit": "100", "after": "123"}),
        (
            "/api/v5/trade/fills",
            {
                "instType": "SWAP",
                "instId": "BTC-USDT-SWAP",
                "ordId": "123",
                "limit": "100",
            },
        ),
    ),
)
def test_exact_read_vocabulary_does_not_claim_account_completeness(path, query):
    assert data.checked_query(path, query) == tuple(sorted(query.items()))


@pytest.mark.parametrize(
    "path,query",
    (
        ("/api/v5/trade/cancel-order", {}),
        ("/api/v5/account/positions", {"instId": "BTC-USDT-SWAP"}),
        ("/api/v5/account/balance", {"ccy": "USDT"}),
        ("/api/v5/trade/orders-pending", {"limit": "100", "after": "0"}),
        ("/api/v5/trade/orders-pending", {"limit": "100", "instId": "BTC-USDT-SWAP"}),
        (
            "/api/v5/trade/order",
            {"instId": "BTC-USDT-SWAP", "clOrdId": "x", "ordId": "123"},
        ),
        (
            "/api/v5/trade/fills",
            {"instType": "SWAP", "instId": "BTC-USDT-SWAP", "limit": "100"},
        ),
    ),
)
def test_mutations_cannot_turn_filtered_or_wrong_route_data_into_full_capture(
    path, query
):
    with pytest.raises(ValueError, match="^native_reconciliation_read_data_invalid$"):
        data.checked_query(path, query)


def journal():
    return data.ReadJournalV1(
        "/api/v5/account/config",
        (),
        "https://www.okx.com/api/v5/account/config",
        "a" * 64,
    )


def test_actual_final_chunk_and_eof_remain_after_late_failure():
    captured = journal()
    captured.retain_chunk(b'{"code":"0",')
    captured.retain_chunk(b'"data":[]}')
    captured.retain_eof()
    captured.fault("read_failed")
    assert b"".join(captured.chunks) == b'{"code":"0","data":[]}'
    assert captured.eof_stamp is not None
    assert captured.observation is None
    with pytest.raises(ValueError):
        captured.finish()


def test_body_overflow_retains_bounded_prefix_and_never_claims_eof():
    captured = journal()
    captured.retain_chunk(b"x" * data.MAX_BODY)
    with pytest.raises(ValueError):
        captured.retain_chunk(b"x")
    assert captured.body_bytes == data.MAX_BODY and captured.eof_stamp is None
    assert b"".join(captured.chunks) == b"x" * data.MAX_BODY


def test_complete_data_cannot_ignore_close_failure_or_content_length_mismatch():
    captured = journal()
    captured.signature = "synthetic-data-only"
    captured.headers_stamp = data.stamp()
    captured.status = 200
    captured.certificate_sha256 = "b" * 64
    captured.tls_hostname = "www.okx.com"
    captured.retain_chunk(b"{}")
    captured.retain_eof()
    captured.declared_length = 3
    with pytest.raises(ValueError):
        captured.finish()
    captured.declared_length = 2
    observation = captured.finish()
    captured.fault("close_failed")
    assert observation.raw_body == b"{}"
    assert observation.account_complete is False
    assert observation.execution_authority is False
    assert observation.order_retry_authority is False
    assert captured.faults == ["close_failed"]
