"""Real G1 mathematics over a complete synthetic owned-public v2 capture.

Clock/TLS/transport/storage are test replacements. No native, Demo, MIE or
execution acceptance follows; no G1 or source validator is stubbed.
"""

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta, timezone, tzinfo
from decimal import Context, Decimal, Inexact, localcontext
from types import MappingProxyType

import pytest
from pydantic import BaseModel, ValidationError, model_serializer

from app.public_market_source.public_market_receipts import canonical, decode, sha
from app.trade_qualification import data, demo_public_origin
from app.trade_qualification import data_v2 as module
from app.trade_qualification import public_market_collector as legacy
from app.trade_qualification import public_market_collector_v2 as public
from app.trade_qualification import qualification_runtime as initial
from app.trade_qualification import quote_collector_v2 as quotes_v2
from app.trade_qualification.executable_quote_v2 import POLICY_SHA256
from app.trade_qualification.quote_collector_v2 import TRANSPORT_POLICY_SHA256
from tests.unit.qualification_prefix_fixtures import DATA_POLICY
from tests.unit.research.test_owned_public_runtime_v2 import (
    captured as captured,  # noqa: PLC0414 -- actual synthetic owner fixture
)
from tests.unit.research.test_owned_public_runtime_v2 import (
    empty_registries,
    policy,
    routed_packet_parts,
    setup,
)
from tests.unit.research.test_owned_public_runtime_v2 import (
    source as source,  # noqa: PLC0414 -- synchronous raw source fixture
)

D = Decimal


def evaluate(packet, now, *, selected=DATA_POLICY):
    return module.evaluate_public_market_data_v2(
        packet,
        expected_bundle_sha256=packet.bundle_sha256,
        policy=selected,
        evaluated_at=now,
    )


def verify(result, packet, now, *, selected=DATA_POLICY):
    return module.verify_public_market_data_v2(
        result,
        packet,
        expected_bundle_sha256=packet.bundle_sha256,
        policy=selected,
        evaluated_at=now,
    )


def reject_without_callbacks(changed, captured, called):
    assert called == []
    with pytest.raises(ValueError):
        _ = changed.evaluation_sha256
    assert called == []
    with pytest.raises(ValueError):
        verify(changed, captured[0].packet, captured[0].context.evaluated_at)
    assert called == []


@pytest.fixture(scope="module")
def passing(captured):
    diagnostic = captured[0]
    result = evaluate(diagnostic.packet, diagnostic.context.evaluated_at)
    assert result.passed, result.gate
    return result


def test_full_owner_raw_profile_and_upcoming_funding_pair_recompute_g1(
    captured, passing
):
    diagnostic = captured[0]
    packet, now = diagnostic.packet, diagnostic.context.evaluated_at
    document = decode(packet.packet_json, public.MAX_PACKET_BYTES)
    pair = decode(passing.funding_pair_json.encode())
    quote = diagnostic.context.quote
    assert passing.schema_version == "ctcc.data_qualification.v2"
    assert passing.public_bundle_sha256 == packet.bundle_sha256
    assert passing.quote_bundle_sha256 == document["quote_sha256"]
    assert passing.quote_profile_sha256 == POLICY_SHA256
    assert passing.quote_transport_policy_sha256 == TRANSPORT_POLICY_SHA256
    assert pair["rate"] == str(quote.funding.forecast.rate)
    assert pair["settlement_at"] == quote.funding.forecast.settlement_at.isoformat()
    assert (
        pair["settlement_at"]
        != quote.funding.following_settlement_forecast_at.isoformat()
    )
    assert pair["raw_body_sha256"] == quote.funding.body_sha256
    assert (
        pair["rate_generated_at"] is None
        and pair["historical_first_available_at"] is None
    )
    assert passing.quote_inspection_sha256 == sha(
        passing.quote_inspection_json.encode()
    )
    assert passing.source_sha256 == sha(passing.source_json.encode())
    assert passing.gate.measured_values["quality_recomputed"] is True
    assert passing.gate.measured_values["analysis_recomputed"] is True
    assert passing.admission == "DENY"
    assert passing.execution_authority is False
    assert passing.source_authenticity_verified is False
    assert passing.original_source_verified is False
    assert verify(passing, packet, now) == passing
    restored = module.DataQualificationResultV2.model_validate_json(
        passing.model_dump_json()
    )
    assert restored.evaluation_sha256 == passing.evaluation_sha256


@pytest.mark.parametrize("region", ["global", "us_au", "eea"])
def test_routed_g1_pins_replayed_quote_transport_without_authority(captured, region):
    packet, _ = public._build(**routed_packet_parts(captured, region))
    now = captured[0].context.evaluated_at
    result = evaluate(packet, now)
    document = decode(packet.packet_json, public.MAX_PACKET_BYTES)
    route = demo_public_origin.reviewed_demo_public_route(region)
    transport_pin = quotes_v2._transport_policy_sha256(route)
    assert result.passed
    assert result.schema_version == "ctcc.data_qualification.v2"
    assert result.quote_transport_policy_sha256 == transport_pin
    assert document["quote"]["transport_policy_sha256"] == transport_pin
    assert document["quote_transport_policy_sha256"] == transport_pin
    assert transport_pin != TRANSPORT_POLICY_SHA256
    assert verify(result, packet, now) == result
    assert result.admission == "DENY"
    assert result.source_authenticity_verified is False
    assert result.execution_authority is False


def test_routed_g1_rejects_cross_route_result_and_unreviewed_pin(captured):
    now = captured[0].context.evaluated_at
    global_packet, _ = public._build(**routed_packet_parts(captured, "global"))
    us_packet, _ = public._build(**routed_packet_parts(captured, "us_au"))
    global_result = evaluate(global_packet, now)
    us_result = evaluate(us_packet, now)
    assert global_result.quote_transport_policy_sha256 != (
        us_result.quote_transport_policy_sha256
    )
    with pytest.raises(ValueError, match="data_v2_replay_mismatch"):
        verify(global_result, us_packet, now)
    changed = json.loads(global_result.model_dump_json())
    changed["quote_transport_policy_sha256"] = us_result.quote_transport_policy_sha256
    forged = module.DataQualificationResultV2.model_validate_json(json.dumps(changed))
    with pytest.raises(ValueError, match="data_v2_replay_mismatch"):
        verify(forged, global_packet, now)
    changed["quote_transport_policy_sha256"] = TRANSPORT_POLICY_SHA256
    old_misbound = module.DataQualificationResultV2.model_validate_json(
        json.dumps(changed)
    )
    with pytest.raises(ValueError, match="data_v2_replay_mismatch"):
        verify(old_misbound, global_packet, now)
    changed["quote_transport_policy_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="data_v2_identity_or_profile_mismatch"):
        module.DataQualificationResultV2.model_validate_json(json.dumps(changed))


def test_later_context_mutation_cannot_replace_raw_snapshot(captured, passing):
    diagnostic = captured[0]
    diagnostic.context.market.quality["untrusted"] = True
    diagnostic.context.market.mark_price = D("999999")
    assert evaluate(diagnostic.packet, diagnostic.context.evaluated_at) == passing


def test_six_seconds_after_evaluation_cannot_restore_stale_quote(captured):
    diagnostic = captured[0]
    with pytest.raises(ValueError):
        evaluate(
            diagnostic.packet, diagnostic.context.evaluated_at + timedelta(seconds=6)
        )


@pytest.mark.parametrize(
    "supplied", ["context", "market", "quote", "packet-dict", "v1-packet"]
)
def test_only_exact_full_v2_packet_is_admitted(captured, source, supplied):
    diagnostic = captured[0]
    value = {
        "context": diagnostic.context,
        "market": diagnostic.context.market,
        "quote": diagnostic.context.quote,
        "packet-dict": decode(diagnostic.packet.packet_json, public.MAX_PACKET_BYTES),
        "v1-packet": legacy.CollectedPublicMarket.model_construct(),
    }[supplied]
    with pytest.raises(ValueError):
        module.evaluate_public_market_data_v2(
            value,
            expected_bundle_sha256=diagnostic.packet.bundle_sha256,
            policy=source.policy,
            evaluated_at=diagnostic.context.evaluated_at,
        )


@pytest.mark.parametrize(
    "mutation", ["profile", "quote-rate", "candle-tail", "raw-body"]
)
def test_rehashed_full_packet_still_requires_raw_semantic_replay(captured, mutation):
    diagnostic = captured[0]
    document = decode(diagnostic.packet.packet_json, public.MAX_PACKET_BYTES)
    if mutation == "profile":
        document["quote_profile_sha256"] = "0" * 64
    elif mutation == "quote-rate":
        document["quote"]["inspection"]["quote_document"]["funding"]["rate"] = "0.01"
    elif mutation == "candle-tail":
        document["candles"]["frames"][0]["verified_through"] = (
            "2024-01-01T00:00:00+00:00"
        )
    else:
        document["quote"]["provenance"][0]["response_body"] += " "
    changed = public.CollectedPublicMarketV2(canonical(document))
    with pytest.raises(ValueError):
        evaluate(changed, diagnostic.context.evaluated_at)


@pytest.mark.parametrize("mutation", ["funding-pair", "source", "inspection", "gate"])
def test_self_signed_result_does_not_replace_replaying_raw_inputs(
    captured, passing, mutation
):
    value = json.loads(passing.model_dump_json())
    if mutation == "funding-pair":
        pair = decode(value["funding_pair_json"].encode())
        pair["settlement_at"] = captured[
            0
        ].context.quote.funding.following_settlement_forecast_at.isoformat()
        raw = canonical(pair)
        value.update(funding_pair_json=raw.decode(), funding_pair_sha256=sha(raw))
    elif mutation == "source":
        value.update(source_json="{}", source_sha256=sha(b"{}"))
    elif mutation == "inspection":
        inspection = decode(value["quote_inspection_json"].encode())
        inspection["caller_claim"] = "passed"
        raw = canonical(inspection)
        value.update(
            quote_inspection_json=raw.decode(), quote_inspection_sha256=sha(raw)
        )
    else:
        value["gate"].update(
            passed=False, code="funding_exceeded", reason="Self-signed claim"
        )
    changed = module.DataQualificationResultV2.model_validate_json(json.dumps(value))
    assert changed.evaluation_sha256 != passing.evaluation_sha256
    with pytest.raises(ValueError, match="data_v2_replay_mismatch"):
        verify(changed, captured[0].packet, captured[0].context.evaluated_at)


@pytest.mark.parametrize(
    "field",
    ["execution_authority", "source_authenticity_verified", "original_source_verified"],
)
@pytest.mark.parametrize("value", [True, 0])
def test_result_flags_must_be_exact_false(passing, field, value):
    raw = json.loads(passing.model_dump_json())
    raw[field] = value
    with pytest.raises(ValidationError):
        module.DataQualificationResultV2.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("target", ["result", "gate", "policy"])
def test_fingerprint_and_verify_reject_hidden_fields_before_serialization(
    captured, passing, target
):
    if target == "result":
        changed = passing.model_copy(update={"hidden": True})
    else:
        changed = passing.model_copy(
            update={
                target: getattr(passing, target).model_copy(update={"hidden": True})
            }
        )
    with pytest.raises(ValueError):
        _ = changed.evaluation_sha256
    with pytest.raises(ValueError):
        verify(changed, captured[0].packet, captured[0].context.evaluated_at)


def test_foreign_policy_serializer_is_not_invoked(captured, passing):
    called = []

    class ForeignPolicy(data.DataQualificationPolicy):
        @model_serializer
        def foreign_dump(self):
            called.append(True)
            raise RuntimeError("foreign policy serializer must not run")

    foreign = ForeignPolicy.model_validate(passing.policy.model_dump(mode="python"))
    changed = passing.model_copy(update={"policy": foreign})
    with pytest.raises(ValueError):
        _ = changed.evaluation_sha256
    with pytest.raises(ValueError):
        verify(changed, captured[0].packet, captured[0].context.evaluated_at)
    assert not called


@pytest.mark.parametrize(
    "field,maximum",
    [
        ("quote_inspection_json", 65536),
        ("funding_pair_json", 2048),
        ("source_json", data.MAX_SOURCE_BYTES),
    ],
)
def test_result_utf8_byte_limit_is_not_only_a_character_limit(
    captured, passing, field, maximum
):
    changed = passing.model_copy(update={field: "é" * (maximum // 2 + 1)})
    assert len(getattr(changed, field)) < maximum
    with pytest.raises(ValueError, match="data_v2_result_source_bound"):
        _ = changed.evaluation_sha256
    with pytest.raises(ValueError, match="data_v2_result_source_bound"):
        verify(changed, captured[0].packet, captured[0].context.evaluated_at)


def test_hostile_decimal_context_and_timezone_preserve_exact_g1_bytes(
    captured, passing
):
    diagnostic = captured[0]
    context = Context(prec=6)
    context.traps[Inexact] = True
    with localcontext(context):
        actual = evaluate(
            diagnostic.packet,
            diagnostic.context.evaluated_at.astimezone(timezone(timedelta(hours=8))),
        )
    assert data._canonical(actual) == data._canonical(passing)
    assert actual.evaluation_sha256 == passing.evaluation_sha256


@pytest.fixture(scope="module")
async def funding_boundary(source, tmp_path_factory):
    samples = []
    for rate in (D("0.001"), D("0.00100000000000000001")):
        raw_source = replace(source, market=source.market.model_copy(deep=True))
        raw_source.market.funding_rate = rate
        with pytest.MonkeyPatch.context() as patch:
            _, _directory, harness, _ = setup(patch, raw_source)
            diagnostic = await initial.capture_initial_public_market_v2(
                tmp_path_factory.mktemp("g1-v2-funding-boundary"),
                instrument_id="BTC-USDT-SWAP",
                market_policy=policy(),
            )
            assert diagnostic.context is not None and diagnostic.packet is not None
            assert diagnostic.context.quote.funding.forecast.rate == rate
            harness.assert_closed()
            empty_registries()
            samples.append(diagnostic)
    return tuple(samples)


@pytest.mark.parametrize("index,code", [(0, "passed"), (1, "funding_exceeded")])
def test_absolute_funding_exact_boundary_and_twenty_decimal_place_overrun(
    funding_boundary, index, code
):
    diagnostic = funding_boundary[index]
    result = evaluate(diagnostic.packet, diagnostic.context.evaluated_at)
    assert result.gate.code == code
    assert verify(result, diagnostic.packet, diagnostic.context.evaluated_at) == result


def test_v1_and_v2_result_types_cannot_be_relabelled(captured, passing, source):
    old = data.evaluate_data(
        source.market,
        report_id=source.report_id,
        instrument_id=source.market.instrument_id,
        quote=source.quote,
        reference=source.reference,
        policy=source.policy,
        evaluated_at=source.evaluated_at,
    )
    assert old.passed
    with pytest.raises(ValueError, match="data_v2_exact_result_required"):
        verify(old, captured[0].packet, captured[0].context.evaluated_at)
    with pytest.raises(ValidationError):
        data.DataQualificationResult.model_validate_json(passing.model_dump_json())
    with pytest.raises(ValueError, match="exact G1 result required"):
        data.verify_data_result(
            passing,
            source.market,
            report_id=source.report_id,
            instrument_id=source.market.instrument_id,
            quote=source.quote,
            reference=source.reference,
            policy=source.policy,
            evaluated_at=source.evaluated_at,
        )


@pytest.mark.parametrize(
    "kind", ["mapping", "mapping-proxy", "dict-subclass", "dict-subclass-proxy"]
)
def test_record_guard_rejects_foreign_mapping_before_any_method(
    captured, passing, kind
):
    called = []

    def foreign(method):
        called.append(method)
        raise AssertionError("foreign measurement mapping must not be accessed")

    class ForeignMapping(Mapping):
        def __getitem__(self, key):
            return foreign("getitem")

        def __iter__(self):
            return foreign("iter")

        def __len__(self):
            return foreign("len")

        def items(self):
            return foreign("items")

        def keys(self):
            return foreign("keys")

        def values(self):
            return foreign("values")

    class ForeignDict(dict):
        def __iter__(self):
            return foreign("dict-iter")

        def __len__(self):
            return foreign("dict-len")

        def items(self):
            return foreign("dict-items")

    supplied = (
        ForeignDict({"untrusted": "value"})
        if kind.startswith("dict-subclass")
        else ForeignMapping()
    )
    if kind.endswith("proxy"):
        supplied = MappingProxyType(supplied)
    changed = passing.model_copy(
        update={"gate": passing.gate.model_copy(update={"measured_values": supplied})}
    )
    reject_without_callbacks(changed, captured, called)


def test_record_guard_rejects_foreign_measurement_model_before_serializer(
    captured, passing
):
    called = []

    class ForeignMeasurement(BaseModel):
        payload: str = "untrusted"

        @model_serializer
        def foreign_dump(self):
            called.append("measurement-serializer")
            raise AssertionError("foreign measurement model must not serialize")

    changed = passing.model_copy(
        update={
            "gate": passing.gate.model_copy(
                update={"measured_values": {"untrusted": ForeignMeasurement()}}
            )
        }
    )
    reject_without_callbacks(changed, captured, called)


def test_record_guard_rejects_decimal_subclass_before_numeric_methods(
    captured, passing
):
    called = []

    class ForeignDecimal(Decimal):
        def __str__(self):
            called.append("decimal-str")
            raise AssertionError("foreign Decimal must not be rendered")

        def is_finite(self):
            called.append("decimal-finite")
            raise AssertionError("foreign Decimal must not be inspected")

        def as_tuple(self):
            called.append("decimal-tuple")
            raise AssertionError("foreign Decimal must not be inspected")

    changed = passing.model_copy(
        update={
            "gate": passing.gate.model_copy(
                update={"measured_values": {"untrusted": ForeignDecimal("1")}}
            )
        }
    )
    reject_without_callbacks(changed, captured, called)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(D("0." + "1" * 129), id="129-digits"),
        pytest.param(D("1e129"), id="positive-exponent-129"),
        pytest.param(D("1e-129"), id="negative-exponent-129"),
        pytest.param(D("NaN"), id="nan"),
        pytest.param(D("Infinity"), id="infinity"),
    ],
)
def test_record_guard_rejects_unbounded_measurement_decimal_representation(
    captured, passing, value
):
    changed = passing.model_copy(
        update={
            "gate": passing.gate.model_copy(
                update={"measured_values": {"untrusted": value}}
            )
        }
    )
    reject_without_callbacks(changed, captured, [])


@pytest.mark.parametrize(
    "field",
    [
        "report_id",
        "public_bundle_sha256",
        "quote_inspection_sha256",
        "reference_sha256",
        "evaluated_at",
        "execution_authority",
        "admission",
    ],
)
def test_record_guard_rejects_foreign_top_scalar_before_serializer(
    captured, passing, field
):
    called = []

    class ForeignScalar(BaseModel):
        payload: str = "untrusted"

        @model_serializer
        def foreign_dump(self):
            called.append("top-scalar-serializer")
            raise AssertionError("foreign scalar must not serialize")

    changed = passing.model_copy(update={field: ForeignScalar()})
    reject_without_callbacks(changed, captured, called)


@pytest.mark.parametrize("field", ["report_id", "gate", "passed", "reason"])
def test_record_guard_rejects_foreign_gate_scalar_before_serializer(
    captured, passing, field
):
    called = []

    class ForeignScalar(BaseModel):
        payload: str = "untrusted"

        @model_serializer
        def foreign_dump(self):
            called.append("gate-scalar-serializer")
            raise AssertionError("foreign gate scalar must not serialize")

    changed = passing.model_copy(
        update={"gate": passing.gate.model_copy(update={field: ForeignScalar()})}
    )
    reject_without_callbacks(changed, captured, called)


def test_record_guard_rejects_custom_timezone_without_calling_it(captured, passing):
    called = []

    class ForeignTimezone(tzinfo):
        def utcoffset(self, value):
            called.append("utcoffset")
            raise AssertionError("foreign timezone must not be consulted")

        def dst(self, value):
            called.append("dst")
            raise AssertionError("foreign timezone must not be consulted")

        def tzname(self, value):
            called.append("tzname")
            raise AssertionError("foreign timezone must not be consulted")

    changed = passing.model_copy(
        update={"evaluated_at": passing.evaluated_at.replace(tzinfo=ForeignTimezone())}
    )
    reject_without_callbacks(changed, captured, called)


@pytest.mark.parametrize("kind", ["dict", "dict-proxy"])
def test_record_guard_accepts_equivalent_native_measurement_containers(
    captured, passing, kind
):
    supplied = dict(passing.gate.measured_values)
    if kind == "dict-proxy":
        supplied = MappingProxyType(supplied)
    changed = passing.model_copy(
        update={"gate": passing.gate.model_copy(update={"measured_values": supplied})}
    )
    assert changed.evaluation_sha256 == passing.evaluation_sha256
    assert (
        verify(changed, captured[0].packet, captured[0].context.evaluated_at) == passing
    )
