"""Actual immutable reference DATA; no actor, database, startup or SQL proof."""

import copy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from scripts import paper58_owner_preflight_v1 as preflight

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/paper58_fresh_preprovision_v1"
RAW_A_SHA = "14cb609d3ce8c3e776ad2cc1c4c1edc62b75aa198bff68bc4f64a268a648d68f"
RAW_B_SHA = "b9468e784b3cf6f3ecf5b1fd2e48c9540fecc9e9821d8c25f7e7fdb66e468fcc"


@pytest.fixture(scope="module")
def genuine_reference_a():
    raw = gzip.decompress((FIXTURE / "paper58-reference-a-raw.json.gz").read_bytes())
    assert hashlib.sha256(raw).hexdigest() == RAW_A_SHA
    return json.loads(raw)


@pytest.mark.parametrize("label,expected_raw_sha", [("a", RAW_A_SHA), ("b", RAW_B_SHA)])
def test_actual_official_pair_matches_only_preprovision_data_purpose(
    label, expected_raw_sha
):
    raw = gzip.decompress(
        (FIXTURE / f"paper58-reference-{label}-raw.json.gz").read_bytes()
    )
    assert hashlib.sha256(raw).hexdigest() == expected_raw_sha
    result = preflight.require_fresh_unpinned_preprovision_catalog_v1(json.loads(raw))
    assert result["preprovision_catalog_matches"] is True
    assert result["normalized_catalog_sha256"] == preflight.CATALOG_SHA256
    for key in (
        "bootstrap_owner_continuation_accepted",
        "postprovision_acl_overlay_verified",
        "schema_admission",
        "paper_actor_admission",
        "startup_admission",
        "execution_authority",
    ):
        assert result[key] is False


def test_bootstrap10_variant_is_denied_before_normalizer_without_edge_repair(
    genuine_reference_a, monkeypatch
):
    document = copy.deepcopy(genuine_reference_a)
    document["actor_physical"]["owner"][0] = 10
    document["actor_physical"]["owner"][2:] = [True] * 7
    document["shared_dependencies"] = []
    calls = []
    monkeypatch.setattr(
        preflight.normalizer,
        "normalize_catalog58_v1",
        lambda value: calls.append(value),
    )
    with pytest.raises(preflight.Paper58OwnerPreflightRefused):
        preflight.require_fresh_unpinned_preprovision_catalog_v1(document)
    assert calls == []
    assert document["actor_physical"]["owner"][0] == 10
    assert document["shared_dependencies"] == []


@pytest.mark.parametrize("index", range(7))
def test_each_reference_owner_flag_is_exact(genuine_reference_a, index):
    document = copy.deepcopy(genuine_reference_a)
    document["actor_physical"]["owner"][index + 2] = not preflight.OWNER_FLAGS[index]
    with pytest.raises(preflight.Paper58OwnerPreflightRefused):
        preflight.require_fresh_unpinned_preprovision_catalog_v1(document)


@pytest.mark.parametrize("count", [False, True, 1, -1, "0"])
def test_membership_is_exact_integer_zero(genuine_reference_a, count):
    document = copy.deepcopy(genuine_reference_a)
    document["actor_physical"]["membership_count"] = count
    with pytest.raises(preflight.Paper58OwnerPreflightRefused):
        preflight.require_fresh_unpinned_preprovision_catalog_v1(document)


@pytest.mark.parametrize(
    "mutation", ["source55", "server17_12", "function_body", "missing_shared_edge"]
)
def test_other_purposes_or_actual_semantic_drift_deny(genuine_reference_a, mutation):
    document = copy.deepcopy(genuine_reference_a)
    if mutation == "source55":
        document["schema"] = "ctcc.canonical0055.full_catalog_data.v1"
        document["head"] = ["0055"]
    elif mutation == "server17_12":
        document["profile"]["server_version"] = "170012"
    elif mutation == "function_body":
        callback_oids = {
            row["oid"] for row in document["bootstrap"]["expression_callbacks"]
        }
        row = next(
            row
            for kind, row, _ in document["entries"]
            if kind == "function" and row["oid"] not in callback_oids
        )
        row["prosrc"] += "\n-- source body changed\n"
    else:
        assert document["shared_dependencies"]
        document["shared_dependencies"].pop()
    with pytest.raises(ValueError):
        preflight.require_fresh_unpinned_preprovision_catalog_v1(document)


def test_foreign_mapping_is_refused_without_getter_or_equality_callbacks():
    calls = []

    class Foreign(dict):
        def get(self, *args):
            calls.append("get")
            return super().get(*args)

        def __eq__(self, other):
            calls.append("eq")
            return True

    with pytest.raises(ValueError):
        preflight.require_fresh_unpinned_preprovision_catalog_v1(Foreign())
    assert calls == []


def test_origin_is_exact_original_pair_and_keeps_authority_false():
    raw = (FIXTURE / "origin.json").read_bytes()
    assert (
        hashlib.sha256(raw).hexdigest()
        == preflight.ORIGIN["independent_readback_sha256"]
    )
    origin = json.loads(raw)
    assert origin["source_commit"] == preflight.ORIGIN["source_commit"]
    assert origin["source_tree"] == preflight.ORIGIN["source_tree"]
    assert origin["official_zip_sha256"] == preflight.ORIGIN["official_zip_sha256"]
    assert origin["literal_candidate_sha256"] == preflight.CATALOG_SHA256
    assert origin["original_archived_verifier"] == "PASS"
    assert origin["normalized_bytes_equal"] is True
    assert origin["paper_actor_admission"] is origin["startup_admission"] is False
