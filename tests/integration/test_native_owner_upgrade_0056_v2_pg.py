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
    async with owner.connect() as connection, connection.begin():
        for setting in subject._OWNER_SETTINGS:
            await connection.exec_driver_sql(setting)
        trial = await connection.begin_nested()
        try:
            # Keep the expected fitted name and unique constraint kind but
            # bind it to the wrong actual column. Only the sanctioned key's
            # attribute can belong to the migration delta.
            await connection.exec_driver_sql(
                "ALTER TABLE public.gate3_fitted_candidate_bindings "
                "DROP CONSTRAINT uq_gate3_fitted_candidate_bindings_binding_sha256, "
                "ADD CONSTRAINT uq_gate3_fitted_candidate_bindings_binding_sha256 "
                "UNIQUE(seal_sha256)"
            )
            wrong_index = await connection.scalar(
                text(
                    "SELECT 'public.uq_gate3_fitted_candidate_bindings_binding_sha256'"
                    "::pg_catalog.regclass::pg_catalog.int8"
                )
            )
            altered = await subject._inventory_v2(
                connection, pins, "0056", read_only=False
            )
            assert [
                row[4]
                for row in altered["objects"]
                if row[:3] == ("attribute", wrong_index, 1)
            ] == [False]
            with pytest.raises(subject.NativeOwnerUpgradeRefusal):
                subject._preserved_v2(before, altered)
        finally:
            await trial.rollback()
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


async def test_actual0056_reviewed_superowner_cli_once_with_fresh_readback(
    tmp_path, _actual55_v2_lab, record_property
):
    """Use the regular CLI, its genuine 0055 owner, and three distinct role logins."""
    import asyncio
    import os
    from pathlib import Path
    from types import SimpleNamespace

    from app.domain.source_primitives import sha
    from scripts import current0056_fitted_catalog_measurement_v1 as measurement
    from scripts import run_retained0017_reference_probe_v1 as admitted_probe
    from tests.integration.test_gate3_fitted_candidate_custody_pg import (
        _reconstruct_owned_lab_archive,
    )
    from tests.native_owner55_upgrade_lab_v2 import run_actual_owner55_cli_v2

    owner, selected, lab_pins, _prepared = _actual55_v2_lab
    identity, files, files_raw = measurement._read_hermetic_inputs()
    probe = admitted_probe._exact_cases_probe(
        SimpleNamespace(supported_superowner_cli=True)
    )
    assert probe is not None and len(probe.nodes) == 1
    assert identity["schema"] == identity["runner_contract"] == probe.schema
    assert identity["validation_scope"] == probe.scope
    assert identity["suite"] == probe.suite
    assert identity["source_contract_head"] == "0058"
    assert identity["database_target"] == "0055_and_0056"
    assert identity["build_context"] == "admitted_archive_bytes_v1"
    assert identity["hermetic_regression"] == "NOT_RUN"
    assert identity["full_suite_acceptance"] is identity["trading_acceptance"] is False
    assert identity["host_env"] is identity["host_credentials"] is False
    assert type(identity["external_order_writes"]) is int
    assert identity["external_order_writes"] == 0
    assert measurement._canonical(files) == files_raw
    assert sha(files_raw) == identity["source_files_sha256"]
    assert files["MANIFEST.sha256"]["sha256"] == identity["manifest_sha256"]
    assert files[admitted_probe.RUNNER]["sha256"] == identity["runner_sha256"]
    assert (
        sha((subject._SOURCE / "MANIFEST.sha256").read_bytes())
        == identity["manifest_sha256"]
    )
    assert (
        sha((subject._SOURCE / admitted_probe.RUNNER).read_bytes())
        == identity["runner_sha256"]
    )
    archive, archive_sha = _reconstruct_owned_lab_archive(tmp_path, identity, files)
    assert lab_pins.route.source_manifest_sha256 == identity["manifest_sha256"]
    pins = replace(
        lab_pins,
        source_commit=identity["commit"],
        source_tree=identity["tree"],
        source_archive_sha256=archive_sha,
    )

    # This disposable dedicated TLS harness uses trust authentication. The
    # private input contract still requires a password-bearing URL. The value
    # is inert lab-only data and demonstrates no authentication acceptance.
    lab_password = "synthetic-hosted-lab-only"
    secret_file = tmp_path / "reviewed-disposable-owner-dsn"
    secret_file.write_bytes(
        owner.url.set(port=pins.route.port, password=lab_password)
        .render_as_string(hide_password=False)
        .encode("ascii")
    )
    secret_file.chmod(0o600)
    ca_file = Path(os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE"])
    role_urls = tuple(
        factory.kw["bind"].url.set(port=pins.route.port, password=lab_password)
        for factory in (
            selected.writer_factory,
            selected.custody_factory,
            selected.frame_reader_factory,
        )
    )

    async def invoke():
        return await run_actual_owner55_cli_v2(
            tmp_path,
            pins,
            owner_dsn_file=secret_file,
            ca_file=ca_file,
            source_archive_file=archive,
            role_urls=role_urls,
        )

    async with subject._marker_checked_v2(owner, pins) as connection:
        actual = tuple(
            (
                await connection.execute(
                    text("""SELECT current_user, session_user, r.oid::bigint,
                                    d.datdba::bigint, r.rolsuper,
                                    COALESCE(s.ssl, false)
                         FROM pg_catalog.pg_roles r
                         JOIN pg_catalog.pg_database d ON d.datname=current_database()
                         JOIN pg_catalog.pg_stat_ssl s ON s.pid=pg_backend_pid()
                         WHERE r.rolname=current_user""")
                )
            ).one()
        )
    assert actual == (
        pins.route.owner,
        pins.route.owner,
        pins.route.owner_oid,
        pins.route.owner_oid,
        True,
        True,
    )
    before_roles = await subject._role_observation_v2(selected, owner, pins, "0055")
    before = await subject._readonly_inventory_v2(owner, pins, "0055")

    # Observe the actual CLI backend waiting on the exact advisory key while
    # our separate transaction holds it. A generic early refusal would never
    # enter pg_locks as this owner's backend blocked by this exact holder.
    wait_sql = text("""SELECT EXISTS (
        SELECT 1 FROM pg_catalog.pg_locks pending
        JOIN pg_catalog.pg_stat_activity actor ON actor.pid=pending.pid
        WHERE pending.locktype='advisory' AND NOT pending.granted
          AND pending.database=:database_oid AND pending.objsubid=1
          AND CAST(pending.classid AS bigint)=:lock_class
          AND CAST(pending.objid AS bigint)=:lock_object
          AND actor.datid=pending.database AND actor.usename=:owner_name
          AND CAST(:blocker_pid AS integer)=ANY(pg_catalog.pg_blocking_pids(pending.pid))
    )""")
    owner_lock_key = 5883109675522810055
    observed_wait = False
    async with owner.begin() as competing, owner.connect() as observer:
        await competing.execute(subject._OWNER_LOCK)
        await observer.execution_options(isolation_level="AUTOCOMMIT")
        arguments = {
            "database_oid": pins.route.database_oid,
            "lock_class": owner_lock_key >> 32,
            "lock_object": owner_lock_key & ((1 << 32) - 1),
            "owner_name": pins.route.owner,
            "blocker_pid": await competing.scalar(
                text("SELECT pg_catalog.pg_backend_pid()")
            ),
        }
        waiting = asyncio.create_task(invoke())
        try:
            while not waiting.done():
                if await observer.scalar(wait_sql, arguments) is True:
                    observed_wait = True
                    break
                await asyncio.sleep(0.05)
        finally:
            refused_code, refused = await waiting
    assert observed_wait is True
    assert (refused_code, refused) == (
        1,
        {"status": "REFUSED", "code": "native_owner_0056_upgrade_refused"},
    )
    assert await subject._readonly_inventory_v2(owner, pins, "0055") == before
    record_property("isolated_actual_owner_lock_contention", "OBSERVED")
    record_property("independent_original_inventory_after_contention", "UNCHANGED_0055")

    code, receipt = await invoke()
    assert code == 0 and receipt["status"] == "PASS"
    assert receipt["schema"] == "ctcc.native_owner_0056.migration_commit_readback.v2"
    assert receipt["source_commit"] == identity["commit"]
    assert receipt["source_tree"] == identity["tree"]
    assert receipt["source_archive_sha256"] == archive_sha
    assert receipt["source_manifest_sha256"] == identity["manifest_sha256"]
    assert receipt["source_head"] == "0058" and receipt["database_target"] == "0056"
    assert receipt["database_head_before"] == "0055"
    assert receipt["database_head_after"] == "0056"
    assert receipt["rehearsal_rollback_verified"] is True
    assert receipt["independent_postcommit_readonly_observations"] == 3
    assert receipt["independent_role_logins_verified"] == 3
    assert receipt["all_original_metadata_preserved"] is True
    assert receipt["all_original_rows_layouts_oids_sequences_preserved"] is True
    assert receipt["unchanged_demo_grants"] == 42
    assert receipt["grants_installed"] is False
    assert receipt["startup_admission"] is receipt["execution_authority"] is False
    after = await subject._readonly_inventory_v2(owner, pins, "0056")
    subject._preserved_v2(before, after)
    assert (
        await subject._role_observation_v2(selected, owner, pins, "0056")
        == before_roles
    )
    assert await subject._readonly_inventory_v2(owner, pins, "0056") == after
    record_property("independent_successor_inventory", "VERIFIED_0056")

    replay_code, replay = await invoke()
    assert (replay_code, replay) == (
        1,
        {"status": "REFUSED", "code": "native_owner_0056_upgrade_refused"},
    )
    assert await subject._readonly_inventory_v2(owner, pins, "0056") == after
    record_property("stale_private_packet_replay", "REFUSED_WITH_UNCHANGED_0056")
