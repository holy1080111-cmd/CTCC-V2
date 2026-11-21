"""Actual fresh source17 SERIAL AST bytes; no reference or upgrade authority."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import retained0017_catalog_baseline_v1 as subject

RAW = (
    Path(__file__).resolve().parents[1]
    / "fixtures/retained0017_pg17_native_serial_defaults_v1.json"
).read_bytes()
assert hashlib.sha256(RAW).hexdigest() == (
    "57e7ae0e30f9b4dc109c2136fc4e23b26ff722035630eaed93968e8ea47021fb"
)
RECEIPT = json.loads(RAW)
DOCUMENT = RECEIPT["cases"][0]["public_source17_serial_defaults"]
DEFAULTS = DOCUMENT["defaults"]


def _identity(row):
    dependency = row["sequence_dependencies"][0]
    return {
        "type": "sequence",
        "schema": dependency["sequence_namespace"],
        "name": dependency["sequence_name"],
        "identity": dependency["sequence_namespace"]
        + "."
        + dependency["sequence_name"],
    }


def _replay(row, **changes):
    kwargs = {
        "sequence_oid": row["sequence_dependencies"][0]["sequence_oid"],
        "sequence_identity": _identity(row),
        "column_type_oid": row["column_type_oid"],
    }
    kwargs.update(changes)
    return subject.normalize_node_tree17_v1(row["adbin"], **kwargs)


def test_actual_capture_is_public_fresh_diagnostic_with_no_reference_authority():
    assert RECEIPT["status"] == "PASS"
    assert RECEIPT["server_stop"] == "FAST_SHUTDOWN_0"
    assert RECEIPT["new_disposable_clusters_initialized"] == 1
    assert RECEIPT["ctcc_pg_private_data_or_configuration_reads"] == 0
    assert RECEIPT["venue_requests"] == RECEIPT["order_writes"] == 0
    assert DOCUMENT["schema_admission"] is DOCUMENT["account_complete"] is False
    assert DOCUMENT["execution_authority"] is False
    assert len(DEFAULTS) == 9
    assert sum(row["column_type_oid"] == 23 for row in DEFAULTS) == 8
    assert sum(row["column_type_oid"] == 20 for row in DEFAULTS) == 1
    assert [
        (
            row["oid"],
            row["name"],
            row["argument_type_oids"],
            row["result_type_oid"],
            row["source"],
        )
        for row in DOCUMENT["functions"]
    ] == [
        (480, "int4", [20], 23, "int84"),
        (1574, "nextval", [2205], 20, "nextval_oid"),
    ]
    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.require_sealed_catalog17_v1(DOCUMENT)


@pytest.mark.parametrize("row", DEFAULTS, ids=lambda row: row["relation"])
def test_actual_column_typed_serial_defaults_keep_the_original_cast_tree(row):
    before = subject.canonical(row)
    result = _replay(row)
    fields = dict(result[2])
    assert fields["funcresulttype"] == [row["column_type_oid"]]
    assert fields["funcid"] == [480 if row["column_type_oid"] == 23 else 1574]
    if row["column_type_oid"] == 23:
        child = fields["args"][0][1][0]
        assert dict(child[2])["funcid"] == [1574]
    assert "$SOURCE_SEQUENCE_REGCLASS" in str(result)
    assert subject.canonical(row) == before


@pytest.mark.parametrize(
    "fault",
    [
        "unknown_cast",
        "wrong_result",
        "wrong_column",
        "untyped_cast",
        "wrong_format",
        "retset",
        "variadic",
        "collation",
        "missing_field",
        "extra_argument",
        "wrong_nextval",
        "foreign_sequence",
        "missing_dependency",
        "missing_expression",
    ],
)
def test_serial_wrapper_never_accepts_an_unreviewed_call_or_column_binding(fault):
    row = copy.deepcopy(next(row for row in DEFAULTS if row["column_type_oid"] == 23))
    kwargs = {}
    if fault == "unknown_cast":
        row["adbin"] = row["adbin"].replace(":funcid 480", ":funcid 481", 1)
    elif fault == "wrong_result":
        row["adbin"] = row["adbin"].replace(
            ":funcresulttype 23", ":funcresulttype 20", 1
        )
    elif fault == "wrong_column":
        kwargs["column_type_oid"] = 20
    elif fault == "untyped_cast":
        kwargs["column_type_oid"] = None
    elif fault == "wrong_format":
        row["adbin"] = row["adbin"].replace(":funcformat 2", ":funcformat 0", 1)
    elif fault in ("retset", "variadic"):
        row["adbin"] = row["adbin"].replace(
            ":func" + fault + " false", ":func" + fault + " true", 1
        )
    elif fault == "collation":
        row["adbin"] = row["adbin"].replace(":inputcollid 0", ":inputcollid 100", 1)
    elif fault == "missing_field":
        row["adbin"] = row["adbin"].replace(" :funcvariadic false", "", 1)
    elif fault == "extra_argument":
        row["adbin"] = row["adbin"].replace(":args (", ":args (42 ", 1)
    elif fault == "wrong_nextval":
        row["adbin"] = row["adbin"].replace(":funcid 1574", ":funcid 1575", 1)
    elif fault == "foreign_sequence":
        kwargs["sequence_oid"] = row["sequence_dependencies"][0]["sequence_oid"] + 1
    elif fault == "missing_dependency":
        kwargs.update(sequence_oid=None, sequence_identity=None, column_type_oid=None)
    else:
        row["adbin"] = None
    with pytest.raises(subject.Retained0017CatalogRefused):
        _replay(row, **kwargs)


def test_bigserial_cannot_be_relabelled_as_integer_serial():
    row = next(row for row in DEFAULTS if row["column_type_oid"] == 20)
    with pytest.raises(subject.Retained0017CatalogRefused):
        _replay(row, column_type_oid=23)
