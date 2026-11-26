"""Actual disposable TLS55→56 preservation; no host DB/venue acceptance.

The fixture's synthetic source identity fields are DATA for the internal held
SQL transaction. The public CLI/archive/private-source boundary is exercised
by the existing non-superuser owner test in the fitted-custody module.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncTransaction

from app.domain import collector_current_0055_catalog_v1 as current55
from app.domain import collector_current_0056_inherited_catalog_v1 as current56
from scripts import current_contract_source_scopes_v1 as scopes
from scripts import native_owner_granted_catalog_v1 as granted
from scripts import native_owner_upgrade_0056_v1 as subject
from scripts import native_owner_upgrade_0057_v1 as owner57
from scripts import retained0056_unscoped_fitted_observation_v1 as unscoped
from tests.integration import test_native_owner_upgrade_0057_pg as owner57_lab

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_actual57_lab = owner57_lab._actual57_lab
_actual56_owner_profile_lab = owner57_lab._actual56_owner_profile_lab


@pytest.fixture
async def _actual55_v2_lab(_actual56_owner_profile_lab):
    owner, selected, old, script = _actual56_owner_profile_lab

    def downgrade(connection):
        migration = MigrationContext.configure(
            connection,
            opts={"fn": lambda heads, _: script._downgrade_revs("0055", heads)},
        )
        assert migration.get_current_heads() == ("0056",)
        with Operations.context(migration), migration.begin_transaction():
            migration.run_migrations()
        assert migration.get_current_heads() == ("0055",)

    async with owner.begin() as connection:
        await connection.run_sync(downgrade)
    pins = subject.OwnerUpgradePinsV2(
        old.source_commit,
        old.source_tree,
        old.source_archive_sha256,
        replace(old.route, database_head="0055"),
        old.role_names,
        old.role_oids,
    )
    before = scopes.database0055_source_scope(subject._SOURCE)
    after = scopes.database0056_source_scope(subject._SOURCE)
    prepared = (SimpleNamespace(url=owner.url), before, after, script)
    yield owner, selected, pins, prepared


async def _assert_exact_catalog_isolation(
    owner, pins, head, catalog_sql, default_observer
):
    async with subject._marker_checked_v2(owner, pins) as connection:
        document = await connection.scalar(text(catalog_sql))
        for section in ("issued", "lineage"):
            assert document[section]["server"]["isolation"] == "repeatable read"
            assert document[section]["server"]["read_only"] == "on"
        with pytest.raises(current55.Current0055CatalogError):
            default_observer(document)
        held = deepcopy(document)
        arguments = {
            "head": head,
            "read_only": True,
            "owner_name": pins.route.owner,
            "owner_oid": pins.route.owner_oid,
            "role_names": pins.role_names,
            "role_oids": dict(zip(pins.role_names, pins.role_oids, strict=True)),
        }
        assert granted.observe_granted_owner_structure(document, **arguments) is None
        assert document == held
        with pytest.raises(granted.GrantedOwnerCatalogError):
            granted.observe_granted_owner_structure(
                document, **{**arguments, "read_only": False}
            )

        changed = deepcopy(document)
        table = changed["native_configuration55"]["tables"][0]
        grant = next(row for row in table["acl"] if row[0] == pins.role_oids[0])
        grant[3] = True
        with pytest.raises(granted.GrantedOwnerCatalogError):
            granted.observe_granted_owner_structure(changed, **arguments)

        changed = deepcopy(document)
        changed["controlled_history55"]["functions"][0]["can_execute"] = False
        with pytest.raises(granted.GrantedOwnerCatalogError):
            granted.observe_granted_owner_structure(changed, **arguments)

        changed = deepcopy(document)
        assert len(changed["controlled_history55"]["roles"]) == 2
        changed["controlled_history55"]["roles"].pop()
        with pytest.raises(granted.GrantedOwnerCatalogError):
            granted.observe_granted_owner_structure(changed, **arguments)

        changed = deepcopy(document)
        changed["issued"]["bad_table_acl"] = False
        with pytest.raises(granted.GrantedOwnerCatalogError):
            granted.observe_granted_owner_structure(changed, **arguments)

    async with owner.connect() as connection, connection.begin():
        for setting in subject._OWNER_SETTINGS:
            await connection.exec_driver_sql(setting)
        document = await connection.scalar(text(catalog_sql))
        for section in ("issued", "lineage"):
            assert document[section]["server"]["isolation"] == "read committed"
            assert document[section]["server"]["read_only"] == "off"
        original = deepcopy(document)
        assert (
            granted.observe_granted_owner_structure(
                document, **{**arguments, "read_only": False}
            )
            is None
        )
        assert document == original
        with pytest.raises(granted.GrantedOwnerCatalogError):
            granted.observe_granted_owner_structure(document, **arguments)


async def test_actual0056_unscoped_fitted_catalog_rejects_permissive_acl(
    _actual56_owner_profile_lab,
):
    owner, _selected, pins, _script = _actual56_owner_profile_lab

    async def observe():
        async with subject._marker_checked_v2(owner, pins) as connection:
            return await unscoped.observe_unscoped_fitted_catalog_0056_v1(
                connection,
                source=subject._SOURCE,
                owner=pins.route.owner,
                owner_oid=pins.route.owner_oid,
            )

    observed = await observe()
    assert observed.execution_authority is False
    assert await observe() == observed
    reader = f'"{pins.role_names[2]}"'
    trials = (
        (
            "GRANT EXECUTE ON FUNCTION public.gate3_fitted_binding_read(text) TO PUBLIC",
            "REVOKE EXECUTE ON FUNCTION public.gate3_fitted_binding_read(text) FROM PUBLIC",
            "unscoped_fitted_direct_acl",
        ),
        (
            f"GRANT SELECT(seal_sha256) ON public.gate3_fitted_candidate_bindings TO {reader}",
            f"REVOKE SELECT(seal_sha256) ON public.gate3_fitted_candidate_bindings FROM {reader}",
            "unscoped_fitted_effective_acl_or_storage",
        ),
        (
            f"GRANT pg_read_all_data TO {reader} WITH INHERIT FALSE",
            f"REVOKE pg_read_all_data FROM {reader}",
            "unscoped_fitted_effective_acl_or_storage",
        ),
    )
    for grant, revoke, expected in trials:
        async with owner.begin() as connection:
            await connection.exec_driver_sql(grant)
        try:
            with pytest.raises(subject.fitted56.CatalogRefusal, match=expected):
                await observe()
        finally:
            async with owner.begin() as connection:
                await connection.exec_driver_sql(revoke)
        assert await observe() == observed


async def test_actual0055_v2_rehearsal_rollback_then_commit_preserves_all_rows_and42_grants(
    _actual55_v2_lab,
):
    owner, selected, pins, prepared = _actual55_v2_lab
    actors = await subject._role_observation_v2(selected, owner, pins, "0055")
    await _assert_exact_catalog_isolation(
        owner,
        pins,
        "0055",
        current55.OWNER_CATALOG_SQL,
        current55.observe_actual_owner_catalog,
    )
    before = await subject._readonly_inventory_v2(owner, pins, "0055")
    final = await subject._commit_once(prepared, pins, owner, before)
    subject._preserved_v2(before, final)
    await _assert_exact_catalog_isolation(
        owner,
        pins,
        "0056",
        current56.OWNER_CATALOG_SQL,
        current56.observe_owner_installation_0056,
    )
    for read_only, settings, expected in (
        (True, subject._READBACK_SETTINGS, ("repeatable read", "on")),
        (False, subject._OWNER_SETTINGS, ("read committed", "off")),
    ):
        async with owner.connect() as connection, connection.begin():
            for setting in settings:
                await connection.exec_driver_sql(setting)
            fitted = await subject._read_fitted_owner_catalog_v2(
                connection, read_only=read_only
            )
            assert fitted["search_path"] == "pg_catalog, pg_temp"
            assert tuple(
                (await connection.execute(subject._CATALOG_CONTEXT)).one()
            ) == (
                "pg_catalog, public, pg_temp",
                *expected,
            )
    assert await subject._readonly_inventory_v2(owner, pins, "0056") == final
    assert await subject._role_observation_v2(selected, owner, pins, "0056") == actors
    assert len(final["relations"]) - len(before["relations"]) == 3
    assert final["snapshot"]["sequence_state"] == before["snapshot"]["sequence_state"]
    with pytest.raises(subject.NativeOwnerUpgradeRefusal):
        await subject._commit_once(prepared, pins, owner, before)
    assert await subject._readonly_inventory_v2(owner, pins, "0056") == final


async def test_actual0055_v2_extra_lineage_acl_refuses_before_maintenance_row_reads(
    _actual55_v2_lab, monkeypatch
):
    owner, _selected, pins, _prepared = _actual55_v2_lab

    async def unexpected_rows(*_args):
        pytest.fail("refused ACL unexpectedly reached the retained row scan")

    async with owner.connect() as connection, connection.begin():
        for setting in subject._OWNER_SETTINGS:
            await connection.exec_driver_sql(setting)
        await subject._catalog_v2(connection, pins, "0055", read_only=False)
        trial = await connection.begin_nested()
        try:
            await connection.exec_driver_sql(
                "GRANT SELECT ON public.demo_collector_current_revision_lineages TO PUBLIC"
            )
            with monkeypatch.context() as guard:
                guard.setattr(owner57, "_snapshot", unexpected_rows)
                with pytest.raises(
                    ValueError,
                    match="^current0055_core_grant_scope_or_catalog_changed$",
                ):
                    await subject._inventory_v2(
                        connection, pins, "0055", read_only=False
                    )
        finally:
            await trial.rollback()
        await subject._catalog_v2(connection, pins, "0055", read_only=False)


@pytest.mark.parametrize("phase", [1, 2])
async def test_actual0055_v2_rehearsal_or_final_exception_rolls_back_catalog_rows_and_acl(
    _actual55_v2_lab, monkeypatch, phase
):
    owner, selected, pins, prepared = _actual55_v2_lab
    before = await subject._readonly_inventory_v2(owner, pins, "0055")
    original = subject._upgrade_exact_0055
    calls = []

    def fail(connection, *args):
        original(connection, *args)
        calls.append(1)
        if len(calls) == phase:
            raise RuntimeError("declared isolated transaction fault")

    monkeypatch.setattr(subject, "_upgrade_exact_0055", fail)
    with pytest.raises(subject.NativeOwnerUpgradeRefusal):
        await subject._commit_once(prepared, pins, owner, before)
    assert len(calls) == phase
    assert await subject._readonly_inventory_v2(owner, pins, "0055") == before
    await subject._role_observation_v2(selected, owner, pins, "0055")


@pytest.mark.parametrize("phase", [1, 2])
async def test_actual0055_v2_null_bitmap_detects_json_only_zero_delta_and_restores_original(
    _actual55_v2_lab, monkeypatch, phase
):
    owner, _selected, pins, prepared = _actual55_v2_lab
    audit_id = uuid4()
    async with owner.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO public.audit_logs(id,actor,action,resource_type,resource_id,\"before\") VALUES(:id,'declared_lab','null_flip','synthetic','owner55-row',NULL)"
            ),
            {"id": audit_id},
        )
    before = await subject._readonly_inventory_v2(owner, pins, "0055")
    original = subject._upgrade_exact_0055
    calls = []
    equal = []

    def alter(connection, *args):
        original(connection, *args)
        calls.append(1)
        if len(calls) == phase:
            old = connection.scalar(
                text(
                    "SELECT pg_catalog.to_jsonb(r)::pg_catalog.text FROM public.audit_logs r WHERE id=:id"
                ),
                {"id": audit_id},
            )
            connection.execute(
                text(
                    "UPDATE public.audit_logs SET \"before\"='null'::pg_catalog.jsonb WHERE id=:id"
                ),
                {"id": audit_id},
            )
            equal.append(
                connection.scalar(
                    text(
                        "SELECT pg_catalog.to_jsonb(r)::pg_catalog.text FROM public.audit_logs r WHERE id=:id"
                    ),
                    {"id": audit_id},
                )
                == old
            )

    monkeypatch.setattr(subject, "_upgrade_exact_0055", alter)
    with pytest.raises(subject.NativeOwnerUpgradeRefusal):
        await subject._commit_once(prepared, pins, owner, before)
    assert equal == [True] and len(calls) == phase
    assert await subject._readonly_inventory_v2(owner, pins, "0055") == before


async def test_actual0055_v2_original_metadata_drift_is_not_hidden_by_correct_new_fitted_schema(
    _actual55_v2_lab, monkeypatch
):
    owner, _selected, pins, prepared = _actual55_v2_lab
    before = await subject._readonly_inventory_v2(owner, pins, "0055")
    original = subject._upgrade_exact_0055
    altered = []

    def alter(connection, *args):
        original(connection, *args)
        connection.exec_driver_sql(
            "ALTER TABLE public.audit_logs ALTER COLUMN \"before\" SET DEFAULT 'null'::pg_catalog.jsonb"
        )
        altered.append(True)

    monkeypatch.setattr(subject, "_upgrade_exact_0055", alter)
    with pytest.raises(subject.NativeOwnerUpgradeRefusal):
        await subject._commit_once(prepared, pins, owner, before)
    assert altered == [True]
    assert await subject._readonly_inventory_v2(owner, pins, "0055") == before


async def test_actual0055_v2_wrong_reviewed_role_oid_refuses_without_ddl(
    _actual55_v2_lab,
):
    owner, _selected, pins, prepared = _actual55_v2_lab
    before = await subject._readonly_inventory_v2(owner, pins, "0055")
    changed = replace(
        pins,
        role_oids=(pins.role_oids[0] + 100000, pins.role_oids[1], pins.role_oids[2]),
    )
    with pytest.raises(subject.NativeOwnerUpgradeRefusal):
        await subject._commit_once(prepared, changed, owner, before)
    assert await subject._readonly_inventory_v2(owner, pins, "0055") == before


async def test_actual0055_v2_commit_response_loss_is_unknown_and_never_reapplies_or_erases_head(
    _actual55_v2_lab, monkeypatch
):
    owner, selected, pins, prepared = _actual55_v2_lab
    before = await subject._readonly_inventory_v2(owner, pins, "0055")
    commit = AsyncTransaction.commit
    calls = []

    async def uncertain(transaction):
        await commit(transaction)
        calls.append(True)
        raise RuntimeError("declared actual commit reply loss")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncTransaction, "commit", uncertain)
        with pytest.raises(subject.NativeOwnerUpgradeCommitUnknown):
            await subject._commit_once(prepared, pins, owner, before)
    assert calls == [True]
    final = await subject._readonly_inventory_v2(owner, pins, "0056")
    subject._preserved_v2(before, final)
    await subject._role_observation_v2(selected, owner, pins, "0056")


async def test_actual0055_v2_marker_view_refuses_before_its_select_callback(
    _actual55_v2_lab,
):
    owner, _selected, pins, _prepared = _actual55_v2_lab
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            "ALTER TABLE public.alembic_version RENAME TO retained_marker_original"
        )
        await connection.exec_driver_sql("""CREATE FUNCTION public.retained_owner55_marker_canary()
 RETURNS varchar LANGUAGE plpgsql VOLATILE AS $$ BEGIN
 RAISE EXCEPTION 'declared_owner55_head_callback'; END $$""")
        await connection.exec_driver_sql(
            "CREATE VIEW public.alembic_version AS SELECT public.retained_owner55_marker_canary() AS version_num"
        )
    # Exact error class distinguishes the catalog refusal from a propagated
    # SQL/canary callback. The readonly inventory must not SELECT the view.
    with pytest.raises(subject.NativeOwnerUpgradeRefusal):
        await subject._readonly_inventory_v2(owner, pins, "0055")


async def test_actual0055_v2_and_commonroute_custom_marker_am_refuse_before_head_reads(
    _actual55_v2_lab,
):
    import re

    from sqlalchemy import event

    from scripts import native_owner_heap_am_v1 as heap

    owner, _selected, pins, _prepared = _actual55_v2_lab
    name = "ctcc_unreviewed_marker_" + uuid4().hex[:12]
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            'CREATE ACCESS METHOD "'
            + name
            + '" TYPE TABLE HANDLER pg_catalog.heap_tableam_handler'
        )
        await connection.exec_driver_sql(
            'ALTER TABLE public.alembic_version SET ACCESS METHOD "' + name + '"'
        )
    opened = []

    def watch(conn, cursor, statement, parameters, context, executemany):
        if (
            re.search(r"\bFROM\s+public\.", statement, re.IGNORECASE)
            or "pg_relation_size(" in statement
        ):
            opened.append(True)

    event.listen(owner.sync_engine, "before_cursor_execute", watch)
    try:
        with pytest.raises(subject.NativeOwnerUpgradeRefusal):
            await subject._readonly_inventory_v2(owner, pins, "0055")
        from scripts import native_owner_route_preflight_0056_v1 as route

        with pytest.raises(heap.NativeOwnerStorageRefused):
            await route._read_one(owner, pins.route)
    finally:
        event.remove(owner.sync_engine, "before_cursor_execute", watch)
    assert opened == []


async def test_actual0055_diagnose_first_granted_owner_preview_failure(
    _actual55_v2_lab, monkeypatch, record_property
):
    """Report the new first fixed gate on a disposable TLS owner, with no raw data."""
    import inspect
    import json
    from collections import Counter
    from pathlib import Path

    owner, _selected, pins, prepared = _actual55_v2_lab
    expected = await subject._readonly_inventory_v2(owner, pins, "0055")
    trace = []
    refusal_lines = []
    object_result = {}
    original_inventory = subject._inventory_v2
    original_preserved = subject._preserved_v2
    original_apply = subject._upgrade_exact_0055
    original_need = subject._need
    modules = {
        "native_owner_upgrade_0056_v1.py",
        "native_owner_granted_catalog_v1.py",
        "collector_current_0055_catalog_v1.py",
        "collector_current_0056_inherited_catalog_v1.py",
        "current0056_fitted_delta_catalog_v1.py",
        "native_owner_retained_rows_v2.py",
    }

    def failure(stage, exc):
        locations = []
        item = exc.__traceback__
        while item is not None:
            basename = Path(item.tb_frame.f_code.co_filename).name
            if basename in modules:
                locations.append((basename, item.tb_lineno))
            item = item.tb_next
        trace.append((stage, "failed", type(exc).__name__, locations[-6:]))

    def need(condition):
        if condition is not True:
            frame = inspect.currentframe().f_back
            if frame.f_code.co_filename == subject.__file__:
                refusal_lines.append(frame.f_lineno)
        original_need(condition)

    def apply(connection, *args):
        trace.append(("ddl", "started"))
        try:
            result = original_apply(connection, *args)
        except Exception as exc:
            failure("ddl", exc)
            raise
        trace.append(("ddl", "completed"))
        return result

    async def inventory(connection, read_pins, head, *, read_only):
        stage = "inventory_" + head
        trace.append((stage, "started"))
        try:
            result = await original_inventory(
                connection, read_pins, head, read_only=read_only
            )
        except Exception as exc:
            failure(stage, exc)
            raise
        trace.append((stage, "completed"))
        if head == "0056":
            old = {row[:3]: row[3] for row in expected["objects"]}
            unreviewed = [
                row
                for row in result["objects"]
                if row[:3] not in old and row[4] is not True
            ]
            changed = [
                row
                for row in result["objects"]
                if row[:3] in old and row[3] != old[row[:3]]
            ]
            attrs = [(row[1], row[2]) for row in unreviewed if row[0] == "attribute"]
            indexes = {rel for rel, _attnum in attrs}
            linked = (
                await connection.execute(
                    text("""
                    SELECT i.indexrelid::bigint, a.attnum::integer
                    FROM pg_catalog.pg_index i
                    JOIN pg_catalog.pg_class c ON c.oid=i.indrelid
                    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indexrelid
                    WHERE i.indexrelid::bigint=ANY(CAST(:indexes AS bigint[]))
                      AND n.nspname='public' AND c.relkind='r'
                      AND c.relname=ANY(CAST(:added AS text[]))
                      AND a.attnum=ANY(CAST(:attnums AS smallint[]))
                      AND a.attnum>0 AND NOT a.attisdropped
                    """),
                    {
                        "indexes": sorted(indexes),
                        "added": sorted(subject._ADDED_TABLES),
                        "attnums": sorted({number for _, number in attrs}),
                    },
                )
            ).all()
            linked_pairs = {tuple(row) for row in linked}
            linked_relations = {row[0] for row in linked_pairs}
            object_result.update(
                {
                    "new_object_kinds_refused": dict(
                        Counter(row[0] for row in unreviewed)
                    ),
                    "prior_object_kinds_changed": dict(
                        Counter(row[0] for row in changed)
                    ),
                    "index_attributes_of_added_table": sum(
                        item in linked_pairs for item in attrs
                    ),
                    "unclassified_index_relations": len(indexes - linked_relations),
                }
            )
        return result

    def preserved(before, after):
        trace.append(("preserved", "started"))
        try:
            result = original_preserved(before, after)
        except Exception as exc:
            failure("preserved", exc)
            raise
        trace.append(("preserved", "completed"))
        return result

    with monkeypatch.context() as wrappers:
        wrappers.setattr(subject, "_need", need)
        wrappers.setattr(subject, "_upgrade_exact_0055", apply)
        wrappers.setattr(subject, "_inventory_v2", inventory)
        wrappers.setattr(subject, "_preserved_v2", preserved)
        with pytest.raises(subject.NativeOwnerUpgradeRefusal):
            await subject._commit_once(prepared, pins, owner, expected)
    assert await subject._readonly_inventory_v2(owner, pins, "0055") == expected
    assert trace and (refusal_lines or any(row[1] == "failed" for row in trace))
    record_property(
        "original_preview_redacted_observation",
        json.dumps(
            {
                "stage_trace": trace,
                "owner_refusal_source_lines": refusal_lines,
                "object_delta_counts": object_result,
                "complete_rollback_actual_readback": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
