"""Synthetic quote/fill arithmetic only; no genuine trading acceptance samples."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal, localcontext

import pytest

from app.trade_evidence import forensics as core
from app.trade_evidence.execution_forensics import (
    ExecutionForensicsPolicy,
    ExecutionReference,
    analyze_execution_forensics,
    verify_execution_forensics,
)
from tests.unit.test_trade_forensics import fixture

D = Decimal
POLICY = ExecutionForensicsPolicy(
    policy_id="synthetic_pre_registered", maximum_reference_age_ms=1000
)


def example(direction="long", *, closed=True, improvement=False):
    packet, pin = fixture(direction, closed=closed)
    result = core.analyze_trade(packet, expected_candidate_sha256=pin)
    references = []
    for fill in packet.fills:
        side = 1 if fill.side == "buy" else -1
        mid = fill.price + side * D(".2") * (1 if improvement else -1)
        references.append(
            ExecutionReference(
                evidence_id="quote_" + fill.evidence_id,
                fill_id=fill.evidence_id,
                report_id=packet.candidate.report_id,
                candidate_sha256=pin,
                instrument_id=packet.candidate.instrument_id,
                account_id=packet.candidate.account_id,
                source_sha256=hashlib.sha256(
                    ("quote_" + fill.evidence_id).encode()
                ).hexdigest(),
                recorded_at=packet.observed_at,
                request_started_at=fill.occurred_at - timedelta(milliseconds=300),
                source_at=fill.occurred_at - timedelta(milliseconds=200),
                received_at=fill.occurred_at - timedelta(milliseconds=100),
                bid=mid - D(".1"),
                ask=mid + D(".1"),
            )
        )
    return result, pin, tuple(references)


def run(result, pin, refs=(), policy=POLICY):
    return analyze_execution_forensics(
        result, expected_candidate_sha256=pin, policy=policy, references=refs
    )


@pytest.mark.parametrize("direction", ["long", "short"])
def test_signed_cost_decomposition_does_not_double_count_actual_fill_costs(direction):
    result, pin, refs = example(direction)
    original = core.freeze_forensics(result, expected_candidate_sha256=pin)
    replay = run(result, pin, refs)
    metrics = dict(replay.metrics)
    assert metrics["net_r_multiple"].value == D("1.136")
    assert metrics["holding_duration"].value == 30
    assert metrics["fees_paid"].value == D(".15")
    assert metrics["rebates_received"].value == D(".01")
    assert metrics["entry_plan_deviation_cash"].value == D("1.2")
    assert metrics["exit_reference_deviation_cash"].value == 3
    for role in ("entry", "exit"):
        assert metrics[role + "_spread_cost"].value == D(".1")
        assert metrics[role + "_residual_slippage"].value == D(".1")
        assert metrics[role + "_total_price_friction"].value == D(".2")
    assert metrics["quote_reference_gross_pnl"].value == D("6.2")
    assert (
        metrics["net_pnl_from_reference_decomposition"].value
        == result.pnl.value
        == D("5.68")
    )
    assert metrics["structural_stop_distance"].value == 5
    assert metrics["structural_target_distance"].value == 10
    assert core.freeze_forensics(result, expected_candidate_sha256=pin) == original
    assert not replay.execution_authority and not replay.source_authenticity_verified
    verified = verify_execution_forensics(
        replay.canonical_payload,
        expected_sha256=replay.sha256,
        base_payload=original,
        expected_base_sha256=hashlib.sha256(original).hexdigest(),
        expected_candidate_sha256=pin,
    )
    assert verified == replay


@pytest.mark.parametrize("direction", ["long", "short"])
def test_price_improvement_remains_signed_and_net_cash_result_unchanged(direction):
    result, pin, refs = example(direction, improvement=True)
    metrics = dict(run(result, pin, refs).metrics)
    for role in ("entry", "exit"):
        assert metrics[role + "_spread_cost"].value == D(".1")
        assert metrics[role + "_residual_slippage"].value == D("-.3")
        assert metrics[role + "_total_price_friction"].value == D("-.2")
    assert metrics["quote_reference_gross_pnl"].value == D("5.4")
    assert metrics["net_pnl_from_reference_decomposition"].value == result.pnl.value


def test_missing_reference_is_unknown_without_erasing_known_cash_result():
    result, pin, refs = example()
    metrics = dict(run(result, pin, refs[1:]).metrics)
    assert metrics["entry_spread_cost"].value is None
    assert metrics["entry_spread_cost"].unknown_reason == "execution_reference_missing"
    assert metrics["exit_spread_cost"].value == D(".1")
    assert metrics["net_r_multiple"].value == D("1.136")
    assert metrics["net_pnl_from_reference_decomposition"].value is None


@pytest.mark.parametrize(
    "when,reason",
    [
        ("stale", "execution_reference_stale"),
        ("future", "execution_reference_received_after_fill"),
    ],
)
def test_stale_source_or_post_fill_receipt_cannot_explain_execution(when, reason):
    result, pin, refs = example()
    quote = refs[0]
    update = (
        {
            "request_started_at": quote.request_started_at - timedelta(seconds=3),
            "source_at": quote.source_at - timedelta(seconds=2),
        }
        if when == "stale"
        else {"received_at": quote.received_at + timedelta(seconds=1)}
    )
    refs = (quote.model_copy(update=update), *refs[1:])
    metrics = dict(run(result, pin, refs).metrics)
    assert metrics["entry_total_price_friction"].unknown_reason == reason
    assert metrics["entry_total_price_friction"].value is None


def test_open_position_has_no_closed_duration_net_r_or_reference_pnl():
    result, pin, refs = example(closed=False)
    metrics = dict(run(result, pin, refs).metrics)
    for key in (
        "net_r_multiple",
        "holding_duration",
        "quote_reference_gross_pnl",
        "net_pnl_from_reference_decomposition",
    ):
        assert metrics[key].value is None
    assert metrics["entry_spread_cost"].value == D(".1")


@pytest.mark.parametrize("stream", ["fills", "fees", "funding"])
def test_incomplete_sources_do_not_become_zero_or_closed_net_r(stream):
    result, pin, refs = example()
    packet = result.inputs.model_copy(
        update={
            "coverage": tuple(
                c.model_copy(
                    update={"status": "unknown", "reason": "synthetic_missing"}
                )
                if c.stream == stream
                else c
                for c in result.inputs.coverage
            )
        }
    )
    result = core.analyze_trade(packet, expected_candidate_sha256=pin)
    metrics = dict(run(result, pin, refs).metrics)
    assert metrics["net_r_multiple"].value is None
    if stream == "fees":
        assert metrics["fees_paid"].value is metrics["rebates_received"].value is None
    if stream == "fills":
        assert (
            metrics["entry_spread_cost"].value
            is metrics["holding_duration"].value
            is None
        )


@pytest.mark.parametrize(
    "update",
    [
        {"fill_id": "missing"},
        {"evidence_id": "fill_0"},
        {"account_id": "999999999"},
        {"candidate_sha256": "f" * 64},
        {"bid": D(999)},
        {"ask": 1.1},
        {"unknown_field": True},
    ],
)
def test_reference_tampering_rejected_before_any_result(update):
    result, pin, refs = example()
    with pytest.raises(ValueError):
        run(result, pin, (refs[0].model_copy(update=update), *refs[1:]))


def test_duplicate_reference_is_not_deduplicated():
    result, pin, refs = example()
    with pytest.raises(ValueError, match="duplicate"):
        run(result, pin, (*refs, refs[0]))


def test_reference_cannot_reuse_an_attribution_fact_identity():
    result, pin, refs = example()
    fact = core.AttributionFact(
        **{name: getattr(refs[0], name) for name in core._BoundEvidence.model_fields},
        category="Execution Error",
        statement="Synthetic source assessment for the identity collision check.",
        evidence_ids=(refs[0].fill_id,),
    )
    packet = result.inputs.model_copy(update={"facts": (fact,)})
    result = core.analyze_trade(packet, expected_candidate_sha256=pin)
    with pytest.raises(ValueError, match="duplicate_or_unbound"):
        run(result, pin, refs)


@pytest.mark.parametrize("value", [True, 0, -1, 60001, 1000.0])
def test_policy_cannot_be_repaired_after_model_copy(value):
    result, pin, refs = example()
    with pytest.raises(ValueError):
        run(
            result,
            pin,
            refs,
            POLICY.model_copy(update={"maximum_reference_age_ms": value}),
        )


@pytest.mark.parametrize(
    "field",
    [
        "execution_authority",
        "source_authenticity_verified",
        "metrics",
        "candidate_sha256",
        "base_result_sha256",
        "extra",
    ],
)
def test_rehashed_forged_receipt_does_not_replace_source_replay(field):
    result, pin, refs = example()
    replay = run(result, pin, refs)
    payload = json.loads(replay.canonical_payload)
    if field == "metrics":
        payload[field]["net_r_multiple"].update(
            value="999", numerator="999", denominator="1"
        )
    else:
        payload[field] = (
            True
            if field in ("execution_authority", "source_authenticity_verified", "extra")
            else "f" * 64
        )
    raw = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode()
    base = core.freeze_forensics(result, expected_candidate_sha256=pin)
    with pytest.raises(ValueError, match="replay_mismatch"):
        verify_execution_forensics(
            raw,
            expected_sha256=hashlib.sha256(raw).hexdigest(),
            base_payload=base,
            expected_base_sha256=hashlib.sha256(base).hexdigest(),
            expected_candidate_sha256=pin,
        )


def test_decimal_context_cannot_change_result():
    result, pin, refs = example()
    expected = run(result, pin, refs)
    with localcontext() as ctx:
        ctx.prec = 3
        assert run(result, pin, refs) == expected


def test_source_quote_age_boundary_is_inclusive_and_policy_hash_is_bound():
    result, pin, refs = example()
    narrow = POLICY.model_copy(update={"maximum_reference_age_ms": 200})
    accepted = run(result, pin, refs, narrow)
    denied = run(
        result, pin, refs, narrow.model_copy(update={"maximum_reference_age_ms": 199})
    )
    assert dict(accepted.metrics)["entry_spread_cost"].value == D(".1")
    assert dict(denied.metrics)["entry_spread_cost"].value is None
    assert accepted.sha256 != denied.sha256
