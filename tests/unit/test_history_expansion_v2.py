"""Synthetic source replay plus native evidence IO, never real trading evidence."""

import asyncio
import hashlib
import json
from datetime import timedelta
from decimal import Decimal

import pytest

from app.trade_evidence.gates import (
    EvidenceGateError,
    HistoryEvidenceGateRunV2,
    publish_qualification_evidence,
)
from app.trade_evidence.models import EvidenceSnapshot
from app.trade_evidence.service import validate_snapshot
from app.trade_evidence.storage import FILE_NAMES
from app.trade_qualification.data import WSReferenceObservation
from app.trade_qualification.history_engine import (
    HistoryPreEvidencePolicyV2,
    evaluate_history_pre_evidence,
    evaluate_history_pre_evidence_v2,
    verify_history_pre_evidence_v2,
)
from app.trade_qualification.history_prefix import (
    HistoryEntryQualificationResultV2,
    HistoryQualificationPrefixPolicyV2,
)
from app.trade_qualification.models import EntryQualificationResult
from app.trade_qualification.one_shot import _original
from app.trade_qualification.recheck import (
    RecordedRecheckAssessment,
    evaluate_recorded_recheck,
    verify_recorded_recheck,
)
from app.trade_qualification.recheck_models import RecheckOrigin, freeze_recheck_origin
from tests.unit.history_qualification_fixtures import history_inputs, history_source
from tests.unit.qualification_recheck_fixtures import _current_risk, _new_market
from tests.unit.test_qualification_evidence_gate import Clock
from tests.unit.test_qualification_quote_collector import Clock as CaptureClock
from tests.unit.test_qualification_quote_collector import _ms, capture
from tests.unit.test_trade_evidence_pipeline import synthetic_snapshot

D = Decimal


def v2_inputs(source):
    inputs = history_inputs(source)
    original = inputs["policy"]
    prefix = original.prefix.model_dump(round_trip=True)
    prefix.pop("contract_version")
    policy = original.model_dump(round_trip=True)
    policy.pop("contract_version")
    policy["prefix"] = HistoryQualificationPrefixPolicyV2.model_validate(
        prefix, strict=True
    )
    inputs["policy"] = HistoryPreEvidencePolicyV2.model_validate(policy, strict=True)
    return inputs


@pytest.fixture(scope="module", params=("long", "short"))
def expanded(request):
    source = history_source(
        "volatility_expansion", request.param, bracket=True, expansion_entry=True
    )
    inputs = v2_inputs(source)
    run = evaluate_history_pre_evidence_v2(source.market, **inputs)
    assert run.pre_evidence_complete, run.result.fail_codes
    return source, inputs, run


@pytest.fixture(scope="module")
def published(expanded, tmp_path_factory):
    source, inputs, run = expanded
    root = tmp_path_factory.mktemp("history-expansion-v2")
    evidence = publish_qualification_evidence(
        root,
        source.market,
        run=run,
        **inputs,
        purpose="synthetic_test",
        clock=Clock(source.evaluated_at),
    )
    assert evidence.result.gates[-1].passed, evidence.error_detail
    assert type(evidence) is HistoryEvidenceGateRunV2
    return root, evidence, freeze_recheck_origin(evidence)


async def current_inputs(source, original_inputs, origin):
    start = source.evaluated_at + timedelta(seconds=1)
    market, observations = _new_market(source, start, "same_interval")
    assert all(
        row.request_started_at > origin.publication_completed_at for row in observations
    )

    def payload(role, body):
        row = body["data"][0]
        index = {"ticker": 0, "mark": 1, "funding": 2}[role]
        row["ts"] = _ms(start + timedelta(milliseconds=index * 3 - 1))
        if role == "ticker":
            row.update(
                bidPx=str(market.ticker.bid),
                askPx=str(market.ticker.ask),
                bidSz="2",
                askSz="3",
            )
        elif role == "mark":
            row["markPx"] = str(market.mark_price)
        else:
            row.update(
                fundingRate="0",
                fundingTime=_ms(start + timedelta(hours=8)),
                nextFundingTime=_ms(start + timedelta(hours=16)),
            )

    quote, _ = await capture(
        change=payload,
        report=source.report_id,
        instrument=market.instrument_id,
        clock=CaptureClock(tuple(start + timedelta(milliseconds=i) for i in range(10))),
        barrier=origin.publication_completed_at,
    )
    reference = WSReferenceObservation(
        report_id=source.report_id,
        instrument_id=market.instrument_id,
        bid=market.ticker.bid,
        ask=market.ticker.ask,
        source_time=start + timedelta(milliseconds=1),
        received_at=start + timedelta(milliseconds=10),
    )
    return market, {
        "origin": origin,
        "original_inputs": original_inputs,
        "quote": quote,
        "reference": reference,
        "current_risk_inputs": _current_risk(
            original_inputs,
            start + timedelta(milliseconds=10),
            start + timedelta(milliseconds=11),
        ),
        "consumed_event_keys": frozenset(),
        "observed_at": start + timedelta(milliseconds=12),
    }


def test_v2_replays_source_through_all_eleven_gates_and_v1_stays_blocked(expanded):
    source, inputs, run = expanded
    assert verify_history_pre_evidence_v2(run, source.market, **inputs) == run
    assert run.prefix.history_admission.history_verified
    assert (
        run.result.gates[2].measured_values["source_sha256"]
        == run.prefix.data_result.source_sha256
    )
    old = evaluate_history_pre_evidence(source.market, **history_inputs(source))
    assert old.result.fail_codes == ("htf_strategy_permission_denied",)
    assert len(old.result.gates) == 3
    assert run.execution_authority is False
    assert _original(source.market, run, inputs)[1] == run


def test_v2_native_g12_readback_preserves_complete_original_identity(
    expanded, published
):
    source, _, run = expanded
    root, evidence, origin = published
    assert len(evidence.result.gates) == 12
    assert evidence.result.gates[:11] == run.result.gates
    assert origin.original_event_key == run.prefix.timing.event_key
    assert (
        evidence.result.candidate_entry,
        evidence.result.stop_loss,
        evidence.result.take_profit,
    ) == (run.result.candidate_entry, run.result.stop_loss, run.result.take_profit)
    assert {p.name for p in (root / source.report_id).iterdir()} == set(FILE_NAMES)
    for row in evidence.receipt.files:
        raw = (root / source.report_id / row.name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row.sha256
        assert len(raw) == row.size_bytes
    report = json.loads((root / source.report_id / "report.json").read_text())
    assert (
        report["snapshot"]["qualification"]["contract_version"]
        == "ctcc-history-qualification-result-v2"
    )


def test_post_publication_recheck_retains_event_geometry_and_no_authority(
    expanded, published
):
    source, inputs, run = expanded
    _, _, origin = published
    market, current = asyncio.run(current_inputs(source, inputs, origin))
    checked = evaluate_recorded_recheck(source.market, market, **current)
    assert checked.computational_checks_passed, [
        (c.step, c.code) for c in checked.checks
    ]
    assert verify_recorded_recheck(checked, source.market, market, **current) == checked
    assert checked.continuation.original_event_key == origin.original_event_key
    assert checked.timing.latest_valid_entry_time <= origin.deadline
    assert (
        checked.fixed_protection.entry,
        checked.fixed_protection.stop_loss,
        checked.fixed_protection.take_profit,
    ) == (run.result.candidate_entry, run.result.stop_loss, run.result.take_profit)
    assert checked.execution_authority is False
    assert checked.runtime_admissible is False
    assert checked.complete_path_verified is False
    replayed = RecordedRecheckAssessment.model_validate_json(
        checked.model_dump_json(round_trip=True), strict=True
    )
    assert replayed == checked
    assert replayed.evaluation_sha256 == checked.evaluation_sha256
    raw = json.loads(checked.model_dump_json(round_trip=True))
    raw["current_conditions"].pop("contract_version")
    with pytest.raises(ValueError):
        RecordedRecheckAssessment.model_validate_json(json.dumps(raw), strict=True)


@pytest.mark.parametrize("case", ("long", "short", "wait", "cancel"))
def test_legacy_evidence_readback_keeps_exact_type_and_serialization(case):
    snapshot = synthetic_snapshot(case)
    before = snapshot.model_dump_json(round_trip=True)
    replay = EvidenceSnapshot.model_validate_json(before, strict=True)
    assert type(replay.qualification) is EntryQualificationResult
    assert replay.model_dump_json(round_trip=True) == before
    assert validate_snapshot(replay) == snapshot


@pytest.mark.parametrize("marker", (None, "ctcc-history-evidence-v1", "unknown"))
def test_evidence_union_requires_an_explicit_exact_v2_marker(published, marker):
    _, evidence, origin = published
    raw = json.loads(origin.model_dump_json(round_trip=True))
    if marker is None:
        raw["evidence"].pop("contract_version")
    else:
        raw["evidence"]["contract_version"] = marker
    with pytest.raises(ValueError):
        RecheckOrigin.model_validate_json(json.dumps(raw), strict=True)
    raw = json.loads(evidence.snapshot.model_dump_json(round_trip=True))
    raw["qualification"].pop("contract_version")
    with pytest.raises(ValueError):
        EvidenceSnapshot.model_validate_json(json.dumps(raw), strict=True)


@pytest.mark.parametrize("mutation", ("source", "event", "entry", "passed", "expiry"))
def test_forged_original_cannot_enter_g12(expanded, tmp_path, mutation):
    source, inputs, run = expanded
    changed = type(run).model_validate_json(
        run.model_dump_json(round_trip=True), strict=True
    )
    if mutation == "source":
        changed.prefix.data_result.__dict__["source_sha256"] = "0" * 64
    elif mutation == "event":
        changed.prefix.detection.trigger.__dict__["trigger_time"] += timedelta(
            seconds=1
        )
    elif mutation == "entry":
        changed.result.__dict__["candidate_entry"] += D(".01")
    elif mutation == "expiry":
        changed.prefix.intent.__dict__["expires_at"] += timedelta(minutes=1)
    else:
        changed.__dict__["passed"] = True
    with pytest.raises(EvidenceGateError, match="pre_evidence_replay_failed"):
        publish_qualification_evidence(
            tmp_path,
            source.market,
            run=changed,
            **inputs,
            purpose="synthetic_test",
            clock=lambda: pytest.fail("clock crossed replay failure"),
        )
    assert not list(tmp_path.iterdir())


def test_v1_record_cannot_be_coerced_to_v2_or_reuse_receipt(expanded, published):
    source, inputs, run = expanded
    old = evaluate_history_pre_evidence(source.market, **history_inputs(source))
    with pytest.raises(ValueError):
        HistoryEntryQualificationResultV2.model_validate(
            old.result.model_dump(round_trip=True), strict=True
        )
    root, evidence, _ = published
    before = {p.name: p.read_bytes() for p in (root / source.report_id).iterdir()}
    repeated = publish_qualification_evidence(
        root,
        source.market,
        run=run,
        **inputs,
        purpose="synthetic_test",
        clock=Clock(source.evaluated_at),
    )
    assert not repeated.result.gates[-1].passed
    assert repeated.execution_authority is False
    assert before == {
        p.name: p.read_bytes() for p in (root / source.report_id).iterdir()
    }
    assert evidence.receipt.status == "written"


@pytest.mark.parametrize(
    "mutation",
    (
        "opposed_htf",
        "neutral_htf",
        "stale_capture",
        "extended_expiry",
        "different_policy",
        "consumed_event",
        "expired_event",
    ),
)
def test_post_g12_rejects_changed_context_without_reoptimizing(
    expanded, published, mutation
):
    source, inputs, run = expanded
    _, _, origin = published
    market, current = asyncio.run(current_inputs(source, inputs, origin))
    geometry = (
        run.result.candidate_entry,
        run.result.stop_loss,
        run.result.take_profit,
    )
    if mutation in ("opposed_htf", "neutral_htf"):
        for index, row in enumerate(market.candles["4H"]):
            close = (
                D(120) - D(index) * D(".02") if mutation == "opposed_htf" else D(100)
            )
            if source.direction == "short":
                close = D(200) - close
            market.candles["4H"][index] = row.model_copy(
                update={
                    "open": close,
                    "close": close,
                    "high": close + D(".15"),
                    "low": close - D(".15"),
                }
            )
    elif mutation == "stale_capture":
        current["quote"] = inputs["quote"]
    elif mutation == "extended_expiry":
        current["original_inputs"] = inputs | {
            "intent": inputs["intent"].model_copy(
                update={
                    "expires_at": inputs["intent"].expires_at + timedelta(minutes=5)
                }
            )
        }
    elif mutation == "different_policy":
        current["original_inputs"] = inputs | {
            "policy": inputs["policy"].model_copy(
                update={"policy_id": "changed-policy-after-publish"}
            )
        }
    elif mutation == "expired_event":
        current["observed_at"] = origin.deadline
    else:
        current["consumed_event_keys"] = frozenset({origin.original_event_key})
    result = evaluate_recorded_recheck(source.market, market, **current)
    assert not result.computational_checks_passed
    assert not result.checks[-1].passed
    if mutation in ("opposed_htf", "neutral_htf"):
        assert result.checks[-1].step == "current_conditions"
        assert result.continuation is None
    elif mutation in ("extended_expiry", "different_policy"):
        assert result.checks[-1].step == "origin_replay"
    elif mutation in ("stale_capture", "expired_event"):
        assert result.checks[-1].step == "capture_barrier"
    else:
        assert result.checks[-1].step == "timing"
    assert (
        origin.candidate.candidate_entry,
        origin.candidate.stop_loss,
        origin.candidate.take_profit,
    ) == geometry
    assert result.execution_authority is False


@pytest.mark.parametrize("mutation", ("duplicate", "reversed", "missing", "future"))
def test_source_chronology_failure_never_reaches_htf_admission(expanded, mutation):
    source, inputs, _ = expanded
    market = source.market.model_copy(deep=True)
    rows = market.candles["15m"]
    if mutation == "duplicate":
        rows[-2] = rows[-1].model_copy()
    elif mutation == "reversed":
        rows[-2], rows[-1] = rows[-1], rows[-2]
    elif mutation == "missing":
        rows.pop(-2)
    else:
        rows[-1] = rows[-1].model_copy(
            update={"timestamp": source.evaluated_at + timedelta(days=1)}
        )
    result = evaluate_history_pre_evidence_v2(market, **inputs)
    assert not result.pre_evidence_complete
    assert len(result.result.gates) == 1
    assert result.prefix.history_admission is None
