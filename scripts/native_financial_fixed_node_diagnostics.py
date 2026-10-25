"""Fixed original-node diagnostics only. Never changes application functions.

Exception text, arbitrary locals, account objects and driver parameters are not
serialized. Only source-bound scalar deadline locals and the actual standard
asyncio Timeout deadline may be observed after an original failure.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import math
import os
import re
import stat
import sys
import time
import types
import xml.etree.ElementTree as ET
from pathlib import Path

NODE_FILE = "tests/unit/test_native_account_financial_operands_v3.py"
ORIGINAL_NAME = "test_v3_full_original_history_move_core_registration_and_inert_math"
NODE_NAMES = {
    "original": ORIGINAL_NAME,
    "original-current": ORIGINAL_NAME,
    "await-temporary": "test_v3_temporary_and_retained_await_keep_wrapper_through_real_yields[True]",
    "await-retained": "test_v3_temporary_and_retained_await_keep_wrapper_through_real_yields[False]",
    "late-b3": "test_v3_B3_growth_in_last_coverage_await_is_refused_by_last_main_fence",
    "post-close": "test_v3_growth_during_final_close_remains_original_bounded_inert_snapshot",
}
NODES = {key: NODE_FILE + "::" + name for key, name in NODE_NAMES.items()}
SCOPED_CHOICES = tuple(key for key in NODES if key != "original")
SCOPED_REF = "refs/heads/work/prospective-event-cutover-pg51-catalog-20261025"
SOURCE = "app/trade_qualification/native_account_risk_assembly_v1.py"
SOURCE_SHA = "e74f4565d2c93b54fd85c588959493d8384fd8e2ffd6fbb3cfdb4526c31fd317"
_PHASES = []
_COLLECTED = None


def selected_node(choice):
    if type(choice) is not str or choice not in NODES:
        raise ValueError("fixed_original_node_choice_required")
    return NODES[choice]


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) < 1e12


def deadline_projection(error):
    """Bounded post-failure observation; never a runtime permit or timer change."""
    source = Path(__file__).resolve().parents[1] / SOURCE
    if (
        source.is_symlink()
        or hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA
    ):
        raise ValueError("fixed_deadline_source_unverified")
    ast.parse(source.read_bytes())
    rows, seen = [], set()
    current = error
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        trace = BaseException.__traceback__.__get__(current)
        for _frame in range(64):
            if trace is None:
                break
            if type(trace) is not types.TracebackType:
                raise ValueError("fixed_deadline_traceback_type_unverified")
            frame = trace.tb_frame
            code = frame.f_code
            module = frame.f_globals.get("__name__")
            if (
                module == "app.trade_qualification.native_account_risk_assembly_v1"
                and code.co_name == "_consume_financial_v3"
            ):
                original = frame.f_globals.get("_consume_financial_v3")
                loaded = sys.modules.get(module)
                if (
                    loaded is None
                    or frame.f_globals is not vars(loaded)
                    or type(original) is not types.FunctionType
                    or original.__code__ is not code
                    or Path(code.co_filename).resolve() != source.resolve()
                ):
                    raise ValueError("fixed_deadline_original_frame_unverified")
                values = frame.f_locals
                row = {
                    "kind": "original_consume_frame",
                    "source_remaining_seconds_local": None,
                    "original_stamp": None,
                }
                remaining = values.get("remaining")
                if _finite(remaining):
                    row["source_remaining_seconds_local"] = remaining
                stamp = values.get("stamp")
                if (
                    type(stamp) is dict
                    and set(stamp) == {"utc_ns", "monotonic_ns"}
                    and all(
                        type(value) is int and 0 <= value <= 32503680000000000000
                        for value in stamp.values()
                    )
                ):
                    row["original_stamp"] = {
                        name: stamp[name] for name in ("utc_ns", "monotonic_ns")
                    }
                rows.append(row)
            elif (
                module == "asyncio.timeouts"
                and code is asyncio.Timeout.__aexit__.__code__
            ):
                timer = frame.f_locals.get("self")
                if type(timer) is not asyncio.Timeout:
                    raise ValueError("fixed_deadline_asyncio_timer_type_unverified")
                when = object.__getattribute__(timer, "_when")
                if when is not None and not _finite(when):
                    raise ValueError("fixed_deadline_asyncio_value_unverified")
                rows.append(
                    {
                        "kind": "actual_asyncio_timeout",
                        "scheduled_loop_deadline": when,
                        "observed_host_monotonic": time.monotonic(),
                        "clock_domain": "asyncio_loop_time_not_wall_time",
                    }
                )
            trace = trace.tb_next
        cause = BaseException.__cause__.__get__(current)
        current = (
            cause if cause is not None else BaseException.__context__.__get__(current)
        )
    return {
        "schema": "ctcc.native_financial_fixed_node.deadline_observation.v1",
        "observations": rows[:16],
        "truncated": len(rows) > 16,
        "unobserved_values_are_unknown": True,
        "runtime_timers_modified": False,
        "diagnostic_only": True,
    }


def pytest_addoption(parser):
    parser.addoption("--ctcc-financial-fixed-node", choices=tuple(NODES), default=None)
    parser.addoption("--ctcc-financial-phase-json", default=None)


def pytest_collection_finish(session):
    global _COLLECTED
    choice = session.config.getoption("--ctcc-financial-fixed-node")
    node = selected_node(choice)
    if (
        session.config.args != [node]
        or len(session.items) != 1
        or session.items[0].nodeid.replace("\\", "/") != node
    ):
        raise ValueError("fixed_original_exact_single_collection_required")
    for name in ("keyword", "markexpr", "ignore", "ignore_glob", "deselect"):
        if getattr(session.config.option, name, None) not in (None, "", [], ()):
            raise ValueError("fixed_original_collection_filter_denied")
    _PHASES.clear()
    _COLLECTED = node


def pytest_runtest_makereport(item, call):
    if (
        _COLLECTED is None
        or item.nodeid.replace("\\", "/") != _COLLECTED
        or call.when not in ("setup", "call", "teardown")
        or len(_PHASES) >= 3
    ):
        raise ValueError("fixed_original_phase_identity_unverified")
    if (
        not all(_finite(value) for value in (call.start, call.stop, call.duration))
        or call.stop < call.start
        or call.duration < 0
    ):
        raise ValueError("fixed_original_phase_time_unverified")
    row = {
        "phase": call.when,
        "start_wall_seconds": call.start,
        "stop_wall_seconds": call.stop,
        "duration_seconds": call.duration,
        "observed_host_monotonic": time.monotonic(),
        "failure_chain": None,
        "deadline_observation": None,
        "deadline_status": "UNKNOWN_NO_FAILURE_FRAME_OBSERVED",
    }
    if call.excinfo is not None:
        from scripts.diagnose_native_financial_original_node import (
            _failure_chain_record,
        )

        error = call.excinfo.value
        try:
            row["failure_chain"] = _failure_chain_record(error, call.when)
        except Exception:  # noqa: BLE001 -- preserve original failure without private text.
            row["failure_chain"] = {
                "status": "UNAVAILABLE_FIXED_CHAIN_OBSERVATION_FAILED"
            }
        try:
            row["deadline_observation"] = deadline_projection(error)
            row["deadline_status"] = (
                "OBSERVED_SCALARS_ONLY"
                if row["deadline_observation"]["observations"]
                else "UNKNOWN_NO_MATCHED_FAILURE_FRAME"
            )
        except Exception:  # noqa: BLE001 -- never serialize private exception text.
            row["deadline_status"] = "UNAVAILABLE_FIXED_FRAME_OBSERVATION_FAILED"
    _PHASES.append(row)
    print(
        json.dumps(
            {"schema": "ctcc.native_financial_fixed_node.phase.v1", **row},
            sort_keys=True,
        ),
        flush=True,
    )


def pytest_sessionfinish(session):
    path = Path(session.config.getoption("--ctcc-financial-phase-json"))
    if (
        _COLLECTED is None
        or path.name != "fixed-node-phases.json"
        or not path.is_absolute()
        or {row["phase"] for row in _PHASES} != {"setup", "call", "teardown"}
    ):
        raise ValueError("fixed_original_phase_report_incomplete")
    record = {
        "schema": "ctcc.native_financial_fixed_node.phases.v1",
        "node": _COLLECTED,
        "source_sha256": SOURCE_SHA,
        "phases": _PHASES,
        "runtime_functions_or_clocks_modified": False,
        "execution_authority": False,
        "diagnostic_only": True,
    }
    raw = (
        json.dumps(record, sort_keys=True, allow_nan=False, separators=(",", ":"))
        + "\n"
    ).encode()
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != raw:
        raise ValueError("fixed_original_phase_report_readback_changed")


def fixed_junit_result(path, choice):
    """One actual original node, including exact parameter identity and timing."""
    if not path.exists():
        return None
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or path.is_symlink()
        or not 0 < info.st_size <= 4 * 1024 * 1024
    ):
        raise ValueError("fixed_original_junit_unsafe")
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("fixed_original_junit_entity_denied")
    root = ET.fromstring(raw)
    suites, cases = list(root.iter("testsuite")), list(root.iter("testcase"))
    if (
        len(suites) != 1
        or len(cases) != 1
        or cases[0].get("classname")
        != "tests.unit.test_native_account_financial_operands_v3"
        or cases[0].get("name") != NODE_NAMES[choice]
    ):
        raise ValueError("fixed_original_junit_exact_node_required")
    counts = {
        key: int(suites[0].attrib[key])
        for key in ("tests", "failures", "errors", "skipped")
    }
    if sum(counts[key] for key in ("failures", "errors", "skipped")) > 1:
        raise ValueError("fixed_original_junit_conflicting_outcomes")
    if counts["tests"] != 1 or any(not 0 <= value <= 1 for value in counts.values()):
        raise ValueError("fixed_original_junit_counts_unverified")
    tags = [part.tag for part in cases[0]]
    if any(
        tag not in ("failure", "error", "skipped", "system-out", "system-err")
        for tag in tags
    ) or any(
        tags.count(tag) != counts[key]
        for tag, key in (
            ("failure", "failures"),
            ("error", "errors"),
            ("skipped", "skipped"),
        )
    ):
        raise ValueError("fixed_original_junit_outcome_conflict")
    duration = float(cases[0].attrib["time"])
    if not _finite(duration) or duration < 0:
        raise ValueError("fixed_original_junit_time_unverified")
    return {
        "counts": counts,
        "case": [cases[0].get("classname"), cases[0].get("name")],
        "duration_seconds": duration,
        "outcome_tags": tags,
        "junit_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _owned_denies_for_readback():
    """Reuse loaded original code without importing host pytest or application."""
    expected = (
        Path(__file__).resolve().with_name("diagnose_native_financial_original_node.py")
    )
    provider = sys.modules.get("scripts.diagnose_native_financial_original_node")
    if provider is None:
        provider = sys.modules.get("__main__")
    filename = getattr(provider, "__file__", None)
    function = getattr(provider, "_owned_deny_codes", None)
    if (
        type(filename) is not str
        or Path(filename).resolve() != expected
        or type(function) is not types.FunctionType
        or function.__globals__ is not vars(provider)
        or Path(function.__code__.co_filename).resolve() != expected
    ):
        raise ValueError("fixed_original_loaded_deny_interpreter_unverified")
    return function()


def _validate_phase_row(row):
    keys = {
        "phase",
        "start_wall_seconds",
        "stop_wall_seconds",
        "duration_seconds",
        "observed_host_monotonic",
        "failure_chain",
        "deadline_observation",
        "deadline_status",
    }
    if (
        type(row) is not dict
        or set(row) != keys
        or row["phase"] not in ("setup", "call", "teardown")
        or not all(
            _finite(row[name])
            for name in (
                "start_wall_seconds",
                "stop_wall_seconds",
                "duration_seconds",
                "observed_host_monotonic",
            )
        )
        or row["stop_wall_seconds"] < row["start_wall_seconds"]
        or row["duration_seconds"] < 0
        or row["deadline_status"]
        not in {
            "UNKNOWN_NO_FAILURE_FRAME_OBSERVED",
            "OBSERVED_SCALARS_ONLY",
            "UNKNOWN_NO_MATCHED_FAILURE_FRAME",
            "UNAVAILABLE_FIXED_FRAME_OBSERVATION_FAILED",
        }
    ):
        raise ValueError("fixed_original_phase_fields_unverified")
    chain = row["failure_chain"]
    if chain is not None and chain != {
        "status": "UNAVAILABLE_FIXED_CHAIN_OBSERVATION_FAILED"
    }:
        if (
            type(chain) is not dict
            or set(chain)
            != {
                "schema",
                "phase",
                "chain",
                "truncated_or_cycle",
                "owned_deny_literal_sources_verified",
                "diagnostic_only",
            }
            or chain["schema"] != "ctcc.native_financial_original_node.failure_chain.v1"
            or chain["phase"] != row["phase"]
            or chain["diagnostic_only"] is not True
            or any(
                type(chain[name]) is not bool
                for name in (
                    "truncated_or_cycle",
                    "owned_deny_literal_sources_verified",
                )
            )
            or type(chain["chain"]) is not list
            or not 1 <= len(chain["chain"]) <= 8
        ):
            raise ValueError("fixed_original_failure_chain_unverified")
        allowed = _owned_denies_for_readback()
        for link in chain["chain"]:
            if (
                type(link) is not dict
                or set(link)
                not in (
                    {"module", "type", "next_link", "context_suppressed"},
                    {
                        "module",
                        "type",
                        "next_link",
                        "context_suppressed",
                        "owned_deny_code",
                    },
                )
                or type(link["module"]) is not str
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", link["module"])
                is None
                or type(link["type"]) is not str
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", link["type"]) is None
                or link["next_link"] not in {"cause", "context", "none"}
                or type(link["context_suppressed"]) is not bool
                or (
                    "owned_deny_code" in link
                    and link["owned_deny_code"]
                    not in allowed.get((link["module"], link["type"]), ())
                )
            ):
                raise ValueError("fixed_original_failure_link_unverified")
    observation = row["deadline_observation"]
    if observation is None:
        return
    if (
        type(observation) is not dict
        or set(observation)
        != {
            "schema",
            "observations",
            "truncated",
            "unobserved_values_are_unknown",
            "runtime_timers_modified",
            "diagnostic_only",
        }
        or observation["schema"]
        != "ctcc.native_financial_fixed_node.deadline_observation.v1"
        or type(observation["truncated"]) is not bool
        or observation["unobserved_values_are_unknown"] is not True
        or observation["runtime_timers_modified"] is not False
        or observation["diagnostic_only"] is not True
        or type(observation["observations"]) is not list
        or len(observation["observations"]) > 16
    ):
        raise ValueError("fixed_original_deadline_envelope_unverified")
    for item in observation["observations"]:
        if type(item) is not dict:
            raise ValueError("fixed_original_deadline_row_unverified")
        if item.get("kind") == "actual_asyncio_timeout":
            if (
                set(item)
                != {
                    "kind",
                    "scheduled_loop_deadline",
                    "observed_host_monotonic",
                    "clock_domain",
                }
                or item["clock_domain"] != "asyncio_loop_time_not_wall_time"
                or (
                    item["scheduled_loop_deadline"] is not None
                    and not _finite(item["scheduled_loop_deadline"])
                )
                or not _finite(item["observed_host_monotonic"])
            ):
                raise ValueError("fixed_original_deadline_row_unverified")
        elif item.get("kind") == "original_consume_frame":
            if set(item) != {
                "kind",
                "source_remaining_seconds_local",
                "original_stamp",
            } or (
                item["source_remaining_seconds_local"] is not None
                and not _finite(item["source_remaining_seconds_local"])
            ):
                raise ValueError("fixed_original_deadline_row_unverified")
            stamp = item["original_stamp"]
            if stamp is not None and (
                type(stamp) is not dict
                or set(stamp) != {"utc_ns", "monotonic_ns"}
                or any(
                    type(value) is not int or not 0 <= value <= 32503680000000000000
                    for value in stamp.values()
                )
            ):
                raise ValueError("fixed_original_deadline_stamp_unverified")
        else:
            raise ValueError("fixed_original_deadline_row_unverified")


def read_phase_report(path, choice):
    from scripts.evidence_json import read_evidence_json
    from scripts.prospective_capture_custody import stable_read

    raw, _identity = stable_read(path, 256 * 1024)
    record = read_evidence_json(raw.decode())
    if (
        type(record) is not dict
        or set(record)
        != {
            "schema",
            "node",
            "source_sha256",
            "phases",
            "runtime_functions_or_clocks_modified",
            "execution_authority",
            "diagnostic_only",
        }
        or record["schema"] != "ctcc.native_financial_fixed_node.phases.v1"
        or record["node"] != selected_node(choice)
        or record["source_sha256"] != SOURCE_SHA
        or record["runtime_functions_or_clocks_modified"] is not False
        or record["execution_authority"] is not False
        or record["diagnostic_only"] is not True
        or type(record["phases"]) is not list
        or [row.get("phase") for row in record["phases"]]
        != ["setup", "call", "teardown"]
    ):
        raise ValueError("fixed_original_phase_report_unverified")
    for row in record["phases"]:
        _validate_phase_row(row)
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "phases": record["phases"],
        "unobserved_deadlines_are_unknown": True,
    }
