"""Verified computed output readback; unknown fields and authority still deny."""

import json

import pytest

from app.trade_qualification import recheck
from tests.unit import qualification_sweep_v6_stage_c_fixtures as fixtures

stage_c_case = fixtures.stage_c_case
RESULT_COMPUTED_FIELDS = (
    "qualified",
    "state",
    "trigger_state",
    "trigger_timestamp",
    "entry_drift_bps",
    "risk_permission",
    "evidence_complete",
    "execution_recheck_passed",
    "failed_gates",
    "fail_codes",
)
NESTED_COMPUTED_PATHS = (
    "origin.evidence.pre_evidence.prefix.result.qualified",
    "origin.evidence.pre_evidence.result.risk_permission",
    "origin.evidence.snapshot.qualification.evidence_complete",
    "current_conditions.result.qualified",
    "origin.evidence.pre_evidence.prefix.prefix_complete",
    "origin.evidence.pre_evidence.pre_evidence_complete",
    "origin.evidence.pre_evidence.prefix.timing_policy.trigger_ttl_seconds",
    "origin.evidence.pre_evidence.prefix.timing.timing_valid",
    "timing.timing_valid",
)
UNKNOWN_PATHS = (
    "",
    "origin",
    "origin.evidence",
    "origin.evidence.pre_evidence",
    "origin.evidence.pre_evidence.prefix",
    "origin.evidence.pre_evidence.prefix.result",
    "origin.evidence.pre_evidence.prefix.timing_policy",
    "origin.evidence.pre_evidence.prefix.timing",
    "origin.evidence.pre_evidence.result",
    "origin.evidence.result",
    "origin.evidence.snapshot",
    "origin.evidence.snapshot.qualification",
    "current_conditions",
    "current_conditions.result",
    "timing",
)


@pytest.fixture(scope="module")
def recorded(stage_c_case):
    case = stage_c_case
    market, inputs = fixtures.sweep_current(case)
    actual = recheck.evaluate_recorded_recheck(case.source.market, market, **inputs)
    assert actual.computational_checks_passed, actual.code
    return actual


def node(raw, path):
    selected = raw
    if path:
        for key in path.split("."):
            selected = selected[key]
    return selected


def different(value):
    if type(value) is bool:
        return not value
    if type(value) is int:
        return value + 1
    if type(value) is list:
        return ["caller-changed-output"]
    return "caller-changed-output"


def test_normal_and_round_trip_v6_json_reconstruct_equal_without_authority(recorded):
    for encoded in (
        recorded.model_dump_json(),
        recorded.model_dump_json(round_trip=True),
    ):
        checked = recheck.RecordedRecheckAssessment.model_validate_json(
            encoded, strict=True
        )
        assert checked == recorded
        assert checked.evaluation_sha256 == recorded.evaluation_sha256
        assert not checked.execution_authority and not checked.runtime_admissible


@pytest.mark.parametrize("field", RESULT_COMPUTED_FIELDS)
def test_each_computed_result_kind_must_equal_reconstruction(recorded, field):
    raw = json.loads(recorded.model_dump_json())
    target = raw["origin"]["evidence"]["result"]
    target[field] = different(target[field])
    with pytest.raises(ValueError, match="v6_json_computed_output_mismatch"):
        recheck.RecordedRecheckAssessment.model_validate_json(
            json.dumps(raw), strict=True
        )


@pytest.mark.parametrize("path", NESTED_COMPUTED_PATHS)
def test_nested_output_field_cannot_be_silently_dropped(recorded, path):
    raw = json.loads(recorded.model_dump_json())
    parent, field = path.rsplit(".", 1)
    target = node(raw, parent)
    target[field] = different(target[field])
    with pytest.raises(ValueError, match="v6_json_computed_output_mismatch"):
        recheck.RecordedRecheckAssessment.model_validate_json(
            json.dumps(raw), strict=True
        )


@pytest.mark.parametrize("path", UNKNOWN_PATHS)
def test_unknown_field_remains_extra_forbidden_at_each_nested_boundary(recorded, path):
    raw = json.loads(recorded.model_dump_json())
    node(raw, path)["caller_passed"] = True
    with pytest.raises(ValueError):
        recheck.RecordedRecheckAssessment.model_validate_json(
            json.dumps(raw), strict=True
        )


@pytest.mark.parametrize("value", (0, 1, "false", "true", None))
def test_computed_boolean_cannot_use_numeric_or_string_equivalence(recorded, value):
    raw = json.loads(recorded.model_dump_json())
    raw["origin"]["evidence"]["result"]["qualified"] = value
    with pytest.raises(ValueError, match="v6_json_computed_output_mismatch"):
        recheck.RecordedRecheckAssessment.model_validate_json(
            json.dumps(raw), strict=True
        )


def test_python_output_fields_do_not_receive_json_coercion(recorded):
    with pytest.raises(ValueError):
        recheck.RecordedRecheckAssessment.model_validate(
            recorded.model_dump(mode="python"), strict=True
        )


@pytest.mark.parametrize(
    "path",
    (
        "origin.evidence.pre_evidence.prefix",
        "origin.evidence.pre_evidence",
        "origin.evidence.result",
        "current_conditions",
    ),
)
def test_mixed_supported_or_unknown_version_cannot_select_v6_decoder(recorded, path):
    for marker in ("ctcc-history-qualification-prefix-v5", "ctcc-unsupported-v999"):
        raw = json.loads(recorded.model_dump_json())
        node(raw, path)["contract_version"] = marker
        with pytest.raises(ValueError):
            recheck.RecordedRecheckAssessment.model_validate_json(
                json.dumps(raw), strict=True
            )


@pytest.mark.parametrize(
    "path", ("", "origin", "origin.evidence", "current_conditions", "fixed_protection")
)
def test_json_output_reconstruction_never_accepts_caller_execution_authority(
    recorded, path
):
    raw = json.loads(recorded.model_dump_json())
    node(raw, path)["execution_authority"] = True
    with pytest.raises(ValueError):
        recheck.RecordedRecheckAssessment.model_validate_json(
            json.dumps(raw), strict=True
        )
