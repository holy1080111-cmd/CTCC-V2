"""Inert bootstrap input/source refusals; no server, private data or lease mint."""

import hashlib
import io
import json
import stat
import tarfile
from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts import retained_pg_bootstrap_custody_v1 as subject


def _locator():
    return {
        "schema": "ctcc.private_python.maintenance_vendor_locator.v1",
        "outer": "/root/declared-independent-vendor-fixture",
        "venv": "/root/declared-independent-vendor-fixture/runtime/venv",
        "tree_manifest": "/root/declared-independent-vendor-fixture/VENDOR_ENV_TREE_PRIVATE.json",
        "tree_sha256": subject.VENDOR_TREE_SHA256,
        "python_image_tree_sha256": subject.PYTHON_TREE_SHA256,
        "source_dependency_input_sha256": subject.DEPENDENCY_INPUT_SHA256,
        "lock_sha256": subject.LOCK_SHA256,
        "logical_executable": "/run/venv/bin/python",
        "logical_venv": "/run/venv",
        "vendor_count": 41,
        "project_installed": False,
        "consumer_must_verify_tree_and_remount_readonly": True,
        "saved_locator_is_maintenance_authority": False,
    }


def test_locator_address_is_data_and_never_its_historical_hash_authority():
    raw = subject._canonical(_locator())
    assert subject._sha(raw) != subject.VENDOR_LOCATOR_SHA256
    parsed, _venv, _manifest = subject._checked_provider_locator(raw)
    assert parsed == _locator()
    assert subject._RECORDS == {}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("saved_locator_is_maintenance_authority", True),
        ("consumer_must_verify_tree_and_remount_readonly", 1),
        ("vendor_count", True),
        ("vendor_count", 42),
        ("project_installed", 0),
        ("tree_sha256", "0" * 64),
        ("python_image_tree_sha256", "0" * 64),
        ("source_dependency_input_sha256", "0" * 64),
        ("lock_sha256", "0" * 64),
        ("logical_executable", "/usr/bin/python"),
        ("venv", "/root/other-runtime/venv"),
        ("tree_manifest", "/root/other-tree.json"),
        ("outer", "/root/../arbitrary"),
    ],
)
def test_locator_cannot_promote_pass_flags_alternate_code_or_conflicting_resource(
    field, value
):
    data = _locator()
    data[field] = value
    with pytest.raises(subject.RetainedBootstrapRefused):
        subject._checked_provider_locator(subject._canonical(data))
    assert subject._RECORDS == {}


def test_locator_extra_authority_field_is_refused():
    data = _locator() | {"verified": True}
    with pytest.raises(subject.RetainedBootstrapRefused):
        subject._checked_provider_locator(subject._canonical(data))


def _fresh_manifest():
    return {
        "schema": "ctcc.retained_pg.fresh_canonical17_cold.v1",
        "origin_nonce": "a" * 32,
        "source17_prefix_sha256": subject.SOURCE17_PREFIX_SHA256,
        "system_identifier_sha256": "b" * 64,
        "entries": [
            {"path": "", "mode": stat.S_IFDIR | 0o700},
            {"path": "global", "mode": stat.S_IFDIR | 0o700},
            {
                "path": "global/pg_control",
                "mode": stat.S_IFREG | 0o600,
                "size": 4,
                "sha256": subject._sha(b"data"),
            },
        ],
        "regular_bytes": 4,
    }


def test_fresh_manifest_cannot_fulfil_preserved_backup_origin():
    raw = subject._canonical(_fresh_manifest())
    result = subject._cold_manifest(raw, origin=subject._Origin.FRESH_FIXTURE)
    assert result[3] == subject._sha(subject.FRESH_FIXTURE_DOMAIN + raw)
    assert result[3] != subject._sha(subject.PRESERVED_DOMAIN + raw)
    with pytest.raises(subject.RetainedBootstrapRefused):
        subject._cold_manifest(raw, origin=subject._Origin.PRESERVED)
    assert subject._RECORDS == {}


@pytest.mark.parametrize(
    "change", ["duplicate", "unknown_parent", "total", "signal", "escape", "symlink"]
)
def test_manifest_refuses_ambiguous_or_unowned_storage_inventory(change):
    data = deepcopy(_fresh_manifest())
    if change == "duplicate":
        data["entries"].append(deepcopy(data["entries"][-1]))
    elif change == "unknown_parent":
        data["entries"][-1]["path"] = "unlisted/pg_control"
    elif change == "total":
        data["regular_bytes"] = 5
    elif change == "signal":
        data["entries"][-1]["path"] = "recovery.signal"
    elif change == "escape":
        data["entries"][-1]["path"] = "../pg_control"
    else:
        data["entries"][-1]["mode"] = stat.S_IFLNK | 0o600
    with pytest.raises(subject.RetainedBootstrapRefused):
        subject._cold_manifest(
            subject._canonical(data), origin=subject._Origin.FRESH_FIXTURE
        )


def test_live_directory_identity_preserves_inode_protection_without_pinning_mutable_directory_counters():
    base = SimpleNamespace(
        st_dev=1,
        st_ino=2,
        st_mode=stat.S_IFDIR | 0o700,
        st_uid=70,
        st_gid=70,
        st_nlink=2,
        st_mtime_ns=100,
    )
    live = SimpleNamespace(**{**vars(base), "st_nlink": 5, "st_mtime_ns": 200})
    assert subject._directory_identity(base) == subject._directory_identity(live)
    live.st_ino = 3
    assert subject._directory_identity(base) != subject._directory_identity(live)


def test_owned_lease_has_no_public_data_constructor():
    with pytest.raises(subject.RetainedBootstrapRefused, match="not_constructible"):
        subject.OwnedRetainedBootstrapV1()
    with pytest.raises(subject.RetainedBootstrapRefused):
        subject._record({"verified": True})
    assert subject._RECORDS == {}


def _archive():
    files = {
        "MANIFEST.sha256": b"declared fixture manifest\n",
        "scripts/example.py": b"VALUE = 1\n",
    }
    source = {
        "commit": "a" * 40,
        "tree": "",
        "archive": "",
        "manifest": subject._sha(files["MANIFEST.sha256"]),
    }

    def object_id(kind, raw):
        return hashlib.sha1(f"{kind} {len(raw)}\0".encode() + raw).digest()

    script_tree = object_id(
        "tree", b"100644 example.py\0" + object_id("blob", files["scripts/example.py"])
    )
    root_tree = object_id(
        "tree",
        b"100644 MANIFEST.sha256\0"
        + object_id("blob", files["MANIFEST.sha256"])
        + b"40000 scripts\0"
        + script_tree,
    )
    source["tree"] = root_tree.hex()
    stream = io.BytesIO()
    with tarfile.open(
        fileobj=stream,
        mode="w",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": source["commit"]},
    ) as archive:
        directory = tarfile.TarInfo("scripts/")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        archive.addfile(directory)
        for name, raw in files.items():
            item = tarfile.TarInfo(name)
            item.mode = 0o644
            item.size = len(raw)
            archive.addfile(item, io.BytesIO(raw))
    raw = stream.getvalue()
    source["archive"] = subject._sha(raw)
    return raw, source, files


def test_archive_reconstructs_exact_git_tree_and_rejects_rehashed_commit_tree_or_manifest_rebinding():
    raw, source, files = _archive()
    assert {
        name: value[0] for name, value in subject._archive_members(raw, source).items()
    } == files
    for key in ("commit", "tree", "archive", "manifest"):
        changed = source | {key: "0" * len(source[key])}
        with pytest.raises(subject.RetainedBootstrapRefused):
            subject._archive_members(raw, changed)


def test_unknown_record_cannot_acquire_write_or_resume_from_a_saved_receipt():
    wrapped = object.__new__(subject.OwnedRetainedBootstrapV1)
    with pytest.raises(subject.RetainedBootstrapRefused):
        subject._record(wrapped)
    assert json.loads(subject._canonical({"verified": True}))["verified"] is True
    assert subject._RECORDS == {}


def test_real_source_guard_installs_with_inert_registries_and_rejects_replaced_callback_before_execution(
    monkeypatch,
):
    from app.trade_qualification.native_continuation_guard_v1 import (
        NativeSourceClosureError,
    )

    assert subject._RECORDS == subject._FRESH_COLD_RECORDS == {}
    subject._install_owner_guard_v1()
    subject._NATIVE_GUARD()
    called = []

    def foreign_record(_value):
        called.append(True)
        raise AssertionError("foreign callback must not execute")

    with monkeypatch.context() as changed:
        changed.setattr(subject, "_record", foreign_record)
        with pytest.raises(NativeSourceClosureError):
            subject._owned_route_v1(object(), object())
    assert called == []
    with monkeypatch.context() as changed:
        changed.setattr(subject, "_NATIVE_GUARD", lambda: called.append(True))
        with pytest.raises(NativeSourceClosureError):
            subject._owned_route_v1(object(), object())
    assert called == []
    subject._NATIVE_GUARD()
