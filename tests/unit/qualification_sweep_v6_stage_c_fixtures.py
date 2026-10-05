"""Declared synthetic source replay and real filesystem G12; no exchange IO.

Market/account observations remain explicit fixture claims. Only publication,
hashing and readback use real local files. No native source/submit authority is
created; no previously failed event, entry, bracket or policy is repaired.
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pytest

from app.trade_evidence.gates import publish_qualification_evidence
from app.trade_qualification.data import WSReferenceObservation
from app.trade_qualification.history_engine import evaluate_history_pre_evidence_v6
from app.trade_qualification.recheck_models import freeze_recheck_origin
from tests.unit.qualification_recheck_fixtures import _current_risk, _new_market
from tests.unit.qualification_sweep_v6_fixtures import sweep_v6_source, v6_inputs
from tests.unit.test_qualification_evidence_gate import Clock
from tests.unit.test_qualification_quote_collector import Clock as CaptureClock
from tests.unit.test_qualification_quote_collector import _ms, capture

D = Decimal
CURRENT_SCENARIOS = (
    "unchanged",
    "outside_zone",
    "adverse_quote",
    "changed_original_row",
    "missing_original_row",
    "htf_denied",
    "extreme_breach",
    "cached",
    "barrier_equal",
    "expired",
)


@dataclass(frozen=True)
class SweepStageCFixture:
    source: object
    inputs: dict
    run: object
    root: object
    evidence: object
    origin: object


def published_sweep_v6(root, direction):
    source = sweep_v6_source(direction, "boundary")
    inputs = v6_inputs(source)
    run = evaluate_history_pre_evidence_v6(source.market, **inputs)
    assert run.pre_evidence_complete, run.result.fail_codes
    evidence = publish_qualification_evidence(
        root,
        source.market,
        run=run,
        **inputs,
        purpose="synthetic_test",
        clock=Clock(source.evaluated_at),
    )
    assert evidence.result.evidence_complete, evidence.error_detail
    return SweepStageCFixture(
        source, inputs, run, root, evidence, freeze_recheck_origin(evidence)
    )


async def capture_sweep_current(case, scenario="unchanged"):
    if scenario not in CURRENT_SCENARIOS:
        raise ValueError("declared_sweep_current_scenario_required")
    source, origin = case.source, case.origin
    start = (
        origin.publication_completed_at
        if scenario == "barrier_equal"
        else source.evaluated_at + timedelta(seconds=1)
    )
    market, observations = _new_market(source, start, "same_interval")
    if scenario not in {"cached", "barrier_equal"}:
        assert all(
            row.request_started_at > origin.publication_completed_at
            for row in observations
        )
    # Each scenario defines actual raw operands before any current evaluation.
    reference = source.market.ticker.last
    if scenario == "outside_zone":
        reference = D(103) if source.direction == "long" else D(97)
    elif scenario == "adverse_quote":
        zone = case.run.result.entry_zone
        reference = zone.zone_high if source.direction == "long" else zone.zone_low
    elif scenario == "extreme_breach":
        extreme = case.run.prefix.detection.invalidation_price
        reference = (
            extreme - source.tick_size
            if source.direction == "long"
            else extreme + source.tick_size
        )
    bid, ask = (
        (reference - source.tick_size, reference)
        if source.direction == "long"
        else (reference, reference + source.tick_size)
    )
    market.ticker = market.ticker.model_copy(
        update={"last": reference, "bid": bid, "ask": ask}
    )
    market.order_book.bids = [
        row.model_copy(update={"price": bid}) for row in market.order_book.bids
    ]
    market.order_book.asks = [
        row.model_copy(update={"price": ask}) for row in market.order_book.asks
    ]
    market.mark_price = reference
    if scenario == "changed_original_row":
        row = market.candles["15m"][-2]
        market.candles["15m"][-2] = row.model_copy(
            update={
                "volume_contracts": row.volume_contracts + 1,
                "volume_currency": row.volume_currency + 1,
                "volume_quote": row.volume_quote + row.close,
            }
        )
    elif scenario == "missing_original_row":
        del market.candles["15m"][-2]
    elif scenario == "htf_denied":
        market.candles["4H"] = [
            row.model_copy(
                update={
                    "open": D(100) + D(index) / 10,
                    "close": D(100) + D(index) / 10,
                    "high": D("100.3") + D(index) / 10,
                    "low": D("99.7") + D(index) / 10,
                }
            )
            for index, row in enumerate(market.candles["4H"])
        ]

    def payload(role, body):
        row = body["data"][0]
        index = {"ticker": 0, "mark": 1, "funding": 2}[role]
        row["ts"] = _ms(start + timedelta(milliseconds=index * 3 - 1))
        if role == "ticker":
            row.update(bidPx=str(bid), askPx=str(ask), bidSz="2", askSz="3")
        elif role == "mark":
            row["markPx"] = str(reference)
        else:
            row.update(
                fundingRate="0",
                fundingTime=_ms(start + timedelta(hours=8)),
                nextFundingTime=_ms(start + timedelta(hours=16)),
            )

    quote, _ = await capture(
        change=payload,
        report=source.report_id,
        instrument=market.instrument_id,
        clock=CaptureClock(tuple(start + timedelta(milliseconds=i) for i in range(10))),
        barrier=None
        if scenario == "barrier_equal"
        else origin.publication_completed_at,
    )
    reference_observation = WSReferenceObservation(
        report_id=source.report_id,
        instrument_id=market.instrument_id,
        bid=bid,
        ask=ask,
        source_time=start + timedelta(milliseconds=1),
        received_at=start + timedelta(milliseconds=10),
    )
    now = start + timedelta(milliseconds=12)
    if scenario == "cached":
        market, quote, reference_observation = (
            source.market,
            source.quote,
            source.reference,
        )
    elif scenario == "expired":
        now = origin.deadline
    return market, {
        "origin": origin,
        "original_inputs": case.inputs,
        "quote": quote,
        "reference": reference_observation,
        "current_risk_inputs": _current_risk(
            case.inputs,
            start + timedelta(milliseconds=10),
            start + timedelta(milliseconds=11),
        ),
        "consumed_event_keys": frozenset(),
        "observed_at": now,
    }


def sweep_current(case, scenario="unchanged"):
    return asyncio.run(capture_sweep_current(case, scenario))


@pytest.fixture(scope="module", params=("long", "short"))
def stage_c_case(request, tmp_path_factory):
    return published_sweep_v6(tmp_path_factory.mktemp("sweep-stage-c"), request.param)
