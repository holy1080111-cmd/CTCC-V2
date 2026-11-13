"""Pure synthetic controls; no PostgreSQL, TLS or private material is opened."""

import copy
import xml.etree.ElementTree as ET
from hashlib import sha256

import pytest

from scripts import current0056_fitted_catalog_measurement_v1 as fitted
from scripts import current0056_hermetic_contract as current56
from scripts import current0056_postgrant_acl_measurement_v1 as postgrant_acl
from scripts import devval_campaign_physical_catalog_v1 as catalog
from tests.unit.mie import test_current0056_fitted_catalog_measurement_v1 as samples
from tests.unit.mie import test_current0056_postgrant_acl_measurement_v1 as acl_samples


def _document():
    bodies = catalog.expected_function_bodies()
    return {
        "schema": catalog.SCHEMA,
        "database": "ctcc_devval_campaign",
        "actor": "ctcc",
        "login": "ctcc",
        "version": "170011",
        "build": catalog.SERVER_BUILD,
        "tls": True,
        "durable": True,
        "has_alembic": False,
        "namespace": {"owner": "ctcc", "acl": []},
        "nonowner_acl": [list(row) for row in sorted(catalog.NONOWNER_ACL)],
        "classes": [
            {
                "name": name,
                "kind": "r" if name in catalog.TABLES else "i",
                "owner": "ctcc",
                "columns": [],
            }
            for name in sorted(catalog.TABLES | catalog.INDEXES)
        ],
        "functions": [
            {
                "name": name,
                "owner": "ctcc",
                "language": "plpgsql",
                "definer": True,
                "strict": False,
                "leakproof": False,
                "volatility": "v",
                "parallel": "u",
                "kind": "f",
                "support_zero": True,
                "config": ["search_path=pg_catalog, pg_temp"],
                "source_sha256": bodies[name],
            }
            for name in sorted(catalog.FUNCTIONS)
        ],
        "constraints": [
            {"name": name, "validated": True} for name in sorted(catalog.CONSTRAINTS)
        ],
        "indexes": [
            {"name": name, "valid": True, "ready": True}
            for name in sorted(catalog.INDEXES)
        ],
        "triggers": [
            {"name": name, "internal": False, "enabled": "O"}
            for name in sorted(catalog.USER_TRIGGERS)
        ]
        + [{"name": "internal", "internal": True, "enabled": "O"} for _ in range(8)],
        "types": [],
        "policies": [],
        "rules": [],
        "inheritance": [],
        "extensions": [],
        "event_triggers": [],
        "global_default_acl": [],
        "default_acl": [],
    }


def test_nine_function_bodies_come_from_both_exact_pinned_sql_files():
    bodies = catalog.expected_function_bodies()
    assert set(bodies) == catalog.FUNCTIONS
    assert len(set(bodies.values())) == 9
    assert catalog.EXPECTED_CATALOG_SHA256 == ""


def test_valid_synthetic_shape_remains_unsealed_then_test_pin_can_check_digest(
    monkeypatch,
):
    document = _document()
    with pytest.raises(catalog.CatalogRefusal, match="catalog_reference_unsealed"):
        catalog.validate_catalog_document(document)
    monkeypatch.setattr(
        catalog, "EXPECTED_CATALOG_SHA256", catalog.catalog_digest(document)
    )
    assert catalog.validate_catalog_document(document) == catalog.catalog_digest(
        document
    )


@pytest.mark.parametrize(
    "part,mutation",
    [
        ("classes", "missing"),
        ("functions", "body"),
        ("constraints", "unvalidated"),
        ("indexes", "invalid"),
        ("triggers", "extra"),
        ("classes", "rls"),
        ("policies", "extra"),
        ("event_triggers", "extra"),
        ("global_default_acl", "extra"),
        ("nonowner_acl", "public"),
        ("classes", "identity"),
    ],
)
def test_physical_drift_is_refused_before_reference_pin(monkeypatch, part, mutation):
    original = _document()
    monkeypatch.setattr(
        catalog, "EXPECTED_CATALOG_SHA256", catalog.catalog_digest(original)
    )
    document = copy.deepcopy(original)
    if mutation == "missing":
        document[part].pop()
    elif mutation == "rls":
        document[part][0]["rls"] = True
    elif mutation == "body":
        document[part][0]["source_sha256"] = "f" * 64
    elif mutation == "unvalidated":
        document[part][0]["validated"] = False
    elif mutation == "invalid":
        document[part][0]["ready"] = False
    elif mutation == "public":
        document[part].append(
            ["function", "_immutable", "PUBLIC", "EXECUTE", False, "ctcc"]
        )
    elif mutation == "identity":
        document[part][0]["columns"] = [
            ["id", "bigint", True, None, "a", "", "default", None]
        ]
    elif part == "triggers":
        document[part].append({"name": "unexpected", "internal": False, "enabled": "O"})
    else:
        document[part].append("unexpected")
    with pytest.raises(catalog.CatalogRefusal):
        catalog.validate_catalog_document(document)


def test_observer_refuses_before_any_connection_without_native_reference():
    class NoConnect:
        def connect(self):
            raise AssertionError("connection must not be opened")

    import asyncio

    with pytest.raises(catalog.CatalogRefusal, match="catalog_reference_unsealed"):
        asyncio.run(catalog.observe_catalog(NoConnect()))


def _linux_catalog_pair():
    document = _document()
    document["build"] = (
        "PostgreSQL 17.11 (Debian 17.11-1.pgdg13+1) on x86_64-pc-linux-gnu, "
        "compiled by gcc 14.2.0, 64-bit"
    )
    return {
        "schema": "ctcc.devval.hermetic_linux_catalog_pair.v1",
        "case": list(catalog.HERMETIC_CATALOG_CASE),
        "scope": "two_disposable_linux_tls_installations",
        "sql_sha256": dict(catalog.SQL_PINS),
        "server_build": document["build"],
        "catalogs": [
            {
                "document": copy.deepcopy(document),
                "catalog_sha256": catalog.catalog_digest(document),
                "native_document_sha256": str(index) * 64,
                "fixture_database_sha256": str(index + 2) * 64,
            }
            for index in (1, 2)
        ],
        "windows_reference_admitted": False,
        "execution_authority": False,
    }


@pytest.mark.parametrize(
    "mutation",
    ["missing_catalog", "same_install", "changed_hash", "repinned_body", "promoted"],
)
def test_hermetic_pair_checks_both_catalogs_without_admitting_windows(mutation):
    document = _linux_catalog_pair()
    catalog.verify_hermetic_linux_catalogs(catalog._canonical(document))
    assert catalog.EXPECTED_CATALOG_SHA256 == ""
    first, second = document["catalogs"]
    if mutation == "missing_catalog":
        document["catalogs"].pop()
    elif mutation == "same_install":
        second["fixture_database_sha256"] = first["fixture_database_sha256"]
    elif mutation == "changed_hash":
        second["catalog_sha256"] = "e" * 64
    elif mutation == "repinned_body":
        second["document"]["functions"][0]["source_sha256"] = "e" * 64
        second["catalog_sha256"] = catalog.catalog_digest(second["document"])
    else:
        document["windows_reference_admitted"] = True
    with pytest.raises(catalog.CatalogRefusal):
        catalog.verify_hermetic_linux_catalogs(catalog._canonical(document))


def test_current56_binds_linux_catalog_pair_to_the_exact_passing_junit(
    tmp_path, monkeypatch
):
    artifact = f"test-results/{catalog.HERMETIC_CATALOG_FILE}"
    assert artifact in current56.component_artifacts("postgres", 1)
    assert artifact in current56.component_artifacts("full", None)
    assert artifact not in current56.component_artifacts("postgres", 0)
    assert artifact not in current56.component_artifacts("shard", 1)
    inherited_artifact = f"test-results/{fitted.INHERITED_FILE}"
    assert inherited_artifact in current56.component_artifacts("postgres", 1)
    assert inherited_artifact in current56.component_artifacts("full", None)
    assert inherited_artifact not in current56.component_artifacts("postgres", 0)
    assert inherited_artifact not in current56.component_artifacts("shard", 1)
    postgrant_artifact = f"test-results/{postgrant_acl.FILE}"
    assert postgrant_artifact in current56.component_artifacts("postgres", 1)
    assert postgrant_artifact in current56.component_artifacts("full", None)
    assert postgrant_artifact not in current56.component_artifacts("postgres", 0)
    assert postgrant_artifact not in current56.component_artifacts("shard", 1)
    evidence = tmp_path / artifact
    evidence.parent.mkdir()
    evidence.write_bytes(catalog._canonical(_linux_catalog_pair()))
    identity, files, _, document, roles = samples._inputs(monkeypatch)
    files.update(
        {name: {"sha256": digest} for name, digest in catalog.SQL_PINS.items()}
    )
    files.update(
        {
            postgrant_acl._ROLE_SOURCE: {"sha256": "b" * 64},
            postgrant_acl._PRODUCER_SOURCE: {"sha256": "c" * 64},
        }
    )
    source_map = fitted._canonical(files)
    identity["source_files_sha256"] = sha256(source_map).hexdigest()
    fitted_evidence = evidence.with_name(fitted.FILE)
    fitted_raw = samples._build((identity, files, source_map, document, roles))
    fitted_evidence.write_bytes(fitted_raw)
    cluster = fitted.verify_measurement(
        fitted_raw, identity=identity, source_files=files
    )["cluster_system_identifier_sha256"]
    digests = {
        "owner_pregrant_inherited_sha256": "a" * 64,
        "owner_pregrant_readwrite_inherited_sha256": "a" * 64,
        "owner_pregrant_fitted_raw_sha256": "b" * 64,
        "owner_pregrant_fitted_normalized_sha256": "c" * 64,
        "postgrant_readonly_inherited_sha256": "a" * 64,
        "postgrant_readwrite_inherited_sha256": "a" * 64,
    }
    inherited_evidence = tmp_path / inherited_artifact
    inherited_evidence.write_bytes(
        fitted._canonical(fitted._inherited_receipt(identity, files, cluster, digests))
    )
    (tmp_path / postgrant_artifact).write_bytes(
        acl_samples._build((identity, files, source_map), cluster=cluster)
    )

    junit_path, _ = current56.component_report_paths("postgres", 1)
    junit = tmp_path / junit_path
    suites = ET.Element("testsuites")
    suite = ET.SubElement(
        suites, "testsuite", tests="3", failures="0", errors="0", skipped="0"
    )
    cases = [
        ET.SubElement(suite, "testcase", classname=module, name=name)
        for module, name in (
            catalog.HERMETIC_CATALOG_CASE,
            fitted.CASE,
            fitted.INHERITED_CASE,
        )
    ]
    report = ET.tostring(suites)
    junit.write_bytes(report)
    identity["tests"] = {"sha256": sha256(report).hexdigest()}
    current56._verify_devval_catalog_evidence(tmp_path, "postgres", 1, identity, files)
    inherited_evidence.write_bytes(
        fitted._canonical(fitted._inherited_receipt(identity, files, "f" * 64, digests))
    )
    with pytest.raises(ValueError, match="current56_fitted_inherited_cluster_mismatch"):
        current56._verify_devval_catalog_evidence(
            tmp_path, "postgres", 1, identity, files
        )
    inherited_evidence.write_bytes(
        fitted._canonical(fitted._inherited_receipt(identity, files, cluster, digests))
    )
    with pytest.raises(ValueError, match="source_or_test_changed"):
        current56._verify_devval_catalog_evidence(tmp_path, "postgres", 1, identity, {})
    for case in cases:
        name = case.get("name")
        case.set("name", "test_other_success")
        report = ET.tostring(suites)
        junit.write_bytes(report)
        identity["tests"]["sha256"] = sha256(report).hexdigest()
        with pytest.raises(ValueError, match="pytest_required_execution_not_verified"):
            current56._verify_devval_catalog_evidence(
                tmp_path, "postgres", 1, identity, files
            )
        case.set("name", name)
