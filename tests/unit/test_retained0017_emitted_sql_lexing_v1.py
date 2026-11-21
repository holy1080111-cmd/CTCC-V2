"""SQL delimiter regression only; genuine PG parsing remains mandatory CI.

This lexer neither validates PostgreSQL grammar nor grants schema admission.
It catches the actual emitted unterminated identifier which Python's literal
lexer accepted when SQL and triple-double delimiters shared the same line.
"""

import ast

import pytest

from scripts import retained0017_catalog_baseline_v1 as subject


def _balanced_sql_quotes(raw):
    active, offset = None, 0
    while offset < len(raw):
        char = raw[offset]
        if active is None:
            if char in "'\"":
                active = char
        elif char == active:
            if offset + 1 < len(raw) and raw[offset + 1] == active:
                offset += 1
            else:
                active = None
        offset += 1
    return active is None


def test_the_actual_old_python_literal_emits_an_unclosed_identifier():
    # Exact old delimiter shape, compiled independently of product source.
    old = ast.literal_eval('"""SELECT c.relname COLLATE pg_catalog."C"""')
    assert old.endswith('pg_catalog."C')
    assert _balanced_sql_quotes(old) is False


@pytest.mark.parametrize(
    "name",
    (
        "_RELATION_NAMES_SQL",
        "CATALOG_SQL",
        "SAFE_CATALOG_SQL",
        "_LANGUAGE_ADMISSION_SQL",
    ),
)
def test_emitted_fixed_catalog_sql_has_closed_string_and_identifier_delimiters(name):
    assert _balanced_sql_quotes(getattr(subject, name)) is True
