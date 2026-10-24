"""Independent catalog policy with lazy database observation adapters.

The host can compare evidence using only the standard library. Actual catalog
and retention reads import the production repository only when awaited.
"""

from __future__ import annotations

from app.domain.controlled_writer_coverage_catalog_v1 import (
    PG17_DEPARSED_BASELINE_STATUS,
    ControlledWriterCoverageError,
    expected_controlled_writer_coverage_schema,
    expected_controlled_writer_coverage_schema_v48,
    verify_controlled_writer_coverage_catalog,
    verify_controlled_writer_coverage_catalog_v48,
)

verify_catalog = verify_controlled_writer_coverage_catalog
# Historical evidence only, never used by the native repository deployment gate.
verify_catalog_v48 = verify_controlled_writer_coverage_catalog_v48


async def read_catalog(session):
    from app.database.repositories.controlled_writer_coverage import (
        read_controlled_writer_coverage_catalog,
    )

    return await read_controlled_writer_coverage_catalog(session)


async def read_retention(session):
    from app.database.repositories.controlled_writer_coverage import (
        read_controlled_writer_coverage_retention,
    )

    return await read_controlled_writer_coverage_retention(session)


__all__ = [
    "PG17_DEPARSED_BASELINE_STATUS",
    "ControlledWriterCoverageError",
    "expected_controlled_writer_coverage_schema",
    "expected_controlled_writer_coverage_schema_v48",
    "read_catalog",
    "read_retention",
    "verify_catalog",
    "verify_catalog_v48",
]
