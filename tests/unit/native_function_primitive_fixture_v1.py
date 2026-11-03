"""Extract real defining code for explicit foreign-namespace DATA primitives.

Never disable or alter a product guard. The resulting clone cannot satisfy
the actual defining-namespace guard and is never a native Source/cap/permit.
"""

from types import FunctionType

from app.trade_qualification import native_continuation_guard_v1 as guard


def foreign_primitive_v1(function, overrides):
    actual = function
    # Only the existing pristine fixed guard wrapper is unwrapped. Verify its
    # captured original function/pin, rather than executing the wrapper with
    # foreign globals or treating mutable __wrapped__ metadata as authority.
    if function.__code__.co_qualname == "_bind_entry.<locals>.entry":
        guard._check_function(guard._function_pin(function, {}))
        cells = dict(
            zip(function.__code__.co_freevars, function.__closure__, strict=True)
        )
        actual = cells["original"].cell_contents
        pin = cells["original_pin"].cell_contents
        if type(actual) is not FunctionType or pin[0] is not actual:
            raise ValueError("primitive_original_function_unbound")
        guard._check_function(pin)
    guard._check_function(guard._function_pin(actual, {}))
    namespace = dict(actual.__globals__)
    namespace.update(overrides)
    clone = FunctionType(
        actual.__code__,
        namespace,
        actual.__name__,
        actual.__defaults__,
        actual.__closure__,
    )
    clone.__kwdefaults__ = (
        None if actual.__kwdefaults__ is None else dict(actual.__kwdefaults__)
    )
    return clone
