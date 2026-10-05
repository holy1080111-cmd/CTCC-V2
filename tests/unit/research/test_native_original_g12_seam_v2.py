"""Fail-closed same-task initial public ownership seam; synthetic IO only."""

import asyncio
from dataclasses import fields

import pytest

from app.public_market_source.public_market_receipts import sha
from app.trade_qualification import post_g12_public_runtime as coordinator
from app.trade_qualification import public_source_runtime as runtime
from app.trade_qualification import qualification_runtime as initial
from tests.unit.research.test_owned_public_runtime_v2 import (
    empty_registries,
    policy,
    setup,
)
from tests.unit.test_qualification_market_bridge import v2_engine_source


@pytest.fixture(scope="module")
def source():
    return v2_engine_source("long")


@pytest.mark.asyncio
async def test_native_original_is_consumed_here_but_cannot_publish_g12(
    source, tmp_path, monkeypatch
):
    _, directory, harness, publications = setup(monkeypatch, source)

    def forbidden(*_args, **_kwargs):
        pytest.fail("candidate and account sources are absent; no G12 or post-capture")

    monkeypatch.setattr(
        coordinator, "publish_qualification_evidence_liquidity_v2", forbidden
    )
    monkeypatch.setattr(coordinator, "_capture_after_publication_v2", forbidden)
    result = await coordinator.capture_native_original_for_g12_v2(
        tmp_path, instrument_id="BTC-USDT-SWAP", market_policy=policy()
    )

    assert result.code == "native_original_v2_candidate_source_required"
    assert result.initial_report_id.startswith("initial-")
    assert result.initial_packet_sha256 is not None
    assert result.journal_sha256 is not None
    assert result.observed_at is not None
    assert len(harness.requests) == 9
    assert not publications
    assert directory.content["plan.json"]
    _, _, persisted_packet = runtime.replay_public_runtime(
        directory, expected_plan_sha256=sha(directory.content["plan.json"])
    )
    assert result.initial_packet_sha256 == persisted_packet.bundle_sha256
    for name in (
        "original_source_verified",
        "candidate_created",
        "g12_published",
        "post_publication_captured",
        "execution_recheck_performed",
        "account_complete",
        "atomic_risk_reserved",
        "execution_authority",
        "order_submitted",
    ):
        assert getattr(result, name) is False
    assert result.admission == "DENY"
    assert {item.name for item in fields(result)}.isdisjoint(
        {"packet", "market", "context", "candidate", "receipt", "barrier"}
    )
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "supplied",
    [
        "original_market",
        "run",
        "original_inputs",
        "candidate",
        "receipt",
        "barrier",
        "passed",
    ],
)
async def test_native_original_seam_accepts_no_caller_replay_inputs(tmp_path, supplied):
    with pytest.raises(TypeError):
        await coordinator.capture_native_original_for_g12_v2(
            tmp_path,
            instrument_id="BTC-USDT-SWAP",
            market_policy=policy(),
            **{supplied: object()},
        )


@pytest.mark.asyncio
async def test_foreign_task_carrier_is_consumed_as_denial(
    source, tmp_path, monkeypatch
):
    _, directory, harness, publications = setup(monkeypatch, source)
    capture = initial._capture_initial_lineage_v2

    async def foreign_task(*args, **kwargs):
        return await asyncio.create_task(capture(*args, **kwargs))

    monkeypatch.setattr(initial, "_capture_initial_lineage_v2", foreign_task)
    result = await coordinator.capture_native_original_for_g12_v2(
        tmp_path, instrument_id="BTC-USDT-SWAP", market_policy=policy()
    )
    assert result.code == "native_original_v2_denied"
    assert result.initial_packet_sha256 is None
    assert not publications
    assert directory.content["summary.json"]
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_final_clock_expiry_cannot_reuse_initial_packet(
    source, tmp_path, monkeypatch
):
    clock, directory, harness, publications = setup(monkeypatch, source)

    def expired_stamp():
        clock.count += 31_000
        return clock.stamp()

    monkeypatch.setattr(coordinator, "native_stamp", expired_stamp)
    result = await coordinator.capture_native_original_for_g12_v2(
        tmp_path, instrument_id="BTC-USDT-SWAP", market_policy=policy()
    )
    assert result.code == "native_original_v2_denied"
    assert result.initial_packet_sha256 is None
    assert not publications
    assert directory.content["summary.json"]
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_cancellation_propagates_without_publication(tmp_path, monkeypatch):
    async def interrupted(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(initial, "_capture_initial_lineage_v2", interrupted)
    with pytest.raises(asyncio.CancelledError):
        await coordinator.capture_native_original_for_g12_v2(
            tmp_path, instrument_id="BTC-USDT-SWAP", market_policy=policy()
        )
    assert not runtime._INITIAL_RESULTS
    assert not coordinator._PUBLICATIONS
