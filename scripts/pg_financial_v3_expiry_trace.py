"""Passive fixed-source Python trace, enabled only for the isolated PG case."""

import asyncio
import inspect
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from scripts import pg_financial_v3_expiry as evidence

# These source lines belong to the exact application archive for this actor.
# Their line text is checked before registering a trace, so a moved site refuses.
_REPLAY_LINES = {
    122: ("fence = native._Fence(request.expires_at)", "capture_prepare"),
    184: (
        "before = await native._catalog(session, fence, approved_collector_role_oid)",
        "initial_catalog",
    ),
    201: ("fence.check()", "page_handoff"),
    204: ("engine.send(page)", "page_replay"),
    205: ("fence.check()", "page_accepted"),
    206: ("original_replay = stream._finish_replay_engine(engine)", "final_stream"),
    214: (
        "after = await native._catalog(session, fence, approved_collector_role_oid)",
        "final_catalog",
    ),
    222: ("replay = stream._finish_financial_stream_v3(", "financial_finish"),
    237: ("await session.commit()", "commit"),
    240: ("await session.close()", "close"),
    242: ("receipt = journal.canonical(", "receipt"),
    281: ("return observation", None),
}
_PAGE_LINES = {
    194: ("request = replace(genesis_request, after=after, previous=previous)", None),
    196: (
        "value = await _execute_one(session, wire.SQL, fence, _parameters(request))",
        "rpc",
    ),
    201: ("decoded = wire.decode_original_frame_wire_v1(", "decode"),
    213: ("page = wire.original_frame_width1_page_v1(", "wire_to_page"),
}


def _confirm_lines(function, expected):
    lines, offset = inspect.getsourcelines(function)
    if any(
        not 0 <= line - offset < len(lines) or lines[line - offset].strip() != text
        for line, (text, _) in expected.items()
    ):
        raise RuntimeError("financial_v3_trace_source_changed")


def _remaining_us(delta):
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


class _Trace:
    def __init__(self, request_builder=None):
        from app.database.repositories import (
            account_history_original_financial_read_v3 as v3,
        )
        from app.database.repositories import (
            account_history_original_native_read_v1 as native,
        )
        from app.database.repositories.account_history_original_frame_v1 import (
            OriginalFrameRequestV1,
        )

        replay = v3.replay_original_history_financial_from_pg_v3
        _confirm_lines(replay, _REPLAY_LINES)
        _confirm_lines(native._pages, _PAGE_LINES)
        _confirm_lines(
            native._Fence.__init__,
            {
                94: ('_deny("original_pg_original_expiry")', None),
                96: (
                    "self.loop_deadline = asyncio.get_running_loop().time() + remaining",
                    None,
                ),
            },
        )
        _confirm_lines(
            native._Fence.check, {109: ('_deny("original_pg_original_expiry")', None)}
        )
        self.codes = {
            replay.__code__: self._replay,
            native._pages.__code__: self._pages,
            native._Fence.__init__.__code__: self._budget,
            native._Fence.check.__code__: self._expiry,
        }
        self.replay_code = replay.__code__
        self.request_type = OriginalFrameRequestV1
        self.request_code = request_builder.__code__ if request_builder else None
        if self.request_code is not None:
            self.codes[self.request_code] = self._request
        self.error_type = v3.OriginalHistoryFinancialPgReadError
        self.start = None
        self.phase, self.page = "capture_prepare", 0
        self.finish = None
        self.expired = False
        self.persisted_lease_at = None
        self.lease_relation = None
        self.records = []
        self.broken = False

    def _record(self, event, **data):
        self.records.append(
            {
                "schema": evidence.SCHEMA,
                "node_sha256": evidence.NODE_SHA256,
                "sequence": len(self.records) + 1,
                "elapsed_ns": time.monotonic_ns() - self.start,
                "event": event,
                "data": data,
            }
        )

    def _phase(self, phase, *, paged):
        if not paged:
            self.page = 0
        self.phase = phase
        self._record("phase", phase=phase, page=self.page)

    def _replay(self, frame, event, arg):
        if event == "exception" and arg[0] is self.error_type:
            error = arg[1]
            code = error.args[0] if len(error.args) == 1 else None
            self.finish = {
                "outcome": "exception",
                "closed_code": code
                if type(code) is str and code in evidence.ERRORS
                else None,
            }
        elif event == "exception" and arg[0] is asyncio.CancelledError:
            self.finish = {"outcome": "cancelled", "closed_code": None}
        elif event == "line" and frame.f_lineno in _REPLAY_LINES:
            _, phase = _REPLAY_LINES[frame.f_lineno]
            if phase is None:
                self.finish = {"outcome": "returned", "closed_code": None}
            else:
                self._phase(
                    phase,
                    paged=phase in {"page_handoff", "page_replay", "page_accepted"},
                )

    def _pages(self, frame, event, arg):
        if event == "line" and frame.f_lineno in _PAGE_LINES:
            _, phase = _PAGE_LINES[frame.f_lineno]
            if phase is None:
                ordinal = frame.f_locals["after"] + 1
                if type(ordinal) is not int or not 1 <= ordinal <= 33:
                    raise ValueError("financial_trace_ordinal_invalid")
                self.page = ordinal
            else:
                self._phase(phase, paged=True)

    def _request(self, frame, event, request):
        if event == "return" and type(request) is self.request_type:
            lease = frame.f_locals["lease"]
            if request.expires_at > lease or self.persisted_lease_at is not None:
                raise ValueError("financial_trace_lease_relation_invalid")
            self.persisted_lease_at = lease
            self.lease_relation = (
                "control_lease" if request.expires_at == lease else "request_cap"
            )

    def _budget(self, frame, event, arg):
        if event == "line" and frame.f_lineno in {94, 96}:
            fence = frame.f_locals["self"]
            remaining = _remaining_us(fence.expires_at - fence.wall)
            if frame.f_lineno == 94:
                if remaining <= 0:
                    self._record("fence_no_budget", wall_remaining_us=remaining)
                return
            self._record(
                "budget",
                original_wall_remaining_us=remaining,
                original_monotonic_budget_ns=fence.monotonic_deadline
                - fence.monotonic_ns,
                boundary=self.lease_relation or "unobserved",
                persisted_lease_remaining_us=(
                    None
                    if self.persisted_lease_at is None
                    else _remaining_us(self.persisted_lease_at - fence.wall)
                ),
            )

    def _expiry(self, frame, event, arg):
        if event == "line" and frame.f_lineno == 109 and not self.expired:
            local = frame.f_locals
            fence = local["self"]
            wall = _remaining_us(fence.expires_at - local["wall"])
            mono = fence.monotonic_deadline - local["monotonic_ns"]
            cause = (
                "wall_and_monotonic"
                if wall <= 0 and mono <= 0
                else "wall"
                if wall <= 0
                else "monotonic"
            )
            self._record(
                "fence_expiry",
                page=self.page,
                phase=self.phase,
                wall_remaining_us=wall,
                monotonic_remaining_ns=mono,
                cause=cause,
            )
            self.expired = True

    def visit(self, code, event, arg=None):
        if self.broken:
            return
        try:
            if self.start is None and code is self.replay_code:
                self.start = time.monotonic_ns()
                self._record("start")
            if self.start is not None or code is self.request_code:
                frame = sys._getframe(2)
                if frame.f_code is code:
                    self.codes[code](frame, event, arg)
                else:
                    self.broken = True
        except (AttributeError, KeyError, ValueError, TypeError, OverflowError):
            # A diagnostic cannot replace the original refusal or result.
            self.broken = True

    def line(self, code, lineno):
        self.visit(code, "line")

    def returned(self, code, instruction_offset, result):
        self.visit(code, "return", result)

    def raised(self, code, instruction_offset, error):
        if code is self.replay_code:
            self.visit(code, "exception", (type(error), error))

    @contextmanager
    def monitor(self):
        monitoring = sys.monitoring
        tool_id = 4
        events = monitoring.events
        monitoring.use_tool_id(tool_id, "ctcc-original-financial-v3-expiry")
        try:
            monitoring.register_callback(tool_id, events.LINE, self.line)
            monitoring.register_callback(tool_id, events.PY_RETURN, self.returned)
            monitoring.register_callback(tool_id, events.RAISE, self.raised)
            for code in self.codes:
                monitoring.set_local_events(
                    tool_id,
                    code,
                    events.PY_RETURN if code is self.request_code else events.LINE,
                )
            # RAISE is a non-local event in CPython 3.12.
            monitoring.set_events(tool_id, events.RAISE)
            yield
        finally:
            monitoring.set_events(tool_id, 0)
            for code in self.codes:
                monitoring.set_local_events(tool_id, code, 0)
            monitoring.register_callback(tool_id, events.LINE, None)
            monitoring.register_callback(tool_id, events.PY_RETURN, None)
            monitoring.register_callback(tool_id, events.RAISE, None)
            monitoring.free_tool_id(tool_id)

    def retain(self):
        if self.broken or not self.records:
            return
        if self.finish is not None:
            self._record("finish", **self.finish)
        raw = b"".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            for row in self.records
        )
        evidence.read_trace(raw)
        with (Path("/validation-results") / evidence.TRACE).open("xb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())


def pytest_configure(config):
    if (
        os.environ.get("CTCC_ORIGINAL_FINANCIAL_V3_EXPIRY") != "1"
        or config.args != [evidence.NODE]
        or config.option.collectonly
    ):
        raise RuntimeError("financial_v3_trace_configuration_invalid")


def pytest_collection_finish(session):
    if [item.nodeid for item in session.items] != [evidence.NODE]:
        raise RuntimeError("financial_v3_trace_node_changed")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    if item.nodeid != evidence.NODE or sys.gettrace() is not None:
        raise RuntimeError("financial_v3_trace_context_invalid")
    expected = Path(__file__).resolve().parents[1] / evidence.NODE.split("::")[0]
    if (
        item.path.resolve(strict=True) != expected
        or Path(item.module.__file__).resolve(strict=True) != expected
    ):
        raise RuntimeError("financial_v3_trace_module_changed")
    trace = _Trace(item.module.original._request)
    try:
        with trace.monitor():
            return (yield)
    finally:
        trace.retain()
