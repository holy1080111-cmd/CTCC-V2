"""Declared independent raw synthetic scenarios; never real-source acceptance."""

import asyncio
from datetime import timedelta
from decimal import Decimal

from app.trade_qualification.data import WSReferenceObservation
from app.trade_qualification.history_engine import HistoryPreEvidencePolicyV6
from app.trade_qualification.history_prefix import HistoryQualificationPrefixPolicyV6
from app.trade_qualification.sweep_contract import (
    SWEEP_PERMISSION_POLICY,
    SWEEP_PERMISSION_SHA256,
    SWEEP_SELECTION_POLICY,
    SWEEP_SELECTION_SHA256,
)
from tests.unit.history_qualification_fixtures import history_inputs
from tests.unit.qualification_prefix_fixtures import DATA_POLICY, SyntheticPrefixSource
from tests.unit.test_qualification_quote_collector import Clock, _ms, capture
from tests.unit.test_sweep_history_permission import OBSERVED, sweep_market

D = Decimal
SCENARIOS = {"original_outside": D(0), "inside": D("0.7"), "boundary": D("3.1")}


async def capture_sweep_v6_source(direction="long", scenario="inside"):
    if direction not in {"long", "short"} or scenario not in SCENARIOS:
        raise ValueError("declared_synthetic_sweep_scenario_required")
    market = sweep_market(direction)
    # Define the entire raw source before evaluating any policy or candidate.
    # These are different scenario identities, never a rescue of an old entry.
    shift = SCENARIOS[scenario] * (-1 if direction == "long" else 1)
    market.candles["5m"] = [
        row.model_copy(
            update={
                field: getattr(row, field) + shift
                for field in ("open", "high", "low", "close")
            }
        )
        for row in market.candles["5m"]
    ]
    market.ticker = market.ticker.model_copy(
        update={
            field: getattr(market.ticker, field) + shift
            for field in ("last", "bid", "ask", "open_24h", "high_24h", "low_24h")
        }
    )
    market.order_book.bids = [
        row.model_copy(update={"price": row.price + shift})
        for row in market.order_book.bids
    ]
    market.order_book.asks = [
        row.model_copy(update={"price": row.price + shift})
        for row in market.order_book.asks
    ]
    market.mark_price += shift
    report = f"synthetic-sweep-v6-{scenario}-{direction}"
    bid, ask, price = market.ticker.bid, market.ticker.ask, market.ticker.last

    def raw_response(role, body):
        row = body["data"][0]
        row["ts"] = _ms(OBSERVED - timedelta(seconds=1))
        if role == "ticker":
            row.update(bidPx=str(bid), askPx=str(ask))
        elif role == "mark":
            row["markPx"] = str(price)
        else:
            row.update(
                fundingRate="0",
                fundingTime=_ms(OBSERVED + timedelta(hours=1)),
                nextFundingTime=_ms(OBSERVED + timedelta(hours=9)),
            )

    quote, _ = await capture(
        change=raw_response,
        report=report,
        clock=Clock(tuple(OBSERVED + timedelta(milliseconds=i) for i in range(10))),
    )
    return SyntheticPrefixSource(
        report_id=report,
        direction=direction,
        market=market,
        quote=quote,
        reference=WSReferenceObservation(
            report_id=report,
            instrument_id=market.instrument_id,
            bid=bid,
            ask=ask,
            source_time=OBSERVED - timedelta(seconds=1),
            received_at=OBSERVED,
        ),
        policy=DATA_POLICY,
        evaluated_at=OBSERVED + timedelta(milliseconds=10),
        strategy="liquidity_sweep_reversal",
    )


def sweep_v6_source(direction="long", scenario="inside"):
    return asyncio.run(capture_sweep_v6_source(direction, scenario))


def v6_inputs(source):
    args = history_inputs(source)
    old = args["policy"]
    prefix = old.prefix.model_dump(mode="python", round_trip=True)
    prefix.update(
        contract_version="ctcc-history-qualification-prefix-v6",
        history_policy_id=SWEEP_PERMISSION_POLICY,
        history_result_classification="History Verified Sweep",
        sweep_permission_policy_sha256=SWEEP_PERMISSION_SHA256,
        sweep_selection_policy=SWEEP_SELECTION_POLICY,
        sweep_selection_policy_sha256=SWEEP_SELECTION_SHA256,
    )
    args["policy"] = HistoryPreEvidencePolicyV6(
        contract_version="ctcc-history-pre-evidence-v6",
        policy_id="synthetic-sweep-v6-policy",
        prefix=HistoryQualificationPrefixPolicyV6(**prefix),
        protection=old.protection,
        economics=old.economics,
        portfolio=old.portfolio,
    )
    return args


def prefix_inputs(source):
    args = v6_inputs(source)
    args["policy"] = args["policy"].prefix
    args.pop("risk_inputs")
    return args
