"""Synthetic exact cohort and source-verifier test doubles for joint readiness."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.source_primitives import canonical, sha, utc_from_ns
from app.mie.validation import prospective_devval_joint_companion_coverage_v1 as subject
from app.mie.validation.legacy_baseline_market_replay_v1 import (
    LegacyBaselineMarketReplayError,
    ReplayedLegacyBaselineMarketV1,
)
from app.mie.validation.prospective_devval_campaign_batch_v1 import _build_batch
from app.mie.validation.prospective_devval_global_companion_schedule_v1 import (
    freeze_global_devval_companion_schedule_v1,
)
from app.trade_qualification.global_public_capture_companion_v1 import (
    GlobalPublicCapturePublicationV1,
    GlobalPublicCompanionReadbackV1,
)
from tests.unit.mie.test_devval_campaign_batch import _ns, synthetic_grid
from tests.unit.mie.test_devval_global_companion_schedule_v1 import _inputs
from tests.unit.test_qualification_market_bridge import v2_engine_source


@pytest.fixture(scope="module")
def cohort():
    inputs = _inputs()
    schedule = freeze_global_devval_companion_schedule_v1(**inputs)
    campaign, rows, pins = synthetic_grid(inputs["campaign_plan"])
    return schedule, _build_batch(campaign, rows, **pins)


def _audit(cohort, outcomes=()):
    schedule, batch = cohort
    return subject.audit_devval_joint_companion_coverage_v1(
        schedule.payload,
        expected_schedule_sha256=schedule.sha256,
        batch_payload=batch.payload,
        expected_batch_sha256=batch.sha256,
        outcomes=outcomes,
    )


def _event_inputs(cohort, tmp_path, *, lags=(2, 4), eth_extra_ns=0):
    schedule, batch = cohort
    slot = schedule.contract.slots[0]
    outcomes, measured, markets = [], {}, {}
    for index, (symbol, lag) in enumerate(
        zip(schedule.contract.instrument_ids, lags, strict=True)
    ):
        token = str(index).encode()
        publication = GlobalPublicCapturePublicationV1(
            instrument_id=symbol,
            plan_sha256=sha(b"plan" + token),
            journal_sha256=sha(b"journal" + token),
            packet_sha256=sha(b"packet" + token),
            market_policy_sha256=schedule.contract.market_policy_sha256,
            companion_sha256=sha(b"companion" + token),
            journal_root_identity=(1, 100 + index),
            companion_root_identity=(1, 200 + index),
        )
        readback = GlobalPublicCompanionReadbackV1(
            publication_sha256=publication.canonical_sha256(),
            companion_sha256=publication.companion_sha256,
            readback_completed={
                "utc_ns": _ns(slot.event_closed_at + timedelta(seconds=lag))
                + (eth_extra_ns if index == 1 else 0),
                "monotonic_ns": (100 + lag) * 1_000_000_000,
            },
        )
        outcomes.append(
            subject.PublishedCompanionV1(
                slot_ordinal=0,
                instrument_id=symbol,
                journal_root=tmp_path / f"journal-{index}",
                companion_root=tmp_path / f"companion-{index}",
                publication_json=publication.canonical_bytes(),
                expected_publication_sha256=publication.canonical_sha256(),
                readback_json=readback.canonical_bytes(),
                expected_readback_sha256=readback.canonical_sha256(),
            )
        )
        measured[symbol] = readback

        market = v2_engine_source("long").market
        candles = dict(market.candles)
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        for frame in batch.contract.series[index].aggregation.timeframes:
            width = timedelta(seconds=frame.horizon.seconds)
            target = epoch + (slot.event_closed_at - epoch) // width * width
            bar = next(
                item.row.bar for item in frame.bars if item.row.bar.closed_at == target
            )
            candles[frame.horizon.label] = [
                candles[frame.horizon.label][-1].model_copy(
                    update={
                        "timestamp": target - width,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume_contracts": bar.volume,
                        "confirmed": True,
                    }
                )
            ]
        markets[symbol] = market.model_copy(
            update={
                "instrument_id": symbol,
                "ticker": market.ticker.model_copy(update={"instrument_id": symbol}),
                "order_book": market.order_book.model_copy(
                    update={"instrument_id": symbol}
                ),
                "candles": candles,
            }
        )
    return tuple(outcomes), measured, markets


def _install_readback_replays(monkeypatch, measured, markets, *, stale_symbol=None):
    seen = []

    def verify(
        journal_root,
        companion_root,
        *,
        publication,
        readback_json,
        expected_readback_sha256,
    ):
        result = measured[publication.instrument_id]
        assert readback_json == result.canonical_bytes()
        assert sha(readback_json) == expected_readback_sha256
        return result

    def replay(
        journal_root,
        companion_root,
        *,
        publication_json,
        expected_publication_sha256,
        decision_at,
    ):
        publication = GlobalPublicCapturePublicationV1.model_validate_json(
            publication_json
        )
        assert publication.canonical_sha256() == expected_publication_sha256
        symbol = publication.instrument_id
        seen.append((symbol, decision_at))
        if symbol == stale_symbol:
            raise LegacyBaselineMarketReplayError("legacy_market_component_stale")
        completed = utc_from_ns(measured[symbol].readback_completed.utc_ns)
        return ReplayedLegacyBaselineMarketV1(
            markets[symbol],
            canonical(
                {
                    "instrument_id": symbol,
                    "decision_at": decision_at.isoformat(),
                    "journal_completed_at": completed.isoformat(),
                    "post_readback_sample_at": completed.isoformat(),
                    "post_readback_receipt_sha256": publication.companion_sha256,
                    "snapshot_sha256": sha(
                        canonical(markets[symbol].model_dump(mode="json"))
                    ),
                }
            ),
        )

    monkeypatch.setattr(subject, "verify_global_public_measured_companion_v1", verify)
    monkeypatch.setattr(
        subject, "replay_legacy_baseline_market_from_companion_v1", replay
    )
    return seen


def test_both_replay_at_later_measured_time_and_other_events_remain(
    cohort, tmp_path, monkeypatch
):
    schedule, _ = cohort
    outcomes, measured, markets = _event_inputs(cohort, tmp_path)
    seen = _install_readback_replays(monkeypatch, measured, markets)
    first = _audit(cohort, outcomes)
    joint = schedule.contract.slots[0].event_closed_at + timedelta(seconds=4)
    assert seen == [(symbol, joint) for symbol in schedule.contract.instrument_ids]
    assert len(first.contract.slots) == len(schedule.contract.slots)
    assert first.contract.slots[0].joint_decision_at == joint
    assert first.contract.slots[0].status == "covered_computational"
    assert all(slot.joint_decision_at is None for slot in first.contract.slots[1:])
    assert all(
        side.status == "missing"
        for slot in first.contract.slots[1:]
        for side in slot.sides
    )
    assert first.contract.complete_computational_slots == 1
    assert first.contract.incomplete_slots == len(schedule.contract.slots) - 1
    for side, supplied in zip(first.contract.slots[0].sides, outcomes, strict=True):
        assert side.aggregate.instrument_id == supplied.instrument_id
        assert side.aggregate.available_at == joint - timedelta(seconds=3)
        assert side.publication_sha256 == supplied.expected_publication_sha256
        assert side.readback_sha256 == supplied.expected_readback_sha256
        assert side.replay_receipt_sha256
    assert first.contract.independent_availability_verified is False
    assert first.contract.execution_authority is False


def test_expired_companion_never_replays_market(cohort, tmp_path, monkeypatch):
    outcomes, measured, markets = _event_inputs(
        cohort, tmp_path, lags=(2, 60), eth_extra_ns=1
    )
    seen = _install_readback_replays(monkeypatch, measured, markets)
    result = _audit(cohort, outcomes)
    assert seen == []
    assert result.contract.slots[0].joint_decision_at is None
    assert result.contract.slots[0].sides[1].status == "late"
    assert result.contract.slots[0].sides[1].postpublication_readback_at == (
        result.contract.slots[0].decision_cutoff_at + timedelta(microseconds=1)
    )
    assert result.contract.slots[0].sides[0].status == "measured"
    missing_peer = _audit(cohort, outcomes[1:])
    assert [side.status for side in missing_peer.contract.slots[0].sides] == [
        "missing",
        "late",
    ]


def test_preclose_readback_is_refused(cohort, tmp_path, monkeypatch):
    outcomes, measured, markets = _event_inputs(cohort, tmp_path, lags=(-1, 2))
    seen = _install_readback_replays(monkeypatch, measured, markets)
    result = _audit(cohort, outcomes)
    slot = result.contract.slots[0]
    assert seen == []
    assert slot.joint_decision_at is None
    assert slot.sides[0].refusal_reason == "readback_before_event_close"


@pytest.mark.parametrize("failure", ("stale", "mismatched_volume"))
def test_pair_rejects_stale_or_disagreed_original_at_joint_time(
    cohort, tmp_path, monkeypatch, failure
):
    outcomes, measured, markets = _event_inputs(cohort, tmp_path)
    if failure == "mismatched_volume":
        markets["ETH-USDT-SWAP"].candles["1H"][-1].volume_contracts += Decimal(1)
    seen = _install_readback_replays(
        monkeypatch,
        measured,
        markets,
        stale_symbol="BTC-USDT-SWAP" if failure == "stale" else None,
    )
    result = _audit(cohort, outcomes)
    assert len(seen) == 2
    assert result.contract.slots[0].joint_decision_at is None
    if failure == "stale":
        assert result.contract.slots[0].sides[0].status == "replay_refused"
    else:
        assert result.contract.slots[0].sides[1].status == "ohlcv_mismatch"


def test_duplicate_outcomes_are_refused_before_pair_can_be_used(cohort, tmp_path):
    outcomes, _, _ = _event_inputs(cohort, tmp_path)
    with pytest.raises(ValueError):
        _audit(cohort, (outcomes[0], outcomes[0]))
