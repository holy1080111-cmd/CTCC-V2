"""Replay full predecision market inputs for a frozen legacy baseline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from app.domain.market import MarketSnapshot
from app.domain.source_primitives import (
    ClockStamp,
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.exchange.okx.parsers import parse_swap_ticker_v2
from app.exchange.okx.symbols import to_canonical_symbol
from app.market.quality.candles import inspect_candles_at
from app.mie.validation.contracts import require_utc
from app.trade_qualification import candle_collector as candles
from app.trade_qualification import global_public_runtime_v1 as public
from app.trade_qualification import quote_collector as quotes
from app.trade_qualification.funding_observation_v2 import parse_funding_observation_v2


class LegacyBaselineMarketReplayError(ValueError):
    """Fixed refusal without a path or raw market payload."""


@dataclass(frozen=True, slots=True, repr=False)
class ReplayedLegacyBaselineMarketV1:
    market: MarketSnapshot
    receipt_json: bytes
    source_authenticity_verified: Literal[False] = field(default=False, init=False)
    independent_availability_verified: Literal[False] = field(default=False, init=False)
    predictive_oos_eligible: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)


def replay_legacy_baseline_market_v1(
    root: Path,
    *,
    instrument_id: str,
    decision_at: datetime,
    expected_plan_sha256: str,
    expected_journal_sha256: str,
    expected_packet_sha256: str,
    expected_market_policy_sha256: str,
    post_readback_receipt_json: bytes,
    expected_post_readback_receipt_sha256: str,
) -> ReplayedLegacyBaselineMarketV1:
    """Join independently retained pins to a reopened native public journal."""
    if (
        type(decision_at) is not datetime
        or type(instrument_id) is not str
        or type(expected_packet_sha256) is not str
        or type(expected_market_policy_sha256) is not str
        or type(expected_post_readback_receipt_sha256) is not str
    ):
        raise LegacyBaselineMarketReplayError("legacy_market_replay_refused")
    try:
        decision = require_utc(decision_at, "legacy decision")
        if (
            type(post_readback_receipt_json) is not bytes
            or not 0 < len(post_readback_receipt_json) <= 1024
            or sha(post_readback_receipt_json) != expected_post_readback_receipt_sha256
        ):
            raise LegacyBaselineMarketReplayError(
                "legacy_market_post_readback_pin_changed"
            )
        receipt_data = decode(post_readback_receipt_json, 1024)
        readback_sample = ClockStamp.model_validate(receipt_data["sample"])
        capture = public.read_global_public_capture_v1(
            root,
            expected_plan_sha256=expected_plan_sha256,
            expected_journal_sha256=expected_journal_sha256,
        )
        packet = capture.packet
        completion_sample = capture.journal_completion_sample
        deadline = capture.invocation_expires_at
        if (
            type(packet) is not public.GlobalPublicComponentPacketV1
            or packet.instrument_id != instrument_id
            or packet.bundle_sha256 != expected_packet_sha256
            or packet.policy_sha256 != expected_market_policy_sha256
        ):
            raise LegacyBaselineMarketReplayError("legacy_market_source_scope_changed")
        if post_readback_receipt_json != public._post_readback_receipt(
            packet, expected_plan_sha256, expected_journal_sha256, readback_sample
        ):
            raise LegacyBaselineMarketReplayError(
                "legacy_market_post_readback_scope_changed"
            )
        if type(completion_sample) is not ClockStamp or type(deadline) is not datetime:
            raise LegacyBaselineMarketReplayError("legacy_market_replay_refused")
        validate_stamps((completion_sample.model_dump(), readback_sample.model_dump()))
        completed = utc_from_ns(completion_sample.utc_ns)
        readback_at = utc_from_ns(readback_sample.utc_ns)
        if readback_at >= deadline:
            raise LegacyBaselineMarketReplayError("legacy_market_post_readback_expired")
        if (
            not packet.completed_at <= completed <= readback_at <= decision
            or readback_sample.monotonic_ns <= completion_sample.monotonic_ns
        ):
            raise LegacyBaselineMarketReplayError(
                "legacy_market_not_available_at_decision"
            )
        for component in (packet.quote, packet.market_aux):
            if any(
                not timedelta(0)
                <= decision - observation.source_time
                <= timedelta(seconds=component.policy.max_age_seconds)
                for observation in component.provenance
            ):
                raise LegacyBaselineMarketReplayError("legacy_market_component_stale")
        if (
            not timedelta(0)
            <= decision - packet.ws.ticker.source_time
            <= timedelta(seconds=packet.ws.policy.max_age_seconds)
        ):
            raise LegacyBaselineMarketReplayError("legacy_market_component_stale")
        if any(
            frame.verified_through != candles._floor(decision, frame.timeframe)
            for frame in packet.candles.frames
        ):
            raise LegacyBaselineMarketReplayError("legacy_market_candle_tail_missing")

        ticker_raw, mark_raw, funding_raw = packet.quote.provenance
        ticker = parse_swap_ticker_v2(
            quotes._row(quotes._parse(ticker_raw.response_body)[0], instrument_id)
        )
        (mark_price,) = quotes._values(
            "mark",
            quotes._row(quotes._parse(mark_raw.response_body)[0], instrument_id),
        )
        funding = parse_funding_observation_v2(
            funding_raw.response_body,
            source_kind="rest",
            instrument_id=instrument_id,
            acquisition_started_at=funding_raw.request_started_at,
            received_at=funding_raw.received_at,
            completed_at=funding_raw.completed_at,
        )
        if funding.forecast.settlement_at <= decision:
            raise LegacyBaselineMarketReplayError("legacy_market_funding_expired")
        series = {
            frame.timeframe: list(frame.candles) for frame in packet.candles.frames
        }
        quality = {
            bar: inspect_candles_at(rows, bar, current_time=decision)
            for bar, rows in series.items()
        }
        if not all(report.ok for report in quality.values()):
            raise LegacyBaselineMarketReplayError("legacy_market_candle_quality_failed")
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
        receipt = canonical(
            {
                "schema_version": "ctcc.mie.legacy_baseline_market_replay.v1",
                "environment": packet.environment,
                "instrument_id": instrument_id,
                "decision_at": decision.isoformat(),
                "journal_completed_at": completed.isoformat(),
                "post_readback_sample_at": readback_at.isoformat(),
                "capture_deadline_at": deadline.isoformat(),
                "post_readback_receipt_sha256": expected_post_readback_receipt_sha256,
                "snapshot_received_at": market.received_at.isoformat(),
                "funding_forecast_settlement_at": funding.forecast.settlement_at.isoformat(),
                "funding_following_forecast_at": funding.following_settlement_forecast_at.isoformat(),
                "plan_sha256": expected_plan_sha256,
                "journal_sha256": expected_journal_sha256,
                "packet_sha256": expected_packet_sha256,
                "market_policy_sha256": expected_market_policy_sha256,
                "snapshot_sha256": sha(canonical(market.model_dump(mode="json"))),
                "source_authenticity_verified": False,
                "independent_availability_verified": False,
                "predictive_oos_eligible": False,
                "execution_authority": False,
            }
        )
    except LegacyBaselineMarketReplayError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, ArithmeticError):
        raise LegacyBaselineMarketReplayError("legacy_market_replay_refused") from None
    return ReplayedLegacyBaselineMarketV1(market, receipt)
