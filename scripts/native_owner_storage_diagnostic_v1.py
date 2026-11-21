"""Fixed catalog-only diagnostic; no storage or startup admission API.

The original combined predicate remains authoritative. These source-named
pieces exist only to identify its first genuine native-schema over-refusal.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from scripts import native_owner_heap_am_v1 as heap
from scripts import native_owner_index_catalog_v1 as index
from scripts.evidence_json import read_evidence_json

CONTRACT = "ctcc.source58.native_storage_diagnostic.v2"
SCOPE = "two_fresh_native55_57_catalog_boolean_diagnostics_no_admission"
SUITE = "native-storage-diagnostic"
SCHEMA = "ctcc.source58.native_storage_diagnostic_identity.v2"
COMPONENT_SCHEMA = "ctcc.native_storage_catalog_components.v2"
ENV = "CTCC_DEVVAL_NATIVE_STORAGE_DIAGNOSTIC_DIR"
MODULE = "tests/integration/test_native_owner_storage_diagnostic_0058_pg.py"
REPORT = "test-results/native-storage-diagnostic.xml"
CASES = tuple(
    (
        "tests.integration.test_native_owner_storage_diagnostic_0058_pg",
        "test_actual" + head + "_native_storage_catalog_components_are_true",
    )
    for head in ("0055", "0057")
)
_HEAP_NAMES = (
    "default_table_access_method",
    "public_heap_metadata",
    "attached_toast_heap_metadata",
    "public_column_type_ids",
)
_INDEX_NAMES = (
    "event_triggers_absent",
    "btree_am_metadata",
    "btree_am_present",
    "expression_constant_type_ids",
    "native_type_io_metadata",
    "native_internal_procedure_metadata",
    "native_operator_metadata",
    "native_operator_family_metadata",
    "native_amproc_set",
    "native_amop_set",
    "public_and_toast_index_metadata_and_predicates",
    "default_collation_metadata",
)


def _need(value):
    if value is not True:
        raise ValueError("native_storage_diagnostic_refused") from None


def _scan(source, word="AND"):
    """Lex only source SQL; retain every byte and never inspect observed SQL."""
    _need(type(source) is str and len(source) <= 200000)
    _need(word in ("AND", "OR"))
    width = len(word)
    depth, quote, position = 0, None, 0
    cuts, outer_closes = [], []
    while position < len(source):
        char = source[position]
        if quote is not None:
            if char == quote:
                if position + 1 < len(source) and source[position + 1] == quote:
                    position += 2
                    continue
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif source[position : position + 2] in ("--", "/*", "*/"):
            _need(False)
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            _need(depth >= 0)
            if depth == 0:
                outer_closes.append(position)
        elif (
            depth == 0
            and source[position : position + width] == word
            and (
                position == 0
                or not (source[position - 1].isalnum() or source[position - 1] == "_")
            )
            and (
                position + width == len(source)
                or not (
                    source[position + width].isalnum()
                    or source[position + width] == "_"
                )
            )
        ):
            cuts.append(position)
            position += width
            continue
        position += 1
    _need(depth == 0 and quote is None)
    return cuts, outer_closes


def _pieces(source, word="AND"):
    cuts, _ = _scan(source, word)
    parts, start = [], 0
    for cut in cuts:
        parts.append(source[start:cut])
        start = cut + len(word)
    parts.append(source[start:])
    _need(all(part.strip() != "" for part in parts))
    # Reconstruction is exact, including spaces, quoted bytes and grouping.
    _need(word.join(parts) == source)
    return tuple(parts)


def queries_v1():
    """Return only the fixed original predicate and its source-owned pieces."""
    all_heap = _pieces(heap.ALL_PUBLIC_TABLES_HEAP_V1)
    _need(len(all_heap) == len(_HEAP_NAMES) + 1)
    btree = index.NATIVE_BTREE_CATALOG_PREDICATE_V1.strip()
    _, closes = _scan(btree)
    _need(btree[0] == "(" and btree[-1] == ")" and closes == [len(btree) - 1])
    index_parts = _pieces(btree[1:-1])
    _need(len(index_parts) == len(_INDEX_NAMES))
    _need(all_heap[-1].strip() == btree)
    combined = heap.NATIVE_MARKER_AND_HEAP_SQL_V1
    _need(combined.startswith("SELECT "))
    marker_parts = _pieces(combined.removeprefix("SELECT "))
    _need(len(marker_parts) == len(all_heap) + 1)
    _need(
        tuple(part.strip() for part in marker_parts[:-1])
        == tuple(part.strip() for part in all_heap)
    )
    named = (
        ("combined", combined),
        ("all_heap_and_indexes", "SELECT " + heap.ALL_PUBLIC_TABLES_HEAP_V1),
        ("btree_combined", "SELECT " + btree),
        *(
            (name, "SELECT " + part)
            for name, part in zip(_HEAP_NAMES, all_heap[:-1], strict=True)
        ),
        *(
            (name, "SELECT " + part)
            for name, part in zip(_INDEX_NAMES, index_parts, strict=True)
        ),
        ("marker_metadata", "SELECT " + marker_parts[-1]),
    )
    _need(len(named) == len({name for name, _ in named}))
    return named


def predicate_sha256_v1():
    return hashlib.sha256(
        heap.NATIVE_MARKER_AND_HEAP_SQL_V1.encode("ascii")
    ).hexdigest()


def _parenthesized(source, prefix="("):
    source = source.strip()
    _, closes = _scan(source)
    _need(
        source.startswith(prefix)
        and source.endswith(")")
        and closes == [len(source) - 1]
    )
    return source[len(prefix) : -1]


def detail_queries_v2():
    """Extract the original rejected CONST IDs and index OR categories.

    Only source-owned fragments are used. No observed identifier, type, node,
    function or SQL is accepted; production predicates remain unchanged.
    """
    groups = dict(queries_v1())
    constants = _parenthesized(
        groups["expression_constant_type_ids"].removeprefix("SELECT "), "NOT EXISTS("
    )
    anchor = "SELECT 1 FROM ("
    _need(constants.count(anchor) == 1)
    rejected_ids = (
        constants.replace(
            anchor, "SELECT DISTINCT m.parts[1]::pg_catalog.int8 FROM (", 1
        )
        + " ORDER BY 1"
    )

    original = _parenthesized(
        groups["public_and_toast_index_metadata_and_predicates"].removeprefix(
            "SELECT "
        ),
        "NOT EXISTS(",
    ).strip()
    anchor = " AND (\n ix.relkind"
    _need(original.count(anchor) == 1 and original.endswith(")"))
    prefix, suffix = original.split(anchor)
    rejected = "\n ix.relkind" + suffix[:-1]
    parts = _pieces(rejected, "OR")
    _need(len(parts) == 12)
    partial = _pieces(_parenthesized(parts[-1]), "AND")
    _need(len(partial) == 2 and partial[0].strip() == "i.indpred IS NOT NULL")
    predicates = _pieces(_parenthesized(partial[1]), "OR")
    _need(len(predicates) == 6)
    named = (
        ("index_layout", "OR".join(parts[:9])),
        ("index_key_operator_class_metadata", parts[9]),
        ("index_dependencies", parts[10]),
        *(
            (name, partial[0] + " AND (" + condition + ")")
            for name, condition in zip(
                (
                    "predicate_node_tags",
                    "predicate_type_ids",
                    "predicate_operator_ids",
                    "predicate_function_ids",
                    "predicate_collation_ids",
                    "predicate_hash_neg_outer_variables",
                ),
                predicates,
                strict=True,
            )
        ),
        (
            "predicate_rejected_arraycoerceexpr",
            partial[0]
            + " AND EXISTS("
            + _parenthesized(predicates[0], "EXISTS(")
            + " AND m.parts[1]='ARRAYCOERCEEXPR')",
        ),
    )
    # The same source FROM/JOIN/WHERE scope is retained for every category.
    categories = tuple(
        (name, "SELECT EXISTS(" + prefix + " AND (" + condition + "))")
        for name, condition in named
    )
    return rejected_ids, categories


def verify_reports_v1(root, source):
    expected_keys = {
        "schema",
        "target",
        "source_commit",
        "source_tree",
        "source_manifest_sha256",
        "predicate_sha256",
        "groups",
        "startup_admission",
        "execution_authority",
        "schema_admission",
        "original_combined_predicate_used",
        "component_conjunction_matches_original",
        "rejected_constant_type_ids",
        "index_rejection_categories",
        "detail_matches_original_groups",
    }
    names = tuple(name for name, _ in queries_v1())
    for target in ("0055", "0057"):
        path = Path(root) / "test-results" / ("native-storage-" + target + ".json")
        _need(path.is_file() and not path.is_symlink() and path.stat().st_size < 20000)
        report = read_evidence_json(path.read_bytes())
        _need(type(report) is dict and set(report) == expected_keys)
        _need(
            report["schema"] == COMPONENT_SCHEMA
            and report["target"] == target
            and report["source_commit"] == source["commit"]
            and report["source_tree"] == source["tree"]
            and report["source_manifest_sha256"] == source["manifest_sha256"]
            and report["predicate_sha256"] == predicate_sha256_v1()
            and report["original_combined_predicate_used"] is True
            and report["component_conjunction_matches_original"] is True
            and report["detail_matches_original_groups"] is True
            and report["rejected_constant_type_ids"] == []
            and type(report["index_rejection_categories"]) is dict
            and set(report["index_rejection_categories"])
            == {name for name, _ in detail_queries_v2()[1]}
            and all(
                value is False
                for value in report["index_rejection_categories"].values()
            )
            and report["startup_admission"] is False
            and report["execution_authority"] is False
            and report["schema_admission"] is False
            and type(report["groups"]) is dict
            and set(report["groups"]) == set(names)
            and all(value is True for value in report["groups"].values())
        )
    return {
        "targets": ["0055", "0057"],
        "all_original_predicates_true": True,
        "admission": "DENY",
    }
