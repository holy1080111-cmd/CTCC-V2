"""Inert negative-stop contracts; no source55/lineage/SQL acceptance."""

import copy
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from scripts import retained0055_negative_stop_guard_v1 as guard
from scripts import retained0055_negative_stop_reassert_v1 as subject

NOW = (
    "{FUNCEXPR :funcid 1299 :funcresulttype 1184 :funcretset false :funcvariadic false"
    " :funcformat 0 :funccollid 0 :inputcollid 0 :args <> :location -1}"
)


def _pins(*, head="0055", settings="f" * 64):
    route = subject.canonical.OwnerRoutePinsV1(
        "a" * 64,
        "b" * 64,
        "c" * 64,
        "127.0.0.1",
        15439,
        "declared_db",
        "declared_owner",
        100,
        10,
        head,
    )
    return subject.NegativeStopPinsV1(
        subject.canonical.OwnerUpgradePinsV1("d" * 40, "e" * 40, "a" * 64, route),
        settings,
    )


def test_negative_stop_requires_exact_stopped55_observation_pin():
    pins = _pins()
    converted = subject._inspection_pins_v1(pins)
    assert type(converted) is subject.owner._StoppedTarget55PinsV1
    assert subject.owner._transaction_source_v1(converted) is pins.retained
    with pytest.raises(subject.owner.Retained0017UpgradeRefused):
        subject._inspection_pins_v1(_pins(head="0056"))
    with pytest.raises(subject.owner.Retained0017UpgradeRefused):
        subject._inspection_pins_v1(_pins(settings=None))


@pytest.mark.asyncio
async def test_stopped_copy_plan_reads_continuity_twice_and_reports_no_mutation(
    monkeypatch,
):
    pins = _pins()
    expected = "a" * 64
    events = []

    def source(*_args):
        events.append("source")

    async def read(_engine, actual, *, expected_after_sha256):
        assert actual is pins and expected_after_sha256 == expected
        events.append("continuity")
        return {"control": _control()}

    monkeypatch.setattr(subject, "_source", source)
    monkeypatch.setattr(subject, "_read", read)
    engine = create_async_engine(
        "postgresql+asyncpg://declared@127.0.0.1/db", poolclass=NullPool
    )
    try:
        result = await subject._plan_stopped_copy_v1(
            engine, pins, object(), expected_after_sha256=expected
        )
    finally:
        await engine.dispose()
    assert events == ["source", "continuity", "continuity", "source"]
    assert result["independent_readbacks"] == 2
    assert result["source_upgrade_after_sha256"] == expected
    assert result["control_revision"] == 0
    assert result["mutation_performed"] is result["execution_authority"] is False


def _catalog():
    # Declared minimal typed DATA. Complete source/profile admission remains
    # upstream and genuine PostgreSQL acceptance is a separate pending suite.
    columns = subject.owner.baseline.source17_contract_v1()["columns"]
    controls = {name: [name, 23, -1, True, 0, "", ""] for name in columns[guard.STATE]}
    for name, oid in (
        ("armed", 16),
        ("emergency_stop", 16),
        ("locked", 16),
        ("lock_reasons", 3802),
    ):
        controls[name] = [name, oid, -1, True, 0, "", ""]
    controls["control_revision"] = ["control_revision", 20, -1, True, 0, "", ""]
    controls["restart_latch_required"] = [
        "restart_latch_required",
        16,
        -1,
        True,
        0,
        "",
        "",
    ]
    state = [
        controls[name]
        for name in columns[guard.STATE]
        + ["control_revision", "restart_latch_required"]
    ]
    audit = [
        ["id", 2950, -1, True, 0, "", ""],
        ["actor", 1043, 104, True, 100, "", ""],
        ["action", 1043, 104, True, 100, "", ""],
        ["resource_type", 1043, 54, True, 100, "", ""],
        ["resource_id", 1043, 104, False, 100, "", ""],
        ["before", 3802, -1, False, 0, "", ""],
        ["after", 3802, -1, False, 0, "", ""],
        ["ip_address", 869, -1, False, 0, "", ""],
        ["occurred_at", 1184, -1, True, 0, "", ""],
    ]
    function = {
        "oid": 20001,
        "owner": 20000,
        "language": "plpgsql",
        "return_type": 2279,
        "argument_types": [],
        "argument_count": 0,
        "default_count": 0,
        "kind": "f",
        "security_definer": False,
        "strict": False,
        "retset": False,
        "leakproof": False,
        "volatility": "v",
        "parallel": "u",
        "variadic": 0,
        "support": 0,
        "argument_names": None,
        "all_argument_types": None,
        "argument_modes": None,
        "transforms": None,
        "cost_source_default": True,
        "rows_source_default": True,
        "config": None,
        "binary": None,
        "defaults": None,
        "parsed_body": None,
        "acl": None,
        "source": guard.source_guard_body_v1(),
    }
    document = {
        "functions": [function],
        "triggers": [
            [
                guard.STATE,
                guard.GUARD,
                20001,
                19,
                "O",
                False,
                0,
                "",
                "",
                True,
                False,
                False,
                0,
                0,
                0,
                0,
                None,
                None,
            ]
        ],
        "audit_defaults": [["occurred_at", NOW]],
        "audit_constraints": [["pk_audit_logs", "p", [1], False, False, True]],
        "state_checks": [],
        "indexes": [
            [guard.STATE, True, True, True, True, True],
            [guard.AUDIT, True, True, True, True, True],
        ],
    }
    return document, [
        [guard.STATE, 21001, "r", state],
        [guard.AUDIT, 21002, "r", audit],
    ]


def test_declared_catalog_data_binds_source_guard_and_never_evaluates_sql():
    document, layouts = _catalog()
    before = copy.deepcopy((document, layouts))
    assert len(guard.require_control_audit_v1(document, layouts, 20000)) == 64
    assert (document, layouts) == before


@pytest.mark.parametrize(
    "fault",
    (
        "body",
        "definer",
        "search_path",
        "trigger_disabled",
        "extra_audit_trigger",
        "audit_check",
        "audit_default",
        "expression_index",
        "audit_uuid_type",
        "integer_boolean",
    ),
)
def test_changed_trigger_function_audit_callback_or_type_cannot_pass_catalog_guard(
    fault,
):
    document, layouts = _catalog()
    if fault == "body":
        document["functions"][0]["source"] += "\nPERFORM public.foreign_callback();"
    elif fault == "definer":
        document["functions"][0]["security_definer"] = True
    elif fault == "search_path":
        document["functions"][0]["config"] = ["search_path=public"]
    elif fault == "trigger_disabled":
        document["triggers"][0][4] = "D"
    elif fault == "extra_audit_trigger":
        document["triggers"].append([guard.AUDIT, "foreign_callback"])
    elif fault == "audit_check":
        document["audit_constraints"].append(
            ["foreign_check", "c", None, False, False, True]
        )
    elif fault == "audit_default":
        document["audit_defaults"][0][1] = NOW.replace(":funcid 1299", ":funcid 20002")
    elif fault == "expression_index":
        document["indexes"][1][1] = False
    elif fault == "audit_uuid_type":
        layouts[1][3][0][1] = 20003
    else:
        document["triggers"][0][12] = False
    with pytest.raises(
        (
            guard.NegativeStopGuardRefused,
            subject.owner.baseline.Retained0017CatalogRefused,
        )
    ):
        guard.require_control_audit_v1(document, layouts, 20000)


def test_foreign_catalog_value_is_rejected_without_comparison_callback():
    called = []

    class Foreign:
        def __eq__(self, _other):
            called.append(True)
            return True

    document, layouts = _catalog()
    document["functions"][0]["source"] = Foreign()
    with pytest.raises(guard.NegativeStopGuardRefused):
        guard.require_control_audit_v1(document, layouts, 20000)
    assert called == []


def _control(revision=0):
    return [
        {
            "id": 1,
            "armed": False,
            "emergency_stop": True,
            "locked": True,
            "restart_latch_required": True,
            "control_revision": revision,
            "lock_reasons": ["unknown_old_reason", "unknown_old_reason", guard.REASON],
        }
    ]


def test_negative_eligibility_keeps_unknown_and_duplicate_reasons_in_order():
    value = _control()
    before = copy.deepcopy(value)
    assert subject._control(value, revision=0) is value[0]
    assert value == before


@pytest.mark.parametrize(
    "change",
    (
        {"armed": True},
        {"emergency_stop": False},
        {"restart_latch_required": False},
        {"control_revision": True},
        {"control_revision": 1},
        {"lock_reasons": None},
        {"lock_reasons": [""]},
    ),
)
def test_negative_reassertion_cannot_clear_stop_or_adopt_wrong_revision(change):
    value = _control()
    value[0].update(change)
    with pytest.raises(subject.NegativeStopRefused):
        subject._control(value, revision=0)


@pytest.mark.asyncio
async def test_confirmed_negative_command_denies_pending_lineage_before_any_private_or_db_use(
    monkeypatch,
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("saved flags/hashes cannot enable the pending source-copy lineage")

    monkeypatch.setattr(subject, "_prepare", forbidden)
    monkeypatch.setattr(subject, "create_async_engine", forbidden)
    with pytest.raises(
        subject.owner.Retained0017UpgradeRefused, match="apply_lineage_pending"
    ):
        await subject.reassert_retained_owner_0055_stop_v1(
            SimpleNamespace(lineage_verified=True),
            owner_dsn_file=object(),
            ca_file=object(),
            archive_file=object(),
            confirmation=subject.CONFIRMATION,
        )


@pytest.mark.asyncio
async def test_internal_negative_write_boundary_is_also_lineage_denied():
    class Engine:
        def connect(self):
            pytest.fail("unproved lineage must refuse before connect")

    with pytest.raises(
        subject.owner.Retained0017UpgradeRefused, match="apply_lineage_pending"
    ):
        await subject._write_once(Engine(), object(), object(), {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", (None, "risk_row", "sequence", "deduplicate_reasons", "second_journal")
)
async def test_inert_full_old_row_comparison_allows_only_exact_control_delta_and_one_journal(
    monkeypatch, fault
):
    before = {
        "rows": [
            [guard.STATE, 1, "a" * 64],
            [guard.AUDIT, 4, "b" * 64],
            ["trades", 2, "f" * 64],
        ],
        "control": _control(),
        "row_layouts": [],
        "sequence_state": [["old_sequence", 1, False]],
    }
    after = copy.deepcopy(before)
    after["rows"][0][2] = "c" * 64
    after["rows"][1][1:] = [5, "d" * 64]
    after["control"][0]["control_revision"] = 1
    after["control"][0]["lock_reasons"].append(guard.REASON)
    if fault == "risk_row":
        after["rows"][2][2] = "0" * 64
    elif fault == "sequence":
        after["sequence_state"][0][1] = 2
    elif fault == "deduplicate_reasons":
        after["control"][0]["lock_reasons"] = sorted(
            set(after["control"][0]["lock_reasons"])
        )
    elif fault == "second_journal":
        after["rows"][1][1] += 1

    async def project(_connection, _layouts, phase, _operation):
        return (
            (4, "b" * 64)
            if phase is subject._Projection.ORIGINAL_AUDIT
            else (1, "e" * 64)
        )

    monkeypatch.setattr(subject, "_project", project)
    arguments = (
        object(),
        before,
        after,
        (1, "c" * 64),
        UUID("a9d098d6-4381-43fd-90c4-250448692840"),
        (1, "e" * 64),
    )
    if fault is None:
        await subject._verify_delta(*arguments)
    else:
        with pytest.raises(subject.NegativeStopRefused):
            await subject._verify_delta(*arguments)


def test_fixed_journal_sql_never_uses_a_serial_counter_or_omitted_default():
    assert "CURRENT_TIMESTAMP" in str(subject._INSERT_SQL)
    assert all(
        column in str(subject._INSERT_SQL)
        for column in subject.owner.baseline.source17_contract_v1()["columns"][
            guard.AUDIT
        ]
    )
    assert "nextval" not in str(subject._INSERT_SQL) and "setval" not in str(
        subject._INSERT_SQL
    )
    assert "occurred_at" not in str(subject._CAS_SQL) and "updated_at" not in str(
        subject._CAS_SQL
    )
    assert "lock_reasons||" in str(subject._CAS_SQL) and " DISTINCT " in str(
        subject._CAS_SQL
    )


def test_projections_keep_shared_null_bitmap_and_exclude_only_exact_operation_uuid():
    _document, layouts = _catalog()
    for phase in subject._Projection:
        size, digest = subject._projection_sql(layouts, phase)
        for statement in (str(size), str(digest)):
            assert (
                "pg_catalog.to_jsonb(r)" in statement and 'r."id" IS NULL' in statement
            )
            if phase is subject._Projection.EXPECTED_CONTROL:
                assert (
                    's."control_revision"+1' in statement and guard.REASON in statement
                )
            else:
                assert "CAST(:operation_id AS pg_catalog.uuid)" in statement
    with pytest.raises(subject.NegativeStopRefused):
        subject._projection_sql(layouts, "caller_selected_sql")


def test_private_cli_error_never_echoes_pasted_secret(capsys):
    assert subject.main(["--postgresql_CANARY=secret"]) == 2
    value = capsys.readouterr()
    assert (
        "CANARY" not in value.out + value.err and "secret" not in value.out + value.err
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", (False, True))
async def test_inert_commit_has_one_journal_cas_and_unknown_never_retries(
    monkeypatch, lost
):
    # Explicitly inert orchestration: no real source/lineage/catalog acceptance.
    events = []
    before = {
        "control": _control(),
        "row_layouts": [],
        "rows": [[guard.STATE, 1, "a" * 64], [guard.AUDIT, 4, "b" * 64]],
    }
    after = {"logical": "private-final"}
    fixed_id = UUID("61dd7d0d-2155-42d1-b78b-116759bdfcb1")

    class Transaction:
        async def commit(self):
            events.append("commit")
            if lost:
                raise OSError("private_CANARY")

        async def rollback(self):
            events.append("rollback")

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, *_args):
            events.append("lock")

        async def exec_driver_sql(self, *_args):
            events.append("marker_lock")

        async def scalar(self, sql, _parameters=None):
            if sql is subject._INSERT_SQL:
                events.append("journal")
                return str(fixed_id)
            if sql is subject._CAS_SQL:
                events.append("cas")
                return 1
            return True

    class Engine:
        def connect(self):
            return Connection()

    async def begin(*_args):
        return Transaction()

    observations = iter((before, after))

    async def observe(*_args):
        return next(observations)

    async def project(*_args):
        return (1, "c" * 64)

    async def no_op(*_args):
        pass

    monkeypatch.setattr(subject.owner, "_require_apply_lineage", lambda: None)
    monkeypatch.setattr(subject, "uuid4", lambda: fixed_id)
    monkeypatch.setattr(subject, "_begin", begin)
    monkeypatch.setattr(subject, "_observe", observe)
    monkeypatch.setattr(subject, "_project", project)
    monkeypatch.setattr(subject, "_verify_delta", no_op)
    monkeypatch.setattr(
        subject.owner.rows.heap_am, "require_native_owner_storage_v1", no_op
    )
    monkeypatch.setattr(subject, "_source", lambda *_args: None)
    pins = SimpleNamespace(
        retained=SimpleNamespace(source_commit="a" * 40, source_tree="b" * 40)
    )
    if lost:
        with pytest.raises(subject.NegativeStopCommitUnknown) as error:
            await subject._write_once(Engine(), pins, object(), before)
        assert error.value.operation_id == fixed_id and "CANARY" not in str(error.value)
    else:
        assert (await subject._write_once(Engine(), pins, object(), before))[
            0
        ] == fixed_id
    assert events.count("journal") == events.count("cas") == events.count("commit") == 1
    assert events.index("journal") < events.index("cas") < events.index("commit")
    assert "rollback" not in events
