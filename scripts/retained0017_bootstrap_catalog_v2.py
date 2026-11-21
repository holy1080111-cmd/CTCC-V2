"""Separate PG17.11 bootstrap-owner catalog match; readonly DATA only.

The original v1 reference purpose and literal are unchanged. No dependency is
removed, synthesized or projected: the complete normalized bootstrap catalog
must equal its own independently reviewed literal. This module opens no DB,
accepts no receipt/pass flag, and grants no migration or runtime authority.
"""

from __future__ import annotations

import json
import math
import os
import stat
from pathlib import Path

from scripts import retained0017_catalog_baseline_v1 as original

ROOT = Path(__file__).resolve().parents[1]
PURPOSE = "PG17_INITDB_BOOTSTRAP_OWNER_OID10"
SOURCE17_PREFIX_SHA256 = (
    "7a998b64b61f5bb30d65c405a995f3d00ffc12d15344b7cbda309f1c58b17cd2"
)
BOOTSTRAP_CATALOG_SHA256 = (
    "e1f30e83be81f3d5d45875627215681f09d168cf02f0a1b599b3529b104d48ee"
)
_PRIMARY_FACTS_SHA256 = (
    "b0ac08203bc765e3f6a9a276a850a2c4ed24992cf865920966bdbd317ba3787c"
)
_PRIMARY_NAMES = ("catalog.c", "pg_authid.dat", "pg_shdepend.c", "transam.h")
_FIXTURES = ROOT / "tests/fixtures/retained0017_bootstrap_source_v2"


class BootstrapCatalog17Refused(ValueError):
    pass


def _need(value):
    if value is not True:
        raise BootstrapCatalog17Refused(
            "retained0017_bootstrap_catalog_refused"
        ) from None


def _plain_data(document):
    pending, containers = [(document, 0)], set()
    count = capacity = 0
    while pending:
        value, depth = pending.pop()
        count += 1
        _need(depth <= 128 and count <= 1000000)
        kind = type(value)
        if kind is dict or kind is list:
            _need(id(value) not in containers)
            containers.add(id(value))
            if kind is dict:
                for key, child in value.items():
                    _need(type(key) is str)
                    capacity += len(key.encode("utf-8"))
                    pending.append((child, depth + 1))
            else:
                pending.extend((child, depth + 1) for child in value)
        elif kind is str:
            capacity += len(value.encode("utf-8"))
        elif kind is float:
            # Native pg_catalog cost/statistic fields are JSON numbers. OID
            # fields still use the original normalizer's exact-int checks.
            _need(math.isfinite(value))
        else:
            _need(value is None or kind is bool or kind is int)
        _need(capacity <= 32 * 1024 * 1024)


def _read_source(path, expected, maximum):
    before = path.lstat()
    _need(
        stat.S_ISREG(before.st_mode)
        and before.st_nlink == 1
        and not getattr(before, "st_file_attributes", 0) & 0x400
    )
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        raw = os.read(descriptor, maximum + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    def stable(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            getattr(info, "st_file_attributes", 0),
        )

    _need(stable(before) == stable(opened) == stable(after) == stable(path.lstat()))
    _need(opened.st_ctime_ns == after.st_ctime_ns and 0 < len(raw) <= maximum)
    _need(original.sha(raw) == expected)
    return raw


def _require_primary_pinned_owner_v2():
    _need(len(_PRIMARY_FACTS_SHA256) == 64)
    facts = json.loads(
        _read_source(
            _FIXTURES / "primary-pinned-owner.json", _PRIMARY_FACTS_SHA256, 16384
        )
    )
    _need(
        type(facts) is dict
        and set(facts)
        == {
            "schema",
            "source_tag",
            "bootstrap_role_oid",
            "first_unpinned_object_id",
            "owner_dependency_kind",
            "sources",
        }
    )
    _need(
        facts["schema"] == "ctcc.pg17.bootstrap_pinned_owner_primary.v2"
        and facts["source_tag"] == "REL_17_11"
    )
    _need(
        type(facts["bootstrap_role_oid"]) is int and facts["bootstrap_role_oid"] == 10
    )
    _need(
        type(facts["first_unpinned_object_id"]) is int
        and facts["first_unpinned_object_id"] == 12000
    )
    _need(facts["owner_dependency_kind"] == "SHARED_DEPENDENCY_OWNER")
    _need(
        type(facts["sources"]) is dict
        and tuple(sorted(facts["sources"])) == _PRIMARY_NAMES
    )
    for name in _PRIMARY_NAMES:
        row = facts["sources"][name]
        _need(type(row) is dict and set(row) == {"url", "sha256", "bytes"})
        _need(type(row["bytes"]) is int and 0 < row["bytes"] <= 200000)
        raw = _read_source(_FIXTURES / "primary" / name, row["sha256"], 200000)
        _need(len(raw) == row["bytes"])


def require_bootstrap_catalog17_v2(document):
    """Match actual raw physical/bootstrap fields and every normalized byte.

    A caller report or saved receipt is never an operand. The returned object
    is DATA; future source-owned maintenance must independently own its real
    connection, current profile, held locks, source custody and safety gates.
    """
    return _require_bootstrap_catalog17_v2(document, read_only="on")


def _require_bootstrap_catalog17_v2(document, *, read_only="on"):
    """Private observed-mode DATA check; it grants no custody/write authority.

    The maintenance owner validates its live connection and explicitly supplies
    that actual mode. The published reader always selects READ ONLY above.
    """
    try:
        _need(type(read_only) is str and read_only in ("on", "off"))
        _plain_data(document)
        _need(type(document) is dict)
        physical, profile = document.get("actor_physical"), document.get("profile")
        _need(type(physical) is dict and type(profile) is dict)
        role = physical.get("owner")
        _need(
            type(role) is list
            and len(role) == 9
            and type(role[0]) is int
            and role[0] == 10
        )
        _need(all(flag is True for flag in role[2:]))
        _need(
            type(physical.get("membership_count")) is int
            and physical["membership_count"] == 0
        )
        _need(physical.get("membership_sha256") == original.sha(b"[]"))
        _need(profile.get("server_version") == "170011")
        _need(
            type(document.get("shared_dependencies")) is list
            and len(document["shared_dependencies"]) == 0
        )
        _require_primary_pinned_owner_v2()
        normalized = original._normalize(document, read_only=read_only)
        _need(normalized["source17_prefix_sha256"] == SOURCE17_PREFIX_SHA256)
        _need(original.sha(original.canonical(normalized)) == BOOTSTRAP_CATALOG_SHA256)
    except (KeyError, TypeError, ValueError, OSError):
        raise BootstrapCatalog17Refused(
            "retained0017_bootstrap_catalog_refused"
        ) from None
    return {
        "schema": "ctcc.retained0017.bootstrap_catalog_match_data.v2",
        "reference_purpose": PURPOSE,
        "catalog_sha256": BOOTSTRAP_CATALOG_SHA256,
        "source17_prefix_sha256": SOURCE17_PREFIX_SHA256,
        "physical_owner_oid": 10,
        "shared_dependency_rows": 0,
        "schema_upgrade_authorized": False,
        "runtime_startup_admission": False,
        "execution_authority": False,
    }
