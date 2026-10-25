"""Prepared synthetic IO tests. No native catalog, G6 or execution acceptance.

The actual NativeNamespacePhaseRepositoryV1 methods run. Replaced observations
are the existing upstream native DB provider, SQL/catalog results, full Control
row provider and ledger row provider. Original coverage MOVE/context/source
deadline fences remain active. The test never writes an owner registry.
"""

import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest

from app.database.repositories import native_namespace_phase_v1 as repository
from app.database.repositories.demo_control import DemoControlRepository
from app.database.repositories.qualification_ledger import (
    QualificationLedgerRepository,
    _ConsumedEventJournalInspection,
)
from app.domain import prospective_event_cutover_catalog_v2 as catalog
from app.domain import prospective_event_cutover_v2 as records
from app.domain.source_primitives import utc_from_ns
from app.trade_qualification import controlled_writer_coverage_v1 as coverage
from app.trade_qualification import native_namespace_phase_v1 as phase
from tests.prospective_cutover_fixtures import (
    cutover_record_fixture,
    empty_scan_document,
)
from tests.unit.test_controlled_writer_coverage_contracts import (
    synthetic_io as synthetic_io,  # noqa: PLC0414 -- explicit pytest fixture re-export.
)

_ACTUAL = (
    repository.NativeNamespacePhaseRepositoryV1._read_once,
    repository.NativeNamespacePhaseRepositoryV1.read_native,
)
_UNSEALED = "UNSEALED_REQUIRES_NATIVE_PG17_CAPTURE"


def _unsealed():
    assert catalog.PG17_DEPARSED_BASELINE is None
    assert catalog.PG17_DEPARSED_BASELINE_STATUS == _UNSEALED


class _Transaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        self.session.active = True
        return self

    async def __aexit__(self, exc, _value, _tb):
        self.session.active = False
        self.session.db.events.append(("transaction_end", self.session.ordinal))
        if exc is None and self.session.db.unknown_commit == self.session.ordinal:
            raise RuntimeError("explicit_synthetic_commit_unknown")
        return False


class _Session:
    def __init__(self, db, ordinal):
        self.db, self.ordinal, self.active, self.closed = db, ordinal, False, False

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc, _value, _tb):
        self.closed = True
        self.db.events.append(("session_closed", self.ordinal))
        if self.ordinal == 2:
            self.db.second_close()
        await asyncio.sleep(0)
        return False

    def begin(self):
        return _Transaction(self)

    async def scalar(self, statement, params=None):
        assert self.active
        sql = str(statement)
        if sql == catalog.CATALOG_SQL:
            return self.db.catalog_sentinel
        assert sql.startswith(
            "SELECT public.controlled_writer_coverage_v2_cutover_read("
        )
        self.db.reads += 1
        original = self.db.historical["record"]
        value = records.decode(original.record_json)
        assert params == {
            "environment": "demo",
            "uid": value["account_id"],
            "epoch": value["epoch_id"],
            "expected": original.record_sha256,
        }
        if self.db.read_failure == self.ordinal:
            raise RuntimeError("explicit_synthetic_read_failure")
        return {
            "record_json": original.record_json.decode(),
            "record_sha256": "0" * 64
            if self.db.corrupt_marker
            else original.record_sha256,
            "recorded_at": original.recorded_at.isoformat(),
            "coverage_start_at": original.coverage_start_at.isoformat(),
        }

    async def execute(self, statement):
        assert self.active
        assert (
            str(statement)
            == "SELECT pg_catalog.txid_current()::text,pg_catalog.clock_timestamp()"
        )
        transaction = "1001" if self.db.same_transaction else str(1000 + self.ordinal)
        at = utc_from_ns(self.db.io.stamp()["utc_ns"])
        return SimpleNamespace(one=lambda: (transaction, at))


class _Factory:
    def __init__(self, db, original):
        self.db, self.kw = db, dict(original.kw)

    def __call__(self):
        result = _Session(self.db, len(self.db.sessions) + 1)
        self.db.sessions.append(result)
        return result


@pytest.fixture
def native_phase_io(monkeypatch, synthetic_io):
    _unsealed()
    io = synthetic_io
    db = SimpleNamespace(
        io=io,
        sessions=[],
        events=[],
        reads=0,
        scans=0,
        native_locks=0,
        catalog_sentinel=object(),
        historical=None,
        unknown_commit=None,
        read_failure=None,
        corrupt_marker=False,
        same_transaction=False,
        changed_scan=False,
        other_currency=False,
        second_close=lambda: None,
    )
    io.factory = _Factory(db, io.factory)
    original_catalog = catalog.verify_catalog
    original_role = catalog.verify_runtime_role

    def observed_catalog(value, *, require_native_baseline=True):
        _unsealed()
        assert require_native_baseline is True
        if value is not db.catalog_sentinel:
            return original_catalog(value, require_native_baseline=True)
        return None

    def observed_role(value):
        _unsealed()
        if value is not db.catalog_sentinel:
            return original_role(value)
        return None

    async def upstream_native(self, session, binding, key, expected=None):
        assert session.active and key is coverage._HOOK_KEY
        state, stamp = coverage._namespace_phase_state_v1(binding, key=phase._HOOK_KEY)
        active = state["active"]
        assert self._factory is state["factory"] is io.factory
        assert expected == (active.journal_sequence, active.journal_sha256)
        db.native_locks += 1
        audit = SimpleNamespace(
            document_json=active.audit_json.encode(), known_flat=not db.other_currency
        )
        held = coverage._repository_binding_v1(binding, key=coverage._HOOK_KEY)
        return held, stamp, audit, json.loads(active.records_json)

    async def observed_lineage(session, state, event):
        assert session.active and state == io.control and event == io.control_event
        # A declared row-provider sentinel can exercise repository orchestration
        # only. The actual owner join rejects this non-ControlLineageV1 type.
        return SimpleNamespace(receipt_json="EXPLICIT_SYNTHETIC_CONTROL_ROW_PROVIDER")

    async def locked(self, session, scope, *, create=False):
        assert self.session_factory is io.factory and session.active and create is False
        return SimpleNamespace(scope=scope)

    async def observed_scan(self, session, scope, selected):
        assert (
            self.session_factory is io.factory
            and session.active
            and selected.scope == scope
        )
        db.scans += 1
        at = utc_from_ns(io.stamp()["utc_ns"])
        document = empty_scan_document(scope.account_id, at)
        if db.changed_scan and session.ordinal == 2:
            document["rows_sha256"] = "f" * 64
        return _ConsumedEventJournalInspection(
            frozenset(), records.canonical(document), at, at
        )

    monkeypatch.setattr(catalog, "verify_catalog", observed_catalog)
    monkeypatch.setattr(catalog, "verify_runtime_role", observed_role)
    monkeypatch.setattr(
        DemoControlRepository, "_read_lineage_prefix", staticmethod(observed_lineage)
    )
    monkeypatch.setattr(QualificationLedgerRepository, "_locked", locked)
    monkeypatch.setattr(
        QualificationLedgerRepository,
        "_inspect_prospective_event_journal_locked",
        observed_scan,
    )
    monkeypatch.setattr(
        coverage,
        "_FUNCTION_PINS",
        tuple(
            (owner, name, getattr(owner, name))
            for owner, name, _ in coverage._FUNCTION_PINS
        ),
    )

    async def start():
        owner = await io.open_owner()
        binding = coverage._take_namespace_phase_v1(owner, key=phase._HOOK_KEY)
        state, _ = coverage._namespace_phase_state_v1(binding, key=phase._HOOK_KEY)
        monkeypatch.setattr(
            type(state["repository"]), "_native_locked", upstream_native, raising=False
        )
        measured = io.stamp()
        start = {name: measured[name] for name in ("utc_ns", "monotonic_ns")}
        # This internal input is explicitly synthetic and cannot mint a cutover
        # owner or bypass the public owner's private MOVE registry.
        raw = cutover_record_fixture(
            template=state["template"],
            active=state["active"],
            post_active=state["activation_complete"],
            start=start,
            scan=empty_scan_document(io.scope.account_id, utc_from_ns(start["utc_ns"])),
        )
        at = records.stamp_utc(start)
        db.historical = {
            "record": SimpleNamespace(
                record_json=raw,
                record_sha256=records.digest(raw),
                recorded_at=at,
                coverage_start_at=at,
            )
        }
        db.binding = binding
        return binding

    db.start = start
    yield db
    if hasattr(db, "binding"):
        coverage._release_namespace_phase_v1(db.binding, key=phase._HOOK_KEY)
    _unsealed()
    assert _ACTUAL == (
        repository.NativeNamespacePhaseRepositoryV1._read_once,
        repository.NativeNamespacePhaseRepositoryV1.read_native,
    )


@pytest.mark.asyncio
async def test_actual_native_body_two_independent_reads_accept_new_observation_times(
    native_phase_io,
):
    db = native_phase_io
    binding = await db.start()
    native = repository.NativeNamespacePhaseRepositoryV1(db.io.factory)
    result = await native.read_native(binding, db.historical, key=phase._HOOK_KEY)
    assert type(result) is repository._NativePhaseReadback
    assert len(db.sessions) == db.reads == db.scans == db.native_locks == 2
    assert all(session.closed and not session.active for session in db.sessions)
    assert db.events.index(("session_closed", 1)) < db.events.index(
        ("session_closed", 2)
    )
    with pytest.raises(records.ProspectiveCutoverError, match="attempt_burned"):
        await native.read_native(binding, db.historical, key=phase._HOOK_KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["same_transaction", "changed_scan", "corrupt_marker", "other_currency"]
)
async def test_actual_native_body_rejects_conflicting_observations(
    native_phase_io, change
):
    db = native_phase_io
    binding = await db.start()
    setattr(db, change, True)
    with pytest.raises(records.ProspectiveCutoverError):
        await repository.NativeNamespacePhaseRepositoryV1(db.io.factory).read_native(
            binding, db.historical, key=phase._HOOK_KEY
        )
    assert len(db.sessions) <= 2 and all(session.closed for session in db.sessions)


@pytest.mark.asyncio
@pytest.mark.parametrize("first", [1, 2])
async def test_actual_native_body_commit_unknown_is_not_retried(native_phase_io, first):
    db = native_phase_io
    binding = await db.start()
    db.unknown_commit = first
    with pytest.raises(
        records.ProspectiveCutoverError, match="native_read_unavailable"
    ):
        await repository.NativeNamespacePhaseRepositoryV1(db.io.factory).read_native(
            binding, db.historical, key=phase._HOOK_KEY
        )
    assert len(db.sessions) == first and all(session.closed for session in db.sessions)


@pytest.mark.asyncio
@pytest.mark.parametrize("late_change", ["source_files", "deadline"])
async def test_actual_native_body_final_session_close_replays_original_source(
    native_phase_io, late_change
):
    db = native_phase_io
    binding = await db.start()

    def mutate():
        if late_change == "source_files":
            db.io.file_revision += 1
        else:
            db.io.offset_ns += 30_000_000_000

    db.second_close = mutate
    with pytest.raises(
        records.ProspectiveCutoverError, match="native_read_unavailable"
    ):
        await repository.NativeNamespacePhaseRepositoryV1(db.io.factory).read_native(
            binding, db.historical, key=phase._HOOK_KEY
        )
    assert len(db.sessions) == 2 and all(session.closed for session in db.sessions)


@pytest.mark.parametrize(
    "which",
    ["unstarted_close", "unstarted_throw", "normal_terminal", "failure_terminal"],
)
def test_fixed_wrapper_closes_unstarted_or_terminal_attempts_without_gc(
    monkeypatch, which
):
    released = []
    marker = object()
    monkeypatch.setattr(
        coverage,
        "_release_namespace_phase_v1",
        lambda binding, *, key: released.append((binding, key)),
    )

    async def inner():
        return None

    native = inner()

    async def outer():
        if which == "failure_terminal":
            raise RuntimeError("synthetic_failure")
        return 7

    held = phase._PhaseAttempt(outer(), native, marker)
    if which == "unstarted_close":
        held.close()
    elif which == "unstarted_throw":
        with pytest.raises(RuntimeError):
            held.throw(RuntimeError("synthetic_prestart"))
    elif which == "failure_terminal":
        with pytest.raises(RuntimeError):
            held.send(None)
    else:
        with pytest.raises(StopIteration):
            held.send(None)
    assert inspect.getcoroutinestate(native) == inspect.CORO_CLOSED
    assert released == [(marker, phase._HOOK_KEY)]
    held.close()
    assert len(released) == 1


def test_saved_dto_or_directly_constructed_owner_cannot_enter_native_join():
    for candidate in (
        object(),
        SimpleNamespace(passed=True),
        object.__new__(phase.OwnedNativeNamespacePhaseV1),
    ):
        with pytest.raises(
            records.ProspectiveCutoverError, match="previous_owner_unavailable"
        ):
            phase.join_owned_native_namespace_phase_v1(candidate, object())


@pytest.mark.asyncio
async def test_prestart_task_cancellation_closes_both_held_coroutines(monkeypatch):
    released = []
    marker = object()
    monkeypatch.setattr(
        coverage,
        "_release_namespace_phase_v1",
        lambda binding, *, key: released.append(binding),
    )

    async def native():
        await asyncio.Event().wait()

    inner = native()

    async def outer():
        return await inner

    held = phase._PhaseAttempt(outer(), inner, marker)
    task = asyncio.create_task(held)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert inspect.getcoroutinestate(inner) == inspect.CORO_CLOSED
    assert inspect.getcoroutinestate(held._outer) == inspect.CORO_CLOSED
    assert released == [marker]


def test_successful_wrapper_transfer_keeps_binding_until_new_owner_release(monkeypatch):
    released = []
    marker = object()
    monkeypatch.setattr(
        coverage,
        "_release_namespace_phase_v1",
        lambda binding, *, key: released.append(binding),
    )

    async def native():
        return None

    inner = native()

    async def outer():
        held._binding = None
        return marker

    held = phase._PhaseAttempt(outer(), inner, marker)
    with pytest.raises(StopIteration) as result:
        held.send(None)
    assert result.value.value is marker
    assert released == []
    phase._release({"binding": marker})
    assert released == [marker]


@pytest.mark.parametrize(
    "changed",
    ["_historical_context", "_take_previous", "_release", "cutover_move", "_join"],
)
def test_direct_entry_pin_fence_precedes_mutable_context_or_move_helpers(
    monkeypatch, changed
):
    entry = phase.join_owned_native_namespace_phase_v1
    with pytest.raises(
        records.ProspectiveCutoverError, match="previous_owner_unavailable"
    ):
        entry(object(), object())
    called = []

    def replaced(*args, **kwargs):
        called.append(True)
        raise AssertionError("mutable helper must not run")

    if changed == "cutover_move":
        monkeypatch.setattr(phase.cutover, "_take_native_namespace_phase_v1", replaced)
    else:
        monkeypatch.setattr(phase, changed, replaced)
    with pytest.raises(records.ProspectiveCutoverError, match="function_changed"):
        entry(object(), object())
    assert called == []
