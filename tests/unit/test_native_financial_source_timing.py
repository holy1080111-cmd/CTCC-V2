"""HELD canaries for diagnostic metrology only; no native acquisition or order."""

import ast
import asyncio
import hashlib
import importlib.util
import stat
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LEAF = "app/trade_qualification/native_financial_timing_v1.py"
HELPER = "scripts/native_financial_fixed_node_diagnostics.py"
ASM = "app/trade_qualification/native_account_risk_assembly_v1.py"


def _load_leaf():
    spec = importlib.util.spec_from_file_location(
        "ctcc_timing_canary_owned_leaf", ROOT / LEAF
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.snapshot()["status"] == "CLOSED_SPANS_OBSERVED"
    assert all(
        row["starts"] == row["closed"] == 0 for row in module.snapshot()["spans"]
    )
    return module


def _validator():
    # Execute only this reviewed pure diagnostic validator in test scope,
    # without importing the native service, pytest plugin or any application.
    tree = ast.parse((ROOT / HELPER).read_bytes())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_validate_timing_snapshot"
    )
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "TIMING_LABELS"
            for target in node.targets
        )
    )
    namespace = {
        "TIMING_PROFILE": "source-owned-timing-v1",
        "TIMING_LABELS": ast.literal_eval(assignment.value),
    }
    exec(  # noqa: S102 -- exact source function, synthetic diagnostic data only.
        compile(
            ast.Module(body=[function], type_ignores=[]),
            "owned-timing-validator",
            "exec",
        ),
        namespace,
    )
    return namespace["_validate_timing_snapshot"]


def test_timing_nested_source_reads_have_fixed_counts_without_CPU_claim():
    leaf = _load_leaf()
    leaf.begin("v3.register")
    for label, size in (
        ("inventory.v3", 11),
        ("inventory.financial", 7),
        ("inventory.writer", 3),
    ):
        leaf.begin(label)
        leaf.source_file(label, size)
    for label in (
        "inventory.writer",
        "inventory.financial",
        "inventory.v3",
        "v3.register",
    ):
        leaf.end(label)
    report = leaf.snapshot()
    _validator()(report)
    assert report["status"] == "CLOSED_SPANS_OBSERVED"
    assert report["cpu_time_measured"] is report["execution_authority"] is False
    rows = {row["label"]: row for row in report["spans"]}
    assert [
        (rows[label]["public_source_reads"], rows[label]["public_source_bytes"])
        for label in ("inventory.v3", "inventory.financial", "inventory.writer")
    ] == [(1, 11), (1, 7), (1, 3)]
    assert all(
        rows[label]["starts"] == rows[label]["closed"] == 1
        for label in (
            "v3.register",
            "inventory.v3",
            "inventory.financial",
            "inventory.writer",
        )
    )
    assert all(row["closed_inclusive_elapsed_ns"] >= 0 for row in report["spans"])


@pytest.mark.parametrize(
    "field,value",
    (
        ("SCHEMA", "private-schema-token"),
        ("PROFILE", "private-profile-token"),
        ("ENABLED", False),
        ("MAX_DEPTH", True),
        ("_CLOCK", lambda: 0),
        ("_FACTORY", lambda: None),
        ("_NS", {}),
        ("_NS_GET", lambda *_: None),
        ("_self_pinned", lambda: True),
        ("_FUNCTION_PINS", (object(),)),
    ),
)
def test_timing_baseline_first_global_mutation_exports_only_literal_unavailable(
    monkeypatch, field, value
):
    leaf = _load_leaf()
    leaf.begin("v3.calculate")
    leaf.end("v3.calculate")
    _validator()(leaf.snapshot())
    monkeypatch.setattr(leaf, field, value)
    assert leaf.snapshot() == {
        "schema": "ctcc.native_financial_source_timing.v1",
        "status": "UNAVAILABLE_SOURCE_OR_STATE_CHANGED",
    }


@pytest.mark.parametrize("field", ("stats", "stack", "__getattribute__", "__setattr__"))
def test_timing_baseline_first_slot_or_class_descriptor_change_never_invokes_private_methods(
    monkeypatch, field
):
    leaf = _load_leaf()
    called = []

    def trap(*args):
        called.append(True)
        raise AssertionError("private-descriptor-must-not-run")

    if field in ("stats", "stack"):
        monkeypatch.setattr(leaf._State, field, property(trap))
    else:
        monkeypatch.setattr(leaf._State, field, trap)
    assert leaf.snapshot()["status"] == "UNAVAILABLE_SOURCE_OR_STATE_CHANGED"
    assert called == []


@pytest.mark.parametrize(
    "field,value",
    (
        ("stats", [object()]),
        ("stack", [object()]),
        ("invalid", object()),
        ("invocations", True),
        ("last_tick", -1),
        ("public_bytes", float("nan")),
    ),
)
def test_timing_baseline_first_private_buffer_corruption_is_not_serialized(
    monkeypatch, field, value
):
    leaf = _load_leaf()
    monkeypatch.setattr(leaf._STATE, field, value)
    assert leaf.snapshot() == {
        "schema": "ctcc.native_financial_source_timing.v1",
        "status": "UNAVAILABLE_SOURCE_OR_STATE_CHANGED",
    }


def test_timing_unknown_private_label_never_calls_str_repr_or_eq():
    leaf = _load_leaf()

    class Private:
        def __str__(self):
            raise AssertionError("no-stringification")

        def __repr__(self):
            raise AssertionError("no-repr")

        def __eq__(self, other):
            raise AssertionError("no-comparison")

    leaf.begin(Private())
    report = leaf.snapshot()
    _validator()(report)
    assert (
        report["status"] == "INCOMPLETE" and report["invalid_code"] == "LABEL_INVALID"
    )
    assert report["open_span_labels"] == ()


def test_timing_depth_exhaustion_does_not_create_extra_closed_span():
    leaf = _load_leaf()
    for _ in range(leaf.MAX_DEPTH):
        leaf.begin("v3.register")
    leaf.begin("v3.register")
    leaf.end("v3.register")
    report = leaf.snapshot()
    _validator()(report)
    assert report["invalid_code"] == "BOUND_EXCEEDED"
    assert len(report["open_span_labels"]) == 16
    assert report["spans"][0]["starts"] == 16 and report["spans"][0]["closed"] == 0


def test_timing_original_exception_identity_and_cancelled_open_span_are_preserved():
    leaf = _load_leaf()
    original = RuntimeError("not-exported-private-original-error")
    with pytest.raises(RuntimeError) as observed:
        leaf.begin("v3.calculate")
        raise original
    assert observed.value is original
    report = leaf.snapshot()
    _validator()(report)
    assert report["status"] == "INCOMPLETE" and report["open_span_labels"] == (
        "v3.calculate",
    )
    assert "not-exported" not in str(report)

    cancelled_leaf = _load_leaf()

    async def operation():
        cancelled_leaf.begin("v3.final_fence")
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(operation())
    assert cancelled_leaf.snapshot()["open_span_labels"] == ("v3.final_fence",)
    assert cancelled_leaf.snapshot()["spans"][4]["closed"] == 0


@pytest.mark.parametrize(
    "tamper",
    (
        "profile",
        "private-label",
        "bool-count",
        "closed-open-conflict",
        "excess-reads",
        "extra-private-key",
    ),
)
def test_timing_portable_validator_refuses_baseline_first_contradictory_record(tamper):
    leaf = _load_leaf()
    leaf.begin("v3.replay")
    leaf.end("v3.replay")
    report = leaf.snapshot()
    validate = _validator()
    validate(report)
    if tamper == "profile":
        report["profile"] = "not-source-owned"
    elif tamper == "private-label":
        report["spans"][0]["label"] = "private-header-token"
    elif tamper == "bool-count":
        report["spans"][0]["starts"] = True
    elif tamper == "closed-open-conflict":
        report["open_span_labels"] = ("v3.replay",)
    elif tamper == "excess-reads":
        report["spans"][7]["public_source_reads"] = (1 << 24) + 1
    else:
        report["private"] = "private-value"
    with pytest.raises(ValueError):
        validate(report)


def test_timing_historical_consumer_remains_old_while_scoped_three_source_pins_match_bytes():
    producer = ast.parse(
        (ROOT / "scripts/diagnose_native_financial_original_node.py").read_bytes()
    )
    original = next(
        node.value
        for node in producer.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "INPUT_PINS"
            for target in node.targets
        )
    )
    literal = {
        key.value: value.value
        for key, value in zip(original.keys, original.values)
        if isinstance(key, ast.Constant)
    }
    assert (
        literal[ASM]
        == "e74f4565d2c93b54fd85c588959493d8384fd8e2ffd6fbb3cfdb4526c31fd317"
    )
    helper = ast.parse((ROOT / HELPER).read_bytes())
    pins = ast.literal_eval(
        next(
            node.value
            for node in helper.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "TIMING_INPUT_PINS"
                for target in node.targets
            )
        )
    )
    assert set(pins) == {
        ASM,
        LEAF,
        "app/trade_qualification/controlled_writer_coverage_v1.py",
    }
    assert pins[ASM] != literal[ASM]
    assert all(
        hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
        for name, digest in pins.items()
    )


def test_timing_public_source_hash_drift_after_first_success_is_refused(tmp_path):
    tree = ast.parse((ROOT / HELPER).read_bytes())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_timing_source_bytes_verified"
    )
    source = tmp_path / "app/owned_public.py"
    source.parent.mkdir()
    source.write_bytes(b"public_original = 1\n")
    namespace = {
        "__file__": str(tmp_path / "scripts/owned_helper.py"),
        "TIMING_INPUT_PINS": {
            "app/owned_public.py": hashlib.sha256(source.read_bytes()).hexdigest()
        },
        "Path": Path,
        "stat": stat,
        "hashlib": hashlib,
    }
    exec(  # noqa: S102 -- exact pure diagnostic source reader, owned temporary public file.
        compile(
            ast.Module(body=[function], type_ignores=[]),
            "owned-timing-source-reader",
            "exec",
        ),
        namespace,
    )
    verify = namespace["_timing_source_bytes_verified"]
    verify()
    source.write_bytes(b"public_modified = 2\n")
    with pytest.raises(ValueError, match="timing_public_source_identity_changed"):
        verify()


@pytest.mark.parametrize("function_name", ("begin", "_self_pinned", "__init__"))
def test_timing_baseline_first_actual_function_code_change_is_unavailable(
    monkeypatch, function_name
):
    leaf = _load_leaf()
    function = (
        leaf._State.__init__
        if function_name == "__init__"
        else getattr(leaf, function_name)
    )
    monkeypatch.setattr(function, "__code__", (lambda: True).__code__)
    assert leaf.snapshot() == {
        "schema": "ctcc.native_financial_source_timing.v1",
        "status": "UNAVAILABLE_SOURCE_OR_STATE_CHANGED",
    }


def test_timing_function_pin_data_has_no_recursive_namespace_mapping():
    leaf = _load_leaf()
    assert all(
        type(item[3]) is int and item[3] == id(leaf.__dict__)
        for item in leaf._FUNCTION_PINS
    )
    pending = [leaf._FUNCTION_PINS, leaf._BUILTIN_PINS, leaf._SLOT_PINS]
    while pending:
        value = pending.pop()
        assert type(value) is not dict
        if type(value) in (tuple, list):
            pending.extend(value)


def test_timing_v3_rows_keep_all_original_guards_and_caps_instead_of_post_cleanup_app_callback():
    linux = ast.parse(
        (ROOT / "scripts/diagnose_native_financial_original_node.py").read_bytes()
    )
    windows = ast.parse(
        (ROOT / "scripts/diagnose_native_financial_fixed_node_windows.py").read_bytes()
    )
    linux_functions = {
        node.name: node for node in linux.body if isinstance(node, ast.FunctionDef)
    }
    windows_main = next(
        node
        for node in windows.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    assert "_original_then_verified_read" in {
        node.func.id
        for node in ast.walk(linux_functions["main"])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "run_owned_node" in {
        node.func.id
        for node in ast.walk(windows_main)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not any(
        isinstance(node, (ast.AsyncFunctionDef, ast.Await))
        for node in ast.walk(windows_main)
    )
    for tree, name, expected in (
        (linux, "CONTAINER_DEADLINE_SECONDS", 300),
        (linux, "CLEANUP_SECONDS", 60),
        (windows, "NODE_SECONDS", 300),
        (windows, "CLEANUP_SECONDS", 60),
    ):
        assert (
            ast.literal_eval(
                next(
                    node.value
                    for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == name
                        for target in node.targets
                    )
                )
            )
            == expected
        )
