"""Synthetic source mathematics and memory publication; zero order authority."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

from app.strategies.regime import route_regime
from app.trade_evidence.models import EvidenceQualification
from app.trade_evidence.storage import FILE_NAMES
from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification import history_engine as h
from app.trade_qualification import reservations as r
from app.trade_qualification import submission_intent as si
from app.trade_qualification.range_policy import (
    range_current_permission,
    replay_range_permission,
)
from app.trade_qualification.recheck import (
    RecordedRecheckAssessment,
)
from tests.unit.qualification_execution_binding_fixtures import consumed_receipt
from tests.unit.qualification_range_fixtures import range_source, v4_inputs
from tests.unit.qualification_range_v5_fixtures import (
    range_v5_ledger_fixture,
    range_v5_source,
    v5_inputs,
)
from tests.unit.test_range_boundary_v4 import input_document


def test_v5_evaluator_rejects_wrong_policy_with_v5_specific_error():
    with pytest.raises(ValueError, match="exact_history_v5_policy_required"):
        h.evaluate_history_pre_evidence_v5(None, policy=object())


@pytest.fixture(scope="module", params=("long", "short"))
def facts(request):
    source = range_v5_source(request.param)
    inputs = v5_inputs(source)
    run = h.evaluate_history_pre_evidence_v5(source.market, **inputs)
    assert run.pre_evidence_complete, run.result.fail_codes
    return source, inputs, run


@pytest.fixture(scope="module", params=("long", "short"))
def chain(request):
    with pytest.MonkeyPatch.context() as mp:
        fixture, binding, content = range_v5_ledger_fixture(request.param, mp)
    return fixture, binding, content


def test_v5_raw_replay_retains_alignment_diagnostic_and_all_legacy_v4_denials(facts):
    source, inputs, run = facts
    assert h.verify_history_pre_evidence_v5(run, source.market, **inputs) == run
    audit = json.loads(run.protection_audit_json)
    assert audit["retained_analysis_blockers"] == ["multi_timeframe_not_aligned"]
    assert audit["alignment_policy"] == "ctcc-neutral-range-protection-v1"
    assert audit["range_permission_sha256"]
    assert all(gate.passed for gate in run.result.gates)
    assert not run.execution_authority
    old = h.evaluate_history_pre_evidence_v4(source.market, **v4_inputs(source))
    assert old.result.fail_codes == ("source_data_blockers",)
    narrow = range_source(source.direction)
    denied = h.evaluate_history_pre_evidence_v5(narrow.market, **v5_inputs(narrow))
    assert denied.result.fail_codes == ("no_valid_structural_bracket",)


@pytest.mark.parametrize(
    "field", ("range_anchor_policy", "range_protection_policy", "contract_version")
)
@pytest.mark.parametrize("node", ("prefix", "result"))
def test_required_profile_markers_never_default_or_upgrade(facts, field, node):
    _, args, run = facts
    if node == "prefix":
        value = json.loads(input_document(args))
        value["policy"]["prefix"].pop(field)
        with pytest.raises(ValueError):
            si._original_inputs_document(si._json(value))
    else:
        value = run.result.model_dump(mode="json", round_trip=True)
        value.pop(field)
        with pytest.raises(ValueError):
            TypeAdapter(EvidenceQualification).validate_json(
                si._json(value), strict=True
            )


@pytest.mark.parametrize("version", ("v2", "v3", "v4", "unknown"))
def test_mixed_original_input_version_cannot_upgrade(facts, version):
    _, args, _ = facts
    value = json.loads(input_document(args))
    value["policy"]["contract_version"] = "ctcc-history-pre-evidence-" + version
    with pytest.raises(ValueError):
        si._original_inputs_document(si._json(value))


@pytest.mark.parametrize(
    "mutation",
    (
        "nonalignment",
        "atr_missing",
        "macd_missing",
        "rsi_missing",
        "math_missing",
        "htf_trend",
        "high_vol",
        "bos",
        "bounds",
    ),
)
def test_only_complete_neutral_range_operands_pass_permission(facts, mutation):
    from app.domain.analysis import MultiTimeframeAnalysis

    source, _, run = facts
    raw = json.loads(run.prefix.data_result.source_json)["analysis"]
    analysis = MultiTimeframeAnalysis.model_validate_json(json.dumps(raw), strict=True)
    changed = analysis.model_copy(deep=True)
    views = changed.timeframe_analyses
    if mutation == "nonalignment":
        changed.blockers.append("synthetic_non_alignment_veto")
    elif mutation == "math_missing":
        changed.mathematical_core = None
    elif mutation in ("atr_missing", "macd_missing", "rsi_missing"):
        timeframe, name = {
            "atr_missing": ("15m", "atr14"),
            "macd_missing": ("5m", "macd_histogram"),
            "rsi_missing": ("5m", "rsi14"),
        }[mutation]
        setattr(views[timeframe].indicators, name, None)
    elif mutation == "htf_trend":
        views["4H"].structure.trend = "bullish"
    elif mutation == "high_vol":
        views["15m"].volatility = "high"
    elif mutation == "bos":
        views["15m"].structure.bos = "bullish"
    else:
        views["15m"].structure.resistance_levels = []
    assert not range_current_permission(
        changed, source.market, route_regime(changed), source.direction
    )
    with pytest.raises(ValueError):
        replay_range_permission(source.market, changed, run.prefix.detection)


def test_g12_memory_readback_and_r7_wire_roundtrip_are_exact(chain):
    fixture, binding, content = chain
    req = fixture.request
    files = content[req.origin.candidate.report_id]
    assert set(files) == set(FILE_NAMES)
    for published in req.origin.evidence.receipt.files:
        assert hashlib.sha256(files[published.name]).hexdigest() == published.sha256
    actual = si._document(binding.recheck_json, RecordedRecheckAssessment)
    assert actual.computational_checks_passed
    assert (
        actual.current_conditions.contract_version
        == "ctcc-history-current-conditions-v5"
    )
    assert (
        actual.fixed_protection.contract_version == "ctcc-history-fixed-protection-v5"
    )
    assert not any(
        (
            actual.runtime_admissible,
            actual.execution_authority,
            actual.source_authenticity_verified,
        )
    )
    assert r.decode_reservation_request(r.canonical(req)) == req
    assert req.contract_version == "ctcc-reservation-request-v3"
    assert req.replay_binding.account_packet_sha256 == binding.account_packet_sha256
    assert req.replay_binding.account_plan_sha256 == binding.account_plan_sha256
    current = r.replay_range_reservation(req, observed_at=fixture.now)
    assert current == actual
    assert current.continuation.original_event_key == req.origin.original_event_key


def test_legacy_v2_request_remains_readable_but_control_bound_reserve_denies(chain):
    fixture, _, _ = chain
    request = fixture.request
    old_binding = r.ReservationReplayBindingV2(
        contract_version="ctcc-reservation-replay-v2",
        **{
            name: getattr(request.replay_binding, name)
            for name in r.ReservationReplayBindingV2.model_fields
            if name not in r.LedgerModel.model_fields and name != "contract_version"
        },
    )
    old_request = r.ReservationRequestV2(
        **{
            name: getattr(request, name)
            for name in (
                "scope",
                "origin",
                "quote",
                "risk_inputs",
                "expected_account_revision",
                "expected_ledger_revision",
            )
        },
        contract_version="ctcc-reservation-request-v2",
        replay_binding=old_binding,
    )
    assert r.decode_reservation_request(r.canonical(old_request)) == old_request
    with pytest.raises(
        r.QualificationLedgerError,
        match="bound_control_account_session_binding_missing",
    ):
        bound._guard_request_session(old_request, SimpleNamespace())


@pytest.mark.parametrize(
    "field",
    (
        "original_market_json",
        "current_market_json",
        "original_inputs_json",
        "quote_json",
        "reference_json",
        "recheck_json",
    ),
)
def test_boolean_or_incomplete_replay_denied_before_caps(chain, field, monkeypatch):
    fixture, _, _ = chain
    altered = fixture.request.model_copy(
        update={
            "replay_binding": fixture.request.replay_binding.model_copy(
                update={field: '{"passed":true}'}
            )
        }
    )
    monkeypatch.setattr(
        r,
        "evaluate_current_risk",
        lambda *a, **k: pytest.fail("caps reached before raw replay"),
    )
    with pytest.raises(r.QualificationLedgerError, match="ledger_range_replay_denied"):
        r.prepare_reservation(altered, fixture.claims, (), observed_at=fixture.now)


def test_bare_v5_request_cannot_reserve_or_create_legacy_intent(chain):
    fixture, _, _ = chain
    values = fixture.request.model_dump(mode="python", round_trip=True)
    values.pop("contract_version")
    values.pop("replay_binding")
    bare = r.ReservationRequest.model_validate(values, strict=True)
    with pytest.raises(
        r.QualificationLedgerError, match="ledger_range_replay_binding_required"
    ):
        r.prepare_reservation(bare, fixture.claims, (), observed_at=fixture.now)
    receipt = consumed_receipt(fixture)
    with pytest.raises(
        r.QualificationLedgerError, match="submit_range_execution_binding_required"
    ):
        si.build_submission_intent(fixture.request, receipt)


@pytest.mark.parametrize(
    "mutation",
    (
        "marker_missing",
        "marker_mixed",
        "profile_missing",
        "duplicate_events",
        "expiry",
        "time_reversed",
    ),
)
def test_new_request_and_time_bindings_fail_closed(chain, mutation):
    fixture, _, _ = chain
    request = fixture.request
    now = fixture.now
    if mutation in ("marker_missing", "marker_mixed", "profile_missing"):
        raw = json.loads(r.canonical(request))
        if mutation == "marker_missing":
            raw.pop("contract_version")
        elif mutation == "marker_mixed":
            raw["contract_version"] = "ctcc-reservation-request-v1"
        else:
            raw["replay_binding"].pop("contract_version")
        with pytest.raises(r.QualificationLedgerError):
            r.decode_reservation_request(si._json(raw))
        return
    if mutation == "duplicate_events":
        request = request.model_copy(
            update={
                "replay_binding": request.replay_binding.model_copy(
                    update={"consumed_event_keys": ("a" * 64, "a" * 64)}
                )
            }
        )
    elif mutation == "expiry":
        now = request.origin.deadline
    else:
        now -= timedelta(microseconds=1)
    with pytest.raises(r.QualificationLedgerError):
        r.prepare_reservation(request, fixture.claims, (), observed_at=now)


def test_fixed_geometry_no_rediscovery_and_current_range_loss_rejected(
    chain, monkeypatch
):
    from app.trade_qualification import events

    fixture, _, _ = chain
    current = r.replay_range_reservation(fixture.request, observed_at=fixture.now)
    original = fixture.request.origin.candidate
    assert (
        current.fixed_protection.entry,
        current.fixed_protection.stop_loss,
        current.fixed_protection.take_profit,
    ) == (original.candidate_entry, original.stop_loss, original.take_profit)
    # The dedicated current-conditions evaluator receives original identity and
    # never asks the event extractor to identify a replacement event.
    from app.trade_qualification.current_conditions import (
        evaluate_history_current_conditions_v5,
    )

    latest = fixture.source.latest_source
    monkeypatch.setattr(
        events,
        "extract_trigger",
        lambda *a, **k: pytest.fail("new current event requested"),
    )
    result = evaluate_history_current_conditions_v5(
        latest.market,
        origin=fixture.request.origin,
        quote=latest.quote,
        reference=latest.reference,
        observed_at=fixture.now,
    )
    assert result.passed
    changed = latest.market.model_copy(deep=True)
    changed.candles["4H"] = []
    denied = evaluate_history_current_conditions_v5(
        changed,
        origin=fixture.request.origin,
        quote=latest.quote,
        reference=latest.reference,
        observed_at=fixture.now,
    )
    assert not denied.passed


def test_sampled_risk_and_complete_v2_intent_replay_no_order_authority(chain):
    fixture, binding, _ = chain
    receipt = consumed_receipt(fixture)
    record = si.build_submission_intent(
        fixture.request, receipt, execution_binding=binding
    )
    replayed, consumed = si.replay_submission_intent(
        record.canonical_json, fixture.request, expected_sha256=record.sha256
    )
    assert replayed == record and consumed == receipt
    body = json.loads(record.canonical_json)
    order = body["exchange_request"]["body"]
    assert order["ordType"] == "fok" and order["tdMode"] == "isolated"
    assert Decimal(order["px"]) == receipt.coverage.execution.entry
    assert not record.execution_authority and not record.order_retry_authority
    assert body["all_fill_prices_covered"] is False
    assert body["source_authenticity_verified"] is False
    assert receipt.coverage.risk_amount > 0


def test_current_source_trend_change_denies_range_with_complete_candles(
    chain, monkeypatch
):
    from app.trade_qualification import events
    from app.trade_qualification.current_conditions import (
        evaluate_history_current_conditions_v5,
    )

    fixture, _, _ = chain
    latest = fixture.source.latest_source
    changed = latest.market.model_copy(deep=True)
    # Retain every timestamp and every candle. Only source OHLC changes: the
    # formerly neutral 4H background becomes a fully observed bullish trend.
    changed.candles["4H"] = [
        row.model_copy(
            update={
                "open": Decimal(80) + Decimal(index) / 10,
                "close": Decimal(80) + Decimal(index) / 10 + Decimal("0.05"),
                "high": Decimal(80) + Decimal(index) / 10 + Decimal("0.1"),
                "low": Decimal(80) + Decimal(index) / 10 - Decimal("0.05"),
            }
        )
        for index, row in enumerate(changed.candles["4H"])
    ]
    monkeypatch.setattr(
        events,
        "extract_trigger",
        lambda *a, **k: pytest.fail("new current event requested"),
    )
    denied = evaluate_history_current_conditions_v5(
        changed,
        origin=fixture.request.origin,
        quote=latest.quote,
        reference=latest.reference,
        observed_at=fixture.now,
    )
    assert not denied.passed
    assert denied.result.gates[0].passed
    assert denied.result.gates[1].code == "regime_strategy_not_allowed"
    assert denied.original_event_key == fixture.request.origin.original_event_key
    assert denied.intent == fixture.request.origin.evidence.pre_evidence.prefix.intent


def test_revised_account_or_substituted_execution_binding_denied(chain):
    fixture, binding, _ = chain
    receipt = consumed_receipt(fixture)
    with pytest.raises(
        r.QualificationLedgerError, match="submit_range_account_revision_changed"
    ):
        si.build_submission_intent(
            fixture.request,
            receipt.model_copy(update={"account_revision": 2}),
            execution_binding=binding,
        )
    changed = binding.model_copy(update={"reference_json": '{"passed":true}'})
    with pytest.raises(
        r.QualificationLedgerError, match="submit_range_replay_binding_changed"
    ):
        si.build_submission_intent(fixture.request, receipt, execution_binding=changed)


def test_new_replay_payload_foreign_callbacks_never_run(chain):
    fixture, _, _ = chain
    calls = []

    class Opaque:
        @property
        def __class__(self):
            calls.append("class")
            raise AssertionError("callback")

        def __str__(self):
            calls.append("str")
            raise AssertionError("callback")

    changed = fixture.request.model_copy(
        update={
            "replay_binding": fixture.request.replay_binding.model_copy(
                update={"original_market_json": Opaque()}
            )
        }
    )
    with pytest.raises(r.QualificationLedgerError, match="submit_intent_input_invalid"):
        r.prepare_reservation(changed, fixture.claims, (), observed_at=fixture.now)
    assert calls == []


def test_full_current_replay_rejects_missing_closed_tail(chain):
    from app.domain.market import MarketSnapshot

    fixture, binding, _ = chain
    after = si._document(binding.current_market_json, MarketSnapshot)
    after.candles["4H"] = after.candles["4H"][:-1]
    raw = si._json(after.model_dump(mode="json", round_trip=True))
    changed = fixture.request.model_copy(
        update={
            "replay_binding": fixture.request.replay_binding.model_copy(
                update={"current_market_json": raw}
            )
        }
    )
    with pytest.raises(r.QualificationLedgerError, match="ledger_range_replay_denied"):
        r.prepare_reservation(changed, fixture.claims, (), observed_at=fixture.now)
