"""Pure profile boundary tests; every accepted diagnostic remains DENY."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.trade_qualification.executable_quote_v2 import (
    ExecutableQuoteV2,
    RestMarkObservationV2,
    RestTickerObservationV2,
    inspect_executable_quote_v2,
)
from app.trade_qualification.funding_observation_v2 import parse_funding_observation_v2

NOW = datetime(2026, 9, 28, 14, tzinfo=UTC)
INST = "BTC-USDT-SWAP"


def ms(value):
    elapsed = value - datetime(1970, 1, 1, tzinfo=UTC)
    return str(
        elapsed.days * 86400000 + elapsed.seconds * 1000 + elapsed.microseconds // 1000
    )


def funding(*, age=38, settles=3600, state="settled", receipt_age=0.2):
    raw = json.dumps(
        {
            "code": "0",
            "msg": "",
            "data": [
                {
                    "instId": INST,
                    "instType": "SWAP",
                    "ts": ms(NOW - timedelta(seconds=age)),
                    "fundingRate": "-0.0001",
                    "fundingTime": ms(NOW + timedelta(seconds=settles)),
                    "nextFundingTime": ms(NOW + timedelta(seconds=settles + 28800)),
                    "formulaType": "withRate",
                    "method": "current_period",
                    "minFundingRate": "-0.00375",
                    "maxFundingRate": "0.00375",
                    "settState": state,
                    "settFundingRate": "0.0002",
                }
            ],
        }
    ).encode()
    receipt = NOW - timedelta(seconds=receipt_age)
    return parse_funding_observation_v2(
        raw,
        source_kind="rest",
        instrument_id=INST,
        acquisition_started_at=receipt - timedelta(seconds=1),
        received_at=receipt,
        completed_at=receipt,
    )


def quote(**funding_options):
    stamps = {
        "instrument_id": INST,
        "request_started_at": NOW - timedelta(seconds=1),
        "headers_received_at": NOW - timedelta(milliseconds=300),
        "body_completed_at": NOW - timedelta(milliseconds=100),
        "raw_body_sha256": "a" * 64,
    }
    return ExecutableQuoteV2(
        report_id="synthetic-v2-profile",
        instrument_id=INST,
        environment="demo",
        capture_started_at=NOW - timedelta(seconds=10),
        capture_completed_at=NOW,
        ticker=RestTickerObservationV2(
            bid=Decimal(100),
            ask=Decimal("100.01"),
            bid_size_contracts=Decimal(2),
            ask_size_contracts=Decimal(3),
            source_generated_at=NOW - timedelta(seconds=1),
            **stamps,
        ),
        mark=RestMarkObservationV2(
            price=Decimal("100.005"),
            exchange_data_return_at=NOW - timedelta(seconds=1),
            **stamps,
        ),
        funding=funding(**funding_options),
    )


def result(value=None, **options):
    current = options.pop("current_time", NOW)
    return json.loads(
        inspect_executable_quote_v2(
            quote() if value is None else value,
            current_time=current,
            **options,
        ).receipt_json
    )


def test_different_component_ages_and_correct_rate_period_do_not_grant_authority():
    observed = result()
    assert observed["profile_satisfied"] is True
    assert observed["admission"] == "DENY"
    assert observed["source_authenticity_verified"] is False
    assert observed["point_in_time_verified"] is False
    assert observed["execution_authority"] is False
    raw = observed["quote_document"]["funding"]
    assert raw["rate"] == "-0.0001"
    assert raw["upcoming_settlement_at"] == (NOW + timedelta(hours=1)).isoformat()
    assert (
        raw["following_settlement_forecast_at"]
        == (NOW + timedelta(hours=9)).isoformat()
    )
    assert "latest_exchange_rate_generation" in observed["unverified"]
    assert "owned_invocation_and_publication_barrier" in observed["unverified"]


@pytest.mark.parametrize("age,passes", [(89.999, True), (90, True), (90.001, False)])
def test_funding_return_age_exact_boundary(age, passes):
    observed = result(quote(age=age))
    assert observed["profile_satisfied"] is passes
    if not passes:
        assert observed["code"] == "funding_return_stale"


@pytest.mark.parametrize("component", ["ticker", "mark"])
@pytest.mark.parametrize("age,passes", [(5, True), (5.000001, False)])
def test_funding_age_does_not_relax_fast_component_freshness(component, age, passes):
    value = quote(age=90)
    field = (
        "source_generated_at" if component == "ticker" else "exchange_data_return_at"
    )
    changed = replace(
        getattr(value, component), **{field: NOW - timedelta(seconds=age)}
    )
    observed = result(replace(value, **{component: changed}))
    assert observed["profile_satisfied"] is passes
    if not passes:
        assert observed["code"] == f"{component}_source_stale"


@pytest.mark.parametrize("age,passes", [(5, True), (5.001, False)])
def test_funding_receipt_age_is_five_seconds_even_when_source_age_is_valid(age, passes):
    observed = result(quote(age=80, receipt_age=age))
    assert observed["profile_satisfied"] is passes
    if not passes:
        assert observed["code"] == "funding_receipt_stale"


@pytest.mark.parametrize(
    "settles,code",
    [
        (-1, "funding_settlement_reached"),
        (0, "funding_settlement_reached"),
        (95, "funding_settlement_transition_guard"),
        (94.999, "funding_settlement_transition_guard"),
        (95.001, "profile_checks_satisfied"),
    ],
)
def test_reached_and_transition_settlement_boundaries(settles, code):
    observed = result(quote(settles=settles))
    assert observed["code"] == code
    assert observed["admission"] == "DENY"


def test_processing_funding_cannot_support_new_exposure():
    observed = result(quote(state="processing"))
    assert observed["code"] == "funding_settlement_processing"
    assert observed["profile_satisfied"] is False


@pytest.mark.parametrize("component", ["ticker", "mark"])
def test_future_component_timestamp_is_rejected_without_tolerance(component):
    value = quote()
    field = (
        "source_generated_at" if component == "ticker" else "exchange_data_return_at"
    )
    changed = replace(
        getattr(value, component), **{field: NOW + timedelta(microseconds=1)}
    )
    assert (
        result(replace(value, **{component: changed}))["code"]
        == "quote_component_causal_order_invalid"
    )


def test_wrong_scope_nested_types_crossed_quote_and_capture_order_fail_closed():
    value = quote()
    for changed in (
        replace(value, ticker=replace(value.ticker, instrument_id="ETH-USDT-SWAP")),
        replace(value, ticker=replace(value.ticker, bid=True)),
        replace(value, ticker=replace(value.ticker, bid=Decimal(101))),
        replace(value, mark=replace(value.mark, raw_body_sha256="caller-passed")),
        replace(value, capture_completed_at=NOW + timedelta(microseconds=1)),
        replace(value, capture_started_at=NOW),
        replace(value, environment="live"),
    ):
        checked = result(changed)
        assert checked["profile_satisfied"] is False
        assert checked["quote_sha256"] is None
        assert checked["admission"] == "DENY"


def test_caller_cannot_select_age_profile_or_reuse_inspector_receipt_as_quote():
    checked = inspect_executable_quote_v2(quote(), current_time=NOW)
    assert (
        result(expected_policy_sha256="0" * 64)["code"]
        == "quote_profile_identity_mismatch"
    )
    assert result(checked)["profile_satisfied"] is False
    value = quote()
    object.__setattr__(value, "execution_authority", 0)
    assert result(value)["code"] == "quote_unowned_flags_invalid"


def test_source_return_cannot_be_replaced_with_receipt_to_rescue_old_funding():
    value = quote(age=91)
    object.__setattr__(
        value.funding, "exchange_data_return_at", value.funding.received_at
    )
    assert result(value)["code"] == "funding_observation_invalid"


def test_replay_is_deterministic_but_later_validation_cannot_reuse_old_result():
    value = quote(age=90)
    first = inspect_executable_quote_v2(value, current_time=NOW)
    second = inspect_executable_quote_v2(value, current_time=NOW)
    assert first.receipt_json == second.receipt_json
    assert (
        result(value, current_time=NOW + timedelta(milliseconds=1))["code"]
        == "funding_return_stale"
    )


def test_v1_inspector_rejects_the_new_type_instead_of_silently_relaxing_policy():
    from app.trade_qualification.location import inspect_executable_quote

    checked, code = inspect_executable_quote(
        quote(),
        current_time=NOW,
        max_quote_age_seconds=5,
    )
    assert checked is None
    assert code == "executable_quote_invalid"
