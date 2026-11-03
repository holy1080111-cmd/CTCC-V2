"""One exact SQLAlchemy2.0.51 generated create_engine wrapper, never a permit.

Compile pinned public source as DATA; never execute a source module, factory,
warning callback or database operation. Other generated functions remain denied.
"""

import __future__

import builtins
import hashlib
import sys
from pathlib import Path
from types import CodeType, FunctionType, ModuleType

TAG = "ctcc.sqlalchemy.create_engine.2.0.51.v1"
# Separate exact source pins for the two locked platform wheels.
_SOURCES = (
    (
        "sqlalchemy.engine.create",
        ("engine", "create.py"),
        "49d840a1060f46f7c994e202f2c43b4b715b34e3bd35afe1006b334acc28b972"
        if sys.platform == "linux"
        else "1ef921e16b07fd6e0e00cab596aeb7f8899da21a2bc50a492c20f464437316e8",
    ),
    (
        "sqlalchemy.util.langhelpers",
        ("util", "langhelpers.py"),
        "73d604262679cc986a02f5f534798d97a41b9127382a6fd3524ec05fbc13669d"
        if sys.platform == "linux"
        else "d838f7779df7915b28751fb94a6eb854beea02fbd43f89abba845bddbb52d511",
    ),
    (
        "sqlalchemy.util.deprecations",
        ("util", "deprecations.py"),
        "15a5daca8b6fd89a08d945f02b1b8230c199f1e997d56d5d5f8910374e9b4666"
        if sys.platform == "linux"
        else "bc99136b948a0a7cf9e2ba766d87bfd33b4b935ece8bcfd1ca37a8410211e5ce",
    ),
)
_WRAPPER_SOURCE = (
    "def create_engine(url, **kwargs):\n    return target(fn, url, **kwargs)\n"
)
_TARGET_QUALNAME = "deprecated_params.<locals>.decorate.<locals>.warned"
_MESSAGE_ITEMS = (
    (
        "strategy",
        "The create_engine.strategy keyword is deprecated, and the only argument accepted is 'mock'; please use create_mock_engine() going forward.  For general customization of create_engine which may have been accomplished using strategies, see CreateEnginePlugin.",
    ),
    (
        "empty_in_strategy",
        'The create_engine.empty_in_strategy keyword is deprecated, and no longer has any effect.  All IN expressions are now rendered using the "expanding parameter" strategy which renders a set of boundexpressions, or an "empty set" SELECT, at statement executiontime.',
    ),
    (
        "implicit_returning",
        "The create_engine.implicit_returning parameter is deprecated and will be removed in a future release. ",
    ),
)
_VERSION_ITEMS = (
    ("strategy", "1.4"),
    ("empty_in_strategy", "1.4"),
    ("implicit_returning", "2.0"),
)
_EMPTY_CELL = object()


class SqlAlchemyFactoryClosureError(ValueError):
    pass


def _need(condition):
    if condition is not True:
        raise SqlAlchemyFactoryClosureError(
            "native_sqlalchemy_factory_unbound"
        ) from None


def _module(name):
    value = sys.modules.get(name)
    _need(
        type(value) is ModuleType
        and type(vars(value).get("__name__")) is str
        and vars(value)["__name__"] == name
    )
    return value


def _basis():
    package, engine = _module("sqlalchemy"), _module("sqlalchemy.engine")
    _need(
        type(vars(package).get("__version__")) is str
        and vars(package)["__version__"] == "2.0.51"
        and type(vars(package).get("__file__")) is str
    )
    root = Path(vars(package)["__file__"]).resolve().parent
    rows = []
    for name, parts, digest in _SOURCES:
        module = _module(name)
        _need(type(vars(module).get("__file__")) is str)
        path = Path(vars(module)["__file__"]).resolve()
        _need(path == root.joinpath(*parts))
        raw = path.read_bytes()
        _need(hashlib.sha256(raw).hexdigest() == digest)
        rows.append((module, vars(module), path, digest, raw))
    return package, engine, tuple(rows)


def _codes(raw, filename, qualname):
    pending = [compile(raw, filename, "exec", dont_inherit=True)]
    result = []
    while pending:
        code = pending.pop()
        if code.co_qualname == qualname:
            result.append(code)
        pending.extend(value for value in code.co_consts if type(value) is CodeType)
    return tuple(result)


def _function(function, module, raw, path, qualname):
    from app.trade_qualification import native_continuation_guard_v1 as guard

    _need(
        type(function) is FunctionType
        and function.__globals__ is vars(module)
        and function.__builtins__ is builtins.__dict__
        and function.__code__.co_qualname == qualname
        and function.__defaults__ is None
        and function.__kwdefaults__ is None
    )
    expected = _codes(raw, str(path), qualname)
    _need(
        sum(
            guard._code_fields(code) == guard._code_fields(function.__code__)
            for code in expected
        )
        == 1
    )
    names = set(function.__code__.co_names)
    globals_pin = tuple(
        (name, vars(module)[name]) for name in sorted(names) if name in vars(module)
    )
    # Python resolves a bound module global before builtins. In particular the
    # genuine module __name__ must not also equal builtins.__name__. A later
    # shadow of a captured builtin is still rejected by require below.
    builtin_pin = tuple(
        (name, builtins.__dict__[name])
        for name in sorted(names)
        if name not in vars(module) and name in builtins.__dict__
    )
    return (
        function,
        function.__code__,
        module,
        vars(module),
        globals_pin,
        builtin_pin,
        function.__closure__,
        function.__defaults__,
        function.__kwdefaults__,
    )


def _cell(cell):
    try:
        return cell.cell_contents
    except ValueError:
        return _EMPTY_CELL


def _tree(value, depth=0):
    _need(depth <= 8)
    if value is _EMPTY_CELL:
        return ("empty",)
    if type(value) is str:
        return ("string", value)
    if type(value) is tuple:
        return ("tuple", value, tuple(_tree(item, depth + 1) for item in value))
    if type(value) is set:
        _need(all(type(item) is str for item in value))
        return ("set", value, tuple(sorted(value)))
    if type(value) is dict:
        _need(len(value) <= 8 and all(type(key) is str for key in value))
        return (
            "dict",
            value,
            tuple((key, _tree(item, depth + 1)) for key, item in sorted(value.items())),
        )
    warning = vars(_module("sqlalchemy.exc")).get("SADeprecationWarning")
    _need(type(warning) is type and value is warning)
    return ("warning_class", value)


def _tree_check(value, pin):
    kind = pin[0]
    if kind == "empty":
        _need(value is _EMPTY_CELL)
    elif kind == "string":
        _need(type(value) is str and value == pin[1])
    elif kind == "warning_class":
        _need(value is pin[1])
    elif kind == "set":
        _need(
            type(value) is set
            and value is pin[1]
            and all(type(item) is str for item in value)
            and tuple(sorted(value)) == pin[2]
        )
    elif kind == "tuple":
        _need(type(value) is tuple and value is pin[1] and len(value) == len(pin[2]))
        for actual, expected in zip(value, pin[2], strict=True):
            _tree_check(actual, expected)
    elif kind == "dict":
        _need(
            type(value) is dict
            and value is pin[1]
            and all(type(key) is str for key in value)
            and tuple(sorted(value)) == tuple(key for key, _ in pin[2])
        )
        for key, expected in pin[2]:
            _tree_check(value[key], expected)
    else:
        _need(False)


def capture_exact_create_engine_v1(function):
    from app.trade_qualification import native_continuation_guard_v1 as guard

    warning = vars(_module("sqlalchemy.exc")).get("SADeprecationWarning")
    _need(type(warning) is type)
    package, engine, rows = _basis()
    create, _, create_path, _, create_raw = rows[0]
    deprecations, _, deprecations_path, _, deprecations_raw = rows[2]
    _need(
        type(function) is FunctionType
        and vars(package).get("create_engine") is function
        and vars(engine).get("create_engine") is function
        and vars(create).get("create_engine") is function
        and function.__code__.co_filename == "<string>"
        and function.__closure__ is None
        and function.__defaults__ is None
        and function.__kwdefaults__ is None
        and function.__builtins__ is builtins.__dict__
    )
    namespace = function.__globals__
    _need(
        type(namespace) is dict
        and set(namespace)
        == {"__builtins__", "__name__", "create_engine", "fn", "target"}
        and namespace["__builtins__"] is builtins.__dict__
        and type(namespace["__name__"]) is str
        and namespace["__name__"] == "sqlalchemy.engine.create"
        and namespace["create_engine"] is function
    )
    expected = compile(
        _WRAPPER_SOURCE,
        "<string>",
        "exec",
        flags=__future__.annotations.compiler_flag,
        dont_inherit=True,
    )
    codes = [value for value in expected.co_consts if type(value) is CodeType]
    _need(
        len(codes) == 1
        and guard._code_fields(codes[0]) == guard._code_fields(function.__code__)
    )
    original, target = namespace["fn"], namespace["target"]
    _need(
        type(original) is FunctionType
        and type(target) is FunctionType
        and vars(function).get("__wrapped__") is original
        and original.__closure__ is None
    )
    original_pin = _function(original, create, create_raw, create_path, "create_engine")
    target_pin = _function(
        target, deprecations, deprecations_raw, deprecations_path, _TARGET_QUALNAME
    )
    _need(type(target.__closure__) is tuple and len(target.__closure__) == 7)
    values = dict(
        zip(
            target.__code__.co_freevars,
            (_cell(cell) for cell in target.__closure__),
            strict=True,
        )
    )
    _need(
        set(values)
        == {
            "check_any_kw",
            "check_defaults",
            "check_kw",
            "defaults",
            "messages",
            "version_warnings",
            "versions",
        }
        and type(values["check_any_kw"]) is str
        and values["check_any_kw"] == "kwargs"
        and type(values["check_defaults"]) is tuple
        and values["check_defaults"] == ()
        and type(values["check_kw"]) is set
        and all(type(item) is str for item in values["check_kw"])
        and values["check_kw"] == set(dict(_MESSAGE_ITEMS))
        and values["defaults"] is _EMPTY_CELL
        and type(values["messages"]) is dict
        and all(
            type(key) is str and type(item) is str
            for key, item in values["messages"].items()
        )
        and values["messages"] == dict(_MESSAGE_ITEMS)
        and type(values["versions"]) is dict
        and all(
            type(key) is str and type(item) is str
            for key, item in values["versions"].items()
        )
        and values["versions"] == dict(_VERSION_ITEMS)
        and type(values["version_warnings"]) is dict
        and all(type(key) is str for key in values["version_warnings"])
        and set(values["version_warnings"]) == set(dict(_MESSAGE_ITEMS))
        and all(item is warning for item in values["version_warnings"].values())
    )
    cells = tuple(
        (name, cell, _tree(_cell(cell)))
        for name, cell in zip(
            target.__code__.co_freevars, target.__closure__, strict=True
        )
    )
    namespace_pin = tuple(sorted(namespace.items()))
    pin = (
        TAG,
        function,
        function.__code__,
        namespace,
        namespace_pin,
        original_pin,
        target_pin,
        cells,
        tuple(
            (module, module_namespace, path, digest)
            for module, module_namespace, path, digest, _ in rows
        ),
        (package, vars(package), engine, vars(engine)),
    )
    require_exact_create_engine_v1(pin)
    return pin


def require_exact_create_engine_v1(pin):
    _need(
        type(pin) is tuple and len(pin) == 10 and type(pin[0]) is str and pin[0] == TAG
    )
    (
        _,
        function,
        code,
        namespace,
        namespace_pin,
        original_pin,
        target_pin,
        cells,
        rows,
        aliases,
    ) = pin
    package, package_namespace, engine, engine_namespace = aliases
    _need(
        _module("sqlalchemy") is package
        and vars(package) is package_namespace
        and _module("sqlalchemy.engine") is engine
        and vars(engine) is engine_namespace
        and type(package_namespace.get("__version__")) is str
        and package_namespace["__version__"] == "2.0.51"
    )
    _need(
        type(function) is FunctionType
        and function.__code__ is code
        and function.__globals__ is namespace
        and function.__builtins__ is builtins.__dict__
        and function.__closure__ is None
        and function.__defaults__ is None
        and function.__kwdefaults__ is None
        and all(type(key) is str for key in namespace)
        and tuple(sorted(namespace)) == tuple(key for key, _ in namespace_pin)
    )
    _need(
        all(namespace[key] is expected for key, expected in namespace_pin)
        and vars(function).get("__wrapped__") is original_pin[0]
        and _module("sqlalchemy").__dict__.get("create_engine") is function
        and _module("sqlalchemy.engine").__dict__.get("create_engine") is function
        and _module("sqlalchemy.engine.create").__dict__.get("create_engine")
        is function
    )
    for item in (original_pin, target_pin):
        (
            value,
            expected_code,
            module,
            module_namespace,
            globals_pin,
            builtin_pin,
            closure,
            defaults,
            kwdefaults,
        ) = item
        _need(
            value.__code__ is expected_code
            and value.__globals__ is module_namespace is vars(module)
            and value.__builtins__ is builtins.__dict__
            and value.__closure__ is closure
            and value.__defaults__ is defaults
            and value.__kwdefaults__ is kwdefaults
            and sys.modules.get(vars(module)["__name__"]) is module
        )
        _need(
            all(
                module_namespace.get(name) is expected for name, expected in globals_pin
            )
            and all(
                builtins.__dict__.get(name) is expected
                and module_namespace.get(name, expected) is expected
                for name, expected in builtin_pin
            )
        )
    target = target_pin[0]
    _need(
        len(target.__closure__) == len(cells)
        and tuple(target.__code__.co_freevars) == tuple(name for name, _, _ in cells)
    )
    for actual, (_, expected, contents) in zip(target.__closure__, cells, strict=True):
        _need(actual is expected)
        _tree_check(_cell(actual), contents)
    for module, module_namespace, path, digest in rows:
        _need(
            vars(module) is module_namespace
            and type(module_namespace.get("__file__")) is str
            and Path(module_namespace["__file__"]).resolve() == path
            and hashlib.sha256(path.read_bytes()).hexdigest() == digest
        )
