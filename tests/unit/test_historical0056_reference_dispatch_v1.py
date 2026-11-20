"""Reviewed retained reference/code replay only; no SQL, grants or authority."""

from dataclasses import replace
from pathlib import Path

import pytest

from scripts import current0056_reviewed_owner_portable_v1 as old
from scripts import historical0056_owner_reference_dispatch_v1 as subject
from tests.historical0056_component_source_pins_v1 import (
    _SOURCE57,
    reviewed_component_source_pins_v1,
)

ROOT = Path(__file__).resolve().parents[2]


def test_source58_uses_reviewed_physical56_reference_and_old_loader_stays_strict():
    value = subject.load_reviewed_historical_owner56_reference_v1(ROOT)
    assert type(value) is old.ReviewedOwnerPortable0056V1
    assert (
        value.grant_authority
        is value.startup_admission
        is value.execution_authority
        is False
    )
    with pytest.raises(old.ReviewedOwnerPortable0056Error):
        old.load_reviewed_owner_portable_0056_v1(ROOT)


@pytest.mark.parametrize("head", ["0059", "0056"])
def test_source_scope_label_cannot_select_unknown_or_fake_legacy_source(
    monkeypatch, head
):
    scope = subject.scopes.database0056_source_scope(ROOT)
    monkeypatch.setattr(
        subject.scopes,
        "database0056_source_scope",
        lambda _: replace(scope, actual_source_head=head),
    )
    with pytest.raises(ValueError):
        subject.load_reviewed_historical_owner56_reference_v1(ROOT)


def test_current58_component_hashes_are_separate_from_caller_legacy_pins():
    legacy = dict.fromkeys(_SOURCE57, "0" * 64)
    expected = reviewed_component_source_pins_v1(ROOT, original_source56=legacy)
    assert expected != legacy and set(expected) == set(legacy)
    assert legacy == dict.fromkeys(_SOURCE57, "0" * 64)
    assert (
        expected["app/core/native_role_witness_0056_v1.py"]
        == _SOURCE57["app/core/native_role_witness_0056_v1.py"]
    )


def test_reference_dispatch_cannot_read_an_unselected_source(tmp_path):
    with pytest.raises(ValueError):
        subject.load_reviewed_historical_owner56_reference_v1(tmp_path)
