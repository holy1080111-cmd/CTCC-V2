"""Synthetic journal fixtures for legacy baseline input replay."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.mie.validation.legacy_baseline_market_replay_v1 import (
    LegacyBaselineMarketReplayError,
    replay_legacy_baseline_market_v1,
)
from app.trade_qualification import global_public_runtime_v1 as public
from tests.unit.research.test_owned_global_public_runtime_v1 import (
    market as market,  # noqa: PLC0414 -- register the existing fixture.
)
from tests.unit.research.test_owned_global_public_runtime_v1 import (
    owned_global as owned_global,  # noqa: PLC0414 -- register the existing fixture.
)
from tests.unit.test_qualification_one_shot import ms
from tests.unit.test_qualification_public_market_collector import policy


async def capture(owned_global, root, *, funding_state="current"):
    directory, harness = owned_global
    original = harness.public

    def server(request):
        rows = original(request)
        if request.url.path.endswith("/funding-rate"):
            observed = harness.clock.last
            upcoming = observed + timedelta(hours=4)
            if funding_state == "expired":
                upcoming = observed - timedelta(seconds=1)
            rows[0].update(
                fundingTime=ms(upcoming),
                nextFundingTime=ms(upcoming + timedelta(hours=8)),
                method="current_period",
                formulaType="withRate",
                settState="settled",
                minFundingRate="-0.01",
                maxFundingRate="0.01",
                settFundingRate="0.001",
            )
            if funding_state == "missing":
                del rows[0]["fundingTime"]
        return rows

    harness.public = server
    recorded = await public.capture_initial_global_public_market_v1(
        root,
        instrument_id="BTC-USDT-SWAP",
        market_policy=policy(counts=(240,) * 4),
    )
    assert recorded.code == "initial_global_public_captured"
    assert recorded.journal_completion_sample is None
    journal_completed = utc_from_ns(
        decode(directory.content["summary.json"])["completed"]["utc_ns"]
    )
    native_available = utc_from_ns(
        decode(recorded.post_readback_receipt_json)["sample"]["utc_ns"]
    )
    assert recorded.packet.completed_at < journal_completed < native_available
    return {
        "instrument_id": recorded.packet.instrument_id,
        "decision_at": native_available,
        "expected_plan_sha256": recorded.plan_sha256,
        "expected_journal_sha256": recorded.journal_sha256,
        "expected_packet_sha256": recorded.packet.bundle_sha256,
        "expected_market_policy_sha256": recorded.packet.policy_sha256,
        "post_readback_receipt_json": recorded.post_readback_receipt_json,
        "expected_post_readback_receipt_sha256": sha(
            recorded.post_readback_receipt_json
        ),
    }, recorded.packet


@pytest.mark.asyncio
async def test_exact_journal_replay_recovers_full_market_at_native_availability(
    owned_global, tmp_path
):
    directory, harness = owned_global
    pins, packet = await capture(owned_global, tmp_path)
    request_count = len(harness.requests)
    original = dict(directory.content)
    first = replay_legacy_baseline_market_v1(tmp_path, **pins)
    repeated = replay_legacy_baseline_market_v1(tmp_path, **pins)
    assert first.receipt_json == repeated.receipt_json
    assert directory.content == original
    assert len(harness.requests) == request_count
    snapshot = first.market
    assert snapshot.instrument_id == "BTC-USDT-SWAP"
    assert tuple(snapshot.candles) == ("4H", "1H", "15m", "5m")
    assert len(snapshot.candles["4H"]) == 240
    assert all(report.ok for report in snapshot.quality.values())
    assert snapshot.ticker.volume_contracts_24h != snapshot.ticker.volume_currency_24h
    assert snapshot.ticker.volume_quote_24h is None
    assert snapshot.open_interest_contracts == packet.market_aux.open_interest.contracts
    assert snapshot.order_book == packet.market_aux.book.order_book
    assert snapshot.mark_price > Decimal(0)
    funding = decode(packet.quote.provenance[-1].response_body)["data"][0]
    upcoming = utc_from_ns(int(funding["fundingTime"]) * 1_000_000)
    following = utc_from_ns(int(funding["nextFundingTime"]) * 1_000_000)
    assert snapshot.next_funding_time == upcoming
    assert pins["decision_at"] < upcoming < following
    assert snapshot.received_at <= packet.completed_at < pins["decision_at"]
    receipt = decode(first.receipt_json)
    assert receipt["snapshot_sha256"] == sha(
        canonical(snapshot.model_dump(mode="json"))
    )
    assert (
        receipt["journal_completed_at"]
        == utc_from_ns(
            decode(directory.content["summary.json"])["completed"]["utc_ns"]
        ).isoformat()
    )
    assert receipt["post_readback_sample_at"] == pins["decision_at"].isoformat()
    assert (
        receipt["capture_deadline_at"]
        == decode(directory.content["plan.json"])["expires_at"]
    )
    assert (
        receipt["post_readback_receipt_sha256"]
        == pins["expected_post_readback_receipt_sha256"]
    )
    assert receipt["funding_forecast_settlement_at"] == upcoming.isoformat()
    assert receipt["funding_following_forecast_at"] == following.isoformat()
    assert receipt["journal_sha256"] == pins["expected_journal_sha256"]
    assert (
        first.independent_availability_verified
        is first.predictive_oos_eligible
        is False
    )
    assert first.source_authenticity_verified is first.execution_authority is False
    harness.assert_closed()


@pytest.mark.asyncio
async def test_early_and_stale_decision_and_wrong_pins_refuse(owned_global, tmp_path):
    pins, _ = await capture(owned_global, tmp_path)
    directory, _ = owned_global
    before_readback = utc_from_ns(
        decode(directory.content["summary.json"])["completed"]["utc_ns"]
    )
    with pytest.raises(
        LegacyBaselineMarketReplayError,
        match="^legacy_market_not_available_at_decision$",
    ):
        replay_legacy_baseline_market_v1(
            tmp_path, **(pins | {"decision_at": before_readback})
        )
    with pytest.raises(
        LegacyBaselineMarketReplayError, match="^legacy_market_component_stale$"
    ):
        replay_legacy_baseline_market_v1(
            tmp_path,
            **(pins | {"decision_at": pins["decision_at"] + timedelta(minutes=1)}),
        )
    for key, value in (
        ("instrument_id", "ETH-USDT-SWAP"),
        ("expected_packet_sha256", "0" * 64),
        ("expected_market_policy_sha256", "0" * 64),
    ):
        with pytest.raises(
            LegacyBaselineMarketReplayError,
            match="^legacy_market_source_scope_changed$",
        ):
            replay_legacy_baseline_market_v1(tmp_path, **(pins | {key: value}))
    with pytest.raises(
        LegacyBaselineMarketReplayError, match="^legacy_market_replay_refused$"
    ):
        replay_legacy_baseline_market_v1(
            tmp_path, **(pins | {"expected_journal_sha256": "0" * 64})
        )


@pytest.mark.asyncio
async def test_missing_or_mismatched_post_readback_record_refuses(
    owned_global, tmp_path
):
    pins, _ = await capture(owned_global, tmp_path)
    with pytest.raises(
        LegacyBaselineMarketReplayError,
        match="^legacy_market_post_readback_pin_changed$",
    ):
        replay_legacy_baseline_market_v1(
            tmp_path, **(pins | {"post_readback_receipt_json": b""})
        )
    receipt = decode(pins["post_readback_receipt_json"])
    swapped = canonical(receipt | {"instrument_id": "ETH-USDT-SWAP"})
    with pytest.raises(
        LegacyBaselineMarketReplayError,
        match="^legacy_market_post_readback_scope_changed$",
    ):
        replay_legacy_baseline_market_v1(
            tmp_path,
            **(
                pins
                | {
                    "post_readback_receipt_json": swapped,
                    "expected_post_readback_receipt_sha256": sha(swapped),
                }
            ),
        )
    directory, _ = owned_global
    same_sample = canonical(
        receipt | {"sample": decode(directory.content["summary.json"])["completed"]}
    )
    with pytest.raises(
        LegacyBaselineMarketReplayError,
        match="^legacy_market_not_available_at_decision$",
    ):
        replay_legacy_baseline_market_v1(
            tmp_path,
            **(
                pins
                | {
                    "post_readback_receipt_json": same_sample,
                    "expected_post_readback_receipt_sha256": sha(same_sample),
                }
            ),
        )
    deadline = datetime.fromisoformat(
        decode(directory.content["plan.json"])["expires_at"]
    )
    lag_ns = ((deadline - pins["decision_at"]) // timedelta(microseconds=1)) * 1000
    expired = canonical(
        receipt
        | {"sample": {key: value + lag_ns for key, value in receipt["sample"].items()}}
    )
    with pytest.raises(
        LegacyBaselineMarketReplayError, match="^legacy_market_post_readback_expired$"
    ):
        replay_legacy_baseline_market_v1(
            tmp_path,
            **(
                pins
                | {
                    "decision_at": deadline,
                    "post_readback_receipt_json": expired,
                    "expected_post_readback_receipt_sha256": sha(expired),
                }
            ),
        )


@pytest.mark.parametrize(
    ("funding_state", "refusal"),
    [
        ("missing", "legacy_market_replay_refused"),
        ("expired", "legacy_market_funding_expired"),
    ],
)
@pytest.mark.asyncio
async def test_missing_or_expired_funding_refuses_without_imputation(
    owned_global, tmp_path, funding_state, refusal
):
    pins, _ = await capture(owned_global, tmp_path, funding_state=funding_state)
    with pytest.raises(LegacyBaselineMarketReplayError, match=f"^{refusal}$"):
        replay_legacy_baseline_market_v1(tmp_path, **pins)
