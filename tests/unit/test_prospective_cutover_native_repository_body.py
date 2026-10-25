"""Prepared synthetic repository orchestration tests; never native acceptance.

Run only against the separately admitted G6 R2 source. The real public MOVE,
NativeProspectiveCutoverRepository._append_native, _settings, _operands_locked,
_record and all original source/context/deadline fences execute unchanged.

Explicit synthetic seams are SQL/session observations, the upstream V1 native
DB provider, ledger observations and catalog observation verifiers. No synthetic
native catalog seal is installed: baseline None and UNSEALED remain unchanged.
The fake store cannot prove PostgreSQL uniqueness, ACL, durability or locking.
The ordinary V1 synthetic fixture obtains its owner through the real issuer;
this file never builds an owner from a saved receipt or writes owner registries.
"""

import asyncio
import inspect
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.database.repositories import controlled_writer_coverage as old_repository
from app.database.repositories import prospective_event_cutover_v2 as repository
from app.database.repositories.qualification_ledger import (
    QualificationLedgerRepository,
    _ConsumedEventJournalInspection,
)
from app.domain import prospective_event_cutover_catalog_v2 as catalog
from app.domain import prospective_event_cutover_v2 as records
from app.domain.source_primitives import utc_from_ns
from app.trade_qualification import controlled_writer_coverage_v1 as coverage
from app.trade_qualification import demo_control as control
from app.trade_qualification import prospective_event_cutover_v2 as issuer
from tests.prospective_cutover_fixtures import empty_scan_document
from tests.unit.test_controlled_writer_coverage_contracts import (
    synthetic_io as synthetic_io,  # noqa: PLC0414 -- explicit pytest fixture re-export.
)

_METHOD_NAMES = (
    "append_native",
    "_append_native",
    "_settings",
    "_operands_locked",
    "_record",
)
_ACTUAL_METHODS = {
    name: getattr(repository.NativeProspectiveCutoverRepository, name)
    for name in _METHOD_NAMES
}
_UNSEALED = "UNSEALED_REQUIRES_NATIVE_PG17_CAPTURE"


def _assert_unsealed():
    assert catalog.PG17_DEPARSED_BASELINE is None
    assert catalog.PG17_DEPARSED_BASELINE_STATUS == _UNSEALED


class _SyntheticTransaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        assert not self.session.active
        self.session.active = True
        return self

    async def __aexit__(self, exc_type, _value, _traceback):
        session, db = self.session, self.session.db
        try:
            if exc_type is not None:
                db.events.append(("rollback", session.ordinal))
                return False
            if session.pending is not None:
                key, entry = session.pending
                assert key not in db.markers
                db.markers[key] = entry
                db.events.append(("append_committed", session.ordinal))
            else:
                db.events.append(("read_transaction_committed", session.ordinal))
            if session.ordinal == 1:
                db.first_commit()
            # Deliberately cross an await after the synthetic commit. Source
            # guards must reject observations changed before control returns.
            await asyncio.sleep(0)
            if db.unknown_commit_session == session.ordinal:
                raise RuntimeError("synthetic_commit_ack_unknown")
            return False
        finally:
            session.active = False


class _SyntheticSession:
    def __init__(self, db, ordinal):
        self.db = db
        self.ordinal = ordinal
        self.active = False
        self.closed = False
        self.pending = None

    async def __aenter__(self):
        self.db.events.append(("session_enter", self.ordinal))
        return self

    async def __aexit__(self, _exc_type, _value, _traceback):
        self.closed = True
        self.db.events.append(("session_close", self.ordinal))
        if self.ordinal == 2:
            self.db.second_close()
        await asyncio.sleep(0)
        return False

    def begin(self):
        return _SyntheticTransaction(self)

    def in_transaction(self):
        return self.active

    async def execute(self, statement, _params=None):
        assert self.active
        sql = str(statement)
        assert sql.startswith("SET ")
        self.db.settings.append((self.ordinal, sql))

    async def scalar(self, statement, params=None):
        assert self.active
        db, sql = self.db, str(statement)
        if sql == repository.history_catalog.CATALOG_SQL:
            db.catalog_queries.append((self.ordinal, "parent"))
            return db.parent_catalog_sentinel
        if sql == catalog.CATALOG_SQL:
            db.catalog_queries.append((self.ordinal, "0051"))
            return db.cutover_catalog_sentinel
        if sql.startswith(
            "SELECT public.controlled_writer_coverage_v2_cutover_append("
        ):
            db.append_calls += 1
            assert set(params) == {"environment", "uid", "epoch", "raw"}
            value = records.decode(params["raw"])
            key = (params["environment"], params["uid"], params["epoch"])
            assert key == (value["environment"], value["account_id"], value["epoch_id"])
            if key in db.markers:
                raise RuntimeError("synthetic_uid_epoch_unique_conflict")
            entry = {
                "raw": params["raw"],
                "sha": records.digest(params["raw"]),
                "recorded_at": utc_from_ns(db.io.stamp()["utc_ns"]),
                "coverage_start_at": records.stamp_utc(value["coverage_start"]),
            }
            self.pending = key, entry
            db.events.append(("append_response", self.ordinal))
            return entry["sha"]
        if sql.startswith("SELECT public.controlled_writer_coverage_v2_cutover_read("):
            db.read_calls += 1
            assert set(params) == {"environment", "uid", "epoch", "expected"}
            assert self.ordinal == 2 and db.sessions[0].closed
            assert ("append_committed", 1) in db.events
            if db.read_failure:
                raise RuntimeError("synthetic_readback_unavailable")
            key = (params["environment"], params["uid"], params["epoch"])
            entry = db.markers[key]
            assert entry["sha"] == params["expected"]
            result = {
                "record_json": entry["raw"].decode("ascii"),
                "record_sha256": entry["sha"],
                "recorded_at": entry["recorded_at"].isoformat(),
                "coverage_start_at": entry["coverage_start_at"].isoformat(),
            }
            if db.corrupt_readback == "raw":
                result["record_json"] += " "
            elif db.corrupt_readback == "sha":
                result["record_sha256"] = "0" * 64
            db.events.append(("read_response", self.ordinal))
            return result
        raise AssertionError("unreviewed_synthetic_sql_observation")


class _SyntheticFactory:
    def __init__(self, db, original):
        self.db = db
        self.kw = dict(original.kw)

    def __call__(self):
        session = _SyntheticSession(self.db, len(self.db.sessions) + 1)
        self.db.sessions.append(session)
        return session


@pytest.fixture
def native_repository_io(monkeypatch, synthetic_io):
    """Explicit test-only observations; no native source/catalog assertion."""
    _assert_unsealed()
    io = synthetic_io
    db = SimpleNamespace(
        io=io,
        markers={},
        sessions=[],
        settings=[],
        events=[],
        catalog_queries=[],
        append_calls=0,
        read_calls=0,
        scan_calls=0,
        native_locked_calls=0,
        first_commit=lambda: None,
        second_close=lambda: None,
        unknown_commit_session=None,
        read_failure=False,
        corrupt_readback=None,
        other_currency_exposure=False,
        journal_changed=False,
        parent_catalog_sentinel=object(),
        cutover_catalog_sentinel=object(),
    )
    io.factory = _SyntheticFactory(db, io.factory)

    parent_verify = repository.history_catalog.verify_account_history_partition_catalog
    cutover_verify = catalog.verify_catalog
    role_verify = catalog.verify_runtime_role

    def parent_observation(value):
        _assert_unsealed()
        if value is not db.parent_catalog_sentinel:
            return parent_verify(value)
        return None

    def cutover_observation(value, *, require_native_baseline=True):
        _assert_unsealed()
        assert require_native_baseline is True
        if value is not db.cutover_catalog_sentinel:
            return cutover_verify(
                value, require_native_baseline=require_native_baseline
            )
        return None

    def runtime_role_observation(value):
        _assert_unsealed()
        if value is not db.cutover_catalog_sentinel:
            return role_verify(value)
        return None

    async def upstream_native_db_observation(
        self, session, binding, key, expected=None
    ):
        # This is the earlier V1 native DB observation seam, not a replacement
        # of any NativeProspectiveCutoverRepository method. Replay the original
        # source binding before emitting explicitly synthetic Control/B5 facts.
        assert session.active and key is coverage._HOOK_KEY
        held = coverage._repository_binding_v1(binding, key=key)
        assert self._factory is io.factory is held["session_factory"]
        db.native_locked_calls += 1
        if control.document(io.control) != control.document(held["control"].state):
            raise coverage.ControlledWriterCoverageError(
                "controlled_writer_coverage_control_changed"
            )
        active = io.records[-1]
        assert expected == (
            active["state"]["journal_sequence"],
            active["event_sha256"],
        )
        audit = SimpleNamespace(
            document_json=active["audit_raw"].encode("ascii"),
            state_sha256=records.digest(active["audit_raw"].encode("ascii")),
            known_flat=not db.other_currency_exposure,
        )
        return held, io.stamp(), audit, {"explicit_synthetic_observation": True}

    async def locked_observation(self, session, scope, *, create=False):
        assert self.session_factory is io.factory and session.active
        assert create is False
        assert scope.account_id == io.scope.account_id
        return SimpleNamespace(scope=scope, explicit_synthetic_observation=True)

    async def journal_observation(self, session, scope, selected):
        assert self.session_factory is io.factory and session.active
        assert selected.scope == scope and selected.explicit_synthetic_observation
        db.scan_calls += 1
        at = utc_from_ns(io.stamp()["utc_ns"])
        document = empty_scan_document(scope.account_id, at)
        if db.journal_changed:
            document["rows_sha256"] = "f" * 64
        return _ConsumedEventJournalInspection(
            frozenset(), records.canonical(document), at, at
        )

    monkeypatch.setattr(
        repository.history_catalog,
        "verify_account_history_partition_catalog",
        parent_observation,
    )
    monkeypatch.setattr(catalog, "verify_catalog", cutover_observation)
    monkeypatch.setattr(catalog, "verify_runtime_role", runtime_role_observation)
    monkeypatch.setattr(
        old_repository.ControlledWriterCoverageRepository,
        "_native_locked",
        upstream_native_db_observation,
        raising=False,
    )
    monkeypatch.setattr(QualificationLedgerRepository, "_locked", locked_observation)
    monkeypatch.setattr(
        QualificationLedgerRepository,
        "_inspect_prospective_event_journal_locked",
        journal_observation,
    )
    # Only the declared synthetic observations above differ. Keep the original
    # MOVE/context/source checks active and pin these fixed unit observations.
    monkeypatch.setattr(
        issuer,
        "_PINS",
        tuple(
            (owner, name, getattr(owner, name)) for owner, name, _old in issuer._PINS
        ),
    )
    for name, original in _ACTUAL_METHODS.items():
        assert getattr(repository.NativeProspectiveCutoverRepository, name) is original
    owners_before = set(issuer._OWNERS)
    try:
        yield io, db
    finally:
        for owner in set(issuer._OWNERS) - owners_before:
            owner.close()
        _assert_unsealed()
        for name, original in _ACTUAL_METHODS.items():
            assert (
                getattr(repository.NativeProspectiveCutoverRepository, name) is original
            )


async def _begin(io):
    # Native registry owner is issued, then MOVED by the actual public function.
    original = await io.open_owner()
    binding = coverage._OWNERS[original]
    pending = issuer.commit_prospective_event_cutover_v2(original)
    assert original not in coverage._OWNERS
    assert coverage._CUTOVERS[binding] is True
    return original, binding, pending


def _assert_burned(original, binding, pending):
    assert original not in coverage._OWNERS
    assert binding not in coverage._BINDINGS
    assert binding not in coverage._CUTOVERS
    assert inspect.getcoroutinestate(pending._inner) == inspect.CORO_CLOSED
    assert inspect.getcoroutinestate(pending._coroutine) == inspect.CORO_CLOSED
    with pytest.raises(
        coverage.ControlledWriterCoverageError, match="owner_unavailable"
    ):
        issuer.commit_prospective_event_cutover_v2(original)


@pytest.mark.asyncio
async def test_actual_body_commits_then_reads_exact_marker_in_distinct_closed_session(
    native_repository_io,
):
    io, db = native_repository_io
    original, binding, pending = await _begin(io)
    owner = await pending
    assert type(owner) is issuer.OwnedProspectiveDemoCutoverV2
    assert len(db.sessions) == 2 and all(session.closed for session in db.sessions)
    assert db.append_calls == db.read_calls == 1 and len(db.markers) == 1
    assert db.native_locked_calls == db.scan_calls == 2
    assert db.catalog_queries == [
        (1, "parent"),
        (1, "0051"),
        (2, "parent"),
        (2, "0051"),
    ]
    assert len(db.settings) == 12
    assert db.events.index(("append_committed", 1)) < db.events.index(
        ("session_enter", 2)
    )
    saved = issuer._OWNERS[owner]["record"]
    document = records.decode(saved.record_json)
    assert all(document[name] is False for name in records.FALSE_FIELDS)
    assert (
        document["coverage_start"]["utc_ns"]
        > document["post_active_readback"]["utc_ns"]
    )
    assert io.control.emergency_stop is True and io.control.arm_request_id is None
    _assert_burned(original, binding, pending)
    owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown_session", [1, 2])
async def test_actual_body_unknown_commit_retains_marker_and_never_retries(
    native_repository_io,
    unknown_session,
):
    io, db = native_repository_io
    db.unknown_commit_session = unknown_session
    original, binding, pending = await _begin(io)
    with pytest.raises(
        records.ProspectiveCutoverError, match="commit_or_readback_unknown"
    ):
        await pending
    assert len(db.markers) == 1 and db.append_calls == 1
    assert db.read_calls == (0 if unknown_session == 1 else 1)
    assert len(db.sessions) == unknown_session
    assert all(session.closed for session in db.sessions)
    assert not issuer._OWNERS
    _assert_burned(original, binding, pending)


@pytest.mark.asyncio
async def test_actual_body_second_session_readback_failure_preserves_committed_marker(
    native_repository_io,
):
    io, db = native_repository_io
    db.read_failure = True
    original, binding, pending = await _begin(io)
    with pytest.raises(
        records.ProspectiveCutoverError, match="commit_or_readback_unknown"
    ):
        await pending
    assert len(db.markers) == 1 and db.append_calls == db.read_calls == 1
    assert len(db.sessions) == 2 and all(session.closed for session in db.sessions)
    assert not issuer._OWNERS
    _assert_burned(original, binding, pending)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["raw", "sha"])
async def test_actual_body_readback_mismatch_cannot_issue_owner_or_delete_marker(
    native_repository_io,
    damage,
):
    io, db = native_repository_io
    db.corrupt_readback = damage
    original, binding, pending = await _begin(io)
    with pytest.raises(records.ProspectiveCutoverError, match="readback_mismatch"):
        await pending
    assert len(db.markers) == 1 and db.append_calls == db.read_calls == 1
    assert not issuer._OWNERS
    _assert_burned(original, binding, pending)


@pytest.mark.asyncio
async def test_actual_body_source_change_at_final_session_close_denies_after_readback(
    native_repository_io,
):
    io, db = native_repository_io
    db.second_close = lambda: setattr(io, "file_revision", io.file_revision + 1)
    original, binding, pending = await _begin(io)
    with pytest.raises(
        records.ProspectiveCutoverError, match="commit_or_readback_unknown"
    ):
        await pending
    assert ("read_response", 2) in db.events and ("session_close", 2) in db.events
    assert len(db.markers) == 1 and db.append_calls == db.read_calls == 1
    assert not issuer._OWNERS
    _assert_burned(original, binding, pending)


@pytest.mark.asyncio
async def test_original_deadline_expires_after_move_before_first_await_without_sql_work(
    native_repository_io,
):
    io, db = native_repository_io
    original, binding, pending = await _begin(io)
    io.offset_ns += 31_000_000_000
    with pytest.raises(
        records.ProspectiveCutoverError, match="commit_or_readback_unknown"
    ):
        await pending
    assert len(db.sessions) == 1 and db.sessions[0].closed and not db.markers
    assert not db.settings and not db.catalog_queries
    assert db.native_locked_calls == db.scan_calls == 0
    assert db.append_calls == db.read_calls == 0 and not issuer._OWNERS
    _assert_burned(original, binding, pending)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["first_commit", "second_close"])
async def test_actual_body_cannot_renew_original_deadline_after_committed_marker(
    native_repository_io,
    boundary,
):
    io, db = native_repository_io
    setattr(
        db, boundary, lambda: setattr(io, "offset_ns", io.offset_ns + 31_000_000_000)
    )
    original, binding, pending = await _begin(io)
    with pytest.raises(
        records.ProspectiveCutoverError, match="commit_or_readback_unknown"
    ):
        await pending
    assert len(db.markers) == 1 and db.append_calls == 1
    assert db.read_calls == (0 if boundary == "first_commit" else 1)
    assert not issuer._OWNERS
    _assert_burned(original, binding, pending)


@pytest.mark.asyncio
@pytest.mark.parametrize("movement", ["before_append", "after_commit"])
async def test_actual_operands_reject_changed_all_currency_b5_observation(
    native_repository_io,
    movement,
):
    io, db = native_repository_io
    if movement == "before_append":
        db.other_currency_exposure = True
    else:
        db.first_commit = lambda: setattr(db, "other_currency_exposure", True)
    original, binding, pending = await _begin(io)
    with pytest.raises(records.ProspectiveCutoverError, match="local_audit_changed"):
        await pending
    expected = int(movement == "after_commit")
    assert len(db.markers) == db.append_calls == expected and db.read_calls == 0
    assert not issuer._OWNERS
    _assert_burned(original, binding, pending)


@pytest.mark.asyncio
async def test_actual_body_rechecks_db_control_in_separate_readback_transaction(
    native_repository_io,
):
    io, db = native_repository_io
    db.first_commit = lambda: setattr(
        io, "control", replace(io.control, revision=io.control.revision + 1)
    )
    original, binding, pending = await _begin(io)
    with pytest.raises(
        records.ProspectiveCutoverError, match="commit_or_readback_unknown"
    ):
        await pending
    assert db.native_locked_calls == 2 and db.read_calls == 0
    assert len(db.markers) == 1 and not issuer._OWNERS
    _assert_burned(original, binding, pending)


@pytest.mark.asyncio
async def test_control_change_after_final_lock_release_leaves_only_historical_custody(
    native_repository_io,
):
    io, db = native_repository_io
    original, binding, pending = await _begin(io)
    pinned_revision = io.control.revision
    db.second_close = lambda: setattr(
        io, "control", replace(io.control, revision=io.control.revision + 1)
    )
    owner = await pending
    document = records.decode(issuer._OWNERS[owner]["record"].record_json)
    assert document["control_revision"] == pinned_revision < io.control.revision
    # This intentional race documents the contract: final synchronous fences
    # replay source/cached lease, not DB Control after its locks are released.
    # The object remains historical custody, never a current G6/order permit.
    assert all(document[name] is False for name in records.FALSE_FIELDS)
    assert io.control.emergency_stop is True and io.control.arm_request_id is None
    assert len(db.markers) == 1 and db.append_calls == db.read_calls == 1
    _assert_burned(original, binding, pending)
    owner.close()


@pytest.mark.asyncio
async def test_actual_body_duplicate_uid_epoch_preserves_preexisting_marker_without_retry(
    native_repository_io,
):
    io, db = native_repository_io
    original, binding, pending = await _begin(io)
    active = io.records[-1]["state"]
    key = ("demo", io.scope.account_id, active["epoch_id"])
    existing = {"raw": b"EXPLICIT_SYNTHETIC_PREEXISTING_IMMUTABLE_MARKER"}
    db.markers[key] = existing
    with pytest.raises(
        records.ProspectiveCutoverError, match="commit_or_readback_unknown"
    ):
        await pending
    assert db.markers == {key: existing}
    assert db.append_calls == 1 and db.read_calls == 0 and len(db.sessions) == 1
    assert not issuer._OWNERS
    _assert_burned(original, binding, pending)


@pytest.mark.asyncio
async def test_actual_body_changed_journal_digest_after_commit_denies_before_marker_read(
    native_repository_io,
):
    io, db = native_repository_io
    db.first_commit = lambda: setattr(db, "journal_changed", True)
    original, binding, pending = await _begin(io)
    with pytest.raises(records.ProspectiveCutoverError, match="journal_changed"):
        await pending
    assert db.scan_calls == 2 and db.append_calls == 1 and db.read_calls == 0
    assert len(db.markers) == 1 and not issuer._OWNERS
    _assert_burned(original, binding, pending)
