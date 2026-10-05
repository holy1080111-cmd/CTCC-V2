"""Synthetic raw payload semantics only; no native capture or authority claim."""

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest

from app.trade_qualification.funding_observation_v2 import (
    MAX_BODY_BYTES,
    FundingForecastV2,
    FundingObservationError,
    FundingObservationV2,
    parse_funding_observation_v2,
    verify_funding_observation_v2,
)

NOW = datetime(2026, 9, 28, 13, 51, tzinfo=UTC)
INSTRUMENT = "BTC-USDT-SWAP"


def ms(value):
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return str(
        delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
    )


def row(**changes):
    result = {
        "instId": INSTRUMENT,
        "instType": "SWAP",
        "ts": ms(NOW - timedelta(seconds=38)),
        "fundingRate": "0.0000877716714231",
        "fundingTime": ms(NOW + timedelta(hours=2)),
        "nextFundingTime": ms(NOW + timedelta(hours=10)),
        "method": "current_period",
        "formulaType": "withRate",
        "minFundingRate": "-0.00375",
        "maxFundingRate": "0.00375",
        "settState": "settled",
        "settFundingRate": "-0.0000076067766736",
        "nextFundingRate": "",
        "premium": "-0.0005167692828509",
    }
    result.update(changes)
    return result


def body(value=None, source="rest"):
    value = row() if value is None else value
    envelope = (
        {"code": "0", "msg": "", "data": [value]}
        if source == "rest"
        else {"arg": {"channel": "funding-rate", "instId": INSTRUMENT}, "data": [value]}
    )
    return json.dumps(envelope).encode()


def parse(raw=None, **kwargs):
    options = {
        "source_kind": "rest",
        "instrument_id": INSTRUMENT,
        "acquisition_started_at": NOW - timedelta(milliseconds=500),
        "received_at": NOW,
        "completed_at": NOW + timedelta(milliseconds=50),
    }
    options.update(kwargs)
    return parse_funding_observation_v2(body() if raw is None else raw, **options)


@pytest.mark.parametrize("source", ["rest", "ws"])
def test_forecast_is_paired_with_upcoming_not_following_settlement(source):
    raw = body(source=source)
    observed = parse(raw, source_kind=source)
    assert observed.forecast == FundingForecastV2(
        Decimal("0.0000877716714231"), NOW + timedelta(hours=2)
    )
    assert observed.following_settlement_forecast_at == NOW + timedelta(hours=10)
    assert observed.exchange_data_return_at == NOW - timedelta(seconds=38)
    assert observed.raw_body == raw
    assert observed.body_sha256 == hashlib.sha256(raw).hexdigest()
    assert json.loads(observed.canonical_json) == json.loads(raw)
    assert (
        observed.canonical_sha256 == hashlib.sha256(observed.canonical_json).hexdigest()
    )
    assert verify_funding_observation_v2(observed) == observed
    assert observed.rate_generated_at is None
    assert observed.historical_first_available_at is None
    assert observed.admission == "DENY"
    for name in (
        "source_authenticity_verified",
        "receipt_authenticity_verified",
        "point_in_time_verified",
        "account_complete",
        "execution_authority",
    ):
        assert getattr(observed, name) is False


@pytest.mark.parametrize("rate", ["0", "-0.000001", "0.000001"])
def test_rate_sign_and_zero_are_retained_without_rounding_or_missing_fallback(rate):
    with localcontext() as context:
        context.prec = 2
        observed = parse(body(row(fundingRate=rate)))
    assert observed.forecast.rate == Decimal(rate)
    assert observed.settlement_reference_rate == Decimal("-0.0000076067766736")


@pytest.mark.parametrize(
    ("state", "meaning"),
    [("processing", "current_processing"), ("settled", "previous_settled")],
)
def test_settlement_reference_cannot_be_mistaken_for_forecast(state, meaning):
    observed = parse(body(row(settState=state)))
    assert observed.settlement_reference_kind == meaning
    assert observed.forecast.rate != observed.settlement_reference_rate
    assert observed.admission == "DENY"


def test_old_or_processing_record_is_diagnostic_not_freshness_acceptance():
    observed = parse(
        body(
            row(
                ts=ms(NOW - timedelta(days=1)),
                fundingTime=ms(NOW - timedelta(seconds=1)),
                settState="processing",
            )
        )
    )
    assert observed.forecast.settlement_at < observed.received_at
    assert observed.admission == "DENY"


def test_unknown_extra_fields_are_retained_and_not_promoted():
    raw = body(row(unrecognized={"future_schema": [None, "preserved"]}))
    observed = parse(raw)
    assert observed.raw_body == raw
    assert json.loads(observed.canonical_json)["data"][0]["unrecognized"] == {
        "future_schema": [None, "preserved"]
    }


@pytest.mark.parametrize(
    "change",
    [
        {"fundingRate": ""},
        {"fundingRate": None},
        {"fundingRate": 0},
        {"fundingRate": "NaN"},
        {"fundingRate": "Infinity"},
        {"fundingRate": "1e-4"},
        {"fundingRate": "0.1"},
        {"minFundingRate": ""},
        {"maxFundingRate": None},
        {"minFundingRate": "0.01"},
        {"settFundingRate": ""},
        {"ts": ms(NOW + timedelta(milliseconds=1))},
        {"ts": ""},
        {"ts": True},
        {"fundingTime": ""},
        {"nextFundingTime": ms(NOW + timedelta(hours=2))},
        {"method": "next_period"},
        {"method": None},
        {"formulaType": "future_formula"},
        {"settState": "unknown"},
        {"nextFundingRate": "0.001"},
        {"instId": "ETH-USDT-SWAP"},
        {"instType": "FUTURES"},
    ],
)
def test_unknown_malformed_future_or_conflicting_source_is_rejected(change):
    with pytest.raises(FundingObservationError):
        parse(body(row(**change)))


@pytest.mark.parametrize(
    "missing",
    [
        "fundingRate",
        "fundingTime",
        "nextFundingTime",
        "ts",
        "minFundingRate",
        "maxFundingRate",
        "settFundingRate",
        "method",
        "formulaType",
        "settState",
    ],
)
def test_missing_required_financial_semantics_never_default(missing):
    value = row()
    del value[missing]
    with pytest.raises(FundingObservationError):
        parse(body(value))


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{}",
        b"[]",
        b"\xff",
        b'{"data":[],"data":[]}',
        b'{"code":"0","msg":"","data":[NaN]}',
        b" " * (MAX_BODY_BYTES + 1),
    ],
    ids=[
        "empty",
        "empty-object",
        "array",
        "invalid-utf8",
        "duplicate-keys",
        "nonfinite",
        "oversized",
    ],
)
def test_raw_body_validation_keeps_malformed_sources_out(raw):
    with pytest.raises(FundingObservationError):
        parse(raw)


def test_duplicate_row_identity_and_malformed_ws_envelopes_are_not_deduplicated():
    payload = json.loads(body())
    payload["data"].append(row())
    with pytest.raises(FundingObservationError):
        parse(json.dumps(payload).encode())
    payload = json.loads(body(source="ws"))
    payload["arg"]["channel"] = "tickers"
    with pytest.raises(FundingObservationError):
        parse(json.dumps(payload).encode(), source_kind="ws")
    payload["arg"]["channel"] = "funding-rate"
    payload["event"] = "subscribe"
    with pytest.raises(FundingObservationError):
        parse(json.dumps(payload).encode(), source_kind="ws")


@pytest.mark.parametrize(
    "changes",
    [
        {"acquisition_started_at": NOW + timedelta(seconds=1)},
        {"completed_at": NOW - timedelta(seconds=1)},
        {"received_at": NOW.replace(tzinfo=None)},
        {"source_kind": "fallback"},
        {"instrument_id": "ANY"},
    ],
)
def test_wrong_clock_order_or_source_selection_is_rejected(changes):
    with pytest.raises(FundingObservationError):
        parse(**changes)


def test_aware_timezone_conversion_does_not_change_source_or_receipt_instant():
    local = NOW.astimezone(timezone(timedelta(hours=8)))
    assert parse(received_at=local).received_at == NOW


def test_frozen_pair_cannot_be_reassigned_and_replay_rejects_forced_tampering():
    observed = parse()
    with pytest.raises(FrozenInstanceError):
        observed.forecast.rate = Decimal("0.1")
    changed = replace(
        observed,
        forecast=replace(
            observed.forecast, settlement_at=observed.following_settlement_forecast_at
        ),
    )
    with pytest.raises(FundingObservationError):
        verify_funding_observation_v2(changed)
    object.__setattr__(observed, "execution_authority", True)
    with pytest.raises(FundingObservationError):
        verify_funding_observation_v2(observed)


@pytest.mark.parametrize(
    "name,value",
    [
        ("body_sha256", "a" * 64),
        ("canonical_json", b"{}"),
        ("point_in_time_verified", 0),
        ("rate_generated_at", NOW),
        ("schema_version", "caller-passed"),
    ],
)
def test_rehashed_or_typed_output_flags_do_not_grant_authority(name, value):
    observed = parse()
    object.__setattr__(observed, name, value)
    with pytest.raises(FundingObservationError):
        verify_funding_observation_v2(observed)


def test_exact_type_and_nested_decimal_are_required_on_replay():
    class Derived(FundingObservationV2):
        pass

    with pytest.raises(FundingObservationError):
        verify_funding_observation_v2(object.__new__(Derived))
    with pytest.raises(FundingObservationError):
        verify_funding_observation_v2(object.__new__(FundingObservationV2))
    observed = parse(body(row(fundingRate="0")))
    object.__setattr__(observed.forecast, "rate", False)
    with pytest.raises(FundingObservationError):
        verify_funding_observation_v2(observed)
