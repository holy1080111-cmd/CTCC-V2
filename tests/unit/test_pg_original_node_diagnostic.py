"""Synthetic helper IO canaries only; no PostgreSQL/native acceptance."""

import json
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from scripts import pg_original_node_diagnostic as diagnostic
from scripts import pg_original_node_trace as trace
from scripts import prospective_capture_custody as custody


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def phases(profile, *, failed=False):
    rows = []

    def add(event, **values):
        index = len(rows) + 1
        rows.append(
            {
                "schema": "ctcc.pg_original_node_phase_event.v1",
                "profile": profile,
                "nodeid": diagnostic.node_id(profile),
                "sequence": index,
                "utc_ns": index,
                "monotonic_ns": index,
                "event": event,
                **values,
            }
        )

    add("session_start")
    add("node_start")
    for when in ("setup", "call", "teardown"):
        add("phase_start", when=when)
        add(
            "phase_report",
            when=when,
            outcome="failed" if failed and when == "call" else "passed",
            duration_seconds=0.001,
        )
    add("node_finish")
    add("session_finish", exitstatus=1 if failed else 0)
    return rows


def sample(profile, *, final=False, child=True):
    return {
        "schema": "ctcc.pg_original_node_lock_observation.v1",
        "profile": profile,
        "final": final,
        "sequence": 1,
        "utc_ns": 1,
        "monotonic_ns": 1,
        "status": "OBSERVED",
        "observation": {
            "database_observed_at": "2026-10-25 00:00:00+00",
            "observer_pid": 10,
            "rows": [
                {
                    "pid": 11,
                    "datname": "ctcc_migration_test_" + "a" * 32 if child else "ctcc",
                    "database_oid": 123,
                    "relation_oid": 98765,
                    "lock_database_oid": 123,
                    "locktype": "relation",
                    "mode": "RowExclusiveLock",
                    "granted": True,
                    "blocking_pids": [],
                }
            ],
        },
    }


def xml(profile, *, failed=False):
    path, name = diagnostic.PROFILES[profile]
    body = (
        '<failure message="original synthetic guard refusal">synthetic traceback</failure>'
        if failed
        else ""
    )
    return (
        f'<testsuites><testsuite tests="1" failures="{1 if failed else 0}" errors="0" skipped="0"><testcase classname="{path.removesuffix(".py").replace("/", ".")}" name="{name}">{body}</testcase></testsuite></testsuites>'
    ).encode()


class NativeCommandObservations:
    """Synthetic Docker control IO only; the actual90s settler runs unchanged."""

    def __init__(self, run_id, resources):
        self.run_id = run_id
        self.visible = {
            kind: {name for candidate, name in resources if candidate == kind}
            for kind in ("container", "network")
        }
        self.calls = []
        self.stuck = False
        self.burn = None

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs["timeout"]))
        assert 0 < kwargs["timeout"] <= 90
        if args[:2] == ["docker", "run"]:
            name = args[args.index("--name") + 1]
            self.visible["container"].add(name)
            if self.burn:
                self.burn()
            return subprocess.CompletedProcess(args, 0)
        if args[:2] == ["docker", "logs"]:
            kwargs["stdout"].write(b"synthetic owned PostgreSQL server log\n")
            return subprocess.CompletedProcess(args, 0)
        assert args[0] == "docker"
        kind, operation = args[1:3]
        if operation == "ls":
            raw = (
                ("\n".join(sorted(self.visible[kind])) + "\n").encode()
                if self.visible[kind]
                else b""
            )
        elif operation == "inspect":
            name = args[-1]
            value = {"Name": "/" + name if kind == "container" else name}
            value["Config" if kind == "container" else "Labels"] = (
                {"Labels": {"org.ctcc.validation.run": self.run_id}}
                if kind == "container"
                else {"org.ctcc.validation.run": self.run_id}
            )
            raw = json.dumps(
                {"org.ctcc.validation.run": self.run_id}
                if "--format" in args
                else [value]
            ).encode()
        elif operation == "rm":
            if not self.stuck:
                self.visible[kind].discard(args[-1])
            raw = b"removed\n"
        else:
            raise AssertionError("unreviewed_synthetic_control_command")
        return subprocess.CompletedProcess(args, 0, stdout=raw, stderr=b"")


@pytest.fixture
def original_io(tmp_path):
    profile = next(iter(diagnostic.PROFILES))
    run_id = "a" * 12
    output = tmp_path / "output"
    tests = tmp_path / "tests"
    export = tmp_path / "export"
    for path in (output, tests, export):
        path.mkdir()
    resources = [
        ("network", f"ctcc-final-{run_id}-network"),
        ("container", f"ctcc-final-{run_id}-postgres"),
        ("container", f"ctcc-final-{run_id}-pg-original-observer"),
        ("container", f"ctcc-final-{run_id}-pg-original-case"),
    ]
    (output / "pg-original-case.log").write_bytes(
        b"original isolated synthetic traceback\n"
    )
    (output / "pg-original-migration-identity.log").write_bytes(
        encoded(
            {
                "schema": "ctcc.migration_identity.v1",
                "status": "PASS",
                "source_head": "0051",
                "database_head": "0051",
                "migration_sha256": "1" * 64,
                "execution_authority": False,
            }
        )
    )
    (tests / diagnostic.PHASE_FILE).write_bytes(
        b"".join(encoded(row) for row in phases(profile))
    )
    (tests / diagnostic.JUNIT_FILE).write_bytes(xml(profile))
    (tests / diagnostic.SAMPLE_FILE).write_bytes(encoded(sample(profile)))
    (tests / diagnostic.FINAL_FILE).write_bytes(encoded(sample(profile, final=True)))
    identity = {
        "status": "PASS",
        "node_id": diagnostic.node_id(profile),
        "node_source_sha256": "2" * 64,
    }
    io = SimpleNamespace(
        output=output,
        tests=tests,
        export=export,
        resources=resources,
        run_id=run_id,
        profile=profile,
        identity=identity,
        clock=[10.0],
        state={"deadline": None, "observer_attempted": True},
        steps=[{"name": name, "exit_code": 0} for name in diagnostic.REQUIRED_STAGES],
    )
    io.command = NativeCommandObservations(run_id, resources)

    def finish():
        return diagnostic.finalize_original_node(
            output=output,
            export=export,
            tests=tests,
            resources=resources,
            run_id=run_id,
            files={diagnostic.PROFILES[profile][0]: {"sha256": "2" * 64}},
            identity=identity,
            steps=io.steps,
            profile=profile,
            network=f"ctcc-final-{run_id}-network",
            postgres=f"ctcc-final-{run_id}-postgres",
            image="sha256:" + "3" * 64,
            state=io.state,
            clock=lambda: io.clock[0],
            command=io.command,
        )

    io.finish = finish
    return io


def test_actual_finalizer_settles_writers_then_retains_actual_safe_trace(original_io):
    io = original_io
    assert len(io.finish()) == 64
    assert io.identity["status"] == "PASS"
    assert io.identity["owned_writers_verified_absent"] is True
    assert (io.export / "pg-original-case.log").read_bytes() == (
        io.output / "pg-original-case.log"
    ).read_bytes()
    assert io.identity["full_postgresql_acceptance"] is False
    assert io.identity["native_baseline_admission"] is False
    assert io.identity["execution_authority"] is False
    assert io.state["deadline"] == 100.0


def test_live_owned_writer_refuses_all_raw_reads_before_artifact_inventory(
    monkeypatch, original_io
):
    io = original_io
    io.command.stuck = True
    reads = []

    def forbidden(*args, **kwargs):
        reads.append(True)
        raise AssertionError("must not inspect mutable artifact")

    monkeypatch.setattr(custody, "stable_read", forbidden)
    io.finish()
    assert (
        io.identity["status"] == "FAIL"
        and io.identity["artifact_reads_refused"] is True
    )
    assert reads == [] and not (io.export / "pg-original-case.log").exists()


def test_final_observer_and_owned_cleanup_share_original_deadline(original_io):
    io = original_io
    io.command.burn = lambda: io.clock.__setitem__(0, 101.0)
    io.finish()
    assert io.state["deadline"] == 100.0
    assert (
        io.identity["status"] == "FAIL"
        and io.identity["artifact_reads_refused"] is True
    )
    assert len(io.command.calls) == 1


@pytest.mark.parametrize(
    "missing",
    [
        diagnostic.JUNIT_FILE,
        diagnostic.PHASE_FILE,
        diagnostic.SAMPLE_FILE,
        diagnostic.FINAL_FILE,
    ],
)
def test_missing_report_preserves_safe_original_trace_but_cannot_pass(
    original_io, missing
):
    io = original_io
    (io.tests / missing).unlink()
    io.finish()
    assert io.identity["status"] == "FAIL"
    assert (io.export / "pg-original-case.log").is_file()


def test_original_failed_case_stays_failed_with_traceback_and_phase_evidence(
    original_io,
):
    io = original_io
    (io.tests / diagnostic.JUNIT_FILE).write_bytes(xml(io.profile, failed=True))
    (io.tests / diagnostic.PHASE_FILE).write_bytes(
        b"".join(encoded(row) for row in phases(io.profile, failed=True))
    )
    io.steps[-1]["exit_code"] = 1
    io.finish()
    assert io.identity["status"] == "FAIL"
    assert io.identity["original_node_result"]["junit"]["failures"] == 1
    assert (io.export / "pg-original-case.log").is_file()


def test_late_passing_junit_after_case_timeout_cannot_restore_acceptance(original_io):
    io = original_io
    io.steps[-1].update(exit_code=None, failure_type="TimeoutExpired")
    io.finish()
    assert io.identity["status"] == "FAIL"
    assert io.identity["accepted_original_node_cases"] == 0
    assert (io.export / diagnostic.JUNIT_FILE).is_file()


def test_secret_like_header_refuses_trace_export_without_erasing_private_file(
    original_io,
):
    io = original_io
    private = b"OK-ACCESS-KEY: explicit-synthetic-header-not-a-real-key\n"
    (io.output / "pg-original-case.log").write_bytes(private)
    io.finish()
    assert io.identity["status"] == "FAIL"
    assert not (io.export / "pg-original-case.log").exists()
    assert (io.output / "pg-original-case.log").read_bytes() == private


@pytest.mark.parametrize("damage", ["other_node", "duplicate_case", "skip", "entity"])
def test_junit_strict_original_single_case_boundary(damage):
    profile = next(iter(diagnostic.PROFILES))
    good = xml(profile)
    assert diagnostic.junit_result(good, profile=profile)["complete_pass"] is True
    if damage == "other_node":
        bad = good.replace(
            diagnostic.PROFILES[profile][1].encode(), b"wrong_original_node"
        )
    elif damage == "duplicate_case":
        bad = good.replace(b"</testcase>", b"</testcase><testcase/>")
    elif damage == "skip":
        bad = good.replace(b'skipped="0"', b'skipped="1"').replace(
            b"</testcase>", b"<skipped/></testcase>"
        )
    else:
        bad = b'<!DOCTYPE testsuites [<!ENTITY x "bad">]>' + good
    if damage == "skip":
        assert diagnostic.junit_result(bad, profile=profile)["complete_pass"] is False
    else:
        with pytest.raises(ValueError):
            diagnostic.junit_result(bad, profile=profile)


@pytest.mark.parametrize(
    "damage", ["duplicate", "bool_sequence", "missing_call", "wrong_profile"]
)
def test_phase_journal_is_not_substituted_for_missing_execution(damage):
    profile = next(iter(diagnostic.PROFILES))
    rows = phases(profile)
    assert (
        diagnostic.phase_result(
            b"".join(encoded(row) for row in rows), profile=profile
        )["complete_pass"]
        is True
    )
    if damage == "duplicate":
        rows[3]["sequence"] = rows[2]["sequence"]
    elif damage == "bool_sequence":
        rows[0]["sequence"] = True
    elif damage == "missing_call":
        rows = [row for row in rows if row.get("when") != "call"]
    else:
        rows[2]["profile"] = "not-an-allowed-profile"
    if damage == "missing_call":
        for index, row in enumerate(rows, start=1):
            row["sequence"] = index
        assert (
            diagnostic.phase_result(
                b"".join(encoded(row) for row in rows), profile=profile
            )["complete_pass"]
            is False
        )
    else:
        with pytest.raises(ValueError):
            diagnostic.phase_result(
                b"".join(encoded(row) for row in rows), profile=profile
            )


def test_child_database_lock_oids_are_kept_without_wrong_parent_resolution():
    profile = next(iter(diagnostic.PROFILES))
    value = sample(profile)
    assert (
        diagnostic.samples_result(encoded(value), profile=profile, final=False)[
            "complete"
        ]
        is True
    )
    value["observation"]["rows"][0]["relation_name"] = "public.wrong_parent_relation"
    with pytest.raises(ValueError):
        diagnostic.samples_result(encoded(value), profile=profile, final=False)


def test_original_case_command_uses_exact_fixed_node_and_unchanged_cap(tmp_path):
    profile = next(iter(diagnostic.PROFILES))
    stages = []
    resources = []

    class Run:
        def command(self, name, args, **options):
            stages.append((name, args, options))

    def container(name, args, **options):
        stages.append((name, args, options))

    diagnostic.run_original_node(
        container,
        Run(),
        profile=profile,
        resources=resources,
        run_id="a" * 12,
        network="ctcc-final-" + "a" * 12 + "-network",
        postgres="ctcc-final-" + "a" * 12 + "-postgres",
        image="sha256:" + "1" * 64,
        tests=tmp_path,
        state={},
    )
    name, args, options = stages[-1]
    assert name == "pg-original-case" and options["timeout"] == 300
    assert args[-1] == diagnostic.node_id(profile)
    assert "faulthandler_timeout=30" in args and "no:faulthandler" not in args
    assert all(
        value in args
        for value in (
            "ENVIRONMENT=test",
            "TRADING_MODE=analysis_only",
            "LIVE_TRADING=false",
            "OKX_LIVE_ALLOW_ORDER_WRITES=false",
            "OKX_DEMO_ALLOW_ORDER_WRITES=false",
            "OKX_DEMO_AUTO_EXECUTION=false",
        )
    )
    assert len(resources) == 2 and all(
        name.startswith("ctcc-final-" + "a" * 12 + "-") for _, name in resources
    )
    with pytest.raises(ValueError):
        diagnostic.node_id("tests/arbitrary.py::test_other")


def test_actual_passive_plugin_fsyncs_each_phase_and_closes_without_test_mutation(
    monkeypatch, tmp_path
):
    profile = next(iter(diagnostic.PROFILES))
    node = diagnostic.node_id(profile)
    monkeypatch.setenv("CTCC_PG_DIAGNOSTIC_PROFILE", profile)
    monkeypatch.setattr(trace, "RESULTS", tmp_path)
    config = SimpleNamespace(args=[node], option=SimpleNamespace(collectonly=False))
    item = SimpleNamespace(nodeid=node)
    session = SimpleNamespace(config=config)
    trace.pytest_configure(config)
    try:
        trace.pytest_sessionstart(session)
        trace.pytest_runtest_logstart(node, None)
        trace.pytest_runtest_setup(item)
        trace.pytest_runtest_logreport(
            SimpleNamespace(nodeid=node, when="setup", outcome="passed", duration=0.01)
        )
        trace.pytest_runtest_call(item)
        trace.pytest_runtest_logreport(
            SimpleNamespace(nodeid=node, when="call", outcome="passed", duration=0.01)
        )
        trace.pytest_runtest_teardown(item, None)
        trace.pytest_runtest_logreport(
            SimpleNamespace(
                nodeid=node, when="teardown", outcome="passed", duration=0.01
            )
        )
        trace.pytest_runtest_logfinish(node, None)
        trace.pytest_sessionfinish(session, 0)
        assert trace._STREAM is None
        assert (
            diagnostic.phase_result(
                (tmp_path / diagnostic.PHASE_FILE).read_bytes(), profile=profile
            )["complete_pass"]
            is True
        )
    finally:
        trace.pytest_unconfigure(config)


@pytest.mark.parametrize("changed", ["profile", "node", "collectonly"])
def test_actual_plugin_rejects_nonfixed_execution_before_opening_journal(
    monkeypatch, tmp_path, changed
):
    profile = next(iter(diagnostic.PROFILES))
    node = diagnostic.node_id(profile)
    monkeypatch.setenv(
        "CTCC_PG_DIAGNOSTIC_PROFILE", "arbitrary" if changed == "profile" else profile
    )
    monkeypatch.setattr(trace, "RESULTS", tmp_path)
    config = SimpleNamespace(
        args=["tests/wrong.py::test_wrong" if changed == "node" else node],
        option=SimpleNamespace(collectonly=changed == "collectonly"),
    )
    with pytest.raises(RuntimeError):
        trace.pytest_configure(config)
    assert not (tmp_path / diagnostic.PHASE_FILE).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", [False, True])
async def test_actual_final_sampler_uses_only_one_readonly_native_query(
    monkeypatch, tmp_path, unavailable
):
    profile = next(iter(diagnostic.PROFILES))
    run_id = "a" * 12
    monkeypatch.setenv("CTCC_PG_DIAGNOSTIC_PROFILE", profile)
    monkeypatch.setenv("CTCC_PG_DIAGNOSTIC_RUN_ID", run_id)
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql+asyncpg://ctcc@ctcc-final-{run_id}-postgres:5432/ctcc",
    )
    monkeypatch.setattr(diagnostic, "RESULTS", tmp_path)
    events = []

    class Transaction:
        async def __aenter__(self):
            events.append("read_only_begin")

        async def __aexit__(self, *args):
            events.append("read_only_end")

    class Connection:
        def transaction(self, **options):
            assert options == {"readonly": True}
            return Transaction()

        async def fetchval(self, query):
            assert query == diagnostic.LOCK_QUERY
            events.append("query")
            if unavailable:
                raise RuntimeError("explicit_synthetic_observation_unavailable")
            return json.dumps(sample(profile, final=True)["observation"])

        async def close(self, **options):
            assert options == {"timeout": 2}
            events.append("closed")

    async def connect(**options):
        assert (
            options["host"] == f"ctcc-final-{run_id}-postgres"
            and options["database"] == "ctcc"
            and options["user"] == "ctcc"
        )
        assert options["timeout"] == options["command_timeout"] == 2
        events.append("connected")
        return Connection()

    provider = ModuleType("asyncpg")
    provider.connect = connect
    monkeypatch.setitem(sys.modules, "asyncpg", provider)
    result = await diagnostic.sample_locks(final=True)
    raw = (tmp_path / diagnostic.FINAL_FILE).read_bytes()
    assert events == [
        "connected",
        "read_only_begin",
        "query",
        "read_only_end",
        "closed",
    ]
    if unavailable:
        assert result == 1
        assert json.loads(raw)["status"] == "UNAVAILABLE"
        with pytest.raises(ValueError):
            diagnostic.samples_result(raw, profile=profile, final=True)
    else:
        assert result == 0
        assert (
            diagnostic.samples_result(raw, profile=profile, final=True)["complete"]
            is True
        )
