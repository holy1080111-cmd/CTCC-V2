"""Current54 nonempty retention evidence; historical52 receipts stay unchanged."""

import hashlib
import json
import re
from copy import deepcopy

from app.domain import collector_current_0054_catalog_v1 as policy54
from scripts import collector_current_0054_toolchain as current54
from scripts import verify_final_hermetic as legacy

HISTORY_PARTITION_DOWNGRADE_DENIAL = legacy.HISTORY_PARTITION_DOWNGRADE_DENIAL
native_migration_json_equal = legacy.native_migration_json_equal


def history_partition_nonempty_snapshot_current54(revision, retention):
    from app.domain.account_history_partition_catalog_v1 import TABLES

    if (
        not native_migration_json_equal(
            revision, current54.expected_current0054_migration_readback()
        )
        or type(retention) is not dict
        or set(retention) != {"rows", "counts", "rows_sha256"}
        or type(retention["rows"]) is not dict
        or set(retention["rows"]) != set(TABLES)
        or type(retention["counts"]) is not dict
        or set(retention["counts"]) != set(TABLES)
    ):
        raise RuntimeError("history_partition_nonempty_snapshot_invalid")
    for name in TABLES:
        count = retention["counts"][name]
        if (
            type(count) is not int
            or not 0 <= count <= 8192
            or type(retention["rows"][name]) is not list
            or len(retention["rows"][name]) != count
            or any(type(row) is not dict for row in retention["rows"][name])
        ):
            raise RuntimeError("history_partition_nonempty_snapshot_invalid")
    body = json.dumps(
        retention["rows"], sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode("ascii")
    if (
        sum(retention["counts"].values()) < 1
        or len(body) > 64 * 1024 * 1024
        or hashlib.sha256(body).hexdigest() != retention["rows_sha256"]
    ):
        raise RuntimeError("history_partition_nonempty_snapshot_invalid")
    return {
        "schema": "ctcc.account_history_partition_db0054_nonempty_snapshot.v1",
        "revision_readback": deepcopy(revision),
        "retention": deepcopy(retention),
        "execution_authority": False,
    }


def history_partition_nonempty_downgrade_readback_current54(before, after):
    for value in (before, after):
        if (
            type(value) is not dict
            or set(value)
            != {"schema", "revision_readback", "retention", "execution_authority"}
            or not native_migration_json_equal(
                value,
                history_partition_nonempty_snapshot_current54(
                    value.get("revision_readback"), value.get("retention")
                ),
            )
        ):
            raise RuntimeError("history_partition_nonempty_downgrade_readback_mismatch")
    if not native_migration_json_equal(before, after):
        raise RuntimeError("history_partition_nonempty_downgrade_readback_mismatch")
    return {
        "schema": "ctcc.account_history_partition_db0054_nonempty_downgrade.v1",
        "source_head": "0054",
        "target": "0049",
        "result": "DENIED",
        "policy_refusal": HISTORY_PARTITION_DOWNGRADE_DENIAL,
        "before": deepcopy(before),
        "after": deepcopy(after),
        "rollback": "EXACT",
        "execution_authority": False,
    }


def verify_history_partition_downgrade_denial_log_current54(path):
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= 2 * 1024 * 1024
    ):
        raise ValueError("current54_partition_downgrade_log_missing")
    output = path.read_bytes().decode("utf-8")
    if (
        re.findall(r"Running downgrade (\d{4} -> \d{4})", output)
        != [
            "0054 -> 0053",
            "0053 -> 0052",
            "0052 -> 0051",
            "0051 -> 0050",
            "0050 -> 0049",
        ]
        or "/app/migrations/versions/0050_account_history_partition.py" not in output
        or len(
            re.findall(
                r"(?m)^asyncpg\.exceptions\.RaiseError: "
                + HISTORY_PARTITION_DOWNGRADE_DENIAL
                + r"\r?$",
                output,
            )
        )
        != 1
        or not re.search(
            r"(?m)^sqlalchemy\.exc\.DBAPIError: .*: "
            + HISTORY_PARTITION_DOWNGRADE_DENIAL
            + r"\r?$",
            output,
        )
    ):
        raise ValueError("current54_partition_actual_downgrade_refusal_unverified")


def verify_current54_partition_migration_receipt(
    value, owner_artifact, *, manifest_sha256, repository_sha256, migration_sha256
):
    if type(value) is not dict or set(value) != {
        "schema_version",
        "manifest_sha256",
        "repository_sha256",
        "migration_sha256",
        "server_major",
        "source_head",
        "before",
        "after",
        "owner_graph_before_sha256",
        "owner_graph_after_sha256",
        "owner_artifact_sha256",
        "refusal_code",
        "downgrade_target",
        "native_baseline_admission",
        "execution_authority",
    }:
        raise ValueError("current54_partition_receipt_shape")
    if (
        value["schema_version"]
        != "ctcc.account_history_partition_migration_acceptance.v4"
        or value["source_head"] != "0054"
        or type(value["server_major"]) is not int
        or value["server_major"] != 17
        or value["manifest_sha256"] != manifest_sha256
        or value["repository_sha256"] != repository_sha256
        or value["migration_sha256"] != migration_sha256
        or value["refusal_code"] != HISTORY_PARTITION_DOWNGRADE_DENIAL
        or value["downgrade_target"] != "0049"
        or value["native_baseline_admission"] is not False
        or value["execution_authority"] is not False
        or any(
            type(pin) is not str or re.fullmatch("[a-f0-9]{64}", pin) is None
            for pin in (
                manifest_sha256,
                repository_sha256,
                migration_sha256,
                value["owner_graph_before_sha256"],
                value["owner_graph_after_sha256"],
                value["owner_artifact_sha256"],
            )
        )
    ):
        raise ValueError("current54_partition_receipt_identity")
    from app.domain.account_history_partition_catalog_v1 import TABLES

    for snap in (value["before"], value["after"]):
        if (
            type(snap) is not dict
            or set(snap) != {"head", "rows_sha256", "counts", "catalog_sha256"}
            or snap["head"] != "0054"
            or type(snap["counts"]) is not dict
            or set(snap["counts"]) != set(TABLES)
            or any(
                type(n) is not int or not 0 <= n <= 8192
                for n in snap["counts"].values()
            )
            or sum(snap["counts"].values()) < 1
            or any(
                type(snap[name]) is not str
                or re.fullmatch("[a-f0-9]{64}", snap[name]) is None
                for name in ("rows_sha256", "catalog_sha256")
            )
        ):
            raise ValueError("current54_partition_receipt_snapshot")
    if not native_migration_json_equal(value["before"], value["after"]):
        raise ValueError("current54_partition_receipt_retention_changed")
    if (
        owner_artifact.is_symlink()
        or not owner_artifact.is_file()
        or not 0 < owner_artifact.stat().st_size <= current54.MAX_BYTES
    ):
        raise ValueError("current54_partition_owner_artifact_missing")
    raw = owner_artifact.read_bytes()
    if hashlib.sha256(raw).hexdigest() != value["owner_artifact_sha256"]:
        raise ValueError("current54_partition_owner_artifact_changed")
    document = legacy.read_evidence_json(raw)
    if (
        type(document) is not dict
        or set(document)
        != {
            "schema",
            "source_head",
            "database_head",
            "before",
            "after",
            "native_baseline_admission",
            "account_complete",
            "execution_authority",
        }
        or document["schema"] != "ctcc.current54.nonempty_rollback_owner_catalog.v1"
        or document["source_head"] != "0054"
        or document["database_head"] != "0054"
        or any(
            document[name] is not False
            for name in (
                "native_baseline_admission",
                "account_complete",
                "execution_authority",
            )
        )
    ):
        raise ValueError("current54_partition_owner_artifact_scope")
    before = policy54.observe_actual_owner_catalog(document["before"])
    after = policy54.observe_actual_owner_catalog(document["after"])
    left = hashlib.sha256(policy54.canonical(document["before"])).hexdigest()
    right = hashlib.sha256(policy54.canonical(document["after"])).hexdigest()
    if (
        left != value["owner_graph_before_sha256"]
        or right != value["owner_graph_after_sha256"]
        or left != right
        or before != after
    ):
        raise ValueError("current54_partition_owner_graph_rollback_changed")
    return deepcopy(value)
