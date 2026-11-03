"""Replay the original event's required confirmed-interval path.

The original base event policy uses low/high touches of fully post-setup
CONFIRMED intervals. Its source extractor explicitly excludes the interval
spanning setup because that wick's ordering is unknown. This module preserves
that exact policy: it does not introduce continuous ticks, a new invalidation
rule, a newer event, or an indefinitely unavailable future watermark.

This pure result establishes calculation only. Native source ownership belongs
to the same-invocation caller and cannot be reconstructed from this record.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from app.domain.source_primitives import canonical, sha
from app.market.quality.candles import BAR_SECONDS, candle_closed_at
from app.trade_qualification import continuation, data
from app.trade_qualification.continuation import ContinuationResult

_POLICY = canonical(
    {
        "version": "ctcc.original_base_confirmed_event_path.v1",
        "extractor_source_sha256": "18361eb3cdc1719276e72d86360c9b4d91e47191c13ab75aecb85233439e6b1d",
        "continuation_source_sha256": "f38aa72ab7740d937229b0637ff400450861ececdd780f948f8c69d727490156",
        "timeframes": list(continuation.TIMEFRAMES),
        "invalidation": "long_low_lte_or_short_high_gte_original_invalidation",
        "required_interval": "confirmed_and_open_at_or_after_original_setup",
        "setup_spanning_interval": "excluded_original_event_policy_unknown_order",
        "history": "exact_original_prefix_and_all_confirmed_closes_through_cutoff",
        "event": "replay_original_source_and_detection_never_extract_current_event",
        "deadline": "unchanged_original_trigger_expiry",
        "continuous_tick_path_claimed": False,
    }
)
POLICY_SHA256 = sha(_POLICY)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _require_event_policy_v1():
    root = Path(__file__).resolve().parent
    for filename, digest in (
        (
            "events.py",
            "18361eb3cdc1719276e72d86360c9b4d91e47191c13ab75aecb85233439e6b1d",
        ),
        (
            "continuation.py",
            "f38aa72ab7740d937229b0637ff400450861ececdd780f948f8c69d727490156",
        ),
    ):
        if sha((root / filename).read_bytes()) != digest:
            raise ValueError("fixed_original_event_policy_source_changed")


@dataclass(frozen=True, slots=True)
class FixedOriginalEventPathV1:
    """A replay result, never a bearer capability or source attestation."""

    continuation: ContinuationResult = field(repr=False)
    code: str
    observed_at: datetime
    policy_sha256: str = field(default=POLICY_SHA256, init=False)
    continuous_tick_path_claimed: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)

    @property
    def required_event_path_complete(self):
        return self.code == "passed" and self.continuation.passed

    @property
    def evaluation_sha256(self):
        return sha(
            canonical(
                {
                    "schema_version": "ctcc.fixed_original_event_path.v1",
                    "policy_sha256": self.policy_sha256,
                    "continuation_sha256": self.continuation.evaluation_sha256,
                    "code": self.code,
                    "observed_at": self.observed_at.isoformat(),
                    "required_event_path_complete": self.required_event_path_complete,
                    "continuous_tick_path_claimed": False,
                    "execution_authority": False,
                }
            )
        )


def evaluate_fixed_original_event_path_v1(
    original_market,
    original_analysis,
    current_market,
    *,
    detection,
    observed_at,
):
    """Recompute exact original policy and reject every required history gap.

    Current quote/zone, fixed protection, costs and account/controls are separate
    checks in the owning R7 continuation. A surviving event cannot repair one.
    """
    _require_event_policy_v1()
    result = continuation.evaluate_continuation(
        original_market,
        original_analysis,
        current_market,
        detection=detection,
        observed_at=observed_at,
    )
    code = result.code
    if result.passed:
        # The old helper checks the closed tail at source receipt. Recheck it at
        # the LATER native evaluation cutoff: an intervening close requires its
        # actual captured row. No source time or source content is rewritten.
        for coverage in result.coverage:
            interval = timedelta(seconds=BAR_SECONDS[coverage.timeframe])
            expected = _EPOCH + ((observed_at - _EPOCH) // interval) * interval
            rows = current_market.candles[coverage.timeframe]
            if (
                coverage.verified_through != expected
                or candle_closed_at(rows[-1], coverage.timeframe) != expected
                or not rows[-1].confirmed
            ):
                code = "required_confirmed_tail_missing_at_cutoff"
                break
    return FixedOriginalEventPathV1(result, code, observed_at)


def verify_fixed_original_event_path_v1(result, *sources, **inputs):
    """A recorded success is replayed from exact unchanged source operands."""
    if type(result) is not FixedOriginalEventPathV1:
        raise ValueError("fixed_original_event_path_exact_result_required")
    repeated = evaluate_fixed_original_event_path_v1(*sources, **inputs)
    if result != repeated:
        raise ValueError("fixed_original_event_path_replay_mismatch")
    return repeated


def join_confirmed_original_path_v1(original_market, current_market, *, detection):
    """A derived append-only view for a rolling native history window.

    Every required post-setup interval must be in the NEW source. Only older
    pre-setup rows may come from the retained original source; every overlap
    field must match. Raw packets and the two supplied snapshots stay intact.
    This is a domain projection, never a rewritten packet or source receipt.
    """
    before = data._market_copy(original_market)
    current = data._market_copy(current_market)
    if (before.instrument_id, before.symbol) != (current.instrument_id, current.symbol):
        raise ValueError("fixed_path_source_identity_changed")
    if detection.setup_time is None:
        raise ValueError("fixed_path_original_setup_required")
    combined = current.model_copy(deep=True)
    for timeframe in continuation.TIMEFRAMES:
        old, new = before.candles[timeframe], current.candles[timeframe]
        if not old or not new or new[0].timestamp > detection.setup_time:
            raise ValueError("fixed_path_required_post_setup_history_missing")
        old_by_time = {row.timestamp: row for row in old}
        new_by_time = {row.timestamp: row for row in new}
        if (
            len(old_by_time) != len(old)
            or len(new_by_time) != len(new)
            or old[-1].timestamp not in new_by_time
            or any(
                new_by_time[at] != row
                for at, row in old_by_time.items()
                if at in new_by_time
            )
        ):
            raise ValueError("fixed_path_overlap_changed_or_missing")
        retained = [row for row in old if row.timestamp < new[0].timestamp]
        if any(
            candle_closed_at(row, timeframe) > detection.setup_time for row in retained
        ):
            raise ValueError("fixed_path_required_interval_not_recaptured")
        joined = retained + new
        if len(joined) > 1024:
            raise ValueError("fixed_path_join_exceeds_existing_bound")
        combined.candles[timeframe] = joined
    return combined
