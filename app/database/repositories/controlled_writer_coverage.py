"""Durable prospective controlled Demo writer coverage; never trade authority.

This is the sole catalog declaration for DB0048. Native admission deliberately
denies until an independently captured PG17 deparser baseline is reviewed and
sealed in source. Diagnostic catalog and row observations cannot mint an owner.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.database.repositories.demo_control import DemoControlRepository
from app.database.repositories.public_runtime_bundle_custody import _native_engine
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.source_primitives import utc_from_ns
from app.trade_qualification.demo_control import (
    ControlScope,
    account_lock_key,
    document,
)

MAX_STATE_BYTES = 65536
MAX_EVENT_BYTES = 32768
MAX_AUDIT_BYTES = 262144
MAX_SNAPSHOT_BYTES = 2097152
PG17_DEPARSED_BASELINE = None
PG17_DEPARSED_BASELINE_STATUS = "PENDING_NOT_ACQUIRED_UNSEALED_DENY"
COVERAGE_TABLES = (
    "demo_controlled_writer_coverage_scopes",
    "demo_controlled_writer_coverage_journal",
)
_SHA = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_ID = re.compile(r"[a-f0-9]{32}\Z", re.ASCII)
_UID = re.compile(r"[0-9]{1,32}\Z", re.ASCII)
STATE_SCHEMA = "ctcc.demo_controlled_writer_coverage_state.v1"
EVENT_SCHEMA = "ctcc.demo_controlled_writer_coverage_event.v1"
FALSE_AUTHORITY_FIELDS = (
    "account_revision_published",
    "account_complete",
    "flat_start_permission",
    "event_eligibility",
    "execution_authority",
)
REVOCATION_REASONS = (
    "expired",
    "restart_reconciliation_required",
    "source_changed",
    "control_changed",
    "local_state_changed",
    "explicit_disarm",
    "commit_or_readback_uncertain",
)
STATE_FIELDS = (
    "schema_version",
    "environment",
    "account_id",
    "generation",
    "epoch_id",
    "journal_sequence",
    "state",
    "process_domain_nonce",
    "writer_inventory_sha256",
    "deployed_manifest_sha256",
    "policy_sha256",
    "identity_sha256",
    "credential_session_sha256",
    "control_owner_epoch",
    "control_revision",
    "control_event_sha256",
    "control_state_sha256",
    "control_pins",
    "control_lease_until",
    "control_emergency_stop",
    "control_arm_request_id",
    "source_plan_sha256",
    "source_chain_head_sha256",
    "source_bundle_sha256",
    "source_proof_envelope_sha256",
    "source_current_receipt_sha256",
    "source_native_proof_sha256",
    "source_native_readback_sha256",
    "source_custody_id",
    "source_current_expires_at",
    "source_current_monotonic_deadline_ns",
    "original_native_anchor",
    "transition_stamp",
    "audit_sha256",
    "created_at",
    "updated_at",
    "revocation_reason",
    "revocation_observed_control",
    *FALSE_AUTHORITY_FIELDS,
)
EVENT_FIELDS = (
    "schema_version",
    "environment",
    "account_id",
    "journal_sequence",
    "generation",
    "epoch_id",
    "command_id",
    "action",
    "previous_sha256",
    "state_sha256",
    "audit_sha256",
    "occurred_at",
)
AUDIT_FIELDS = (
    "schema_version",
    "environment",
    "account_id",
    "requested_settlement_currency",
    "currency_states",
    "unresolved_intents",
    "legacy",
    "blocking_reasons",
    "future_writer_exclusion",
    "execution_authority",
)
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
_MUTABLE_TRANSITION_FIELDS = frozenset(
    (
        "state",
        "journal_sequence",
        "transition_stamp",
        "updated_at",
        "revocation_reason",
        "revocation_observed_control",
    )
)


class ControlledWriterCoverageError(ValueError):
    """Fixed redacted diagnostic, never driver text or account/source content."""


def _deny(code="controlled_writer_coverage_invalid"):
    raise ControlledWriterCoverageError(code) from None


def canonical(value):
    """Canonical fixed native JSON, excluding callbacks and bool/int aliases."""
    budget = [65536]

    def visit(item, depth=0):
        budget[0] -= 1
        if budget[0] < 0 or depth > 24:
            _deny("controlled_writer_coverage_wire_bound")
        kind = type(item)
        if item is None or kind in (str, bool, int):
            return item
        if kind is dict and len(item) <= 4096 and all(type(k) is str for k in item):
            return {k: visit(v, depth + 1) for k, v in item.items()}
        if kind in (tuple, list) and len(item) <= 4096:
            return [visit(v, depth + 1) for v in item]
        _deny("controlled_writer_coverage_wire_type")

    return json.dumps(
        visit(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _digest(raw):
    return hashlib.sha256(raw.encode("ascii") if type(raw) is str else raw).hexdigest()


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


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        _deny("controlled_writer_coverage_shape")


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


COVERAGE_TABLE_SPECS = {
    COVERAGE_TABLES[0]: {
        "columns": [
            ("environment", "character varying(8)", False),
            ("account_id", "character varying(32)", False),
            ("generation", "bigint", False),
            ("epoch_id", "character varying(32)", False),
            ("journal_sequence", "bigint", False),
            ("state", "character varying(8)", False),
            ("state_json", "text", False),
            ("state_sha256", "character varying(64)", False),
            ("journal_sha256", "character varying(64)", False),
            ("created_at", "timestamp with time zone", False),
            ("updated_at", "timestamp with time zone", False),
        ],
        "primary_key": ("environment", "account_id"),
        "checks": {
            "ck_cwc_scope_identity": "environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND epoch_id ~ '^[a-f0-9]{32}$'",
            "ck_cwc_scope_revisions": "generation>0 AND journal_sequence>=generation",
            "ck_cwc_scope_state": "state IN ('prepared','active','revoked')",
            "ck_cwc_scope_hashes": "state_sha256 ~ '^[a-f0-9]{64}$' AND journal_sha256 ~ '^[a-f0-9]{64}$' AND encode(sha256(convert_to(state_json,'UTF8')),'hex')=state_sha256",
            "ck_cwc_scope_bounds": "octet_length(state_json) BETWEEN 1 AND 65536",
            "ck_cwc_scope_clocks": "created_at<=updated_at",
        },
        "foreign_keys": [
            (
                "fk_cwc_scope_control",
                ("environment", "account_id"),
                "demo_account_controls",
                ("environment", "account_id"),
                False,
                False,
            )
        ],
        "unique": [],
        "indexes": [],
    },
    COVERAGE_TABLES[1]: {
        "columns": [
            ("environment", "character varying(8)", False),
            ("account_id", "character varying(32)", False),
            ("journal_sequence", "bigint", False),
            ("generation", "bigint", False),
            ("epoch_id", "character varying(32)", False),
            ("command_id", "character varying(64)", False),
            ("action", "character varying(8)", False),
            ("previous_sha256", "character varying(64)", True),
            ("state_json", "text", False),
            ("state_sha256", "character varying(64)", False),
            ("event_json", "text", False),
            ("event_sha256", "character varying(64)", False),
            ("audit_json", "text", False),
            ("audit_sha256", "character varying(64)", False),
            ("occurred_at", "timestamp with time zone", False),
            ("recorded_at", "timestamp with time zone", False),
        ],
        "primary_key": ("environment", "account_id", "journal_sequence"),
        "checks": {
            "ck_cwc_journal_identity": "environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND epoch_id ~ '^[a-f0-9]{32}$' AND command_id ~ '^[a-f0-9]{64}$'",
            "ck_cwc_journal_revisions": "generation>0 AND journal_sequence>=generation",
            "ck_cwc_journal_action": "action IN ('prepared','active','revoked')",
            "ck_cwc_journal_previous": "(journal_sequence=1 AND previous_sha256 IS NULL) OR (journal_sequence>1 AND previous_sha256 IS NOT NULL AND previous_sha256 ~ '^[a-f0-9]{64}$')",
            "ck_cwc_journal_hashes": "state_sha256 ~ '^[a-f0-9]{64}$' AND event_sha256 ~ '^[a-f0-9]{64}$' AND audit_sha256 ~ '^[a-f0-9]{64}$' AND encode(sha256(convert_to(state_json,'UTF8')),'hex')=state_sha256 AND encode(sha256(convert_to(event_json,'UTF8')),'hex')=event_sha256 AND encode(sha256(convert_to(audit_json,'UTF8')),'hex')=audit_sha256",
            "ck_cwc_journal_bounds": "octet_length(state_json) BETWEEN 1 AND 65536 AND octet_length(event_json) BETWEEN 1 AND 32768 AND octet_length(audit_json) BETWEEN 1 AND 262144",
        },
        "foreign_keys": [
            (
                "fk_cwc_journal_scope",
                ("environment", "account_id"),
                COVERAGE_TABLES[0],
                ("environment", "account_id"),
                True,
                True,
            )
        ],
        "unique": [
            ("uq_cwc_command", ("environment", "account_id", "command_id")),
            (
                "uq_cwc_generation_action",
                ("environment", "account_id", "generation", "action"),
            ),
            (
                "uq_cwc_epoch_action",
                ("environment", "account_id", "epoch_id", "action"),
            ),
            ("uq_cwc_event_sha256", ("event_sha256",)),
        ],
        "indexes": [
            (
                "ix_cwc_scope_generation_sequence",
                ("environment", "account_id", "generation", "journal_sequence"),
                False,
            )
        ],
    },
}


def _table_ddl(name, spec):
    types = {
        "character varying(8)": "VARCHAR(8)",
        "character varying(32)": "VARCHAR(32)",
        "character varying(64)": "VARCHAR(64)",
    }
    fields = [
        f"{column} {types.get(kind, kind)}{' NOT NULL' if not nullable else ''}"
        + (" DEFAULT clock_timestamp()" if column == "recorded_at" else "")
        for column, kind, nullable in spec["columns"]
    ]
    fields.append(f"CONSTRAINT pk_{name} PRIMARY KEY ({','.join(spec['primary_key'])})")
    fields.extend(
        f"CONSTRAINT {key} CHECK ({expression})"
        for key, expression in spec["checks"].items()
    )
    for key, columns, target, target_columns, deferred, initially in spec[
        "foreign_keys"
    ]:
        fields.append(
            f"CONSTRAINT {key} FOREIGN KEY ({','.join(columns)}) REFERENCES public.{target} ({','.join(target_columns)}) ON DELETE RESTRICT"
            + (" DEFERRABLE" if deferred else " NOT DEFERRABLE")
            + (" INITIALLY DEFERRED" if initially else "")
        )
    fields.extend(
        f"CONSTRAINT {key} UNIQUE ({','.join(columns)})"
        for key, columns in spec["unique"]
    )
    return f"CREATE TABLE public.{name} (" + ",".join(fields) + ")"


COVERAGE_TABLE_SQL = tuple(
    _table_ddl(name, spec) for name, spec in COVERAGE_TABLE_SPECS.items()
)
COVERAGE_INDEX_SQL = tuple(
    f"CREATE {'UNIQUE ' if unique else ''}INDEX {name} ON public.{table} ({','.join(columns)})"
    for table, spec in COVERAGE_TABLE_SPECS.items()
    for name, columns, unique in spec["indexes"]
)

# All security-definer SQL uses qualified objects and the existing UID lock.
_UID_SQL = "('x'||substr(encode(pg_catalog.sha256(pg_catalog.convert_to('ctcc-qualification-account-v1','UTF8')||decode('00','hex')||pg_catalog.convert_to(p_environment,'UTF8')||decode('00','hex')||pg_catalog.convert_to(p_account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint"

_JOURNAL_BODY = r"""
DECLARE h public.demo_controlled_writer_coverage_scopes%ROWTYPE;
        c public.demo_account_controls%ROWTYPE; cj public.demo_control_journal%ROWTYPE;
        s jsonb; e jsonb; a jsonb; old_s jsonb; item record; raw text;
        k text; v jsonb; rebuilt text; ch text; encoded text; value_text text;
        i integer; code integer; low_code integer; kind text; frame jsonb;
        stack jsonb[]; stack_length integer; node_count integer; level integer;
        key_count integer; key_index integer;
        p_environment text; p_account_id text;
BEGIN
  p_environment:=NEW.environment; p_account_id:=NEW.account_id;
  PERFORM pg_catalog.pg_advisory_xact_lock(__UID_SQL__);
  SELECT * INTO c FROM public.demo_account_controls
    WHERE environment=NEW.environment AND account_id=NEW.account_id FOR UPDATE;
  SELECT * INTO h FROM public.demo_controlled_writer_coverage_scopes
    WHERE environment=NEW.environment AND account_id=NEW.account_id FOR UPDATE;
  IF c.account_id IS NULL THEN RAISE EXCEPTION 'controlled_writer_coverage_control_missing'; END IF;
  IF octet_length(NEW.state_json)>65536 OR octet_length(NEW.event_json)>32768
     OR octet_length(NEW.audit_json)>262144 THEN
    RAISE EXCEPTION 'controlled_writer_coverage_wire_bound';
  END IF;
  IF NOT (NEW.state_json IS JSON OBJECT WITH UNIQUE KEYS)
    OR NOT (NEW.event_json IS JSON OBJECT WITH UNIQUE KEYS)
    OR NOT (NEW.audit_json IS JSON OBJECT WITH UNIQUE KEYS) THEN
    RAISE EXCEPTION 'controlled_writer_coverage_duplicate_or_malformed'; END IF;
  s:=NEW.state_json::jsonb; e:=NEW.event_json::jsonb; a:=NEW.audit_json::jsonb;
  IF jsonb_typeof(s) IS DISTINCT FROM 'object' OR jsonb_typeof(e) IS DISTINCT FROM 'object'
    OR jsonb_typeof(a) IS DISTINCT FROM 'object'
    OR (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(s) key) IS DISTINCT FROM ARRAY[__STATE_KEYS__]::text[]
    OR (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(e) key) IS DISTINCT FROM ARRAY[__EVENT_KEYS__]::text[]
    OR (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(a) key) IS DISTINCT FROM ARRAY[__AUDIT_KEYS__]::text[] THEN
    RAISE EXCEPTION 'controlled_writer_coverage_shape';
  END IF;
  -- Rebuild canonical bytes using a bounded local stack, rather than assuming
  -- PostgreSQL JSONB ordering/escaping equals Python's sorted ensure_ascii JSON.
  -- The comparison also detects duplicate keys before they could be accepted:
  -- JSONB collapse cannot reproduce the original duplicate-containing bytes.
  FOREACH raw IN ARRAY ARRAY[NEW.state_json,NEW.event_json,NEW.audit_json] LOOP
    rebuilt:=''; stack:=ARRAY[jsonb_build_array('node',raw::jsonb,0)]; node_count:=0;
    WHILE cardinality(stack)>0 LOOP
      IF octet_length(rebuilt)>262144 THEN RAISE EXCEPTION 'controlled_writer_coverage_wire_bound'; END IF;
      stack_length:=cardinality(stack); frame:=stack[stack_length]; stack:=stack[1:stack_length-1];
      IF frame->>0='literal' THEN rebuilt:=rebuilt||(frame->>1); CONTINUE; END IF;
      v:=frame->1; level:=(frame->>2)::integer; kind:=jsonb_typeof(v);
      IF frame->>0<>'key' THEN node_count:=node_count+1; END IF;
      IF (frame->>0<>'key' AND level>24) OR node_count>65536 OR cardinality(stack)>262144 THEN
        RAISE EXCEPTION 'controlled_writer_coverage_wire_bound'; END IF;
      IF kind='object' THEN
        rebuilt:=rebuilt||'{'; stack:=array_append(stack,jsonb_build_array('literal','}'));
        SELECT count(*) INTO key_count FROM jsonb_each(v); key_index:=0;
        FOR item IN SELECT key,value FROM jsonb_each(v) ORDER BY key COLLATE "C" DESC LOOP
          key_index:=key_index+1;
          stack:=array_append(stack,jsonb_build_array('node',item.value,level+1));
          stack:=array_append(stack,jsonb_build_array('literal',':'));
          stack:=array_append(stack,jsonb_build_array('key',to_jsonb(item.key),level+1));
          IF key_index<key_count THEN stack:=array_append(stack,jsonb_build_array('literal',',')); END IF;
        END LOOP;
      ELSIF kind='array' THEN
        rebuilt:=rebuilt||'['; stack:=array_append(stack,jsonb_build_array('literal',']'));
        FOR item IN SELECT value,ordinality FROM jsonb_array_elements(v) WITH ORDINALITY ORDER BY ordinality DESC LOOP
          stack:=array_append(stack,jsonb_build_array('node',item.value,level+1));
          IF item.ordinality>1 THEN stack:=array_append(stack,jsonb_build_array('literal',',')); END IF;
        END LOOP;
      ELSIF kind='string' THEN
        value_text:=v#>>'{}'; encoded:='"';
        FOR i IN 1..length(value_text) LOOP
          ch:=substr(value_text,i,1); code:=ascii(ch);
          IF code=34 OR code=92 THEN encoded:=encoded||chr(92)||ch;
          ELSIF code=8 THEN encoded:=encoded||chr(92)||'b';
          ELSIF code=9 THEN encoded:=encoded||chr(92)||'t';
          ELSIF code=10 THEN encoded:=encoded||chr(92)||'n';
          ELSIF code=12 THEN encoded:=encoded||chr(92)||'f';
          ELSIF code=13 THEN encoded:=encoded||chr(92)||'r';
          ELSIF code<32 OR code>=127 THEN
            IF code<=65535 THEN encoded:=encoded||chr(92)||'u'||lpad(to_hex(code),4,'0');
            ELSE code:=code-65536; low_code:=56320+(code%1024);
              encoded:=encoded||chr(92)||'u'||lpad(to_hex(55296+(code/1024)),4,'0')||chr(92)||'u'||lpad(to_hex(low_code),4,'0'); END IF;
          ELSE encoded:=encoded||ch; END IF;
        END LOOP;
        rebuilt:=rebuilt||encoded||'"';
      ELSIF kind='number' THEN
        IF v::text !~ '^-?(0|[1-9][0-9]*)$' OR v::text='-0' THEN
          RAISE EXCEPTION 'controlled_writer_coverage_integer'; END IF;
        rebuilt:=rebuilt||v::text;
      ELSIF kind IN ('boolean','null') THEN rebuilt:=rebuilt||v::text;
      ELSE RAISE EXCEPTION 'controlled_writer_coverage_wire_type'; END IF;
    END LOOP;
    IF rebuilt IS DISTINCT FROM raw THEN RAISE EXCEPTION 'controlled_writer_coverage_noncanonical'; END IF;
  END LOOP;
  IF s->>'schema_version' IS DISTINCT FROM 'ctcc.demo_controlled_writer_coverage_state.v1'
    OR e->>'schema_version' IS DISTINCT FROM 'ctcc.demo_controlled_writer_coverage_event.v1'
    OR a->>'schema_version' IS DISTINCT FROM 'ctcc.portfolio_local_checkpoint.v2'
    OR s->>'environment' IS DISTINCT FROM NEW.environment OR s->>'account_id' IS DISTINCT FROM NEW.account_id
    OR s->>'state' IS DISTINCT FROM NEW.action OR e->>'action' IS DISTINCT FROM NEW.action
    OR e->>'environment' IS DISTINCT FROM NEW.environment OR e->>'account_id' IS DISTINCT FROM NEW.account_id
    OR s->>'epoch_id' IS DISTINCT FROM NEW.epoch_id OR e->>'epoch_id' IS DISTINCT FROM NEW.epoch_id
    OR e->>'command_id' IS DISTINCT FROM NEW.command_id
    OR e->>'previous_sha256' IS DISTINCT FROM NEW.previous_sha256
    OR e->>'state_sha256' IS DISTINCT FROM NEW.state_sha256
    OR e->>'audit_sha256' IS DISTINCT FROM NEW.audit_sha256
    OR s->>'audit_sha256' IS DISTINCT FROM NEW.audit_sha256
    OR encode(sha256(convert_to(NEW.state_json,'UTF8')),'hex') IS DISTINCT FROM NEW.state_sha256
    OR encode(sha256(convert_to(NEW.event_json,'UTF8')),'hex') IS DISTINCT FROM NEW.event_sha256
    OR encode(sha256(convert_to(NEW.audit_json,'UTF8')),'hex') IS DISTINCT FROM NEW.audit_sha256 THEN
    RAISE EXCEPTION 'controlled_writer_coverage_binding';
  END IF;
  FOREACH k IN ARRAY ARRAY['schema_version','environment','account_id','state'] LOOP
    IF jsonb_typeof(s->k) IS DISTINCT FROM 'string' THEN
      RAISE EXCEPTION 'controlled_writer_coverage_wire_type'; END IF;
  END LOOP;
  FOREACH k IN ARRAY ARRAY['schema_version','environment','account_id','epoch_id','command_id','action','state_sha256','audit_sha256','occurred_at'] LOOP
    IF jsonb_typeof(e->k) IS DISTINCT FROM 'string' THEN
      RAISE EXCEPTION 'controlled_writer_coverage_wire_type'; END IF;
  END LOOP;
  IF (NEW.journal_sequence=1 AND e->'previous_sha256' IS DISTINCT FROM 'null'::jsonb)
    OR (NEW.journal_sequence>1 AND jsonb_typeof(e->'previous_sha256') IS DISTINCT FROM 'string') THEN
    RAISE EXCEPTION 'controlled_writer_coverage_wire_type'; END IF;
  FOREACH k IN ARRAY ARRAY['generation','journal_sequence','control_owner_epoch','control_revision'] LOOP
    IF jsonb_typeof(s->k) IS DISTINCT FROM 'number' OR s->>k !~ '^[1-9][0-9]{0,18}$'
      OR (s->>k)::numeric>9223372036854775807 THEN
      RAISE EXCEPTION 'controlled_writer_coverage_integer'; END IF;
  END LOOP;
  FOREACH k IN ARRAY ARRAY['generation','journal_sequence'] LOOP
    IF e->k IS DISTINCT FROM s->k THEN RAISE EXCEPTION 'controlled_writer_coverage_integer'; END IF;
  END LOOP;
  IF (s->>'generation')::bigint<>NEW.generation OR (s->>'journal_sequence')::bigint<>NEW.journal_sequence
    OR (s->>'updated_at')::timestamptz IS DISTINCT FROM NEW.occurred_at
    OR s->>'updated_at' IS DISTINCT FROM e->>'occurred_at'
    OR s->'control_emergency_stop' IS DISTINCT FROM 'true'::jsonb
    OR s->'control_arm_request_id' IS DISTINCT FROM 'null'::jsonb THEN
    RAISE EXCEPTION 'controlled_writer_coverage_binding'; END IF;
  FOREACH k IN ARRAY ARRAY[__FALSE_KEYS__] LOOP
    IF s->k IS DISTINCT FROM 'false'::jsonb THEN RAISE EXCEPTION 'controlled_writer_coverage_authority_denied'; END IF;
  END LOOP;
  FOR item IN SELECT key,value FROM jsonb_each(s) WHERE key LIKE '%\_sha256' ESCAPE '\' LOOP
    IF jsonb_typeof(item.value) IS DISTINCT FROM 'string' OR item.value#>>'{}' !~ '^[a-f0-9]{64}$' THEN
      RAISE EXCEPTION 'controlled_writer_coverage_digest'; END IF;
  END LOOP;
  FOREACH k IN ARRAY ARRAY['epoch_id','process_domain_nonce','source_custody_id'] LOOP
    IF jsonb_typeof(s->k) IS DISTINCT FROM 'string' OR s->>k !~ '^[a-f0-9]{32}$' THEN
      RAISE EXCEPTION 'controlled_writer_coverage_identity'; END IF;
  END LOOP;
  IF jsonb_typeof(s->'control_pins') IS DISTINCT FROM 'object'
    OR (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(s->'control_pins') key)
      IS DISTINCT FROM ARRAY['config_sha256','credential_session_sha256','policy_sha256']::text[]
    OR EXISTS(SELECT 1 FROM jsonb_each(s->'control_pins') WHERE jsonb_typeof(value)<>'string' OR value#>>'{}' !~ '^[a-f0-9]{64}$') THEN
    RAISE EXCEPTION 'controlled_writer_coverage_pins'; END IF;
  IF s->>'policy_sha256' IS DISTINCT FROM s->'control_pins'->>'policy_sha256'
    OR s->>'credential_session_sha256' IS DISTINCT FROM s->'control_pins'->>'credential_session_sha256'
    OR a->>'environment' IS DISTINCT FROM NEW.environment OR a->>'account_id' IS DISTINCT FROM NEW.account_id
    OR jsonb_typeof(a->'account_id') IS DISTINCT FROM 'string'
    OR a->>'requested_settlement_currency' IS DISTINCT FROM 'USDT'
    OR a->'future_writer_exclusion' IS DISTINCT FROM 'false'::jsonb
    OR a->'execution_authority' IS DISTINCT FROM 'false'::jsonb
    OR a->'blocking_reasons' IS DISTINCT FROM '[]'::jsonb
    OR a->'unresolved_intents' IS DISTINCT FROM '[]'::jsonb
    OR jsonb_typeof(a->'currency_states') IS DISTINCT FROM 'array'
    OR jsonb_typeof(a->'legacy') IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'controlled_writer_coverage_audit'; END IF;
  FOREACH k IN ARRAY ARRAY['original_native_anchor','transition_stamp'] LOOP
    IF jsonb_typeof(s->k) IS DISTINCT FROM 'object'
      OR (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(s->k) key)
        IS DISTINCT FROM ARRAY['monotonic_ns','utc_ns']::text[]
      OR EXISTS(SELECT 1 FROM jsonb_each(s->k) WHERE jsonb_typeof(value)<>'number' OR value::text !~ '^(0|[1-9][0-9]{0,19})$' OR value::text::numeric>32503680000000000000) THEN
      RAISE EXCEPTION 'controlled_writer_coverage_stamp'; END IF;
  END LOOP;
  IF jsonb_typeof(s->'source_current_monotonic_deadline_ns') IS DISTINCT FROM 'number'
    OR s->>'source_current_monotonic_deadline_ns' !~ '^(0|[1-9][0-9]{0,19})$'
    OR (s->>'source_current_monotonic_deadline_ns')::numeric>32503680000000000000 THEN
    RAISE EXCEPTION 'controlled_writer_coverage_stamp'; END IF;
  FOREACH k IN ARRAY ARRAY['created_at','updated_at','control_lease_until','source_current_expires_at'] LOOP
    IF jsonb_typeof(s->k) IS DISTINCT FROM 'string' OR s->>k !~ '^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?\+00:00$' THEN
      RAISE EXCEPTION 'controlled_writer_coverage_time'; END IF;
  END LOOP;
  IF NEW.action<>'revoked' AND NEW.occurred_at IS DISTINCT FROM
    TIMESTAMPTZ 'epoch'+(ceil((s->'transition_stamp'->>'utc_ns')::numeric/1000)::text||' microseconds')::interval THEN
    RAISE EXCEPTION 'controlled_writer_coverage_transition_time'; END IF;
  IF NEW.action<>'revoked' THEN
    SELECT * INTO cj FROM public.demo_control_journal WHERE environment=c.environment
      AND account_id=c.account_id AND control_revision=c.control_revision;
    IF NOT c.emergency_stop OR c.arm_request_id IS NOT NULL OR c.owner_epoch<1
      OR c.lease_until<=clock_timestamp()
      OR NEW.occurred_at<c.updated_at OR NEW.occurred_at>=c.lease_until
      OR c.owner_epoch IS DISTINCT FROM (s->>'control_owner_epoch')::bigint
      OR c.control_revision IS DISTINCT FROM (s->>'control_revision')::bigint
      OR c.state_sha256 IS DISTINCT FROM s->>'control_state_sha256'
      OR cj.event_sha256 IS DISTINCT FROM s->>'control_event_sha256'
      OR encode(sha256(convert_to(cj.event_json,'UTF8')),'hex') IS DISTINCT FROM cj.event_sha256
      OR jsonb_build_object('config_sha256',c.state_json::jsonb->'config_sha256',
         'policy_sha256',c.state_json::jsonb->'policy_sha256',
         'credential_session_sha256',c.state_json::jsonb->'credential_session_sha256') IS DISTINCT FROM s->'control_pins'
      OR c.lease_until IS DISTINCT FROM (s->>'control_lease_until')::timestamptz
      OR (s->>'source_current_expires_at')::timestamptz<=clock_timestamp()
      OR s->'revocation_reason' IS DISTINCT FROM 'null'::jsonb
      OR s->'revocation_observed_control' IS DISTINCT FROM 'null'::jsonb THEN
      RAISE EXCEPTION 'controlled_writer_coverage_control_denied'; END IF;
  ELSE
    IF jsonb_typeof(s->'revocation_reason') IS DISTINCT FROM 'string'
      OR s->>'revocation_reason' NOT IN (__REVOCATION_REASONS__) THEN
      RAISE EXCEPTION 'controlled_writer_coverage_revocation'; END IF;
    IF s->'revocation_observed_control' IS DISTINCT FROM
      jsonb_build_object('revision',c.control_revision,'owner_epoch',c.owner_epoch,
      'event_sha256',(SELECT event_sha256 FROM public.demo_control_journal WHERE environment=c.environment AND account_id=c.account_id AND control_revision=c.control_revision),
      'state_sha256',c.state_sha256,'emergency_stop',c.emergency_stop,'arm_request_id',c.arm_request_id) THEN
      RAISE EXCEPTION 'controlled_writer_coverage_revocation'; END IF;
  END IF;
  IF h.account_id IS NULL THEN
    IF NEW.journal_sequence<>1 OR NEW.generation<>1 OR NEW.action<>'prepared'
      OR NEW.previous_sha256 IS NOT NULL THEN RAISE EXCEPTION 'controlled_writer_coverage_initial'; END IF;
  ELSE
    old_s:=h.state_json::jsonb;
    IF NEW.journal_sequence<>h.journal_sequence+1 OR NEW.previous_sha256 IS DISTINCT FROM h.journal_sha256 THEN
      RAISE EXCEPTION 'controlled_writer_coverage_cas'; END IF;
    IF NEW.action='prepared' THEN
      IF h.state<>'revoked' OR NEW.generation<>h.generation+1 OR NEW.epoch_id=h.epoch_id
        OR (s->>'control_owner_epoch')::bigint<=(old_s->>'control_owner_epoch')::bigint
        OR s->>'source_custody_id'=old_s->>'source_custody_id'
        OR s->>'source_bundle_sha256'=old_s->>'source_bundle_sha256'
        OR s->>'process_domain_nonce'=old_s->>'process_domain_nonce'
        OR s->>'created_at' IS DISTINCT FROM old_s->>'created_at' THEN
        RAISE EXCEPTION 'controlled_writer_coverage_generation'; END IF;
    ELSE
      IF NEW.generation<>h.generation OR NEW.epoch_id<>h.epoch_id
        OR NOT ((h.state='prepared' AND NEW.action IN ('active','revoked')) OR (h.state='active' AND NEW.action='revoked'))
        OR (s-ARRAY[__MUTABLE_KEYS__]::text[]) IS DISTINCT FROM (old_s-ARRAY[__MUTABLE_KEYS__]::text[])
        OR NEW.audit_sha256 IS DISTINCT FROM old_s->>'audit_sha256' THEN
        RAISE EXCEPTION 'controlled_writer_coverage_transition'; END IF;
      IF NEW.action='revoked' AND s->'transition_stamp' IS DISTINCT FROM old_s->'transition_stamp' THEN
        RAISE EXCEPTION 'controlled_writer_coverage_revocation_clock_domain'; END IF;
    END IF;
    IF NEW.occurred_at<h.updated_at OR (NEW.generation=h.generation AND
      ((s->'transition_stamp'->>'utc_ns')::numeric<(old_s->'transition_stamp'->>'utc_ns')::numeric
       OR (s->'transition_stamp'->>'monotonic_ns')::numeric<(old_s->'transition_stamp'->>'monotonic_ns')::numeric)) THEN
      RAISE EXCEPTION 'controlled_writer_coverage_clock_regressed'; END IF;
  END IF;
  RETURN NEW;
END
"""


def _array(values):
    return ",".join("'" + value + "'" for value in sorted(values))


_JOURNAL_BODY = (
    _JOURNAL_BODY.replace("__UID_SQL__", _UID_SQL)
    .replace("__STATE_KEYS__", _array(STATE_FIELDS))
    .replace("__EVENT_KEYS__", _array(EVENT_FIELDS))
    .replace("__AUDIT_KEYS__", _array(AUDIT_FIELDS))
    .replace("__FALSE_KEYS__", _array(FALSE_AUTHORITY_FIELDS))
    .replace("__MUTABLE_KEYS__", _array(_MUTABLE_TRANSITION_FIELDS))
    .replace("__REVOCATION_REASONS__", _array(REVOCATION_REASONS))
)

_SCOPE_BODY = """
DECLARE j public.demo_controlled_writer_coverage_journal%ROWTYPE;
BEGIN
  IF TG_OP='UPDATE' AND (NEW.environment IS DISTINCT FROM OLD.environment
    OR NEW.account_id IS DISTINCT FROM OLD.account_id OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.journal_sequence<>OLD.journal_sequence+1) THEN
    RAISE EXCEPTION 'controlled_writer_coverage_scope_cas'; END IF;
  IF TG_OP='INSERT' AND (NEW.journal_sequence<>1 OR NEW.generation<>1 OR NEW.state<>'prepared') THEN
    RAISE EXCEPTION 'controlled_writer_coverage_initial'; END IF;
  SELECT * INTO j FROM public.demo_controlled_writer_coverage_journal WHERE environment=NEW.environment
    AND account_id=NEW.account_id AND journal_sequence=NEW.journal_sequence;
  IF j.account_id IS NULL OR (j.generation,j.epoch_id,j.action,j.state_json,j.state_sha256,j.event_sha256,j.occurred_at)
    IS DISTINCT FROM (NEW.generation,NEW.epoch_id,NEW.state,NEW.state_json,NEW.state_sha256,NEW.journal_sha256,NEW.updated_at)
    OR NEW.created_at IS DISTINCT FROM (NEW.state_json::jsonb->>'created_at')::timestamptz THEN
    RAISE EXCEPTION 'controlled_writer_coverage_pair'; END IF;
  RETURN NEW;
END
"""
_PAIR_BODY = """
DECLARE h public.demo_controlled_writer_coverage_scopes%ROWTYPE;
        j public.demo_controlled_writer_coverage_journal%ROWTYPE;
BEGIN
  SELECT * INTO h FROM public.demo_controlled_writer_coverage_scopes
    WHERE environment=NEW.environment AND account_id=NEW.account_id;
  SELECT * INTO j FROM public.demo_controlled_writer_coverage_journal
    WHERE environment=NEW.environment AND account_id=NEW.account_id ORDER BY journal_sequence DESC LIMIT 1;
  IF h.account_id IS NULL OR j.account_id IS NULL
    OR (h.journal_sequence,h.generation,h.epoch_id,h.state,h.state_json,h.state_sha256,h.journal_sha256,h.updated_at)
      IS DISTINCT FROM (j.journal_sequence,j.generation,j.epoch_id,j.action,j.state_json,j.state_sha256,j.event_sha256,j.occurred_at) THEN
    RAISE EXCEPTION 'controlled_writer_coverage_pair'; END IF;
  RETURN NULL;
END
"""
_IMMUTABLE_BODY = """
BEGIN
  RAISE EXCEPTION 'controlled_writer_coverage_immutable';
END
"""
_APPEND_BODY = """
DECLARE h public.demo_controlled_writer_coverage_scopes%ROWTYPE; s jsonb; e jsonb;
        seq bigint; event_hash text; state_hash text; audit_hash text;
BEGIN
  IF current_setting('role')<>'none' OR current_setting('transaction_isolation')<>'read committed'
    OR current_setting('synchronous_commit')<>'on' OR current_setting('fsync')<>'on'
    OR current_setting('full_page_writes')<>'on' OR current_setting('session_replication_role')<>'origin'
    OR pg_is_in_recovery() OR EXISTS(SELECT 1 FROM pg_roles r WHERE rolname=session_user
      AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
    OR EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname<>session_user AND pg_has_role(session_user,r.oid,'MEMBER'))
    OR EXISTS(SELECT 1 FROM pg_class c WHERE c.oid IN
      ('public.demo_controlled_writer_coverage_scopes'::regclass,'public.demo_controlled_writer_coverage_journal'::regclass)
      AND (c.relowner=(SELECT oid FROM pg_roles WHERE rolname=session_user)
        OR has_table_privilege(session_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
        OR has_any_column_privilege(session_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
    OR has_schema_privilege(session_user,'public','CREATE')
    OR has_database_privilege(session_user,current_database(),'CREATE') THEN
    RAISE EXCEPTION 'controlled_writer_coverage_role_denied'; END IF;
  IF p_environment IS DISTINCT FROM 'demo' OR p_account_id IS NULL OR p_account_id !~ '^[0-9]{1,32}$'
    OR p_expected_sequence IS NULL OR p_expected_sequence<0
    OR (p_expected_sequence=0 AND p_expected_sha256 IS NOT NULL)
    OR (p_expected_sequence>0 AND (p_expected_sha256 IS NULL OR p_expected_sha256 !~ '^[a-f0-9]{64}$'))
    OR p_state_raw IS NULL OR octet_length(p_state_raw) NOT BETWEEN 1 AND 65536
    OR p_event_raw IS NULL OR octet_length(p_event_raw) NOT BETWEEN 1 AND 32768
    OR p_audit_raw IS NULL OR octet_length(p_audit_raw) NOT BETWEEN 1 AND 262144 THEN
    RAISE EXCEPTION 'controlled_writer_coverage_input'; END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(__UID_SQL__);
  PERFORM 1 FROM public.demo_account_controls WHERE environment=p_environment AND account_id=p_account_id FOR UPDATE;
  SELECT * INTO h FROM public.demo_controlled_writer_coverage_scopes WHERE environment=p_environment AND account_id=p_account_id FOR UPDATE;
  LOCK TABLE public.demo_controlled_writer_coverage_scopes, public.demo_controlled_writer_coverage_journal IN ROW EXCLUSIVE MODE NOWAIT;
  IF coalesce(h.journal_sequence,0)<>p_expected_sequence OR h.journal_sha256 IS DISTINCT FROM p_expected_sha256 THEN
    RAISE EXCEPTION 'controlled_writer_coverage_cas'; END IF;
  IF NOT (convert_from(p_state_raw,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
    OR NOT (convert_from(p_event_raw,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
    OR NOT (convert_from(p_audit_raw,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS) THEN
    RAISE EXCEPTION 'controlled_writer_coverage_duplicate_or_malformed'; END IF;
  s:=convert_from(p_state_raw,'UTF8')::jsonb; e:=convert_from(p_event_raw,'UTF8')::jsonb;
  seq:=p_expected_sequence+1; state_hash:=encode(sha256(p_state_raw),'hex');
  event_hash:=encode(sha256(p_event_raw),'hex'); audit_hash:=encode(sha256(p_audit_raw),'hex');
  INSERT INTO public.demo_controlled_writer_coverage_journal
    (environment,account_id,journal_sequence,generation,epoch_id,command_id,action,previous_sha256,
     state_json,state_sha256,event_json,event_sha256,audit_json,audit_sha256,occurred_at)
    VALUES(p_environment,p_account_id,seq,(s->>'generation')::bigint,s->>'epoch_id',e->>'command_id',s->>'state',
      p_expected_sha256,convert_from(p_state_raw,'UTF8'),state_hash,convert_from(p_event_raw,'UTF8'),event_hash,
      convert_from(p_audit_raw,'UTF8'),audit_hash,(s->>'updated_at')::timestamptz);
  IF h.account_id IS NULL THEN
    INSERT INTO public.demo_controlled_writer_coverage_scopes VALUES
      (p_environment,p_account_id,(s->>'generation')::bigint,s->>'epoch_id',seq,s->>'state',convert_from(p_state_raw,'UTF8'),
       state_hash,event_hash,(s->>'created_at')::timestamptz,(s->>'updated_at')::timestamptz);
  ELSE
    UPDATE public.demo_controlled_writer_coverage_scopes SET generation=(s->>'generation')::bigint,
      epoch_id=s->>'epoch_id',journal_sequence=seq,state=s->>'state',state_json=convert_from(p_state_raw,'UTF8'),
      state_sha256=state_hash,journal_sha256=event_hash,updated_at=(s->>'updated_at')::timestamptz
      WHERE environment=p_environment AND account_id=p_account_id
      AND demo_controlled_writer_coverage_scopes.journal_sequence=p_expected_sequence
      AND journal_sha256=p_expected_sha256;
    IF NOT FOUND THEN RAISE EXCEPTION 'controlled_writer_coverage_cas'; END IF;
  END IF;
  RETURN QUERY SELECT seq,event_hash;
END
""".replace("__UID_SQL__", _UID_SQL)

_READ_BODY = """
DECLARE h public.demo_controlled_writer_coverage_scopes%ROWTYPE; result jsonb;
BEGIN
  IF p_environment IS DISTINCT FROM 'demo' OR p_account_id IS NULL OR p_account_id !~ '^[0-9]{1,32}$'
    OR p_lock_mode IS NULL OR p_lock_mode NOT IN ('discover','append','readback') THEN
    RAISE EXCEPTION 'controlled_writer_coverage_selector_denied'; END IF;
  IF p_lock_mode='discover' THEN
    IF p_expected_sequence IS NOT NULL OR p_expected_sha256 IS NOT NULL THEN
      RAISE EXCEPTION 'controlled_writer_coverage_selector_denied'; END IF;
  ELSIF NOT ((p_lock_mode='append' AND p_expected_sequence=0 AND p_expected_sha256 IS NULL)
    OR (p_expected_sequence IS NOT NULL AND p_expected_sequence>0 AND p_expected_sha256 IS NOT NULL
      AND p_expected_sha256 ~ '^[a-f0-9]{64}$')) THEN
    RAISE EXCEPTION 'controlled_writer_coverage_selector_denied';
  ELSIF p_expected_sequence IS NULL THEN
    RAISE EXCEPTION 'controlled_writer_coverage_selector_denied';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(__UID_SQL__);
  PERFORM 1 FROM public.demo_account_controls WHERE environment=p_environment AND account_id=p_account_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'controlled_writer_coverage_control_missing'; END IF;
  SELECT * INTO h FROM public.demo_controlled_writer_coverage_scopes
    WHERE environment=p_environment AND account_id=p_account_id FOR UPDATE;
  IF p_lock_mode='append' THEN
    LOCK TABLE public.demo_controlled_writer_coverage_scopes, public.demo_controlled_writer_coverage_journal IN ROW EXCLUSIVE MODE NOWAIT;
  ELSIF p_lock_mode='readback' THEN
    LOCK TABLE public.demo_controlled_writer_coverage_scopes, public.demo_controlled_writer_coverage_journal IN SHARE MODE NOWAIT;
  END IF;
  IF p_lock_mode<>'discover' AND (coalesce(h.journal_sequence,0) IS DISTINCT FROM p_expected_sequence
    OR h.journal_sha256 IS DISTINCT FROM p_expected_sha256) THEN
    RAISE EXCEPTION 'controlled_writer_coverage_cas'; END IF;
  IF h.account_id IS NULL THEN
    IF p_lock_mode='readback' THEN RAISE EXCEPTION 'controlled_writer_coverage_cas'; END IF;
    RETURN jsonb_build_object('head',NULL,'records','[]'::jsonb,'previous_terminal_anchor',NULL);
  END IF;
  WITH r AS (
    SELECT j.* FROM public.demo_controlled_writer_coverage_journal j
    WHERE j.environment=p_environment AND j.account_id=p_account_id AND j.generation=h.generation
    ORDER BY j.journal_sequence
  ), a AS (
    SELECT j.* FROM public.demo_controlled_writer_coverage_journal j
    WHERE j.environment=p_environment AND j.account_id=p_account_id
      AND j.journal_sequence=(SELECT min(journal_sequence)-1 FROM r)
  ) SELECT jsonb_build_object('head',to_jsonb(h),'records',(SELECT jsonb_agg(to_jsonb(r) ORDER BY journal_sequence) FROM r),
      'previous_terminal_anchor',(SELECT to_jsonb(a) FROM a)) INTO result
    WHERE (SELECT count(*) FROM r) BETWEEN 1 AND 3
      AND (SELECT min(action) FROM r WHERE journal_sequence=(SELECT min(journal_sequence) FROM r))='prepared'
      AND (SELECT max(journal_sequence) FROM r)=h.journal_sequence
      AND (h.generation=1 OR EXISTS(SELECT 1 FROM a WHERE action='revoked' AND generation=h.generation-1))
      AND (h.generation<>1 OR NOT EXISTS(SELECT 1 FROM a));
  IF result IS NULL OR octet_length(result::text)>2097152 THEN
    RAISE EXCEPTION 'controlled_writer_coverage_readback'; END IF;
  RETURN result;
END
""".replace("__UID_SQL__", _UID_SQL)

_FUNCTIONS = (
    (
        "controlled_writer_coverage_v1_journal_guard",
        "",
        "trigger",
        "plpgsql",
        False,
        "VOLATILE",
        _JOURNAL_BODY,
    ),
    (
        "controlled_writer_coverage_v1_scope_guard",
        "",
        "trigger",
        "plpgsql",
        False,
        "VOLATILE",
        _SCOPE_BODY,
    ),
    (
        "controlled_writer_coverage_v1_pair_guard",
        "",
        "trigger",
        "plpgsql",
        False,
        "VOLATILE",
        _PAIR_BODY,
    ),
    (
        "controlled_writer_coverage_v1_immutable",
        "",
        "trigger",
        "plpgsql",
        False,
        "VOLATILE",
        _IMMUTABLE_BODY,
    ),
    (
        "controlled_writer_coverage_v1_append",
        "p_environment text,p_account_id text,p_expected_sequence bigint,p_expected_sha256 text,p_state_raw bytea,p_event_raw bytea,p_audit_raw bytea",
        "TABLE(journal_sequence bigint,event_sha256 text)",
        "plpgsql",
        True,
        "VOLATILE",
        _APPEND_BODY,
    ),
    (
        "controlled_writer_coverage_v1_read",
        "p_environment text,p_account_id text,p_expected_sequence bigint,p_expected_sha256 text,p_lock_mode text",
        "jsonb",
        "plpgsql",
        True,
        "VOLATILE",
        _READ_BODY,
    ),
)
COVERAGE_FUNCTION_SQL = {
    name: f"CREATE FUNCTION public.{name}({args}) RETURNS {returns} LANGUAGE {language} {volatility} SECURITY {'DEFINER' if definer else 'INVOKER'} SET search_path=pg_catalog,public,pg_temp AS $cwc${body}$cwc$"
    for name, args, returns, language, definer, volatility, body in _FUNCTIONS
}
COVERAGE_FUNCTION_SIGNATURES = {
    name: ",".join(part.rsplit(" ", 1)[-1] for part in args.split(",")) if args else ""
    for name, args, *_ in _FUNCTIONS
}
COVERAGE_TRIGGER_SPECS = (
    (
        "cwc_scope_guard",
        COVERAGE_TABLES[0],
        "BEFORE INSERT OR UPDATE",
        "ROW",
        "controlled_writer_coverage_v1_scope_guard",
        False,
    ),
    (
        "cwc_scope_no_delete",
        COVERAGE_TABLES[0],
        "BEFORE DELETE",
        "ROW",
        "controlled_writer_coverage_v1_immutable",
        False,
    ),
    (
        "cwc_scope_no_truncate",
        COVERAGE_TABLES[0],
        "BEFORE TRUNCATE",
        "STATEMENT",
        "controlled_writer_coverage_v1_immutable",
        False,
    ),
    (
        "cwc_journal_guard",
        COVERAGE_TABLES[1],
        "BEFORE INSERT",
        "ROW",
        "controlled_writer_coverage_v1_journal_guard",
        False,
    ),
    (
        "cwc_journal_immutable",
        COVERAGE_TABLES[1],
        "BEFORE UPDATE OR DELETE",
        "ROW",
        "controlled_writer_coverage_v1_immutable",
        False,
    ),
    (
        "cwc_journal_no_truncate",
        COVERAGE_TABLES[1],
        "BEFORE TRUNCATE",
        "STATEMENT",
        "controlled_writer_coverage_v1_immutable",
        False,
    ),
    (
        "cwc_scope_pair",
        COVERAGE_TABLES[0],
        "AFTER INSERT OR UPDATE",
        "ROW",
        "controlled_writer_coverage_v1_pair_guard",
        True,
    ),
    (
        "cwc_journal_pair",
        COVERAGE_TABLES[1],
        "AFTER INSERT",
        "ROW",
        "controlled_writer_coverage_v1_pair_guard",
        True,
    ),
)
COVERAGE_TRIGGER_SQL = tuple(
    f"CREATE {'CONSTRAINT ' if deferred else ''}TRIGGER {name} {events} ON public.{table} "
    + ("DEFERRABLE INITIALLY DEFERRED " if deferred else "")
    + f"FOR EACH {level} EXECUTE FUNCTION public.{function}()"
    for name, table, events, level, function, deferred in COVERAGE_TRIGGER_SPECS
)


def expected_controlled_writer_coverage_schema():
    """Source policy only. Pending native hashes never become observed truth."""
    return json.loads(
        canonical(
            {
                "schema_version": "ctcc.controlled_writer_coverage_catalog_policy.v1",
                "tables": COVERAGE_TABLE_SPECS,
                "functions": [
                    {
                        "name": name,
                        "arguments": COVERAGE_FUNCTION_SIGNATURES[name],
                        "namespace": "public",
                        "body_sha256": _digest(body),
                        "language": language,
                        "security_definer": definer,
                        "volatility": volatility,
                        "search_path": ["search_path=pg_catalog, public, pg_temp"],
                    }
                    for name, _, _, language, definer, volatility, body in _FUNCTIONS
                ],
                "triggers": COVERAGE_TRIGGER_SPECS,
                "native_baseline_status": PG17_DEPARSED_BASELINE_STATUS,
                "native_deparser_baseline": PG17_DEPARSED_BASELINE,
                "native_source_acceptance": False,
            }
        )
    )


_CATALOG_SQL = r"""
SELECT jsonb_build_object(
  'schema_version','ctcc.controlled_writer_coverage_actual_catalog.v1',
  'server',jsonb_build_object('version_num',current_setting('server_version_num'),
    'version',version(),'database',current_database(),'current_user',current_user,
    'session_user',session_user,'role_setting',current_setting('role'),'timezone',current_setting('TimeZone'),'search_path',current_setting('search_path'),
    'isolation',current_setting('transaction_isolation'),'synchronous_commit',current_setting('synchronous_commit'),
    'fsync',current_setting('fsync'),'full_page_writes',current_setting('full_page_writes'),
    'replication_role',current_setting('session_replication_role'),'in_recovery',pg_is_in_recovery()),
  'role',(SELECT jsonb_build_object('oid',oid,'name',rolname,'can_login',rolcanlogin,'superuser',rolsuper,
    'createdb',rolcreatedb,'createrole',rolcreaterole,'replication',rolreplication,'bypassrls',rolbypassrls,
    'other_membership',EXISTS(SELECT 1 FROM pg_roles x WHERE x.oid<>r.oid AND pg_has_role(current_user,x.oid,'MEMBER')),
    'can_create',has_schema_privilege(current_user,'public','CREATE') OR has_database_privilege(current_user,current_database(),'CREATE'))
    FROM pg_roles r WHERE rolname=current_user),
  'tables',coalesce((SELECT jsonb_agg(jsonb_build_object('name',c.relname,'oid',c.oid,'owner_oid',c.relowner,
    'owner',pg_get_userbyid(c.relowner),'kind',c.relkind,'persistence',c.relpersistence,
    'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,'acl',c.relacl::text,
    'table_access',has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
      OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'),
    'public_access',EXISTS(SELECT 1 FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) WHERE grantee=0))
    ORDER BY c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname=ANY(ARRAY[__TABLES__])), '[]'::jsonb),
  'columns',coalesce((SELECT jsonb_agg(jsonb_build_object('table',c.relname,'name',a.attname,
    'position',a.attnum,'type',format_type(a.atttypid,a.atttypmod),'nullable',NOT a.attnotnull,
    'default',pg_get_expr(d.adbin,d.adrelid),'generated',a.attgenerated,'identity',a.attidentity)
    ORDER BY c.relname,a.attnum) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
    WHERE n.nspname='public' AND c.relname=ANY(ARRAY[__TABLES__]) AND a.attnum>0 AND NOT a.attisdropped), '[]'::jsonb),
  'constraints',coalesce((SELECT jsonb_agg(jsonb_build_object('table',c.relname,'name',x.conname,
    'kind',x.contype,'definition',pg_get_constraintdef(x.oid,true),'validated',x.convalidated,
    'deferrable',x.condeferrable,'deferred',x.condeferred,'delete_action',x.confdeltype,
    'columns',(SELECT jsonb_agg(a.attname ORDER BY ord) FROM unnest(x.conkey) WITH ORDINALITY z(attnum,ord)
      JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=z.attnum),
    'target',CASE WHEN x.confrelid=0 THEN NULL ELSE (SELECT relname FROM pg_class WHERE oid=x.confrelid) END,
    'target_columns',(SELECT jsonb_agg(a.attname ORDER BY ord) FROM unnest(x.confkey) WITH ORDINALITY z(attnum,ord)
      JOIN pg_attribute a ON a.attrelid=x.confrelid AND a.attnum=z.attnum))
    ORDER BY c.relname,x.conname) FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(ARRAY[__TABLES__])), '[]'::jsonb),
  'indexes',coalesce((SELECT jsonb_agg(jsonb_build_object('table',c.relname,'name',ic.relname,
    'definition',pg_get_indexdef(i.indexrelid),'unique',i.indisunique,'primary',i.indisprimary,
    'valid',i.indisvalid,'ready',i.indisready,'method',am.amname,
    'predicate',pg_get_expr(i.indpred,i.indrelid),'expressions',pg_get_expr(i.indexprs,i.indrelid),
    'columns',(SELECT jsonb_agg(a.attname ORDER BY ord) FROM unnest(i.indkey) WITH ORDINALITY z(attnum,ord)
      JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=z.attnum))
    ORDER BY c.relname,ic.relname) FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_class ic ON ic.oid=i.indexrelid
    JOIN pg_am am ON am.oid=ic.relam WHERE n.nspname='public' AND c.relname=ANY(ARRAY[__TABLES__])), '[]'::jsonb),
  'functions',coalesce((SELECT jsonb_agg(jsonb_build_object('name',p.proname,'oid',p.oid,
    'namespace_oid',n.oid,'namespace',n.nspname,
    'arguments',oidvectortypes(p.proargtypes),'identity_arguments',pg_get_function_identity_arguments(p.oid),
    'returns',pg_get_function_result(p.oid),'owner_oid',p.proowner,'owner',pg_get_userbyid(p.proowner),
    'language',l.lanname,'security_definer',p.prosecdef,'volatility',p.provolatile,'config',p.proconfig,
    'kind',p.prokind,'leakproof',p.proleakproof,'strict',p.proisstrict,'parallel',p.proparallel,
    'source',p.prosrc,'definition',pg_get_functiondef(p.oid),'acl',p.proacl::text,
    'can_execute',has_function_privilege(current_user,p.oid,'EXECUTE'),
    'public_execute',EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) WHERE grantee=0 AND privilege_type='EXECUTE'))
    ORDER BY p.proname,oidvectortypes(p.proargtypes)) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    JOIN pg_language l ON l.oid=p.prolang WHERE n.nspname='public' AND p.proname LIKE 'controlled_writer_coverage_v1_%'), '[]'::jsonb),
  'triggers',coalesce((SELECT jsonb_agg(jsonb_build_object('table',c.relname,'name',t.tgname,
    'function',p.proname,'function_oid',t.tgfoid,
    'function_namespace_oid',fn.oid,'function_namespace',fn.nspname,
    'enabled',t.tgenabled,'type',t.tgtype,'deferrable',t.tgdeferrable,
    'deferred',t.tginitdeferred,'constraint',t.tgconstraint<>0,'arguments',encode(t.tgargs,'hex'),
    'when',pg_get_expr(t.tgqual,t.tgrelid),'definition',pg_get_triggerdef(t.oid,true))
    ORDER BY c.relname,t.tgname) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
    JOIN pg_namespace fn ON fn.oid=p.pronamespace
    WHERE n.nspname='public' AND c.relname=ANY(ARRAY[__TABLES__]) AND NOT t.tgisinternal), '[]'::jsonb)
)
""".replace("__TABLES__", _array(COVERAGE_TABLES))


async def read_controlled_writer_coverage_catalog(session):
    """Capture actual PG facts before admission, including a pending baseline."""
    if session.get_bind().dialect.name != "postgresql":
        _deny("controlled_writer_coverage_postgresql_required")
    result = await session.scalar(text(_CATALOG_SQL))
    if (
        type(result) is not dict
        or len(canonical(result).encode("ascii")) > MAX_SNAPSHOT_BYTES
    ):
        _deny("controlled_writer_coverage_catalog_bound")
    return result


def verify_controlled_writer_coverage_catalog(catalog, *, require_native_baseline=True):
    """Independently compare captured facts; never learn a desired baseline."""
    if type(require_native_baseline) is not bool:
        _deny("controlled_writer_coverage_catalog_invalid")
    _keys(
        catalog,
        (
            "schema_version",
            "server",
            "role",
            "tables",
            "columns",
            "constraints",
            "indexes",
            "functions",
            "triggers",
        ),
    )
    if catalog["schema_version"] != "ctcc.controlled_writer_coverage_actual_catalog.v1":
        _deny("controlled_writer_coverage_catalog_invalid")
    if not 170000 <= int(catalog["server"]["version_num"]) < 180000:
        _deny("controlled_writer_coverage_pg17_required")
    if {row["name"] for row in catalog["tables"]} != set(COVERAGE_TABLES) or len(
        catalog["tables"]
    ) != 2:
        _deny("controlled_writer_coverage_table_drift")
    owners = {row["owner_oid"] for row in catalog["tables"]}
    if len(owners) != 1 or any(
        row["kind"] != "r"
        or row["persistence"] != "p"
        or row["rls"] is not False
        or row["force_rls"] is not False
        or row["public_access"] is not False
        for row in catalog["tables"]
    ):
        _deny("controlled_writer_coverage_table_drift")
    for table, spec in COVERAGE_TABLE_SPECS.items():
        columns = [row for row in catalog["columns"] if row["table"] == table]
        if [(row["name"], row["type"], row["nullable"]) for row in columns] != spec[
            "columns"
        ]:
            _deny("controlled_writer_coverage_column_drift")
        if any(
            row["identity"] != ""
            or row["generated"] != ""
            or row["default"]
            != ("clock_timestamp()" if row["name"] == "recorded_at" else None)
            for row in columns
        ):
            _deny("controlled_writer_coverage_column_drift")
        constraints = {
            row["name"]: row for row in catalog["constraints"] if row["table"] == table
        }
        expected_names = {
            f"pk_{table}",
            *spec["checks"],
            *(row[0] for row in spec["foreign_keys"]),
            *(row[0] for row in spec["unique"]),
        }
        if set(constraints) != expected_names or any(
            row["validated"] is not True for row in constraints.values()
        ):
            _deny("controlled_writer_coverage_constraint_drift")
        primary = constraints[f"pk_{table}"]
        if (
            primary["kind"] != "p"
            or primary["columns"] != list(spec["primary_key"])
            or primary["deferrable"]
            or primary["deferred"]
        ):
            _deny("controlled_writer_coverage_constraint_drift")
        for name in spec["checks"]:
            row = constraints[name]
            if row["kind"] != "c" or row["deferrable"] or row["deferred"]:
                _deny("controlled_writer_coverage_constraint_drift")
        for name, columns, target, target_columns, deferrable, deferred in spec[
            "foreign_keys"
        ]:
            row = constraints[name]
            if (
                row["kind"],
                row["columns"],
                row["target"],
                row["target_columns"],
                row["delete_action"],
                row["deferrable"],
                row["deferred"],
            ) != (
                "f",
                list(columns),
                target,
                list(target_columns),
                "r",
                deferrable,
                deferred,
            ):
                _deny("controlled_writer_coverage_constraint_drift")
        for name, columns in spec["unique"]:
            row = constraints[name]
            if (
                row["kind"] != "u"
                or row["columns"] != list(columns)
                or row["deferrable"]
                or row["deferred"]
            ):
                _deny("controlled_writer_coverage_constraint_drift")
        indexes = {
            row["name"]: row for row in catalog["indexes"] if row["table"] == table
        }
        expected_indexes = {
            f"pk_{table}": (spec["primary_key"], True, True),
            **{name: (columns, True, False) for name, columns in spec["unique"]},
            **{
                name: (columns, unique, False)
                for name, columns, unique in spec["indexes"]
            },
        }
        if set(indexes) != set(expected_indexes):
            _deny("controlled_writer_coverage_index_drift")
        for name, (columns, unique, primary) in expected_indexes.items():
            row = indexes[name]
            if (
                row["columns"],
                row["unique"],
                row["primary"],
                row["method"],
                row["valid"],
                row["ready"],
                row["predicate"],
                row["expressions"],
            ) != (list(columns), unique, primary, "btree", True, True, None, None):
                _deny("controlled_writer_coverage_index_drift")
    functions = {row["name"]: row for row in catalog["functions"]}
    if len(functions) != len(catalog["functions"]) or set(functions) != set(
        COVERAGE_FUNCTION_SQL
    ):
        _deny("controlled_writer_coverage_function_drift")
    namespace_oids = set()
    function_oids = set()
    for name, _, returns, language, definer, volatility, body in _FUNCTIONS:
        row = functions[name]
        if (
            type(row["oid"]) is not int
            or not 1 <= row["oid"] <= 2**32 - 1
            or type(row["namespace_oid"]) is not int
            or not 1 <= row["namespace_oid"] <= 2**32 - 1
            or type(row["namespace"]) is not str
            or row["namespace"] != "public"
        ):
            _deny("controlled_writer_coverage_function_namespace_drift")
        namespace_oids.add(row["namespace_oid"])
        function_oids.add(row["oid"])
        if (
            row["arguments"].replace(" ", ""),
            row["source"],
            row["language"],
            row["security_definer"],
            row["volatility"],
            row["config"],
            row["kind"],
            row["leakproof"],
            row["strict"],
            row["parallel"],
            row["public_execute"],
        ) != (
            COVERAGE_FUNCTION_SIGNATURES[name],
            body,
            language,
            definer,
            "v" if volatility == "VOLATILE" else "s",
            ["search_path=pg_catalog, public, pg_temp"],
            "f",
            False,
            False,
            "u",
            False,
        ):
            _deny("controlled_writer_coverage_function_drift")
        if row["owner_oid"] not in owners:
            _deny("controlled_writer_coverage_owner_drift")
        if row["returns"].replace(" ", "") != returns.replace(" ", ""):
            _deny("controlled_writer_coverage_function_drift")
    if len(namespace_oids) != 1 or len(function_oids) != len(functions):
        _deny("controlled_writer_coverage_function_namespace_drift")
    triggers = {row["name"]: row for row in catalog["triggers"]}
    if len(triggers) != len(catalog["triggers"]) or set(triggers) != {
        row[0] for row in COVERAGE_TRIGGER_SPECS
    }:
        _deny("controlled_writer_coverage_trigger_drift")
    for name, table, events, level, function, deferred in COVERAGE_TRIGGER_SPECS:
        kind = (1 if level == "ROW" else 0) + (2 if events.startswith("BEFORE") else 0)
        kind += sum(
            bit
            for event, bit in (
                ("INSERT", 4),
                ("DELETE", 8),
                ("UPDATE", 16),
                ("TRUNCATE", 32),
            )
            if event in events
        )
        row = triggers[name]
        if (
            row["table"],
            row["function"],
            row["enabled"],
            row["type"],
            row["deferrable"],
            row["deferred"],
            row["constraint"],
            row["arguments"],
            row["when"],
        ) != (table, function, "A", kind, deferred, deferred, deferred, "", None):
            _deny("controlled_writer_coverage_trigger_drift")
        intended = functions[function]
        if (
            type(row["function_oid"]) is not int
            or type(row["function_namespace_oid"]) is not int
            or type(row["function_namespace"]) is not str
            or row["function_oid"] != intended["oid"]
            or row["function_namespace_oid"] != intended["namespace_oid"]
            or row["function_namespace"] != intended["namespace"]
            or row["function_namespace"] != "public"
        ):
            _deny("controlled_writer_coverage_trigger_identity_drift")
    if require_native_baseline:
        # Source never silently learns expected deparser output from this read.
        if (
            PG17_DEPARSED_BASELINE is None
            or PG17_DEPARSED_BASELINE_STATUS != "SEALED_FROM_REVIEWED_NATIVE_PG17"
        ):
            _deny("controlled_writer_coverage_native_baseline_unsealed")
        actual = {"constraints": catalog["constraints"], "indexes": catalog["indexes"]}
        if _digest(canonical(actual)) != PG17_DEPARSED_BASELINE:
            _deny("controlled_writer_coverage_deparser_drift")
    return expected_controlled_writer_coverage_schema()


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
