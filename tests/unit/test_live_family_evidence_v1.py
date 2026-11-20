"""Synthetic history-family artifact codec; no Live account or trade acceptance."""

import json

import pytest
from pydantic import ValidationError

from app.domain.source_primitives import decode
from app.trade_evidence import live_v1 as evidence
from app.trade_evidence import native_v2, service, storage
from app.trade_qualification import owned_current_original_prefix_v1 as family
from app.trade_qualification.executable_quote_live_v1 import LiveExecutableQuoteV1
from tests.unit.test_native_owned_eight_family_v3 import (
    _pure_local,
    raw_family,  # noqa: F401 -- original raw-source pytest fixture
)


@pytest.fixture(scope="module")
def family_snapshot(raw_family):  # noqa: F811 -- shared original raw fixture
    record, journal, at, selected = _pure_local(raw_family)
    actual = family._evaluate_owned_family_local_math_v2(
        record, journal, observed_at=at
    )
    assert len(actual.gates) == 10 and all(g.passed for g in actual.gates)
    # Explicit synthetic risk DATA solely for artifact serialization. It never
    # enters an owner/SQL ledger, reserve/intent or exchange transport.
    values = {name: getattr(actual, name) for name in type(actual).model_fields}
    values["gates"] = (
        *actual.gates,
        family.GateAssessment(
            report_id=actual.report_id,
            gate=family.QualificationGate.RISK,
            passed=True,
            code="passed",
            reason="Synthetic artifact codec only; no account risk authority.",
            measured_values={"synthetic_data_only": True, "execution_authority": False},
        ),
    )
    qualification = family.OwnedFamilyEntryQualificationResultV2(**values)
    original = record["quote_context"].quote
    # Distinct declared Live DATA, never a Demo quote admitted by production.
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
    return evidence.prepare_live_evidence_v1(
        record["market"],
        record["analysis"],
        qualification=qualification,
        detection=record["computed"].detection,
        quote=quote,
        protection=selected,
        prepared_at=at,
        zone_tick_size=record["tick_size"],
    )


def test_exact_live_family_roundtrip_preserves_history_and_geometry(family_snapshot):
    snapshot = family_snapshot
    assert type(snapshot) is evidence.LiveFamilyEvidenceSnapshotV1
    assert type(snapshot.qualification) is family.OwnedFamilyEntryQualificationResultV2
    assert snapshot.qualification.history_admission_sha256 is not None
    assert (
        not snapshot.execution_authority and not snapshot.source_authenticity_verified
    )
    assert type(snapshot.quote) is LiveExecutableQuoteV1
    reopened = evidence.LiveFamilyEvidenceSnapshotV1.model_validate_json(
        snapshot.model_dump_json(round_trip=True), strict=True
    )
    assert evidence.validate_live_snapshot_v1(reopened) == snapshot
    assert reopened.qualification == snapshot.qualification
    assert reopened.detection == snapshot.detection
    assert reopened.protection_audit_json == snapshot.protection_audit_json
    with pytest.raises(service.EvidenceError):
        native_v2.validate_native_snapshot_v2(reopened)
    with pytest.raises(ValidationError):
        evidence.LiveEvidenceSnapshotV1.model_validate_json(
            snapshot.model_dump_json(round_trip=True), strict=True
        )


def test_missing_or_changed_family_history_never_roundtrips(family_snapshot):
    raw = family_snapshot.model_dump(mode="json", round_trip=True)
    for field in ("history_admission_sha256", "family_policy_sha256"):
        # A missing required family field or a changed sealed history must not
        # become a base result or retain the original candidate digest.
        changed = json.loads(json.dumps(raw))
        changed["qualification"][field] = "f" * 64
        with pytest.raises((ValidationError, service.EvidenceError)):
            snapshot = evidence.LiveFamilyEvidenceSnapshotV1.model_validate_json(
                json.dumps(changed), strict=True
            )
            evidence.validate_live_snapshot_v1(snapshot)


def test_live_family_render_retains_typed_history_in_six_files(family_snapshot):
    packet = evidence.render_live_evidence_v1(family_snapshot)
    assert tuple(packet) == storage.FILE_NAMES
    report = decode(packet["report.json"])
    saved = report["snapshot"]
    assert saved["schema_version"] == "ctcc_trade_evidence_live_family_v1"
    assert saved["quote"]["environment"] == "live"
    assert saved["qualification"]["history_admission_sha256"] == (
        family_snapshot.qualification.history_admission_sha256
    )
    assert report["execution_authority"] is False
