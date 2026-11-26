"""Test resource DATA for the genuine, fixed-purpose maintenance supervisor.

This helper cannot construct a cold origin, a live lease, a connection, a
submission permission or an acceptance result. The canonical supervisor must
independently open and verify all resources and perform the actual operation.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

RESOURCE_KEYS = frozenset(
    {
        "pg_rootfs",
        "pg_inventory_file",
        "python_rootfs",
        "python_inventory_file",
        "private_output_parent",
        "ca_certificate_file",
        "server_certificate_file",
        "server_private_key_file",
        "runtime_provider_file",
        "source_archive_file",
    }
)
SOURCE_KEYS = frozenset({"commit", "tree", "archive", "manifest"})
HEX64 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
HEX40 = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
REQUEST_ENVIRONMENT = "CTCC_NATIVE_MAINTENANCE_FRESH_REQUEST"


def _need(value):
    if value is not True:
        raise ValueError("fresh_custody_test_resource_data_invalid") from None


def fresh_request_data_v1(resources, source):
    """Validate locations and source identifiers as DATA, without reading them."""
    _need(type(resources) is dict and set(resources) == RESOURCE_KEYS)
    for value in resources.values():
        _need(type(value) is str and 0 < len(value) <= 2048)
        path = PurePosixPath(value)
        _need(
            path.is_absolute()
            and path.as_posix() == value
            and ".." not in path.parts
            and "\\" not in value
            and not any(ord(char) < 32 or ord(char) == 127 for char in value)
        )
    _need(type(source) is dict and set(source) == SOURCE_KEYS)
    for name, value in source.items():
        _need(type(value) is str)
        pattern = HEX40 if name in ("commit", "tree") else HEX64
        _need(pattern.fullmatch(value) is not None)
    return {
        "schema": "ctcc.retained_pg.fresh_acceptance_request.v1",
        "resources": dict(resources),
        "source": dict(source),
    }


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result)
        result[key] = value
    return result


def requested_fresh_resource_v1():
    """Load only an explicitly supplied private test request; never discover one."""
    value = os.environ.get(REQUEST_ENVIRONMENT)
    _need(type(value) is str and len(value) > 0 and os.name == "posix")
    path = Path(value)
    _need(path.is_absolute() and ".." not in path.parts)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        _need(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and before.st_uid == before.st_gid == 0
            and stat.S_IMODE(before.st_mode) == 0o600
            and 0 < before.st_size <= 65536
        )
        raw = os.read(descriptor, 65537)
        after = os.fstat(descriptor)
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_uid",
            "st_gid",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        _need(
            len(raw) == before.st_size
            and os.read(descriptor, 1) == b""
            and all(
                getattr(before, name) == getattr(after, name) for name in stable_fields
            )
        )
    finally:
        os.close(descriptor)
    request = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    _need(
        type(request) is dict
        and set(request) == {"schema", "resources", "source"}
        and request["schema"] == "ctcc.retained_pg.fresh_acceptance_request.v1"
    )
    expected = fresh_request_data_v1(request["resources"], request["source"])
    _need(request == expected)
    return path, expected


def assert_fresh_acceptance_data_v1(value):
    """Assert a returned observation; never pass it back as an authority input."""
    _need(type(value) is dict)
    exact = {
        "schema": "ctcc.retained_pg.fresh_acceptance_result.v1",
        "status": "FRESH_CANONICAL17_COLD_TO_STOPPED0055_VERIFIED",
        "origin": "fresh_canonical17_cold_fixture",
        "database_head": "0055",
    }
    _need(
        all(
            type(value.get(key)) is str and value[key] == expected
            for key, expected in exact.items()
        )
    )
    true_fields = (
        "migration_performed",
        "source17_bootstrap_catalog_verified",
        "rehearsal_rolled_back_verified",
        "cold_source_unchanged_verified",
        "distinct_working_copy_verified",
        "owned_server_fast_shutdown_verified",
        "emergency_stop",
        "restart_latch_required",
    )
    false_fields = (
        "armed",
        "preserved_backup_lineage_verified",
        "schema_upgrade_authorized",
        "account_complete",
        "execution_authority",
    )
    _need(all(value.get(key) is True for key in true_fields))
    _need(all(value.get(key) is False for key in false_fields))
    for name, expected in (
        ("independent_readbacks", 2),
        ("commit_attempts", 1),
        ("preserved_pg_inputs_read", 0),
        ("control_revision", 0),
    ):
        _need(type(value.get(name)) is int and value[name] == expected)
    _need("exchange_flat" in value and value["exchange_flat"] is None)
    for name in (
        "cold_manifest_sha256",
        "origin_sha256",
        "system_identifier_sha256",
        "before_sha256",
        "after_sha256",
    ):
        _need(type(value.get(name)) is str and HEX64.fullmatch(value[name]) is not None)
    _need(value.get("row_encoding") == "ctcc.owner.retained_row_sql_null_bitmap.v2")
    return value
