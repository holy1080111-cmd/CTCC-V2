"""Synthetic clock/transport cases; no real R5 or trading acceptance."""

from __future__ import annotations

import json

import pytest

from app.domain.source_primitives import sha
from app.public_market_source.public_temporal_evidence_v2 import (
    PublicTemporalEvidenceError,
    _assess_component,
    inspect_runtime_temporal_evidence_v2,
)
from tests.unit.research.test_owned_public_runtime_v2 import (
    invoke,
    setup,
)
from tests.unit.research.test_owned_public_runtime_v2 import (
    source as source,  # noqa: PLC0414 -- module-scoped synchronous fixture
)
from tests.unit.research.test_public_journal_contracts import MemoryDirectory

BASE = 1_700_000_000_000_000_000


def stamp(offset_ms):
    return {
        "utc_ns": BASE + offset_ms * 1_000_000,
        "monotonic_ns": 1_000_000_000 + offset_ms * 1_000_000,
    }


def inspect_component(*, source_ms=-500, barrier_ms=-100, **changes):
    values = {
        "role": "ticker",
        "ts_raw": str((BASE // 1_000_000) + source_ms),
        "raw_sha256": "a" * 64,
        "dispatch": stamp(0),
        "receipt": stamp(10),
        "eof": stamp(20),
        "closed": stamp(21),
        "validation_upper_bound": stamp(30),
        "barrier": stamp(barrier_ms),
        "max_age_seconds": 5,
    }
    values.update(changes)
    return _assess_component(**values)


def test_cached_pre_request_ticker_remains_diagnostic_after_new_dispatch():
    result = inspect_component()
    assert result["content_relation_to_request"] == "pre_request_cached_content"
    assert result["dispatch"]["utc_ns"] > result["barrier"]["utc_ns"]
    assert result["post_barrier_generation_required"] is True
    assert result["post_barrier_generation_satisfied"] is False
    assert result["content_ts_raw"] == str(BASE // 1_000_000 - 500)


def test_generation_after_barrier_does_not_rewrite_content_or_receipt_time():
    result = inspect_component(source_ms=-50)
    assert result["content_relation_to_request"] == "pre_request_cached_content"
    assert result["post_barrier_generation_satisfied"] is True
    assert result["content_ts_utc_ns"] == BASE - 50_000_000
    assert result["dispatch"]["utc_ns"] == BASE


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"source_ms": 11}, "temporal_source_after_receipt"),
        ({"source_ms": -6000}, "temporal_source_stale"),
        ({"barrier_ms": 0}, "temporal_dispatch_before_barrier"),
        (
            {"receipt": {"utc_ns": BASE + 10_000_000, "monotonic_ns": 0}},
            "temporal_native_clock_invalid",
        ),
    ],
)
def test_future_stale_barrier_and_native_clock_reversal_deny(changes, code):
    source_ms = changes.pop("source_ms", -500)
    barrier_ms = changes.pop("barrier_ms", -100)
    with pytest.raises(PublicTemporalEvidenceError, match=code):
        inspect_component(source_ms=source_ms, barrier_ms=barrier_ms, **changes)


def test_exchange_return_stamp_is_not_relabelled_as_generation():
    result = inspect_component(role="funding", source_ms=-500)
    assert result["timestamp_semantics"] == "exchange_data_return"
    assert result["content_timestamp_has_generation_semantics"] is False
    assert result["post_barrier_generation_required"] is False
    assert result["post_barrier_generation_satisfied"] is None


def test_ws_message_has_no_fabricated_http_headers_or_body_eof():
    result = inspect_component(
        role="ws_ticker", source_ms=-50, eof=None, receipt=stamp(10)
    )
    assert result["http_body_eof"] is None
    assert result["timestamp_semantics"] == "ticker_generation"
    assert result["post_barrier_generation_satisfied"] is True


@pytest.mark.asyncio
async def test_sealed_synthetic_runtime_journal_replays_distinct_native_phases(
    tmp_path, monkeypatch, source
):
    _clock, directory, harness, _ = setup(monkeypatch, source)
    result = await invoke(tmp_path)
    assert result.packet is not None
    evidence = inspect_runtime_temporal_evidence_v2(
        MemoryDirectory(dict(directory.content)),
        expected_plan_sha256=sha(directory.content["plan.json"]),
    )
    receipt = json.loads(evidence.receipt_json)
    assert receipt["schema_version"] == "ctcc.public.temporal_evidence.v2"
    assert receipt["stage"] == "initial_public"
    assert receipt["post_barrier_generation_satisfied"] is None
    assert receipt["post_barrier_generation_required_roles"] == []
    assert receipt["generation_semantics_unproven_roles"] == [
        "funding",
        "mark",
        "open_interest",
    ]
    assert [item["role"] for item in receipt["components"]] == [
        "funding",
        "mark",
        "ticker",
        "books",
        "open_interest",
        "ws_ticker",
    ]
    assert any(
        item["content_relation_to_request"] == "pre_request_cached_content"
        for item in receipt["components"]
    )
    assert all(
        item["dispatch"]["monotonic_ns"]
        <= item["headers_or_message_received"]["monotonic_ns"]
        <= item["closed"]["monotonic_ns"]
        <= item["packet_preseal_stamp"]["monotonic_ns"]
        for item in receipt["components"]
    )
    assert receipt["per_component_online_validation_instant"] is None
    assert "after_stamp_unmeasured" in receipt["online_validation_time_semantics"]
    assert receipt["journal_native_origin_independently_attested"] is False
    assert receipt["source_authenticity_verified"] is False
    assert receipt["execution_authority"] is False
    assert receipt["admission"] == "DENY" == evidence.admission
    assert result.execution_authority is False
    harness.assert_closed()


@pytest.mark.asyncio
async def test_tampered_sealed_runtime_raw_does_not_create_temporal_evidence(
    tmp_path, monkeypatch, source
):
    _clock, directory, harness, _ = setup(monkeypatch, source)
    assert (await invoke(tmp_path)).packet is not None
    contents = dict(directory.content)
    name = next(key for key in contents if key.endswith(".raw"))
    contents[name] += b" "
    with pytest.raises(
        PublicTemporalEvidenceError, match="temporal_runtime_replay_denied"
    ):
        inspect_runtime_temporal_evidence_v2(
            MemoryDirectory(contents),
            expected_plan_sha256=sha(directory.content["plan.json"]),
        )
    harness.assert_closed()
