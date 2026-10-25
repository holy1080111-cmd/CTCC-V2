"""Synthetic producer/reader acceptance, never native PG or trading acceptance."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import verify_final_hermetic as host
from scripts import verify_linux_shard as selection
from scripts import verify_linux_shard_union as reader
from tests.unit.test_linux_shard_ci import (
    COMMIT,
    MANIFEST,
    SOURCE_FILES,
    TREE,
    _artifacts,
    _full_nodeids,
    _junit,
    _sha,
    synthetic_sealed_native_identity_baseline,  # noqa: F401 -- scoped synthetic catalog IO fixture.
)


def _root(artifacts, index):
    return artifacts / f"ctcc-linux-postgres-{index}-{COMMIT}" / "ctcc-validation"


def _read(path):
    return json.loads(path.read_bytes())


def _save(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _rehash(root):
    _save(
        root / "results.sha256.json",
        {
            path.relative_to(root).as_posix(): _sha(path.read_bytes())
            for path in root.rglob("*")
            if path.is_file() and path.name != "results.sha256.json"
        },
    )


def _accept(artifacts):
    return reader.verify_union(
        artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
    )


def _exercise_actual_post_common_driver(
    monkeypatch, tmp_path, index, *, fail_main=False, cleanup_failure=False
):
    """Run the actual driver body/try-finally over fixed synthetic lower IO."""
    (tmp_path / "template").mkdir()
    artifacts, _ = _artifacts(tmp_path / "template")
    template = _root(artifacts, index)
    # The union fixture is evidence, not a complete Run.command transcript.
    # Author this fixed synthetic main-stage log before any driver invocation.
    main_log = template / "postgres-intent.log"
    assert not main_log.exists()
    main_log.write_bytes(b"synthetic-postgres-intent-fixture-only\n")
    _rehash(template)
    assert _accept(artifacts)["hermetic_regression"] == "PASS"
    template_identity = _read(template / "identity.json")
    output = tmp_path / "output"
    results = output / "test-results"
    results.mkdir(parents=True)
    args = SimpleNamespace(
        suite="postgres",
        postgres_component_index=index,
        shard_index=None,
        output=output,
    )
    identity = {
        "manifest_sha256": _sha(MANIFEST),
        "migration": template_identity["migration"],
    }
    calls, cleanup_calls, native_calls = [], [], []

    def lower_write(name, command, **kwargs):
        calls.append((name, list(command), kwargs))
        source = template / f"{name}.log"
        assert source.is_file(), name
        (output / f"{name}.log").write_bytes(source.read_bytes())
        # Actual Run.command creates its log before a subprocess can fail.
        if name == "postgres-intent" and fail_main:
            raise RuntimeError("synthetic_main_execution_failed")
        if name == "postgres-intent":
            names = ["postgres-intent.xml", "postgres-collection.json"]
            if index == 0:
                names += [
                    "controlled-writer-coverage-migration.json",
                    "controlled-writer-pair-guard-migration.json",
                    "account-history-partition-migration.json",
                ]
        elif (
            name in reader.POSTGRES_PREFLIGHT_STAGES
            or name == "predecessor-0047-two-cases"
        ):
            names = [name + ".xml"]
        else:
            names = []
        for filename in names:
            (results / filename).write_bytes(
                (template / "test-results" / filename).read_bytes()
            )
        if name == "crash-probe-start":
            (output / "crash-probe/durable.json").write_bytes(
                (template / "crash-probe/durable.json").read_bytes()
            )

    def native_io(command, **kwargs):
        # Only the two original read-only process-state/readiness observations.
        native_calls.append((list(command), kwargs))
        assert command[:2] == ["docker", "inspect"] or command[:2] == ["docker", "exec"]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"Running": True}))

    def cleanup_io(resources, suffix):
        cleanup_calls.append((list(resources), suffix))
        return [
            {
                "kind": kind,
                "name": name,
                "status": "FAIL" if cleanup_failure else "ABSENT",
            }
            for kind, name in resources
        ]

    monkeypatch.setattr(host.subprocess, "run", native_io)
    scope = {
        "__builtins__": {
            "Exception": Exception,
            "RuntimeError": RuntimeError,
            "any": any,
            "type": type,
        },
        "_run_component_tests_and_recovery": host._run_component_tests_and_recovery,
        "cleanup_resources": cleanup_io,
        "json": json,
        "hashlib": hashlib,
        "args": args,
        "run": SimpleNamespace(output=output, command=lower_write),
        "container": lower_write,
        "identity": identity,
        "test_results": results,
        "test_results_mount": (),
        "files": json.loads(SOURCE_FILES),
        "postgres": "ctcc-final-aaaaaaaaaaaa-postgres",
        "redis": "ctcc-final-aaaaaaaaaaaa-redis",
        "network": "ctcc-final-aaaaaaaaaaaa-network",
        "environment": ["-e", "ENVIRONMENT=test", "-e", "TRADING_MODE=analysis_only"],
        "label": "org.ctcc.validation.run=aaaaaaaaaaaa",
        "image": "synthetic-not-run",
        "resources": [
            ("network", "ctcc-final-aaaaaaaaaaaa-network"),
            ("container", "ctcc-final-aaaaaaaaaaaa-postgres"),
            ("container", "ctcc-final-aaaaaaaaaaaa-redis"),
        ],
        "suffix": "a" * 12,
    }
    source = ast.parse(Path(host.__file__).read_bytes())
    main = next(
        node
        for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    outer = next(
        node for node in main.body if isinstance(node, ast.Try) and node.finalbody
    )
    call = next(
        node
        for node in outer.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "_run_component_tests_and_recovery"
    )
    # Exact actual call, handlers and finalizer; unrelated build/common IO is
    # already represented by admitted synthetic input, never claimed native.
    retained_try = ast.Try(
        body=[call],
        handlers=outer.handlers,
        orelse=outer.orelse,
        finalbody=outer.finalbody,
    )
    code = compile(
        ast.fix_missing_locations(ast.Module(body=[retained_try], type_ignores=[])),
        str(Path(host.__file__)),
        "exec",
    )
    assert not (fail_main and cleanup_failure)
    if cleanup_failure:
        with pytest.raises(
            RuntimeError, match="^validation_resource_cleanup_unconfirmed$"
        ):
            exec(code, scope)  # noqa: S102 -- exact actual finally must fail closed on lower-IO uncertainty.
    elif fail_main:
        with pytest.raises(RuntimeError, match="^synthetic_main_execution_failed$"):
            exec(code, scope)  # noqa: S102 -- exact reviewed validation body with synthetic lower IO only.
    else:
        exec(code, scope)  # noqa: S102 -- no app/native/exchange source executed.
    assert len(cleanup_calls) == 1
    assert _read(output / "identity.json")["cleanup"] == (
        "FAIL" if cleanup_failure else "PASS"
    )
    assert (output / "results.sha256.json").is_file()
    return calls, identity, native_calls, scope


@pytest.mark.parametrize("index", [0, 1])
def test_actual_post_common_driver_runs_exact_role_and_original_finally(
    monkeypatch, tmp_path, index
):
    calls, identity, native_calls, scope = _exercise_actual_post_common_driver(
        monkeypatch, tmp_path, index
    )
    names = [name for name, _command, _kwargs in calls]
    assert names.count("postgres-intent") == 1
    main = next(call for call in calls if call[0] == "postgres-intent")
    assert main[2]["timeout"] == 7200
    assert f"--ctcc-linux-postgres-component-index={index}" in main[1]
    assert "CTCC_RUN_B3_PREFIX_33=1" in main[2]["extra_environment"]
    assert (
        "CTCC_COVERAGE_RECEIPT_DIR=/validation-results" in main[2]["extra_environment"]
    ) is (index == 0)
    if index == 0:
        assert names == [
            *reader.POSTGRES_PREFLIGHT_STAGES,
            "postgres-intent",
            *reader.POSTGRES_COVERAGE_STAGES,
            *reader.PREDECESSOR_0047_STAGES,
            *reader.POSTGRES_RESTART_STAGES,
        ]
        denial = next(
            call
            for call in calls
            if call[0] == "migration-history-partition-nonempty-denial"
        )
        assert denial[1] == ["alembic", "downgrade", "0049"]
        assert denial[2]["refusal_source_head"] == "0051"
        assert "extra_environment" not in denial[2]
        child = next(call for call in calls if call[0] == "predecessor-0047-upgrade")
        assert any(
            value.endswith("/ctcc_predecessor_47_aaaaaaaaaaaa")
            for value in child[2]["extra_environment"]
        )
        assert (
            identity["history_partition_nonempty_downgrade_policy"][
                "execution_authority"
            ]
            is False
        )
        assert identity["api_service_restart"] == "PASS"
        assert identity["live_restart_acceptance"] is False
        assert len(native_calls) == 2
        assert len(scope["resources"]) == 5
    else:
        assert names == ["postgres-intent"]
        reader._verify_cases_only_no_anchor_claims(identity, {})
        assert native_calls == []
        assert len(scope["resources"]) == 3


@pytest.mark.parametrize("index", [0, 1])
def test_actual_post_common_main_failure_reaches_finally_without_recovery_or_retry(
    monkeypatch, tmp_path, index
):
    calls, identity, _native_calls, scope = _exercise_actual_post_common_driver(
        monkeypatch, tmp_path, index, fail_main=True
    )
    assert [name for name, _command, _kwargs in calls].count("postgres-intent") == 1
    assert not any(
        name in reader.POSTGRES_COVERAGE_STAGES
        or name in reader.PREDECESSOR_0047_STAGES
        or name in reader.POSTGRES_RESTART_STAGES
        for name, _command, _kwargs in calls
    )
    assert identity["component_validation"] == identity["hermetic_regression"] == "FAIL"
    assert identity["failure_type"] == "RuntimeError"
    assert (scope["args"].output / "postgres-intent.log").is_file()
    assert not (scope["test_results"] / "postgres-intent.xml").exists()
    assert not (scope["test_results"] / "postgres-collection.json").exists()
    assert "postgres_tests" not in identity


@pytest.mark.parametrize("index", [0, 1])
def test_actual_post_common_finally_refuses_unknown_cleanup_and_retains_failure_inventory(
    monkeypatch, tmp_path, index
):
    _calls, identity, _native_calls, scope = _exercise_actual_post_common_driver(
        monkeypatch, tmp_path, index, cleanup_failure=True
    )
    assert identity["component_validation"] == identity["hermetic_regression"] == "FAIL"
    retained = _read(scope["args"].output / "identity.json")
    assert retained["cleanup"] == "FAIL"
    assert (scope["args"].output / "results.sha256.json").is_file()


def test_pg2_owns_every_whole_module_and_keeps_both_stateful_anchors_on_zero():
    owned = [selection.postgres_paths(index) for index in range(2)]
    assert len(selection.POSTGRES_TEST_PATHS) == 65
    assert [len(paths) for paths in owned] == [33, 32]
    assert not owned[0] & owned[1]
    assert owned[0] | owned[1] == selection.POSTGRES_TEST_PATHS
    assert selection.POSTGRES_ANCHOR_PATHS <= owned[0]
    for path in selection.POSTGRES_TEST_PATHS:
        expected = (
            0
            if path in selection.POSTGRES_ANCHOR_PATHS
            else int.from_bytes(hashlib.sha256(path.encode()).digest()[:8], "big") % 2
        )
        assert selection.postgres_component(path) == expected
        for suffix in (
            "::test_one",
            "::TestGroup::test_one[long]",
            "::test_one[short]",
        ):
            assert (
                sum(
                    selection.selected_for("postgres", index, path + suffix)
                    for index in range(2)
                )
                == 1
            )
            assert not any(
                selection.selected_for("shard", index, path + suffix)
                for index in range(8)
            )


def test_pg2_keeps_all_required_cases_and_modules_once_across_both_roles():
    required = [host.postgres_required_cases(index) for index in range(2)]
    assert [len(cases) for cases in required] == [13, 10]
    assert not set(required[0]) & set(required[1])
    assert set(required[0]) | set(required[1]) == set(host.ARCHIVE_REQUIRED_CASES)
    assert host.POSTGRES_TEST_TIMEOUT_SECONDS == 7200
    assert host.VALIDATION_BUDGET_SECONDS == 9900
    assert host.CLEANUP_BUDGET_SECONDS == 300
    assert selection.SHARD_COUNT == 8


@pytest.mark.parametrize("index", [True, False, -1, 2, "0"])
def test_pg2_source_selector_refuses_noncanonical_component_index(index):
    assert selection.postgres_paths(0)
    assert selection.postgres_paths(1)
    with pytest.raises(ValueError, match="^postgres_component_index_invalid$"):
        selection.postgres_paths(index)


@pytest.mark.parametrize("index", [0, 1])
def test_actual_pg2_collection_hooks_write_selected_observed_and_exact_junit(
    monkeypatch, tmp_path, index
):
    # The enclosing real CI may itself use this plugin. Preserve its observed
    # set object; direct synthetic hooks must never clear the outer session.
    monkeypatch.setattr(selection, "_OBSERVED", set())
    full = _full_nodeids()
    deselected = []
    output = tmp_path / "postgres-collection.json"
    options = {
        "--ctcc-linux-suite": "postgres",
        "--ctcc-linux-shard-index": None,
        "--ctcc-linux-postgres-component-index": index,
        "--ctcc-linux-collection-json": str(output),
    }
    config = SimpleNamespace(
        getoption=options.__getitem__,
        rootpath=tmp_path,
        hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items)),
    )
    session = SimpleNamespace(exitstatus=0)
    items = [
        SimpleNamespace(nodeid=node, path=tmp_path / node.split("::", 1)[0])
        for node in full
    ]
    selection.pytest_collection_modifyitems(session, config, items)
    expected = [
        node for node in full if selection.selected_for("postgres", index, node)
    ]
    assert [item.nodeid for item in items] == expected
    assert {item.nodeid for item in deselected} == set(full) - set(expected)
    for node in expected:
        selection.pytest_runtest_logreport(SimpleNamespace(nodeid=node))
    selection.pytest_sessionfinish(session, 0)
    _junit(tmp_path / "postgres-intent.xml", expected)
    report = host.verify_pytest_report(
        tmp_path / "postgres-intent.xml",
        required_cases=host.postgres_required_cases(index),
    )
    observed = host.verify_linux_collection(
        output, mode="postgres", index=index, report=report
    )
    assert observed["selected_count"] == len(expected)
    record = _read(output)
    assert record["postgres_component_role"] == (
        "anchor" if index == 0 else "cases_only"
    )
    assert record["shard_index"] is None
    assert record["full_nodeids"] == full
    assert record["selected_nodeids"] == record["observed_nodeids"] == expected


def test_actual_pg2_sessionfinish_cannot_claim_unobserved_selected_case(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(selection, "_OBSERVED", set())
    session = SimpleNamespace(
        exitstatus=0,
        _ctcc_linux_collection={
            "selected_nodeids": ["tests/integration/a.py::test_missing"]
        },
        _ctcc_linux_collection_path=tmp_path / "collection.json",
    )
    selection._OBSERVED.clear()
    selection.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1
    assert _read(session._ctcc_linux_collection_path)["observed_nodeids"] == []


def test_current_pg2_ten_component_fixture_reaches_actual_union_and_owns_anchor(
    tmp_path,
):
    artifacts, full = _artifacts(tmp_path)
    result = _accept(artifacts)
    assert result["schema"] == "ctcc.linux_hermetic_union.v2"
    assert len(result["components"]) == 10
    assert result["selected_cases"] == result["full_collected_cases"] == len(full)
    anchor, cases = [
        _read(_root(artifacts, index) / "identity.json") for index in range(2)
    ]
    assert anchor["coverage_migration_acceptance"]
    assert anchor["pair_guard_migration_acceptance"]
    assert anchor["history_partition_migration_acceptance"]
    assert anchor["history_partition_nonempty_downgrade_policy"]
    assert anchor["predecessor_0047_validation"]
    assert anchor["synthetic_process_pg_redis_crash_durability"] == "PASS"
    assert cases["anchor_experiments"] == "NOT_PERFORMED_CASES_ONLY"
    assert "coverage_nonempty_downgrade_policy" not in cases
    assert "api_service_restart" not in cases
    assert not result["trading_acceptance"]


@pytest.mark.parametrize(
    "damage,code",
    [
        ("missing_pg1", "linux_union_component_set_incomplete"),
        ("extra_old_pg", "linux_union_component_set_incomplete"),
        ("wrong_index", "linux_union_postgres_component_identity_invalid"),
        ("boolean_index", "linux_union_postgres_component_identity_invalid"),
        ("boolean_count", "linux_union_postgres_component_identity_invalid"),
        ("wrong_role", "linux_union_postgres_component_identity_invalid"),
        ("old_collection_schema", "linux_collection_execution_incomplete"),
        ("missing_observed", "linux_collection_execution_incomplete"),
        ("junit_identity", "linux_selected_junit_identity_mismatch"),
        ("junit_duplicate", "linux_selected_junit_identity_mismatch"),
        ("pg1_anchor_claim", "linux_union_cases_only_anchor_claim_forbidden"),
        ("pg1_legacy_coverage_claim", "linux_union_cases_only_anchor_claim_forbidden"),
        ("pg1_restart_claim", "linux_union_cases_only_anchor_claim_forbidden"),
        (
            "anchor_missing_0048_receipt",
            "linux_union_coverage_migration_receipt_missing",
        ),
        (
            "anchor_missing_nonempty_stage",
            "linux_union_coverage_or_predecessor_stage_or_hash_missing",
        ),
    ],
)
def test_actual_pg2_union_refuses_each_post_baseline_role_or_case_fabrication(
    tmp_path, damage, code
):
    artifacts, _ = _artifacts(tmp_path)
    # Every negative first proves this exact fixture works in the actual reader.
    assert _accept(artifacts)["hermetic_regression"] == "PASS"
    root = _root(artifacts, 1)
    identity_path = root / "identity.json"
    identity = _read(identity_path)
    collection_path = root / "test-results/postgres-collection.json"
    collection = _read(collection_path)
    if damage == "missing_pg1":
        shutil.rmtree(root.parent)
    elif damage == "extra_old_pg":
        (artifacts / f"ctcc-linux-postgres-{COMMIT}").mkdir()
    elif damage == "wrong_index":
        identity["postgres_component_index"] = 0
    elif damage == "boolean_index":
        identity["postgres_component_index"] = True
    elif damage == "boolean_count":
        identity["postgres_component_count"] = True
    elif damage == "wrong_role":
        identity["postgres_component_role"] = "anchor"
    elif damage == "old_collection_schema":
        collection["schema"] = "ctcc.linux_full_collection_shard.v1"
    elif damage == "missing_observed":
        collection["observed_nodeids"].pop()
    elif damage.startswith("junit_"):
        path = root / "test-results/postgres-intent.xml"
        document = ET.fromstring(path.read_bytes())
        cases = document[0].findall("testcase")
        target = next(
            case
            for case in cases
            if case.get("name") == "test_module_smoke"
            and sum(other.get("classname") == case.get("classname") for other in cases)
            > 1
        )
        if damage == "junit_identity":
            target.set("name", "test_replaced_with_equal_count")
        else:
            peer = next(
                case
                for case in cases
                if case.get("name") == "test_module_smoke"
                and case.get("classname") != target.get("classname")
            )
            target.set("classname", peer.get("classname"))
            target.set("name", peer.get("name"))
        path.write_bytes(ET.tostring(document))
        updated = host.verify_pytest_report(
            path,
            required_cases=host.postgres_required_cases(1),
            required_modules=tuple(
                sorted(
                    module.removesuffix(".py").replace("/", ".")
                    for module in selection.postgres_paths(1)
                )
            ),
        )
        identity["postgres_tests"] = updated
    elif damage == "pg1_anchor_claim":
        identity["coverage_migration_acceptance"] = {"status": "PASS"}
    elif damage == "pg1_legacy_coverage_claim":
        identity["coverage_nonempty_downgrade_policy"] = {"status": "PASS"}
    elif damage == "pg1_restart_claim":
        identity["api_service_restart"] = "PASS"
    elif damage == "anchor_missing_0048_receipt":
        root = _root(artifacts, 0)
        (root / "test-results/controlled-writer-coverage-migration.json").unlink()
    else:
        root = _root(artifacts, 0)
        steps_path = root / "steps.json"
        steps = _read(steps_path)
        _save(
            steps_path,
            [
                step
                for step in steps
                if step["name"] != "migration-history-partition-nonempty-before"
            ],
        )
    if root == _root(artifacts, 1) and root.exists():
        _save(identity_path, identity)
        _save(collection_path, collection)
    if root.exists():
        _rehash(root)
    with pytest.raises(ValueError, match="^" + code + "$"):
        _accept(artifacts)


@pytest.mark.parametrize("damage", ["classname", "parameter", "duplicate"])
def test_actual_junit_join_keeps_parameter_and_class_identity_after_valid_baseline(
    tmp_path, damage
):
    path = tmp_path / "junit.xml"
    selected = [
        "tests/unit/example.py::TestGroup::test_case[long::source]",
        "tests/unit/example.py::TestGroup::test_case[short]",
    ]
    suites = ET.Element("testsuites")
    suite = ET.SubElement(
        suites, "testsuite", tests="2", failures="0", errors="0", skipped="0"
    )
    for name in ("test_case[long::source]", "test_case[short]"):
        ET.SubElement(
            suite, "testcase", classname="tests.unit.example.TestGroup", name=name
        )
    path.write_bytes(ET.tostring(suites))
    assert (
        host.verify_selected_junit_nodes(
            path, selected, expected_sha256=_sha(path.read_bytes())
        )["selected_identity_count"]
        == 2
    )
    cases = suite.findall("testcase")
    if damage == "classname":
        cases[0].set("classname", "tests.unit.example.OtherGroup")
    elif damage == "parameter":
        cases[0].set("name", "test_case[long]")
    else:
        cases[1].set("name", cases[0].get("name"))
    path.write_bytes(ET.tostring(suites))
    with pytest.raises(ValueError, match="^linux_selected_junit_identity_mismatch$"):
        host.verify_selected_junit_nodes(
            path, selected, expected_sha256=_sha(path.read_bytes())
        )


@pytest.mark.parametrize(
    "suite,index,shard",
    [
        ("postgres", None, None),
        ("postgres", -1, None),
        ("postgres", 2, None),
        ("full", 0, None),
        ("shard", 1, 0),
    ],
)
def test_actual_driver_refuses_component_argument_mismatch_before_archive_or_native_io(
    monkeypatch, suite, index, shard
):
    argv = [
        "verify",
        "--archive",
        "MUST_NOT_READ.tar",
        "--archive-sha256",
        "a" * 64,
        "--commit",
        "a" * 40,
        "--tree",
        "b" * 40,
        "--output",
        "MUST_NOT_CREATE",
        "--suite",
        suite,
    ]
    if index is not None:
        argv += ["--postgres-component-index", str(index)]
    if shard is not None:
        argv += ["--shard-index", str(shard)]
    monkeypatch.setattr(sys, "argv", argv)
    calls = []

    def no_io(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("archive/native IO must not start")

    monkeypatch.setattr(Path, "read_bytes", no_io)
    monkeypatch.setattr(host.subprocess, "run", no_io)
    with pytest.raises(ValueError, match="^postgres_component_selection_invalid$"):
        host.main()
    assert calls == []


@pytest.mark.parametrize("damage", ["old_pin", "source_drift", "missing_member"])
def test_current_linux_diagnostic_consumer_uses_new_driver_pin_and_retains_helper_fix(
    damage,
):
    root = Path(__file__).resolve().parents[2]
    producer = root / "scripts/diagnose_native_financial_original_node.py"
    tree = ast.parse(producer.read_bytes())
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    target = "scripts/verify_final_hermetic.py"
    pins = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Subscript)
        and isinstance(node.targets[0].value, ast.Name)
        and node.targets[0].value.id == "input_pins"
        and isinstance(node.targets[0].slice, ast.Constant)
    ]
    values = {
        node.targets[0].slice.value: ast.literal_eval(node.value) for node in pins
    }
    actual = _sha((root / target).read_bytes())
    assert values[target] == actual
    assert (
        values["scripts/native_financial_fixed_node_diagnostics.py"]
        == "a8318180305b9bfb136f6c43447b586033be6e1ed9e5a42551da93defba8916a"
    )
    loops = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Call)
        and isinstance(node.iter.func, ast.Attribute)
        and isinstance(node.iter.func.value, ast.Name)
        and node.iter.func.value.id == "input_pins"
        and node.iter.func.attr == "items"
    ]
    assert len(loops) == 1
    assert all(
        (isinstance(call.func, ast.Name) and call.func.id == "ValueError")
        or (isinstance(call.func, ast.Attribute) and call.func.attr in {"items", "get"})
        for call in ast.walk(loops[0])
        if isinstance(call, ast.Call)
    )
    guard = compile(ast.Module(body=[loops[0]], type_ignores=[]), str(producer), "exec")
    scope = {
        "__builtins__": {"ValueError": ValueError},
        "input_pins": {target: actual},
        "files": {target: {"sha256": actual}},
    }
    exec(guard, scope)  # noqa: S102 -- actual reviewed pure dictionary pin loop only.
    if damage == "old_pin":
        scope["input_pins"][target] = (
            "6b3ed4a605fbfcb6d28d5f5114fedae7bdb9613775c1cf73fcc68a4587cd9d80"
        )
    elif damage == "source_drift":
        scope["files"][target]["sha256"] = "0" * 64
    else:
        del scope["files"][target]
    with pytest.raises(ValueError, match="^diagnostic_reviewed_input_changed$"):
        exec(guard, scope)  # noqa: S102 -- baseline-first original guard rejection.
