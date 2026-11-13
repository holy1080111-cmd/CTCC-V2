"""Synthetic controls for the test-only, redacted fitted-catalog receipt."""

from __future__ import annotations

import copy
import json
import runpy
from pathlib import Path

import pytest

from scripts import current0056_fitted_catalog_measurement_v1 as capture
from scripts import current0056_fitted_catalog_pair_v1 as pair
from scripts.verify_final_hermetic import verify_pytest_report


def _inputs(monkeypatch):
    source = runpy.run_path(
        str(Path(__file__).with_name("test_current0056_fitted_delta_catalog_v1.py"))
    )
    migration = {capture.MIGRATION_PATH: capture.MIGRATION_SHA256}
    monkeypatch.setattr(capture, "MIGRATION_INVENTORY_COUNT", 1)
    monkeypatch.setattr(
        capture,
        "MIGRATION_INVENTORY_SHA256",
        capture._sha(capture._canonical(migration)),
    )
    files = {path: {"sha256": "a" * 64} for path in capture._REQUIRED_FILES}
    files[capture.MIGRATION_PATH]["sha256"] = capture.MIGRATION_SHA256
    source_raw = capture._canonical(files)
    identity = {
        "runner_contract": "ctcc.current0056.hermetic.v1",
        "source_contract_head": "0056",
        "source_files_sha256": capture._sha(source_raw),
        "commit": "1" * 40,
        "tree": "2" * 40,
        "archive_sha256": "3" * 64,
        "manifest_sha256": "a" * 64,
        "run_id": "b" * 12,
    }
    catalog = source["synthetic_catalog"]()
    catalog["server_build"] = pair.PG_BUILD
    return identity, files, source_raw, catalog, source["ROLES"]


def _build(inputs):
    identity, files, source_raw, catalog, roles = inputs
    return capture.build_measurement(
        identity=identity,
        source_files=files,
        source_files_raw=source_raw,
        catalog=catalog,
        **roles,
        cluster_system_identifier="12345678901234567890",
        database_name="ctcc_g3fcat_123456789abc",
        database_oid=12990,
    )


def test_synthetic_measurement_is_redacted_and_denied(monkeypatch):
    inputs = _inputs(monkeypatch)
    raw = _build(inputs)
    identity, files, _, _, _ = inputs
    receipt = capture.verify_measurement(raw, identity=identity, source_files=files)
    assert receipt["admission"] == "DENY"
    assert receipt["actual_measurement_independently_attested"] is False
    assert receipt["native_reference_sealed"] is False
    assert b"ctcc_fitted_publisher" not in raw
    assert b"ctcc_native_writer" not in raw
    assert b"12345678901234567890" not in raw
    assert b"12990" not in raw
    assert receipt["source"]["commit"] == identity["commit"]


def test_inherited_receipt_keeps_raw_and_portable_pregrant_digests_separate(
    monkeypatch,
):
    identity, files, _, _, _ = _inputs(monkeypatch)
    digests = {
        "owner_pregrant_inherited_sha256": "a" * 64,
        "owner_pregrant_readwrite_inherited_sha256": "a" * 64,
        "owner_pregrant_fitted_raw_sha256": "b" * 64,
        "owner_pregrant_fitted_normalized_sha256": "c" * 64,
        "postgrant_readonly_inherited_sha256": "d" * 64,
        "postgrant_readwrite_inherited_sha256": "d" * 64,
    }
    value = capture._inherited_receipt(identity, files, "f" * 64, digests)
    raw = capture._canonical(value)
    assert (
        capture.verify_inherited_measurement(raw, identity=identity, source_files=files)
        == value
    )
    assert value["startup_admission"] is False
    value["definitions"]["postgrant_readwrite_inherited_sha256"] = "e" * 64
    with pytest.raises(capture.FittedMeasurementRefusal):
        capture.verify_inherited_measurement(
            capture._canonical(value), identity=identity, source_files=files
        )


def test_changed_archive_inventory_or_head_refused(monkeypatch):
    inputs = _inputs(monkeypatch)
    identity, files, _, catalog, roles = inputs
    changed = copy.deepcopy(files)
    changed[capture.MIGRATION_PATH]["sha256"] = "f" * 64
    changed_raw = capture._canonical(changed)
    identity = {**identity, "source_files_sha256": capture._sha(changed_raw)}
    with pytest.raises(capture.FittedMeasurementRefusal, match="archive_inventory"):
        capture.build_measurement(
            identity=identity,
            source_files=changed,
            source_files_raw=changed_raw,
            catalog=catalog,
            **roles,
            cluster_system_identifier="12345678901234567890",
            database_name="ctcc_g3fcat_123456789abc",
            database_oid=12990,
        )
    raw = _build(inputs)
    changed_identity = {**inputs[0], "commit": "e" * 40}
    with pytest.raises(capture.FittedMeasurementRefusal, match="identity"):
        capture.verify_measurement(raw, identity=changed_identity, source_files=files)


def test_changed_receipt_and_non_numeric_cluster_id_refused(monkeypatch):
    inputs = _inputs(monkeypatch)
    raw = _build(inputs)
    identity, files, raw_map, catalog, roles = inputs
    with pytest.raises(capture.FittedMeasurementRefusal, match="encoding"):
        capture.verify_measurement(raw + b" ", identity=identity, source_files=files)
    with pytest.raises(capture.FittedMeasurementRefusal, match="database_identity"):
        capture.build_measurement(
            identity=identity,
            source_files=files,
            source_files_raw=raw_map,
            catalog=catalog,
            **roles,
            cluster_system_identifier="not-a-system-id",
            database_name="ctcc_g3fcat_123456789abc",
            database_oid=12990,
        )


@pytest.mark.parametrize(
    "field, changed",
    [
        ("case", ["tests.integration.other", "wrong_case"]),
        ("pg_server_build", "PostgreSQL 17.11 unverified build"),
    ],
)
def test_wrong_case_or_runtime_refused_even_when_artifact_rehashed(
    monkeypatch, field, changed
):
    identity, files, *_ = _inputs(monkeypatch)
    value = json.loads(_build(_inputs(monkeypatch)))
    value[field] = changed
    with pytest.raises(capture.FittedMeasurementRefusal, match="identity"):
        capture.verify_measurement(
            capture._canonical(value), identity=identity, source_files=files
        )


def test_two_separate_cluster_receipts_pair_without_startup_authority(monkeypatch):
    inputs = _inputs(monkeypatch)
    first = _build(inputs)
    identity, files, source_raw, catalog, roles = inputs
    second_identity = {**identity, "run_id": "c" * 12}
    second = capture.build_measurement(
        identity=second_identity,
        source_files=files,
        source_files_raw=source_raw,
        catalog=catalog,
        **roles,
        cluster_system_identifier="98765432109876543210",
        database_name="ctcc_g3fcat_abcdef123456",
        database_oid=12990,  # OIDs need not differ across separate clusters.
    )
    result = capture.compare_retained_measurements(
        first,
        second,
        first_identity=identity,
        first_source_files=files,
        first_expected_sha256=capture._sha(first),
        second_identity=second_identity,
        second_source_files=files,
        second_expected_sha256=capture._sha(second),
    )
    assert result["admission"] == "DENY"
    assert result["native_reference_sealed"] is False
    assert result["actual_measurement_independently_attested"] is False
    assert (
        result["normalized_catalog_sha256"]
        == capture.verify_measurement(first, identity=identity, source_files=files)[
            "normalized_catalog_sha256"
        ]
    )
    with pytest.raises(capture.FittedMeasurementRefusal, match="pair_mismatch"):
        capture.compare_retained_measurements(
            first,
            first,
            first_identity=identity,
            first_source_files=files,
            first_expected_sha256=capture._sha(first),
            second_identity=identity,
            second_source_files=files,
            second_expected_sha256=capture._sha(first),
        )
    changed = json.loads(second)
    changed["normalized_catalog"]["functions"][0]["source_sha256"] = "d" * 64
    changed["normalized_catalog_sha256"] = capture._sha(
        capture._canonical(changed["normalized_catalog"])
    )
    with pytest.raises(capture.FittedMeasurementRefusal, match="external_pin"):
        capture.compare_retained_measurements(
            first,
            capture._canonical(changed),
            first_identity=identity,
            first_source_files=files,
            first_expected_sha256=capture._sha(first),
            second_identity=second_identity,
            second_source_files=files,
            second_expected_sha256=capture._sha(second),
        )


@pytest.mark.parametrize(
    "case_name, skipped", [("wrong_case", False), (capture.CASE[1], True)]
)
def test_missing_or_skipped_capture_case_cannot_verify_junit(
    tmp_path, case_name, skipped
):
    path = tmp_path / "focused.xml"
    child = "<skipped/>" if skipped else ""
    path.write_text(
        '<testsuites><testsuite tests="1" failures="0" errors="0" '
        f'skipped="{int(skipped)}"><testcase classname="{capture.CASE[0]}" '
        f'name="{case_name}">{child}</testcase></testsuite></testsuites>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="pytest_required_execution_not_verified"):
        verify_pytest_report(path, required_cases=(capture.CASE,))
