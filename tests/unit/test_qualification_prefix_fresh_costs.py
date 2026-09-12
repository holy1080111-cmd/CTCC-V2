"""G4 limits against newly captured synthetic REST fields, not legacy funding."""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from app.trade_qualification import service as coordinator
from app.trade_qualification.data import DataQualificationPolicy
from app.trade_qualification.models import QualificationGate
from app.trade_qualification.quote_collector import validate_collected_quote
from app.trade_qualification.service import (
    QualificationIntent,
    QualificationPrefixPolicy,
    evaluate_qualification_prefix,
)
from tests.unit.qualification_prefix_fixtures import prefix_source
from tests.unit.test_qualification_quote_collector import Clock, _ms, capture

D = Decimal
EPSILON_BPS = D("0.000000000001")
ZERO = D(0)


def fresh_source(direction, *, spread_bps=None, funding_bps=ZERO):
    """Make actual collector provenance from raw responses; never edit a result."""
    source = prefix_source(direction)
    market = source.market
    bid, ask = market.ticker.bid, market.ticker.ask
    if spread_bps is not None:
        # Finite symmetric prices represent the requested midpoint spread exactly.
        # Move the center slightly inward so both directions remain in the raw
        # fixture's source-derived entry zone at the 8 bps boundary.
        midpoint = market.ticker.last + (
            D("-0.05") if direction == "long" else D("0.05")
        )
        half_spread = midpoint * spread_bps / D(20000)
        bid, ask = midpoint - half_spread, midpoint + half_spread
        assert (Fraction(ask) - Fraction(bid)) / (
            (Fraction(ask) + Fraction(bid)) / 2
        ) * 10000 == Fraction(spread_bps)

    def payload_changes(role, body):
        row = body["data"][0]
        row["ts"] = _ms(market.ticker.timestamp)
        if role == "ticker":
            row.update(bidPx=str(bid), askPx=str(ask))
        elif role == "mark":
            row["markPx"] = str(market.ticker.last)
        else:
            row["fundingRate"] = str(funding_bps / D(10000))

    quote, requests = asyncio.run(
        capture(
            change=payload_changes,
            report=source.report_id,
            clock=Clock(
                tuple(market.received_at + timedelta(milliseconds=i) for i in range(10))
            ),
        )
    )
    assert validate_collected_quote(quote) == quote
    assert len(requests) == 3 and all(request.method == "GET" for request in requests)
    assert all(
        "cookie" not in request.headers and "authorization" not in request.headers
        for request in requests
    )
    funding_row = json.loads(quote.provenance[2].response_body)["data"][0]
    assert D(funding_row["fundingRate"]) == funding_bps / D(10000)
    assert quote.quote.funding_time == market.ticker.timestamp
    # These are test-specific G1 bounds, deliberately wider than G4's immutable
    # upper bounds. No production default or evaluator is changed or bypassed.
    data_values = source.policy.model_dump(round_trip=True)
    data_values.update(
        policy_id="synthetic-g4-fresh-cost-boundaries",
        maximum_spread_bps=D(10),
        maximum_absolute_funding_bps=D(20),
    )
    policy = DataQualificationPolicy.model_validate(data_values, strict=True)
    return replace(source, quote=quote, policy=policy)


def run_prefix(source, monkeypatch, *, expected_code="passed"):
    if expected_code != "passed":

        def forbidden(*_args, **_kwargs):
            pytest.fail("G4 failure must stop before historical event extraction")

        monkeypatch.setattr(coordinator, "extract_trigger", forbidden)
    policy = QualificationPrefixPolicy(
        policy_id="synthetic-g4-cost-check",
        data=source.policy,
        minimum_score=85,
        tick_size=source.tick_size,
        max_allowed_drift_bps=source.maximum_drift_bps,
    )
    assert policy.maximum_strategy_spread_bps == D(8)
    assert policy.maximum_adverse_funding_bps == D(15)
    intent = QualificationIntent(
        report_id=source.report_id,
        instrument_id=source.market.instrument_id,
        strategy=source.strategy,
        direction=source.direction,
        candidate_entry=source.market.ticker.last,
        created_at=source.evaluated_at,
        expires_at=source.evaluated_at + timedelta(minutes=5),
    )
    result = evaluate_qualification_prefix(
        source.market,
        intent=intent,
        quote=source.quote,
        reference=source.reference,
        policy=policy,
        consumed_event_keys=frozenset(),
        evaluated_at=source.evaluated_at,
    )
    assert result.data_result.passed, result.data_result.gate
    assert all(gate.passed for gate in result.result.gates[:3])
    assert result.result.gates[3].gate == QualificationGate.SETUP
    setup = result.result.gates[3]
    assert setup.code == expected_code
    assert setup.measured_values["raw_score"] == 100
    assert setup.measured_values["required_failures"] == "none"
    assert setup.measured_values["veto_failures"] == "none"
    assert setup.measured_values["quote_bundle_sha256"] == source.quote.bundle_sha256
    assert result.data_result.quote_bundle_sha256 == source.quote.bundle_sha256
    if expected_code == "passed":
        assert setup.passed and result.prefix_complete, result.result.gates
        assert (
            result.detection is not None
            and result.timing is not None
            and result.location is not None
        )
        assert all(gate.passed for gate in result.result.gates)
    else:
        assert not setup.passed and len(result.result.gates) == 4
        assert result.result.fail_codes == (expected_code,)
        assert result.detection is result.timing is result.location is None
        assert result.result.effective_score == 0
    assert not result.execution_authority and not result.result.qualified
    assert (
        not result.execution_recheck_performed
        and not result.source_authenticity_verified
    )
    return result, setup


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize(
    "offset", [-EPSILON_BPS, D(0), EPSILON_BPS], ids=["below", "equal", "above"]
)
def test_fresh_midpoint_spread_boundary_is_eight_bps_in_both_directions(
    direction, offset, monkeypatch
):
    spread = D(8) + offset
    source = fresh_source(direction, spread_bps=spread)
    expected = "strategy_spread_exceeded" if offset > 0 else "passed"
    result, setup = run_prefix(source, monkeypatch, expected_code=expected)
    assert setup.measured_values["fresh_spread_bps"] == spread
    assert setup.measured_values["spread_within_strategy_limit"] is (offset <= 0)
    assert setup.measured_values["funding_within_strategy_limit"] is True
    assert result.data_result.policy.maximum_spread_bps == D(10)


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize(
    "offset", [-EPSILON_BPS, D(0), EPSILON_BPS], ids=["below", "equal", "above"]
)
def test_fresh_adverse_funding_boundary_is_signed_fifteen_bps(
    direction, offset, monkeypatch
):
    adverse = D(15) + offset
    signed_rate = adverse if direction == "long" else -adverse
    source = fresh_source(direction, funding_bps=signed_rate)
    expected = "strategy_adverse_funding_exceeded" if offset > 0 else "passed"
    result, setup = run_prefix(source, monkeypatch, expected_code=expected)
    assert setup.measured_values["fresh_signed_funding_cost_bps"] == adverse
    assert setup.measured_values["funding_within_strategy_limit"] is (offset <= 0)
    assert setup.measured_values["spread_within_strategy_limit"] is True
    assert result.data_result.policy.maximum_absolute_funding_bps == D(20)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_favorable_funding_above_fifteen_absolute_bps_is_not_adverse(
    direction, monkeypatch
):
    source = fresh_source(
        direction, funding_bps=D(-19) if direction == "long" else D(19)
    )
    _, setup = run_prefix(source, monkeypatch)
    assert setup.measured_values["fresh_signed_funding_cost_bps"] == D(-19)
    assert setup.measured_values["funding_within_strategy_limit"] is True


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize(
    "fresh_fails", [False, True], ids=["fresh_passes", "fresh_fails"]
)
def test_legacy_snapshot_funding_neither_blocks_fresh_pass_nor_rescues_fresh_failure(
    direction, fresh_fails, monkeypatch
):
    sign = D(1) if direction == "long" else D(-1)
    fresh_rate = sign * (D(15) + EPSILON_BPS) if fresh_fails else D(0)
    source = fresh_source(direction, funding_bps=fresh_rate)
    expected = "strategy_adverse_funding_exceeded" if fresh_fails else "passed"
    baseline, baseline_setup = run_prefix(source, monkeypatch, expected_code=expected)
    # This is a changed raw legacy metadata input, not an edited passing result.
    # G1 rebuilds and re-hashes it normally, but G4 must still use the same newly
    # captured funding timestamp/value from the independently validated quote.
    source.market.funding_rate = -sign if fresh_fails else sign
    altered, altered_setup = run_prefix(source, monkeypatch, expected_code=expected)
    assert baseline_setup == altered_setup
    assert baseline.data_result.source_sha256 != altered.data_result.source_sha256
    assert (
        baseline.data_result.quote_bundle_sha256
        == altered.data_result.quote_bundle_sha256
    )
