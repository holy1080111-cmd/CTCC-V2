"""Differential against frozen f943 traversal; no source/clock permit is cached."""

import hashlib
import math
import random
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.trade_qualification import candle_collector as subject

_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "candle_guard_before_native_budget_v1.py.txt"
)
_SHA256 = "8ef5c64217ba5bc9302465ec6172c9e14ef813d8e4d7c6d6a0c54680559d69a9"


def _legacy():
    raw = _FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _SHA256
    namespace = dict(vars(subject))
    exec(compile(raw, str(_FIXTURE), "exec"), namespace)  # noqa: S102 -- fixed hashed test-only historical source.
    return namespace["_guard"]


def _outcome(function, value, depth=0, budget=120000):
    remaining = [budget]
    try:
        result = function(value, depth, remaining)
        return type(result), result, remaining[0]
    except (ValueError, TypeError, KeyError, IndexError) as error:
        return type(error), str(error), remaining[0]


def _candle():
    return subject.CapturedCandle(
        timestamp=datetime(2026, 11, 20, tzinfo=UTC),
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume_contracts=Decimal(1),
        volume_currency=Decimal("0.01"),
        volume_quote=Decimal(1),
        confirmed=True,
    )


def _values():
    class Foreign(BaseModel):
        value: int

    class IntegerSubclass(int):
        pass

    values = [
        None,
        False,
        True,
        -(10**15),
        10**15,
        10**15 + 1,
        IntegerSubclass(1),
        0.0,
        math.inf,
        math.nan,
        "x",
        b"x",
        "x" * subject.MAX_RESPONSE_BYTES,
        "x" * (subject.MAX_RESPONSE_BYTES + 1),
        bytearray(b"x"),
        Decimal(0),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("1E-20"),
        Decimal("1E-21"),
        Decimal("1" * 40),
        Decimal("1" * 41),
        datetime(2026, 11, 20, tzinfo=UTC),
        datetime(2026, 11, 20),  # noqa: DTZ001 -- deliberate invalid-clock refusal input.
        (),
        tuple(range(1024)),
        tuple(range(1025)),
        {},
        {"a": 1},
        {1: 1},
        {"x" * 65: 1},
        Foreign(value=1),
        subject.FrameRequest(timeframe="4H", requested_confirmed_bars=200),
        _candle(),
    ]
    candle = _candle()
    hidden = candle.model_copy()
    hidden.__dict__["hidden"] = True
    extra = candle.model_copy()
    object.__setattr__(extra, "__pydantic_extra__", {"hidden": True})
    bad = candle.model_copy(update={"open": Decimal("NaN")})
    values.extend((hidden, extra, bad, (candle, candle, candle)))
    # Constructed DTOs are deliberately unauthenticated. These exercise all
    # existing model-shape/pin/sequence guards, not packet acceptance.
    for model in (
        subject.CandleCollectionPolicy,
        subject.CandlePage,
        subject.FrameCapture,
        subject.CollectedCandles,
    ):
        fields = {name: None for name in model.model_fields}
        for name in fields:
            if name.endswith("_sha256"):
                fields[name] = "a" * 64
            elif name == "instrument_id":
                fields[name] = "BTC-USDT-SWAP"
            elif name == "report_id":
                fields[name] = "fixture-report"
        if model is subject.CandleCollectionPolicy:
            fields["requests"] = tuple(
                subject.FrameRequest(timeframe=bar, requested_confirmed_bars=200)
                for bar in subject.TIMEFRAMES
            )
            bounded = "requests"
        elif model is subject.CandlePage:
            fields["parameters"] = (
                ("instId", "BTC-USDT-SWAP"),
                ("bar", "4H"),
                ("limit", "200"),
            )
            bounded = "parameters"
        elif model is subject.FrameCapture:
            fields["pages"] = (None,)
            fields["confirmed"] = (candle,) * 200
            bounded = "confirmed"
        else:
            fields["frames"] = (None,) * 4
            fields["volume_units"] = ("contracts", "base", "quote")
            bounded = "frames"
        constructed = model.model_construct(**fields)
        values.append(constructed)
        values.append(constructed.model_copy(update={bounded: []}))
        values.append(constructed.model_copy(update={bounded: ()}))
    for count in (17, 18, 19, 21):
        nested = 1
        for _ in range(count):
            nested = (nested,)
        values.append(nested)
    cycle = {}
    cycle["self"] = cycle
    values.append(cycle)
    return values


@pytest.mark.parametrize("depth", (-1, 0, 18, 19))
@pytest.mark.parametrize("budget", (0, 1, 5, 120000))
def test_iterative_guard_preserves_acceptance_refusal_and_exact_node_budget(
    depth, budget
):
    legacy = _legacy()
    for value in _values():
        assert _outcome(subject._guard, value, depth, budget) == _outcome(
            legacy, value, depth, budget
        )


def test_random_nested_values_match_frozen_depth_first_rejection_order():
    rng = random.Random(356)
    legacy = _legacy()
    leaves = [
        None,
        False,
        True,
        0,
        1,
        10**15 + 1,
        "ok",
        b"ok",
        Decimal("1.25"),
        Decimal("NaN"),
        0.0,
    ]

    def value(depth):
        if depth == 0 or rng.randrange(3) == 0:
            return rng.choice(leaves)
        if rng.randrange(2):
            return tuple(value(depth - 1) for _ in range(rng.randrange(6)))
        return {str(index): value(depth - 1) for index in range(rng.randrange(6))}

    for _ in range(500):
        incoming = value(5)
        budget = rng.choice((1, 8, 50, 120000))
        assert _outcome(subject._guard, incoming, budget=budget) == _outcome(
            legacy, incoming, budget=budget
        )


def test_reordered_or_aliased_model_keys_keep_legacy_guard_behavior():
    class FieldAlias(str):
        def endswith(self, suffix, *args):
            return self == "open" or super().endswith(suffix, *args)

    reordered = _candle()
    data = reordered.__dict__
    object.__setattr__(reordered, "__dict__", dict(reversed(tuple(data.items()))))
    aliased = _candle()
    data = {
        FieldAlias(name) if name == "open" else name: item
        for name, item in aliased.__dict__.items()
    }
    object.__setattr__(aliased, "__dict__", data)
    legacy = _legacy()
    for value in (reordered, aliased):
        assert _outcome(subject._guard, value) == _outcome(legacy, value)


def test_every_call_and_shared_occurrence_is_rewalked_after_mutation():
    candle = _candle()
    pair = (candle, candle)
    assert _outcome(subject._guard, pair) == _outcome(_legacy(), pair)
    subject._guard(pair)
    object.__setattr__(candle, "open", Decimal("NaN"))
    with pytest.raises(subject.CandleCollectionError, match="capture_decimal_invalid"):
        subject._guard(pair)
    assert _outcome(subject._guard, pair) == _outcome(_legacy(), pair)
