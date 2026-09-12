"""Synthetic public-packet bridge evidence, not observed Shadow/Demo samples."""

import hashlib
import json
import os
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.domain.market import MarketSnapshot, SwapTickerV2
from app.exchange.okx.parsers import parse_swap_ticker_v2, parse_ticker
from app.trade_qualification.data import _canonical, _market_copy
from app.trade_qualification.engine import evaluate_pre_evidence, verify_pre_evidence
from app.trade_qualification.market_bridge import (
    _snapshot,
    public_market_snapshot,
    qualify_public_market,
)
from tests.unit.qualification_engine_fixtures import engine_inputs, engine_source
from tests.unit.qualification_prefix_fixtures import (
    DATA_POLICY,
    capture_prefix_source,
    prefix_market,
    qualify_data,
    restore_source,
)
from tests.unit.test_qualification_evidence_gate import assert_g12, publish
from tests.unit.test_qualification_public_market_collector import capture


def row():
    return {
        "instType": "SWAP",
        "instId": "BTC-USDT-SWAP",
        "last": "100.005",
        "bidPx": "100",
        "askPx": "100.01",
        "bidSz": "2",
        "askSz": "3",
        "open24h": "99",
        "high24h": "102",
        "low24h": "98",
        "vol24h": "12345.67",
        "volCcy24h": "123.4567",
        "ts": "1789175401000",
    }


def full_ticker(request, value):
    if request.url.path == "/api/v5/market/ticker":
        for key in ("last", "open24h", "high24h", "low24h", "vol24h", "volCcy24h"):
            value["data"][0][key] = row()[key]
    elif request.url.path == "/api/v5/market/books":
        for index, level in enumerate(value["data"][0]["bids"]):
            level[0] = str(Decimal(100) - Decimal("0.01") * index)
        for index, level in enumerate(value["data"][0]["asks"]):
            level[0] = str(Decimal("100.01") + Decimal("0.01") * index)


def test_v1_source_hash_and_serialization_unchanged():
    market = prefix_market()
    assert hashlib.sha256(_canonical(market)).hexdigest() == (
        "1fbcb54f20e73e382d4271d6a86eaa8bb0944205e3ddd292dc5982ec76828fa8"
    )
    assert _market_copy(market) == market
    assert "schema_version" not in market.ticker.model_dump()


@pytest.mark.parametrize("instrument", ["BTC-USD-SWAP", "NEW-USDT-SWAP"])
def test_bridge_never_infers_settlement_from_arbitrary_instrument(instrument):
    # Test the mapping rejection before touching source fields. The public API
    # separately requires a complete replayed exact CollectedPublicMarket.
    with pytest.raises(ValueError, match="unsupported instrument"):
        _snapshot(SimpleNamespace(instrument_id=instrument))


def test_new_swap_volume_units_and_exact_timestamp_roundtrip():
    ticker = parse_ticker(row())
    assert type(ticker) is SwapTickerV2
    assert ticker.volume_contracts_24h == Decimal("12345.67")
    assert ticker.volume_currency_24h == Decimal("123.4567")
    assert ticker.volume_quote_24h is None
    assert "volume_24h" not in ticker.model_dump()
    assert (
        SwapTickerV2.model_validate_json(ticker.model_dump_json(), strict=True)
        == ticker
    )
    market = prefix_market()
    market.ticker = ticker
    restored = MarketSnapshot.model_validate_json(market.model_dump_json(), strict=True)
    assert type(restored.ticker) is SwapTickerV2
    assert _market_copy(market) == restored


@pytest.mark.parametrize(
    "value", [None, "", "NaN", "Infinity", "-1", "1e3", 0, True, "1" * 41]
)
@pytest.mark.parametrize("field", ["vol24h", "volCcy24h"])
def test_unknown_or_malformed_volume_never_becomes_zero(field, value):
    raw = row()
    raw[field] = value
    with pytest.raises(ValueError):
        parse_swap_ticker_v2(raw)


@pytest.mark.parametrize(
    "field",
    ["last", "open24h", "high24h", "low24h", "vol24h", "volCcy24h", "instType", "ts"],
)
def test_missing_source_fields_rejected(field):
    raw = row()
    del raw[field]
    with pytest.raises(ValueError):
        parse_swap_ticker_v2(raw)


@pytest.mark.parametrize(
    "edit",
    [
        lambda data: data.pop("schema_version"),
        lambda data: data.update(schema_version="okx-swap-ticker-v3"),
        lambda data: data.update(volume_24h="12345.67"),
        lambda data: data.update(volume_quote_24h="0"),
        lambda data: data.update(volume_quote_24h="12345.67"),
        lambda data: data.update(unknown="hidden"),
    ],
)
def test_mixed_version_or_estimated_quote_turnover_cannot_replay(edit):
    market = prefix_market()
    market.ticker = parse_ticker(row())
    wire = json.loads(market.model_dump_json())
    edit(wire["ticker"])
    with pytest.raises(ValidationError):
        MarketSnapshot.model_validate_json(json.dumps(wire), strict=True)


@pytest.mark.asyncio
async def test_real_g1_v2_pass_and_source_replay():
    source = await capture_prefix_source()
    market = source.market.model_copy(deep=True)
    values = market.ticker.model_dump()
    contracts = values.pop("volume_24h")
    values.pop("volume_quote_24h")
    market.ticker = SwapTickerV2(
        **values,
        schema_version="okx-swap-ticker-v2",
        volume_contracts_24h=contracts,
        volume_currency_24h=Decimal("123.4567"),
    )
    data = qualify_data(replace(source, market=market))
    assert data.gate.passed, data.gate
    restored, _ = restore_source(data)
    assert type(restored.ticker) is SwapTickerV2
    assert restored.ticker.volume_quote_24h is None
    assert not data.execution_authority and not data.source_authenticity_verified
    market.ticker.volume_currency_24h += 1
    changed = qualify_data(replace(source, market=market))
    assert changed.source_sha256 != data.source_sha256


@pytest.mark.asyncio
async def test_public_packet_bridge_replays_raw_and_runs_actual_g1(monkeypatch):
    packet, *_ = await capture(monkeypatch, response_edit=full_ticker)
    market = public_market_snapshot(packet, expected_bundle_sha256=packet.bundle_sha256)
    assert type(market.ticker) is SwapTickerV2
    assert market.ticker.volume_currency_24h == Decimal("123.4567")
    assert market.ticker.volume_quote_24h is None
    assert tuple(market.candles) == ("4H", "1H", "15m", "5m")
    assert market.received_at == packet.completed_at
    result = qualify_public_market(
        packet,
        expected_bundle_sha256=packet.bundle_sha256,
        policy=DATA_POLICY,
        evaluated_at=packet.completed_at + timedelta(milliseconds=1),
    )
    assert result.source_json is not None, result.gate
    assert (
        json.loads(result.source_json)["market"]["ticker"]["schema_version"]
        == "okx-swap-ticker-v2"
    )
    assert result.execution_authority is False
    assert result.source_authenticity_verified is False
    # The public packet does not claim a qualifying signal or a trustworthy socket.
    assert packet.qualification_performed is False
    market.ticker.volume_currency_24h = Decimal(999)
    fresh = public_market_snapshot(packet, expected_bundle_sha256=packet.bundle_sha256)
    assert fresh.ticker.volume_currency_24h == Decimal("123.4567")


@pytest.mark.asyncio
async def test_minimal_quote_packet_is_not_fabricated_into_full_market(monkeypatch):
    packet, *_ = await capture(monkeypatch)
    with pytest.raises(ValueError, match="decimal_invalid"):
        public_market_snapshot(packet, expected_bundle_sha256=packet.bundle_sha256)


@pytest.mark.asyncio
async def test_external_pin_and_nested_mutation_rejected(monkeypatch):
    packet, *_ = await capture(monkeypatch, response_edit=full_ticker)
    with pytest.raises(ValueError, match="pin_mismatch"):
        public_market_snapshot(packet, expected_bundle_sha256="0" * 64)
    with pytest.raises(ValueError, match="pin_invalid"):
        public_market_snapshot(
            packet, expected_bundle_sha256=packet.bundle_sha256.upper()
        )
    observation = packet.quote.provenance[0]
    object.__setattr__(
        observation,
        "response_body",
        observation.response_body.replace(b"123.4567", b"923.4567"),
    )
    with pytest.raises(ValueError):
        public_market_snapshot(packet, expected_bundle_sha256=packet.bundle_sha256)


def test_nested_unexpected_serializer_rejected_before_callback():
    calls = []
    market = prefix_market()
    ticker = parse_ticker(row())
    ticker.__dict__["model_dump"] = lambda **kwargs: calls.append(True)
    market.ticker = ticker
    with pytest.raises(ValueError):
        _market_copy(market)
    assert calls == []


def v2_engine_source(direction):
    source = engine_source(direction)
    values = source.market.ticker.model_dump()
    contracts = values.pop("volume_24h")
    values.pop("volume_quote_24h")
    source.market.ticker = SwapTickerV2(
        **values,
        schema_version="okx-swap-ticker-v2",
        volume_contracts_24h=contracts,
        volume_currency_24h=Decimal("123.4567"),
    )
    return source


@pytest.mark.parametrize("direction", ["long", "short"])
def test_v2_actual_g1_to_g11_and_strict_replay(direction):
    source = v2_engine_source(direction)
    inputs = engine_inputs(source)
    run = evaluate_pre_evidence(source.market, **inputs)
    assert run.pre_evidence_complete, run.result.fail_codes
    assert verify_pre_evidence(run, source.market, **inputs) == run
    source.market.ticker.volume_currency_24h += 1
    with pytest.raises(ValueError, match="replay_mismatch"):
        verify_pre_evidence(run, source.market, **inputs)


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX native publication; no Windows ancestor-pin success claim",
)
@pytest.mark.parametrize("direction", ["long", "short"])
def test_v2_actual_g12_six_file_readback_remains_unauthorized(direction, tmp_path):
    source = v2_engine_source(direction)
    inputs = engine_inputs(source)
    run = evaluate_pre_evidence(source.market, **inputs)
    assert run.pre_evidence_complete, run.result.fail_codes
    root = tmp_path / "synthetic-v2-evidence"
    root.mkdir()
    result = publish(source, inputs, run, root)
    assert_g12(result, run, "passed")
    assert result.receipt is not None
    assert len(result.receipt.files) == 6
    assert result.result.qualified is False
