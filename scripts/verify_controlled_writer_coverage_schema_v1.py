"""Independent actual PG catalog reader; source policy has one declaration.

Capture is diagnostic, including when baseline admission refuses. No desired
hash or captured catalog can automatically seal the native PG17 comparison.
"""

from __future__ import annotations

from app.database.repositories.controlled_writer_coverage import (
    PG17_DEPARSED_BASELINE_STATUS,
    ControlledWriterCoverageError,
    expected_controlled_writer_coverage_schema,
    read_controlled_writer_coverage_catalog,
    read_controlled_writer_coverage_retention,
    verify_controlled_writer_coverage_catalog,
)


async def read_catalog(session):
    return await read_controlled_writer_coverage_catalog(session)


def verify_catalog(catalog, *, require_native_baseline=True):
    return verify_controlled_writer_coverage_catalog(
        catalog, require_native_baseline=require_native_baseline
    )


async def read_retention(session):
    return await read_controlled_writer_coverage_retention(session)


__all__ = [
    "PG17_DEPARSED_BASELINE_STATUS",
    "ControlledWriterCoverageError",
    "expected_controlled_writer_coverage_schema",
    "read_catalog",
    "read_retention",
    "verify_catalog",
]
