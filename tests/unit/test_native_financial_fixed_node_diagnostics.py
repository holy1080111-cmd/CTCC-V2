"""Synthetic diagnostic boundaries only; no native process/test acceptance."""

import json
from types import SimpleNamespace

import pytest

from scripts import diagnose_native_financial_fixed_node_windows as windows
from scripts import native_financial_fixed_node_diagnostics as diagnostic


@pytest.mark.parametrize("choice", tuple(diagnostic.NODES))
def test_fixed_selector_keeps_full_original_node_identity(choice):
    node = diagnostic.selected_node(choice)
    assert node == diagnostic.NODE_FILE + "::" + diagnostic.NODE_NAMES[choice]
    assert len(diagnostic.NODES) == 6 and len(diagnostic.SCOPED_CHOICES) == 5
    assert all(
        node.startswith(diagnostic.NODE_FILE + "::")
        for node in diagnostic.NODES.values()
    )


@pytest.mark.parametrize(
    "choice",
    [None, "", "True", "pytest", "tests/unit", "late-b3;other", {"passed": True}],
)
def test_caller_cannot_select_another_path_or_pass_flag(choice):
    with pytest.raises(ValueError, match="fixed_original_node_choice_required"):
        diagnostic.selected_node(choice)


def _junit(path, *, name=None, count="1", failure=False):
    name = diagnostic.NODE_NAMES["await-temporary"] if name is None else name
    child = '<failure message="synthetic failure"/>' if failure else ""
    path.write_text(
        f'<testsuites><testsuite tests="{count}" failures="{int(failure)}" errors="0" skipped="0"><testcase classname="tests.unit.test_native_account_financial_operands_v3" name="{name}" time="2.125">{child}</testcase></testsuite></testsuites>'
    )


def test_exact_parameter_junit_is_preserved_with_its_real_outcome(tmp_path):
    path = tmp_path / "original-node.xml"
    _junit(path, failure=True)
    result = diagnostic.fixed_junit_result(path, "await-temporary")
    assert result["case"][1].endswith("[True]")
    assert result["duration_seconds"] == 2.125
    assert result["counts"] == {"tests": 1, "failures": 1, "errors": 0, "skipped": 0}


@pytest.mark.parametrize("damage", ["other_parameter", "other_name", "two_count"])
def test_junit_cannot_substitute_another_case_or_broaden_the_run(tmp_path, damage):
    path = tmp_path / "original-node.xml"
    if damage == "other_parameter":
        _junit(path, name=diagnostic.NODE_NAMES["await-retained"])
    elif damage == "other_name":
        _junit(path, name=diagnostic.ORIGINAL_NAME)
    else:
        _junit(path, count="2")
    with pytest.raises(ValueError):
        diagnostic.fixed_junit_result(path, "await-temporary")


def test_unknown_failure_locals_and_arguments_are_not_exported_as_deadlines():
    try:
        private_token = "CANARY_ONLY_PRIVATE_TOKEN"
        raise RuntimeError(private_token)
    except RuntimeError as error:
        result = diagnostic.deadline_projection(error)
    assert result["observations"] == []
    assert result["unobserved_values_are_unknown"] is True
    assert "CANARY_ONLY" not in json.dumps(result)


@pytest.mark.parametrize("damage", ["two_items", "wrong_node", "filter"])
def test_actual_collection_boundary_denies_extra_or_substituted_nodes(damage):
    node = diagnostic.NODES["late-b3"]
    option = SimpleNamespace(
        keyword="", markexpr="", ignore=[], ignore_glob=[], deselect=[]
    )
    config = SimpleNamespace(
        args=[node], option=option, getoption=lambda _name: "late-b3"
    )
    items = [SimpleNamespace(nodeid=node)]
    if damage == "two_items":
        items *= 2
    elif damage == "wrong_node":
        items[0].nodeid = diagnostic.NODES["post-close"]
    else:
        option.keyword = "partial"
    with pytest.raises(ValueError):
        diagnostic.pytest_collection_finish(SimpleNamespace(config=config, items=items))


class Clock:
    def __init__(self, increment=1):
        self.now, self.increment = 0, increment

    def __call__(self):
        result = self.now
        self.now += self.increment
        return result


class NativeJobObservations:
    """Replace only native process observations; execute actual orchestration."""

    def __init__(self, *, active=0, primary=True, query_unknown=False):
        self.active_count, self.primary = active, primary
        self.query_unknown, self.terminated, self.closed = query_unknown, False, False

    def start(self, command, cwd, environment, log_path):
        assert command == ["synthetic-python", "original-node"]
        assert environment == {"ENVIRONMENT": "test"}
        log_path.write_bytes(b"explicit-synthetic-process-output")

    def wait(self, _milliseconds):
        return self.primary

    def terminate(self):
        self.terminated = True

    def active(self):
        if self.query_unknown:
            raise OSError("CANARY_ONLY_PRIVATE_NATIVE_DETAIL")
        return self.active_count

    def exit_code(self):
        return 0

    def close(self):
        self.closed = True


def _run(tmp_path, native, *, increment=1):
    return windows.run_owned_node(
        ["synthetic-python", "original-node"],
        tmp_path,
        {"ENVIRONMENT": "test"},
        tmp_path / "original-node.log",
        native=native,
        clock=Clock(increment),
    )


def test_actual_windows_orchestration_requires_primary_exit_and_job_zero(tmp_path):
    native = NativeJobObservations()
    result = _run(tmp_path, native)
    assert result["verified_terminated"] is True
    assert native.terminated and native.closed
    assert result["node_cap_seconds"] == 300 and result["cleanup_budget_seconds"] == 60


@pytest.mark.parametrize("damage", ["orphan", "query_unknown", "primary_still_running"])
def test_exit_zero_or_termination_ack_cannot_promote_unknown_process_state(
    tmp_path, damage
):
    native = NativeJobObservations(
        active=int(damage == "orphan"),
        primary=damage != "primary_still_running",
        query_unknown=damage == "query_unknown",
    )
    result = _run(tmp_path, native, increment=2)
    assert result["verified_terminated"] is False
    assert native.terminated and native.closed
    assert "CANARY_ONLY" not in json.dumps(result)
    assert result["failure_category"] is not None


def test_expired_real_node_budget_still_fails_even_if_later_job_settles(tmp_path):
    native = NativeJobObservations(primary=False)
    original_terminate = native.terminate
    clock = Clock(10)

    def settle_after_cap():
        original_terminate()
        native.primary = True
        clock.increment = 1

    native.terminate = settle_after_cap
    result = windows.run_owned_node(
        ["synthetic-python", "original-node"],
        tmp_path,
        {"ENVIRONMENT": "test"},
        tmp_path / "original-node.log",
        native=native,
        clock=clock,
    )
    assert result["timed_out"] is True
    assert result["verified_terminated"] is True
    assert (
        windows.original_run_passed(
            result, {"counts": {"tests": 1, "failures": 0, "errors": 0, "skipped": 0}}
        )
        is False
    )
    assert native.terminated and native.closed


@pytest.mark.parametrize("late_phase", ["termination", "primary_query", "job_query"])
def test_late_native_observation_cannot_cross_shared_cleanup_deadline(
    tmp_path, late_phase
):
    native = NativeJobObservations()
    clock = Clock(0.125)
    if late_phase == "termination":
        original = native.terminate

        def late():
            original()
            clock.now += 61

        native.terminate = late
    elif late_phase == "primary_query":
        original = native.wait

        def late(milliseconds):
            result = original(milliseconds)
            if native.terminated:
                clock.now += 61
            return result

        native.wait = late
    else:
        original = native.active

        def late():
            result = original()
            clock.now += 61
            return result

        native.active = late
    result = windows.run_owned_node(
        ["synthetic-python", "original-node"],
        tmp_path,
        {"ENVIRONMENT": "test"},
        tmp_path / "original-node.log",
        native=native,
        clock=clock,
    )
    assert result["verified_terminated"] is False
    assert result["failure_category"] == "TimeoutError"
    assert native.closed


def test_owned_handle_close_failure_prevents_any_termination_receipt(tmp_path):
    native = NativeJobObservations()

    def failed_close():
        native.closed = True
        raise OSError("CANARY_ONLY_PRIVATE_CLOSE_DETAIL")

    native.close = failed_close
    result = _run(tmp_path, native)
    assert native.closed and result["verified_terminated"] is False
    assert "CANARY_ONLY" not in json.dumps(result)


def test_actual_plugin_registers_observer_before_first_result_report_hook():
    from scripts import native_financial_fixed_node_plugin as plugin

    assert plugin.pytest_runtest_makereport is diagnostic.pytest_runtest_makereport
    assert plugin.pytest_runtest_makereport.pytest_impl["tryfirst"] is True
    assert plugin.pytest_runtest_makereport.pytest_impl["hookwrapper"] is False


@pytest.mark.parametrize("job_failure", ["false_ack", "raised_error"])
def test_actual_termination_method_does_not_skip_unassigned_primary(job_failure):
    calls = []

    class Kernel:
        def TerminateJobObject(self, handle, _exit):
            calls.append(("job", handle))
            if job_failure == "raised_error":
                raise OSError("CANARY_ONLY_PRIVATE_JOB_DETAIL")
            return False

        def TerminateProcess(self, handle, _exit):
            calls.append(("primary", handle))
            return True

    native = SimpleNamespace(
        job=101, process=202, kernel=Kernel(), wait=lambda _milliseconds: False
    )
    with pytest.raises(OSError, match="windows_fixed_owned_termination_unknown"):
        windows.WindowsOwnedJob.terminate(native)
    assert calls == [("job", 101), ("primary", 202)]


def test_late_owned_handle_close_revokes_otherwise_complete_barrier(tmp_path):
    native = NativeJobObservations()
    clock = Clock(0.125)
    original = native.close

    def late_close():
        original()
        clock.now += 61

    native.close = late_close
    result = windows.run_owned_node(
        ["synthetic-python", "original-node"],
        tmp_path,
        {"ENVIRONMENT": "test"},
        tmp_path / "original-node.log",
        native=native,
        clock=clock,
    )
    assert result["verified_terminated"] is False
    assert result["failure_category"] == "TimeoutError"
    assert native.closed


def test_historical_default_and_current_original_are_distinct_selections():
    assert diagnostic.selected_node("original") == diagnostic.selected_node(
        "original-current"
    )
    assert "original" not in diagnostic.SCOPED_CHOICES
    assert diagnostic.SCOPED_CHOICES == (
        "original-current",
        "await-temporary",
        "await-retained",
        "late-b3",
        "post-close",
    )


def test_deadline_reader_refuses_changed_source_pin(monkeypatch):
    monkeypatch.setattr(diagnostic, "SOURCE_SHA", "0" * 64)
    with pytest.raises(ValueError, match="fixed_deadline_source_unverified"):
        diagnostic.deadline_projection(RuntimeError("CANARY_ONLY"))


def test_deadline_reader_refuses_non_exception_traceback_operand():
    with pytest.raises(TypeError):
        diagnostic.deadline_projection(
            SimpleNamespace(
                __traceback__=None, remaining=20, stamp={"utc_ns": 1, "monotonic_ns": 1}
            )
        )


@pytest.mark.parametrize(
    "damage",
    [
        "extra_private_field",
        "nan_duration",
        "wrong_clock_domain",
        "boolean_stamp",
        "unknown_deadline_kind",
    ],
)
def test_actual_phase_reader_accepts_baseline_then_refuses_context_fabrication(
    tmp_path, damage
):
    row = {
        "phase": "call",
        "start_wall_seconds": 1.0,
        "stop_wall_seconds": 3.0,
        "duration_seconds": 2.0,
        "observed_host_monotonic": 10.0,
        "failure_chain": None,
        "deadline_status": "OBSERVED_SCALARS_ONLY",
        "deadline_observation": {
            "schema": "ctcc.native_financial_fixed_node.deadline_observation.v1",
            "observations": [
                {
                    "kind": "actual_asyncio_timeout",
                    "scheduled_loop_deadline": 15.0,
                    "observed_host_monotonic": 20.0,
                    "clock_domain": "asyncio_loop_time_not_wall_time",
                }
            ],
            "truncated": False,
            "unobserved_values_are_unknown": True,
            "runtime_timers_modified": False,
            "diagnostic_only": True,
        },
    }
    phases = [dict(row, phase=phase) for phase in ("setup", "call", "teardown")]
    report = {
        "schema": "ctcc.native_financial_fixed_node.phases.v1",
        "node": diagnostic.NODES["original-current"],
        "source_sha256": diagnostic.SOURCE_SHA,
        "phases": phases,
        "runtime_functions_or_clocks_modified": False,
        "execution_authority": False,
        "diagnostic_only": True,
    }
    path = tmp_path / "fixed-node-phases.json"
    path.write_text(json.dumps(report))
    assert diagnostic.read_phase_report(path, "original-current")["phases"] == phases
    target = phases[1]
    if damage == "extra_private_field":
        target["raw_account"] = "CANARY_ONLY_PRIVATE_DETAIL"
    elif damage == "nan_duration":
        target["duration_seconds"] = "NaN"
    elif damage == "wrong_clock_domain":
        target["deadline_observation"]["observations"][0]["clock_domain"] = "wall_clock"
    elif damage == "boolean_stamp":
        target["deadline_observation"]["observations"] = [
            {
                "kind": "original_consume_frame",
                "source_remaining_seconds_local": 1.0,
                "original_stamp": {"utc_ns": True, "monotonic_ns": 1},
            }
        ]
    else:
        target["deadline_observation"]["observations"][0]["kind"] = "caller_passed"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        diagnostic.read_phase_report(path, "original-current")


def test_allowlist_readback_reuses_actual_main_without_importing_host_pytest(
    monkeypatch,
):
    import sys

    from scripts import diagnose_native_financial_original_node as original

    baseline = original._owned_deny_codes()
    monkeypatch.setitem(
        sys.modules, "scripts.diagnose_native_financial_original_node", None
    )
    monkeypatch.setitem(sys.modules, "__main__", original)
    assert diagnostic._owned_denies_for_readback() == baseline
    assert sys.modules["scripts.diagnose_native_financial_original_node"] is None


def test_allowlist_readback_unknown_provider_refuses_without_new_import(monkeypatch):
    import sys

    monkeypatch.setitem(
        sys.modules, "scripts.diagnose_native_financial_original_node", None
    )
    monkeypatch.setitem(sys.modules, "__main__", SimpleNamespace())
    with pytest.raises(
        ValueError, match="fixed_original_loaded_deny_interpreter_unverified"
    ):
        diagnostic._owned_denies_for_readback()
    assert sys.modules["scripts.diagnose_native_financial_original_node"] is None
