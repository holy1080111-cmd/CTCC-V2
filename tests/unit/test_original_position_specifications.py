"""Actual metadata validation; source values never grant account authority."""

from fractions import Fraction

import pytest

from app.trade_qualification import account_original_position_path_v1 as mapping

INSTRUMENT = "BTC-USDT-SWAP"
ROW = {
    "instId": INSTRUMENT,
    "instType": "SWAP",
    "ctType": "linear",
    "settleCcy": "USDT",
    "ctValCcy": "BTC",
    "ctMult": "1",
    "ctVal": "0.01",
}


def read(choices):
    return mapping._specifications(
        {"original_instrument_specs": {INSTRUMENT: choices}}, {INSTRUMENT}
    )


def test_actual_nonempty_contract_metadata_returns_exact_base_units():
    amount, pin = read([dict(ROW)])[INSTRUMENT]
    assert amount == Fraction(1, 100)
    assert pin == mapping.sha(mapping.canonical(ROW))
    assert read([dict(ROW), dict(ROW)]) == read([dict(ROW)])


@pytest.mark.parametrize("choices", [None, [], (), {}, False])
def test_missing_empty_or_wrong_type_metadata_still_rejects(choices):
    with pytest.raises(mapping.OriginalPositionPathError, match="metadata_missing"):
        read(choices)


@pytest.mark.parametrize("value", [[], [True], 1, (True,)])
def test_strict_guard_still_requires_the_exact_boolean_true(value):
    with pytest.raises(mapping.OriginalPositionPathError, match="declared_test_guard"):
        mapping._need(value, "declared_test_guard")


def test_conflicting_original_specifications_still_reject():
    with pytest.raises(mapping.OriginalPositionPathError, match="metadata_changed"):
        read([dict(ROW), dict(ROW, ctVal="0.02")])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("settleCcy", "BTC"),
        ("ctType", "inverse"),
        ("ctValCcy", "USDT"),
        ("ctMult", "2"),
    ],
)
def test_unsupported_contract_units_still_reject(field, value):
    with pytest.raises(mapping.OriginalPositionPathError, match="unit_unsupported"):
        read([{**ROW, field: value}])


def test_original_metadata_population_bound_is_not_relaxed():
    assert read([dict(ROW)] * 4096)[INSTRUMENT][0] == Fraction(1, 100)
    with pytest.raises(
        mapping.OriginalPositionPathError, match="context_population_bound"
    ):
        read([dict(ROW)] * 4097)
