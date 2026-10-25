"""Passive fixed-node phase journal. Runs only in the isolated cloud worker."""

from __future__ import annotations

import json
import os
import time

import pytest

from scripts.pg_original_node_diagnostic import PHASE_FILE, PROFILES, RESULTS, node_id

_STREAM = None
_SEQUENCE = 0
_PROFILE = None


def _event(event, **fields):
    global _SEQUENCE
    if _STREAM is None:
        raise RuntimeError("pg_original_phase_stream_unavailable")
    _SEQUENCE += 1
    if _SEQUENCE > 32:
        raise RuntimeError("pg_original_phase_event_bound")
    raw = (
        json.dumps(
            {
                "schema": "ctcc.pg_original_node_phase_event.v1",
                "sequence": _SEQUENCE,
                "profile": _PROFILE,
                "nodeid": node_id(_PROFILE),
                "event": event,
                "utc_ns": time.time_ns(),
                "monotonic_ns": time.monotonic_ns(),
                **fields,
            },
            sort_keys=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    _STREAM.write(raw)
    _STREAM.flush()
    os.fsync(_STREAM.fileno())


def pytest_configure(config):
    global _STREAM, _PROFILE, _SEQUENCE
    profile = os.environ.get("CTCC_PG_DIAGNOSTIC_PROFILE", "")
    if (
        profile not in PROFILES
        or config.args != [node_id(profile)]
        or config.option.collectonly
        or _STREAM is not None
    ):
        raise RuntimeError("pg_original_phase_configuration_invalid")
    _PROFILE, _SEQUENCE = profile, 0
    _STREAM = (RESULTS / PHASE_FILE).open("xb")


def pytest_sessionstart(session):
    _event("session_start")


def pytest_runtest_logstart(nodeid, location):
    if nodeid != node_id(_PROFILE):
        raise RuntimeError("pg_original_phase_node_changed")
    _event("node_start")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    if item.nodeid != node_id(_PROFILE):
        raise RuntimeError("pg_original_phase_node_changed")
    _event("phase_start", when="setup")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    if item.nodeid != node_id(_PROFILE):
        raise RuntimeError("pg_original_phase_node_changed")
    _event("phase_start", when="call")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    if item.nodeid != node_id(_PROFILE) or nextitem is not None:
        raise RuntimeError("pg_original_phase_node_changed")
    _event("phase_start", when="teardown")


def pytest_runtest_logreport(report):
    if report.nodeid != node_id(_PROFILE):
        raise RuntimeError("pg_original_phase_node_changed")
    _event(
        "phase_report",
        when=report.when,
        outcome=report.outcome,
        duration_seconds=report.duration,
    )


def pytest_runtest_logfinish(nodeid, location):
    if nodeid != node_id(_PROFILE):
        raise RuntimeError("pg_original_phase_node_changed")
    _event("node_finish")


def pytest_sessionfinish(session, exitstatus):
    try:
        _event("session_finish", exitstatus=int(exitstatus))
    finally:
        pytest_unconfigure(session.config)


def pytest_unconfigure(config):
    global _STREAM
    if _STREAM is not None:
        _STREAM.close()
        _STREAM = None
