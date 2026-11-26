"""Operator CLI wiring using only existing MIE test fixtures."""

import json

import pytest

from app.domain.source_primitives import sha
from app.mie.validation import cash_pnl_concentration_v1, cost_stress_v1
from scripts import inspect_offline_mie
from tests.unit.mie.test_cash_pnl_concentration_v1 import _args, _cohort
from tests.unit.mie.test_cash_pnl_concentration_v1 import (
    plan as plan,  # noqa: PLC0414 -- pytest collects this fixture.
)
from tests.unit.mie.test_cost_stress_v1 import arguments
from tests.unit.mie.test_cost_stress_v1 import (
    stress_case as stress_case,  # noqa: PLC0414 -- pytest collects this fixture.
)
from tests.unit.mie.test_economic_descriptive_metrics import outcomes
from tests.unit.mie.test_shadow_trace import complete_shadow_trace


def _input(workspace, name, raw):
    path = workspace / (name + ".json")
    path.write_bytes(raw)
    return [f"--{name}", str(path), f"--{name}-sha256", sha(raw)]


def _assert_diagnostic(response, mode):
    assert response["schema"] == "ctcc.mie.offline_diagnostic.v1"
    assert response["mode"] == mode and response["status"] == "DIAGNOSTIC_COMPLETED"
    assert response["admission"] == "DENY"
    for flag in (
        "gate3_accepted",
        "gate4_accepted",
        "predictive_oos_accepted",
        "economic_oos_accepted",
        "independent_preregistration_verified",
        "source_authenticity_verified",
        "real_trade_sample_verified",
        "execution_authority",
    ):
        assert response[flag] is False


def _run(workspace, capsys, mode, args):
    original = {path.name: path.read_bytes() for path in workspace.iterdir()}
    assert inspect_offline_mie.main([mode, *args]) == 0
    assert {path.name: path.read_bytes() for path in workspace.iterdir()} == original
    captured = capsys.readouterr()
    assert not captured.err
    response = json.loads(captured.out)
    _assert_diagnostic(response, mode)
    return response


def test_inspects_the_existing_shadow_trace_without_emitting_its_rows(tmp_path, capsys):
    trace = complete_shadow_trace()
    args = _input(tmp_path, "trace", trace.model_dump_json().encode("utf-8"))
    response = _run(tmp_path, capsys, "shadow", args)

    assert response["replay_sha256"] == trace.replay_sha256()
    assert response["trace_authority"] == "shadow_only"
    assert {"report", "trace", "decision", "forecast"}.isdisjoint(response)


@pytest.mark.parametrize(
    "change", ["mismatched-pin", "missing-identity", "nonlocal-file", "parent-alias"]
)
def test_shadow_refuses_tamper_or_an_incomplete_trace(tmp_path, capsys, change):
    trace = complete_shadow_trace().model_dump(mode="json")
    if change == "missing-identity":
        del trace["trace_id"]
    args = _input(tmp_path, "trace", json.dumps(trace).encode("utf-8"))
    if change == "mismatched-pin":
        args[-1] = "0" * 64
    if change == "nonlocal-file":
        args[1] = "//ctcc-no-network.invalid/share/trace.json"
    if change == "parent-alias":
        args[1] = str(tmp_path / "alias" / ".." / "trace.json")

    assert inspect_offline_mie.main(["shadow", *args]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err == '{"admission":"DENY","status":"REFUSED"}\n'


def test_reports_existing_economic_diagnostic(tmp_path, capsys, plan):
    cohort = _cohort(plan, outcomes())
    arguments = _args(plan, cohort)
    expected = cash_pnl_concentration_v1.evaluate_cash_pnl_concentration_v1(**arguments)
    args = _input(tmp_path, "plan", arguments["plan_bytes"])
    args += _input(tmp_path, "cohort", arguments["cohort_bytes"])

    response = _run(tmp_path, capsys, "economic", args)
    assert response["report_sha256"] == expected.sha256
    assert response["report"] == json.loads(expected.payload)


def test_reports_existing_cost_stress_diagnostic(tmp_path, capsys, stress_case):
    plan, path = stress_case
    inputs = arguments(plan, path)
    expected = cost_stress_v1.evaluate_cost_stress_v1(**inputs)
    args = _input(tmp_path, "plan", inputs["plan_bytes"])
    args += _input(tmp_path, "path", inputs["path_bytes"])

    response = _run(tmp_path, capsys, "cost-stress", args)
    assert response["report_sha256"] == expected.sha256
    assert response["report"] == json.loads(expected.payload)
