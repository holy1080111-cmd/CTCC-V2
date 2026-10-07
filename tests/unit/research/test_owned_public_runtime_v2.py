"""Owned v2 control flow over synthetic clock/TLS/IO; no native acceptance."""

import asyncio
import copy
import pickle
from datetime import timedelta
from decimal import Decimal

import pytest

from app.public_market_source.public_market_receipts import canonical, decode, sha
from app.trade_qualification import data_v2, demo_public_origin
from app.trade_qualification import post_g12_public_runtime as coordinator
from app.trade_qualification import public_market_collector as legacy
from app.trade_qualification import public_market_collector_v2 as public
from app.trade_qualification import public_source_runtime as runtime
from app.trade_qualification import qualification_runtime as initial
from app.trade_qualification import quote_collector_v2 as quotes
from app.trade_qualification.engine import evaluate_pre_evidence
from app.trade_qualification.market_bridge_v2 import public_market_context_v2
from tests.unit.research.test_owned_public_runtime_p1 import (
    journal,
    resign_journal,
    synthetic_runtime,
)
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_qualification_market_bridge import v2_engine_source
from tests.unit.test_qualification_one_shot import inputs as inputs  # noqa: PLC0414
from tests.unit.test_qualification_one_shot import ms
from tests.unit.test_qualification_public_market_collector import (
    policy as legacy_policy,
)


def policy():
    old = legacy_policy(counts=(240, 240, 240, 240))
    return public.PublicMarketCollectionPolicyV2(
        candles=old.candles, market_aux=old.market_aux, ws=old.ws
    )


def setup(monkeypatch, source, *, original=None):
    clock, directory, harness, publications = synthetic_runtime(
        monkeypatch, (source, None, None) if original is None else original
    )
    # Exercise immutable historical v2 packet/journal behavior with synthetic
    # transport only. Production has no bypass and still refuses new capture.
    monkeypatch.setattr(
        runtime, "_require_trusted_v2_demo_origin_profile", lambda _: None
    )
    monkeypatch.setattr(initial, "native_stamp", clock.stamp)
    original_public = harness.public

    def response(request):
        rows = original_public(request)
        if request.url.path.endswith("/funding-rate"):
            rows[0].update(
                ts=ms(clock.last - timedelta(seconds=38)),
                fundingTime=ms(clock.last + timedelta(hours=1)),
                nextFundingTime=ms(clock.last + timedelta(hours=9)),
                method="current_period",
                formulaType="withRate",
                minFundingRate="-0.00375",
                maxFundingRate="0.00375",
                settFundingRate="-0.0002",
                settState="settled",
            )
        return rows

    harness.public = response
    return clock, directory, harness, publications


def fresh_funding_return(harness, clock):
    original = harness.public

    def fresh(request):
        rows = original(request)
        if request.url.path.endswith("/funding-rate"):
            rows[0]["ts"] = ms(clock.last - timedelta(seconds=2))
        return rows

    harness.public = fresh


async def invoke(root):
    return await initial.capture_initial_public_market_v2(
        root, instrument_id="BTC-USDT-SWAP", market_policy=policy()
    )


@pytest.mark.asyncio
async def test_unbound_v2_demo_capture_refuses_before_journal_or_network(
    source, tmp_path, monkeypatch
):
    clock, directory, harness, _ = synthetic_runtime(monkeypatch, (source, None, None))
    monkeypatch.setattr(initial, "native_stamp", clock.stamp)

    def unexpected_io(*_args, **_kwargs):
        raise AssertionError("v2_demo_capture_entered_io")

    monkeypatch.setattr(runtime, "_runtime_attempt", unexpected_io)
    monkeypatch.setattr(runtime, "_new_owned_client", unexpected_io)
    monkeypatch.setattr(runtime, "_ws_options", unexpected_io)
    result = await invoke(tmp_path)
    assert result.code == "initial_public_v2_denied"
    assert result.admission == "DENY" and result.execution_authority is False
    assert result.packet is None and result.journal_sha256 is None
    assert not harness.requests and not directory.content
    empty_registries()


def test_caller_origin_strings_cannot_create_trusted_demo_profile():
    with pytest.raises(
        runtime.PublicSourceRuntimeError, match="demo_public_origin_mismatch"
    ):
        runtime._require_trusted_v2_demo_origin_profile(
            {"environment": "demo", "ws_origin": "wss://ws.okx.com:443/ws/v5/public"}
        )
    with pytest.raises(
        runtime.PublicSourceRuntimeError,
        match="trusted_demo_public_origin_profile_unavailable",
    ):
        runtime._require_trusted_v2_demo_origin_profile(
            {
                "environment": "demo",
                "registration_region": "global",
                "rest_origin": "https://openapi.okx.com",
                "ws_origin": "wss://wspap.okx.com:443/ws/v5/public",
                "trusted_demo_public_origin_profile": {"caller_supplied": True},
            }
        )


@pytest.mark.parametrize("region", ["global", "us_au", "eea"])
@pytest.mark.parametrize("entry", ["client", "request", "ws"])
@pytest.mark.parametrize(
    "schema_version",
    ["ctcc.public.initial_runtime_plan.v2", "ctcc.public.runtime_plan.v2"],
)
def test_v2_demo_transport_rechecks_origin_before_native_io(
    monkeypatch, region, entry, schema_version
):
    route = demo_public_origin.reviewed_demo_public_route(region)
    plan = {
        "schema_version": schema_version,
        "environment": "demo",
        "registration_region": region,
        "rest_origin": route.rest_origin,
        "ws_origin": route.ws_origin,
        # A caller claim is not an authenticated region/session proof.
        "trusted_demo_public_origin_profile": {
            "registration_region_verified": True,
            "source_authenticity_verified": True,
        },
    }
    state = {"plan": plan}
    monkeypatch.setattr(runtime, "_state", lambda *_args, **_kwargs: state)

    def no_io(*_args, **_kwargs):
        pytest.fail("native_transport_entered_before_demo_origin_proof")

    monkeypatch.setattr(runtime.ssl, "create_default_context", no_io)
    monkeypatch.setattr(runtime, "_verified_client", no_io)
    monkeypatch.setattr(runtime, "_event", no_io)
    invoke_entry = {
        "client": lambda: runtime._new_owned_client(object(), "quote"),
        "request": lambda: runtime._request_scope(object(), object(), object(), None),
        "ws": lambda: runtime._ws_options(object(), None),
    }[entry]
    with pytest.raises(
        runtime.PublicSourceRuntimeError,
        match="trusted_demo_public_origin_profile_unavailable",
    ):
        invoke_entry()
    assert state == {"plan": plan}


@pytest.mark.asyncio
async def test_post_g12_v2_demo_capture_refuses_before_new_public_io(
    inputs, tmp_path, monkeypatch
):
    source, values, run = inputs
    _, directory, harness, publications = synthetic_runtime(monkeypatch, inputs)

    def unexpected_io(*_args, **_kwargs):
        raise AssertionError("post_g12_v2_capture_entered_io")

    monkeypatch.setattr(runtime, "_runtime_attempt", unexpected_io)
    monkeypatch.setattr(runtime, "_new_owned_client", unexpected_io)
    monkeypatch.setattr(runtime, "_ws_options", unexpected_io)
    result = await coordinator.publish_capture_public_v2(
        tmp_path / "g12",
        tmp_path / "fresh",
        source.market,
        run=run,
        original_inputs=values,
        market_policy=policy(),
    )
    assert result.code == "public_v2_denied"
    assert result.evidence is not None and result.evidence.result.evidence_complete
    assert len(publications) == 1
    assert result.admission == "DENY" and result.execution_authority is False
    assert result.packet is None and result.journal_sha256 is None
    assert not harness.requests and not directory.content
    empty_registries()


def replay(directory):
    return runtime.replay_public_runtime(
        directory, expected_plan_sha256=sha(directory.content["plan.json"])
    )


def empty_registries():
    assert not initial._INITIAL_SCOPES
    assert not coordinator._PUBLICATIONS
    assert not runtime._SOURCES
    assert not runtime._INITIAL_RESULTS
    assert not runtime._RESULTS


@pytest.fixture(scope="module")
def source():
    return v2_engine_source("long")


@pytest.fixture(scope="module")
async def captured(source, tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        _, directory, harness, publications = setup(patch, source)
        result = await invoke(tmp_path_factory.mktemp("v2-owned"))
        assert result.code == "initial_public_v2_captured_g1_metadata_account_required"
        assert result.packet is not None and result.context is not None
        harness.assert_closed()
        assert not publications
        empty_registries()
        return result, dict(directory.content)


def test_initial_owner_replays_all_components_and_returns_no_permit(captured):
    result, content = captured
    summary, events, packet = replay(MemoryDirectory(content))
    plan = decode(content["plan.json"])
    document = decode(packet.packet_json, public.MAX_PACKET_BYTES)
    assert summary["disposition"] == "captured"
    assert plan["schema_version"] == "ctcc.public.initial_runtime_plan.v2"
    assert plan["ws_origin"] == "wss://ws.okx.com:443/ws/v5/public"
    assert document["invocation_id"] == plan["invocation_id"]
    assert result.journal_sha256 == sha(content["summary.json"])
    assert not {
        "candidate_sha256",
        "event_key",
        "barrier",
        "publication_completed_at",
    }.intersection(plan)
    assert result.admission == "DENY" and result.original_source_verified is False
    assert result.execution_authority is False and result.account_complete is False
    requests = [item["metadata"] for item in events if item["kind"] == "request"]
    assert {item["role"] for item in requests} == {"quote", "candles", "market_aux"}
    assert len(requests) == 9
    assert {
        dict(item["query"])["bar"] for item in requests if item["role"] == "candles"
    } == {"4H", "1H", "15m", "5m"}
    assert all(
        item["started"]["utc_ns"] > plan["invocation_started"]["utc_ns"]
        for item in requests
    )
    assert all(
        item["started"]["monotonic_ns"] > plan["invocation_started"]["monotonic_ns"]
        for item in requests
    )
    assert (
        result.context.quote.funding.exchange_data_return_at
        < result.context.quote.funding.acquisition_started_at
    )
    assert (
        result.context.quote.funding.forecast.settlement_at
        < result.context.market.next_funding_time
    )
    assert result.context.market.ticker.volume_quote_24h is None
    assert packet.packet_json == result.packet.packet_json


def test_historical_v2_replay_remains_deny(captured):
    result, content = captured
    _, _, packet = replay(MemoryDirectory(content))
    assert packet.admission == "DENY" and packet.execution_authority is False
    assert result.context.admission == "DENY"
    assert result.context.original_source_verified is False
    assert result.context.execution_authority is False


def test_v2_does_not_enter_old_packet_validator(captured):
    with pytest.raises(ValueError):
        legacy.validate_collected_public_market(captured[0].packet)


def test_context_cannot_extend_freshness_or_change_raw_funding_pair(captured):
    packet = captured[0].packet
    now = captured[0].context.evaluated_at
    with pytest.raises(ValueError):
        public_market_context_v2(
            packet,
            expected_bundle_sha256=packet.bundle_sha256,
            evaluated_at=now + timedelta(seconds=6),
        )
    context = public_market_context_v2(
        packet, expected_bundle_sha256=packet.bundle_sha256, evaluated_at=now
    )
    context.market.candles.clear()
    fresh = public_market_context_v2(
        packet, expected_bundle_sha256=packet.bundle_sha256, evaluated_at=now
    )
    assert len(fresh.market.candles) == 4 and not fresh.original_source_verified


@pytest.mark.parametrize(
    "kind",
    [
        initial._InitialScopeV2,
        coordinator._PublicationV2,
        runtime._CapturedInitialPublicV2,
        runtime._CapturedPublicV2,
    ],
)
@pytest.mark.parametrize("transfer", [copy.copy, copy.deepcopy, pickle.dumps])
def test_v2_private_types_cannot_be_transferred(kind, transfer):
    with pytest.raises(ValueError):
        transfer(object.__new__(kind))


@pytest.mark.asyncio
async def test_genuine_initial_scope_and_carrier_are_stage_version_and_one_use_bound(
    source, tmp_path, monkeypatch
):
    _, directory, harness, _ = setup(monkeypatch, source)
    capture = initial._capture_initial_public_v2
    consume = initial._consume_initial_public_capture_v2

    async def checked_capture(scope, selected, root):
        for call in (
            runtime._capture_initial_public,
            runtime._capture_after_publication,
            runtime._capture_after_publication_v2,
        ):
            with pytest.raises(ValueError):
                await call(scope, selected, root)
        carrier = await capture(scope, selected, root)
        with pytest.raises(ValueError):
            await capture(scope, selected, root)
        return carrier

    def checked_consume(carrier, invocation):
        for call in (
            runtime._consume_initial_public_capture,
            runtime._consume_public_capture,
            runtime._consume_public_capture_v2,
        ):
            with pytest.raises(ValueError):
                call(carrier, invocation)
        outcome = consume(carrier, invocation)
        with pytest.raises(ValueError):
            consume(carrier, invocation)
        return outcome

    monkeypatch.setattr(initial, "_capture_initial_public_v2", checked_capture)
    monkeypatch.setattr(initial, "_consume_initial_public_capture_v2", checked_consume)
    assert (await invoke(tmp_path)).context is not None
    assert replay(directory)[0]["disposition"] == "captured"
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["scope", "handoff"])
async def test_foreign_task_consumption_burns_real_v2_capability(
    source, tmp_path, monkeypatch, point
):
    _, directory, harness, _ = setup(monkeypatch, source)
    capture = initial._capture_initial_public_v2

    async def changed(scope, selected, root):
        if point == "scope":

            async def foreign_scope():
                with pytest.raises(ValueError):
                    initial._take_initial_scope_v2(scope)

            await asyncio.create_task(foreign_scope())
        carrier = await capture(scope, selected, root)
        if point == "handoff":

            async def foreign_handoff():
                with pytest.raises(ValueError):
                    runtime._consume_initial_public_capture_v2(carrier, object())

            await asyncio.create_task(foreign_handoff())
        return carrier

    monkeypatch.setattr(initial, "_capture_initial_public_v2", changed)
    result = await invoke(tmp_path)
    assert result.context is None and result.code == "initial_public_v2_denied"
    if point == "scope":
        assert not directory.content and not harness.requests
    else:
        assert replay(directory)[0]["disposition"] == "captured"
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_cancellation_retains_rejected_journal_and_never_restarts(
    source, tmp_path, monkeypatch
):
    _, directory, harness, _ = setup(monkeypatch, source)
    harness.public_hold = asyncio.Event()
    task = asyncio.create_task(invoke(tmp_path))
    await asyncio.wait_for(harness.started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(harness.requests) == 1
    assert replay(directory)[0]["disposition"] == "rejected"
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["processing", "future-source"])
async def test_failed_funding_keeps_actual_raw_and_burned_attempt(
    source, tmp_path, monkeypatch, failure
):
    _, directory, harness, _ = setup(monkeypatch, source)
    original = harness.public

    def rejected(request):
        rows = original(request)
        if request.url.path.endswith("/funding-rate"):
            if failure == "processing":
                rows[0]["settState"] = "processing"
            else:
                rows[0]["ts"] = ms(harness.clock.last + timedelta(seconds=1))
        return rows

    harness.public = rejected
    result = await invoke(tmp_path)
    assert result.context is None and result.admission == "DENY"
    summary, _, packet = replay(directory)
    assert summary["disposition"] == "rejected" and packet is None
    assert any(
        b'"fundingRate"' in raw
        for name, raw in directory.content.items()
        if name.endswith(".raw")
    )
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["scope", "handoff"])
async def test_v2_expiry_does_not_resume_a_saved_packet(
    source, tmp_path, monkeypatch, point
):
    clock, directory, harness, _ = setup(monkeypatch, source)
    original = initial._capture_initial_public_v2

    async def expire(scope, selected, root):
        if point == "scope":
            clock.count += 31000
        carrier = await original(scope, selected, root)
        if point == "handoff":
            clock.count += 31000
        return carrier

    monkeypatch.setattr(initial, "_capture_initial_public_v2", expire)
    result = await invoke(tmp_path)
    assert result.context is None and result.admission == "DENY"
    if point == "scope":
        assert not harness.requests
    else:
        assert replay(directory)[0]["disposition"] == "captured"
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["caller-policy", "owned-policy", "owned-profile"])
async def test_v2_owned_plan_is_pinned_before_transport(
    source, tmp_path, monkeypatch, mutation
):
    _, directory, harness, _ = setup(monkeypatch, source)
    selected = policy()
    original = initial._capture_initial_public_v2

    async def change(scope, owned, root):
        if mutation == "caller-policy":
            object.__setattr__(selected, "total_timeout_seconds", 60)
        elif mutation == "owned-policy":
            object.__setattr__(owned, "total_timeout_seconds", 60)
        else:
            initial._INITIAL_SCOPES[scope]["plan"]["quote_profile_sha256"] = "0" * 64
        return await original(scope, owned, root)

    monkeypatch.setattr(initial, "_capture_initial_public_v2", change)
    result = await initial.capture_initial_public_market_v2(
        tmp_path, instrument_id="BTC-USDT-SWAP", market_policy=selected
    )
    if mutation == "caller-policy":
        assert result.context is not None
        assert decode(directory.content["plan.json"])[
            "policy_sha256"
        ] == public._policy_digest(policy())
    else:
        assert result.context is None and not harness.requests and not directory.content
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "supplied", ["scope", "clock", "client", "packet", "receipt", "report_id"]
)
async def test_initial_public_entry_has_no_caller_ownership_inputs(tmp_path, supplied):
    with pytest.raises(TypeError):
        await initial.capture_initial_public_market_v2(
            tmp_path,
            instrument_id="BTC-USDT-SWAP",
            market_policy=policy(),
            **{supplied: object()},
        )


@pytest.mark.asyncio
async def test_actual_v2_publisher_flow_precedes_all_new_requests(
    inputs, tmp_path, monkeypatch
):
    source, values, run = inputs
    clock, directory, harness, publications = setup(
        monkeypatch, source, original=inputs
    )
    fresh_funding_return(harness, clock)

    def published_first():
        assert len(publications) == 1

    harness.before_request = published_first
    result = await coordinator.publish_capture_public_v2(
        tmp_path / "g12",
        tmp_path / "fresh",
        source.market,
        run=run,
        original_inputs=values,
        market_policy=policy(),
    )
    assert result.context is not None and result.evidence.result.evidence_complete
    assert result.current_g1 is not None and result.current_g1.passed
    assert result.final_g1 is not None and result.final_g1.passed
    assert result.final_g1.evaluated_at == result.projected_economics.observed_at
    assert result.code == "public_v2_base_projected_economics_passed_account_required"
    assert result.current_g1.public_bundle_sha256 == result.packet.bundle_sha256
    assert result.current_g1.report_id == result.evidence.result.report_id
    assert result.current_g1.evaluated_at > result.evidence.receipt.completed_at
    assert (
        data_v2.verify_public_market_data_v2(
            result.current_g1,
            result.packet,
            expected_bundle_sha256=result.packet.bundle_sha256,
            policy=values["policy"].prefix.data,
            evaluated_at=result.current_g1.evaluated_at,
        )
        == result.current_g1
    )
    assert result.current_conditions is not None and result.current_conditions.passed
    assert result.current_conditions.original_event_key
    assert (
        result.current_conditions.current_g1_sha256
        == result.current_g1.evaluation_sha256
    )
    assert (
        result.current_conditions.result.candidate_entry
        == values["intent"].candidate_entry
    )
    assert result.current_conditions.result.stop_loss is None
    assert result.current_conditions.result.take_profit is None
    assert result.current_conditions.original_event_survival_verified is False
    assert result.current_conditions.execution_authority is False
    assert result.original_event_zone is not None and result.original_event_zone.passed
    assert result.original_event_zone.continuation.original_event_key == (
        result.current_conditions.original_event_key
    )
    assert result.original_event_zone.continuation.complete_path_verified is False
    assert result.original_event_zone.original_event_survival_verified is False
    assert result.original_event_zone.execution_authority is False
    costs = result.projected_economics
    assert costs is not None and costs.projected_math_passed
    assert costs.candidate.entry == values["intent"].candidate_entry
    assert costs.execution.entry == (
        result.context.quote.ticker.ask
        if source.direction == "long"
        else result.context.quote.ticker.bid
    )
    assert (
        costs.original_stop_loss
        == costs.candidate.stop_loss
        == costs.execution.stop_loss
    )
    assert (
        costs.original_take_profit
        == costs.candidate.take_profit
        == costs.execution.take_profit
    )
    assert costs.actual_account_fee_bps is None
    assert costs.actual_account_funding is None
    assert costs.actual_margin_cost is None
    assert costs.fixed_protection_rechecked is False
    assert costs.admission == "DENY" and costs.execution_authority is False
    assert (
        not result.original_source_verified and not result.execution_recheck_performed
    )
    assert not result.execution_authority and result.admission == "DENY"
    summary, events, packet = replay(directory)
    plan = decode(directory.content["plan.json"])
    assert summary["disposition"] == "captured"
    assert plan["schema_version"] == "ctcc.public.runtime_plan.v2"
    assert (
        plan["publication_completed_at"]
        == result.evidence.receipt.completed_at.isoformat()
    )
    assert (
        decode(packet.packet_json, public.MAX_PACKET_BYTES)["barrier_completed_at"]
        == plan["publication_completed_at"]
    )
    for event in events:
        if event["kind"] not in ("request", "ws_connect"):
            continue
        stamp = event["metadata"]["started"]
        assert all(
            stamp[key] > plan["barrier"][key] for key in ("utc_ns", "monotonic_ns")
        )
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_current_g1_rejects_new_wide_spread_without_changing_original(
    inputs, tmp_path, monkeypatch
):
    source, values, run = inputs
    _, directory, harness, publications = setup(monkeypatch, source, original=inputs)
    normal_ticker = harness.ticker

    def wide_ticker():
        row = normal_ticker()
        row["askPx"] = str(Decimal(row["bidPx"]) * Decimal("1.05"))
        return row

    harness.ticker = wide_ticker
    result = await coordinator.publish_capture_public_v2(
        tmp_path / "g12",
        tmp_path / "fresh",
        source.market,
        run=run,
        original_inputs=values,
        market_policy=policy(),
    )
    assert len(publications) == 1
    assert result.evidence is not None and result.evidence.result.evidence_complete
    assert result.current_g1 is not None and not result.current_g1.passed
    assert result.current_conditions is None
    assert result.original_event_zone is None
    assert result.projected_economics is None
    assert result.code == "public_v2_current_g1_rejected"
    assert result.admission == "DENY" and result.execution_authority is False
    assert result.atomic_risk_reserved is False and result.order_submitted is False
    assert replay(directory)[0]["disposition"] == "captured"
    harness.assert_closed()
    empty_registries()


@pytest.fixture
def opposite_source(inputs):
    source = inputs[0]
    return v2_engine_source("short" if source.direction == "long" else "long")


@pytest.mark.asyncio
async def test_new_opposite_market_cannot_requalify_original_direction(
    inputs, opposite_source, tmp_path, monkeypatch
):
    source, values, run = inputs
    _, directory, harness, publications = setup(monkeypatch, source, original=inputs)
    harness.source = opposite_source
    result = await coordinator.publish_capture_public_v2(
        tmp_path / "g12",
        tmp_path / "fresh",
        source.market,
        run=run,
        original_inputs=values,
        market_policy=policy(),
    )
    assert len(publications) == 1
    assert result.evidence is not None and result.evidence.result.evidence_complete
    assert result.current_g1 is not None and result.current_g1.passed
    assert result.current_conditions is not None
    assert not result.current_conditions.passed
    assert result.code == "public_v2_current_g2_g4_rejected"
    assert (
        result.current_conditions.result.candidate_entry
        == values["intent"].candidate_entry
    )
    assert result.current_conditions.original_event_survival_verified is False
    assert result.original_event_zone is None
    assert result.projected_economics is None
    assert result.admission == "DENY" and result.execution_authority is False
    assert replay(directory)[0]["disposition"] == "captured"
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_revised_original_candle_does_not_prove_same_event(
    inputs, tmp_path, monkeypatch
):
    source, values, run = inputs
    _, directory, harness, publications = setup(monkeypatch, source, original=inputs)
    changed = copy.deepcopy(source)
    row = changed.market.candles["4H"][0]
    changed.market.candles["4H"][0] = row.model_copy(
        update={"volume_contracts": row.volume_contracts + Decimal(1)}
    )
    harness.source = changed
    result = await coordinator.publish_capture_public_v2(
        tmp_path / "g12",
        tmp_path / "fresh",
        source.market,
        run=run,
        original_inputs=values,
        market_policy=policy(),
    )
    assert len(publications) == 1
    assert result.current_g1 is not None and result.current_g1.passed
    assert result.current_conditions is not None and result.current_conditions.passed
    assert result.original_event_zone is not None
    assert result.original_event_zone.event_code == "history_rewritten"
    assert result.original_event_zone.zone_code == "not_evaluated"
    assert result.projected_economics is None
    assert not result.original_event_zone.passed
    assert result.code == "public_v2_original_event_zone_rejected"
    assert result.admission == "DENY" and result.execution_authority is False
    assert replay(directory)[0]["disposition"] == "captured"
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_old_cost_policy_rejects_stale_v2_funding_return(
    inputs, tmp_path, monkeypatch
):
    source, values, run = inputs
    _, directory, harness, publications = setup(monkeypatch, source, original=inputs)
    result = await coordinator.publish_capture_public_v2(
        tmp_path / "g12",
        tmp_path / "fresh",
        source.market,
        run=run,
        original_inputs=values,
        market_policy=policy(),
    )
    assert len(publications) == 1
    assert result.current_g1 is not None and result.current_g1.passed
    assert result.current_conditions is not None and result.current_conditions.passed
    assert result.original_event_zone is not None and result.original_event_zone.passed
    costs = result.projected_economics
    assert costs is not None and not costs.projected_math_passed
    assert costs.code == "projected_quote_source_stale"
    assert costs.failure_stage == "quote"
    assert costs.candidate is None and costs.execution is None
    assert costs.actual_account_fee_bps is None
    assert costs.actual_account_funding is None
    assert result.code == "public_v2_projected_economics_rejected"
    assert result.admission == "DENY" and result.execution_authority is False
    assert replay(directory)[0]["disposition"] == "captured"
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_final_fence_rejects_quote_that_ages_out_after_projected_math(
    inputs, tmp_path, monkeypatch
):
    source, values, _ = inputs
    old = values["policy"]
    values = {
        **values,
        "policy": old.model_copy(
            update={
                "economics": old.economics.model_copy(
                    update={"maximum_quote_age_seconds": 3}
                )
            }
        ),
    }
    run = evaluate_pre_evidence(source.market, **values)
    assert run.pre_evidence_complete
    clock, directory, harness, publications = setup(
        monkeypatch, source, original=(source, values, run)
    )
    fresh_funding_return(harness, clock)
    original = coordinator.current_economics_v2.evaluate_current_projected_economics_v2
    calculations = []

    def age_after_first_calculation(*args, **kwargs):
        result = original(*args, **kwargs)
        calculations.append(result)
        if len(calculations) == 1:
            assert result.projected_math_passed
            clock.count += 2000  # 2 seconds; fixed V2 transport still accepts it.
        return result

    monkeypatch.setattr(
        coordinator.current_economics_v2,
        "evaluate_current_projected_economics_v2",
        age_after_first_calculation,
    )
    result = await coordinator.publish_capture_public_v2(
        tmp_path / "g12",
        tmp_path / "fresh",
        source.market,
        run=run,
        original_inputs=values,
        market_policy=policy(),
    )
    assert len(publications) == 1 and len(calculations) == 2
    assert result.code == "public_v2_projected_economics_rejected"
    assert result.projected_economics is calculations[-1]
    assert result.projected_economics.code == "projected_quote_source_stale"
    assert result.projected_economics.failure_stage == "quote"
    assert result.final_g1 is not None and result.final_g1.passed
    assert result.admission == "DENY" and result.execution_authority is False
    assert replay(directory)[0]["disposition"] == "captured"
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_final_fence_rejects_g1_that_ages_out_after_projected_math(
    inputs, tmp_path, monkeypatch
):
    source, values, _ = inputs
    old = values["policy"]
    values = {
        **values,
        "policy": old.model_copy(
            update={
                "prefix": old.prefix.model_copy(
                    update={
                        "data": old.prefix.data.model_copy(
                            update={"maximum_quote_age_seconds": 2}
                        )
                    }
                )
            }
        ),
    }
    run = evaluate_pre_evidence(source.market, **values)
    assert run.pre_evidence_complete
    clock, directory, harness, publications = setup(
        monkeypatch, source, original=(source, values, run)
    )
    fresh_funding_return(harness, clock)
    original = coordinator.current_economics_v2.evaluate_current_projected_economics_v2
    calculations = []

    def age_after_first_calculation(*args, **kwargs):
        result = original(*args, **kwargs)
        calculations.append(result)
        assert result.projected_math_passed
        clock.count += 2000
        return result

    monkeypatch.setattr(
        coordinator.current_economics_v2,
        "evaluate_current_projected_economics_v2",
        age_after_first_calculation,
    )
    result = await coordinator.publish_capture_public_v2(
        tmp_path / "g12",
        tmp_path / "fresh",
        source.market,
        run=run,
        original_inputs=values,
        market_policy=policy(),
    )
    assert len(publications) == 1 and len(calculations) == 1
    assert result.current_g1 is None
    assert result.current_conditions is None
    assert result.original_event_zone is None
    assert result.final_g1 is not None and not result.final_g1.passed
    assert result.final_g1.gate.code == "stale_market_data"
    assert result.final_g1.evaluated_at == result.observed_at
    assert result.code == "public_v2_current_g1_rejected_at_finish"
    assert result.projected_economics is None
    assert result.admission == "DENY" and result.execution_authority is False
    assert replay(directory)[0]["disposition"] == "captured"
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_already_present_g12_cannot_issue_v2_native_capture(
    inputs, tmp_path, monkeypatch
):
    from app.trade_evidence import gates

    source, values, run = inputs
    _, directory, harness, _ = setup(monkeypatch, source, original=inputs)
    publish = gates.publish_evidence
    monkeypatch.setattr(
        gates,
        "publish_evidence",
        lambda *a, **kw: publish(*a, **kw).model_copy(
            update={"status": "already_present"}
        ),
    )
    result = await coordinator.publish_capture_public_v2(
        tmp_path / "g12",
        tmp_path / "fresh",
        source.market,
        run=run,
        original_inputs=values,
        market_policy=policy(),
    )
    assert result.context is None and not directory.content and not harness.requests
    empty_registries()


@pytest.mark.asyncio
async def test_genuine_publication_and_handoff_are_version_stage_and_one_use_bound(
    inputs, tmp_path, monkeypatch
):
    source, values, run = inputs
    _, directory, harness, _ = setup(monkeypatch, source, original=inputs)
    capture = coordinator._capture_after_publication_v2
    consume = coordinator._consume_public_capture_v2
    issued = coordinator._publish_lineage_v2
    scopes = []

    def checked_issuer(*args, **kwargs):
        result = issued(*args, **kwargs)
        assert type(result[0]) is coordinator._PublicationV2
        scopes.append(result[0])
        return result

    async def checked_capture(scope, selected, root):
        assert scope is scopes[0]
        for call in (
            runtime._capture_after_publication,
            runtime._capture_initial_public,
            runtime._capture_initial_public_v2,
        ):
            with pytest.raises(ValueError):
                await call(scope, selected, root)
        carrier = await capture(scope, selected, root)
        with pytest.raises(ValueError):
            await capture(scope, selected, root)
        return carrier

    def checked_consume(carrier, invocation):
        for call in (
            runtime._consume_public_capture,
            runtime._consume_initial_public_capture,
            runtime._consume_initial_public_capture_v2,
        ):
            with pytest.raises(ValueError):
                call(carrier, invocation)
        result = consume(carrier, invocation)
        with pytest.raises(ValueError):
            consume(carrier, invocation)
        return result

    monkeypatch.setattr(coordinator, "_publish_lineage_v2", checked_issuer)
    monkeypatch.setattr(coordinator, "_capture_after_publication_v2", checked_capture)
    monkeypatch.setattr(coordinator, "_consume_public_capture_v2", checked_consume)
    result = await coordinator.publish_capture_public_v2(
        tmp_path / "g12",
        tmp_path / "fresh",
        source.market,
        run=run,
        original_inputs=values,
        market_policy=policy(),
    )
    assert result.context is not None and result.original_source_verified is False
    assert replay(directory)[0]["disposition"] == "captured"
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "supplied", ["publication", "barrier", "clock", "client", "packet", "receipt"]
)
async def test_post_public_entry_has_no_caller_ownership_inputs(tmp_path, supplied):
    with pytest.raises(TypeError):
        await coordinator.publish_capture_public_v2(
            tmp_path / "g12",
            tmp_path / "fresh",
            object(),
            run=object(),
            original_inputs=object(),
            market_policy=policy(),
            **{supplied: object()},
        )


def _replace_packet(directory, raw):
    def mutate(event, target):
        if event["kind"] == "packet":
            target.content[f"event-{event['index']:04d}.raw"] = raw
            event["raw_sha256"] = sha(raw)
            event["raw_bytes"] = len(raw)
            event["metadata"]["bundle_sha256"] = sha(raw)

    resign_journal(directory, mutate)
    summary = decode(directory.content["summary.json"])
    summary["packet_sha256"] = sha(raw)
    directory.content["summary.json"] = canonical(summary)


@pytest.mark.parametrize(
    "mutation",
    [
        "flag",
        "profile",
        "invocation",
        "stage",
        "missing-aux",
        "missing-ws",
        "quote-rate",
        "candle-tail",
    ],
)
def test_rehashed_packet_cannot_override_full_raw_semantic_replay(captured, mutation):
    result, content = captured
    directory = MemoryDirectory(dict(content))
    value = decode(result.packet.packet_json, public.MAX_PACKET_BYTES)
    if mutation == "flag":
        value["source_authenticity_verified"] = True
    elif mutation == "profile":
        value["quote_profile_sha256"] = "0" * 64
    elif mutation == "invocation":
        value["invocation_id"] = "0" * 32
    elif mutation == "stage":
        value["stage"] = "post_publication"
    elif mutation == "missing-aux":
        value["market_aux"]["provenance"].pop()
    elif mutation == "missing-ws":
        value["ws"].pop("ack")
    elif mutation == "quote-rate":
        funding = value["quote"]["inspection"]["quote_document"]["funding"]
        original_rate = funding["rate"]
        funding["rate"] = "0.001" if original_rate != "0.001" else "0"
        assert funding["rate"] != original_rate
    else:
        value["candles"]["frames"][0]["verified_through"] = "2024-01-01T00:00:00+00:00"
    mutated_raw = canonical(value)
    assert mutated_raw != result.packet.packet_json
    _replace_packet(directory, mutated_raw)
    with pytest.raises((ValueError, KeyError)):
        replay(directory)
    empty_registries()


def test_coherent_new_quote_bytes_cannot_replace_journal_http_observation(captured):
    result, content = captured
    packet, (selected, quote, provenance, candle_packet, auxiliary, reference) = (
        public._parts(result.packet)
    )
    old = provenance[0]
    changed_raw = old.response_body + b" "
    changed = old.model_copy(
        update={
            "response_body": changed_raw,
            "body_sha256": sha(changed_raw),
            "body_size_bytes": len(changed_raw),
        }
    )
    value = decode(packet.packet_json, public.MAX_PACKET_BYTES)
    new_quote = quotes.build_diagnostic_quote_packet_v2(
        report_id=quote.report_id,
        instrument_id=quote.instrument_id,
        environment=quote.environment,
        observations=(changed, *provenance[1:]),
        capture_started_at=quote.capture_started_at,
        capture_completed_at=quote.capture_completed_at,
        barrier_completed_at=None,
    )
    rebuilt, _ = public._build(
        scope={
            key: value[key]
            for key in (
                "invocation_id",
                "stage",
                "environment",
                "report_id",
                "instrument_id",
                "barrier_completed_at",
            )
        },
        policy=selected,
        quote=new_quote,
        candle_packet=candle_packet,
        market_aux=auxiliary,
        reference=reference,
        started=public._time(value["started_at"]),
        completed=public._time(value["completed_at"]),
    )
    assert (
        public.replay_collected_public_market_v2(
            rebuilt.packet_json, expected_sha256=rebuilt.bundle_sha256
        )
        == rebuilt
    )
    directory = MemoryDirectory(dict(content))
    _replace_packet(directory, rebuilt.packet_json)
    with pytest.raises(ValueError, match="raw_join_mismatch"):
        journal.replay_runtime_attempt(
            directory, expected_plan_sha256=sha(directory.content["plan.json"])
        )


@pytest.mark.parametrize(
    "mutation", ["candidate-in-initial", "wrong-stage", "old-schema", "quote-schema"]
)
def test_versioned_initial_plan_is_exact(captured, mutation):
    plan = decode(captured[1]["plan.json"])
    if mutation == "candidate-in-initial":
        plan["candidate_sha256"] = "1" * 64
    elif mutation == "wrong-stage":
        plan["stage"] = "post_publication"
    elif mutation == "old-schema":
        plan["schema_version"] = "ctcc.public.initial_runtime_plan.v1"
    else:
        plan["quote_collector_schema"] = "ctcc.collected_executable_quote.v1"
    with pytest.raises(ValueError):
        journal._validate_plan(plan)


def test_versioned_plan_keeps_legacy_8443_receipt_replay(captured):
    plan = decode(captured[1]["plan.json"])
    plan["ws_origin"] = "wss://ws.okx.com:8443/ws/v5/public"
    journal._validate_plan(plan)


@pytest.mark.asyncio
async def test_offline_replay_and_output_never_recreate_owned_capability(captured):
    result, content = captured
    replayed = replay(MemoryDirectory(content))[2]
    for value in (
        result,
        result.context,
        result.packet,
        replayed,
        decode(replayed.packet_json, public.MAX_PACKET_BYTES),
    ):
        with pytest.raises(ValueError):
            initial._take_initial_scope_v2(value)
        with pytest.raises(ValueError):
            runtime._consume_initial_public_capture_v2(value, object())
    empty_registries()
