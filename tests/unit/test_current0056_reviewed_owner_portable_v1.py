"""Check the retained real baseline and the scope of its portable comparison."""

from dataclasses import replace
from shutil import copy2, copytree

import pytest

from scripts import current0056_reviewed_owner_portable_v1 as portable
from scripts import historical0056_owner_source_on_0057_v1 as source57
from scripts import historical0056_owner_source_on_0058_v1 as historical
from scripts import manifest
from scripts.current0056_native_owner_pregrant_v1 import (
    NativeOwnerPregrant0056Diagnostic,
)

_INHERITED = "6243338abe9cfcbb4eb613bc3d21cd7adbaf8059644e8b7a1c2df9755ac340c7"
_NORMALIZED = "e3a07e5d1221d8677896479339a923d3189e84a065013f1e29a087fd8d4db9fa"
_REFERENCE = "57898f9a111d2bfeeeff7b1e01aa166e22b9166fd33b8e5717ea0d66a2f8bdf7"


@pytest.fixture(autouse=True)
def approved_source58(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    source = portable._SOURCE
    for name in (*historical._REVIEWED_SOURCE58_FILES, "alembic.ini"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        copy2(source / name, path)
    copytree(
        source / "migrations",
        root / "migrations",
        ignore=lambda _root, names: {"__pycache__"} & set(names),
    )
    copytree(source / portable._EVIDENCE, root / portable._EVIDENCE)
    manifest.write_manifest(root, root / "MANIFEST.sha256")
    monkeypatch.setattr(portable, "_SOURCE", root)
    monkeypatch.setattr(historical, "_SOURCE", root)
    monkeypatch.setattr(source57, "_SOURCE", root)


def _verify(target):
    return historical.verify_historical_owner56_reference_on_source58(
        portable._SOURCE, target
    )


def _target_observation():
    return NativeOwnerPregrant0056Diagnostic(
        inherited_native_definitions_sha256=_INHERITED,
        inherited_readwrite_native_definitions_sha256=_INHERITED,
        inherited_raw_owner_graph_sha256="1" * 64,
        cluster_system_identifier_sha256="2" * 64,
        fitted_raw_owner_catalog_sha256="3" * 64,
        fitted_normalized_owner_catalog_sha256=_NORMALIZED,
        exact_grant_plan_sha256="4" * 64,
    )


def test_portable_match_is_stable_across_target_specific_raw_observations():
    target = _target_observation()
    reference = _verify(target)
    assert (
        _verify(replace(target, fitted_raw_owner_catalog_sha256="5" * 64)) == reference
    )
    assert reference.reference_sha256 == _REFERENCE
    assert reference.inherited_native_definitions_sha256 == _INHERITED
    assert reference.fitted_normalized_owner_catalog_sha256 == _NORMALIZED
    assert reference.admission == "DENY"
    assert reference.startup_admission is reference.grant_authority is False
    assert reference.execution_authority is False


@pytest.mark.parametrize(
    "field",
    (
        "inherited_native_definitions_sha256",
        "inherited_readwrite_native_definitions_sha256",
        "fitted_normalized_owner_catalog_sha256",
    ),
)
def test_portable_drift_refuses(field):
    target = replace(_target_observation(), **{field: "f" * 64})
    with pytest.raises(historical.HistoricalOwnerSource0058Error):
        _verify(target)


def test_tampered_receipt_bytes_refuse(monkeypatch):
    original = portable._read

    def tampered(root, name, budget=1_048_576):
        raw = original(root, name, budget)
        return raw + b" " if name.endswith("cluster-1.inherited.json") else raw

    monkeypatch.setattr(portable, "_read", tampered)
    with pytest.raises(portable.ReviewedOwnerPortable0056Error):
        _verify(_target_observation())


def test_measured_source_drift_refuses_before_using_receipts(monkeypatch):
    original = portable._read
    reads = []

    def tampered(root, name, budget=1_048_576):
        reads.append(name)
        raw = original(root, name, budget)
        if name == "scripts/current0056_native_owner_pregrant_v1.py":
            return raw + b"\n"
        return raw

    monkeypatch.setattr(portable, "_read", tampered)
    with pytest.raises(historical.HistoricalOwnerSource0058Error):
        _verify(_target_observation())
    assert not any(name.endswith(".inherited.json") for name in reads)


def test_default0056_portable_loader_still_refuses_to_relabel_source58():
    with pytest.raises(portable.ReviewedOwnerPortable0056Error):
        portable.load_reviewed_owner_portable_0056_v1(portable._SOURCE)


@pytest.mark.parametrize(
    "field,value",
    [
        ("database_head", "0057"),
        ("grant_statements", 44),
        ("grants_present", True),
        ("execution_authority", True),
    ],
)
def test_historical_source58_reference_cannot_promote_physical57_or_permissions(
    field, value
):
    target = _target_observation()
    object.__setattr__(target, field, value)
    with pytest.raises(historical.HistoricalOwnerSource0058Error):
        _verify(target)


def test_source57_reader_stays_strict_on_actual_source58():
    with pytest.raises(source57.HistoricalOwnerSource0057Error):
        source57.require_historical_owner_source57(portable._SOURCE)
