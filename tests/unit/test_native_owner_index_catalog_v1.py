"""Source artifact safety and fixed native-index admission contracts, no SQL."""

import hashlib
import json
import re
from pathlib import Path

import pytest

from scripts import native_owner_heap_am_v1 as heap
from scripts import native_owner_index_catalog_v1 as subject
from scripts import native_owner_retained_rows_v2 as rows
from scripts import native_owner_upgrade_0056_v1 as owner55
from scripts import retained0017_owner_plan_v1 as owner17


def test_source_facts_have_exact_primary_bootstrap_identity_and_no_observed_catalog():
    raw = (
        Path(subject.__file__)
        .with_name("native_owner_pg17_btree_facts_v1.json")
        .read_bytes()
    )
    assert hashlib.sha256(raw).hexdigest() == subject._FACTS_SHA256
    facts = json.loads(raw)
    assert facts["source_tag"] == "REL_17_11"
    assert set(facts["primary_sha256"]) == {
        "pg_amop",
        "pg_amproc",
        "pg_opclass",
        "pg_opfamily",
        "pg_operator",
        "pg_proc",
        "pg_type",
        "pg_type.h",
    }
    assert all(
        re.fullmatch("[0-9a-f]{64}", value)
        for value in facts["primary_sha256"].values()
    )
    assert len(facts["classes"]) == 17 and len(facts["amproc"]) == 77
    assert len(facts["amop"]) == 170 and len(facts["operators"]) == 205
    assert len(facts["procedures"]) == 380
    assert len(facts["types"]) == 22
    assert {47, 3805, 3806} <= {row[0] for row in facts["procedures"]}
    handlers = {row[0]: row for row in facts["procedures"] if row[0] in (3, 330)}
    assert handlers[3][1:5] == [
        "heap_tableam_handler",
        "2281",
        269,
        "heap_tableam_handler",
    ]
    assert handlers[330][1:5] == ["bthandler", "2281", 325, "bthandler"]
    assert next(row for row in facts["procedures"] if row[0] == 3214)[-1] == [
        "from_json",
        "field_name",
    ]


def test_all_version_and_shared_row_reads_bind_one_source_closed_index_predicate():
    expected = subject.NATIVE_BTREE_CATALOG_PREDICATE_V1
    for actual in (
        heap.NATIVE_MARKER_AND_HEAP_SQL_V1,
        str(owner55._SAFE_MARKER_V2),
        str(owner17.SAFE_ROW_TYPES_SQL),
        str(rows._LAYOUT_SQL),
    ):
        assert expected in actual
    for foreign_call in (
        "pg_get_expr",
        "pg_get_indexdef",
        "pg_get_functiondef",
        "pg_relation_size",
        "FROM public.",
    ):
        assert foreign_call not in expected
    assert not re.search(
        r"\b(?:bthandler|heap_tableam_handler|bttextcmp)\s*\(", expected
    )
    assert "i.indexprs IS NOT NULL" in expected
    assert "c.opcnamespace<>11" in expected and "c.opcfamily NOT IN" in expected
    assert (
        "p.prosrc" in expected and "o.oprrest" in expected and "o.oprjoin" in expected
    )
    assert "pg_catalog.pg_amproc" in expected and "pg_catalog.pg_amop" in expected
    assert (
        "pg_catalog.pg_depend" in expected and "pg_catalog.regexp_matches" in expected
    )
    assert "'FUNCEXPR'" not in expected
    from sqlalchemy import text

    assert not text(expected)._bindparams


@pytest.mark.parametrize("tamper", ["bytes", "link_count", "reparse"])
def test_source_fact_file_drift_or_external_alias_is_refused_before_parsing(
    monkeypatch, tamper
):
    called = []
    original = subject.os.read
    if tamper == "bytes":
        monkeypatch.setattr(subject.os, "read", lambda *args: original(*args) + b" ")
    else:
        from types import SimpleNamespace

        old = subject.Path.lstat

        def changed(path):
            actual = old(path)
            return SimpleNamespace(
                st_mode=actual.st_mode,
                st_nlink=2 if tamper == "link_count" else 1,
                st_file_attributes=0x400 if tamper == "reparse" else 0,
            )

        monkeypatch.setattr(subject.Path, "lstat", changed)
    monkeypatch.setattr(subject.json, "loads", lambda *args: called.append(True))
    with pytest.raises(ValueError, match="native_owner_btree_source_refused"):
        subject._source_facts()
    assert called == []
