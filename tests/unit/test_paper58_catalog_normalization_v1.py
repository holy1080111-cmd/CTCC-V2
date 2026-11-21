"""New DATA-purpose checks on genuine unretagged55; no58 SQL acceptance."""

import copy
import json
from pathlib import Path

import pytest

from scripts import paper58_catalog_normalization_v1 as codec
from scripts import paper58_catalog_reference_v1 as reference
from scripts import paper58_source_shape_v1 as shape

ROOT = Path(__file__).resolve().parents[2]
PREFIX = ROOT / "tests/fixtures/paper58_source55_prefix_v1/canonical0055-raw.json"


def _prefix():
    return json.loads(PREFIX.read_bytes())


def test_genuine55_is_only_new_diagnostic_data_and_never_retagged_into58():
    value = _prefix()
    before = codec.canonical(value)
    result = codec._source55_diagnostic_v1(value)
    assert result["schema"] == "ctcc.paper58.source55_codec_diagnostic.v1"
    assert value["schema"] == "ctcc.canonical0055.full_catalog_data.v1"
    assert value["head"] == ["0055"] and codec.canonical(value) == before
    assert result["schema_admission"] is result["paper_actor_admission"] is False
    assert result["execution_authority"] is False
    assert sum(item[0] == "function" for item in result["entries"]) == 125
    with pytest.raises(codec.Paper58CatalogDataRefused):
        codec.normalize_catalog58_v1(value)
    value["schema"], value["head"] = reference.RAW_SCHEMA, ["0058"]
    # Changing labels cannot manufacture13 missing relations/36 functions.
    with pytest.raises(codec.Paper58CatalogDataRefused):
        codec.normalize_catalog58_v1(value)


def test_new_public_callback_reference_is_qualified_and_full_body_is_preserved():
    original = _prefix()
    public = {
        row["oid"]: row for kind, row, _ in original["entries"] if kind == "function"
    }
    callback = next(
        row
        for row in original["bootstrap"]["expression_callbacks"]
        if row["oid"] in public
    )
    name = callback["proname"]
    result = codec._source55_diagnostic_v1(original)
    entry = next(
        row
        for kind, _, row in result["entries"]
        if kind == "function" and row["proname"] == name
    )
    assert entry["prosrc"] == callback["prosrc"]
    changed = copy.deepcopy(original)
    for kind, row, _ in changed["entries"]:
        if kind == "function" and row["oid"] == callback["oid"]:
            row["prosrc"] += "\n-- changed source body"
    for row in changed["bootstrap"]["expression_callbacks"]:
        if row["oid"] == callback["oid"]:
            row["prosrc"] += "\n-- changed source body"
    assert codec._source55_diagnostic_v1(changed) != result


def test_public_callback_duplicate_row_disagreement_refuses():
    value = _prefix()
    public = {row["oid"] for kind, row, _ in value["entries"] if kind == "function"}
    callback = next(
        row
        for row in value["bootstrap"]["expression_callbacks"]
        if row["oid"] in public
    )
    callback["prosrc"] += "\n-- inconsistent duplicate"
    with pytest.raises(codec.Paper58CatalogDataRefused):
        codec._source55_diagnostic_v1(value)


@pytest.mark.parametrize("field", ["relacl", "condeferrable", "indisvalid", "prosrc"])
def test_semantic_metadata_changes_remain_in_complete_data_hash(field):
    value = _prefix()
    original = codec._source55_diagnostic_v1(value)
    kinds = {
        "relacl": "relation",
        "condeferrable": "constraint",
        "indisvalid": "index",
        "prosrc": "function",
    }
    row = next(
        row
        for kind, row, _ in value["entries"]
        if kind == kinds[field] and field in row
    )
    if field == "relacl":
        # Explicit owner-only ACL is distinct from the original NULL/default.
        row[field] = [value["profile"]["owner"] + "=r/" + value["profile"]["owner"]]
    elif type(row[field]) is bool:
        row[field] = not row[field]
    else:
        row[field] += "\n-- changed"
    try:
        changed = codec._source55_diagnostic_v1(value)
    except codec.Paper58CatalogDataRefused:
        return
    assert changed != original


def test_non_native_containers_are_rejected_before_copy_or_comparison_callbacks():
    calls = []

    class Foreign:
        def __deepcopy__(self, memo):
            calls.append("copy")
            return self

        def __eq__(self, other):
            calls.append("equal")
            return True

    value = _prefix()
    value["untrusted"] = Foreign()
    with pytest.raises(codec.Paper58CatalogDataRefused):
        codec._source55_diagnostic_v1(value)
    assert calls == []


def test_original17_abi_and_partial_index_policy_are_unchanged():
    ancestor = codec.ancestor
    assert 19 not in ancestor._CONST_ABI and 705 not in ancestor._CONST_ABI
    assert codec._CONST_ABI[19] == (64, False)
    assert codec._CONST_ABI[705] == (-2, False)
    with pytest.raises(ancestor.Retained0017CatalogRefused):
        ancestor.normalize_catalog17_v1(_prefix())


def test_closed_shape_is_full105_161_and_remains_data_only():
    value = shape.source_shape_v1()
    assert len(value["column_names"]) == 105
    assert len(value["functions"]) == 161
    assert "qualification_native_protective_flat_data_v58" in value["functions"]
    assert value["source55_prefix"]["prefix_function_count"] == 125
    assert (
        value["complete_schema_admission"] is value["paper_startup_admission"] is False
    )
    assert value["execution_authority"] is False
