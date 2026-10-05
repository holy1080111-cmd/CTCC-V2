"""Synthetic raw replay and ownership rejection; no native integration claim."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.trade_qualification import public_source_runtime as runtime
from app.trade_qualification import quote_collector as wire
from app.trade_qualification import quote_collector_v2 as module

NOW = datetime(2026, 9, 28, 15, tzinfo=UTC)
INST = "BTC-USDT-SWAP"


def ms(value):
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return str(
        delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
    )


def observations(*, funding_age=38, padding=False):
    result = []
    for index, (role, path) in enumerate(module.ORDER):
        started = NOW + timedelta(milliseconds=index * 100)
        source_time = NOW - timedelta(seconds=funding_age if role == "funding" else 1)
        row = {"instId": INST, "instType": "SWAP", "ts": ms(source_time)}
        row.update(
            {
                "funding": {
                    "fundingRate": "0.0001",
                    "fundingTime": ms(NOW + timedelta(hours=1)),
                    "nextFundingTime": ms(NOW + timedelta(hours=9)),
                    "method": "current_period",
                    "formulaType": "withRate",
                    "minFundingRate": "-0.00375",
                    "maxFundingRate": "0.00375",
                    "settFundingRate": "-0.0002",
                    "settState": "settled",
                },
                "mark": {"markPx": "100.005"},
                "ticker": {
                    "bidPx": "100",
                    "askPx": "100.01",
                    "bidSz": "2",
                    "askSz": "3",
                },
            }[role]
        )
        if padding:
            # Individually bounded fields can make the complete response >4KiB.
            row["extra"] = ["retained-public-field" * 100] * 3
        raw = json.dumps({"code": "0", "msg": "", "data": [row]}, indent=2).encode()
        _, canonical = wire._parse(raw)
        params = (("instId", INST),) + (
            (("instType", "SWAP"),) if role == "mark" else ()
        )
        result.append(
            wire.EndpointObservation(
                role=role,
                endpoint=path,
                parameters=params,
                instrument_id=INST,
                request_started_at=started,
                received_at=started + timedelta(milliseconds=20),
                completed_at=started + timedelta(milliseconds=50),
                ts_raw=row["ts"],
                source_time=source_time,
                timestamp_semantics="ticker_generation"
                if role == "ticker"
                else "exchange_data_return",
                response_body=raw,
                body_sha256=wire._sha(raw),
                body_size_bytes=len(raw),
                canonical_json=canonical,
                canonical_sha256=wire._sha(canonical.encode()),
            )
        )
    return tuple(result)


def packet(values=None, **changes):
    args = {
        "report_id": "synthetic-v2-raw",
        "instrument_id": INST,
        "environment": "demo",
        "observations": observations() if values is None else values,
        "capture_started_at": NOW - timedelta(milliseconds=1),
        "capture_completed_at": NOW + timedelta(milliseconds=300),
        "barrier_completed_at": NOW - timedelta(seconds=1),
    }
    args.update(changes)
    return module.build_diagnostic_quote_packet_v2(**args)


def test_raw_observations_rebuild_quote_with_unchanged_funding_time_and_no_authority():
    source = observations()
    result = packet(source)
    replay = module.replay_quote_packet_v2(
        result.packet_json, expected_sha256=result.bundle_sha256
    )
    assert replay.packet_json == result.packet_json
    data = json.loads(result.packet_json)
    for stored, original in zip(data["provenance"], source, strict=True):
        assert stored["response_body"].encode() == original.response_body
        assert stored["body_sha256"] == original.body_sha256
        assert original.source_time < original.request_started_at
    funding = data["inspection"]["quote_document"]["funding"]
    assert funding["exchange_data_return_at"] == source[0].source_time.isoformat()
    assert funding["upcoming_settlement_at"] == (NOW + timedelta(hours=1)).isoformat()
    assert data["inspection"]["profile_satisfied"] is True
    assert data["admission"] == "DENY"
    assert result.execution_authority is False
    assert result.source_authenticity_verified is False


def test_packet_retains_whole_response_larger_than_individual_response_field_limit():
    source = observations(padding=True)
    assert len(source[0].response_body) > 4096
    result = packet(source)
    assert (
        module.replay_quote_packet_v2(
            result.packet_json, expected_sha256=result.bundle_sha256
        ).packet_json
        == result.packet_json
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "caller-passed",
        "authority",
        "profile",
        "transport",
        "price",
        "funding-time",
        "raw-body",
        "observation-time",
        "extra-field",
    ],
)
def test_rehashed_packet_cannot_replace_raw_replay(mutation):
    value = json.loads(packet().packet_json)
    if mutation == "caller-passed":
        value["inspection"]["profile_satisfied"] = False
    elif mutation == "authority":
        value["execution_authority"] = True
    elif mutation == "profile":
        value["quote_profile_sha256"] = "0" * 64
    elif mutation == "transport":
        value["transport_policy_sha256"] = "0" * 64
    elif mutation == "price":
        value["inspection"]["quote_document"]["ticker"]["ask"] = "99"
    elif mutation == "funding-time":
        funding = value["inspection"]["quote_document"]["funding"]
        funding["upcoming_settlement_at"] = funding["following_settlement_forecast_at"]
    elif mutation == "raw-body":
        value["provenance"][0]["response_body"] += " "
    elif mutation == "observation-time":
        value["provenance"][0]["source_time"] = NOW.isoformat()
    else:
        value["passed"] = True
    raw = module._canonical(value)
    with pytest.raises(module.QuoteCollectionV2Error):
        module.replay_quote_packet_v2(raw, expected_sha256=wire._sha(raw))


def test_stale_funding_does_not_rewrite_timestamp_to_get_new_packet():
    source = observations(funding_age=91)
    with pytest.raises(module.QuoteCollectionV2Error, match="profile_rejected"):
        packet(source)
    assert source[0].source_time == NOW - timedelta(seconds=91)


def test_order_duplicate_instrument_and_exact_barrier_are_not_repaired():
    source = observations()
    for values in (source[::-1], (source[0], source[0], source[2])):
        with pytest.raises(module.QuoteCollectionV2Error):
            packet(values)
    with pytest.raises(module.QuoteCollectionV2Error):
        packet(instrument_id="ETH-USDT-SWAP")
    with pytest.raises(module.QuoteCollectionV2Error, match="barrier_not_crossed"):
        packet(capture_started_at=NOW, barrier_completed_at=NOW)


def test_duplicate_packet_json_key_and_wrong_hash_are_rejected():
    raw = packet().packet_json
    duplicate = raw[:-1] + b',"admission":"DENY"}'
    with pytest.raises(module.QuoteCollectionV2Error):
        module.replay_quote_packet_v2(duplicate, expected_sha256=wire._sha(duplicate))
    with pytest.raises(module.QuoteCollectionV2Error):
        module.replay_quote_packet_v2(raw, expected_sha256="0" * 64)


@pytest.mark.asyncio
async def test_caller_packet_or_unregistered_private_type_never_reaches_io(monkeypatch):
    calls = []

    async def forbidden_send(*args, **kwargs):
        calls.append(True)
        raise AssertionError("No request may be sent for unowned input")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_send)
    # No source-state dictionary or fake registry membership is installed.
    for value in (object(), packet(), runtime._Source(runtime._ISSUER)):
        with pytest.raises(runtime.PublicSourceRuntimeError):
            await module._collect_owned_quote_v2(value)
    assert calls == []


def test_v1_bundle_validator_rejects_new_packet_type():
    with pytest.raises((wire.QuoteCollectionError, ValueError)):
        wire.validate_collected_quote(packet())


@pytest.mark.parametrize(
    "microseconds,accepted",
    [(1999999, True), (2000000, True), (2000001, False), (20000000, False)],
    ids=["below-2s", "exact-2s", "over-2s-by-1us", "old-20s-repro"],
)
def test_request_elapsed_budget_includes_time_before_headers(microseconds, accepted):
    values = observations()
    first_start = values[0].completed_at - timedelta(microseconds=microseconds)
    values = (
        values[0].model_copy(update={"request_started_at": first_start}),
        *values[1:],
    )
    kwargs = {
        "capture_started_at": first_start - timedelta(microseconds=1),
        "barrier_completed_at": None,
    }
    if accepted:
        result = packet(values, **kwargs)
        assert (
            module.replay_quote_packet_v2(
                result.packet_json, expected_sha256=result.bundle_sha256
            )
            == result
        )
    else:
        with pytest.raises(
            module.QuoteCollectionV2Error, match="request_elapsed_limit"
        ):
            packet(values, **kwargs)


@pytest.mark.parametrize(
    "microseconds,accepted",
    [(2000000, True), (2000001, False)],
    ids=["response-close-exact-2s", "response-close-over-2s-by-1us"],
)
def test_request_budget_includes_body_and_response_close(microseconds, accepted):
    values = observations()
    first_completed = values[0].request_started_at + timedelta(
        microseconds=microseconds
    )
    delayed = [values[0].model_copy(update={"completed_at": first_completed})]
    for index, item in enumerate(values[1:], start=1):
        start = first_completed + timedelta(milliseconds=index * 100)
        delayed.append(
            item.model_copy(
                update={
                    "request_started_at": start,
                    "received_at": start + timedelta(milliseconds=20),
                    "completed_at": start + timedelta(milliseconds=50),
                }
            )
        )
    kwargs = {"capture_completed_at": delayed[-1].completed_at}
    if accepted:
        assert packet(tuple(delayed), **kwargs).admission == "DENY"
    else:
        with pytest.raises(
            module.QuoteCollectionV2Error, match="request_elapsed_limit"
        ):
            packet(tuple(delayed), **kwargs)


@pytest.mark.parametrize(
    "microseconds,accepted",
    [(5999999, True), (6000000, True), (6000001, False), (20000000, False)],
    ids=["below-6s", "exact-6s", "over-6s-by-1us", "old-20s-repro"],
)
def test_capture_elapsed_budget_includes_startup_and_cleanup(microseconds, accepted):
    completed = NOW + timedelta(milliseconds=300)
    kwargs = {
        "capture_started_at": completed - timedelta(microseconds=microseconds),
        "capture_completed_at": completed,
        "barrier_completed_at": None,
    }
    if accepted:
        result = packet(**kwargs)
        assert (
            module.replay_quote_packet_v2(
                result.packet_json, expected_sha256=result.bundle_sha256
            )
            == result
        )
    else:
        with pytest.raises(
            module.QuoteCollectionV2Error, match="capture_elapsed_limit"
        ):
            packet(**kwargs)


@pytest.mark.parametrize(
    "changes",
    [
        {"capture_started_at": NOW + timedelta(microseconds=1)},
        {"capture_completed_at": NOW + timedelta(milliseconds=249, microseconds=999)},
        {"capture_completed_at": NOW - timedelta(seconds=2)},
    ],
    ids=["request-before-capture", "close-after-capture", "reversed-capture"],
)
def test_every_response_must_fit_capture_bounds(changes):
    with pytest.raises(module.QuoteCollectionV2Error, match="outside_capture"):
        packet(**changes)


def test_rehashed_packet_cannot_hide_measured_transport_overrun():
    data = json.loads(packet().packet_json)
    late_start = NOW - timedelta(seconds=20)
    data["provenance"][0]["request_started_at"] = late_start.isoformat()
    data["capture_started_at"] = late_start.isoformat()
    data["barrier_completed_at"] = None
    raw = module._canonical(data)
    with pytest.raises(module.QuoteCollectionV2Error, match="request_elapsed_limit"):
        module.replay_quote_packet_v2(raw, expected_sha256=wire._sha(raw))
