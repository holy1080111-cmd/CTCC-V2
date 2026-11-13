"""Opt-in, source-free timing of the original PostgreSQL read in tests.

This observes SQLAlchemy's completed DBAPI round trips. It never records SQL,
parameters, rows, exception text, account identity, or source bytes. The
unmeasured remainder includes decoding, replay, and Python/driver overhead; it
must not be presented as a pure decoder measurement.
"""

import json
import os
from time import perf_counter_ns

from sqlalchemy import event

_ENV = "CTCC_ORIGINAL_PG_TIMING_V1"
_MAX_ROUNDTRIPS = 512


def _kind(statement):
    if statement.lstrip().startswith(
        "SELECT public.account_history_original_frame_v1_read("
    ):
        return "frame"
    if statement.lstrip().startswith("SELECT jsonb_build_object("):
        return "catalog"
    return "other"


class OriginalPgTimingProbe:
    """Bounded diagnostic only; no effect on the production request or fence."""

    def __init__(self, async_engine):
        self.engine = async_engine.sync_engine
        self.enabled = os.environ.get(_ENV) == "1"
        self._started = None
        self._elapsed_ns = None
        self._pending = {}
        self._totals = {
            kind: {"count": 0, "ns": 0} for kind in ("frame", "catalog", "other")
        }
        self._frame_ns = []
        self._unfinished = 0
        self._overflow = False

    def _before(self, conn, cursor, statement, parameters, context, executemany):
        if (
            len(self._pending) + sum(item["count"] for item in self._totals.values())
            >= _MAX_ROUNDTRIPS
        ):
            self._overflow = True
            return
        self._pending[id(context)] = (perf_counter_ns(), _kind(statement))

    def _finish(self, context):
        pair = self._pending.pop(id(context), None)
        if pair is None:
            return
        started, kind = pair
        elapsed = max(0, perf_counter_ns() - started)
        self._totals[kind]["count"] += 1
        self._totals[kind]["ns"] += elapsed
        if kind == "frame":
            self._frame_ns.append(elapsed)

    def _after(self, conn, cursor, statement, parameters, context, executemany):
        self._finish(context)

    def _error(self, exception_context):
        if exception_context.execution_context is not None:
            self._finish(exception_context.execution_context)

    def __enter__(self):
        if self.enabled:
            self._started = perf_counter_ns()
            event.listen(self.engine, "before_cursor_execute", self._before)
            event.listen(self.engine, "after_cursor_execute", self._after)
            event.listen(self.engine, "handle_error", self._error)
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.enabled:
            self._elapsed_ns = max(0, perf_counter_ns() - self._started)
            for name, callback in (
                ("before_cursor_execute", self._before),
                ("after_cursor_execute", self._after),
                ("handle_error", self._error),
            ):
                event.remove(self.engine, name, callback)
            self._unfinished = len(self._pending)
            self._pending.clear()
        return False

    def receipt_json(self):
        if not self.enabled or self._elapsed_ns is None:
            return None
        return json.dumps(
            {
                "schema": "ctcc.test.original_pg_roundtrip_timing.v1",
                "total_ms": self._elapsed_ns // 1_000_000,
                "dbapi": {
                    kind: {
                        "count": item["count"],
                        "total_ms": item["ns"] // 1_000_000,
                    }
                    for kind, item in self._totals.items()
                },
                "frame_ms_in_order": [value // 1_000_000 for value in self._frame_ns],
                "unfinished": self._unfinished,
                "overflow": self._overflow,
                "account_complete": False,
                "execution_authority": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
