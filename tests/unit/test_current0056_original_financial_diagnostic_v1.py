"""Pure redaction controls for two fixed synthetic PostgreSQL nodes."""

import hashlib
import json
import sys
from xml.sax.saxutils import escape

import pytest

from scripts import current0056_original_financial_diagnostic_v1 as diagnostic
from scripts import verify_current0056_hermetic as runner


def _progress(*, failed=False, unfinished=False):
    order = hashlib.sha256(
        json.dumps(diagnostic.NODES, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    rows = [
        {
            "schema": "ctcc.linux_postgres_progress.v2",
            "event": "collection",
            "postgres_component_index": 0,
            "selected_count": 2,
            "selected_order_sha256": order,
        }
    ]
    for index, node in enumerate(diagnostic.NODES):
        common = {
            "selection_index": index,
            "source_module": node.split("::", 1)[0],
            "node_sha256": hashlib.sha256(node.encode("ascii")).hexdigest(),
        }
        rows.append({"event": "case_start", **common})
        if failed and index == 0:
            rows.append({"event": "case_failure", **common, "phase": "call"})
        if not unfinished or index == 0:
            rows.append({"event": "case_finish", **common})
    return b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        for row in rows
    )


def _junit(*, first="passed", second="passed", first_code=None):
    elements = []
    for index, status in enumerate((first, second)):
        module, name = diagnostic.CASES[index]
        property_xml = (
            '<properties><property name="ctcc_pg52_original_financial_closed_code_v1" '
            f'value="{escape(first_code)}"/></properties>'
            if index == 0 and first_code is not None
            else ""
        )
        failure_xml = (
            f'<{status} message="private SQL values"/>' if status != "passed" else ""
        )
        elements.append(
            f'<testcase classname="{module}" name="{name}" time="285.818">'
            f"{property_xml}{failure_xml}</testcase>"
        )
    return (
        "<testsuites><testsuite>" + "".join(elements) + "</testsuite></testsuites>"
    ).encode()


def test_exact_pass_is_diagnostic_only_and_never_grants_authority():
    result = diagnostic.redacted_diagnostic(_progress(), _junit())
    assert result["status"] == "PASS_DIAGNOSTIC_ONLY"
    assert result["full_regression_passed"] is False
    assert result["execution_authority"] is False
    assert len(result["cases"]) == 2


def test_failure_and_crash_prefix_keep_only_fixed_code_and_phase():
    result = diagnostic.redacted_diagnostic(
        _progress(failed=True),
        _junit(first="failure", first_code="original_pg_original_expiry"),
    )
    assert result["status"] == "INCOMPLETE"
    assert result["cases"][0]["closed_code"] == "original_pg_original_expiry"
    assert "private SQL values" not in json.dumps(result)
    partial = diagnostic.redacted_diagnostic(_progress(unfinished=True), None)
    assert partial["status"] == "INCOMPLETE"
    assert partial["cases"] is None
    assert partial["progress"][-1] == {"event": "case_start", "selection_index": 1}


def test_unlisted_refusal_or_progress_identity_is_denied():
    with pytest.raises(ValueError, match="original_financial_junit_invalid"):
        diagnostic.redacted_diagnostic(
            _progress(), _junit(first="failure", first_code="private SQL values")
        )
    altered = _progress().replace(b'"selected_count":2', b'"selected_count":3')
    with pytest.raises(ValueError, match="original_financial_progress_invalid"):
        diagnostic.redacted_diagnostic(altered, _junit())


def test_runner_admits_only_postgres_zero_and_two_fixed_nodes(monkeypatch):
    base = [
        "verify-current56",
        "--archive",
        "source.tar",
        "--archive-sha256",
        "a" * 64,
        "--commit",
        "b" * 40,
        "--tree",
        "c" * 40,
        "--output",
        "unused",
        "--suite",
        "postgres",
        "--postgres-component-index",
    ]
    monkeypatch.setattr(sys, "argv", [*base, "0", "--original-financial-diagnostic"])
    args, index = runner._arguments()
    assert args.original_financial_diagnostic is True and index == 0
    assert diagnostic.NODES == tuple(
        module.replace(".", "/") + ".py::" + name for module, name in diagnostic.CASES
    )
    monkeypatch.setattr(sys, "argv", [*base, "1", "--original-financial-diagnostic"])
    with pytest.raises(ValueError, match="current56_runner_arguments_invalid"):
        runner._arguments()
    monkeypatch.setattr(
        sys,
        "argv",
        [*base, "0", "--original-financial-diagnostic", "--fitted-catalog-diagnostic"],
    )
    with pytest.raises(ValueError, match="current56_runner_arguments_invalid"):
        runner._arguments()
