"""Synthetic raw transports; real history engines, never source acceptance."""

import asyncio
import inspect
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.trade_qualification import account_capture as account
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import history_prefix, market_bridge
from app.trade_qualification import original_history_candidate_policy as module
from app.trade_qualification.data import WSReferenceObservation
from tests.unit import test_original_candidate_policy as base_fixture
from tests.unit.history_qualification_fixtures import history_source
from tests.unit.qualification_range_v5_fixtures import range_v5_source
from tests.unit.test_qualification_events import OBSERVED

NOW = base_fixture.NOW
CASES = tuple(
    (strategy, direction)
    for strategy in ("structure_reversal", "volatility_expansion", "range_reversal")
    for direction in ("long", "short")
)


def capture_case(strategy, direction):
    if strategy == "range_reversal":
        source = range_v5_source(direction)
    else:
        source = history_source(
            strategy,
            direction,
            bracket=strategy != "liquidity_sweep_reversal",
            expansion_entry=strategy == "volatility_expansion",
            reversal_bracket=strategy == "structure_reversal",
        )
    market = source.market.model_copy(deep=True)
    # Whole-day shift before any capture, preserving all original chronology.
    # No receipt, source timestamp or event from a captured packet is rewritten.
    shift = NOW - OBSERVED
    assert shift.seconds == 0 and shift.microseconds == 0
    market.candles = {
        frame: [
            row.model_copy(update={"timestamp": row.timestamp + shift}) for row in rows
        ]
        for frame, rows in market.candles.items()
    }
    with pytest.MonkeyPatch.context() as patch:
        # Only a raw fixture constructor is replaced; all production parsers,
        # analysis, routes, predicates, events and permission replay execute.
        patch.setattr(base_fixture.prefix, "prefix_market", lambda _: market)
        return asyncio.run(base_fixture.raw_sources(patch, direction))


@pytest.fixture(scope="module")
def captures():
    return {case: capture_case(*case) for case in CASES}


def derive(public, packet, strategy="structure_reversal", **changes):
    frozen = account.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    return module.derive_history_candidate_precursor(
        public,
        packet,
        **{
            "strategy": strategy,
            "engine_contract": module.ENGINE_CONTRACTS[strategy],
            "expected_public_bundle_sha256": public.bundle_sha256,
            "expected_account_plan_sha256": packet.plan_sha256,
            "expected_account_packet_sha256": frozen.sha256,
            "data_policy": base_fixture.prefix.DATA_POLICY,
            "expected_data_policy_sha256": module.data_policy_sha256(
                base_fixture.prefix.DATA_POLICY
            ),
            "created_at": public.completed_at + timedelta(milliseconds=1),
            "service_deadline": NOW + timedelta(hours=1),
            **changes,
        },
    )


@pytest.mark.parametrize("case", CASES)
def test_raw_source_direction_event_receipt_cutoffs_and_unknown_past_availability(
    captures, case
):
    public, packet = captures[case]
    strategy, direction = case
    before = public.model_dump_json(round_trip=True)
    result = derive(public, packet, strategy)
    value = json.loads(result.receipt_json)
    assert value["code"] == "precursor_derived_remaining_dependencies_unverified", (
        value["code"]
    )
    assert result.intent is not None and result.intent.direction == direction
    assert result.intent.candidate_entry == getattr(
        public.quote.quote, "ask" if direction == "long" else "bid"
    )
    assert value["g1"]["passed"] is True and value["current_permission"] is True
    assert value["detection"]["source_sha256"] == value["g1"]["source_sha256"]
    assert value["timeline_sha256"] == journal.digest(
        journal.canonical(value["timeline"])
    )
    assert len(value["timeline"]) == 4
    created = result.intent.created_at
    for frame, captured in zip(value["timeline"], public.candles.frames, strict=True):
        assert frame["frame_sha256"] == captured.frame_sha256
        assert frame["confirmed_count"] == 240
        assert len(frame["rows_in_original_page_order"]) == 240
        for row in frame["rows_in_original_page_order"]:
            page = captured.pages[row["page_index"]]
            raw = json.loads(page.response_body)["data"][row["row_index"]]
            assert row["raw_row_sha256"] == journal.digest(journal.canonical(raw))
            assert row["page_body_sha256"] == page.body_sha256
            assert datetime.fromisoformat(row["body_completed_at"]) <= created
            assert datetime.fromisoformat(row["closed_at"]) <= created
            assert row["historical_first_available_at"] is None
    assert {item["role"] for item in value["event_prefix_witness"]} >= {
        "setup",
        "trigger",
    }
    assert all(
        item["past_receipt_availability_verified"] is False
        for item in value["event_prefix_witness"]
    )
    assert value["admission"] == "DENY" and value["action"] == "WAIT"
    assert all(
        value[field] is False
        for field in (
            "historical_first_availability_verified",
            "predictive_point_in_time_verified",
            "pre_evidence_complete",
            "account_complete",
            "original_source_verified",
            "source_authenticity_verified",
            "execution_authority",
        )
    )
    assert all(
        value[field] is None
        for field in ("quantity", "leverage", "stop_loss", "take_profit")
    )
    assert not result.execution_authority and not result.original_source_verified
    assert public.model_dump_json(round_trip=True) == before


@pytest.mark.parametrize("case", CASES)
def test_precursor_replays_the_actual_versioned_prefix_event_and_zone(captures, case):
    public, packet = captures[case]
    strategy, _ = case
    precursor = derive(public, packet, strategy)
    assert precursor.intent is not None
    value = json.loads(precursor.receipt_json)
    version = {"structure_reversal": 3, "volatility_expansion": 2, "range_reversal": 5}[
        strategy
    ]
    policy_type = getattr(history_prefix, f"HistoryQualificationPrefixPolicyV{version}")
    run = getattr(history_prefix, f"evaluate_history_qualification_prefix_v{version}")
    extra = {}
    if version == 5:
        extra.update(
            range_protection_policy=module.RANGE_PROTECTION_POLICY,
            range_anchor_policy=module.RANGE_ANCHOR_POLICY,
        )
    policy = policy_type(
        contract_version=f"ctcc-history-qualification-prefix-v{version}",
        policy_id="synthetic-history-precursor-comparison",
        data=base_fixture.prefix.DATA_POLICY,
        minimum_score=85,
        tick_size=Decimal("0.01"),
        max_allowed_drift_bps=Decimal(30),
        **extra,
    )
    ticker = public.ws.ticker
    result = run(
        market_bridge.public_market_snapshot(
            public, expected_bundle_sha256=public.bundle_sha256
        ),
        intent=precursor.intent,
        quote=public.quote,
        reference=WSReferenceObservation(
            report_id=public.report_id,
            instrument_id=public.instrument_id,
            bid=ticker.bid,
            ask=ticker.ask,
            source_time=ticker.source_time,
            received_at=ticker.received_at,
        ),
        policy=policy,
        # Explicit fixture-only synthetic ledger for independent G6 comparison.
        # The precursor itself has no such input and cannot claim ledger proof.
        consumed_event_keys=frozenset(),
        evaluated_at=precursor.intent.created_at,
    )
    assert result.prefix_complete, result.result.fail_codes
    assert value["detection"] == result.detection.model_dump(mode="json")
    assert value["zone"] == result.result.entry_zone.model_dump(mode="json")
    assert value["event_key"] == result.timing.event_key
    assert result.timing.latest_valid_entry_time == precursor.intent.expires_at
    if version == 5:
        assert value["range_permission_replay_sha256"] is not None
        assert value["history_admission"] is None
    else:
        assert value["history_admission"] == result.history_admission.model_dump(
            mode="json"
        )


@pytest.mark.parametrize("case", CASES)
def test_replay_and_shorter_deadline_keep_original_event_entry_and_expiry(
    captures, case
):
    public, packet = captures[case]
    strategy, _ = case
    first = derive(public, packet, strategy)
    again = derive(public, packet, strategy)
    assert first == again
    later = derive(
        public, packet, strategy, created_at=public.completed_at + timedelta(seconds=1)
    )
    short = derive(
        public, packet, strategy, service_deadline=NOW + timedelta(minutes=1)
    )
    assert first.intent and later.intent and short.intent
    a, b, c = (json.loads(item.receipt_json) for item in (first, later, short))
    assert a["event_key"] == b["event_key"] == c["event_key"]
    assert a["initial_entry"] == b["initial_entry"] == c["initial_entry"]
    assert first.intent.expires_at == later.intent.expires_at
    assert short.intent.expires_at == NOW + timedelta(minutes=1)
    assert a["original_event_expires_at"] == c["original_event_expires_at"]
    assert a["zone"]["zone_low"] == c["zone"]["zone_low"]
    assert a["zone"]["zone_high"] == c["zone"]["zone_high"]


@pytest.mark.parametrize(
    "field",
    (
        "expected_public_bundle_sha256",
        "expected_account_plan_sha256",
        "expected_account_packet_sha256",
        "expected_data_policy_sha256",
        "expected_policy_sha256",
    ),
)
def test_no_hash_only_source_substitution(captures, field):
    public, packet = captures[CASES[0]]
    with pytest.raises(module.OriginalHistoryCandidatePolicyError):
        derive(public, packet, **{field: "0" * 64})


@pytest.mark.parametrize(
    "strategy,contract",
    (
        ("structure_reversal", "ctcc-history-pre-evidence-v1"),
        ("volatility_expansion", "ctcc-history-pre-evidence-v3"),
        ("range_reversal", "ctcc-history-pre-evidence-v4"),
        ("range_reversal", "ctcc-original-base-precursor-v1"),
        ("liquidity_sweep_reversal", "ctcc-history-pre-evidence-v5"),
        ("trend_pullback", "ctcc-history-pre-evidence-v3"),
        ("fvg_return", "ctcc-history-pre-evidence-v2"),
    ),
)
def test_exact_contract_no_strategy_or_base_fallback(captures, strategy, contract):
    public, packet = captures[CASES[0]]
    result = (
        derive(public, packet, strategy=strategy, engine_contract=contract)
        if strategy in module.ENGINE_CONTRACTS
        else module.derive_history_candidate_precursor(
            public,
            packet,
            strategy=strategy,
            engine_contract=contract,
            expected_public_bundle_sha256=public.bundle_sha256,
            expected_account_plan_sha256=packet.plan_sha256,
            expected_account_packet_sha256=account.freeze_demo_account_packet(
                packet, expected_plan_sha256=packet.plan_sha256
            ).sha256,
            data_policy=base_fixture.prefix.DATA_POLICY,
            expected_data_policy_sha256=module.data_policy_sha256(
                base_fixture.prefix.DATA_POLICY
            ),
            created_at=public.completed_at + timedelta(milliseconds=1),
            service_deadline=NOW + timedelta(minutes=5),
        )
    )
    value = json.loads(result.receipt_json)
    assert result.intent is None and value["action"] == "NO_TRADE"
    assert value["code"] == "selected_strategy_version_not_integrated"
    assert value["g1"] is None and value["detection"] is None


@pytest.mark.parametrize(
    "field",
    ("direction", "candidate_entry", "event", "analysis", "expires_at", "score"),
)
def test_no_caller_decision_fields(field):
    assert (
        field
        not in inspect.signature(module.derive_history_candidate_precursor).parameters
    )


def test_creation_before_any_source_is_rejected(captures):
    public, packet = captures[CASES[0]]
    with pytest.raises(
        module.OriginalHistoryCandidatePolicyError, match="creation_precedes_sources"
    ):
        derive(public, packet, created_at=NOW)


def test_missing_next_closed_tail_is_wait_not_recreated_history(captures):
    public, packet = captures[CASES[0]]
    result = derive(public, packet, created_at=NOW + timedelta(minutes=5))
    assert result.intent is None
    assert json.loads(result.receipt_json)["code"] == "confirmed_tail_missing"


def test_expired_service_never_restarts_event_clock(captures):
    public, packet = captures[CASES[0]]
    result = derive(public, packet, service_deadline=NOW)
    assert result.intent is None
    assert json.loads(result.receipt_json)["code"] == "service_deadline_expired"


def test_off_tick_entry_is_cancelled_without_rounding(captures):
    public, _ = captures[CASES[0]]
    result = derive(public, base_fixture.account_source("3"))
    assert result.intent is None
    assert json.loads(result.receipt_json)["code"] == "initial_entry_off_tick"


@pytest.mark.parametrize("direction", ("long", "short"))
def test_sweep_retains_real_history_and_unspecified_htf_wait(direction):
    public, packet = capture_case("liquidity_sweep_reversal", direction)
    result = derive(public, packet, "liquidity_sweep_reversal")
    value = json.loads(result.receipt_json)
    assert value["code"] == "sweep_htf_policy_unspecified", value["code"]
    assert value["history_admission"]["history_verified"] is True
    assert value["history_admission"]["admitted"] is False
    assert result.intent is None and value["admission"] == "DENY"


@pytest.mark.parametrize("kind", ("raw_bytes", "confirmed_dto"))
def test_raw_or_confirmed_candle_tamper_fails_closed(captures, kind):
    public, packet = captures[CASES[0]]
    frame = public.candles.frames[0]
    if kind == "raw_bytes":
        page = frame.pages[0]
        value = json.loads(page.response_body)
        value["data"][0][5] = "999"
        frame = frame.model_copy(
            update={
                "pages": (
                    page.model_copy(
                        update={"response_body": json.dumps(value).encode()}
                    ),
                    *frame.pages[1:],
                )
            }
        )
    else:
        row = frame.confirmed[0]
        frame = frame.model_copy(
            update={
                "confirmed": (
                    row.model_copy(update={"volume_contracts": Decimal(999)}),
                    *frame.confirmed[1:],
                )
            }
        )
    changed = public.model_copy(
        update={
            "candles": public.candles.model_copy(
                update={"frames": (frame, *public.candles.frames[1:])}
            )
        }
    )
    with pytest.raises(module.OriginalHistoryCandidatePolicyError):
        derive(changed, packet)
