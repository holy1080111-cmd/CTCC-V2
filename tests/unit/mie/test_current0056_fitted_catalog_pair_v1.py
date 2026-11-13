"""Synthetic physical-shape and role-redaction controls only."""

from __future__ import annotations

import copy
import runpy
from pathlib import Path

import pytest

from scripts import current0056_fitted_catalog_pair_v1 as pair


def _source():
    return runpy.run_path(
        str(Path(__file__).with_name("test_current0056_fitted_delta_catalog_v1.py"))
    )


def _catalog_and_roles():
    source = _source()
    document = source["synthetic_catalog"]()
    document["server_build"] = pair.PG_BUILD
    return document, source["ROLES"]


def test_normalized_catalog_replays_strict_shape_without_raw_roles():
    catalog, roles = _catalog_and_roles()
    normalized = pair.normalized_catalog_document(catalog, **roles)
    raw = pair._canonical(normalized)
    assert b"ctcc_" not in raw
    assert normalized["actor"] == normalized["login"] == "owner"
    assert pair.verify_normalized_catalog_document(normalized) == pair._sha(raw)


def test_pregrant_and_postgrant_fitted_catalog_are_distinct_profiles():
    catalog, roles = _catalog_and_roles()
    after = pair.normalized_catalog_document(catalog, **roles)
    with pytest.raises(ValueError, match="fitted_catalog_function_acl_changed"):
        pair.normalized_catalog_document(catalog, **(roles | {"fitted_roles": None}))
    catalog["function_grants"] = []
    before = pair.normalized_catalog_document(
        catalog, **(roles | {"fitted_roles": None})
    )
    assert pair.verify_normalized_catalog_document(before, pregrant=True) != (
        pair.verify_normalized_catalog_document(after)
    )
    with pytest.raises(pair.FittedPairRefusal):
        pair.verify_normalized_catalog_document(before)
    with pytest.raises(pair.FittedPairRefusal):
        pair.verify_normalized_catalog_document(after, pregrant=True)


def test_extra_catalog_role_is_hashed_in_retained_document():
    catalog, roles = _catalog_and_roles()
    catalog["roles"].append(
        ["ctcc_other_fixture", True, False, False, False, False, False]
    )
    catalog["role_create"].append(["ctcc_other_fixture", False, False])
    normalized = pair.normalized_catalog_document(catalog, **roles)
    assert b"ctcc_other_fixture" not in pair._canonical(normalized)
    assert b"ctcc_" not in pair._canonical(normalized)
    assert pair.verify_normalized_catalog_document(normalized) == pair._sha(
        pair._canonical(normalized)
    )


@pytest.mark.parametrize("field,value", [("rls", True), ("persistence", "u")])
def test_replayed_catalog_table_drift_refused(field, value):
    catalog, roles = _catalog_and_roles()
    normalized = pair.normalized_catalog_document(catalog, **roles)
    changed = copy.deepcopy(normalized)
    changed["tables"][0][field] = value
    with pytest.raises(pair.FittedPairRefusal, match="fitted_pair_normalized_invalid"):
        pair.verify_normalized_catalog_document(changed)


def test_unreviewed_pg_build_or_function_grant_refused():
    catalog, roles = _catalog_and_roles()
    catalog["server_build"] = "PostgreSQL 17.11 unreviewed"
    with pytest.raises(pair.FittedPairRefusal, match="fitted_pair_pg_runtime"):
        pair.normalized_catalog_document(catalog, **roles)
    catalog["server_build"] = pair.PG_BUILD
    catalog["function_grants"].append(
        ["public.gate3_fitted_binding_read(text)", "PUBLIC", "EXECUTE", False, "ctcc"]
    )
    with pytest.raises(ValueError, match="fitted_catalog_function_acl_changed"):
        pair.normalized_catalog_document(catalog, **roles)
