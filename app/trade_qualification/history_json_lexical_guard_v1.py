"""Finite source-derived custody for the historical bundle JSON parser only.

No data source, owner, clock, lease, cache, execution permission or retry is
created here. Expected code comes from manifest-verified saved source. This is
held external source preparation and has not been imported or admitted.
"""

import ast
import builtins
import hashlib
import json
import re
import stat
import sys
from pathlib import Path
from types import CodeType, FunctionType, MethodDescriptorType, ModuleType

SCHEMA = "ctcc.history_json_lexical_source_guard.v1"
GUARD_PATH = "app/trade_qualification/history_json_lexical_guard_v1.py"
BUNDLE_PATH = "app/trade_qualification/account_history_source_bundle_v1.py"
PATTERN_BYTES = rb'["\\{}\[\],:]'
PATTERN = re.compile(PATTERN_BYTES)
PATTERN_TYPE = type(PATTERN)
MATCH_TYPE = re.Match
FINDITER = PATTERN_TYPE.finditer
MATCH_START = MATCH_TYPE.start
_ROOT = Path(__file__).resolve(strict=True).parents[2]
_NAMESPACE = globals()


class HistoryJsonSourceGuardError(ValueError):
    """A fixed public refusal, without source payload or foreign exception text."""


_FILE_IDENTITY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_nlink",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


def _file_identity(info):
    return tuple(getattr(info, key) for key in _FILE_IDENTITY_FIELDS)


def _read_manifest():
    path = _ROOT / "MANIFEST.sha256"
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or getattr(info, "st_file_attributes", 0) & 1024
        or not 0 < info.st_size <= 2 * 1024 * 1024
    ):
        raise HistoryJsonSourceGuardError("history_json_guard_manifest_invalid")
    raw = path.read_bytes()
    identity = _file_identity(info)
    if len(raw) != info.st_size or _file_identity(path.lstat()) != identity:
        raise HistoryJsonSourceGuardError("history_json_guard_manifest_changed")
    return raw.splitlines(), hashlib.sha256(raw).hexdigest(), identity


def _read_source_bytes(relative, manifest=None):
    lines, manifest_digest, _identity = (
        _read_manifest() if manifest is None else manifest
    )
    path = _ROOT
    for part in relative.split("/"):
        path /= part
        before = path.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or getattr(before, "st_file_attributes", 0) & 1024
        ):
            raise HistoryJsonSourceGuardError("history_json_guard_source_path_changed")
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or not 0 < before.st_size <= 2 * 1024 * 1024
    ):
        raise HistoryJsonSourceGuardError("history_json_guard_source_invalid")
    raw = path.read_bytes()
    if _file_identity(before) != _file_identity(path.lstat()):
        raise HistoryJsonSourceGuardError("history_json_guard_source_changed")
    suffix = b"  " + relative.encode()
    source_digest = hashlib.sha256(raw).hexdigest()
    expected = source_digest.encode() + suffix
    matches = [line for line in lines if line.endswith(suffix)]
    if matches != [expected] or len(raw) != before.st_size:
        raise HistoryJsonSourceGuardError("history_json_guard_manifest_mismatch")
    return raw, path, source_digest, manifest_digest


def _source(relative):
    raw, path, source_digest, manifest_digest = _read_source_bytes(relative)
    parsed = ast.parse(raw, filename=str(path))
    code = compile(raw, str(path), "exec", dont_inherit=True, optimize=0)
    return (
        parsed,
        {part.co_qualname: part for part in code.co_consts if type(part) is CodeType},
        (relative, source_digest, manifest_digest),
    )


def _current_sources():
    manifest = _read_manifest()
    for relative, original_digest, original_manifest in _SOURCE_DIGESTS:
        _raw, _path, source_digest, manifest_digest = _read_source_bytes(
            relative, manifest
        )
        if source_digest != original_digest or manifest_digest != original_manifest:
            raise ValueError("history_json_guard_saved_source_changed")
    if _file_identity((_ROOT / "MANIFEST.sha256").lstat()) != manifest[2]:
        raise HistoryJsonSourceGuardError("history_json_guard_manifest_changed")


def _lookups(code):
    names = set(code.co_names)
    for value in code.co_consts:
        if type(value) is CodeType:
            names.update(name for name, _value in _lookups(value))
    # __name__ is defining-module metadata, separately pinned in _REFERENCES
    # and checked through the fixed defining-module namespace before dispatch.
    return tuple(
        (name, builtins.__dict__[name])
        for name in sorted(names)
        if name != "__name__" and name in builtins.__dict__
    )


def _verify(
    function,
    code,
    namespace,
    defaults,
    keywords,
    lookups,
    _type=type,
    _get=dict.get,
    _error=ValueError,
):
    if (
        _type(function) is not FunctionType
        or function.__code__ != code
        or function.__code__.co_filename != code.co_filename
        or function.__code__.co_qualname != code.co_qualname
        or function.__globals__ is not namespace
        or function.__builtins__ is not builtins.__dict__
        or function.__closure__ is not None
        or code.co_freevars != ()
    ):
        raise _error("history_json_guard_callable_changed")
    if function.__defaults__ is not defaults or function.__kwdefaults__ is not keywords:
        raise _error("history_json_guard_defaults_changed")
    for name, original in lookups:
        if (
            _get(namespace, name, original) is not original
            or _get(builtins.__dict__, name) is not original
        ):
            raise _error("history_json_guard_builtin_changed")


def _regex(_type=type, _error=ValueError):
    if (
        _type(_REGEX_REFERENCES) is not tuple
        or len(_REGEX_REFERENCES) != 5
        or _type(PATTERN_BYTES) is not bytes
        or _type(PATTERN) is not PATTERN_TYPE
        or PATTERN_TYPE is not _REGEX_REFERENCES[0]
        or MATCH_TYPE is not _REGEX_REFERENCES[1]
        or _type(FINDITER) is not MethodDescriptorType
        or _type(MATCH_START) is not MethodDescriptorType
        or FINDITER is not _REGEX_REFERENCES[2]
        or MATCH_START is not _REGEX_REFERENCES[3]
        or FINDITER is not PATTERN_TYPE.finditer
        or FINDITER.__objclass__ is not PATTERN_TYPE
        or MATCH_START is not MATCH_TYPE.start
        or MATCH_START.__objclass__ is not MATCH_TYPE
        or PATTERN is not _REGEX_REFERENCES[4]
        or PATTERN.pattern != rb'["\\{}\[\],:]'
        or PATTERN.flags != 0
        or PATTERN.groups != 0
        or PATTERN.groupindex != {}
        or PATTERN_BYTES != rb'["\\{}\[\],:]'
        or re.Pattern is not PATTERN_TYPE
        or re.Match is not MATCH_TYPE
    ):
        raise _error("history_json_guard_regex_changed")


def require(
    _type=type, _get=dict.get, _namespace=_NAMESPACE, _error=ValueError, _len=len
):
    # The caller verifies this exact code/default tuple before invoking it.
    # Only original builtin aliases are used before checking module lookups.
    if (
        _type(BINDING) is not _type(())
        or _len(BINDING) != 8
        or _type(_ENTRY_DEFAULTS) is not _type(())
        or _type(require) is not _FUNCTION_TYPE
        or require.__defaults__ is not _ENTRY_DEFAULTS
        or _namespace is not _NAMESPACE
        or _type is not _ENTRY_DEFAULTS[0]
        or _get is not _ENTRY_DEFAULTS[1]
        or _namespace is not _ENTRY_DEFAULTS[2]
        or _error is not _ENTRY_DEFAULTS[3]
        or _len is not _ENTRY_DEFAULTS[4]
        or _type(_REFERENCES) is not _type(())
        or _REFERENCES is not _REFERENCES_PIN
        or _type(_OWN_PROFILES) is not _type(())
        or _OWN_PROFILES is not _OWN_PROFILES_PIN
    ):
        raise _error("history_json_guard_entry_changed")
    for name, original in _REFERENCES:
        if _get(_namespace, name) is not original:
            raise _error("history_json_guard_global_changed")
    loaded = _get(sys.modules, "app.trade_qualification.history_json_lexical_guard_v1")
    if _type(loaded) is not ModuleType or loaded.__dict__ is not _namespace:
        raise _error("history_json_guard_defining_namespace_changed")
    for name, function, code, defaults, keywords, lookups in _OWN_PROFILES:
        if _get(_namespace, name) is not function:
            raise _error("history_json_guard_function_replaced")
        # Inline function/code/namespace/default checks precede helper calls.
        if (
            _type(function) is not _FUNCTION_TYPE
            or function.__code__ != code
            or function.__code__.co_filename != code.co_filename
            or function.__code__.co_qualname != code.co_qualname
            or function.__globals__ is not _namespace
            or function.__builtins__ is not _BUILTINS
            or function.__closure__ is not None
            or function.__defaults__ is not defaults
            or function.__kwdefaults__ is not keywords
        ):
            raise _error("history_json_guard_function_changed")
        for lookup, original in lookups:
            if (
                _get(_namespace, lookup, original) is not original
                or _get(_BUILTINS, lookup) is not original
            ):
                raise _error("history_json_guard_builtin_changed")
    error_class = _get(_namespace, "HistoryJsonSourceGuardError")
    if (
        _type(error_class) is not _type
        or error_class.__bases__ != (ValueError,)
        or any(
            name in error_class.__dict__
            for name in (
                "__new__",
                "__init__",
                "__getattribute__",
                "__str__",
                "__repr__",
            )
        )
    ):
        raise _error("history_json_guard_error_type_changed")
    _current_sources()
    _regex()
    if (
        _type(_BUNDLE_PROFILE) is not _type(())
        or _len(_BUNDLE_PROFILE) != 4
        or _BUNDLE_PROFILE is not _BUNDLE_PROFILE_PIN
    ):
        raise _error("history_json_guard_bundle_not_bound")
    namespace, profiles, aliases, expected_maximum = _BUNDLE_PROFILE
    defining_bundle = sys.modules[
        "app.trade_qualification.account_history_source_bundle_v1"
    ]
    if (
        _type(defining_bundle) is not ModuleType
        or namespace is not defining_bundle.__dict__
    ):
        raise _error("history_json_guard_bundle_namespace_changed")
    for name, function, code, defaults, keywords, lookups in profiles:
        if _get(namespace, name) is not function:
            raise _error("history_json_guard_bundle_function_replaced")
        _verify(function, code, namespace, defaults, keywords, lookups)
        if name == "_json":
            actual_keywords = function.__kwdefaults__
            if (
                _type(actual_keywords) is not _type({})
                or _len(actual_keywords) != 3
                or _get(actual_keywords, "_json_guard") is not BINDING[0]
                or _get(actual_keywords, "_json_binding") is not BINDING
                or _get(actual_keywords, "_json_module") is not sys.modules[__name__]
            ):
                raise _error("history_json_guard_bundle_defaults_changed")
    for name, original in aliases:
        if _get(namespace, name) is not original:
            raise _error("history_json_guard_bundle_alias_changed")
    if (
        _type(_get(namespace, "MAX_BUNDLE_BYTES")) is not _type(0)
        or _get(namespace, "MAX_BUNDLE_BYTES") != expected_maximum
    ):
        raise _error("history_json_guard_original_limit_changed")
    for name, expected in (
        ("MAX_JSON_DEPTH", 32),
        ("MAX_JSON_NODES", 262144),
        ("MAX_TIMESTAMP_NS", 32_503_680_000_000_000_000),
    ):
        actual = _get(namespace, name)
        if _type(actual) is not _type(expected) or actual != expected:
            raise _error("history_json_guard_original_limit_changed")


def bind_bundle(
    namespace, _type=type, _get=dict.get, _len=len, _tuple=tuple, _error=ValueError
):
    """One source-derived registration at defining-module initialization only."""
    global _BUNDLE_PROFILE, _BUNDLE_PROFILE_PIN
    if (
        _type(_NAMESPACE) is not _type({})
        or _type(_REFERENCES) is not _type(())
        or _REFERENCES is not _REFERENCES_PIN
    ):
        raise _error("history_json_guard_binding_globals_changed")
    for name, original in _REFERENCES:
        if _get(_NAMESPACE, name) is not original:
            raise _error("history_json_guard_binding_globals_changed")
    if _BUNDLE_PROFILE is not None or _BUNDLE_PROFILE_PIN is not None:
        raise _error("history_json_guard_rebinding_forbidden")
    defining_bundle = sys.modules[
        "app.trade_qualification.account_history_source_bundle_v1"
    ]
    if (
        _type(defining_bundle) is not ModuleType
        or namespace is not defining_bundle.__dict__
    ):
        raise _error("history_json_guard_defining_namespace_required")
    # Expected bundle code was compiled from verified source before import could
    # expose any mutable parser. No current leaf/code baseline is captured here.
    codes = _BUNDLE_CODES
    checker, expected_checker, checker_defaults = _VERIFY_PROFILE
    if (
        checker is not _verify
        or checker.__code__ != expected_checker
        or checker.__code__.co_filename != expected_checker.co_filename
        or checker.__globals__ is not _NAMESPACE
        or checker.__builtins__ is not _BUILTINS
        or checker.__defaults__ is not checker_defaults
        or checker.__kwdefaults__ is not None
        or checker.__closure__ is not None
    ):
        raise _error("history_json_guard_verifier_changed")
    profiles = []
    for position, name in ((0, "_deny"), (1, "_json")):
        function = namespace[name]
        if (
            _type(function) is not _FUNCTION_TYPE
            or function.__globals__ is not namespace
        ):
            raise _error("history_json_guard_bundle_function_changed")
        actual_defaults = function.__defaults__
        if _type(actual_defaults) is not _type(()) or _len(actual_defaults) != 1:
            raise _error("history_json_guard_source_defaults_changed")
        if name == "_deny":
            if (
                _type(actual_defaults[0]) is not _type("")
                or actual_defaults[0] != "history_source_bundle_invalid"
                or function.__kwdefaults__ is not None
            ):
                raise _error("history_json_guard_source_defaults_changed")
        else:
            if (
                _type(actual_defaults[0]) is not _type(0)
                or actual_defaults[0] != _EXPECTED_MAXIMUM
                or _type(namespace["MAX_BUNDLE_BYTES"]) is not _type(0)
                or namespace["MAX_BUNDLE_BYTES"] != _EXPECTED_MAXIMUM
            ):
                raise _error("history_json_guard_source_defaults_changed")
            keywords = function.__kwdefaults__
            if (
                _type(keywords) is not _type({})
                or _len(keywords) != 3
                or _get(keywords, "_json_guard") is not require
                or _get(keywords, "_json_binding") is not BINDING
                or _get(keywords, "_json_module") is not sys.modules[__name__]
            ):
                raise _error("history_json_guard_source_defaults_changed")
        code = codes[position]
        defaults, keywords = function.__defaults__, function.__kwdefaults__
        checker(
            function, code, namespace, defaults, keywords, _BUNDLE_LOOKUPS[position]
        )
        profiles.append(
            (name, function, code, defaults, keywords, _BUNDLE_LOOKUPS[position])
        )
    aliases = (
        ("_JSON_SPECIAL_PATTERN", PATTERN),
        ("_JSON_SPECIAL_FINDITER", FINDITER),
        ("_JSON_MATCH_START", MATCH_START),
        ("_JSON_LEXICAL_BINDING", BINDING),
        ("_json_lexical_guard", sys.modules[__name__]),
        ("json", json),
        ("re", re),
    )
    _BUNDLE_PROFILE = (namespace, _tuple(profiles), aliases, _EXPECTED_MAXIMUM)
    _BUNDLE_PROFILE_PIN = _BUNDLE_PROFILE
    require()


_BUILTINS = builtins.__dict__
_FUNCTION_TYPE = FunctionType
_REGEX_REFERENCES = (PATTERN_TYPE, MATCH_TYPE, FINDITER, MATCH_START, PATTERN)
_BUNDLE_PROFILE = _BUNDLE_PROFILE_PIN = None
_parsed, _codes, _guard_source_pin = _source(GUARD_PATH)
_bundle_parsed, _bundle_codes, _bundle_source_pin = _source(BUNDLE_PATH)
_bundle_nodes = {
    node.name: node for node in _bundle_parsed.body if isinstance(node, ast.FunctionDef)
}
for _name in ("_deny", "_json"):
    _node = _bundle_nodes[_name]
    if (
        _node.decorator_list
        or _node.args.kwarg is not None
        or _node.args.vararg is not None
    ):
        raise HistoryJsonSourceGuardError(
            "history_json_guard_bundle_source_contract_changed"
        )
    if len(_node.args.defaults) != 1:
        raise HistoryJsonSourceGuardError(
            "history_json_guard_bundle_source_contract_changed"
        )
if ast.dump(
    _bundle_nodes["_deny"].args.defaults[0], include_attributes=False
) != ast.dump(
    ast.Constant(value="history_source_bundle_invalid"), include_attributes=False
):
    raise HistoryJsonSourceGuardError(
        "history_json_guard_bundle_source_contract_changed"
    )
if ast.dump(
    _bundle_nodes["_json"].args.defaults[0], include_attributes=False
) != ast.dump(
    ast.Name(id="MAX_BUNDLE_BYTES", ctx=ast.Load()), include_attributes=False
):
    raise HistoryJsonSourceGuardError(
        "history_json_guard_bundle_source_contract_changed"
    )
_json_keywords = _bundle_nodes["_json"].args
if [arg.arg for arg in _json_keywords.kwonlyargs] != [
    "_json_guard",
    "_json_binding",
    "_json_module",
] or [ast.unparse(value) for value in _json_keywords.kw_defaults] != [
    "_json_lexical_guard.require",
    "_json_lexical_guard.BINDING",
    "_json_lexical_guard",
]:
    raise HistoryJsonSourceGuardError(
        "history_json_guard_bundle_source_contract_changed"
    )
_maximum_source, _unused_codes, _maximum_source_pin = _source(
    "app/database/repositories/public_runtime_bundle_custody.py"
)
_maximum_nodes = [
    node.value
    for node in _maximum_source.body
    if isinstance(node, ast.Assign)
    and len(node.targets) == 1
    and isinstance(node.targets[0], ast.Name)
    and node.targets[0].id == "MAX_BUNDLE_BYTES"
]
if len(_maximum_nodes) != 1 or ast.dump(
    _maximum_nodes[0], include_attributes=False
) != ast.dump(
    ast.parse("64 * 1024 * 1024", mode="eval").body, include_attributes=False
):
    raise HistoryJsonSourceGuardError("history_json_guard_maximum_source_changed")
_EXPECTED_MAXIMUM = 64 * 1024 * 1024
_SOURCE_DIGESTS = (_guard_source_pin, _bundle_source_pin, _maximum_source_pin)
_BUNDLE_CODES = (_bundle_codes["_deny"], _bundle_codes["_json"])
_BUNDLE_LOOKUPS = tuple(_lookups(code) for code in _BUNDLE_CODES)
_OWN_DEFAULT_VALUES = {
    "_file_identity": None,
    "_read_manifest": None,
    "_read_source_bytes": (None,),
    "_source": None,
    "_current_sources": None,
    "_lookups": None,
    "_verify": (type, dict.get, ValueError),
    "_regex": (type, ValueError),
    "require": (type, dict.get, _NAMESPACE, ValueError, len),
    "bind_bundle": (type, dict.get, len, tuple, ValueError),
}
for _name, _expected_defaults in _OWN_DEFAULT_VALUES.items():
    _observed_defaults = _NAMESPACE[_name].__defaults__
    if _expected_defaults is None:
        if _observed_defaults is not None:
            raise HistoryJsonSourceGuardError("history_json_guard_own_defaults_changed")
    elif (
        type(_observed_defaults) is not tuple
        or len(_observed_defaults) != len(_expected_defaults)
        or any(
            left is not right
            for left, right in zip(_observed_defaults, _expected_defaults, strict=True)
        )
    ):
        raise HistoryJsonSourceGuardError("history_json_guard_own_defaults_changed")
    if _NAMESPACE[_name].__kwdefaults__ is not None:
        raise HistoryJsonSourceGuardError("history_json_guard_own_defaults_changed")
_ENTRY_DEFAULTS = require.__defaults__
_BINDER_DEFAULTS = bind_bundle.__defaults__
_REFERENCES = tuple(
    (name, _NAMESPACE[name])
    for name in (
        "ast",
        "builtins",
        "hashlib",
        "json",
        "Path",
        "re",
        "stat",
        "sys",
        "CodeType",
        "FunctionType",
        "_ROOT",
        "__file__",
        "__name__",
        "MethodDescriptorType",
        "ModuleType",
        "_FUNCTION_TYPE",
        "_BUILTINS",
        "_REGEX_REFERENCES",
        "_ENTRY_DEFAULTS",
        "_BINDER_DEFAULTS",
        "HistoryJsonSourceGuardError",
        "PATTERN_BYTES",
        "PATTERN",
        "PATTERN_TYPE",
        "MATCH_TYPE",
        "FINDITER",
        "MATCH_START",
        "_BUNDLE_CODES",
        "_BUNDLE_LOOKUPS",
        "_EXPECTED_MAXIMUM",
        "_SOURCE_DIGESTS",
        "_FILE_IDENTITY_FIELDS",
    )
)
_REFERENCES_PIN = _REFERENCES
_OWN_PROFILES = tuple(
    (
        name,
        _NAMESPACE[name],
        _codes[name],
        _NAMESPACE[name].__defaults__,
        _NAMESPACE[name].__kwdefaults__,
        _lookups(_codes[name]),
    )
    for name in (
        "_file_identity",
        "_read_manifest",
        "_read_source_bytes",
        "_source",
        "_current_sources",
        "_lookups",
        "_verify",
        "_regex",
        "require",
        "bind_bundle",
    )
)
_OWN_PROFILES_PIN = _OWN_PROFILES
for _name, _function, _code, _defaults, _keywords, _builtin_lookups in _OWN_PROFILES:
    _verify(_function, _code, _NAMESPACE, _defaults, _keywords, _builtin_lookups)
_VERIFY_PROFILE = (_verify, _codes["_verify"], _verify.__defaults__)
_REFERENCES = (*_REFERENCES, ("_VERIFY_PROFILE", _VERIFY_PROFILE))
_REFERENCES_PIN = _REFERENCES
# These ids retain their actual original targets strongly in _OWN_PROFILES.
# The exported finite tuple never contains a module namespace dictionary; V3's
# structural default encoder therefore does not recurse through entire modules.
BINDING = (
    require,
    _codes["require"],
    id(require.__defaults__),
    id(_NAMESPACE),
    id(_BUILTINS),
    bind_bundle,
    _codes["bind_bundle"],
    id(bind_bundle.__defaults__),
)
