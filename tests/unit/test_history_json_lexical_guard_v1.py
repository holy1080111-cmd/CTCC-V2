"""Finite JSON-leaf mutation regressions; synthetic IO is not native acceptance."""

import asyncio
import re
import sys
from pathlib import Path
from types import FunctionType, SimpleNamespace

import pytest

from app.database.repositories import public_runtime_bundle_custody as custody
from app.trade_qualification import account_history_source_bundle_v1 as bundle
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import history_json_lexical_guard_v1 as guard
from app.trade_qualification import native_account_risk_assembly_v1 as financial
from tests.unit.test_account_history_source_v1 import acquisition_fixture


def test_original_json_decoder_and_limits_still_apply():
    guard.require()
    assert bundle._json(b'{"a":[1,true,null,"hex"]}') == {"a": [1, True, None, "hex"]}
    for malformed in (b'{"a":1,"a":2}', b'{"a":1.5}', b'{"a":NaN}', b"{} ", b"[" * 33):
        with pytest.raises(ValueError):
            bundle._json(malformed)
    assert bundle.MAX_JSON_DEPTH == 32
    assert bundle.MAX_JSON_NODES == 262144
    assert bundle.MAX_BUNDLE_BYTES == 64 * 1024 * 1024


def test_manifest_change_during_source_readback_is_refused(monkeypatch):
    guard.require()
    manifest = guard._ROOT / "MANIFEST.sha256"
    guarded_source = guard._ROOT / guard.BUNDLE_PATH
    real_read = Path.read_bytes
    real_stat = Path.lstat
    read_guarded_source = False

    def read(path):
        nonlocal read_guarded_source
        raw = real_read(path)
        if path == guarded_source:
            read_guarded_source = True
        return raw

    def lstat(path):
        info = real_stat(path)
        if path == manifest and read_guarded_source:
            fields = {name: getattr(info, name) for name in guard._FILE_IDENTITY_FIELDS}
            fields["st_mtime_ns"] += 1
            return SimpleNamespace(**fields)
        return info

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError, match="history_json_guard_manifest_changed"):
        guard.require()


@pytest.mark.parametrize(
    "defect",
    [
        "same_object_json_code",
        "foreign_json_namespace",
        "guard_code",
        "source_reader_code",
        "guard_defaults",
        "guard_callable",
        "builtin_shadow",
        "pattern_alias",
        "invalid_pattern",
        "finditer_descriptor",
        "match_descriptor",
        "bundle_finditer",
        "bundle_pattern",
        "json_keyword_contents",
        "guard_file",
        "module_name",
        "defining_module",
    ],
)
def test_genuine_baseline_then_changed_leaf_refuses_before_callback(
    monkeypatch, defect
):
    assert bundle._json(b"{}") == {}
    guard.require()
    invoked = []

    def probe(*_args, **_kwargs):
        invoked.append(True)
        return {}

    if defect == "same_object_json_code":
        compiled = compile(
            "def _json(raw, maximum=67108864, **kwargs):\n    return probe(raw)\n",
            bundle._json.__code__.co_filename,
            "exec",
        )
        forged = next(
            value for value in compiled.co_consts if type(value) is type(compiled)
        )
        monkeypatch.setitem(bundle.__dict__, "probe", probe)
        monkeypatch.setattr(bundle._json, "__code__", forged)
    elif defect == "foreign_json_namespace":
        actual = bundle._json
        foreign = FunctionType(
            actual.__code__, dict(vars(bundle)), actual.__name__, actual.__defaults__
        )
        foreign.__kwdefaults__ = actual.__kwdefaults__
        monkeypatch.setattr(bundle, "_json", foreign)
    elif defect == "guard_code":
        compiled = compile(
            "def require(*args, **kwargs):\n    return probe()\n",
            guard.require.__code__.co_filename,
            "exec",
        )
        forged = next(
            value for value in compiled.co_consts if type(value) is type(compiled)
        )
        monkeypatch.setitem(guard.__dict__, "probe", probe)
        monkeypatch.setattr(guard.require, "__code__", forged)
    elif defect == "source_reader_code":
        compiled = compile(
            "def _read_source_bytes(relative):\n    return probe(relative)\n",
            guard._read_source_bytes.__code__.co_filename,
            "exec",
        )
        forged = next(
            value for value in compiled.co_consts if type(value) is type(compiled)
        )
        monkeypatch.setitem(guard.__dict__, "probe", probe)
        monkeypatch.setattr(guard._read_source_bytes, "__code__", forged)
    elif defect == "guard_defaults":
        monkeypatch.setattr(
            guard.require, "__defaults__", (probe, *guard.require.__defaults__[1:])
        )
    elif defect == "guard_callable":
        monkeypatch.setattr(guard, "require", probe)
    elif defect == "builtin_shadow":
        monkeypatch.setitem(guard.__dict__, "type", probe)
    elif defect == "pattern_alias":

        class ForeignPattern:
            @property
            def pattern(self):
                return probe()

        monkeypatch.setattr(guard, "PATTERN", ForeignPattern())
    elif defect == "invalid_pattern":
        monkeypatch.setattr(guard, "PATTERN", re.compile(rb"[a-z]"))
    elif defect == "finditer_descriptor":
        monkeypatch.setattr(guard, "FINDITER", probe)
    elif defect == "match_descriptor":
        monkeypatch.setattr(guard, "MATCH_START", probe)
    elif defect == "bundle_finditer":
        monkeypatch.setattr(bundle, "_JSON_SPECIAL_FINDITER", probe)
    elif defect == "bundle_pattern":
        monkeypatch.setattr(
            bundle, "_JSON_SPECIAL_PATTERN", SimpleNamespace(pattern=probe)
        )
    elif defect == "json_keyword_contents":
        monkeypatch.setitem(bundle._json.__kwdefaults__, "_json_guard", probe)
    elif defect == "guard_file":
        monkeypatch.setattr(guard, "__file__", "untrusted/private/location.py")
    elif defect == "module_name":
        monkeypatch.setattr(guard, "__name__", "foreign.module")
    else:
        monkeypatch.setitem(sys.modules, guard.__name__, SimpleNamespace())
    # The original acquisition checker reaches the source guard before it can
    # touch even this deliberately incomplete state or invoke a changed leaf.
    with pytest.raises(ValueError, match="history_json_guard_"):
        history._check_pins({})
    assert invoked == []


@pytest.mark.asyncio
async def test_preexisting_same_filename_code_denied_before_first_acquisition(
    monkeypatch, tmp_path
):
    with acquisition_fixture(monkeypatch, tmp_path) as value:
        guard.require()
        invoked = []

        def probe(*_args, **_kwargs):
            invoked.append(True)
            return {}

        compiled = compile(
            "def _json(raw, maximum=67108864, **kwargs):\n    return probe(raw)\n",
            bundle._json.__code__.co_filename,
            "exec",
        )
        code = next(part for part in compiled.co_consts if type(part) is type(compiled))
        forged = FunctionType(code, vars(bundle), "_json", bundle._json.__defaults__)
        monkeypatch.setitem(vars(bundle), "probe", probe)
        monkeypatch.setattr(bundle, "_json", forged)
        monkeypatch.setattr(history, "_context", probe)
        before = {name: dict(files) for name, files in value.files.items()}
        with pytest.raises(ValueError, match="history_json_guard_"):
            await history.acquire_native_history_source_facts_v1(**value.args)
        assert invoked == []
        assert value.files == before


@pytest.mark.asyncio
async def test_original_await_boundary_rejects_late_mutation_and_retains_journals(
    monkeypatch, tmp_path
):
    with acquisition_fixture(monkeypatch, tmp_path) as value:
        owner = await history.acquire_native_history_source_facts_v1(**value.args)
        binding = history._take_financial_assembly_v1(owner, key=financial._HOOK_KEY)
        before = {name: dict(files) for name, files in value.files.items()}
        invoked = []

        def probe(*_args, **_kwargs):
            invoked.append(True)
            return iter(())

        async def deployment(_self):
            await asyncio.sleep(0)
            monkeypatch.setattr(bundle, "_JSON_SPECIAL_FINDITER", probe)

        monkeypatch.setattr(
            custody.RuntimeBundleCustodyRepository, "verify_deployment", deployment
        )
        with pytest.raises(ValueError, match="history_json_guard_"):
            await history._replay_financial_assembly_v1(
                binding, key=financial._HOOK_KEY
            )
        assert binding not in history._FINANCIAL
        assert owner not in history._OWNERS
        assert value.files == before
        assert invoked == []


def test_v3_structural_default_projection_has_no_namespace_cycle():
    guard.require()
    assert type(guard.BINDING) is tuple and len(guard.BINDING) == 8
    assert all(type(value) is not dict for value in guard.BINDING)
    projected = financial._v3_callable_pin(bundle._json)
    assert projected[0] is bundle._json
    assert projected[1] is bundle._json.__code__
    assert projected[3:] == financial._v3_callable_pin(bundle._json)[3:]


@pytest.mark.parametrize(
    "entry",
    [
        "_calculate",
        "_calculate_financial_operands_v2",
        "_partition_fences_v3",
        "_latest_native_linkage_v3",
    ],
)
def test_direct_financial_json_consumers_refuse_changed_leaf_before_dispatch(
    monkeypatch, entry
):
    assert bundle._json(b"{}") == {}
    guard.require()
    invoked = []

    def probe(*_args, **_kwargs):
        invoked.append(True)
        return {}

    monkeypatch.setattr(bundle, "_json", probe)
    arguments = (
        (None, None, None)
        if entry in ("_calculate", "_calculate_financial_operands_v2")
        else (None, None)
    )
    with pytest.raises(ValueError, match="history_json_guard_"):
        getattr(financial, entry)(*arguments)
    assert invoked == []
