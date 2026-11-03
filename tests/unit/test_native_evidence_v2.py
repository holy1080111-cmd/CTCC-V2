"""Explicit synthetic V2 snapshot replay; no native collection/publication."""

import json
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.strategies.structural_protection import select_structural_protection
from app.trade_evidence import native_v2 as evidence
from app.trade_evidence import renderer, service
from app.trade_qualification.executable_quote_v2 import (
    ExecutableQuoteV2,
    RestMarkObservationV2,
    RestTickerObservationV2,
)
from app.trade_qualification.funding_observation_v2 import (
    FundingObservationV2,
    parse_funding_observation_v2,
)
from tests.unit.qualification_recheck_fixtures import recheck_source


def milliseconds(at):
    delta = at - datetime(1970, 1, 1, tzinfo=UTC)
    return str(
        delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
    )


@pytest.fixture(scope="module", params=["long", "short"])
def inputs(request):
    source = recheck_source(request.param, scenario="next_boundary")
    pre = source.original_run
    raw = json.loads(pre.prefix.data_result.source_json)
    market = MarketSnapshot.model_validate_json(json.dumps(raw["market"]), strict=True)
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(raw["analysis"]), strict=True
    )
    policy = pre.policy.protection
    selection = select_structural_protection(
        pre.prefix.detection,
        market,
        analysis,
        observed_at=source.original_source.evaluated_at,
        entry=pre.result.candidate_entry,
        tick_size=pre.policy.prefix.tick_size,
        **{
            name: value
            for name, value in policy.model_dump().items()
            if name != "policy_id"
        },
    )
    now = pre.result.evaluated_at
    original_quote = source.original_inputs["quote"].quote
    instrument = market.instrument_id
    body = json.dumps(
        {
            "code": "0",
            "msg": "",
            "data": [
                {
                    "instId": instrument,
                    "instType": "SWAP",
                    "ts": milliseconds(now - timedelta(seconds=1)),
                    "fundingRate": "0.0001",
                    "fundingTime": milliseconds(now + timedelta(hours=1)),
                    "nextFundingTime": milliseconds(now + timedelta(hours=9)),
                    "formulaType": "withRate",
                    "method": "current_period",
                    "minFundingRate": "-0.00375",
                    "maxFundingRate": "0.00375",
                    "settState": "settled",
                    "settFundingRate": "0.0002",
                }
            ],
        }
    ).encode()
    funding = parse_funding_observation_v2(
        body,
        source_kind="rest",
        instrument_id=instrument,
        acquisition_started_at=now - timedelta(seconds=1),
        received_at=now - timedelta(milliseconds=200),
        completed_at=now - timedelta(milliseconds=100),
    )
    stamps = {
        "instrument_id": instrument,
        "request_started_at": now - timedelta(seconds=1),
        "headers_received_at": now - timedelta(milliseconds=300),
        "body_completed_at": now - timedelta(milliseconds=100),
        "raw_body_sha256": "a" * 64,
    }
    quote = ExecutableQuoteV2(
        report_id=pre.result.report_id,
        instrument_id=instrument,
        environment="demo",
        capture_started_at=now - timedelta(seconds=2),
        capture_completed_at=now,
        ticker=RestTickerObservationV2(
            bid=original_quote.bid,
            ask=original_quote.ask,
            bid_size_contracts=Decimal(10),
            ask_size_contracts=Decimal(10),
            source_generated_at=now - timedelta(seconds=1),
            **stamps,
        ),
        mark=RestMarkObservationV2(
            price=original_quote.mark_price,
            exchange_data_return_at=now - timedelta(seconds=1),
            **stamps,
        ),
        funding=funding,
    )
    return source, market, analysis, selection, quote


def test_native_snapshot_retains_v2_quote_and_correct_funding_pair(inputs):
    source, market, analysis, selection, quote = inputs
    snapshot = evidence.prepare_native_evidence_v2(
        market,
        analysis,
        qualification=source.original_run.result,
        detection=source.original_run.prefix.detection,
        quote=quote,
        protection=selection,
        prepared_at=source.original_run.result.evaluated_at,
        zone_tick_size=source.original_run.policy.prefix.tick_size,
    )
    assert snapshot.schema_version == "ctcc_trade_evidence_native_v2"
    assert type(snapshot.quote) is ExecutableQuoteV2
    assert evidence.validate_native_snapshot_v2(snapshot) == snapshot
    assert (
        snapshot.quote.funding.forecast.settlement_at
        != snapshot.quote.funding.following_settlement_forecast_at
    )
    assert not snapshot.execution_authority
    price, at, label = renderer._quote_display(snapshot)
    assert price == (
        quote.ticker.ask if snapshot.direction == "long" else quote.ticker.bid
    )
    assert at == quote.ticker.source_generated_at
    assert "fundingTime" in label


def test_legacy_quote_cannot_enter_native_snapshot_adapter(inputs):
    source, market, analysis, selection, _ = inputs
    with pytest.raises(
        service.EvidenceError, match="^native_v2_exact_original_g11_required$"
    ):
        evidence.prepare_native_evidence_v2(
            market,
            analysis,
            qualification=source.original_run.result,
            detection=source.original_run.prefix.detection,
            quote=source.original_inputs["quote"].quote,
            protection=selection,
            prepared_at=source.original_run.result.evaluated_at,
            zone_tick_size=source.original_run.policy.prefix.tick_size,
        )


@pytest.fixture(scope="module")
def codec_snapshot(inputs):
    source, market, analysis, selection, quote = inputs
    return evidence.prepare_native_evidence_v2(
        market,
        analysis,
        qualification=source.original_run.result,
        detection=source.original_run.prefix.detection,
        quote=quote,
        protection=selection,
        prepared_at=source.original_run.result.evaluated_at,
        zone_tick_size=source.original_run.policy.prefix.tick_size,
    )


def test_native_v2_full_json_and_instance_roundtrip_preserves_fixed_metadata(
    codec_snapshot,
):
    snapshot = codec_snapshot
    wire = snapshot.model_dump_json(round_trip=True)
    replay = evidence.NativeEvidenceSnapshotV2.model_validate_json(wire, strict=True)
    assert replay == snapshot
    assert type(replay.quote) is ExecutableQuoteV2
    assert type(replay.quote.funding) is FundingObservationV2
    assert (
        evidence.NativeEvidenceSnapshotV2.model_validate(snapshot, strict=True)
        == snapshot
    )
    document = json.loads(wire)["quote"]
    assert set(document) == {item.name for item in fields(ExecutableQuoteV2)}
    assert set(document["funding"]) == {
        item.name for item in fields(FundingObservationV2)
    }
    assert document["schema_version"] == "ctcc.executable_quote.v2"
    assert document["region"] == "global"
    assert document["funding"]["schema_version"] == "ctcc.funding_observation.v2"
    assert document["funding"]["rate_generated_at"] is None
    assert document["funding"]["historical_first_available_at"] is None
    for value in (document, document["funding"]):
        assert value["source_authenticity_verified"] is False
        assert value["execution_authority"] is False
        assert value["admission"] == "DENY"
    assert evidence.validate_native_snapshot_v2(replay) == snapshot


_FIXED_METADATA_MUTANTS = (
    ("quote", "schema_version", "ctcc.executable_quote.v1"),
    ("quote", "region", "unapproved"),
    ("quote", "source_authenticity_verified", True),
    ("quote", "execution_authority", True),
    ("quote", "admission", "ALLOW"),
    ("funding", "schema_version", "ctcc.funding_observation.v1"),
    ("funding", "rate_generated_at", "2026-09-12T10:00:00Z"),
    ("funding", "historical_first_available_at", "2026-09-12T10:00:00Z"),
    ("funding", "source_authenticity_verified", True),
    ("funding", "receipt_authenticity_verified", True),
    ("funding", "point_in_time_verified", True),
    ("funding", "account_complete", True),
    ("funding", "execution_authority", True),
    ("funding", "admission", "ALLOW"),
)


@pytest.mark.parametrize("scope,name,changed", _FIXED_METADATA_MUTANTS)
def test_native_v2_rejects_every_changed_or_missing_fixed_wire_field(
    codec_snapshot, scope, name, changed
):
    snapshot = codec_snapshot
    original = snapshot.model_dump_json(round_trip=True)
    for missing in (False, True):
        document = json.loads(original)
        target = document["quote"] if scope == "quote" else document["quote"]["funding"]
        if missing:
            del target[name]
        else:
            target[name] = changed
        with pytest.raises(ValidationError):
            evidence.NativeEvidenceSnapshotV2.model_validate_json(
                json.dumps(document), strict=True
            )
    # A fresh genuine typed instance must reject the same change, rather than
    # silently restoring defaults. The accepted module fixture is untouched.
    quote = replace(snapshot.quote, funding=replace(snapshot.quote.funding))
    target = quote if scope == "quote" else quote.funding
    object.__setattr__(target, name, changed)
    document = {
        field: getattr(snapshot, field) for field in type(snapshot).model_fields
    }
    document["quote"] = quote
    with pytest.raises(ValidationError) as rejected:
        evidence.NativeEvidenceSnapshotV2(**document)
    assert any(error["loc"][0] == "quote" for error in rejected.value.errors())


@pytest.mark.parametrize("scope", ("quote", "funding", "ticker", "mark", "forecast"))
def test_native_v2_extra_quote_or_component_fields_remain_forbidden(
    codec_snapshot, scope
):
    document = json.loads(codec_snapshot.model_dump_json(round_trip=True))
    quote = document["quote"]
    target = {
        "quote": quote,
        "funding": quote["funding"],
        "ticker": quote["ticker"],
        "mark": quote["mark"],
        "forecast": quote["funding"]["forecast"],
    }[scope]
    target["unapproved"] = False
    with pytest.raises(ValidationError):
        evidence.NativeEvidenceSnapshotV2.model_validate_json(
            json.dumps(document), strict=True
        )


@pytest.mark.parametrize(
    "changed",
    (
        "raw_body",
        "body_sha256",
        "canonical_sha256",
        "forecast_pair",
        "legacy_quote",
        "false_as_int",
    ),
)
def test_native_v2_replays_original_funding_bytes_pair_and_exact_flags(
    codec_snapshot, inputs, changed
):
    document = json.loads(codec_snapshot.model_dump_json(round_trip=True))
    quote, funding = document["quote"], document["quote"]["funding"]
    if changed == "raw_body":
        original = funding["raw_body"]
        funding["raw_body"] = original.replace(
            '"fundingRate": "0.0001"', '"fundingRate": "0.0003"'
        )
        assert funding["raw_body"] != original
    elif changed in ("body_sha256", "canonical_sha256"):
        funding[changed] = "0" * 64
    elif changed == "forecast_pair":
        funding["forecast"]["settlement_at"] = funding[
            "following_settlement_forecast_at"
        ]
    elif changed == "legacy_quote":
        source = inputs[0]
        document["quote"] = source.original_inputs["quote"].quote.model_dump(
            mode="json", round_trip=True
        )
    else:
        quote["execution_authority"] = 0
    with pytest.raises(ValidationError):
        evidence.NativeEvidenceSnapshotV2.model_validate_json(
            json.dumps(document), strict=True
        )
