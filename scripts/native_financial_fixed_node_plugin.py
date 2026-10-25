"""Pytest-only registration; never imported by either host producer."""

from pytest import hookimpl

from scripts.native_financial_fixed_node_diagnostics import (
    pytest_addoption,  # noqa: F401 -- pytest hook registration.
    pytest_collection_finish,  # noqa: F401 -- pytest hook registration.
    pytest_runtest_makereport,
    pytest_sessionfinish,  # noqa: F401 -- pytest hook registration.
)

# makereport is a first-result hook; record before core's report producer.
hookimpl(tryfirst=True)(pytest_runtest_makereport)
