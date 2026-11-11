"""Offline denial checks; mocked catalog rows do not establish DB acceptance."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import pickle
import ssl
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain import collector_current_0055_catalog_v1 as current55
from tests.integration import test_public_runtime_bundle_custody_repository as pg_cases


@pytest.mark.parametrize(
    "name",
    [
        "verify_deployment",
        "seal_bundle",
        "_check_factory",
        "_catalog",
        "_require_deployment",
        "_read",
        "read_retained_bundle_by_pins",
    ],
)
def test_native_repository_cannot_accept_instance_method_callbacks(name):
    repository = object.__new__(custody.RuntimeBundleCustodyRepository)
    assert not hasattr(repository, "__dict__")
    with pytest.raises(AttributeError):
        setattr(repository, name, lambda *_args, **_kwargs: None)


def _migration():
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/0046_public_runtime_bundle_custody.py"
    )
    spec = importlib.util.spec_from_file_location("runtime_custody_0046", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalog():
    migration = _migration()
    bodies = dict(
        zip(
            custody._FUNCTIONS,
            (
                migration._INSERT_BODY,
                migration._IMMUTABLE_BODY,
                migration._APPEND_BODY,
                migration._READ_BODY,
            ),
            strict=True,
        )
    )
    role = SimpleNamespace(
        direct_login=True,
        privileged=False,
        other_role=False,
        login_oid=23,
        owner_oid=24,
        table_access=False,
        can_create=False,
        relkind="r",
        relpersistence="p",
        relrowsecurity=False,
        relforcerowsecurity=False,
        fsync="on",
        full_page_writes="on",
        synchronous_commit="on",
        replication_role="origin",
        recovery=False,
        database_oid=25,
        database_name="isolated_test",
        server_address="127.0.0.1",
        server_port=5432,
        server_started=datetime(2026, 10, 22, tzinfo=UTC),
    )
    functions = [
        SimpleNamespace(
            proname=name,
            prosrc=bodies[name],
            prosecdef=expected[1],
            lanname=expected[2],
            return_type=expected[3],
            proretset=expected[4],
            argtypes=expected[5],
            provolatile=expected[6],
            proconfig=["search_path=pg_catalog, public"],
            proowner=24,
            proisstrict=False,
            proparallel="u",
            proleakproof=False,
            prokind="f",
            prosupport=0,
            public_execute=False,
            can_execute=name.endswith(("_append", "_read")),
        )
        for name, expected in custody._FUNCTIONS.items()
    ]
    triggers = [
        SimpleNamespace(
            tgname=f"public_runtime_bundle_v1_{name}",
            tgtype=kind,
            tgenabled="A",
            tgisinternal=False,
            tgconstraint=0,
            tgnargs=0,
            tgargs=b"",
            qualification=None,
            tgoldtable=None,
            tgnewtable=None,
            proname=f"public_runtime_bundle_v1_{function}",
            nspname="public",
        )
        for name, kind, function in (
            ("insert_guard", 7, "guard"),
            ("immutable", 27, "immutable"),
            ("no_truncate", 34, "immutable"),
        )
    ]
    columns = [
        SimpleNamespace(
            attname=name,
            type_name=kind,
            attnotnull=True,
            attidentity="",
            attgenerated="",
        )
        for name, kind in (
            ("bundle_id", "character varying(32)"),
            ("bundle_sha256", "character varying(64)"),
            ("proof_envelope_sha256", "character varying(64)"),
            ("bundle_raw", "bytea"),
            ("contract_version", "integer"),
            ("recorded_at", "timestamp with time zone"),
        )
    ]
    constraints = [
        SimpleNamespace(
            conname=f"{prefix}_public_runtime_raw_bundles_v1{suffix}",
            contype=kind,
            key_columns=keys,
            convalidated=True,
            condeferrable=False,
            condeferred=False,
            valid_index=True,
        )
        for prefix, suffix, kind, keys in (
            ("pk", "", "p", [1]),
            ("uq", "_bundle_sha256", "u", [2]),
            ("ck", "_identity", "c", None),
            ("ck", "_content", "c", None),
        )
    ]
    definitions = {
        "ck_public_runtime_raw_bundles_v1_content": "CHECK (contract_version = 1 AND octet_length(bundle_raw) >= 1 AND octet_length(bundle_raw) <= 67108864 AND encode(sha256(bundle_raw), 'hex'::text) = bundle_sha256::text)",
        "ck_public_runtime_raw_bundles_v1_identity": "CHECK (bundle_id::text ~ '^[a-f0-9]{32}$'::text AND bundle_sha256::text ~ '^[a-f0-9]{64}$'::text AND proof_envelope_sha256::text ~ '^[a-f0-9]{64}$'::text)",
        "pk_public_runtime_raw_bundles_v1": "PRIMARY KEY (bundle_id)",
        "uq_public_runtime_raw_bundles_v1_bundle_sha256": "UNIQUE (bundle_sha256)",
    }
    for constraint in constraints:
        constraint.definition = definitions[constraint.conname]
    incoming = [SimpleNamespace(lineage_oid=None, constraint_oid=None)]
    return role, functions, triggers, incoming, columns, constraints


async def _run_catalog(
    items,
    *,
    path_readback="pg_catalog, public, pg_temp",
    path_failure=None,
    trace=None,
    migration_head="0052",
    head_layout=None,
):
    class Result:
        def __init__(self, value):
            self.value = value

        def one_or_none(self):
            return self.value

        def all(self):
            return self.value

        def scalar_one(self):
            return self.value

    class Session:
        def __init__(self):
            self.items = iter(items)
            self.path = "untrusted, pg_catalog, public"

        async def execute(self, statement):
            if trace is not None:
                trace.append(str(statement))
            if statement is custody._SET_CATALOG_SEARCH_PATH_SQL:
                if path_failure == "set":
                    raise RuntimeError("private native detail must not escape")
                self.path = "pg_catalog, public, pg_temp"
                return Result(None)
            if statement is custody._CATALOG_SEARCH_PATH_SQL:
                if path_failure == "readback":
                    raise RuntimeError("private native detail must not escape")
                assert self.path == "pg_catalog, public, pg_temp"
                return Result(path_readback)
            assert self.path == "pg_catalog, public, pg_temp"
            if statement is custody._HEAD_LAYOUT_SQL:
                return Result(
                    head_layout
                    if head_layout is not None
                    else {
                        "has_version": migration_head is not None,
                        "standalone_only": migration_head is None,
                    }
                )
            if statement is custody._HEAD_SQL:
                return Result(migration_head)
            return Result(next(self.items))

    return await custody.RuntimeBundleCustodyRepository._catalog(Session())


def test_caller_path_is_forced_and_read_back_before_first_catalog_query():
    trace = []
    assert len(asyncio.run(_run_catalog(_catalog(), trace=trace))) == 64
    assert trace[:3] == [
        "SET LOCAL search_path = pg_catalog, public, pg_temp",
        "SELECT pg_catalog.current_setting('search_path')",
        str(custody._ROLE_SQL),
    ]
    # Stored DB0046 function settings and the nine catalog text projections
    # remain their original contract; this is the caller transaction path.
    assert all(
        row.proconfig == ["search_path=pg_catalog, public"] for row in _catalog()[1]
    )
    assert "current_user,pg_catalog.current_database(),'CREATE'" in str(
        custody._ROLE_SQL
    )
    assert "d.datname=pg_catalog.current_database()" in str(custody._ROLE_SQL)


@pytest.mark.parametrize("phase", ["set", "readback"])
def test_path_native_failure_stops_before_role_query_and_redacts(phase):
    trace = []
    with pytest.raises(custody.RuntimeBundleCustodyError) as error:
        asyncio.run(_run_catalog(_catalog(), path_failure=phase, trace=trace))
    assert str(error.value) == "runtime_bundle_custody_unavailable"
    assert error.value.diagnostic_category == "role_query"
    assert str(custody._ROLE_SQL) not in trace


@pytest.mark.parametrize(
    "path",
    [
        None,
        True,
        "public",
        "pg_temp, pg_catalog, public",
        "pg_catalog, public",
        ["pg_catalog, public, pg_temp"],
    ],
)
def test_missing_or_changed_native_path_readback_cannot_start_catalog(path):
    trace = []
    with pytest.raises(custody.RuntimeBundleCustodyError, match="custody_unavailable"):
        asyncio.run(_run_catalog(_catalog(), path_readback=path, trace=trace))
    assert len(trace) == 2 and str(custody._ROLE_SQL) not in trace


@pytest.mark.parametrize(
    "operation", ["verify", "seal", "read", "recover", "recover_by_pins"]
)
def test_every_controlled_custody_transaction_guards_path_before_catalog(
    monkeypatch, operation
):
    """Synthetic sessions test path placement only, never native DB acceptance."""
    data = {
        "bundle_raw": b"{}",
        "expected_sha256": hashlib.sha256(b"{}").hexdigest(),
        "proof_envelope_sha256": "a" * 64,
    }
    bundle_id = custody.runtime_bundle_id_v1(
        bundle_sha256=data["expected_sha256"],
        proof_envelope_sha256=data["proof_envelope_sha256"],
    )
    now = datetime(2026, 10, 24, tzinfo=UTC)
    sessions = []

    class Transaction:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            self.session.active = True

        async def __aexit__(self, *_args):
            self.session.active = False

    class Session:
        def __init__(self):
            self.active = self.path_verified = False
            self.path = "untrusted, pg_catalog, public"
            self.trace = []
            self.tx = str(len(sessions) + 1)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def begin(self):
            return Transaction(self)

        async def execute(self, statement, _parameters=None):
            assert self.active
            query = str(statement)
            self.trace.append(query)
            if query in {
                "SET LOCAL synchronous_commit = 'on'",
                "SET TRANSACTION READ ONLY",
            }:
                return None
            if statement is custody._SET_CATALOG_SEARCH_PATH_SQL:
                self.path = "pg_catalog, public, pg_temp"
                return None
            if statement is custody._CATALOG_SEARCH_PATH_SQL:
                self.path_verified = True
                return SimpleNamespace(scalar_one=lambda: self.path)
            assert self.path_verified and self.path == "pg_catalog, public, pg_temp"
            if statement is custody._HEAD_LAYOUT_SQL:
                return SimpleNamespace(
                    scalar_one=lambda: {"has_version": True, "standalone_only": False}
                )
            if statement is custody._HEAD_SQL:
                return SimpleNamespace(scalar_one=lambda: "0052")
            catalog_queries = (
                custody._ROLE_SQL,
                custody._FUNCTION_SQL,
                custody._TRIGGER_SQL,
                custody._INCOMING_CUSTODY_FK_SQL,
                custody._COLUMN_SQL,
                custody._CONSTRAINT_SQL,
            )
            catalog_index = next(
                (
                    index
                    for index, query in enumerate(catalog_queries)
                    if statement is query
                ),
                None,
            )
            if catalog_index is not None:
                value = _catalog()[catalog_index]
                return SimpleNamespace(one_or_none=lambda: value, all=lambda: value)
            if query.startswith("SELECT public.public_runtime_bundle_v1_append("):
                return None
            if query.startswith("SELECT * FROM public.public_runtime_bundle_v1_read("):
                return SimpleNamespace(
                    one_or_none=lambda: SimpleNamespace(
                        bundle_id=bundle_id,
                        bundle_sha256=data["expected_sha256"],
                        proof_envelope_sha256=data["proof_envelope_sha256"],
                        bundle_raw=data["bundle_raw"],
                        contract_version=1,
                        recorded_at=now,
                    )
                )
            if query.startswith(
                ("SELECT pg_backend_pid()", "SELECT clock_timestamp()")
            ):
                return SimpleNamespace(
                    one=lambda: SimpleNamespace(
                        backend_pid=100,
                        transaction_id=self.tx,
                        observed=now,
                    )
                )
            raise AssertionError("unexpected synthetic statement")

    def factory():
        session = Session()
        sessions.append(session)
        return session

    monkeypatch.setattr(
        custody.RuntimeBundleCustodyRepository, "_check_factory", lambda _self: None
    )

    async def run():
        repository = object.__new__(custody.RuntimeBundleCustodyRepository)
        repository._factory, repository._poisoned, repository._deployment = (
            factory,
            False,
            None,
        )
        await repository.verify_deployment()
        if operation == "seal":
            receipt = await repository.seal_bundle(**data)
            assert (
                custody._consume_runtime_bundle_custody_receipt(
                    receipt,
                    expected_bundle_sha256=data["expected_sha256"],
                    expected_proof_envelope_sha256=data["proof_envelope_sha256"],
                ).bundle_raw
                == data["bundle_raw"]
            )
        elif operation in {"read", "recover", "recover_by_pins"}:
            if operation.startswith("recover"):
                fresh = object.__new__(custody.RuntimeBundleCustodyRepository)
                fresh._factory, fresh._poisoned, fresh._deployment = (
                    factory,
                    False,
                    None,
                )
                await fresh.verify_deployment()
                repository = fresh
            if operation == "recover_by_pins":
                result = await repository.read_retained_bundle_by_pins(
                    expected_sha256=data["expected_sha256"],
                    proof_envelope_sha256=data["proof_envelope_sha256"],
                )
            else:
                result = await repository.read_retained_bundle(
                    bundle_id=bundle_id,
                    expected_sha256=data["expected_sha256"],
                    proof_envelope_sha256=data["proof_envelope_sha256"],
                )
            assert (
                result.bundle_raw == data["bundle_raw"]
                and not result.execution_authority
            )

    asyncio.run(run())
    assert (
        len(sessions)
        == {"verify": 1, "seal": 3, "read": 2, "recover": 3, "recover_by_pins": 3}[
            operation
        ]
    )
    for session in sessions:
        role_at = session.trace.index(str(custody._ROLE_SQL))
        assert session.trace[role_at - 2 : role_at] == [
            "SET LOCAL search_path = pg_catalog, public, pg_temp",
            "SELECT pg_catalog.current_setting('search_path')",
        ]
        assert session.path_verified and not session.active


def test_sql_function_bodies_are_pinned_to_reviewed_migration():
    _role, functions, *_remaining = _catalog()
    for function in functions:
        assert (
            hashlib.sha256(function.prosrc.encode()).hexdigest()
            == custody._FUNCTIONS[function.proname][0]
        )
    assert _migration().down_revision == "0045"
    assert len(asyncio.run(_run_catalog(_catalog()))) == 64


@pytest.mark.parametrize(
    "field,value",
    (
        ("direct_login", False),
        ("privileged", True),
        ("other_role", True),
        ("table_access", True),
        ("can_create", True),
        ("owner_oid", 23),
        ("relpersistence", "u"),
        ("relrowsecurity", True),
        ("fsync", "off"),
        ("full_page_writes", "off"),
        ("synchronous_commit", "off"),
        ("replication_role", "replica"),
        ("recovery", True),
    ),
)
def test_unsafe_deployment_role_or_durability_denied(field, value):
    items = _catalog()
    setattr(items[0], field, value)
    with pytest.raises(custody.RuntimeBundleCustodyError, match="custody_unavailable"):
        asyncio.run(_run_catalog(items))


@pytest.mark.parametrize(
    "collection,index,field,value",
    (
        (1, 0, "prosrc", "BEGIN RETURN NEW; END;"),
        (1, 2, "prosecdef", False),
        (1, 3, "public_execute", True),
        (1, 3, "proconfig", ["search_path=public"]),
        (1, 0, "can_execute", True),
        (1, 2, "proowner", 23),
        (2, 0, "tgenabled", "O"),
        (2, 1, "tgtype", 7),
        (2, 2, "qualification", "false"),
        (2, 0, "tgargs", b"changed"),
        (4, 3, "type_name", "text"),
        (4, 0, "attnotnull", False),
        (5, 0, "valid_index", False),
        (5, 1, "key_columns", [3]),
        (5, 2, "definition", "CHECK (true)"),
    ),
)
def test_changed_catalog_cannot_be_promoted(collection, index, field, value):
    items = _catalog()
    setattr(items[collection][index], field, value)
    with pytest.raises(custody.RuntimeBundleCustodyError, match="custody_unavailable"):
        asyncio.run(_run_catalog(items))


@pytest.mark.parametrize(
    "raw,expected,proof",
    (
        (b"", "a" * 64, "b" * 64),
        (bytearray(b"x"), "a" * 64, "b" * 64),
        (b"x", "a" * 64, "b" * 64),
        (b"x", hashlib.sha256(b"x").hexdigest(), "B" * 64),
    ),
)
def test_invalid_bundle_rejected_before_session_factory(raw, expected, proof):
    repository = object.__new__(custody.RuntimeBundleCustodyRepository)
    with pytest.raises(custody.RuntimeBundleCustodyError, match="input_invalid"):
        asyncio.run(
            repository.seal_bundle(
                bundle_raw=raw, expected_sha256=expected, proof_envelope_sha256=proof
            )
        )


def test_retained_identity_is_deterministic_domain_separated_and_bound_to_both_pins():
    expected = "88c1b973f7a3ada74b59e9f9f0170b16"
    first = custody.runtime_bundle_id_v1(
        bundle_sha256="a" * 64, proof_envelope_sha256="b" * 64
    )
    assert first == expected
    assert first == custody.runtime_bundle_id_v1(
        bundle_sha256="a" * 64, proof_envelope_sha256="b" * 64
    )
    assert first != custody.runtime_bundle_id_v1(
        bundle_sha256="b" * 64, proof_envelope_sha256="a" * 64
    )
    assert first != custody.runtime_bundle_id_v1(
        bundle_sha256="a" * 64, proof_envelope_sha256="c" * 64
    )


@pytest.mark.parametrize(
    "bundle_pin,proof_pin", [("A" * 64, "b" * 64), ("a" * 64, ""), (None, "b" * 64)]
)
def test_retained_identity_rejects_unknown_or_noncanonical_pins(bundle_pin, proof_pin):
    with pytest.raises(custody.RuntimeBundleCustodyError, match="input_invalid"):
        custody.runtime_bundle_id_v1(
            bundle_sha256=bundle_pin, proof_envelope_sha256=proof_pin
        )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://ctcc@localhost:5432/ctcc",
        "postgresql+asyncpg://ctcc@ctcc-final-123456789abc-postgres:5432/production",
        "postgresql+asyncpg://other@ctcc-final-123456789abc-postgres:5432/ctcc",
        "postgresql+asyncpg://ctcc:password@ctcc-final-123456789abc-postgres:5432/ctcc",
        "postgresql+asyncpg://ctcc@ctcc-final-123456789abc-postgres:5433/ctcc",
        "postgresql+asyncpg://ctcc@ctcc-final-123456789abc-postgres:5432/ctcc?ssl=require",
        "postgresql://ctcc@ctcc-final-123456789abc-postgres:5432/ctcc",
        "malformed-private-url",
    ],
)
def test_provisioning_fixture_rejects_nonhermetic_url_without_creating_engine(
    monkeypatch, url
):
    def denied_engine(*_args, **_kwargs):
        raise AssertionError("fixture must reject URL before engine construction")

    monkeypatch.setattr(pg_cases, "create_async_engine", denied_engine)
    monkeypatch.setenv("DATABASE_URL", url)

    async def enter_fixture():
        fixture = pg_cases.runtime_custody_database.__wrapped__()
        try:
            await anext(fixture)
        finally:
            await fixture.aclose()

    with pytest.raises(
        RuntimeError, match="^runtime_custody_hermetic_cluster_required$"
    ):
        asyncio.run(enter_fixture())


def test_provisioning_fixture_accepts_only_exact_hermetic_identity():
    value = "postgresql+asyncpg://ctcc@ctcc-final-123456789abc-postgres:5432/ctcc"
    assert str(pg_cases._hermetic_provisioning_url(value)) == value


def test_arbitrary_factory_or_caller_true_cannot_issue_receipt():
    with pytest.raises(custody.RuntimeBundleCustodyError, match="custody_unavailable"):
        custody.RuntimeBundleCustodyRepository(lambda: None)
    with pytest.raises(custody.RuntimeBundleCustodyError, match="receipt_unavailable"):
        custody.OwnedRuntimeBundleCustodyReceipt(True)
    unissued = object.__new__(custody.OwnedRuntimeBundleCustodyReceipt)
    with pytest.raises(custody.RuntimeBundleCustodyError, match="receipt_unavailable"):
        custody._consume_runtime_bundle_custody_receipt(
            unissued,
            expected_bundle_sha256="a" * 64,
            expected_proof_envelope_sha256="b" * 64,
        )
    for operation in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(
            custody.RuntimeBundleCustodyError, match="receipt_unavailable"
        ):
            operation(unissued)


@pytest.mark.parametrize(
    "kind",
    (
        "engine_hook",
        "pool_hook",
        "custom_creator",
        "connection_class",
        "session_class",
        "autocommit",
        "statement_name_callback",
        "password_callback",
        "nested_callback",
    ),
)
def test_native_factory_rejects_substituted_execution_before_connect(kind):
    options = {"poolclass": NullPool}

    async def fake_creator():
        raise AssertionError("must not connect")

    if kind == "custom_creator":
        options["async_creator"] = fake_creator
    elif kind == "connection_class":
        options["connect_args"] = {"connection_class": object}
    elif kind == "autocommit":
        options["isolation_level"] = "AUTOCOMMIT"
    elif kind == "statement_name_callback":
        options["connect_args"] = {"prepared_statement_name_func": lambda: "caller"}
    elif kind == "password_callback":
        options["connect_args"] = {"password": lambda: "not-a-credential"}
    elif kind == "nested_callback":
        options["connect_args"] = {
            "server_settings": {"application_name": [lambda: "caller"]}
        }
    engine = create_async_engine(
        "postgresql+asyncpg://fixture@127.0.0.1/fixture", **options
    )
    factory_options = {}
    if kind == "engine_hook":
        event.listen(engine.sync_engine, "before_cursor_execute", lambda *_args: None)
    elif kind == "pool_hook":
        event.listen(engine.sync_engine.pool, "connect", lambda *_args: None)
    elif kind == "session_class":
        factory_options["sync_session_class"] = object
    factory = async_sessionmaker(engine, expire_on_commit=False, **factory_options)
    try:
        with pytest.raises(
            custody.RuntimeBundleCustodyError, match="custody_unavailable"
        ):
            custody.RuntimeBundleCustodyRepository(factory)
    finally:
        asyncio.run(engine.dispose())


def test_unmodified_native_factory_is_config_only_until_real_preflight():
    engine = create_async_engine(
        "postgresql+asyncpg://fixture@127.0.0.1/fixture", poolclass=NullPool
    )
    try:
        repository = custody.RuntimeBundleCustodyRepository(
            async_sessionmaker(engine, expire_on_commit=False)
        )

        async def run():
            with pytest.raises(
                custody.RuntimeBundleCustodyError, match="custody_unavailable"
            ):
                await repository.seal_bundle(
                    bundle_raw=b"{}",
                    expected_sha256=hashlib.sha256(b"{}").hexdigest(),
                    proof_envelope_sha256="a" * 64,
                )
            with pytest.raises(
                custody.RuntimeBundleCustodyError, match="custody_unavailable"
            ):
                await repository.read_retained_bundle_by_pins(
                    expected_sha256=hashlib.sha256(b"{}").hexdigest(),
                    proof_envelope_sha256="a" * 64,
                )

        asyncio.run(run())
    finally:
        asyncio.run(engine.dispose())


def test_native_ssl_context_and_plain_nested_connection_settings_remain_supported():
    context = ssl.create_default_context()
    engine = create_async_engine(
        "postgresql+asyncpg://fixture@127.0.0.1/fixture",
        poolclass=NullPool,
        connect_args={
            "ssl": context,
            "server_settings": {"application_name": "ctcc-custody"},
        },
    )
    try:
        assert (
            type(
                custody.RuntimeBundleCustodyRepository(
                    async_sessionmaker(engine, expire_on_commit=False)
                )
            )
            is custody.RuntimeBundleCustodyRepository
        )
    finally:
        asyncio.run(engine.dispose())


def test_plain_readback_never_becomes_native_receipt_and_hides_raw_repr():
    now = datetime(2026, 10, 22, tzinfo=UTC)
    result = custody.RetainedRuntimeBundleReadback(
        "a" * 32, "b" * 64, "c" * 64, now, now, b"private-runtime-body"
    )
    assert "private-runtime-body" not in repr(result)
    assert (
        not result.execution_authority and not result.historical_availability_verified
    )
    assert not result.independently_protected
    with pytest.raises(custody.RuntimeBundleCustodyError):
        custody._consume_runtime_bundle_custody_receipt(
            result,
            expected_bundle_sha256="b" * 64,
            expected_proof_envelope_sha256="c" * 64,
        )


def test_uncertain_commit_never_issues_receipt_or_retries(monkeypatch):
    """Synthetic DB failure exercises negative control only, never acceptance."""
    trace = []

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            trace.append("commit_unknown")
            raise RuntimeError("private connection detail must not escape")

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def begin(self):
            return Transaction()

        async def execute(self, statement, _parameters=None):
            if str(statement).startswith(
                "SELECT public.public_runtime_bundle_v1_append("
            ):
                trace.append("append")
            if str(statement).startswith("SELECT pg_backend_pid()"):
                return SimpleNamespace(
                    one=lambda: SimpleNamespace(backend_pid=1, transaction_id="2")
                )

    def factory():
        trace.append("session")
        return Session()

    async def catalog(_session):
        return "f" * 64

    monkeypatch.setattr(
        custody.RuntimeBundleCustodyRepository, "_check_factory", lambda _self: None
    )
    monkeypatch.setattr(
        custody.RuntimeBundleCustodyRepository, "_catalog", staticmethod(catalog)
    )

    async def run():
        repository = object.__new__(custody.RuntimeBundleCustodyRepository)
        repository._factory, repository._poisoned = factory, False
        repository._deployment = custody._Deployment("f" * 64, custody._owner())
        data = {
            "bundle_raw": b"{}",
            "expected_sha256": hashlib.sha256(b"{}").hexdigest(),
            "proof_envelope_sha256": "a" * 64,
        }
        before = len(custody._RECEIPTS)
        with pytest.raises(
            custody.RuntimeBundleCustodyError, match="^runtime_bundle_commit_uncertain$"
        ):
            await repository.seal_bundle(**data)
        assert repository._poisoned and len(custody._RECEIPTS) == before
        with pytest.raises(
            custody.RuntimeBundleCustodyError, match="custody_unavailable"
        ):
            await repository.seal_bundle(**data)

    asyncio.run(run())
    assert trace == ["session", "append", "commit_unknown"]


def _declared_native_0053_ri_catalog():
    """Synthetic native-catalog wire only; real PG cases prove the metadata."""
    role, functions, triggers, _absent, columns, constraints = _catalog()
    incoming = SimpleNamespace(
        parent_oid=100,
        lineage_oid=101,
        lineage_owner=24,
        lineage_kind="r",
        lineage_persistence="p",
        relrowsecurity=False,
        relforcerowsecurity=False,
        relispartition=False,
        lineage_inherited=False,
        constraint_oid=200,
        conname="fk_collector_current_custody",
        constraint_namespace="public",
        constraint_type="f",
        child_oid=101,
        target_oid=100,
        conkey=[15],
        confkey=[1],
        delete_action="r",
        update_action="a",
        match_type="s",
        convalidated=True,
        condeferrable=False,
        condeferred=False,
        parent_constraint_oid=0,
        conislocal=True,
        coninhcount=0,
        child_namespace="public",
        child_name="demo_collector_current_revision_lineages",
        child_owner=24,
        child_column_number=15,
        child_column_name="custody_id",
        child_column_type="character varying(32)",
        child_column_notnull=True,
        child_identity="",
        child_generated="",
        target_column_number=1,
        target_column_name="bundle_id",
        target_column_type="character varying(32)",
        target_column_notnull=True,
        target_identity="",
        target_generated="",
        matching_collation=True,
        index_oid=300,
        expected_index_oid=300,
        parent_pk_name="pk_public_runtime_raw_bundles_v1",
        parent_pk_validated=True,
        index_owner=24,
        index_name="pk_public_runtime_raw_bundles_v1",
        index_namespace="public",
        index_table_oid=100,
        indisunique=True,
        indisprimary=True,
        indisvalid=True,
        indisready=True,
        indislive=True,
        plain_index=True,
        single_key_index=True,
    )
    native = [
        SimpleNamespace(
            tgname=f"RI_ConstraintTrigger_a_{oid}",
            tgtype=kind,
            tgenabled="O",
            tgisinternal=True,
            tgconstraint=200,
            tgnargs=0,
            tgargs=b"",
            qualification=None,
            tgoldtable=None,
            tgnewtable=None,
            proname=function,
            nspname="pg_catalog",
            trigger_oid=oid,
            relation_oid=100,
            other_oid=101,
            tgdeferrable=False,
            tginitdeferred=False,
            attribute_numbers="",
            parent_trigger_oid=0,
            function_oid=expected,
            expected_function_oid=expected,
        )
        for oid, kind, function, expected in (
            (401, 9, "RI_FKey_restrict_del", 501),
            (402, 17, "RI_FKey_noaction_upd", 502),
        )
    ]
    return role, functions, [*triggers, *native], [incoming], columns, constraints


def test_declared_0053_native_RI_pair_is_distinct_from_original_three_user_triggers():
    items = _declared_native_0053_ri_catalog()
    fingerprint = asyncio.run(_run_catalog(items, migration_head="0053"))
    assert len(fingerprint) == 64
    assert fingerprint != asyncio.run(_run_catalog(_catalog()))
    assert len([row for row in items[2] if row.tgisinternal is False]) == 3
    assert {row.tgenabled for row in items[2] if row.tgisinternal} == {"O"}


def test_every_other_incoming_reference_is_rejected_instead_of_filtered():
    items = _declared_native_0053_ri_catalog()
    items[3].append(SimpleNamespace(lineage_oid=101, constraint_oid=999))
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(_run_catalog(items, migration_head="0053"))


def _declared_native_0054_ri_catalog():
    """Synthetic catalog DTO only; actual54 PG must prove the relationships."""
    role, functions, triggers, incoming, columns, constraints = (
        _declared_native_0053_ri_catalog()
    )
    second = SimpleNamespace(**vars(incoming[0]))
    second.lineage_oid = second.child_oid = 102
    second.constraint_oid = 201
    second.conname = "fk_collector_issued_hwm_custody"
    second.child_name = "demo_collector_issued_account_revisions"
    second.child_column_number = 16
    second.child_column_name = "hwm_custody_id"
    second.conkey = [16]
    native = []
    for original in triggers[-2:]:
        row = SimpleNamespace(**vars(original))
        row.trigger_oid += 2
        row.tgname = "RI_ConstraintTrigger_a_" + str(row.trigger_oid)
        row.tgconstraint = 201
        row.other_oid = 102
        native.append(row)
    return (
        role,
        functions,
        [*triggers, *native],
        [*incoming, second],
        columns,
        constraints,
    )


def test_current54_requires_exact_both_FKs_and_all_four_parent_actions():
    value = _declared_native_0054_ri_catalog()
    actual = asyncio.run(_run_catalog(value, migration_head="0054"))
    assert len(actual) == 64
    assert actual != asyncio.run(
        _run_catalog(_declared_native_0053_ri_catalog(), migration_head="0053")
    )
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(_run_catalog(value, migration_head="0053"))
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(
            _run_catalog(_declared_native_0053_ri_catalog(), migration_head="0054")
        )


@pytest.mark.parametrize("head", ["0055", "0056", "0057", "", 54, False])
def test_unknown_actual_version_has_no_custody_fallback(head):
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(_run_catalog(_catalog(), migration_head=head))


def test_actual56_uses_unchanged55_custody_carrier_with_distinct_head(monkeypatch):
    observed = []

    async def carrier(_session, _role, *, expected_head):
        observed.append(expected_head)
        return ("reviewed_actual55_baseline", f"head_{expected_head}_readback")

    monkeypatch.setattr(custody, "_current0055_custody_schema_identity", carrier)
    actual55 = asyncio.run(
        _run_catalog(_declared_native_0054_ri_catalog(), migration_head="0055")
    )
    actual56 = asyncio.run(
        _run_catalog(_declared_native_0054_ri_catalog(), migration_head="0056")
    )
    assert observed == ["0055", "0056"]
    assert len(actual55) == len(actual56) == 64
    assert actual55 != actual56


def test_actual56_still_rejects_changed_native_custody_fk(monkeypatch):
    async def carrier(_session, _role, *, expected_head):
        assert expected_head == "0056"
        return ("reviewed_actual55_baseline", "head_0056_readback")

    monkeypatch.setattr(custody, "_current0055_custody_schema_identity", carrier)
    changed = _declared_native_0054_ri_catalog()
    changed[3][1].conkey = [15]
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(_run_catalog(changed, migration_head="0056"))


def test_actual56_carrier_refuses_unapproved_expected_head():
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(
            custody._current0055_custody_schema_identity(
                object(), object(), expected_head="0057"
            )
        )


def test_actual56_carrier_reads_real_head_and_unchanged55_descriptors():
    expected_functions = current55._source_functions()
    expected_functions["collector_issued_account_revision_guard_v1"] = (
        current55._sha(current55._body(current55.operator.ISSUER_GUARD_SQL, "$ctcc$")),
        True,
        ["search_path=pg_catalog, pg_temp"],
        False,
    )
    value = {
        "head": ["0056"],
        "tables": [
            {
                "name": name,
                "owner": 24,
                "kind": "r",
                "persistence": "p",
                "rls": False,
                "force_rls": False,
                "partition": False,
                "inherited": False,
                "rules": False,
                "policies": False,
                "columns": current55._COLUMNS[name],
            }
            for name in (
                current55.operator.CLEAR_TABLE,
                current55.operator.COUNTER_TABLE,
            )
        ],
        "functions": [
            {
                "name": name,
                "owner": 24,
                "language": "plpgsql",
                "arity": 0,
                "defaults": 0,
                "returns_trigger": True,
                "returns_set": False,
                "security_definer": descriptor[1],
                "strict": False,
                "leakproof": False,
                "volatility": "v",
                "parallel": "u",
                "kind": "f",
                "support_zero": True,
                "config": descriptor[2],
                "source_sha256": descriptor[0],
            }
            for name, descriptor in expected_functions.items()
        ],
    }

    class Session:
        async def execute(self, statement):
            assert statement is custody._CURRENT0055_SCHEMA_IDENTITY_SQL
            return self

        def scalar_one(self):
            return value

    observed = asyncio.run(
        custody._current0055_custody_schema_identity(
            Session(), SimpleNamespace(owner_oid=24), expected_head="0056"
        )
    )
    assert observed[0] == current55.PG17_NATIVE_BASELINE_SHA256
    assert len(observed[1]) == 64
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(
            custody._current0055_custody_schema_identity(
                Session(), SimpleNamespace(owner_oid=24), expected_head="0055"
            )
        )


def test_missing_version_with_other_public_relations_is_not_standalone():
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(
            _run_catalog(
                _catalog(),
                migration_head=None,
                head_layout={"has_version": False, "standalone_only": False},
            )
        )


def test_explicit_standalone_component_never_claims_a_migration_revision():
    standalone = asyncio.run(_run_catalog(_catalog(), migration_head=None))
    versioned = asyncio.run(_run_catalog(_catalog(), migration_head="0052"))
    assert len(standalone) == len(versioned) == 64
    assert standalone != versioned


def test_version53_missing_its_relation_cannot_use_absent_predecessor_profile():
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(_run_catalog(_catalog(), migration_head="0053"))


def test_current54_wrong_issuer_FK_column_is_rejected():
    value = _declared_native_0054_ri_catalog()
    value[3][1].conkey = [15]
    value[3][1].child_column_number = 15
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(_run_catalog(value, migration_head="0054"))


def test_current54_extra_internal_trigger_or_third_reference_is_not_filtered():
    value = _declared_native_0054_ri_catalog()
    extra = SimpleNamespace(**vars(value[2][-1]))
    extra.trigger_oid = 999
    extra.tgname = "RI_ConstraintTrigger_a_999"
    value[2].append(extra)
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(_run_catalog(value, migration_head="0054"))


def test_absent0053_requires_zero_internal_triggers_and_zero_incoming_FK():
    absent = _catalog()
    native = _declared_native_0053_ri_catalog()[2][-1]
    absent[2].append(native)
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(_run_catalog(absent))
    absent = _catalog()
    absent[3][0].constraint_oid = 999
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(_run_catalog(absent))


@pytest.mark.parametrize(
    "field,value",
    (
        ("function_oid", 999),
        ("tgenabled", "D"),
        ("qualification", "false"),
        ("tgargs", b"unapproved"),
        ("relation_oid", 101),
        ("tgconstraint", 999),
        ("tgname", "renamed_native_action"),
    ),
)
def test_native_RI_relationship_or_function_drift_remains_fail_closed(field, value):
    items = _declared_native_0053_ri_catalog()
    setattr(items[2][-1], field, value)
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(_run_catalog(items, migration_head="0053"))


@pytest.mark.parametrize(
    "field,value",
    (
        ("child_owner", 999),
        ("child_name", "other_table"),
        ("conkey", [16]),
        ("delete_action", "c"),
        ("convalidated", False),
        ("condeferrable", True),
        ("expected_index_oid", 999),
    ),
)
def test_0053_FK_owner_columns_actions_validation_and_index_are_exact(field, value):
    items = _declared_native_0053_ri_catalog()
    setattr(items[3][0], field, value)
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(_run_catalog(items, migration_head="0053"))


def test_same_semantic_FK_and_RI_recreation_changes_deployment_identity():
    old = _declared_native_0053_ri_catalog()
    changed = _declared_native_0053_ri_catalog()
    changed[3][0].constraint_oid = 210
    for row in changed[2][-2:]:
        row.tgconstraint = 210
        row.trigger_oid += 20
        row.tgname = f"RI_ConstraintTrigger_a_{row.trigger_oid}"
    assert asyncio.run(_run_catalog(old, migration_head="0053")) != asyncio.run(
        _run_catalog(changed, migration_head="0053")
    )


def test_unknown_extra_user_trigger_is_still_rejected_with0053_present():
    items = _declared_native_0053_ri_catalog()
    items[2].append(SimpleNamespace(tgisinternal=False, tgname="unapproved_extra_user"))
    with pytest.raises(custody.RuntimeBundleCustodyError):
        asyncio.run(_run_catalog(items, migration_head="0053"))
