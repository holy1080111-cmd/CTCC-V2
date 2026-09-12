"""Versioned actual gate/replay integration; synthetic, not trading samples."""

import inspect
import json
from copy import deepcopy
from datetime import tzinfo
from decimal import Context, Decimal, Inexact, localcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.trade_evidence.gates import EvidenceGateError, publish_qualification_evidence
from app.trade_qualification import history_engine as engine
from app.trade_qualification import history_prefix as prefix
from app.trade_qualification.engine import evaluate_pre_evidence, verify_pre_evidence
from app.trade_qualification.history_engine import (
    HistoryPreEvidencePolicy,
    HistoryPreEvidenceRun,
    evaluate_history_pre_evidence,
    verify_history_pre_evidence,
)
from app.trade_qualification.history_prefix import (
    HistoryEntryQualificationResult,
    HistoryQualificationPrefixPolicy,
    HistoryQualificationPrefixRun,
    evaluate_history_qualification_prefix,
    verify_history_qualification_prefix,
)
from app.trade_qualification.models import (
    EntryQualificationResult,
    MarketRegime,
)
from app.trade_qualification.one_shot import OneShotInputError, _original
from app.trade_qualification.service import (
    verify_qualification_prefix,
)
from tests.unit.history_qualification_fixtures import history_inputs, history_source
from tests.unit.qualification_engine_fixtures import engine_inputs, engine_source
from tests.unit.test_qualification_events import _row

D = Decimal


@pytest.fixture(scope="module", params=("long", "short"))
def reversal(request):
    source = history_source(direction=request.param, bracket=True)
    args = history_inputs(source)
    return source, args, evaluate_history_pre_evidence(source.market, **args)


@pytest.fixture(scope="module", params=("long", "short"))
def legacy(request):
    source = engine_source(request.param)
    args = history_inputs(source)
    return source, args, evaluate_history_pre_evidence(source.market, **args)


def prefix_args(args):
    return {
        key: value
        for key, value in (args | {"policy": args["policy"].prefix}).items()
        if key != "risk_inputs"
    }


def forbidden(*args, **kwargs):
    raise AssertionError("a later gate or IO ran after its boundary")


def test_actual_reversal_history_advances_seven_gates_but_preserves_g8_blocker(
    reversal,
):
    source, args, run = reversal
    assert type(run) is HistoryPreEvidenceRun
    assert type(run.prefix) is HistoryQualificationPrefixRun
    assert type(run.result) is HistoryEntryQualificationResult
    assert run.prefix.prefix_complete
    assert len(run.result.gates) == 8
    assert run.result.fail_codes == ("source_data_blockers",)
    assert all(item.passed for item in run.result.gates[:7])
    assert run.result.market_regime == "History Verified Reversal"
    history = run.prefix.history_admission
    assert history.snapshot_regime == "Unknown"  # Never rewritten as Trend.
    assert history.admitted and history.history_verified
    assert history.detection == run.prefix.detection
    assert history.source_sha256 == run.prefix.data_result.source_sha256
    assert run.result.history_admission_sha256 == history.evaluation_sha256
    assert (
        run.prefix.result
        == evaluate_history_qualification_prefix(
            source.market, **prefix_args(args)
        ).result
    )
    audit = json.loads(run.protection_audit_json)
    assert audit["fail_codes"] == ["source_data_blockers"]
    assert (
        run.result.stop_loss
        is run.result.take_profit
        is run.economics
        is run.portfolio
        is None
    )
    assert not run.pre_evidence_complete and not run.result.qualified
    assert not run.execution_authority and not run.atomic_risk_reserved
    assert (
        not run.source_authenticity_verified and not run.account_evidence_authenticated
    )


def test_original_unknown_route_stays_blocked_and_old_hash_is_unchanged(reversal):
    source, _args, run = reversal
    old_args = engine_inputs(source)
    before = evaluate_pre_evidence(source.market, **old_args)
    old_hash = before.evaluation_sha256
    assert len(before.result.gates) == 2
    assert before.result.market_regime is MarketRegime.UNKNOWN
    assert before.result.fail_codes == ("regime_strategy_not_allowed",)
    assert (
        evaluate_pre_evidence(source.market, **old_args).evaluation_sha256 == old_hash
    )
    assert run.evaluation_sha256 != old_hash
    assert run.policy_sha256 != before.policy_sha256
    assert run.prefix.policy_sha256 != before.prefix.policy_sha256
    assert run.result.gates[0] == before.result.gates[0]


def test_versioned_legacy_family_runs_all_eleven_actual_gates_without_formula_changes(
    legacy,
):
    source, _args, run = legacy
    old = evaluate_pre_evidence(source.market, **engine_inputs(source))
    assert run.pre_evidence_complete and old.pre_evidence_complete
    assert len(run.result.gates) == 11
    assert run.prefix.history_admission is None
    assert run.result.history_admission_sha256 is None
    assert run.result.gates[0] == old.result.gates[0]
    assert run.result.gates[2:] == old.result.gates[2:]
    assert run.prefix.detection == old.prefix.detection
    assert run.prefix.timing == old.prefix.timing
    assert run.prefix.location == old.prefix.location
    assert run.protection_audit_json == old.protection_audit_json
    assert run.economics == old.economics and run.portfolio == old.portfolio
    for name in (
        "candidate_entry",
        "stop_loss",
        "take_profit",
        "gross_rr",
        "net_rr",
        "raw_score",
        "effective_score",
    ):
        assert getattr(run.result, name) == getattr(old.result, name)
    assert not run.result.qualified and not run.result.evidence_complete
    assert not run.execution_authority and not run.atomic_risk_reserved
    assert (
        not run.source_authenticity_verified and not run.account_evidence_authenticated
    )


@pytest.mark.parametrize(
    "strategy,count,code",
    [
        ("volatility_expansion", 3, "htf_strategy_permission_denied"),
        ("liquidity_sweep_reversal", 2, "sweep_htf_policy_unspecified"),
    ],
)
@pytest.mark.parametrize("direction", ("long", "short"))
def test_missing_htf_policy_remains_a_real_gate_failure(
    strategy, count, code, direction, monkeypatch
):
    source = history_source(strategy, direction)
    monkeypatch.setattr(engine, "select_structural_protection", forbidden)
    run = evaluate_history_pre_evidence(source.market, **history_inputs(source))
    assert len(run.result.gates) == count
    assert run.result.fail_codes == (code,)
    assert run.prefix.history_admission.history_verified
    if strategy == "volatility_expansion":
        assert run.prefix.history_admission.admitted
        assert run.result.gates[-1].measured_values["failed_htf_conditions"] == "none"
    else:
        assert not run.prefix.history_admission.admitted


@pytest.mark.parametrize("missing", ("quote", "reference"))
def test_g1_missing_source_stops_before_history_or_other_gates(
    reversal, monkeypatch, missing
):
    source, args, _ = reversal
    monkeypatch.setattr(prefix, "evaluate_regime_admission", forbidden)
    monkeypatch.setattr(prefix, "route_regime", forbidden)
    monkeypatch.setattr(engine, "select_structural_protection", forbidden)
    run = evaluate_history_pre_evidence(source.market, **(args | {missing: None}))
    assert len(run.result.gates) == 1 and not run.result.gates[0].passed
    assert run.prefix.history_admission is None


def test_removed_opposed_history_stops_g2_not_snapshot_boolean(reversal, monkeypatch):
    source, args, _ = reversal
    market = source.market.model_copy(deep=True)
    for index in range(len(market.candles["1H"]) - 7):
        old = market.candles["1H"][index]
        market.candles["1H"][index] = _row(old.timestamp, D(100))
    monkeypatch.setattr(prefix, "assess_conditions", forbidden)
    run = evaluate_history_pre_evidence(market, **args)
    assert len(run.result.gates) == 2
    assert run.result.fail_codes == ("setup_missing",)


def test_g1_allows_old_tail_but_history_requires_latest_confirmed_close(
    reversal, monkeypatch
):
    source, args, _ = reversal
    market = source.market.model_copy(deep=True)
    market.candles["5m"] = market.candles["5m"][:-1]
    monkeypatch.setattr(prefix, "assess_conditions", forbidden)
    run = evaluate_history_pre_evidence(market, **args)
    assert len(run.result.gates) == 2
    assert run.result.fail_codes == ("confirmed_tail_missing",)
    assert run.prefix.history_admission.instrument_id == "unknown"


def test_g4_still_uses_current_executable_spread_limit(reversal, monkeypatch):
    source, args, _ = reversal
    policy = args["policy"]
    policy = policy.model_copy(
        update={
            "prefix": policy.prefix.model_copy(
                update={"maximum_strategy_spread_bps": D(1)}
            )
        }
    )
    monkeypatch.setattr(prefix, "extract_trigger", forbidden)
    run = evaluate_history_pre_evidence(source.market, **(args | {"policy": policy}))
    assert len(run.result.gates) == 4
    assert run.result.fail_codes == ("strategy_spread_exceeded",)
    assert run.prefix.detection is None and run.prefix.history_admission.admitted


def test_consumed_event_stops_g6_without_renewing_history(reversal, monkeypatch):
    source, args, original = reversal
    monkeypatch.setattr(engine, "select_structural_protection", forbidden)
    run = evaluate_history_pre_evidence(
        source.market,
        **(
            args
            | {"consumed_event_keys": frozenset({original.prefix.timing.event_key})}
        ),
    )
    assert len(run.result.gates) == 6
    assert run.result.fail_codes == ("stale_candidate",)
    assert run.prefix.timing.event_key == original.prefix.timing.event_key
    assert run.prefix.history_admission == original.prefix.history_admission
    assert run.prefix.detection == original.prefix.detection


def test_outside_original_zone_stops_g7_before_protection(reversal, monkeypatch):
    source, args, _ = reversal
    monkeypatch.setattr(engine, "select_structural_protection", forbidden)
    intent = args["intent"].model_copy(
        update={"candidate_entry": D(120) if source.direction == "long" else D(80)}
    )
    run = evaluate_history_pre_evidence(source.market, **(args | {"intent": intent}))
    assert len(run.result.gates) == 7
    assert run.result.fail_codes == ("candidate_outside_entry_zone",)


@pytest.mark.parametrize(
    "policy_field,expected_count", [("economics", 10), ("portfolio", 11)]
)
def test_full_engine_does_not_invent_missing_cost_or_risk_policy(
    legacy, policy_field, expected_count
):
    source, args, original = legacy
    policy = args["policy"].model_copy(update={policy_field: None})
    run = evaluate_history_pre_evidence(source.market, **(args | {"policy": policy}))
    assert len(run.result.gates) == expected_count
    assert not run.result.gates[-1].passed and not run.pre_evidence_complete
    assert run.result.gates[:9] == original.result.gates[:9]
    assert run.result.candidate_entry == original.result.candidate_entry


@pytest.mark.parametrize("risk_field", ("account", "instrument", "authority"))
def test_full_engine_requires_all_risk_evidence(legacy, risk_field):
    source, args, original = legacy
    risk = args["risk_inputs"].model_copy(update={risk_field: None})
    run = evaluate_history_pre_evidence(source.market, **(args | {"risk_inputs": risk}))
    assert len(run.result.gates) == 11 and not run.pre_evidence_complete
    assert run.result.gates[:10] == original.result.gates[:10]
    assert f"{risk_field}_missing" in run.portfolio.causes


def _check_exact_replay_json_roundtrip_and_input_nonmutation(case):
    source, args, run = case
    market_before, args_before = deepcopy(source.market), deepcopy(args)
    assert verify_history_pre_evidence(run, source.market, **args) == run
    assert (
        verify_history_qualification_prefix(
            run.prefix, source.market, **prefix_args(args)
        )
        == run.prefix
    )
    restored = HistoryPreEvidenceRun.model_validate_json(
        run.model_dump_json(round_trip=True), strict=True
    )
    assert restored == run and restored.evaluation_sha256 == run.evaluation_sha256
    assert source.market == market_before and args == args_before


def test_reversal_exact_replay_json_roundtrip_and_input_nonmutation(reversal):
    _check_exact_replay_json_roundtrip_and_input_nonmutation(reversal)


def test_legacy_exact_replay_json_roundtrip_and_input_nonmutation(legacy):
    _check_exact_replay_json_roundtrip_and_input_nonmutation(legacy)


def test_decimal_context_cannot_change_the_versioned_gate_chain(reversal):
    source, args, run = reversal
    with localcontext(Context(prec=5, traps=[Inexact])):
        replay = evaluate_history_pre_evidence(source.market, **args)
    assert replay == run


def test_replay_rejects_consistent_source_or_ledger_substitution(reversal):
    source, args, run = reversal
    changed = source.market.model_copy(deep=True)
    changed.open_interest_contracts += 1
    with pytest.raises(ValueError, match="replay_mismatch"):
        verify_history_pre_evidence(run, changed, **args)
    with pytest.raises(ValueError, match="replay_mismatch"):
        verify_history_pre_evidence(
            run,
            source.market,
            **(args | {"consumed_event_keys": frozenset({"f" * 64})}),
        )


def test_legacy_g12_and_one_shot_reject_new_contract_before_clock_or_io(legacy):
    source, args, run = legacy
    with pytest.raises(ValueError, match="exact PreEvidenceRun"):
        verify_pre_evidence(run, source.market, **args)
    with pytest.raises(ValueError, match="exact QualificationPrefixRun"):
        verify_qualification_prefix(run.prefix, source.market, **prefix_args(args))
    with pytest.raises(EvidenceGateError, match="pre_evidence_replay_failed"):
        publish_qualification_evidence(
            Path("never-open-this-history-path"),
            source.market,
            run=run,
            **args,
            purpose="synthetic_test",
            clock=forbidden,
        )
    with pytest.raises(OneShotInputError, match="intent_or_run_invalid"):
        _original(source.market, run, args)


@pytest.mark.parametrize(
    "name",
    (
        "execution_authority",
        "source_authenticity_verified",
        "account_evidence_authenticated",
        "atomic_risk_reserved",
        "execution_recheck_performed",
    ),
)
@pytest.mark.parametrize("value", (True, 1, "false"))
def test_authority_flags_never_coerce_to_permission(legacy, name, value):
    source, args, run = legacy
    with pytest.raises((ValueError, ValidationError)):
        verify_history_pre_evidence(
            run.model_copy(update={name: value}), source.market, **args
        )


@pytest.mark.parametrize(
    "attribute",
    ("__pydantic_private__", "__pydantic_extra__", "__pydantic_fields_set__"),
)
@pytest.mark.parametrize("which", ("run", "policy", "intent", "history"))
def test_hidden_metadata_is_rejected_before_serializers_or_callbacks(
    reversal, attribute, which
):
    source, args, run = reversal

    class Hostile:
        def __repr__(self):
            forbidden()

        def __str__(self):
            raise AssertionError("must not stringify hostile state")

    args = deepcopy(args)
    run = HistoryPreEvidenceRun.model_validate_json(
        run.model_dump_json(round_trip=True), strict=True
    )
    target = {
        "run": run,
        "policy": args["policy"],
        "intent": args["intent"],
        "history": run.prefix.history_admission,
    }[which]
    object.__setattr__(
        target,
        attribute,
        {"hidden": Hostile()} if attribute != "__pydantic_fields_set__" else {"hidden"},
    )
    with pytest.raises(ValueError, match="hidden|field"):
        verify_history_pre_evidence(run, source.market, **args)


def test_exact_versions_and_types_cannot_be_cross_loaded(legacy):
    _source, args, run = legacy
    for model in (HistoryPreEvidencePolicy, HistoryQualificationPrefixPolicy):
        obj = (
            args["policy"]
            if model is HistoryPreEvidencePolicy
            else args["policy"].prefix
        )
        data = obj.model_dump(round_trip=True)
        with pytest.raises(ValidationError):
            model.model_validate(data | {"contract_version": "legacy"}, strict=True)
    with pytest.raises(ValidationError):
        EntryQualificationResult.model_validate_json(
            run.result.model_dump_json(round_trip=True)
        )
    assert "ctcc-history-pre-evidence-v1" in run.model_dump_json(round_trip=True)


def test_unknown_and_risk_off_cannot_be_forged_as_passing_classifications(reversal):
    source, args, run = reversal
    for value in (MarketRegime.UNKNOWN, MarketRegime.RISK_OFF):
        forged = run.result.model_copy(update={"market_regime": value})
        with pytest.raises(ValidationError):
            verify_history_pre_evidence(
                run.model_copy(update={"result": forged}), source.market, **args
            )


def test_direct_custom_timezone_is_rejected_before_callback(reversal):
    source, args, _ = reversal

    class HostileTimezone(tzinfo):
        def utcoffset(self, dt):
            forbidden()

    malformed = source.evaluated_at.replace(tzinfo=HostileTimezone())
    with pytest.raises(ValueError, match="timezone"):
        evaluate_history_pre_evidence(
            source.market, **(args | {"evaluated_at": malformed})
        )


def test_source_has_no_io_or_runtime_registration():
    for module in (prefix, engine):
        text = inspect.getsource(module)
        for forbidden_name in (
            "requests.",
            "httpx.",
            "asyncio.",
            "subprocess.",
            "open(",
            "datetime.now",
            "submit_order(",
            "reserve(",
        ):
            assert forbidden_name not in text


@pytest.mark.parametrize(
    "where",
    (
        "instrument",
        "account_stamp",
        "portfolio_policy",
        "intent",
        "quote",
        "reference",
        "market",
        "verification_run",
    ),
)
def test_every_nested_datetime_is_guarded_before_timezone_callback(legacy, where):
    source, args, run = legacy
    args = deepcopy(args)
    callbacks = []

    class HostileTimezone(tzinfo):
        def utcoffset(self, dt):
            callbacks.append("utcoffset")
            raise AssertionError("timezone callback reached")

    bad = source.evaluated_at.replace(tzinfo=HostileTimezone())
    market = source.market.model_copy(deep=True)
    if where == "instrument":
        args["risk_inputs"] = args["risk_inputs"].model_copy(
            update={
                "instrument": args["risk_inputs"].instrument.model_copy(
                    update={"observed_at": bad}
                )
            }
        )
    elif where == "account_stamp":
        account = args["risk_inputs"].account
        account = account.model_copy(
            update={
                "positions_stamp": account.positions_stamp.model_copy(
                    update={"observed_at": bad}
                )
            }
        )
        args["risk_inputs"] = args["risk_inputs"].model_copy(
            update={"account": account}
        )
    elif where == "portfolio_policy":
        policy = args["policy"]
        args["policy"] = policy.model_copy(
            update={
                "portfolio": policy.portfolio.model_copy(
                    update={"drawdown_window_started_at": bad}
                )
            }
        )
    elif where == "intent":
        args["intent"] = args["intent"].model_copy(update={"created_at": bad})
    elif where == "quote":
        args["quote"] = args["quote"].model_copy(
            update={
                "quote": args["quote"].quote.model_copy(update={"observed_at": bad})
            }
        )
    elif where == "reference":
        args["reference"] = args["reference"].model_copy(update={"received_at": bad})
    elif where == "market":
        market.received_at = bad
    else:
        run = run.model_copy(
            update={"result": run.result.model_copy(update={"evaluated_at": bad})}
        )
    with pytest.raises(ValueError):
        if where == "verification_run":
            verify_history_pre_evidence(run, market, **args)
        else:
            evaluate_history_pre_evidence(market, **args)
    assert callbacks == []


@pytest.mark.parametrize(
    "where", ("policy_field", "risk_field", "market", "quote", "reference", "run_field")
)
def test_unknown_object_class_property_cannot_execute_in_preflight(legacy, where):
    source, args, run = legacy
    args = dict(args)
    callbacks = []

    class Hostile:
        @property
        def __class__(self):
            callbacks.append("class")
            raise AssertionError("class callback reached")

    opaque = Hostile()
    market = source.market
    if where == "policy_field":
        args["policy"] = args["policy"].model_copy(update={"economics": opaque})
    elif where == "risk_field":
        args["risk_inputs"] = args["risk_inputs"].model_copy(
            update={"instrument": opaque}
        )
    elif where == "market":
        market = opaque
    elif where in {"quote", "reference"}:
        args[where] = opaque
    else:
        run = run.model_copy(update={"economics": opaque})
    with pytest.raises(ValueError):
        if where == "run_field":
            verify_history_pre_evidence(run, market, **args)
        else:
            evaluate_history_pre_evidence(market, **args)
    assert callbacks == []


def test_engine_prefix_fallback_shares_the_whole_input_budget(legacy):
    _source, args, _run = legacy
    budget = [3]
    with pytest.raises(ValueError, match="bound"):
        engine._preflight(args["policy"].prefix, depth=2, budget=budget)
    assert budget[0] < 0


@pytest.mark.parametrize(
    "where", ("risk_scalar", "prefix_scalar", "time", "nested_time")
)
def test_hostile_metaclass_equality_or_hash_never_runs(legacy, where):
    source, args, _run = legacy
    callbacks = []

    class HostileMeta(type):
        def __eq__(cls, other):
            callbacks.append("eq")
            raise AssertionError("metaclass equality callback reached")

        def __hash__(cls):
            callbacks.append("hash")
            raise AssertionError("metaclass hash callback reached")

    class Hostile(metaclass=HostileMeta):
        pass

    class HostileTimezone(tzinfo, metaclass=HostileMeta):
        def utcoffset(self, dt):
            callbacks.append("timezone")
            raise AssertionError("timezone callback reached")

    args = dict(args)
    if where == "risk_scalar":
        args["risk_inputs"] = args["risk_inputs"].model_copy(
            update={"requested_contracts": Hostile()}
        )
    elif where == "prefix_scalar":
        policy = args["policy"]
        args["policy"] = policy.model_copy(
            update={
                "prefix": policy.prefix.model_copy(update={"minimum_score": Hostile()})
            }
        )
    else:
        bad = source.evaluated_at.replace(tzinfo=HostileTimezone())
        if where == "time":
            args["evaluated_at"] = bad
        else:
            args["risk_inputs"] = args["risk_inputs"].model_copy(
                update={
                    "instrument": args["risk_inputs"].instrument.model_copy(
                        update={"observed_at": bad}
                    )
                }
            )
    with pytest.raises(ValueError):
        evaluate_history_pre_evidence(source.market, **args)
    assert callbacks == []
