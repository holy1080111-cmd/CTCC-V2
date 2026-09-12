"""Independent hostile-input review of the pure, synthetic pre-evidence engine.

No evaluator is replaced with PASS; accepted source chains use actual raw-OHLC
evaluators. Hashes and roundtrips prove consistency, never external authority.
"""

import hashlib
import json
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from itertools import product

import pytest

from app.trade_qualification.engine import (
    PortfolioInputs,
    PreEvidencePolicy,
    PreEvidenceRun,
    evaluate_pre_evidence,
    verify_pre_evidence,
)
from app.trade_qualification.models import QualificationGate, QualificationState
from app.trade_qualification.timing import event_identity
from tests.unit.qualification_engine_fixtures import engine_inputs, engine_source

D = Decimal


@pytest.fixture(scope="module")
def complete():
    source = engine_source()
    args = engine_inputs(source)
    return source, args, evaluate_pre_evidence(source.market, **args)


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _replace_audit(run, raw):
    return run.model_copy(
        update={
            "protection_audit_json": raw,
            "protection_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        }
    )


def test_legitimate_complete_record_roundtrips_and_replays_without_authority(complete):
    source, args, result = complete
    restored = PreEvidenceRun.model_validate_json(
        result.model_dump_json(round_trip=True), strict=True
    )
    assert restored == result
    assert restored.evaluation_sha256 == result.evaluation_sha256
    assert verify_pre_evidence(restored, source.market, **args) == result
    assert (
        result.pre_evidence_complete
        and result.result.state == QualificationState.RISK_VALID
    )
    assert not result.result.qualified and not result.result.execution_recheck_passed
    assert not result.execution_authority and not result.atomic_risk_reserved


def test_self_signed_non_authoritative_audit_annotation_cannot_pass_replay(complete):
    source, args, result = complete
    audit = json.loads(result.protection_audit_json)
    audit["reviewer_untrusted_annotation"] = (
        "self-signing this string is not provenance"
    )
    altered = _replace_audit(result, _canonical(audit))
    # Even a schema-consistent, fresh outer checksum never certifies that the
    # source-selected audit was produced by the evaluator.
    assert altered.evaluation_sha256 != result.evaluation_sha256
    with pytest.raises(ValueError, match="pre_evidence_replay_mismatch"):
        verify_pre_evidence(altered, source.market, **args)
    assert altered.execution_authority is False


@pytest.mark.parametrize(
    "field",
    [
        "execution_authority",
        "source_authenticity_verified",
        "account_evidence_authenticated",
        "atomic_risk_reserved",
        "execution_recheck_performed",
    ],
)
@pytest.mark.parametrize("value", [True, 1, 0, "false", None])
def test_model_copy_cannot_grant_or_coerce_any_run_authority(complete, field, value):
    source, args, result = complete
    injected = result.model_copy(update={field: value})
    with pytest.raises(ValueError):
        verify_pre_evidence(injected, source.market, **args)


@pytest.mark.parametrize(
    "field", ["execution_authority", "economics_validated", "policy_calibrated"]
)
def test_rehashed_nested_audit_authority_is_rejected(complete, field):
    source, args, result = complete
    audit = json.loads(result.protection_audit_json)
    audit[field] = True
    with pytest.raises(ValueError):
        verify_pre_evidence(
            _replace_audit(result, _canonical(audit)), source.market, **args
        )


@pytest.mark.parametrize(
    "alteration",
    [
        "wrong_report",
        "wrong_instrument",
        "wrong_direction",
        "moved_entry",
        "tightened_stop",
        "farther_target",
        "selected_list",
        "stop_missing",
        "invalid_decimal",
        "nan_decimal",
    ],
)
def test_audit_price_identity_shape_tampering_fails_closed_after_rehash(
    complete, alteration
):
    source, args, result = complete
    audit = json.loads(result.protection_audit_json)
    if alteration.startswith("wrong_"):
        field = {
            "wrong_report": "report_id",
            "wrong_instrument": "instrument_id",
            "wrong_direction": "direction",
        }[alteration]
        audit[field] = "other"
    elif alteration == "moved_entry":
        audit["reference_entry"] = "101"
    elif alteration == "tightened_stop":
        audit["selected"]["stop"]["final_stop"] = "100.5"
    elif alteration == "farther_target":
        audit["selected"]["target"]["final_target"] = "110.17"
    elif alteration == "selected_list":
        audit["selected"] = ["unexpected"]
    elif alteration == "stop_missing":
        del audit["selected"]["stop"]
    else:
        audit["selected"]["stop"]["final_stop"] = (
            "not-a-decimal" if alteration == "invalid_decimal" else "NaN"
        )
    with pytest.raises(ValueError):
        verify_pre_evidence(
            _replace_audit(result, _canonical(audit)), source.market, **args
        )


@pytest.mark.parametrize(
    "raw",
    [
        "[" * 1500 + "0" + "]" * 1500,
        '{"schema":"ctcc_structural_selection_v1","schema":"ctcc_structural_selection_v1"}',
        '{"schema":"ctcc_structural_selection_v1","number":NaN}',
        '{"schema":"ctcc_structural_selection_v1","number":Infinity}',
    ],
)
def test_malformed_deep_duplicate_or_nonfinite_audit_rejects_as_value_error(
    complete, raw
):
    source, args, result = complete
    with pytest.raises(ValueError):
        verify_pre_evidence(_replace_audit(result, raw), source.market, **args)


@pytest.mark.parametrize(
    "field",
    [
        "prefix_sha256",
        "intent_sha256",
        "policy_sha256",
        "risk_inputs_sha256",
        "protection_sha256",
    ],
)
def test_each_external_input_or_subrecord_consistency_digest_is_checked(
    complete, field
):
    source, args, result = complete
    with pytest.raises(ValueError):
        verify_pre_evidence(
            result.model_copy(update={field: "0" * 64}), source.market, **args
        )


@pytest.mark.parametrize("field", ["gross_rr", "net_rr"])
def test_report_rounding_cannot_replace_authoritative_economics(complete, field):
    source, args, result = complete
    altered_result = result.result.model_copy(update={field: D("2.3")})
    with pytest.raises(ValueError):
        verify_pre_evidence(
            result.model_copy(update={"result": altered_result}), source.market, **args
        )


@pytest.mark.parametrize(
    "field", ["report_id", "direction", "requested_contracts", "requested_leverage"]
)
def test_nested_risk_identity_and_original_requested_size_are_retained(complete, field):
    source, args, result = complete
    value = {
        "report_id": "another-report",
        "direction": "short",
        "requested_contracts": D(11),
        "requested_leverage": 11,
    }[field]
    altered = result.model_copy(
        update={"portfolio": result.portfolio.model_copy(update={field: value})}
    )
    with pytest.raises(ValueError):
        verify_pre_evidence(altered, source.market, **args)


@pytest.mark.parametrize("missing", list(product([False, True], repeat=3)))
@pytest.mark.parametrize("missing_policy", [False, True])
def test_all_combinations_of_missing_risk_claims_stop_at_g11_not_defaults(
    complete, missing, missing_policy
):
    source, args, _ = complete
    fields = ("instrument", "account", "authority")
    absent = [name for name, flag in zip(fields, missing, strict=True) if flag]
    inputs = args["risk_inputs"].model_copy(update={name: None for name in absent})
    policy = (
        args["policy"].model_copy(update={"portfolio": None})
        if missing_policy
        else args["policy"]
    )
    run = evaluate_pre_evidence(
        source.market, **(args | {"risk_inputs": inputs, "policy": policy})
    )
    assert len(run.result.gates) == 11
    assert all(gate.passed for gate in run.result.gates[:10])
    expected_missing = {f"{name}_missing" for name in absent}
    if missing_policy:
        expected_missing.add("policy_missing")
    assert set(run.portfolio.causes) == expected_missing
    assert run.pre_evidence_complete == (not expected_missing)
    assert run.portfolio.requested_contracts == D(10)
    assert run.portfolio.requested_leverage == 10
    restored = PreEvidenceRun.model_validate_json(
        run.model_dump_json(round_trip=True), strict=True
    )
    assert restored == run
    assert (
        verify_pre_evidence(
            restored,
            source.market,
            **(args | {"risk_inputs": inputs, "policy": policy}),
        )
        == run
    )
    assert not run.execution_authority


@pytest.mark.parametrize("failure_gate", [1, 6, 7, 10])
def test_downstream_missing_claims_cannot_mask_or_advance_first_failure(failure_gate):
    source = engine_source()
    args = engine_inputs(source)
    original = evaluate_pre_evidence(source.market, **args)
    args["risk_inputs"] = args["risk_inputs"].model_copy(
        update={"instrument": None, "account": None, "authority": None}
    )
    args["policy"] = args["policy"].model_copy(
        update={"economics": None, "portfolio": None}
    )
    if failure_gate == 1:
        del source.market.candles["4H"]
    elif failure_gate == 6:
        args["consumed_event_keys"] = frozenset(
            {event_identity(original.prefix.detection)}
        )
    elif failure_gate == 7:
        args["intent"] = args["intent"].model_copy(
            update={"candidate_entry": D("101.01")}
        )
    run = evaluate_pre_evidence(source.market, **args)
    assert len(run.result.gates) == failure_gate
    assert all(gate.passed for gate in run.result.gates[:-1])
    assert not run.result.gates[-1].passed
    assert run.portfolio is None
    assert (run.economics is not None) == (failure_gate == 10)
    assert (run.protection_audit_json is not None) == (failure_gate >= 8)
    assert not run.pre_evidence_complete


@pytest.mark.parametrize("direction", ["long", "short"])
def test_unrounded_cost_per_base_reaches_risk_while_only_report_rr_is_rounded(
    direction,
):
    source = engine_source(direction)
    args = engine_inputs(source)
    policy = args["policy"]
    args["policy"] = policy.model_copy(
        update={
            "economics": policy.economics.model_copy(
                update={"round_trip_fee_bps": D("10.12345678901234567890")}
            )
        }
    )
    run = evaluate_pre_evidence(source.market, **args)
    assert run.pre_evidence_complete
    costs, risk, result = run.economics, run.portfolio, run.result
    with localcontext(Context(prec=100)):
        assert result.gross_rr == costs.gross_rr.quantize(
            D("1e-30"), rounding=ROUND_HALF_EVEN
        )
        assert result.net_rr == costs.net_rr.quantize(
            D("1e-30"), rounding=ROUND_HALF_EVEN
        )
        assert result.net_rr != costs.net_rr
        exact_loss = D("0.1") * (
            abs(result.candidate_entry - result.stop_loss) + costs.cost_per_base
        )
        assert risk.max_loss_amount == exact_loss
        assert exact_loss != exact_loss.quantize(D("1e-20"))
        assert run.result.gates[9].measured_values["net_rr"] == costs.net_rr
        assert run.result.gates[10].measured_values["max_loss_amount"] == exact_loss


@pytest.mark.parametrize("target", ["policy", "risk_inputs", "economics", "stamp"])
def test_subclass_cannot_hide_fields_or_execute_custom_serializers_before_validation(
    complete, target
):
    source, args, _ = complete
    args = dict(args)
    original = {
        "policy": args["policy"],
        "risk_inputs": args["risk_inputs"],
        "economics": args["policy"].economics,
        "stamp": args["risk_inputs"].authority.stamp,
    }[target]

    class Subclass(type(original)):
        def model_dump(self, *args, **kwargs):
            raise AssertionError("untrusted serializer executed")

        def model_dump_json(self, *args, **kwargs):
            raise AssertionError("untrusted serializer executed")

    value = Subclass.model_construct(**original.__dict__)
    if target in ("policy", "risk_inputs"):
        args[target] = value
    elif target == "economics":
        args["policy"] = args["policy"].model_copy(update={"economics": value})
    else:
        authority = args["risk_inputs"].authority.model_copy(update={"stamp": value})
        args["risk_inputs"] = args["risk_inputs"].model_copy(
            update={"authority": authority}
        )
    with pytest.raises(ValueError):
        evaluate_pre_evidence(source.market, **args)


@pytest.mark.parametrize(
    "field,value",
    [
        ("requested_leverage", True),
        ("requested_leverage", 10.0),
        ("requested_leverage", "10"),
        ("requested_contracts", True),
        ("requested_contracts", 10.0),
        ("requested_contracts", "10"),
        ("requested_contracts", D("NaN")),
        ("requested_contracts", D("sNaN")),
        ("requested_contracts", D("Infinity")),
        ("requested_contracts", D("-Infinity")),
    ],
)
def test_raw_model_copy_wrong_scalar_types_reject_instead_of_coercing(
    complete, field, value
):
    source, args, _ = complete
    altered = args["risk_inputs"].model_copy(update={field: value})
    with pytest.raises(ValueError):
        evaluate_pre_evidence(source.market, **(args | {"risk_inputs": altered}))


@pytest.mark.parametrize("kind", [PreEvidencePolicy, PortfolioInputs])
def test_hidden_extra_field_cannot_disappear_during_input_copy(complete, kind):
    source, args, _ = complete
    field = "policy" if kind is PreEvidencePolicy else "risk_inputs"
    altered = args[field].model_copy(update={"execution_authority": True})
    with pytest.raises(ValueError):
        evaluate_pre_evidence(source.market, **(args | {field: altered}))


def test_g12_cannot_be_appended_to_a_pre_evidence_run(complete):
    source, args, run = complete
    gate = run.result.gates[-1].model_copy(update={"gate": QualificationGate.EVIDENCE})
    result = run.result.model_copy(update={"gates": (*run.result.gates, gate)})
    with pytest.raises(ValueError):
        verify_pre_evidence(
            run.model_copy(update={"result": result}), source.market, **args
        )
