"""Replay mutations of the official catalog bytes without claiming SQL proof."""

import copy
import gzip
import json
from pathlib import Path

import pytest

from scripts import paper58_actor_overlay_v1 as overlay
from scripts import paper58_catalog_reference_v1 as reference
from scripts import paper58_owner_preflight_v1 as owner
from scripts import paper58_source_coverage_v1 as source

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/paper58_fresh_preprovision_v1"
ACTOR = "ctcc_paper_acl_test"
ACTOR_OID = 50001


def _set_acl(row, field, acl):
    row[field] = acl
    dims = row["_ctcc_catalog_array_dims_v1"]
    if field in dims:
        dims[field] = None if acl is None else f"[1:{len(acl)}]"


def _observed_overlay(label):
    raw = gzip.decompress(
        (FIXTURE / f"paper58-reference-{label}-raw.json.gz").read_bytes()
    )
    document = json.loads(raw)
    db = document["actor_physical"]["database_oid"]
    maintenance = document["actor_physical"]["owner"][1]
    document["references"].append(
        [
            "pg_authid",
            ACTOR_OID,
            {
                "name": ACTOR,
                "identity": ACTOR,
                "schema": None,
                "type": "role",
            },
        ]
    )
    edges = document["shared_dependencies"]
    privileges = {
        "alembic_version": "r",
        "audit_logs": "ar",
        "orchestrator_fingerprint_state": "r",
        "orchestrator_run_state": "r",
        "paper_account_state": "arw",
        "paper_order_state": "arwd",
        "paper_position_state": "arwd",
        "recovery_checkpoints": "arw",
        "system_events": "ar",
    }
    assert set(privileges) == dict(source.TABLE_PRIVILEGES).keys()
    assert sum(map(len, privileges.values())) == 21
    for kind, row, _ in document["entries"]:
        if kind == "relation" and row["relname"] in privileges:
            assert row["relacl"] is None
            _set_acl(
                row,
                "relacl",
                [
                    f"{maintenance}=arwdDxtm/{maintenance}",
                    f"{ACTOR}={privileges[row['relname']]}/{maintenance}",
                ],
            )
            edges.append([db, "pg_class", row["oid"], 0, "pg_authid", ACTOR_OID, "a"])
        elif kind == "namespace":
            _set_acl(row, "nspacl", [*row["nspacl"], f"{ACTOR}=U/pg_database_owner"])
            edges.append(
                [db, "pg_namespace", row["oid"], 0, "pg_authid", ACTOR_OID, "a"]
            )
        elif kind == "database":
            assert row["datacl"] is None
            _set_acl(
                row,
                "datacl",
                [
                    f"=c/{maintenance}",
                    f"{maintenance}=CTc/{maintenance}",
                    f"{ACTOR}=c/{maintenance}",
                ],
            )
            edges.append([0, "pg_database", db, 0, "pg_authid", ACTOR_OID, "a"])
        elif kind == "function" and row["proacl"] is None:
            _set_acl(row, "proacl", [f"{maintenance}=X/{maintenance}"])
    for row in document["bootstrap"]["expression_callbacks"]:
        if row["pronamespace"] == 2200:
            assert row["proacl"] is None
            _set_acl(row, "proacl", [f"{maintenance}=X/{maintenance}"])
    return document


def _verify(document):
    return overlay.require_fresh_unpinned_postprovision_catalog_data_v1(
        document,
        actor_oid=ACTOR_OID,
        actor_name=ACTOR,
    )


@pytest.mark.parametrize("label", ["a", "b"])
def test_verified_projection_retains_genuine_structure_and_obs_exact_data(label):
    document = _observed_overlay(label)
    observed_hash = reference.digest(reference.canonical(document))
    projected, result = _verify(document)
    assert result["observed_catalog_sha256"] == observed_hash
    assert reference.digest(reference.canonical(document)) == observed_hash
    assert (
        len(document["shared_dependencies"]) - len(projected["shared_dependencies"])
        == 11
    )
    assert result["projected_normalized_catalog_sha256"] == owner.CATALOG_SHA256
    assert result["source58_scope_sha256"] == owner.SOURCE_SCOPE_SHA256
    assert result["postprovision_acl_overlay_verified"] is True
    for key in (
        "bootstrap_owner_continuation_accepted",
        "schema_admission",
        "paper_actor_admission",
        "startup_admission",
        "execution_authority",
    ):
        assert result[key] is False
    with pytest.raises(ValueError):
        owner.require_fresh_unpinned_preprovision_catalog_v1(document)


@pytest.mark.parametrize(
    "mutation",
    [
        "grant_option",
        "missing_table_grant",
        "acl_array_bounds",
        "public_temp",
        "public_schema_create",
        "incorrect_schema_grantor",
        "omit_public_revoke",
        "extra_role",
        "missing_acl_edge",
        "duplicate_acl_edge",
        "incorrect_shared_database",
        "native_function_body",
        "split_callback_observation",
        "other_column_acl",
        "bootstrap_owner",
    ],
)
def test_postprovision_overlay_refuses_extra_access_or_structural_drift(mutation):
    document = _observed_overlay("a")
    maintenance = document["actor_physical"]["owner"][1]
    table = next(
        row
        for kind, row, _ in document["entries"]
        if kind == "relation" and row["relname"] == "paper_account_state"
    )
    db = next(row for kind, row, _ in document["entries"] if kind == "database")
    namespace = next(row for kind, row, _ in document["entries"] if kind == "namespace")
    if mutation == "grant_option":
        _set_acl(table, "relacl", [table["relacl"][0], f"{ACTOR}=a*rw/{maintenance}"])
    elif mutation == "missing_table_grant":
        _set_acl(table, "relacl", [table["relacl"][0], f"{ACTOR}=ar/{maintenance}"])
    elif mutation == "acl_array_bounds":
        table["_ctcc_catalog_array_dims_v1"]["relacl"] = "[0:1]"
    elif mutation == "public_temp":
        db["datacl"][0] = f"=Tc/{maintenance}"
    elif mutation == "public_schema_create":
        namespace["nspacl"][1] = "=UC/pg_database_owner"
    elif mutation == "incorrect_schema_grantor":
        namespace["nspacl"][2] = f"{ACTOR}=U/{maintenance}"
    elif mutation == "omit_public_revoke":
        function = next(
            row
            for kind, row, _ in document["entries"]
            if kind == "function" and row["proname"] == "qualification_ledger_immutable"
        )
        _set_acl(function, "proacl", None)
    elif mutation == "extra_role":
        _set_acl(table, "relacl", [*table["relacl"], f"anonymous=r/{maintenance}"])
    elif mutation == "missing_acl_edge":
        document["shared_dependencies"].pop()
    elif mutation == "duplicate_acl_edge":
        document["shared_dependencies"].append(document["shared_dependencies"][-1][:])
    elif mutation == "incorrect_shared_database":
        document["shared_dependencies"][-1][0] = 0
    elif mutation == "native_function_body":
        function = next(
            row
            for kind, row, _ in document["entries"]
            if kind == "function" and row["proname"] == "qualification_ledger_immutable"
        )
        function["prosrc"] += "\n-- semantic drift\n"
    elif mutation == "split_callback_observation":
        callback = next(
            row
            for row in document["bootstrap"]["expression_callbacks"]
            if row["pronamespace"] == 2200
        )
        _set_acl(callback, "proacl", None)
    elif mutation == "other_column_acl":
        column = next(
            row
            for kind, row, _ in document["entries"]
            if kind == "attribute" and row["attrelid"] == table["oid"]
        )
        _set_acl(column, "attacl", [f"{ACTOR}=r/{maintenance}"])
    else:
        document["actor_physical"]["owner"][0] = 10
    original = copy.deepcopy(document)
    with pytest.raises(ValueError):
        _verify(document)
    assert document == original
