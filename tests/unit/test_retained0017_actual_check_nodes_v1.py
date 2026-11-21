"""Replay actual fresh PG17 source-CHECK bytes; never a schema reference seal.

The retained controller framing failure remains explicit in the fixture. Its
public source-only output was independently recovered with exact SQL/stdout
hashes, known prefix and clean server stop, without a new server or private DB.
"""

import hashlib
import json
from pathlib import Path

import pytest

from scripts import retained0017_catalog_baseline_v1 as subject

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures/retained0017_pg17_native_checks_v1.json"
)
RAW = FIXTURE.read_bytes()
assert (
    hashlib.sha256(RAW).hexdigest()
    == "f6ec30b3bd83f386d5f10cc3055dc96ae9160ca9bdc064519e6584fa3787e703"
)
RECEIPT = json.loads(RAW)
CHECKS = RECEIPT["public_source17_checks"]


def _node_end(raw, start):
    depth = 0
    for offset in range(start, len(raw)):
        if raw[offset] == "{":
            depth += 1
        elif raw[offset] == "}":
            depth -= 1
            if depth == 0:
                return offset + 1
    raise AssertionError("actual captured subtree is incomplete")


def _actual_array_coerce():
    raw = next(row["conbin"] for row in CHECKS if "{ARRAYCOERCEEXPR " in row["conbin"])
    start = raw.index("{ARRAYCOERCEEXPR ")
    return raw[start : _node_end(raw, start)]


def test_public_native_capture_remains_diagnostic_and_not_a_schema_seal():
    assert len(CHECKS) == 66
    assert (
        RECEIPT["schema_admission"] is RECEIPT["source17_reference_acceptance"] is False
    )
    assert RECEIPT["execution_authority"] is False
    assert RECEIPT["retained_pg_input_reads"] == RECEIPT["new_server_starts"] == 0
    assert RECEIPT["original_controller_server_stop"] == "FAST_SHUTDOWN_0"
    assert "ARRAYCOERCEEXPR" in RECEIPT["native_tag_inventory"]
    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.require_sealed_catalog17_v1(RECEIPT)


@pytest.mark.parametrize(
    "row", CHECKS, ids=lambda row: row["relation"] + ":" + row["constraint"]
)
def test_every_actual_source17_check_tree_replays_without_executing_sql(row):
    before = row["conbin"]
    result = subject.normalize_node_tree17_v1(before)
    assert result[:1] == ["NODE"]
    assert row["conbin"] == before


def test_actual_array_coercion_uses_exact_official_pg17_fields():
    # REL_17_11 src/include/nodes/primnodes.h, ArrayCoerceExpr: 1165–1178.
    # https://raw.githubusercontent.com/postgres/postgres/REL_17_11/src/include/nodes/primnodes.h
    result = subject.normalize_node_tree17_v1(_actual_array_coerce())
    assert result[:2] == ["NODE", "ARRAYCOERCEEXPR"]
    assert [field for field, _value in result[2]] == [
        "arg",
        "elemexpr",
        "resulttype",
        "resulttypmod",
        "resultcollid",
        "coerceformat",
        "location",
    ]


@pytest.mark.parametrize(
    "fault", ("unknown_tag", "unknown_field", "missing_element", "foreign_type")
)
def test_array_coercion_cannot_admit_unreviewed_tag_field_type_or_missing_element(
    fault,
):
    raw = _actual_array_coerce()
    if fault == "unknown_tag":
        raw = raw.replace("ARRAYCOERCEEXPR", "UNREVIEWEDARRAYCOERCE", 1)
    elif fault == "unknown_field":
        raw = raw.replace("{ARRAYCOERCEEXPR ", "{ARRAYCOERCEEXPR :unreviewed 0 ", 1)
    elif fault == "missing_element":
        field = raw.index(":elemexpr ")
        start = raw.index("{", field)
        raw = raw[:field] + raw[_node_end(raw, start) :]
    else:
        index = raw.rfind(":resulttype ") + len(":resulttype ")
        end = raw.index(" ", index)
        raw = raw[:index] + "17000" + raw[end:]
    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.normalize_node_tree17_v1(raw)


def test_every_array_coercion_semantic_field_remains_part_of_the_comparison():
    raw = _actual_array_coerce()
    baseline = subject.normalize_node_tree17_v1(raw)
    # The genuine first captured node has no typmod. A changed coercion typmod
    # is semantic and must not be erased as an allocation/parser location.
    assert ":resulttypmod -1" in raw
    assert (
        subject.normalize_node_tree17_v1(
            raw.replace(":resulttypmod -1", ":resulttypmod 32", 1)
        )
        != baseline
    )
