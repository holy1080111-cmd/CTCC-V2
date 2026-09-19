"""Synthetic source-derived reversal policy; no exchange, OOS or order authority."""

import asyncio
import hashlib
import json
from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.strategies.structural_protection import select_reversal_structural_protection
from app.trade_evidence.gates import (
    EvidenceGateError,
    HistoryEvidenceGateRunV3,
    publish_qualification_evidence,
)
from app.trade_evidence.models import EvidenceSnapshot
from app.trade_evidence.service import validate_snapshot
from app.trade_evidence.storage import FILE_NAMES
from app.trade_qualification.events import extract_trigger
from app.trade_qualification.history_engine import (
    HistoryPreEvidencePolicyV3,
    evaluate_history_pre_evidence,
    evaluate_history_pre_evidence_v3,
    verify_history_pre_evidence_v3,
)
from app.trade_qualification.history_prefix import HistoryEntryQualificationResultV3
from app.trade_qualification.one_shot import _original
from app.trade_qualification.recheck import (
    RecordedRecheckAssessment,
    evaluate_recorded_recheck,
    verify_recorded_recheck,
)
from app.trade_qualification.recheck_models import RecheckOrigin, freeze_recheck_origin
from tests.unit.history_qualification_fixtures import history_inputs, history_source
from tests.unit.test_history_expansion_v2 import current_inputs
from tests.unit.test_qualification_evidence_gate import Clock


def v3_inputs(source):
    inputs = history_inputs(source)
    policy = inputs["policy"].model_dump(round_trip=True)
    policy["contract_version"] = "ctcc-history-pre-evidence-v3"
    policy["prefix"]["contract_version"] = "ctcc-history-qualification-prefix-v3"
    inputs["policy"] = HistoryPreEvidencePolicyV3.model_validate(policy, strict=True)
    return inputs


@pytest.fixture(scope="module", params=("long", "short"))
def reversal(request):
    source = history_source(
        "structure_reversal", request.param, bracket=True, reversal_bracket=True
    )
    inputs = v3_inputs(source)
    run = evaluate_history_pre_evidence_v3(source.market, **inputs)
    assert run.pre_evidence_complete, run.result.fail_codes
    return source, inputs, run


@pytest.fixture(scope="module")
def published(reversal, tmp_path_factory):
    source, inputs, run = reversal
    root = tmp_path_factory.mktemp("history-reversal-v3")
    evidence = publish_qualification_evidence(
        root,
        source.market,
        run=run,
        **inputs,
        purpose="synthetic_test",
        clock=Clock(source.evaluated_at),
    )
    assert evidence.result.gates[-1].passed, evidence.error_detail
    assert type(evidence) is HistoryEvidenceGateRunV3
    return root, evidence, freeze_recheck_origin(evidence)


@pytest.fixture(scope="module")
def rechecked(reversal, published):
    source, inputs, _ = reversal
    _, _, origin = published
    market, args = asyncio.run(current_inputs(source, inputs, origin))
    result = evaluate_recorded_recheck(source.market, market, **args)
    assert result.computational_checks_passed
    return result


def test_source_reversal_replays_without_removing_diagnostic_or_relabeling_snapshot(
    reversal,
):
    source, inputs, run = reversal
    assert verify_history_pre_evidence_v3(run, source.market, **inputs) == run
    assert _original(source.market, run, inputs)[1] == run
    old = evaluate_history_pre_evidence(source.market, **history_inputs(source))
    assert old.result.fail_codes == ("source_data_blockers",)
    assert (
        old.result.gates[:2] != run.result.gates[:2]
    )  # Explicit policy identity differs.
    assert old.prefix.data_result == run.prefix.data_result
    assert old.prefix.history_admission == run.prefix.history_admission
    assert old.prefix.detection == run.prefix.detection
    audit = json.loads(run.protection_audit_json)
    assert audit["retained_analysis_blockers"] == ["multi_timeframe_not_aligned"]
    assert (
        audit["history_admission_sha256"]
        == run.prefix.history_admission.evaluation_sha256
    )
    assert audit["schema"] == "ctcc_structural_selection_reversal_v1"
    assert json.loads(run.prefix.data_result.source_json)["analysis"]["blockers"] == [
        "multi_timeframe_not_aligned"
    ]
    assert run.result.market_regime == "History Verified Reversal"
    assert run.prefix.history_admission.snapshot_regime == "Unknown"
    assert not run.execution_authority and not run.source_authenticity_verified


def test_native_g12_readback_and_exact_v3_identity(published):
    root, evidence, origin = published
    assert type(evidence.snapshot.qualification) is HistoryEntryQualificationResultV3
    assert validate_snapshot(evidence.snapshot) == evidence.snapshot
    assert (
        EvidenceSnapshot.model_validate_json(
            evidence.snapshot.model_dump_json(round_trip=True), strict=True
        )
        == evidence.snapshot
    )
    assert (
        RecheckOrigin.model_validate_json(
            origin.model_dump_json(round_trip=True), strict=True
        )
        == origin
    )
    assert tuple(f.name for f in evidence.receipt.files) == FILE_NAMES
    for artifact in evidence.receipt.files:
        body = (root / evidence.result.report_id / artifact.name).read_bytes()
        assert hashlib.sha256(body).hexdigest() == artifact.sha256
    assert evidence.result.stop_loss == evidence.pre_evidence.result.stop_loss
    assert evidence.result.take_profit == evidence.pre_evidence.result.take_profit


def test_post_g12_v3_replays_fixed_original_bracket_and_event(reversal, published):
    source, inputs, original = reversal
    _, _, origin = published
    market, args = asyncio.run(current_inputs(source, inputs, origin))
    result = evaluate_recorded_recheck(source.market, market, **args)
    assert result.computational_checks_passed, [(c.step, c.code) for c in result.checks]
    assert verify_recorded_recheck(result, source.market, market, **args) == result
    assert (
        RecordedRecheckAssessment.model_validate_json(
            result.model_dump_json(round_trip=True), strict=True
        )
        == result
    )
    assert (
        result.fixed_protection.history_admission_sha256
        == original.prefix.history_admission.evaluation_sha256
    )
    assert result.current_conditions.original_event_key == origin.original_event_key
    assert (
        result.fixed_protection.entry,
        result.fixed_protection.stop_loss,
        result.fixed_protection.take_profit,
    ) == (
        original.result.candidate_entry,
        original.result.stop_loss,
        original.result.take_profit,
    )
    assert (
        result.timing.latest_valid_entry_time
        == original.prefix.timing.latest_valid_entry_time
    )
    assert not result.execution_authority and not result.runtime_admissible


@pytest.mark.parametrize(
    "mutation",
    ("source", "event", "entry", "stop", "target", "passed", "expiry", "history"),
)
def test_forged_original_never_crosses_publication(reversal, tmp_path, mutation):
    source, inputs, run = reversal
    forged = type(run).model_validate_json(
        run.model_dump_json(round_trip=True), strict=True
    )
    if mutation == "source":
        forged.prefix.data_result.__dict__["source_sha256"] = "0" * 64
    elif mutation == "event":
        forged.prefix.detection.trigger.__dict__["trigger_time"] += timedelta(seconds=1)
    elif mutation in ("entry", "stop", "target"):
        key = {
            "entry": "candidate_entry",
            "stop": "stop_loss",
            "target": "take_profit",
        }[mutation]
        forged.result.__dict__[key] += Decimal(".01")
    elif mutation == "expiry":
        forged.prefix.intent.__dict__["expires_at"] += timedelta(minutes=1)
    elif mutation == "history":
        forged.prefix.history_admission.__dict__["snapshot_sha256"] = "0" * 64
    else:
        forged.__dict__["passed"] = True
    with pytest.raises(EvidenceGateError, match="pre_evidence_replay_failed"):
        publish_qualification_evidence(
            tmp_path,
            source.market,
            run=forged,
            **inputs,
            purpose="synthetic_test",
            clock=lambda: pytest.fail("publication reached after forged original"),
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("marker", (None, "ctcc-history-evidence-v2", "unknown"))
def test_v3_serialized_evidence_requires_exact_explicit_marker(published, marker):
    _, evidence, origin = published
    raw = json.loads(origin.model_dump_json(round_trip=True))
    if marker is None:
        raw["evidence"].pop("contract_version")
    else:
        raw["evidence"]["contract_version"] = marker
    with pytest.raises(ValueError):
        RecheckOrigin.model_validate_json(json.dumps(raw), strict=True)
    raw = json.loads(evidence.snapshot.model_dump_json(round_trip=True))
    if marker is None:
        raw["qualification"].pop("contract_version")
    else:
        raw["qualification"]["contract_version"] = marker
    with pytest.raises(ValueError):
        EvidenceSnapshot.model_validate_json(json.dumps(raw), strict=True)


@pytest.mark.parametrize(
    "path",
    (
        ("contract_version",),
        ("policy", "contract_version"),
        ("policy", "prefix", "contract_version"),
        ("prefix", "contract_version"),
        ("prefix", "policy", "contract_version"),
        ("prefix", "result", "contract_version"),
        ("result", "contract_version"),
    ),
)
def test_nested_v3_marker_is_required_without_subtype_defaults(reversal, path):
    _, _, run = reversal
    raw = json.loads(run.model_dump_json(round_trip=True))
    node = raw
    for key in path[:-1]:
        node = node[key]
    node.pop(path[-1])
    with pytest.raises(ValueError):
        type(run).model_validate_json(json.dumps(raw), strict=True)


@pytest.mark.parametrize(
    "mutation",
    (
        "opposed_4h",
        "lost_1h_choch",
        "stale_capture",
        "extended_expiry",
        "different_policy",
        "consumed_event",
        "expired_event",
    ),
)
def test_post_publication_rejects_failed_context_with_original_geometry(
    reversal, published, mutation
):
    source, inputs, run = reversal
    _, _, origin = published
    market, args = asyncio.run(current_inputs(source, inputs, origin))
    geometry = (
        run.result.candidate_entry,
        run.result.stop_loss,
        run.result.take_profit,
    )
    if mutation == "opposed_4h":
        for index, row in enumerate(market.candles["4H"]):
            close = Decimal(120) - Decimal(index) * Decimal(".02")
            if source.direction == "short":
                close = Decimal(200) - close
            market.candles["4H"][index] = row.model_copy(
                update={
                    "open": close,
                    "close": close,
                    "high": close + Decimal(".15"),
                    "low": close - Decimal(".15"),
                }
            )
    elif mutation == "lost_1h_choch":
        row = market.candles["1H"][-1]
        close = Decimal(101 if source.direction == "long" else 99)
        market.candles["1H"][-1] = row.model_copy(
            update={
                "close": close,
                "high": max(row.high, close),
                "low": min(row.low, close),
            }
        )
    elif mutation == "stale_capture":
        args["quote"] = inputs["quote"]
    elif mutation == "extended_expiry":
        args["original_inputs"] = inputs | {
            "intent": inputs["intent"].model_copy(
                update={
                    "expires_at": inputs["intent"].expires_at + timedelta(minutes=5)
                }
            )
        }
    elif mutation == "different_policy":
        args["original_inputs"] = inputs | {
            "policy": inputs["policy"].model_copy(
                update={"policy_id": "changed-after-publication"}
            )
        }
    elif mutation == "expired_event":
        args["observed_at"] = origin.deadline
    else:
        args["consumed_event_keys"] = frozenset({origin.original_event_key})
    result = evaluate_recorded_recheck(source.market, market, **args)
    assert not result.computational_checks_passed
    assert not result.checks[-1].passed
    expected = (
        "current_conditions"
        if mutation in ("opposed_4h", "lost_1h_choch")
        else "origin_replay"
        if mutation in ("extended_expiry", "different_policy")
        else "capture_barrier"
        if mutation in ("stale_capture", "expired_event")
        else "timing"
    )
    assert result.checks[-1].step == expected
    if expected == "current_conditions":
        assert result.continuation is None
    assert (
        origin.candidate.candidate_entry,
        origin.candidate.stop_loss,
        origin.candidate.take_profit,
    ) == geometry
    assert not result.execution_authority


@pytest.mark.parametrize("mutation", ("duplicate", "reversed", "missing", "future"))
def test_source_chronology_fails_before_history_permission(reversal, mutation):
    source, inputs, _ = reversal
    market = source.market.model_copy(deep=True)
    rows = market.candles["1H"]
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
    rejected = evaluate_history_pre_evidence_v3(market, **inputs)
    assert len(rejected.result.gates) == 1
    assert not rejected.result.gates[0].passed
    assert rejected.prefix.history_admission is None


def test_v1_cannot_be_upcast_or_republish_old_receipt(reversal, published):
    source, inputs, run = reversal
    old = evaluate_history_pre_evidence(source.market, **history_inputs(source))
    with pytest.raises(ValueError):
        HistoryEntryQualificationResultV3.model_validate(
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
    assert repeated.receipt.status == "already_present"
    with pytest.raises(ValueError):
        freeze_recheck_origin(repeated)
    assert before == {
        p.name: p.read_bytes() for p in (root / source.report_id).iterdir()
    }
    assert evidence.receipt.status == "written"


def test_source_without_legal_bracket_still_fails_g8(reversal):
    source, _, _ = reversal
    no_bracket = history_source("structure_reversal", source.direction, bracket=True)
    rejected = evaluate_history_pre_evidence_v3(
        no_bracket.market, **v3_inputs(no_bracket)
    )
    assert rejected.result.fail_codes == ("no_valid_structural_bracket",)
    assert len(rejected.result.gates) == 8
    assert not rejected.execution_authority


@pytest.mark.parametrize(
    "path",
    (
        ("current_conditions",),
        ("current_conditions", "policy"),
        ("current_conditions", "result"),
        ("fixed_protection",),
    ),
)
def test_recheck_requires_all_explicit_v3_markers(rechecked, path):
    raw = json.loads(rechecked.model_dump_json(round_trip=True))
    node = raw
    for key in path:
        node = node[key]
    node.pop("contract_version")
    with pytest.raises(ValueError):
        RecordedRecheckAssessment.model_validate_json(json.dumps(raw), strict=True)


@pytest.mark.parametrize("mutation", ("analysis", "shortened_trigger_ttl"))
def test_protection_independently_replays_history_from_raw_market(reversal, mutation):
    source, inputs, run = reversal
    raw = json.loads(run.prefix.data_result.source_json)
    market = MarketSnapshot.model_validate_json(json.dumps(raw["market"]), strict=True)
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(raw["analysis"]), strict=True
    )
    ttl = 600
    if mutation == "analysis":
        # A caller can hash its own invented analysis and reconstruct the same
        # OHLC event. Source hash consistency alone must never authenticate it.
        analysis = analysis.model_copy(update={"blockers": []})
    else:
        ttl = 599
    event = extract_trigger(
        market,
        analysis,
        report_id=source.report_id,
        strategy="structure_reversal",
        direction=source.direction,
        observed_at=source.evaluated_at,
        trigger_ttl_seconds=ttl,
    )
    assert event.trigger is not None and not event.fail_codes
    selection = select_reversal_structural_protection(
        event,
        market,
        analysis,
        observed_at=source.evaluated_at,
        entry=inputs["intent"].candidate_entry,
        tick_size=inputs["policy"].prefix.tick_size,
        **inputs["policy"].protection.model_dump(exclude={"policy_id"}),
    )
    assert not selection.protection_valid
    assert selection.fail_codes == ("reversal_protection_history_denied",)
    assert not selection.execution_authority


def test_candidate_deadline_may_shorten_without_rewriting_event(reversal):
    source, inputs, run = reversal
    shorter = inputs | {
        "intent": inputs["intent"].model_copy(
            update={"expires_at": source.evaluated_at + timedelta(seconds=20)}
        )
    }
    result = evaluate_history_pre_evidence_v3(source.market, **shorter)
    assert result.pre_evidence_complete
    assert result.prefix.detection == run.prefix.detection
    assert result.prefix.timing.event_key == run.prefix.timing.event_key
    assert (
        result.prefix.timing.latest_valid_entry_time
        < run.prefix.timing.latest_valid_entry_time
    )
    assert (
        result.result.candidate_entry,
        result.result.stop_loss,
        result.result.take_profit,
    ) == (run.result.candidate_entry, run.result.stop_loss, run.result.take_profit)
