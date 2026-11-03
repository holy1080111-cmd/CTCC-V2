"""Source-owned same-lease namespace history acquisition; never trade authority.

A genuine fresh namespace phase is MOVEd and its old current authority released
at CALL TIME. A distinct measured V6 archival stage then uses that SAME active
native GET lease. It preserves original current expiry, Control prefix and all
UID tombstones as historical custody, not as renewed current risk or G6 proof.
"""

from __future__ import annotations

import ast
import asyncio
import builtins as _source_builtins
import os
import stat
import sys
import uuid
from builtins import id as _state_identity
from collections.abc import Coroutine
from pathlib import Path, PosixPath, WindowsPath
from threading import get_ident
from types import CodeType, CoroutineType, FunctionType
from weakref import WeakKeyDictionary, ref

from app.database.repositories import public_runtime_bundle_custody as custody
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.source_primitives import canonical, sha, utc_from_ns, validate_stamps
from app.exchange.okx.private_rest import (
    DemoPinnedReadSession,
    OkxDemoPrivateRestClient,
)
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_collector as collector
from app.trade_qualification import account_current_source_v4 as account
from app.trade_qualification import account_history_query_verifier as history_queries
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import controlled_writer_coverage_v1 as coverage
from app.trade_qualification import demo_operational_route_v4 as operational
from app.trade_qualification import history_json_lexical_guard_v1 as _json_lexical_guard
from app.trade_qualification import native_namespace_history_bundle_v1 as bundle
from app.trade_qualification import native_namespace_phase_v1 as phase
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.demo_market_source_v4 import _route_proof
from app.trade_qualification.demo_public_origin import ReviewedDemoPublicRoute
from app.trade_qualification.reservations import LedgerScope

_HOOK_KEY = object()
_OWNERS = WeakKeyDictionary()
_MOVED_BINDINGS = WeakKeyDictionary()
MAX_OWNERS = 128
MAX_RAW_ROOT_BYTES = custody.MAX_BUNDLE_BYTES + 2 * 1024 * 1024
MAX_CLOCK_ROOT_BYTES = len(proof.FILES) * storage.MAX_PART
_ROUTE_FILES = frozenset(
    {
        "account-route-attempt.v4.plan.json",
        "account-route-attempt.v4.json",
        "account-route-attempt.v4.raw",
        "operational-route-proof.namespace-v1.json",
    }
)


_SOURCE_MODULE_NAMESPACE = globals()


class NativeNamespaceHistorySourceError(ValueError):
    """Fixed redacted codes only."""


def _deny(code="native_namespace_history_source_unavailable"):
    raise NativeNamespaceHistorySourceError(code) from None


class _MovedNamespaceHistoryBindingV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny("namespace_history_moved_binding_not_constructible")


def _register_moved_namespace_history_v1(moved, *, key):
    if key is not phase._HOOK_KEY or len(_MOVED_BINDINGS) >= MAX_OWNERS:
        _deny("namespace_history_original_phase_move_required")
    binding = object.__new__(_MovedNamespaceHistoryBindingV1)
    _MOVED_BINDINGS[binding] = {
        "moved": moved,
        "state_ref": None,
        "acquisition_started": False,
    }
    return binding


class _NamespaceHistoryStateV1(dict):
    """Private weakrefable state; identity is never an allocator integer."""

    __slots__ = ("__weakref__",)

    def __new__(cls, *_args, **_kwargs):
        _deny("namespace_history_state_not_constructible")

    def __copy__(self):
        _deny("namespace_history_state_not_transferable")

    def __deepcopy__(self, memo):
        _deny("namespace_history_state_not_transferable")

    def __reduce_ex__(self, protocol):
        _deny("namespace_history_state_not_transferable")


_SOURCE_CLEANUP_REFERENCES = (
    isinstance,
    dict,
    type,
    _MovedNamespaceHistoryBindingV1,
    CoroutineType,
    BaseException,
)


def _release_dead_state(binding_ref, state_ref):
    # This callback owns only a weak binding reference. In particular, neither
    # its closure nor the registry value retains the original state or binding.
    binding = binding_ref()
    record = (
        _MOVED_BINDINGS.get(binding)
        if _SOURCE_PIN_CHECKER_DEFAULTS[2][2](binding)
        is _SOURCE_PIN_CHECKER_DEFAULTS[2][3]
        else None
    )
    if record is not None and record["state_ref"] is state_ref:
        _MOVED_BINDINGS.pop(binding, None)


def _bind_moved_state(state, *, key):
    if type(state) is not _NamespaceHistoryStateV1:
        _deny("namespace_history_moved_state_already_bound_or_unavailable")
    binding = state.get("binding")
    record = (
        _MOVED_BINDINGS.get(binding)
        if type(binding) is _MovedNamespaceHistoryBindingV1
        else None
    )
    if (
        key is not _HOOK_KEY
        or record is None
        or record["moved"] is not state.get("moved")
        or record["state_ref"] is not None
    ):
        _deny("namespace_history_moved_state_already_bound_or_unavailable")
    binding_ref = ref(binding)

    def release_original_state(dead_ref, release=_release_dead_state):
        release(binding_ref, dead_ref)

    # The registry owns a weak reference, never state -> binding by a strong
    # cycle. A still-retained binding is removed when its exact state dies.
    record["state_ref"] = ref(state, release_original_state)


def _begin_acquisition(state):
    _check(state)
    record = _MOVED_BINDINGS[state["binding"]]
    if record["acquisition_started"] is not False:
        _deny("namespace_history_acquisition_attempt_already_spent")
    record["acquisition_started"] = True


def _release_moved(state):
    if not _SOURCE_PIN_CHECKER_DEFAULTS[2][0](
        state, _SOURCE_PIN_CHECKER_DEFAULTS[2][1]
    ):
        return
    binding = _SOURCE_PIN_CHECKER_DEFAULTS[2][1].get(state, "binding")
    record = (
        _MOVED_BINDINGS.get(binding)
        if _SOURCE_PIN_CHECKER_DEFAULTS[2][2](binding)
        is _SOURCE_PIN_CHECKER_DEFAULTS[2][3]
        else None
    )
    # A copied/rebound DTO must not revoke another in-flight or accepted owner.
    if (
        record is not None
        and record["state_ref"] is not None
        and record["state_ref"]() is state
    ):
        _MOVED_BINDINGS.pop(binding, None)


class OwnedNativeNamespaceHistoryV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny("namespace_history_owner_not_constructible")

    def __copy__(self):
        _deny("namespace_history_owner_not_transferable")

    def __deepcopy__(self, memo):
        _deny("namespace_history_owner_not_transferable")

    def __reduce_ex__(self, protocol):
        _deny("namespace_history_owner_not_transferable")

    def close(self):
        state = _OWNERS.pop(self, None)
        if state is not None:
            _release_moved(state)
            state.clear()


def _assert_source_callable(
    function,
    expected_code,
    defaults,
    kwdefaults,
    expected_globals,
    expected_builtins,
    builtin_lookups,
    _type=type,
    _len=len,
    _zip=zip,
    _error=NativeNamespaceHistorySourceError,
):
    """Inspect finite original source contracts without executing inspected leaves."""
    if (
        _type is not _SOURCE_VERIFIER_DEFAULTS[0]
        or _len is not _SOURCE_VERIFIER_DEFAULTS[1]
        or _zip is not _SOURCE_VERIFIER_DEFAULTS[2]
        or _error is not _SOURCE_VERIFIER_DEFAULTS[3]
    ):
        raise _SOURCE_VERIFIER_DEFAULTS[3](
            "namespace_history_original_function_or_policy_changed:source_callable_contract_changed"
        ) from None
    # Captured builtin aliases are guarded before this function is invoked.
    # Do not call a module-global type/getattr/len callback to verify itself.
    failed = (
        _type(function) is not FunctionType
        or function.__code__ != expected_code
        or function.__code__.co_filename != expected_code.co_filename
        or function.__code__.co_qualname != expected_code.co_qualname
        or function.__closure__ is not None
        or expected_code.co_freevars != ()
        or function.__globals__ is not expected_globals
        or function.__builtins__ is not expected_builtins
    )
    if failed:
        raise _error(
            "namespace_history_original_function_or_policy_changed:source_callable_contract_changed"
        ) from None
    # Only names actually read by the compiled leaf and its nested code are
    # considered. A same-code foreign namespace or a builtin shadow is never a
    # new valid baseline. Reading an ordinary dict value invokes no callback.
    for name, original in builtin_lookups:
        if (
            expected_globals.get(name, original) is not original
            or expected_builtins.get(name) is not original
        ):
            raise _error(
                "namespace_history_original_function_or_policy_changed:source_callable_contract_changed"
            ) from None
    actual = function.__defaults__
    if defaults is None:
        failed = actual is not None
    elif _type(actual) is not _type(()) or _len(actual) != _len(defaults):
        failed = True
    else:
        for left, right in _zip(actual, defaults, strict=True):
            if _type(right) in (
                _type(None),
                _type(True),
                _type(0),
                _type(""),
                _type(b""),
            ):
                failed = failed or _type(left) is not _type(right) or left != right
            elif _type(right) is _type(()):
                # This closed source tuple contains only original builtin/type
                # references. Never iterate an inspected foreign object.
                if _type(left) is not _type(()) or _len(left) != _len(right):
                    failed = True
                else:
                    for actual_ref, expected_ref in _zip(left, right, strict=True):
                        failed = failed or actual_ref is not expected_ref
            else:
                # Captured builtin/error-class aliases are exact objects.
                failed = failed or left is not right
    actual_kw = function.__kwdefaults__
    if kwdefaults is None:
        failed = failed or actual_kw is not None
    elif _type(actual_kw) is not _type({}) or _len(actual_kw) != _len(kwdefaults):
        failed = True
    else:
        for name, value in kwdefaults.items():
            failed = failed or name not in actual_kw
            if name in actual_kw:
                failed = (
                    failed
                    or _type(actual_kw[name]) is not _type(value)
                    or actual_kw[name] != value
                )
    if failed:
        raise _error(
            "namespace_history_original_function_or_policy_changed:source_callable_contract_changed"
        ) from None


def _compiled_builtin_lookups(code):
    names = set(code.co_names)
    for value in code.co_consts:
        if type(value) is CodeType:
            names.update(name for name, _value in _compiled_builtin_lookups(value))
    return tuple(
        (name, _source_builtins.__dict__[name])
        for name in sorted(names)
        if name in _source_builtins.__dict__ and not name.startswith("_")
    )


def _source_default_literal(node, assignments, seen=()):
    if isinstance(node, ast.Name) and node.id == "_json_lexical_guard":
        return sys.modules["app.trade_qualification.history_json_lexical_guard_v1"]
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and (node.value.id == "_json_lexical_guard")
        and (node.attr in ("require", "BINDING"))
    ):
        module = sys.modules["app.trade_qualification.history_json_lexical_guard_v1"]
        return module.BINDING[0] if node.attr == "require" else module.BINDING
    if isinstance(node, ast.Constant) and type(node.value) in (
        type(None),
        bool,
        int,
        str,
        bytes,
    ):
        return node.value
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and (node.func.id == "globals")
        and (not node.args)
        and (not node.keywords)
    ):
        return sys.modules[__name__].__dict__
    if isinstance(node, ast.Tuple):
        return tuple(
            _source_default_literal(value, assignments, seen) for value in node.elts
        )
    if isinstance(node, ast.Name) and node.id in (
        "type",
        "len",
        "zip",
        "getattr",
        "isinstance",
        "dict",
        "BaseException",
    ):
        return _source_builtins.__dict__[node.id]
    if isinstance(node, ast.Name) and node.id == "_MovedNamespaceHistoryBindingV1":
        return _MovedNamespaceHistoryBindingV1
    if isinstance(node, ast.Name) and node.id == "CoroutineType":
        return CoroutineType
    if isinstance(node, ast.Name) and node.id == "NativeNamespaceHistorySourceError":
        return NativeNamespaceHistorySourceError
    if isinstance(node, ast.Name) and node.id not in seen and (node.id in assignments):
        return _source_default_literal(
            assignments[node.id], assignments, (*seen, node.id)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        left = _source_default_literal(node.left, assignments, seen)
        right = _source_default_literal(node.right, assignments, seen)
        if (
            type(left) is type(right) is int
            and 0 <= left <= 2**24
            and (0 <= right <= 2**24)
        ):
            return left * right
    raise NativeNamespaceHistorySourceError(
        "namespace_history_source_default_not_in_closed_profile"
    ) from None


def _compile_source_callable_profiles():
    """Expected executable code comes from approved source, never observed code."""
    root = Path(__file__).resolve(strict=True).parents[2]
    manifest_path = root / "MANIFEST.sha256"
    info = manifest_path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or manifest_path.is_symlink()
        or getattr(info, "st_file_attributes", 0) & 1024
        or info.st_size > 2 * 1024 * 1024
    ):
        raise NativeNamespaceHistorySourceError(
            "namespace_history_source_manifest_invalid"
        )
    manifest = manifest_path.read_bytes().splitlines()
    current = sys.modules[__name__]
    specifications = (
        (
            current,
            "_secret_checked_carried_event",
            "app/trade_qualification/native_namespace_history_source_v1.py",
        ),
        (
            current,
            "_secret_checked_carried_sources",
            "app/trade_qualification/native_namespace_history_source_v1.py",
        ),
        (
            current,
            "_replay_original_route_attempt",
            "app/trade_qualification/native_namespace_history_source_v1.py",
        ),
        (
            current,
            "_pins",
            "app/trade_qualification/native_namespace_history_source_v1.py",
        ),
        (
            current,
            "_check",
            "app/trade_qualification/native_namespace_history_source_v1.py",
        ),
        (
            current,
            "_await",
            "app/trade_qualification/native_namespace_history_source_v1.py",
        ),
        (
            current,
            "_acquire",
            "app/trade_qualification/native_namespace_history_source_v1.py",
        ),
        (
            current,
            "_consume",
            "app/trade_qualification/native_namespace_history_source_v1.py",
        ),
        (
            current,
            "acquire_owned_native_namespace_history_v1",
            "app/trade_qualification/native_namespace_history_source_v1.py",
        ),
        (
            current,
            "consume_owned_native_namespace_history_v1",
            "app/trade_qualification/native_namespace_history_source_v1.py",
        ),
        (
            current,
            "_assert_source_callable",
            "app/trade_qualification/native_namespace_history_source_v1.py",
        ),
        (
            runtime,
            "_secret_checked",
            "app/trade_qualification/account_native_runtime.py",
        ),
        (collector, "_no_secrets", "app/trade_qualification/account_collector.py"),
        (collector, "_no_secret_json", "app/trade_qualification/account_collector.py"),
        (
            journal,
            "checked_event",
            "app/trade_qualification/account_capture_journal.py",
        ),
        (
            journal,
            "_check_owned_packet_for_storage",
            "app/trade_qualification/account_capture_journal.py",
        ),
        (
            account,
            "replay_current_account_bundle_v4",
            "app/trade_qualification/account_current_source_v4.py",
        ),
        (account, "decode", "app/domain/source_primitives.py"),
        (account, "_unhex", "app/trade_qualification/account_current_source_v4.py"),
    )
    sources, profiles = {}, []
    for owner, name, relative in specifications:
        if relative not in sources:
            path = root
            for part in relative.split("/"):
                path /= part
                observed_info = path.lstat()
                if (
                    path.is_symlink()
                    or getattr(observed_info, "st_file_attributes", 0) & 1024
                ):
                    raise NativeNamespaceHistorySourceError(
                        "namespace_history_source_path_changed"
                    )
            raw = path.read_bytes()
            lines = [row for row in manifest if row.endswith(b"  " + relative.encode())]
            if (
                len(lines) != 1
                or lines[0] != sha(raw).encode() + b"  " + relative.encode()
                or not stat.S_ISREG(observed_info.st_mode)
                or observed_info.st_nlink != 1
                or len(raw) != observed_info.st_size
                or len(raw) > 2 * 1024 * 1024
            ):
                raise NativeNamespaceHistorySourceError(
                    "namespace_history_source_manifest_mismatch"
                )
            parsed = ast.parse(raw, filename=str(path))
            compiled = compile(raw, str(path), "exec", dont_inherit=True, optimize=0)
            functions = {
                value.co_qualname: value
                for value in compiled.co_consts
                if type(value) is CodeType
            }
            assignments = {
                node.targets[0].id: node.value
                for node in parsed.body
                if isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            }
            sources[relative] = (parsed, functions, assignments)
        parsed, functions, assignments = sources[relative]
        nodes = [
            node
            for node in parsed.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ]
        if len(nodes) != 1 or name not in functions or nodes[0].decorator_list:
            raise NativeNamespaceHistorySourceError(
                "namespace_history_source_leaf_not_in_closed_profile"
            )
        node = nodes[0]
        defaults = (
            tuple(
                _source_default_literal(value, assignments)
                for value in node.args.defaults
            )
            or None
        )
        keywords = {
            argument.arg: _source_default_literal(value, assignments)
            for argument, value in zip(
                node.args.kwonlyargs, node.args.kw_defaults, strict=True
            )
            if value is not None
        } or None
        expected = functions[name]
        namespace = sys.modules[relative[:-3].replace("/", ".")].__dict__
        builtin_namespace = _source_builtins.__dict__
        lookups = _compiled_builtin_lookups(expected)
        _assert_source_callable(
            getattr(owner, name),
            expected,
            defaults,
            keywords,
            namespace,
            builtin_namespace,
            lookups,
        )
        profiles.append(
            (
                owner,
                name,
                expected,
                defaults,
                keywords,
                namespace,
                builtin_namespace,
                lookups,
            )
        )
    return tuple(profiles)


def _pins(
    _getattr=getattr,
    _namespace=_SOURCE_MODULE_NAMESPACE,
    _cleanup=_SOURCE_CLEANUP_REFERENCES,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
    if (
        _getattr is not _SOURCE_PIN_CHECKER_DEFAULTS[0]
        or _namespace is not _SOURCE_PIN_CHECKER_DEFAULTS[1]
        or _cleanup is not _SOURCE_PIN_CHECKER_DEFAULTS[2]
        or (_SOURCE_CLEANUP_REFERENCES is not _cleanup)
    ):
        raise NativeNamespaceHistorySourceError(
            "namespace_history_original_function_or_policy_changed:source_callable_contract_changed"
        ) from None
    if _namespace.get("getattr", _getattr) is not _getattr:
        raise NativeNamespaceHistorySourceError(
            "namespace_history_original_function_or_policy_changed:source_callable_contract_changed"
        ) from None
    if (
        _assert_source_callable is not _SOURCE_VERIFIER_FUNCTION
        or _assert_source_callable.__code__ != _SOURCE_VERIFIER_CODE
        or _assert_source_callable.__code__.co_filename
        != _SOURCE_VERIFIER_CODE.co_filename
        or (_assert_source_callable.__defaults__ is not _SOURCE_VERIFIER_DEFAULTS)
        or (_assert_source_callable.__kwdefaults__ is not None)
        or (_assert_source_callable.__closure__ is not None)
    ):
        raise NativeNamespaceHistorySourceError(
            "namespace_history_original_function_or_policy_changed:source_callable_contract_changed"
        ) from None
    for (
        owner,
        name,
        code,
        defaults,
        keywords,
        namespace,
        builtin_namespace,
        lookups,
    ) in _SOURCE_CALLABLE_PROFILES:
        _assert_source_callable(
            _getattr(owner, name),
            code,
            defaults,
            keywords,
            namespace,
            builtin_namespace,
            lookups,
        )
    if (
        any(
            (
                _getattr(_getattr(module, name), "__func__", _getattr(module, name))
                is not function
                for module, name, function in _PINS
            )
        )
        or sha(canonical(bundle.POLICY)) != bundle.POLICY_SHA256
    ):
        _deny("namespace_history_original_function_or_policy_changed")


def _context():
    task = asyncio.current_task()
    if task is None or task.cancelling():
        _deny("namespace_history_original_context_required")
    return os.getpid(), get_ident(), asyncio.get_running_loop(), task


def _check(state):
    if (
        _pins is not _SOURCE_PIN_CHECKER_FUNCTION
        or _pins.__code__ != _SOURCE_PIN_CHECKER_CODE
        or _pins.__code__.co_filename != _SOURCE_PIN_CHECKER_CODE.co_filename
        or _pins.__defaults__ is not _SOURCE_PIN_CHECKER_DEFAULTS
        or _pins.__kwdefaults__ is not None
        or _pins.__closure__ is not None
    ):
        raise NativeNamespaceHistorySourceError(
            "namespace_history_original_function_or_policy_changed:source_callable_contract_changed"
        ) from None
    _pins()
    if type(state) is not _NamespaceHistoryStateV1:
        _deny("namespace_history_original_moved_binding_required")
    binding = state.get("binding")
    record = (
        _MOVED_BINDINGS.get(binding)
        if type(binding) is _MovedNamespaceHistoryBindingV1
        else None
    )
    if (
        record is None
        or record["moved"] is not state.get("moved")
        or type(record["state_ref"]) is not ref
        or record["state_ref"]() is not state
    ):
        _deny("namespace_history_original_moved_binding_required")
    actual, expected = _context(), state["context"]
    if actual[:3] != expected[:3] or actual[3] is not expected[3]():
        _deny("namespace_history_context_changed")
    moved = state["moved"]
    phase._historical_context(moved["historical"])
    lease, client = state["lease"], state["private_client"]
    if (
        type(lease) is not DemoPinnedReadSession
        or type(client) is not OkxDemoPrivateRestClient
        or lease._owner is not client
        or client._active_read_session() is not lease
    ):
        _deny("namespace_history_same_active_native_lease_required")
    lease.verify_current()
    pins = coverage._factory_pins(moved["factory"], moved["custody"])
    expected_pins = state["factory_pins"]
    if (
        any(pins[n] is not expected_pins[n] for n in (0, 1, 3, 4, 5))
        or pins[2] != expected_pins[2]
        or pins[6] != expected_pins[6]
    ):
        _deny("namespace_history_original_database_factory_changed")
    # Integrity-only read of old files. _check_current and old Control lease
    # replay are deliberately NOT used as a later current permission.
    for root, names, maximum in (
        ("private_root", coverage._PRIVATE_NAMES, coverage._MAX_SOURCE_FILE),
        ("native_root", (*proof.FILES, "proof.json", "readback.json"), proof.MAX_PROOF),
    ):
        if (
            coverage._source_files(moved[root], names, maximum)
            != moved["source_files"][root]
        ):
            _deny("namespace_history_original_phase_files_changed")
    if "session" in state:
        session, plan = state["session"], state["plan"]
        if (
            session._used is not state.get("session_claimed", False)
            or session._plan is not plan
            or capture.plan_sha256(plan) != state["plan_sha256"]
            or session._pin != state["plan_sha256"]
            or session._checked_read_session(plan) is not lease
            or native._checked_credential_pin(session) != state["credential_pin"]
        ):
            _deny("namespace_history_new_phase_session_changed")
    now = native.clock.native_stamp()
    validate_stamps((state["original"], state["last"], now))
    state["last"] = now
    return now


async def _await(state, awaitable):
    try:
        if (
            _pins is not _SOURCE_PIN_CHECKER_FUNCTION
            or _pins.__code__ != _SOURCE_PIN_CHECKER_CODE
            or _pins.__code__.co_filename != _SOURCE_PIN_CHECKER_CODE.co_filename
            or _pins.__defaults__ is not _SOURCE_PIN_CHECKER_DEFAULTS
            or _pins.__kwdefaults__ is not None
            or _pins.__closure__ is not None
        ):
            raise NativeNamespaceHistorySourceError(
                "namespace_history_original_function_or_policy_changed:source_callable_contract_changed"
            ) from None
        _pins()
        if (
            _SOURCE_PIN_CHECKER_DEFAULTS[2][2](awaitable)
            is not _SOURCE_PIN_CHECKER_DEFAULTS[2][4]
        ):
            _deny("namespace_history_fixed_native_coroutine_required")
        now = _check(state)
        loop = asyncio.get_running_loop()
        remaining = (
            bundle.POLICY["native_anchor_ns"]
            - now["monotonic_ns"]
            + state["original"]["monotonic_ns"]
        ) / 1_000_000_000
        deadline = min(state["loop_deadline"], loop.time() + remaining)
        if remaining <= 0 or loop.time() >= deadline:
            _deny("namespace_history_original_deadline_expired")
    except _SOURCE_PIN_CHECKER_DEFAULTS[2][5]:
        if (
            _SOURCE_PIN_CHECKER_DEFAULTS[2][2](awaitable)
            is _SOURCE_PIN_CHECKER_DEFAULTS[2][4]
        ):
            awaitable.close()
        raise
    limit = asyncio.timeout_at(deadline)
    try:
        async with limit:
            result = await awaitable
    except TimeoutError:
        _deny("namespace_history_original_deadline_expired")
    # A lower IO coroutine which suppresses timeout cancellation still cannot
    # return an accepted result after this original invocation's one deadline.
    if limit.expired() or loop.time() >= deadline:
        _deny("namespace_history_original_deadline_expired")
    if (
        _pins is not _SOURCE_PIN_CHECKER_FUNCTION
        or _pins.__code__ != _SOURCE_PIN_CHECKER_CODE
        or _pins.__code__.co_filename != _SOURCE_PIN_CHECKER_CODE.co_filename
        or _pins.__defaults__ is not _SOURCE_PIN_CHECKER_DEFAULTS
        or _pins.__kwdefaults__ is not None
        or _pins.__closure__ is not None
    ):
        raise NativeNamespaceHistorySourceError(
            "namespace_history_original_function_or_policy_changed:source_callable_contract_changed"
        ) from None
    _pins()
    _check(state)
    return result


def _plan(state):
    moved, stamp = state["moved"], state["original"]
    original_record = phase.records.decode(moved["historical"]["record"].record_json)
    start = original_record["coverage_start"]["utc_ns"]
    cutoff = stamp["utc_ns"] // 1_000_000 * 1_000_000
    if stamp["utc_ns"] - start > bundle.MAX_LOOKBACK_NS or not start < cutoff:
        _deny("namespace_history_original_start_outside_closed_profile")
    old = moved["current_account"]["plan"]
    if (
        type(old) is not capture.CurrentDemoAccountCapturePlanV7
        or old.expected_uid != original_record["account_id"]
        or old.environment != "demo"
        or old.settlement_currency != "USDT"
    ):
        _deny("namespace_history_original_account_scope_changed")
    # A new plan, not rewritten metadata on an old capture/session. Floor the
    # inclusive LOWER request bound; keep exact original nanosecond start above.
    selected = capture.AllProductDemoAccountCapturePlanV6(
        contract_version="ctcc.demo_account_plan.v6",
        plan_id="native-namespace-" + uuid.uuid4().hex,
        created_at=utc_from_ns(stamp["utc_ns"]),
        expected_uid=old.expected_uid,
        expected_main_uid=old.expected_main_uid,
        session_binding_id=state["lease"].binding_id,
        settlement_currency=old.settlement_currency,
        leverage_instrument_ids=old.leverage_instrument_ids,
        history_start=utc_from_ns(start // 1_000_000 * 1_000_000),
        history_end=utc_from_ns(cutoff),
        max_batch_seconds=bundle.COLLECTOR_BATCH_SECONDS,
        max_response_bytes=capture.MAX_RESPONSE_BYTES,
        max_total_bytes=journal.TOTAL_BYTES,
        registration_region=old.registration_region,
        origin=old.origin,
        registration_evidence_sha256=old.registration_evidence_sha256,
    )
    if (
        selected.max_batch_seconds
        != capture.AllProductDemoAccountCapturePlanV6.model_fields[
            "max_batch_seconds"
        ].default
    ):
        _deny("namespace_history_existing_collector_policy_changed")
    return selected


def _publish(directory, name, raw):
    if type(raw) is not bytes or not 0 < len(raw) <= custody.MAX_BUNDLE_BYTES:
        _deny("namespace_history_file_bound")
    directory.publish(name, raw)
    if directory.read(name, len(raw)) != raw:
        _deny("namespace_history_file_readback_changed")


def _inventory(state, directory, expected, maximum):
    _check(state)
    if (
        set(directory.names()) != set(expected)
        or sum(size for size, _ in expected.values()) > maximum
    ):
        _deny("namespace_history_file_inventory_changed")
    for name, (size, digest) in expected.items():
        _check(state)
        raw = directory.read(name, size)
        if (
            type(size) is not int
            or not 0 < size <= custody.MAX_BUNDLE_BYTES
            or len(raw) != size
            or sha(raw) != digest
        ):
            _deny("namespace_history_file_inventory_changed")
    if set(directory.names()) != set(expected):
        _deny("namespace_history_file_inventory_changed")


async def _historical_custody(state):
    moved = state["moved"]
    original = moved["historical"]["phase_source"]
    reads = [
        (
            original["custody"],
            original["source_custody_id"],
            original["source_bundle_sha256"],
            original["source_proof_envelope_sha256"],
            original["bundle_raw"],
        ),
    ]
    for hop in moved["hops"]:
        source = hop["active"].state_document
        reads.append(
            (
                moved["custody"],
                source["source_custody_id"],
                source["source_bundle_sha256"],
                source["source_proof_envelope_sha256"],
                None,
            )
        )
    bundles, total = [], 0
    for repository, identifier, digest, envelope, expected_raw in reads:
        retained = await _await(
            state,
            custody.RuntimeBundleCustodyRepository.read_retained_bundle(
                repository,
                bundle_id=identifier,
                expected_sha256=digest,
                proof_envelope_sha256=envelope,
            ),
        )
        if (
            type(retained) is not custody.RetainedRuntimeBundleReadback
            or retained.bundle_id != identifier
            or retained.bundle_sha256 != digest
            or retained.proof_envelope_sha256 != envelope
            or expected_raw is not None
            and retained.bundle_raw != expected_raw
        ):
            _deny("namespace_history_historical_custody_changed")
        total += len(retained.bundle_raw)
        # Bound before retaining/hexing another full source. This intentionally
        # refuses oversized lineages; no old phase or source is dropped.
        if 2 * total + phase.MAX_CHAIN_BYTES + 1024 * 1024 > custody.MAX_BUNDLE_BYTES:
            _deny("namespace_history_historical_sources_bound")
        bundles.append(retained.bundle_raw)
    if bundles[-1] != moved["current_account"]["bundle_raw"]:
        _deny("namespace_history_historical_custody_changed")
    state["hop_source_bundles"] = tuple(bundles[1:])


def _secret_checked_carried_event(event, *, tokens):
    # Full original source replay precedes this operation. Only the explicitly
    # owned capture-start plan label is permitted; bytes on disk never change.
    document = journal.checked_event(event)
    runtime._secret_checked(event.event_json, (*tokens[:3], *tokens[4:]))
    if document["kind"] == "capture_start":
        plan = document["data"]["plan"]
        if plan["session_binding_id"] != tokens[3]:
            _deny("namespace_history_carried_label_binding_changed")
        # Scanner view only, exactly matching the existing owned packet rule.
        # A missing label is a rejection above, never a supplied default.
        plan["session_binding_id"] = ""
    runtime._secret_checked(canonical(document), (tokens[3],))


def _secret_checked_carried_sources(state, tokens):
    """Check only already replayed V4 schema components before carrier hex."""
    _check(state)
    if (
        type(tokens) is not tuple
        or len(tokens) < 4
        or tokens[:3] != state["lease"]._credentials
        or tokens[3] != state["lease"].binding_id
        or any(
            type(token) is not str or not token or not token.isascii()
            for token in tokens
        )
    ):
        _deny("namespace_history_carried_terminal_tokens_required")
    moved = state["moved"]
    original = moved["historical"]["phase_source"]
    sources = state["hop_source_bundles"]
    if (
        type(sources) is not tuple
        or not 1 <= len(sources) <= phase.MAX_HOPS
        or len(sources) != len(moved["hops"])
    ):
        _deny("namespace_history_carried_source_inventory_changed")
    original_sources = (
        (original["bundle_raw"], original["source_bundle_sha256"]),
        *(
            (raw, hop["active"].state_document["source_bundle_sha256"])
            for raw, hop in zip(sources, moved["hops"], strict=True)
        ),
    )
    try:
        for raw, digest in original_sources:
            _check(state)
            # This validates the entire CLOSED V4 envelope, original V7/B1
            # packet, exact source hashes and clock/readback semantics before
            # any schema-declared hex component is decoded for secret scanning.
            recorded = account.replay_current_account_bundle_v4(
                raw, expected_sha256=digest
            )
            if recorded.packet.plan.session_binding_id != tokens[3]:
                _deny("namespace_history_carried_label_binding_changed")
            document = account.decode(raw, custody.MAX_BUNDLE_BYTES)
            runtime._secret_checked(
                account._unhex(document["route_proof"]["config_raw_hex"], 8192), tokens
            )
            chain = account._chain_replay(document["chain"])
            runtime._secret_checked(raw, tokens)
            for point in chain:
                _secret_checked_carried_event(point.event, tokens=tokens)
                if point.event.raw_body is not None:
                    runtime._secret_checked(point.event.raw_body, tokens)
                if point.event.packet_payload is not None:
                    journal._check_owned_packet_for_storage(
                        point.event.packet_payload, tokens, recorded.packet.plan
                    )
            # These are the only declared non-chain hex fields of V4. There is
            # no generic recursion, source substitution, skipped malformed part
            # or decoding of an arbitrary caller-selected JSON string.
            runtime._secret_checked(
                account._unhex(document["native_proof_hex"], proof.MAX_PROOF), tokens
            )
            runtime._secret_checked(
                account._unhex(document["native_readback_hex"], storage.MAX_PART),
                tokens,
            )
            for name in proof.FILES:
                runtime._secret_checked(
                    account._unhex(document["clock_files"][name], storage.MAX_PART),
                    tokens,
                )
            _check(state)
    except collector.AccountCollectionError:
        _deny("namespace_history_carried_source_secret_echo")


def _wire_bound(state, chain, clock_files):
    moved = state["moved"]
    phase_bytes = (
        sum(map(len, state["hop_source_bundles"]))
        + len(moved["historical"]["phase_source"]["bundle_raw"])
        + len(moved["historical"]["record"].record_json)
    )
    phase_bytes += sum(
        len(hop["active"].records_json)
        + len(hop["scan_json"])
        + sum(map(len, hop["control_rows"]))
        + 128 * len(hop["scan_keys"])
        for hop in moved["hops"]
    )
    chain_bytes = sum(
        len(point.event.event_json)
        + len(point.event.raw_body or b"")
        + len(point.event.packet_payload or b"")
        for point in chain
    )
    upper = (
        2 * (phase_bytes + chain_bytes + sum(map(len, clock_files.values())))
        + 8192 * len(chain)
        + 1024 * 1024
    )
    if upper > custody.MAX_BUNDLE_BYTES:
        _deny("namespace_history_bundle_bound_before_hex")


def _replay_original_route_attempt(
    raw, *, attempt_plan_raw, route_state, account_plan_sha256
):
    """Replay exact V4 producer facts; this returns no permission or new source."""
    try:
        document = proof._canonical_document(raw, 32768)
        attempt_plan = proof._canonical_document(attempt_plan_raw, 32768)
        if type(route_state) is not dict:
            _deny("namespace_history_route_original_attempt_changed")
        original = route_state["raw"]
        route = route_state["route"]
        if (
            type(route) is not ReviewedDemoPublicRoute
            or route.environment != "demo"
            or route.admission != "DENY"
            or route.source_authenticity_verified is not False
            or route.execution_authority is not False
            or route.rest_origin != route_state["origin"]
            or type(account_plan_sha256) is not str
            or len(account_plan_sha256) != 64
            or any(char not in "0123456789abcdef" for char in account_plan_sha256)
            or route_state["plan_sha256"] != account_plan_sha256
            or attempt_plan_raw
            != canonical(
                {
                    "schema_version": "ctcc.private_demo_route_attempt_plan.v4",
                    "account_plan_sha256": account_plan_sha256,
                    "environment": "demo",
                    "route_region": route.registration_region,
                    "origin": route.rest_origin,
                    "method": "GET",
                    "endpoint": "/api/v5/account/config",
                    "max_response_bytes": 8192,
                    "retries": 0,
                    "private_account_evidence": True,
                    "execution_authority": False,
                }
            )
            or type(attempt_plan) is not dict
        ):
            _deny("namespace_history_route_original_attempt_changed")
        if (
            type(route_state) is not dict
            or type(original) is not bytes
            or not 0 < len(original) <= 8192
            or type(document) is not dict
            or set(document)
            != {
                "schema_version",
                "plan_sha256",
                "disposition",
                "code",
                "status",
                "safe_response_headers",
                "omitted_header_count",
                "request_start",
                "headers_received",
                "body_completed",
                "body_stream_exhausted",
                "tls_peer_sha256",
                "observed_bytes",
                "observed_sha256",
                "raw_name",
                "retained_bytes",
                "raw_retained",
                "retained_sha256",
                "truncated",
                "raw_omitted_for_credential_safety",
                "source_authenticity_verified",
                "account_complete",
                "execution_authority",
            }
            or document["schema_version"] != "ctcc.private_demo_route_attempt.v4"
            or document["plan_sha256"] != sha(attempt_plan_raw)
            or document["disposition"] != "accepted"
            or document["code"] != "accepted"
            or type(document["status"]) is not int
            or document["status"] != 200
            or document["body_stream_exhausted"] is not True
            or document["raw_retained"] is not True
            or document["raw_name"] != "account-route-attempt.v4.raw"
            or document["truncated"] is not False
            or document["raw_omitted_for_credential_safety"] is not False
            or type(document["observed_bytes"]) is not int
            or type(document["retained_bytes"]) is not int
            or document["observed_bytes"] != len(original)
            or document["retained_bytes"] != len(original)
            or document["observed_sha256"] != sha(original)
            or document["retained_sha256"] != sha(original)
            or document["tls_peer_sha256"] != route_state["peer_sha256"]
            or type(document["omitted_header_count"]) is not int
            or document["omitted_header_count"] < 0
            or type(document["safe_response_headers"]) is not list
            or any(
                type(row) is not list
                or len(row) != 2
                or any(type(value) is not str for value in row)
                for row in document["safe_response_headers"]
            )
            or any(
                document[field] is not False
                for field in (
                    "source_authenticity_verified",
                    "account_complete",
                    "execution_authority",
                )
            )
        ):
            _deny("namespace_history_route_original_attempt_changed")
        for field in ("request_start", "headers_received", "body_completed"):
            if (
                document[field] != route_state[field]
                or route_state[field]["utc_ns"] != route_state[field + "_ns"]
            ):
                _deny("namespace_history_route_original_attempt_changed")
        validate_stamps(
            (
                route_state["request_start"],
                route_state["headers_received"],
                route_state["body_completed"],
                route_state["validated"],
            )
        )
        return document
    except NativeNamespaceHistorySourceError:
        raise
    except Exception:  # noqa: BLE001 -- malformed source has one redacted code.
        _deny("namespace_history_route_original_attempt_changed")


async def _acquire(state):
    success = False
    began_here = False
    raw_dir = None
    try:
        if (
            _pins is not _SOURCE_PIN_CHECKER_FUNCTION
            or _pins.__code__ != _SOURCE_PIN_CHECKER_CODE
            or _pins.__code__.co_filename != _SOURCE_PIN_CHECKER_CODE.co_filename
            or _pins.__defaults__ is not _SOURCE_PIN_CHECKER_DEFAULTS
            or _pins.__kwdefaults__ is not None
            or _pins.__closure__ is not None
        ):
            raise NativeNamespaceHistorySourceError(
                "namespace_history_original_function_or_policy_changed:source_callable_contract_changed"
            ) from None
        _pins()
        _begin_acquisition(state)
        began_here = True
        moved, lease = state["moved"], state["lease"]
        selected = state["prepared_plan"]
        pin = capture.plan_sha256(selected)
        session = ControlledDemoAccountSession.from_pinned_read_session(
            read_session=lease, plan=selected, expected_plan_sha256=pin
        )
        # Constructor copies the plan. Preserve that exact owned plan object.
        state.update(
            session=session,
            plan=session._plan,
            plan_sha256=pin,
            credential_pin=native._checked_credential_pin(session),
        )
        if session._checked_read_session(session._plan) is not lease:
            _deny("namespace_history_constructor_changed_native_lease")
        scope = LedgerScope(
            account_id=selected.expected_uid, settlement_currency="USDT"
        )
        with (
            storage._companion_attempt(state["raw_root"]) as raw_dir,
            storage._companion_attempt(state["native_root"]) as native_dir,
        ):
            if storage._root_identity(raw_dir.path) == storage._root_identity(
                native_dir.path
            ):
                _deny("namespace_history_distinct_new_roots_required")
            claim = canonical(
                {
                    "schema_version": "ctcc.native_namespace_history_attempt.v1",
                    "attempt_id": uuid.uuid4().hex,
                    "policy_sha256": bundle.POLICY_SHA256,
                    "plan_sha256": pin,
                    "started": state["original"],
                    "execution_authority": False,
                }
            )
            _publish(raw_dir, "attempt.claim.json", claim)
            produced_raw = {"attempt.claim.json": claim}
            await _historical_custody(state)
            await _await(
                state,
                custody.RuntimeBundleCustodyRepository.verify_deployment(
                    moved["custody"]
                ),
            )
            observation = await _await(
                state,
                operational._probe_initial_demo_operational_route_retained_v4(
                    session, state["private_client"], proof_directory=raw_dir
                ),
            )
            route_state = operational._consume_initial_route_state_v4(
                observation, session, state["private_client"]
            )
            route = _route_proof(route_state)
            route_raw = canonical(route)
            _publish(raw_dir, "operational-route-proof.namespace-v1.json", route_raw)
            produced_raw["operational-route-proof.namespace-v1.json"] = route_raw
            for name in _ROUTE_FILES - {"operational-route-proof.namespace-v1.json"}:
                produced_raw[name] = raw_dir.read(name, 262144)
            if produced_raw["account-route-attempt.v4.raw"] != route_state["raw"]:
                _deny("namespace_history_route_original_raw_changed")
            _replay_original_route_attempt(
                produced_raw["account-route-attempt.v4.json"],
                attempt_plan_raw=produced_raw["account-route-attempt.v4.plan.json"],
                route_state=route_state,
                account_plan_sha256=pin,
            )
            # Recreate only the exact already-issued config signature, in memory,
            # so a later body cannot retain that private header value either.
            _, route_signature = collector._signed_request(
                operational._ConfigSpec(selected.origin),
                session._credentials,
                utc_from_ns(route_state["request_start_ns"]),
                min(selected.max_request_seconds, 5),
            )
            if set(raw_dir.names()) != {"attempt.claim.json", *_ROUTE_FILES}:
                _deny("namespace_history_route_file_inventory")
            with (
                native._claim_initial_session(session),
                native._initial_stage(
                    plan_sha256=pin,
                    scope_sha256=proof.scope_sha256(scope),
                    _claimed_session=session,
                ) as stage,
            ):
                state["session_claimed"] = True
                native_state = native._state(stage)
                clock = native_state["clock"]
                clock_files = {
                    "host-before.json": native._host_observation(stage, "before")
                }
                tokens = collector._credential_values(session._credentials)[:3]
                runtime._secret_checked(clock_files["host-before.json"], tokens)
                storage._publish_part(
                    native_dir, "host-before.json", clock_files["host-before.json"]
                )
                if native_state["clock_results"] != {"before": "accepted"}:
                    _deny("namespace_history_native_host_clock_rejected")
                time_plan = proof._exchange_plan(native_state["started"])
                clock_files["exchange-plan.json"] = time_plan.canonical_bytes()
                storage._publish_part(
                    native_dir, "exchange-plan.json", clock_files["exchange-plan.json"]
                )
                for when in ("before",):
                    parts = await _await(
                        state,
                        runtime._time_probe(
                            stage,
                            time_plan,
                            directory=native_dir,
                            phase=when,
                            tokens=tokens,
                        ),
                    )
                    for name, raw in zip(
                        (
                            f"exchange-{when}.json",
                            f"exchange-{when}.raw",
                            f"exchange-{when}-trace.json",
                        ),
                        parts,
                        strict=True,
                    ):
                        clock_files[name] = raw
                ledger = QualificationLedgerRepository(moved["factory"], clock=clock)
                journal_repository = AccountCaptureJournalRepository(
                    moved["factory"], clock=clock
                )
                recorded, owner = await _await(
                    state,
                    bootstrap._collect_recorded(
                        session,
                        repository=ledger,
                        journal_repository=journal_repository,
                        clock=clock,
                        barrier_completed_at=utc_from_ns(
                            native_state["started"]["utc_ns"]
                        ),
                        _native_observer=native_state["observer"],
                    ),
                )
                if (
                    type(owner) is not journal._OwnedAccountJournal
                    or owner is not native_state.get("journal")
                    or owner.repository is not journal_repository
                    or not all(
                        (
                            owner.finished,
                            owner.acquisition_ok,
                            owner.closed_safe,
                            owner.finalization_complete,
                            native_state["closed"],
                        )
                    )
                    or native_state["session_claim"]["bootstrap_used"] is not True
                    or recorded.bootstrap.transport_provenance
                    != "owned_signed_verified_tls"
                ):
                    _deny("namespace_history_original_native_B1_required")
                chain = await _await(
                    state, journal_repository.read_chain(scope, owner.capture_id)
                )
                reference, packet, _, _ = proof._source(
                    chain, scope, proof_schema=proof.ALGO_V6_SCHEMA
                )
                if (
                    len(chain) != owner.sequence
                    or reference.head_sha256 != owner.previous
                    or reference.packet_sha256 != owner.owned_packet_sha256
                    or packet.plan != session._plan
                ):
                    _deny("namespace_history_original_B1_readback_changed")
                full_tokens = (*owner.tokens, route_signature)
                clock_files["host-after.json"] = native._host_observation(
                    stage, "after"
                )
                runtime._secret_checked(clock_files["host-after.json"], full_tokens)
                storage._publish_part(
                    native_dir, "host-after.json", clock_files["host-after.json"]
                )
                if native_state["clock_results"].get("after") != "accepted":
                    _deny("namespace_history_native_host_clock_rejected")
                parts = await _await(
                    state,
                    runtime._time_probe(
                        stage,
                        time_plan,
                        directory=native_dir,
                        phase="after",
                        tokens=full_tokens,
                    ),
                )
                for name, raw in zip(
                    (
                        "exchange-after.json",
                        "exchange-after.raw",
                        "exchange-after-trace.json",
                    ),
                    parts,
                    strict=True,
                ):
                    clock_files[name] = raw
                await _await(
                    state,
                    runtime._recheck_original_db_chain(
                        journal_repository,
                        scope,
                        chain,
                        reference,
                        packet,
                        proof_schema=proof.ALGO_V6_SCHEMA,
                    ),
                )
                # Scan original plaintext before any JSON/hex envelope can hide it.
                for point in chain:
                    if point.event.raw_body is not None:
                        runtime._secret_checked(point.event.raw_body, full_tokens)
                runtime._secret_checked(route_state["raw"], full_tokens)
                for raw in clock_files.values():
                    runtime._secret_checked(raw, full_tokens)
                for name in _ROUTE_FILES:
                    runtime._secret_checked(produced_raw[name], full_tokens)
                temporal = {
                    "schema_version": bundle.TEMPORAL_SCHEMA,
                    "policy_sha256": bundle.POLICY_SHA256,
                    "stage": {
                        "kind": "native_namespace_history_only",
                        "invocation_id": native_state["invocation_id"],
                        "started": native_state["started"],
                    },
                    "original_started": state["original"],
                    "witnesses": native_state["witnesses"],
                    "finalization_witnesses": native_state["finalization_witnesses"],
                    "clock_files_hex": {
                        name: raw.hex() for name, raw in clock_files.items()
                    },
                    "source_readback_complete": _check(state),
                    "proof_persist_start": _check(state),
                }
                _wire_bound(state, chain, clock_files)
                original_record = phase.records.decode(
                    moved["historical"]["record"].record_json
                )
                query = history_queries.verify_algo_history_query_chain_v6(
                    chain, **observed._pins(reference, scope)
                )
                # The inherited upper bound has been checked above. Close
                # the NEW terminal secret set over every carried plaintext
                # source before phase_document creates nested carrier hex.
                _secret_checked_carried_sources(state, full_tokens)
                value = {
                    "schema_version": bundle.SCHEMA,
                    "policy_sha256": bundle.POLICY_SHA256,
                    "phase": bundle.phase_document(
                        moved, source_bundles=state["hop_source_bundles"]
                    ),
                    "route_proof": route,
                    "chain": account._chain_document(chain),
                    "temporal": temporal,
                    "source_reference": observed.reference_document(reference),
                    "query_inventory": bundle.namespace_queries(packet),
                    "query_receipt_hex": query.receipt_json.hex(),
                    "cutoff_ns": state["original"]["utc_ns"] // 1_000_000 * 1_000_000,
                    "requested_start_ms": str(
                        original_record["coverage_start"]["utc_ns"] // 1_000_000
                    ),
                    "requested_end_ms": capture._milliseconds(selected.history_end),
                    "namespace_finality": "UNKNOWN",
                    **{name: False for name in bundle.FALSE_FIELDS},
                }
                from app.trade_qualification.account_history_source_bundle_v1 import (
                    _encoded_size,
                )

                _encoded_size(value)
                raw = journal.canonical(value)
                metadata_tokens = (*full_tokens[:3], *full_tokens[4:])
                runtime._secret_checked(raw, metadata_tokens)
                facts = bundle.replay_native_namespace_history_bundle_v1(
                    raw, expected_sha256=sha(raw)
                )
                proof_pin = sha(journal.canonical(temporal))
                receipt = await _await(
                    state,
                    custody.RuntimeBundleCustodyRepository.seal_bundle(
                        moved["custody"],
                        bundle_raw=raw,
                        expected_sha256=sha(raw),
                        proof_envelope_sha256=proof_pin,
                    ),
                )
                retained = custody._consume_runtime_bundle_custody_receipt(
                    receipt,
                    expected_bundle_sha256=sha(raw),
                    expected_proof_envelope_sha256=proof_pin,
                )
                if retained.bundle_raw != raw:
                    _deny("namespace_history_native_custody_readback_changed")
                await _await(
                    state,
                    runtime._recheck_original_db_chain(
                        journal_repository,
                        scope,
                        chain,
                        reference,
                        packet,
                        proof_schema=proof.ALGO_V6_SCHEMA,
                    ),
                )
                _publish(raw_dir, "source.bundle.json", raw)
                produced_raw["source.bundle.json"] = raw
                accepted = canonical(
                    {
                        "schema_version": "ctcc.native_namespace_history_source_readback.v1",
                        "bundle_sha256": sha(raw),
                        "proof_sha256": proof_pin,
                        "custody_bundle_id": retained.bundle_id,
                        "custody_recorded_at": retained.recorded_at.isoformat(),
                        "custody_readback_at": retained.database_readback_at.isoformat(),
                        "namespace_finality": "UNKNOWN",
                        **{name: False for name in bundle.FALSE_FIELDS},
                    }
                )
                _publish(raw_dir, "accepted.json", accepted)
                produced_raw["accepted.json"] = accepted
                raw_inventory = {
                    name: (len(body), sha(body)) for name, body in produced_raw.items()
                }
                clock_inventory = {
                    name: (len(body), sha(body)) for name, body in clock_files.items()
                }
                _inventory(state, raw_dir, raw_inventory, MAX_RAW_ROOT_BYTES)
                _inventory(state, native_dir, clock_inventory, MAX_CLOCK_ROOT_BYTES)
                state.update(
                    facts=facts,
                    scope=scope,
                    chain=chain,
                    reference=reference,
                    journal_repository=journal_repository,
                    custody_id=retained.bundle_id,
                    proof_pin=proof_pin,
                    raw_inventory=raw_inventory,
                    clock_inventory=clock_inventory,
                    raw_root_identity=storage._root_identity(raw_dir.path),
                    native_root_identity=storage._root_identity(native_dir.path),
                )
        # Every original native/session/storage context has closed successfully.
        # A late context-exit error retains evidence but issues no private owner.
        _check(state)
        result = object.__new__(OwnedNativeNamespaceHistoryV1)
        _OWNERS[result] = state
        state["transferred"] = True
        success = True
        return result
    except asyncio.CancelledError:
        raise
    except NativeNamespaceHistorySourceError:
        raise
    except Exception:  # noqa: BLE001 -- retain fixed private-source rejection only.
        _deny()
    finally:
        # Identity-only cleanup also retires a genuine, still-unspent first
        # send which failed a pin/context/clock/lease fence. It grants no
        # permission and must not revoke an earlier successful/spent owner.
        binding = (
            _SOURCE_PIN_CHECKER_DEFAULTS[2][1].get(state, "binding")
            if _SOURCE_PIN_CHECKER_DEFAULTS[2][0](
                state, _SOURCE_PIN_CHECKER_DEFAULTS[2][1]
            )
            else None
        )
        record = (
            _MOVED_BINDINGS.get(binding)
            if _SOURCE_PIN_CHECKER_DEFAULTS[2][2](binding)
            is _SOURCE_PIN_CHECKER_DEFAULTS[2][3]
            else None
        )
        original_unspent = (
            record is not None
            and record["state_ref"] is not None
            and record["state_ref"]() is state
            and record["acquisition_started"] is False
        )
        if not success and (began_here or original_unspent):
            try:
                if raw_dir is not None:
                    # Accepted B1/state journals and partial files survive late
                    # failure. Never erase, retry or promote an earlier outcome.
                    try:
                        with storage._root_context(state["raw_root"]) as directory:
                            _publish(
                                directory,
                                "failed.json",
                                canonical(
                                    {
                                        "schema_version": "ctcc.native_namespace_history_attempt_failed.v1",
                                        "outcome": "INCOMPLETE",
                                        "execution_authority": False,
                                    }
                                ),
                            )
                    except Exception:  # noqa: BLE001 -- fixed private rejection only.
                        if not asyncio.current_task().cancelling():
                            _deny("namespace_history_failure_retention_incomplete")
            finally:
                # Direct first-send failure cleans up without an _Attempt or GC.
                # Already-spent retries do not acquire this responsibility.
                _release_moved(state)
                state.clear()


class _Attempt(Coroutine):
    __slots__ = ("_closed", "_inner", "_state")

    def __init__(self, inner, state):
        self._inner, self._state, self._closed = inner, state, False

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            return self._inner.send(value)
        except _SOURCE_PIN_CHECKER_DEFAULTS[2][5]:
            self.close()
            raise

    def throw(self, typ, val=None, tb=None):
        try:
            return self._inner.throw(typ, val, tb)
        except _SOURCE_PIN_CHECKER_DEFAULTS[2][5]:
            self.close()
            raise

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._inner.close()
            finally:
                state, self._state = self._state, None
                if not state.get("transferred"):
                    _release_moved(state)
                    state.clear()

    def __del__(self):
        try:
            self.close()
        except _SOURCE_PIN_CHECKER_DEFAULTS[2][5]:
            pass


def acquire_owned_native_namespace_history_v1(
    phase_owner,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
    *,
    raw_root,
    native_root,
):
    """MOVE before any await. Paths are storage only, never source/clock inputs."""
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
    if (
        _pins is not _SOURCE_PIN_CHECKER_FUNCTION
        or _pins.__code__ != _SOURCE_PIN_CHECKER_CODE
        or _pins.__code__.co_filename != _SOURCE_PIN_CHECKER_CODE.co_filename
        or (_pins.__defaults__ is not _SOURCE_PIN_CHECKER_DEFAULTS)
        or (_pins.__kwdefaults__ is not None)
        or (_pins.__closure__ is not None)
    ):
        raise NativeNamespaceHistorySourceError(
            "namespace_history_original_function_or_policy_changed:source_callable_contract_changed"
        ) from None
    _pins()
    binding = phase._take_native_namespace_history_v1(phase_owner, key=_HOOK_KEY)
    record = (
        _MOVED_BINDINGS.get(binding)
        if type(binding) is _MovedNamespaceHistoryBindingV1
        else None
    )
    moved = None if record is None else record["moved"]
    if moved is None:
        _deny("namespace_history_original_moved_binding_required")
    state = None
    try:
        if len(_OWNERS) >= MAX_OWNERS or any(
            type(path) not in (PosixPath, WindowsPath) or not path.is_absolute()
            for path in (raw_root, native_root)
        ):
            _deny("namespace_history_closed_storage_profile_required")
        held = moved["current_account"]
        actual = _context()
        original = native.clock.native_stamp()
        state = dict.__new__(_NamespaceHistoryStateV1)
        state.update(
            {
                "moved": moved,
                "binding": binding,
                "context": (*actual[:3], ref(actual[3])),
                "lease": held["read_session"],
                "private_client": held["private_client"],
                "factory_pins": coverage._factory_pins(
                    moved["factory"], moved["custody"]
                ),
                "original": original,
                "loop_deadline": actual[2].time()
                + bundle.POLICY["native_anchor_ns"] / 1000000000,
                "last": original,
                "raw_root": raw_root,
                "native_root": native_root,
                "transferred": False,
            }
        )
        _bind_moved_state(state, key=_HOOK_KEY)
        _check(state)
        state["prepared_plan"] = _plan(state)
        return _Attempt(_acquire(state), state)
    except BaseException:
        if state is not None:
            _release_moved(state)
            state.clear()
        _MOVED_BINDINGS.pop(binding, None)
        moved.clear()
        raise


def consume_owned_native_namespace_history_v1(owner):
    """Burn at call time; audit/source-fact handoff never grants G6 authority."""
    if (
        _pins is not _SOURCE_PIN_CHECKER_FUNCTION
        or _pins.__code__ != _SOURCE_PIN_CHECKER_CODE
        or _pins.__code__.co_filename != _SOURCE_PIN_CHECKER_CODE.co_filename
        or _pins.__defaults__ is not _SOURCE_PIN_CHECKER_DEFAULTS
        or _pins.__kwdefaults__ is not None
        or _pins.__closure__ is not None
    ):
        raise NativeNamespaceHistorySourceError(
            "namespace_history_original_function_or_policy_changed:source_callable_contract_changed"
        ) from None
    _pins()
    state = (
        _OWNERS.pop(owner, None)
        if type(owner) is OwnedNativeNamespaceHistoryV1
        else None
    )
    if state is None:
        _deny("namespace_history_owner_unavailable")
    state["transferred"] = False
    return _Attempt(_consume(state), state)


async def _consume(state):
    try:
        if (
            _pins is not _SOURCE_PIN_CHECKER_FUNCTION
            or _pins.__code__ != _SOURCE_PIN_CHECKER_CODE
            or _pins.__code__.co_filename != _SOURCE_PIN_CHECKER_CODE.co_filename
            or _pins.__defaults__ is not _SOURCE_PIN_CHECKER_DEFAULTS
            or _pins.__kwdefaults__ is not None
            or _pins.__closure__ is not None
        ):
            raise NativeNamespaceHistorySourceError(
                "namespace_history_original_function_or_policy_changed:source_callable_contract_changed"
            ) from None
        _pins()
        _check(state)
        for root, identity, inventory, maximum in (
            ("raw_root", "raw_root_identity", "raw_inventory", MAX_RAW_ROOT_BYTES),
            (
                "native_root",
                "native_root_identity",
                "clock_inventory",
                MAX_CLOCK_ROOT_BYTES,
            ),
        ):
            with storage._root_context(state[root]) as directory:
                if storage._root_identity(directory.path) != state[identity]:
                    _deny("namespace_history_original_roots_changed")
                _inventory(state, directory, state[inventory], maximum)
        await _await(
            state,
            runtime._recheck_original_db_chain(
                state["journal_repository"],
                state["scope"],
                state["chain"],
                state["reference"],
                state["facts"].packet,
                proof_schema=proof.ALGO_V6_SCHEMA,
            ),
        )
        retained = await _await(
            state,
            custody.RuntimeBundleCustodyRepository.read_retained_bundle(
                state["moved"]["custody"],
                bundle_id=state["custody_id"],
                expected_sha256=state["facts"].bundle_sha256,
                proof_envelope_sha256=state["proof_pin"],
            ),
        )
        if (
            type(retained) is not custody.RetainedRuntimeBundleReadback
            or retained.bundle_raw != state["facts"].bundle_raw
        ):
            _deny("namespace_history_native_custody_changed")
        facts = bundle.replay_native_namespace_history_bundle_v1(
            retained.bundle_raw, expected_sha256=state["facts"].bundle_sha256
        )
        _check(state)
        return facts
    except asyncio.CancelledError:
        raise
    except NativeNamespaceHistorySourceError:
        raise
    except Exception:  # noqa: BLE001 -- private source/driver details never escape.
        _deny()
    finally:
        _release_moved(state)
        state.clear()


_PINS = (
    (sys.modules[__name__], "ReviewedDemoPublicRoute", ReviewedDemoPublicRoute),
    (
        sys.modules[__name__],
        "_secret_checked_carried_event",
        _secret_checked_carried_event,
    ),
    (
        sys.modules[__name__],
        "_secret_checked_carried_sources",
        _secret_checked_carried_sources,
    ),
    (runtime, "_secret_checked", runtime._secret_checked),
    (collector, "_no_secrets", collector._no_secrets),
    (collector, "_no_secret_json", collector._no_secret_json),
    (journal, "checked_event", journal.checked_event),
    (
        journal,
        "_check_owned_packet_for_storage",
        journal._check_owned_packet_for_storage,
    ),
    (
        account,
        "replay_current_account_bundle_v4",
        account.replay_current_account_bundle_v4,
    ),
    (account, "decode", account.decode),
    (account, "_unhex", account._unhex),
    # The historical alias remains pinned; integer identity is no authority.
    (sys.modules[__name__], "_state_identity", _state_identity),
    (sys.modules[__name__], "ref", ref),
    (sys.modules[__name__], "_NamespaceHistoryStateV1", _NamespaceHistoryStateV1),
    (_NamespaceHistoryStateV1, "__new__", _NamespaceHistoryStateV1.__new__),
    (_NamespaceHistoryStateV1, "__copy__", _NamespaceHistoryStateV1.__copy__),
    (_NamespaceHistoryStateV1, "__deepcopy__", _NamespaceHistoryStateV1.__deepcopy__),
    (_NamespaceHistoryStateV1, "__reduce_ex__", _NamespaceHistoryStateV1.__reduce_ex__),
    (sys.modules[__name__], "_release_dead_state", _release_dead_state),
    (sys.modules[__name__], "_bind_moved_state", _bind_moved_state),
    (sys.modules[__name__], "_begin_acquisition", _begin_acquisition),
    (
        sys.modules[__name__],
        "_register_moved_namespace_history_v1",
        _register_moved_namespace_history_v1,
    ),
    (sys.modules[__name__], "_release_moved", _release_moved),
    (sys.modules[__name__], "validate_stamps", validate_stamps),
    (sys.modules[__name__], "canonical", canonical),
    (sys.modules[__name__], "sha", sha),
    (sys.modules[__name__], "utc_from_ns", utc_from_ns),
    (sys.modules[__name__], "get_ident", get_ident),
    (os, "getpid", os.getpid),
    (asyncio, "current_task", asyncio.current_task),
    (asyncio, "get_running_loop", asyncio.get_running_loop),
    (asyncio, "timeout_at", asyncio.timeout_at),
    (sys.modules[__name__], "_pins", _pins),
    (sys.modules[__name__], "_context", _context),
    (sys.modules[__name__], "_check", _check),
    (sys.modules[__name__], "_await", _await),
    (sys.modules[__name__], "_plan", _plan),
    (sys.modules[__name__], "_acquire", _acquire),
    (sys.modules[__name__], "_consume", _consume),
    (sys.modules[__name__], "_publish", _publish),
    (sys.modules[__name__], "_inventory", _inventory),
    (sys.modules[__name__], "_historical_custody", _historical_custody),
    (sys.modules[__name__], "_wire_bound", _wire_bound),
    (
        sys.modules[__name__],
        "_replay_original_route_attempt",
        _replay_original_route_attempt,
    ),
    (sys.modules[__name__], "_route_proof", _route_proof),
    (
        sys.modules[__name__],
        "acquire_owned_native_namespace_history_v1",
        acquire_owned_native_namespace_history_v1,
    ),
    (
        sys.modules[__name__],
        "consume_owned_native_namespace_history_v1",
        consume_owned_native_namespace_history_v1,
    ),
    (_Attempt, "send", _Attempt.send),
    (_Attempt, "throw", _Attempt.throw),
    (_Attempt, "close", _Attempt.close),
    (
        phase,
        "_take_native_namespace_history_v1",
        phase._take_native_namespace_history_v1,
    ),
    (phase, "_historical_context", phase._historical_context),
    (coverage, "_factory_pins", coverage._factory_pins),
    (coverage, "_source_files", coverage._source_files),
    (DemoPinnedReadSession, "verify_current", DemoPinnedReadSession.verify_current),
    (
        OkxDemoPrivateRestClient,
        "_active_read_session",
        OkxDemoPrivateRestClient._active_read_session,
    ),
    (
        ControlledDemoAccountSession,
        "from_pinned_read_session",
        ControlledDemoAccountSession.from_pinned_read_session.__func__,
    ),
    (
        ControlledDemoAccountSession,
        "_checked_read_session",
        ControlledDemoAccountSession._checked_read_session,
    ),
    (native.clock, "native_stamp", native.clock.native_stamp),
    (native, "_claim_initial_session", native._claim_initial_session),
    (native, "_initial_stage", native._initial_stage),
    (native, "_checked_credential_pin", native._checked_credential_pin),
    (bootstrap, "_collect_recorded", bootstrap._collect_recorded),
    (runtime, "_time_probe", runtime._time_probe),
    (runtime, "_recheck_original_db_chain", runtime._recheck_original_db_chain),
    (
        operational,
        "_probe_initial_demo_operational_route_retained_v4",
        operational._probe_initial_demo_operational_route_retained_v4,
    ),
    (
        operational,
        "_consume_initial_route_state_v4",
        operational._consume_initial_route_state_v4,
    ),
    (
        collector,
        "_collect_owned_demo_account_records",
        collector._collect_owned_demo_account_records,
    ),
    (collector, "_signed_request", collector._signed_request),
    (
        AccountCaptureJournalRepository,
        "read_chain",
        AccountCaptureJournalRepository.read_chain,
    ),
    (
        custody.RuntimeBundleCustodyRepository,
        "verify_deployment",
        custody.RuntimeBundleCustodyRepository.verify_deployment,
    ),
    (
        custody.RuntimeBundleCustodyRepository,
        "seal_bundle",
        custody.RuntimeBundleCustodyRepository.seal_bundle,
    ),
    (
        custody.RuntimeBundleCustodyRepository,
        "read_retained_bundle",
        custody.RuntimeBundleCustodyRepository.read_retained_bundle,
    ),
    (
        custody,
        "_consume_runtime_bundle_custody_receipt",
        custody._consume_runtime_bundle_custody_receipt,
    ),
    (account, "_chain_document", account._chain_document),
    (account, "_chain_replay", account._chain_replay),
    (proof, "_source", proof._source),
    (bundle, "phase_document", bundle.phase_document),
    (bundle, "sha", bundle.sha),
    (bundle, "canonical", bundle.canonical),
    (bundle, "utc_from_ns", bundle.utc_from_ns),
    (bundle, "validate_stamps", bundle.validate_stamps),
    (bundle, "ledger_digest", bundle.ledger_digest),
    (bundle, "_phase", bundle._phase),
    (bundle, "_control_lineage", bundle._control_lineage),
    (bundle, "_coverage_source", bundle._coverage_source),
    (bundle, "_replay_succession", bundle._replay_succession),
    (bundle.coverage_rows, "_observation", bundle.coverage_rows._observation),
    (
        bundle.control,
        "replay_control_journal_lineage_v1",
        bundle.control.replay_control_journal_lineage_v1,
    ),
    (phase, "_join", phase._join),
    (bundle, "namespace_queries", bundle.namespace_queries),
    (
        bundle,
        "replay_native_namespace_history_bundle_v1",
        bundle.replay_native_namespace_history_bundle_v1,
    ),
)


# Expected code/default/closure contracts are source-compiled once before any
# acquisition. Later runtime mutations cannot become a new observed baseline.
_SOURCE_VERIFIER_FUNCTION = _assert_source_callable
_SOURCE_VERIFIER_DEFAULTS = _assert_source_callable.__defaults__
_SOURCE_PIN_CHECKER_FUNCTION = _pins
_SOURCE_PIN_CHECKER_DEFAULTS = _pins.__defaults__
_SOURCE_CALLABLE_PROFILES = _compile_source_callable_profiles()
_SOURCE_VERIFIER_CODE = next(
    code
    for owner, name, code, _defaults, _keywords, _namespace, _builtins, _lookups in _SOURCE_CALLABLE_PROFILES
    if owner is sys.modules[__name__] and name == "_assert_source_callable"
)
_SOURCE_PIN_CHECKER_CODE = next(
    code
    for owner, name, code, _defaults, _keywords, _namespace, _builtins, _lookups in _SOURCE_CALLABLE_PROFILES
    if owner is sys.modules[__name__] and name == "_pins"
)
