"""Declared synthetic artifact-codec tests, never actual SQL/catalog proof."""

import copy
import json
from pathlib import Path

import pytest

from scripts import canonical0055_catalog_reference_v1 as reference
from scripts import current0058_collection_v1 as collection
from scripts import current0058_full_hermetic_contract_v1 as full
from scripts import native_owner_storage_diagnostic_v1 as diagnostic
from scripts import verify_final_hermetic as reports
from scripts import verify_windows_regression as windows


def _synthetic_documents():
    owner, database, oid = (
        "ctcc_ref55_owner_" + "a" * 16,
        "ctcc_ref55_database_" + "b" * 16,
        12345,
    )
    flags = [True, False, False, False, True, False, False]
    raw = {
        "schema": reference.SCHEMA,
        "head": ["0055"],
        "profile": {
            "owner": owner,
            "session_owner": owner,
            "database": database,
            "role": "none",
            "read_only": "on",
            "search_path": "pg_catalog",
            "timezone": "UTC",
            "tls": True,
            "datum_abi": "pg17.amd64.little.datum8.signedchar",
            "server_version": "170011",
        },
        "actor_physical": {
            "database_oid": 23456,
            "owner": [oid, owner, *flags],
            "membership_count": 0,
        },
        "entries": [
            ["relation", {"relname": name, "relkind": "r"}, {}]
            for name in ("alembic_version", "audit_logs")
        ]
        + [
            ["constraint", {"conname": "pk_audit_logs", "contype": "p"}, {}],
            [
                "index",
                {
                    "indpred": "declared synthetic partial predicate DATA",
                    "indexprs": None,
                },
                {},
            ],
        ],
    }
    native = {
        "schema": "ctcc.collector_current0055.actual_catalog.v1",
        "role": {
            "oid": oid,
            "name": owner,
            "current_user": owner,
            "session_user": owner,
            "setting": "none",
            "login": True,
            "superuser": True,
            "createdb": False,
            "createrole": False,
            "replication": False,
            "bypassrls": False,
            "membership": True,
            "create": True,
        },
    }
    receipt = {
        "schema": reference.RECEIPT_SCHEMA,
        "database_target": "0055",
        "canonical_naming_convention": True,
        "raw_sha256": reference.digest(reference.canonical(raw)),
        "native_owner_sha256": reference.digest(reference.canonical(native)),
        "full_typed_catalog_query_sha256": reference.digest(
            reference.catalog_query_v1().encode()
        ),
        "native_owner_query_sha256": "e" * 64,
        "native55_observation": {
            "schema": "ctcc.collector_current0055.owner_installation.v1",
            "database_head": "0055",
            "role_oid": oid,
            "native_definitions_sha256": reference.native_baseline_v1(),
            "native_baseline_admission": True,
            "execution_authority": False,
            "account_complete": False,
        },
        "public_table_names": ["alembic_version", "audit_logs"],
        "physical": {
            "database": database,
            "database_oid": 23456,
            "owner": owner,
            "owner_oid": oid,
            "owner_is_database_owner": True,
            "marker_relation_oid": 34567,
            "server_version_num": "170011",
            "actual_tls": True,
            "owner_flags": flags,
            "owner_memberships": 0,
            "cluster_system_identifier_sha256": "d" * 64,
        },
        **dict.fromkeys(
            (
                "schema_admission",
                "startup_admission",
                "paper_admission",
                "execution_authority",
                "saved_reference_is_authority",
            ),
            False,
        ),
    }
    source = {"commit": "1" * 40, "tree": "2" * 40, "manifest_sha256": "3" * 64}
    files = {
        reference._PRODUCER: {"sha256": "4" * 64},
        reference._NATIVE_MODULE: {
            "sha256": reference.digest(
                (reference.ROOT / reference._NATIVE_MODULE).read_bytes()
            )
        },
    }
    receipt.update(
        source_commit=source["commit"],
        source_tree=source["tree"],
        source_manifest_sha256=source["manifest_sha256"],
        producer_sha256=files[reference._PRODUCER]["sha256"],
        native_source_sha256=files[reference._NATIVE_MODULE]["sha256"],
    )
    return raw, native, receipt, source, files


def _publish(root, values):
    folder = root / "test-results"
    folder.mkdir()
    reference._write_files_v1(folder, tuple(values[:3]))


def test_real_filesystem_export_and_archived_readback_preserve_data_only(tmp_path):
    values = _synthetic_documents()
    _publish(tmp_path, values)
    result = reference.verify_reference_v1(tmp_path, values[3], values[4])
    assert result["admission"] == "DENY" and result["database_target"] == "0055"
    assert all((tmp_path / name).is_file() for name in reference.FILES)
    assert not (tmp_path / "test-results/test-results").exists()
    originals = [(tmp_path / name).read_bytes() for name in reference.FILES]
    with pytest.raises(FileExistsError):
        reference._write_files_v1(tmp_path / "test-results", tuple(values[:3]))
    assert originals == [(tmp_path / name).read_bytes() for name in reference.FILES]


@pytest.mark.parametrize("membership", [False, 0, 1, None, 0.0, "true"])
def test_native55_super_semantic_membership_is_exact_true_not_physical_count(
    tmp_path, membership
):
    raw, native, receipt, source, files = _synthetic_documents()
    native["role"]["membership"] = membership
    receipt["native_owner_sha256"] = reference.digest(reference.canonical(native))
    _publish(tmp_path, (raw, native, receipt))
    with pytest.raises(ValueError, match="canonical0055_reference_evidence_invalid"):
        reference.verify_reference_v1(tmp_path, source, files)


@pytest.mark.parametrize("count", [False, True, 1, None, 0.0, "0"])
@pytest.mark.parametrize("field", ["actor", "physical"])
def test_physical_membership_edge_counts_are_exact_integer_zero(tmp_path, count, field):
    raw, native, receipt, source, files = _synthetic_documents()
    if field == "actor":
        raw["actor_physical"]["membership_count"] = count
        receipt["raw_sha256"] = reference.digest(reference.canonical(raw))
    else:
        receipt["physical"]["owner_memberships"] = count
    _publish(tmp_path, (raw, native, receipt))
    with pytest.raises(ValueError, match="canonical0055_reference_evidence_invalid"):
        reference.verify_reference_v1(tmp_path, source, files)


def test_failed_codec_retains_separate_data_and_never_becomes_formal_reference(
    tmp_path, monkeypatch
):
    # Only the container artifact-mount/source-envelope seam is synthetic.
    # The actual case performs native55 admission before this same exporter.
    raw, native, receipt, source, files = _synthetic_documents()
    native["role"]["membership"] = 0
    receipt["native_owner_sha256"] = reference.digest(reference.canonical(native))
    folder = tmp_path / "test-results"
    folder.mkdir()
    monkeypatch.setattr(reference, "_record_context_v1", lambda value: (folder, value))
    with pytest.raises(ValueError, match="canonical0055_reference_evidence_invalid"):
        reference.record_if_requested_v1(raw, native, receipt)
    assert all(not (tmp_path / name).exists() for name in reference.FILES)
    captured = [
        json.loads((tmp_path / name).read_bytes()) for name in reference.FAILED_FILES
    ]
    assert captured[:2] == [raw, native]
    failed = captured[2]
    assert failed["schema"] == reference.FAILED_SCHEMA
    assert failed["status"] == "FAILED_CAPTURE_RAW"
    assert failed["accepted_reference"] is False and failed["diagnostic_only"] is True
    assert failed["source_commit"] == source["commit"]
    assert failed["physical"] == receipt["physical"]
    assert failed["native55_observation"] == receipt["native55_observation"]
    assert failed["producer_sha256"] == files[reference._PRODUCER]["sha256"]
    # Repair only this declared synthetic input's original bad field, so the
    # negative below specifically proves failure-purpose refusal, not another
    # membership rejection. No actual captured artifact is retagged/repaired.
    repaired_native = copy.deepcopy(native)
    repaired_native["role"]["membership"] = True
    repaired_receipt = dict(
        failed,
        native_owner_sha256=reference.digest(reference.canonical(repaired_native)),
    )
    reference._document(
        raw, repaired_native, dict(repaired_receipt, schema=reference.RECEIPT_SCHEMA)
    )
    for formal_name, value in zip(
        reference.FILES, (raw, repaired_native, repaired_receipt), strict=True
    ):
        (tmp_path / formal_name).write_bytes(reference.canonical(value))
    with pytest.raises(ValueError, match="canonical0055_reference_evidence_invalid"):
        reference.verify_reference_v1(tmp_path, source, files)


def test_successful_codec_never_writes_failed_capture_bundle(tmp_path, monkeypatch):
    raw, native, receipt, source, files = _synthetic_documents()
    folder = tmp_path / "test-results"
    folder.mkdir()
    monkeypatch.setattr(reference, "_record_context_v1", lambda value: (folder, value))
    reference.record_if_requested_v1(raw, native, receipt)
    assert all(not (tmp_path / name).exists() for name in reference.FAILED_FILES)
    assert reference.verify_reference_v1(tmp_path, source, files)["admission"] == "DENY"


@pytest.mark.parametrize(
    "changed",
    [
        "source17",
        "source58",
        "native_baseline",
        "bootstrap_oid",
        "owner_flags",
        "tls",
        "query",
        "naming",
        "paper",
        "saved_receipt",
        "source",
        "no_partial",
    ],
)
def test_rehashed_wrong_purpose_identity_source_or_authority_refuses(tmp_path, changed):
    raw, native, receipt, source, files = copy.deepcopy(_synthetic_documents())
    if changed == "source17":
        raw["schema"], raw["head"] = "ctcc.retained0017.raw_catalog.v1", ["0017"]
    elif changed == "source58":
        raw["head"] = ["0058"]
    elif changed == "native_baseline":
        receipt["native55_observation"]["native_definitions_sha256"] = "f" * 64
    elif changed == "bootstrap_oid":
        receipt["physical"]["owner_oid"] = 10
    elif changed == "owner_flags":
        raw["actor_physical"]["owner"][-1] = True
    elif changed == "tls":
        receipt["physical"]["actual_tls"] = False
    elif changed == "query":
        receipt["full_typed_catalog_query_sha256"] = "f" * 64
    elif changed == "naming":
        next(row[1] for row in raw["entries"] if row[0] == "constraint")["conname"] = (
            "audit_logs_pkey"
        )
    elif changed == "paper":
        receipt["paper_admission"] = True
    elif changed == "saved_receipt":
        receipt["saved_reference_is_authority"] = True
    elif changed == "no_partial":
        raw["entries"][-1][1]["indpred"] = None
    else:
        receipt["source_tree"] = "f" * 40
    receipt["raw_sha256"] = reference.digest(reference.canonical(raw))
    receipt["native_owner_sha256"] = reference.digest(reference.canonical(native))
    _publish(tmp_path, (raw, native, receipt))
    with pytest.raises(ValueError, match="canonical0055_reference_evidence_invalid"):
        reference.verify_reference_v1(tmp_path, source, files)


def test_canonical55_case_is_required_once_only_on_current58_owner3():
    assert reference.MODULE in collection.postgres_paths(3)
    assert Path(reference.MODULE).name in windows.POSTGRES_INTEGRATION
    assert full.REQUIRED_CASES.count(reference.CASE) == 1
    assert full.required_cases("postgres", 3).count(reference.CASE) == 1
    assert all(
        reference.CASE not in full.required_cases("postgres", index)
        for index in (0, 1, 2, 4)
    )
    assert len(diagnostic.CASES) == 2 and reference.CASE not in diagnostic.CASES


def test_missing_canonical55_case_cannot_pass_three_case_diagnostic(tmp_path):
    xml = tmp_path / "only-old-diagnostics.xml"
    xml.write_text(
        '<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0">'
        + "".join(
            '<testcase classname="' + module + '" name="' + name + '"/>'
            for module, name in diagnostic.CASES
        )
        + "</testsuite></testsuites>",
        encoding="ascii",
    )
    with pytest.raises(ValueError, match="pytest_required_execution_not_verified"):
        reports.verify_pytest_report(
            xml,
            required_cases=(*diagnostic.CASES, reference.CASE),
            allow_unrelated_skips=False,
        )


def test_capture_query_keeps_actual_head_and_complete_typed_data_without_source17_tag():
    query = reference.catalog_query_v1()
    assert "'schema','" + reference.SCHEMA + "'" in query
    assert "ctcc.retained0017.raw_catalog.v1" not in query
    assert "FROM public.alembic_version" in query
    assert "'0055'" not in query  # Head is always an actual SQL observation.
    assert all(
        "'" + key + "'" in query
        for key in (
            "dependencies",
            "shared_dependencies",
            "bootstrap",
            "actor_physical",
            "entries",
        )
    )
    assert not any(
        name in query
        for name in ("pg_get_expr(", "pg_get_functiondef(", "pg_get_constraintdef(")
    )


def test_source55_partial_wrapper_retains_all_other_source17_safety_bytes():
    from scripts import retained0017_catalog_baseline_v1 as source

    original = source.SAFE_CATALOG_SQL
    old = "(i.indexprs IS NOT NULL OR i.indpred IS NOT NULL)"
    new = "(i.indexprs IS NOT NULL)"
    clauses = reference.raw_predicates_v1()
    assert original.count(old) == 1 and clauses[0].count(new) == 1
    assert clauses[0].replace(new, old) == original == source.SAFE_CATALOG_SQL
    assert clauses[1:] == (
        "SELECT " + source._LANGUAGE_ADMISSION_SQL,
        "SELECT " + source._unknown_nonpublic_objects_predicate(),
    )


@pytest.mark.parametrize("duplicate", [False, True])
def test_changed_or_duplicated_source17_anchor_refuses_instead_of_broad_replacement(
    monkeypatch, duplicate
):
    from scripts import retained0017_catalog_baseline_v1 as source

    old = "(i.indexprs IS NOT NULL OR i.indpred IS NOT NULL)"
    changed = (
        source.SAFE_CATALOG_SQL + " AND " + old
        if duplicate
        else source.SAFE_CATALOG_SQL.replace(old, "(changed_source_clause)")
    )
    monkeypatch.setattr(source, "SAFE_CATALOG_SQL", changed)
    with pytest.raises(ValueError, match="canonical0055_reference_evidence_invalid"):
        reference.raw_predicates_v1()


def test_codec_requires_exact_canonical_json_bytes(tmp_path):
    values = _synthetic_documents()
    _publish(tmp_path, values)
    path = tmp_path / reference.FILES[0]
    path.write_text(json.dumps(values[0], indent=2), encoding="ascii")
    with pytest.raises(ValueError):
        reference.verify_reference_v1(tmp_path, values[3], values[4])
