"""Exact existing metric language, with PostgreSQL-compatible finite bounds."""

import re

import pytest

from app.domain import native_protective_report_schema_v58 as schema


@pytest.mark.parametrize("digits", [0, 1, 254, 255, 256, 299, 300, 301])
def test_equivalent_numeric_spelling_preserves_existing_three_hundred_digit_limit(
    digits,
):
    for value in ("9" * digits, "-" + "9" * digits, "0", "00", "+1", "1.0", "a1", "1a"):
        for original, compiled in (
            (schema.LEGACY_METRIC_NUMERATOR_REGEX, schema.METRIC_NUMERATOR_REGEX),
            (schema.LEGACY_METRIC_DENOMINATOR_REGEX, schema.METRIC_DENOMINATOR_REGEX),
        ):
            assert bool(re.fullmatch(compiled, value)) == bool(
                re.fullmatch(original, value)
            )


def test_physical58_changes_both_report_profiles_and_keeps_frozen55_restore():
    for pattern in (schema.METRIC_NUMERATOR_REGEX, schema.METRIC_DENOMINATOR_REGEX):
        assert schema.ELIGIBLE_SQL.count(pattern) == 2
        for minimum, maximum in re.findall(r"\{([0-9]+),([0-9]+)\}", pattern):
            assert 0 <= int(minimum) <= int(maximum) <= 255
    assert schema.LEGACY_METRIC_NUMERATOR_REGEX not in schema.ELIGIBLE_SQL
    assert schema.LEGACY_METRIC_DENOMINATOR_REGEX not in schema.ELIGIBLE_SQL
    assert schema.LEGACY_METRIC_NUMERATOR_REGEX in schema.ancestor.ELIGIBLE_SQL
    assert schema.LEGACY_METRIC_DENOMINATOR_REGEX in schema.ancestor.ELIGIBLE_SQL
    schema.statements()
    assert schema.restore_statements() == (
        schema.ancestor.ELIGIBLE_SQL.replace(
            "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
        ),
    )
