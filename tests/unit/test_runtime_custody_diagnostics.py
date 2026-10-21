"""Failure grouping is safe diagnostics, never deployment or custody authority."""

from __future__ import annotations

import asyncio
import hashlib
import xml.etree.ElementTree as ET

import pytest

from app.database.repositories import public_runtime_bundle_custody as custody
from scripts import diagnose_runtime_custody_postgres as diagnostic
from tests.integration import test_public_runtime_bundle_custody_repository as pg_cases
from tests.unit.test_public_runtime_bundle_custody import _catalog, _run_catalog


def test_error_keeps_generic_code_and_read_only_allowlisted_category():
    error = custody.RuntimeBundleCustodyError(
        "runtime_bundle_custody_unavailable", diagnostic_category="function_attributes"
    )
    assert error.args == ("runtime_bundle_custody_unavailable",)
    assert str(error) == "runtime_bundle_custody_unavailable"
    assert error.diagnostic_category == "function_attributes"
    with pytest.raises(AttributeError):
        error.diagnostic_category = "role_scope"
    for untrusted in ("not-a-category", "private-driver-detail", None, {}):
        error = custody.RuntimeBundleCustodyError(
            "runtime_bundle_custody_unavailable", diagnostic_category=untrusted
        )
        assert error.diagnostic_category == "unclassified"
        assert "private-driver-detail" not in str(error)


def test_error_conditions_are_read_only_bounded_fixed_metadata():
    error = custody.RuntimeBundleCustodyError(
        "runtime_bundle_custody_unavailable",
        diagnostic_category="role_scope",
        diagnostic_conditions=("ordinary_table", "persistent_table"),
    )
    assert str(error) == "runtime_bundle_custody_unavailable"
    assert error.diagnostic_conditions == ("ordinary_table", "persistent_table")
    with pytest.raises(AttributeError):
        error.diagnostic_conditions = ("fsync",)
    assert set(custody._ROLE_DIAGNOSTIC_CONDITIONS) == diagnostic._ROLE_CONDITIONS


@pytest.mark.parametrize(
    "conditions",
    (
        ["fsync"],
        "fsync",
        (None,),
        (123,),
        ("private-driver-detail",),
        ("fsync", "private-driver-detail"),
        ("fsync", "fsync"),
        ("fsync",) * (len(custody._ROLE_DIAGNOSTIC_CONDITIONS) + 1),
    ),
)
def test_untrusted_diagnostic_conditions_rejected_without_values(conditions):
    with pytest.raises(ValueError) as caught:
        custody.RuntimeBundleCustodyError(
            "runtime_bundle_custody_unavailable",
            diagnostic_category="role_scope",
            diagnostic_conditions=conditions,
        )
    assert str(caught.value) == "runtime_bundle_diagnostic_conditions_invalid"
    with pytest.raises(
        ValueError, match="^runtime_bundle_diagnostic_conditions_invalid$"
    ):
        custody.RuntimeBundleCustodyError(
            "runtime_bundle_custody_unavailable",
            diagnostic_category="role_query",
            diagnostic_conditions=("fsync",),
        )


@pytest.mark.parametrize(
    "field,value,condition",
    (
        ("direct_login", False, "direct_login"),
        ("privileged", True, "nonprivileged"),
        ("other_role", True, "no_other_role"),
        ("owner_oid", 23, "owner_separation"),
        ("table_access", True, "no_table_access"),
        ("can_create", True, "no_create"),
        ("relkind", "v", "ordinary_table"),
        ("relpersistence", "u", "persistent_table"),
        ("relrowsecurity", True, "no_rls"),
        ("relforcerowsecurity", True, "no_force_rls"),
        ("fsync", "off", "fsync"),
        ("full_page_writes", "off", "full_page_writes"),
        ("synchronous_commit", "off", "synchronous_commit"),
        ("replication_role", "replica", "replication_origin"),
        ("recovery", True, "not_recovery"),
    ),
)
def test_role_diagnostic_identifies_each_existing_denied_condition(
    field, value, condition
):
    items = _catalog()
    setattr(items[0], field, value)
    with pytest.raises(custody.RuntimeBundleCustodyError) as caught:
        asyncio.run(_run_catalog(items))
    assert caught.value.diagnostic_category == "role_scope"
    assert caught.value.diagnostic_conditions == (condition,)


def test_role_diagnostic_collects_all_failures_from_same_row_without_requery():
    items = _catalog()
    role = items[0]
    role.direct_login = False
    role.privileged = role.other_role = role.table_access = role.can_create = True
    role.owner_oid = role.login_oid
    role.relkind = "v"
    role.relpersistence = "u"
    role.relrowsecurity = role.relforcerowsecurity = role.recovery = True
    role.fsync = role.full_page_writes = role.synchronous_commit = "off"
    role.replication_role = "replica"
    # Only the first row is supplied: a second query would fail this test.
    with pytest.raises(custody.RuntimeBundleCustodyError) as caught:
        asyncio.run(_run_catalog((role,)))
    assert caught.value.diagnostic_conditions == custody._ROLE_DIAGNOSTIC_CONDITIONS[1:]
    with pytest.raises(custody.RuntimeBundleCustodyError) as caught:
        asyncio.run(_run_catalog((None,)))
    assert caught.value.diagnostic_conditions == ("role_row_present",)


def test_synthetic_internal_char_bytes_explain_denials_without_relaxing_guard():
    items = _catalog()
    items[0].relkind = b"r"
    items[0].relpersistence = b"p"
    with pytest.raises(custody.RuntimeBundleCustodyError) as caught:
        asyncio.run(_run_catalog(items))
    assert caught.value.diagnostic_conditions == ("ordinary_table", "persistent_table")


@pytest.mark.parametrize(
    "collection,category",
    [
        (index, name + "_query")
        for index, name in enumerate(
            ("role", "function", "trigger", "column", "constraint")
        )
    ],
)
def test_catalog_query_failure_redacts_driver_payload(collection, category):
    values = _catalog()

    class Result:
        def __init__(self, value):
            self.value = value

        def one_or_none(self):
            return self.value

        def all(self):
            return self.value

    class Session:
        count = 0

        async def execute(self, _statement):
            index = self.count
            self.count += 1
            if index == collection:
                raise RuntimeError("private-driver-detail")
            return Result(values[index])

    with pytest.raises(custody.RuntimeBundleCustodyError) as caught:
        asyncio.run(custody.RuntimeBundleCustodyRepository._catalog(Session()))
    assert str(caught.value) == "runtime_bundle_custody_unavailable"
    assert caught.value.diagnostic_category == category
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "collection,field,value,category",
    (
        (0, "direct_login", False, "role_scope"),
        (1, None, None, "function_inventory"),
        (1, "prosrc", "changed", "function_attributes"),
        (2, None, None, "trigger_inventory"),
        (2, "tgenabled", "O", "trigger_attributes"),
        (3, "type_name", "text", "column_shape"),
        (4, None, None, "constraint_inventory"),
        (4, "definition", "CHECK (true)", "constraint_attributes"),
    ),
)
def test_rejected_catalog_groups_do_not_change_acceptance(
    collection, field, value, category
):
    items = _catalog()
    if field is None:
        items[collection].pop()
    else:
        row = items[0] if collection == 0 else items[collection][0]
        setattr(row, field, value)
    with pytest.raises(custody.RuntimeBundleCustodyError) as caught:
        asyncio.run(_run_catalog(items))
    assert str(caught.value) == "runtime_bundle_custody_unavailable"
    assert caught.value.diagnostic_category == category


def test_safe_pg_diagnostic_keeps_exact_case_and_emits_only_fixed_category():
    async def case():
        raise custody.RuntimeBundleCustodyError(
            "runtime_bundle_custody_unavailable", diagnostic_category="constraint_query"
        ) from RuntimeError("private-driver-detail")

    wrapped = pg_cases._safe_custody_diagnostic(case)
    assert wrapped.__wrapped__ is case
    assert wrapped.__name__ == case.__name__
    with pytest.raises(pytest.fail.Exception) as caught:
        asyncio.run(wrapped())
    assert str(caught.value) == "runtime_custody_diagnostic:constraint_query"
    assert not caught.value.pytrace


def _junit(*, failure=None, error=None):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for index, (module, case) in enumerate(diagnostic.RUNTIME_CUSTODY_PREFLIGHT_CASES):
        item = ET.SubElement(suite, "testcase", classname=module, name=case)
        if index == 0 and failure is not None:
            ET.SubElement(
                item, "failure", message="private-driver-detail"
            ).text = failure
        if index == 1 and error is not None:
            ET.SubElement(item, "error", message=error).text = error
    return ET.tostring(root)


def test_safe_pg_failure_has_no_exception_context_and_matches_strict_redactor():
    async def rejected():
        raise custody.RuntimeBundleCustodyError(
            "runtime_bundle_custody_unavailable", diagnostic_category="role_query"
        ) from RuntimeError("private-driver-detail")

    with pytest.raises(pytest.fail.Exception) as caught:
        asyncio.run(pg_cases._safe_custody_diagnostic(rejected)())
    failure = caught.value
    assert failure.__context__ is None
    assert failure.__cause__ is None
    assert not failure.pytrace
    assert str(failure) == "runtime_custody_diagnostic:role_query"
    summary, clean = diagnostic.redacted_report(_junit(failure=str(failure)))
    assert summary["cases"][0]["category"] == "role_query"
    assert summary["cases"][0]["status"] == "failure"
    assert not summary["all_four_passed"]
    assert b"private-driver-detail" not in clean
    assert b"runtime_bundle_custody_unavailable" not in clean


@pytest.mark.parametrize(
    "conditions", [("fsync",), ("ordinary_table", "persistent_table")]
)
def test_safe_pg_failure_and_redactor_preserve_all_fixed_conditions(conditions):
    async def rejected():
        raise custody.RuntimeBundleCustodyError(
            "runtime_bundle_custody_unavailable",
            diagnostic_category="role_scope",
            diagnostic_conditions=conditions,
        ) from RuntimeError("private-driver-detail")

    with pytest.raises(pytest.fail.Exception) as caught:
        asyncio.run(pg_cases._safe_custody_diagnostic(rejected)())
    assert caught.value.__context__ is None
    expected = "runtime_custody_diagnostic:role_scope;checks=" + ",".join(conditions)
    assert str(caught.value) == expected
    summary, clean = diagnostic.redacted_report(_junit(failure=str(caught.value)))
    assert summary["cases"][0]["category"] == "role_scope"
    assert summary["cases"][0]["failed_conditions"] == list(conditions)
    assert (
        ET.fromstring(clean).find("./testsuite/testcase/failure").get("message")
        == expected
    )
    assert not summary["all_four_passed"]


@pytest.mark.parametrize(
    "line",
    (
        "runtime_custody_diagnostic:role_scope;checks=private_driver_detail",
        "runtime_custody_diagnostic:role_scope;checks=fsync,private_driver_detail",
        "runtime_custody_diagnostic:role_scope;checks=fsync,fsync",
        "runtime_custody_diagnostic:role_scope;checks=fsync,,no_create",
        "runtime_custody_diagnostic:role_scope;checks=",
        "runtime_custody_diagnostic:role_scope;checks=fsync;checks=no_create",
        "runtime_custody_diagnostic:role_query;checks=fsync",
        "runtime_custody_diagnostic:role_scope;checks=127.0.0.1",
    ),
)
def test_strict_condition_grammar_rejects_untrusted_metadata(line):
    summary, clean = diagnostic.redacted_report(_junit(failure=line))
    assert summary["cases"][0]["category"] == "unclassified"
    assert summary["cases"][0]["failed_conditions"] == []
    assert summary["counts"]["failures"] == 1
    assert b"private_driver_detail" not in clean
    assert b"127.0.0.1" not in clean
    assert b"checks=" not in clean


def test_diagnostic_export_preserves_failure_and_drops_all_native_payloads():
    raw = _junit(
        failure="Failed: runtime_custody_diagnostic:role_query",
        error="private-driver-detail",
    )
    summary, clean = diagnostic.redacted_report(raw)
    assert summary["counts"] == {"tests": 4, "failures": 1, "errors": 1, "skipped": 0}
    assert summary["exact_four_cases_present"]
    assert not summary["all_four_passed"]
    assert summary["cases"][0]["category"] == "role_query"
    assert summary["cases"][1]["category"] == "unclassified"
    assert summary["raw_junit_sha256"] == hashlib.sha256(raw).hexdigest()
    assert b"private-driver-detail" not in clean
    assert not summary["full_postgresql_acceptance"]


def test_diagnostic_export_exact_cases_required_and_duplicate_rejected():
    summary, _clean = diagnostic.redacted_report(_junit())
    assert summary["all_four_passed"]
    root = ET.fromstring(_junit())
    suite = root.find("testsuite")
    suite.remove(suite[-1])
    summary, _clean = diagnostic.redacted_report(ET.tostring(root))
    assert not summary["exact_four_cases_present"]
    assert not summary["all_four_passed"]
    suite.append(suite[0])
    with pytest.raises(ValueError, match="case_inventory_invalid"):
        diagnostic.redacted_report(ET.tostring(root))
