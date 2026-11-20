"""Declared synthetic unchanged Global bytes; no live account/Arm/order proof."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.source_primitives import canonical, decode
from app.okx_live import qualification_math_v1 as subject
from app.trade_qualification import fixed_protection
from app.trade_qualification import native_live_public_source_v1 as public
from app.trade_qualification.service import QualificationIntent
from tests.unit.qualification_engine_fixtures import engine_inputs
from tests.unit.research import test_native_live_public_source_v1 as public_fixture
from tests.unit.research.test_native_live_public_source_v1 import (
    live_source as live_source,  # noqa: PLC0414 -- explicitly synthetic mock-transport fixture.
)
from tests.unit.test_qualification_market_bridge import v2_engine_source


@pytest.fixture(scope="module", params=("long", "short"))
def market(request):
    return v2_engine_source(request.param)


async def initial(live_source, market, **changes):
    _, _, _, _, key, request = live_source
    opaque, _ = request()
    owner = await public.capture_owned_live_public_v1(opaque, key=key)
    source = public._replay_owned_live_public_v1(owner, key=key)
    args = engine_inputs(market)
    intent = QualificationIntent(
        report_id=source.report_id,
        instrument_id=source.instrument_id,
        strategy=args["intent"].strategy,
        direction=args["intent"].direction,
        candidate_entry=args["intent"].candidate_entry,
        created_at=source.evaluated_at,
        expires_at=source.expires_at,
    )
    options = {
        "intent": intent,
        "policy": args["policy"],
        "consumed_event_keys": frozenset(),
    } | changes
    return (
        owner,
        source,
        options,
        subject.evaluate_live_qualification_math_v1(source, **options),
    )


@pytest.mark.asyncio
async def test_unchanged_global_raw_recomputes_live_g1_g10_and_waits_for_financial(
    live_source, market
):
    _, source, _, result = await initial(live_source, market)
    assert result.market_math_complete, [
        (g.gate.value, g.code) for g in result.result.gates
    ]
    assert not result.pre_evidence_complete
    assert result.result.gates[-1].code == "live_financial_snapshot_required"
    assert result.execution_authority is result.account_complete is False
    assert result.source_authenticity_verified is result.atomic_risk_reserved is False
    assert source.packet.report_id != source.report_id
    assert source.packet.rest_origin == "https://openapi.okx.com"
    assert source.packet.environment == "anonymous_global_public"
    assert result.source.quote.environment == "live"
    assert (
        subject.verify_live_qualification_math_v1(result).receipt_json
        == result.receipt_json
    )
    original, current = result, source
    old = decode(original.source_json.encode())
    new = decode(result.source_json.encode())
    fixed_args = {
        "original_market": MarketSnapshot.model_validate_json(
            canonical(old["market"]), strict=True
        ),
        "original_analysis": MultiTimeframeAnalysis.model_validate_json(
            canonical(old["analysis"]), strict=True
        ),
        "current_market": MarketSnapshot.model_validate_json(
            canonical(new["market"]), strict=True
        ),
        "current_analysis": MultiTimeframeAnalysis.model_validate_json(
            canonical(new["analysis"]), strict=True
        ),
        "detection": original.detection,
        "original_selection_audit_json": original.selection_audit_json,
        "stop_loss": original.result.stop_loss,
        "take_profit": original.result.take_profit,
        "entry": original.intent.candidate_entry,
        "policy": original.policy.protection,
        "tick_size": original.policy.prefix.tick_size,
        "data_policy": original.policy.prefix.data,
        "quote": current.quote,
        "observed_at": current.evaluated_at,
    }
    assert fixed_protection.evaluate_live_owned_fixed_protection_v1(**fixed_args).passed
    assert not fixed_protection.evaluate_owned_fixed_protection_v2(**fixed_args).passed
    changed = fixed_protection.evaluate_live_owned_fixed_protection_v1(
        **(
            fixed_args
            | {
                "take_profit": original.result.take_profit
                + original.policy.prefix.tick_size
            }
        )
    )
    assert not changed.passed
    assert (
        changed.take_profit
        == original.result.take_profit + original.policy.prefix.tick_size
    )
    raw = decode(result.risk_operands_json)
    assert raw["stop_loss"] == str(result.result.stop_loss)
    assert raw["take_profit"] == str(result.result.take_profit)


@pytest.mark.asyncio
async def test_unknown_event_ledger_waits_instead_of_becoming_empty(
    live_source, market
):
    _, _, _, result = await initial(live_source, market, consumed_event_keys=None)
    assert result.result.gates[-1].code == "event_ledger_unknown"
    assert result.result.entry_timing_state == "wait"


@pytest.mark.asyncio
async def test_all_eight_existing_strategy_routes_keep_explicit_history_and_no_permission(
    live_source, market
):
    _, source, args, _ = await initial(live_source, market)
    strategies = (
        "trend_pullback",
        "breakout_continuation",
        "liquidity_sweep_reversal",
        "fvg_return",
        "order_block_return",
        "range_reversal",
        "structure_reversal",
        "volatility_expansion",
    )
    for strategy in strategies:
        intent = args["intent"].model_copy(update={"strategy": strategy})
        value = subject.evaluate_live_qualification_math_v1(
            source, **(args | {"intent": intent, "consumed_event_keys": None})
        )
        assert value.intent.strategy == value.result.strategy == strategy
        assert value.execution_authority is value.account_complete is False
        assert not value.pre_evidence_complete
        assert value.source.packet.packet_json == source.packet.packet_json
        assert len(value.result.gates) <= 6
        if value.result.gates[-1].gate.value == "G6":
            assert value.result.gates[-1].code == "event_ledger_unknown"


@pytest.mark.asyncio
async def test_raw_tamper_and_stale_clock_cannot_reuse_a_passing_math_record(
    live_source, market
):
    _, source, args, original = await initial(live_source, market)
    item = source.raw_files[0]
    changed = replace(
        source, raw_files=(replace(item, raw=item.raw + b" "), *source.raw_files[1:])
    )
    stale = replace(source, evaluated_at=source.evaluated_at + timedelta(seconds=6))
    for current in (changed, stale):
        result = subject.evaluate_live_qualification_math_v1(current, **args)
        assert not result.market_math_complete
        assert result.result.gates[0].code == "live_public_raw_replay_refused"
    forged = replace(
        original,
        result=original.result.model_copy(
            update={"stop_loss": original.result.stop_loss - Decimal(1)}
        ),
    )
    with pytest.raises(subject.LiveQualificationMathError, match="replay_mismatch"):
        subject.verify_live_qualification_math_v1(forged)
    object.__setattr__(original, "execution_authority", True)
    with pytest.raises(subject.LiveQualificationMathError):
        subject.verify_live_qualification_math_v1(original)


@pytest.mark.asyncio
async def test_original_cost_failure_cannot_be_repaired_by_a_later_quote(
    live_source, market
):
    _, source, args, original = await initial(live_source, market)
    policy = args["policy"].model_copy(
        update={
            "economics": args["policy"].economics.model_copy(
                update={"round_trip_fee_bps": Decimal(500)}
            )
        }
    )
    result = subject.evaluate_live_qualification_math_v1(
        source, **(args | {"policy": policy})
    )
    assert result.result.gates[-1].gate.value == "G10"
    assert not result.result.gates[-1].passed
    with pytest.raises(subject.LiveQualificationMathError, match="did_not_pass"):
        subject.recheck_live_qualification_math_v1(
            source,
            original=result,
            consumed_event_keys=frozenset(),
            publication_candidate_sha256="a" * 64,
        )
    assert original.market_math_complete


@pytest.mark.asyncio
async def test_post_g12_reuses_original_event_zone_prices_and_distinct_publication_digest(
    live_source, market, monkeypatch, tmp_path
):
    owner, _, _, original = await initial(live_source, market)
    old_clock = live_source[0]
    owner.close()
    # A second independently declared TEST transport/socket/clock/filesystem;
    # the first transport is correctly closed and cannot be reused.
    with (
        monkeypatch.context() as fresh_patch,
        contextmanager(public_fixture.live_source.__wrapped__)(
            fresh_patch, market, tmp_path / "second-source"
        ) as fresh,
    ):
        fresh[0].count = old_clock.count + 10
        fresh[0].stamp()
        await _current_checks(fresh, original)


async def _current_checks(live_source, original):
    _, _, _, _, key, request = live_source
    g12_digest = "a" * 64
    opaque, _ = request(
        "post_g12", invocation=original.source.invocation, candidate_sha256=g12_digest
    )
    current_owner = await public.capture_owned_live_public_v1(opaque, key=key)
    current = public._replay_owned_live_public_v1(current_owner, key=key)
    result = subject.recheck_live_qualification_math_v1(
        current,
        original=original,
        consumed_event_keys=frozenset(),
        publication_candidate_sha256=g12_digest,
    )
    assert result.market_math_complete, [
        (g.gate.value, g.code) for g in result.result.gates
    ]
    assert result.detection == original.detection
    assert result.original_zone == original.original_zone
    assert (
        result.result.candidate_entry,
        result.result.stop_loss,
        result.result.take_profit,
    ) == (
        original.result.candidate_entry,
        original.result.stop_loss,
        original.result.take_profit,
    )
    assert result.candidate_geometry_sha256 == original.candidate_geometry_sha256
    assert result.publication_candidate_sha256 == g12_digest
    assert g12_digest != original.evaluation_sha256
    assert (
        subject.verify_live_qualification_math_v1(result).receipt_json
        == result.receipt_json
    )
    with pytest.raises(subject.LiveQualificationMathError, match="binding_changed"):
        subject.recheck_live_qualification_math_v1(
            current,
            original=original,
            consumed_event_keys=frozenset(),
            publication_candidate_sha256="b" * 64,
        )
    consumed = subject.recheck_live_qualification_math_v1(
        current,
        original=original,
        consumed_event_keys=frozenset({subject.event_identity(original.detection)}),
        publication_candidate_sha256=g12_digest,
    )
    assert (
        not consumed.market_math_complete
        and consumed.result.gates[-1].code == "stale_candidate"
    )
    assert canonical(decode(original.receipt_json)) == original.receipt_json
