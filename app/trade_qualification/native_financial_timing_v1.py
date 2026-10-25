"""Opt-in diagnostic source build. No trading authority or application clocks."""

import time as _time
import types as _types

ENABLED = True
PROFILE = "source-owned-timing-v1"
SCHEMA = "ctcc.native_financial_source_timing.v1"
LABELS = (
    "v3.register",
    "v3.calculate",
    "v3.replay",
    "v3.original_sources",
    "v3.final_fence",
    "inventory.v3",
    "inventory.financial",
    "inventory.writer",
)
MAX_INVOCATIONS = 65536
MAX_READS = 1 << 24
MAX_TOTAL_PUBLIC_BYTES = 1 << 34
MAX_DEPTH = 16

# Aliases and their source-owned baseline exist before any native acquisition.
_TYPE, _LEN, _RANGE, _ALL, _ID = type, len, range, all, id
_INT, _STR, _BOOL, _LIST, _TUPLE = int, str, bool, list, tuple
_GET, _SET = object.__getattribute__, object.__setattr__
_NS, _NS_GET = globals(), dict.get
_NS_ID = id(_NS)
_CLOCK = _time.perf_counter_ns
_TIME_PIN = _time
_FN_TYPE, _CODE_TYPE, _MODULE_TYPE = (
    _types.FunctionType,
    _types.CodeType,
    _types.ModuleType,
)


class _State:
    __slots__ = (  # noqa: RUF023 -- preserve the source-owned captured slot order.
        "stack",
        "stats",
        "invalid",
        "last_tick",
        "invocations",
        "public_bytes",
    )

    def __init__(self):
        self.stack = []
        self.stats = [[0, 0, 0, 0, 0] for _ in LABELS]
        self.invalid = None
        self.last_tick = 0
        self.invocations = 0
        self.public_bytes = 0


_FACTORY = _State
_STATE = _FACTORY()
_STATE_PIN = _STATE
_FACTORY_PIN = _FACTORY
_FACTORY_INIT_PIN = _State.__init__
_FACTORY_INIT_CODE = _State.__init__.__code__
_CLOCK_PIN = _CLOCK
_SLOT_PINS = tuple((name, _State.__dict__[name]) for name in _State.__slots__)
_BUILTIN_PINS = tuple(
    (name, _NS[name])
    for name in (
        "_TYPE",
        "_LEN",
        "_RANGE",
        "_ALL",
        "_INT",
        "_STR",
        "_BOOL",
        "_LIST",
        "_TUPLE",
        "_GET",
        "_SET",
        "_NS_GET",
        "_ID",
    )
)
_DATA_PIN = (
    ENABLED,
    PROFILE,
    SCHEMA,
    LABELS,
    MAX_INVOCATIONS,
    MAX_READS,
    MAX_TOTAL_PUBLIC_BYTES,
    MAX_DEPTH,
)


def _valid_state():
    stack = _GET(_STATE, "stack")
    stats = _GET(_STATE, "stats")
    invalid = _GET(_STATE, "invalid")
    last = _GET(_STATE, "last_tick")
    invocations = _GET(_STATE, "invocations")
    public_bytes = _GET(_STATE, "public_bytes")
    if (
        _TYPE(stack) is not _LIST
        or _LEN(stack) > MAX_DEPTH
        or _TYPE(stats) is not _LIST
        or _LEN(stats) != 8
        or _TYPE(last) is not _INT
        or not 0 <= last < 1 << 63
        or _TYPE(invocations) is not _INT
        or not 0 <= invocations <= MAX_INVOCATIONS
        or _TYPE(public_bytes) is not _INT
        or not 0 <= public_bytes <= MAX_TOTAL_PUBLIC_BYTES
        or (
            invalid is not None
            and (
                _TYPE(invalid) is not _STR
                or invalid
                not in (
                    "CLOCK_READ_INVALID",
                    "LABEL_INVALID",
                    "BOUND_EXCEEDED",
                    "BALANCE_INVALID",
                    "PUBLIC_SOURCE_COUNTER_INVALID",
                )
            )
        )
    ):
        return False
    opened = [0] * 8
    for item in stack:
        if (
            _TYPE(item) is not _TUPLE
            or _LEN(item) != 2
            or _TYPE(item[0]) is not _INT
            or not 0 <= item[0] < 8
            or _TYPE(item[1]) is not _INT
            or not 0 <= item[1] <= last
        ):
            return False
        opened[item[0]] += 1
    starts, byte_count, reads = 0, 0, 0
    for index in _RANGE(8):
        row = stats[index]
        if (
            _TYPE(row) is not _LIST
            or _LEN(row) != 5
            or not _ALL(_TYPE(value) is _INT and value >= 0 for value in row)
            or not row[1] <= row[0] <= MAX_INVOCATIONS
            or row[0] - row[1] != opened[index]
            or row[2] >= MAX_INVOCATIONS * (1 << 63)
            or row[3] > MAX_READS
            or row[4] > MAX_TOTAL_PUBLIC_BYTES
            or (index < 5 and (row[3] != 0 or row[4] != 0))
        ):
            return False
        starts += row[0]
        reads += row[3]
        byte_count += row[4]
    return starts == invocations and byte_count == public_bytes and reads <= MAX_READS


def _self_pinned(
    _get=dict.get, _globals=globals, _type=type, _len=len, _any=any, _dict=dict
):
    # Identity checks precede calling the mutable module aliases. The outer V3
    # declared-data fence also pins every alias and these baseline tuples.
    if (
        _NS_GET is not _get
        or _NS is not _globals()
        or _type(_BUILTIN_PINS) is not _TUPLE
        or _len(_BUILTIN_PINS) != 13
        or _any(
            _type(item) is not _TUPLE or _len(item) != 2 or _type(item[0]) is not _STR
            for item in _BUILTIN_PINS
        )
    ):
        return False
    for name, original in (
        ("globals", _globals),
        ("type", _type),
        ("len", _len),
        ("any", _any),
        ("dict", _dict),
    ):
        if name in _NS and _get(_NS, name) is not original:
            return False
    for name, original in _BUILTIN_PINS:
        if _get(_NS, name) is not original:
            return False
    if (
        _type(_FACTORY) is not _type
        or _FACTORY is not _FACTORY_PIN
        or _State is not _FACTORY_PIN
    ):
        return False
    factory_dict = _State.__dict__
    factory_init = factory_dict.get("__init__")
    factory_slots = factory_dict.get("__slots__")
    if (
        _ID(_NS) != _NS_ID
        or _TYPE(_DATA_PIN) is not _TUPLE
        or _LEN(_DATA_PIN) != 8
        or _TYPE(_DATA_PIN[0]) is not _BOOL
        or _TYPE(_DATA_PIN[1]) is not _STR
        or _TYPE(_DATA_PIN[2]) is not _STR
        or _TYPE(_DATA_PIN[3]) is not _TUPLE
        or not _ALL(_TYPE(item) is _STR for item in _DATA_PIN[3])
        or not _ALL(_TYPE(item) is _INT for item in _DATA_PIN[4:])
        or _TYPE(ENABLED) is not _BOOL
        or ENABLED is not _DATA_PIN[0]
        or _TYPE(PROFILE) is not _STR
        or _TYPE(SCHEMA) is not _STR
        or _TYPE(LABELS) is not _TUPLE
        or not _ALL(_TYPE(label) is _STR for label in LABELS)
        or not _ALL(
            _TYPE(value) is _INT
            for value in (MAX_INVOCATIONS, MAX_READS, MAX_TOTAL_PUBLIC_BYTES, MAX_DEPTH)
        )
        or (
            ENABLED,
            PROFILE,
            SCHEMA,
            LABELS,
            MAX_INVOCATIONS,
            MAX_READS,
            MAX_TOTAL_PUBLIC_BYTES,
            MAX_DEPTH,
        )
        != _DATA_PIN
        or factory_init is not _FACTORY_INIT_PIN
        or factory_init.__code__ is not _FACTORY_INIT_CODE
        or _TYPE(_STATE) is not _FACTORY_PIN
        or _STATE is not _STATE_PIN
        or _CLOCK is not _CLOCK_PIN
        or _TYPE(_time) is not _MODULE_TYPE
        or _time is not _TIME_PIN
        or _get(_time.__dict__, "perf_counter_ns") is not _CLOCK_PIN
        or "__getattribute__" in factory_dict
        or "__setattr__" in factory_dict
        or "__new__" in factory_dict
        or _TYPE(_SLOT_PINS) is not _TUPLE
        or _LEN(_SLOT_PINS) != 6
        or not _ALL(
            _TYPE(item) is _TUPLE and _LEN(item) == 2 and _TYPE(item[0]) is _STR
            for item in _SLOT_PINS
        )
        or _TYPE(factory_slots) is not _TUPLE
        or not _ALL(_TYPE(item) is _STR for item in factory_slots)
        or factory_slots != _TUPLE(name for name, _descriptor in _SLOT_PINS)
        or not _ALL(
            factory_dict.get(name) is descriptor for name, descriptor in _SLOT_PINS
        )
        or _TYPE(_FUNCTION_PINS) is not _TUPLE
        or _LEN(_FUNCTION_PINS) != 8
        or not _ALL(
            _TYPE(item) is _TUPLE
            and _LEN(item) == 6
            and _TYPE(item[0]) is _STR
            and _TYPE(item[1]) is _FN_TYPE
            and _TYPE(item[2]) is _CODE_TYPE
            and _TYPE(item[3]) is _INT
            and (item[4] is None or _TYPE(item[4]) is _TUPLE)
            and item[5] is None
            for item in _FUNCTION_PINS
        )
    ):
        return False
    for name, function, code, namespace_id, defaults, kwdefaults in _FUNCTION_PINS:
        if (
            _NS_GET(_NS, name) is not function
            or function.__code__ is not code
            or function.__globals__ is not _NS
            or _ID(function.__globals__) != namespace_id
            or function.__defaults__ is not defaults
            or function.__kwdefaults__ is not kwdefaults
        ):
            return False
    return _valid_state()


def _tick():
    value = _CLOCK()
    if _TYPE(value) is not _INT or not _GET(_STATE, "last_tick") <= value < 1 << 63:
        _SET(_STATE, "invalid", "CLOCK_READ_INVALID")
        return None
    _SET(_STATE, "last_tick", value)
    return value


def _label_index(label):
    if _TYPE(label) is not _STR:
        return None
    for index in _RANGE(8):
        if label == LABELS[index]:
            return index
    return None


def begin(label, _guard=_self_pinned):
    if (
        _guard is not _SELF_CHECK_PIN
        or _guard.__code__ is not _SELF_CHECK_CODE
        or _guard.__defaults__ is not _SELF_CHECK_DEFAULTS
        or _guard.__kwdefaults__ is not None
        or _guard.__globals__ is not _NS
    ):
        return
    if not _guard() or not ENABLED or _GET(_STATE, "invalid") is not None:
        return
    index = _label_index(label)
    if index is None:
        _SET(_STATE, "invalid", "LABEL_INVALID")
        return
    if (
        _GET(_STATE, "invocations") >= MAX_INVOCATIONS
        or _LEN(_GET(_STATE, "stack")) >= MAX_DEPTH
    ):
        _SET(_STATE, "invalid", "BOUND_EXCEEDED")
        return
    stamp = _tick()
    if stamp is not None:
        _SET(_STATE, "invocations", _GET(_STATE, "invocations") + 1)
        _GET(_STATE, "stack").append((index, stamp))
        _GET(_STATE, "stats")[index][0] += 1


def end(label, _guard=_self_pinned):
    if (
        _guard is not _SELF_CHECK_PIN
        or _guard.__code__ is not _SELF_CHECK_CODE
        or _guard.__defaults__ is not _SELF_CHECK_DEFAULTS
        or _guard.__kwdefaults__ is not None
        or _guard.__globals__ is not _NS
    ):
        return
    if not _guard() or not ENABLED or _GET(_STATE, "invalid") is not None:
        return
    index = _label_index(label)
    stack = _GET(_STATE, "stack")
    if index is None or not stack or stack[-1][0] != index:
        _SET(_STATE, "invalid", "BALANCE_INVALID")
        return
    stamp = _tick()
    if stamp is not None:
        _index, started = stack.pop()
        row = _GET(_STATE, "stats")[index]
        row[1] += 1
        row[2] += stamp - started


def source_file(label, public_bytes, _guard=_self_pinned):
    if (
        _guard is not _SELF_CHECK_PIN
        or _guard.__code__ is not _SELF_CHECK_CODE
        or _guard.__defaults__ is not _SELF_CHECK_DEFAULTS
        or _guard.__kwdefaults__ is not None
        or _guard.__globals__ is not _NS
    ):
        return
    if not _guard() or not ENABLED or _GET(_STATE, "invalid") is not None:
        return
    index = _label_index(label)
    stack = _GET(_STATE, "stack")
    stats = _GET(_STATE, "stats")
    if (
        index is None
        or index < 5
        or _TYPE(public_bytes) is not _INT
        or not 0 <= public_bytes <= 4 * 1024 * 1024
        or not stack
        or stack[-1][0] != index
        or _GET(_STATE, "public_bytes") + public_bytes > MAX_TOTAL_PUBLIC_BYTES
        or stats[5][3] + stats[6][3] + stats[7][3] >= MAX_READS
    ):
        _SET(_STATE, "invalid", "PUBLIC_SOURCE_COUNTER_INVALID")
        return
    _SET(_STATE, "public_bytes", _GET(_STATE, "public_bytes") + public_bytes)
    stats[index][3] += 1
    stats[index][4] += public_bytes


def snapshot(_guard=_self_pinned):
    if (
        _guard is not _SELF_CHECK_PIN
        or _guard.__code__ is not _SELF_CHECK_CODE
        or _guard.__defaults__ is not _SELF_CHECK_DEFAULTS
        or _guard.__kwdefaults__ is not None
        or _guard.__globals__ is not _NS
    ):
        return {
            "schema": "ctcc.native_financial_source_timing.v1",
            "status": "UNAVAILABLE_SOURCE_OR_STATE_CHANGED",
        }
    if not _guard():
        return {
            "schema": "ctcc.native_financial_source_timing.v1",
            "status": "UNAVAILABLE_SOURCE_OR_STATE_CHANGED",
        }
    if not ENABLED:
        return {
            "schema": "ctcc.native_financial_source_timing.v1",
            "status": "DISABLED",
        }
    stack = _GET(_STATE, "stack")
    stats = _GET(_STATE, "stats")
    return {
        "schema": "ctcc.native_financial_source_timing.v1",
        "profile": PROFILE,
        "status": "INCOMPLETE"
        if _GET(_STATE, "invalid") is not None or stack
        else "CLOSED_SPANS_OBSERVED",
        "invalid_code": _GET(_STATE, "invalid"),
        "open_span_labels": _TUPLE(LABELS[index] for index, _started in stack),
        "spans": _TUPLE(
            {
                "label": LABELS[index],
                "starts": stats[index][0],
                "closed": stats[index][1],
                "closed_inclusive_elapsed_ns": stats[index][2],
                "public_source_reads": stats[index][3],
                "public_source_bytes": stats[index][4],
            }
            for index in _RANGE(8)
        ),
        "cpu_time_measured": False,
        "application_clocks_modified": False,
        "execution_authority": False,
    }


_FUNCTION_PINS = tuple(
    (
        name,
        function,
        function.__code__,
        _ID(function.__globals__),
        function.__defaults__,
        function.__kwdefaults__,
    )
    for name in (
        "_valid_state",
        "_self_pinned",
        "_tick",
        "_label_index",
        "begin",
        "end",
        "source_file",
        "snapshot",
    )
    for function in (_NS[name],)
)
_SELF_CHECK_PIN = _self_pinned
_SELF_CHECK_CODE = _self_pinned.__code__
_SELF_CHECK_DEFAULTS = _self_pinned.__defaults__
