"""Inert failure/transaction contracts; never a source17 baseline or SQL proof."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts import retained0017_owner_upgrade_0055_v1 as subject
from scripts import retained0017_upgrade_projection_v1 as projection
from tests.unit import test_native_retained0017_catalog_data_v1 as catalog_data


def _declared_retained_pins(*, expected_catalog=None):
    """Typed inert SQL-unit input, never a verified route/backup/connection."""
    route = subject.original.RetainedOwnerRoute0017V1(
        "a" * 64,
        "b" * 64,
        "c" * 64,
        "127.0.0.1",
        15439,
        "declared_database",
        "declared_owner",
        20,
        10,
    )
    retained = subject.original.RetainedOwnerPins0017V1(
        "d" * 40,
        "e" * 40,
        "f" * 64,
        "1" * 64,
        route,
        expected_catalog_sha256=expected_catalog,
    )
    return subject.Retained0017UpgradePinsV1(retained, "a" * 64)


def _control():
    return [
        {
            "id": 1,
            "armed": True,
            "emergency_stop": False,
            "locked": True,
            "lock_reasons": [
                "unreviewed_old_reason",
                projection.REASON_0034,
                "unreviewed_old_reason",
            ],
            "active_trades": {},
            "active_instrument_id": None,
            "active_client_order_id": None,
            "active_start_equity": None,
            "active_started_at": None,
            "last_started_at": None,
            "last_completed_at": None,
        }
    ]


def test_empty_literal_seal_denies_before_any_private_input_or_connection(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("pending literal must deny before private-source/route preparation")

    monkeypatch.setattr(
        subject.baseline,
        "REFERENCE_STATUS",
        "PENDING_TWO_INDEPENDENT_ACTUAL_PG17_REFERENCES",
    )
    monkeypatch.setattr(subject.baseline, "SEALED_CATALOG_SHA256", "")
    monkeypatch.setattr(subject.baseline, "SEALED_SOURCE17_PREFIX_SHA256", "")
    monkeypatch.setattr(subject.original, "_prepared", forbidden)
    with pytest.raises(subject.Retained0017UpgradeRefused, match="reference_pending"):
        subject._prepared(
            object(),
            owner_dsn_file=object(),
            ca_file=object(),
            source_archive_file=object(),
        )


@pytest.mark.asyncio
async def test_apply_lineage_pending_denies_before_private_preparation_or_connection(
    monkeypatch,
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("a backup label/caller flag cannot authorize an unproved target")

    monkeypatch.setattr(subject, "_prepared", forbidden)
    monkeypatch.setattr(subject, "create_async_engine", forbidden)
    with pytest.raises(
        subject.Retained0017UpgradeRefused, match="apply_lineage_pending"
    ):
        await subject.upgrade_preserved_owner_0017_to_0055_v1(
            SimpleNamespace(backup_to_database_lineage_verified=True),
            owner_dsn_file=object(),
            ca_file=object(),
            source_archive_file=object(),
            confirmation=subject.CONFIRMATION,
        )


@pytest.mark.asyncio
async def test_internal_commit_boundary_also_denies_unproved_lineage_before_any_io():
    class Engine:
        def connect(self):
            pytest.fail("the internal write boundary must not open an unproved target")

    with pytest.raises(
        subject.Retained0017UpgradeRefused, match="apply_lineage_pending"
    ):
        await subject._commit_once(Engine(), object(), (), {})


def test_source55_table_inventory_includes_original48_and_excludes_all_future_tables():
    names = subject._target_tables()
    assert set(subject.baseline.source17_contract_v1()["columns"]) <= names
    assert subject.target55._RELATIONS <= names
    assert not (names & subject._FUTURE_TABLES)


def test_public_readonly_catalog_contract_cannot_adopt_readwrite_profile(monkeypatch):
    document = catalog_data._document("one", 20000)
    monkeypatch.setattr(
        subject.baseline,
        "source17_contract_v1",
        lambda: {
            "columns": {"declared_fixture": ["id"]},
            "source17_prefix_sha256": catalog_data.PREFIX,
        },
    )
    readonly = subject.baseline.normalize_catalog17_v1(document)
    document["profile"]["read_only"] = "off"
    with pytest.raises(subject.baseline.Retained0017CatalogRefused):
        subject.baseline.normalize_catalog17_v1(document)
    assert subject.baseline._normalize(document, read_only="off") == readonly
    assert document["profile"]["read_only"] == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("expected", (None, "matching", "wrong"))
async def test_optional_old_catalog_review_pin_is_an_additional_refusal_only(expected):
    queries = []
    document = {"relations": [{"oid": "20001", "relname": "declared_fixture"}]}
    canonical = subject.original._canonical(document)
    digest = subject.original._sha(canonical)

    class Connection:
        async def scalar(self, sql):
            queries.append(sql)
            assert sql is subject.original.CATALOG_SQL
            return canonical.decode("ascii")

    pin = None if expected is None else digest if expected == "matching" else "0" * 64
    pins = _declared_retained_pins(expected_catalog=pin)
    if expected == "wrong":
        with pytest.raises(
            subject.Retained0017UpgradeRefused, match="reviewed_catalog_mismatch"
        ):
            await subject._optional_legacy_catalog_pin(Connection(), pins)
    else:
        assert await subject._optional_legacy_catalog_pin(Connection(), pins) == pin
    assert queries == ([] if expected is None else [subject.original.CATALOG_SQL])


@pytest.mark.asyncio
async def test_old_review_pin_cannot_be_read_before_mandatory_full_source17_proof(
    monkeypatch,
):
    events = []

    async def actor(*_args, **_kwargs):
        events.append("actual_actor")

    async def unsealed(*_args, **_kwargs):
        events.append("full_source17_refused")
        raise subject.Retained0017UpgradeRefused("declared refusal")

    async def forbidden(*_args):
        pytest.fail("old diagnostic pin cannot replace or precede full source17 proof")

    monkeypatch.setattr(subject, "_actor", actor)
    monkeypatch.setattr(subject, "_capture_source17", unsealed)
    monkeypatch.setattr(subject, "_optional_legacy_catalog_pin", forbidden)
    with pytest.raises(subject.Retained0017UpgradeRefused):
        await subject._before(object(), object(), read_only=True)
    assert events == ["actual_actor", "full_source17_refused"]


@pytest.mark.asyncio
@pytest.mark.parametrize("first_admission", (False, True))
async def test_private_source_capture_locks_all_original_tables_and_rechecks_before_data(
    monkeypatch, first_admission
):
    events = []
    contract = {"columns": {"source_b": ["id"], "source_a": ["id"]}}

    async def admission(*_args):
        events.append("raw_catalog_admission")
        if not first_admission:
            raise subject.baseline.Retained0017CatalogRefused("declared refusal")

    async def storage(*_args):
        events.append("native_storage_recheck")

    class Connection:
        async def exec_driver_sql(self, sql):
            events.append(sql)

        async def scalar(self, _sql):
            events.append("capture_full_catalog")
            return {
                key: []
                for key in (
                    "head",
                    "references",
                    "toast",
                    "entries",
                    "dependencies",
                    "shared_dependencies",
                    "bootstrap",
                    "actor_physical",
                )
            }

    monkeypatch.setattr(
        subject.baseline, "_require_raw_catalog_admission_v1", admission
    )
    monkeypatch.setattr(
        subject.rows.heap_am, "require_native_owner_storage_v1", storage
    )
    monkeypatch.setattr(subject.baseline, "source17_contract_v1", lambda: contract)
    monkeypatch.setattr(subject.baseline, "_normalize", lambda _raw, **_kwargs: {})
    monkeypatch.setattr(
        subject.baseline,
        "SEALED_CATALOG_SHA256",
        subject.baseline.sha(subject.baseline.canonical({})),
    )
    monkeypatch.setattr(subject, "_require_reference_seal", lambda: None)
    if not first_admission:
        with pytest.raises(subject.baseline.Retained0017CatalogRefused):
            await subject._capture_source17(Connection(), read_only=True)
        assert events == ["raw_catalog_admission"]
        return
    await subject._capture_source17(Connection(), read_only=True)
    assert events == [
        "raw_catalog_admission",
        'LOCK TABLE public."source_a",public."source_b" IN ACCESS SHARE MODE NOWAIT',
        "native_storage_recheck",
        "raw_catalog_admission",
        "capture_full_catalog",
    ]


@pytest.mark.parametrize("reasons", (None, {}, "[]", [None], [1], [True], [""]))
def test_malformed_original_reasons_are_never_coalesced_or_normalized(reasons):
    value = _control()
    value[0]["lock_reasons"] = reasons
    with pytest.raises(projection.Retained0017ProjectionRefused):
        projection.validate_original_control_v1(value)


def test_unknown_duplicate_reasons_and_original_armed_state_are_preserved_as_data():
    value = _control()
    original = deepcopy(value)
    assert projection.validate_original_control_v1(value) is value[0]
    assert value == original


@pytest.mark.parametrize(
    "change",
    (
        {"active_trades": {"BTC-USDT-SWAP": {"uncertain": True}}},
        {"active_client_order_id": "retained_unknown"},
        {"last_started_at": "2026-11-21T01:00:00+00:00", "last_completed_at": None},
        {
            "last_started_at": "2026-11-21T02:00:00+00:00",
            "last_completed_at": "2026-11-21T01:00:00+00:00",
        },
        {
            "last_started_at": "2026-11-21T01:00:00",
            "last_completed_at": "2026-11-21T02:00:00",
        },
    ),
)
def test_actual_local_unknown_or_inflight_state_is_not_inferred_flat(change):
    value = _control()
    value[0].update(change)
    with pytest.raises(projection.Retained0017ProjectionRefused):
        projection.validate_original_control_v1(value)


def test_old_projection_excludes_new_columns_before_whole_row_json():
    columns = ["id", "armed", "emergency_stop", "locked", "lock_reasons", "daily_pnl"]
    size, digest = projection.old_projection_sql_v1(
        "demo_automation_state",
        columns,
        phase=projection.OldColumnProjectionV1.AFTER_FIXED_0034,
    )
    for statement in (str(size), str(digest)):
        assert (
            "(SELECT " in statement
            and 'FROM public."demo_automation_state" s) r' in statement
        )
        assert 's."daily_pnl" AS "daily_pnl"' in statement
        assert (
            'CASE WHEN s."id"=1 THEN false ELSE s."armed" END AS "armed"' in statement
        )
        assert "control_protocol_upgrade_requires_clear" in statement
        assert "COALESCE(s." not in statement
        assert (
            "control_revision" not in statement
            and "restart_latch_required" not in statement
        )
        assert (
            "pg_catalog.to_jsonb(r)" in statement
            and 'r."lock_reasons" IS NULL' in statement
        )


def test_existing_columns_oids_and_order_cannot_change_or_hide_a_new_column():
    old = [["demo_automation_state", 20001, "r", [["id", 23, -1, True, 0, "", ""]]]]
    new = deepcopy(old)
    new[0][3] += list(projection._NEW_STATE_COLUMNS)
    projection.require_preserved_old_layouts_v1(old, new)
    for changed in (
        [["demo_automation_state", 20002, "r", new[0][3]]],
        [["demo_automation_state", 20001, "r", list(reversed(new[0][3]))]],
        [["demo_automation_state", 20001, "r", old[0][3]]],
    ):
        with pytest.raises(projection.Retained0017ProjectionRefused):
            projection.require_preserved_old_layouts_v1(old, changed)


class _Transaction:
    def __init__(
        self, events, *, commit_error=None, after_commit_error=None, savepoint=False
    ):
        self.events = events
        self.commit_error = commit_error
        self.after_commit_error = after_commit_error
        self.savepoint = savepoint

    async def commit(self):
        self.events.append("commit_attempt")
        if self.commit_error:
            raise self.commit_error
        self.events.append("commit_returned")

    async def rollback(self):
        self.events.append("savepoint_rollback" if self.savepoint else "outer_rollback")


class _Connection:
    def __init__(self, events, commit_error):
        self.events = events
        self.transaction = _Transaction(events, commit_error=commit_error)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def begin(self):
        return self.transaction

    async def begin_nested(self):
        self.events.append("savepoint_begin")
        return _Transaction(self.events, savepoint=True)

    async def exec_driver_sql(self, _sql):
        pass

    async def execute(self, _sql):
        pass

    async def scalar(self, _sql):
        return True

    async def run_sync(self, _function, _script):
        self.events.append("migrate")


def _inert_transaction(
    monkeypatch, *, commit_error=None, failure_phase=None, rehearsal_catalog_drift=False
):
    # Deliberately inert orchestration only; this is not a lineage/source/SQL
    # acceptance fixture. The real public and internal apply denials above
    # remain mandatory until actual copy/bootstrap custody is implemented.
    monkeypatch.setattr(subject, "_require_apply_lineage", lambda: None)
    events = []
    conn = _Connection(events, commit_error)
    before = {"original": "held"}
    final = {"logical_sha256": "a" * 64, "rehearsal_catalog_sha256": "b" * 64}
    count = 0

    async def no_op(*_args, **_kwargs):
        pass

    async def before_read(*_args, **_kwargs):
        events.append("before_read")
        return before

    async def after_read(*_args, **_kwargs):
        nonlocal count
        count += 1
        events.append("after_read")
        if failure_phase == count:
            raise RuntimeError("declared inert fault")
        if count == 1:
            return final | {
                "logical_sha256": "c" * 64,
                "rehearsal_catalog_sha256": ("d" if rehearsal_catalog_drift else "b")
                * 64,
            }
        return final

    monkeypatch.setattr(subject, "_maintenance", no_op)
    monkeypatch.setattr(subject, "_replay_source", lambda *_args: None)

    async def begin_inert(*_args, **_kwargs):
        return conn.transaction

    monkeypatch.setattr(subject, "_begin_reviewed", begin_inert)
    monkeypatch.setattr(subject, "_lock_original_tables", no_op)
    monkeypatch.setattr(subject.rows.heap_am, "require_native_owner_storage_v1", no_op)
    monkeypatch.setattr(subject, "_before", before_read)
    monkeypatch.setattr(subject, "_after", after_read)
    monkeypatch.setattr(
        subject.baseline, "source17_contract_v1", lambda: {"columns": {}}
    )
    engine = SimpleNamespace(connect=lambda: conn)
    return events, engine, before, final


@pytest.mark.asyncio
async def test_rehearsal_must_rollback_and_exact_before_replays_before_one_commit(
    monkeypatch,
):
    events, engine, before, final = _inert_transaction(monkeypatch)
    assert await subject._commit_once(
        engine, object(), (None, None, None, object()), before
    ) == (before, final)
    assert events == [
        "before_read",
        "savepoint_begin",
        "migrate",
        "after_read",
        "savepoint_rollback",
        "before_read",
        "migrate",
        "after_read",
        "commit_attempt",
        "commit_returned",
    ]


@pytest.mark.asyncio
async def test_rehearsal_catalog_semantic_drift_rolls_back_before_commit(monkeypatch):
    events, engine, before, _final = _inert_transaction(
        monkeypatch, rehearsal_catalog_drift=True
    )
    with pytest.raises(subject.Retained0017UpgradeRefused):
        await subject._commit_once(
            engine, object(), (None, None, None, object()), before
        )
    assert "commit_attempt" not in events and events[-1] == "outer_rollback"


def test_rehearsal_sql_does_not_bind_pg_node_tree_field_tokens():
    statement = subject._RETAINED_CATALOG.compile()
    assert statement.params == {}
    assert str(statement).count("{FUNCEXPR :funcid ") == 6
    assert str(statement).count(" :funcresulttype ") == 6


def test_native55_retained_readback_pins_private_owner_snapshot_isolation(monkeypatch):
    calls = []

    def observe(value, **policy):
        calls.append(policy)
        return value

    target = subject.target55
    monkeypatch.setattr(target, "_observe", observe)
    marker = object()
    assert target._observe_retained_postcommit_owner_catalog_v1(marker) is marker
    assert target.observe_actual_owner_catalog(marker) is marker
    assert target.observe_actual_writer_catalog(marker, read_only="on") is marker
    assert calls == [
        {"owner_mode": True, "read_only": "on", "isolation": "repeatable read"},
        {"owner_mode": True, "read_only": "on", "isolation": "read committed"},
        {"owner_mode": False, "read_only": "on", "isolation": "read committed"},
    ]
    for owner, read_only in ((True, "off"), (False, "on")):
        with pytest.raises(target.Current0055CatalogError):
            target._observed(
                marker, owner_mode=owner, read_only=read_only, _retained_postcommit=True
            )
    assert len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", (1, 2))
async def test_every_precommit_rehearsal_or_final_failure_rolls_back_without_commit(
    monkeypatch, phase
):
    events, engine, before, _final = _inert_transaction(
        monkeypatch, failure_phase=phase
    )
    with pytest.raises(subject.Retained0017UpgradeRefused):
        await subject._commit_once(
            engine, object(), (None, None, None, object()), before
        )
    assert "commit_attempt" not in events and events[-1] == "outer_rollback"


@pytest.mark.asyncio
async def test_unknown_commit_is_terminal_without_rollback_or_reapply(monkeypatch):
    events, engine, before, _final = _inert_transaction(
        monkeypatch, commit_error=OSError("private_CANARY")
    )
    with pytest.raises(subject.Retained0017UpgradeCommitUnknown) as error:
        await subject._commit_once(
            engine, object(), (None, None, None, object()), before
        )
    assert events[-1] == "commit_attempt"
    assert events.count("migrate") == 2 and "outer_rollback" not in events
    assert "private_CANARY" not in str(error.value)


def test_cli_unknown_private_argument_is_never_echoed(capsys):
    assert subject.main(["--private_CANARY_DSN=secret"]) == 2
    captured = capsys.readouterr()
    assert (
        "CANARY" not in captured.out + captured.err
        and "secret" not in captured.out + captured.err
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", tuple(subject._TransactionPurpose))
async def test_policy_probe_rolls_back_before_actual_transaction_settings(
    monkeypatch, purpose
):
    events = []

    class Transaction:
        async def rollback(self):
            events.append("rollback")

    class Connection:
        async def begin(self):
            events.append("begin")
            return Transaction()

        async def exec_driver_sql(self, statement):
            events.append(statement)

    async def storage(*_args):
        events.append("native_storage")

    async def policy(*_args, **_kwargs):
        events.append("observe_policy")

    monkeypatch.setattr(
        subject.rows.heap_am, "require_native_owner_storage_v1", storage
    )
    monkeypatch.setattr(subject, "_maintenance", policy)
    await subject._begin_reviewed(Connection(), object(), purpose=purpose)
    assert events[:5] == [
        "begin",
        "native_storage",
        "observe_policy",
        "rollback",
        "begin",
    ]
    assert events[5].startswith("SET TRANSACTION ISOLATION LEVEL ")
    assert all(isinstance(sql, str) and sql.startswith("SET ") for sql in events[5:])
    path = next(sql for sql in events[5:] if sql.startswith("SET LOCAL search_path"))
    assert (
        path
        == {
            subject._TransactionPurpose.READ_SOURCE17: "SET LOCAL search_path TO pg_catalog",
            subject._TransactionPurpose.WRITE_FIXED_DDL: "SET LOCAL search_path TO public, pg_catalog, pg_temp",
            subject._TransactionPurpose.READ_TARGET55: "SET LOCAL search_path TO pg_catalog, public, pg_temp",
        }[purpose]
    )
    assert (
        "SET LOCAL search_path TO pg_catalog, public, pg_temp"
        in subject.shared._OWNER_SETTINGS
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("name", tuple(subject._POLICY))
async def test_unexpected_effective_ddl_setting_is_rejected_without_normalization(name):
    queries = []

    class Connection:
        async def scalar(self, sql, parameters=None):
            queries.append(str(sql))
            if sql is subject._NO_OTHER_CLIENT:
                return True
            if sql is subject._STORED_POLICY_SQL:
                return True
            if sql is subject._SETTINGS_HASH:
                return "a" * 64
            if parameters is not None:
                setting = parameters["name"]
                return (
                    "unexpected_CANARY"
                    if setting == name
                    else min(subject._POLICY[setting])
                )
            return True

    pins = _declared_retained_pins()
    with pytest.raises(
        subject.Retained0017UpgradeRefused, match="session_policy_refused"
    ):
        await subject._maintenance(Connection(), pins, before_settings=True)
    assert all("FROM public." not in sql and "SET LOCAL" not in sql for sql in queries)


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery", (False, True, None, "false"))
async def test_reviewed_ddl_policy_requires_actual_primary_not_unknown(recovery):
    class Connection:
        async def scalar(self, sql, parameters=None):
            if sql is subject._STORED_POLICY_SQL:
                return True
            if sql is subject._SETTINGS_HASH:
                return "a" * 64
            if parameters is not None:
                return min(subject._POLICY[parameters["name"]])
            if "pg_is_in_recovery" in str(sql):
                return recovery
            return True

    pins = _declared_retained_pins()
    if recovery is False:
        await subject._maintenance(Connection(), pins, before_settings=True)
    else:
        with pytest.raises(
            subject.Retained0017UpgradeRefused, match="readwrite_owner_required"
        ):
            await subject._maintenance(Connection(), pins, before_settings=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", (False, None, 1, "true"))
async def test_startup_overrides_cannot_promote_unapproved_original_stored_policy(
    stored,
):
    queries = []

    class Connection:
        async def scalar(self, sql, _parameters=None):
            queries.append(sql)
            if sql is subject._STORED_POLICY_SQL:
                return stored
            pytest.fail(
                "rejected original defaults must stop before later effective-value/hash acceptance"
            )

    pins = _declared_retained_pins()
    with pytest.raises(
        subject.Retained0017UpgradeRefused, match="stored_maintenance_policy_refused"
    ):
        await subject._maintenance(Connection(), pins, before_settings=True)
    assert queries == [subject._STORED_POLICY_SQL]


@pytest.mark.asyncio
async def test_controlled_startup_library_role_path_and_readonly_settings_reach_driver(
    monkeypatch,
):
    captured = []

    class Engine:
        async def dispose(self):
            pass

    def construct(*_args, **kwargs):
        captured.append(kwargs)
        return Engine()

    async def stop_before_connect(*_args):
        raise subject.Retained0017UpgradeRefused("declared inert boundary")

    monkeypatch.setattr(
        subject,
        "_prepared",
        lambda *_args, **_kwargs: (object(), object(), {}, object(), object()),
    )
    monkeypatch.setattr(subject, "create_async_engine", construct)
    monkeypatch.setattr(subject, "_readonly_before", stop_before_connect)
    with pytest.raises(subject.Retained0017UpgradeRefused):
        await subject.upgrade_preserved_owner_0017_to_0055_v1(
            object(),
            owner_dsn_file=object(),
            ca_file=object(),
            source_archive_file=object(),
        )
    settings = captured[0]["connect_args"]["server_settings"]
    assert (
        settings["session_preload_libraries"]
        == settings["local_preload_libraries"]
        == ""
    )
    assert settings["role"] == "none" and settings["search_path"] == "pg_catalog"
    assert settings["default_transaction_read_only"] == "on"
    assert (
        settings["statement_timeout"] == "120000ms"
        and settings["lock_timeout"] == "1000ms"
    )
    assert settings is not subject._STARTUP_SETTINGS
