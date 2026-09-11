"""Synthetic domain consistency tests; passing records are not trade permits."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Context, Decimal, localcontext

import pytest
from pydantic import ValidationError

from app.trade_qualification.models import (
    GATE_ORDER,
    EntryQualificationResult,
    EntryTrigger,
    EntryZone,
    GateAssessment,
    MarketRegime,
    QualificationGate,
    QualificationState,
)

NOW = datetime(2024, 1, 1, tzinfo=UTC)
D = Decimal
REPORT = "synthetic-qualification-001"


def zone(**changes):
    values = dict(
        report_id=REPORT,
        zone_low=D("99"),
        zone_high=D("101"),
        zone_type="synthetic_retest",
        zone_source="synthetic closed candles",
        created_at=NOW - timedelta(minutes=2),
        expires_at=NOW + timedelta(minutes=1),
        max_allowed_drift_bps=D("20"),
        invalidation_price=D("95"),
    )
    return EntryZone(**(values | changes))


def trigger(**changes):
    values = dict(
        report_id=REPORT,
        trigger_type="synthetic_reclaim",
        trigger_time=NOW - timedelta(seconds=30),
        trigger_price=D("100"),
        expires_at=NOW + timedelta(seconds=30),
    )
    return EntryTrigger(**(values | changes))


def gate(gate_id, passed=True, code=None, **changes):
    values = dict(
        report_id=REPORT,
        gate=gate_id,
        passed=passed,
        code=code or ("passed" if passed else "synthetic_failure"),
        reason="Synthetic gate record, not real market evidence",
        measured_values={"synthetic": True},
    )
    return GateAssessment(**(values | changes))


def inputs():
    return dict(
        report_id=REPORT,
        symbol="BTC-USDT-SWAP",
        strategy="trend_pullback",
        direction="long",
        evaluated_at=NOW,
        raw_score=95,
        effective_score=90,
        market_regime=MarketRegime.TREND,
        htf_bias="long",
        setup_state="valid",
        entry_timing_state="valid",
        trigger=trigger(),
        entry_zone=zone(),
        candidate_entry=D("100"),
        reference_price=D("100.1"),
        stop_loss=D("95"),
        take_profit=D("110"),
        gross_rr=D("2"),
        net_rr=D("1.8"),
        gates=tuple(gate(item) for item in GATE_ORDER),
    )


def test_empty_chain_is_not_qualified_even_with_high_score_and_rr():
    result = EntryQualificationResult(**(inputs() | {"gates": ()}))
    assert result.qualified is False
    assert result.state == QualificationState.SIGNAL_DETECTED
    assert result.evidence_complete is False
    assert result.execution_recheck_passed is False
    assert result.risk_permission is False


def test_complete_consistent_record_and_json_roundtrip():
    result = EntryQualificationResult(**inputs())
    assert result.qualified is True
    assert result.state == QualificationState.ORDER_ELIGIBLE
    assert result.entry_drift_bps == D("10")
    assert result.trigger_timestamp == trigger().trigger_time
    assert result.trigger_state == "valid"
    assert result.failed_gates == result.fail_codes == ()
    assert result.risk_permission is True
    assert result.evidence_complete is True
    assert result.execution_recheck_passed is True
    serialized = result.model_dump_json(round_trip=True)
    assert EntryQualificationResult.model_validate_json(serialized) == result
    assert result.model_dump(mode="json")["qualified"] is True


@pytest.mark.parametrize("failed_index", range(13))
def test_every_gate_failure_blocks_high_score(failed_index):
    gates = (
        *(gate(item) for item in GATE_ORDER[:failed_index]),
        gate(GATE_ORDER[failed_index], False, "blocked_synthetic_gate"),
    )
    result = EntryQualificationResult(**(inputs() | {"gates": gates}))
    assert not result.qualified
    assert result.state != QualificationState.ORDER_ELIGIBLE
    assert result.failed_gates == (GATE_ORDER[failed_index],)
    assert result.fail_codes == ("blocked_synthetic_gate",)


def test_evidence_complete_still_needs_recheck_and_stop_alone_is_not_protection():
    result = EntryQualificationResult(**(inputs() | {"gates": inputs()["gates"][:12]}))
    assert result.state == QualificationState.EVIDENCE_COMPLETE
    assert not result.qualified
    assert not result.execution_recheck_passed
    result = EntryQualificationResult(**(inputs() | {"gates": inputs()["gates"][:8]}))
    assert result.state == QualificationState.ENTRY_LOCATION_VALID


@pytest.mark.parametrize(
    "gates",
    [
        (gate(QualificationGate.EVIDENCE),),
        (gate(QualificationGate.DATA), gate(QualificationGate.DATA)),
        (gate(QualificationGate.DATA, False), gate(QualificationGate.REGIME)),
        (gate(QualificationGate.DATA, report_id="other-report"),),
    ],
)
def test_skipped_duplicate_reordered_or_cross_candidate_gates_rejected(gates):
    with pytest.raises(ValidationError):
        EntryQualificationResult(**(inputs() | {"gates": gates}))


@pytest.mark.parametrize(
    "changes",
    [
        {"qualified": True},
        {"state": "ORDER_ELIGIBLE"},
        {"evidence_complete": True},
        {"execution_recheck_passed": True},
        {"risk_permission": True},
        {"fail_codes": ()},
        {"entry_drift_bps": D("0")},
        {"trigger_timestamp": NOW},
        {"effective_score": 96},
        {"raw_score": True},
        {"raw_score": "95"},
        {"market_regime": MarketRegime.UNKNOWN},
        {"market_regime": MarketRegime.RISK_OFF},
        {"htf_bias": None},
        {"setup_state": "waiting"},
        {"entry_timing_state": "wait"},
        {"trigger": None},
        {"trigger": trigger(report_id="other-report")},
        {"trigger": trigger(expires_at=NOW)},
        {"trigger": trigger(trigger_time=NOW + timedelta(seconds=1))},
        {"trigger": trigger(invalidation_reason="synthetic_invalidation")},
        {"entry_zone": None},
        {"entry_zone": zone(report_id="other-report")},
        {"entry_zone": zone(expires_at=NOW)},
        {"reference_price": D("101.1")},
        {"reference_price": D("100.3")},
        {"candidate_entry": D("102")},
        {"candidate_entry": None},
        {"candidate_entry": D("NaN")},
        {"reference_price": D("Infinity")},
        {"stop_loss": D("101")},
        {"take_profit": D("99")},
        {"stop_loss": None},
        {"take_profit": None},
        {"gross_rr": None},
        {"net_rr": None},
        {"net_rr": D("2.1")},
        {"take_profit": D("100.01")},
        {"evaluated_at": NOW.replace(tzinfo=None)},
        {"report_id": "../escape"},
    ],
)
def test_inconsistent_or_self_asserted_qualification_is_rejected(changes):
    with pytest.raises(ValidationError):
        EntryQualificationResult(**(inputs() | changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"zone_low": D("102")},
        {"zone_low": D("0")},
        {"invalidation_price": D("100")},
        {"expires_at": NOW - timedelta(minutes=3)},
        {"created_at": NOW.replace(tzinfo=None)},
        {"max_allowed_drift_bps": D("-1")},
        {"max_allowed_drift_bps": D("NaN")},
    ],
)
def test_entry_zone_contract(changes):
    with pytest.raises(ValidationError):
        zone(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"expires_at": NOW - timedelta(seconds=31)},
        {"trigger_time": NOW.replace(tzinfo=None)},
        {"trigger_price": 100.0},
        {"trigger_price": D("0")},
    ],
)
def test_trigger_contract(changes):
    with pytest.raises(ValidationError):
        trigger(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"passed": 1},
        {"passed": "true"},
        {"code": "invalid_code_for_pass"},
        {"passed": False, "code": "passed"},
        {"reason": " "},
        {"measured_values": {}},
        {"measured_values": {"bad": D("NaN")}},
        {"measured_values": {"bad": {"nested": True}}},
        {"measured_values": {"bad": "x" * 513}},
        {"measured_values": {"bad": 10**41}},
        {"measured_values": {str(n): True for n in range(33)}},
    ],
    ids=[
        "integer-bool",
        "string-bool",
        "pass-code",
        "failure-code",
        "no-reason",
        "no-measurement",
        "nan",
        "nested",
        "long-text",
        "large-int",
        "many-keys",
    ],
)
def test_gate_contract(changes):
    with pytest.raises(ValidationError):
        gate(QualificationGate.DATA, **changes)


def test_nested_invalid_model_copy_is_revalidated():
    invalid_trigger = trigger().model_copy(update={"trigger_price": D("-1")})
    with pytest.raises(ValidationError):
        EntryQualificationResult(**(inputs() | {"trigger": invalid_trigger}))


def test_short_geometry_is_direction_specific():
    result = EntryQualificationResult(
        **(
            inputs()
            | dict(
                direction="short",
                htf_bias="short",
                stop_loss=D("105"),
                take_profit=D("90"),
                entry_zone=zone(invalidation_price=D("105")),
            )
        )
    )
    assert result.qualified
    with pytest.raises(ValidationError, match="entry zone"):
        EntryQualificationResult(
            **(
                inputs()
                | dict(
                    direction="short",
                    stop_loss=D("105"),
                    take_profit=D("90"),
                )
            )
        )


def test_missing_early_context_remains_unknown_not_invented():
    result = EntryQualificationResult(
        report_id=REPORT,
        symbol="BTC-USDT-SWAP",
        strategy="trend_pullback",
        direction="long",
        evaluated_at=NOW,
        raw_score=95,
        effective_score=95,
    )
    assert result.market_regime == MarketRegime.UNKNOWN
    assert result.trigger_state == "missing"
    assert result.trigger_timestamp is None
    assert result.entry_drift_bps is None
    assert not result.qualified


def test_gate_measurements_are_immutable_and_detached_from_input():
    raw = {"synthetic": True}
    assessment = gate(QualificationGate.DATA, measured_values=raw)
    raw.clear()
    assert assessment.measured_values["synthetic"] is True
    with pytest.raises(TypeError):
        assessment.measured_values["bad"] = D("NaN")
    with pytest.raises(AttributeError):
        assessment.measured_values.clear()
    copied = assessment.model_dump()
    copied["measured_values"].clear()
    assert assessment.measured_values["synthetic"] is True
    assert (
        GateAssessment.model_validate_json(assessment.model_dump_json()) == assessment
    )


def test_mixed_measurement_types_survive_json_roundtrip():
    values = {
        "numeric_text": "1.23",
        "exponent_text": "1e3",
        "text": "observed",
        "decimal": D("1.2300"),
        "whole_decimal": D("1"),
        "integer": 1,
        "bool": True,
        "unknown": None,
    }
    assessment = gate(QualificationGate.DATA, measured_values=values)
    reloaded = GateAssessment.model_validate_json(assessment.model_dump_json())
    assert reloaded == assessment
    assert {key: type(value) for key, value in reloaded.measured_values.items()} == {
        key: type(value) for key, value in values.items()
    }
    result = EntryQualificationResult(**(inputs() | {"gates": (assessment,)}))
    assert (
        EntryQualificationResult.model_validate_json(
            result.model_dump_json(round_trip=True)
        )
        == result
    )


@pytest.mark.parametrize("precision", [9, 28, 50])
@pytest.mark.parametrize("rounding", [ROUND_UP, ROUND_DOWN])
def test_drift_and_rr_are_independent_of_ambient_decimal_context(precision, rounding):
    with localcontext(Context(prec=precision, rounding=rounding)):
        result = EntryQualificationResult(**inputs())
        assert result.qualified and result.entry_drift_bps == D("10")
        inconsistent = inputs() | {"take_profit": D("100.01")}
        with pytest.raises(ValidationError, match="gross RR"):
            EntryQualificationResult(**inconsistent)
        borderline = inputs() | {
            "candidate_entry": D("100.12345678"),
            "reference_price": D("100.12355682"),
            "entry_zone": zone(max_allowed_drift_bps=D("0.00999166461")),
            "gates": inputs()["gates"][:7],
        }
        with pytest.raises(ValidationError, match="entry zone"):
            EntryQualificationResult(**borderline)
