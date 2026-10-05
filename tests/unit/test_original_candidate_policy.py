"""Raw synthetic source replay, never native capture or trading acceptance.

All G1/analysis/route/conditions/events/zone evaluators execute normally. Only
HTTP/socket transports provide fixtures; no analysis flags or fake PASS exist.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.trade_qualification import account_capture as account
from app.trade_qualification import original_candidate_policy as module
from tests.unit import qualification_prefix_fixtures as prefix
from tests.unit import test_qualification_account_capture as account_fixture
from tests.unit import test_qualification_public_market_collector as public_fixture
from tests.unit.test_qualification_account_capture import NOW as ACCOUNT_AT
from tests.unit.test_qualification_account_capture import row
from tests.unit.test_qualification_account_runtime import regional
from tests.unit.test_qualification_candle_collector import ms

NOW = ACCOUNT_AT + timedelta(minutes=10)


def account_source(tick="0.01", *, receipt_delay=timedelta(0)):
    # New synthetic receipt observations, retaining original source uTime/ts.
    # Receipt time never replaces those older exchange field values.
    return account_fixture.verify(
        *account_fixture.records(
            selected=regional(),
            stamp_shift=NOW - ACCOUNT_AT + receipt_delay,
            pages={"account_instruments": [[row("account_instruments", tickSz=tick)]]},
        )
    )


async def raw_sources(monkeypatch, direction="long", *, neutral=False):
    # Choose all raw fixture timestamps before capture; never rewrite receipt
    # fields in a collected packet. The raw account history cutoff stays 10:00.
    # As in the original 01:10 prefix, the 15m first return is known at 10:00,
    # before the 5m trigger bar opens at 10:05 and closes at 10:10.
    monkeypatch.setattr(prefix, "CAPTURED_AT", NOW)
    market = prefix.prefix_market(direction)
    bid, ask, price = market.ticker.bid, market.ticker.ask, market.ticker.last
    at = NOW + timedelta(seconds=1)
    original_ticker = public_fixture.ticker

    def ticker(stamp):
        value = json.loads(original_ticker(stamp))
        value["data"][0].update(bidPx=str(bid), askPx=str(ask))
        return json.dumps(value)

    def edit(request, value):
        if request.url.path == public_fixture.module.candles.ENDPOINT:
            params = request.url.params
            frames = market.candles[params["bar"]]
            data = [
                [
                    ms(c.timestamp),
                    *map(
                        str,
                        (
                            c.open,
                            c.high,
                            c.low,
                            c.close,
                            c.volume_contracts,
                            c.volume_currency,
                            c.volume_quote,
                        ),
                    ),
                    "1",
                ]
                for c in reversed(frames)
            ]
            if neutral:
                for values in data:
                    values[1:5] = ["100", "100.1", "99.9", "100"]
            if params.get("after"):
                data = [
                    values for values in data if int(values[0]) < int(params["after"])
                ]
            value["data"] = data[: int(params["limit"])]
            return
        values = value["data"][0]
        if request.url.path.endswith("/ticker"):
            values.update(
                last=str(price),
                bidPx=str(bid),
                askPx=str(ask),
                bidSz="2",
                askSz="3",
                open24h="100",
                high24h="102",
                low24h="98",
                vol24h="100",
                volCcy24h="1",
            )
        elif request.url.path.endswith("/mark-price"):
            values["markPx"] = str(price)
        elif request.url.path.endswith("/funding-rate"):
            values.update(
                fundingRate="0",
                fundingTime=ms(NOW + timedelta(hours=1)),
                nextFundingTime=ms(NOW + timedelta(hours=9)),
            )
        elif request.url.path.endswith("/books"):
            values.update(
                bids=[[str(bid), "2", "0", "1"]], asks=[[str(ask), "3", "0", "1"]]
            )

    monkeypatch.setattr(public_fixture, "ticker", ticker)
    public, *_ = await public_fixture.capture(
        monkeypatch,
        selected=public_fixture.policy(counts=(240, 240, 240, 240)),
        at=at,
        response_edit=edit,
    )
    packet = account_source()
    return public, packet


@pytest.fixture(scope="module", params=["long", "short"])
async def captured(request):
    with pytest.MonkeyPatch.context() as patch:
        public, packet = await raw_sources(patch, request.param)
    return request.param, public, packet


def derive(public, packet, **changes):
    frozen = account.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    return module.derive_original_candidate_precursor(
        public,
        packet,
        **{
            "strategy": "fvg_return",
            "engine_contract": module.BASE_ENGINE_CONTRACT,
            "expected_public_bundle_sha256": public.bundle_sha256,
            "expected_account_plan_sha256": packet.plan_sha256,
            "expected_account_packet_sha256": frozen.sha256,
            "data_policy": prefix.DATA_POLICY,
            "expected_data_policy_sha256": module.data_policy_sha256(
                prefix.DATA_POLICY
            ),
            "created_at": public.completed_at + timedelta(milliseconds=1),
            "service_deadline": NOW + timedelta(minutes=5),
            **changes,
        },
    )


def test_actual_source_rebuild_fixes_direction_initial_quote_and_original_event(
    captured,
):
    direction, public, packet = captured
    before_public = public.model_dump_json(round_trip=True)
    before_account = account.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    result = derive(public, packet)
    value = json.loads(result.receipt_json)
    assert value["code"] == "precursor_derived_remaining_dependencies_unverified"
    assert value["g1"]["passed"] is True
    assert value["route"]["decision"] == "allow_scoring"
    assert value["conditions"]["score"] >= 85
    assert result.intent is not None and result.intent.direction == direction
    side = "ask" if direction == "long" else "bid"
    assert result.intent.candidate_entry == getattr(public.quote.quote, side)
    assert Decimal(value["initial_entry"]) == getattr(public.quote.quote, side)
    assert value["entry_quote_side"] == side
    assert value["detection"]["source_sha256"] == value["g1"]["source_sha256"]
    assert value["event_key"] is not None and value["zone"] is not None
    assert result.intent.expires_at == NOW + timedelta(minutes=5)
    assert value["admission"] == "DENY" and value["action"] == "WAIT"
    assert all(
        value[name] is None
        for name in ("quantity", "leverage", "stop_loss", "take_profit")
    )
    assert all(
        value[name] is False
        for name in (
            "pre_evidence_complete",
            "account_complete",
            "original_source_verified",
            "source_authenticity_verified",
            "execution_authority",
        )
    )
    assert not result.execution_authority and not result.original_source_verified
    assert "all_state_event_ledger" in value["unverified"]
    assert public.model_dump_json(round_trip=True) == before_public
    assert (
        account.freeze_demo_account_packet(
            packet, expected_plan_sha256=packet.plan_sha256
        )
        == before_account
    )
    assert derive(public, packet) == result


def test_later_replay_cannot_renew_same_event_expiry_or_replace_fixed_entry(captured):
    _, public, packet = captured
    deadline = NOW + timedelta(hours=1)
    first = derive(public, packet, service_deadline=deadline)
    second = derive(
        public,
        packet,
        service_deadline=deadline,
        created_at=public.completed_at + timedelta(seconds=1),
    )
    a, b = json.loads(first.receipt_json), json.loads(second.receipt_json)
    assert first.intent is not None and second.intent is not None
    assert a["event_key"] == b["event_key"]
    assert a["initial_entry"] == b["initial_entry"]
    assert (
        first.intent.expires_at
        == second.intent.expires_at
        == min(
            datetime.fromisoformat(a["original_event_expires_at"]),
            datetime.fromisoformat(a["timing_deadline"]),
            deadline,
        )
    )
    assert first.intent.expires_at <= NOW + timedelta(minutes=10)


def test_service_deadline_shortens_without_moving_original_event(captured):
    _, public, packet = captured
    original = derive(public, packet)
    shortened = derive(public, packet, service_deadline=NOW + timedelta(minutes=1))
    a, b = json.loads(original.receipt_json), json.loads(shortened.receipt_json)
    assert original.intent is not None and shortened.intent is not None
    assert a["event_key"] == b["event_key"] and a["initial_entry"] == b["initial_entry"]
    assert a["original_event_expires_at"] == b["original_event_expires_at"]
    assert shortened.intent.expires_at == NOW + timedelta(minutes=1)


def test_exact_tick_rejection_never_rounds_or_clamps_initial_entry(captured):
    _, public, _ = captured
    packet = account_source("0.1")
    result = derive(public, packet)
    value = json.loads(result.receipt_json)
    assert value["code"] == "initial_entry_off_tick" and value["action"] == "CANCEL"
    assert result.intent is None and value["detection"] is None
    assert Decimal(value["initial_entry"]) in (Decimal("100.99"), Decimal("99.01"))


@pytest.mark.parametrize(
    "strategy,contract",
    [
        ("structure_reversal", "ctcc-history-pre-evidence-v3"),
        ("volatility_expansion", "ctcc-history-pre-evidence-v2"),
        ("range_reversal", "ctcc-history-pre-evidence-v5"),
        ("liquidity_sweep_reversal", module.BASE_ENGINE_CONTRACT),
        ("fvg_return", "ctcc-pre-evidence-v1"),
    ],
)
def test_unintegrated_strategy_contract_has_no_base_event_fallback(
    captured, monkeypatch, strategy, contract
):
    _, public, packet = captured

    def forbidden(*args, **kwargs):
        raise AssertionError("unsupported event extraction was attempted")

    monkeypatch.setattr(module, "extract_trigger", forbidden)
    result = derive(public, packet, strategy=strategy, engine_contract=contract)
    value = json.loads(result.receipt_json)
    assert result.intent is None and value["g1"] is None
    assert (
        value["action"] == "NO_TRADE"
        and value["code"] == "selected_strategy_version_not_integrated"
    )


@pytest.mark.parametrize(
    "name",
    [
        "expected_public_bundle_sha256",
        "expected_account_plan_sha256",
        "expected_account_packet_sha256",
        "expected_data_policy_sha256",
        "expected_policy_sha256",
    ],
)
def test_external_source_and_policy_pins_cannot_be_replaced(captured, name):
    _, public, packet = captured
    with pytest.raises(module.OriginalCandidatePolicyError):
        derive(public, packet, **{name: "0" * 64})


def test_creation_must_follow_both_sources_and_expired_service_is_cancelled(captured):
    _, public, packet = captured
    with pytest.raises(
        module.OriginalCandidatePolicyError, match="creation_precedes_sources"
    ):
        derive(
            public, packet, created_at=public.completed_at - timedelta(microseconds=1)
        )
    value = json.loads(
        derive(public, packet, service_deadline=public.completed_at).receipt_json
    )
    assert (
        value["code"] == "service_deadline_expired"
        and value["precursor_derived"] is False
    )
    assert value["expires_at"] is None


def test_later_actual_account_completion_is_an_independent_creation_floor(captured):
    _, public, _ = captured
    packet = account_source(receipt_delay=timedelta(seconds=3))
    assert packet.completed_at > public.completed_at
    with pytest.raises(
        module.OriginalCandidatePolicyError, match="creation_precedes_sources"
    ):
        derive(public, packet)


@pytest.mark.parametrize(
    "field,value",
    [
        ("direction", "long"),
        ("candidate_entry", Decimal(1)),
        ("quantity", Decimal(1)),
        ("stop_loss", Decimal(1)),
        ("take_profit", Decimal(2)),
        ("expires_at", NOW + timedelta(days=1)),
        ("passed", True),
    ],
)
def test_caller_candidate_fields_are_not_an_input_surface(captured, field, value):
    _, public, packet = captured
    with pytest.raises(TypeError):
        derive(public, packet, **{field: value})


@pytest.mark.asyncio
async def test_unknown_neutral_raw_history_does_not_fall_back_to_trend(monkeypatch):
    public, packet = await raw_sources(monkeypatch, neutral=True)
    result = derive(public, packet)
    value = json.loads(result.receipt_json)
    assert result.intent is None and value["precursor_derived"] is False
    assert value["action"] in {"WAIT", "NO_TRADE"}
    assert value["code"] != "precursor_derived_remaining_dependencies_unverified"
    assert value["admission"] == "DENY"


def test_returned_diagnostic_cannot_replace_either_original_raw_packet(captured):
    _, public, packet = captured
    result = derive(public, packet)
    frozen = account.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    # Call directly: the convenience helper intentionally requires both packets.
    for first, second in ((result, packet), (public, result)):
        with pytest.raises(module.OriginalCandidatePolicyError):
            module.derive_original_candidate_precursor(
                first,
                second,
                strategy="fvg_return",
                engine_contract=module.BASE_ENGINE_CONTRACT,
                expected_public_bundle_sha256=public.bundle_sha256,
                expected_account_plan_sha256=packet.plan_sha256,
                expected_account_packet_sha256=frozen.sha256,
                data_policy=prefix.DATA_POLICY,
                expected_data_policy_sha256=module.data_policy_sha256(
                    prefix.DATA_POLICY
                ),
                created_at=public.completed_at + timedelta(seconds=1),
                service_deadline=NOW + timedelta(minutes=5),
            )
