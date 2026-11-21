"""Synthetic PG17 expression bytes; no DB, schema seal or upgrade authority."""

import copy
import re

import pytest
from sqlalchemy import text

from scripts import retained0017_catalog_baseline_v1 as subject


def constant(value=1, *, oid=23, location=-1):
    raw = value.to_bytes(8, "little", signed=True)
    values = " ".join(str(v if v < 128 else v - 256) for v in raw)
    return (
        "{CONST :consttype " + str(oid) + " :consttypmod -1 :constcollid 0 :constlen 4"
        " :constbyval true :constisnull false :location "
        + str(location)
        + " :constvalue 4 [ "
        + values
        + " ]}"
    )


def sequence(oid, *, location=-1):
    return (
        "{FUNCEXPR :funcid 1574 :funcresulttype 20 :funcretset false :funcvariadic false"
        " :funcformat 0 :funccollid 0 :inputcollid 0 :args ("
        + constant(oid, oid=2205, location=location)
        + ") :location "
        + str(location)
        + "}"
    )


IDENTITY = {
    "type": "sequence",
    "schema": "public",
    "name": "declared_sequence",
    "identity": "public.declared_sequence",
}


def test_source_location_is_the_only_ordinary_expression_normalization():
    a = subject.normalize_node_tree17_v1(constant(1, location=5))
    assert a == subject.normalize_node_tree17_v1(constant(1, location=1001))
    assert a != subject.normalize_node_tree17_v1(constant(2, location=5))
    changed = constant(1).replace(":consttypmod -1", ":consttypmod 32")
    assert a != subject.normalize_node_tree17_v1(changed)
    assert subject.normalize_node_tree17_v1(None) is None


def test_exact_dependency_proven_sequence_allocations_normalize_without_changing_other_fields():
    a = subject.normalize_node_tree17_v1(
        sequence(16413), sequence_oid=16413, sequence_identity=IDENTITY
    )
    b = subject.normalize_node_tree17_v1(
        sequence(23887, location=30),
        sequence_oid=23887,
        sequence_identity=copy.deepcopy(IDENTITY),
    )
    assert a == b and "$SOURCE_SEQUENCE_REGCLASS" in str(a)
    other = copy.deepcopy(IDENTITY)
    other["identity"] = "public.another_sequence"
    assert a != subject.normalize_node_tree17_v1(
        sequence(16413), sequence_oid=16413, sequence_identity=other
    )


@pytest.mark.parametrize(
    "change",
    [
        "missing_dependency",
        "wrong_oid",
        "not_nextval",
        "not_byval",
        "wrong_word",
        "big_endian",
        "null",
        "multiple",
    ],
)
def test_regclass_normalization_refuses_guessed_unrelated_or_wrong_abi_operands(change):
    raw = sequence(16413)
    kwargs = {"sequence_oid": 16413, "sequence_identity": IDENTITY}
    if change == "missing_dependency":
        kwargs = {}
    elif change == "wrong_oid":
        kwargs["sequence_oid"] = 16414
    elif change == "not_nextval":
        raw = raw.replace(":funcid 1574", ":funcid 1575")
    elif change == "not_byval":
        raw = raw.replace(":constbyval true", ":constbyval false")
    elif change == "wrong_word":
        raw = raw.replace("29 64 0 0 0 0 0 0", "29 64 0 0")
    elif change == "big_endian":
        raw = raw.replace("29 64 0 0 0 0 0 0", "0 0 0 0 0 0 64 29")
    elif change == "null":
        raw = raw.replace(":constisnull false", ":constisnull true")
    elif change == "multiple":
        raw = raw.replace(
            ") :location", " " + constant(16413, oid=2205) + ") :location"
        )
    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.normalize_node_tree17_v1(raw, **kwargs)


@pytest.mark.parametrize(
    "change",
    [
        "node",
        "field",
        "duplicate",
        "type",
        "type_abi",
        "collation",
        "function",
        "syntax",
        "trailing",
        "bytes",
        "too_deep",
    ],
)
def test_unknown_expression_shape_cannot_be_normalized_into_a_source_receipt(change):
    raw = constant(1)
    if change == "node":
        raw = raw.replace("CONST", "EXTENSIBLENODE")
    elif change == "field":
        raw = raw.replace(":location", ":unreviewed")
    elif change == "duplicate":
        raw = raw.replace(":location", ":constlen 4 :location")
    elif change == "type":
        raw = raw.replace(":consttype 23", ":consttype 999999")
    elif change == "type_abi":
        raw = raw.replace(":consttype 23", ":consttype 20")
    elif change == "collation":
        raw = raw.replace(":constcollid 0", ":constcollid 999999")
    elif change == "function":
        raw = "{FUNCEXPR :funcid 20000}"
    elif change == "syntax":
        raw = raw[:-1]
    elif change == "trailing":
        raw += " unexpected"
    elif change == "bytes":
        raw = raw.replace("[ 1 0", "[ 256 0")
    elif change == "too_deep":
        raw = "{BOOLEXPR :boolop and :args (" * 130 + raw + ") :location -1}" * 130
    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.normalize_node_tree17_v1(raw)


def test_foreign_objects_are_refused_without_conversion_or_callback():
    called = []

    class Foreign:
        def __str__(self):
            called.append(True)
            return constant()

        def __bytes__(self):
            called.append(True)
            return b"private"

    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.normalize_node_tree17_v1(Foreign())
    assert called == []


def test_source17_functions_are_inert_literals_and_all_queries_have_no_observed_deparser():
    bodies = subject._source_trigger_bodies()
    assert set(bodies) == subject._FUNCTIONS
    assert "qualification_ledger_immutable" in bodies["qualification_ledger_immutable"]
    assert not re.search(r"\bpg_get_\w+\s*\(", subject.CATALOG_SQL)
    assert re.findall(r"FROM\s+public\.([a-z_]+)", subject.CATALOG_SQL) == [
        "alembic_version"
    ]
    assert not text(subject.CATALOG_SQL)._bindparams
    pre = (
        subject.SAFE_CATALOG_SQL
        + " AND "
        + subject._source_functions_predicate()
        + " AND "
        + subject._LANGUAGE_ADMISSION_SQL
        + " AND "
        + subject._unknown_nonpublic_objects_predicate()
    )
    assert not text(pre)._bindparams
    assert "pg_catalog.pg_policy" in pre and "pg_catalog.pg_statistic_ext" in pre
    assert (
        "pg_catalog.pg_depend" in subject.CATALOG_SQL
        and "pg_catalog.pg_shdepend" in subject.CATALOG_SQL
    )


def test_pending_reference_never_replays_caller_document_or_enables_upgrade(
    monkeypatch,
):
    monkeypatch.setattr(
        subject, "REFERENCE_STATUS", "PENDING_TWO_INDEPENDENT_ACTUAL_PG17_REFERENCES"
    )
    monkeypatch.setattr(subject, "SEALED_CATALOG_SHA256", "")
    monkeypatch.setattr(subject, "SEALED_SOURCE17_PREFIX_SHA256", "")
    called = []
    monkeypatch.setattr(
        subject, "normalize_catalog17_v1", lambda document: called.append(True)
    )
    assert subject.SEALED_CATALOG_SHA256 == subject.SEALED_SOURCE17_PREFIX_SHA256 == ""
    assert subject.REFERENCE_STATUS == "PENDING_TWO_INDEPENDENT_ACTUAL_PG17_REFERENCES"
    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.require_sealed_catalog17_v1({"claimed_pass": True})
    assert called == []
