"""Synthetic pure values only: no native/operator/DB/Arm/account acceptance."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.trade_qualification import demo_control as legacy
from app.trade_qualification import demo_control_clear_v2 as clear

AT = datetime(2026, 11, 2, tzinfo=UTC)
SCOPE = legacy.ControlScope("demo", "1234")
PINS = legacy.ControlPins("1" * 64, "2" * 64, "3" * 64)
OWNER = "4" * 64


def _v1_row(state, action, command, previous):
    raw = legacy.canonical(
        {
            "version": "ctcc.demo_control_event.v1",
            "command_id": command,
            "action": action,
            "previous_sha256": previous,
            "state": legacy.document(state),
        }
    )
    return legacy.ControlJournalSourceRowV1(
        SCOPE,
        state.revision,
        command,
        action,
        previous,
        legacy.digest(raw),
        legacy.digest(legacy.canonical(legacy.document(state))),
        raw,
        state.updated_at,
    )


def _stopped_prefix():
    first = legacy.acquire(None, scope=SCOPE, owner_sha256=OWNER, pins=PINS, now=AT)
    row1 = _v1_row(first, "acquire", "5" * 64, None)
    stopped = legacy.advance(
        first,
        owner_sha256=OWNER,
        owner_epoch=first.owner_epoch,
        expected_revision=first.revision,
        action="estop",
        command_id="6" * 64,
        now=AT + timedelta(seconds=1),
    )
    return stopped, (row1, _v1_row(stopped, "estop", "6" * 64, row1.event_sha256))


def _ack(previous, head, *, now=AT + timedelta(seconds=2)):
    command = "7" * 64
    expiry = now + timedelta(seconds=10)
    target = replace(
        previous, revision=previous.revision + 1, emergency_stop=False, updated_at=now
    )
    operator = legacy.canonical(
        {
            "schema_version": clear.OPERATOR_SCHEMA_V2,
            "purpose": "clear_stop",
            "scope": ["demo", "1234", "USDT"],
            "command_id": command,
            "stopped_control_revision": previous.revision,
            "stopped_event_sha256": head,
            "source_lineage_sha256": "8" * 64,
            "original_expires_at": expiry.isoformat(),
            "confirmation": clear.CLEAR_CONFIRMATION_V2,
        }
    )
    proofs = tuple(
        (name, legacy.digest(operator) if name == "operator_ack" else "9" * 64)
        for name in clear.CLEAR_PROOF_NAMES_V2
    )
    return clear.ClearStopAckV2(
        SCOPE,
        "USDT",
        command,
        previous.revision,
        head,
        legacy.digest(legacy.canonical(legacy.document(previous))),
        legacy.digest(legacy.canonical(legacy.document(target))),
        "8" * 64,
        0,
        0,
        None,
        OWNER,
        previous.owner_epoch,
        previous.lease_until,
        now,
        expiry,
        100,
        200,
        "300",
        proofs,
        operator,
    )


def _advance(previous, head, ack):
    return clear.advance_clear_stop_v2(
        previous,
        owner_sha256=OWNER,
        owner_epoch=previous.owner_epoch,
        expected_revision=previous.revision,
        previous_event_sha256=head,
        command_id=ack.command_id,
        now=ack.owner_sampled_at,
        fresh_ack=ack,
    )


def _clear_row(state, previous, ack):
    raw = legacy.canonical(
        clear.clear_event_document_v2(
            state,
            command_id=ack.command_id,
            previous_sha256=previous,
            ack_sha256=ack.ack_sha256,
        )
    )
    return legacy.ControlJournalSourceRowV1(
        SCOPE,
        state.revision,
        ack.command_id,
        "clear_stop",
        previous,
        legacy.digest(raw),
        legacy.digest(legacy.canonical(legacy.document(state))),
        raw,
        state.updated_at,
    )


def test_pure_clear_preserves_owner_pins_original_lease_and_remains_disarmed():
    previous, rows = _stopped_prefix()
    ack = _ack(previous, rows[-1].event_sha256)
    raw = legacy.canonical(clear.clear_ack_document_v2(ack))
    assert clear.decode_clear_ack_v2(raw) == ack
    state = _advance(previous, rows[-1].event_sha256, ack)
    assert state.emergency_stop is False
    assert state.arm_request_id is state.arm_expires_at is None
    assert state.lease_until == previous.lease_until
    assert state.owner_sha256 == previous.owner_sha256
    assert state.owner_epoch == previous.owner_epoch
    assert state.pins is previous.pins
    assert state.execution_authority is ack.execution_authority is False


def test_complete_v2_replay_uses_original_v1_actions_and_all_exact_ack_bytes():
    stopped, rows = _stopped_prefix()
    ack = _ack(stopped, rows[-1].event_sha256)
    state = _advance(stopped, rows[-1].event_sha256, ack)
    clear_row = _clear_row(state, rows[-1].event_sha256, ack)
    armed = legacy.advance(
        state,
        owner_sha256=OWNER,
        owner_epoch=state.owner_epoch,
        expected_revision=state.revision,
        action="arm_requested",
        command_id="a" * 64,
        now=AT + timedelta(seconds=3),
        arm_expires_at=AT + timedelta(seconds=8),
    )
    arm_row = _v1_row(armed, "arm_requested", "a" * 64, clear_row.event_sha256)
    full = (*rows, clear_row, arm_row)
    lineage = clear.replay_control_journal_lineage_v2(
        full,
        scope=SCOPE,
        target_state=armed,
        expected_head_sha256=arm_row.event_sha256,
        clear_ack_rows=(ack,),
    )
    assert lineage.source_rows == full
    assert lineage.clear_ack_rows == (ack,)
    assert lineage.execution_authority is False
    # Historical v1 is preserved and cannot silently admit a new clear action.
    with pytest.raises(legacy.DemoControlError):
        legacy.replay_control_journal_lineage_v1(
            full,
            scope=SCOPE,
            target_state=armed,
            expected_head_sha256=arm_row.event_sha256,
        )


@pytest.mark.parametrize("case", ["missing", "duplicate", "orphan"])
def test_v2_prefix_rejects_missing_duplicate_or_orphan_ack(case):
    stopped, rows = _stopped_prefix()
    ack = _ack(stopped, rows[-1].event_sha256)
    state = _advance(stopped, rows[-1].event_sha256, ack)
    row = _clear_row(state, rows[-1].event_sha256, ack)
    supplied = (
        () if case == "missing" else (ack, ack) if case == "duplicate" else (ack,)
    )
    targets = rows if case == "orphan" else (*rows, row)
    target = stopped if case == "orphan" else state
    with pytest.raises(legacy.DemoControlError):
        clear.replay_control_journal_lineage_v2(
            targets,
            scope=SCOPE,
            target_state=target,
            expected_head_sha256=targets[-1].event_sha256,
            clear_ack_rows=supplied,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("stopped_control_revision", True),
        ("account_revision", True),
        ("owner_epoch", 0),
        ("db_role_oid", True),
        ("db_transaction_id", "01"),
        ("settlement_currency", "BTC"),
        ("claims_sha256", "1" * 64),
        ("proofs", ()),
        ("operator_ack_json", "{}"),
    ],
)
def test_exact_ack_types_and_closed_operator_binding_reject_mutants(field, value):
    stopped, rows = _stopped_prefix()
    ack = _ack(stopped, rows[-1].event_sha256)
    with pytest.raises(legacy.DemoControlError):
        replace(ack, **{field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("stopped_event_sha256", "a" * 64),
        ("clear_state_sha256", "a" * 64),
        ("owner_sha256", "a" * 64),
        ("stopped_state_sha256", "a" * 64),
    ],
)
def test_full_held_transition_rechecks_ack_before_return(field, value):
    stopped, rows = _stopped_prefix()
    ack = _ack(stopped, rows[-1].event_sha256)
    # These are deliberately corrupted synthetic values; source objects cannot
    # be authenticated by caller construction or this pure mutation test.
    object.__setattr__(ack, field, value)
    with pytest.raises(legacy.DemoControlError):
        _advance(stopped, rows[-1].event_sha256, ack)


def test_expired_original_lease_and_unowned_genesis_cannot_clear():
    stopped, rows = _stopped_prefix()
    ack = _ack(stopped, rows[-1].event_sha256)
    with pytest.raises(legacy.DemoControlError):
        clear.advance_clear_stop_v2(
            stopped,
            owner_sha256=OWNER,
            owner_epoch=stopped.owner_epoch,
            expected_revision=stopped.revision,
            previous_event_sha256=rows[-1].event_sha256,
            command_id=ack.command_id,
            now=stopped.lease_until,
            fresh_ack=ack,
        )
    genesis = legacy.stopped_genesis(SCOPE, AT)
    with pytest.raises(legacy.DemoControlError):
        clear.advance_clear_stop_v2(
            genesis,
            owner_sha256=OWNER,
            owner_epoch=0,
            expected_revision=1,
            previous_event_sha256=rows[-1].event_sha256,
            command_id=ack.command_id,
            now=AT,
            fresh_ack=ack,
        )


@pytest.mark.parametrize("kind", ["extra", "duplicate", "wrong_purpose", "nonfinite"])
def test_closed_ack_json_rejects_extra_duplicate_wrong_purpose_and_nonfinite(kind):
    stopped, rows = _stopped_prefix()
    ack = _ack(stopped, rows[-1].event_sha256)
    value = clear.clear_ack_document_v2(ack)
    raw = legacy.canonical(value)
    if kind == "extra":
        value["unapproved"] = 1
        raw = legacy.canonical(value)
    elif kind == "duplicate":
        raw = '{"purpose":"clear_stop",' + raw[1:]
    elif kind == "wrong_purpose":
        value["purpose"] = "policy_counter_initialize"
        raw = legacy.canonical(value)
    else:
        value["db_role_oid"] = float("nan")
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    with pytest.raises(legacy.DemoControlError):
        clear.decode_clear_ack_v2(raw)
