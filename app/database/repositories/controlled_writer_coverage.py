"""Durable prospective controlled Demo writer coverage; never trade authority.

The shared stdlib domain module owns the sole DB0048 catalog policy. This
repository retains native role, transaction and readback checks; diagnostic
catalog and row observations cannot mint an owner.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.database.repositories.demo_control import DemoControlRepository
from app.database.repositories.public_runtime_bundle_custody import _native_engine
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain import controlled_writer_coverage_catalog_v1 as _catalog_policy
from app.domain.source_primitives import utc_from_ns
from app.trade_qualification.demo_control import (
    ControlScope,
    account_lock_key,
    document,
)

# Compatibility exports are the same policy objects, not another implementation.
# Verifier globals and the sole sealed declarations remain in the domain module.
AUDIT_FIELDS = _catalog_policy.AUDIT_FIELDS
COVERAGE_FUNCTION_SIGNATURES = _catalog_policy.COVERAGE_FUNCTION_SIGNATURES
COVERAGE_FUNCTION_SQL = _catalog_policy.COVERAGE_FUNCTION_SQL
COVERAGE_INDEX_SQL = _catalog_policy.COVERAGE_INDEX_SQL
COVERAGE_TABLES = _catalog_policy.COVERAGE_TABLES
COVERAGE_TABLE_SPECS = _catalog_policy.COVERAGE_TABLE_SPECS
COVERAGE_TABLE_SQL = _catalog_policy.COVERAGE_TABLE_SQL
COVERAGE_TRIGGER_SPECS = _catalog_policy.COVERAGE_TRIGGER_SPECS
COVERAGE_TRIGGER_SQL = _catalog_policy.COVERAGE_TRIGGER_SQL
ControlledWriterCoverageError = _catalog_policy.ControlledWriterCoverageError
EVENT_FIELDS = _catalog_policy.EVENT_FIELDS
FALSE_AUTHORITY_FIELDS = _catalog_policy.FALSE_AUTHORITY_FIELDS
PG17_DEPARSED_BASELINE = _catalog_policy.PG17_DEPARSED_BASELINE
PG17_DEPARSED_BASELINE_STATUS = _catalog_policy.PG17_DEPARSED_BASELINE_STATUS
REVOCATION_REASONS = _catalog_policy.REVOCATION_REASONS
STATE_FIELDS = _catalog_policy.STATE_FIELDS
_APPEND_BODY = _catalog_policy._APPEND_BODY
_CATALOG_SQL = _catalog_policy._CATALOG_SQL
_FUNCTIONS = _catalog_policy._FUNCTIONS
_IMMUTABLE_BODY = _catalog_policy._IMMUTABLE_BODY
_JOURNAL_BODY = _catalog_policy._JOURNAL_BODY
_MUTABLE_TRANSITION_FIELDS = _catalog_policy._MUTABLE_TRANSITION_FIELDS
_PAIR_BODY = _catalog_policy._PAIR_BODY
_READ_BODY = _catalog_policy._READ_BODY
_SCOPE_BODY = _catalog_policy._SCOPE_BODY
_UID_SQL = _catalog_policy._UID_SQL
_array = _catalog_policy._array
_deny = _catalog_policy._deny
_digest = _catalog_policy._digest
_keys = _catalog_policy._keys
_table_ddl = _catalog_policy._table_ddl
canonical = _catalog_policy.canonical
expected_controlled_writer_coverage_schema = (
    _catalog_policy.expected_controlled_writer_coverage_schema
)
verify_controlled_writer_coverage_catalog = (
    _catalog_policy.verify_controlled_writer_coverage_catalog
)

MAX_STATE_BYTES = 65536
MAX_EVENT_BYTES = 32768
MAX_AUDIT_BYTES = 262144
MAX_SNAPSHOT_BYTES = 2097152
_SHA = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_ID = re.compile(r"[a-f0-9]{32}\Z", re.ASCII)
_UID = re.compile(r"[0-9]{1,32}\Z", re.ASCII)
STATE_SCHEMA = "ctcc.demo_controlled_writer_coverage_state.v1"
EVENT_SCHEMA = "ctcc.demo_controlled_writer_coverage_event.v1"
_PINS = ("config_sha256", "policy_sha256", "credential_session_sha256")
_STAMP = ("utc_ns", "monotonic_ns")
_REVOCATION_OBSERVATION = (
    "revision",
    "owner_epoch",
    "event_sha256",
    "state_sha256",
    "emergency_stop",
    "arm_request_id",
)


def _decode(raw, limit):
    if type(raw) is not str or not 1 <= len(raw.encode("utf8")) <= limit:
        _deny("controlled_writer_coverage_wire_bound")

    def pairs(items):
        if len(items) != len({key for key, _ in items}):
            _deny("controlled_writer_coverage_duplicate_key")
        return dict(items)

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: _deny("controlled_writer_coverage_nonfinite"),
        )
        if canonical(value) != raw:
            _deny("controlled_writer_coverage_noncanonical")
    except (ValueError, TypeError, RecursionError, UnicodeError):
        _deny("controlled_writer_coverage_wire_invalid")
    return value


def _integer(value, minimum=1, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        _deny("controlled_writer_coverage_integer")


def _hex(value, pattern=_SHA):
    if type(value) is not str or pattern.fullmatch(value) is None:
        _deny("controlled_writer_coverage_identity")


def _utc(value):
    if type(value) is not str:
        _deny("controlled_writer_coverage_time")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        _deny("controlled_writer_coverage_time")
    if stamp.tzinfo is not UTC or stamp.isoformat() != value:
        _deny("controlled_writer_coverage_time")
    return stamp


def _validate_state(raw):
    state = _decode(raw, MAX_STATE_BYTES)
    _keys(state, STATE_FIELDS)
    if state["schema_version"] != STATE_SCHEMA or state["environment"] != "demo":
        _deny("controlled_writer_coverage_demo_only")
    _hex(state["account_id"], _UID)
    for name in ("epoch_id", "process_domain_nonce", "source_custody_id"):
        _hex(state[name], _ID)
    for name, value in state.items():
        if name.endswith("_sha256"):
            _hex(value)
    for name in (
        "generation",
        "journal_sequence",
        "control_owner_epoch",
        "control_revision",
    ):
        _integer(state[name])
    if state["journal_sequence"] < state["generation"]:
        _deny("controlled_writer_coverage_revisions")
    if state["state"] not in ("prepared", "active", "revoked"):
        _deny("controlled_writer_coverage_transition")
    if (
        state["control_emergency_stop"] is not True
        or state["control_arm_request_id"] is not None
    ):
        _deny("controlled_writer_coverage_control_denied")
    if any(state[name] is not False for name in FALSE_AUTHORITY_FIELDS):
        _deny("controlled_writer_coverage_authority_denied")
    _keys(state["control_pins"], _PINS)
    for value in state["control_pins"].values():
        _hex(value)
    if any(
        state[name] != state["control_pins"][name]
        for name in ("policy_sha256", "credential_session_sha256")
    ):
        _deny("controlled_writer_coverage_pins")
    for name in ("original_native_anchor", "transition_stamp"):
        _keys(state[name], _STAMP)
        for value in state[name].values():
            _integer(value, 0, 32503680000000000000)
    _integer(state["source_current_monotonic_deadline_ns"], 0, 32503680000000000000)
    for name in (
        "control_lease_until",
        "source_current_expires_at",
        "created_at",
        "updated_at",
    ):
        _utc(state[name])
    if _utc(state["created_at"]) > _utc(state["updated_at"]):
        _deny("controlled_writer_coverage_time")
    if state["state"] != "revoked" and _utc(state["updated_at"]) != utc_from_ns(
        state["transition_stamp"]["utc_ns"]
    ):
        _deny("controlled_writer_coverage_transition_time")
    if state["state"] != "revoked":
        if (
            state["revocation_reason"] is not None
            or state["revocation_observed_control"] is not None
        ):
            _deny("controlled_writer_coverage_revocation")
    else:
        if state["revocation_reason"] not in REVOCATION_REASONS:
            _deny("controlled_writer_coverage_revocation")
        observed = state["revocation_observed_control"]
        if observed is not None:
            _keys(observed, _REVOCATION_OBSERVATION)
            _integer(observed["revision"])
            _integer(observed["owner_epoch"], 0)
            _hex(observed["event_sha256"])
            _hex(observed["state_sha256"])
            if type(observed["emergency_stop"]) is not bool:
                _deny("controlled_writer_coverage_revocation")
            if observed["arm_request_id"] is not None:
                _hex(observed["arm_request_id"])
    return state


def validate_coverage_records(state_raw, event_raw, audit_raw):
    """Pure diagnostics only; SQL/native separately verify actual provenance."""
    state = _validate_state(state_raw)
    event = _decode(event_raw, MAX_EVENT_BYTES)
    audit = _decode(audit_raw, MAX_AUDIT_BYTES)
    _keys(event, EVENT_FIELDS)
    if (
        type(audit) is not dict
        or audit.get("schema_version") != "ctcc.portfolio_local_checkpoint.v2"
    ):
        _deny("controlled_writer_coverage_audit")
    _keys(audit, AUDIT_FIELDS)
    if (
        audit["environment"] != state["environment"]
        or audit["account_id"] != state["account_id"]
        or type(audit["account_id"]) is not str
        or audit["requested_settlement_currency"] != "USDT"
        or type(audit["currency_states"]) is not list
        or type(audit["legacy"]) is not dict
        or audit["unresolved_intents"] != []
        or type(audit["unresolved_intents"]) is not list
        or audit["blocking_reasons"] != []
        or type(audit["blocking_reasons"]) is not list
        or audit["future_writer_exclusion"] is not False
        or audit["execution_authority"] is not False
    ):
        _deny("controlled_writer_coverage_audit")
    if state["audit_sha256"] != _digest(audit_raw):
        _deny("controlled_writer_coverage_audit")
    expected = {
        "schema_version": EVENT_SCHEMA,
        **{
            name: state[name]
            for name in (
                "environment",
                "account_id",
                "journal_sequence",
                "generation",
                "epoch_id",
            )
        },
        "command_id": event["command_id"],
        "action": state["state"],
        "previous_sha256": event["previous_sha256"],
        "state_sha256": _digest(state_raw),
        "audit_sha256": _digest(audit_raw),
        "occurred_at": state["updated_at"],
    }
    _hex(event["command_id"])
    if state["journal_sequence"] == 1:
        if event["previous_sha256"] is not None:
            _deny("controlled_writer_coverage_previous")
    else:
        _hex(event["previous_sha256"])
    if canonical(expected) != event_raw:
        _deny("controlled_writer_coverage_event")
    return state, event, audit


# All security-definer SQL uses qualified objects and the existing UID lock.
async def read_controlled_writer_coverage_catalog(session):
    """Capture actual PG facts before admission, including a pending baseline."""
    if isinstance(session, AsyncConnection):
        dialect = session.dialect
    elif isinstance(session, AsyncSession):
        dialect = session.get_bind().dialect
    else:
        _deny("controlled_writer_coverage_postgresql_required")
    if dialect.name != "postgresql":
        _deny("controlled_writer_coverage_postgresql_required")
    result = await session.scalar(text(_CATALOG_SQL))
    if (
        type(result) is not dict
        or len(canonical(result).encode("ascii")) > MAX_SNAPSHOT_BYTES
    ):
        _deny("controlled_writer_coverage_catalog_bound")
    return result


def _verify_native_role_catalog(catalog):
    """R6 mandatory actual runtime gate, independent of admin schema evidence."""
    try:
        role = catalog["role"]
        server = catalog["server"]
        tables = catalog["tables"]
        functions = catalog["functions"]
        if (
            type(role) is not dict
            or type(server) is not dict
            or type(tables) is not list
            or len(tables) != 2
            or type(functions) is not list
            or len(functions) != 6
            or type(role["oid"]) is not int
            or role["oid"] < 1
            or server["current_user"] != server["session_user"]
            or role["name"] != server["session_user"]
            or server["role_setting"] != "none"
            or role["can_login"] is not True
            or server["timezone"] != "UTC"
            or any(role["oid"] == row["owner_oid"] for row in [*tables, *functions])
            or any(
                role[name] is not False
                for name in (
                    "superuser",
                    "createdb",
                    "createrole",
                    "replication",
                    "bypassrls",
                    "other_membership",
                    "can_create",
                )
            )
            or any(row["table_access"] is not False for row in tables)
            or server["isolation"] != "read committed"
            or server["search_path"] != "pg_catalog, public, pg_temp"
            or any(
                server[name] != "on"
                for name in ("synchronous_commit", "fsync", "full_page_writes")
            )
            or server["replication_role"] != "origin"
            or server["in_recovery"] is not False
            or any(
                row["can_execute"]
                is not (
                    row["name"].endswith("_append") or row["name"].endswith("_read")
                )
                for row in functions
            )
        ):
            _deny("controlled_writer_coverage_role_denied")
    except (KeyError, TypeError, AttributeError):
        _deny("controlled_writer_coverage_role_denied")


async def read_controlled_writer_coverage_retention(session):
    """Exact bounded private row snapshot for actual downgrade retention proof."""
    rows = {}
    for table in COVERAGE_TABLES:
        raw = await session.scalar(
            text(
                f"WITH r AS MATERIALIZED (SELECT * FROM public.{table} LIMIT 8193), bounds AS ("
                "SELECT count(*) n,coalesce(sum(octet_length(to_jsonb(r)::text)),0) b FROM r) "
                "SELECT CASE WHEN n<=8192 AND b<=2097152 THEN (SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY environment,account_id"
                + (",journal_sequence" if table == COVERAGE_TABLES[1] else "")
                + "),'[]'::jsonb) FROM r) ELSE NULL END FROM bounds"
            )
        )
        if type(raw) is not list or len(raw) > 8192:
            _deny("controlled_writer_coverage_retention_bound")
        rows[table] = raw
    wire = canonical(rows)
    if len(wire.encode("ascii")) > MAX_SNAPSHOT_BYTES:
        _deny("controlled_writer_coverage_retention_bound")
    return {
        "schema_version": "ctcc.controlled_writer_coverage_retention.v1",
        "rows": rows,
        "counts": {name: len(value) for name, value in rows.items()},
        "rows_sha256": _digest(wire),
    }


@dataclass(frozen=True, slots=True, repr=False)
class CoverageObservationV1:
    scope: ControlScope
    state_json: str
    state_sha256: str
    journal_sha256: str
    journal_sequence: int
    generation: int
    epoch_id: str
    state: str
    audit_json: str
    audit_sha256: str
    records_json: str

    @property
    def state_document(self):
        return _validate_state(self.state_json)

    @property
    def execution_authority(self):
        return False


def _observation(scope, snapshot):
    _keys(snapshot, ("head", "records", "previous_terminal_anchor"))
    head = snapshot["head"]
    _keys(
        head,
        [
            column
            for column, _, _ in COVERAGE_TABLE_SPECS[COVERAGE_TABLES[0]]["columns"]
        ],
    )
    records = snapshot["records"]
    if type(records) is not list or not 1 <= len(records) <= 3:
        _deny("controlled_writer_coverage_readback")
    prior = snapshot["previous_terminal_anchor"]
    expected_previous = None if prior is None else prior["event_sha256"]
    previous_sequence = 0 if prior is None else prior["journal_sequence"]
    last = None
    last_state = None
    if prior is not None:
        _keys(
            prior,
            [
                column
                for column, _, _ in COVERAGE_TABLE_SPECS[COVERAGE_TABLES[1]]["columns"]
            ],
        )
        prior_state, prior_event, _ = validate_coverage_records(
            prior["state_json"], prior["event_json"], prior["audit_json"]
        )
        if (
            canonical(
                (
                    prior["environment"],
                    prior["account_id"],
                    prior["journal_sequence"],
                    prior["generation"],
                    prior["epoch_id"],
                    prior["action"],
                    prior["state_sha256"],
                    prior["event_sha256"],
                    prior["audit_sha256"],
                )
            )
            != canonical(
                (
                    scope.environment,
                    scope.account_id,
                    prior_state["journal_sequence"],
                    prior_state["generation"],
                    prior_state["epoch_id"],
                    "revoked",
                    _digest(prior["state_json"]),
                    _digest(prior["event_json"]),
                    _digest(prior["audit_json"]),
                )
            )
            or prior["previous_sha256"] != prior_event["previous_sha256"]
            or prior["command_id"] != prior_event["command_id"]
        ):
            _deny("controlled_writer_coverage_readback")
    for row in records:
        _keys(
            row,
            [
                column
                for column, _, _ in COVERAGE_TABLE_SPECS[COVERAGE_TABLES[1]]["columns"]
            ],
        )
        state, event, _ = validate_coverage_records(
            row["state_json"], row["event_json"], row["audit_json"]
        )
        if canonical(
            (
                row["environment"],
                row["account_id"],
                row["journal_sequence"],
                row["generation"],
                row["epoch_id"],
                row["action"],
                row["state_sha256"],
                row["event_sha256"],
                row["audit_sha256"],
                row["command_id"],
                row["previous_sha256"],
            )
        ) != canonical(
            (
                scope.environment,
                scope.account_id,
                state["journal_sequence"],
                state["generation"],
                state["epoch_id"],
                state["state"],
                _digest(row["state_json"]),
                _digest(row["event_json"]),
                _digest(row["audit_json"]),
                event["command_id"],
                expected_previous,
            )
        ):
            _deny("controlled_writer_coverage_readback")
        if row["journal_sequence"] != previous_sequence + 1:
            _deny("controlled_writer_coverage_readback")
        if last is None and row["action"] != "prepared":
            _deny("controlled_writer_coverage_readback")
        if last is not None and not (
            (last["action"] == "prepared" and row["action"] in ("active", "revoked"))
            or (last["action"] == "active" and row["action"] == "revoked")
        ):
            _deny("controlled_writer_coverage_readback")
        if last_state is not None:
            if (
                canonical(
                    {
                        key: value
                        for key, value in state.items()
                        if key not in _MUTABLE_TRANSITION_FIELDS
                    }
                )
                != canonical(
                    {
                        key: value
                        for key, value in last_state.items()
                        if key not in _MUTABLE_TRANSITION_FIELDS
                    }
                )
                or row["audit_json"] != last["audit_json"]
                or any(
                    state["transition_stamp"][key] < last_state["transition_stamp"][key]
                    for key in _STAMP
                )
                or _utc(state["updated_at"]) < _utc(last_state["updated_at"])
            ):
                _deny("controlled_writer_coverage_readback")
            if state["state"] == "revoked" and canonical(
                state["transition_stamp"]
            ) != canonical(last_state["transition_stamp"]):
                _deny("controlled_writer_coverage_revocation_clock_domain")
        elif prior is not None:
            if (
                state["generation"] != prior_state["generation"] + 1
                or state["epoch_id"] == prior_state["epoch_id"]
                or state["control_owner_epoch"] <= prior_state["control_owner_epoch"]
                or state["source_custody_id"] == prior_state["source_custody_id"]
                or state["source_bundle_sha256"] == prior_state["source_bundle_sha256"]
                or state["process_domain_nonce"] == prior_state["process_domain_nonce"]
                or state["created_at"] != prior_state["created_at"]
            ):
                _deny("controlled_writer_coverage_readback")
        for name in ("occurred_at", "recorded_at"):
            if type(row[name]) is not str:
                _deny("controlled_writer_coverage_readback")
            try:
                database_time = datetime.fromisoformat(row[name])
            except ValueError:
                _deny("controlled_writer_coverage_readback")
            if (
                database_time.tzinfo is not UTC
                or name == "occurred_at"
                and database_time != _utc(state["updated_at"])
            ):
                _deny("controlled_writer_coverage_readback")
        expected_previous = row["event_sha256"]
        previous_sequence = row["journal_sequence"]
        last = row
        last_state = state
    if prior is not None:
        validate_coverage_records(
            prior["state_json"], prior["event_json"], prior["audit_json"]
        )
        if (
            prior["action"] != "revoked"
            or prior["generation"] != head["generation"] - 1
            or _digest(prior["event_json"]) != prior["event_sha256"]
        ):
            _deny("controlled_writer_coverage_readback")
    elif head["generation"] != 1:
        _deny("controlled_writer_coverage_readback")
    for name, journal_name in (
        ("journal_sequence", "journal_sequence"),
        ("generation", "generation"),
        ("epoch_id", "epoch_id"),
        ("state", "action"),
        ("state_json", "state_json"),
        ("state_sha256", "state_sha256"),
        ("journal_sha256", "event_sha256"),
    ):
        if canonical(head[name]) != canonical(last[journal_name]):
            _deny("controlled_writer_coverage_readback")
    if (
        head["environment"] != scope.environment
        or head["account_id"] != scope.account_id
    ):
        _deny("controlled_writer_coverage_readback")
    for name in ("created_at", "updated_at"):
        try:
            database_time = datetime.fromisoformat(head[name])
        except (ValueError, TypeError):
            _deny("controlled_writer_coverage_readback")
        if database_time.tzinfo is not UTC or database_time != _utc(last_state[name]):
            _deny("controlled_writer_coverage_readback")
    raw = canonical(snapshot)
    if len(raw.encode("ascii")) > MAX_SNAPSHOT_BYTES:
        _deny("controlled_writer_coverage_readback_bound")
    return CoverageObservationV1(
        scope,
        head["state_json"],
        head["state_sha256"],
        head["journal_sha256"],
        head["journal_sequence"],
        head["generation"],
        head["epoch_id"],
        head["state"],
        last["audit_json"],
        last["audit_sha256"],
        raw,
    )


class ControlledWriterCoverageRepository:
    """Function-only new-table custody and source-owned same-UID transitions."""

    def __init__(self, session_factory):
        self._factory = session_factory
        self._engine = None
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
            or self._engine is not None
            and engine is not self._engine
        ):
            _deny("controlled_writer_coverage_native_factory")

    async def _setup(self, session, *, budget_ms=1000):
        self._check_factory()
        if (
            type(session) is not AsyncSession
            or type(budget_ms) is not int
            or not 1 <= budget_ms <= 1000
        ):
            _deny("controlled_writer_coverage_native_session")
        await session.execute(text("SET LOCAL synchronous_commit='on'"))
        await session.execute(
            text("SET LOCAL search_path TO pg_catalog, public, pg_temp")
        )
        await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        await session.execute(text(f"SET LOCAL statement_timeout='{budget_ms}ms'"))
        await session.execute(text(f"SET LOCAL lock_timeout='{budget_ms}ms'"))
        settings = (
            await session.execute(
                text(
                    "SELECT current_setting('transaction_isolation'),current_setting('search_path')"
                )
            )
        ).one()
        if tuple(settings) != ("read committed", "pg_catalog, public, pg_temp"):
            _deny("controlled_writer_coverage_native_session")

    @staticmethod
    async def _head(session, scope):
        await session.execute(
            select(func.pg_advisory_xact_lock(account_lock_key(scope)))
        )
        control, event = await DemoControlRepository.read_in_transaction(session, scope)
        # R5: discover locks the actual head before B5. The later fixed mode
        # takes the required table locks only after the old B5/legacy locks.
        head = await session.scalar(
            text(
                "SELECT public.controlled_writer_coverage_v1_read(:env,:uid,NULL,NULL,'discover')"
            ),
            {"env": scope.environment, "uid": scope.account_id},
        )
        if head is None:
            _deny("controlled_writer_coverage_selector_denied")
        if head == {"head": None, "records": [], "previous_terminal_anchor": None}:
            head = None
        return control, event, head

    @staticmethod
    async def _late_head(session, scope, expected, mode):
        sequence, sha256 = expected
        result = await session.scalar(
            text(
                "SELECT public.controlled_writer_coverage_v1_read(:env,:uid,:seq,:sha,:mode)"
            ),
            {
                "env": scope.environment,
                "uid": scope.account_id,
                "seq": sequence,
                "sha": sha256,
                "mode": mode,
            },
        )
        if result is None:
            _deny("controlled_writer_coverage_selector_denied")
        if result == {"head": None, "records": [], "previous_terminal_anchor": None}:
            if mode != "append" or expected != (0, None):
                _deny("controlled_writer_coverage_selector_denied")
            return None
        return result

    async def verify_deployment(self):
        async with self._factory() as session, session.begin():
            await self._setup(session)
            catalog = await read_controlled_writer_coverage_catalog(session)
            _verify_native_role_catalog(catalog)
            verify_controlled_writer_coverage_catalog(catalog)
        return {
            "schema_version": "ctcc.controlled_writer_coverage_deployment_observation.v1",
            "catalog_sha256": _digest(canonical(catalog)),
            "execution_authority": False,
        }

    async def read(self, scope):
        if type(scope) is not ControlScope:
            _deny("controlled_writer_coverage_scope")
        async with self._factory() as session, session.begin():
            await self._setup(session)
            _, _, snapshot = await self._head(session, scope)
            if snapshot is not None:
                await self._late_head(
                    session,
                    scope,
                    (
                        snapshot["head"]["journal_sequence"],
                        snapshot["head"]["journal_sha256"],
                    ),
                    "readback",
                )
            verify_controlled_writer_coverage_catalog(
                await read_controlled_writer_coverage_catalog(session)
            )
            if snapshot is None:
                _deny("controlled_writer_coverage_scope_missing")
            return _observation(scope, snapshot)

    @staticmethod
    def _binding(binding, key):
        from app.trade_qualification.controlled_writer_coverage_v1 import (
            _repository_binding_v1,
        )

        held = _repository_binding_v1(binding, key=key)
        _keys(
            held,
            (
                "session_factory",
                "control",
                "ledger",
                "ledger_scope",
                "state_template",
                "clock",
            ),
        )
        return held

    async def _native_locked(self, session, binding, key, expected=None):
        from app.trade_qualification.controlled_writer_coverage_v1 import (
            _repository_replay_v1,
        )

        held = self._binding(binding, key)
        if (
            held["session_factory"] is not self._factory
            or type(held["ledger"]) is not QualificationLedgerRepository
            or held["ledger"].session_factory is not self._factory
        ):
            _deny("controlled_writer_coverage_binding")
        stamp = await _repository_replay_v1(binding, key=key)
        now = held["clock"]()
        if type(now) is not datetime or now.tzinfo is not UTC:
            _deny("controlled_writer_coverage_clock")
        deadline = min(
            held["control"].state.lease_until,
            _utc(held["state_template"]["source_current_expires_at"]),
        )
        budget = min(1000, int((deadline - now).total_seconds() * 1000))
        await self._setup(session, budget_ms=budget)
        scope = held["control"].state.scope
        control, event, snapshot = await self._head(session, scope)
        if (
            canonical(document(control)) != canonical(document(held["control"].state))
            or event != held["control"].event_sha256
        ):
            _deny("controlled_writer_coverage_control_changed")
        ledger = held["ledger"]
        selected = await ledger._locked(session, held["ledger_scope"])
        audit = await ledger._read_portfolio_checkpoint_locked(
            session, held["ledger_scope"], selected
        )
        if not audit.known_flat:
            _deny("controlled_writer_coverage_local_state_unknown")
        current_pins = (
            (0, None)
            if snapshot is None
            else (
                snapshot["head"]["journal_sequence"],
                snapshot["head"]["journal_sha256"],
            )
        )
        if expected is not None and current_pins != expected:
            _deny("controlled_writer_coverage_cas")
        late = await self._late_head(
            session,
            scope,
            current_pins if expected is None else expected,
            "append" if expected is None else "readback",
        )
        if canonical(late) != canonical(snapshot):
            _deny("controlled_writer_coverage_head_changed")
        catalog = await read_controlled_writer_coverage_catalog(session)
        _verify_native_role_catalog(catalog)
        verify_controlled_writer_coverage_catalog(catalog)
        return held, stamp, audit, snapshot

    async def _append(self, session, scope, expected, state, audit_raw):
        state_raw = canonical(state)
        sequence = 0 if expected is None else expected.journal_sequence
        previous = None if expected is None else expected.journal_sha256
        event_raw = canonical(
            {
                "schema_version": EVENT_SCHEMA,
                "environment": scope.environment,
                "account_id": scope.account_id,
                "journal_sequence": sequence + 1,
                "generation": state["generation"],
                "epoch_id": state["epoch_id"],
                "command_id": secrets.token_hex(32),
                "action": state["state"],
                "previous_sha256": previous,
                "state_sha256": _digest(state_raw),
                "audit_sha256": _digest(audit_raw),
                "occurred_at": state["updated_at"],
            }
        )
        validate_coverage_records(state_raw, event_raw, audit_raw)
        result = (
            await session.execute(
                text(
                    "SELECT * FROM public.controlled_writer_coverage_v1_append(:env,:uid,:seq,:prev,:state,:event,:audit)"
                ),
                {
                    "env": scope.environment,
                    "uid": scope.account_id,
                    "seq": sequence,
                    "prev": previous,
                    "state": state_raw.encode("ascii"),
                    "event": event_raw.encode("ascii"),
                    "audit": audit_raw.encode("ascii"),
                },
            )
        ).one()
        if tuple(result) != (sequence + 1, _digest(event_raw)):
            _deny("controlled_writer_coverage_append_unknown")
        return sequence + 1, _digest(event_raw)

    async def _transition_native(self, binding, prepared, key):
        from app.trade_qualification.controlled_writer_coverage_v1 import (
            _repository_replay_v1,
        )

        if self._poisoned:
            _deny("controlled_writer_coverage_binding_poisoned")
        try:
            async with self._factory() as session, session.begin():
                held, stamp, audit, snapshot = await self._native_locked(
                    session, binding, key
                )
                scope = held["control"].state.scope
                old = None if snapshot is None else _observation(scope, snapshot)
                stamp = await _repository_replay_v1(binding, key=key)
                now = utc_from_ns(stamp["utc_ns"]).isoformat()
                if prepared is None:
                    if old is not None and old.state != "revoked":
                        _deny("controlled_writer_coverage_prior_epoch_unresolved")
                    state = dict(held["state_template"])
                    state.update(
                        generation=1 if old is None else old.generation + 1,
                        epoch_id=secrets.token_hex(16),
                        journal_sequence=1 if old is None else old.journal_sequence + 1,
                        state="prepared",
                        created_at=now
                        if old is None
                        else old.state_document["created_at"],
                        updated_at=now,
                        transition_stamp={
                            "utc_ns": stamp["utc_ns"],
                            "monotonic_ns": stamp["monotonic_ns"],
                        },
                        audit_sha256=audit.state_sha256,
                        revocation_reason=None,
                        revocation_observed_control=None,
                    )
                else:
                    if (
                        type(prepared) is not CoverageObservationV1
                        or old != prepared
                        or old.state != "prepared"
                        or audit.document_json.decode("ascii") != old.audit_json
                    ):
                        _deny("controlled_writer_coverage_prepared_changed")
                    state = old.state_document
                    state.update(
                        state="active",
                        journal_sequence=old.journal_sequence + 1,
                        updated_at=now,
                        transition_stamp={
                            "utc_ns": stamp["utc_ns"],
                            "monotonic_ns": stamp["monotonic_ns"],
                        },
                    )
                expected = await self._append(
                    session, scope, old, state, audit.document_json.decode("ascii")
                )
                await _repository_replay_v1(binding, key=key)
                self._binding(binding, key)
            # The committed session has closed before this new transaction.
            return await self._readback_native(binding, expected, key=key)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            self._poisoned = True
            raise
        except ControlledWriterCoverageError:
            self._poisoned = True
            raise
        except Exception:  # noqa: BLE001 - Never expose driver parameters or source bytes.
            self._poisoned = True
            _deny("controlled_writer_coverage_transition_unavailable")

    async def _prepare_native(self, binding, *, key):
        return await self._transition_native(binding, None, key)

    async def _activate_native(self, binding, prepared, *, key):
        return await self._transition_native(binding, prepared, key)

    async def _readback_native(self, binding, expected, *, key):
        from app.trade_qualification.controlled_writer_coverage_v1 import (
            _repository_replay_v1,
        )

        if self._poisoned:
            _deny("controlled_writer_coverage_binding_poisoned")
        try:
            target = (
                (expected.journal_sequence, expected.journal_sha256)
                if type(expected) is CoverageObservationV1
                else expected
            )
            if type(target) is not tuple or len(target) != 2:
                _deny("controlled_writer_coverage_readback")
            _integer(target[0])
            _hex(target[1])
            async with self._factory() as session, session.begin():
                held, _, audit, snapshot = await self._native_locked(
                    session, binding, key, target
                )
                if snapshot is None:
                    _deny("controlled_writer_coverage_readback")
                observed = _observation(held["control"].state.scope, snapshot)
                if (
                    observed.journal_sequence,
                    observed.journal_sha256,
                ) != target or observed.audit_json != audit.document_json.decode(
                    "ascii"
                ):
                    _deny("controlled_writer_coverage_readback")
                await _repository_replay_v1(binding, key=key)
                self._binding(binding, key)
            return observed
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            self._poisoned = True
            raise
        except ControlledWriterCoverageError:
            self._poisoned = True
            raise
        except Exception:  # noqa: BLE001 - Preserve uncertain rows; redact native driver text.
            self._poisoned = True
            _deny("controlled_writer_coverage_readback_unavailable")

    async def revoke_observed(
        self, scope, *, expected_sequence, expected_sha256, reason_code
    ):
        """Safety-only exact-CAS append; cannot reactivate or erase any epoch."""
        if type(scope) is not ControlScope or reason_code not in REVOCATION_REASONS:
            _deny("controlled_writer_coverage_revocation")
        _integer(expected_sequence)
        _hex(expected_sha256)
        async with self._factory() as session, session.begin():
            await self._setup(session)
            control, event, snapshot = await self._head(session, scope)
            await self._late_head(
                session, scope, (expected_sequence, expected_sha256), "append"
            )
            verify_controlled_writer_coverage_catalog(
                await read_controlled_writer_coverage_catalog(session)
            )
            if snapshot is None:
                _deny("controlled_writer_coverage_scope_missing")
            old = _observation(scope, snapshot)
            if (old.journal_sequence, old.journal_sha256) != (
                expected_sequence,
                expected_sha256,
            ) or old.state == "revoked":
                _deny("controlled_writer_coverage_cas")
            state = old.state_document
            state = _revocation_document(
                state,
                now=datetime.now(UTC),
                reason=reason_code,
                observed={
                    "revision": control.revision,
                    "owner_epoch": control.owner_epoch,
                    "event_sha256": event,
                    "state_sha256": _digest(canonical(document(control))),
                    "emergency_stop": control.emergency_stop,
                    "arm_request_id": control.arm_request_id,
                },
            )
            # R8: original stamps remain in their original boot/process domain.
            # Only the new actual UTC records this safety-only revocation.
            target = await self._append(session, scope, old, state, old.audit_json)
        observed = await self.read(scope)
        if (
            observed.journal_sequence,
            observed.journal_sha256,
        ) != target or observed.state != "revoked":
            _deny("controlled_writer_coverage_revocation_readback")
        return observed


def _revocation_document(state, *, now, reason, observed):
    """Pure safety-record construction, never a clock/source/owner issuer."""
    checked = _validate_state(canonical(state))
    if (
        type(now) is not datetime
        or now.tzinfo is not UTC
        or now < _utc(checked["updated_at"])
        or reason not in REVOCATION_REASONS
        or checked["state"] not in ("prepared", "active")
    ):
        _deny("controlled_writer_coverage_revocation_time")
    checked.update(
        state="revoked",
        journal_sequence=checked["journal_sequence"] + 1,
        updated_at=now.isoformat(),
        revocation_reason=reason,
        revocation_observed_control=observed,
    )
    _validate_state(canonical(checked))
    return checked
