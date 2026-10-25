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


_TRACE_SOURCE_PINS = {
    "app/trade_qualification/native_account_risk_assembly_v1.py": "e74f4565d2c93b54fd85c588959493d8384fd8e2ffd6fbb3cfdb4526c31fd317",
    "app/trade_qualification/account_history_source_v1.py": "fb06524a1ee540e36bc3b1366acfc647edd587b06bb8bd517ef22267fbcb6e79",
    "app/trade_qualification/account_history_source_bundle_v1.py": "06a993d5b10961f302107ff2a45dc5cbfb282384793cb4b1b317563b35c460ac",
    "app/trade_qualification/controlled_writer_coverage_v1.py": "06c5221b4a9aa4f1fcea241881f6fcfd573b0b0b92a752f4b85bcf94700065bf",
    "app/trade_qualification/account_current_source_v4.py": "7d2ce189d92449035d6542386c656b8a37a6b1115ade636b5c7dc7b9afd1e177",
    "app/trade_qualification/account_observation_index.py": "b2184bf6806e6ad978a8d6b74e533e5f866e56579fcc9a176aff7f95920bdec9",
    "app/trade_qualification/account_bootstrap_runtime.py": "39bc6cd29a44ad442fee7e5a7f0e2410b262b7540aae565e3a91ea9836b0397e",
    "app/trade_qualification/account_native_clock.py": "61b1d50304d34c9c1790c85c948eaf849298ee617764c7c2ec65911f314ecb0f",
    "app/trade_qualification/account_native_proof_storage.py": "0081e24c454dbe85beff1a53c28066f02b8a92d394cb1e23b4888b69360c292d",
    "app/trade_qualification/account_native_runtime.py": "32cb9e1fde514ef0e91b447b7c0dfef459ce276cd08bf4b9a293cce896d3615c",
    "app/trade_qualification/account_history_native_stage_v1.py": "18086123e3226f27718f23ac3a21df22509ce0debfb057061d00dee7c8f5b7ba",
    "app/database/repositories/account_history_partition.py": "9fb11f4daed67ee970834fa5a5e4d7d1cc1c3533dc2b69862697c6f7777d4629",
    "app/database/repositories/public_runtime_bundle_custody.py": "69cca49251e9bce5f97261a8171aeefabac208a4d1244d864380727dd1597d30",
    "app/domain/source_primitives.py": "660a18a2bab787297c370d5316fa08cbf0ecf01273ea95ed990b949c4953420d",
    "tests/unit/test_native_account_financial_operands_v3.py": "0879abd5ce8170203bc8c3a8b969d0f73e4e6f66701169067e53d45caba16fbf",
    "tests/unit/test_account_history_source_v1.py": "0abc9de525d36cfb8adbbb17a4e7880939ecfc6535aa1bde6ac2a427a3b8df13",
    "tests/unit/test_account_current_source_v7.py": "2f306107444f5d637f348fe5ec21dbd6c447aed791e501648d3765c2f3774c98",
}


def _trace_constant_key(value, budget, depth=0):
    """Only exact compiler constants; no arbitrary str/repr or object methods."""
    budget[0] -= 1
    if depth > 64 or budget[0] < 0:
        raise ValueError("fixed_traceback_code_constant_bound")
    kind = type(value)
    if value is None or value is Ellipsis:
        return ("none",) if value is None else ("ellipsis",)
    if kind in (bool, int, str):
        return kind.__name__, value
    if kind is bytes:
        return "bytes", value.hex()
    if kind is float:
        return "float", value.hex()
    if kind is complex:
        return "complex", value.real.hex(), value.imag.hex()
    if kind is tuple:
        return "tuple", tuple(
            _trace_constant_key(item, budget, depth + 1) for item in value
        )
    if kind is frozenset:
        items = [
            json.dumps(
                _trace_constant_key(item, budget, depth + 1),
                separators=(",", ":"),
                ensure_ascii=True,
            )
            for item in value
        ]
        return "frozenset", tuple(sorted(items))
    if kind is types.CodeType:
        return (
            "code",
            value.co_name,
            value.co_qualname,
            value.co_firstlineno,
            value.co_argcount,
            value.co_posonlyargcount,
            value.co_kwonlyargcount,
            value.co_nlocals,
            value.co_stacksize,
            value.co_flags,
            value.co_code.hex(),
            value.co_linetable.hex(),
            value.co_exceptiontable.hex(),
            value.co_names,
            value.co_varnames,
            value.co_freevars,
            value.co_cellvars,
            tuple(
                _trace_constant_key(item, budget, depth + 1) for item in value.co_consts
            ),
        )
    raise ValueError("fixed_traceback_unknown_code_constant")


def _trace_code_key(code):
    if type(code) is not types.CodeType:
        raise ValueError("fixed_traceback_code_type_unverified")
    value = _trace_constant_key(code, [16384])
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _trace_source_catalog():
    """Compile pinned source as data; no execution or application imports."""
    root = Path(__file__).resolve().parents[1]
    catalog, filenames = {}, {}
    if type(_TRACE_SOURCE_PINS) is not dict or not 1 <= len(_TRACE_SOURCE_PINS) <= 24:
        raise ValueError("fixed_traceback_source_inventory_bound")
    for name, digest in _TRACE_SOURCE_PINS.items():
        if (
            type(name) is not str
            or re.fullmatch(r"(?:app|tests)/[A-Za-z0-9_/]+\.py", name) is None
            or type(digest) is not str
            or re.fullmatch("[a-f0-9]{64}", digest) is None
        ):
            raise ValueError("fixed_traceback_source_inventory_unverified")
        source = root / name
        before = source.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or source.is_symlink()
            or before.st_nlink != 1
            or getattr(before, "st_file_attributes", 0) & 0x400
            or not 0 < before.st_size <= 4 * 1024 * 1024
        ):
            raise ValueError("fixed_traceback_source_file_unverified")
        raw = source.read_bytes()
        after = source.lstat()
        fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            tuple(getattr(before, key) for key in fields)
            != tuple(getattr(after, key) for key in fields)
            or len(raw) != before.st_size
            or hashlib.sha256(raw).hexdigest() != digest
        ):
            raise ValueError("fixed_traceback_source_identity_changed")
        # Source is compiled only to compare real frame bytecode/constant/
        # exception/line tables; no compiled top-level code is executed.
        compiled = compile(
            raw, str(source), "exec", dont_inherit=True, optimize=sys.flags.optimize
        )
        pending, keys = [compiled], {}
        while pending:
            code = pending.pop()
            if len(keys) >= 4096:
                raise ValueError("fixed_traceback_source_code_count_bound")
            keys[_trace_code_key(code)] = {
                "function": code.co_name,
                "lines": frozenset(
                    line for _start, _stop, line in code.co_lines() if line is not None
                ),
            }
            pending.extend(
                item for item in code.co_consts if type(item) is types.CodeType
            )
        catalog[name] = {"sha256": digest, "code": keys}
        filenames[os.path.normcase(os.path.normpath(str(source)))] = name
    return catalog, filenames


def traceback_projection(error):
    """Post-failure, bounded original tracebacks; never reads frame locals."""
    catalog, filenames = _trace_source_catalog()
    nodes, seen = [], set()
    current = error
    while current is not None and len(nodes) < 8 and id(current) not in seen:
        seen.add(id(current))
        trace = BaseException.__traceback__.__get__(current)
        frames, visited, omitted, mismatched = [], 0, 0, 0
        while trace is not None and visited < 64:
            if type(trace) is not types.TracebackType:
                raise ValueError("fixed_traceback_runtime_type_unverified")
            visited += 1
            code = trace.tb_frame.f_code
            filename = code.co_filename
            name = (
                filenames.get(os.path.normcase(os.path.normpath(filename)))
                if type(filename) is str and os.path.isabs(filename)
                else None
            )
            if name is None:
                omitted += 1
            else:
                known = catalog[name]["code"].get(_trace_code_key(code))
                if (
                    known is None
                    or known["function"] != code.co_name
                    or trace.tb_lineno not in known["lines"]
                ):
                    mismatched += 1
                else:
                    frames.append(
                        {
                            "source": name,
                            "source_sha256": catalog[name]["sha256"],
                            "function": known["function"],
                            "line": trace.tb_lineno,
                        }
                    )
            trace = trace.tb_next
        nodes.append(
            {
                "chain_index": len(nodes),
                "frames": frames,
                "visited_frames": visited,
                "unlisted_frames_omitted": omitted,
                "code_mismatch_frames_omitted": mismatched,
                "frames_truncated": trace is not None,
            }
        )
        cause = BaseException.__cause__.__get__(current)
        current = (
            cause if cause is not None else BaseException.__context__.__get__(current)
        )
    return {
        "schema": "ctcc.native_financial_fixed_node.traceback_projection.v1",
        "exception_nodes": nodes,
        "chain_truncated_or_cycle": current is not None,
        "source_files_verified": True,
        "unobserved_frames_are_unknown": True,
        "runtime_functions_or_clocks_modified": False,
        "diagnostic_only": True,
    }


def _validate_traceback_projection(value, chain):
    if type(value) is not dict:
        raise ValueError("fixed_traceback_readback_envelope_unverified")
    if (
        set(value) == {"status"}
        and type(value["status"]) is str
        and value["status"] == "UNAVAILABLE_SOURCE_BOUND_TRACEBACK_PROJECTION"
    ):
        return
    if (
        type(value) is not dict
        or set(value)
        != {
            "schema",
            "exception_nodes",
            "chain_truncated_or_cycle",
            "source_files_verified",
            "unobserved_frames_are_unknown",
            "runtime_functions_or_clocks_modified",
            "diagnostic_only",
        }
        or type(value["schema"]) is not str
        or value["schema"] != "ctcc.native_financial_fixed_node.traceback_projection.v1"
        or type(value["chain_truncated_or_cycle"]) is not bool
        or value["source_files_verified"] is not True
        or value["unobserved_frames_are_unknown"] is not True
        or value["runtime_functions_or_clocks_modified"] is not False
        or value["diagnostic_only"] is not True
        or type(value["exception_nodes"]) is not list
        or not 1 <= len(value["exception_nodes"]) <= 8
    ):
        raise ValueError("fixed_traceback_readback_envelope_unverified")
    if (
        type(chain) is dict
        and "chain" in chain
        and (
            len(chain["chain"]) != len(value["exception_nodes"])
            or chain["truncated_or_cycle"] != value["chain_truncated_or_cycle"]
        )
    ):
        raise ValueError("fixed_traceback_readback_chain_mismatch")
    catalog, _filenames = _trace_source_catalog()
    for index, node in enumerate(value["exception_nodes"]):
        if (
            type(node) is not dict
            or set(node)
            != {
                "chain_index",
                "frames",
                "visited_frames",
                "unlisted_frames_omitted",
                "code_mismatch_frames_omitted",
                "frames_truncated",
            }
            or type(node["chain_index"]) is not int
            or node["chain_index"] != index
            or type(node["frames"]) is not list
            or len(node["frames"]) > 64
            or type(node["frames_truncated"]) is not bool
            or any(
                type(node[name]) is not int or not 0 <= node[name] <= 64
                for name in (
                    "visited_frames",
                    "unlisted_frames_omitted",
                    "code_mismatch_frames_omitted",
                )
            )
            or node["visited_frames"]
            != len(node["frames"])
            + node["unlisted_frames_omitted"]
            + node["code_mismatch_frames_omitted"]
            or (node["frames_truncated"] and node["visited_frames"] != 64)
        ):
            raise ValueError("fixed_traceback_readback_node_unverified")
        for frame in node["frames"]:
            if type(frame) is not dict or set(frame) != {
                "source",
                "source_sha256",
                "function",
                "line",
            }:
                raise ValueError("fixed_traceback_readback_frame_fields")
            name, function, line = frame["source"], frame["function"], frame["line"]
            if (
                type(name) is not str
                or name not in catalog
                or type(frame["source_sha256"]) is not str
                or frame["source_sha256"] != catalog[name]["sha256"]
                or type(function) is not str
                or type(line) is not int
                or not any(
                    row["function"] == function and line in row["lines"]
                    for row in catalog[name]["code"].values()
                )
            ):
                raise ValueError("fixed_traceback_readback_frame_source_unverified")


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
        "traceback_projection": None,
    }
    if call.excinfo is not None:
        from scripts.diagnose_native_financial_original_node import (
            _failure_chain_record,
        )

        error = call.excinfo.value
        try:
            row["traceback_projection"] = traceback_projection(error)
        except Exception:  # noqa: BLE001 -- preserve original failure; no private text.
            row["traceback_projection"] = {
                "status": "UNAVAILABLE_SOURCE_BOUND_TRACEBACK_PROJECTION"
            }
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
            {"schema": "ctcc.native_financial_fixed_node.phase.v2", **row},
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
        "schema": "ctcc.native_financial_fixed_node.phases.v2",
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


def _validate_phase_row(row, *, wire_version=1):
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
    if wire_version == 2:
        keys.add("traceback_projection")
    elif wire_version != 1:
        raise ValueError("fixed_original_phase_wire_version_unverified")
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
    if wire_version == 2:
        projection = row["traceback_projection"]
        if chain is None:
            if projection is not None:
                raise ValueError("fixed_traceback_projection_without_failure")
        elif projection is None:
            raise ValueError("fixed_traceback_projection_missing")
        else:
            _validate_traceback_projection(projection, chain)
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
        or record["schema"]
        not in {
            "ctcc.native_financial_fixed_node.phases.v1",
            "ctcc.native_financial_fixed_node.phases.v2",
        }
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
    wire_version = 2 if record["schema"].endswith(".v2") else 1
    for row in record["phases"]:
        _validate_phase_row(row, wire_version=wire_version)
    result = {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "phases": record["phases"],
        "unobserved_deadlines_are_unknown": True,
    }
    if wire_version == 2:
        result["phase_wire_schema"] = record["schema"]
        result["unobserved_traceback_frames_are_unknown"] = True
    return result
