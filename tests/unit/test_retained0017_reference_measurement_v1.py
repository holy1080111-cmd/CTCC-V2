"""Declared synthetic artifact-codec tests, never actual catalog references."""

import copy
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import scripts
from scripts import current0058_full_hermetic_contract_v1 as full
from scripts import retained0017_reference_measurement_v1 as subject

SOURCE = {
    "source_head": "0058",
    "producer_sha256": "a" * 64,
    "comparator_sha256": "b" * 64,
}
PREFIX = "c" * 64


@pytest.fixture
def synthetic_pair(tmp_path, monkeypatch):
    # This deliberate pure comparator seam validates only artifact custody.
    # The separately required actual PG nodes use the genuine comparator.
    normal = {
        "schema": "ctcc.retained0017.normalized_catalog.v1",
        "source17_prefix_sha256": PREFIX,
        "entries": ["DECLARED_SYNTHETIC_CODEC_ONLY"],
    }
    comparator = types.ModuleType("scripts.retained0017_catalog_baseline_v1")
    comparator.normalize_catalog17_v1 = lambda document: copy.deepcopy(normal)
    comparator.source17_contract_v1 = lambda: {"source17_prefix_sha256": PREFIX}
    monkeypatch.setitem(sys.modules, comparator.__name__, comparator)
    monkeypatch.setattr(
        scripts, "retained0017_catalog_baseline_v1", comparator, raising=False
    )
    result = []
    for index, label in enumerate(subject.LABELS):
        owner, database = (
            "ctcc_ref17_owner_" + str(index),
            "ctcc_ref17_database_" + str(index),
        )
        physical = {
            "database": database,
            "database_oid": 17000 + index,
            "owner": owner,
            "owner_oid": 18000 + index,
            "owner_flags": copy.deepcopy(subject._FLAGS),
            "owner_memberships": 0,
            "marker_relation_oid": 19000 + index,
            "server_version_num": "170011",
            "cluster_system_identifier_sha256": "d" * 64,
            "actual_tls": True,
        }
        raw = {
            "schema": "ctcc.retained0017.raw_catalog.v1",
            "head": ["0017"],
            "profile": {
                "owner": owner,
                "session_owner": owner,
                "role": "none",
                "database": database,
                "read_only": "on",
                "search_path": "pg_catalog",
                "timezone": "UTC",
                "server_version": "170011",
                "tls": True,
            },
            "references": [
                ["pg_authid", physical["owner_oid"], {"identity": owner}],
                ["pg_database", physical["database_oid"], {"identity": database}],
            ],
            "actor_physical": {
                "database_oid": physical["database_oid"],
                "owner": [physical["owner_oid"], owner, *subject._FLAGS.values()],
                "membership_count": 0,
                "membership_sha256": "e" * 64,
                "maintenance_settings_sha256": "f" * 64,
            },
            "entries": [
                [
                    "relation",
                    {
                        "oid": physical["marker_relation_oid"],
                        "relname": "alembic_version",
                        "relowner": physical["owner_oid"],
                        "relkind": "r",
                        "relam": 2,
                    },
                    {},
                ],
                [
                    "database",
                    {
                        "oid": physical["database_oid"],
                        "datname": database,
                        "datdba": physical["owner_oid"],
                    },
                    {},
                ],
            ],
        }
        raw_bytes, normalized = subject.canonical(raw), subject.canonical(normal)
        value = {
            "schema": "ctcc.retained0017.reference_measurement.v1",
            "source": SOURCE,
            "reference": label,
            "database_head": "0017",
            "physical": physical,
            "source17_prefix_sha256": PREFIX,
            "raw_catalog_sha256": subject.digest(raw_bytes),
            "normalized_catalog_sha256": subject.digest(normalized),
            "catalog_scope": "fresh_source17_schema_only",
            "authentication_scope": "isolated_tls_fixture_no_password_auth_acceptance",
            "reference_seal_installed": False,
            "schema_upgrade_authorized": False,
            "startup_admission": False,
            "execution_authority": False,
        }
        result.append((raw, normal, value))
        for name, data in zip(
            subject.paths(label),
            (raw_bytes, normalized, subject.canonical(value)),
            strict=True,
        ):
            path = tmp_path / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)
    return tmp_path, result


def test_complete_independent_pair_remains_data_and_never_installs_a_reference(
    synthetic_pair,
):
    root, values = synthetic_pair
    pair = subject.verify_reference_pair(root, source=SOURCE)
    assert (
        pair["normalized_catalog_sha256"] == values[0][2]["normalized_catalog_sha256"]
    )
    assert len(pair["references"]) == 2
    assert pair["database_head"] == "0017" and pair["source"]["source_head"] == "0058"
    assert all(
        pair[key] is False
        for key in (
            "reference_seal_installed",
            "schema_upgrade_authorized",
            "startup_admission",
            "execution_authority",
        )
    )


def test_real_bind_mount_writer_exports_all_six_files_for_original_pair_reader(
    synthetic_pair, tmp_path, monkeypatch
):
    _old_root, values = synthetic_pair
    artifact_root = tmp_path / "new-export"
    mounted_root = artifact_root / "test-results"
    mounted_root.mkdir(parents=True)
    original_writer = subject._write_reference_files
    original_read = Path.read_bytes
    observed_roots = []

    def mounted_writer(root, label, payloads):
        observed_roots.append(root)
        assert root == Path("/validation-results")
        original_writer(mounted_root, label, payloads)

    def declared_source_inputs(path):
        if path.as_posix() in ("/ctcc-run-identity.json", "/ctcc-source-files.json"):
            return b"{}"
        return original_read(path)

    # Only the existing explicitly synthetic codec source/comparator inputs
    # and container mount mapping are inert seams; all six writes, fsyncs,
    # independent byte readbacks and the original pair reader use real files.
    monkeypatch.setenv(subject.ENV, "/validation-results")
    monkeypatch.setattr(subject, "source_identity", lambda *_args: SOURCE)
    monkeypatch.setattr(subject, "_write_reference_files", mounted_writer)
    monkeypatch.setattr(Path, "read_bytes", declared_source_inputs)
    for label, (raw, _normal, receipt) in zip(subject.LABELS, values, strict=True):
        subject.record_reference_if_requested(
            label=label, document=raw, physical=receipt["physical"]
        )
    assert observed_roots == [Path("/validation-results")] * 2
    assert not (mounted_root / "test-results").exists()
    assert {path.name for path in mounted_root.iterdir()} == {
        Path(name).name for name in subject.REPORTS
    }
    pair = subject.verify_reference_pair(artifact_root, source=SOURCE)
    assert len(pair["references"]) == 2 and pair["reference_seal_installed"] is False


def test_bind_mount_writer_never_clobbers_an_existing_leaf_or_accepts_unknown_label(
    synthetic_pair, tmp_path
):
    _old_root, values = synthetic_pair
    root = tmp_path / "new-mounted-root"
    root.mkdir()
    raw, normal, receipt = values[0]
    payloads = tuple(subject.canonical(value) for value in (raw, normal, receipt))
    subject._write_reference_files(root, subject.LABELS[0], payloads)
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    with pytest.raises(FileExistsError):
        subject._write_reference_files(root, subject.LABELS[0], payloads)
    with pytest.raises(ValueError, match="retained0017_reference_evidence_invalid"):
        subject._write_reference_files(root, "../unreviewed", payloads)
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before


@pytest.mark.parametrize("missing", subject.REPORTS)
def test_pair_gate_refuses_each_missing_raw_normalized_or_receipt_artifact(
    synthetic_pair, missing
):
    root, _ = synthetic_pair
    (root / missing).unlink()
    with pytest.raises(ValueError, match="retained0017_reference_evidence_invalid"):
        subject.verify_reference_pair(root, source=SOURCE)


@pytest.mark.parametrize(
    "field",
    (
        "normalized_catalog_sha256",
        "source17_prefix_sha256",
        "source",
        "owner",
        "owner_oid",
        "database",
        "database_oid",
        "marker_relation_oid",
        "raw_catalog_sha256",
    ),
)
def test_pair_gate_refuses_mixed_schema_prefix_source_or_reused_allocation(
    synthetic_pair, field
):
    root, _ = synthetic_pair
    values = subject.verify_reference_pair(root, source=SOURCE)["references"]
    changed = copy.deepcopy(values)
    if field in {
        "owner",
        "owner_oid",
        "database",
        "database_oid",
        "marker_relation_oid",
    }:
        changed[1]["physical"][field] = changed[0]["physical"][field]
    elif field == "raw_catalog_sha256":
        changed[1][field] = changed[0][field]
    elif field == "source":
        changed[1][field]["comparator_sha256"] = "f" * 64
    else:
        changed[1][field] = "f" * 64
    with pytest.raises(ValueError, match="retained0017_reference_evidence_invalid"):
        subject.verify_pair_values(changed, source=SOURCE)


@pytest.mark.parametrize(
    "change",
    (
        "wrong_marker_oid",
        "not_readonly",
        "superuser",
        "membership",
        "missing_tls",
        "sealed",
        "upgrade_authority",
        "rehashed_raw_owner",
        "foreign_database_owner",
        "foreign_marker_owner",
        "foreign_raw_actor_database",
        "foreign_raw_actor_owner",
        "raw_actor_superuser",
        "raw_actor_membership",
    ),
)
def test_rehashed_receipt_cannot_hide_physical_or_authority_mismatch(
    synthetic_pair, change
):
    _, values = synthetic_pair
    raw, normal, value = copy.deepcopy(values[0])
    if change == "wrong_marker_oid":
        value["physical"]["marker_relation_oid"] += 5
    elif change == "not_readonly":
        raw["profile"]["read_only"] = "off"
    elif change == "superuser":
        value["physical"]["owner_flags"]["rolsuper"] = True
    elif change == "membership":
        value["physical"]["owner_memberships"] = 1
    elif change == "missing_tls":
        value["physical"]["actual_tls"] = False
    elif change == "sealed":
        value["reference_seal_installed"] = True
    elif change == "upgrade_authority":
        value["schema_upgrade_authorized"] = True
    elif change == "foreign_database_owner":
        raw["entries"][1][1]["datdba"] += 9
    elif change == "foreign_marker_owner":
        raw["entries"][0][1]["relowner"] += 9
    elif change == "foreign_raw_actor_database":
        raw["actor_physical"]["database_oid"] += 9
    elif change == "foreign_raw_actor_owner":
        raw["actor_physical"]["owner"][0] += 9
    elif change == "raw_actor_superuser":
        raw["actor_physical"]["owner"][2] = True
    elif change == "raw_actor_membership":
        raw["actor_physical"]["membership_count"] = 1
    else:
        raw["profile"]["owner"] = "ctcc_ref17_foreign_owner"
    raw_bytes = subject.canonical(raw)
    value["raw_catalog_sha256"] = subject.digest(raw_bytes)
    with pytest.raises(ValueError, match="retained0017_reference_evidence_invalid"):
        subject.verify_measurement(
            raw_bytes,
            subject.canonical(normal),
            subject.canonical(value),
            source=SOURCE,
            label=subject.LABELS[0],
        )


def test_both_reference_nodes_and_six_artifacts_belong_only_to_current58_owner3():
    from scripts import verify_windows_regression as windows

    assert len(subject.CASES) == len(set(subject.CASES)) == 2
    for case in subject.CASES:
        assert full.REQUIRED_CASES.count(case) == 1
        node = case[0].replace(".", "/") + ".py::" + case[1]
        assert [
            index
            for index in range(5)
            if full.collection.selected_for("postgres", index, node)
        ] == [3]
        assert not any(
            full.collection.selected_for("shard", index, node) for index in range(8)
        )
        assert case not in full.base.REQUIRED_CASES
    assert (
        "test_retained0017_catalog_references_v1_pg.py" in windows.POSTGRES_INTEGRATION
    )
    for mode, index in (
        ("full", None),
        *(("postgres", n) for n in range(5)),
        *(("shard", n) for n in range(8)),
    ):
        expected = mode == "full" or mode == "postgres" and index == 3
        assert full.requires_reference_pair17(mode, index) is expected
        assert (
            set(subject.REPORTS).issubset(full.component_artifacts(mode, index))
            is expected
        )
        assert (
            subject.ENV + "=/validation-results"
            in full.measurement_environment(mode, index)
        ) is expected
    assert Path(subject._COMPARATOR).name in full.HOST_HELPERS
    assert Path(subject._PRODUCER).name in full.HOST_HELPERS


def test_actual_component3_post_test_gate_propagates_pair_refusal(
    tmp_path, monkeypatch
):
    called = []
    monkeypatch.setattr(subject, "source_identity", lambda *_args: SOURCE)

    def refuse(root, *, source):
        called.append((root, source))
        raise ValueError("declared_synthetic_reference_pair_refusal")

    monkeypatch.setattr(subject, "verify_reference_pair", refuse)
    with pytest.raises(ValueError, match="declared_synthetic_reference_pair_refusal"):
        full.extra_observations(
            tmp_path, {"suite": "postgres", "postgres_component_index": 3}, {}
        )
    assert called == [(tmp_path, SOURCE)]
    assert full.extra_observations(
        tmp_path, {"suite": "postgres", "postgres_component_index": 4}, {}
    ) == {
        "historical0057": None,
        "actual0058_g12_custody": None,
        "retained0017_reference_pair": None,
        "paper58_reference_pair": None,
    }


@pytest.mark.parametrize("missing", subject.CASES)
def test_each_independent_reference_requires_actual_passed_junit_node(
    tmp_path, missing
):
    root = ET.Element("testsuites")
    cases = [case for case in subject.CASES if case != missing]
    suite = ET.SubElement(
        root, "testsuite", tests=str(len(cases)), failures="0", errors="0", skipped="0"
    )
    for module, name in cases:
        ET.SubElement(suite, "testcase", classname=module, name=name)
    path = tmp_path / "declared-synthetic-reference-codec.xml"
    path.write_bytes(ET.tostring(root))
    with pytest.raises(ValueError, match="pytest_required_execution_not_verified"):
        full.base.shared.verify_pytest_report(path, required_cases=subject.CASES)
