"""Actual shared math/raw replay over synthetic owner IO; no native acceptance."""

import inspect
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, tzinfo
from decimal import Context, Decimal, Inexact, Rounded, localcontext
from types import MappingProxyType

import pytest
from pydantic import BaseModel, model_serializer

from app.public_market_source.public_market_receipts import canonical, sha
from app.trade_qualification import full_public_numeric_v2_profile as profile_module
from app.trade_qualification import public_market_collector_v2 as public
from app.trade_qualification.full_public_economics_v2 import (
    diagnose_full_public_economics_v2,
    verify_full_public_economics_v2,
)
from app.trade_qualification.full_public_location_v2 import (
    diagnose_full_public_location_v2,
    verify_full_public_location_v2,
)
from app.trade_qualification.full_public_numeric_v2_guard import (
    FullPublicNumericV2Error,
    primitive_tree,
)
from app.trade_qualification.full_public_numeric_v2_models import (
    FullPublicEconomicsDiagnosticV2,
    FullPublicLocationDiagnosticV2,
)
from app.trade_qualification.full_public_numeric_v2_profile import (
    PROFILE_BYTES,
    PROFILE_ID,
    PROFILE_SHA256,
)
from tests.unit.full_public_numeric_v2_fixtures import (
    arguments,
    capture_direction,
    capture_history,
)
from tests.unit.test_qualification_location import evaluate as old_location

APIS = (
    (
        diagnose_full_public_location_v2,
        verify_full_public_location_v2,
        FullPublicLocationDiagnosticV2,
    ),
    (
        diagnose_full_public_economics_v2,
        verify_full_public_economics_v2,
        FullPublicEconomicsDiagnosticV2,
    ),
)


@pytest.fixture(scope="module", params=["long", "short"])
def captured(request, tmp_path_factory):
    return capture_direction(request.param, tmp_path_factory)


@pytest.fixture(scope="module", params=APIS, ids=["location", "economics"])
def diagnostic(request, captured):
    _, raw, account = captured
    evaluate, verify, kind = request.param
    result = evaluate(raw.packet, account, **arguments(raw, account))
    return evaluate, verify, kind, result


def closed(result):
    value = json.loads(result.receipt_json)
    assert value["admission"] == "DENY" and result.execution_authority is False
    assert all(
        value[name] is False
        for name in (
            "execution_authority",
            "qualification_performed",
            "execution_recheck_performed",
            "native_clock_verified",
            "original_source_verified",
            "account_complete",
            "atomic_risk_reserved",
            "metadata_current_owned",
        )
    )
    assert all(
        value["unknown"][name] is None
        for name in (
            "quantity",
            "leverage",
            "portfolio_risk",
            "actual_exchange_fee_source",
            "fee_authenticity",
            "rate_generated_at",
            "historical_first_available_at",
            "native_production_profile",
        )
    )
    assert result.receipt_sha256 == sha(result.receipt_json)
    return value


def test_fixed_profile_is_explicit_synthetic_and_public_signatures_are_closed():
    profile = json.loads(PROFILE_BYTES)
    assert profile["native_profile_registered"] is False
    assert profile["actual_fee_source"] is None and profile["admission"] == "DENY"
    assert profile["economics"]["round_trip_fee_bps"] == "10"
    assert PROFILE_SHA256 == sha(PROFILE_BYTES)
    prohibited = {
        "direction",
        "entry",
        "candidate",
        "zone",
        "stop_loss",
        "take_profit",
        "score",
        "quantity",
        "leverage",
        "passed",
        "callback",
        "g1",
        "context",
        "data_policy",
    }
    for evaluate, _, _ in APIS:
        assert not prohibited.intersection(inspect.signature(evaluate).parameters)


def test_actual_raw_derivation_math_and_fixed_original_geometry(captured, diagnostic):
    direction, raw, account = captured
    evaluate, verify, _, result = diagnostic
    before_public = raw.packet.packet_json
    before_account = account.model_dump_json(round_trip=True)
    value = closed(result)
    assert value["math_checks_passed"] is True
    assert value["precursor"]["direction"] == direction
    assert value["precursor"]["action"] == "WAIT"
    assert value["bindings"]["initial_entry"] == value["precursor"]["initial_entry"]
    assert value["bindings"]["fixed_expires_at"] == value["precursor"]["expires_at"]
    assert value["bindings"]["funding_pair"]["rate_generated_at"] is None
    assert (
        value["bindings"]["funding_pair_sha256"]
        == value["precursor"]["funding_pair_sha256"]
    )
    assert value["g1"]["gate"]["passed"] is True
    if value["kind"] == "economics":
        costs = value["economics"]
        assert (
            costs["candidate"]["entry"]
            == costs["execution"]["entry"]
            == value["bindings"]["initial_entry"]
        )
        assert costs["candidate"]["stop_loss"] == costs["execution"]["stop_loss"]
        assert costs["candidate"]["take_profit"] == costs["execution"]["take_profit"]
        assert Decimal(costs["comparison"]["entry_delta_per_base"]) == 0
        assert value["selection"]["source_sha256"] == value["g1"]["source_sha256"]
        assert costs["actual_fee_authenticity"] is None
    assert (
        verify(result, raw.packet, account, **arguments(raw, account)).receipt_json
        == result.receipt_json
    )
    assert (
        evaluate(raw.packet, account, **arguments(raw, account)).receipt_json
        == result.receipt_json
    )
    assert raw.packet.packet_json == before_public
    assert account.model_dump_json(round_trip=True) == before_account


@pytest.mark.parametrize(
    "strategy",
    ["trend_pullback", "breakout_continuation", "fvg_return", "order_block_return"],
)
def test_fixed_base_catalog_retains_original_failures(captured, strategy):
    _, raw, account = captured
    result = diagnose_full_public_location_v2(
        raw.packet, account, **arguments(raw, account, strategy=strategy)
    )
    value = closed(result)
    if value["precursor"]["intent"] is None:
        assert (
            value["code"] == value["precursor"]["code"]
            and value["math_checks_passed"] is False
        )
    else:
        assert value["location"] is not None
        assert value["bindings"]["event_key"] == value["precursor"]["event_key"]


@pytest.mark.parametrize(
    "strategy",
    [
        "structure_reversal",
        "volatility_expansion",
        "range_reversal",
        "liquidity_sweep_reversal",
    ],
)
@pytest.mark.parametrize("direction", ["long", "short"])
def test_history_source_remains_explicit_wait_without_borrowed_stage_c(
    strategy, direction, tmp_path_factory
):
    _, _, raw, account = capture_history(strategy, direction, tmp_path_factory)
    result = diagnose_full_public_economics_v2(
        raw.packet, account, **arguments(raw, account, strategy=strategy)
    )
    value = closed(result)
    assert value["precursor"]["sweep_stage_C_integrated"] is False
    assert value["economics"] is None and value["math_checks_passed"] is False
    if value["precursor"]["intent"] is not None:
        assert (
            value["code"] == "history_numeric_slice_not_integrated"
            and value["action"] == "WAIT"
        )
    else:
        assert value["code"] == value["precursor"]["code"]


@pytest.mark.parametrize(
    "field",
    ["bindings", "precursor", "g1", "location", "selection", "economics", "unknown"],
)
def test_canonical_rehashed_diagnostic_requires_actual_raw_replay(
    captured, diagnostic, field
):
    _, raw, account = captured
    _, verify, kind, result = diagnostic
    value = json.loads(result.receipt_json)
    value[field] = {"self_signed": True}
    changed = kind(canonical(value))
    assert changed.receipt_sha256 == sha(changed.receipt_json)
    with pytest.raises(FullPublicNumericV2Error, match="raw_replay_mismatch"):
        verify(changed, raw.packet, account, **arguments(raw, account))


@pytest.mark.parametrize(
    "field",
    [
        "execution_authority",
        "qualification_performed",
        "execution_recheck_performed",
        "account_complete",
    ],
)
def test_no_caller_flag_can_upgrade_diagnostic(captured, diagnostic, field):
    _, raw, account = captured
    _, verify, kind, result = diagnostic
    value = json.loads(result.receipt_json)
    value[field] = True
    changed = kind(canonical(value))
    with pytest.raises(FullPublicNumericV2Error, match="cannot_grant_authority"):
        _ = changed.receipt_sha256
    with pytest.raises(FullPublicNumericV2Error, match="cannot_grant_authority"):
        verify(changed, raw.packet, account, **arguments(raw, account))


@pytest.mark.parametrize("component", ["candles", "market_aux", "ws", "quote"])
def test_raw_component_removal_cannot_be_repaired_by_packet_rehash(captured, component):
    _, raw, account = captured
    value = json.loads(raw.packet.packet_json)
    value.pop(component)
    changed = public.CollectedPublicMarketV2(canonical(value))
    with pytest.raises(FullPublicNumericV2Error):
        diagnose_full_public_location_v2(
            changed,
            account,
            **arguments(
                raw, account, expected_public_bundle_sha256=changed.bundle_sha256
            ),
        )


def test_full_raw_inspection_rehash_cannot_hide_unknown_funding_field(captured):
    _, raw, account = captured
    original_raw = raw.packet.packet_json
    value = json.loads(original_raw)
    funding = value["quote"]["inspection"]["quote_document"]["funding"]
    assert "rate" in funding and funding["rate"] == "0"
    original_provenance = canonical(value["quote"]["provenance"])
    funding["rate"] = "0"
    same = public.CollectedPublicMarketV2(canonical(value))
    assert same.packet_json == original_raw
    assert same.bundle_sha256 == raw.packet.bundle_sha256
    # Alter the known inspection field while retaining its original raw source.
    funding["rate"] = "0.001"
    changed = public.CollectedPublicMarketV2(canonical(value))
    assert changed.packet_json != original_raw
    assert changed.bundle_sha256 != raw.packet.bundle_sha256
    assert canonical(value["quote"]["provenance"]) == original_provenance
    assert raw.packet.packet_json == original_raw
    with pytest.raises(FullPublicNumericV2Error):
        diagnose_full_public_location_v2(
            changed,
            account,
            **arguments(
                raw, account, expected_public_bundle_sha256=changed.bundle_sha256
            ),
        )


def test_legacy_packet_and_bare_context_are_not_raw_authority(captured, diagnostic):
    _, raw, account = captured
    evaluate, verify, _, _ = diagnostic
    for value in (
        raw.context,
        raw.context.market,
        b"{}",
        public.CollectedPublicMarketV2(
            b'{"schema_version":"ctcc.collected_public_market.v1"}'
        ),
    ):
        with pytest.raises(FullPublicNumericV2Error):
            evaluate(value, account, **arguments(raw, account))
    with pytest.raises(FullPublicNumericV2Error, match="exact_result_required"):
        verify(old_location(), raw.packet, account, **arguments(raw, account))
    with pytest.raises(TypeError):
        evaluate(
            raw.packet, account, **arguments(raw, account), candidate_entry=Decimal(100)
        )


def test_actual_profile_and_plan_pins_and_six_second_freshness(captured, diagnostic):
    _, raw, account = captured
    evaluate, _, _, _ = diagnostic
    for updates in (
        {"profile_id": PROFILE_ID + "-native"},
        {"expected_profile_sha256": "0" * 64},
        {"expected_account_plan_sha256": "0" * 64},
        {"expected_account_packet_sha256": "0" * 64},
    ):
        with pytest.raises(FullPublicNumericV2Error):
            evaluate(raw.packet, account, **arguments(raw, account, **updates))
    with pytest.raises(FullPublicNumericV2Error):
        evaluate(
            raw.packet,
            account,
            **arguments(
                raw,
                account,
                created_at=arguments(raw, account)["created_at"] + timedelta(seconds=6),
            ),
        )
    with pytest.raises(FullPublicNumericV2Error):
        evaluate(
            raw.packet,
            account,
            **arguments(
                raw,
                account,
                created_at=account.completed_at - timedelta(microseconds=1),
            ),
        )


class ForeignMapping(Mapping):
    def __init__(self, called):
        self.called = called

    def __len__(self):
        self.called.append("len")
        raise AssertionError("foreign mapping executed")

    def __iter__(self):
        self.called.append("iter")
        raise AssertionError("foreign mapping executed")

    def __getitem__(self, key):
        self.called.append("getitem")
        raise AssertionError("foreign mapping executed")

    def items(self):
        self.called.append("items")
        raise AssertionError("foreign mapping executed")


@pytest.mark.parametrize("wrapped", [False, True])
def test_native_proxy_referent_checked_before_traversing_foreign_mapping(wrapped):
    called = []
    value = ForeignMapping(called)
    if wrapped:
        value = MappingProxyType(value)
    with pytest.raises(FullPublicNumericV2Error):
        primitive_tree(value)
    assert not called


def test_result_foreign_serializer_is_not_run_by_property_or_verifier(
    captured, diagnostic
):
    _, raw, account = captured
    _, verify, _, result = diagnostic
    called = []

    class ForeignModel(BaseModel):
        @model_serializer
        def unsafe(self):
            called.append("serializer")
            raise AssertionError("foreign serializer executed")

    changed = replace(result, receipt_json=ForeignModel())
    with pytest.raises(FullPublicNumericV2Error):
        _ = changed.receipt_sha256
    with pytest.raises(FullPublicNumericV2Error):
        verify(changed, raw.packet, account, **arguments(raw, account))
    assert not called


def test_foreign_account_proxy_and_timezone_are_denied_before_callbacks(
    captured, diagnostic
):
    _, raw, account = captured
    evaluate, _, _, _ = diagnostic
    called = []
    altered = account.model_copy(
        update={"completed_at": MappingProxyType(ForeignMapping(called))}
    )
    with pytest.raises(FullPublicNumericV2Error):
        evaluate(raw.packet, altered, **arguments(raw, account))
    assert not called

    class ForeignZone(tzinfo):
        def utcoffset(self, dt):
            called.append("timezone")
            raise AssertionError("foreign timezone executed")

    foreign = datetime(2026, 9, 12, 10, 10, tzinfo=ForeignZone())
    with pytest.raises(FullPublicNumericV2Error):
        evaluate(raw.packet, account, **arguments(raw, account, created_at=foreign))
    assert not called


@pytest.mark.parametrize(
    "value", [Decimal("1" * 129), Decimal("1e129"), Decimal("1e-129"), Decimal("NaN")]
)
def test_bounded_decimal_preflight_cannot_accept_unbounded_native_operands(value):
    with pytest.raises(FullPublicNumericV2Error):
        primitive_tree(value)


def test_decimal_subclass_is_denied_without_callback():
    called = []

    class ForeignDecimal(Decimal):
        def is_finite(self):
            called.append("decimal")
            raise AssertionError("foreign decimal executed")

    with pytest.raises(FullPublicNumericV2Error):
        primitive_tree(ForeignDecimal("100"))
    assert not called


def test_replay_bytes_are_deterministic_under_hostile_ambient_decimal_context(
    captured, diagnostic
):
    _, raw, account = captured
    evaluate, _, _, result = diagnostic
    with localcontext(Context(prec=3)) as context:
        context.traps[Inexact] = context.traps[Rounded] = True
        changed = evaluate(raw.packet, account, **arguments(raw, account))
    assert changed.receipt_json == result.receipt_json


def test_descriptive_global_dicts_cannot_change_sealed_profile(
    captured, diagnostic, monkeypatch
):
    _, raw, account = captured
    evaluate, _, _, result = diagnostic
    monkeypatch.setitem(profile_module.DATA, "minimum_confirmed_bars", 1)
    monkeypatch.setitem(profile_module.COST, "minimum_net_rr", "0.01")
    monkeypatch.setitem(profile_module.PROTECTION, "min_net_rr", "0.01")
    assert (
        evaluate(raw.packet, account, **arguments(raw, account)).receipt_json
        == result.receipt_json
    )


def test_foreign_proxy_raw_result_denied_before_traversal(captured, diagnostic):
    _, raw, account = captured
    _, verify, _, result = diagnostic
    called = []
    changed = replace(result, receipt_json=MappingProxyType(ForeignMapping(called)))
    with pytest.raises(FullPublicNumericV2Error):
        _ = changed.receipt_sha256
    with pytest.raises(FullPublicNumericV2Error):
        verify(changed, raw.packet, account, **arguments(raw, account))
    assert not called


def test_foreign_metaclass_cannot_execute_equality_or_hash_before_type_rejection():
    called = []

    class ForeignMeta(type):
        def __eq__(self, other):
            called.append("metaclass_equal")
            raise AssertionError("foreign metaclass executed")

        def __hash__(self):
            called.append("metaclass_hash")
            raise AssertionError("foreign metaclass executed")

    class Foreign(metaclass=ForeignMeta):
        pass

    with pytest.raises(FullPublicNumericV2Error):
        primitive_tree(Foreign(), account_models=True)
    assert not called


def test_equal_service_expiry_cancels_original_without_renewal(captured, diagnostic):
    _, raw, account = captured
    evaluate, _, _, _ = diagnostic
    cutoff = arguments(raw, account)["created_at"]
    value = closed(
        evaluate(
            raw.packet, account, **arguments(raw, account, service_deadline=cutoff)
        )
    )
    assert value["precursor"]["code"] == value["code"] == "service_deadline_expired"
    assert value["precursor"]["intent"] is None and value["action"] == "CANCEL"
    assert value["math_checks_passed"] is False
