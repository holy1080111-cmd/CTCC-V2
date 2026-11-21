"""Failure-only stage diagnostics cannot promote a rejected catalog proof."""

import pytest

from scripts import retained0017_catalog_baseline_v1 as subject


class _CatalogResults:
    def __init__(self, values):
        self.values = iter(values)
        self.statements = []

    async def scalar(self, statement):
        self.statements.append(str(statement))
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


COMPONENTS = tuple(
    (name, "fixed_catalog_" + name)
    for name in ("scope", "functions", "language", "nonpublic")
)


@pytest.mark.asyncio
async def test_success_never_runs_diagnostics():
    connection = _CatalogResults([True])
    await subject._require_raw_catalog_admission_v1(
        connection, "SELECT fixed_combined", COMPONENTS
    )
    assert connection.statements == ["SELECT fixed_combined"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", range(4))
async def test_false_component_reports_only_a_fixed_stage_and_stops(failed):
    connection = _CatalogResults([False, *([True] * failed), False])
    with pytest.raises(subject.Retained0017CatalogRefused) as error:
        await subject._require_raw_catalog_admission_v1(
            connection, "SELECT fixed_combined", COMPONENTS
        )
    assert str(error.value) == (
        "retained0017_raw_admission_" + COMPONENTS[failed][0] + "_refused"
    )
    assert len(connection.statements) == failed + 2
    assert all("public.alembic_version" not in sql for sql in connection.statements)


@pytest.mark.asyncio
@pytest.mark.parametrize("first", (False, None, 1, "true"))
async def test_later_all_true_never_overrides_failed_or_untyped_first_proof(first):
    connection = _CatalogResults([first, True, True, True, True])
    with pytest.raises(
        subject.Retained0017CatalogRefused,
        match="^retained0017_raw_admission_changed$",
    ):
        await subject._require_raw_catalog_admission_v1(
            connection, "SELECT fixed_combined", COMPONENTS
        )
    assert len(connection.statements) == 5


@pytest.mark.asyncio
async def test_diagnostic_driver_error_is_redacted_and_still_denied():
    connection = _CatalogResults([False, True, ValueError("private_CANARY")])
    with pytest.raises(subject.Retained0017CatalogRefused) as error:
        await subject._require_raw_catalog_admission_v1(
            connection, "SELECT fixed_combined", COMPONENTS
        )
    assert str(error.value) == "retained0017_raw_admission_diagnostic_unavailable"
    assert "private_CANARY" not in str(error.value)
    assert len(connection.statements) == 3
    assert all("public.alembic_version" not in sql for sql in connection.statements)


@pytest.mark.asyncio
async def test_unknown_component_cannot_be_promoted_by_truthiness():
    connection = _CatalogResults([False, None])
    with pytest.raises(
        subject.Retained0017CatalogRefused,
        match="^retained0017_raw_admission_scope_refused$",
    ):
        await subject._require_raw_catalog_admission_v1(
            connection, "SELECT fixed_combined", COMPONENTS
        )
    assert len(connection.statements) == 2
