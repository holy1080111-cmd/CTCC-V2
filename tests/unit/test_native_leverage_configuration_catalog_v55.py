"""Pure finite catalog-atom mechanics; no DB, seal, owner or native credit."""

import pytest

from app.domain import native_leverage_configuration_catalog_v55 as catalog


@pytest.mark.parametrize(
    "source,rendered",
    [
        ("environment='demo'", "CHECK ((environment)::text = 'demo'::text)"),
        (
            "ledger_revision=previous_ledger_revision+1",
            "CHECK ((ledger_revision = (previous_ledger_revision + 1)))",
        ),
        (
            "octet_length(core_json) BETWEEN 1 AND 16384",
            "CHECK (((octet_length(core_json) >= 1) AND (octet_length(core_json) <= 16384)))",
        ),
        (
            "phase IN ('prepared','unknown')",
            "CHECK ((phase)::text = ANY ((ARRAY['prepared'::character varying, 'unknown'::character varying])::text[]))",
        ),
    ],
)
def test_only_finite_pg17_check_atom_renderings_are_equivalent(source, rendered):
    assert catalog._signature(source) == catalog._signature(rendered)


@pytest.mark.parametrize(
    "mutant",
    [
        "CHECK (octet_length(core_json) >= 1 OR octet_length(core_json) <= 16384)",
        "CHECK (octet_length(core_json) >= 1 AND octet_length(source_json) <= 16384)",
        "CHECK (octet_length(core_json) >= 0 AND octet_length(core_json) <= 16384)",
        "CHECK (octet_length(core_json) >= 1)",
    ],
)
def test_bound_or_boolean_mutants_keep_distinct_exact_signatures(mutant):
    assert catalog._signature(mutant) != catalog._signature(
        "octet_length(core_json) BETWEEN 1 AND 16384"
    )


def test_quoted_regex_parentheses_are_not_deleted():
    assert catalog._signature("CHECK (value ~ '^a(b)$')") != catalog._signature(
        "CHECK (value ~ '^ab$')"
    )


@pytest.mark.parametrize(
    "mutant",
    [
        "CHECK (value >= 0) /* ignored */",
        "CHECK (value::double precision >= 0)",
        "CHECK (value::public.text >= 0)",
        "CHECK (octet_length(core_json) >= 1 AND octet_length(core_json) < 16384)",
    ],
)
def test_unrecognized_cast_or_non_token_syntax_is_rejected(mutant):
    with pytest.raises(
        ValueError, match="native_leverage_configuration55_actual_catalog_changed"
    ):
        catalog._signature(mutant)
