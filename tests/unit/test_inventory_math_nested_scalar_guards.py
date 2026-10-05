"""Exact Fraction class does not authorize non-native internal operands."""

from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest

from app.trade_evidence.inventory_math import InventoryMathError, reduce_inventory

NOW = datetime(2026, 10, 1, tzinfo=UTC)
LOCATIONS = ("unit", "contracts", "price")
SLOTS = (
    "numerator_foreign",
    "denominator_foreign",
    "numerator_bool",
    "denominator_bool",
    "denominator_zero",
    "denominator_negative",
)
CASES = tuple((location, slot) for location in LOCATIONS for slot in SLOTS)
CASE_IDS = tuple(f"{location}:{slot}" for location, slot in CASES)


class ForeignOperand:
    def __init__(self, calls):
        self.calls = calls

    def _hit(self, method):
        self.calls.append(method)
        raise AssertionError("nested_fraction_foreign_callback_reached")

    def __mul__(self, other):
        return self._hit("__mul__")

    def __rmul__(self, other):
        return self._hit("__rmul__")

    def __add__(self, other):
        return self._hit("__add__")

    def __radd__(self, other):
        return self._hit("__radd__")

    def __sub__(self, other):
        return self._hit("__sub__")

    def __rsub__(self, other):
        return self._hit("__rsub__")

    def __truediv__(self, other):
        return self._hit("__truediv__")

    def __rtruediv__(self, other):
        return self._hit("__rtruediv__")

    def __lt__(self, other):
        return self._hit("__lt__")

    def __le__(self, other):
        return self._hit("__le__")

    def __gt__(self, other):
        return self._hit("__gt__")

    def __ge__(self, other):
        return self._hit("__ge__")

    def __eq__(self, other):
        return self._hit("__eq__")

    def __bool__(self):
        return self._hit("__bool__")

    def __hash__(self):
        return self._hit("__hash__")

    def __str__(self):
        return self._hit("__str__")

    def __repr__(self):
        return self._hit("__repr__")


def assert_genuine_native_baseline():
    result = reduce_inventory(
        direction="long",
        unit=Fraction(1),
        fills=(
            ("entry", "buy", Fraction(2), Fraction(10), NOW),
            ("exit", "sell", Fraction(2), Fraction(12), NOW + timedelta(seconds=1)),
        ),
        funding_times=(),
    )
    assert result == (
        Fraction(2),
        Fraction(2),
        Fraction(20),
        Fraction(24),
        Fraction(4),
        [
            (NOW, Fraction(0), ((Fraction(2), Fraction(10)),)),
            (NOW + timedelta(seconds=1), Fraction(4), ()),
        ],
    )


def declared_fraction(slot, calls):
    # Preserve exact class without invoking Fraction's constructor/coercions.
    value = object.__new__(Fraction)
    object.__setattr__(value, "_numerator", 1)
    object.__setattr__(value, "_denominator", 1)
    declared = {
        "numerator_foreign": ("_numerator", ForeignOperand(calls)),
        "denominator_foreign": ("_denominator", ForeignOperand(calls)),
        "numerator_bool": ("_numerator", True),
        "denominator_bool": ("_denominator", True),
        "denominator_zero": ("_denominator", 0),
        "denominator_negative": ("_denominator", -1),
    }
    field, operand = declared[slot]
    object.__setattr__(value, field, operand)
    assert type(value) is Fraction
    return value


@pytest.mark.parametrize(("location", "slot"), CASES, ids=CASE_IDS)
def test_exact_fraction_slots_reject_before_foreign_callbacks(location, slot):
    assert_genuine_native_baseline()
    calls = []
    changed = declared_fraction(slot, calls)
    unit = changed if location == "unit" else Fraction(1)
    contracts = changed if location == "contracts" else Fraction(2)
    price = changed if location == "price" else Fraction(10)
    with pytest.raises(InventoryMathError) as failure:
        reduce_inventory(
            direction="long",
            unit=unit,
            fills=(("entry", "buy", contracts, price, NOW),),
            funding_times=(),
        )
    assert type(failure.value) is InventoryMathError
    assert failure.value.args == ("inventory_math_input_invalid",)
    assert calls == []
