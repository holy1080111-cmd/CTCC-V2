"""Finite real54->53->52 prefix for the current54 hermetic harness.

Every subprocess reads the actual database and its independent actual-source54
scope. Historical52/53 nested declarations retain their genuine observed heads.
No old52 document is relabelled as54 and no subprocess-success boolean suffices.
"""

from pathlib import Path

from scripts import collector_current_0053_toolchain as old53
from scripts import collector_current_0054_toolchain as current54
from scripts import verify_final_hermetic as legacy

_TOOL = ["python", "-m", "scripts.collector_current_0054_toolchain"]


def _read(output, name):
    path = Path(output) / (name + ".log")
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= current54.MAX_BYTES
    ):
        raise ValueError("current54_transition_actual_log_missing")
    return legacy.read_evidence_json(path.read_bytes())


def _scope(value, target):
    if (
        type(value) is not dict
        or value.get("source_head") != "0054"
        or value.get("database_head") != target
        or value.get("account_complete") is not False
        or value.get("execution_authority") is not False
        or type(value.get("source_scope")) is not dict
        or value["source_scope"].get("source_head") != "0054"
        or value["source_scope"].get("database_head") != target
        or any(
            value["source_scope"].get(name) is not False
            for name in ("account_complete", "execution_authority")
        )
    ):
        raise ValueError("current54_transition_source_database_scope_changed")


def run_empty_prefix(container, *, output):
    container(
        "migration-current54-empty-before", [*_TOOL, "--readback0054"], timeout=90
    )
    before54 = _read(output, "migration-current54-empty-before")
    if not legacy.native_migration_json_equal(
        before54, current54.expected_current0054_migration_readback()
    ):
        raise ValueError("current54_empty_actual_readback_changed")
    container(
        "migration-current54-empty-downgrade",
        ["alembic", "downgrade", "0053"],
        timeout=90,
    )
    container(
        "migration-historical53-on54-readback",
        [*_TOOL, "--historical53-readback"],
        timeout=90,
    )
    after53 = _read(output, "migration-historical53-on54-readback")
    _scope(after53, "0053")
    if (
        after53.get("schema") != "ctcc.source0054_historical0053.migration_readback.v1"
        or not legacy.native_migration_json_equal(
            after53.get("historical0053_readback"),
            old53.expected_current0053_migration_readback(),
        )
    ):
        raise ValueError("current54_downgrade_actual53_changed")
    container(
        "migration-current53-empty-downgrade",
        ["alembic", "downgrade", "0052"],
        timeout=90,
    )
    container(
        "migration-historical52-on54-readback",
        [*_TOOL, "--historical52-readback"],
        timeout=90,
    )
    after52 = _read(output, "migration-historical52-on54-readback")
    _scope(after52, "0052")
    if (
        after52.get("schema") != "ctcc.source0054_historical0052.migration_readback.v1"
        or not legacy.native_migration_json_equal(
            after52.get("historical0052_readback"),
            legacy.expected_migration_revision_readback("0052"),
        )
    ):
        raise ValueError("current54_downgrade_actual52_changed")
    return {
        "schema": "ctcc.current0054.actual_empty_prefix.v1",
        "source_head": "0054",
        "current0054_before": before54,
        "current0054_down_revision": "0053",
        "historical0053_readback": after53,
        "historical0053_down_revision": "0052",
        "historical0052_readback": after52,
        "account_complete": False,
        "execution_authority": False,
    }


def verify_empty_prefix_evidence(root, value):
    expected_keys = {
        "schema",
        "source_head",
        "current0054_before",
        "current0054_down_revision",
        "historical0053_readback",
        "historical0053_down_revision",
        "historical0052_readback",
        "account_complete",
        "execution_authority",
    }
    if (
        type(value) is not dict
        or set(value) != expected_keys
        or value["schema"] != "ctcc.current0054.actual_empty_prefix.v1"
        or value["source_head"] != "0054"
        or value["current0054_down_revision"] != "0053"
        or value["historical0053_down_revision"] != "0052"
        or value["account_complete"] is not False
        or value["execution_authority"] is not False
    ):
        raise ValueError("current54_empty_prefix_receipt_changed")
    before54 = _read(root, "migration-current54-empty-before")
    after53 = _read(root, "migration-historical53-on54-readback")
    after52 = _read(root, "migration-historical52-on54-readback")
    _scope(after53, "0053")
    _scope(after52, "0052")
    if (
        value["current0054_before"] != before54
        or value["historical0053_readback"] != after53
        or value["historical0052_readback"] != after52
        or not legacy.native_migration_json_equal(
            before54, current54.expected_current0054_migration_readback()
        )
        or not legacy.native_migration_json_equal(
            after53["historical0053_readback"],
            old53.expected_current0053_migration_readback(),
        )
        or not legacy.native_migration_json_equal(
            after52["historical0052_readback"],
            legacy.expected_migration_revision_readback("0052"),
        )
    ):
        raise ValueError("current54_empty_prefix_actual_log_changed")
    return value
