"""Real synthetic-source R1--R4 computation, no fake passing evaluators.

The only replaced dependency is the synthetic G12 storage contract. Neither
that receipt nor the fictional OHLC/WS/Demo-account envelopes authenticate IO.
"""

import ast
import asyncio
import inspect
import json
from datetime import timedelta, timezone
from decimal import Context, Decimal, Inexact, localcontext
from pathlib import Path

import pytest
from pydantic import create_model

from app.trade_evidence import gates
from app.trade_qualification import recheck as module
from app.trade_qualification.models import QualificationGate
from app.trade_qualification.recheck import (
    RecordedRecheckAssessment,
    copy_recorded_recheck,
    evaluate_recorded_recheck,
    verify_recorded_recheck,
)
from app.trade_qualification.recheck_models import freeze_recheck_origin
from app.trade_qualification.service import _plain
from tests.unit.qualification_recheck_fixtures import recheck_source
from tests.unit.test_qualification_evidence_gate import Clock, synthetic_publisher
from tests.unit.test_qualification_quote_collector import Clock as QuoteClock
from tests.unit.test_qualification_quote_collector import capture
from tests.unit.test_qualification_recheck_models import (
    origin as origin,  # noqa: PLC0414
)
from tests.unit.test_qualification_recheck_models import (
    source as source,  # noqa: PLC0414
)

D = Decimal


def inputs(source, origin):
    return {
        "origin": origin,
        "original_inputs": dict(source.original_inputs),
        "quote": source.latest_source.quote,
        "reference": source.latest_source.reference,
        "current_risk_inputs": source.current_risk_inputs,
        "consumed_event_keys": frozenset(),
        "observed_at": source.latest_source.evaluated_at,
    }


def evaluate(source, origin, *, current_market=None, original_market=None, **changes):
    return evaluate_recorded_recheck(
        source.original_source.market if original_market is None else original_market,
        source.latest_source.market if current_market is None else current_market,
        **{**inputs(source, origin), **changes},
    )


@pytest.fixture(scope="module")
def result(source, origin):
    return evaluate(source, origin)


def forbid_after(monkeypatch, first):
    names = (
        "evaluate_current_conditions",
        "evaluate_continuation",
        "evaluate_timing",
        "evaluate_location",
        "evaluate_fixed_protection",
        "evaluate_current_risk",
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("later evaluator ran after a failed check")

    for name in names[names.index(first) :]:
        monkeypatch.setattr(module, name, forbidden)


def recapture(source, *, barrier=None, edit=None):
    """Changed quotes traverse actual bounded raw-response capture validation."""
    previous = source.latest_source.quote
    start = previous.quote.request_started_at
    raw = {item.role: json.loads(item.response_body) for item in previous.provenance}

    def body(role, unused):
        packet = raw[role]
        if edit is not None:
            edit(role, packet["data"][0])
        return packet

    return asyncio.run(
        capture(
            change=body,
            clock=QuoteClock(
                tuple(start + timedelta(milliseconds=i) for i in range(10))
            ),
            report=previous.quote.report_id,
            instrument=previous.quote.instrument_id,
            barrier=source.barrier_completed_at if barrier is None else barrier,
        )
    )[0]


def test_actual_long_short_chain_preserves_original_candidate(source, origin, result):
    assert origin.publication_completed_at == source.barrier_completed_at
    assert result.computational_checks_passed is True
    assert tuple(check.step for check in result.checks) == module.STEPS
    assert all(check.passed for check in result.checks)
    assert result.current_conditions.passed
    assert len(result.current_conditions.gates) == 4
    assert result.continuation.passed and result.fixed_protection.passed
    assert result.current_risk.economics.passed
    assert result.current_risk.candidate_risk.passed
    assert result.current_risk.execution_risk.passed
    assert (
        result.fixed_protection.entry,
        result.fixed_protection.stop_loss,
        result.fixed_protection.take_profit,
    ) == (
        origin.candidate.candidate_entry,
        origin.candidate.stop_loss,
        origin.candidate.take_profit,
    )
    assert result.timing.event_key == origin.original_event_key
    assert result.timing.latest_valid_entry_time == origin.deadline
    assert result.origin.candidate == origin.candidate
    assert len(result.origin.candidate.gates) == 12
    assert result.origin.candidate.qualified is False
    assert all(getattr(result, field) is False for field in module._FALSE_FLAGS)
    assert result.intrabar_status == "unknown"
    assert all(
        frame.intrabar_status == "unknown" for frame in result.continuation.coverage
    )
    assert all(
        frame.blind_to == result.observed_at for frame in result.continuation.coverage
    )


def test_full_replay_and_json_record_are_not_runtime_permission(source, origin, result):
    actual = verify_recorded_recheck(
        result,
        source.original_source.market,
        source.latest_source.market,
        **inputs(source, origin),
    )
    decoded = RecordedRecheckAssessment.model_validate_json(
        result.model_dump_json(round_trip=True),
        strict=True,
    )
    assert actual == decoded == result
    assert decoded.evaluation_sha256 == result.evaluation_sha256
    assert decoded.record_kind == "offline_recorded_recheck_not_runtime_permission"
    with pytest.raises(ValueError):
        result.runtime_admissible = True


@pytest.mark.parametrize("direction", ("long", "short"))
def test_next_boundary_continues_event_but_cannot_skip_new_target(
    direction, monkeypatch
):
    source = recheck_source(direction, scenario="next_boundary")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(gates, "publish_evidence", synthetic_publisher())
        evidence = gates.publish_qualification_evidence(
            Path.cwd(),
            source.original_source.market,
            run=source.original_run,
            **source.original_inputs,
            purpose="synthetic_test",
            clock=Clock(source.original_source.evaluated_at),
        )
    origin = freeze_recheck_origin(evidence)
    assert origin.publication_completed_at == source.barrier_completed_at
    forbid_after(monkeypatch, "evaluate_current_risk")
    result = evaluate(source, origin)
    assert result.current_conditions.passed and result.continuation.passed
    assert result.timing.timing_valid and result.location.passed
    assert result.code == "fixed_target_intervening_barrier"
    assert result.checks[-1].step == "fixed_protection"
    assert result.current_risk is None
    assert not result.computational_checks_passed
    counts = {
        frame.timeframe: frame.appended_count for frame in result.continuation.coverage
    }
    assert counts == {"4H": 0, "1H": 0, "15m": 1, "5m": 1}


def test_original_replay_failure_does_not_touch_any_new_input(
    source, origin, monkeypatch
):
    market = source.original_source.market.model_copy(deep=True)
    market.candles["4H"][0].volume_quote += 1
    forbid_after(monkeypatch, "evaluate_current_conditions")
    result = evaluate(
        source,
        origin,
        original_market=market,
        current_market=object(),
        quote=object(),
        reference=object(),
        current_risk_inputs=object(),
        consumed_event_keys=object(),
    )
    assert result.code == "original_replay_failed"
    assert len(result.checks) == 1
    assert result.quote_bundle_sha256 is None


@pytest.mark.parametrize(
    "when", ("publication", "completed", "deadline", "after_deadline")
)
def test_exact_clock_boundaries_stop_before_current_g1(
    source, origin, monkeypatch, when
):
    at = {
        "publication": origin.publication_completed_at,
        "completed": source.latest_source.quote.completed_at,
        "deadline": origin.deadline,
        "after_deadline": origin.deadline + timedelta(microseconds=1),
    }[when]
    forbid_after(monkeypatch, "evaluate_current_conditions")
    result = evaluate(source, origin, observed_at=at)
    assert result.checks[-1].step == "capture_barrier"
    assert result.code == (
        "decision_not_after_capture_completion"
        if when == "completed"
        else "outside_original_recheck_window"
    )


def test_valid_collector_with_wrong_recorded_barrier_is_rejected(
    source, origin, monkeypatch
):
    quote = recapture(
        source, barrier=origin.publication_completed_at - timedelta(microseconds=1)
    )
    assert quote.barrier_completed_at != origin.publication_completed_at
    forbid_after(monkeypatch, "evaluate_current_conditions")
    result = evaluate(source, origin, quote=quote)
    assert result.code == "publication_barrier_mismatch"


@pytest.mark.parametrize("field", ("source_time", "received_at"))
def test_reference_needs_new_observation_not_rewrapped_receipt(
    source, origin, monkeypatch, field
):
    ref = source.latest_source.reference.model_copy(
        update={field: origin.publication_completed_at}
    )
    forbid_after(monkeypatch, "evaluate_current_conditions")
    result = evaluate(source, origin, reference=ref)
    assert result.code in {"reference_not_after_publication", "new_capture_invalid"}
    assert result.checks[-1].step == "capture_barrier"


def test_market_receipt_equal_barrier_is_not_new_capture(source, origin, monkeypatch):
    market = source.latest_source.market.model_copy(
        update={"received_at": origin.publication_completed_at}
    )
    forbid_after(monkeypatch, "evaluate_current_conditions")
    assert (
        evaluate(source, origin, current_market=market).code
        == "market_capture_not_after_publication"
    )


@pytest.mark.parametrize("field", ("quote", "reference"))
def test_missing_capture_never_borrows_original(source, origin, monkeypatch, field):
    forbid_after(monkeypatch, "evaluate_current_conditions")
    assert evaluate(source, origin, **{field: None}).code == "new_capture_missing"


def test_future_confirmed_bar_is_rejected_by_actual_current_g1(
    source, origin, monkeypatch
):
    market = source.latest_source.market.model_copy(deep=True)
    market.candles["5m"][-1].timestamp += timedelta(minutes=5)
    forbid_after(monkeypatch, "evaluate_continuation")
    result = evaluate(source, origin, current_market=market)
    assert result.checks[-1].step == "current_conditions"
    assert not result.current_conditions.data_result.passed
    assert result.continuation is None


def test_rewritten_confirmed_overlap_cannot_be_hidden_by_current_analysis(
    source, origin, monkeypatch
):
    market = source.latest_source.market.model_copy(deep=True)
    market.candles["4H"][0].volume_quote += 1
    forbid_after(monkeypatch, "evaluate_timing")
    result = evaluate(source, origin, current_market=market)
    assert result.current_conditions.passed
    assert not result.continuation.passed
    assert result.checks[-1].step == "continuation"
    assert result.timing is None


def test_consumed_original_event_stops_before_location(source, origin, monkeypatch):
    forbid_after(monkeypatch, "evaluate_location")
    result = evaluate(
        source, origin, consumed_event_keys=frozenset({origin.original_event_key})
    )
    assert result.continuation.passed
    assert result.checks[-1].step == "timing"
    assert result.timing.action == "CANCEL"
    assert not result.computational_checks_passed


@pytest.mark.parametrize("ledger", (set(), (), None, frozenset({"not-a-digest"})))
def test_bounded_exact_ledger_is_required_without_consuming_iterator(
    source, origin, monkeypatch, ledger
):
    forbid_after(monkeypatch, "evaluate_timing")
    assert (
        evaluate(source, origin, consumed_event_keys=ledger).code
        == "consumed_event_ledger_invalid"
    )


@pytest.mark.parametrize("field", ("account", "authority", "instrument"))
def test_missing_account_evidence_reaches_real_current_risk_denial(
    source, origin, field
):
    risk = source.current_risk_inputs.model_copy(update={field: None})
    result = evaluate(source, origin, current_risk_inputs=risk)
    assert all(check.passed for check in result.checks[:-1])
    assert result.code == "risk_authority_denied"
    assert result.current_risk.failure_stage == "candidate_risk"
    assert result.current_risk.execution_risk is None


@pytest.mark.parametrize(
    "role",
    (
        "balance_stamp",
        "positions_stamp",
        "history_stamp",
        "reservations_stamp",
        "authority",
        "instrument",
    ),
)
@pytest.mark.parametrize("clock", ("observed_at", "received_at"))
def test_every_account_source_and_receipt_must_be_after_barrier(
    source, origin, monkeypatch, role, clock
):
    risk = source.current_risk_inputs
    if role == "instrument":
        risk = risk.model_copy(
            update={
                role: risk.instrument.model_copy(
                    update={clock: origin.publication_completed_at}
                )
            }
        )
    elif role == "authority":
        risk = risk.model_copy(
            update={
                role: risk.authority.model_copy(
                    update={
                        "stamp": risk.authority.stamp.model_copy(
                            update={clock: origin.publication_completed_at}
                        )
                    }
                )
            }
        )
    else:
        stamp = getattr(risk.account, role).model_copy(
            update={clock: origin.publication_completed_at}
        )
        risk = risk.model_copy(
            update={"account": risk.account.model_copy(update={role: stamp})}
        )
    forbid_after(monkeypatch, "evaluate_current_risk")
    result = evaluate(source, origin, current_risk_inputs=risk)
    assert result.code == "current_account_claim_not_after_publication"
    assert result.current_risk is None and result.fixed_protection.passed


@pytest.mark.parametrize("change", ("size", "leverage", "armed", "live", "drawdown"))
def test_current_risk_cannot_resize_or_ignore_guard_and_drawdown(
    source, origin, change
):
    risk = source.current_risk_inputs
    if change == "size":
        risk = risk.model_copy(
            update={"requested_contracts": risk.requested_contracts * 2}
        )
    elif change == "leverage":
        risk = risk.model_copy(
            update={"requested_leverage": risk.requested_leverage + 1}
        )
    elif change in {"armed", "live"}:
        field, value = ("armed", False) if change == "armed" else ("live_trading", True)
        risk = risk.model_copy(
            update={"authority": risk.authority.model_copy(update={field: value})}
        )
    else:
        risk = risk.model_copy(
            update={
                "account": risk.account.model_copy(
                    update={"peak_equity": risk.account.equity * 2}
                )
            }
        )
    result = evaluate(source, origin, current_risk_inputs=risk)
    assert all(check.passed for check in result.checks[:-1])
    assert not result.computational_checks_passed
    assert result.code in {"current_risk_input_invalid", "risk_authority_denied"}
    assert result.origin.candidate == origin.candidate


@pytest.mark.parametrize("flag", module._FALSE_FLAGS)
def test_no_record_can_gain_runtime_or_authentication_authority(result, flag):
    with pytest.raises(ValueError):
        copy_recorded_recheck(result.model_copy(update={flag: True}))


@pytest.mark.parametrize("value", (0, 1, "false", None))
def test_no_boolean_coercion_for_authority(result, value):
    with pytest.raises(ValueError):
        copy_recorded_recheck(result.model_copy(update={"runtime_admissible": value}))


@pytest.mark.parametrize(
    "field",
    (
        "origin",
        "current_conditions",
        "continuation",
        "timing",
        "location",
        "fixed_protection",
        "current_risk",
    ),
)
def test_nested_hidden_fields_and_subclasses_rejected_before_serialization(
    result, field
):
    nested = getattr(result, field)
    hidden = nested.model_copy(update={"hidden_authority": True})
    with pytest.raises(ValueError):
        copy_recorded_recheck(result.model_copy(update={field: hidden}))
    subclass = create_model("UntrustedSubresult", __base__=type(nested))
    derived = subclass.model_construct(**nested.__dict__)
    with pytest.raises(ValueError):
        copy_recorded_recheck(result.model_copy(update={field: derived}))


def test_result_rejects_lazy_checks_without_iteration(result):
    def never():
        raise AssertionError("unbounded iterator consumed")
        yield  # pragma: no cover

    with pytest.raises(ValueError):
        copy_recorded_recheck(result.model_copy(update={"checks": never()}))
    with pytest.raises(ValueError):
        RecordedRecheckAssessment.model_validate(
            {**_plain(result), "checks": never()}, strict=True
        )


@pytest.mark.parametrize(
    "field",
    (
        "computational_checks_passed",
        "quote_sha256",
        "consumed_event_keys_sha256",
        "current_risk_inputs_sha256",
    ),
)
def test_tampered_record_status_and_pins_cannot_replay(source, origin, result, field):
    value = False if field == "computational_checks_passed" else "0" * 64
    dirty = result.model_copy(update={field: value})
    with pytest.raises(ValueError):
        verify_recorded_recheck(
            dirty,
            source.original_source.market,
            source.latest_source.market,
            **inputs(source, origin),
        )


def test_deterministic_clock_and_decimal_context(source, origin, result):
    tz = timezone(timedelta(hours=8))
    with localcontext(Context(prec=6, traps=[Inexact])):
        actual = evaluate(
            source, origin, observed_at=source.latest_source.evaluated_at.astimezone(tz)
        )
    assert actual == result
    assert actual.evaluation_sha256 == result.evaluation_sha256


def test_no_new_event_zone_bracket_execution_or_thirteenth_gate():
    tree = ast.parse(inspect.getsource(module))
    forbidden = {
        "extract_trigger",
        "evaluate_qualification_prefix",
        "evaluate_pre_evidence",
        "build_entry_zone",
        "select_structural_protection",
        "get_settings",
        "now",
        "place_order",
        "reserve",
        "publish_qualification_evidence",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else getattr(node.func, "attr", "")
            )
            assert name not in forbidden
    assert (
        len(QualificationGate) == 13
    )  # existing domain final gate, not generated here
    assert not any(
        isinstance(node, ast.Name) and node.id == "QualificationGate"
        for node in ast.walk(tree)
    )
