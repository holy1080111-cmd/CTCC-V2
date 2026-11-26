"""Synthetic version isolation and preserved default56 contracts; no authority."""

import copy
import shutil
from pathlib import Path

import pytest

from app.domain import collector_current_0056_inherited_catalog_v1 as inherited
from app.domain import native_database_profile_v1 as profiles
from scripts import current0055_native_lifecycle_acl_v1 as previous_acl
from tests.historical_source_prefix_v1 import trim_synthetic_migration_tree
from tests.unit.test_controlled_history_current55_schema_v2 import _catalog
from tests.unit.test_g12_restricted_writer_0057_v1 import _fixture


@pytest.mark.parametrize("value", ["0056", "0057", "0058", 57, None, True, {}])
def test_profile_requires_closed_source_enum(value):
    with pytest.raises(ValueError, match="native_database_profile_unsupported"):
        profiles.checked(value)


def test_default56_query_and_plan_remain_exact():
    names = ("demo_writer", "demo_custody", "demo_frame")
    inherited_plan, lifecycle = previous_acl._plans(*names)
    assert profiles.grant_plan(profiles.DB0056, names) == inherited_plan + lifecycle
    assert len(profiles.grant_plan(profiles.DB0056, names)) == 42
    assert inherited.actor_catalog_sql() == inherited.ACTOR_CATALOG_SQL
    assert profiles.arguments(profiles.DB0056) == {}


def test_successor_is_explicit44_and_captures_real_head_and_g12():
    names = ("demo_writer", "demo_custody", "demo_frame")
    previous = profiles.grant_plan(profiles.DB0056, names)
    successor = profiles.grant_plan(profiles.DB0057, names)
    assert successor[:42] == previous and len(successor) == 44
    assert all(
        item.rights == ("SELECT", "INSERT") and item.login == names[0]
        for item in successor[42:]
    )
    query = inherited.actor_catalog_sql(profiles.DB0057)
    assert "'schema','ctcc.collector_current0057.actual_inherited_catalog.v1'" in query
    assert "'g12_0057'" in query
    assert "FROM public.alembic_version" in query
    assert profiles.arguments(profiles.DB0057) == {"profile": profiles.DB0057}
    assert "0057" not in inherited.ACTOR_CATALOG_SQL


@pytest.mark.parametrize("reader", [False, True])
def test_history_accepts_only_actual_requested_profile_without_projection(reader):
    value = _catalog(reader=reader)
    value["head"] = "0057"
    original = copy.deepcopy(value)
    kwargs = {
        "owner": 101,
        "actor": 301 if reader else 201,
        "owner_mode": False,
        "reader_role_oid": 301 if reader else None,
    }
    with pytest.raises(ValueError):
        inherited.observe_controlled_history_0056(value, **kwargs)
    accepted = inherited.observe_controlled_history_0056(
        value, profile=profiles.DB0057, **kwargs
    )
    assert accepted["writer_role_oid"] == 201 and accepted["raw_role_oid"] == 301
    assert accepted["execution_authority"] is False and value == original
    value["head"] = "0056"
    with pytest.raises(ValueError):
        inherited.observe_controlled_history_0056(
            value, profile=profiles.DB0057, **kwargs
        )


@pytest.mark.parametrize("read_only", ["on", "off"])
def test_context_checks_physical57_without_relabeling_input(read_only):
    from tests.unit.test_native_role_preflight_0056_full_diagnostic_v1 import (
        _context,
        _role,
    )

    role = _role("writer", 201)
    schema = "ctcc.collector_current0054.actual_issued_catalog.v1"
    value = _context(role, schema=schema, read_only=read_only, head="0057")
    original = copy.deepcopy(value)
    kwargs = {
        "role": role,
        "read_only": read_only,
        "isolation": "read committed",
        "parents": (),
        "schema_name": schema,
    }
    with pytest.raises(ValueError):
        inherited._context(value, **kwargs)
    assert inherited._context(value, profile=profiles.DB0057, **kwargs) == {}
    assert value == original


def test_successor_migration_inventory_supports_exact_source58_and_proven_ancestors():
    root = Path(__file__).resolve().parents[2]
    profiles.require_migration(profiles.DB0056, root)
    profiles.require_migration(profiles.DB0057, root)
    profiles.require_migration(profiles.DB0058, root)


@pytest.fixture
def migration_copy(tmp_path):
    root = Path(__file__).resolve().parents[2]
    shutil.copyfile(root / "alembic.ini", tmp_path / "alembic.ini")
    shutil.copytree(
        root / "migrations",
        tmp_path / "migrations",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return tmp_path


def test_source56_prefix_remains_supported_without_successor_promotion(migration_copy):
    trim_synthetic_migration_tree(
        migration_copy, source_root=Path(__file__).resolve().parents[2], target="0056"
    )
    profiles.require_migration(profiles.DB0056, migration_copy)
    with pytest.raises(ValueError):
        profiles.require_migration(profiles.DB0057, migration_copy)


@pytest.mark.parametrize("fault", ["old_prefix", "successor_bytes", "extra_revision"])
def test_migration_dispatch_refuses_changed_prefix_successor_or_unknown_head(
    migration_copy, fault
):
    if fault == "extra_revision":
        (migration_copy / "migrations/versions/0059_unreviewed.py").write_text(
            'revision="0059"\ndown_revision="0058"\n', encoding="utf-8"
        )
    else:
        name = (
            "0056_gate3_fitted_candidate_custody.py"
            if fault == "old_prefix"
            else "0057_g12_restricted_writer.py"
        )
        path = migration_copy / "migrations/versions" / name
        path.write_bytes(path.read_bytes() + b"\n")
    for profile in (profiles.DB0056, profiles.DB0057):
        with pytest.raises(ValueError):
            profiles.require_migration(profile, migration_copy)


def test_g12_definition_reference_is_separate_from_actual_oid_witness():
    from app.domain import g12_custody_catalog_0057_v1 as catalog

    original = _fixture()
    changed = copy.deepcopy(original)
    changed["tables"][0]["oid"] += 1000
    changed["tables"][1]["oid"] += 1000
    for function in changed["functions"]["functions"]:
        function["oid"] += 1000
    for ri in changed["ri"]:
        ri["oid"] += 1000
        ri["constraint_oid"] += 1000
    before = catalog.observe_catalog_0057(original, owner=101, writer=201)
    after = catalog.observe_catalog_0057(changed, owner=101, writer=201)
    assert before["definitions_sha256"] == after["definitions_sha256"]
    assert before["catalog_sha256"] != after["catalog_sha256"]
    assert after["execution_authority"] is after["startup_admission"] is False


def test_runtime_and_stable_witnesses_keep_profile_identity_distinct():
    from app.core import native_role_identity_0056_v2 as identity
    from app.core import native_role_witness_0056_v1 as witness
    from tests.unit.test_native_role_identity_0056_v2 import _identity

    names = ("writer", "custody", "frame")
    route = ("postgresql+asyncpg", "synthetic.invalid", 5432, "synthetic")
    actor = _identity(names[0], 201)
    assert witness.runtime_witness_0056(
        actor, names, (route,) * 3
    ) != witness.runtime_witness_0056(
        actor, names, (route,) * 3, profile=profiles.DB0057
    )
    roles = (201, 202, 203)
    assert identity._static_identity_sha256(
        93, 101, names, roles, route
    ) != identity._static_identity_sha256(
        93, 101, names, roles, route, profile=profiles.DB0057
    )


def test_acl_shape_uses_exact_source_profile_without_promoting_admission():
    from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles

    names = ("writer", "custody", "frame")
    original = roles._granted_inherited_acl_shape_sha256(names, "a" * 64)
    successor = roles._granted_inherited_acl_shape_sha256(
        names, "a" * 64, profile=profiles.DB0057
    )
    assert original != successor
    assert successor == roles._granted_inherited_acl_shape_sha256(
        ("writer2", "custody2", "frame2"), "a" * 64, profile=profiles.DB0057
    )
