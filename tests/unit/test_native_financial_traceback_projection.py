"""Synthetic source/exception projection only; no application/native acceptance."""

import ast
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import diagnose_native_financial_original_node as linux_original
from scripts import native_financial_fixed_node_diagnostics as diagnostic


def _source(monkeypatch, tmp_path, raw=None, *, filename=None):
    raw = b"def fail(error):\n    raise error\n" if raw is None else raw
    path = tmp_path / "tests" / "allowed.py"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(raw)
    monkeypatch.setattr(
        diagnostic, "__file__", str(tmp_path / "scripts" / "observer.py")
    )
    monkeypatch.setattr(
        diagnostic,
        "_TRACE_SOURCE_PINS",
        {"tests/allowed.py": hashlib.sha256(raw).hexdigest()},
    )
    namespace = {}
    # Explicit synthetic code only. No application source/import/authority.
    exec(compile(raw, str(path) if filename is None else filename, "exec"), namespace)  # noqa: S102
    return path, namespace


def _project(namespace, error):
    try:
        namespace["fail"](error)
    except BaseException as caught:  # noqa: BLE001 -- synthetic capture includes cancellation without changing original propagation.
        return diagnostic.traceback_projection(caught)
    raise AssertionError("synthetic_failure_not_raised")


def test_real_exception_frames_export_only_pinned_relative_source_scalars(
    monkeypatch, tmp_path
):
    _path, namespace = _source(monkeypatch, tmp_path)
    result = _project(namespace, RuntimeError("CANARY_SECRET_SQL_HEADER"))
    diagnostic._validate_traceback_projection(result, None)
    frames = result["exception_nodes"][0]["frames"]
    assert frames == [
        {
            "source": "tests/allowed.py",
            "source_sha256": diagnostic._TRACE_SOURCE_PINS["tests/allowed.py"],
            "function": "fail",
            "line": 2,
        }
    ]
    payload = json.dumps(result)
    assert "CANARY_SECRET" not in payload and str(tmp_path) not in payload
    assert "__traceback__" not in payload and "locals" not in payload


def test_exception_private_args_and_custom_string_methods_are_never_invoked(
    monkeypatch, tmp_path
):
    _path, namespace = _source(monkeypatch, tmp_path)

    class PrivateError(RuntimeError):
        def __str__(self):
            raise AssertionError("str_invoked")

        def __repr__(self):
            raise AssertionError("repr_invoked")

    result = _project(
        namespace, PrivateError("Bearer CANARY_TOKEN", {"SQL": "CANARY_SQL"})
    )
    assert "CANARY" not in json.dumps(result)
    assert result["exception_nodes"][0]["frames"][0]["line"] == 2


@pytest.mark.parametrize(
    "filename",
    [
        "C:/Users/CANARY_PRIVATE/person/secret.py",
        "/home/CANARY_PRIVATE/secret.py",
        "<CANARY_PRIVATE>",
        "tests/allowed.py",
    ],
)
def test_private_unknown_and_relative_filename_frames_remain_unexported(
    monkeypatch, tmp_path, filename
):
    _path, namespace = _source(monkeypatch, tmp_path, filename=filename)
    result = _project(namespace, RuntimeError("CANARY_PRIVATE"))
    node = result["exception_nodes"][0]
    assert node["frames"] == [] and node["unlisted_frames_omitted"] >= 1
    assert "CANARY" not in json.dumps(result)


def test_changed_source_is_refused_before_any_frame_projection(monkeypatch, tmp_path):
    path, namespace = _source(monkeypatch, tmp_path)
    baseline = _project(namespace, RuntimeError("CANARY"))
    assert baseline["exception_nodes"][0]["frames"]
    path.write_bytes(path.read_bytes() + b"# changed\n")
    with pytest.raises(ValueError, match="fixed_traceback_source_identity_changed"):
        _project(namespace, RuntimeError("CANARY"))


def test_same_filename_different_code_cannot_be_exported(monkeypatch, tmp_path):
    path, namespace = _source(monkeypatch, tmp_path)
    assert _project(namespace, RuntimeError())["exception_nodes"][0]["frames"]
    changed = {}
    exec(  # noqa: S102 -- explicit synthetic code only; no application execution.
        compile("def fail(error):\n    pass\n    raise error\n", str(path), "exec"),
        changed,
    )
    node = _project(changed, RuntimeError("CANARY"))["exception_nodes"][0]
    assert node["frames"] == [] and node["code_mismatch_frames_omitted"] == 1


def test_non_exception_operand_and_unknown_code_constants_do_not_call_repr(
    monkeypatch, tmp_path
):
    _path, namespace = _source(monkeypatch, tmp_path)
    with pytest.raises(TypeError):
        diagnostic.traceback_projection(SimpleNamespace(__traceback__=None))

    class Private:
        def __str__(self):
            raise AssertionError("str_invoked")

        def __repr__(self):
            raise AssertionError("repr_invoked")

    code = namespace["fail"].__code__.replace(co_consts=(Private(),))
    with pytest.raises(ValueError, match="fixed_traceback_unknown_code_constant"):
        diagnostic._trace_code_key(code)


def test_actual_deep_traceback_is_bounded_to_64_frames(monkeypatch, tmp_path):
    _path, namespace = _source(
        monkeypatch,
        tmp_path,
        b"def fail(error, depth=90):\n    if depth:\n        return fail(error, depth-1)\n    raise error\n",
    )
    result = _project(namespace, RuntimeError("CANARY"))
    node = result["exception_nodes"][0]
    assert node["visited_frames"] == 64 and node["frames_truncated"] is True
    assert len(node["frames"]) <= 64
    diagnostic._validate_traceback_projection(result, None)


@pytest.mark.parametrize("cycle", [False, True])
def test_actual_exception_chain_is_bounded_to_eight_and_preserves_unknown_tail(
    monkeypatch, tmp_path, cycle
):
    _path, namespace = _source(monkeypatch, tmp_path)
    errors = [RuntimeError("CANARY") for _ in range(10)]
    for before, after in zip(errors, errors[1:], strict=False):  # noqa: RUF007 -- preserve exact synthetic chain setup AST.
        before.__cause__ = after
    if cycle:
        errors[1].__cause__ = errors[0]
    result = _project(namespace, errors[0])
    assert len(result["exception_nodes"]) == (2 if cycle else 8)
    assert result["chain_truncated_or_cycle"] is True
    diagnostic._validate_traceback_projection(result, None)


@pytest.mark.parametrize(
    "damage",
    [
        "private_path",
        "wrong_source_hash",
        "unknown_function",
        "wrong_line",
        "boolean_line",
        "private_field",
        "ninth_node",
        "sixty_fifth_frame",
        "wrong_chain_index",
        "mismatched_chain",
        "false_counter",
    ],
)
def test_strict_traceback_reader_accepts_baseline_then_denies_fabricated_fields(
    monkeypatch, tmp_path, damage
):
    _path, namespace = _source(monkeypatch, tmp_path)
    original = _project(namespace, RuntimeError("CANARY"))
    chain = {"chain": [{}], "truncated_or_cycle": False}
    diagnostic._validate_traceback_projection(original, chain)
    result = copy.deepcopy(original)
    node = result["exception_nodes"][0]
    frame = node["frames"][0]
    if damage == "private_path":
        frame["source"] = "C:/Users/CANARY_PRIVATE/secret.py"
    elif damage == "wrong_source_hash":
        frame["source_sha256"] = "0" * 64
    elif damage == "unknown_function":
        frame["function"] = "caller_selected"
    elif damage == "wrong_line":
        frame["line"] = 1000000
    elif damage == "boolean_line":
        frame["line"] = True
    elif damage == "private_field":
        frame["SQL"] = "CANARY_PRIVATE"
    elif damage == "ninth_node":
        result["exception_nodes"] *= 9
    elif damage == "sixty_fifth_frame":
        node["frames"] *= 65
        node["visited_frames"] = 65
    elif damage == "wrong_chain_index":
        node["chain_index"] = True
    elif damage == "mismatched_chain":
        chain["chain"] *= 2
    else:
        node["visited_frames"] += 1
    with pytest.raises(ValueError):
        diagnostic._validate_traceback_projection(result, chain)


def test_phase_v2_readback_distinguishes_legacy_unknown_from_new_projection(
    monkeypatch, tmp_path
):
    _path, namespace = _source(monkeypatch, tmp_path)
    projection = _project(namespace, RuntimeError("CANARY"))
    row = {
        "phase": "call",
        "start_wall_seconds": 1.0,
        "stop_wall_seconds": 2.0,
        "duration_seconds": 1.0,
        "observed_host_monotonic": 3.0,
        "failure_chain": {"status": "UNAVAILABLE_FIXED_CHAIN_OBSERVATION_FAILED"},
        "deadline_observation": None,
        "deadline_status": "UNKNOWN_NO_MATCHED_FAILURE_FRAME",
        "traceback_projection": projection,
    }
    diagnostic._validate_phase_row(row, wire_version=2)
    with pytest.raises(ValueError):
        diagnostic._validate_phase_row(row)
    legacy = dict(row)
    del legacy["traceback_projection"]
    diagnostic._validate_phase_row(legacy)
    with pytest.raises(ValueError):
        diagnostic._validate_phase_row(legacy, wire_version=2)


def test_unknown_projection_type_is_rejected_without_its_equality_or_text_methods():
    class Private:
        def __eq__(self, _other):
            raise AssertionError("unknown_equality_invoked")

        def __str__(self):
            raise AssertionError("unknown_str_invoked")

        def __repr__(self):
            raise AssertionError("unknown_repr_invoked")

    with pytest.raises(
        ValueError, match="fixed_traceback_readback_envelope_unverified"
    ):
        diagnostic._validate_traceback_projection(Private(), None)


@pytest.mark.parametrize(
    "damage",
    [
        "missing_projection",
        "wrong_version",
        "private_source",
        "source_hash",
        "extra_private_field",
    ],
)
def test_actual_versioned_phase_reader_roundtrip_and_legacy_unknown_are_preserved(
    monkeypatch, tmp_path, damage
):
    _path, namespace = _source(monkeypatch, tmp_path)
    projection = _project(namespace, RuntimeError("CANARY"))
    empty = {
        "phase": "setup",
        "start_wall_seconds": 1.0,
        "stop_wall_seconds": 2.0,
        "duration_seconds": 1.0,
        "observed_host_monotonic": 3.0,
        "failure_chain": None,
        "deadline_observation": None,
        "deadline_status": "UNKNOWN_NO_FAILURE_FRAME_OBSERVED",
        "traceback_projection": None,
    }
    call = dict(
        empty,
        phase="call",
        failure_chain={
            "status": "UNAVAILABLE_FIXED_CHAIN_OBSERVATION_FAILED",
        },
        traceback_projection=projection,
    )
    phases = [empty, call, dict(empty, phase="teardown")]
    record = {
        "schema": "ctcc.native_financial_fixed_node.phases.v2",
        "node": diagnostic.NODES["original-current"],
        "source_sha256": diagnostic.SOURCE_SHA,
        "phases": phases,
        "runtime_functions_or_clocks_modified": False,
        "execution_authority": False,
        "diagnostic_only": True,
    }
    path = tmp_path / "fixed-node-phases.json"
    path.write_text(json.dumps(record))
    result = diagnostic.read_phase_report(
        path, "original-current", reader_profile=diagnostic.HISTORICAL_PHASE_PROFILE
    )
    assert result["phase_wire_schema"].endswith(".v2")
    assert result["phases"][1]["traceback_projection"] == projection
    assert result["unobserved_traceback_frames_are_unknown"] is True
    legacy = copy.deepcopy(record)
    legacy["schema"] = "ctcc.native_financial_fixed_node.phases.v1"
    for row in legacy["phases"]:
        del row["traceback_projection"]
    path.write_text(json.dumps(legacy))
    old = diagnostic.read_phase_report(
        path, "original-current", reader_profile=diagnostic.HISTORICAL_PHASE_PROFILE
    )
    assert "phase_wire_schema" not in old
    assert all("traceback_projection" not in row for row in old["phases"])
    if damage == "missing_projection":
        del record["phases"][1]["traceback_projection"]
    elif damage == "wrong_version":
        record["schema"] = "ctcc.native_financial_fixed_node.phases.v1"
    elif damage == "private_source":
        projection["exception_nodes"][0]["frames"][0]["source"] = (
            "C:/Users/CANARY/secret.py"
        )
    elif damage == "source_hash":
        projection["exception_nodes"][0]["frames"][0]["source_sha256"] = "0" * 64
    else:
        projection["SQL"] = "CANARY_PRIVATE_DETAIL"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        diagnostic.read_phase_report(
            path, "original-current", reader_profile=diagnostic.HISTORICAL_PHASE_PROFILE
        )


@pytest.mark.parametrize("damage", ["old_pin", "source_drift", "missing_member"])
def test_actual_linux_consumer_pin_loop_accepts_current_source_then_denies_drift(
    damage,
):
    """Run the producer's actual pure pin loop, never main/Docker/app code."""
    producer = Path(linux_original.__file__)
    tree = ast.parse(producer.read_bytes())
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    target = "scripts/native_financial_fixed_node_diagnostics.py"
    assignments = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Subscript)
        and isinstance(node.targets[0].value, ast.Name)
        and node.targets[0].value.id == "input_pins"
        and isinstance(node.targets[0].slice, ast.Constant)
        and node.targets[0].slice.value == target
    ]
    assert len(assignments) == 1 and isinstance(assignments[0].value, ast.Constant)
    pin = ast.literal_eval(assignments[0].value)
    actual_sha = hashlib.sha256(Path(diagnostic.__file__).read_bytes()).hexdigest()
    assert pin == actual_sha
    loops = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Call)
        and isinstance(node.iter.func, ast.Attribute)
        and isinstance(node.iter.func.value, ast.Name)
        and node.iter.func.value.id == "input_pins"
        and node.iter.func.attr == "items"
    ]
    assert len(loops) == 1
    loop = loops[0]
    assert isinstance(loop.target, ast.Tuple)
    assert [item.id for item in loop.target.elts] == ["name", "pin"]
    calls = [node for node in ast.walk(loop) if isinstance(node, ast.Call)]
    assert all(
        (isinstance(node.func, ast.Name) and node.func.id == "ValueError")
        or (isinstance(node.func, ast.Attribute) and node.func.attr in {"items", "get"})
        for node in calls
    )
    guard = compile(ast.Module(body=[loop], type_ignores=[]), str(producer), "exec")
    scope = {
        "__builtins__": {"ValueError": ValueError},
        "input_pins": {target: pin},
        "files": {target: {"sha256": actual_sha}},
    }
    # The source-extracted original verification loop, not a mirrored checker.
    exec(guard, scope)  # noqa: S102 -- one reviewed pure dictionary guard only.
    if damage == "old_pin":
        scope["input_pins"][target] = (
            "d9fbc3296869b5975b6b27628ade795dd91a06c7e4f3f54c0ae22a723017f8c4"
        )
    elif damage == "source_drift":
        scope["files"][target]["sha256"] = "0" * 64
    else:
        del scope["files"][target]
    with pytest.raises(ValueError, match="^diagnostic_reviewed_input_changed$"):
        exec(guard, scope)  # noqa: S102 -- actual guard must reject post-baseline damage.
