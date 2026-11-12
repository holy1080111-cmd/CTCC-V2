"""Synthetic replay input and score isolation for a frozen raw scorer."""

from datetime import timedelta
from decimal import ROUND_DOWN, localcontext
from typing import get_args

import pytest

from app.domain.source_primitives import canonical, sha
from app.mie.validation import frozen_legacy_raw_scorer_v1 as scorer
from app.mie.validation.legacy_baseline_market_replay_v1 import (
    replay_legacy_baseline_market_v1,
)
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
)
from tests.unit.mie.test_legacy_baseline_market_replay_v1 import capture
from tests.unit.research.test_owned_global_public_runtime_v1 import (
    market as market,  # noqa: PLC0414 -- register the existing fixture.
)
from tests.unit.research.test_owned_global_public_runtime_v1 import (
    owned_global as owned_global,  # noqa: PLC0414 -- register the existing fixture.
)
from tests.unit.strategies.test_pure_conditions import fixture


@pytest.mark.asyncio
async def test_exact_replay_bytes_bind_config_source_snapshot_and_decision(
    owned_global, tmp_path, monkeypatch
):
    pins, _ = await capture(owned_global, tmp_path)
    replay = replay_legacy_baseline_market_v1(tmp_path, **pins)
    snapshot_json = canonical(replay.market.model_dump(mode="json"))
    source = DevvalCandidateSourceV1(
        commit="a" * 40, tree="b" * 40, manifest_sha256="c" * 64
    )
    config = scorer.FrozenLegacyRawScorerConfigV1(
        strategy="trend_pullback",
        analysis_version="synthetic-v1",
        source_checkpoint=source,
        source_archive_sha256="d" * 64,
    )
    arguments = {
        "config_json": config.canonical_json_bytes(),
        "expected_config_sha256": config.canonical_sha256(),
        "expected_source_checkpoint": source,
        "expected_source_archive_sha256": config.source_archive_sha256,
        "receipt_json": replay.receipt_json,
        "expected_receipt_sha256": replay.receipt_sha256,
        "snapshot_json": snapshot_json,
        "expected_snapshot_sha256": sha(snapshot_json),
        "instrument_id": pins["instrument_id"],
        "decision_at": pins["decision_at"],
    }
    first = scorer.replay_frozen_legacy_raw_score_v1(**arguments)
    assert (
        scorer.replay_frozen_legacy_raw_score_v1(**arguments).payload == first.payload
    )
    with localcontext() as ambient:
        ambient.prec = 7
        ambient.rounding = ROUND_DOWN
        assert (
            scorer.replay_frozen_legacy_raw_score_v1(**arguments).payload
            == first.payload
        )
    assert first.contract.market_replay_receipt_sha256 == replay.receipt_sha256
    assert (
        first.contract.execution_authority
        is first.contract.predictive_oos_eligible
        is False
    )

    with pytest.raises(scorer.FrozenLegacyRawScoreError, match="time_changed"):
        scorer.replay_frozen_legacy_raw_score_v1(
            **(arguments | {"decision_at": pins["decision_at"] + timedelta(seconds=1)})
        )
    with pytest.raises(scorer.FrozenLegacyRawScoreError, match="source_changed"):
        scorer.replay_frozen_legacy_raw_score_v1(
            **(arguments | {"expected_source_archive_sha256": "e" * 64})
        )
    with pytest.raises(scorer.FrozenLegacyRawScoreError, match="market_missing"):
        scorer.replay_frozen_legacy_raw_score_v1(
            **(arguments | {"snapshot_json": None})
        )

    def refused_neutral(*args, **kwargs):
        pytest.fail("neutral reached directional predicates")

    for strategy in get_args(scorer.Strategy):
        selected = config.model_copy(update={"strategy": strategy})
        strategy_arguments = arguments | {
            "config_json": selected.canonical_json_bytes(),
            "expected_config_sha256": selected.canonical_sha256(),
        }
        for direction in ("long", "short", "neutral"):
            ctx = fixture(strategy, direction)
            with monkeypatch.context() as patch:
                patch.setattr(
                    scorer,
                    "analyze_snapshot_at",
                    lambda *args, analysis=ctx.analysis, **kwargs: analysis,
                )
                if direction == "neutral":
                    expected = None
                    patch.setattr(scorer, "assess_conditions", refused_neutral)
                else:
                    expected = scorer.assess_conditions(ctx, strategy).score
                scored = scorer.replay_frozen_legacy_raw_score_v1(
                    **strategy_arguments
                ).contract
            assert scored.strategy == strategy
            assert scored.raw_direction == direction
            assert scored.raw_score == expected


@pytest.mark.parametrize("strategy", get_args(scorer.Strategy))
@pytest.mark.parametrize("direction", ("long", "short", "neutral"))
def test_direction_guard_matches_all_eight_legacy_direction_selectors(
    strategy, direction
):
    ctx = fixture(strategy, direction)
    assert scorer._condition_direction(ctx, strategy) == direction
    assert scorer.assess_conditions(ctx, strategy).direction == direction
