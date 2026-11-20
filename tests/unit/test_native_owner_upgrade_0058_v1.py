"""Owner upgrade failure semantics with declared fake transport; no SQL or venue."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import native_owner_upgrade_0058_v1 as subject
from tests.unit import test_native_owner_route_preflight_0056_v1 as prior_route
from tests.unit.test_native_owner_upgrade_0057_v1 import _pins as old_pins

prepared_files = prior_route.prepared_files


def _pins(head="0057"):
    old = old_pins()
    return subject.OwnerProfileUpgrade0058PinsV1(
        old.source_commit,
        old.source_tree,
        old.source_archive_sha256,
        replace(old.route, database_head=head),
        old.role_names,
        old.role_oids,
        None if head == "0057" else "a" * 64,
    )


def _inventory(phase):
    rows = [["retained_history", 5, "a" * 64]]
    relations = [("alembic_version", 100, "r"), ("retained_history", 101, "r")]
    if phase:
        rows += [[name, 0, subject.sha(b"")] for name in subject.ADDED_TABLES]
        relations += [
            (name, 200 + offset, "r")
            for offset, name in enumerate(subject.ADDED_TABLES)
        ]
    return {
        "relations": tuple(relations),
        "snapshot": {
            "rows": rows,
            "schema_sha256": ("c" if phase else "b") * 64,
            "role_state_sha256": "d" * 64,
        },
        "logical": {
            "definitions_sha256": ("e" if phase else "f") * 64,
            "acl_sha256": "1" * 64,
        },
    }


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return False


class _Transaction:
    def __init__(self, connection, *, nested=False, commit_error=None):
        self.connection, self.nested, self.commit_error = (
            connection,
            nested,
            commit_error,
        )

    async def rollback(self):
        self.connection.events.append(
            "SAVEPOINT_ROLLBACK" if self.nested else "ROLLBACK"
        )
        self.connection.phase = False

    async def commit(self):
        self.connection.events.append("COMMIT")
        if self.commit_error:
            raise self.commit_error(
                "private password and database route must not escape"
            )


class _Connection:
    def __init__(self, events, *, commit_error=None):
        self.events, self.phase, self.migrations = events, False, 0
        self.transaction = _Transaction(self, commit_error=commit_error)

    async def begin(self):
        self.events.append("BEGIN")
        return self.transaction

    async def begin_nested(self):
        self.events.append("SAVEPOINT")
        return _Transaction(self, nested=True)

    async def exec_driver_sql(self, sql):
        self.events.append("SETTING_OR_LOCK")

    async def execute(self, *args):
        self.events.append("ADVISORY_LOCK")

    async def run_sync(self, function, script):
        assert function is subject._migrate
        self.migrations += 1
        self.events.append("DDL" + str(self.migrations))
        self.phase = True


def _transaction_fixture(monkeypatch, *, commit_error=None, fail_at=None):
    events = []
    connection = _Connection(events, commit_error=commit_error)
    owner = SimpleNamespace(connect=lambda: _Context(connection))

    async def relations(*args, **kwargs):
        events.append("TABLE_LOCKS")
        return _inventory(False)["relations"]

    async def held(actual, pins, head):
        assert actual is connection
        assert head == ("0058" if connection.phase else "0057")
        events.append("READ_" + head)
        if (
            fail_at
            and len([e for e in events if e == "READ_0058"]) == fail_at
            and head == "0058"
        ):
            raise RuntimeError("private catalog must not escape")
        return _inventory(connection.phase)

    monkeypatch.setattr(subject.parent, "_relations", relations)
    monkeypatch.setattr(subject, "_held_inventory", held)
    monkeypatch.setattr(
        subject.route,
        "_source_for_profile",
        lambda profile: SimpleNamespace(
            actual_source_head="0058",
            manifest_sha256=_pins().route.source_manifest_sha256,
            execution_authority=False,
        ),
    )
    return events, connection, owner


@pytest.mark.asyncio
async def test_one_commit_occurs_only_after_real_savepoint_rollback_and_two_matching_ddl_observations(
    monkeypatch,
):
    events, connection, owner = _transaction_fixture(monkeypatch)
    result = await subject._commit_once(object(), _pins(), owner, _inventory(False))
    assert result == _inventory(True)
    assert connection.migrations == 2
    important = [
        event
        for event in events
        if event
        in {
            "BEGIN",
            "SAVEPOINT",
            "SAVEPOINT_ROLLBACK",
            "DDL1",
            "DDL2",
            "COMMIT",
            "ROLLBACK",
        }
    ]
    assert important == [
        "BEGIN",
        "SAVEPOINT",
        "DDL1",
        "SAVEPOINT_ROLLBACK",
        "DDL2",
        "COMMIT",
    ]
    assert events.count("READ_0057") == events.count("READ_0058") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", (1, 2))
async def test_ddl_or_readback_failure_rolls_back_and_never_commits(
    monkeypatch, fail_at
):
    events, connection, owner = _transaction_fixture(monkeypatch, fail_at=fail_at)
    with pytest.raises(
        subject.OwnerProfileUpgrade0058Refused, match="^owner_profile_0058_refused$"
    ):
        await subject._commit_once(object(), _pins(), owner, _inventory(False))
    assert events[-1] == "ROLLBACK"
    assert "COMMIT" not in events
    assert connection.phase is False


@pytest.mark.asyncio
@pytest.mark.parametrize("error", (RuntimeError, asyncio.CancelledError))
async def test_uncertain_commit_never_rolls_back_or_retries_and_redacts_private_details(
    monkeypatch, error
):
    events, connection, owner = _transaction_fixture(monkeypatch, commit_error=error)
    with pytest.raises(
        subject.OwnerProfileUpgrade0058CommitUnknown,
        match="^owner_profile_0058_commit_unknown_do_not_retry$",
    ):
        await subject._commit_once(object(), _pins(), owner, _inventory(False))
    assert events[-1] == "COMMIT"
    assert events.count("COMMIT") == 1 and "ROLLBACK" not in events
    assert connection.migrations == 2


@pytest.mark.asyncio
async def test_stale_inventory_refuses_before_any_ddl(monkeypatch):
    events, connection, owner = _transaction_fixture(monkeypatch)
    expected = _inventory(False)
    expected["snapshot"]["rows"][0][1] = 6
    with pytest.raises(subject.OwnerProfileUpgrade0058Refused):
        await subject._commit_once(object(), _pins(), owner, expected)
    assert connection.migrations == 0 and events[-1] == "ROLLBACK"


@pytest.mark.parametrize(
    "mutation", ("old_row", "role", "old_oid", "missing_table", "new_row")
)
def test_any_retained_history_actor_relation_or_new_live_row_change_refuses(mutation):
    before, after = _inventory(False), _inventory(True)
    if mutation == "old_row":
        after["snapshot"]["rows"][0][2] = "9" * 64
    elif mutation == "role":
        after["snapshot"]["role_state_sha256"] = "9" * 64
    elif mutation == "old_oid":
        after["relations"] = (
            ("alembic_version", 100, "r"),
            ("retained_history", 999, "r"),
            *after["relations"][2:],
        )
    elif mutation == "missing_table":
        after["snapshot"]["rows"].pop()
    else:
        after["snapshot"]["rows"][-1][1] = 1
    with pytest.raises(subject.OwnerProfileUpgrade0058Refused):
        subject._preserved(before, after)


def test_catalog_reinstall_pin_binds_migration_and_live_source_spec(monkeypatch):
    prepared = SimpleNamespace(migration_sha256="a" * 64)
    original = subject._installed_pin(prepared, _inventory(True)["logical"])
    assert original != subject._installed_pin(
        SimpleNamespace(migration_sha256="b" * 64), _inventory(True)["logical"]
    )
    monkeypatch.setattr(subject.live, "SPEC_SHA256", "c" * 64)
    assert original != subject._installed_pin(prepared, _inventory(True)["logical"])
    changed_live = subject._installed_pin(prepared, _inventory(True)["logical"])
    monkeypatch.setattr(
        subject.financial, "live_financial_epoch_spec_sha256_v1", lambda: "d" * 64
    )
    assert changed_live != subject._installed_pin(prepared, _inventory(True)["logical"])


@pytest.mark.parametrize("head", ("0055", "0056", "0059", "58", None))
def test_no_other_physical_head_can_enter_owner58(head):
    with pytest.raises(subject.OwnerProfileUpgrade0058Refused):
        subject._pins(
            replace(_pins(), route=replace(_pins().route, database_head=head))
        )


@pytest.mark.parametrize(
    "head,pin", (("0057", "a" * 64), ("0058", None), ("0058", "A" * 64))
)
def test_source57_requires_no_installed_pin_and_source58_requires_exact_reviewed_pin(
    head, pin
):
    with pytest.raises(subject.OwnerProfileUpgrade0058Refused):
        subject._pins(replace(_pins(head), installed_catalog_sha256=pin))


def test_only_explicit58_owner_route_adds_physical57_58_without_changing_default(
    monkeypatch,
):
    # Unknown profile/head refuses before any private route or SQL. Existing
    # source/transport positive coverage remains in the separate route suite.
    for head in ("0057", "0058"):
        with pytest.raises(subject.route.OwnerRoutePreflightError):
            subject.route._prepare(
                replace(_pins().route, database_head=head),
                owner_dsn_file=Path("absent"),
                ca_file=Path("absent"),
            )


@pytest.mark.parametrize("head", ("0057", "0058"))
def test_explicit58_route_accepts_only_reviewed_actual58_source_and_pinned_parent_or_head(
    prepared_files, monkeypatch, head
):
    pins, secret, ca, source = prepared_files
    pins = replace(pins, database_head=head)
    source.actual_source_head = "0058"
    seen = []

    def current(profile):
        seen.append(profile)
        return source

    monkeypatch.setattr(subject.route, "_source_for_profile", current)
    actual = subject.route._prepare(
        pins, owner_dsn_file=secret, ca_file=ca, profile=subject.profiles.DB0058
    )
    assert actual.source_head == "0058" and seen == [subject.profiles.DB0058]
    with pytest.raises(subject.route.OwnerRoutePreflightError):
        subject.route._prepare(pins, owner_dsn_file=secret, ca_file=ca)


def test_cli_missing_or_implicit_write_confirmation_is_fixed_redacted_refusal(capsys):
    assert subject.main(["--execute", "--confirmation", "UPGRADE"]) == 1
    assert (
        capsys.readouterr().out
        == '{"status":"REFUSED","code":"owner_profile_0058_refused"}\n'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("already_installed", (False, True))
async def test_dry_run_or_exact_reinstall_never_calls_ddl(
    monkeypatch, already_installed
):
    pins = _pins("0058" if already_installed else "0057")
    prepared = SimpleNamespace(
        migration_sha256="a" * 64,
        source_head="0058",
        manifest_sha256=pins.route.source_manifest_sha256,
    )
    inventory = _inventory(already_installed)
    if already_installed:
        pins = replace(
            pins,
            installed_catalog_sha256=subject._installed_pin(
                prepared, inventory["logical"]
            ),
        )
    events = []

    async def resources(*args):
        return object(), object()

    async def observe(*args):
        return SimpleNamespace(stable_identity_sha256="a" * 64), ("same_actor",)

    async def read_inventory(*args):
        return deepcopy(inventory)

    async def preflight(*args, **kwargs):
        return {}

    async def forbidden(*args):
        events.append("DDL")
        raise AssertionError("read-only path attempted DDL")

    monkeypatch.setattr(subject, "_prepare", lambda *args: (prepared, object()))
    monkeypatch.setattr(subject.parent, "_configured_resources", resources)
    monkeypatch.setattr(subject.route, "preflight_owner_route_0056_v1", preflight)
    monkeypatch.setattr(subject, "_checked_preflight", lambda *args: None)
    monkeypatch.setattr(subject.parent, "_same_source", lambda *args: None)
    monkeypatch.setattr(subject, "_inventory", read_inventory)
    monkeypatch.setattr(subject, "_role_observation", observe)
    monkeypatch.setattr(subject, "_commit_once", forbidden)
    result = await subject.install_reviewed_owner_profile_0058_v1(
        pins,
        owner_dsn_file=Path("private"),
        ca_file=Path("private"),
        source_archive_file=Path("archive"),
        confirmation=subject.CONFIRMATION if already_installed else None,
    )
    assert result["status"] == (
        "ALREADY_INSTALLED_READBACK_PASS" if already_installed else "DRY_RUN_PASS"
    )
    assert result["order_writes"] is result["execution_authority"] is False
    assert result["ddl_rehearsal_executed"] is False and events == []
