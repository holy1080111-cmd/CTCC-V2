"""Replay full predecision market inputs for a frozen legacy baseline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from app.domain.market import MarketSnapshot
from app.domain.source_primitives import (
    ClockStamp,
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.mie.validation.contracts import require_utc
from app.trade_qualification import candle_collector as candles
from app.trade_qualification import global_public_runtime_v1 as public
from app.trade_qualification.global_public_capture_companion_v1 import (
    GlobalPublicCompanionError,
    read_global_public_companion_v1,
    verify_global_public_companion_publication_v1,
)
from app.trade_qualification.global_public_runtime_v2 import (
    project_global_public_market_snapshot_v2,
)


class LegacyBaselineMarketReplayError(ValueError):
    """Fixed refusal without a path or raw market payload."""


@dataclass(frozen=True, slots=True, repr=False)
class ReplayedLegacyBaselineMarketV1:
    market: MarketSnapshot
    receipt_json: bytes
    source_authenticity_verified: Literal[False] = field(default=False, init=False)
    independent_availability_verified: Literal[False] = field(default=False, init=False)
    predictive_oos_eligible: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)


def replay_legacy_baseline_market_v1(
    root: Path,
    *,
    instrument_id: str,
    decision_at: datetime,
    expected_plan_sha256: str,
    expected_journal_sha256: str,
    expected_packet_sha256: str,
    expected_market_policy_sha256: str,
    post_readback_receipt_json: bytes,
    expected_post_readback_receipt_sha256: str,
    expected_journal_root_identity: tuple[int, int] | None = None,
) -> ReplayedLegacyBaselineMarketV1:
    """Join independently retained pins to a reopened native public journal."""
    if (
        type(decision_at) is not datetime
        or type(instrument_id) is not str
        or type(expected_packet_sha256) is not str
        or type(expected_market_policy_sha256) is not str
        or type(expected_post_readback_receipt_sha256) is not str
    ):
        raise LegacyBaselineMarketReplayError("legacy_market_replay_refused")
    try:
        decision = require_utc(decision_at, "legacy decision")
        if (
            type(post_readback_receipt_json) is not bytes
            or not 0 < len(post_readback_receipt_json) <= 1024
            or sha(post_readback_receipt_json) != expected_post_readback_receipt_sha256
        ):
            raise LegacyBaselineMarketReplayError(
                "legacy_market_post_readback_pin_changed"
            )
        receipt_data = decode(post_readback_receipt_json, 1024)
        readback_sample = ClockStamp.model_validate(receipt_data["sample"])
        capture = public.read_global_public_capture_v1(
            root,
            expected_plan_sha256=expected_plan_sha256,
            expected_journal_sha256=expected_journal_sha256,
            expected_root_identity=expected_journal_root_identity,
        )
        packet = capture.packet
        completion_sample = capture.journal_completion_sample
        deadline = capture.invocation_expires_at
        if (
            type(packet) is not public.GlobalPublicComponentPacketV1
            or packet.instrument_id != instrument_id
            or packet.bundle_sha256 != expected_packet_sha256
            or packet.policy_sha256 != expected_market_policy_sha256
        ):
            raise LegacyBaselineMarketReplayError("legacy_market_source_scope_changed")
        if post_readback_receipt_json != public._post_readback_receipt(
            instrument_id=instrument_id,
            plan_sha256=expected_plan_sha256,
            journal_sha256=expected_journal_sha256,
            packet_sha256=expected_packet_sha256,
            sample=readback_sample,
        ):
            raise LegacyBaselineMarketReplayError(
                "legacy_market_post_readback_scope_changed"
            )
        if type(completion_sample) is not ClockStamp or type(deadline) is not datetime:
            raise LegacyBaselineMarketReplayError("legacy_market_replay_refused")
        validate_stamps((completion_sample.model_dump(), readback_sample.model_dump()))
        completed = utc_from_ns(completion_sample.utc_ns)
        readback_at = utc_from_ns(readback_sample.utc_ns)
        if readback_at >= deadline:
            raise LegacyBaselineMarketReplayError("legacy_market_post_readback_expired")
        if (
            not packet.completed_at <= completed <= readback_at <= decision
            or readback_sample.monotonic_ns <= completion_sample.monotonic_ns
        ):
            raise LegacyBaselineMarketReplayError(
                "legacy_market_not_available_at_decision"
            )
        for component in (packet.quote, packet.market_aux):
            if any(
                not timedelta(0)
                <= decision - observation.source_time
                <= timedelta(seconds=component.policy.max_age_seconds)
                for observation in component.provenance
            ):
                raise LegacyBaselineMarketReplayError("legacy_market_component_stale")
        if (
            not timedelta(0)
            <= decision - packet.ws.ticker.source_time
            <= timedelta(seconds=packet.ws.policy.max_age_seconds)
        ):
            raise LegacyBaselineMarketReplayError("legacy_market_component_stale")
        if any(
            frame.verified_through != candles._floor(decision, frame.timeframe)
            for frame in packet.candles.frames
        ):
            raise LegacyBaselineMarketReplayError("legacy_market_candle_tail_missing")

        market, following_settlement = project_global_public_market_snapshot_v2(
            packet, evaluated_at=decision
        )
        if market.next_funding_time is None or market.next_funding_time <= decision:
            raise LegacyBaselineMarketReplayError("legacy_market_funding_expired")
        if not all(report.ok for report in market.quality.values()):
            raise LegacyBaselineMarketReplayError("legacy_market_candle_quality_failed")
        receipt = canonical(
            {
                "schema_version": "ctcc.mie.legacy_baseline_market_replay.v1",
                "environment": packet.environment,
                "instrument_id": instrument_id,
                "decision_at": decision.isoformat(),
                "journal_completed_at": completed.isoformat(),
                "post_readback_sample_at": readback_at.isoformat(),
                "capture_deadline_at": deadline.isoformat(),
                "post_readback_receipt_sha256": expected_post_readback_receipt_sha256,
                "snapshot_received_at": market.received_at.isoformat(),
                "funding_forecast_settlement_at": market.next_funding_time.isoformat(),
                "funding_following_forecast_at": following_settlement.isoformat(),
                "plan_sha256": expected_plan_sha256,
                "journal_sha256": expected_journal_sha256,
                "packet_sha256": expected_packet_sha256,
                "market_policy_sha256": expected_market_policy_sha256,
                "snapshot_sha256": sha(canonical(market.model_dump(mode="json"))),
                "source_authenticity_verified": False,
                "independent_availability_verified": False,
                "predictive_oos_eligible": False,
                "execution_authority": False,
            }
        )
    except LegacyBaselineMarketReplayError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, ArithmeticError):
        raise LegacyBaselineMarketReplayError("legacy_market_replay_refused") from None
    return ReplayedLegacyBaselineMarketV1(market, receipt)


def replay_legacy_baseline_market_from_companion_v1(
    journal_root: Path,
    companion_root: Path,
    *,
    publication_json: bytes,
    expected_publication_sha256: str,
    decision_at: datetime,
) -> ReplayedLegacyBaselineMarketV1:
    """Reopen the pinned companion after restart before replaying the raw journal."""
    try:
        publication = verify_global_public_companion_publication_v1(
            publication_json, expected_sha256=expected_publication_sha256
        )
        raw = read_global_public_companion_v1(
            journal_root, companion_root, publication=publication
        )
        result = replay_legacy_baseline_market_v1(
            journal_root,
            instrument_id=publication.instrument_id,
            decision_at=decision_at,
            expected_plan_sha256=publication.plan_sha256,
            expected_journal_sha256=publication.journal_sha256,
            expected_packet_sha256=publication.packet_sha256,
            expected_market_policy_sha256=publication.market_policy_sha256,
            post_readback_receipt_json=raw,
            expected_post_readback_receipt_sha256=publication.companion_sha256,
            expected_journal_root_identity=publication.journal_root_identity,
        )
    except (GlobalPublicCompanionError, OSError):
        raise LegacyBaselineMarketReplayError(
            "legacy_market_companion_refused"
        ) from None
    return result
