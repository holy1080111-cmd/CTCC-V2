"""Pre-evidence ordering/replay contracts using actual synthetic OHLC gates.

Fictional Demo claims are explicit test inputs, never runtime receipts. Failure
sentinels forbid downstream calls; no passing evaluator result is mocked.
"""

import ast
import hashlib
import inspect
import json
from datetime import timedelta, timezone
from decimal import Context, Decimal, localcontext

import pytest
from pydantic import ValidationError

from app.trade_qualification import engine
from app.trade_qualification.engine import (
    PortfolioInputs,
    PreEvidencePolicy,
    PreEvidenceRun,
    ProtectionPolicy,
    evaluate_pre_evidence,
    verify_pre_evidence,
)
from app.trade_qualification.models import QualificationGate
from tests.unit.qualification_engine_fixtures import engine_inputs, engine_source
from tests.unit.qualification_prefix_fixtures import prefix_source
from tests.unit.test_qualification_portfolio import outcome, position, reservation

D = Decimal


@pytest.fixture(scope="module")
def baseline():
    source = engine_source()
    inputs = engine_inputs(source)
    return source, inputs, evaluate_pre_evidence(source.market, **inputs)


def _forbid(monkeypatch, *names):
    def forbidden(*args, **kwargs):
        raise AssertionError("a later evaluator ran after the first failure")

    for name in names:
        monkeypatch.setattr(engine, name, forbidden)


def _payload(run):
    return json.loads(run.model_dump_json(round_trip=True))


def _from_json(value):
    return PreEvidenceRun.model_validate_json(json.dumps(value), strict=True)


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _stopped(run, count):
    assert len(run.result.gates) == count
    assert tuple(g.gate for g in run.result.gates) == tuple(QualificationGate)[:count]
    assert all(g.passed for g in run.result.gates[:-1])
    assert not run.result.gates[-1].passed
    assert not run.pre_evidence_complete and not run.result.qualified
    assert not run.execution_authority


@pytest.mark.parametrize("missing", ["quote", "reference"])
def test_g1_failure_preserves_original_prefix_and_never_selects(
    baseline, monkeypatch, missing
):
    source, inputs, _ = baseline
    _forbid(
        monkeypatch,
        "select_structural_protection",
        "evaluate_economics",
        "evaluate_portfolio",
    )
    run = evaluate_pre_evidence(source.market, **(inputs | {missing: None}))
    _stopped(run, 1)
    assert run.result == run.prefix.result
    assert run.protection_audit_json is run.economics is run.portfolio is None


def test_consumed_source_event_stops_at_g6_before_structural_work(
    baseline, monkeypatch
):
    source, inputs, full = baseline
    _forbid(
        monkeypatch,
        "select_structural_protection",
        "evaluate_economics",
        "evaluate_portfolio",
    )
    run = evaluate_pre_evidence(
        source.market,
        **(inputs | {"consumed_event_keys": frozenset({full.prefix.timing.event_key})}),
    )
    _stopped(run, 6)
    assert run.result == run.prefix.result
    assert run.protection_audit_json is run.economics is run.portfolio is None


@pytest.mark.parametrize("direction", ["long", "short"])
def test_targetless_bracket_never_invents_a_passing_g8_stop(direction, monkeypatch):
    source = prefix_source(direction)
    _forbid(monkeypatch, "evaluate_economics", "evaluate_portfolio")
    run = evaluate_pre_evidence(source.market, **engine_inputs(source))
    _stopped(run, 8)
    audit = json.loads(run.protection_audit_json)
    assert "structural_target_missing" in audit["fail_codes"]
    assert run.result.gates[-1].code == audit["fail_codes"][0]
    assert run.result.stop_loss is run.result.take_profit is None
    assert run.economics is run.portfolio is None


@pytest.mark.parametrize(
    "field,value", [("cost_bps", D(0)), ("min_stop_distance_atr", D(100))]
)
def test_real_structural_policy_failure_cannot_be_repaired_by_cost_or_risk(
    baseline, monkeypatch, field, value
):
    source, inputs, _ = baseline
    policy = inputs["policy"]
    updated = policy.model_copy(
        update={"protection": policy.protection.model_copy(update={field: value})}
    )
    _forbid(monkeypatch, "evaluate_economics", "evaluate_portfolio")
    run = evaluate_pre_evidence(source.market, **(inputs | {"policy": updated}))
    _stopped(run, 8)
    assert run.economics is run.portfolio is None
    assert run.result.stop_loss is run.result.take_profit is None


@pytest.mark.parametrize("missing", [True, False])
def test_g10_missing_or_insufficient_cost_policy_never_calls_portfolio(
    baseline, monkeypatch, missing
):
    source, inputs, full = baseline
    policy = inputs["policy"]
    economics = (
        None
        if missing
        else policy.economics.model_copy(update={"minimum_net_rr": D(100)})
    )
    updated = policy.model_copy(update={"economics": economics})
    _forbid(monkeypatch, "evaluate_portfolio")
    run = evaluate_pre_evidence(source.market, **(inputs | {"policy": updated}))
    _stopped(run, 10)
    assert run.protection_audit_json == full.protection_audit_json
    assert (
        run.result.candidate_entry,
        run.result.stop_loss,
        run.result.take_profit,
    ) == (full.result.candidate_entry, full.result.stop_loss, full.result.take_profit)
    assert run.result.gross_rr is run.result.net_rr is run.portfolio is None
    assert not run.economics.passed
    if missing:
        assert run.economics.code == "cost_policy_missing"
    else:
        assert run.economics.net_rr < economics.minimum_net_rr


@pytest.mark.parametrize(
    "field", ["account", "instrument", "authority", "portfolio_policy"]
)
def test_missing_risk_evidence_is_explicit_g11_failure_not_default_permission(
    baseline, field
):
    source, original, full = baseline
    inputs = dict(original)
    if field == "portfolio_policy":
        inputs["policy"] = inputs["policy"].model_copy(update={"portfolio": None})
        cause = "policy_missing"
    else:
        inputs["risk_inputs"] = inputs["risk_inputs"].model_copy(update={field: None})
        cause = f"{field}_missing"
    run = evaluate_pre_evidence(source.market, **inputs)
    _stopped(run, 11)
    assert cause in run.portfolio.causes
    assert run.result.gates[:10] == full.result.gates[:10]
    assert run.economics == full.economics


def test_many_simultaneous_risk_failures_emit_bounded_gate_and_complete_causes(
    baseline,
):
    source, inputs, _ = baseline
    claims = inputs["risk_inputs"]
    now = source.evaluated_at
    ledger = claims.account.model_copy(
        update={
            "positions": (position(),),
            "position_count": 1,
            "pending_reservations": (reservation(),),
            "pending_reservation_count": 1,
            "equity": D(500),
            "available_margin": D(0),
            "loss_history": tuple(
                outcome(
                    outcome_id=f"loss-{i}",
                    sequence=i,
                    closed_at=now - timedelta(minutes=3 - i),
                    realized_pnl=D(-200),
                )
                for i in range(3)
            ),
        }
    )
    policy = inputs["policy"]
    risk_policy = policy.portfolio.model_copy(
        update={
            "risk_per_trade_pct": D("0.000001"),
            "max_daily_loss_pct": D("0.000001"),
            "max_weekly_loss_pct": D("0.000001"),
            "max_drawdown_pct": D("0.000001"),
            "max_open_positions": 1,
            "max_same_direction_positions": 1,
            "max_correlated_positions": 1,
            "max_order_notional": D("0.01"),
            "max_order_contracts": D("0.01"),
            "max_portfolio_notional": D("0.01"),
            "max_same_direction_notional": D("0.01"),
            "max_correlated_notional": D("0.01"),
            "max_portfolio_risk_pct": D("0.000001"),
            "max_portfolio_margin_pct": D("0.000001"),
            "max_leverage": 1,
        }
    )
    run = evaluate_pre_evidence(
        source.market,
        **(
            inputs
            | {
                "policy": policy.model_copy(update={"portfolio": risk_policy}),
                "risk_inputs": claims.model_copy(
                    update={
                        "account": ledger,
                        "instrument": claims.instrument.model_copy(
                            update={"min_contracts": D(2000), "max_contracts": D(3000)}
                        ),
                        "requested_contracts": D("1001.1"),
                        "requested_leverage": 125,
                    }
                ),
            }
        ),
    )
    _stopped(run, 11)
    causes = run.portfolio.causes
    assert len(",".join(causes)) > 512
    measured = run.result.gates[-1].measured_values
    assert measured["risk_cause_count"] == len(causes)
    assert measured["risk_first_cause"] == causes[0]
    assert (
        measured["risk_result_sha256"]
        == hashlib.sha256(
            _canonical(run.portfolio.model_dump(mode="json", round_trip=True)).encode()
        ).hexdigest()
    )
    assert all(not isinstance(v, str) or len(v) <= 512 for v in measured.values())
    assert _from_json(_payload(run)) == run


def test_full_run_json_is_immutable_roundtrippable_and_rr_is_reporting_only(baseline):
    source, inputs, run = baseline
    assert _from_json(_payload(run)) == run
    assert verify_pre_evidence(run, source.market, **inputs) == run
    assert len(run.evaluation_sha256) == 64
    assert run.pre_evidence_complete and not run.result.qualified
    with localcontext(Context(prec=100)):
        assert abs(run.result.gross_rr - run.economics.gross_rr) <= D("1e-30")
        assert abs(run.result.net_rr - run.economics.net_rr) <= D("1e-30")
    assert run.result.net_rr.as_tuple().exponent >= -30
    assert run.economics.net_rr.as_tuple().exponent < -30
    with pytest.raises(ValidationError):
        run.execution_authority = True
    with pytest.raises(TypeError):
        run.result.gates[-1].measured_values["atomic_risk_reserved"] = True


@pytest.mark.parametrize(
    "flag",
    [
        "execution_authority",
        "source_authenticity_verified",
        "account_evidence_authenticated",
        "atomic_risk_reserved",
        "execution_recheck_performed",
    ],
)
@pytest.mark.parametrize("value", [True, 0, 1, "false"])
def test_run_cannot_grant_or_coerce_authority(baseline, flag, value):
    payload = _payload(baseline[2])
    payload[flag] = value
    with pytest.raises(ValueError):
        _from_json(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("requested_contracts", 10),
        ("requested_contracts", 10.0),
        ("requested_contracts", D("NaN")),
        ("requested_contracts", D("1e-201")),
        ("requested_leverage", True),
        ("requested_leverage", "10"),
        ("requested_leverage", 10.0),
    ],
)
def test_model_copy_wrong_scalar_is_rejected_before_serialization_or_any_gate(
    baseline, monkeypatch, field, value
):
    source, inputs, _ = baseline
    risk = inputs["risk_inputs"].model_copy(update={field: value})
    _forbid(monkeypatch, "evaluate_qualification_prefix")
    with pytest.raises(ValueError):
        evaluate_pre_evidence(source.market, **(inputs | {"risk_inputs": risk}))


@pytest.mark.parametrize(
    "kind",
    ["hidden", "missing", "generator", "oversized", "wrong_tuple", "unknown_subclass"],
)
def test_nested_risk_tampering_cannot_be_erased_or_iterated_by_serializer(
    baseline, monkeypatch, kind
):
    source, inputs, _ = baseline
    risk = inputs["risk_inputs"]
    account = risk.account
    if kind == "hidden":
        account = account.model_copy(
            update={
                "balance_stamp": account.balance_stamp.model_copy(
                    update={"undeclared": True}
                )
            }
        )
    elif kind == "missing":
        account = account.model_copy()
        account.__dict__.pop("equity")
    elif kind == "generator":

        def forbidden_iterator():
            raise AssertionError("preflight must not consume an untrusted iterable")
            yield None

        account = account.model_copy(update={"positions": forbidden_iterator()})
    elif kind == "oversized":
        account = account.model_copy(update={"positions": (position(),) * 2049})
    elif kind == "wrong_tuple":
        account = account.model_copy(update={"positions": []})
    else:

        class UntrustedRisk(PortfolioInputs):
            pass

        risk = UntrustedRisk.model_validate(risk.model_dump(round_trip=True))
    risk = risk.model_copy(update={"account": account})
    _forbid(monkeypatch, "evaluate_qualification_prefix")
    with pytest.raises(ValueError):
        evaluate_pre_evidence(source.market, **(inputs | {"risk_inputs": risk}))


@pytest.mark.parametrize("field", ["prefix", "protection", "economics", "portfolio"])
def test_nested_policy_extras_are_not_silently_discarded(baseline, monkeypatch, field):
    source, inputs, _ = baseline
    policy = inputs["policy"]
    policy = policy.model_copy(
        update={
            field: getattr(policy, field).model_copy(update={"hidden_override": True})
        }
    )
    _forbid(monkeypatch, "evaluate_qualification_prefix")
    with pytest.raises(ValueError):
        evaluate_pre_evidence(source.market, **(inputs | {"policy": policy}))


@pytest.mark.parametrize(
    "model", [PreEvidencePolicy, ProtectionPolicy, PortfolioInputs]
)
def test_every_policy_and_risk_contract_has_explicit_required_fields(baseline, model):
    inputs = baseline[1]
    instance = {
        PreEvidencePolicy: inputs["policy"],
        ProtectionPolicy: inputs["policy"].protection,
        PortfolioInputs: inputs["risk_inputs"],
    }[model]
    for field in model.model_fields:
        raw = instance.model_dump(round_trip=True)
        raw.pop(field)
        with pytest.raises(ValueError):
            model.model_validate(raw, strict=True)


@pytest.mark.parametrize("field", ["net_rr", "gross_rr"])
def test_changed_reporting_rr_cannot_diverge_from_full_cost_result(baseline, field):
    payload = _payload(baseline[2])
    payload["result"][field] = str(D(payload["result"][field]) - D("1e-15"))
    with pytest.raises(ValueError):
        _from_json(payload)


@pytest.mark.parametrize(
    "kind",
    ["duplicate", "noncanonical", "missing_stop", "nonnumeric", "nan", "authority"],
)
def test_structural_audit_tampering_is_rejected_even_with_recomputed_digest(
    baseline, kind
):
    payload = _payload(baseline[2])
    original = payload["protection_audit_json"]
    audit = json.loads(original)
    if kind == "duplicate":
        raw = original[:-1] + ',"schema":"ctcc_structural_selection_v1"}'
    elif kind == "noncanonical":
        raw = json.dumps(audit, indent=2)
    else:
        if kind == "missing_stop":
            audit["selected"].pop("stop")
        elif kind in {"nonnumeric", "nan"}:
            audit["selected"]["stop"]["final_stop"] = (
                "bad" if kind == "nonnumeric" else "NaN"
            )
        else:
            audit["execution_authority"] = True
        raw = _canonical(audit)
    payload["protection_audit_json"] = raw
    payload["protection_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    with pytest.raises(ValueError):
        _from_json(payload)


def test_self_consistent_input_hash_is_not_original_source_authentication(baseline):
    source, inputs, run = baseline
    payload = _payload(run)
    payload["policy"]["policy_id"] = "different-unapproved-input-policy"
    payload["policy_sha256"] = hashlib.sha256(
        _canonical(payload["policy"]).encode()
    ).hexdigest()
    forged = _from_json(payload)
    assert not forged.source_authenticity_verified
    with pytest.raises(ValueError, match="pre_evidence_replay_mismatch"):
        verify_pre_evidence(forged, source.market, **inputs)


@pytest.mark.parametrize("kind", ["computed", "extra", "nested_extra", "subclass"])
def test_verify_requires_exact_raw_result_without_undeclared_model_copy_state(
    baseline, kind
):
    source, inputs, run = baseline
    if kind == "computed":
        dirty = run.model_copy(update={"pre_evidence_complete": True})
    elif kind == "extra":
        dirty = run.model_copy(update={"unknown": True})
    elif kind == "nested_extra":
        dirty = run.model_copy(
            update={"economics": run.economics.model_copy(update={"unknown": True})}
        )
    else:

        class UntrustedRun(PreEvidenceRun):
            pass

        dirty = UntrustedRun.model_validate_json(run.model_dump_json(round_trip=True))
    with pytest.raises(ValueError):
        verify_pre_evidence(dirty, source.market, **inputs)


def test_equivalent_explicit_utc_clock_does_not_change_any_gate_or_hash(baseline):
    source, inputs, run = baseline
    local_time = source.evaluated_at.astimezone(timezone(timedelta(hours=8)))
    repeated = evaluate_pre_evidence(
        source.market, **(inputs | {"evaluated_at": local_time})
    )
    assert repeated == run and repeated.evaluation_sha256 == run.evaluation_sha256


def test_no_g12_clock_settings_order_io_or_external_pass_input_exists():
    tree = ast.parse(inspect.getsource(engine))
    forbidden = {
        "now",
        "utcnow",
        "get_settings",
        "build_candidate",
        "place_order",
        "create_order",
        "send_order",
        "open",
        "publish_evidence",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            assert name not in forbidden
        if isinstance(node, ast.Import):
            assert all(
                part.name.split(".")[0] not in {"httpx", "requests", "socket"}
                for part in node.names
            )
    parameters = inspect.signature(evaluate_pre_evidence).parameters
    assert set(parameters) == {
        "market",
        "intent",
        "quote",
        "reference",
        "policy",
        "risk_inputs",
        "consumed_event_keys",
        "evaluated_at",
    }
    assert not {
        "passed",
        "analysis",
        "selection",
        "gate",
        "qualification",
        "stop_loss",
        "take_profit",
    } & set(parameters)
