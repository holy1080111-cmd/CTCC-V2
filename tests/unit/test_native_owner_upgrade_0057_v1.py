"""Synthetic owner maintenance decisions; no database or venue activity."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import native_owner_upgrade_0057_v1 as subject

NOW = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)


def _pins():
    return subject.OwnerProfileUpgrade0057PinsV1(
        "a" * 40,
        "b" * 40,
        "c" * 64,
        subject.route.OwnerRoutePinsV1(
            "d" * 64,
            "e" * 64,
            "f" * 64,
            "synthetic.invalid",
            5432,
            "ctcc_synthetic",
            "ctcc_owner",
            16385,
            16384,
            "0056",
        ),
        ("ctcc_writer", "ctcc_custody", "ctcc_frame"),
        (20001, 20002, 20003),
    )


def _document():
    state = subject.control.stopped_genesis(
        subject.control.ControlScope("demo", "12345"), NOW - timedelta(days=1)
    )
    state_json = subject.control.canonical(subject.control.document(state))
    row = {
        "environment": "demo",
        "account_id": "12345",
        "control_revision": state.revision,
        "owner_sha256": state.owner_sha256,
        "owner_epoch": state.owner_epoch,
        "state_json": state_json,
        "state_sha256": subject.control.digest(state_json),
        "emergency_stop": True,
        "arm_request_id": None,
        "arm_expires_at": None,
        **{
            name: getattr(state, name).isoformat()
            for name in ("lease_until", "created_at", "updated_at")
        },
    }
    singleton = {
        "id": 1,
        "armed": False,
        "emergency_stop": True,
        "locked": True,
        "restart_latch_required": True,
        "control_revision": 7,
        "lock_reasons": ["emergency_stop_engaged", "operator_maintenance"],
        "last_error": None,
        "active_trades": {},
        "active_instrument_id": None,
        "active_client_order_id": None,
        "active_start_equity": None,
        "active_started_at": None,
        "last_started_at": (NOW - timedelta(hours=2)).isoformat(),
        "last_completed_at": (NOW - timedelta(hours=1)).isoformat(),
        "updated_at": NOW.isoformat(),
        "daily_pnl": "-12.5",
        "peak_equity": "1234",
        "trades_today": 13,
        "consecutive_losses": 3,
        "realized_pnl_events": [{"net_pnl": "-12.5"}],
    }
    return {
        "now": NOW.isoformat(),
        "controls": [row],
        "automation": [singleton],
        "counts": dict.fromkeys(subject._COUNTS, 0),
    }


def test_flat_local_check_preserves_existing_loss_risk_and_history_values():
    document = _document()
    before = deepcopy(document)
    subject._validate_local(document)
    assert document == before
    assert "armed" not in document["controls"][0]
    assert document["automation"][0]["daily_pnl"] == "-12.5"


@pytest.mark.parametrize("counter", tuple(subject._COUNTS))
@pytest.mark.parametrize("value", (1, False, None, "0"))
def test_any_unknown_or_nonzero_local_inventory_blocks(counter, value):
    document = _document()
    document["counts"][counter] = value
    with pytest.raises(subject.OwnerProfileUpgrade0057Refused):
        subject._validate_local(document)


@pytest.mark.parametrize(
    "path,value",
    (
        (("controls",), []),
        (("automation",), []),
        (("automation", 0, "armed"), True),
        (("automation", 0, "emergency_stop"), False),
        (("automation", 0, "restart_latch_required"), False),
        (("automation", 0, "locked"), False),
        (("automation", 0, "active_trades"), {"trade": {}}),
        (("automation", 0, "last_completed_at"), None),
        (("automation", 0, "last_error"), "uncertain"),
        (("controls", 0, "emergency_stop"), False),
        (("controls", 0, "arm_request_id"), "a" * 64),
        (("controls", 0, "state_sha256"), "b" * 64),
        (("controls", 0, "owner_epoch"), 1),
        (("controls", 0, "lease_until"), (NOW + timedelta(seconds=1)).isoformat()),
    ),
)
def test_nonflat_or_inconsistent_state_is_not_treated_as_stopped(path, value):
    document = _document()
    row = document
    for part in path[:-1]:
        row = row[part]
    row[path[-1]] = value
    with pytest.raises(subject.OwnerProfileUpgrade0057Refused):
        subject._validate_local(document)


def test_exact_two_g12_grants_add_only_four_fixed_direct_privileges():
    before = (("schema", 2200, 20001, 16384, "USAGE", False),)
    after = subject._expected_acl(
        before, target_oids=(30001, 30002), writer_oid=20001, owner_oid=16384
    )
    assert set(after) - set(before) == {
        ("relation", oid, 20001, 16384, privilege, False)
        for oid in (30001, 30002)
        for privilege in ("SELECT", "INSERT")
    }
    assert (
        len(
            subject.grants.current57_native_g12_grant_sql_v1(
                writer="ctcc_writer", custody="ctcc_custody", frame_reader="ctcc_frame"
            )
        )
        == 2
    )
    with pytest.raises(subject.OwnerProfileUpgrade0057Refused):
        subject._expected_acl(
            after, target_oids=(30001, 30002), writer_oid=20001, owner_oid=16384
        )


@pytest.mark.parametrize("oids", ((1, 1), (False, 2), (1, None), (1,), (1, 2, 3)))
def test_invalid_or_repeated_target_identity_is_refused(oids):
    with pytest.raises(subject.OwnerProfileUpgrade0057Refused):
        subject._expected_acl((), target_oids=oids, writer_oid=20001, owner_oid=16384)


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return False


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def one(self):
        return self.rows[0]

    def all(self):
        return self.rows


class _Transaction:
    def __init__(self, events, commit_error):
        self.events, self.commit_error = events, commit_error

    async def commit(self):
        self.events.append("COMMIT")
        if self.commit_error:
            raise self.commit_error("private database password must never escape")

    async def rollback(self):
        self.events.append("ROLLBACK")


class _Connection:
    def __init__(self, events, before_acl, after_acl, commit_error=None):
        self.events, self.before_acl, self.after_acl = events, before_acl, after_acl
        self.migrated = False
        self.transaction = _Transaction(events, commit_error)

    async def begin(self):
        return self.transaction

    async def exec_driver_sql(self, sql):
        self.events.append(sql)

    async def execute(self, sql, *args):
        if sql is subject._ACL:
            return _Rows(self.after_acl if self.migrated else self.before_acl)
        if sql is subject.route._PROFILE_SQL:
            return _Rows([("0057" if self.migrated else "0056",)])
        self.events.append("OWNER_ADVISORY")
        return _Rows([])

    async def scalar(self, sql, args=None):
        if args is None:
            assert (
                str(sql) == subject.retained_rows.heap_am.NATIVE_MARKER_AND_HEAP_SQL_V1
            )
            self.events.append("NATIVE_HEAP_METADATA")
            return True
        return 30001 if args["name"].endswith("claims") else 30002

    async def run_sync(self, migration, script):
        self.events.append("MIGRATE")
        self.migrated = True


def _transaction_lab(monkeypatch, *, commit_error=None, fail=None):
    from app.core import native_role_preflight_v1 as acl

    events = []
    before = (("schema", 2200, 20001, 16384, "USAGE", False),)
    expected = subject._expected_acl(
        before, target_oids=(30001, 30002), writer_oid=20001, owner_oid=16384
    )
    connection = _Connection(events, before, expected, commit_error)
    engine = SimpleNamespace(connect=lambda: _Context(connection))
    snapshot = {
        "rows": [["retained", 1, "a" * 64]],
        "schema_sha256": "b" * 64,
        "role_state_sha256": "c" * 64,
    }

    async def local(conn):
        events.append("LOCAL_FLAT")

    async def relations(conn, *, lock):
        events.append("TABLE_LOCKS" if lock else "RELATIONS_READBACK")
        return (("retained", 30004, "r"),)

    async def take_snapshot(conn, rels, pins):
        events.append("SNAPSHOT")
        if fail == "changed_rows" and connection.migrated:
            return {**snapshot, "rows": []}
        return snapshot

    async def exact_acl(*args):
        events.append("ACTUAL_42")
        if fail == "before_acl":
            raise ValueError("private SQL detail")

    monkeypatch.setattr(subject, "_local", local)
    monkeypatch.setattr(subject, "_relations", relations)
    monkeypatch.setattr(subject, "_snapshot", take_snapshot)
    monkeypatch.setattr(acl, "_exact_existing_acl", exact_acl)
    monkeypatch.setattr(
        subject, "_checked_owner", lambda *a, **k: events.append("OWNER_IDENTITY")
    )
    return engine, snapshot, before, events


@pytest.mark.asyncio
async def test_one_commit_follows_held_locks_flat_check_exact42_and_complete_preservation(
    monkeypatch,
):
    engine, snapshot, acl, events = _transaction_lab(monkeypatch)
    retained, _ = await subject._commit_once(object(), _pins(), engine, snapshot, acl)
    assert retained == snapshot
    assert events.count("COMMIT") == 1 and "ROLLBACK" not in events
    assert (
        events.index("TABLE_LOCKS")
        < events.index("LOCAL_FLAT")
        < events.index("ACTUAL_42")
        < events.index("MIGRATE")
        < events.index("COMMIT")
    )
    assert sum(event.startswith("GRANT ") for event in events) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", ("changed_rows", "before_acl"))
async def test_failure_before_commit_rolls_back_once_and_never_retries(
    monkeypatch, fail
):
    engine, snapshot, acl, events = _transaction_lab(monkeypatch, fail=fail)
    with pytest.raises(
        subject.OwnerProfileUpgrade0057Refused, match="^owner_profile_0057_refused$"
    ):
        await subject._commit_once(object(), _pins(), engine, snapshot, acl)
    assert events.count("ROLLBACK") == 1 and "COMMIT" not in events
    assert events.count("MIGRATE") == (1 if fail == "changed_rows" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", (TimeoutError, OSError))
async def test_uncertain_commit_is_terminal_and_does_not_rollback_or_regrant(
    monkeypatch, error
):
    engine, snapshot, acl, events = _transaction_lab(monkeypatch, commit_error=error)
    with pytest.raises(
        subject.OwnerProfileUpgrade0057CommitUnknown,
        match="^owner_profile_0057_commit_unknown_do_not_retry$",
    ):
        await subject._commit_once(object(), _pins(), engine, snapshot, acl)
    assert events.count("COMMIT") == 1 and "ROLLBACK" not in events
    assert events.count("MIGRATE") == 1
    assert sum(event.startswith("GRANT ") for event in events) == 2


def test_default_cli_rejects_missing_files_without_selecting_a_database(
    monkeypatch, capsys
):
    monkeypatch.setattr(subject, "_load_pins", lambda *a: pytest.fail("input opened"))
    assert subject.main([]) == 1
    assert "owner_profile_0057_refused" in capsys.readouterr().out


@pytest.mark.parametrize(
    "arguments",
    (
        ("--execute",),
        ("--confirmation", subject.CONFIRMATION),
        ("--execute", "--confirmation", "incorrect"),
    ),
)
def test_cli_execution_requires_exact_contemporaneous_phrase(
    monkeypatch, capsys, arguments
):
    monkeypatch.setattr(subject, "_load_pins", lambda *a: pytest.fail("input opened"))
    assert subject.main(list(arguments)) == 1
    assert "incorrect" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "error,code",
    (
        (subject.OwnerProfileUpgrade0057CommitUnknown, 3),
        (subject.OwnerProfileUpgrade0057ReadbackRequired, 4),
    ),
)
def test_cli_uncertain_outcome_has_fixed_distinct_code(
    monkeypatch, capsys, error, code
):
    monkeypatch.setattr(subject, "_load_pins", lambda *a: _pins())

    def unavailable(coroutine):
        coroutine.close()
        raise error("private database secret")

    monkeypatch.setattr(subject.asyncio, "run", unavailable)
    args = [
        "--reviewed-pins-file",
        "pins",
        "--owner-dsn-file",
        "dsn",
        "--ca-file",
        "ca",
        "--source-archive-file",
        "archive",
    ]
    assert subject.main(args) == code
    output = capsys.readouterr().out
    assert "private" not in output and "INCOMPLETE" in output


def test_private_reviewed_inputs_are_hash_bound_and_never_carry_a_dsn(
    tmp_path, monkeypatch
):
    pins = _pins()
    document = {"schema": "ctcc.owner_profile0057.reviewed_inputs.v1", **asdict(pins)}
    document["role_names"] = list(document["role_names"])
    document["role_oids"] = list(document["role_oids"])
    raw = subject.canonical(document)
    path = tmp_path / "pins.json"
    path.write_bytes(raw)
    checked = []
    monkeypatch.setattr(
        subject.private_inputs, "_require_private_file", lambda p: checked.append(p)
    )
    assert subject._load_pins(path, subject.sha(raw)) == pins
    assert checked == [path]
    with pytest.raises(subject.OwnerProfileUpgrade0057Refused):
        subject._load_pins(path, "0" * 64)
    assert "password" not in raw.decode() and "dsn" not in raw.decode()


def test_owner_server_hash_is_role_neutral_and_keeps_actual_profile_label():
    actor = (
        "ctcc_owner",
        "ctcc_owner",
        "none",
        16384,
        16385,
        NOW,
        "170006",
        "127.0.0.1",
        5432,
    )
    assert subject._server_sha(actor, subject.profiles.DB0056) != subject._server_sha(
        actor, subject.profiles.DB0057
    )
    assert subject._server_sha(actor, subject.profiles.DB0057) == subject._server_sha(
        ("other", "other", "none", 99999, *actor[4:]), subject.profiles.DB0057
    )


def _public_lab(monkeypatch, *, fail_readback=False, drift_before=False):
    events = []
    prepared = SimpleNamespace(
        url="inert",
        manifest_sha256="a" * 64,
        migration_sha256="b" * 64,
        source_head="0057",
        route_sha256="c" * 64,
    )
    snapshot = {"rows": [["historical_losses", 4, "e" * 64]]}
    acl = (("schema", 2200, 20001, 16384, "USAGE", False),)
    committed = False
    reads = 0

    def prepare(*args):
        events.append("SOURCE_VERIFIED")
        return prepared, object()

    async def resources(*args):
        return object(), object()

    async def route_read(*args, **kwargs):
        events.append("OWNER_TWO_READS")
        return {}

    async def roles(*args):
        events.append("ROLES_" + args[-1])
        if committed and fail_readback:
            raise ValueError("private driver error")
        return SimpleNamespace(stable_identity_sha256="d" * 64), ("same_actual_server",)

    async def inventory(*args):
        nonlocal reads
        reads += 1
        if drift_before and reads == 2:
            return {"rows": []}, acl
        return snapshot, acl

    async def commit(*args):
        nonlocal committed
        events.append("ONE_COMMIT")
        committed = True
        return snapshot, subject.sha(subject.canonical(acl))

    monkeypatch.setattr(subject, "_prepare", prepare)
    monkeypatch.setattr(subject, "_configured_resources", resources)
    monkeypatch.setattr(subject.route, "preflight_owner_route_0056_v1", route_read)
    monkeypatch.setattr(subject, "_checked_preflight", lambda *a: None)
    monkeypatch.setattr(subject, "_role_observation", roles)
    monkeypatch.setattr(subject, "_readonly_inventory", inventory)
    monkeypatch.setattr(subject, "_commit_once", commit)
    return events


@pytest.mark.asyncio
async def test_public_default_is_read_only_and_returns_no_execution_or_account_claim(
    monkeypatch,
):
    events = _public_lab(monkeypatch)
    result = await subject.install_reviewed_owner_profile_0057_v1(
        _pins(),
        owner_dsn_file=Path("private"),
        ca_file=Path("ca"),
        source_archive_file=Path("source"),
    )
    assert result["status"] == "DRY_RUN_PASS" and "ONE_COMMIT" not in events
    assert events.count("SOURCE_VERIFIED") == 2
    assert (
        result["account_complete"]
        is result["execution_authority"]
        is result["order_writes"]
        is False
    )


@pytest.mark.asyncio
async def test_public_result_requires_independent_actual57_readback_after_single_commit(
    monkeypatch,
):
    events = _public_lab(monkeypatch)
    result = await subject.install_reviewed_owner_profile_0057_v1(
        _pins(),
        owner_dsn_file=Path("private"),
        ca_file=Path("ca"),
        source_archive_file=Path("source"),
        confirmation=subject.CONFIRMATION,
    )
    assert result["status"] == "PASS" and events.count("ONE_COMMIT") == 1
    assert (
        events.index("ROLES_0056")
        < events.index("ONE_COMMIT")
        < events.index("ROLES_0057")
    )
    assert (
        result["startup_admission"]
        is result["account_complete"]
        is result["execution_authority"]
        is False
    )


@pytest.mark.asyncio
async def test_public_postcommit_failure_preserves_terminal_outcome_without_second_upgrade(
    monkeypatch,
):
    events = _public_lab(monkeypatch, fail_readback=True)
    with pytest.raises(
        subject.OwnerProfileUpgrade0057ReadbackRequired,
        match="^owner_profile_0057_committed_readback_required$",
    ):
        await subject.install_reviewed_owner_profile_0057_v1(
            _pins(),
            owner_dsn_file=Path("private"),
            ca_file=Path("ca"),
            source_archive_file=Path("source"),
            confirmation=subject.CONFIRMATION,
        )
    assert events.count("ONE_COMMIT") == 1


@pytest.mark.asyncio
async def test_inventory_drift_around_three_role_observation_blocks_before_ddl(
    monkeypatch,
):
    events = _public_lab(monkeypatch, drift_before=True)
    with pytest.raises(subject.OwnerProfileUpgrade0057Refused):
        await subject.install_reviewed_owner_profile_0057_v1(
            _pins(),
            owner_dsn_file=Path("private"),
            ca_file=Path("ca"),
            source_archive_file=Path("source"),
            confirmation=subject.CONFIRMATION,
        )
    assert "ONE_COMMIT" not in events
