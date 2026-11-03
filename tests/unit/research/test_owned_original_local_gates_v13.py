"""Synthetic providers / pure values only; no native, PG or account acceptance.

The pure arithmetic cases construct no owner. Ownership cases reuse the existing
explicit synthetic V4-provider fixture and never await the PostgreSQL reader.
Negative cases rebind guard globals only to require rejection. No substituted
guard table, Session listener or production admission supplies a positive case.
"""

import asyncio
import copy
import inspect
import json
import pickle
from dataclasses import replace
from datetime import timedelta
from decimal import Context, localcontext

import pytest

from app.database.repositories.qualification_ledger import (
    _ConsumedEventJournalInspection,
)
from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.source_primitives import canonical, decode
from app.strategies.structural_protection import select_structural_protection
from app.trade_qualification import owned_current_original_frame_v1 as frame
from app.trade_qualification import owned_current_original_prefix_v1 as module
from app.trade_qualification import quote_collector as legacy_quote
from app.trade_qualification import quote_collector_v2 as raw_quote_v2
from app.trade_qualification.captured_instrument_rules import (
    derive_captured_instrument_rules,
)
from app.trade_qualification.engine import ProtectionPolicy
from app.trade_qualification.executable_quote_v2 import ExecutableQuoteV2
from app.trade_qualification.models import QualificationGate
from tests.unit import test_qualification_quote_collector as legacy_quote_fixture
from tests.unit.research import test_owned_current_original_frame_v1 as native_fixture
from tests.unit.research import (
    test_owned_current_original_prefix_v1 as arithmetic_fixture,
)
from tests.unit.research.test_owned_current_original_prefix_v1 import (
    raw_market as raw_market,  # noqa: PLC0414 -- explicit pytest fixture binding.
)


@pytest.fixture(scope="module")
def arithmetic_replay(tmp_path_factory):
    return arithmetic_fixture._arithmetic_replay_values(
        tmp_path_factory, fresh_funding=True
    )


@pytest.fixture(scope="module")
def stale_funding_replay(tmp_path_factory):
    # Keep the original real synthetic wire's38-second return-age scenario.
    return arithmetic_fixture._arithmetic_replay_values(tmp_path_factory)


@pytest.fixture
def pure_gate_values(arithmetic_replay):
    return _pure_gate_values(arithmetic_replay)


def _pure_gate_values(arithmetic_replay):
    """Declared values, deliberately not registered as a held source owner."""
    computed = module._compute(arithmetic_replay)
    packet, private, context, _, _, created, _ = arithmetic_replay
    policy_raw = module._freeze_local_gate_policy_v13()
    policy = decode(policy_raw)
    protection = ProtectionPolicy.model_validate_json(
        json.dumps(policy["protection"]), strict=True
    )
    source = decode(computed.g1.source_json.encode())
    market = MarketSnapshot.model_validate_json(
        json.dumps(source["market"]), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(source["analysis"]), strict=True
    )
    rules = derive_captured_instrument_rules(
        private.packet,
        instrument_id=computed.intent.instrument_id,
        expected_plan_sha256=private.packet.plan_sha256,
        expected_packet_sha256=private.reference.packet_sha256,
    )
    selection = select_structural_protection(
        computed.detection,
        market,
        analysis,
        observed_at=created,
        entry=computed.intent.candidate_entry,
        tick_size=rules.tick_size,
        **{
            name: value
            for name, value in protection.model_dump().items()
            if name != "policy_id"
        },
    )
    quote_context = module._replay_local_quote_v13(
        packet, context.quote, computed, evaluated_at=created
    )
    record = {
        "computed": computed,
        "public_packet": packet,
        "quote_value": context.quote,
        "quote_context": quote_context,
        "market": market,
        "analysis": analysis,
        "protection": protection,
        "policy_raw": policy_raw,
        "tick_size": rules.tick_size,
        "created_at": created,
        "selection_raw": selection.to_audit_json(),
    }
    journal = _ConsumedEventJournalInspection(
        frozenset(), canonical({"ledger_revision": 0}), created, created
    )
    assert packet.bundle_sha256
    assert journal.execution_authority is False
    return record, journal, created


def test_original_cost_age_still_rejects_the_original_stale_funding_source(
    stale_funding_replay,
):
    record, journal, at = _pure_gate_values(stale_funding_replay)
    quote = record["quote_context"].quote
    assert (at - quote.funding.exchange_data_return_at).total_seconds() > 5
    assert decode(record["policy_raw"])["economics"]["maximum_quote_age_seconds"] == 5
    result = module._evaluate_original_local_math_v13(record, journal, observed_at=at)
    assert len(result.gates) == 10
    assert result.gates[-1].code == "projected_quote_source_stale"
    assert result.candidate_entry == record["computed"].intent.candidate_entry
    assert result.trigger == record["computed"].detection.trigger
    assert not any(gate.gate == QualificationGate.RISK for gate in result.gates)


def test_pure_actual_tail_uses_original_event_zone_and_never_registers_owner(
    pure_gate_values,
):
    record, journal, at = pure_gate_values
    before = (len(module._LOCAL_HOLDS_V13), len(module._LOCAL_OWNERS_V13))
    result = module._evaluate_original_local_math_v13(record, journal, observed_at=at)
    assert [gate.gate for gate in result.gates[:5]] == [
        gate.gate for gate in record["computed"].result.gates
    ]
    assert result.gates[5].gate == QualificationGate.TIMING and result.gates[5].passed
    assert result.gates[6].gate == QualificationGate.LOCATION and result.gates[6].passed
    assert len(result.gates) == 10 and all(gate.passed for gate in result.gates), (
        result.fail_codes
    )
    assert result.stop_loss < result.candidate_entry < result.take_profit
    assert result.gross_rr >= result.net_rr >= 2
    assert result.trigger == record["computed"].detection.trigger
    assert result.entry_zone == record["computed"].original_zone
    assert result.candidate_entry == record["computed"].intent.candidate_entry
    assert not any(gate.gate == QualificationGate.RISK for gate in result.gates)
    assert (len(module._LOCAL_HOLDS_V13), len(module._LOCAL_OWNERS_V13)) == before
    with pytest.raises(module.CurrentOriginalPrefixError):
        module.consume_owned_original_local_gates_v13(result)


def test_cost_failure_cannot_move_the_fixed_original_bracket(pure_gate_values):
    # Explicit pure-value stress scenario only. These changed values cannot enter
    # the native continuation, which compares the pre-capture policy bytes.
    record, journal, at = pure_gate_values
    original = module._evaluate_original_local_math_v13(record, journal, observed_at=at)
    policy = decode(record["policy_raw"])
    policy["economics"]["minimum_net_rr"] = "1000"
    changed = dict(record, policy_raw=canonical(policy))
    failed = module._evaluate_original_local_math_v13(changed, journal, observed_at=at)
    assert len(failed.gates) == 10 and failed.gates[-1].code == "net_rr_below_minimum"
    assert not failed.gates[-1].passed
    assert (failed.candidate_entry, failed.stop_loss, failed.take_profit) == (
        original.candidate_entry,
        original.stop_loss,
        original.take_profit,
    )
    assert (
        failed.trigger == original.trigger and failed.entry_zone == original.entry_zone
    )


def test_policy_and_real_gate_math_ignore_ambient_decimal_precision(pure_gate_values):
    record, journal, at = pure_gate_values
    expected_policy = module._freeze_local_gate_policy_v13()
    expected_result = module._evaluate_original_local_math_v13(
        record, journal, observed_at=at
    )
    with localcontext(Context(prec=2)):
        assert module._freeze_local_gate_policy_v13() == expected_policy
        actual = module._evaluate_original_local_math_v13(
            record, journal, observed_at=at
        )
    assert actual == expected_result


def test_local_consumed_event_stops_at_real_g6_without_bracket_repair(pure_gate_values):
    record, journal, at = pure_gate_values
    event = module.event_identity(record["computed"].detection)
    result = module._evaluate_original_local_math_v13(
        record, replace(journal, consumed_event_keys=frozenset({event})), observed_at=at
    )
    assert len(result.gates) == 6 and result.gates[-1].gate == QualificationGate.TIMING
    assert not result.gates[-1].passed and result.gates[-1].code == "stale_candidate"
    assert result.stop_loss is result.take_profit is None
    assert result.trigger == record["computed"].detection.trigger


def test_original_expiry_is_not_renewed_by_g6(pure_gate_values):
    record, journal, _ = pure_gate_values
    result = module._evaluate_original_local_math_v13(
        record, journal, observed_at=record["computed"].intent.expires_at
    )
    assert len(result.gates) == 6 and result.gates[-1].code == "stale_candidate"
    assert result.trigger.expires_at == record["computed"].detection.trigger.expires_at


def test_changed_original_bracket_audit_cannot_be_reselected(pure_gate_values):
    record, journal, at = pure_gate_values
    changed = dict(record, selection_raw=record["selection_raw"] + " ")
    with pytest.raises(module.CurrentOriginalPrefixError, match="bracket_changed"):
        module._evaluate_original_local_math_v13(changed, journal, observed_at=at)


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_no_saved_values_or_constructed_partial_owner_can_mint(operation):
    forged = object.__new__(module.OwnedCurrentOriginalLocalGatesV13)
    with pytest.raises(module.CurrentOriginalPrefixError):
        operation(forged)
    with pytest.raises(module.CurrentOriginalPrefixError):
        module.consume_owned_original_local_gates_v13(forged)
    for value in ({"passed": True}, b"receipt", object()):
        with pytest.raises(module.CurrentOriginalPrefixError):
            module.continue_owned_original_local_gates_v13(value)


def test_public_boundary_accepts_no_scope_clock_policy_gate_or_callback():
    assert tuple(
        inspect.signature(module.continue_owned_original_local_gates_v13).parameters
    ) == ("owner",)
    assert tuple(
        inspect.signature(module.consume_owned_original_local_gates_v13).parameters
    ) == ("owner",)
    assert module._BASE_STRATEGIES == frozenset(
        {"trend_pullback", "breakout_continuation", "fvg_return", "order_block_return"}
    )
    with pytest.raises(module.CurrentOriginalPrefixError, match="fixed_purpose"):
        module._local_journal_context_v13(object(), key=object())


@pytest.mark.asyncio
async def test_calltime_move_and_unstarted_close_release_original_sources(
    monkeypatch, tmp_path, raw_market
):
    with native_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        original = await frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        ).capture()
        owner = module.consume_current_original_base_prefix_v1(original)
        attempt = module.continue_owned_original_local_gates_v13(owner)
        assert owner not in module._PREFIXES and len(module._LOCAL_HOLDS_V13) == 1
        with pytest.raises(module.CurrentOriginalPrefixError):
            module.consume_owned_current_original_base_prefix_v1(owner)
        attempt.close()
        attempt.close()
        assert not module._LOCAL_HOLDS_V13 and not module._LOCAL_OWNERS_V13
        assert state.calls == ["public", "account"]  # No DB method was awaited.
        assert (
            state.held["accepted_account_bundle"] and state.held["account_native_files"]
        )


@pytest.mark.asyncio
async def test_throw_before_first_await_burns_and_sanitizes_cancel(
    monkeypatch, tmp_path, raw_market
):
    with native_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        original = await frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        ).capture()
        owner = module.consume_current_original_base_prefix_v1(original)
        attempt = module.continue_owned_original_local_gates_v13(owner)
        with pytest.raises(asyncio.CancelledError) as caught:
            attempt.throw(asyncio.CancelledError("DECLARED_SYNTHETIC_PRIVATE_ARGUMENT"))
        assert caught.value.args == ()
        assert not module._LOCAL_HOLDS_V13 and not module._LOCAL_OWNERS_V13


@pytest.mark.asyncio
async def test_cap_denies_before_original_owner_pop(monkeypatch, tmp_path, raw_market):
    with native_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        original = await frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        ).capture()
        owner = module.consume_current_original_base_prefix_v1(original)
        with monkeypatch.context() as patch:
            patch.setattr(module, "_LOCAL_MAX_V13", 17)
            with pytest.raises(
                module.CurrentOriginalPrefixError, match="storage_changed"
            ):
                module.continue_owned_original_local_gates_v13(owner)
            assert owner in module._PREFIXES
        assert (
            module.consume_owned_current_original_base_prefix_v1(owner).intent
            is not None
        )


@pytest.mark.asyncio
async def test_real_function_pin_replacement_denies_before_move(
    monkeypatch, tmp_path, raw_market
):
    with native_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        original = await frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        ).capture()
        owner = module.consume_current_original_base_prefix_v1(original)
        with monkeypatch.context() as patch:
            patch.setattr(module, "evaluate_timing", lambda *args, **kwargs: True)
            with pytest.raises(
                module.CurrentOriginalPrefixError, match="function_changed"
            ):
                module.continue_owned_original_local_gates_v13(owner)
            assert owner in module._PREFIXES
        module.consume_owned_current_original_base_prefix_v1(owner)


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["empty", "cloned", "list"])
async def test_pin_table_rebinding_cannot_vacate_before_move(
    monkeypatch, tmp_path, raw_market, replacement
):
    with native_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        original = await frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        ).capture()
        owner = module.consume_current_original_base_prefix_v1(original)
        changed = (
            ()
            if replacement == "empty"
            else tuple(item for item in module._LOCAL_PINS_V13)
            if replacement == "cloned"
            else list(module._LOCAL_PINS_V13)
        )
        with monkeypatch.context() as patch:
            patch.setattr(module, "_LOCAL_PINS_V13", changed)
            with pytest.raises(
                module.CurrentOriginalPrefixError, match="pin_table_changed"
            ):
                module.continue_owned_original_local_gates_v13(owner)
            assert owner in module._PREFIXES
        module.consume_owned_current_original_base_prefix_v1(owner)


@pytest.mark.asyncio
@pytest.mark.parametrize("vacate_table", [False, True])
async def test_checker_noop_replacement_cannot_vacate_before_move(
    monkeypatch, tmp_path, raw_market, vacate_table
):
    with native_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        original = await frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        ).capture()
        owner = module.consume_current_original_base_prefix_v1(original)
        with monkeypatch.context() as patch:
            patch.setattr(module, "_check_local_storage_v13", lambda: None)
            if vacate_table:
                patch.setattr(module, "_LOCAL_PINS_V13", ())
            with pytest.raises(
                module.CurrentOriginalPrefixError, match="function_changed"
            ):
                module.continue_owned_original_local_gates_v13(owner)
            assert owner in module._PREFIXES
        module.consume_owned_current_original_base_prefix_v1(owner)


@pytest.mark.asyncio
async def test_post_move_held_checker_rejects_replacement_without_losing_source(
    monkeypatch, tmp_path, raw_market
):
    with native_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        original = await frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        ).capture()
        owner = module.consume_current_original_base_prefix_v1(original)
        attempt = module.continue_owned_original_local_gates_v13(owner)
        binding, record = next(iter(module._LOCAL_HOLDS_V13.items()))
        checker = record["guard"]
        with monkeypatch.context() as patch:
            patch.setattr(module, "_check_local_storage_v13", lambda: None)
            patch.setattr(module, "_LOCAL_PINS_V13", ())
            with pytest.raises(
                module.CurrentOriginalPrefixError, match="function_changed"
            ):
                module._local_gate_record_v13(binding)
            assert module._LOCAL_HOLDS_V13[binding]["guard"] is checker
        attempt.close()
        assert not module._LOCAL_HOLDS_V13
        assert (
            state.held["accepted_account_bundle"] and state.held["account_native_files"]
        )


@pytest.mark.asyncio
async def test_post_move_timing_policy_rebinding_burns_the_original(
    monkeypatch, tmp_path, raw_market
):
    with native_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        original = await frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        ).capture()
        owner = module.consume_current_original_base_prefix_v1(original)
        with monkeypatch.context() as patch:
            patch.setattr(module, "TIMING_POLICIES", dict(module.TIMING_POLICIES))
            with pytest.raises(
                module.CurrentOriginalPrefixError, match="timing_policy_changed"
            ):
                module.continue_owned_original_local_gates_v13(owner)
            # Policy check is post-MOVE. The source cannot be retried with a
            # replacement policy even after the global is restored.
            assert owner not in module._PREFIXES
        assert not module._LOCAL_HOLDS_V13
        with pytest.raises(module.CurrentOriginalPrefixError):
            module.consume_owned_current_original_base_prefix_v1(owner)


@pytest.mark.asyncio
async def test_failed_release_keeps_original_state_once_without_retry(
    monkeypatch, tmp_path, raw_market
):
    # All providers/rows here are declared synthetic. Fixture teardown restores
    # only its isolated maps; it does not retry the unknown release or delete any
    # accepted source journal/evidence file.
    with native_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        original = await frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        ).capture()
        owner = module.consume_current_original_base_prefix_v1(original)
        attempt = module.continue_owned_original_local_gates_v13(owner)
        binding, record = next(iter(module._LOCAL_HOLDS_V13.items()))
        held_frame = record["prefix"]["frame"]
        calls = []

        def failed_release(*args, **kwargs):
            calls.append(1)
            raise RuntimeError("DECLARED_SYNTHETIC_PRIVATE_ARGUMENT")

        try:
            with monkeypatch.context() as patch:
                patch.setattr(frame, "_release_original_base_prefix_v1", failed_release)
                with pytest.raises(
                    module.CurrentOriginalPrefixError, match="release_unknown"
                ):
                    attempt.close()
                attempt.close()
            assert calls == [1]
            assert binding not in module._LOCAL_HOLDS_V13
            assert module._LOCAL_UNKNOWN_V13[binding]["prefix"]["frame"] is held_frame
            assert (
                state.held["accepted_account_bundle"]
                and state.held["account_native_files"]
            )
        finally:
            module._LOCAL_UNKNOWN_V13.pop(
                binding, None
            )  # Isolated synthetic test registry only.


def test_original_v2_raw_replay_keeps_upcoming_funding_pair_and_no_generation_clock(
    pure_gate_values,
):
    record, _, at = pure_gate_values
    replayed = module._replay_local_quote_v13(
        record["public_packet"],
        record["quote_value"],
        record["computed"],
        evaluated_at=at,
    )
    pair = decode(record["computed"].g1.funding_pair_json.encode())
    assert type(record["quote_value"]) is ExecutableQuoteV2
    assert type(replayed.quote) is ExecutableQuoteV2
    assert replayed == record["quote_context"]
    assert (
        decode(record["public_packet"].packet_json)["quote_sha256"]
        == record["computed"].g1.quote_bundle_sha256
    )
    assert str(replayed.quote.funding.forecast.rate) == pair["rate"]
    assert (
        replayed.quote.funding.forecast.settlement_at.isoformat()
        == pair["settlement_at"]
    )
    assert (
        replayed.quote.funding.exchange_data_return_at.isoformat()
        == pair["exchange_data_return_at"]
    )
    assert pair["rate_generated_at"] is pair["historical_first_available_at"] is None
    assert not hasattr(replayed.quote, "funding_time")
    assert replayed.execution_authority is replayed.quote.execution_authority is False


@pytest.mark.parametrize("damage", ["raw_bytes", "body_hash", "profile", "causal_time"])
def test_v2_raw_packet_hash_profile_and_component_times_cannot_be_relabelled(
    pure_gate_values, damage
):
    record, _, at = pure_gate_values
    # Mutate the actual full raw public packet while keeping the original
    # domain value and G1 expected digest. A denial may occur at the outer
    # packet binding or during raw semantic replay; it does not prove which
    # inner rule was reached.
    document = decode(record["public_packet"].packet_json)
    if damage == "raw_bytes":
        changed = record["public_packet"].packet_json + b" "
    else:
        if damage == "body_hash":
            document["quote"]["provenance"][0]["body_sha256"] = "0" * 64
        elif damage == "profile":
            document["quote"]["transport_policy_sha256"] = "0" * 64
        else:
            document["quote"]["provenance"][0]["received_at"] = (
                at + timedelta(seconds=1)
            ).isoformat()
        changed = canonical(document)
    with pytest.raises(module.CurrentOriginalPrefixError):
        module._replay_local_quote_v13(
            type(record["public_packet"])(changed),
            record["quote_value"],
            record["computed"],
            evaluated_at=at,
        )


@pytest.mark.parametrize("clock", ["before_receipt", "stale"])
def test_v2_actual_source_clocks_are_rechecked_without_renewal(pure_gate_values, clock):
    record, _, at = pure_gate_values
    observed = (
        record["quote_context"].quote.capture_started_at - timedelta(microseconds=1)
        if clock == "before_receipt"
        else at + timedelta(seconds=6)
    )
    with pytest.raises(module.CurrentOriginalPrefixError):
        module._replay_local_quote_v13(
            record["public_packet"],
            record["quote_value"],
            record["computed"],
            evaluated_at=observed,
        )


@pytest.mark.parametrize("kind", ["raw_v2", "bare_object", "changed_domain_value"])
def test_v13_domain_input_rejects_raw_or_substituted_values(pure_gate_values, kind):
    record, _, at = pure_gate_values
    if kind == "raw_v2":
        # Original raw subdocument, not bytes invented from a domain quote.
        raw = decode(record["public_packet"].packet_json)["quote"]
        supplied = raw_quote_v2.CollectedQuoteV2(raw_quote_v2._canonical(raw))
    elif kind == "bare_object":
        supplied = object()
    else:
        supplied = replace(
            record["quote_value"],
            report_id=record["quote_value"].report_id + "-changed",
        )
    with pytest.raises(
        module.CurrentOriginalPrefixError, match="original_local_gate_quote_changed"
    ):
        module._replay_local_quote_v13(
            record["public_packet"], supplied, record["computed"], evaluated_at=at
        )


@pytest.mark.asyncio
async def test_legacy_v1_quote_stays_strict_and_cannot_enter_the_v13_v2_purpose(
    pure_gate_values,
):
    record, _, at = pure_gate_values
    old, _ = await legacy_quote_fixture.capture()
    assert legacy_quote.validate_collected_quote(old) == old
    with pytest.raises(legacy_quote.QuoteCollectionError):
        legacy_quote.validate_collected_quote(record["quote_value"])
    with pytest.raises(module.CurrentOriginalPrefixError):
        module._replay_local_quote_v13(
            record["public_packet"], old, record["computed"], evaluated_at=at
        )


@pytest.mark.asyncio
async def test_real_owned_v2_preparation_retains_raw_context_and_rejects_value_replacement(
    monkeypatch, tmp_path, raw_market
):
    with native_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        original = await frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        ).capture()
        owner = module.consume_current_original_base_prefix_v1(original)
        attempt = module.continue_owned_original_local_gates_v13(owner)
        binding, record = next(iter(module._LOCAL_HOLDS_V13.items()))
        try:
            assert len(module._LOCAL_PINS_V13) == 61
            assert type(record["quote_value"]) is ExecutableQuoteV2
            assert type(record["quote_context"].quote) is ExecutableQuoteV2
            checked, replay = module._local_gate_record_v13(binding)
            assert checked is record and record["quote_value"] == replay[2].quote
            record["quote_context"] = replace(record["quote_context"], quote=object())
            with pytest.raises(
                module.CurrentOriginalPrefixError, match="quote_changed"
            ):
                module._local_gate_record_v13(binding)
            assert state.calls == ["public", "account"]  # No journal/PG method awaited.
        finally:
            attempt.close()
        assert not module._LOCAL_HOLDS_V13 and not module._LOCAL_OWNERS_V13
        assert (
            state.held["accepted_account_bundle"] and state.held["account_native_files"]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "component",
    ["raw_replay", "bridge", "inspection", "location", "economics", "cost_age"],
)
async def test_new_v2_callee_replacement_is_denied_before_the_original_move(
    monkeypatch, tmp_path, raw_market, component
):
    with native_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        original = await frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        ).capture()
        owner = module.consume_current_original_base_prefix_v1(original)
        choices = {
            "raw_replay": (module.public_v2, "_quote_parts"),
            "bridge": (module._local_bridge_v2, "public_market_context_v2"),
            "inspection": (module._local_bridge_v2, "inspect_executable_quote_v2"),
            "location": (module._local_location_v13, "_evaluate_location_numeric_tail"),
            "economics": (
                module._local_economics_v13,
                "_evaluate_economics_numeric_tail",
            ),
            "cost_age": (
                module._local_current_economics_v2,
                "quote_within_economics_policy_age_v2",
            ),
        }
        target, name = choices[component]
        with monkeypatch.context() as patch:
            patch.setattr(target, name, lambda *args, **kwargs: None)
            with pytest.raises(
                module.CurrentOriginalPrefixError, match="function_changed"
            ):
                module.continue_owned_original_local_gates_v13(owner)
            assert owner in module._PREFIXES
        module.consume_owned_current_original_base_prefix_v1(owner)


@pytest.mark.asyncio
async def test_module_and_repository_owner_aliases_cannot_redirect_the_held_purpose(
    monkeypatch, tmp_path, raw_market
):
    with native_fixture.setup(monkeypatch, tmp_path, raw_market) as state:
        original = await frame.open_current_original_capture_v1(
            **dict(state.arguments, strategy=raw_market.strategy)
        ).capture()
        owner = module.consume_current_original_base_prefix_v1(original)
        # One unchanged owner is retained across independent pre-MOVE
        # rejections; each original alias is restored before the next case.
        for name in (
            "_local_bridge_v2",
            "_local_current_economics_v2",
            "_local_quote_v2",
            "_local_location_v13",
            "_local_economics_v13",
            "public_v2",
            "frame",
            "QualificationLedgerRepository",
        ):
            with monkeypatch.context() as patch:
                patch.setattr(module, name, object())
                with pytest.raises(
                    module.CurrentOriginalPrefixError, match="module_alias_changed"
                ):
                    module.continue_owned_original_local_gates_v13(owner)
                assert owner in module._PREFIXES
        attempt = module.continue_owned_original_local_gates_v13(owner)
        binding, record = next(iter(module._LOCAL_HOLDS_V13.items()))
        try:
            with monkeypatch.context() as patch:
                patch.setattr(module, "_local_bridge_v2", object())
                patch.setattr(module, "_local_current_economics_v2", object())
                with pytest.raises(
                    module.CurrentOriginalPrefixError, match="module_alias_changed"
                ):
                    module._local_gate_record_v13(binding)
                assert module._LOCAL_HOLDS_V13[binding] is record
            assert state.calls == ["public", "account"]
        finally:
            attempt.close()
        assert not module._LOCAL_HOLDS_V13 and not module._LOCAL_OWNERS_V13
        assert (
            state.held["accepted_account_bundle"] and state.held["account_native_files"]
        )
