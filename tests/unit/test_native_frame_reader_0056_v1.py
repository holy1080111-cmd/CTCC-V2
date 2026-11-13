"""DB0056 held-frame wiring tests; synthetic rows never establish admission."""

from datetime import UTC, datetime

import pytest

from app.core import native_frame_reader_0056_v1 as subject
from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.core import native_role_preflight_v1 as roles55
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as catalog55,
)
from app.database.repositories import (
    collector_current_0056_multirole_catalog_v1 as catalog56,
)
from app.domain import collector_current_0055_catalog_v1 as old_catalog


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def one(self):
        return self.value

    def all(self):
        return self.value


class _SyncEngine:
    dialect = type("Dialect", (), {"name": "postgresql"})()


class _Engine:
    def __init__(self):
        self.sync_engine = _SyncEngine()
        self.url = "same-db"


class _Session:
    def __init__(self, role, engine, events, *, head="0056", mode="off"):
        self.role = role
        self.engine = engine
        self.events = events
        self.head = head
        self.mode = mode
        self.closed = False
        self.new = set()
        self.dirty = set()
        self.deleted = set()
        self.sync_session = self
        self.transaction = object()

    def in_transaction(self):
        return True

    def get_nested_transaction(self):
        return None

    def get_transaction(self):
        return self.transaction

    def get_bind(self):
        return self.engine.sync_engine

    async def begin(self):
        self.events.append((self.role, "begin"))

    async def commit(self):
        self.events.append((self.role, "commit"))

    async def close(self):
        self.closed = True
        self.events.append((self.role, "close"))

    async def connection(self):
        return self

    async def execute(self, statement, parameters=None):
        sql = str(statement)
        self.events.append((self.role, sql))
        if sql == str(subject._FRAME_CONTEXT):
            if getattr(self, "replace_transaction", False):
                self.transaction = object()
            return _Result(("read committed", self.mode))
        if sql == str(roles55._IDENTITY_SQL):
            oid = {"writer": 201, "frame": 301}[self.role]
            return _Result(
                (
                    self.role,
                    self.role,
                    "none",
                    oid,
                    93,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    170011,
                    "127.0.0.1",
                    5432,
                )
            )
        if sql == str(roles56._HEAD):
            return _Result([(self.head, True)])
        if sql == subject.partition_policy.CATALOG_SQL:
            return _Result({"tables": [{"owner_oid": 101}]})
        if sql == subject.frame_policy.CATALOG_SQL:
            return _Result({"observed": "original"})
        if sql == subject.inherited.ACTOR_CATALOG_SQL:
            return _Result({"schema": old_catalog.SCHEMA, "role": self.role})
        if sql == str(roles55._OWNER_OID_SQL):
            return _Result(101)
        if sql == str(roles56._FITTED_DEMO_ACCESS):
            return _Result(
                [
                    (name, routine, False)
                    for name in parameters["roles"]
                    for routine in roles56._FITTED_FUNCTION_NAMES
                ]
            )
        assert sql.startswith("SET ")
        return _Result(None)


class _Factory:
    def __init__(self, role, engine, session=None):
        self.role = role
        self.kw = {"bind": engine}
        self.session = session

    def __call__(self):
        assert self.session is not None
        return self.session


class _Fence:
    def __init__(self):
        self.checks = 0

    def check(self):
        self.checks += 1


@pytest.fixture
def held(monkeypatch):
    events = []
    writer_engine, custody_engine, frame_engine = (_Engine() for _ in range(3))
    frame = _Session("frame", frame_engine, events)
    writer = _Session("writer", writer_engine, events)
    factories = (
        _Factory("writer", writer_engine, writer),
        _Factory("custody", custody_engine),
        _Factory("frame", frame_engine),
    )
    monkeypatch.setattr(subject, "AsyncSession", _Session)
    monkeypatch.setattr(
        catalog55,
        "_factory_route",
        lambda factory: ((None, factory.kw["bind"]), None, "same-route", factory.role),
    )
    monkeypatch.setattr(subject.selectors, "_database_route", lambda url: url)
    return frame, writer, factories, events, _Fence()


@pytest.mark.asyncio
async def test_actual_frame_queries_reach_strict_partition_and_original_helpers(
    held, monkeypatch
):
    frame, writer, factories, events, fence = held
    seen = []

    def partition(document):
        seen.append(("partition", document))

    def original(document, parent, *, approved_collector_role_oid):
        seen.append(("original", document, parent, approved_collector_role_oid))
        raise ValueError("synthetic catalog incomplete")

    monkeypatch.setattr(
        subject.partition_policy, "verify_account_history_partition_catalog", partition
    )
    monkeypatch.setattr(subject.frame_policy, "verify_catalog", original)
    with pytest.raises(subject.NativeFrameReader0056Error):
        await subject.verify_held_frame_catalog_0056_v1(frame, fence, *factories)
    assert seen == [
        ("partition", {"tables": [{"owner_oid": 101}]}),
        (
            "original",
            {"observed": "original"},
            {"tables": [{"owner_oid": 101}]},
            301,
        ),
    ]
    assert ("frame", subject.partition_policy.CATALOG_SQL) in events
    assert ("frame", subject.frame_policy.CATALOG_SQL) in events
    assert ("frame", str(roles56._HEAD)) in events
    assert not any(role == "writer" for role, _ in events)
    assert writer.closed is False and fence.checks >= 8


@pytest.mark.asyncio
async def test_actual_writer_acl_fitted_and_pair_reject_0055_graph(held, monkeypatch):
    frame, writer, factories, events, fence = held
    calls = []
    monkeypatch.setattr(
        subject.partition_policy,
        "verify_account_history_partition_catalog",
        lambda document: calls.append(("partition", document)),
    )
    monkeypatch.setattr(
        subject.frame_policy,
        "verify_catalog",
        lambda document, parent, *, approved_collector_role_oid: calls.append(
            ("original", approved_collector_role_oid)
        ),
    )

    async def acl(connection, names, oid):
        assert connection is writer
        calls.append(("acl", names, oid))

    monkeypatch.setattr(roles55, "_exact_existing_acl", acl)
    actual_pair = catalog56.verify_0056_pair

    def pair(raw, writer_graph, **identity):
        calls.append(("pair", raw, writer_graph, identity))
        return actual_pair(raw, writer_graph, **identity)

    monkeypatch.setattr(catalog56, "verify_0056_pair", pair)
    with pytest.raises(subject.NativeFrameReader0056Error):
        await subject.verify_held_frame_catalog_0056_v1(frame, fence, *factories)
    assert calls[0][0] == "partition" and calls[1] == ("original", 301)
    assert calls[2] == ("acl", ("writer", "custody", "frame"), 201)
    assert calls[3] == (
        "pair",
        {"schema": old_catalog.SCHEMA, "role": "frame"},
        {"schema": old_catalog.SCHEMA, "role": "writer"},
        {
            "raw_name": "frame",
            "writer_name": "writer",
            "approved_raw_role_oid": 301,
            "approved_writer_role_oid": 201,
            "parent_owner_oid": 101,
        },
    )
    assert ("writer", str(roles56._FITTED_DEMO_ACCESS)) in events
    assert ("writer", subject.inherited.ACTOR_CATALOG_SQL) in events
    assert ("frame", subject.inherited.ACTOR_CATALOG_SQL) in events
    assert writer.closed is True
    assert ("writer", "commit") not in events


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["read_only", "old_head"])
async def test_wrong_held_transaction_or_head_denied_before_catalog(held, fault):
    frame, writer, factories, events, fence = held
    if fault == "read_only":
        frame.mode = "on"
    else:
        frame.head = "0055"
    with pytest.raises(subject.NativeFrameReader0056Error):
        await subject.verify_held_frame_catalog_0056_v1(frame, fence, *factories)
    assert not any(
        sql in {subject.partition_policy.CATALOG_SQL, subject.frame_policy.CATALOG_SQL}
        for _, sql in events
    )
    assert writer.closed is False


@pytest.mark.asyncio
async def test_frame_factory_replacement_denied_before_query(held):
    frame, writer, factories, events, fence = held
    factories[2].kw["bind"] = _Engine()
    with pytest.raises(subject.NativeFrameReader0056Error):
        await subject.verify_held_frame_catalog_0056_v1(frame, fence, *factories)
    assert events == [] and writer.closed is False


@pytest.mark.asyncio
async def test_pending_frame_orm_write_is_denied_before_catalog(held):
    frame, writer, factories, events, fence = held
    frame.new.add(object())
    with pytest.raises(subject.NativeFrameReader0056Error):
        await subject.verify_held_frame_catalog_0056_v1(frame, fence, *factories)
    assert events == [] and writer.closed is False


@pytest.mark.asyncio
async def test_replaced_caller_transaction_denied_before_catalog(held):
    frame, writer, factories, events, fence = held
    frame.replace_transaction = True
    with pytest.raises(subject.NativeFrameReader0056Error):
        await subject.verify_held_frame_catalog_0056_v1(frame, fence, *factories)
    assert ("frame", str(subject._FRAME_CONTEXT)) in events
    assert ("frame", subject.partition_policy.CATALOG_SQL) not in events
    assert writer.closed is False
