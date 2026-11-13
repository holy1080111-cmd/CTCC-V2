"""Check the retained real baseline and the scope of its portable comparison."""

from dataclasses import replace

import pytest

from scripts import current0056_reviewed_owner_portable_v1 as portable
from scripts.current0056_native_owner_pregrant_v1 import (
    NativeOwnerPregrant0056Diagnostic,
)

_INHERITED = "6243338abe9cfcbb4eb613bc3d21cd7adbaf8059644e8b7a1c2df9755ac340c7"
_NORMALIZED = "e3a07e5d1221d8677896479339a923d3189e84a065013f1e29a087fd8d4db9fa"
_REFERENCE = "57898f9a111d2bfeeeff7b1e01aa166e22b9166fd33b8e5717ea0d66a2f8bdf7"


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
    reference = portable.verify_reviewed_owner_portable_0056_v1(
        portable._SOURCE, target
    )
    assert (
        portable.verify_reviewed_owner_portable_0056_v1(
            portable._SOURCE,
            replace(target, fitted_raw_owner_catalog_sha256="5" * 64),
        )
        == reference
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
    with pytest.raises(portable.ReviewedOwnerPortable0056Error):
        portable.verify_reviewed_owner_portable_0056_v1(portable._SOURCE, target)


def test_tampered_receipt_bytes_refuse(monkeypatch):
    original = portable._read

    def tampered(root, name, budget=1_048_576):
        raw = original(root, name, budget)
        return raw + b" " if name.endswith("cluster-1.inherited.json") else raw

    monkeypatch.setattr(portable, "_read", tampered)
    with pytest.raises(portable.ReviewedOwnerPortable0056Error):
        portable.verify_reviewed_owner_portable_0056_v1(
            portable._SOURCE, _target_observation()
        )


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
    with pytest.raises(portable.ReviewedOwnerPortable0056Error):
        portable.verify_reviewed_owner_portable_0056_v1(
            portable._SOURCE, _target_observation()
        )
    assert not any(name.endswith(".inherited.json") for name in reads)
