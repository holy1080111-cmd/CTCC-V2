"""Recorded native PG17 catalog regressions only; no runtime/account authority."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.domain import account_history_partition_catalog_v1 as policy

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    ROOT / "tests/fixtures/account_history_partition_native_pg17_0403_catalog.json"
)
RAW_SHA = "fec7abcfaecebaa5216ef9835a0d4497a473b8363a0bbdaf6995ef3d59f9f4d6"
BASELINE = "37190935d1b8e2807d26590225165779bb12e9684c260dd19fb61fba0d3c64c3"


@pytest.fixture
def recorded_catalog():
    raw = FIXTURE.read_bytes()
    assert len(raw) == 100039 and hashlib.sha256(raw).hexdigest() == RAW_SHA
    envelope = json.loads(raw)
    assert (
        envelope["schema"]
        == "ctcc.account_history_partition_native_catalog_observation.v1"
    )
    # source_head is the migration revision, NOT the source Git commit.
    assert envelope["source_head"] == envelope["database_head"] == "0050"
    assert (
        envelope["manifest_sha256"]
        == "2466d148f72faa2dece775ba1b13ec9019b73bc7c556a99aaa03ed63a8af5a0e"
    )
    assert (
        envelope["source_files"]["app/domain/account_history_partition_catalog_v1.py"]
        == "0b7500f0c9570ffa27867762ef82ad73997c2a47f9ec5989bdcb402f45a6d9ac"
    )
    assert (
        envelope["native_baseline_admission"]
        is envelope["execution_authority"]
        is False
    )
    assert envelope["catalog"]["server"]["version_num"] == "170011"
    return envelope["catalog"]


def test_recorded_native_schema_matches_reviewed_formal_baseline_without_authority(
    recorded_catalog,
):
    before = copy.deepcopy(recorded_catalog)
    assert policy.PG17_DEPARSED_BASELINE == BASELINE
    assert policy.PG17_DEPARSED_BASELINE_STATUS == "SEALED_FROM_REVIEWED_NATIVE_PG17"
    assert (
        policy.ddl_identity()
        == "ea1673b244a9cdda216f49300045ef0f36633b292ac59da853b940291d7576bb"
    )
    assert policy.deparser_sha256(recorded_catalog) == BASELINE
    result = policy.verify_account_history_partition_catalog(recorded_catalog)
    assert result["revision"] == "0050"
    assert result["native_acceptance"] is result["execution_authority"] is False
    assert recorded_catalog == before


def test_recorded_admin_capture_must_fail_actual_restricted_runtime_gate(
    recorded_catalog,
):
    assert recorded_catalog["role"]["superuser"] is True
    assert recorded_catalog["server"]["isolation"] == "repeatable read"
    with pytest.raises(
        policy.AccountHistoryPartitionError,
        match="^account_history_partition_role_denied$",
    ):
        policy.verify_runtime_role(recorded_catalog)


@pytest.mark.parametrize("target", ("check", "index"))
def test_native_deparsed_definition_drift_cannot_hide_behind_structural_metadata(
    recorded_catalog, target
):
    altered = copy.deepcopy(recorded_catalog)
    if target == "check":
        row = next(
            row
            for row in altered["constraints"]
            if row["name"] == "ck_history_member_bytes"
        )
        assert "262144" in row["definition"]
        row["definition"] = row["definition"].replace("262144", "262145")
    else:
        row = next(
            row
            for row in altered["indexes"]
            if row["name"] == "uq_history_member_capture"
        )
        row["definition"] += " WHERE capture_id IS NOT NULL"
    # Structural diagnostics intentionally do not certify deparser contents.
    policy.verify_account_history_partition_catalog(
        altered, require_native_baseline=False
    )
    with pytest.raises(
        policy.AccountHistoryPartitionError,
        match="^account_history_partition_native_baseline_drift$",
    ):
        policy.verify_account_history_partition_catalog(altered)


@pytest.mark.parametrize("field", ("table_oid", "target_oid"))
def test_excluded_dynamic_oids_still_require_actual_graph_identity(
    recorded_catalog, field
):
    altered = copy.deepcopy(recorded_catalog)
    row = next(row for row in altered["constraints"] if row["kind"] == "f")
    row[field] += 1
    assert policy.deparser_sha256(altered) == BASELINE
    with pytest.raises(policy.AccountHistoryPartitionError):
        policy.verify_account_history_partition_catalog(altered)


def test_structural_diagnostic_and_saved_catalog_cannot_install_a_missing_seal(
    recorded_catalog, monkeypatch
):
    monkeypatch.setattr(policy, "PG17_DEPARSED_BASELINE", None)
    monkeypatch.setattr(
        policy, "PG17_DEPARSED_BASELINE_STATUS", "PENDING_NATIVE_PG17_REVIEW"
    )
    policy.verify_account_history_partition_catalog(
        recorded_catalog, require_native_baseline=False
    )
    with pytest.raises(
        policy.AccountHistoryPartitionError,
        match="^account_history_partition_native_baseline_pending$",
    ):
        policy.verify_account_history_partition_catalog(recorded_catalog)
    assert policy.PG17_DEPARSED_BASELINE is None
    assert policy.PG17_DEPARSED_BASELINE_STATUS == "PENDING_NATIVE_PG17_REVIEW"


@pytest.mark.parametrize(
    "defect",
    (
        "public_execute",
        "changed_function_source",
        "wrong_function_namespace",
        "deferred_trigger_disabled",
        "missing_trigger",
    ),
)
def test_recorded_native_source_acl_and_trigger_changes_are_refused(
    recorded_catalog, defect
):
    altered = copy.deepcopy(recorded_catalog)
    if defect == "public_execute":
        altered["functions"][0]["public_execute"] = True
    elif defect == "changed_function_source":
        altered["functions"][0]["source"] += "\n-- changed"
    elif defect == "wrong_function_namespace":
        altered["functions"][0]["namespace"] = "other"
    elif defect == "deferred_trigger_disabled":
        next(row for row in altered["triggers"] if row["constraint"])["enabled"] = "D"
    else:
        altered["triggers"].pop()
    with pytest.raises(policy.AccountHistoryPartitionError):
        policy.verify_account_history_partition_catalog(altered)
