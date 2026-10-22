"""Timing only: fixed pytest cases and test helpers, preserving current task/await."""

from __future__ import annotations

import functools
import inspect
import os
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest

from scripts.pg_phase_policy_v1 import EXPERIMENTS, canonical, required, validate_event

_file = None
_experiment = None
_sequence = 0
_previous = 0
_node = "SESSION"
_wrapped = []
_stack = ExitStack()


def _emit(kind, phase, outcome, duration=0):
    global _sequence, _previous
    row = {
        "sequence": _sequence,
        "kind": kind,
        "nodeid": _node,
        "phase": phase,
        "monotonic_ns": time.monotonic_ns(),
        "duration_ns": duration,
        "outcome": outcome,
    }
    validate_event(
        row, experiment=_experiment, sequence=_sequence, previous_ns=_previous
    )
    raw = canonical(row)
    _file.write(raw)
    _file.flush()
    os.fsync(_file.fileno())
    _previous, _sequence = row["monotonic_ns"], _sequence + 1


def pytest_addoption(parser):
    parser.addoption(
        "--ctcc-pg-phase-experiment", choices=tuple(EXPERIMENTS), default=None
    )


@contextmanager
def _phase_stream():
    with Path("/validation-results/phase-events.jsonl").open("xb") as stream:
        yield stream


def pytest_configure(config):
    global _experiment, _file
    _experiment = config.getoption("--ctcc-pg-phase-experiment")
    required(_experiment in EXPERIMENTS)
    _file = _stack.enter_context(_phase_stream())


def _wrap(module, attribute, phase, *, revision=False):
    original = getattr(module, attribute)
    required(callable(original) and not inspect.isasyncgenfunction(original))

    def label(args):
        if not revision:
            return phase
        value = args[1]
        required(
            type(value) is str
            and len(value) == 4
            and value.isascii()
            and value.isdecimal()
        )
        return "migration_" + value

    if inspect.iscoroutinefunction(original):

        @functools.wraps(original)
        async def wrapped(*args, **kwargs):
            selected, started = label(args), time.monotonic_ns()
            _emit("helper_start", selected, "RUNNING")
            outcome = "ERROR"
            try:
                value = await original(*args, **kwargs)
                outcome = "RETURNED"
                return value
            finally:
                _emit("helper_end", selected, outcome, time.monotonic_ns() - started)
    else:

        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            selected, started = label(args), time.monotonic_ns()
            _emit("helper_start", selected, "RUNNING")
            outcome = "ERROR"
            try:
                value = original(*args, **kwargs)
                outcome = "RETURNED"
                return value
            finally:
                _emit("helper_end", selected, outcome, time.monotonic_ns() - started)

    setattr(module, attribute, wrapped)
    _wrapped.append((module, attribute, original))


def pytest_collection_modifyitems(items):
    required(tuple(item.nodeid for item in items) == EXPERIMENTS[_experiment])
    collected = {}
    for item in items:
        relative = item.nodeid.split("::", 1)[0]
        module = item.module
        qualified = relative.removesuffix(".py").replace("/", ".")
        required(module.__name__ in (qualified, qualified.rsplit(".", 1)[-1]))
        required(sys.modules.get(module.__name__) is module)
        required(Path(module.__file__).as_posix().endswith(relative))
        required(Path(item.path).resolve() == Path(module.__file__).resolve())
        required(relative not in collected or collected[relative] is module)
        collected[relative] = module
    # The source files and application methods remain untouched. Only these
    # already imported test helper references get direct same-task wrappers.
    for name, attribute, phase, revision in (
        (
            "tests.integration.test_durable_migration_downgrade",
            "migrate",
            "historical_migrate_helper",
            True,
        ),
        (
            "tests.integration.test_submission_transport_denial_migration",
            "migrate",
            "body_migrate_helper",
            True,
        ),
        (
            "tests.integration.test_submission_reporting_repository",
            "setup",
            "reports_setup",
            False,
        ),
        (
            "tests.integration.test_qualification_ledger_repository",
            "initialize",
            "ledger_initialize",
            False,
        ),
        (
            "tests.integration.test_submission_transport_denial_migration",
            "_private_rows",
            "private_rows",
            False,
        ),
        (
            "tests.integration.test_public_receipt_clock_custody_repository_v1",
            "_apply_revision",
            "catalog_migrate_helper",
            True,
        ),
    ):
        if (
            name.endswith("test_public_receipt_clock_custody_repository_v1")
            and _experiment == "A"
        ):
            continue
        relative = name.replace(".", "/") + ".py"
        module = collected.get(relative) or sys.modules.get(name)
        required(module is not None)
        required(module.__name__ in (name, name.rsplit(".", 1)[-1]))
        required(sys.modules.get(module.__name__) is module)
        required(Path(module.__file__).as_posix().endswith(relative))
        _wrap(module, attribute, phase, revision=revision)
        required(getattr(module, attribute).__wrapped__ is _wrapped[-1][2])
        _emit("helper_installed", phase, "COMPLETE")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    global _node
    _node = item.nodeid
    started = time.monotonic_ns()
    _emit("phase_start", "setup", "RUNNING")
    yield
    _emit("phase_end", "setup", "RETURNED", time.monotonic_ns() - started)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    started = time.monotonic_ns()
    _emit("phase_start", "call", "RUNNING")
    yield
    _emit("phase_end", "call", "RETURNED", time.monotonic_ns() - started)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    started = time.monotonic_ns()
    _emit("phase_start", "teardown", "RUNNING")
    yield
    _emit("phase_end", "teardown", "RETURNED", time.monotonic_ns() - started)


def pytest_runtest_logreport(report):
    required(
        report.when in ("setup", "call", "teardown")
        and report.outcome in ("passed", "failed", "skipped")
    )
    _emit(
        "report",
        report.when,
        report.outcome.upper(),
        int(report.duration * 1_000_000_000),
    )


def pytest_sessionfinish(session, exitstatus):
    global _node
    _node = "SESSION"
    _emit("session_end", "session", "COMPLETE" if exitstatus == 0 else "INCOMPLETE")


def pytest_unconfigure():
    while _wrapped:
        module, attribute, original = _wrapped.pop()
        setattr(module, attribute, original)
    if _file is not None:
        _stack.close()
