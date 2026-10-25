"""Prepared source-variant canaries; no native acquisition, DB or trading."""

import ast
import copy
import hashlib
import json
from pathlib import Path
from types import CodeType, FunctionType, SimpleNamespace

import pytest

from scripts import native_financial_fixed_node_diagnostics as diagnostic
from tests.unit.test_native_financial_source_timing import _load_leaf, _validator

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "name",
    (
        "snapshot",
        "_self_pinned",
        "_valid_state",
        "_tick",
        "_label_index",
        "begin",
        "end",
        "source_file",
        "__init__",
    ),
)
def test_timing_baseline_first_same_filename_forged_callable_is_not_blessed(
    name, monkeypatch
):
    leaf = _load_leaf()
    before = diagnostic._timing_loaded_source_verified(leaf)
    assert before[1] is leaf._STATE
    original = leaf._State.__init__ if name == "__init__" else getattr(leaf, name)
    leaf._CANARY_REPORT = leaf.snapshot()
    _validator()(leaf._CANARY_REPORT)
    padding = "\n" * (original.__code__.co_firstlineno - 1)
    body = (
        f"def {name}(_guard=None):\n    return _CANARY_REPORT\n"
        if name == "snapshot"
        else f"def {name}(*args, **kwargs):\n    return _CANARY_REPORT\n"
    )
    compiled = compile(padding + body, original.__code__.co_filename, "exec")
    code = next(value for value in compiled.co_consts if type(value) is CodeType)
    forged = FunctionType(code, vars(leaf), name, original.__defaults__)
    if name == "snapshot":
        # Correct filename/globals and syntactically valid measurements are
        # insufficient: the original R3 bootstrap could capture this callable.
        _validator()(forged())
    monkeypatch.setattr(leaf._State if name == "__init__" else leaf, name, forged)
    with pytest.raises(
        ValueError, match="timing_loaded_callable_not_exact_compiled_source"
    ):
        diagnostic._timing_loaded_source_verified(leaf)


def test_timing_original_source_code_with_foreign_builtin_namespace_is_refused(
    monkeypatch,
):
    leaf = _load_leaf()
    diagnostic._timing_loaded_source_verified(leaf)
    original = leaf.snapshot
    original_builtins = leaf.__dict__["__builtins__"]
    leaf.__dict__["__builtins__"] = dict(original_builtins)
    forged = FunctionType(
        original.__code__, vars(leaf), "snapshot", original.__defaults__
    )
    leaf.__dict__["__builtins__"] = original_builtins
    monkeypatch.setattr(leaf, "snapshot", forged)
    assert (
        forged.__code__ is original.__code__
        and forged.__globals__ is original.__globals__
    )
    with pytest.raises(ValueError, match="timing_loaded_callable_namespace_unverified"):
        diagnostic._timing_loaded_source_verified(leaf)


def test_timing_factory_override_is_rejected_before_the_factory_can_run(monkeypatch):
    leaf = _load_leaf()
    diagnostic._timing_loaded_source_verified(leaf)
    calls = []

    def probe(*args):
        calls.append(True)
        raise AssertionError("untrusted-factory-must-not-run")

    monkeypatch.setattr(leaf._State, "__new__", probe)
    with pytest.raises(ValueError, match="timing_loaded_source_factory_unverified"):
        diagnostic._timing_loaded_source_verified(leaf)
    assert calls == []


@pytest.mark.parametrize("name", ("globals", "dict", "type", "len", "any"))
def test_saved_original_snapshot_refuses_raw_global_shadow_without_invoking_it(
    name, monkeypatch
):
    leaf = _load_leaf()
    diagnostic._timing_loaded_source_verified(leaf)
    saved_snapshot = leaf.snapshot
    calls = []

    def probe(*args):
        calls.append(True)
        return leaf._NS

    monkeypatch.setattr(leaf, name, probe, raising=False)
    assert saved_snapshot() == {
        "schema": "ctcc.native_financial_source_timing.v1",
        "status": "UNAVAILABLE_SOURCE_OR_STATE_CHANGED",
    }
    assert calls == []


@pytest.mark.parametrize("damage", ("guard_alias", "guard_defaults", "guard_code"))
def test_original_hooks_never_call_a_replaced_guard_or_guard_default(
    damage, monkeypatch
):
    leaf = _load_leaf()
    diagnostic._timing_loaded_source_verified(leaf)
    saved_snapshot = leaf.snapshot
    calls = []

    def probe(*args):
        calls.append(True)
        return True

    if damage == "guard_alias":
        monkeypatch.setattr(leaf, "_self_pinned", probe)
    elif damage == "guard_defaults":
        defaults = leaf._self_pinned.__defaults__
        monkeypatch.setattr(
            leaf._self_pinned, "__defaults__", (defaults[0], probe, *defaults[2:])
        )
    else:
        monkeypatch.setattr(leaf, "_CANARY_PROBE", probe, raising=False)
        compiled = compile(
            "def changed_guard(*args):\n    return _CANARY_PROBE(*args)\n",
            leaf._self_pinned.__code__.co_filename,
            "exec",
        )
        code = next(value for value in compiled.co_consts if type(value) is CodeType)
        monkeypatch.setattr(leaf._self_pinned, "__code__", code)
    leaf.begin("v3.register")
    leaf.source_file("inventory.writer", 1)
    leaf.end("v3.register")
    assert saved_snapshot() == {
        "schema": "ctcc.native_financial_source_timing.v1",
        "status": "UNAVAILABLE_SOURCE_OR_STATE_CHANGED",
    }
    assert calls == [] and leaf._STATE.invocations == 0


def test_same_source_function_replacement_after_baseline_cannot_refresh_original_binding(
    monkeypatch,
):
    leaf = _load_leaf()
    before = diagnostic._timing_loaded_source_verified(leaf)
    original = leaf.begin
    clone = FunctionType(original.__code__, vars(leaf), "begin", original.__defaults__)
    monkeypatch.setattr(leaf, "begin", clone)
    after = diagnostic._timing_loaded_source_verified(leaf)
    assert after[3][4][1] is clone
    assert diagnostic._same_timing_loaded_pin(before, after) is False


@pytest.mark.parametrize("name", ("__slots__", "__init__", "__new__"))
def test_recorder_class_metadata_descriptor_is_rejected_without_invoking_it(
    name, monkeypatch
):
    leaf = _load_leaf()
    diagnostic._timing_loaded_source_verified(leaf)
    saved_snapshot = leaf.snapshot
    calls = []

    class Probe:
        def __get__(self, instance, owner):
            calls.append(True)
            raise AssertionError("private-descriptor-must-not-run")

    monkeypatch.setattr(leaf._State, name, Probe(), raising=False)
    assert saved_snapshot() == {
        "schema": "ctcc.native_financial_source_timing.v1",
        "status": "UNAVAILABLE_SOURCE_OR_STATE_CHANGED",
    }
    with pytest.raises(ValueError):
        diagnostic._timing_loaded_source_verified(leaf)
    assert calls == []


def test_non_native_perf_counter_is_rejected_without_sampling_the_probe(monkeypatch):
    leaf = _load_leaf()
    diagnostic._timing_loaded_source_verified(leaf)
    saved_snapshot = leaf.snapshot
    calls = []

    def probe():
        calls.append(True)
        return 1

    monkeypatch.setattr(leaf._time, "perf_counter_ns", probe)
    with pytest.raises(
        ValueError, match="timing_native_perf_counter_builtin_unverified"
    ):
        diagnostic._timing_loaded_source_verified(leaf)
    assert saved_snapshot() == {
        "schema": "ctcc.native_financial_source_timing.v1",
        "status": "UNAVAILABLE_SOURCE_OR_STATE_CHANGED",
    }
    assert calls == []


def _phase_rows():
    return [
        {
            "phase": phase,
            "start_wall_seconds": 1.0,
            "stop_wall_seconds": 2.0,
            "duration_seconds": 1.0,
            "observed_host_monotonic": 3.0,
            "failure_chain": None,
            "deadline_observation": None,
            "deadline_status": "UNKNOWN_NO_FAILURE_FRAME_OBSERVED",
            "traceback_projection": None,
        }
        for phase in ("setup", "call", "teardown")
    ]


def _timing_record():
    return {
        "schema": "ctcc.native_financial_fixed_node.phases.v3",
        "node": diagnostic.NODES["original-current"],
        "source_sha256": diagnostic.SOURCE_SHA,
        "phases": _phase_rows(),
        "source_timing": _load_leaf().snapshot(),
        "source_timing_profile": diagnostic.TIMING_PROFILE,
        "timing_source_pins": diagnostic.TIMING_INPUT_PINS,
        "application_source_instrumented": True,
        "runtime_functions_monkeypatched": False,
        "application_clocks_or_deadlines_modified": False,
        "captured_before_parent_settlement": True,
        "parent_read_requires_owned_settlement": True,
        "execution_authority": False,
        "diagnostic_only": True,
    }


def test_explicit_new_profile_roundtrips_only_v3_and_never_promotes_legacy_wire(
    tmp_path,
):
    path = tmp_path / "fixed-node-phases.json"
    record = _timing_record()
    path.write_text(json.dumps(record))
    result = diagnostic.read_phase_report(
        path, "original-current", reader_profile=diagnostic.TIMING_PROFILE
    )
    assert result["phase_wire_schema"] == "ctcc.native_financial_fixed_node.phases.v3"
    assert result["application_source_instrumented"] is True
    assert result["incomplete_or_unavailable_timing_is_not_zero_or_acceptance"] is True
    with pytest.raises(ValueError):
        diagnostic.read_phase_report(
            path, "original-current", reader_profile=diagnostic.HISTORICAL_PHASE_PROFILE
        )
    historical = {
        "schema": "ctcc.native_financial_fixed_node.phases.v2",
        "node": record["node"],
        "source_sha256": record["source_sha256"],
        "phases": record["phases"],
        "runtime_functions_or_clocks_modified": False,
        "execution_authority": False,
        "diagnostic_only": True,
    }
    path.write_text(json.dumps(historical))
    legacy = diagnostic.read_phase_report(
        path, "original-current", reader_profile=diagnostic.HISTORICAL_PHASE_PROFILE
    )
    assert legacy["historical_wire_readback_only_not_runtime_acceptance"] is True
    assert "source_timing" not in legacy
    with pytest.raises(ValueError):
        diagnostic.read_phase_report(
            path, "original-current", reader_profile=diagnostic.TIMING_PROFILE
        )


@pytest.mark.parametrize("profile", (None, False, "", "caller-passed-success"))
def test_missing_or_unknown_reader_profile_never_falls_back_to_legacy(
    tmp_path, profile
):
    path = tmp_path / "fixed-node-phases.json"
    path.write_text(json.dumps(_timing_record()))
    with pytest.raises(
        ValueError, match="fixed_phase_explicit_reader_profile_required"
    ):
        diagnostic.read_phase_report(path, "original-current", reader_profile=profile)
    with pytest.raises(TypeError):
        diagnostic.read_phase_report(path, "original-current")


@pytest.mark.parametrize(
    "damage",
    ("old_source_hash", "uninstrumented_flag", "clock_modified", "legacy_schema"),
)
def test_source_timing_profile_accepts_baseline_then_denies_forged_claims(
    tmp_path, damage
):
    path = tmp_path / "fixed-node-phases.json"
    baseline = _timing_record()
    path.write_text(json.dumps(baseline))
    diagnostic.read_phase_report(
        path, "original-current", reader_profile=diagnostic.TIMING_PROFILE
    )
    record = copy.deepcopy(baseline)
    if damage == "old_source_hash":
        record["source_sha256"] = (
            "e74f4565d2c93b54fd85c588959493d8384fd8e2ffd6fbb3cfdb4526c31fd317"
        )
    elif damage == "uninstrumented_flag":
        record["application_source_instrumented"] = False
    elif damage == "clock_modified":
        record["application_clocks_or_deadlines_modified"] = True
    else:
        record["schema"] = "ctcc.native_financial_fixed_node.phases.v2"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        diagnostic.read_phase_report(
            path, "original-current", reader_profile=diagnostic.TIMING_PROFILE
        )


@pytest.mark.parametrize(
    "scoped,profile,accepted",
    (
        (False, None, True),
        (True, "source-owned-timing-v1", True),
        (True, None, False),
        (False, "source-owned-timing-v1", False),
        (True, "caller-selected", False),
    ),
)
def test_actual_linux_profile_guard_keeps_historical_default_separate(
    scoped, profile, accepted
):
    tree = ast.parse(
        (ROOT / "scripts/diagnose_native_financial_original_node.py").read_bytes()
    )
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    guard = next(
        node
        for node in main.body
        if isinstance(node, ast.If)
        and any(
            isinstance(part, ast.Constant)
            and part.value == "diagnostic_source_timing_requires_explicit_scoped_opt_in"
            for part in ast.walk(node)
        )
    )
    code = compile(
        ast.Module(body=[guard], type_ignores=[]), "actual-owned-profile-guard", "exec"
    )
    namespace = {
        "__builtins__": {"ValueError": ValueError},
        "scoped": scoped,
        "args": SimpleNamespace(source_timing_profile=profile),
        "TIMING_PROFILE": diagnostic.TIMING_PROFILE,
    }
    if accepted:
        exec(code, namespace)  # noqa: S102 -- exact pure profile guard; no application/main/process code.
    else:
        with pytest.raises(ValueError):
            exec(code, namespace)  # noqa: S102 -- baseline source guard must deny another profile.


def test_timing_workflow_is_distinct_opt_in_and_keeps_existing_native_caps_and_exports():
    old = ROOT / ".github/workflows/native-financial-fixed-node-context-diagnostic.yml"
    assert (
        hashlib.sha256(old.read_bytes()).hexdigest()
        == "c8ffecb7e6617c0624d5ac07b3988af44fa2c85e1e87f204065707a2b5988213"
    )
    text = (
        ROOT / ".github/workflows/native-financial-source-timing-diagnostic.yml"
    ).read_text()
    assert text.count("--source-timing-profile 'source-owned-timing-v1'") == 2
    assert (
        "diagnostic/native-financial-source-timing-20261026" in text
        and "default: disabled" in text
    )
    assert "work/prospective-event-cutover-pg51-catalog-20261025" not in text
    assert text.count("timeout-minutes: 25") == 2 and text.count("max-parallel: 2") == 2
    assert (
        text.count(
            "matrix:\n        node: [original-current, await-temporary, await-retained, late-b3, post-close]"
        )
        == 2
    )
    assert (
        'test "$commit" = "$GITHUB_SHA"' in text
        and "$commit -ne $env:GITHUB_SHA" in text
    )
    assert "persist-credentials: false" in text and "contents: read" in text


def test_new_timing_declarations_pin_source_owned_aliases_without_factory_descriptor_getters():
    tree = ast.parse(
        (
            ROOT / "app/trade_qualification/native_account_risk_assembly_v1.py"
        ).read_bytes()
    )
    declarations = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in ("_V3_DECLARED_BINDINGS", "_V3_DECLARED_DATA")
        ):
            declarations[node.targets[0].id] = node.value
    for value in declarations.values():
        assert isinstance(value, ast.Tuple)
        assert all(
            not (
                isinstance(item, ast.Tuple)
                and isinstance(item.elts[0], ast.Attribute)
                and isinstance(item.elts[0].value, ast.Name)
                and item.elts[0].value.id == "_native_timing"
                and item.elts[0].attr == "_State"
            )
            for item in value.elts
        )
        assert all(
            not (
                isinstance(item, ast.Tuple)
                and isinstance(item.elts[0], ast.Attribute)
                and isinstance(item.elts[0].value, ast.Name)
                and item.elts[0].value.id == "_native_timing"
                and item.elts[0].attr == "_time"
            )
            for item in value.elts
        )
    module_bindings = next(
        item.elts[1]
        for item in declarations["_V3_DECLARED_BINDINGS"].elts
        if isinstance(item, ast.Tuple)
        and isinstance(item.elts[0], ast.Name)
        and item.elts[0].id == "_native_timing"
    )
    assert {"_FACTORY_INIT_PIN", "_CLOCK"} <= set(ast.literal_eval(module_bindings))
    module_data = next(
        item.elts[1]
        for item in declarations["_V3_DECLARED_DATA"].elts
        if isinstance(item, ast.Tuple)
        and isinstance(item.elts[0], ast.Name)
        and item.elts[0].id == "_native_timing"
    )
    assert {"_SLOT_PINS", "_State", "_SELF_CHECK_DEFAULTS"} <= set(
        ast.literal_eval(module_data)
    )
