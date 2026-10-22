"""Pure closed-contract checks. No application/test helpers, Docker or PG runs."""

import ast
import asyncio
import hashlib
import io
import json
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from scripts import diagnose_pg_stage_budget_v1 as runner
from scripts import pg_phase_plugin_v1 as plugin
from scripts import pg_phase_policy_v1 as policy
from scripts import pg_wait_observer_v1 as observer
from scripts.diagnose_pg_stage_budget_v1 import export_measured_prefix
from scripts.pg_wait_redaction_v1 import PgWaitFormatterV1, PgWaitPolicyV1

_ABSENT = object()
_NAMESPACE_MODULES = (
    "tests",
    "tests.integration",
    "tests.integration.test_durable_migration_downgrade",
    "tests.integration.test_submission_reporting_repository",
    "tests.integration.test_qualification_ledger_repository",
    "tests.integration.test_submission_transport_denial_migration",
    "test_durable_migration_downgrade",
    "test_submission_reporting_repository",
    "test_qualification_ledger_repository",
    "test_submission_transport_denial_migration",
)
_PARENT_ATTRIBUTES = {
    "tests": ("integration",),
    "tests.integration": (
        "test_durable_migration_downgrade",
        "test_submission_reporting_repository",
        "test_qualification_ledger_repository",
        "test_submission_transport_denial_migration",
    ),
}


@contextmanager
def _isolated_namespace_cache():
    """Only the closed synthetic namespace; restore prior real objects exactly."""
    prior = {name: sys.modules.get(name, _ABSENT) for name in _NAMESPACE_MODULES}
    attributes = {
        (name, attr): getattr(prior[name], attr, _ABSENT)
        for name, attrs in _PARENT_ATTRIBUTES.items()
        if prior[name] is not _ABSENT
        for attr in attrs
    }
    paths = {
        name: tuple(module.__path__)
        for name, module in prior.items()
        if module is not _ABSENT and hasattr(module, "__path__")
    }
    snapshot = prior, attributes, paths
    try:
        for name in _NAMESPACE_MODULES:
            sys.modules.pop(name, None)
        yield snapshot
    finally:
        for name, module in prior.items():
            if module is _ABSENT:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        for (name, attr), value in attributes.items():
            parent = prior[name]
            if value is _ABSENT:
                parent.__dict__.pop(attr, None)
            else:
                setattr(parent, attr, value)


def _assert_namespace_restored(snapshot):
    prior, attributes, paths = snapshot
    assert all(sys.modules.get(name, _ABSENT) is value for name, value in prior.items())
    assert all(
        getattr(prior[name], attr, _ABSENT) is value
        for (name, attr), value in attributes.items()
    )
    assert all(
        tuple(sys.modules[name].__path__) == value for name, value in paths.items()
    )


def event(sequence=0, **updates):
    return {
        "sequence": sequence,
        "kind": "phase_start",
        "nodeid": policy.LAST_TWO[0],
        "phase": "setup",
        "monotonic_ns": sequence + 1,
        "duration_ns": 0,
        "outcome": "RUNNING",
        **updates,
    }


def test_exact_case_inventory():
    assert len(policy.EXPERIMENTS["A"]) == 2
    assert len(policy.EXPERIMENTS["B"]) == 14
    assert len(set(policy.EXPERIMENTS["A"] + policy.EXPERIMENTS["B"])) == 14
    assert sum(map(len, policy.EXPERIMENTS.values())) == 16
    assert policy.EXPERIMENTS["B"][-2:] == policy.EXPERIMENTS["A"]


def test_durable_prefix_survives_truncated_tail():
    first = policy.canonical(event())
    result = policy.read_phase_prefix(
        first + b'{"untrusted_secret_canary', experiment="A"
    )
    assert result["events"] == [event()]
    assert result["truncated_tail"]["bytes"] == 25
    assert result["complete"] is False
    assert "untrusted_secret_canary" not in json.dumps(result)


@pytest.mark.parametrize(
    "update",
    [
        {"sequence": True},
        {"sequence": 2},
        {"nodeid": "untrusted_secret_canary"},
        {"kind": "unknown"},
        {"phase": "untrusted_secret_canary"},
        {"monotonic_ns": -1},
        {"duration_ns": False},
        {"duration_ns": 1_200_000_000_001},
        {"outcome": "unknown"},
        {"unknown": "untrusted_secret_canary"},
    ],
    ids=[f"invalid-{n}" for n in range(10)],
)
def test_event_unknown_fields_and_types_deny(update):
    with pytest.raises(ValueError, match="PG_PHASE_DIAGNOSTIC_EVIDENCE_DENIED"):
        policy.read_phase_prefix(policy.canonical(event(**update)), experiment="A")


def test_duplicate_json_key_denied():
    raw = policy.canonical(event()).replace(
        b'"sequence":0', b'"sequence":0,"sequence":0'
    )
    with pytest.raises(ValueError):
        policy.read_phase_prefix(raw, experiment="A")


def junit_bytes(*, unexpected=False):
    cases = []
    for node in policy.LAST_TWO:
        path, name = node.split("::")
        cls = path.removesuffix(".py").replace("/", ".")
        cases.append(
            f'<testcase classname="{cls}" name="{name}" time="1.25" hostname="secret_canary">'
            '<failure message="secret_canary">secret_canary</failure>'
            "<system-out>secret_canary</system-out></testcase>"
        )
    if unexpected:
        cases.append(
            '<testcase classname="secret_canary" name="secret_canary" time="1"/>'
        )
    return (
        '<testsuites><testsuite hostname="secret_canary">'
        + "".join(cases)
        + "</testsuite></testsuites>"
    ).encode()


def test_junit_reconstructed_from_fixed_inventory_only():
    summary, clean = policy.redacted_junit(junit_bytes(), experiment="A")
    assert len(summary["cases"]) == 2 and summary["all_passed"] is False
    assert b"secret_canary" not in clean
    assert "secret_canary" not in json.dumps(summary)
    with pytest.raises(ValueError):
        policy.redacted_junit(junit_bytes(unexpected=True), experiment="A")


@pytest.mark.asyncio
async def test_test_helper_wrapper_preserves_task_await_and_return(monkeypatch):
    class Sink(io.BytesIO):
        def fileno(self):
            return 0

        def close(self):
            pass

    sink = Sink()
    monkeypatch.setattr(plugin, "_file", sink)
    monkeypatch.setattr(plugin, "_experiment", "A")
    monkeypatch.setattr(plugin, "_sequence", 0)
    monkeypatch.setattr(plugin, "_previous", 0)
    monkeypatch.setattr(plugin, "_node", policy.LAST_TWO[0])
    monkeypatch.setattr(plugin, "_wrapped", [])
    monkeypatch.setattr(plugin.os, "fsync", lambda _descriptor: None)
    owned = asyncio.current_task()
    value = object()

    async def original():
        assert asyncio.current_task() is owned
        await asyncio.sleep(0)
        assert asyncio.current_task() is owned
        return value

    module = SimpleNamespace(setup=original)
    plugin._wrap(module, "setup", "reports_setup")
    assert await module.setup() is value
    rows = policy.read_phase_prefix(sink.getvalue(), experiment="A")["events"]
    assert [row["kind"] for row in rows] == ["helper_start", "helper_end"]
    assert rows[-1]["outcome"] == "RETURNED"
    assert plugin._wrapped == [(module, "setup", original)]


def test_only_test_helper_wrappers_no_engine_or_native_factory_changes():
    tree = ast.parse(Path(plugin.__file__).read_bytes())
    method = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "pytest_collection_modifyitems"
    )
    literal = ast.unparse(method)
    assert "tests.integration." in literal
    for forbidden in (
        "app.database",
        "native_factory",
        "setprofile",
        "settrace",
        "listen",
        "create_task",
    ):
        assert forbidden not in literal


def test_missing_evidence_is_unknown_and_fail(tmp_path):
    private, export = tmp_path / "private", tmp_path / "export"
    private.mkdir()
    export.mkdir()
    result = export_measured_prefix(private, export, "B")
    assert result["status"] == "FAIL"
    assert (
        result["phase_prefix"] == result["wait_prefix"] == result["junit"] == "UNKNOWN"
    )
    assert list(export.iterdir()) == []


def test_partial_phase_evidence_exported_without_junit(tmp_path):
    private, export = tmp_path / "private", tmp_path / "export"
    private.mkdir()
    export.mkdir()
    (private / "phase-events.jsonl").write_bytes(
        policy.canonical(event()) + b'{"untrusted_secret_canary'
    )
    result = export_measured_prefix(private, export, "A")
    assert result["status"] == "FAIL" and len(result["phase_prefix"]) == 64
    assert result["junit"] == "UNKNOWN"
    assert result["phases_complete"] is False
    assert b"untrusted_secret_canary" not in (export / "A-phases.json").read_bytes()


def test_observer_sql_exports_only_server_hashes_not_query_or_role():
    path = Path(plugin.__file__).with_name("pg_wait_observer_v1.py")
    tree = ast.parse(path.read_bytes())
    sql = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "SQL"
    )
    assert "query_sha256" in sql and "query_bytes" in sql
    assert "usename" not in sql and "LIMIT 129" in sql
    assert "SELECT pid, datname, backend_type" in sql
    assert not any(line.strip() in {"query,", "query"} for line in sql.splitlines())


def test_workflow_closed_branch_parent_pinned_actions_and_export_only():
    root = Path(plugin.__file__).parents[1]
    text = (root / ".github/workflows/pg-stage-budget-diagnostic.yml").read_text()
    assert "timeout-minutes: 25" in text
    assert "workflow_dispatch" not in text and "pull_request" not in text
    assert "validation/pg-stage-budget-diagnostic-20261022" in text
    assert 'test "$(git rev-parse HEAD^)" = "$baseline"' in text
    assert "persist-credentials: false" in text
    assert (
        "/export/*" in text and "/test-results" not in text and "/private-" not in text
    )


def observer_result(count=2, **updates):
    return {
        "schema": "ctcc.pg_wait_observer_result.v1",
        "status": "PASS",
        "measured_sample_count": count,
        "observations_complete": True,
        "reason": "STOP_REQUEST_OBSERVED",
        "raw_sql_or_identity_exported": False,
        "execution_authority": False,
        "sampling_elapsed_monotonic_ns": 100,
        "sampling_budget_ns": 210_000_000_000,
        "connection_close_grace_seconds": 3,
        **updates,
    }


def actual_wait_frames():
    formatter = PgWaitFormatterV1(
        run_secret=b"x" * 32,
        policy=PgWaitPolicyV1(
            max_samples=64,
            max_rows=128,
            max_blockers_per_row=64,
            max_query_bytes=1048576,
            max_duration_ns=210_000_000_000,
        ),
    )
    row = {
        "pid": 123,
        "datname": "ctcc",
        "backend_type": "client backend",
        "state": "active",
        "wait_event_type": None,
        "wait_event": None,
        "blocking_pids": [],
        "query_sha256": "a" * 64,
        "query_bytes": 8,
        "command_category": "SELECT",
    }
    return [
        formatter.format_sample(
            [row], sample_index=index, elapsed_monotonic_ns=index + 1
        )
        for index in range(2)
    ]


@pytest.mark.parametrize(
    "bad_marker", [False, True], ids=["valid-marker", "denied-late-marker"]
)
def test_actual_formatter_multi_sample_jsonl_export_roundtrip(tmp_path, bad_marker):
    private, export = tmp_path / "private", tmp_path / "export"
    private.mkdir()
    export.mkdir()
    frames = actual_wait_frames()
    assert all(not raw.endswith(b"\n") for raw in frames)
    (private / "wait-samples.jsonl").write_bytes(
        b"".join(raw + b"\n" for raw in frames)
    )
    marker = observer_result(count=3 if bad_marker else 2)
    (private / "observer-result.json").write_bytes(json.dumps(marker).encode())
    result = export_measured_prefix(private, export, "A")
    assert len(result["wait_prefix"]) == 64 and result["status"] == "FAIL"
    value = json.loads((export / "A-wait-samples.json").read_bytes())
    assert len(value["samples"]) == 2
    assert (
        value["observer"] == "DENIED"
        if bad_marker
        else value["observer"]["status"] == "PASS"
    )
    assert result["observer_complete"] == ("UNKNOWN" if bad_marker else True)
    assert '"ctcc"' not in json.dumps(value["samples"])


def test_legacy_concatenated_non_jsonl_frames_deny(tmp_path):
    private, export = tmp_path / "private", tmp_path / "export"
    private.mkdir()
    export.mkdir()
    (private / "wait-samples.jsonl").write_bytes(b"".join(actual_wait_frames()))
    result = export_measured_prefix(private, export, "A")
    assert result["wait_prefix"] == "DENIED" and result["status"] == "FAIL"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_stage",
    ["connect", "fetch", "close"],
    ids=["connect-error", "sql-error", "close-error"],
)
async def test_observer_native_errors_export_no_raw_sql_or_traceback(
    monkeypatch, tmp_path, capsys, failure_stage
):
    secret = "UNREVIEWED_SQL_DSN_PASSWORD_TRACEBACK_CANARY"

    class Connection:
        async def fetch(self, *_args, **_kwargs):
            raise RuntimeError(secret)

        async def close(self):
            if failure_stage == "close":
                raise RuntimeError(secret)

    async def connect(*_args, **_kwargs):
        if failure_stage == "connect":
            raise RuntimeError(secret)
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", SimpleNamespace(connect=connect))
    monkeypatch.setattr(observer, "ROOT", tmp_path)
    monkeypatch.setenv("CTCC_PG_PHASE_EXPERIMENT", "A")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://ctcc@ctcc-final-0123456789ab-postgres:5432/ctcc",
    )
    assert await observer.observe() == 1
    result_raw = (tmp_path / "observer-result.json").read_bytes()
    assert json.loads(result_raw)["status"] == "FAIL"
    assert secret.encode() not in result_raw
    assert "Traceback" not in result_raw.decode()
    assert secret not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_expired_stop_cannot_turn_observer_pass(monkeypatch, tmp_path):
    class Connection:
        async def fetch(self, *_args, **_kwargs):
            raise AssertionError("expired_owner_must_not_query")

        async def close(self):
            return None

    async def connect(*_args, **_kwargs):
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", SimpleNamespace(connect=connect))
    monkeypatch.setattr(observer, "ROOT", tmp_path)
    monkeypatch.setenv("CTCC_PG_PHASE_EXPERIMENT", "A")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://ctcc@ctcc-final-0123456789ab-postgres:5432/ctcc",
    )
    (tmp_path / "observer-stop").write_bytes(b"STOP\n")
    values = iter([0, 211_000_000_000, 211_000_000_001])
    monkeypatch.setattr(observer.time, "monotonic_ns", lambda: next(values))
    assert await observer.observe() == 1
    result = json.loads((tmp_path / "observer-result.json").read_bytes())
    assert (
        result["status"] == "FAIL"
        and result["sampling_elapsed_monotonic_ns"] >= result["sampling_budget_ns"]
    )


@pytest.mark.parametrize("short_name", [False, True], ids=["qualified", "pytest-short"])
def test_actual_module_name_installation_is_required_and_measured(
    monkeypatch, short_name
):
    class Sink(io.BytesIO):
        def fileno(self):
            return 0

    sink = Sink()
    monkeypatch.setattr(plugin, "_file", sink)
    monkeypatch.setattr(plugin, "_experiment", "A")
    monkeypatch.setattr(plugin, "_sequence", 0)
    monkeypatch.setattr(plugin, "_previous", 0)
    monkeypatch.setattr(plugin, "_node", "SESSION")
    monkeypatch.setattr(plugin, "_wrapped", [])
    monkeypatch.setattr(plugin.os, "fsync", lambda _descriptor: None)
    names = {
        "test_durable_migration_downgrade": ("migrate",),
        "test_submission_transport_denial_migration": ("migrate", "_private_rows"),
        "test_submission_reporting_repository": ("setup",),
        "test_qualification_ledger_repository": ("initialize",),
    }
    target = None
    for short, attributes in names.items():
        name = "tests.integration." + short
        actual_name = (
            short
            if short_name and short == "test_submission_transport_denial_migration"
            else name
        )
        module = ModuleType(actual_name)
        module.__file__ = "/app/" + name.replace(".", "/") + ".py"
        for attribute in attributes:
            setattr(module, attribute, lambda *_args, **_kwargs: None)
        monkeypatch.setitem(sys.modules, actual_name, module)
        if short == "test_submission_transport_denial_migration":
            target = module
    items = [
        SimpleNamespace(nodeid=node, module=target, path=Path(target.__file__))
        for node in policy.LAST_TWO
    ]
    plugin.pytest_collection_modifyitems(items)
    result = policy.read_phase_prefix(sink.getvalue(), experiment="A")
    assert result["test_helper_instrumentation_complete"] is True
    assert len(plugin._wrapped) == 5
    monkeypatch.delitem(
        sys.modules, "tests.integration.test_submission_reporting_repository"
    )
    with pytest.raises(ValueError):
        plugin.pytest_collection_modifyitems(items)


def test_synthetic_pytest_namespace_collection_installs_real_collected_module(
    monkeypatch, tmp_path
):
    """Real pytest namespace import and hooks, synthetic sync helpers, no app/DB."""
    root = tmp_path / "namespace"
    integration = root / "tests/integration"
    integration.mkdir(parents=True)
    (root / "pytest.ini").write_text("[pytest]\naddopts=\n")
    sources = {
        "test_durable_migration_downgrade.py": "def migrate(connection, revision, direction):\n    return None\n",
        "test_qualification_ledger_repository.py": "def initialize(database, fixture):\n    return None\n",
        "test_submission_reporting_repository.py": (
            "from tests.integration import test_qualification_ledger_repository as fixtures\n"
            "def setup(database, fixture):\n    return fixtures.initialize(database, fixture)\n"
        ),
        "test_submission_transport_denial_migration.py": (
            "import pytest\n"
            "from tests.integration.test_durable_migration_downgrade import migrate\n"
            "from tests.integration import test_submission_reporting_repository as reports\n"
            "def _private_rows(*args):\n    return None\n"
            "@pytest.mark.parametrize('revision', ['0041'])\n"
            "def test_new_acknowledged_response_is_retained_only_as_ineligible_evidence(revision):\n"
            "    migrate(None, '0042', 'upgrade')\n    reports.setup(None, None)\n    _private_rows()\n"
            "@pytest.mark.parametrize('revision', ['0041'])\n"
            "def test_active_writer_prevents_partial_upgrade(revision):\n"
            "    migrate(None, '0042', 'upgrade')\n"
        ),
    }
    for name, text in sources.items():
        (integration / name).write_text(text)
    assert not (root / "tests/__init__.py").exists()
    assert not (integration / "__init__.py").exists()
    phase = tmp_path / "synthetic-phase.jsonl"

    @contextmanager
    def stream():
        with phase.open("xb") as output:
            yield output

    with monkeypatch.context() as isolated, _isolated_namespace_cache() as snapshot:
        isolated.setattr(plugin, "_phase_stream", stream)
        isolated.setattr(plugin, "_stack", ExitStack())
        isolated.setattr(plugin, "_file", None)
        isolated.setattr(plugin, "_sequence", 0)
        isolated.setattr(plugin, "_previous", 0)
        isolated.setattr(plugin, "_node", "SESSION")
        isolated.setattr(plugin, "_wrapped", [])
        isolated.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
        isolated.syspath_prepend(str(root))
        isolated.chdir(root)
        code = pytest.main(
            [
                "-c",
                str(root / "pytest.ini"),
                "--rootdir",
                str(root),
                "--confcutdir",
                str(root),
                "-p",
                "no:cacheprovider",
                "--ctcc-pg-phase-experiment=A",
                "-q",
                *policy.LAST_TWO,
            ],
            plugins=[plugin],
        )
        assert code == 0
        value = policy.read_phase_prefix(phase.read_bytes(), experiment="A")
        assert value["test_helper_instrumentation_complete"] is True
        assert value["all_expected_phases_passed"] is True
        actual = sys.modules["test_submission_transport_denial_migration"]
        assert actual.__name__ == "test_submission_transport_denial_migration"
        assert (
            Path(actual.__file__).resolve()
            == (integration / "test_submission_transport_denial_migration.py").resolve()
        )
    _assert_namespace_restored(snapshot)


@pytest.mark.parametrize("fail", [False, True], ids=["normal-return", "raised-error"])
def test_namespace_cache_exact_cleanup_including_preexisting_real_objects(
    fail, monkeypatch
):
    package = ModuleType("tests")
    package.__path__ = ["fixed-prior-tests-path"]
    parent = ModuleType("tests.integration")
    parent.__path__ = ["fixed-prior-integration-path"]
    helper = ModuleType("tests.integration.test_durable_migration_downgrade")
    package.integration = parent
    parent.test_durable_migration_downgrade = helper
    monkeypatch.setitem(sys.modules, "tests", package)
    monkeypatch.setitem(sys.modules, "tests.integration", parent)
    monkeypatch.setitem(
        sys.modules, "tests.integration.test_durable_migration_downgrade", helper
    )
    with (
        pytest.raises(ValueError, match="fixed_test_failure")
        if fail
        else _normal_return(),
        _isolated_namespace_cache() as snapshot,
    ):
        assert all(name not in sys.modules for name in _NAMESPACE_MODULES)
        sys.modules["tests.integration.test_durable_migration_downgrade"] = ModuleType(
            "tests.integration.test_durable_migration_downgrade"
        )
        prior, attributes, _paths = snapshot
        for name, attr in attributes:
            setattr(prior[name], attr, object())
        if fail:
            raise ValueError("fixed_test_failure")
    _assert_namespace_restored(snapshot)


@contextmanager
def _normal_return():
    yield


def test_missing_experiment_rejected_before_phase_file_IO(monkeypatch):
    monkeypatch.setattr(plugin, "_experiment", None)
    monkeypatch.setattr(
        plugin, "_phase_stream", lambda: pytest.fail("journal_IO_before_option_guard")
    )
    with pytest.raises(ValueError):
        plugin.pytest_configure(SimpleNamespace(getoption=lambda _option: None))


@pytest.mark.parametrize(
    "failed_stage",
    ["build", "network"],
    ids=["build-before-resources", "network-attempt-cleanup"],
)
def test_early_startup_failure_stays_fail_and_exports_only_safe_artifacts(
    monkeypatch, tmp_path, capsys, failed_stage
):
    archive = tmp_path / "source.tar"
    archive.write_bytes(b"fixed-pure-not-a-real-tar")
    output = tmp_path / "result"
    files = {name: {"sha256": "a" * 64} for name in runner.EXECUTED}
    files["MANIFEST.sha256"] = {"sha256": "b" * 64}
    cleaned = []

    class FakeRun:
        def __init__(self, path, **_kwargs):
            self.output, self.steps, self.deadline = path, [], time.monotonic() + 1080

        def command(self, name, _argv, **_kwargs):
            self.steps.append(
                {"name": name, "exit_code": None, "failure_type": "RuntimeError"}
            )
            if name == "build" and failed_stage == "build" or name.endswith("-network"):
                raise RuntimeError("RAW_SQL_DSN_PASSWORD_TRACEBACK_CANARY")
            if name == "image-identity":
                (self.output / "image-identity.log").write_text("sha256:" + "a" * 64)

    def cleanup(resources, suffix, **_kwargs):
        cleaned.append((list(resources), suffix))
        assert all(kind == "network" and suffix in name for kind, name in resources)
        return [
            {"kind": kind, "name": name, "status": "REMOVED"}
            for kind, name in resources
        ]

    monkeypatch.setattr(runner, "Run", FakeRun)
    monkeypatch.setattr(runner, "source_guard", lambda *_args, **_kwargs: files)
    monkeypatch.setattr(runner, "cleanup_resources", cleanup)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("unexpected_native_child"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pure",
            "--archive",
            str(archive),
            "--archive-sha256",
            hashlib.sha256(archive.read_bytes()).hexdigest(),
            "--commit",
            "c" * 40,
            "--tree",
            "d" * 40,
            "--output",
            str(output),
        ],
    )
    assert runner.main() == 1
    value = json.loads((output / "export/identity.json").read_bytes())
    assert value["status"] != "PASS_DIAGNOSTIC_ONLY"
    if failed_stage == "network":
        assert len(cleaned) == 2
        assert all(
            value["experiments"][name]["measured_evidence"]["phase_prefix"] == "UNKNOWN"
            for name in ("A", "B")
        )
    else:
        assert cleaned == [] and all(
            value["experiments"][name]["status"] == "NOT_STARTED" for name in ("A", "B")
        )
    exported = b"".join(path.read_bytes() for path in (output / "export").iterdir())
    assert b"RAW_SQL_DSN_PASSWORD_TRACEBACK_CANARY" not in exported
    assert "RAW_SQL_DSN_PASSWORD_TRACEBACK_CANARY" not in capsys.readouterr().out
    assert (output / "export/receipt.json").is_file()
