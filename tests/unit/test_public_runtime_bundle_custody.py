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
    return role, functions, triggers, columns, constraints


async def _run_catalog(
    items,
    *,
    path_readback="pg_catalog, public, pg_temp",
    path_failure=None,
    trace=None,
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
            catalog_queries = (
                custody._ROLE_SQL,
                custody._FUNCTION_SQL,
                custody._TRIGGER_SQL,
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
        (3, 3, "type_name", "text"),
        (3, 0, "attnotnull", False),
        (4, 0, "valid_index", False),
        (4, 1, "key_columns", [3]),
        (4, 2, "definition", "CHECK (true)"),
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
