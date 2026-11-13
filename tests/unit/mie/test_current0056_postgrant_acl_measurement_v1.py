"""Pure negative controls for the inert DB0056 postgrant ACL receipt."""

from __future__ import annotations

import json

import pytest

from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from scripts import current0056_postgrant_acl_measurement_v1 as subject
from tests.unit.mie import test_current0056_fitted_catalog_measurement_v1 as samples


def _inputs(monkeypatch):
    identity, files, _, _, _ = samples._inputs(monkeypatch)
    files.update(
        {
            subject._ROLE_SOURCE: {"sha256": "b" * 64},
            subject._PRODUCER_SOURCE: {"sha256": "c" * 64},
        }
    )
    raw_files = subject._canonical(files)
    identity["source_files_sha256"] = subject._sha(raw_files)
    return identity, files, raw_files


def _observations(*, inherited="a" * 64, full_acl="d" * 64, restricted_acl="d" * 64):
    full = roles56.NativeRoles0056ObservationV1(
        inherited, "e" * 64, "f" * 64, "1" * 64, full_acl
    )
    restricted = roles56.RestrictedNativeRoles0056ObservationV1(
        inherited, "e" * 64, "f" * 64, restricted_acl
    )
    return full, restricted


def _build(inputs, *, cluster="2" * 64, full=None, restricted=None):
    identity, files, raw_files = inputs
    native, owner_free = _observations()
    return subject.build_measurement(
        identity=identity,
        source_files=files,
        source_files_raw=raw_files,
        cluster_system_identifier_sha256=cluster,
        full=native if full is None else full,
        restricted=owner_free if restricted is None else restricted,
    )


def test_postgrant_receipt_is_separate_redacted_and_denied(monkeypatch):
    inputs = _inputs(monkeypatch)
    raw = _build(inputs)
    identity, files, _ = inputs
    receipt = subject.verify_measurement(raw, identity=identity, source_files=files)
    assert receipt["schema"] == subject.SCHEMA
    assert receipt["full_granted_acl_shape_sha256"] == "d" * 64
    assert receipt["restricted_granted_acl_shape_sha256"] == "d" * 64
    assert receipt["source"]["role_observer_sha256"] == "b" * 64
    assert receipt["source"]["acl_measurement_producer_sha256"] == "c" * 64
    assert receipt["admission"] == "DENY"
    assert receipt["startup_admission"] is receipt["execution_authority"] is False
    assert receipt["actual_measurement_independently_attested"] is False
    assert b"writer_a" not in raw and b"custody_a" not in raw


def test_full_and_restricted_acl_drift_or_authority_is_refused(monkeypatch):
    inputs = _inputs(monkeypatch)
    full, restricted = _observations(restricted_acl="9" * 64)
    with pytest.raises(subject.PostgrantAclMeasurementRefusal, match="observations"):
        _build(inputs, full=full, restricted=restricted)
    full, restricted = _observations()
    object.__setattr__(full, "startup_admission", True)
    with pytest.raises(subject.PostgrantAclMeasurementRefusal, match="observations"):
        _build(inputs, full=full, restricted=restricted)


def test_rehashed_receipt_change_and_wrong_external_pin_are_refused(monkeypatch):
    inputs = _inputs(monkeypatch)
    raw = _build(inputs)
    identity, files, _ = inputs
    changed = json.loads(raw)
    changed["restricted_granted_acl_shape_sha256"] = "9" * 64
    with pytest.raises(subject.PostgrantAclMeasurementRefusal, match="identity"):
        subject.verify_measurement(
            subject._canonical(changed), identity=identity, source_files=files
        )
    with pytest.raises(subject.PostgrantAclMeasurementRefusal, match="external_pin"):
        subject.compare_retained_measurements(
            raw,
            raw,
            first_identity=identity,
            first_source_files=files,
            first_expected_sha256="0" * 64,
            second_identity=identity,
            second_source_files=files,
            second_expected_sha256=subject._sha(raw),
        )


def test_two_cluster_pair_is_inert_and_requires_distinct_clusters(monkeypatch):
    inputs = _inputs(monkeypatch)
    first = _build(inputs)
    identity, files, raw_files = inputs
    second_identity = {**identity, "run_id": "c" * 12}
    second = _build((second_identity, files, raw_files), cluster="3" * 64)
    pair = subject.compare_retained_measurements(
        first,
        second,
        first_identity=identity,
        first_source_files=files,
        first_expected_sha256=subject._sha(first),
        second_identity=second_identity,
        second_source_files=files,
        second_expected_sha256=subject._sha(second),
    )
    assert pair["granted_inherited_acl_shape_sha256"] == "d" * 64
    assert pair["admission"] == "DENY" and pair["startup_admission"] is False
    with pytest.raises(subject.PostgrantAclMeasurementRefusal, match="pair_mismatch"):
        subject.compare_retained_measurements(
            first,
            first,
            first_identity=identity,
            first_source_files=files,
            first_expected_sha256=subject._sha(first),
            second_identity=identity,
            second_source_files=files,
            second_expected_sha256=subject._sha(first),
        )
    changed = json.loads(second)
    changed["full_granted_acl_shape_sha256"] = "9" * 64
    changed["restricted_granted_acl_shape_sha256"] = "9" * 64
    changed_raw = subject._canonical(changed)
    with pytest.raises(subject.PostgrantAclMeasurementRefusal, match="pair_mismatch"):
        subject.compare_retained_measurements(
            first,
            changed_raw,
            first_identity=identity,
            first_source_files=files,
            first_expected_sha256=subject._sha(first),
            second_identity=second_identity,
            second_source_files=files,
            second_expected_sha256=subject._sha(changed_raw),
        )
