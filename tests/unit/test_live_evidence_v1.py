"""Synthetic artifact verification only; no Live source, qualification or order."""

import hashlib
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.domain.source_primitives import ClockStamp, utc_from_ns
from app.trade_evidence import live_v1 as evidence
from app.trade_evidence import native_v2, renderer, service, storage
from app.trade_qualification.executable_quote_live_v1 import LiveExecutableQuoteV1
from tests.unit.test_native_evidence_v2 import inputs  # noqa: F401 -- shared fixture


@pytest.fixture(scope="module")
def live_inputs(inputs):  # noqa: F811 -- imported shared pytest fixture
    source, market, analysis, selection, original = inputs
    quote = LiveExecutableQuoteV1(
        report_id=original.report_id,
        instrument_id=original.instrument_id,
        environment="live",
        capture_started_at=original.capture_started_at,
        capture_completed_at=original.capture_completed_at,
        ticker=original.ticker,
        mark=original.mark,
        funding=original.funding,
    )
    kwargs = {
        "qualification": source.original_run.result,
        "detection": source.original_run.prefix.detection,
        "quote": quote,
        "protection": selection,
        "prepared_at": source.original_run.result.evaluated_at,
        "zone_tick_size": source.original_run.policy.prefix.tick_size,
    }
    return market, analysis, kwargs, original


@pytest.fixture(scope="module")
def snapshot(live_inputs):
    market, analysis, kwargs, _ = live_inputs
    return evidence.prepare_live_evidence_v1(market, analysis, **kwargs)


def test_exact_live_wire_replays_without_demo_or_legacy_alias(snapshot):
    assert type(snapshot) is evidence.LiveEvidenceSnapshotV1
    assert type(snapshot.quote) is LiveExecutableQuoteV1
    assert snapshot.quote.environment == "live"
    assert not snapshot.execution_authority
    raw = snapshot.model_dump_json(round_trip=True)
    reopened = evidence.LiveEvidenceSnapshotV1.model_validate_json(raw, strict=True)
    assert evidence.validate_live_snapshot_v1(reopened) == snapshot
    assert (
        renderer._quote_display(reopened)[1]
        == snapshot.quote.ticker.source_generated_at
    )
    with pytest.raises(ValidationError):
        native_v2.NativeEvidenceSnapshotV2.model_validate_json(raw, strict=True)


@pytest.mark.parametrize(
    "scope,name,changed",
    (
        ("quote", "environment", "demo"),
        ("quote", "schema_version", "ctcc.executable_quote.v2"),
        ("quote", "execution_authority", True),
        ("quote", "source_authenticity_verified", True),
        ("quote", "admission", "ALLOW"),
        ("funding", "rate_generated_at", "2026-01-01T00:00:00Z"),
        ("funding", "execution_authority", True),
    ),
)
def test_changed_wire_metadata_or_omitted_fields_never_roundtrip(
    snapshot, scope, name, changed
):
    for missing in (False, True):
        doc = json.loads(snapshot.model_dump_json(round_trip=True))
        target = doc["quote"] if scope == "quote" else doc["quote"]["funding"]
        if missing:
            del target[name]
        else:
            target[name] = changed
        with pytest.raises(ValidationError):
            evidence.LiveEvidenceSnapshotV1.model_validate_json(
                json.dumps(doc), strict=True
            )


def test_demo_quote_cannot_be_used_by_live_adapter(live_inputs):
    market, analysis, kwargs, demo = live_inputs
    with pytest.raises(service.EvidenceError):
        evidence.prepare_live_evidence_v1(market, analysis, **{**kwargs, "quote": demo})
    with pytest.raises(service.EvidenceError):
        evidence.prepare_live_evidence_v1(
            market,
            analysis,
            **{**kwargs, "quote": replace(kwargs["quote"], environment="demo")},
        )


def test_all_six_artifacts_are_read_back_and_old_packet_gets_no_new_barrier(
    tmp_path, snapshot
):
    published = evidence.publish_live_evidence_v1(tmp_path, snapshot)
    receipt = published.receipt
    assert receipt.status == "written"
    assert tuple(item.name for item in receipt.files) == storage.FILE_NAMES
    assert type(published.publication_barrier) is ClockStamp
    assert utc_from_ns(published.publication_barrier.utc_ns) >= receipt.completed_at
    assert published.execution_authority is False
    assert published.recheck_performed is False
    for item in receipt.files:
        raw = (tmp_path / snapshot.report_id / item.name).read_bytes()
        assert len(raw) == item.size_bytes
        assert hashlib.sha256(raw).hexdigest() == item.sha256
    doc = json.loads((tmp_path / snapshot.report_id / "report.json").read_bytes())
    assert doc["native_source_contract"] == "ctcc.native_live_original_candidate.v1"
    assert doc["snapshot"]["quote"]["environment"] == "live"
    assert doc["execution_authority"] is False
    before = {p.name: p.read_bytes() for p in (tmp_path / snapshot.report_id).iterdir()}
    with pytest.raises(service.EvidenceError, match="^live_new_publication_required$"):
        evidence.publish_live_evidence_v1(tmp_path, snapshot)
    assert before == {
        p.name: p.read_bytes() for p in (tmp_path / snapshot.report_id).iterdir()
    }


def test_corrupted_readback_never_returns_a_live_publication(
    tmp_path, snapshot, monkeypatch
):
    original = storage._verify

    def corrupt(directory, packet):
        original(directory, packet)
        raise storage.EvidencePublicationError("synthetic_readback_failure")

    monkeypatch.setattr(storage, "_verify", corrupt)
    with pytest.raises(storage.EvidencePublicationError):
        evidence.publish_live_evidence_v1(tmp_path, snapshot)
    # A late refusal must not erase the already durable evidence packet.
    assert len(tuple((tmp_path / snapshot.report_id).iterdir())) == len(
        storage.FILE_NAMES
    )
