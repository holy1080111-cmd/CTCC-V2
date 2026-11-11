"""Synthetic raw/replay comparison for N162, with no exchange or trading authority."""

from decimal import Decimal

import pytest

from app.exchange.okx.parsers import parse_swap_ticker_v2
from app.trade_qualification import global_public_runtime_v1 as global_public
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification import quote_collector
from app.trade_qualification.market_bridge_v2 import public_market_context_v2
from tests.unit.research.test_owned_global_public_runtime_v1 import (
    market as market,  # noqa: PLC0414 -- register the existing fixture.
)
from tests.unit.research.test_owned_global_public_runtime_v1 import (
    owned_global as owned_global,  # noqa: PLC0414 -- register the existing fixture.
)
from tests.unit.test_qualification_public_market_collector import policy


def ticker_row(packet):
    observation = packet.quote.provenance[0]
    assert observation.role == "ticker"
    raw, _ = quote_collector._parse(observation.response_body)
    return quote_collector._row(raw, packet.instrument_id)


@pytest.mark.asyncio
async def test_global_replay_distinguishes_swap_units_without_bridge_authority(
    owned_global, tmp_path
):
    _, harness = owned_global
    original = harness.public

    def server(request):
        rows = original(request)
        path = request.url.path
        if path.endswith("/candles"):
            for row in rows:
                row[5:8] = ["37", "0.037", "2190.31"]
        elif path.endswith("/ticker"):
            rows[0].update(vol24h="5139", volCcy24h="5.139")
        elif path.endswith("/open-interest"):
            rows[0].update(oi="87", oiCcy="0.087", oiUsd="5031.75")
        return rows

    harness.public = server
    result = await global_public.capture_initial_global_public_market_v1(
        tmp_path,
        instrument_id="BTC-USDT-SWAP",
        market_policy=policy(counts=(240,) * 4),
    )
    assert result.code == "initial_global_public_captured"
    replayed = global_public.read_global_public_capture_v1(
        tmp_path,
        expected_plan_sha256=result.plan_sha256,
        expected_journal_sha256=result.journal_sha256,
    )
    assert replayed.admission == "DENY"
    assert replayed.account_requests == replayed.order_writes == 0
    assert replayed.execution_authority is replayed.qualification_performed is False
    packet = replayed.packet
    assert type(packet) is global_public.GlobalPublicComponentPacketV1
    assert packet.source_authenticity_verified is False
    assert packet.bundle_sha256 == result.packet.bundle_sha256
    assert packet.candles.volume_units == (
        "contracts",
        "base_currency",
        "quote_currency",
    )
    for frame in packet.candles.frames:
        volumes = {
            (row.volume_contracts, row.volume_currency, row.volume_quote)
            for row in frame.candles
        }
        assert volumes == {(Decimal(37), Decimal("0.037"), Decimal("2190.31"))}

    interest = packet.market_aux.open_interest
    assert (interest.contracts, interest.currency, interest.usd) == (
        Decimal(87),
        Decimal("0.087"),
        Decimal("5031.75"),
    )
    ticker = parse_swap_ticker_v2(ticker_row(packet))
    assert ticker.volume_contracts_24h == Decimal(5139)
    assert ticker.volume_currency_24h == Decimal("5.139")
    assert ticker.volume_quote_24h is None
    with pytest.raises(
        public_v2.PublicMarketV2Error, match="^public_v2_exact_packet_required$"
    ):
        public_market_context_v2(
            packet,
            expected_bundle_sha256=packet.bundle_sha256,
            evaluated_at=packet.completed_at,
        )
    harness.assert_closed()


@pytest.mark.asyncio
async def test_global_quote_replay_does_not_prove_typed_ticker_volume(
    owned_global, tmp_path
):
    _, harness = owned_global
    original = harness.public

    def server(request):
        rows = original(request)
        if request.url.path.endswith("/ticker"):
            del rows[0]["volCcy24h"]
        return rows

    harness.public = server
    result = await global_public.capture_initial_global_public_market_v1(
        tmp_path,
        instrument_id="BTC-USDT-SWAP",
        market_policy=policy(counts=(240,) * 4),
    )
    assert result.code == "initial_global_public_captured"
    replayed = global_public.read_global_public_capture_v1(
        tmp_path,
        expected_plan_sha256=result.plan_sha256,
        expected_journal_sha256=result.journal_sha256,
    )
    assert replayed.admission == "DENY"
    assert replayed.account_requests == replayed.order_writes == 0
    row = ticker_row(replayed.packet)
    assert "volCcy24h" not in row
    with pytest.raises(ValueError, match="^swap_ticker_decimal_invalid$"):
        parse_swap_ticker_v2(row)
    harness.assert_closed()
