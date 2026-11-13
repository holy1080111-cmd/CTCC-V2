"""Passive pytest observer for the exact isolated bootstrap diagnostic."""

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import CoroutineType

import asyncpg
import pytest
from sqlalchemy.exc import SQLAlchemyError

from scripts import pg_account_bootstrap_diagnostic as contract

_stream = None
_sequence = 0
_active = None


@dataclass
class _Capture:
    ordinal: int
    plan_kind: str
    phase: str = "bootstrap_capture"
    script_request_ordinal: int = 0
    journal_sequence: int = 0
    journal_last_completed: str | None = None
    writes: dict = field(default_factory=dict)
    failures: dict = field(default_factory=dict)

    def coordinates(self, phase):
        return {
            "capture_ordinal": self.ordinal,
            "plan_kind": self.plan_kind,
            "phase": phase,
            "journal_sequence": self.journal_sequence,
            "journal_last_completed": self.journal_last_completed,
            "script_request_ordinal": self.script_request_ordinal,
        }


def _write(event, **fields):
    global _sequence
    _sequence += 1
    if _stream is None or _active is None or _sequence > contract.MAX_RECORDS:
        raise RuntimeError("account_bootstrap_trace_unavailable")
    line = (
        json.dumps(
            {
                "schema": contract.SCHEMA,
                "sequence": _sequence,
                "event": event,
                "node_sha256": _active,
                **fields,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        + b"\n"
    )
    _stream.write(line)
    _stream.flush()
    os.fsync(_stream.fileno())


def _failure(error, known):
    if type(error) is TimeoutError:
        return "deadline_timeout", None
    if type(error) is asyncio.CancelledError:
        return "cancelled", None
    if type(error) in known:
        code = error.args[0] if len(error.args) == 1 else None
        return known[type(error)], (
            code if type(code) is str and code in contract.FIXED_CODES else None
        )
    if isinstance(error, (SQLAlchemyError, asyncpg.PostgresError)):
        return "database_or_driver_error", None
    return "other_exception", None


async def _observe(active, phase, awaitable, known):
    previous, active.phase = active.phase, phase
    _write("phase_start", **active.coordinates(phase))
    start = time.monotonic_ns()
    disposition, code = "returned", None
    try:
        return await awaitable
    except (Exception, asyncio.CancelledError) as error:
        disposition, code = _failure(error, known)
        raise
    finally:
        elapsed = time.monotonic_ns() - start
        active.phase = previous
        _write(
            "phase_finish",
            **active.coordinates(phase),
            elapsed_ns=elapsed,
            disposition=disposition,
            fixed_code=code,
            journal_writes=active.writes.pop(phase, {}),
            journal_failure=active.failures.pop(phase, None),
        )


def _instrument(monkeypatch):
    from app.trade_qualification import (
        account_bootstrap_runtime as bootstrap,
    )
    from app.trade_qualification import (
        account_capture as capture,
    )
    from app.trade_qualification import (
        account_capture_journal as journal,
    )
    from app.trade_qualification import (
        account_collector as collector,
    )
    from app.trade_qualification import (
        account_runtime as runtime,
    )

    known = {
        collector.AccountCollectionError: "account_collection_error",
        runtime.AccountRuntimeError: "account_runtime_error",
        journal.AccountJournalError: "account_journal_error",
    }
    plan_kinds = {
        capture.RegionalDemoAccountCapturePlan: "regional",
        capture.AllProductDemoAccountCapturePlan: "historical_v5",
        capture.CurrentDemoAccountCapturePlanV7: "current_v7",
    }
    recorded = bootstrap._collect_recorded
    collect = bootstrap._collect
    original_page = collector._page
    emit = journal._OwnedAccountJournal.emit
    finalize = journal.bounded_finalization
    bounded_codes = {
        journal._OwnedAccountJournal.close_acquisition.__code__: "source_close",
        journal._OwnedAccountJournal.finish.__code__: "terminal",
    }
    active, ordinal = None, 0

    async def observed_recorded(session, **kwargs):
        nonlocal active, ordinal
        if active is not None:
            raise RuntimeError("account_bootstrap_trace_unexpected_capture")
        ordinal += 1
        if ordinal > contract.MAX_CAPTURES:
            raise RuntimeError("account_bootstrap_trace_capture_bound")
        active = _Capture(ordinal, plan_kinds.get(type(session._plan), "other"))
        try:
            return await _observe(
                active, "bootstrap_recorded", recorded(session, **kwargs), known
            )
        finally:
            active = None

    async def observed_collect(*args, **kwargs):
        if active is None:
            return await collect(*args, **kwargs)
        return await _observe(
            active, "bootstrap_capture", collect(*args, **kwargs), known
        )

    async def observed_page(*args, **kwargs):
        if active is not None:
            active.script_request_ordinal += 1
        return await original_page(*args, **kwargs)

    async def observed_emit(owner, kind, *args, **kwargs):
        current = active
        if current is None:
            return await emit(owner, kind, *args, **kwargs)
        category = (
            kind if type(kind) is str and kind in contract.JOURNAL_KINDS else "other"
        )
        counters = current.writes.setdefault(current.phase, {}).setdefault(
            category, [0, 0, 0]
        )
        start = time.monotonic_ns()
        try:
            value = await emit(owner, kind, *args, **kwargs)
        except (Exception, asyncio.CancelledError) as error:
            current.failures.setdefault(
                current.phase, [category, *_failure(error, known)]
            )
            raise
        finally:
            elapsed = time.monotonic_ns() - start
            counters[0] += 1
            counters[1] += elapsed
            counters[2] = max(counters[2], elapsed)
        current.journal_sequence = owner.sequence
        current.journal_last_completed = category
        return value

    async def observed_finalization(awaitable):
        phase = (
            bounded_codes.get(awaitable.cr_code)
            if type(awaitable) is CoroutineType
            else None
        )
        if active is None or phase is None:
            return await finalize(awaitable)
        return await _observe(active, phase, finalize(awaitable), known)

    monkeypatch.setattr(bootstrap, "_collect_recorded", observed_recorded)
    monkeypatch.setattr(bootstrap, "_collect", observed_collect)
    monkeypatch.setattr(collector, "_page", observed_page)
    monkeypatch.setattr(journal._OwnedAccountJournal, "emit", observed_emit)
    monkeypatch.setattr(journal, "bounded_finalization", observed_finalization)


def pytest_configure(config):
    global _stream, _sequence
    if (
        os.environ.get("CTCC_ACCOUNT_BOOTSTRAP_DIAGNOSTIC") != "1"
        or tuple(config.args) != contract.NODES
        or config.option.collectonly
        or _stream is not None
    ):
        raise RuntimeError("account_bootstrap_trace_configuration_invalid")
    _sequence = 0
    _stream = (Path("/validation-results") / contract.TRACE).open("xb")


def pytest_collection_finish(session):
    if tuple(item.nodeid for item in session.items) != contract.NODES:
        raise RuntimeError("account_bootstrap_trace_collection_changed")


def pytest_runtest_logstart(nodeid, location):
    global _active
    if nodeid not in contract.NODES or _active is not None:
        raise RuntimeError("account_bootstrap_trace_node_changed")
    _active = hashlib.sha256(nodeid.encode()).hexdigest()
    _write("node_start")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    if item.nodeid not in contract.NODES:
        raise RuntimeError("account_bootstrap_trace_node_changed")
    with pytest.MonkeyPatch.context() as monkeypatch:
        _instrument(monkeypatch)
        return (yield)


def pytest_runtest_logreport(report):
    _write("pytest_report", when=report.when, outcome=report.outcome)


def pytest_runtest_logfinish(nodeid, location):
    global _active
    _write("node_finish")
    _active = None


def pytest_unconfigure(config):
    global _stream
    if _stream is not None:
        _stream.close()
        _stream = None
