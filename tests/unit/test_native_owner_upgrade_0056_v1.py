"""Owner upgrade state transitions using synthetic files and no database."""

from __future__ import annotations

import hashlib
import io
import tarfile
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import native_owner_upgrade_0056_v1 as subject

_CLUSTER = "12345678901234567890"


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _pins(manifest_sha="a" * 64):
    route = subject.OwnerRoutePinsV1(
        source_manifest_sha256=manifest_sha,
        ca_sha256="b" * 64,
        cluster_system_identifier_sha256=_sha(_CLUSTER.encode("ascii")),
        host="synthetic.example.invalid",
        port=5432,
        database="ctcc_synthetic",
        owner="ctcc_owner",
        database_oid=16385,
        owner_oid=16384,
        database_head="0055",
    )
    return subject.OwnerUpgradePinsV1("c" * 40, "d" * 40, "e" * 64, route)


def _row(pins, head, read_only="off"):
    return (
        pins.route.owner,
        pins.route.owner,
        pins.route.database,
        pins.route.port,
        pins.route.owner_oid,
        pins.route.database_oid,
        pins.route.owner_oid,
        True,
        "TLSv1.3",
        read_only,
        head,
        _CLUSTER,
    )


def test_default_cli_refuses_before_opening_any_input(capsys, monkeypatch):
    monkeypatch.setattr(subject, "_load_pins", lambda *a: pytest.fail("input opened"))
    assert subject.main([]) == 1
    assert capsys.readouterr().out.strip() == (
        '{"status":"REFUSED","code":"native_owner_0056_upgrade_refused"}'
    )


@pytest.mark.parametrize(
    ("error", "exit_code", "terminal"),
    (
        (
            subject.NativeOwnerUpgradeCommitUnknown,
            3,
            '{"status":"INCOMPLETE","code":"native_owner_0056_commit_outcome_unknown"}',
        ),
        (
            subject.NativeOwnerUpgradePostcommitIncomplete,
            4,
            '{"status":"INCOMPLETE","code":"native_owner_0056_committed_readback_required"}',
        ),
    ),
)
def test_cli_commit_uncertainty_has_distinct_fixed_terminal(
    capsys, monkeypatch, error, exit_code, terminal
):
    monkeypatch.setattr(subject, "_load_pins", lambda *args: _pins())

    def stop_before_any_runtime(coroutine):
        coroutine.close()
        raise error("private database detail")

    monkeypatch.setattr(subject.asyncio, "run", stop_before_any_runtime)
    assert subject.main(["--upgrade-reviewed-native-owner-0055-to-0056"]) == exit_code
    assert capsys.readouterr().out.strip() == terminal


def test_archive_reconstructs_git_tree_and_checks_every_manifest_row(
    tmp_path, monkeypatch
):
    root = tmp_path / "source"
    root.mkdir()
    code = b"print('synthetic')\n"
    manifest = _sha(code).encode("ascii") + b"  a.py\n"
    (root / "MANIFEST.sha256").write_bytes(manifest)
    archive_path = tmp_path / "source.tar"
    with tarfile.open(archive_path, "w", pax_headers={"comment": "c" * 40}) as tar:
        for name, raw in (("a.py", code), ("MANIFEST.sha256", manifest)):
            info = tarfile.TarInfo(name)
            info.mode = 0o644
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    with tarfile.open(archive_path, "r:") as tar:
        tree = subject.archive_checks.archive_tree(tar)
    pins = subject.OwnerUpgradePinsV1(
        "c" * 40,
        tree,
        _sha(archive_path.read_bytes()),
        _pins(_sha(manifest)).route,
    )
    monkeypatch.setattr(subject, "_SOURCE", root)
    subject._verify_reviewed_archive(pins, archive_path)
    (root / "MANIFEST.sha256").write_bytes(b"0" * 64 + b"  a.py\n")
    with pytest.raises(subject.NativeOwnerUpgradeRefusal):
        subject._verify_reviewed_archive(pins, archive_path)


@pytest.mark.parametrize("changed", ["owner", "ssl", "head", "read_only", "cluster"])
def test_transaction_profile_denies_any_changed_identity(changed):
    pins = _pins()
    row = list(_row(pins, "0055"))
    index = {"owner": 1, "ssl": 7, "head": 10, "read_only": 9, "cluster": 11}[changed]
    row[index] = "changed"
    with pytest.raises(subject.NativeOwnerUpgradeRefusal):
        subject._checked_profile(tuple(row), pins.route, "0055", read_only=False)


@pytest.mark.parametrize("head", [("0055",), ("0056",)])
def test_migration_graph_rechecks_exact_predecessor_and_source_scope(monkeypatch, head):
    events = []
    before, after = object(), object()

    def check_scope(connection, root, scope):
        events.append("BEFORE_SCOPE" if scope is before else "AFTER_SCOPE")

    class Migration:
        def get_current_heads(self):
            return head

        def begin_transaction(self):
            return nullcontext()

        def run_migrations(self):
            events.append("MIGRATE")
            options["fn"](head, None)

    def configure(connection, *, opts):
        nonlocal options
        options = opts
        events.append("MIGRATION_CONTEXT")
        return Migration()

    def upgrade_revs(target, heads):
        assert target == "0056" and heads == ("0055",)
        events.append("EXACT_0056_REVISION")

    options = {}
    monkeypatch.setattr(subject.scopes, "verify_database_scope", check_scope)
    monkeypatch.setattr(
        subject, "MigrationContext", SimpleNamespace(configure=configure)
    )
    monkeypatch.setattr(
        subject, "Operations", SimpleNamespace(context=lambda migration: nullcontext())
    )
    if head == ("0055",):
        subject._upgrade_exact_0055(
            object(), before, after, SimpleNamespace(_upgrade_revs=upgrade_revs)
        )
        assert events == [
            "BEFORE_SCOPE",
            "MIGRATION_CONTEXT",
            "MIGRATE",
            "EXACT_0056_REVISION",
            "AFTER_SCOPE",
        ]
    else:
        with pytest.raises(subject.NativeOwnerUpgradeRefusal):
            subject._upgrade_exact_0055(object(), before, after, object())
        assert events == ["BEFORE_SCOPE", "MIGRATION_CONTEXT"]


def _fake_commit(monkeypatch, *, fail_at=None):
    pins = _pins()
    events = []

    class Transaction:
        async def commit(self):
            events.append("COMMIT")
            if fail_at == "commit":
                raise RuntimeError("private database detail")

        async def rollback(self):
            events.append("ROLLBACK")

    class Connection:
        def __init__(self):
            self.reads = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def begin(self):
            events.append("BEGIN")
            return Transaction()

        async def exec_driver_sql(self, statement):
            events.append(statement)
            if fail_at == "lock" and statement == subject._LOCK_REVISION:
                raise RuntimeError("private database detail")

        async def execute(self, statement):
            if statement is subject._PROFILE_SQL:
                head = "0055" if self.reads == 0 else "0056"
                self.reads += 1
                events.append("PROFILE_" + head)
                return SimpleNamespace(one=lambda: _row(pins, head))
            assert statement is subject._OWNER_LOCK
            events.append("ADVISORY_LOCK")

        async def run_sync(self, fn, *args):
            events.append("MIGRATE")
            fn(None, *args)

    class Engine:
        def connect(self):
            return Connection()

        async def dispose(self):
            events.append("DISPOSE")
            if fail_at == "dispose":
                raise RuntimeError("private database detail")

    def create(*args, **kwargs):
        assert kwargs["poolclass"] is subject.NullPool
        assert kwargs["connect_args"]["timeout"] == 10
        events.append("ENGINE")
        return Engine()

    monkeypatch.setattr(subject, "create_async_engine", create)
    monkeypatch.setattr(
        subject,
        "_upgrade_exact_0055",
        lambda *args: events.append("CHECKED_0055_TO_0056"),
    )
    route = SimpleNamespace(url="synthetic", tls=object())
    return pins, (route, object(), object(), object()), events


@pytest.mark.asyncio
async def test_single_transaction_locks_checks_migrates_then_commits(monkeypatch):
    pins, prepared, events = _fake_commit(monkeypatch)
    await subject._commit_once(prepared, pins)
    assert events.index("ADVISORY_LOCK") < events.index(subject._LOCK_REVISION)
    assert events.index("PROFILE_0055") < events.index("MIGRATE")
    assert events.index("CHECKED_0055_TO_0056") < events.index("PROFILE_0056")
    assert events.index("PROFILE_0056") < events.index("COMMIT")
    assert events[-1] == "DISPOSE"
    assert events.count("COMMIT") == 1 and "ROLLBACK" not in events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "expected"),
    (
        ("lock", subject.NativeOwnerUpgradeRefusal),
        ("commit", subject.NativeOwnerUpgradeCommitUnknown),
        ("dispose", subject.NativeOwnerUpgradePostcommitIncomplete),
    ),
)
async def test_failure_stage_is_fixed_and_never_retries(monkeypatch, stage, expected):
    pins, prepared, events = _fake_commit(monkeypatch, fail_at=stage)
    with pytest.raises(expected) as caught:
        await subject._commit_once(prepared, pins)
    assert "private database detail" not in str(caught.value)
    assert events.count("COMMIT") == (0 if stage == "lock" else 1)
    assert events.count("ROLLBACK") == (1 if stage == "lock" else 0)
    assert events.count("DISPOSE") == 1


@pytest.mark.asyncio
async def test_postcommit_readback_failure_is_incomplete_not_second_upgrade(
    monkeypatch,
):
    pins = _pins()
    route = SimpleNamespace(
        url="synthetic",
        manifest_sha256=pins.route.source_manifest_sha256,
        migration_sha256="f" * 64,
        route_sha256="1" * 64,
    )
    prepared = (route, object(), object(), object())
    phases = []
    monkeypatch.setattr(subject, "_prepared", lambda *args: prepared)

    async def preflight(*args, **kwargs):
        phases.append("PREFLIGHT")
        return {
            "schema": "ctcc.native_owner_0056.route_preflight.v1",
            "status": "PASS",
            "source_head": "0056",
            "source_manifest_sha256": route.manifest_sha256,
            "migration_sha256": route.migration_sha256,
            "database_head": pins.route.database_head,
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

    async def commit(*args):
        phases.append("COMMITTED")

    async def readback(*args):
        phases.append("READBACK")
        raise RuntimeError("private database detail")

    monkeypatch.setattr(subject, "preflight_owner_route_0056_v1", preflight)
    monkeypatch.setattr(subject, "_commit_once", commit)
    monkeypatch.setattr(subject, "_independent_readback", readback)
    with pytest.raises(subject.NativeOwnerUpgradePostcommitIncomplete) as caught:
        await subject.upgrade_owner_0055_to_0056_v1(
            pins,
            owner_dsn_file=Path("synthetic-dsn"),
            ca_file=Path("synthetic-ca"),
            source_archive_file=Path("synthetic-archive"),
        )
    assert phases == ["PREFLIGHT", "COMMITTED", "READBACK"]
    assert "private database detail" not in str(caught.value)
