"""Synthetic checks against N162 raw ownership with no provider activity."""

from decimal import Decimal

import pytest

from app.domain.source_primitives import decode, sha
from app.trade_qualification import global_public_runtime_v1 as raw
from app.trade_qualification.global_public_runtime_v2 import (
    capture_initial_global_public_market_v2,
    read_global_public_capture_v2,
)
from tests.unit.research.test_owned_global_public_runtime_v1 import (
    market as market,  # noqa: PLC0414 -- register the existing fixture.
)
from tests.unit.research.test_owned_global_public_runtime_v1 import (
    owned_global as owned_global,  # noqa: PLC0414 -- register the existing fixture.
)
from tests.unit.test_qualification_public_market_collector import policy


def pins(result):
    return {
        "expected_plan_sha256": result.plan_sha256,
        "expected_journal_sha256": result.journal_sha256,
    }


@pytest.mark.asyncio
async def test_typed_ticker_exactly_replays_and_cannot_widen_admission(
    owned_global, tmp_path
):
    directory, harness = owned_global
    original = harness.public

    def server(request):
        rows = original(request)
        if request.url.path.endswith("/ticker"):
            rows[0].update(
                vol24h="987654321.010",
                volCcy24h="0.0012300",
                volCcyQuote="123456789",
            )
        return rows

    harness.public = server
    result = await capture_initial_global_public_market_v2(
        tmp_path, instrument_id="BTC-USDT-SWAP", market_policy=policy(counts=(240,) * 4)
    )
    assert result.code == "initial_global_public_typed_captured"
    assert result.reason is None
    journal_before = dict(directory.content)
    requests_before = len(harness.requests)
    replayed = read_global_public_capture_v2(tmp_path, **pins(result))
    legacy = raw.read_global_public_capture_v1(tmp_path, **pins(result))
    assert replayed.code == "initial_global_public_typed_replayed"
    assert replayed.typed_receipt_json == result.typed_receipt_json
    assert replayed.typed_receipt_sha256 == sha(result.typed_receipt_json)
    assert directory.content == journal_before
    assert len(harness.requests) == requests_before
    assert replayed.admission == legacy.admission == "DENY"
    assert replayed.account_requests == replayed.order_writes == 0
    assert replayed.source_authenticity_verified is False
    assert replayed.qualification_performed is replayed.execution_authority is False
    doc = decode(replayed.typed_receipt_json)
    assert doc["schema_version"] == "ctcc.public.global_typed_ticker_receipt.v2"
    assert doc["environment"] == "anonymous_global_public"
    assert doc["instrument_id"] == "BTC-USDT-SWAP"
    assert doc["plan_sha256"] == result.plan_sha256
    assert doc["journal_sha256"] == result.journal_sha256
    assert doc["raw_packet_sha256"] == legacy.packet.bundle_sha256
    assert (
        doc["ticker_response_sha256"] == legacy.packet.quote.provenance[0].body_sha256
    )
    assert Decimal(doc["ticker"]["volume_contracts_24h"]) == Decimal("987654321.010")
    assert Decimal(doc["ticker"]["volume_currency_24h"]) == Decimal("0.0012300")
    assert doc["ticker"]["volume_quote_24h"] is None
    assert doc["admission"] == "DENY"
    assert doc["account_requests"] == doc["order_writes"] == 0
    assert doc["source_authenticity_verified"] is False
    assert doc["qualification_performed"] is doc["execution_authority"] is False
    with pytest.raises(raw.GlobalPublicCaptureError, match="global_public_readback"):
        read_global_public_capture_v2(
            tmp_path,
            expected_plan_sha256=result.plan_sha256,
            expected_journal_sha256="0" * 64,
        )
    harness.assert_closed()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("volCcy24h", None),
        ("vol24h", 100),
        ("vol24h", "1e9"),
        ("volCcy24h", "-0.5"),
        ("volCcy24h", "1" * 41),
    ],
    ids=["truncated", "json-integer", "exponent", "negative", "overwidth"],
)
@pytest.mark.asyncio
async def test_bad_ticker_refuses_v2_and_preserves_owned_v1(
    owned_global, tmp_path, field, value
):
    directory, harness = owned_global
    original = harness.public

    def server(request):
        rows = original(request)
        if request.url.path.endswith("/ticker"):
            if value is None:
                del rows[0][field]
            else:
                rows[0][field] = value
        return rows

    harness.public = server
    result = await capture_initial_global_public_market_v2(
        tmp_path, instrument_id="BTC-USDT-SWAP", market_policy=policy(counts=(240,) * 4)
    )
    assert result.code == "initial_global_public_typed_denied"
    assert result.reason == "typed_ticker_incomplete"
    assert result.admission == "DENY"
    assert result.typed_receipt_json is result.typed_receipt_sha256 is None
    journal_before = dict(directory.content)
    requests_before = len(harness.requests)
    legacy = raw.read_global_public_capture_v1(tmp_path, **pins(result))
    assert legacy.code == "initial_global_public_replayed"
    assert legacy.packet.bundle_sha256 == result.raw_packet_sha256
    assert read_global_public_capture_v2(tmp_path, **pins(result)) == result
    assert directory.content == journal_before
    assert len(harness.requests) == requests_before
    harness.assert_closed()
