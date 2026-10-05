"""Synthetic, offline diagnostics; never a trusted account or order test."""

from __future__ import annotations

import hashlib
import json

import pytest

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_gap_inventory as gaps
from app.trade_qualification import account_materializer as materializer
from tests.unit.test_qualification_account_materializer import inputs, packet


@pytest.fixture(scope="module")
def source():
    captured, packet_sha256 = packet(pending=False)
    frozen = capture.freeze_demo_account_packet(
        captured, expected_plan_sha256=captured.plan_sha256
    )
    assert frozen.sha256 == packet_sha256
    supplied = inputs()
    inputs_sha256 = materializer.materialization_inputs_sha256(supplied)
    report = gaps.inventory_demo_account_gaps(
        frozen.payload,
        expected_packet_sha256=packet_sha256,
        expected_plan_sha256=captured.plan_sha256,
        inputs=supplied,
        expected_inputs_sha256=inputs_sha256,
    )
    return captured, frozen, supplied, inputs_sha256, report


def test_inventory_lists_actual_capture_and_mapping_blockers_without_authority(source):
    captured, frozen, supplied, inputs_sha256, report = source
    payload = json.loads(report.payload)
    findings = {entry["reason"]: entry for entry in payload["findings"]}
    assert set(captured.incomplete_reasons) <= set(findings)
    assert set(capture._BASE_GAPS) <= set(gaps._PROOF_BY_REASON)
    assert findings["source_authenticity_unverified"] == {
        "reason": "source_authenticity_unverified",
        "recorded_in": "capture",
        "missing_proof": "owned_authenticated_session_and_exact_uid_provenance",
    }
    assert findings["local_ledger_missing"]["recorded_in"] == "materialization"
    assert payload["packet_sha256"] == frozen.sha256
    assert payload["inputs_sha256"] == inputs_sha256
    assert payload["capture_gap_count"] == len(captured.incomplete_reasons)
    assert payload["materialization_gap_count"] == len(findings)
    assert payload["state"] == "diagnostic_incomplete"
    assert payload["formal_portfolio_snapshot_issued"] is False
    assert payload["account_complete"] is False
    assert payload["source_authenticity_verified"] is False
    assert payload["execution_authority"] is False
    assert report.account_complete is False
    assert report.execution_authority is False
    assert supplied.account_id.encode() not in report.payload


def test_inventory_is_deterministic_and_requires_source_readback(source):
    captured, frozen, supplied, inputs_sha256, report = source
    replay = gaps.verify_demo_account_gap_inventory(
        report,
        frozen.payload,
        expected_packet_sha256=frozen.sha256,
        expected_plan_sha256=captured.plan_sha256,
        inputs=supplied,
        expected_inputs_sha256=inputs_sha256,
    )
    assert replay == report
    assert hashlib.sha256(report.payload).hexdigest() == report.sha256

    altered = json.loads(report.payload)
    altered["findings"].pop()
    tampered_payload = json.dumps(
        altered, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    tampered = gaps.AccountGapInventory(
        payload=tampered_payload,
        sha256=hashlib.sha256(tampered_payload).hexdigest(),
    )
    with pytest.raises(gaps.AccountGapInventoryError, match="replay_mismatch"):
        gaps.verify_demo_account_gap_inventory(
            tampered,
            frozen.payload,
            expected_packet_sha256=frozen.sha256,
            expected_plan_sha256=captured.plan_sha256,
            inputs=supplied,
            expected_inputs_sha256=inputs_sha256,
        )


def test_inventory_rejects_unpinned_packet_and_inputs(source):
    captured, frozen, supplied, inputs_sha256, _ = source
    with pytest.raises(
        capture.AccountCaptureError, match="external_packet_pin_mismatch"
    ):
        gaps.inventory_demo_account_gaps(
            frozen.payload,
            expected_packet_sha256="0" * 64,
            expected_plan_sha256=captured.plan_sha256,
            inputs=supplied,
            expected_inputs_sha256=inputs_sha256,
        )
    with pytest.raises(
        materializer.AccountMaterializationError, match="inputs_pin_mismatch"
    ):
        gaps.inventory_demo_account_gaps(
            frozen.payload,
            expected_packet_sha256=frozen.sha256,
            expected_plan_sha256=captured.plan_sha256,
            inputs=supplied,
            expected_inputs_sha256="0" * 64,
        )
