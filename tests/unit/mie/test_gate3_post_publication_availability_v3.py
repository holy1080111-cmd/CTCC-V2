"""Synthetic V3 durable-readback contract tests; no real PostgreSQL claim."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadError,
    PublicReceiptPostReadReadback,
    PublicReceiptPostReadRepository,
)
from app.mie.contracts import ForecastHorizon
from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.post_publication_availability_v2 import (
    PostPublicationAvailabilityError,
)
from app.mie.validation.post_publication_availability_v3 import (
    PostPublicationCaptureV3,
    computational_point_in_time_rows_v3,
    freeze_post_publication_capture_v3,
    verify_post_publication_capture_v3,
)
from app.mie.validation.replay import ReplayValidationError, replay_features_at

pytestmark = pytest.mark.asyncio
pytest_plugins = ("tests.unit.mie.test_gate3_post_publication_availability_v2",)


@pytest.fixture
def observation_case(monkeypatch, synthetic_case):
    source_kwargs, source_clock, _ = synthetic_case
    repository = PublicReceiptPostReadRepository(
        None, source_kwargs["witness_repository"]
    )
    state = {"readback": None, "append_calls": 0}

    async def append_new(**pins):
        state["append_calls"] += 1
        if state["readback"] is not None:
            raise PublicReceiptPostReadError("post_read_rejected")
        observed = pins["not_before"] + timedelta(seconds=1)
        state["readback"] = PublicReceiptPostReadReadback(
            **{key: value for key, value in pins.items() if key != "not_before"},
            capture_sequence=1,
            capture_head_sha256=source_kwargs["journal"].checkpoint.head_sha256,
            witness_recorded_at=pins["not_before"] - timedelta(seconds=1),
            observed_at=observed,
            database_readback_at=observed + timedelta(seconds=1),
        )
        return state["readback"]

    async def read(**pins):
        if state["readback"] is None:
            raise PublicReceiptPostReadError("post_read_missing")
        for key, value in pins.items():
            if key != "not_before" and getattr(state["readback"], key) != value:
                raise PublicReceiptPostReadError("post_read_readback_mismatch")
        if pins["not_before"] > state["readback"].observed_at:
            raise PublicReceiptPostReadError("post_read_readback_mismatch")
        return state["readback"]

    monkeypatch.setattr(repository, "append_new", append_new)
    monkeypatch.setattr(repository, "read", read)
    return source_kwargs, source_clock, repository, state


async def test_v3_persists_exact_v2_and_stays_computational(observation_case):
    source_kwargs, source_clock, repository, state = observation_case
    frozen = await freeze_post_publication_capture_v3(
        observation_repository=repository, **source_kwargs
    )
    result = await verify_post_publication_capture_v3(
        frozen.payload,
        expected_sha256=frozen.sha256,
        observation_repository=repository,
        **source_kwargs,
    )
    assert result == frozen.contract
    assert result.v2_capture.available_at == source_clock["at"]
    assert result.available_at == state["readback"].observed_at
    assert result.available_at > result.v2_capture.available_at
    assert result.persisted_observation_independently_readable
    assert not result.trusted_clock_verified
    assert not result.independently_protected
    assert not result.evaluator_first_read_proven
    assert not result.predictive_oos_eligible
    assert not result.execution_authority
    assert state["append_calls"] == 1
    with pytest.raises(PublicReceiptPostReadError, match="post_read_rejected"):
        await freeze_post_publication_capture_v3(
            observation_repository=repository, **source_kwargs
        )


async def test_v3_rejects_changed_persisted_row_and_source(observation_case):
    source_kwargs, source_clock, repository, state = observation_case
    frozen = await freeze_post_publication_capture_v3(
        observation_repository=repository, **source_kwargs
    )
    original = state["readback"]
    state["readback"] = replace(
        original, observed_at=original.observed_at + timedelta(seconds=1)
    )
    with pytest.raises(ArtifactVerificationError, match="persisted post-read"):
        await verify_post_publication_capture_v3(
            frozen.payload,
            expected_sha256=frozen.sha256,
            observation_repository=repository,
            **source_kwargs,
        )
    state["readback"] = original
    source_clock["at"] = frozen.contract.v2_capture.available_at - timedelta(seconds=1)
    with pytest.raises(ArtifactVerificationError):
        await verify_post_publication_capture_v3(
            frozen.payload,
            expected_sha256=frozen.sha256,
            observation_repository=repository,
            **source_kwargs,
        )


async def test_v3_flags_and_pins_cannot_be_changed(observation_case):
    source_kwargs, _, repository, _ = observation_case
    frozen = await freeze_post_publication_capture_v3(
        observation_repository=repository, **source_kwargs
    )
    changed = frozen.contract.model_dump(mode="python")
    changed["predictive_oos_eligible"] = True
    with pytest.raises(ValueError):
        PostPublicationCaptureV3.model_validate(changed)
    changed = frozen.contract.model_dump(mode="python")
    changed["post_read_observation"]["receipt_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="post-read source or time pins differ"):
        PostPublicationCaptureV3.model_validate(changed)


async def test_v3_replays_original_raw_page_before_accepting_db_row(observation_case):
    source_kwargs, _, repository, _ = observation_case
    frozen = await freeze_post_publication_capture_v3(
        observation_repository=repository, **source_kwargs
    )
    entries, _ = source_kwargs["journal"].read_all()
    entries[0][2]["page-000.raw"] += b" "
    with pytest.raises(PostPublicationAvailabilityError):
        await verify_post_publication_capture_v3(
            frozen.payload,
            expected_sha256=frozen.sha256,
            observation_repository=repository,
            **source_kwargs,
        )


async def test_offline_adapter_uses_later_persisted_time_not_v2_sample(
    observation_case,
):
    source_kwargs, _, repository, _ = observation_case
    frozen = await freeze_post_publication_capture_v3(
        observation_repository=repository, **source_kwargs
    )
    rows = await computational_point_in_time_rows_v3(
        frozen.payload,
        expected_sha256=frozen.sha256,
        observation_repository=repository,
        **source_kwargs,
    )
    assert len(rows) == len(frozen.contract.v2_capture.rows)
    assert all(row.available_at == frozen.contract.available_at for row in rows)
    assert all(
        row.available_at > frozen.contract.v2_capture.available_at for row in rows
    )
    with pytest.raises(
        ReplayValidationError,
        match="a due replay bar was not available at the cutoff",
    ):
        replay_features_at(
            rows,
            as_of=frozen.contract.v2_capture.available_at,
            bar_horizon=ForecastHorizon(label="1m", seconds=60),
        )
