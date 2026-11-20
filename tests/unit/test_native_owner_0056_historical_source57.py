"""Real source graph preparation with synthetic route data; no SQL or grants."""

from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from shutil import copy2, copytree
from types import SimpleNamespace

import pytest

from scripts import current_contract_source_scopes_v1 as scopes
from scripts import manifest
from scripts import native_owner_upgrade_0056_v1 as upgrade
from tests.historical_source_prefix_v1 import trim_synthetic_migration_tree
from tests.unit.test_native_owner_upgrade_0056_v1 import _pins, _upgrade_pins


@pytest.mark.parametrize("head", ["0056", "0057", "0058"])
def test_upgrade_preparation_keeps_fixed_database55_to56_under_real_source_graph(
    tmp_path, monkeypatch, head
):
    source = tmp_path / "source"
    source.mkdir()
    copy2(upgrade._SOURCE / "alembic.ini", source / "alembic.ini")
    copytree(
        upgrade._SOURCE / "migrations",
        source / "migrations",
        ignore=lambda _root, names: {"__pycache__"} & set(names),
    )
    trim_synthetic_migration_tree(source, source_root=upgrade._SOURCE, target=head)
    manifest.write_manifest(source, source / "MANIFEST.sha256")
    current = scopes.actual_current_source_scope(source)
    route = SimpleNamespace(
        source_head=head,
        manifest_sha256=current.manifest_sha256,
        migration_sha256=current.actual_migration_sha256,
    )
    monkeypatch.setattr(upgrade, "_SOURCE", source)
    monkeypatch.setattr(upgrade, "prepare_route", lambda *_a, **_k: route)
    archive_checked = []
    monkeypatch.setattr(
        upgrade, "_verify_reviewed_archive", lambda *args: archive_checked.append(args)
    )
    monkeypatch.setattr(
        upgrade,
        "create_async_engine",
        lambda *_a, **_k: pytest.fail("prepare reached a database"),
    )
    pins = _pins(current.manifest_sha256)
    prepared = upgrade._prepared(
        pins,
        Path("synthetic-private-dsn"),
        Path("synthetic-ca"),
        Path("synthetic-archive"),
    )
    assert prepared[0] is route and len(archive_checked) == 1
    assert prepared[1].actual_source_head == prepared[2].actual_source_head == head
    assert (
        prepared[1].database_target == "0055" and prepared[2].database_target == "0056"
    )
    assert tuple(prepared[3].get_heads()) == (head,)
    assert prepared[2].historical_prefix_sha256 == scopes._PREFIX["0056"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("head", ["0056", "0057", "0058"])
async def test_upgrade_receipt_does_not_project_actual57_source_as56(monkeypatch, head):
    pins = _upgrade_pins()
    route = SimpleNamespace(
        source_head=head,
        url="DECLARED_SYNTHETIC",
        manifest_sha256=pins.route.source_manifest_sha256,
        migration_sha256="f" * 64,
        route_sha256="1" * 64,
    )
    after = SimpleNamespace(historical_prefix_sha256=scopes._PREFIX["0056"][0])
    prepared = (route, object(), after, object())
    monkeypatch.setattr(upgrade, "_prepared", lambda *_: prepared)
    monkeypatch.setattr(
        upgrade, "_require_positive_private_file_v2", lambda *args: None
    )
    calls = []

    async def read(*_a, **_kw):
        return {
            "schema": "ctcc.native_owner_0056.route_preflight.v1",
            "status": "PASS",
            "source_head": head,
            "source_manifest_sha256": route.manifest_sha256,
            "migration_sha256": route.migration_sha256,
            "database_head": "0055",
            "route_sha256": route.route_sha256,
            "cluster_system_identifier_sha256": pins.route.cluster_system_identifier_sha256,
            "ca_sha256": pins.route.ca_sha256,
            "independent_readonly_observations": 2,
            "database_owner_verified": True,
            "schema_drift_verified": False,
            "owner_provision_verified": False,
            "startup_admission": False,
            "execution_authority": False,
        }

    async def commit(*_):
        calls.append("synthetic_commit_callback_only")
        return {"snapshot": {"row_encoding": upgrade.retained_rows.ROW_ENCODING}}

    async def readback(*_):
        return {"database_head": "0056"}

    async def resources(*_):
        return object(), object()

    async def roles(*_):
        return "same-synthetic-role-identities"

    async def inventory(*_):
        return {"snapshot": {"row_encoding": upgrade.retained_rows.ROW_ENCODING}}

    monkeypatch.setattr(upgrade, "preflight_owner_route_0056_v1", read)
    monkeypatch.setattr(upgrade, "_commit_once", commit)
    monkeypatch.setattr(upgrade, "_independent_readback", readback)
    monkeypatch.setattr(upgrade, "_configured_resources_v2", resources)
    monkeypatch.setattr(upgrade, "_role_observation_v2", roles)
    monkeypatch.setattr(upgrade, "_readonly_inventory_v2", inventory)

    @asynccontextmanager
    async def marker(*args):
        yield object()

    monkeypatch.setattr(upgrade, "_marker_checked_v2", marker)
    result = await upgrade.upgrade_owner_0055_to_0056_v1(
        pins,
        owner_dsn_file=Path("dsn"),
        ca_file=Path("ca"),
        source_archive_file=Path("source"),
    )
    assert calls == ["synthetic_commit_callback_only"]
    assert (
        result["database_head_before"] == "0055"
        and result["database_head_after"] == "0056"
    )
    assert result["grants_installed"] is result["execution_authority"] is False
    assert result["schema_drift_verified"] is False
    assert result["native_profile_catalog_verified"] is True
    assert result["row_encoding"] == upgrade.retained_rows.ROW_ENCODING
    if head in {"0057", "0058"}:
        assert result["source_head"] == head and result["database_target"] == "0056"
        assert result["database_migration_prefix_sha256"] == scopes._PREFIX["0056"][0]
    else:
        assert "source_head" not in result and "database_target" not in result


def test_preflight_cannot_relabel_actual57_route_as56_source():
    pins = _pins()
    route = SimpleNamespace(
        source_head="0057",
        manifest_sha256="a" * 64,
        migration_sha256="b" * 64,
        route_sha256="c" * 64,
    )
    with pytest.raises(upgrade.NativeOwnerUpgradeRefusal):
        upgrade._checked_preflight(
            {
                "schema": "ctcc.native_owner_0056.route_preflight.v1",
                "status": "PASS",
                "source_head": "0056",
            },
            route,
            replace(pins.route, database_head="0056"),
        )
