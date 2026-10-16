"""Cross-frame OHLC is reconstructed from raw pages, including offline replay."""

import json
from datetime import timedelta
from decimal import Decimal

import pytest

from app.public_market_source.public_market_receipts import canonical, decode, sha
from app.trade_qualification import candle_collector as candles
from app.trade_qualification import public_market_collector_v2 as public
from tests.unit.research.test_owned_public_runtime_v2 import (
    captured as captured,  # noqa: PLC0414 -- complete synthetic owner capture
)
from tests.unit.research.test_owned_public_runtime_v2 import routed_packet_parts
from tests.unit.research.test_owned_public_runtime_v2 import (
    source as source,  # noqa: PLC0414 -- dependency of captured fixture
)


def _changed_high(packet, timeframe, *, timestamp=None, minimum_high=None):
    frames = list(packet.frames)
    frame_index = next(
        index for index, frame in enumerate(frames) if frame.timeframe == timeframe
    )
    frame = frames[frame_index]
    target = (
        frame.confirmed[-1]
        if timestamp is None
        else next(row for row in frame.confirmed if row.timestamp == timestamp)
    )
    pages = list(frame.pages)
    page = pages[0]
    body = json.loads(page.response_body)
    row = next(
        row for row in body["data"] if candles._timestamp(row[0]) == target.timestamp
    )
    raised = max(
        target.high, target.high if minimum_high is None else minimum_high
    ) + Decimal(1)
    row[2] = str(raised)
    raw = canonical(body)
    _, canonical_text = candles._parse(raw)
    pages[0] = page.model_copy(
        update={
            "response_body": raw,
            "body_size_bytes": len(raw),
            "body_sha256": sha(raw),
            "canonical_json": canonical_text,
            "canonical_sha256": sha(canonical_text.encode()),
        }
    )
    confirmed = tuple(
        row.model_copy(update={"high": raised})
        if row.timestamp == target.timestamp
        else row
        for row in frame.confirmed
    )
    changed = frame.model_copy(update={"pages": tuple(pages), "confirmed": confirmed})
    frames[frame_index] = changed.model_copy(
        update={"frame_sha256": candles._hash(changed, omit="frame_sha256")}
    )
    result = packet.model_copy(update={"frames": tuple(frames)})
    return result.model_copy(
        update={"bundle_sha256": candles._hash(result, omit="bundle_sha256")}
    )


@pytest.mark.parametrize("timeframe", ["4H", "1H", "15m"])
def test_rehashed_raw_parent_conflict_is_denied_at_capture_and_readback(
    captured, timeframe
):
    parts = routed_packet_parts(captured, "global")
    accepted, _ = public._build(**parts)
    assert (
        public.replay_collected_public_market_v2(
            accepted.packet_json, expected_sha256=accepted.bundle_sha256
        ).packet_json
        == accepted.packet_json
    )

    parts["candle_packet"] = _changed_high(parts["candle_packet"], timeframe)
    with pytest.raises(
        public.PublicMarketV2Error, match="public_v2_candle_cross_frame_mismatch"
    ):
        public._build(**parts)

    document = decode(accepted.packet_json, public.MAX_PACKET_BYTES)
    document["candles"] = parts["candle_packet"].model_dump(
        mode="json", round_trip=True
    )
    raw = canonical(document)
    with pytest.raises(
        public.PublicMarketV2Error, match="public_v2_candle_cross_frame_mismatch"
    ):
        public.replay_collected_public_market_v2(raw, expected_sha256=sha(raw))


def test_rehashed_raw_5m_conflict_reaches_15m_child_check(captured):
    parts = routed_packet_parts(captured, "global")
    frames = {frame.timeframe: frame for frame in parts["candle_packet"].frames}
    children = {row.timestamp for row in frames["5m"].confirmed}
    parent = next(
        row
        for row in frames["15m"].confirmed
        if all(
            row.timestamp + timedelta(minutes=5 * index) in children
            for index in range(3)
        )
    )
    parts["candle_packet"] = _changed_high(
        parts["candle_packet"],
        "5m",
        timestamp=parent.timestamp,
        minimum_high=parent.high,
    )
    with pytest.raises(
        public.PublicMarketV2Error, match="public_v2_candle_cross_frame_mismatch"
    ):
        public._build(**parts)

    accepted, _ = public._build(**routed_packet_parts(captured, "global"))
    document = decode(accepted.packet_json, public.MAX_PACKET_BYTES)
    document["candles"] = parts["candle_packet"].model_dump(
        mode="json", round_trip=True
    )
    raw = canonical(document)
    with pytest.raises(
        public.PublicMarketV2Error, match="public_v2_candle_cross_frame_mismatch"
    ):
        public.replay_collected_public_market_v2(raw, expected_sha256=sha(raw))
