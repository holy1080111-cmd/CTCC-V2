"""Original exact FIFO arithmetic, without candidate or account authority.

Source completeness, actual funding attribution and chronology are the caller's
separate obligations. An empty funding-time tuple proves no funding completeness.
"""

from datetime import UTC, datetime
from fractions import Fraction


class InventoryMathError(ValueError):
    """Only fixed arithmetic/input error codes."""


def _positive_native_fraction(value):
    """Read native slots before comparison or any other arithmetic."""
    if type(value) is not Fraction:
        return False
    try:
        numerator = object.__getattribute__(value, "_numerator")
        denominator = object.__getattribute__(value, "_denominator")
    except AttributeError:
        return False
    return (
        type(numerator) is int
        and type(denominator) is int
        and denominator > 0
        and numerator > 0
    )


def reduce_inventory(*, direction, unit, fills, funding_times):
    """Preserve forensics' ordered FIFO return values and static errors."""
    if (
        type(direction) is not str
        or direction not in {"long", "short"}
        or not _positive_native_fraction(unit)
        or type(fills) is not tuple
        or len(fills) > 512
        or type(funding_times) is not tuple
        or len(funding_times) > 1024
    ):
        raise InventoryMathError("inventory_math_input_invalid")
    for fill in fills:
        if type(fill) is not tuple or len(fill) != 5:
            raise InventoryMathError("inventory_math_input_invalid")
        role, side, contracts, price, at = fill
        if (
            type(role) is not str
            or role not in {"entry", "exit"}
            or type(side) is not str
            or side not in {"buy", "sell"}
            or not _positive_native_fraction(contracts)
            or not _positive_native_fraction(price)
            or type(at) is not datetime
            or at.tzinfo is not UTC
        ):
            raise InventoryMathError("inventory_math_input_invalid")
    if any(type(at) is not datetime or at.tzinfo is not UTC for at in funding_times):
        raise InventoryMathError("inventory_math_input_invalid")
    sign = 1 if direction == "long" else -1
    lots = []
    states = []
    realized = Fraction(0)
    entries = exits = entry_value = exit_value = Fraction(0)
    closed = False
    for role, side, quantity, price, occurred_at in fills:
        expected_side = "buy" if (direction == "long") == (role == "entry") else "sell"
        if side != expected_side:
            raise InventoryMathError("fill_side_role_direction_conflict")
        if role == "entry":
            if closed:
                raise InventoryMathError("same_report_position_reopened")
            lots.append([quantity, price])
            entries += quantity
            entry_value += quantity * price
        else:
            if quantity > entries - exits:
                raise InventoryMathError("exit_exceeds_known_inventory")
            exits += quantity
            exit_value += quantity * price
            while quantity:
                matched = min(quantity, lots[0][0])
                realized += sign * matched * unit * (price - lots[0][1])
                quantity -= matched
                lots[0][0] -= matched
                if not lots[0][0]:
                    lots.pop(0)
            closed = not lots
        states.append((occurred_at, realized, tuple((q, p) for q, p in lots)))
    for at in funding_times:
        if any(at == stamp for stamp, _, _ in states):
            raise InventoryMathError("funding_at_fill_time_is_ambiguous")
        prior = [state for state in states if state[0] < at]
        if not prior or not prior[-1][2]:
            raise InventoryMathError("funding_outside_known_holding_period")
    return entries, exits, entry_value, exit_value, realized, states
