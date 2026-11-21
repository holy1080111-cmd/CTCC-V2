"""Pair/writer DATA contract tests; synthetic values are not schema evidence."""

import copy

import pytest
from sqlalchemy import text

from scripts import paper58_catalog_reference_v1 as reference


def _summaries():
    source = {"source_head": "0058", "source58_scope_sha256": "1" * 64}
    values = [
        {
            "reference": label,
            "source": source,
            "database_head": "0058",
            "raw_catalog_sha256": str(index + 2) * 64,
            "normalized_catalog_sha256": "4" * 64,
            "source58_scope_sha256": "1" * 64,
            "physical": {
                "database": "synthetic_database_" + label,
                "database_oid": 20000 + index,
                "owner": "synthetic_owner_" + label,
                "owner_oid": 21000 + index,
                "marker_relation_oid": 22000 + index,
                "server_version_num": "170011",
                "cluster_system_identifier_sha256": "5" * 64,
            },
        }
        for index, label in enumerate(reference.LABELS)
    ]
    return source, values


def test_equal_complete_pair_is_still_data_without_runtime_or_actor_admission():
    source, values = _summaries()
    result = reference.verify_pair_values(values, source=source)
    assert result["schema"] == reference.PAIR_SCHEMA
    assert result["database_head"] == "0058"
    assert result["fresh_fixture_only"] is True
    assert result["schema_admission"] is result["paper_actor_admission"] is False
    assert result["startup_admission"] is result["execution_authority"] is False


@pytest.mark.parametrize(
    "field",
    ["database", "database_oid", "owner", "owner_oid", "marker_relation_oid"],
)
def test_each_physical_identity_must_be_independent(field):
    source, values = _summaries()
    values[1]["physical"][field] = values[0]["physical"][field]
    with pytest.raises(ValueError, match="paper58_structural_reference_refused"):
        reference.verify_pair_values(values, source=source)


@pytest.mark.parametrize(
    "field,value",
    [
        ("normalized_catalog_sha256", "6" * 64),
        ("source58_scope_sha256", "7" * 64),
        ("raw_catalog_sha256", "2" * 64),
        ("database_head", "0055"),
        ("reference", "a"),
    ],
)
def test_wrong_hash_head_or_label_cannot_form_a_pair(field, value):
    source, values = _summaries()
    values[1][field] = value
    with pytest.raises(ValueError, match="paper58_structural_reference_refused"):
        reference.verify_pair_values(values, source=source)


def test_missing_or_reversed_reference_never_passes():
    source, values = _summaries()
    for changed in ([], values[:1], list(reversed(values))):
        with pytest.raises(ValueError):
            reference.verify_pair_values(changed, source=source)


def test_new_query_changes_only_data_tag_and_keeps_original17_source_query():
    from scripts.retained0017_catalog_baseline_v1 import CATALOG_SQL

    previous = "'ctcc.retained0017.raw_catalog.v1'"
    actual = reference.catalog_query_v1()
    assert actual.replace("'" + reference.RAW_SCHEMA + "'", previous) == CATALOG_SQL
    assert not text(actual)._bindparams
    assert len(reference.TABLES) == 105


def test_exclusive_file_writer_preserves_first_bytes_on_late_rejection(tmp_path):
    value = {"schema": "clearly.synthetic.writer_data.v1", "authority": False}
    name = reference.paths("a")[0]
    reference._write(tmp_path, name, value)
    target = tmp_path / "paper58-reference-a-raw.json"
    first = target.read_bytes()
    changed = copy.deepcopy(value)
    changed["authority"] = True
    with pytest.raises(FileExistsError):
        reference._write(tmp_path, name, changed)
    assert target.read_bytes() == first == reference.canonical(value)
