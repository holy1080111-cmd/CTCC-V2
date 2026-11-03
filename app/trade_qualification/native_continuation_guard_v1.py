"""Compiled-source leaf/namespace/closure guard for native V2 continuations.

Installation is local source assembly, never caller authority. Public entries
capture their actual guard and original source function before returning any
awaitable. The wrapper checks again on EVERY coroutine resume, including the
first send and throw/close paths. Mutation retires the one-use attempt.
"""

import builtins as _source_builtins
import sys
from collections.abc import Coroutine
from pathlib import Path
from struct import pack as _pack
from types import CodeType, FunctionType, MethodType, ModuleType
from weakref import finalize, ref

from app.domain.source_primitives import sha
from app.trade_qualification import (
    native_sqlalchemy_factory_guard_v1 as _sqlalchemy_factory,
)

_ROOT = Path(__file__).resolve().parents[2]


class NativeSourceClosureError(ValueError):
    """Fixed rejection codes; no source path or exception payload escapes."""


def _code_fields(code):
    """Closed code-value fingerprint; no repr, frames, pickle or execution."""
    nodes = 0
    encoded_bytes = 0

    def frame(tag, payload):
        nonlocal encoded_bytes
        encoded_bytes += len(payload)
        if encoded_bytes > 8 * 1024 * 1024:
            raise ValueError("postpublication_code_fingerprint_byte_capacity")
        return tag + len(payload).to_bytes(8, "big") + payload

    def encode(value, depth):
        nonlocal nodes
        nodes += 1
        if depth > 128 or nodes > 100000:
            raise ValueError("postpublication_code_fingerprint_node_capacity")
        kind = type(value)
        if value is None:
            return frame(b"N", b"")
        if value is Ellipsis:
            return frame(b"E", b"")
        if kind is bool:
            return frame(b"B", b"1" if value else b"0")
        if kind is int:
            size = max(1, (value.bit_length() + 7) // 8)
            if size > 1024 * 1024:
                raise ValueError("postpublication_code_integer_capacity")
            return frame(
                b"I", (b"-" if value < 0 else b"+") + abs(value).to_bytes(size, "big")
            )
        if kind is float:
            return frame(b"F", _pack(">d", value))
        if kind is complex:
            return frame(b"J", _pack(">dd", value.real, value.imag))
        if kind is str:
            return frame(b"S", value.encode("utf-8", "surrogatepass"))
        if kind is bytes:
            return frame(b"Y", value)
        if kind is tuple:
            return frame(b"T", b"".join(encode(item, depth + 1) for item in value))
        if kind is frozenset:
            return frame(
                b"R", b"".join(sorted(encode(item, depth + 1) for item in value))
            )
        if kind is CodeType:
            fields = (
                value.co_filename,
                value.co_name,
                value.co_qualname,
                value.co_firstlineno,
                value.co_argcount,
                value.co_posonlyargcount,
                value.co_kwonlyargcount,
                value.co_nlocals,
                value.co_stacksize,
                value.co_flags,
                value.co_code,
                value.co_consts,
                value.co_names,
                value.co_varnames,
                value.co_freevars,
                value.co_cellvars,
                value.co_linetable,
                value.co_exceptiontable,
            )
            return frame(b"C", encode(fields, depth + 1))
        raise ValueError("postpublication_code_constant_type_not_supported")

    if type(code) is not CodeType:
        raise ValueError("postpublication_code_type_changed")
    return encode(code, 0)


def _source_code(value, raw, filename):
    """Compile known pinned source as data; never execute its imports or body."""
    pending = [compile(raw, filename, "exec", dont_inherit=True)]
    found = []
    while pending:
        code = pending.pop()
        if code.co_qualname == value.__code__.co_qualname:
            found.append(code)
        pending.extend(item for item in code.co_consts if type(item) is type(code))
    try:
        matching = len(found) == 1 and _code_fields(found[0]) == _code_fields(
            value.__code__
        )
    except ValueError:
        raise NativeSourceClosureError("postpublication_source_code_unbound")
    if not matching:
        raise NativeSourceClosureError("postpublication_source_code_unbound")
    # A matching code object alone cannot bless a preexisting FunctionType
    # clone. Resolve its defining module from the pinned SOURCE PATH, never
    # value.__module__ or an observed foreign function namespace.
    path = Path(filename).resolve()
    relative = path.relative_to(_ROOT).as_posix()
    defining_globals = sys.modules[relative[:-3].replace("/", ".")].__dict__
    defining_builtins = _source_builtins.__dict__
    if (
        value.__globals__ is not defining_globals
        or value.__builtins__ is not defining_builtins
    ):
        raise NativeSourceClosureError("postpublication_source_namespace_unbound")
    # Inspect only builtin names read by this exact source-compiled function
    # and nested code. No leaf or source module is executed by this traversal.
    pending = [found[0]]
    names = set()
    while pending:
        code = pending.pop()
        names.update(code.co_names)
        pending.extend(item for item in code.co_consts if type(item) is type(code))
    lookups = tuple(
        (name, defining_builtins[name])
        for name in sorted(names)
        if name in defining_builtins and not name.startswith("_")
    )
    for name, original in lookups:
        if (
            defining_globals.get(name, original) is not original
            or defining_builtins.get(name) is not original
        ):
            raise NativeSourceClosureError("postpublication_source_namespace_unbound")
    return defining_globals, defining_builtins, lookups


def _function(value):
    if type(value) in (staticmethod, classmethod):
        return value.__func__
    return value if type(value) is FunctionType else None


def _function_pin(function, files, *, generated_dataclass=False):
    path = Path(function.__code__.co_filename)
    compiled = None
    namespace_name = function.__globals__.get("__name__")
    if (
        type(namespace_name) is str
        and namespace_name.startswith(("app.", "tests."))
        and path.is_absolute()
        and not path.resolve().is_relative_to(_ROOT)
    ):
        raise NativeSourceClosureError("native_continuation_source_changed")
    if path.is_absolute() and path.is_file():
        raw = path.read_bytes()
        if path.resolve().is_relative_to(_ROOT):
            compiled = _source_code(function, raw, str(path))
            files[path.resolve()] = sha(raw)
    elif function.__code__.co_filename == "<string>" and not generated_dataclass:
        try:
            factory = _sqlalchemy_factory.capture_exact_create_engine_v1(function)
        except Exception:  # noqa: BLE001 -- unrecognized generated functions deny without exception text.
            raise NativeSourceClosureError(
                "native_continuation_source_changed"
            ) from None
        compiled = (_sqlalchemy_factory.TAG, factory)
        for _, _, source_path, digest in factory[8]:
            files[source_path] = digest
    elif function.__code__.co_filename != "<string>" or not generated_dataclass:
        # Only generated dataclass init/representation code lacks a source
        # file. The declaring class and every generated function are identity,
        # code, defaults, namespace and closure pinned below.
        raise NativeSourceClosureError("native_continuation_source_changed")
    closure = function.__closure__
    kw = function.__kwdefaults__
    return (
        function,
        function.__code__,
        function.__globals__,
        function.__builtins__,
        function.__defaults__,
        kw,
        () if kw is None else tuple(sorted(kw.items())),
        closure,
        () if closure is None else tuple(cell.cell_contents for cell in closure),
        compiled,
    )


def _check_function(pin):
    (
        function,
        code,
        namespace,
        builtins,
        defaults,
        kw,
        kw_values,
        closure,
        cells,
        compiled,
    ) = pin
    if (
        function.__code__ is not code
        or function.__globals__ is not namespace
        or function.__builtins__ is not builtins
        or function.__defaults__ is not defaults
        or function.__kwdefaults__ is not kw
        or function.__closure__ is not closure
        or (kw is not None and tuple(sorted(kw.items())) != kw_values)
        or (
            closure is not None
            and (
                len(closure) != len(cells)
                or any(
                    cell.cell_contents is not original
                    for cell, original in zip(closure, cells, strict=True)
                )
            )
        )
    ):
        raise NativeSourceClosureError("native_continuation_source_changed")
    if compiled is not None:
        if len(compiled) == 2 and compiled[0] == _sqlalchemy_factory.TAG:
            try:
                _sqlalchemy_factory.require_exact_create_engine_v1(compiled[1])
            except Exception:  # noqa: BLE001 -- changed factory closure/source denies without exception text.
                raise NativeSourceClosureError(
                    "native_continuation_source_changed"
                ) from None
            return
        defining_globals, defining_builtins, lookups = compiled
        if namespace is not defining_globals or builtins is not defining_builtins:
            raise NativeSourceClosureError("native_continuation_source_changed")
        for name, original in lookups:
            if (
                defining_builtins.get(name) is not original
                or defining_globals.get(name, original) is not original
            ):
                raise NativeSourceClosureError("native_continuation_source_changed")


def _namespace_pins(module, excluded, files):
    records, functions, class_shapes = [], [], []
    path = Path(module.__file__).resolve()
    if not path.is_relative_to(_ROOT):
        raise NativeSourceClosureError("native_continuation_source_changed")
    files[path] = sha(path.read_bytes())
    for name, value in tuple(vars(module).items()):
        if name.startswith("__") or name in excluded or name == "_NATIVE_GUARD":
            continue
        records.append((module, name, value))
        function = _function(value)
        if function is not None:
            functions.append(_function_pin(function, files))
        elif isinstance(value, type) and value.__module__ == module.__name__:
            # Include source-defined methods and generated dataclass methods.
            for attribute, item in tuple(vars(value).items()):
                if attribute in {"__dict__", "__weakref__"}:
                    continue
                records.append((value, attribute, item))
                method = _function(item)
                if method is not None:
                    generated = (
                        "__dataclass_fields__" in vars(value)
                        and "__dataclass_params__" in vars(value)
                        and method.__globals__ is vars(module)
                        and method.__qualname__.startswith(value.__qualname__ + ".")
                        and attribute
                        in {
                            "__init__",
                            "__repr__",
                            "__eq__",
                            "__hash__",
                            "__setattr__",
                            "__delattr__",
                        }
                    )
                    functions.append(
                        _function_pin(method, files, generated_dataclass=generated)
                    )
                elif type(item) is property:
                    for accessor in (item.fget, item.fset, item.fdel):
                        if accessor is not None:
                            functions.append(_function_pin(accessor, files))
            fields = vars(value).get("model_fields")
            if type(fields) is dict:
                class_shapes.append((value, fields, tuple(fields.items())))
    return records, functions, class_shapes


def _guard_snapshot(guard):
    return (
        guard.__code__,
        guard.__globals__,
        guard.__defaults__,
        guard.__kwdefaults__,
        guard.__closure__,
        tuple(cell.cell_contents for cell in guard.__closure__ or ()),
    )


def _check_guard(guard, snapshot):
    code, namespace, defaults, kw, closure, cells = snapshot
    if (
        guard.__code__ is not code
        or guard.__globals__ is not namespace
        or guard.__defaults__ is not defaults
        or guard.__kwdefaults__ is not kw
        or guard.__closure__ is not closure
        or (
            closure is not None
            and (
                len(closure) != len(cells)
                or any(
                    cell.cell_contents is not item
                    for cell, item in zip(closure, cells, strict=True)
                )
            )
        )
    ):
        raise NativeSourceClosureError("native_continuation_source_changed")
    guard()


def save_native_guard_v1(state, guard):
    state["native_source_guard"] = guard
    state["native_source_guard_pin"] = _guard_snapshot(guard)


def check_saved_native_guard_v1(state):
    _check_guard(state["native_source_guard"], state["native_source_guard_pin"])


def _finalized_close(closer):
    try:
        closer()
    except BaseException:  # noqa: BLE001,S110 -- abandoned synchronous MOVE cleanup cannot propagate; accepted state remains spent.
        # An abandoned attempt cannot emit private errors or regain ownership.
        # Accepted files/rows stay intact; native close only retires its hold.
        pass


_COROUTINES = {}
_COROUTINE_UNKNOWN = {}


def _finalized_coroutine_close(registry, identity, token, closer, state):
    state["closed"] = True
    try:
        closer()
    except BaseException:  # noqa: BLE001 -- interrupted async cleanup is unknown; retain its exact frame/owners, never log it.
        state["cleanup_unknown"] = True
        _COROUTINE_UNKNOWN[token] = state
        return
    if registry.get(identity) is state and state["token"] is token:
        registry.pop(identity, None)


def _retire_coroutine_state(instance, registry):
    state = registry.get(id(instance))
    if state is None or state["owner"]() is not instance or state["closed"]:
        return
    state["closed"] = True
    state["finalizer"].detach()
    try:
        state["close"]()
    except BaseException:  # noqa: BLE001 -- Preserve unknown cleanup custody without authorizing retry.
        _COROUTINE_UNKNOWN[state["token"]] = state
    else:
        registry.pop(id(instance), None)


def _resume_coroutine_state(instance, registry, retire):
    state = registry.get(id(instance))
    if type(state) is not dict or state["owner"]() is not instance or state["closed"]:
        raise NativeSourceClosureError("native_continuation_source_changed")
    checked = state["checked"]
    try:
        if any(
            actual is not expected
            for actual, expected in zip(
                (
                    checked.__code__,
                    checked.__globals__,
                    checked.__defaults__,
                    checked.__kwdefaults__,
                ),
                state["checked_pin"],
                strict=True,
            )
        ):
            raise NativeSourceClosureError("native_continuation_source_changed")
        checked(state["guard"], state["snapshot"])
    except BaseException:  # noqa: BLE001 -- authenticate the fixed checkpoint before any underlying continuation.
        retire(instance, registry)
        raise NativeSourceClosureError("native_continuation_source_changed") from None
    return state


def _make_guarded_coroutine_type(registry, resume, retire):
    # The interpreter-facing methods capture this private registry and original
    # checkpoint structurally. A returned object has no writable instance dict,
    # body, resume/check callback, guard or snapshot attributes.
    resume_pin = (
        resume.__code__,
        resume.__globals__,
        resume.__defaults__,
        resume.__kwdefaults__,
    )
    retire_pin = (
        retire.__code__,
        retire.__globals__,
        retire.__defaults__,
        retire.__kwdefaults__,
    )
    native_hash, native_eq = object.__hash__, object.__eq__
    unknown = _COROUTINE_UNKNOWN

    class _GuardedCoroutine(Coroutine):
        """Opaque per-instance continuation, with a private authenticated hold."""

        __slots__ = ("__weakref__",)
        __hash__, __eq__ = native_hash, native_eq

        def __init__(self, body, guard, snapshot, checked):
            if (
                type(self) is not _GuardedCoroutine
                or vars(_GuardedCoroutine).get("__hash__") is not native_hash
                or vars(_GuardedCoroutine).get("__eq__") is not native_eq
            ):
                raise NativeSourceClosureError("native_continuation_source_changed")
            closer = body.close
            identity, token = id(self), object()
            state = {
                "body": body,
                "guard": guard,
                "snapshot": snapshot,
                "checked": checked,
                "checked_pin": (
                    checked.__code__,
                    checked.__globals__,
                    checked.__defaults__,
                    checked.__kwdefaults__,
                ),
                "close": closer,
                "closed": False,
                "owner": ref(self),
                "token": token,
            }
            registry[identity] = state
            state["finalizer"] = finalize(
                self,
                _finalized_coroutine_close,
                registry,
                identity,
                token,
                closer,
                state,
            )

        def __await__(self):
            return self

        def __iter__(self):
            return self

        def __next__(self):
            return self.send(None)

        def _check(self):
            # Compatibility inspection returns no private registry state.
            if (
                type(self) is not _GuardedCoroutine
                or vars(_GuardedCoroutine).get("__hash__") is not native_hash
                or vars(_GuardedCoroutine).get("__eq__") is not native_eq
                or resume.__code__ is not resume_pin[0]
                or resume.__globals__ is not resume_pin[1]
                or resume.__defaults__ is not resume_pin[2]
                or resume.__kwdefaults__ is not resume_pin[3]
                or retire.__code__ is not retire_pin[0]
                or retire.__globals__ is not retire_pin[1]
                or retire.__defaults__ is not retire_pin[2]
                or retire.__kwdefaults__ is not retire_pin[3]
            ):
                state = registry.get(id(self))
                if type(state) is dict:
                    state["closed"] = True
                    unknown[state["token"]] = state
                raise NativeSourceClosureError("native_continuation_source_changed")
            _state = resume(self, registry, retire)

        def send(self, value):
            if (
                type(self) is not _GuardedCoroutine
                or vars(_GuardedCoroutine).get("__hash__") is not native_hash
                or vars(_GuardedCoroutine).get("__eq__") is not native_eq
                or resume.__code__ is not resume_pin[0]
                or resume.__globals__ is not resume_pin[1]
                or resume.__defaults__ is not resume_pin[2]
                or resume.__kwdefaults__ is not resume_pin[3]
                or retire.__code__ is not retire_pin[0]
                or retire.__globals__ is not retire_pin[1]
                or retire.__defaults__ is not retire_pin[2]
                or retire.__kwdefaults__ is not retire_pin[3]
            ):
                state = registry.get(id(self))
                if type(state) is dict:
                    state["closed"] = True
                    unknown[state["token"]] = state
                raise NativeSourceClosureError("native_continuation_source_changed")
            state = resume(self, registry, retire)
            try:
                return state["body"].send(value)
            except BaseException:
                state["closed"] = True
                state["finalizer"].detach()
                registry.pop(id(self), None)
                raise

        def throw(self, *args):
            if (
                type(self) is not _GuardedCoroutine
                or vars(_GuardedCoroutine).get("__hash__") is not native_hash
                or vars(_GuardedCoroutine).get("__eq__") is not native_eq
                or resume.__code__ is not resume_pin[0]
                or resume.__globals__ is not resume_pin[1]
                or resume.__defaults__ is not resume_pin[2]
                or resume.__kwdefaults__ is not resume_pin[3]
                or retire.__code__ is not retire_pin[0]
                or retire.__globals__ is not retire_pin[1]
                or retire.__defaults__ is not retire_pin[2]
                or retire.__kwdefaults__ is not retire_pin[3]
            ):
                state = registry.get(id(self))
                if type(state) is dict:
                    state["closed"] = True
                    unknown[state["token"]] = state
                raise NativeSourceClosureError("native_continuation_source_changed")
            state = resume(self, registry, retire)
            try:
                return state["body"].throw(*args)
            except BaseException:
                state["closed"] = True
                state["finalizer"].detach()
                registry.pop(id(self), None)
                raise

        def close(self):
            state = registry.get(id(self))
            if state is None or state["closed"]:
                return
            if (
                type(self) is not _GuardedCoroutine
                or vars(_GuardedCoroutine).get("__hash__") is not native_hash
                or vars(_GuardedCoroutine).get("__eq__") is not native_eq
                or resume.__code__ is not resume_pin[0]
                or resume.__globals__ is not resume_pin[1]
                or resume.__defaults__ is not resume_pin[2]
                or resume.__kwdefaults__ is not resume_pin[3]
                or retire.__code__ is not retire_pin[0]
                or retire.__globals__ is not retire_pin[1]
                or retire.__defaults__ is not retire_pin[2]
                or retire.__kwdefaults__ is not retire_pin[3]
            ):
                state["closed"] = True
                unknown[state["token"]] = state
                raise NativeSourceClosureError("native_continuation_source_changed")
            try:
                resume(self, registry, retire)
            finally:
                retire(self, registry)

    return _GuardedCoroutine


_GuardedCoroutine = _make_guarded_coroutine_type(
    _COROUTINES, _resume_coroutine_state, _retire_coroutine_state
)


def _bind_entry(module, name, original, guard, files):
    original_pin = _function_pin(original, files)
    snapshot = _guard_snapshot(guard)
    checked, checked_code = _check_guard, _check_guard.__code__
    check_function, check_function_code = _check_function, _check_function.__code__
    coroutine_type, coroutine_init, coroutine_send = (
        _GuardedCoroutine,
        _GuardedCoroutine.__init__,
        _GuardedCoroutine.send,
    )

    def entry(*args, **kwargs):
        if (
            vars(module).get(name) is not entry
            or checked.__code__ is not checked_code
            or check_function.__code__ is not check_function_code
            or _check_guard is not checked
            or _check_function is not check_function
            or _GuardedCoroutine is not coroutine_type
            or coroutine_type.__init__ is not coroutine_init
            or coroutine_type.send is not coroutine_send
        ):
            raise NativeSourceClosureError("native_continuation_source_changed")
        check_function(original_pin)
        checked(guard, snapshot)
        result = original(*args, **kwargs)
        if isinstance(result, Coroutine):
            return coroutine_type(result, guard, snapshot, checked)
        return result

    entry.__name__, entry.__module__ = original.__name__, original.__module__
    return entry


def install_native_guards_v1(module, exports, *, dependencies=(), bindings=()):
    """Install only from module source after definitions, never from caller IO."""
    if (
        type(module) is not ModuleType
        or type(exports) is not tuple
        or type(dependencies) is not tuple
        or not exports
        or type(bindings) is not tuple
        or any(type(name) is not str for name in exports)
        or any(type(item) is not ModuleType for item in dependencies)
    ):
        raise NativeSourceClosureError("native_continuation_source_changed")
    originals = tuple((name, vars(module)[name]) for name in exports)
    if any(type(value) is not FunctionType for _, value in originals):
        raise NativeSourceClosureError("native_continuation_source_changed")
    helper = sys.modules[__name__]
    files, records, functions, shapes = {}, [], [], []
    for namespace in (helper, _sqlalchemy_factory, module, *dependencies):
        own_records, own_functions, own_shapes = _namespace_pins(
            namespace, frozenset(exports) if namespace is module else frozenset(), files
        )
        records.extend(own_records)
        functions.extend(own_functions)
        shapes.extend(own_shapes)
    # Fixed trusted import-time assembly only. No trade API accepts this table.
    # Direct class dictionaries keep a changed descriptor visible and avoid a
    # caller attribute callback or a freshly allocated bound-method identity.
    for binding in bindings:
        if (
            type(binding) is not tuple
            or len(binding) != 3
            or not (type(binding[0]) is ModuleType or isinstance(binding[0], type))
            or type(binding[1]) is not str
            or not binding[1].isidentifier()
            or len(binding[1]) > 128
        ):
            raise NativeSourceClosureError("native_continuation_binding_unbound")
        owner, attribute, expected = binding
        actual = vars(owner).get(attribute)
        function = _function(actual)
        matched = actual is expected or (function is not None and function is expected)
        if type(expected) is MethodType:
            matched = (
                type(actual) is classmethod
                and actual.__func__ is expected.__func__
                and expected.__self__ is owner
            )
        if not matched or actual is None:
            raise NativeSourceClosureError("native_continuation_binding_unbound")
        records.append((owner, attribute, actual))
        if function is not None:
            functions.append(_function_pin(function, files))
    records, functions, shapes = tuple(records), tuple(functions), tuple(shapes)
    checker, checker_code = _check_function, _check_function.__code__
    path_type, reader, reader_code = Path, Path.read_bytes, Path.read_bytes.__code__
    path_mro = Path.__mro__
    reader_owner = next(cls for cls in path_mro if "read_bytes" in vars(cls))

    def guard():
        if (
            vars(module).get("_NATIVE_GUARD") is not guard
            or _check_function is not checker
            or checker.__code__ is not checker_code
            or Path is not path_type
            or path_type.__mro__ != path_mro
            or vars(reader_owner).get("read_bytes") is not reader
            or reader.__code__ is not reader_code
        ):
            raise NativeSourceClosureError("native_continuation_source_changed")
        for owner, name, value in records:
            if vars(owner).get(name) is not value:
                raise NativeSourceClosureError("native_continuation_source_changed")
        for pin in functions:
            checker(pin)
        for cls, fields, contents in shapes:
            if (
                vars(cls).get("model_fields") is not fields
                or tuple(fields.items()) != contents
            ):
                raise NativeSourceClosureError("native_continuation_source_changed")
        for path, digest in file_pins:
            try:
                raw = reader(path)
            except OSError:
                raise NativeSourceClosureError("native_continuation_source_changed")
            if sha(raw) != digest:
                raise NativeSourceClosureError("native_continuation_source_changed")

    # Bootstrap the checker/entry code from its exact defining source. Compiling
    # source here does not execute a source module or adopt a foreign namespace.
    _source_code(guard, Path(__file__).read_bytes(), __file__)
    file_pins = tuple(files.items())
    module._NATIVE_GUARD = guard
    for name, original in originals:
        vars(module)[name] = _bind_entry(module, name, original, guard, files)
    return guard
