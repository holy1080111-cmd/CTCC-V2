"""Exact physical58 function sources; observed SQL never supplies a baseline."""

from types import MappingProxyType

from app.domain import native_live_ledger_schema_v1 as ancestor
from app.domain import native_live_lifecycle_schema_v1 as lifecycle


def live_function_statements_v58():
    functions = {}
    for sql in ancestor.UPGRADE_SQL:
        if sql.startswith("CREATE FUNCTION public."):
            name = sql.split("public.", 1)[1].split("(", 1)[0]
            if name in functions:
                raise ValueError("native_live_function_source_changed")
            functions[name] = sql
    overrides = lifecycle.live_lifecycle_function_overrides_v1()
    if set(functions) != {
        "native_live_control_fence_v1",
        "native_live_inhibit_v1",
        "native_live_disarm_v1",
        "native_live_reservation_guard_v1",
        "native_live_data_guard_v1",
    } or set(overrides) != {"native_live_reservation_guard_v1"}:
        raise ValueError("native_live_function_source_changed")
    original = functions["native_live_reservation_guard_v1"]
    restored = lifecycle.live_lifecycle_ancestor_reservation_guard_v1()
    if restored.replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1) != original:
        raise ValueError("native_live_function_ancestor_changed")
    functions.update(overrides)
    return MappingProxyType(functions)


def live_function_metadata_v58(control_type_oid):
    if type(control_type_oid) is not int or control_type_oid <= 0:
        raise ValueError("native_live_function_return_type_unknown")
    # strict, volatility, parallel, support, language, args, argument OIDs,
    # returns_set, return OID, defaults, variadic, argument modes/all types.
    return MappingProxyType(
        {
            name: (
                False,
                "v",
                "u",
                0,
                "plpgsql",
                count,
                " ".join(["25"] * count),
                name == "native_live_control_fence_v1",
                result,
                0,
                0,
                None,
                None,
            )
            for name, count, result in (
                ("native_live_control_fence_v1", 2, control_type_oid),
                ("native_live_inhibit_v1", 2, 20),
                ("native_live_disarm_v1", 3, 20),
                ("native_live_reservation_guard_v1", 0, 2279),
                ("native_live_data_guard_v1", 0, 2279),
            )
        }
    )
