"""Current raw evidence retained against a restricted application DB role.

The owner validates and redacts the complete bundle before this seam. It binds
the exact bytes and an account/provenance-envelope digest, synchronously commits,
and reads those bytes in another transaction. It does not prove native exchange
authenticity, independent custody, historical availability, WORM or DBA safety.
No settings, migration credentials or network callbacks are accepted here.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import re
import ssl
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import CodeType, FunctionType
from weakref import WeakKeyDictionary

import asyncpg
from sqlalchemy import text
from sqlalchemy.dialects.postgresql.asyncpg import (
    AsyncAdapt_asyncpg_dbapi,
    PGDialect_asyncpg,
)
from sqlalchemy.engine.create import create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool
from sqlalchemy.util import immutabledict
from sqlalchemy.util.langhelpers import only_once

MAX_BUNDLE_BYTES = 64 * 1024 * 1024
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_ID = re.compile(r"[a-f0-9]{32}\Z")
_ISSUER = object()
_RECEIPTS = WeakKeyDictionary()
_RECEIPT_LIFETIME_NS = 5_000_000_000
_BUNDLE_ID_DOMAIN = b"ctcc.public_runtime_bundle_custody.bundle_id.v1\x00"


def _code(function, name):
    return next(
        item
        for item in inspect.unwrap(function).__code__.co_consts
        if type(item) is CodeType and item.co_name == name
    )


_CONNECT_CODE = _code(create_engine, "connect")
_ON_CONNECT_CODE = _code(create_engine, "on_connect")
_FIRST_CONNECT_CODE = _code(create_engine, "first_connect")
_ONLY_ONCE_CODE = _code(only_once, "go")
_DIALECT_CONNECT_CODE = _code(PGDialect_asyncpg.on_connect, "connect")


def _closure(function):
    if type(function) is not FunctionType:
        return {}
    return dict(
        zip(
            function.__code__.co_freevars,
            (cell.cell_contents for cell in function.__closure__ or ()),
            strict=True,
        )
    )


def _native_connection_parameters(value, *, depth=0, budget=None):
    """Bounded native scalar/container parameters, with no caller callbacks."""
    if budget is None:
        budget = [512]
    budget[0] -= 1
    if budget[0] < 0 or depth > 8 or callable(value):
        return False
    kind = type(value)
    if value is None or kind in (str, bytes, int, float, bool):
        return True
    if kind is ssl.SSLContext:
        # Native client TLS is supported; custom debug/server callbacks are not
        # part of the reviewed execution path.
        return value._msg_callback is None and value.sni_callback is None
    if kind in (dict, immutabledict):
        return all(
            type(key) is str
            and _native_connection_parameters(member, depth=depth + 1, budget=budget)
            for key, member in value.items()
        )
    if kind in (tuple, list, set, frozenset):
        return all(
            _native_connection_parameters(member, depth=depth + 1, budget=budget)
            for member in value
        )
    return False


def _native_engine(engine):
    """Reject caller hooks, replacement creators and fake DBAPI adapters.

    This uses the pinned SQLAlchemy/asyncpg native path. Arbitrary in-process
    code replacement and hostile DBAs are outside app-role custody's scope.
    """
    if type(engine) is not AsyncEngine:
        return False
    sync, dialect, pool = engine.sync_engine, engine.dialect, engine.sync_engine.pool
    if (
        type(dialect) is not PGDialect_asyncpg
        or type(dialect.dbapi) is not AsyncAdapt_asyncpg_dbapi
        or dialect.dbapi.asyncpg is not asyncpg
        or type(pool) not in (NullPool, AsyncAdaptedQueuePool)
        or sync.get_execution_options()
        or AsyncSession.sync_session_class is not Session
        or dialect._on_connect_isolation_level == "AUTOCOMMIT"
    ):
        return False
    session_dispatch = Session(autobegin=False).dispatch
    for dispatch in (sync.dispatch, dialect.dispatch, session_dispatch):
        if any(len(getattr(dispatch, name)) for name in dispatch._event_names):
            return False
    if any(
        len(getattr(pool.dispatch, name))
        for name in pool.dispatch._event_names
        if name != "connect"
    ):
        return False
    creator = pool._creator
    scope = _closure(creator)
    if (
        type(creator) is not FunctionType
        or creator.__code__ is not _CONNECT_CODE
        or set(scope) != {"cargs_tup", "cparams", "dialect"}
        or scope["dialect"] is not dialect
        or scope["cargs_tup"] != []
        or not _native_connection_parameters(scope["cparams"])
        or any(
            name in scope["cparams"]
            for name in (
                "async_creator_fn",
                "connection_class",
                "record_class",
                "async_fallback",
                "prepared_statement_name_func",
            )
        )
    ):
        return False
    hooks = tuple(pool.dispatch.connect)
    if len(hooks) != 2:
        return False
    on_connect, once = hooks
    if (
        type(on_connect) is not FunctionType
        or on_connect.__code__ is not _ON_CONNECT_CODE
        or type(once) is not FunctionType
        or once.__code__ is not _ONLY_ONCE_CODE
    ):
        return False
    connected, first = (
        _closure(on_connect).get("do_on_connect"),
        _closure(once).get("fn"),
    )
    return (
        type(connected) is FunctionType
        and connected.__code__ is _DIALECT_CONNECT_CODE
        and _closure(connected).get("self") is dialect
        and type(first) is FunctionType
        and first.__code__ is _FIRST_CONNECT_CODE
        and _closure(first).get("dialect") is dialect
        and _closure(first).get("engine") is sync
    )


_FUNCTIONS = {
    "public_runtime_bundle_v1_guard": (
        "13e77635eb0518e5db1da94a077e8930616bd7972be6069a9ddfb5f02a3e82e6",
        False,
        "plpgsql",
        "trigger",
        False,
        "",
        "v",
    ),
    "public_runtime_bundle_v1_immutable": (
        "1daea630a98adaed8e61ac18a428189e9d216413e52b6fe5588dd35690872635",
        False,
        "plpgsql",
        "trigger",
        False,
        "",
        "v",
    ),
    "public_runtime_bundle_v1_append": (
        "d5d1586f2d4e2a5292981179a8c29710cf5f86a39ed2ec5ab3fb12ca8d2179a3",
        True,
        "plpgsql",
        "void",
        False,
        "text, text, text, bytea",
        "v",
    ),
    "public_runtime_bundle_v1_read": (
        "9dbc4008637b6a6092f893c347d60dc8608a6678db860f37daf6d1782de4705f",
        True,
        "sql",
        "public_runtime_raw_bundles_v1",
        True,
        "text",
        "s",
    ),
}
# Native PostgreSQL 17.11 deparser output from the byte-exact reviewed DB0046
# migration (schema-only probe39647583880). This is schema identity, not a
# historical/source acceptance attestation or a database-supplied expected hash.
_CONSTRAINT_DEFINITION_SHA256 = {
    "ck_public_runtime_raw_bundles_v1_content": "e09c32d8d1c64fa7bd867325b89c8fac0f7b050cd71d37649ab45fff2e17a542",
    "ck_public_runtime_raw_bundles_v1_identity": "31242ca55a04c56bb824865996239bd8064e4200c740e2e689ae882dd8c679c8",
    "pk_public_runtime_raw_bundles_v1": "6149072230c4daef7add37e0bf6fc793f7583e94d95f12627020d214f1c4cb7d",
    "uq_public_runtime_raw_bundles_v1_bundle_sha256": "53750f27830362606cde6e5e46ebc11cbb15acbd176c48c3543466bc87fe88e6",
}
_ROLE_SQL = text("""
SELECT session_user=current_user AS direct_login,
  r.oid AS login_oid,c.relowner AS owner_oid,c.relkind,c.relpersistence,
  c.relrowsecurity,c.relforcerowsecurity,
  r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication
    OR r.rolbypassrls AS privileged,
  EXISTS(SELECT 1 FROM pg_catalog.pg_roles x WHERE x.oid<>r.oid
    AND pg_catalog.pg_has_role(current_user,x.oid,'MEMBER')) AS other_role,
  pg_catalog.has_table_privilege(current_user,c.oid,'SELECT')
    OR pg_catalog.has_table_privilege(current_user,c.oid,'INSERT')
    OR pg_catalog.has_table_privilege(current_user,c.oid,'UPDATE')
    OR pg_catalog.has_table_privilege(current_user,c.oid,'DELETE')
    OR pg_catalog.has_table_privilege(current_user,c.oid,'TRUNCATE')
    OR pg_catalog.has_table_privilege(current_user,c.oid,'REFERENCES')
    OR pg_catalog.has_table_privilege(current_user,c.oid,'TRIGGER')
    OR pg_catalog.has_table_privilege(current_user,c.oid,'MAINTAIN')
    OR pg_catalog.has_any_column_privilege(current_user,c.oid,'SELECT')
    OR pg_catalog.has_any_column_privilege(current_user,c.oid,'INSERT')
    OR pg_catalog.has_any_column_privilege(current_user,c.oid,'UPDATE')
    OR pg_catalog.has_any_column_privilege(current_user,c.oid,'REFERENCES') AS table_access,
  pg_catalog.has_schema_privilege(current_user,'public','CREATE')
    OR pg_catalog.has_database_privilege(current_user,current_database(),'CREATE') AS can_create,
  pg_catalog.current_setting('fsync') AS fsync,
  pg_catalog.current_setting('full_page_writes') AS full_page_writes,
  pg_catalog.current_setting('synchronous_commit') AS synchronous_commit,
  pg_catalog.current_setting('session_replication_role') AS replication_role,
  pg_catalog.pg_is_in_recovery() AS recovery,
  d.oid AS database_oid,pg_catalog.current_database() AS database_name,
  pg_catalog.inet_server_addr()::text AS server_address,
  pg_catalog.inet_server_port() AS server_port,
  pg_catalog.pg_postmaster_start_time() AS server_started
FROM pg_catalog.pg_roles r,pg_catalog.pg_class c,pg_catalog.pg_database d
WHERE r.rolname=current_user AND d.datname=current_database()
  AND c.oid=pg_catalog.to_regclass('public.public_runtime_raw_bundles_v1')
""")
_FUNCTION_SQL = text("""
SELECT p.proname,p.prosrc,p.prosecdef,l.lanname,t.typname AS return_type,
  p.proretset,pg_catalog.oidvectortypes(p.proargtypes) AS argtypes,
  p.provolatile,p.proconfig,p.proowner,p.proisstrict,p.proparallel,p.proleakproof,
  p.prokind,p.prosupport::bigint AS prosupport,
  pg_catalog.has_function_privilege(current_user,p.oid,'EXECUTE') AS can_execute,
  EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(p.proacl,
    pg_catalog.acldefault('f',p.proowner))) a
    WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS public_execute
FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
JOIN pg_catalog.pg_language l ON l.oid=p.prolang
JOIN pg_catalog.pg_type t ON t.oid=p.prorettype
WHERE n.nspname='public' AND p.proname IN ('public_runtime_bundle_v1_guard',
  'public_runtime_bundle_v1_immutable','public_runtime_bundle_v1_append',
  'public_runtime_bundle_v1_read')
""")
_TRIGGER_SQL = text("""
SELECT g.tgname,g.tgtype,g.tgenabled,g.tgisinternal,g.tgconstraint,
  g.tgnargs,g.tgargs,g.tgqual::text AS qualification,g.tgoldtable,g.tgnewtable,
  p.proname,n.nspname
FROM pg_catalog.pg_trigger g JOIN pg_catalog.pg_proc p ON p.oid=g.tgfoid
JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
WHERE g.tgrelid=pg_catalog.to_regclass('public.public_runtime_raw_bundles_v1')
""")
_COLUMN_SQL = text("""
SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod) AS type_name,
  a.attnotnull,a.attidentity,a.attgenerated
FROM pg_catalog.pg_attribute a
WHERE a.attrelid=pg_catalog.to_regclass('public.public_runtime_raw_bundles_v1')
  AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum
""")
_CONSTRAINT_SQL = text("""
SELECT conname,contype,convalidated,condeferrable,condeferred,
  pg_catalog.pg_get_constraintdef(oid,true) AS definition,
  CASE WHEN contype IN ('p','u') THEN conkey ELSE NULL END AS key_columns,
  CASE WHEN contype IN ('p','u') THEN
    (SELECT indisvalid AND indisready AND indisunique AND indpred IS NULL
       FROM pg_catalog.pg_index WHERE indexrelid=conindid) ELSE TRUE END AS valid_index
FROM pg_catalog.pg_constraint
WHERE conrelid=pg_catalog.to_regclass('public.public_runtime_raw_bundles_v1')
""")


class RuntimeBundleCustodyError(ValueError):
    """Fixed safe codes only, never connection strings, raw bytes or identities."""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def runtime_bundle_id_v1(*, bundle_sha256, proof_envelope_sha256) -> str:
    """Pure retained-row identity, never a custody or source capability.

    Canonical V1 bytes are the fixed domain followed by the two exact 32-byte
    digests in bundle/proof order. The first 128 SHA256 bits form the existing
    32-lowercase-hex database key. Known persisted pins can recover this key
    after an uncertain commit without repeating an append or minting a lease.
    """
    if (
        type(bundle_sha256) is not str
        or _SHA.fullmatch(bundle_sha256) is None
        or type(proof_envelope_sha256) is not str
        or _SHA.fullmatch(proof_envelope_sha256) is None
    ):
        raise RuntimeBundleCustodyError("runtime_bundle_input_invalid")
    return _sha(
        _BUNDLE_ID_DOMAIN
        + bytes.fromhex(bundle_sha256)
        + bytes.fromhex(proof_envelope_sha256)
    )[:32]


def _pins(raw, expected, proof):
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAX_BUNDLE_BYTES
        or type(expected) is not str
        or _SHA.fullmatch(expected) is None
        or type(proof) is not str
        or _SHA.fullmatch(proof) is None
        or _sha(raw) != expected
    ):
        raise RuntimeBundleCustodyError("runtime_bundle_input_invalid")


def _owner():
    try:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
    except RuntimeError:
        raise RuntimeBundleCustodyError("runtime_bundle_custody_unavailable") from None
    if task is None or task.done() or task.cancelling():
        raise RuntimeBundleCustodyError("runtime_bundle_custody_unavailable")
    return os.getpid(), threading.get_ident(), loop, task


@dataclass(frozen=True, slots=True)
class _Deployment:
    fingerprint: str
    owner: tuple = field(repr=False)


@dataclass(frozen=True, slots=True)
class RetainedRuntimeBundleReadback:
    """Exact DB readback only; constructing/replaying this grants no capability."""

    bundle_id: str
    bundle_sha256: str
    proof_envelope_sha256: str
    recorded_at: datetime
    database_readback_at: datetime
    bundle_raw: bytes = field(repr=False)
    append_backend_pid: int | None = None
    append_transaction_id: str | None = None
    readback_backend_pid: int | None = None
    readback_transaction_id: str | None = None
    execution_authority: bool = field(default=False, init=False)
    historical_availability_verified: bool = field(default=False, init=False)
    independently_protected: bool = field(default=False, init=False)


class OwnedRuntimeBundleCustodyReceipt:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            raise RuntimeBundleCustodyError("runtime_bundle_receipt_unavailable")

    def __copy__(self):
        raise RuntimeBundleCustodyError("runtime_bundle_receipt_unavailable")

    def __deepcopy__(self, memo):
        raise RuntimeBundleCustodyError("runtime_bundle_receipt_unavailable")

    def __reduce_ex__(self, protocol):
        raise RuntimeBundleCustodyError("runtime_bundle_receipt_unavailable")


def _consume_runtime_bundle_custody_receipt(
    receipt, *, expected_bundle_sha256, expected_proof_envelope_sha256
) -> RetainedRuntimeBundleReadback:
    if type(receipt) is not OwnedRuntimeBundleCustodyReceipt:
        raise RuntimeBundleCustodyError("runtime_bundle_receipt_unavailable")
    state = _RECEIPTS.pop(receipt, None)
    if state is None:
        raise RuntimeBundleCustodyError("runtime_bundle_receipt_unavailable")
    readback, owner, issued = state
    elapsed = time.monotonic_ns() - issued
    if (
        owner != _owner()
        or not 0 <= elapsed <= _RECEIPT_LIFETIME_NS
        or type(expected_bundle_sha256) is not str
        or _SHA.fullmatch(expected_bundle_sha256) is None
        or type(expected_proof_envelope_sha256) is not str
        or _SHA.fullmatch(expected_proof_envelope_sha256) is None
        or readback.bundle_sha256 != expected_bundle_sha256
        or readback.proof_envelope_sha256 != expected_proof_envelope_sha256
        or _sha(readback.bundle_raw) != expected_bundle_sha256
    ):
        raise RuntimeBundleCustodyError("runtime_bundle_receipt_unavailable")
    return readback


class RuntimeBundleCustodyRepository:
    """Explicit restricted PostgreSQL connection; one invocation owner.

    No receipt is issued by constructor, configuration, deployment preflight or
    read-only replay. Only an actual commit followed by exact native readback
    creates a receipt. A possibly committed append is never retried here.
    """

    __slots__ = ("_deployment", "_engine", "_factory", "_poisoned")

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._factory = session_factory
        self._engine = None
        self._deployment = None
        self._poisoned = False
        self._check_factory()
        self._engine = session_factory.kw["bind"]

    def _check_factory(self):
        factory = self._factory
        engine = factory.kw.get("bind") if type(factory) is async_sessionmaker else None
        if (
            type(factory) is not async_sessionmaker
            or factory.class_ is not AsyncSession
            or not set(factory.kw) <= {"bind", "autoflush", "expire_on_commit"}
            or type(engine) is not AsyncEngine
            or engine.dialect.name != "postgresql"
            or engine.dialect.driver != "asyncpg"
            or not _native_engine(engine)
            or (self._engine is not None and engine is not self._engine)
        ):
            raise RuntimeBundleCustodyError("runtime_bundle_custody_unavailable")

    @staticmethod
    async def _catalog(session):
        role = (await session.execute(_ROLE_SQL)).one_or_none()
        if role is None or not (
            role.direct_login
            and not role.privileged
            and not role.other_role
            and role.login_oid != role.owner_oid
            and not role.table_access
            and not role.can_create
            and role.relkind == "r"
            and role.relpersistence == "p"
            and not role.relrowsecurity
            and not role.relforcerowsecurity
            and role.fsync == "on"
            and role.full_page_writes == "on"
            and role.synchronous_commit == "on"
            and role.replication_role == "origin"
            and not role.recovery
        ):
            raise RuntimeBundleCustodyError("runtime_bundle_custody_unavailable")
        functions = tuple((await session.execute(_FUNCTION_SQL)).all())
        if len(functions) != len(_FUNCTIONS) or {r.proname for r in functions} != set(
            _FUNCTIONS
        ):
            raise RuntimeBundleCustodyError("runtime_bundle_custody_unavailable")
        for row in functions:
            expected = _FUNCTIONS[row.proname]
            actual = (
                _sha(row.prosrc.encode("utf-8")),
                row.prosecdef,
                row.lanname,
                row.return_type,
                row.proretset,
                row.argtypes,
                row.provolatile,
            )
            if (
                actual != expected
                or tuple(row.proconfig or ()) != ("search_path=pg_catalog, public",)
                or row.proowner != role.owner_oid
                or row.proisstrict
                or row.proparallel != "u"
                or row.proleakproof
                or row.prokind != "f"
                or row.prosupport != 0
                or row.public_execute
                or row.can_execute != row.proname.endswith(("_append", "_read"))
            ):
                raise RuntimeBundleCustodyError("runtime_bundle_custody_unavailable")
        triggers = tuple((await session.execute(_TRIGGER_SQL)).all())
        expected_triggers = {
            "public_runtime_bundle_v1_insert_guard": (
                7,
                "public_runtime_bundle_v1_guard",
            ),
            "public_runtime_bundle_v1_immutable": (
                27,
                "public_runtime_bundle_v1_immutable",
            ),
            "public_runtime_bundle_v1_no_truncate": (
                34,
                "public_runtime_bundle_v1_immutable",
            ),
        }
        if len(triggers) != 3 or {r.tgname for r in triggers} != set(expected_triggers):
            raise RuntimeBundleCustodyError("runtime_bundle_custody_unavailable")
        for row in triggers:
            if (
                (row.tgtype, row.proname) != expected_triggers[row.tgname]
                or row.tgenabled != "A"
                or row.tgisinternal
                or row.tgconstraint
                or row.tgnargs
                or bytes(row.tgargs) != b""
                or row.qualification is not None
                or row.tgoldtable is not None
                or row.tgnewtable is not None
                or row.nspname != "public"
            ):
                raise RuntimeBundleCustodyError("runtime_bundle_custody_unavailable")
        columns = tuple((await session.execute(_COLUMN_SQL)).all())
        expected_columns = (
            ("bundle_id", "character varying(32)"),
            ("bundle_sha256", "character varying(64)"),
            ("proof_envelope_sha256", "character varying(64)"),
            ("bundle_raw", "bytea"),
            ("contract_version", "integer"),
            ("recorded_at", "timestamp with time zone"),
        )
        if tuple((r.attname, r.type_name) for r in columns) != expected_columns or any(
            not r.attnotnull or r.attidentity or r.attgenerated for r in columns
        ):
            raise RuntimeBundleCustodyError("runtime_bundle_custody_unavailable")
        constraints = tuple((await session.execute(_CONSTRAINT_SQL)).all())
        expected_constraints = {
            "pk_public_runtime_raw_bundles_v1": ("p", [1]),
            "uq_public_runtime_raw_bundles_v1_bundle_sha256": ("u", [2]),
            "ck_public_runtime_raw_bundles_v1_identity": ("c", None),
            "ck_public_runtime_raw_bundles_v1_content": ("c", None),
        }
        if len(constraints) != 4 or {r.conname for r in constraints} != set(
            expected_constraints
        ):
            raise RuntimeBundleCustodyError("runtime_bundle_custody_unavailable")
        if any(
            (r.contype, r.key_columns) != expected_constraints[r.conname]
            or _sha(r.definition.encode("utf-8"))
            != _CONSTRAINT_DEFINITION_SHA256[r.conname]
            or not r.convalidated
            or r.condeferrable
            or r.condeferred
            or not r.valid_index
            for r in constraints
        ):
            raise RuntimeBundleCustodyError("runtime_bundle_custody_unavailable")
        identity = (
            role.database_oid,
            role.database_name,
            role.server_address,
            role.server_port,
            role.server_started.isoformat(),
            role.login_oid,
            role.owner_oid,
        )
        return _sha(repr(identity).encode("utf-8"))

    async def verify_deployment(self) -> None:
        """Read actual role/catalog/durability state before any source I/O."""
        try:
            self._check_factory()
            if self._poisoned:
                raise RuntimeBundleCustodyError("runtime_bundle_custody_unavailable")
            async with self._factory() as session, session.begin():
                await session.execute(text("SET LOCAL synchronous_commit = 'on'"))
                await session.execute(text("SET TRANSACTION READ ONLY"))
                fingerprint = await self._catalog(session)
            self._deployment = _Deployment(fingerprint, _owner())
        except RuntimeBundleCustodyError:
            raise
        except Exception:  # noqa: BLE001 - Redact native connection/catalog details.
            raise RuntimeBundleCustodyError(
                "runtime_bundle_custody_unavailable"
            ) from None

    def _require_deployment(self):
        self._check_factory()
        if (
            self._poisoned
            or self._deployment is None
            or self._deployment.owner != _owner()
        ):
            raise RuntimeBundleCustodyError("runtime_bundle_custody_unavailable")
        return self._deployment

    async def _read(self, *, bundle_id, expected_sha256, proof_envelope_sha256):
        deployment = self._require_deployment()
        async with self._factory() as session, session.begin():
            await session.execute(text("SET LOCAL synchronous_commit = 'on'"))
            await session.execute(text("SET TRANSACTION READ ONLY"))
            if await self._catalog(session) != deployment.fingerprint:
                raise RuntimeBundleCustodyError("runtime_bundle_custody_unavailable")
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM public.public_runtime_bundle_v1_read(:bundle_id)"
                    ),
                    {"bundle_id": bundle_id},
                )
            ).one_or_none()
            # Sample only after exact committed bytes have been read.
            sample = (
                await session.execute(
                    text(
                        "SELECT clock_timestamp() AS observed,pg_backend_pid() AS backend_pid,"
                        "pg_current_xact_id()::text AS transaction_id"
                    )
                )
            ).one()
            observed = sample.observed
        if row is None:
            raise RuntimeBundleCustodyError("runtime_bundle_readback_mismatch")
        raw = bytes(row.bundle_raw)
        _pins(raw, expected_sha256, proof_envelope_sha256)
        if (
            row.bundle_id != bundle_id
            or row.bundle_sha256 != expected_sha256
            or row.proof_envelope_sha256 != proof_envelope_sha256
            or row.contract_version != 1
            or type(row.recorded_at) is not datetime
            or row.recorded_at.tzinfo is None
            or type(observed) is not datetime
            or observed.tzinfo is None
            or row.recorded_at > observed
        ):
            raise RuntimeBundleCustodyError("runtime_bundle_readback_mismatch")
        return RetainedRuntimeBundleReadback(
            bundle_id,
            expected_sha256,
            proof_envelope_sha256,
            row.recorded_at,
            observed,
            raw,
            readback_backend_pid=sample.backend_pid,
            readback_transaction_id=sample.transaction_id,
        )

    async def seal_bundle(self, *, bundle_raw, expected_sha256, proof_envelope_sha256):
        """Append once, commit, then issue a task-bound receipt after exact readback."""
        _pins(bundle_raw, expected_sha256, proof_envelope_sha256)
        deployment = self._require_deployment()
        bundle_id = runtime_bundle_id_v1(
            bundle_sha256=expected_sha256,
            proof_envelope_sha256=proof_envelope_sha256,
        )
        attempted = False
        try:
            async with self._factory() as session, session.begin():
                await session.execute(text("SET LOCAL synchronous_commit = 'on'"))
                if await self._catalog(session) != deployment.fingerprint:
                    raise RuntimeBundleCustodyError(
                        "runtime_bundle_custody_unavailable"
                    )
                append_identity = (
                    await session.execute(
                        text(
                            "SELECT pg_backend_pid() AS backend_pid,pg_current_xact_id()::text AS transaction_id"
                        )
                    )
                ).one()
                attempted = True
                await session.execute(
                    text(
                        "SELECT public.public_runtime_bundle_v1_append("
                        ":bundle_id,:bundle_sha256,:proof_envelope_sha256,:bundle_raw)"
                    ),
                    {
                        "bundle_id": bundle_id,
                        "bundle_sha256": expected_sha256,
                        "proof_envelope_sha256": proof_envelope_sha256,
                        "bundle_raw": bundle_raw,
                    },
                )
            readback = await self._read(
                bundle_id=bundle_id,
                expected_sha256=expected_sha256,
                proof_envelope_sha256=proof_envelope_sha256,
            )
            if readback.bundle_raw != bundle_raw:
                raise RuntimeBundleCustodyError("runtime_bundle_readback_mismatch")
            if (
                type(append_identity.backend_pid) is not int
                or type(readback.readback_backend_pid) is not int
                or type(append_identity.transaction_id) is not str
                or not append_identity.transaction_id.isascii()
                or not append_identity.transaction_id.isdigit()
                or type(readback.readback_transaction_id) is not str
                or not readback.readback_transaction_id.isascii()
                or not readback.readback_transaction_id.isdigit()
                or append_identity.transaction_id == readback.readback_transaction_id
            ):
                raise RuntimeBundleCustodyError("runtime_bundle_readback_mismatch")
            readback = replace(
                readback,
                append_backend_pid=append_identity.backend_pid,
                append_transaction_id=append_identity.transaction_id,
            )
            self._require_deployment()
            receipt = OwnedRuntimeBundleCustodyReceipt(_ISSUER)
            _RECEIPTS[receipt] = (readback, _owner(), time.monotonic_ns())
            return receipt
        except BaseException as error:
            if attempted:
                self._poisoned = True
            if isinstance(
                error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
            ):
                raise
            if isinstance(error, RuntimeBundleCustodyError):
                raise
            raise RuntimeBundleCustodyError(
                "runtime_bundle_commit_uncertain"
                if attempted
                else "runtime_bundle_custody_unavailable"
            ) from None

    async def read_retained_bundle(
        self, *, bundle_id, expected_sha256, proof_envelope_sha256
    ):
        """Read-only reconciliation; it cannot recreate a consumed runtime receipt."""
        if (
            type(bundle_id) is not str
            or _ID.fullmatch(bundle_id) is None
            or type(expected_sha256) is not str
            or _SHA.fullmatch(expected_sha256) is None
            or type(proof_envelope_sha256) is not str
            or _SHA.fullmatch(proof_envelope_sha256) is None
        ):
            raise RuntimeBundleCustodyError("runtime_bundle_input_invalid")
        try:
            return await self._read(
                bundle_id=bundle_id,
                expected_sha256=expected_sha256,
                proof_envelope_sha256=proof_envelope_sha256,
            )
        except RuntimeBundleCustodyError:
            raise
        except Exception:  # noqa: BLE001 - Redact native connection/catalog details.
            raise RuntimeBundleCustodyError(
                "runtime_bundle_custody_unavailable"
            ) from None

    async def read_retained_bundle_by_pins(
        self, *, expected_sha256, proof_envelope_sha256
    ) -> RetainedRuntimeBundleReadback:
        """Reconcile known pins in a new owner; no append or runtime receipt.

        A poisoned owner stays poisoned. A new owner must complete the same
        restricted-role deployment preflight before this exact native readback.
        """
        bundle_id = runtime_bundle_id_v1(
            bundle_sha256=expected_sha256,
            proof_envelope_sha256=proof_envelope_sha256,
        )
        return await self.read_retained_bundle(
            bundle_id=bundle_id,
            expected_sha256=expected_sha256,
            proof_envelope_sha256=proof_envelope_sha256,
        )


__all__ = (
    "OwnedRuntimeBundleCustodyReceipt",
    "RetainedRuntimeBundleReadback",
    "RuntimeBundleCustodyError",
    "RuntimeBundleCustodyRepository",
    "runtime_bundle_id_v1",
)
