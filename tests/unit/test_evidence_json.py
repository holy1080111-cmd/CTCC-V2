"""Evidence must not lose a failure field or accept non-JSON numeric constants."""

import pytest

from scripts.evidence_json import read_evidence_json


@pytest.mark.parametrize(
    "raw",
    (
        b'{"exit_code":19,"exit_code":0}',
        b'{"nested":{"exit_code":19,"exit_code":0}}',
        b'{"ex\\u0069t_code":19,"exit_code":0}',
    ),
)
def test_duplicate_failure_fields_are_rejected_at_every_depth(raw):
    with pytest.raises(ValueError, match="evidence_json_duplicate_key"):
        read_evidence_json(raw)


@pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity"))
def test_non_json_numbers_cannot_enter_evidence(constant):
    with pytest.raises(ValueError, match="evidence_json_non_json_constant"):
        read_evidence_json('{"nested":{"elapsed":' + constant + "}}")


def test_native_json_types_remain_distinct_for_typed_consumers():
    value = read_evidence_json(b'{"code":0,"flag":false,"elapsed":0.0,"rows":[]}')
    assert type(value["code"]) is int
    assert type(value["flag"]) is bool
    assert type(value["elapsed"]) is float
    assert value["rows"] == []


def test_foreign_text_subclass_is_not_executed():
    class ForeignText(str):
        def __str__(self):
            raise AssertionError("foreign_text_callback")

    with pytest.raises(ValueError, match="evidence_json_native_text_or_bytes_required"):
        read_evidence_json(ForeignText('{"exit_code":0}'))
