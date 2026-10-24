"""Explicitly synthetic DB0048 fixtures. Never a native source or live owner."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.database.repositories.controlled_writer_coverage import (
    COVERAGE_TRIGGER_SPECS,
    EVENT_SCHEMA,
    FALSE_AUTHORITY_FIELDS,
    STATE_FIELDS,
    STATE_SCHEMA,
    _observation,
    canonical,
    validate_coverage_records,
)
from app.trade_qualification.demo_control import (
    ControlPins,
    ControlScope,
    acquire,
    advance,
    document,
)


def sha(raw):
    return hashlib.sha256(raw.encode("ascii") if type(raw) is str else raw).hexdigest()


def coverage_record_fixture(
    *,
    uid="730048",
    now=None,
    state_overrides=None,
    control=None,
    control_event_sha256=None,
    previous_sha256=None,
    audit_raw=None,
):
    """Fixed canonical synthetic bytes, with no private issuer binding API."""
    now = datetime(2026, 10, 24, tzinfo=UTC) if now is None else now
    pins = ControlPins("1" * 64, "2" * 64, "3" * 64)
    if control is None:
        acquired = acquire(
            None,
            scope=ControlScope("demo", uid),
            owner_sha256="4" * 64,
            pins=pins,
            now=now,
        )
        control = advance(
            acquired,
            owner_sha256="4" * 64,
            owner_epoch=1,
            expected_revision=1,
            action="estop",
            command_id="5" * 64,
            now=now + timedelta(microseconds=1),
        )
        now = control.updated_at
    if control_event_sha256 is None:
        control_event_sha256 = "6" * 64
    if audit_raw is None:
        audit_raw = canonical(
            {
                "schema_version": "ctcc.portfolio_local_checkpoint.v2",
                "environment": "demo",
                "account_id": uid,
                "requested_settlement_currency": "USDT",
                "currency_states": [],
                "unresolved_intents": [],
                "legacy": {},
                "blocking_reasons": [],
                "future_writer_exclusion": False,
                "execution_authority": False,
            }
        )
    ns = int(now.timestamp()) * 1_000_000_000 + now.microsecond * 1000
    state = {name: "7" * 64 for name in STATE_FIELDS if name.endswith("_sha256")}
    state.update(
        {
            "schema_version": STATE_SCHEMA,
            "environment": "demo",
            "account_id": uid,
            "generation": 1,
            "epoch_id": "8" * 32,
            "journal_sequence": 1,
            "state": "prepared",
            "process_domain_nonce": "9" * 32,
            "control_owner_epoch": control.owner_epoch,
            "control_revision": control.revision,
            "control_event_sha256": control_event_sha256,
            "control_state_sha256": sha(canonical(document(control))),
            "policy_sha256": control.pins.policy_sha256,
            "credential_session_sha256": control.pins.credential_session_sha256,
            "control_pins": {
                "config_sha256": control.pins.config_sha256,
                "policy_sha256": control.pins.policy_sha256,
                "credential_session_sha256": control.pins.credential_session_sha256,
            },
            "control_lease_until": control.lease_until.isoformat(),
            "control_emergency_stop": True,
            "control_arm_request_id": None,
            "source_custody_id": "a" * 32,
            "source_current_expires_at": (now + timedelta(seconds=20)).isoformat(),
            "source_current_monotonic_deadline_ns": 21_000_000_000,
            "original_native_anchor": {"utc_ns": ns, "monotonic_ns": 1_000_000_000},
            "transition_stamp": {"utc_ns": ns, "monotonic_ns": 1_000_000_000},
            "audit_sha256": sha(audit_raw),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "revocation_reason": None,
            "revocation_observed_control": None,
            **{name: False for name in FALSE_AUTHORITY_FIELDS},
        }
    )
    state.update(state_overrides or {})
    state_raw = canonical(state)
    event_raw = canonical(
        {
            "schema_version": EVENT_SCHEMA,
            "environment": "demo",
            "account_id": uid,
            "journal_sequence": state["journal_sequence"],
            "generation": state["generation"],
            "epoch_id": state["epoch_id"],
            "command_id": secrets.token_hex(32),
            "action": state["state"],
            "previous_sha256": previous_sha256,
            "state_sha256": sha(state_raw),
            "audit_sha256": sha(audit_raw),
            "occurred_at": state["updated_at"],
        }
    )
    validate_coverage_records(state_raw, event_raw, audit_raw)
    return {
        "state": state,
        "state_raw": state_raw,
        "event_raw": event_raw,
        "audit_raw": audit_raw,
        "event_sha256": sha(event_raw),
    }


async def _store_control_fixture(
    connection, state, *, command_id, action, previous_sha256
):
    """Insert actual source-validated synthetic Control rows into owned test DB."""
    raw = canonical(document(state))
    values = {
        "env": state.scope.environment,
        "uid": state.scope.account_id,
        "revision": state.revision,
        "owner": state.owner_sha256,
        "epoch": state.owner_epoch,
        "lease": state.lease_until,
        "stop": state.emergency_stop,
        "arm": state.arm_request_id,
        "arm_expires": state.arm_expires_at,
        "raw": raw,
        "sha": sha(raw),
        "created": state.created_at,
        "updated": state.updated_at,
    }
    if state.revision == 1:
        await connection.execute(
            text("""
          INSERT INTO public.demo_account_controls(environment,account_id,control_revision,owner_sha256,
            owner_epoch,lease_until,emergency_stop,arm_request_id,arm_expires_at,state_json,state_sha256,created_at,updated_at)
          VALUES(:env,:uid,:revision,:owner,:epoch,:lease,:stop,:arm,:arm_expires,:raw,:sha,:created,:updated)
        """),
            values,
        )
    else:
        await connection.execute(
            text("""
          UPDATE public.demo_account_controls SET control_revision=:revision,owner_sha256=:owner,
            owner_epoch=:epoch,lease_until=:lease,emergency_stop=:stop,arm_request_id=:arm,
            arm_expires_at=:arm_expires,state_json=:raw,state_sha256=:sha,updated_at=:updated
          WHERE environment=:env AND account_id=:uid
        """),
            values,
        )
    event_raw = canonical(
        {
            "version": "ctcc.demo_control_event.v1",
            "command_id": command_id,
            "action": action,
            "previous_sha256": previous_sha256,
            "state": document(state),
        }
    )
    await connection.execute(
        text("""
      INSERT INTO public.demo_control_journal(environment,account_id,control_revision,command_id,action,
        previous_sha256,event_sha256,state_sha256,event_json,occurred_at)
      VALUES(:env,:uid,:revision,:command,:action,:previous,:event_sha,:sha,:event_raw,:updated)
    """),
        {
            **values,
            "command": command_id,
            "action": action,
            "previous": previous_sha256,
            "event_sha": sha(event_raw),
            "event_raw": event_raw,
        },
    )
    return sha(event_raw)


async def seed_controlled_writer_control(connection, *, uid="730048"):
    """Actual acquire/owned-estop records, synthetic UID; no native admission."""
    now = datetime.now(UTC)
    owner = sha(secrets.token_bytes(32))
    acquired = acquire(
        None,
        scope=ControlScope("demo", uid),
        owner_sha256=owner,
        pins=ControlPins("1" * 64, "2" * 64, "3" * 64),
        now=now,
    )
    previous = await _store_control_fixture(
        connection,
        acquired,
        command_id=secrets.token_hex(32),
        action="acquire",
        previous_sha256=None,
    )
    stopped = advance(
        acquired,
        owner_sha256=owner,
        owner_epoch=1,
        expected_revision=1,
        action="estop",
        command_id=secrets.token_hex(32),
        now=now + timedelta(microseconds=1),
    )
    event = await _store_control_fixture(
        connection,
        stopped,
        command_id=secrets.token_hex(32),
        action="estop",
        previous_sha256=previous,
    )
    return stopped, event


async def seed_controlled_writer_coverage(
    connection, *, uid="730048", action="prepared"
):
    """Persist valid synthetic guarded rows. Retain them until owned DB disposal.

    The fixture is for migration/retention proofs, never a positive native owner.
    It deliberately uses the fixture administrator's direct DML so no deployment
    role grant or original native source is fabricated for this seed operation.
    """
    if action != "prepared":
        raise ValueError("synthetic_coverage_seed_prepared_only")
    control, event = await seed_controlled_writer_control(connection, uid=uid)
    fixture = coverage_record_fixture(
        uid=uid, now=control.updated_at, control=control, control_event_sha256=event
    )
    state = fixture["state"]
    await connection.execute(
        text("""
      INSERT INTO public.demo_controlled_writer_coverage_journal
      (environment,account_id,journal_sequence,generation,epoch_id,command_id,action,previous_sha256,
       state_json,state_sha256,event_json,event_sha256,audit_json,audit_sha256,occurred_at)
      VALUES('demo',:uid,1,1,:epoch,:command,'prepared',NULL,:state,:state_sha,:event,:event_sha,:audit,:audit_sha,:now)
    """),
        {
            "uid": uid,
            "epoch": state["epoch_id"],
            "command": json.loads(fixture["event_raw"])["command_id"],
            "state": fixture["state_raw"],
            "state_sha": sha(fixture["state_raw"]),
            "event": fixture["event_raw"],
            "event_sha": fixture["event_sha256"],
            "audit": fixture["audit_raw"],
            "audit_sha": sha(fixture["audit_raw"]),
            "now": control.updated_at,
        },
    )
    await connection.execute(
        text("""
      INSERT INTO public.demo_controlled_writer_coverage_scopes
      VALUES('demo',:uid,1,:epoch,1,'prepared',:state,:state_sha,:event_sha,:now,:now)
    """),
        {
            "uid": uid,
            "epoch": state["epoch_id"],
            "state": fixture["state_raw"],
            "state_sha": sha(fixture["state_raw"]),
            "event_sha": fixture["event_sha256"],
            "now": control.updated_at,
        },
    )
    return fixture


def coverage_observation_fixture(
    state_raw, event_raw, audit_raw, *, prior_records=(), previous_terminal_anchor=None
):
    """Replay a synthetic exact journal through the real diagnostic validator."""
    state, _, _ = validate_coverage_records(state_raw, event_raw, audit_raw)

    def row(fixture):
        s, e, _ = validate_coverage_records(
            fixture["state_raw"], fixture["event_raw"], fixture["audit_raw"]
        )
        return {
            "environment": s["environment"],
            "account_id": s["account_id"],
            "journal_sequence": s["journal_sequence"],
            "generation": s["generation"],
            "epoch_id": s["epoch_id"],
            "command_id": e["command_id"],
            "action": s["state"],
            "previous_sha256": e["previous_sha256"],
            "state_json": fixture["state_raw"],
            "state_sha256": sha(fixture["state_raw"]),
            "event_json": fixture["event_raw"],
            "event_sha256": sha(fixture["event_raw"]),
            "audit_json": fixture["audit_raw"],
            "audit_sha256": sha(fixture["audit_raw"]),
            "occurred_at": s["updated_at"],
            "recorded_at": s["updated_at"],
        }

    current = row(
        {"state_raw": state_raw, "event_raw": event_raw, "audit_raw": audit_raw}
    )
    snapshot = {
        "head": {
            "environment": state["environment"],
            "account_id": state["account_id"],
            "generation": state["generation"],
            "epoch_id": state["epoch_id"],
            "journal_sequence": state["journal_sequence"],
            "state": state["state"],
            "state_json": state_raw,
            "state_sha256": sha(state_raw),
            "journal_sha256": sha(event_raw),
            "created_at": state["created_at"],
            "updated_at": state["updated_at"],
        },
        "records": [*(row(fixture) for fixture in prior_records), current],
        "previous_terminal_anchor": None
        if previous_terminal_anchor is None
        else row(previous_terminal_anchor),
    }
    return _observation(ControlScope("demo", state["account_id"]), snapshot)


def coverage_constraint_trigger_catalog_rows_fixture():
    """Synthetic pair rows only, never an actual PG read or native baseline.

    The small structural values follow the separately reviewed PG17 observation;
    constructing these records does not provide catalog or role provenance.
    """
    return [
        {
            "table": table,
            "name": name,
            "kind": "t",
            "validated": True,
            "deferrable": True,
            "deferred": True,
            "columns": None,
            "target": None,
            "target_columns": None,
            "definition": "TRIGGER DEFERRABLE INITIALLY DEFERRED",
            "delete_action": " ",
        }
        for name, table, _events, _level, _function, deferred in COVERAGE_TRIGGER_SPECS
        if deferred
    ]
