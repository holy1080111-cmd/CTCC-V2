"""Synthetic native providers and real raw/gate math; no native acceptance."""

import asyncio
import copy
import inspect
import json
import pickle
import time
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import account_capture, data_v2
from app.trade_qualification import account_current_source_v4 as account
from app.trade_qualification import demo_market_source_v4 as market
from app.trade_qualification import demo_operational_route_v4 as route
from app.trade_qualification import original_candidate_precursor_v2 as precursor
from app.trade_qualification import owned_current_original_frame_v1 as frame
from app.trade_qualification import owned_current_original_prefix_v1 as module
from app.trade_qualification.account_observation_index import CaptureReference
from app.trade_qualification.native_original_g1_policy_v1 import (
    DATA_POLICY_SHA256,
    fixed_native_original_g1_policy,
)
from tests.unit import qualification_prefix_fixtures as prefix_fixture
from tests.unit import test_original_candidate_policy as original_fixture
from tests.unit.research import test_owned_current_original_frame_v1 as current_fixture
from tests.unit.test_original_candidate_precursor_v2 import capture_raw_source, inputs
from tests.unit.test_qualification_market_bridge import v2_engine_source


@pytest.fixture(scope="module")
def raw_market():
    source = v2_engine_source("long")
    assert source.strategy == "fvg_return"
    return source


@pytest.fixture(scope="module")
def arithmetic_replay(tmp_path_factory):
    """Declared raw replay data, not registered as any native frame/owner.

    The exact account replay dataclass deliberately retains all false authority
    fields. This fixture only exercises the private pure mathematical function;
    public issuer tests below use both original V4 native-provider issuers.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(prefix_fixture, "CAPTURED_AT", original_fixture.NOW)
        source = replace(
            v2_engine_source("long"),
            evaluated_at=original_fixture.NOW + timedelta(milliseconds=10),
        )
    diagnostic = capture_raw_source(source, tmp_path_factory.mktemp("opx-math"))
    packet = original_fixture.account_source()
    policy = fixed_native_original_g1_policy()
    args = inputs(
        diagnostic,
        packet,
        data_policy=policy,
        expected_data_policy_sha256=DATA_POLICY_SHA256,
    )
    original = precursor.derive_original_candidate_precursor_v2(
        diagnostic.packet, packet, **args
    )
    assert original.intent is not None
    frozen = account_capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    reference = CaptureReference(
        capture_id="1" * 32,
        head_sha256="2" * 64,
        plan_sha256=packet.plan_sha256,
        packet_sha256=frozen.sha256,
        session_binding_sha256="3" * 64,
    )
    private = account.RecordedCurrentAccountBundleV4(
        packet,
        reference,
        b"{}",
        "4" * 64,
        "5" * 64,
        "6" * 64,
        args["service_deadline"],
        1,
    )
    g1 = data_v2.evaluate_public_market_data_v2(
        diagnostic.packet,
        expected_bundle_sha256=diagnostic.packet.bundle_sha256,
        policy=policy,
        evaluated_at=args["created_at"],
    )
    assert g1.passed
    return (
        diagnostic.packet,
        private,
        diagnostic.context,
        g1,
        original,
        args["created_at"],
        args["service_deadline"],
    )


def _changed_arithmetic(
    replay, *, source_change=None, intent_change=None, doc_change=None
):
    """A pure diagnostic mutation, never offered to a native source issuer."""
    packet, private, context, g1, original, created, deadline = replay
    document = decode(original.receipt_json)
    intent = original.intent
    if source_change is not None:
        source = decode(g1.source_json.encode())
        source_change(source)
        raw = canonical(source)
        fields = g1.model_dump(mode="python")
        fields.update(source_json=raw.decode(), source_sha256=sha(raw))
        g1 = data_v2.DataQualificationResultV2.model_validate(fields, strict=True)
        document["g1"].update(
            evaluation_sha256=g1.evaluation_sha256,
            source_sha256=g1.source_sha256,
        )
    if intent_change is not None:
        intent = intent.model_copy(update=intent_change)
        document["intent"] = precursor._intent_tree(intent)
    if doc_change is not None:
        doc_change(document)
    original = precursor.OriginalCandidatePrecursorV2(intent, canonical(document))
    return packet, private, context, g1, original, created, deadline


def test_pure_prefix_computes_full_typed_original_g1_g5(arithmetic_replay):
    assert not module._PREFIXES
    result = module._compute(arithmetic_replay)
    assert tuple(gate.gate.value for gate in result.result.gates) == (
        "G1",
        "G2",
        "G3",
        "G4",
        "G5",
    )
    assert all(gate.passed for gate in result.result.gates)
    assert result.result.trigger == result.detection.trigger
    assert result.result.entry_timing_state == "not_evaluated"
    assert result.result.entry_zone is None  # G7 is not evaluated.
    assert result.original_zone.expires_at == result.intent.expires_at
    assert result.result.stop_loss is None and result.result.take_profit is None
    record = decode(result.receipt_json)
    assert record["event_key"] == decode(arithmetic_replay[4].receipt_json)["event_key"]
    assert all(record[name] is False for name in module._FALSE_FIELDS)
    assert not module._PREFIXES  # Pure arithmetic can never mint the carrier.


@pytest.mark.parametrize("case", ["G2", "G3", "G4-required", "G4-veto", "G5"])
def test_actual_shared_math_failures_never_produce_a_prefix(arithmetic_replay, case):
    def change(source):
        analysis = source["analysis"]
        if case == "G2":
            analysis["blockers"] = ["synthetic_data_quality_failure"]
        elif case == "G4-required":
            analysis["timeframe_analyses"]["15m"]["structure"]["fair_value_gaps"] = []
        elif case == "G4-veto":
            # Existing routing preserves this blocker; real condition quality
            # veto must still reject despite the other 85 points.
            analysis["blockers"] = ["multi_timeframe_not_aligned"]
        elif case == "G5":
            for row in source["market"]["candles"]["5m"]:
                row.update(open="100", high="100", low="100", close="100")

    changed = _changed_arithmetic(
        arithmetic_replay,
        source_change=None if case == "G3" else change,
        intent_change={"direction": "short"} if case == "G3" else None,
    )
    with pytest.raises(
        module.CurrentOriginalPrefixError, match="original_prefix_" + case[:2].lower()
    ):
        module._compute(changed)
    assert not module._PREFIXES


@pytest.mark.parametrize(
    "case", ["entry", "expiry", "event", "timeline", "zone", "rules"]
)
def test_changed_original_lineage_is_not_repaired(arithmetic_replay, case):
    def change(document):
        if case == "event":
            document["event_key"] = "0" * 64
        elif case == "timeline":
            document["timeline_sha256"] = "0" * 64
        elif case == "zone":
            document["zone"]["expires_at"] = document["created_at"]
        elif case == "rules":
            document["instrument_rules_sha256"] = "0" * 64

    intent = arithmetic_replay[4].intent
    changes = (
        {"candidate_entry": intent.candidate_entry + 1}
        if case == "entry"
        else (
            {"expires_at": intent.expires_at + timedelta(seconds=1)}
            if case == "expiry"
            else None
        )
    )
    changed = _changed_arithmetic(
        arithmetic_replay, intent_change=changes, doc_change=change
    )
    with pytest.raises((module.CurrentOriginalPrefixError, ValueError)):
        module._compute(changed)
    assert not module._PREFIXES


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_opaque_construction_and_saved_values_do_not_mint(operation):
    # Deliberately inert caller-made values: no raw fixture, capture or math.
    forged_receipt = canonical({"passed": True, "execution_authority": True})
    computed = module.CurrentOriginalBasePrefixInputsV1(
        None, None, None, None, None, forged_receipt
    )
    for value in (
        computed,
        computed.receipt_json,
        decode(computed.receipt_json),
        object(),
    ):
        with pytest.raises(module.CurrentOriginalPrefixError):
            module.consume_current_original_base_prefix_v1(value)
        with pytest.raises(module.CurrentOriginalPrefixError):
            module.consume_owned_current_original_base_prefix_v1(value)
    forged = object.__new__(module.OwnedCurrentOriginalBasePrefixV1)
    with pytest.raises(module.CurrentOriginalPrefixError):
        operation(forged)
    with pytest.raises(module.CurrentOriginalPrefixError):
        module.OwnedCurrentOriginalBasePrefixV1()
    assert not module._PREFIXES


def test_public_signatures_have_no_result_clock_policy_or_callback_overrides():
    assert tuple(
        inspect.signature(module.consume_current_original_base_prefix_v1).parameters
    ) == ("original_frame",)
    assert tuple(
        inspect.signature(
            module.consume_owned_current_original_base_prefix_v1
        ).parameters
    ) == ("owner",)
    with pytest.raises(frame.CurrentOriginalFrameError):
        frame._take_original_base_prefix_v1(object(), key=object())


@pytest.mark.asyncio
async def test_real_v4_capture_positive_prefix_and_replay_cross_use(
    monkeypatch, tmp_path, raw_market
):
    started = time.perf_counter_ns()

    def marker(phase):
        print(
            json.dumps(
                {
                    "schema": "ctcc.original_prefix_component_phase.v1",
                    "phase": phase,
                    "elapsed_ns": time.perf_counter_ns() - started,
                },
                sort_keys=True,
            ),
            flush=True,
        )

    marker("capture-before")
    with current_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        invocation = frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        )
        original = await invocation.capture()
        marker("capture-complete")
        owner = module.consume_current_original_base_prefix_v1(original)
        marker("prefix-earned")
        with pytest.raises(frame.CurrentOriginalFrameError):
            frame.consume_current_original_inputs_v1(original)
        with pytest.raises(module.CurrentOriginalPrefixError):
            module.consume_current_original_base_prefix_v1(original)
        with pytest.raises(market.CurrentMarketSourceV4Error):
            market.evaluate_owned_current_market_v4(state.held["public_owner"])
        with pytest.raises(account.CurrentAccountSourceV4Error):
            account.consume_owned_current_demo_account_v4(state.held["account_owner"])
        computed = module.consume_owned_current_original_base_prefix_v1(owner)
        marker("handoff-complete")
        assert state.calls == ["public", "account"]
        assert all(gate.passed for gate in computed.result.gates)
        assert computed.result.candidate_entry == computed.intent.candidate_entry
        assert all(
            decode(computed.receipt_json)[name] is False
            for name in module._FALSE_FIELDS
        )
        for value in (owner, computed, computed.receipt_json):
            with pytest.raises(module.CurrentOriginalPrefixError):
                module.consume_owned_current_original_base_prefix_v1(value)
        assert state.held["accepted_account_bundle"]
        assert state.held["account_native_files"]
        assert not frame._FRAMES and not module._PREFIXES and not route._OWNED_ROUTES


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["expiry", "cancel"])
async def test_late_math_failure_burns_frame_preserves_journals(
    monkeypatch, tmp_path, raw_market, case
):
    with current_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        original = await frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        ).capture()
        compute = module._compute

        def late(replay):
            computed = compute(replay)
            if case == "cancel":
                raise asyncio.CancelledError
            state.held["public_fixture"][0].count += 31_000
            return computed

        monkeypatch.setattr(module, "_compute", late)
        with pytest.raises(
            asyncio.CancelledError
            if case == "cancel"
            else module.CurrentOriginalPrefixError
        ):
            module.consume_current_original_base_prefix_v1(original)
        with pytest.raises(frame.CurrentOriginalFrameError):
            frame.consume_current_original_inputs_v1(original)
        assert state.held["accepted_account_bundle"]
        assert state.held["account_native_files"]
        assert state.calls == ["public", "account"]
        assert not frame._FRAMES and not module._PREFIXES and not route._OWNED_ROUTES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["foreign_task", "lease", "raw", "database", "receipt", "close"]
)
async def test_owned_prefix_current_context_failure_is_burned(
    monkeypatch, tmp_path, raw_market, case
):
    with current_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        original = await frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        ).capture()
        owner = module.consume_current_original_base_prefix_v1(original)
        if case == "close":
            owner.close()
            owner.close()
        elif case == "foreign_task":

            async def other():
                module.consume_owned_current_original_base_prefix_v1(owner)

            with pytest.raises(module.CurrentOriginalPrefixError):
                await asyncio.create_task(other())
        else:
            if case == "lease":
                state.lease._lease.active = False
            elif case == "raw":
                public = state.held["public_state"]
                public["bundle_raw"] += b" "
                public["bundle_sha256"] = sha(public["bundle_raw"])
            elif case == "database":
                state.factory.kw["expire_on_commit"] = True
            else:
                module._PREFIXES[owner]["receipt_json"] += b" "
            with pytest.raises(module.CurrentOriginalPrefixError):
                module.consume_owned_current_original_base_prefix_v1(owner)
        with pytest.raises(module.CurrentOriginalPrefixError):
            module.consume_owned_current_original_base_prefix_v1(owner)
        assert state.held["accepted_account_bundle"]
        assert state.calls == ["public", "account"]
        assert not frame._FRAMES and not module._PREFIXES and not route._OWNED_ROUTES


@pytest.mark.asyncio
async def test_existing_dto_consumer_cannot_then_create_prefix(
    monkeypatch, tmp_path, raw_market
):
    with current_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        original = await frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        ).capture()
        dto = frame.consume_current_original_inputs_v1(original)
        for value in (original, dto, dto.receipt_json):
            with pytest.raises(module.CurrentOriginalPrefixError):
                module.consume_current_original_base_prefix_v1(value)
        assert not module._PREFIXES and not frame._FRAMES and not route._OWNED_ROUTES


@pytest.mark.asyncio
async def test_original_g1_without_an_intent_cannot_earn_prefix(
    monkeypatch, tmp_path, raw_market
):
    with current_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        # A distinct invocation deliberately keeps the older frame fixture's
        # preconfigured trend strategy. It is never changed after acquisition.
        assert state.arguments["strategy"] == "trend_pullback"
        original = await frame.open_current_original_capture_v1(
            **state.arguments
        ).capture()
        retained = frame._FRAMES[original]
        assert retained["g1"].passed and retained["precursor"].intent is None
        with pytest.raises(
            module.CurrentOriginalPrefixError,
            match="original_base_prefix_intent_required",
        ):
            module.consume_current_original_base_prefix_v1(original)
        assert state.held["accepted_account_bundle"]
        assert not module._PREFIXES and not frame._FRAMES and not route._OWNED_ROUTES
