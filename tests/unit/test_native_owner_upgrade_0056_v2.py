"""Source-owned successor contracts; fixtures confer no database authority."""

from __future__ import annotations

import copy
import json
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import native_owner_upgrade_0056_v1 as subject
from tests.unit.test_native_owner_upgrade_0056_v1 import _pins, _upgrade_pins


def _inventory(*, successor=False):
    names = ("alembic_version", "audit_logs")
    rows = [["audit_logs", 2, "a" * 64]]
    layouts = [
        [name, index, "r", [["before", 3802, -1, False, 0, "", ""]]]
        for index, name in enumerate(names, 1)
    ]
    relations = tuple((name, index, "r") for index, name in enumerate(names, 1))
    objects = (("relation", 1, 0, "b" * 64, False), ("relation", 2, 0, "c" * 64, False))
    acl = (("relation", 1, 500, 400, "SELECT", False),)
    if successor:
        for index, name in enumerate(sorted(subject._ADDED_TABLES), 3):
            rows.append([name, 0, subject._sha(b"")])
            layouts.append([name, index, "r", [["hash", 1043, 68, True, 100, "", ""]]])
            relations += ((name, index, "r"),)
            objects += (("relation", index, 0, "d" * 64, True),)
            acl += (("relation", index, 400, 400, "SELECT", False),)
    return {
        "relations": relations,
        "objects": objects,
        "acl": acl,
        "logical_sha256": "e" * 64,
        "snapshot": {
            "row_encoding": subject.retained_rows.ROW_ENCODING,
            "rows": rows,
            "row_layouts": layouts,
            "sequence_state": [["audit_id_seq", 31, 400, 8, True]],
            "role_state_sha256": "f" * 64,
        },
    }


def test_positive_pins_are_explicit_and_v1_remains_data_only():
    assert subject._upgrade_pins_v2(_upgrade_pins()) == _pins()
    with pytest.raises(subject.NativeOwnerUpgradeRefusal):
        subject._upgrade_pins_v2(_pins())


@pytest.mark.parametrize(
    "field,value",
    [
        ("role_names", ["writer", "custody", "frame"]),
        ("role_names", ("writer", "writer", "frame")),
        ("role_names", ("public", "custody", "frame")),
        ("role_names", ("pg_writer", "custody", "frame")),
        ("role_names", ("ctcc_owner", "custody", "frame")),
        ("role_names", ("writer;DROP", "custody", "frame")),
        ("role_oids", [20001, 20002, 20003]),
        ("role_oids", (True, 20002, 20003)),
        ("role_oids", (0, 20002, 20003)),
        ("role_oids", (20001, 20001, 20003)),
        ("role_oids", (16384, 20002, 20003)),
    ],
)
def test_v2_cannot_guess_normalize_or_accept_privileged_role_identity(field, value):
    with pytest.raises(subject.NativeOwnerUpgradeRefusal):
        subject._upgrade_pins_v2(replace(_upgrade_pins(), **{field: value}))


@pytest.mark.asyncio
async def test_legacy_positive_input_refuses_before_any_source_or_database_open(
    monkeypatch,
):
    monkeypatch.setattr(subject, "_prepared", lambda *a: pytest.fail("source opened"))
    with pytest.raises(subject.NativeOwnerUpgradeRefusal):
        await subject.upgrade_owner_0055_to_0056_v1(
            _pins(), owner_dsn_file=None, ca_file=None, source_archive_file=None
        )


def test_preservation_accepts_only_source_three_empty_tables_and_same_old_objects():
    subject._preserved_v2(_inventory(), _inventory(successor=True))


@pytest.mark.parametrize(
    "fault",
    [
        "encoding",
        "sql_null_flip",
        "duplicate_old_row_entry",
        "old_layout",
        "old_oid",
        "sequence_advance",
        "sequence_drop",
        "role_state",
        "metadata_body",
        "metadata_oid",
        "unreviewed_added_object",
        "old_acl_grantee",
        "extra_old_acl",
        "missing_old_acl",
        "nonempty_new",
        "unexpected_table",
        "missing_new_table",
    ],
)
def test_retained_data_metadata_and_acl_faults_are_not_a_source_defined_delta(fault):
    before, after = _inventory(), copy.deepcopy(_inventory(successor=True))
    snapshot = after["snapshot"]
    if fault == "encoding":
        snapshot["row_encoding"] = "old_json_only"
    elif fault == "sql_null_flip":
        snapshot["rows"][0][2] = "b" * 64
    elif fault == "duplicate_old_row_entry":
        snapshot["rows"].append(snapshot["rows"][0])
    elif fault == "old_layout":
        snapshot["row_layouts"][1][3][0][3] = True
    elif fault == "old_oid":
        after["relations"] = (("alembic_version", 999, "r"), *after["relations"][1:])
    elif fault == "sequence_advance":
        snapshot["sequence_state"][0][3] += 1
    elif fault == "sequence_drop":
        snapshot["sequence_state"] = []
    elif fault == "role_state":
        snapshot["role_state_sha256"] = "0" * 64
    elif fault == "metadata_body":
        after["objects"] = (
            (*after["objects"][0][:3], "0" * 64, False),
            *after["objects"][1:],
        )
    elif fault == "metadata_oid":
        after["objects"] = (
            ("relation", 999, 0, "b" * 64, False),
            *after["objects"][1:],
        )
    elif fault == "unreviewed_added_object":
        after["objects"] += (("function", 999, 0, "a" * 64, False),)
    elif fault == "old_acl_grantee":
        after["acl"] = (("relation", 1, 999, 400, "SELECT", False), *after["acl"][1:])
    elif fault == "extra_old_acl":
        after["acl"] += (("relation", 1, 501, 400, "UPDATE", False),)
    elif fault == "missing_old_acl":
        after["acl"] = after["acl"][1:]
    elif fault == "nonempty_new":
        snapshot["rows"][1][1] = 1
    elif fault == "unexpected_table":
        snapshot["rows"].append(["foreign_rows", 0, subject._sha(b"")])
    elif fault == "missing_new_table":
        snapshot["rows"].pop()
    with pytest.raises(subject.NativeOwnerUpgradeRefusal):
        subject._preserved_v2(before, after)


@pytest.mark.parametrize("legacy", [False, True])
def test_private_v2_loader_requires_exact_explicit_role_fields(
    tmp_path, monkeypatch, legacy
):
    pins = _upgrade_pins()
    value = asdict(pins)
    value["schema"] = (
        "ctcc.native_owner_0056.reviewed_upgrade_inputs.v1"
        if legacy
        else "ctcc.native_owner_0056.reviewed_upgrade_inputs.v2"
    )
    raw = json.dumps(value).encode()
    path = tmp_path / "synthetic-pins"
    monkeypatch.setattr(subject, "_read_bounded_regular_file_v1", lambda *a: raw)
    monkeypatch.setattr(
        subject, "_require_positive_private_file_v2", lambda *args: None
    )
    if legacy:
        with pytest.raises(subject.NativeOwnerUpgradeRefusal):
            subject._load_upgrade_pins_v2(path, subject._sha(raw))
    else:
        assert subject._load_upgrade_pins_v2(path, subject._sha(raw)) == pins


@pytest.mark.asyncio
async def test_catalog_refusal_precedes_every_application_row_select(monkeypatch):
    from scripts import native_owner_upgrade_0057_v1 as shared

    async def refuse(*a, **k):
        subject._deny()

    monkeypatch.setattr(subject, "_catalog_v2", refuse)
    monkeypatch.setattr(
        shared, "_local", lambda *a: pytest.fail("application rows read")
    )

    class Connection:
        async def scalar(self, sql, *args):
            assert sql is subject._SAFE_MARKER_V2
            return True

        async def execute(self, sql):
            from tests.unit.test_native_owner_upgrade_0056_v1 import _row

            class Result:
                def one(self):
                    return _row(_upgrade_pins(), "0055", read_only="on")

            return Result()

    with pytest.raises(subject.NativeOwnerUpgradeRefusal):
        await subject._inventory_v2(
            Connection(), _upgrade_pins(), "0055", read_only=True
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["readback", "roles", "cleanup"])
async def test_committed_independent_failure_never_runs_a_second_upgrade(
    monkeypatch, failure
):
    pins = _upgrade_pins()
    route = SimpleNamespace(
        url="synthetic",
        manifest_sha256=pins.route.source_manifest_sha256,
        migration_sha256="f" * 64,
        route_sha256="1" * 64,
        source_head="0056",
    )
    prepared = (route, object(), object(), object())
    events = []
    monkeypatch.setattr(subject, "_prepared", lambda *a: prepared)
    monkeypatch.setattr(
        subject, "_require_positive_private_file_v2", lambda *args: None
    )
    monkeypatch.setattr(subject, "_checked_preflight", lambda *a: None)

    async def preflight(*a, **k):
        return {}

    monkeypatch.setattr(subject, "preflight_owner_route_0056_v1", preflight)

    async def resources(stack, *a):
        async def dispose():
            events.append("CLEANUP")
            if failure == "cleanup":
                raise RuntimeError("private cleanup canary")

        stack.push_async_callback(dispose)
        return object(), object()

    async def roles(*a):
        events.append("ROLES_" + a[-1])
        if failure == "roles" and a[-1] == "0056":
            raise RuntimeError("private role canary")
        return "same-original-roles"

    async def commit(*a):
        events.append("COMMIT_ONCE")
        return _inventory(successor=True)

    async def inventory(*a):
        return _inventory(successor=a[-1] == "0056")

    async def readback(*a):
        events.append("READBACK")
        if failure == "readback":
            raise RuntimeError("private readback canary")
        return {"database_head": "0056"}

    monkeypatch.setattr(subject, "_configured_resources_v2", resources)
    monkeypatch.setattr(subject, "_role_observation_v2", roles)
    monkeypatch.setattr(subject, "_commit_once", commit)
    monkeypatch.setattr(subject, "_readonly_inventory_v2", inventory)
    monkeypatch.setattr(subject, "_independent_readback", readback)

    @asynccontextmanager
    async def marker(*args):
        yield object()

    monkeypatch.setattr(subject, "_marker_checked_v2", marker)
    with pytest.raises(subject.NativeOwnerUpgradePostcommitIncomplete) as caught:
        await subject.upgrade_owner_0055_to_0056_v1(
            pins,
            owner_dsn_file=Path("private"),
            ca_file=Path("ca"),
            source_archive_file=Path("archive"),
        )
    assert events.count("COMMIT_ONCE") == 1 and events.count("READBACK") == 1
    assert events.count("CLEANUP") == 1 and "private" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [False, None, 1, "true"])
async def test_native_marker_metadata_must_pass_before_head_row_or_catalog_callback(
    value,
):
    class Connection:
        async def scalar(self, sql, *args):
            assert sql is subject._SAFE_MARKER_V2
            return value

        async def execute(self, *args):
            pytest.fail("head row read before native marker metadata")

    with pytest.raises(subject.NativeOwnerUpgradeRefusal):
        await subject._inventory_v2(
            Connection(), _upgrade_pins(), "0055", read_only=True
        )
