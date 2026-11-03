"""Fresh different-session original sources; no native/TLS/PG authority credit."""

import json

import pytest

from app.trade_qualification import account_history_original_stream_v1 as stream
from tests.unit import test_account_history_global_financial_v2 as originals
from tests.unit import test_account_history_original_financial_stream_v2 as wire_fixture
from tests.unit.test_account_financial_operands_v2 import native_position_source


async def acquired_wire_sessions(monkeypatch):
    def declared_all_product_plan(*, session_binding_id, **times):
        # This constructor runs before each acquisition. The inherited strict
        # three-time fixture does not accept a session keyword, so use an
        # explicit new AllProduct plan instead of relabelling any saved plan.
        assert set(times) == {"created_at", "history_start", "history_end"}
        return wire_fixture.capture.AllProductDemoAccountCapturePlan(
            plan_id="declared_cross_session_original_wire",
            created_at=times["created_at"],
            expected_uid=wire_fixture.UID,
            expected_main_uid=wire_fixture.MAIN_UID,
            session_binding_id=session_binding_id,
            settlement_currency="USDT",
            leverage_instrument_ids=(wire_fixture.INSTRUMENT,),
            history_start=times["history_start"],
            history_end=times["history_end"],
            registration_region="global",
            origin="https://openapi.okx.com",
            registration_evidence_sha256=wire_fixture.hashlib.sha256(
                b"DECLARED_SYNTHETIC_WIRE_REGISTRATION_NOT_AUTHENTICATED"
            ).hexdigest(),
            contract_version="ctcc.demo_account_plan.v5",
            capture_scope="all_standard_products_v5_documented_algos",
        )

    with wire_fixture._wire_construction(monkeypatch) as construction:
        construction.setattr(
            wire_fixture.acquisition, "regional", declared_all_product_plan
        )
        return await originals.acquire_distinct_session_sources(
            construction,
            bindings=(
                "declared-original-session-a",
                "declared-original-session-b",
                "declared-original-session-c",
            ),
        )


def arguments(frames):
    return wire_fixture.arguments(
        frames,
        expected_policy_sha256=stream.original_history_financial_stream_policy_sha256_v3(),
    )


@pytest.mark.asyncio
async def test_different_original_sessions_join_full_original_replay_and_latest_archive(
    monkeypatch,
):
    sources, batches = await acquired_wire_sessions(monkeypatch)
    assert len(sources) == len(batches) == 3
    assert len({reference.session_binding_sha256 for _, reference in sources}) == 3
    frames = wire_fixture.frames_for(sources, batches)
    original = wire_fixture.v1_replay(frames)
    result = stream.replay_original_history_financial_stream_v3(
        wire_fixture.pages_for(frames),
        **arguments(frames),
        position_source=native_position_source(sources[-1][1]),
    )
    assert type(result) is stream.OriginalHistoryFinancialStreamReplayV3
    assert result.original_replay.receipt_json == original.receipt_json
    data = json.loads(result.financial_facts.receipt_json)
    joined = json.loads(result.receipt_json)
    assert data["schema_version"] == "ctcc.global_original_financial_facts.v2"
    assert joined["schema_version"] == stream.FINANCIAL_STREAM_SCHEMA_V3
    assert joined["original_stream_receipt_sha256"] == original.receipt_sha256
    assert data["through_sequence"] == 3
    assert data["closed_runs"][0]["reconciled_closed_run_net_pnl"] == {
        "numerator": "7",
        "denominator": "20",
    }
    assert (
        data["funding_accrual_events"]
        is data["loss_streak_seed"]
        is data["historical_native_hwm"]
        is None
    )
    assert not data["source_authenticity_verified"]
    assert not data["account_complete"] and not result.execution_authority
    assert "native_acquisition_not_owned" in joined["remaining_obligations"]


@pytest.mark.asyncio
async def test_latest_archive_cannot_borrow_earlier_valid_session_and_legacy_stays_strict(
    monkeypatch,
):
    sources, batches = await acquired_wire_sessions(monkeypatch)
    frames = wire_fixture.frames_for(sources, batches)
    with pytest.raises(
        stream.OriginalHistoryStreamError, match="stream_financial_replay_failed"
    ):
        stream.replay_original_history_financial_stream_v3(
            wire_fixture.pages_for(frames),
            **arguments(frames),
            position_source=native_position_source(sources[0][1]),
        )
    # A new V3 policy cannot silently change an already versioned V2 contract.
    with pytest.raises(
        stream.OriginalHistoryStreamError, match="stream_financial_replay_failed"
    ):
        stream.replay_original_history_financial_stream_v2(
            wire_fixture.pages_for(frames),
            **wire_fixture.arguments(frames),
            position_source=native_position_source(sources[-1][1]),
        )


@pytest.mark.asyncio
async def test_sync_async_v3_same_original_bytes_are_deterministic(monkeypatch):
    sources, batches = await acquired_wire_sessions(monkeypatch)
    frames = wire_fixture.frames_for(sources, batches)
    position = native_position_source(sources[-1][1])
    first = stream.replay_original_history_financial_stream_v3(
        wire_fixture.pages_for(frames),
        **arguments(frames),
        position_source=position,
    )

    async def pages():
        for page in wire_fixture.pages_for(frames):
            yield page

    second = await stream.replay_original_history_financial_stream_async_v3(
        pages(),
        **arguments(frames),
        position_source=position,
    )
    assert second.receipt_json == first.receipt_json
    assert second.original_replay.receipt_json == first.original_replay.receipt_json
    assert second.financial_facts.receipt_json == first.financial_facts.receipt_json
