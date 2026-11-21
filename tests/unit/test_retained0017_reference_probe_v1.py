"""Declared synthetic source/probe codecs only; never PostgreSQL execution."""

import copy
from pathlib import Path

import pytest
import yaml

from scripts import retained0017_reference_measurement_v1 as measurement
from scripts import run_retained0017_reference_probe_v1 as probe

ROOT = Path(__file__).resolve().parents[2]
BRANCH = "work/db0058-retained0017-reference-probe"


@pytest.fixture
def synthetic_probe_identity(monkeypatch):
    files = {
        "MANIFEST.sha256": {"sha256": "1" * 64},
        measurement.PROBE_RUNNER: {"sha256": "2" * 64},
        measurement._PRODUCER: {"sha256": "3" * 64},
        measurement._COMPARATOR: {"sha256": "4" * 64},
    }
    raw = measurement.canonical(files)
    identity = {
        "runner_contract": measurement.PROBE_CONTRACT,
        "validation_scope": measurement.PROBE_SCOPE,
        "suite": "retained0017-reference-probe",
        "source_contract_head": "0058",
        "build_context": "admitted_archive_bytes_v1",
        "hermetic_regression": "NOT_RUN",
        "full_suite_acceptance": False,
        "host_credentials": False,
        "host_env": False,
        "external_order_writes": 0,
        "trading_acceptance": False,
        "commit": "a" * 40,
        "tree": "b" * 40,
        "archive_sha256": "c" * 64,
        "source_files_sha256": measurement.digest(raw),
        "manifest_sha256": "1" * 64,
        "runner_sha256": "2" * 64,
    }
    seen = []
    monkeypatch.setattr(
        measurement.source_contract,
        "migration_digest",
        lambda value: seen.append(value),
    )
    return identity, files, raw, seen


def test_probe_source_identity_is_distinct_data_and_checks_exact_migration_inventory(
    synthetic_probe_identity,
):
    identity, files, raw, seen = synthetic_probe_identity
    source = measurement.source_identity(identity, files, raw)
    assert source["diagnostic_scope"] == measurement.PROBE_SCOPE
    assert source["source_head"] == "0058" and seen == [files]
    assert source["runner_sha256"] == files[measurement.PROBE_RUNNER]["sha256"]
    assert source["producer_sha256"] == files[measurement._PRODUCER]["sha256"]
    assert "postgres_component_index" not in identity
    assert (
        identity["full_suite_acceptance"] is False
        and identity["hermetic_regression"] == "NOT_RUN"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("validation_scope", "full_regression"),
        ("suite", "postgres"),
        ("source_contract_head", "0057"),
        ("hermetic_regression", "PASS"),
        ("full_suite_acceptance", True),
        ("host_credentials", True),
        ("host_env", True),
        ("external_order_writes", False),
        ("external_order_writes", 1),
        ("trading_acceptance", True),
        ("runner_sha256", "f" * 64),
        ("source_files_sha256", "f" * 64),
        ("manifest_sha256", "f" * 64),
    ],
)
def test_probe_source_codec_refuses_relabelled_or_changed_custody(
    synthetic_probe_identity, field, value
):
    identity, files, raw, _ = synthetic_probe_identity
    changed = copy.deepcopy(identity)
    changed[field] = value
    with pytest.raises(ValueError, match="retained0017_reference_evidence_invalid"):
        measurement.source_identity(changed, files, raw)


def test_reference_probe_fixed_workflow_keeps_archive_and_read_only_permissions():
    path = ROOT / ".github/workflows/db0058-retained0017-reference-probe.yml"
    value = yaml.load(path.read_text(encoding="utf8"), Loader=yaml.BaseLoader)
    assert value["on"] == {"push": {"branches": [BRANCH]}, "workflow_dispatch": ""}
    assert value["permissions"] == {"contents": "read"}
    assert set(value["jobs"]) == {"reference-probe"}
    job = value["jobs"]["reference-probe"]
    assert job["if"] == "github.ref == 'refs/heads/" + BRANCH + "'"
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] == "35"
    assert len(job["steps"]) == 3
    assert (
        job["steps"][0]["uses"]
        == "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    )
    assert job["steps"][0]["with"] == {"persist-credentials": "false"}
    assert (
        job["steps"][2]["uses"]
        == "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
    )
    assert (
        job["steps"][2]["if"] == "always()"
        and job["steps"][2]["with"]["if-no-files-found"] == "error"
    )
    run = job["steps"][1]["run"]
    assert "git -c core.autocrlf=false -c core.eol=lf archive --format=tar" in run
    assert 'test "$commit" = "$GITHUB_SHA"' in run
    assert "git rev-parse 'HEAD^{tree}'" in run
    assert "python3 -m scripts.run_retained0017_reference_probe_v1" in run
    assert '--archive "$archive" --archive-sha256 "$digest"' in run
    assert '--commit "$commit" --tree "$tree"' in run
    assert "secrets." not in str(value) and "inputs." not in str(value)
    assert (
        "env" not in value
        and "env" not in job
        and all("env" not in step for step in job["steps"])
    )
    assert probe.TEST_MODULE == measurement.MODULE.replace(".", "/") + ".py"
    assert len(measurement.CASES) == 2
