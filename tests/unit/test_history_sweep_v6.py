"""Source-replayed synthetic V6; no runtime source ownership or execution."""

import json
from dataclasses import replace
from datetime import datetime, tzinfo
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.strategies.structural_protection import (
    _restrict_sweep_stops,
    _source_anchors,
    select_sweep_structural_protection,
)
from app.trade_qualification import history_engine as engine
from app.trade_qualification import history_prefix as prefix
from app.trade_qualification import sweep_contract as contract
from app.trade_qualification.models import MarketRegime, QualificationGate
from app.trade_qualification.regime_admission import _guard as original_guard
from tests.unit.qualification_engine_fixtures import PROTECTION_PARAMETERS
from tests.unit.qualification_prefix_fixtures import restore_source
from tests.unit.qualification_sweep_v6_fixtures import (
    prefix_inputs,
    sweep_v6_source,
    v6_inputs,
)

D = Decimal


@pytest.fixture(scope="module", params=("long", "short"))
def inside(request):
    return sweep_v6_source(request.param, "inside")


def admission_inputs(source):
    return {
        "report_id": source.report_id,
        "direction": source.direction,
        "observed_at": source.evaluated_at,
        "analysis_version": source.policy.analysis_version,
    }


def test_admission_replays_raw_source_and_never_grants_authority(inside):
    result = contract.derive_sweep_admission(inside.market, **admission_inputs(inside))
    assert result.admitted, result.code
    assert result.policy_sha256 == contract.SWEEP_PERMISSION_SHA256
    assert (
        contract.verify_sweep_admission(
            result, inside.market, **admission_inputs(inside)
        )
        == result
    )
    assert not result.execution_authority and not result.source_authenticity_verified
    changed = inside.market.model_copy(deep=True)
    row = changed.candles["4H"][0]
    changed.candles["4H"][0] = row.model_copy(
        update={"volume_contracts": row.volume_contracts + 1}
    )
    with pytest.raises(ValueError, match="source_replay_mismatch"):
        contract.verify_sweep_admission(result, changed, **admission_inputs(inside))


def test_canonical_self_signed_receipt_is_not_source_admission(inside):
    result = contract.derive_sweep_admission(inside.market, **admission_inputs(inside))
    value = json.loads(result.receipt_json)
    value["range_context"]["legacy_classifier"] = "caller_relabelled"
    value["range_context_sha256"] = contract.journal.digest(
        contract.journal.canonical(value["range_context"])
    )
    raw = result.model_dump(mode="python", round_trip=True)
    raw["receipt_json"] = contract.journal.canonical(value).decode()
    forged = contract.SweepHistoryAdmissionRecord.model_validate(raw, strict=True)
    assert forged.evaluation_sha256 != result.evaluation_sha256
    with pytest.raises(ValueError, match="source_replay_mismatch"):
        contract.verify_sweep_admission(
            forged, inside.market, **admission_inputs(inside)
        )


@pytest.mark.parametrize(
    "name", ("execution_authority", "source_authenticity_verified")
)
def test_admission_authority_flags_remain_exact_false(inside, name):
    record = contract.derive_sweep_admission(inside.market, **admission_inputs(inside))
    raw = record.model_dump(mode="python", round_trip=True)
    raw[name] = True
    with pytest.raises(ValidationError):
        contract.SweepHistoryAdmissionRecord.model_validate(raw, strict=True)


def test_hidden_record_field_cannot_disappear_in_serializer(inside):
    record = contract.derive_sweep_admission(inside.market, **admission_inputs(inside))
    record.__dict__["caller_passed"] = True
    with pytest.raises(ValueError, match="hidden_fields"):
        contract.verify_sweep_admission(
            record, inside.market, **admission_inputs(inside)
        )


def test_in_zone_raw_scenario_runs_all_seven_shared_gates(inside):
    inputs = prefix_inputs(inside)
    run = prefix.evaluate_history_qualification_prefix_v6(inside.market, **inputs)
    assert run.prefix_complete, run.result.fail_codes
    assert run.result.market_regime == "History Verified Sweep"
    assert run.result.gates[1].measured_values["regime"] == MarketRegime.UNKNOWN.value
    assert run.result.gates[1].measured_values["actual_router_regime"] == (
        MarketRegime.UNKNOWN.value
    )
    assert run.result.htf_bias == "neutral"
    assert run.detection == run.history_admission.detection
    assert (
        run.result.entry_zone.zone_low
        <= run.result.candidate_entry
        <= (run.result.entry_zone.zone_high)
    )
    assert run.result.gates[1].measured_values["route_diagnostics"] == (
        "range_transition_history_missing"
    )
    assert not run.execution_authority and not run.event_ledger_verified
    assert (
        prefix.verify_history_qualification_prefix_v6(run, inside.market, **inputs)
        == run
    )


@pytest.mark.parametrize("direction", ("long", "short"))
def test_original_outside_quote_remains_cancel_without_new_zone(direction):
    source = sweep_v6_source(direction, "original_outside")
    run = prefix.evaluate_history_qualification_prefix_v6(
        source.market, **prefix_inputs(source)
    )
    assert len(run.result.gates) == 7
    assert all(gate.passed for gate in run.result.gates[:6]), run.result.fail_codes
    assert not run.result.gates[-1].passed
    assert run.result.gates[-1].gate == QualificationGate.LOCATION
    assert not run.prefix_complete
    assert (run.result.entry_zone.zone_low, run.result.entry_zone.zone_high) == (
        (D(99), D("101.5")) if direction == "long" else (D("98.5"), D(101))
    )
    assert run.result.candidate_entry == source.market.ticker.last


def test_old_history_version_keeps_sweep_wait(inside):
    inputs = prefix_inputs(inside)
    raw = inputs["policy"].model_dump(mode="python", round_trip=True)
    for name in (
        "history_result_classification",
        "sweep_permission_policy_sha256",
        "sweep_selection_policy",
        "sweep_selection_policy_sha256",
    ):
        raw.pop(name)
    raw.update(
        contract_version="ctcc-history-qualification-prefix-v1",
        history_policy_id="ctcc-history-regime-admission-v1",
    )
    inputs["policy"] = prefix.HistoryQualificationPrefixPolicy(**raw)
    run = prefix.evaluate_history_qualification_prefix(inside.market, **inputs)
    assert run.result.gates[-1].code == "sweep_htf_policy_unspecified"
    assert not run.prefix_complete


def test_sweep_classification_requires_admission_digest_and_actual_unknown_route(
    inside,
):
    run = prefix.evaluate_history_qualification_prefix_v6(
        inside.market, **prefix_inputs(inside)
    )
    assert run.prefix_complete, run.result.fail_codes
    raw = run.result.model_dump(mode="python", round_trip=True)
    for changed in (
        {"market_regime": MarketRegime.UNKNOWN},
        {"history_admission_sha256": "0" * 64},
    ):
        with pytest.raises(ValidationError):
            prefix.HistoryEntryQualificationResultV6.model_validate(
                {**raw, **changed}, strict=True
            )
    measured = dict(run.result.gates[1].measured_values)
    measured["actual_router_regime"] = MarketRegime.TREND.value
    gate = run.result.gates[1].model_copy(update={"measured_values": measured})
    with pytest.raises(ValidationError):
        prefix.HistoryEntryQualificationResultV6.model_validate(
            {**raw, "gates": (run.result.gates[0], gate, *run.result.gates[2:])},
            strict=True,
        )


@pytest.mark.parametrize(
    "pin", ("sweep_permission_policy_sha256", "sweep_selection_policy_sha256")
)
def test_policy_pins_cannot_be_drifted_by_model_copy(inside, pin):
    inputs = prefix_inputs(inside)
    inputs["policy"] = inputs["policy"].model_copy(update={pin: "0" * 64})
    with pytest.raises(ValueError):
        prefix.evaluate_history_qualification_prefix_v6(inside.market, **inputs)


def test_v6_cannot_run_another_strategy_or_promote_old_version(inside):
    inputs = prefix_inputs(inside)
    inputs["intent"] = inputs["intent"].model_copy(
        update={"strategy": "range_reversal"}
    )
    with pytest.raises(ValueError, match="sweep_only"):
        prefix.evaluate_history_qualification_prefix_v6(inside.market, **inputs)
    with pytest.raises(ValidationError):
        prefix.HistoryQualificationPrefixPolicyV6.model_validate(
            {
                name: value
                for name, value in prefix_inputs(inside)["policy"].__dict__.items()
                if name != "contract_version"
            },
            strict=True,
        )


def test_raw_selector_keeps_every_anchor_and_pool_in_audit(inside):
    run = prefix.evaluate_history_qualification_prefix_v6(
        inside.market, **prefix_inputs(inside)
    )
    assert run.prefix_complete, run.result.fail_codes
    market, analysis = restore_source(run.data_result)
    selected = select_sweep_structural_protection(
        run.detection,
        market,
        analysis,
        observed_at=inside.evaluated_at,
        entry=run.result.candidate_entry,
        tick_size=inside.tick_size,
        **PROTECTION_PARAMETERS,
    )
    raw_stops, raw_targets = _source_anchors(market.candles, run.detection)
    assert [item.anchor.anchor_id for item in selected.stops] == [
        item.anchor_id for item in raw_stops
    ]
    assert [item.anchor.anchor_id for item in selected.targets] == [
        item.anchor_id for item in raw_targets
    ]
    assert selected.original_extreme_anchor_id is not None
    assert all(
        "sweep_requires_original_extreme" in item.rejection_codes
        for item in selected.stops
        if item.anchor.anchor_id != selected.original_extreme_anchor_id
    )
    if selected.selected is not None:
        assert (
            selected.selected.stop.anchor.anchor_id
            == selected.original_extreme_anchor_id
        )
    audit = engine._sweep_audit(selected.to_audit_json())
    assert audit["sweep_permission_sha256"] == run.history_admission.evaluation_sha256
    assert audit["selection_policy_sha256"] == contract.SWEEP_SELECTION_SHA256
    assert not audit["execution_authority"]


def test_missing_or_duplicate_extreme_denies_without_dropping_other_pools(inside):
    run = prefix.evaluate_history_qualification_prefix_v6(
        inside.market, **prefix_inputs(inside)
    )
    market, _ = restore_source(run.data_result)
    stops, _ = _source_anchors(market.candles, run.detection)
    original = next(
        item for item in stops if item.source == "sweep_extreme_invalidation"
    )
    for hostile in (
        tuple(item for item in stops if item is not original),
        (*stops, replace(original)),
    ):
        restricted, chosen = _restrict_sweep_stops(hostile, run.detection)
        assert chosen is None and len(restricted) == len(hostile)
        assert all(
            "sweep_requires_original_extreme" in item.rejection_codes
            for item in restricted
        )
        assert [item.anchor_id for item in restricted] == [
            item.anchor_id for item in hostile
        ]


def test_failed_rr_cannot_skip_nearer_targets_or_select_another_stop(inside):
    args = v6_inputs(inside)
    run = engine.evaluate_history_pre_evidence_v6(inside.market, **args)
    assert run.prefix.prefix_complete, run.result.fail_codes
    assert len(run.result.gates) == 8 and not run.result.gates[-1].passed
    assert run.protection_audit_json is not None
    audit = engine._sweep_audit(run.protection_audit_json)
    assert audit["selected"] is None and not audit["protection_valid"]
    original = audit["original_extreme_anchor_id"]
    candidates = [
        item
        for item in audit["alternatives"]
        if item["stop"]["anchor"]["anchor_id"] == original
    ]
    assert candidates
    assert all(item["rejection_codes"] for item in candidates)
    assert any("net_rr_below_minimum" in item["rejection_codes"] for item in candidates)
    assert not run.execution_authority and not run.atomic_risk_reserved
    assert engine.verify_history_pre_evidence_v6(run, inside.market, **args) == run


@pytest.mark.parametrize("direction", ("long", "short"))
def test_boundary_source_can_only_select_original_extreme_with_unchanged_buffers(
    direction,
):
    source = sweep_v6_source(direction, "boundary")
    args = v6_inputs(source)
    run = engine.evaluate_history_pre_evidence_v6(source.market, **args)
    assert run.prefix.prefix_complete, run.result.fail_codes
    assert len(run.result.gates) >= 9 and all(g.passed for g in run.result.gates[:9]), (
        run.result.fail_codes,
        run.protection_audit_json,
    )
    audit = engine._sweep_audit(run.protection_audit_json)
    stop = audit["selected"]["stop"]
    assert stop["anchor"]["source"] == "sweep_extreme_invalidation"
    assert stop["anchor"]["anchor_id"] == audit["original_extreme_anchor_id"]
    extreme = D(stop["anchor"]["anchor_price"])
    final = D(stop["final_stop"])
    assert final < extreme if direction == "long" else final > extreme
    assert D(stop["buffer"]) > 0
    assert D(audit["selected"]["net_rr"]) >= PROTECTION_PARAMETERS["min_net_rr"]
    assert run.result.candidate_entry == source.market.ticker.last
    assert not run.execution_authority


def test_declared_v6_replays_failed_prefix_without_evidence_or_authority_upgrade(
    inside,
):
    from app.trade_evidence.gates import verify_pre_evidence_versioned

    args = v6_inputs(inside)
    run = engine.evaluate_history_pre_evidence_v6(inside.market, **args)
    verified = verify_pre_evidence_versioned(run, inside.market, **args)
    assert verified == run
    assert not verified.pre_evidence_complete
    assert not verified.execution_authority
    assert len(verified.result.gates) == 8 and not verified.result.gates[-1].passed


def guard_outcome(guard, value):
    try:
        guard(value)
    except ValueError as exc:
        return "DENIED", str(exc)
    return "ACCEPTED", None


@pytest.mark.parametrize(
    "value",
    (
        None,
        True,
        10**40,
        10**40 + 1,
        D(1),
        D("NaN"),
        D("1e41"),
        "pin",
        " pin",
        tuple(range(32)),
        tuple(range(33)),
        [],
        {},
        ("tuple", True, None, D(1)),
    ),
    ids=(
        "none",
        "bool",
        "integer-bound",
        "integer-over-bound",
        "decimal",
        "decimal-NaN",
        "decimal-exponent",
        "text",
        "noncanonical-text",
        "tuple-bound",
        "tuple-over-bound",
        "list",
        "mapping",
        "mixed-tuple",
    ),
)
def test_leaf_guard_preserves_original_non_source_scalar_safety(value):
    assert guard_outcome(contract._guard, value) == guard_outcome(original_guard, value)


@pytest.mark.parametrize(
    "mutation",
    (
        "valid",
        "detection_hidden",
        "trigger_hidden",
        "wrong_trigger",
        "foreign_timezone",
    ),
)
def test_leaf_event_guard_preserves_hidden_field_and_timezone_checks(inside, mutation):
    detection = contract.derive_sweep_admission(
        inside.market, **admission_inputs(inside)
    ).detection.model_copy(deep=True)
    callbacks = []

    class ForeignTimezone(tzinfo):
        def utcoffset(self, dt):
            callbacks.append("utcoffset")
            raise AssertionError("foreign timezone must not be called")

    if mutation == "detection_hidden":
        detection.__dict__["caller_passed"] = True
    elif mutation == "trigger_hidden":
        detection.trigger.__dict__["caller_passed"] = True
    elif mutation == "wrong_trigger":
        detection.__dict__["trigger"] = {"passed": True}
    elif mutation == "foreign_timezone":
        detection.trigger.__dict__["trigger_time"] = datetime(
            2026, 9, 28, tzinfo=ForeignTimezone()
        )
    expected = guard_outcome(original_guard, detection)
    assert guard_outcome(contract._guard, detection) == expected
    assert expected[0] == ("ACCEPTED" if mutation == "valid" else "DENIED")
    assert callbacks == []
