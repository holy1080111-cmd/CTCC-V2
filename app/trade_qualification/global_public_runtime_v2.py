"""Typed readback and market projection for the anonymous Global raw journal."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.domain.market import MarketSnapshot, SwapTickerV2
from app.domain.source_primitives import canonical, sha
from app.exchange.okx.parsers import parse_swap_ticker_v2
from app.exchange.okx.symbols import to_canonical_symbol
from app.market.quality.candles import inspect_candles_at
from app.trade_qualification import global_public_runtime_v1 as raw
from app.trade_qualification import quote_collector
from app.trade_qualification.funding_observation_v2 import parse_funding_observation_v2
from app.trade_qualification.public_market_collector import PublicMarketCollectionPolicy

_RECEIPT_SCHEMA = "ctcc.public.global_typed_ticker_receipt.v2"
_SuccessCode = Literal[
    "initial_global_public_typed_captured",
    "initial_global_public_typed_replayed",
]
_Refusal = Literal[
    "raw_capture_denied",
    "raw_readback_refused",
    "typed_ticker_incomplete",
]


@dataclass(frozen=True, slots=True)
class GlobalPublicCaptureV2:
    code: Literal["initial_global_public_typed_denied"] | _SuccessCode
    reason: _Refusal | None = None
    plan_sha256: str | None = None
    journal_sha256: str | None = None
    raw_packet_sha256: str | None = None
    typed_receipt_json: bytes | None = field(default=None, repr=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)
    account_requests: Literal[0] = field(default=0, init=False)
    order_writes: Literal[0] = field(default=0, init=False)
    source_authenticity_verified: Literal[False] = field(default=False, init=False)
    qualification_performed: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)

    @property
    def typed_receipt_sha256(self) -> str | None:
        return None if self.typed_receipt_json is None else sha(self.typed_receipt_json)


def _parse_ticker(
    observation: quote_collector.EndpointObservation, instrument_id: str
) -> SwapTickerV2:
    payload, _ = quote_collector._parse(observation.response_body)
    row = quote_collector._row(payload, instrument_id)
    return parse_swap_ticker_v2(row)


def project_global_public_market_snapshot_v2(
    packet: raw.GlobalPublicComponentPacketV1, *, evaluated_at: datetime
) -> tuple[MarketSnapshot, datetime]:
    """Project a reopened raw packet and retain its following funding forecast."""
    instrument_id = packet.instrument_id
    ticker_raw, mark_raw, funding_raw = packet.quote.provenance
    ticker = _parse_ticker(ticker_raw, instrument_id)
    (mark_price,) = quote_collector._values(
        "mark",
        quote_collector._row(
            quote_collector._parse(mark_raw.response_body)[0], instrument_id
        ),
    )
    funding = parse_funding_observation_v2(
        funding_raw.response_body,
        source_kind="rest",
        instrument_id=instrument_id,
        acquisition_started_at=funding_raw.request_started_at,
        received_at=funding_raw.received_at,
        completed_at=funding_raw.completed_at,
    )
    series = {frame.timeframe: list(frame.candles) for frame in packet.candles.frames}
    quality = {
        bar: inspect_candles_at(rows, bar, current_time=evaluated_at)
        for bar, rows in series.items()
    }
    market = MarketSnapshot(
        symbol=to_canonical_symbol(instrument_id),
        instrument_id=instrument_id,
        ticker=ticker,
        mark_price=mark_price,
        funding_rate=funding.forecast.rate,
        next_funding_time=funding.forecast.settlement_at,
        open_interest_contracts=packet.market_aux.open_interest.contracts,
        open_interest_currency=packet.market_aux.open_interest.currency,
        order_book=packet.market_aux.book.order_book,
        candles=series,
        quality=quality,
        received_at=max(
            *(row.completed_at for row in packet.quote.provenance),
            *(row.completed_at for row in packet.market_aux.provenance),
            *(
                page.completed_at
                for frame in packet.candles.frames
                for page in frame.pages
            ),
            packet.ws.ticker.received_at,
        ),
    )
    return market, funding.following_settlement_forecast_at


def matches_global_public_market_identity_v2(
    market: MarketSnapshot, instrument_id: str
) -> bool:
    """Check the exact instrument on the normalized snapshot and both quote models."""
    return (
        type(market) is MarketSnapshot
        and type(market.ticker) is SwapTickerV2
        and market.instrument_id == instrument_id
        and market.ticker.instrument_id == instrument_id
        and market.order_book.instrument_id == instrument_id
        and market.symbol == to_canonical_symbol(instrument_id)
    )


def _read_typed(
    root: Path,
    *,
    expected_plan_sha256: str,
    expected_journal_sha256: str,
    success_code: _SuccessCode,
) -> GlobalPublicCaptureV2:
    reopened = raw.read_global_public_capture_v1(
        root,
        expected_plan_sha256=expected_plan_sha256,
        expected_journal_sha256=expected_journal_sha256,
    )
    packet = reopened.packet
    if type(packet) is not raw.GlobalPublicComponentPacketV1:
        raise raw.GlobalPublicCaptureError("global_public_readback_unavailable")
    observation = packet.quote.provenance[0]
    if observation.role != "ticker":
        raise raw.GlobalPublicCaptureError("global_public_readback_unavailable")
    try:
        ticker = _parse_ticker(observation, packet.instrument_id)
    except ValueError:
        return GlobalPublicCaptureV2(
            "initial_global_public_typed_denied",
            reason="typed_ticker_incomplete",
            plan_sha256=expected_plan_sha256,
            journal_sha256=expected_journal_sha256,
            raw_packet_sha256=packet.bundle_sha256,
        )
    packet_sha256 = packet.bundle_sha256
    receipt = canonical(
        {
            "schema_version": _RECEIPT_SCHEMA,
            "environment": packet.environment,
            "instrument_id": packet.instrument_id,
            "plan_sha256": expected_plan_sha256,
            "journal_sha256": expected_journal_sha256,
            "raw_packet_sha256": packet_sha256,
            "ticker_response_sha256": observation.body_sha256,
            "ticker": ticker.model_dump(mode="json"),
            "admission": "DENY",
            "account_requests": 0,
            "order_writes": 0,
            "source_authenticity_verified": False,
            "qualification_performed": False,
            "execution_authority": False,
        }
    )
    return GlobalPublicCaptureV2(
        success_code,
        plan_sha256=expected_plan_sha256,
        journal_sha256=expected_journal_sha256,
        raw_packet_sha256=packet_sha256,
        typed_receipt_json=receipt,
    )


async def capture_initial_global_public_market_v2(
    root: Path, *, instrument_id: str, market_policy: PublicMarketCollectionPolicy
) -> GlobalPublicCaptureV2:
    """Capture once via the owned raw path, then require exact typed readback."""
    capture = await raw.capture_initial_global_public_market_v1(
        root, instrument_id=instrument_id, market_policy=market_policy
    )
    if (
        capture.code != "initial_global_public_captured"
        or capture.plan_sha256 is None
        or capture.journal_sha256 is None
    ):
        return GlobalPublicCaptureV2(
            "initial_global_public_typed_denied", reason="raw_capture_denied"
        )
    try:
        return _read_typed(
            root,
            expected_plan_sha256=capture.plan_sha256,
            expected_journal_sha256=capture.journal_sha256,
            success_code="initial_global_public_typed_captured",
        )
    except raw.GlobalPublicCaptureError:
        return GlobalPublicCaptureV2(
            "initial_global_public_typed_denied", reason="raw_readback_refused"
        )


def read_global_public_capture_v2(
    root: Path, *, expected_plan_sha256: str, expected_journal_sha256: str
) -> GlobalPublicCaptureV2:
    """Replay the raw journal and regenerate the same separately versioned receipt."""
    return _read_typed(
        root,
        expected_plan_sha256=expected_plan_sha256,
        expected_journal_sha256=expected_journal_sha256,
        success_code="initial_global_public_typed_replayed",
    )
