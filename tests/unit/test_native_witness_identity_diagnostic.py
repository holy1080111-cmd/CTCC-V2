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
from scripts.verify_final_hermetic import (
    NATIVE_WITNESS_IDENTITY_SCHEMA,
    native_identity_nonempty_snapshot,
)


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
    tmp_path,
    monkeypatch,
    *,
    profile,
    fail_tests=False,
    migration_head="0047",
    migration_damage=None,
    cleanup_failure=False,
    current_upgrade_failure=None,
):
    if profile == diagnostic.NATIVE_WITNESS_MIGRATION_PROFILE:
        constraint = list(NATIVE_WITNESS_IDENTITY_SCHEMA["constraint"])
        constraint[-1] = "9" * 64  # Synthetic, never a native baseline.
        monkeypatch.setitem(NATIVE_WITNESS_IDENTITY_SCHEMA, "constraint", constraint)
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
        "scripts/verify_native_witness_identity_schema.py",
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
    if migration_damage == "source":
        files["scripts/verify_native_witness_identity_schema.py"]["sha256"] = "0" * 64
    monkeypatch.setattr(diagnostic, "archive_tree", lambda _archive: tree)
    monkeypatch.setattr(diagnostic, "archive_files", lambda _archive: files)
    monkeypatch.setattr(
        diagnostic.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        diagnostic,
        "cleanup_resources",
        lambda *_args, **_kwargs: [{"status": "FAILED"}] if cleanup_failure else [],
    )
    calls = []

    class FakeRun:
        def __init__(self, output, **_kwargs):
            self.output, self.steps, self.deadline = output, [], float("inf")

        def command(self, name, command, **_kwargs):
            calls.append((name, command))
            self.steps.append(
                {
                    "name": name,
                    "exit_code": 1 if name == "native-witness-nonempty-denial" else 0,
                }
            )
            raw = b""
            if name == "image-identity":
                raw = b"sha256:" + b"d" * 64
            elif (
                name == "native-witness-current-upgrade"
                and current_upgrade_failure is not None
            ):
                self.steps[-1]["exit_code"] = 1
                (self.output / f"{name}.log").write_bytes(current_upgrade_failure)
                raise RuntimeError("private-driver-detail")
            elif name == "migration-identity":
                raw = json.dumps(
                    {
                        "schema": "ctcc.migration_predecessor_identity.v1",
                        "status": "PASS",
                        "source_head": "0051",
                        "database_head": migration_head,
                        "target_revision": "0047",
                        "migration_sha256": "e" * 64,
                        "execution_authority": False,
                        "untrusted_extra": "private-driver-detail",
                    }
                ).encode()
            elif name == "native-witness-current-identity":
                raw = json.dumps(
                    {
                        "schema": "ctcc.migration_identity.v1",
                        "status": "PASS",
                        "source_head": "0051",
                        "database_head": "0051",
                        "migration_sha256": "e" * 64,
                        "execution_authority": False,
                    }
                ).encode()
            elif name == "native-witness-public-catalog":
                raw = json.dumps(_catalog()).encode()
            elif name == "history-partition-native-catalog":
                path = (
                    self.output
                    / "test-results/account-history-partition-native-catalog.json"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'{"synthetic_diagnostic_fixture_only":true}\n')
                raw = b"synthetic capture stage only"
            elif name == "native-witness-migration-before":
                observed = diagnostic.native_identity_empty_before_readback(
                    diagnostic.expected_migration_revision_readback("0047"), "f" * 64
                )
                if migration_damage == "before":
                    observed["execution_authority"] = 0
                elif migration_damage == "extra":
                    observed["private_row"] = "private-driver-detail"
                raw = json.dumps(observed).encode()
                if migration_damage == "duplicate":
                    raw = raw.replace(
                        b'"execution_authority": false',
                        b'"execution_authority": false,"execution_authority":true',
                        1,
                    )
            elif name == "native-witness-empty-readback":
                observed = diagnostic.expected_native_identity_empty_downgrade_readback(
                    "f" * 64
                )
                if migration_damage == "legacy":
                    observed["legacy_witness_catalog"]["columns"][0][1] = "numeric"
                elif migration_damage == "custody":
                    observed["object_custody_sha256"] = "e" * 64
                raw = json.dumps(observed).encode()
            elif name == "native-witness-reupgrade-readback":
                observed = diagnostic.expected_migration_revision_readback("0047")
                if migration_damage == "reupgrade":
                    observed["observed"] = "0046"
                raw = json.dumps(observed).encode()
            elif name in (
                "native-witness-nonempty-before",
                "native-witness-nonempty-after",
            ):
                observed = native_identity_nonempty_snapshot(
                    diagnostic.expected_migration_revision_readback("0047"),
                    {
                        "row_count": 4,
                        "small_row_count": 1,
                        "wide_row_count": 3,
                        "records_sha256": "a" * 64,
                        "catalog_sha256": "b" * 64,
                    },
                )
                if migration_damage == "nonempty" and name.endswith("after"):
                    observed["retention"]["records_sha256"] = "c" * 64
                raw = json.dumps(observed).encode()
            elif name == "native-witness-nonempty-denial":
                assert (
                    _kwargs["expected_refusal"]
                    == diagnostic.NATIVE_IDENTITY_DOWNGRADE_DENIAL
                )
                if migration_damage == "denial":
                    raise RuntimeError("private-driver-detail")
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
    assert "--profile native-witness-migration-0047" in workflow
    assert 'test "$commit" = "$GITHUB_SHA"' in workflow
    assert "/export/*" in workflow and "/test-results" not in workflow
    assert "secrets." not in workflow and "--env-file" not in workflow


def test_migration_profile_keeps_exact_seventeen_case_redactor_contract():
    assert (
        diagnostic.required_cases(diagnostic.NATIVE_WITNESS_MIGRATION_PROFILE)
        == diagnostic.NATIVE_WITNESS_CASES
    )
    summary, clean = diagnostic.redacted_report(
        _junit(), profile=diagnostic.NATIVE_WITNESS_MIGRATION_PROFILE
    )
    assert summary["profile"] == diagnostic.NATIVE_WITNESS_MIGRATION_PROFILE
    assert summary["all_required_passed"] and summary["expected_case_count"] == 17
    assert b"private-driver-detail" not in clean


def test_migration_main_runs_empty_roundtrip_before_cases_then_one_nonempty_refusal(
    tmp_path, monkeypatch
):
    result, calls, identity, export = _mock_main(
        tmp_path, monkeypatch, profile=diagnostic.NATIVE_WITNESS_MIGRATION_PROFILE
    )
    assert result == 0 and identity["status"] == "PASS"
    names = [name for name, _ in calls]
    expected = [
        "migration-upgrade",
        "migration-identity",
        "native-witness-migration-before",
        "native-witness-empty-downgrade",
        "native-witness-empty-readback",
        "native-witness-reupgrade",
        "native-witness-reupgrade-readback",
        "native-witness-identity-tests",
        "native-witness-nonempty-before",
        "native-witness-nonempty-denial",
        "native-witness-nonempty-after",
        "native-witness-current-upgrade",
        "native-witness-current-identity",
        "native-witness-current-drift",
    ]
    assert [name for name in names if name in expected] == expected
    assert all(names.count(name) == 1 for name in expected)
    commands = dict(calls)
    assert commands["migration-upgrade"][-3:] == ["alembic", "upgrade", "0047"]
    assert commands["native-witness-reupgrade"][-3:] == ["alembic", "upgrade", "0047"]
    assert (
        diagnostic.PREDECESSOR_0047_IDENTITY_PROBE_0051
        in commands["migration-identity"]
    )
    assert "native-witness-reupgrade-drift" not in names
    assert identity["migration"]["source_head"] == "0051"
    assert identity["migration"]["database_head"] == "0047"
    assert identity["current_source_migration"]["database_head"] == "0051"
    assert (
        diagnostic.MIGRATION_REVISION_PROBE
        in commands["native-witness-migration-before"]
    )
    assert (
        commands["native-witness-empty-readback"][-1]
        == "native-identity-0047-empty-down"
    )
    assert (
        diagnostic.NATIVE_IDENTITY_RETENTION_PROBE
        in commands["native-witness-nonempty-before"]
    )
    assert commands["native-witness-nonempty-denial"][-3:] == [
        "alembic",
        "downgrade",
        "0046",
    ]
    path = export / "native-witness-migration-receipt.json"
    receipt = json.loads(path.read_bytes())
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == identity["migration_diagnostic_receipt_sha256"]
    )
    assert receipt["status"] == "PASS" and receipt["phase"] == "COMPLETE"
    assert receipt["required_case_count"] == 17
    assert receipt["source"]["commit"] == identity["commit"]
    assert receipt["source"]["source_files_sha256"] == identity["source_files_sha256"]
    assert len(receipt["executed_source_sha256"]) == 4
    assert receipt["nonempty_downgrade_policy"]["rollback"] == "EXACT"
    assert (
        receipt["nonempty_downgrade_policy"]["before"]
        == receipt["nonempty_downgrade_policy"]["after"]
    )
    assert len(path.read_bytes()) <= 65536
    assert receipt["full_postgresql_acceptance"] is False
    assert (
        receipt["execution_authority"] is False
        and receipt["external_order_writes"] == 0
    )


@pytest.mark.parametrize(
    "damage,phase",
    (
        ("before", "native-witness-migration-before"),
        ("extra", "native-witness-migration-before"),
        ("duplicate", "native-witness-migration-before"),
        ("legacy", "native-witness-empty-readback"),
        ("custody", "native-witness-empty-readback"),
        ("reupgrade", "native-witness-reupgrade-readback"),
        ("denial", "native-witness-nonempty-denial"),
        ("nonempty", "native-witness-nonempty-after"),
    ),
)
def test_migration_failure_exports_only_validated_partial_evidence_and_never_retries(
    tmp_path, monkeypatch, damage, phase
):
    result, calls, identity, export = _mock_main(
        tmp_path,
        monkeypatch,
        profile=diagnostic.NATIVE_WITNESS_MIGRATION_PROFILE,
        migration_damage=damage,
    )
    assert result == 1 and identity["status"] == "FAIL"
    receipt = json.loads(
        (export / "native-witness-migration-receipt.json").read_bytes()
    )
    assert receipt["status"] == "FAIL" and receipt["phase"] == phase
    assert phase not in receipt["observations"]
    assert "nonempty_downgrade_policy" not in receipt
    names = [name for name, _ in calls]
    assert all(names.count(name) == 1 for name in set(names))
    assert receipt["full_postgresql_acceptance"] is False


def test_migration_test_failure_never_attempts_nonempty_downgrade(
    tmp_path, monkeypatch
):
    result, calls, _, export = _mock_main(
        tmp_path,
        monkeypatch,
        profile=diagnostic.NATIVE_WITNESS_MIGRATION_PROFILE,
        fail_tests=True,
    )
    assert result == 1
    names = [name for name, _ in calls]
    assert "native-witness-nonempty-before" not in names
    receipt = json.loads(
        (export / "native-witness-migration-receipt.json").read_bytes()
    )
    assert (
        receipt["status"] == "FAIL"
        and receipt["phase"] == "native-witness-identity-tests"
    )
    assert "nonempty_downgrade_policy" not in receipt


def test_migration_profile_pins_host_validator_dependency_before_any_runtime_launch(
    tmp_path, monkeypatch
):
    with pytest.raises(ValueError, match="executed_runner_differs_from_archive"):
        _mock_main(
            tmp_path,
            monkeypatch,
            profile=diagnostic.NATIVE_WITNESS_MIGRATION_PROFILE,
            migration_damage="source",
        )
    assert not (tmp_path / "output").exists()


def test_migration_completed_observations_do_not_override_failed_cleanup(
    tmp_path, monkeypatch
):
    result, _, identity, export = _mock_main(
        tmp_path,
        monkeypatch,
        profile=diagnostic.NATIVE_WITNESS_MIGRATION_PROFILE,
        cleanup_failure=True,
    )
    receipt = json.loads(
        (export / "native-witness-migration-receipt.json").read_bytes()
    )
    assert result == 1 and identity["status"] == "FAIL"
    assert receipt["phase"] == "COMPLETE" and receipt["status"] == "FAIL"
    assert receipt["full_postgresql_acceptance"] is False


@pytest.mark.parametrize(
    "raw",
    (b'{"a":1,"a":2}', b'{"a":{"b":1,"b":2}}', b"[]", b"x" * 65537),
    ids=("duplicate_top", "duplicate_nested", "nonobject", "oversized"),
)
def test_migration_observation_reader_rejects_duplicates_nonobject_or_oversize(
    tmp_path, raw
):
    path = tmp_path / "observation.log"
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="diagnostic_migration_"):
        diagnostic._migration_object(path)


# Prepared appended unit tests; no test execution during authoring.
_UPGRADE_FAILURE_PRIVATE = (
    "never-persist-driver-password-3c9f4",
    "postgresql+asyncpg://private-user:private-password@private-host/private-db",
    "OK-ACCESS-SIGN: never-persist-private-signature",
    "OK-ACCESS-PASSPHRASE: never-persist-private-passphrase",
)
_UPGRADE_FAILURE_SOURCE = {
    "migrations/versions/0050_account_history_partition.py": "a" * 64
}


def _upgrade_failure_raw():
    return (
        "Traceback (most recent call last):\n"
        '  File "/app/migrations/versions/0050_account_history_partition.py", line 123, in upgrade\n'
        "asyncpg.exceptions.PostgresSyntaxError: "
        + " | ".join(_UPGRADE_FAILURE_PRIVATE)
        + "\nSQLSTATE: 42601\n"
        + "sqlalchemy.exc.ProgrammingError: "
        + " | ".join(_UPGRADE_FAILURE_PRIVATE)
        + "\n[SQL: SELECT private_account_value]\n"
        + '[parameters: {"password": "never-persist-driver-password-3c9f4"}]\n'
    ).encode()


def test_upgrade_failure_metadata_retains_only_observed_allowlisted_tokens(tmp_path):
    path = tmp_path / "native-witness-current-upgrade.log"
    raw = _upgrade_failure_raw()
    path.write_bytes(raw)
    value = diagnostic._read_upgrade_failure_metadata(
        path, source_migrations=_UPGRADE_FAILURE_SOURCE, exit_code=1
    )
    assert value["status"] == "observed"
    assert (
        value["stage"] == "native-witness-current-upgrade" and value["exit_code"] == 1
    )
    assert value["exception_classes"] == ["PostgresSyntaxError", "ProgrammingError"]
    assert value["sqlstates"] == ["42601"]
    assert value["migration_frames"] == [
        {
            "file": "migrations/versions/0050_account_history_partition.py",
            "line": 123,
            "source_sha256": "a" * 64,
        }
    ]
    assert value["log_sha256"] == hashlib.sha256(raw).hexdigest()
    assert value["log_bytes"] == len(raw)
    encoded = json.dumps(value)
    assert all(private not in encoded for private in _UPGRADE_FAILURE_PRIVATE)
    assert (
        "private_account_value" not in encoded
        and "parameters" not in encoded.replace("parameters_exported", "")
    )
    assert value["full_postgresql_acceptance"] is value["execution_authority"] is False
    assert path.read_bytes() == raw


@pytest.mark.parametrize(
    "kind,status",
    [
        ("missing", "missing"),
        ("empty", "malformed"),
        ("utf8", "malformed"),
        ("nul", "malformed"),
        ("oversize", "oversize"),
        ("unknown", "unclassified"),
    ],
)
def test_upgrade_failure_missing_malformed_or_overbound_never_invents_cause(
    tmp_path, kind, status
):
    path = tmp_path / "native-witness-current-upgrade.log"
    if kind != "missing":
        raw = {
            "empty": b"",
            "utf8": b"\xff",
            "nul": b"RuntimeError: private\0detail",
            "oversize": b"x" * (diagnostic._UPGRADE_FAILURE_LOG_LIMIT + 1),
            "unknown": (
                "UnknownSecretDriverError: " + " | ".join(_UPGRADE_FAILURE_PRIVATE)
            ).encode(),
        }[kind]
        path.write_bytes(raw)
    value = diagnostic._read_upgrade_failure_metadata(
        path, source_migrations=_UPGRADE_FAILURE_SOURCE, exit_code=1
    )
    assert value["status"] == status
    assert (
        value["exception_classes"]
        == value["sqlstates"]
        == value["migration_frames"]
        == []
    )
    assert all(private not in json.dumps(value) for private in _UPGRADE_FAILURE_PRIVATE)


def test_upgrade_failure_does_not_accept_unknown_frame_sqlstate_or_exception():
    value = diagnostic._upgrade_failure_metadata(
        b'  File "/app/migrations/versions/9999_private_password.py", line 123, in upgrade\n'
        b"FakePrivatePasswordError: do not export\nSQLSTATE: ABCDE\n",
        source_migrations=_UPGRADE_FAILURE_SOURCE,
        exit_code=None,
    )
    assert value["status"] == "unclassified"
    assert (
        value["exception_classes"]
        == value["sqlstates"]
        == value["migration_frames"]
        == []
    )
    assert "private_password" not in json.dumps(value)


def test_upgrade_failure_metadata_overbound_discards_partial_classification():
    raw = "\n".join(
        name + ": private" for name in sorted(diagnostic._UPGRADE_FAILURE_CLASSES)[:9]
    ).encode()
    value = diagnostic._upgrade_failure_metadata(
        raw, source_migrations=_UPGRADE_FAILURE_SOURCE, exit_code=1
    )
    assert value["status"] == "metadata_bound_exceeded"
    assert (
        value["exception_classes"]
        == value["sqlstates"]
        == value["migration_frames"]
        == []
    )


def test_upgrade_failure_unsafe_file_is_not_opened(tmp_path, monkeypatch):
    path = tmp_path / "native-witness-current-upgrade.log"
    path.write_bytes(_upgrade_failure_raw())
    observed = path.lstat()
    original = Path.lstat
    unsafe = SimpleNamespace(
        **{
            name: getattr(observed, name)
            for name in (
                "st_mode",
                "st_nlink",
                "st_size",
                "st_dev",
                "st_ino",
                "st_mtime_ns",
            )
        },
        st_file_attributes=1024,
    )
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda selected: unsafe if selected == path else original(selected),
    )

    def refuse_open(*_args, **_kwargs):
        raise AssertionError("unsafe log was opened")

    monkeypatch.setattr(Path, "open", refuse_open)
    value = diagnostic._read_upgrade_failure_metadata(
        path, source_migrations=_UPGRADE_FAILURE_SOURCE, exit_code=1
    )
    assert value["status"] == "unsafe"
    assert value["log_sha256"] is None and value["exception_classes"] == []


def test_upgrade_failure_private_read_error_is_only_fixed_metadata(
    tmp_path, monkeypatch
):
    path = tmp_path / "native-witness-current-upgrade.log"
    path.write_bytes(_upgrade_failure_raw())

    def private_error(*_args, **_kwargs):
        raise OSError(" | ".join(_UPGRADE_FAILURE_PRIVATE))

    monkeypatch.setattr(Path, "open", private_error)
    value = diagnostic._read_upgrade_failure_metadata(
        path, source_migrations=_UPGRADE_FAILURE_SOURCE, exit_code=1
    )
    assert value["status"] == "read_failed" and value["exception_classes"] == []
    assert all(private not in json.dumps(value) for private in _UPGRADE_FAILURE_PRIVATE)


def test_main_failed_current_upgrade_exports_safe_metadata_and_keeps_failure(
    tmp_path, monkeypatch
):
    result, calls, identity, export = _mock_main(
        tmp_path,
        monkeypatch,
        profile=diagnostic.NATIVE_WITNESS_MIGRATION_PROFILE,
        current_upgrade_failure=_upgrade_failure_raw(),
    )
    assert (
        result == 1
        and identity["status"] == "FAIL"
        and identity["failure_type"] == "RuntimeError"
    )
    names = [name for name, _ in calls]
    assert names.count("native-witness-current-upgrade") == 1
    assert (
        "history-partition-native-catalog" not in names
        and "history-partition-current-formal" not in names
    )
    record_path = export / "native-witness-current-upgrade-failure.json"
    raw = record_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == identity["current_upgrade_failure_sha256"]
    value = json.loads(raw)
    assert value["exception_classes"] == ["PostgresSyntaxError", "ProgrammingError"]
    assert value["sqlstates"] == ["42601"] and value["migration_frames"] == []
    # _mock_main's admitted archive has no migration files. It cannot mint a
    # source frame from a log path even though the pure parser case above can.
    assert value["full_postgresql_acceptance"] is value["execution_authority"] is False
    for item in export.iterdir():
        assert all(
            private.encode() not in item.read_bytes()
            for private in _UPGRADE_FAILURE_PRIVATE
        )
    steps = json.loads((export / "steps.json").read_bytes())
    assert (
        next(s for s in steps if s["name"] == "native-witness-current-upgrade")[
            "exit_code"
        ]
        == 1
    )
    receipt = json.loads((export / "receipt.json").read_bytes())
    assert (
        receipt["status"] == "FAIL"
        and receipt["artifacts"][record_path.name] == hashlib.sha256(raw).hexdigest()
    )


def test_upgrade_failure_publisher_retains_private_log_and_is_no_clobber(tmp_path):
    output, export = tmp_path / "private", tmp_path / "export"
    output.mkdir()
    export.mkdir()
    raw = _upgrade_failure_raw()
    log = output / "native-witness-current-upgrade.log"
    log.write_bytes(raw)
    run = SimpleNamespace(
        steps=[{"name": "native-witness-current-upgrade", "exit_code": 1}]
    )
    files = {
        name: {"sha256": digest} for name, digest in _UPGRADE_FAILURE_SOURCE.items()
    }
    pin = diagnostic._export_upgrade_failure(
        run, output=output, export=export, source_files=files
    )
    path = export / "native-witness-current-upgrade-failure.json"
    saved = path.read_bytes()
    assert hashlib.sha256(saved).hexdigest() == pin and log.read_bytes() == raw
    with pytest.raises(FileExistsError):
        diagnostic._export_upgrade_failure(
            run, output=output, export=export, source_files=files
        )
    assert path.read_bytes() == saved and log.read_bytes() == raw
