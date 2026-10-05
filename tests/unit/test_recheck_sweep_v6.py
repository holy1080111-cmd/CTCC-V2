"""Fixed-origin V6 replay from declared synthetic post-G12 source bytes."""

import json

import pytest

from app.trade_qualification import recheck
from app.trade_qualification.current_conditions import HistoryCurrentConditionsResultV6
from app.trade_qualification.fixed_protection import FixedProtectionResultV6
from tests.unit import qualification_sweep_v6_stage_c_fixtures as fixtures

stage_c_case = fixtures.stage_c_case
sweep_current = fixtures.sweep_current


def test_current_v6_replays_all_checks_with_exact_original_event_and_bracket(
    stage_c_case,
):
    case = stage_c_case
    current_market, inputs = sweep_current(case)
    actual = recheck.evaluate_recorded_recheck(
        case.source.market, current_market, **inputs
    )
    assert actual.computational_checks_passed, (actual.code, actual.checks)
    assert type(actual.current_conditions) is HistoryCurrentConditionsResultV6
    assert type(actual.fixed_protection) is FixedProtectionResultV6
    original = case.run
    assert actual.origin == case.origin
    assert (
        actual.current_conditions.original_event_key == case.origin.original_event_key
    )
    assert (
        actual.current_conditions.history_admission_sha256
        == original.prefix.history_admission.evaluation_sha256
    )
    assert actual.current_conditions.result.market_regime == "History Verified Sweep"
    assert (
        actual.current_conditions.gates[1].measured_values["actual_router_regime"]
        == "Unknown"
    )
    assert actual.current_conditions.result.htf_bias == "neutral"
    assert (
        actual.fixed_protection.entry,
        actual.fixed_protection.stop_loss,
        actual.fixed_protection.take_profit,
    ) == (
        original.result.candidate_entry,
        original.result.stop_loss,
        original.result.take_profit,
    )
    audit = json.loads(original.protection_audit_json)
    assert (
        actual.fixed_protection.original_extreme_anchor_id
        == audit["original_extreme_anchor_id"]
    )
    assert (
        actual.fixed_protection.sweep_permission_sha256
        == original.prefix.history_admission.evaluation_sha256
    )
    assert (
        actual.timing.latest_valid_entry_time
        == original.prefix.timing.latest_valid_entry_time
    )
    assert (
        recheck.verify_recorded_recheck(
            actual, case.source.market, current_market, **inputs
        )
        == actual
    )
    assert (
        recheck.RecordedRecheckAssessment.model_validate_json(
            actual.model_dump_json(), strict=True
        )
        == actual
    )
    assert not actual.execution_authority and not actual.execution_recheck_performed


@pytest.mark.parametrize(
    "scenario",
    (
        "outside_zone",
        "adverse_quote",
        "changed_original_row",
        "missing_original_row",
        "htf_denied",
        "extreme_breach",
        "cached",
        "barrier_equal",
        "expired",
    ),
)
def test_changed_or_incomplete_current_source_never_repairs_original_sweep(
    stage_c_case, scenario
):
    case = stage_c_case
    market, inputs = sweep_current(case, scenario)
    denied = recheck.evaluate_recorded_recheck(case.source.market, market, **inputs)
    assert not denied.computational_checks_passed, (scenario, denied.checks)
    assert denied.origin == case.origin
    assert denied.origin.candidate == case.origin.candidate
    assert denied.origin.deadline == case.origin.deadline
    assert not denied.execution_authority
    assert (
        recheck.verify_recorded_recheck(denied, case.source.market, market, **inputs)
        == denied
    )
    assert (
        recheck.RecordedRecheckAssessment.model_validate_json(
            denied.model_dump_json(), strict=True
        )
        == denied
    )


@pytest.mark.parametrize(
    "field", ("origin_sha256", "original_event_key", "history_admission_sha256")
)
def test_current_record_cannot_switch_original_lineage(stage_c_case, field):
    case = stage_c_case
    market, inputs = sweep_current(case)
    actual = recheck.evaluate_recorded_recheck(case.source.market, market, **inputs)
    assert actual.computational_checks_passed, actual.code
    raw = actual.model_dump(mode="python", round_trip=True)
    raw["current_conditions"][field] = "f" * 64
    with pytest.raises(ValueError):
        recheck.RecordedRecheckAssessment.model_validate(raw, strict=True)


@pytest.mark.parametrize(
    "field",
    (
        "sweep_permission_sha256",
        "original_extreme_anchor_id",
        "sweep_selection_policy_sha256",
    ),
)
def test_fixed_record_cannot_switch_extreme_permission_or_selection(
    stage_c_case, field
):
    case = stage_c_case
    market, inputs = sweep_current(case)
    actual = recheck.evaluate_recorded_recheck(case.source.market, market, **inputs)
    assert actual.computational_checks_passed, actual.code
    raw = actual.model_dump(mode="python", round_trip=True)
    raw["fixed_protection"][field] = "f" * 64
    with pytest.raises(ValueError):
        recheck.RecordedRecheckAssessment.model_validate(raw, strict=True)


def test_current_failed_receipt_cannot_be_relabelled_as_passed(stage_c_case):
    case = stage_c_case
    market, inputs = sweep_current(case, "cached")
    denied = recheck.evaluate_recorded_recheck(case.source.market, market, **inputs)
    assert not denied.computational_checks_passed
    raw = denied.model_dump(mode="python", round_trip=True)
    raw["checks"][-1].update(passed=True, code="passed")
    with pytest.raises(ValueError):
        rewritten = recheck.RecordedRecheckAssessment.model_validate(raw, strict=True)
        recheck.verify_recorded_recheck(rewritten, case.source.market, market, **inputs)
