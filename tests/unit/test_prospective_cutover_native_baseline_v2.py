"""Retained real PG17 schema observation only; no account or trading authority."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.domain import prospective_event_cutover_catalog_v2 as policy
from app.domain.prospective_event_cutover_v2 import ProspectiveCutoverError

BASELINE = "531b56a9680701a6f1bd462c52d0c57edb883194cbf88c75b84bfa7b391b66fd"
STATUS = "SEALED_FROM_REVIEWED_NATIVE_PG17"
FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests/fixtures/prospective_cutover_native_pg17_16d310_catalog.json"
)


@pytest.fixture
def recorded_catalog():
    raw = FIXTURE.read_bytes()
    assert len(raw) == 23894
    assert (
        hashlib.sha256(raw).hexdigest()
        == "fb345ba79f40a89282977b2c09045aee958e0af8613df50396c730e6818142fb"
    )
    envelope = json.loads(raw)
    assert (
        envelope["schema"] == "ctcc.prospective_cutover_native_catalog_observation.v2"
    )
    assert envelope["source_head"] == envelope["database_head"] == "0051"
    # This is the retained UNSEALED16d310 observation, never relabelled as a
    # capture from this later sealed source or a restricted runtime session.
    assert (
        envelope["manifest_sha256"]
        == "86923cb635fe5193450ee90d72a89a346a18a2c2efbccfdd7694023029b810a2"
    )
    assert (
        envelope["source_files"]["app/domain/prospective_event_cutover_catalog_v2.py"]
        == "549f5125e1696c125c28319f65e0a3a30139174e9d97f553c643d514407cb47a"
    )
    assert (
        envelope["native_baseline_admission"]
        is envelope["g6_qualified"]
        is envelope["execution_authority"]
        is False
    )
    return envelope["catalog"]


def test_reviewed_native_deparser_and_unchanged_ddl_match_formal_gate(recorded_catalog):
    before = copy.deepcopy(recorded_catalog)
    assert policy.PG17_DEPARSED_BASELINE == BASELINE
    assert policy.PG17_DEPARSED_BASELINE_STATUS == STATUS
    assert (
        policy.ddl_identity()
        == "dba77c3af835900e4d49ff9aa17f5d47031aeb2c40cdc4f84566e9c08e020924"
    )
    assert policy.deparser_sha256(recorded_catalog) == BASELINE
    policy.verify_catalog(recorded_catalog)
    summary = policy.expected_schema()
    assert (
        summary["native_acceptance"]
        is summary["g6_qualified"]
        is summary["execution_authority"]
        is False
    )
    assert recorded_catalog == before


def test_schema_owner_observation_cannot_pass_restricted_runtime_role(recorded_catalog):
    policy.verify_catalog(recorded_catalog)
    assert recorded_catalog["role"]["superuser"] is True
    with pytest.raises(
        ProspectiveCutoverError, match="^prospective_cutover_runtime_role$"
    ):
        policy.verify_runtime_role(recorded_catalog)


@pytest.mark.parametrize("defect", ("missing_digest", "pending_status"))
def test_saved_capture_cannot_reinstall_a_missing_or_pending_seal(
    recorded_catalog, monkeypatch, defect
):
    policy.verify_catalog(recorded_catalog)
    original = (policy.PG17_DEPARSED_BASELINE, policy.PG17_DEPARSED_BASELINE_STATUS)
    with monkeypatch.context() as pending:
        if defect == "missing_digest":
            pending.setattr(policy, "PG17_DEPARSED_BASELINE", None)
        else:
            pending.setattr(
                policy,
                "PG17_DEPARSED_BASELINE_STATUS",
                "UNSEALED_REQUIRES_NATIVE_PG17_CAPTURE",
            )
        policy.verify_catalog(recorded_catalog, require_native_baseline=False)
        with pytest.raises(
            ProspectiveCutoverError,
            match="^prospective_cutover_native_baseline_pending$",
        ):
            policy.verify_catalog(recorded_catalog)
    assert (
        policy.PG17_DEPARSED_BASELINE,
        policy.PG17_DEPARSED_BASELINE_STATUS,
    ) == original


@pytest.mark.parametrize(
    "defect", ("constraint", "index", "trigger_definition", "function_arguments")
)
def test_deparsed_semantic_drift_cannot_hide_behind_structural_checks(
    recorded_catalog, defect
):
    policy.verify_catalog(recorded_catalog)
    altered = copy.deepcopy(recorded_catalog)
    if defect == "constraint":
        row = next(
            v for v in altered["constraints"] if v["name"] == "ck_cwc_cutover_bytes"
        )
        row["definition"] = row["definition"].replace("16384", "16385")
    elif defect == "index":
        altered["indexes"][0]["definition"] += " WHERE epoch_id IS NOT NULL"
    elif defect == "trigger_definition":
        altered["triggers"][0]["definition"] += " /* drift */"
    else:
        altered["functions"][0]["args"] += ", forged text"
    assert policy.deparser_sha256(altered) != BASELINE
    policy.verify_catalog(altered, require_native_baseline=False)
    with pytest.raises(
        ProspectiveCutoverError, match="^prospective_cutover_native_baseline_drift$"
    ):
        policy.verify_catalog(altered)


@pytest.mark.parametrize(
    "defect",
    (
        "function_body",
        "function_owner",
        "public_execute",
        "disabled_trigger",
        "function_oid",
    ),
)
def test_actual_source_owner_acl_and_trigger_fences_remain_required(
    recorded_catalog, defect
):
    policy.verify_catalog(recorded_catalog)
    altered = copy.deepcopy(recorded_catalog)
    if defect == "function_body":
        altered["functions"][0]["body"] += "\n-- drift"
    elif defect == "function_owner":
        altered["functions"][0]["owner_oid"] += 1
    elif defect == "public_execute":
        row = dict(altered["acl"][0])
        row["grantee"] = 0
        altered["acl"].append(row)
    elif defect == "disabled_trigger":
        altered["triggers"][0]["enabled"] = "D"
    else:
        altered["triggers"][0]["function_oid"] += 1
    with pytest.raises(ProspectiveCutoverError):
        policy.verify_catalog(altered)
