"""One declared common 5m tape, aggregated into every synthetic raw timeframe."""

from dataclasses import replace
from datetime import timedelta
from decimal import Context, localcontext
from decimal import Decimal as D

from app.market.quality.candles import BAR_SECONDS
from tests.unit import test_native_owned_eight_family_v3 as retained


def common_raw_high_vol_source_v1(strategy, direction, *, quote_profile="point_zone"):
    if quote_profile not in {"point_zone", "inherited_outside_zone"}:
        raise ValueError("explicit_high_vol_fixture_quote_required")
    source = retained._owned_full_bracket_raw_source_v3(
        "volatility_expansion", direction
    )
    market = source.market.model_copy(deep=True)
    now = retained.raw_fixture.NOW
    first = min(rows[0].timestamp for rows in market.candles.values())
    step = timedelta(minutes=5)
    count = (now - first) // step
    default = market.candles["5m"][0]
    q = D(".00000001")

    def baseline(at):
        hours = D((at - now) // timedelta(seconds=1)) / D(3600)
        return (D("100.38") + hours * D(".08")).quantize(q)

    def closed_price(at):
        phase = int(at.timestamp()) // 300 % 3
        value = baseline(at) + (D(0), D(".028"), D("-.028"))[phase]
        if at == now - step:
            value = baseline(at) - D(".01")
        elif at == now:
            value = D("100.4")
        return value.quantize(q)

    with localcontext(Context(prec=100)):
        tape = []
        for index in range(count):
            opened_at = first + index * step
            ended_at = opened_at + step
            opened, closed = closed_price(opened_at), closed_price(ended_at)
            wick = (
                (closed * D(".007")).quantize(q)
                if ended_at <= now - timedelta(hours=16)
                else D(".01")
            )
            high, low = max(opened, closed) + wick, min(opened, closed) - wick
            if opened_at == now.replace(hour=8, minute=45):
                high = max(high, D("100.352"))
            if strategy == "volatility_expansion" and opened_at == now.replace(
                hour=9, minute=45
            ):
                high = D(115)
            contracts = default.volume_contracts * (2 if ended_at == now else 1)
            currency = default.volume_currency * (2 if ended_at == now else 1)
            tape.append(
                default.model_copy(
                    update={
                        "timestamp": opened_at,
                        "open": opened,
                        "high": high,
                        "low": low,
                        "close": closed,
                        "volume_contracts": contracts,
                        "volume_currency": currency,
                        "volume_quote": currency * closed,
                        "confirmed": True,
                    }
                )
            )
        frames = {}
        for frame, rows in market.candles.items():
            width = BAR_SECONDS[frame] // 300
            rebuilt = []
            for row in rows:
                index = (row.timestamp - first) // step
                children = tape[index : index + width]
                assert len(children) == width
                rebuilt.append(
                    row.model_copy(
                        update={
                            "open": children[0].open,
                            "high": max(child.high for child in children),
                            "low": min(child.low for child in children),
                            "close": children[-1].close,
                            "volume_contracts": sum(
                                (child.volume_contracts for child in children), D(0)
                            ),
                            "volume_currency": sum(
                                (child.volume_currency for child in children), D(0)
                            ),
                            "volume_quote": sum(
                                (child.volume_quote for child in children), D(0)
                            ),
                            "confirmed": True,
                        }
                    )
                )
            frames[frame] = rebuilt
        if direction == "short":
            frames = {
                frame: [
                    row.model_copy(
                        update={
                            "open": D(200) - row.open,
                            "high": D(200) - row.low,
                            "low": D(200) - row.high,
                            "close": D(200) - row.close,
                            # Exact affine mapping of each child turnover, then sums.
                            "volume_quote": D(200) * row.volume_currency
                            - row.volume_quote,
                        }
                    )
                    for row in rows
                ]
                for frame, rows in frames.items()
            }
        market.candles = frames
        # Two distinct declared raw-source cases, never changes to a captured
        # candidate. The inherited quote is retained as an explicit rejection.
        if quote_profile == "point_zone":
            bid, ask, last = (
                (D("100.35"), D("100.36"), D("100.36"))
                if direction == "long"
                else (D("99.64"), D("99.65"), D("99.64"))
            )
            daily = tape[-288:]
            daily_open = daily[0].open
            daily_high = max(row.high for row in daily)
            daily_low = min(row.low for row in daily)
            if direction == "short":
                daily_open, daily_high, daily_low = (
                    D(200) - daily_open,
                    D(200) - daily_low,
                    D(200) - daily_high,
                )
            market.ticker = market.ticker.model_copy(
                update={
                    "last": last,
                    "bid": bid,
                    "ask": ask,
                    "open_24h": daily_open,
                    "high_24h": max(daily_high, ask),
                    "low_24h": min(daily_low, bid),
                    "volume_contracts_24h": sum(
                        (row.volume_contracts for row in daily), D(0)
                    ),
                    "volume_currency_24h": sum(
                        (row.volume_currency for row in daily), D(0)
                    ),
                }
            )
            market.order_book = market.order_book.model_copy(
                update={
                    "bids": [
                        row.model_copy(update={"price": bid - D(index) * D(".01")})
                        for index, row in enumerate(market.order_book.bids)
                    ],
                    "asks": [
                        row.model_copy(update={"price": ask + D(index) * D(".01")})
                        for index, row in enumerate(market.order_book.asks)
                    ],
                }
            )
            market.mark_price = last
    return replace(source, market=market, strategy=strategy)
