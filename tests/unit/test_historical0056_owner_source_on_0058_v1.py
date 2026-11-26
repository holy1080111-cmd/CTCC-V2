"""Retained physical56 reference replay; no database, grant or startup action."""

from dataclasses import replace
from pathlib import Path

import pytest

from scripts import historical0056_owner_source_on_0058_v1 as subject

ROOT = Path(__file__).resolve().parents[2]


def test_source58_can_replay_original_two_physical56_references_only_as_inert_data():
    scope = subject.require_historical_owner_source58(ROOT)
    assert scope.actual_source_head == "0058" and scope.database_target == "0056"
    value = subject.load_historical_owner56_reference_on_source58(ROOT)
    assert len(value.reference_sha256) == 64
    assert (
        value.grant_authority
        is value.startup_admission
        is value.execution_authority
        is False
    )
    assert value.admission == "DENY"


def test_historical_owner_wrapper_rejects_changed_reviewed_observer(monkeypatch):
    files = dict(subject._REVIEWED_SOURCE58_FILES)
    files["scripts/current0056_fitted_catalog_measurement_v1.py"] = "0" * 64
    monkeypatch.setattr(subject, "_REVIEWED_SOURCE58_FILES", files)
    with pytest.raises(subject.HistoricalOwnerSource0058Error):
        subject.require_historical_owner_source58(ROOT)


def test_source58_owner_readers_require_a_complete_reviewed_pair():
    paths = subject._OWNER_READER_PATHS
    legacy = tuple(subject._REVIEWED_SOURCE58_FILES[path] for path in paths)
    revised = subject._REVIEWED_OWNER_READER_PAIR58_V2
    assert subject._requires_owner_reader_compatibility58(legacy) is False
    assert subject._requires_owner_reader_compatibility58(revised) is True
    for mismatched in (
        (legacy[0], revised[1]),
        (revised[0], legacy[1]),
        ("0" * 64, revised[1]),
    ):
        with pytest.raises(subject.HistoricalOwnerSource0058Error):
            subject._requires_owner_reader_compatibility58(mismatched)


def test_revised_source58_reader_pair_requires_its_exact_reviewed_measurement(
    monkeypatch,
):
    measurement_path, _ = subject._OWNER_READER_PAIR58_V2_MEASUREMENT
    original_read = subject.previous._read
    seen = []

    def tampered(root, name, budget=1_048_576):
        seen.append(name)
        raw = original_read(root, name, budget)
        return raw + b" " if name == measurement_path else raw

    monkeypatch.setattr(subject.previous, "_read", tampered)
    with pytest.raises(subject.HistoricalOwnerSource0058Error):
        subject.load_historical_owner56_reference_on_source58(ROOT)
    assert measurement_path in seen
    assert not any(name.endswith(".inherited.json") for name in seen)


def test_historical_owner_wrapper_rejects_relabelled_source_or_database(monkeypatch):
    scope = subject.require_historical_owner_source58(ROOT)
    for changed in (
        replace(scope, actual_source_head="0057"),
        replace(scope, database_target="0058"),
    ):
        monkeypatch.setattr(
            subject.scopes, "database0056_source_scope", lambda _, value=changed: value
        )
        with pytest.raises(subject.HistoricalOwnerSource0058Error):
            subject.require_historical_owner_source58(ROOT)
