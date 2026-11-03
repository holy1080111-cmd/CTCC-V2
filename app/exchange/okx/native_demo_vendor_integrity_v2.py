"""Finite pristine-runtime checks for the native Demo physical transport.

The return value is inert verification DATA. Only the native driver may retain
the result of its own admission call inside its genuine private gate. Constructing
or passing this record never creates an order permit, owner, or retry authority.
No candidate method or descriptor is invoked by these checks.
"""

import asyncio.proactor_events as _proactor
import asyncio.selector_events as _selector
import builtins
import hashlib
import ssl
import sys
from abc import ABCMeta
from dataclasses import dataclass, field
from pathlib import Path
from types import (
    BuiltinFunctionType,
    CodeType,
    FunctionType,
    MethodDescriptorType,
    ModuleType,
    WrapperDescriptorType,
)

import anyio._backends._asyncio as _anyio_asyncio
import anyio.streams.tls as _anyio_tls
import httpcore._backends.anyio as _httpcore_anyio

from app.trade_qualification import native_continuation_guard_v1 as _guard

_MODULES = (
    ("asyncio.proactor_events", _proactor),
    ("asyncio.selector_events", _selector),
    ("ssl", ssl),
    ("anyio._backends._asyncio", _anyio_asyncio),
    ("anyio.streams.tls", _anyio_tls),
    ("httpcore._backends.anyio", _httpcore_anyio),
)
_FILES = (
    (
        ("asyncio", "proactor_events.py"),
        "62d15e99315cae52e8548f487391ae6c46827bf8e5b4bcf3382c777a494a759b",
        33500,
    ),
    (
        ("asyncio", "selector_events.py"),
        "b77c4b8a85f1e1e2882a2bf3bd85a4b1c1fcfd7cb37ed0d8d8eb5f9a2ea44231",
        48332,
    ),
    # These are two exact reviewed runtimes. The official Linux image's
    # stdlib has its own bytes; a different patch still denies admission.
    (
        (
            ("ssl.py",),
            "039a0e3f1782e14824b1624c7ffefc2de31714b4a9994424be45a4271966097c",
            50904,
        )
        if sys.platform == "linux"
        else (
            ("ssl.py",),
            "88c619ce59e1c92f2234c10700836c5c7198a070cdab21cfebb4a023e700205f",
            51099,
        )
    ),
    (
        ("anyio", "_backends", "_asyncio.py"),
        "56acbb3b5ca6984a714e23f37a14ae042553ce21802cd57e05bb0845a297b20e",
        107767,
    ),
    (
        ("anyio", "streams", "tls.py"),
        "7ea2e6c6f50b14ef6bbf11b2277dd53eb38837b1377a2535abdd019262a2810f",
        15818,
    ),
    (
        ("httpcore", "_backends", "anyio.py"),
        "c7c3e01215d10bc6d5aac77393f609c7c63a77d4ee6f4e82b944b09db9adaa86",
        5252,
    ),
)
# module index, lookup class, actual declaring class, attribute, exact defaults.
# Twenty IO, five cleanup callees and one fixed read-only stream lookup;
# inheritance is explicit, not inferred
# from a candidate function's __module__, qualname, or claimed source filename.
_SPECS = (
    (4, "TLSStream", "TLSStream", "_call_sslobject_method", None),
    (4, "TLSStream", "TLSStream", "send", None),
    (4, "TLSStream", "TLSStream", "receive", (65536,)),
    (4, "TLSStream", "TLSStream", "aclose", None),
    (3, "SocketStream", "SocketStream", "send", None),
    (3, "SocketStream", "SocketStream", "receive", (65536,)),
    (3, "SocketStream", "SocketStream", "aclose", None),
    (5, "AnyIOStream", "AnyIOStream", "read", (None,)),
    (5, "AnyIOStream", "AnyIOStream", "write", (None,)),
    (5, "AnyIOStream", "AnyIOStream", "start_tls", (None, None)),
    (5, "AnyIOStream", "AnyIOStream", "aclose", None),
    (5, "AnyIOBackend", "AnyIOBackend", "connect_tcp", (None, None, None)),
    (2, "SSLObject", "SSLObject", "write", None),
    (2, "SSLObject", "SSLObject", "read", (1024, None)),
    (0, "_ProactorSocketTransport", "_ProactorBaseWritePipeTransport", "write", None),
    (0, "_ProactorSocketTransport", "_ProactorBaseWritePipeTransport", "abort", None),
    (0, "_ProactorSocketTransport", "_ProactorBasePipeTransport", "close", None),
    (1, "_SelectorSocketTransport", "_SelectorSocketTransport", "write", None),
    (1, "_SelectorSocketTransport", "_SelectorTransport", "abort", None),
    (1, "_SelectorSocketTransport", "_SelectorSocketTransport", "close", None),
    (0, "_ProactorSocketTransport", "_ProactorBasePipeTransport", "_force_close", None),
    (
        0,
        "_ProactorSocketTransport",
        "_ProactorBasePipeTransport",
        "_call_connection_lost",
        None,
    ),
    (1, "_SelectorSocketTransport", "_SelectorTransport", "_force_close", None),
    (
        1,
        "_SelectorSocketTransport",
        "_SelectorSocketTransport",
        "_call_connection_lost",
        None,
    ),
    (1, "_SelectorTransport", "_SelectorTransport", "_call_connection_lost", None),
    (5, "AnyIOStream", "AnyIOStream", "get_extra_info", None),
)


class NativeDemoVendorIntegrityError(ValueError):
    """Closed public codes only; never format a candidate, path, or exception."""


def _need(condition, code="native_demo_vendor_integrity_changed"):
    if condition is not True:
        raise NativeDemoVendorIntegrityError(code) from None


def _class_dict(owner):
    _need(type(owner) is type or type(owner) is ABCMeta)
    return type.__getattribute__(owner, "__dict__")


def _normalise_descriptor(value):
    """Return the raw descriptor and its function without invoking __get__."""
    kind = type(value)
    if kind is FunctionType:
        return value, value, "function"
    if kind is staticmethod:
        return value, value.__func__, "staticmethod"
    if kind is classmethod:
        return value, value.__func__, "classmethod"
    if (
        kind is BuiltinFunctionType
        or kind is MethodDescriptorType
        or kind is WrapperDescriptorType
    ):
        return value, value, "builtin"
    raise NativeDemoVendorIntegrityError("native_demo_vendor_descriptor_changed")


def _read_fixed_source(row):
    path, digest, size = row
    _need(type(size) is int and 0 < size <= 2 * 1024 * 1024)
    try:
        _need(not path.is_symlink())
        with path.open("rb") as stream:
            raw = stream.read(size + 1)
    except OSError:
        raise NativeDemoVendorIntegrityError(
            "native_demo_vendor_source_unavailable"
        ) from None
    _need(
        len(raw) == size and hashlib.sha256(raw).hexdigest() == digest,
        "native_demo_vendor_source_changed",
    )
    return raw


def _source_row(namespace, spec):
    """A known module selects its own file; path spelling creates no authority."""
    filename = namespace.get("__file__")
    _need(
        type(filename) is str and 0 < len(filename) <= 4096 and "\0" not in filename,
        "native_demo_vendor_namespace_unbound",
    )
    path = Path(filename)
    relative, digest, size = spec
    _need(
        path.is_absolute() and tuple(path.parts[-len(relative) :]) == relative,
        "native_demo_vendor_namespace_unbound",
    )
    return path, digest, size


def _expected_code(module_code, qualname):
    _need(type(module_code) is CodeType)
    pending = [module_code]
    found = []
    visited = 0
    while pending:
        code = pending.pop()
        visited += 1
        _need(visited <= 100000, "native_demo_vendor_code_capacity")
        if code.co_qualname == qualname:
            found.append(code)
        pending.extend(item for item in code.co_consts if type(item) is CodeType)
    _need(len(found) == 1, "native_demo_vendor_declaration_unbound")
    return found[0]


def _defaults_match(actual, expected):
    if expected is None:
        return actual is None
    return (
        type(actual) is tuple
        and len(actual) == len(expected)
        and all(
            type(left) is type(right) and left == right
            for left, right in zip(actual, expected, strict=True)
        )
    )


def _resolve(namespace, lookup_name, declaring_name, attribute):
    lookup, declaring = namespace.get(lookup_name), namespace.get(declaring_name)
    _need(
        (type(lookup) is type or type(lookup) is ABCMeta)
        and (type(declaring) is type or type(declaring) is ABCMeta)
    )
    mro = type.__getattribute__(lookup, "__mro__")
    actual_owner = next(
        (owner for owner in mro if attribute in _class_dict(owner)), None
    )
    _need(actual_owner is declaring, "native_demo_vendor_declaring_owner_changed")
    descriptor = _class_dict(declaring)[attribute]
    raw, function, kind = _normalise_descriptor(descriptor)
    return lookup, declaring, mro, raw, function, kind


@dataclass(frozen=True, slots=True, repr=False)
class _FunctionPinV2:
    module_index: int
    lookup_name: str
    declaring_name: str
    attribute: str
    lookup: object
    declaring: object
    mro: tuple = field()
    descriptor: object
    function: object
    code: object
    namespace: dict
    builtin_namespace: dict
    defaults: object
    closure: object
    cells: tuple


@dataclass(frozen=True, slots=True, repr=False)
class NativeDemoVendorAdmissionV2:
    """Inert guard DATA; a constructor never supplies native order authority."""

    bindings: tuple
    file_pins: tuple
    _functions: tuple = field(repr=False)
    _module_pins: tuple = field(repr=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


def _fixed_basis_v2(*, _specs=_SPECS, _modules=_MODULES, _files=_FILES):
    """Private original metadata capture; public APIs accept no basis override."""
    _need(_SPECS is _specs and _MODULES is _modules and _FILES is _files)
    _need(len(_specs) == 26 and len(_modules) == len(_files) == 6)
    return _specs, _modules, _files


def admit_native_demo_vendors_v2():
    """Verify 26 fixed IO/cleanup/read purposes against six pristine source files."""
    _specs, _modules, _files = _fixed_basis_v2()
    compiled_sources, module_pins, source_rows = [], [], []
    _need(type(sys.modules) is dict)
    for (name, module), spec in zip(_modules, _files, strict=True):
        _need(type(module) is ModuleType and sys.modules.get(name) is module)
        namespace = ModuleType.__getattribute__(module, "__dict__")
        _need(
            type(namespace) is dict
            and type(namespace.get("__name__")) is str
            and namespace.get("__name__") == name
        )
        row = _source_row(namespace, spec)
        source_rows.append(row)
        raw = _read_fixed_source(row)
        # Compile each of the six known files once as DATA, never execute it.
        compiled_sources.append(
            compile(raw, str(row[0]), "exec", dont_inherit=True, optimize=0)
        )
        module_pins.append((name, module, namespace))
    pins = []
    for index, lookup_name, declaring_name, attribute, defaults in _specs:
        namespace = module_pins[index][2]
        lookup, declaring, mro, descriptor, function, kind = _resolve(
            namespace, lookup_name, declaring_name, attribute
        )
        # All26 in this exact runtime are Python functions. A new builtin or
        # descriptor kind is an explicit runtime transition, never a fallback.
        _need(
            kind == "function" and type(function) is FunctionType,
            "native_demo_vendor_descriptor_changed",
        )
        expected = _expected_code(
            compiled_sources[index], declaring_name + "." + attribute
        )
        _need(
            function.__globals__ is namespace
            and function.__builtins__ is builtins.__dict__,
            "native_demo_vendor_namespace_unbound",
        )
        _need(
            _guard._code_fields(function.__code__) == _guard._code_fields(expected),
            "native_demo_vendor_code_unbound",
        )
        _need(
            _defaults_match(function.__defaults__, defaults)
            and function.__kwdefaults__ is None,
            "native_demo_vendor_defaults_changed",
        )
        closure = function.__closure__
        cells = () if closure is None else tuple(cell.cell_contents for cell in closure)
        _need(
            (not expected.co_freevars and closure is None)
            or (
                expected.co_freevars == ("__class__",)
                and len(cells) == 1
                and cells[0] is declaring
            ),
            "native_demo_vendor_closure_unbound",
        )
        pins.append(
            _FunctionPinV2(
                index,
                lookup_name,
                declaring_name,
                attribute,
                lookup,
                declaring,
                mro,
                descriptor,
                function,
                function.__code__,
                namespace,
                builtins.__dict__,
                function.__defaults__,
                closure,
                cells,
            )
        )
    result = NativeDemoVendorAdmissionV2(
        tuple((pin.declaring, pin.attribute, pin.descriptor) for pin in pins),
        tuple((row[0], row[1]) for row in source_rows),
        tuple(pins),
        tuple(module_pins),
    )
    require_native_demo_vendors_v2(result)
    return result


def require_native_demo_vendors_v2(admission):
    """Recheck a driver-retained admission without invoking vendor callbacks.

    This consumes no owner and emits no pass token. The driver's source guard
    protects this helper plus the captured admission object; arbitrary caller
    records cannot be substituted for its own original admission return.
    """
    _specs, _modules, _files = _fixed_basis_v2()
    _need(type(sys.modules) is dict)
    _need(
        type(admission) is NativeDemoVendorAdmissionV2
        and type(admission._functions) is tuple
        and len(admission._functions) == 26
        and type(admission._module_pins) is tuple
        and len(admission._module_pins) == 6
        and type(admission.bindings) is tuple
        and len(admission.bindings) == 26
        and admission.execution_authority is False
        and admission.order_retry_authority is False
    )
    _need(type(admission.file_pins) is tuple and len(admission.file_pins) == 6)
    source_rows = []
    for actual, (expected_name, expected_module) in zip(
        admission._module_pins, _modules, strict=True
    ):
        _need(type(actual) is tuple and len(actual) == 3)
        name, module, namespace = actual
        _need(
            type(name) is str
            and name == expected_name
            and module is expected_module
            and sys.modules.get(name) is module
            and ModuleType.__getattribute__(module, "__dict__") is namespace
        )
    for (name, _module, namespace), spec, actual in zip(
        admission._module_pins, _files, admission.file_pins, strict=True
    ):
        _need(
            type(namespace.get("__name__")) is str and namespace.get("__name__") == name
        )
        row = _source_row(namespace, spec)
        _need(
            type(actual) is tuple
            and len(actual) == 2
            and type(actual[0]) is type(row[0])
            and actual[0] == row[0]
            and type(actual[1]) is str
            and actual[1] == row[1]
        )
        source_rows.append(row)
    for pin, spec, binding in zip(
        admission._functions, _specs, admission.bindings, strict=True
    ):
        _need(type(pin) is _FunctionPinV2)
        index, lookup_name, declaring_name, attribute, defaults = spec
        _need(
            type(pin.module_index) is int
            and type(pin.lookup_name) is str
            and type(pin.declaring_name) is str
            and type(pin.attribute) is str
            and type(pin.cells) is tuple
        )
        _need(
            (pin.module_index, pin.lookup_name, pin.declaring_name, pin.attribute)
            == (index, lookup_name, declaring_name, attribute)
        )
        namespace = admission._module_pins[index][2]
        lookup, declaring, mro, descriptor, function, kind = _resolve(
            namespace, lookup_name, declaring_name, attribute
        )
        _need(
            kind == "function"
            and lookup is pin.lookup
            and declaring is pin.declaring
            and mro is pin.mro
            and descriptor is pin.descriptor
            and function is pin.function
            and type(binding) is tuple
            and len(binding) == 3
            and binding[0] is declaring
            and type(binding[1]) is str
            and binding[1] == attribute
            and binding[2] is descriptor
        )
        _need(
            function.__code__ is pin.code
            and function.__globals__ is pin.namespace
            and namespace is pin.namespace
            and function.__builtins__ is pin.builtin_namespace
            and pin.builtin_namespace is builtins.__dict__
            and function.__defaults__ is pin.defaults
            and _defaults_match(function.__defaults__, defaults)
            and function.__kwdefaults__ is None
            and function.__closure__ is pin.closure
        )
        if pin.closure is not None:
            _need(
                len(pin.closure) == len(pin.cells)
                and all(
                    cell.cell_contents is expected
                    for cell, expected in zip(pin.closure, pin.cells, strict=True)
                )
            )
    for row in source_rows:
        _read_fixed_source(row)


def capture_native_demo_cleanup_v2(admission, hold):
    """Capture only the actual abort/force-close/lost-callback chain.

    The returned closure closes a known native socket or returns False and
    strongly retains it as unknown. It grants no request permission. Fallback
    validation invokes no module-global `_need` or exception constructor.
    The scheduled connection-lost callback revalidates before executing too.
    """
    require_native_demo_vendors_v2(admission)
    _need(type(hold) is dict)
    selected = tuple(
        pin
        for pin in admission._functions
        if pin.attribute in {"abort", "_force_close", "_call_connection_lost"}
    )
    _need(len(selected) == 7)
    rows = []
    for pin in selected:
        namespace = pin.namespace
        names = tuple(name for name in pin.code.co_names if name in namespace)
        globals_pin = tuple((name, namespace[name]) for name in names)
        builtins_pin = tuple(
            (name, pin.builtin_namespace[name])
            for name in pin.code.co_names
            if name in pin.builtin_namespace
        )
        rows.append(
            (
                pin.lookup,
                pin.declaring,
                pin.attribute,
                pin.mro,
                pin.descriptor,
                pin.function,
                pin.code,
                namespace,
                pin.builtin_namespace,
                pin.defaults,
                pin.closure,
                pin.cells,
                globals_pin,
                builtins_pin,
            )
        )
    rows = tuple(rows)
    native_type, native_dict, native_function = type, dict, FunctionType
    native_any, native_id, native_next, native_len, native_zip = any, id, next, len, zip
    type_get, object_get, object_set = (
        type.__getattribute__,
        object.__getattribute__,
        object.__setattr__,
    )
    base_exception = BaseException
    callbacks, unknown = {}, []
    hold["cleanup_unknown_sockets"] = unknown

    def failed(native):
        if not native_any(item is native for item in unknown):
            unknown.append(native)
        hold["phase"] = "unknown"
        return False

    def validate(native):
        try:
            kind = native_type(native)
            if not native_any(row[0] is kind and row[2] == "abort" for row in rows):
                return False
            members = object_get(native, "__dict__")
            if native_type(members) is not native_dict:
                return False
            for name in ("abort", "close", "write", "writelines", "_force_close"):
                if name in members:
                    return False
            callback = callbacks.get(native_id(native))
            if (
                "_call_connection_lost" in members
                and members["_call_connection_lost"] is not callback
            ):
                return False
            for (
                lookup,
                declaring,
                attribute,
                mro,
                descriptor,
                function,
                code,
                namespace,
                builtin_namespace,
                defaults,
                closure,
                cells,
                globals_pin,
                builtins_pin,
            ) in rows:
                actual_mro = type_get(lookup, "__mro__")
                if actual_mro is not mro:
                    return False
                actual_owner = native_next(
                    (
                        owner
                        for owner in actual_mro
                        if attribute in type_get(owner, "__dict__")
                    ),
                    None,
                )
                if (
                    actual_owner is not declaring
                    or type_get(declaring, "__dict__").get(attribute) is not descriptor
                    or native_type(function) is not native_function
                    or function.__code__ is not code
                    or function.__globals__ is not namespace
                    or function.__builtins__ is not builtin_namespace
                    or function.__defaults__ is not defaults
                    or function.__kwdefaults__ is not None
                    or function.__closure__ is not closure
                ):
                    return False
                if closure is not None and (
                    native_len(closure) != native_len(cells)
                    or native_any(
                        cell.cell_contents is not original
                        for cell, original in native_zip(closure, cells, strict=True)
                    )
                ):
                    return False
                if native_any(
                    namespace.get(name) is not original
                    for name, original in globals_pin
                ):
                    return False
                if native_any(
                    builtin_namespace.get(name) is not original
                    or namespace.get(name, original) is not original
                    for name, original in builtins_pin
                ):
                    return False
            return True
        except base_exception:
            return False

    def abort(native):
        if (
            validate.__code__ is not validator_code
            or failed.__code__ is not failed_code
        ):
            hold["cleanup_corrupt_socket"], hold["phase"] = native, "unknown"
            return False
        if not validate(native):
            return failed(native)
        kind = native_type(native)
        call = native_next(
            row[5] for row in rows if row[0] is kind and row[2] == "abort"
        )
        lost = native_next(
            row[5]
            for row in rows
            if row[0] is kind and row[2] == "_call_connection_lost"
        )
        if native_id(native) not in callbacks:

            def connection_lost(exc):
                if (
                    validate.__code__ is not validator_code
                    or failed.__code__ is not failed_code
                ):
                    hold["cleanup_corrupt_socket"], hold["phase"] = native, "unknown"
                    return False
                if not validate(native):
                    return failed(native)
                try:
                    lost(native, exc)
                except base_exception:
                    return failed(native)
                return True

            callbacks[native_id(native)] = connection_lost
            object_set(native, "_call_connection_lost", connection_lost)
        try:
            call(native)
        except base_exception:
            return failed(native)
        return True

    validator_code, failed_code = validate.__code__, failed.__code__
    return abort
