"""Pure diagnostics contract tests; no actual DB, Docker, native or network I/O."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import sys
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import diagnose_runtime_custody_postgres as diagnostic


def _junit(*, omit=None, outcome=None, private="private-driver-detail"):
    root = ET.Element("testsuites", private=private)
    suite = ET.SubElement(root, "testsuite", name=private)
    for index, (module, name) in enumerate(diagnostic.NATIVE_WITNESS_CASES):
        if index == omit:
            continue
        case = ET.SubElement(
            suite,
            "testcase",
            classname=module,
            name=name,
            file=private,
            time=private,
        )
        ET.SubElement(case, "system-out").text = private
        if index == 0 and outcome:
            ET.SubElement(case, outcome, message=private, type=private).text = private
    _counts(suite)
    return ET.tostring(root)


def _counts(suite):
    suite.set("tests", str(len(suite.findall("testcase"))))
    for name, tag in (
        ("failures", "failure"),
        ("errors", "error"),
        ("skipped", "skipped"),
    ):
        suite.set(name, str(len(suite.findall(f"testcase/{tag}"))))


def _catalog():
    return {
        "constraint_definition": (
            "CHECK (((root_device >= (0)::numeric) AND "
            "(root_device <= '18446744073709551615'::numeric) AND "
            "(root_device = trunc(root_device)) AND (root_inode >= (1)::numeric) "
            "AND (root_inode <= '340282366920938463463374607431768211455'::numeric) "
            "AND (root_inode = trunc(root_inode))))"
        ),
        "columns": [
            {"name": "root_device", "type": "numeric", "typmod": -1},
            {"name": "root_inode", "type": "numeric", "typmod": -1},
        ],
        "functions": [
            {"name": "public_receipt_witness_append(jsonb)", "prosrc_sha256": "a" * 64},
            {
                "name": "public_receipt_witness_insert_guard()",
                "prosrc_sha256": "b" * 64,
            },
        ],
    }


def test_reviewed_inventory_matches_all_exact_source_test_cases():
    source = (
        Path(__file__).parents[1]
        / "integration/test_public_receipt_witness_repository.py"
    )
    module = ast.parse(source.read_text(encoding="utf-8"))
    names = []
    for function in module.body:
        if not isinstance(
            function, ast.AsyncFunctionDef
        ) or not function.name.startswith("test_"):
            continue
        parameterizations = [
            decorator
            for decorator in function.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "parametrize"
        ]
        assert len(parameterizations) <= 1
        if not parameterizations:
            names.append(function.name)
            continue
        values = parameterizations[0].args[1]
        assert isinstance(values, ast.List)
        for value in values.elts:
            assert isinstance(value, ast.Call)
            identifier = [item.value for item in value.keywords if item.arg == "id"]
            assert len(identifier) == 1 and isinstance(identifier[0], ast.Constant)
            names.append(f"{function.name}[{identifier[0].value}]")
    assert len(names) == 17
    assert (
        tuple((diagnostic.NATIVE_WITNESS_MODULE, name) for name in names)
        == diagnostic.NATIVE_WITNESS_CASES
    )
    assert (
        diagnostic.required_cases(diagnostic.DEFAULT_PROFILE)
        == diagnostic.RUNTIME_CUSTODY_PREFLIGHT_CASES
    )
    with pytest.raises(ValueError, match="profile_invalid"):
        diagnostic.required_cases("caller-invented-profile")


def test_native_profile_exports_exact_success_inventory_and_no_raw_metadata():
    raw = _junit()
    summary, clean = diagnostic.redacted_report(
        raw, profile=diagnostic.NATIVE_WITNESS_PROFILE
    )
    assert summary["expected_case_count"] == 17
    assert summary["exact_required_cases_present"] and summary["all_required_passed"]
    assert summary["counts"] == {"tests": 17, "failures": 0, "errors": 0, "skipped": 0}
    assert summary["raw_junit_sha256"] == hashlib.sha256(raw).hexdigest()
    assert not summary["full_postgresql_acceptance"]
    assert b"private-driver-detail" not in clean
    assert "private-driver-detail" not in json.dumps(summary)
    assert "exact_four_cases_present" not in summary


@pytest.mark.parametrize("outcome,category", diagnostic._NATIVE_CATEGORIES.items())
def test_native_failure_export_keeps_only_fixed_category(outcome, category):
    summary, clean = diagnostic.redacted_report(
        _junit(
            outcome=outcome,
            private="runtime_custody_diagnostic:role_scope;checks=private_secret",
        ),
        profile=diagnostic.NATIVE_WITNESS_PROFILE,
    )
    assert not summary["all_required_passed"]
    assert summary["cases"][0]["category"] == category
    assert summary["cases"][0]["failed_conditions"] == []
    assert summary["category_counts"][category] == 1
    assert b"private_secret" not in clean
    assert "private_secret" not in json.dumps(summary)


def test_native_missing_case_cannot_pass():
    summary, _ = diagnostic.redacted_report(
        _junit(omit=16), profile=diagnostic.NATIVE_WITNESS_PROFILE
    )
    assert not summary["all_required_passed"]
    assert not summary["exact_required_cases_present"]


@pytest.mark.parametrize(
    "mutation",
    ["extra", "duplicate", "changed", "counts", "unknown_suite", "two_outcomes"],
)
def test_native_inventory_schema_and_counts_tampering_rejected(mutation):
    root = ET.fromstring(_junit())
    suite = root.find("testsuite")
    if mutation == "extra":
        ET.SubElement(suite, "testcase", classname="private-name", name="private-name")
        _counts(suite)
    elif mutation == "duplicate":
        suite.append(suite[0])
        _counts(suite)
    elif mutation == "changed":
        suite[0].set("name", "changed-case")
    elif mutation == "counts":
        suite.set("tests", "18")
    elif mutation == "unknown_suite":
        ET.SubElement(root, "private-name")
    else:
        ET.SubElement(suite[0], "failure")
        ET.SubElement(suite[0], "skipped")
        _counts(suite)
    with pytest.raises(ValueError, match="diagnostic_report_"):
        diagnostic.redacted_report(
            ET.tostring(root), profile=diagnostic.NATIVE_WITNESS_PROFILE
        )


def test_native_runner_runs_whole_module_with_credential_free_pytest(tmp_path):
    calls = []

    def container(name, command, **kwargs):
        calls.append((name, command, kwargs))
        (tmp_path / diagnostic.NATIVE_WITNESS_REPORT).write_bytes(_junit())

    report = diagnostic.run_native_witness_identity_diagnostic(
        container, test_results=tmp_path, mounts=("literal-readwrite-results-only",)
    )
    name, command, options = calls[0]
    assert name == "native-witness-identity-tests"
    assert command[:3] == ["python", "-m", "scripts.hermetic_pytest"]
    assert command[-1] == "tests/integration/test_public_receipt_witness_repository.py"
    assert "--tb=no" in command and "--show-capture=no" in command
    assert "junit_logging=no" in command
    assert options == {"mounts": ("literal-readwrite-results-only",), "timeout": 300}
    assert report["passed"] == 17
    assert set(report["required_case_passed"].values()) == {1}


@pytest.mark.parametrize("change", ["missing", "skipped", "extra"])
def test_native_runner_rejects_nonexact_actual_execution(tmp_path, change):
    def container(*_args, **_kwargs):
        raw = _junit(
            omit=16 if change == "missing" else None,
            outcome="skipped" if change == "skipped" else None,
        )
        if change == "extra":
            root = ET.fromstring(raw)
            suite = root.find("testsuite")
            ET.SubElement(
                suite,
                "testcase",
                classname=diagnostic.NATIVE_WITNESS_MODULE,
                name="unreviewed_case",
            )
            _counts(suite)
            raw = ET.tostring(root)
        (tmp_path / diagnostic.NATIVE_WITNESS_REPORT).write_bytes(raw)

    with pytest.raises(ValueError):
        diagnostic.run_native_witness_identity_diagnostic(
            container, test_results=tmp_path, mounts=()
        )


def test_catalog_exports_fixed_public_ddl_only_not_acceptance():
    fixture = _catalog()
    assert diagnostic.native_witness_catalog(json.dumps(fixture).encode()) == fixture
    sql = diagnostic.NATIVE_WITNESS_CATALOG_SQL
    assert "pg_get_constraintdef(oid,true)" in sql
    assert "pg_attribute" in sql and "pg_proc" in sql
    assert "checkpoint_json" not in sql and "pg_roles" not in sql


def test_catalog_pretty_whitespace_is_preserved_without_normalization():
    fixture = _catalog()
    original = fixture["constraint_definition"].replace(" AND ", "\r\n\tAND ")
    fixture["constraint_definition"] = original
    result = diagnostic.native_witness_catalog(json.dumps(fixture).encode())
    assert result["constraint_definition"] == original
    assert "\r\n\t" in result["constraint_definition"]


@pytest.mark.parametrize("space", ["\x00", "\v", "\f", "\u00a0"])
def test_catalog_rejects_other_control_or_unicode_whitespace(space):
    fixture = _catalog()
    fixture["constraint_definition"] += space
    with pytest.raises(ValueError, match="^diagnostic_catalog_constraint_invalid$"):
        diagnostic.native_witness_catalog(json.dumps(fixture).encode())


@pytest.mark.parametrize("field", ["constraint_definition", "type", "prosrc_sha256"])
def test_catalog_duplicate_keys_rejected_at_every_object_depth(field):
    raw = json.dumps(_catalog())
    value = {
        "constraint_definition": _catalog()["constraint_definition"],
        "type": "numeric",
        "prosrc_sha256": "a" * 64,
    }[field]
    entry = json.dumps(field) + ": " + json.dumps(value)
    assert entry in raw
    raw = raw.replace(entry, entry + ", " + entry, 1)
    with pytest.raises(ValueError, match="^diagnostic_catalog_duplicate_key$"):
        diagnostic.native_witness_catalog(raw.encode())


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "definition",
        "number",
        "oversize",
        "column",
        "typmod",
        "function",
        "digest",
        "function_extra",
        "typmod_float",
    ],
)
def test_catalog_rejects_nonallowlisted_metadata(mutation):
    fixture = _catalog()
    if mutation == "extra":
        fixture["private_row"] = "private-driver-detail"
    elif mutation == "definition":
        fixture["constraint_definition"] += " private_driver_detail"
    elif mutation == "number":
        fixture["constraint_definition"] += " 99999"
    elif mutation == "oversize":
        fixture["constraint_definition"] += " " * 2048
    elif mutation == "column":
        fixture["columns"][0]["name"] = "private-driver-detail"
    elif mutation == "typmod":
        fixture["columns"][0]["typmod"] = 20
    elif mutation == "typmod_float":
        fixture["columns"][0]["typmod"] = -1.0
    elif mutation == "function":
        fixture["functions"][0]["name"] = "unreviewed_function()"
    elif mutation == "digest":
        fixture["functions"][0]["prosrc_sha256"] = "private-driver-detail"
    else:
        fixture["functions"][0]["body"] = "private-driver-detail"
    with pytest.raises(ValueError, match="diagnostic_catalog_"):
        diagnostic.native_witness_catalog(json.dumps(fixture).encode())


def _mock_main(
    tmp_path, monkeypatch, *, profile, fail_tests=False, migration_head="0047"
):
    commit, tree = "a" * 40, "b" * 40
    archive_path = tmp_path / "exact-source.tar"
    raw = io.BytesIO()
    with tarfile.open(
        fileobj=raw,
        mode="w",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": commit},
    ) as archive:
        entry = tarfile.TarInfo("synthetic-public-file")
        entry.size = 1
        archive.addfile(entry, io.BytesIO(b"x"))
    archive_raw = raw.getvalue()
    archive_path.write_bytes(archive_raw)
    source = Path(diagnostic.__file__).parents[1]
    executed = (
        "scripts/diagnose_runtime_custody_postgres.py",
        "scripts/verify_final_hermetic.py",
        "scripts/verify_linux_shard.py",
    )
    files = {
        name: {"sha256": hashlib.sha256((source / name).read_bytes()).hexdigest()}
        for name in executed
    }
    files.update(
        {
            "requirements/validation-linux-py312.lock": {
                "sha256": diagnostic.LOCK_SHA256
            },
            "MANIFEST.sha256": {"sha256": "c" * 64},
        }
    )
    monkeypatch.setattr(diagnostic, "archive_tree", lambda _archive: tree)
    monkeypatch.setattr(diagnostic, "archive_files", lambda _archive: files)
    monkeypatch.setattr(
        diagnostic.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(diagnostic, "cleanup_resources", lambda *_args, **_kwargs: [])
    calls = []

    class FakeRun:
        def __init__(self, output, **_kwargs):
            self.output, self.steps, self.deadline = output, [], float("inf")

        def command(self, name, command, **_kwargs):
            calls.append((name, command))
            self.steps.append({"name": name, "exit_code": 0})
            raw = b""
            if name == "image-identity":
                raw = b"sha256:" + b"d" * 64
            elif name == "migration-identity":
                raw = json.dumps(
                    {
                        "status": "PASS",
                        "source_head": migration_head,
                        "database_head": migration_head,
                        "migration_sha256": "e" * 64,
                        "execution_authority": False,
                        "untrusted_extra": "private-driver-detail",
                    }
                ).encode()
            elif name == "native-witness-public-catalog":
                raw = json.dumps(_catalog()).encode()
            elif name == "native-witness-identity-tests":
                (
                    self.output / "test-results" / diagnostic.NATIVE_WITNESS_REPORT
                ).write_bytes(_junit(outcome="error" if fail_tests else None))
                if fail_tests:
                    raise RuntimeError("private-driver-detail")
            (self.output / f"{name}.log").write_bytes(raw)

    monkeypatch.setattr(diagnostic, "Run", FakeRun)

    def original_default(_container, *, test_results, mounts):
        calls.append(("original-default-four-case-helper", mounts))
        root = ET.Element("testsuites")
        suite = ET.SubElement(root, "testsuite")
        for module, name in diagnostic.RUNTIME_CUSTODY_PREFLIGHT_CASES:
            ET.SubElement(suite, "testcase", classname=module, name=name)
        _counts(suite)
        (test_results / "runtime-bundle-custody-preflight.xml").write_bytes(
            ET.tostring(root)
        )
        return {"passed": 4}

    monkeypatch.setattr(
        diagnostic, "run_runtime_bundle_custody_preflight", original_default
    )
    args = [
        "diagnostic",
        "--archive",
        str(archive_path),
        "--archive-sha256",
        hashlib.sha256(archive_raw).hexdigest(),
        "--commit",
        commit,
        "--tree",
        tree,
        "--output",
        str(tmp_path / "output"),
    ]
    if profile:
        args.extend(("--profile", profile))
    monkeypatch.setattr(sys, "argv", args)
    result = diagnostic.main()
    export = tmp_path / "output/export"
    identity = json.loads((export / "identity.json").read_bytes())
    assert all(
        b"private-driver-detail" not in path.read_bytes() for path in export.iterdir()
    )
    return result, calls, identity, export


def test_default_profile_keeps_original_four_case_path(tmp_path, monkeypatch):
    result, calls, identity, _ = _mock_main(tmp_path, monkeypatch, profile=None)
    assert result == 0
    names = [name for name, _ in calls]
    assert "original-default-four-case-helper" in names
    assert (
        "migration-upgrade" not in names
        and "native-witness-identity-tests" not in names
    )
    assert "native-witness-public-catalog" not in names
    assert identity["scope"] == "db0046_four_case_diagnostic_only"


def test_native_profile_rejects_other_head_before_witness_cases(tmp_path, monkeypatch):
    result, calls, identity, _ = _mock_main(
        tmp_path,
        monkeypatch,
        profile=diagnostic.NATIVE_WITNESS_PROFILE,
        migration_head="0046",
    )
    assert result == 1 and identity["status"] == "FAIL"
    names = [name for name, _ in calls]
    assert "native-witness-identity-tests" not in names
    assert "native-witness-public-catalog" not in names
    assert identity["full_postgresql_acceptance"] is False


@pytest.mark.parametrize("fail_tests", [False, True])
def test_native_main_migrates_before_cases_and_preserves_catalog_after_failure(
    tmp_path, monkeypatch, fail_tests
):
    result, calls, identity, export = _mock_main(
        tmp_path,
        monkeypatch,
        profile=diagnostic.NATIVE_WITNESS_PROFILE,
        fail_tests=fail_tests,
    )
    assert result == int(fail_tests)
    names = [name for name, _ in calls]
    assert (
        names.index("migration-upgrade")
        < names.index("migration-identity")
        < names.index("native-witness-identity-tests")
    )
    assert names.index("native-witness-public-catalog") > names.index(
        "native-witness-identity-tests"
    )
    assert "original-default-four-case-helper" not in names
    assert identity["status"] == ("FAIL" if fail_tests else "PASS")
    assert identity["catalog_is_observation_not_admission"] is True
    assert identity["full_postgresql_acceptance"] is False
    assert identity["host_credentials"] is False and identity["host_env"] is False
    assert (
        identity["execution_authority"] is False
        and identity["external_order_writes"] == 0
    )
    assert "untrusted_extra" not in identity["migration"]
    assert (export / "native-witness-public-catalog.json").is_file()
    for _, command in calls:
        if command[:2] == ["docker", "run"]:
            assert "--env-file" not in command and "--volume" not in command
            assert not any(".env" in item for item in command)


def test_new_workflow_exact_branch_budget_pinned_actions_and_export_allowlist():
    workflow = (
        Path(__file__).parents[2]
        / ".github/workflows/native-witness-identity-pg-diagnostic.yml"
    ).read_text(encoding="utf-8")
    assert "      - work/native-file-identity-wide-20261022\n" in workflow
    assert "workflow_dispatch" not in workflow and "pull_request" not in workflow
    assert "timeout-minutes: 25" in workflow and "contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in workflow
    assert (
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow
    )
    assert "--profile native-witness-identity-0047" in workflow
    assert 'test "$commit" = "$GITHUB_SHA"' in workflow
    assert "/export/*" in workflow and "/test-results" not in workflow
    assert "secrets." not in workflow and "--env-file" not in workflow
