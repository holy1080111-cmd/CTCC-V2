"""Synthetic original Control rows and native-interface negatives; no DB proof."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories.demo_control import DemoControlRepository
from app.trade_qualification import demo_control as c
from tests.unit.test_demo_control import NOW, OWNER, PINS, SCOPE


def source_row(state, action, *, previous=None, command=None):
    command = command or f"{state.revision:064x}"
    value = {
        "version": "ctcc.demo_control_event.v1",
        "command_id": command,
        "action": action,
        "previous_sha256": previous,
        "state": c.document(state),
    }
    raw = c.canonical(value)
    return c.ControlJournalSourceRowV1(
        state.scope,
        state.revision,
        command,
        action,
        previous,
        c.digest(raw),
        c.digest(c.canonical(c.document(state))),
        raw,
        state.updated_at,
    )


def chain():
    """Declare an entire synthetic lifetime, including real cross-epoch rules."""
    state = c.acquire(None, scope=SCOPE, owner_sha256=OWNER, pins=PINS, now=NOW)
    rows = [source_row(state, "acquire")]
    for action, second in (
        ("arm_requested", 1),
        ("renew", 2),
        ("disarm", 3),
        ("rebind", 4),
        ("estop", 5),
        ("release", 6),
    ):
        state = c.advance(
            state,
            owner_sha256=state.owner_sha256,
            owner_epoch=state.owner_epoch,
            expected_revision=state.revision,
            action=action,
            command_id=f"{state.revision + 1:064x}",
            now=NOW + timedelta(seconds=second),
            arm_expires_at=NOW + timedelta(seconds=20)
            if action == "arm_requested"
            else None,
            pins=c.ControlPins("a" * 64, "b" * 64, "c" * 64)
            if action == "rebind"
            else None,
        )
        rows.append(source_row(state, action, previous=rows[-1].event_sha256))
    state = c.acquire(
        state,
        scope=SCOPE,
        owner_sha256="7" * 64,
        pins=PINS,
        now=NOW + timedelta(seconds=7),
    )
    rows.append(source_row(state, "acquire", previous=rows[-1].event_sha256))
    # Safety tightening after this new owner's expiry preserves stop and epoch.
    state = replace(
        state,
        revision=state.revision + 1,
        arm_request_id=None,
        arm_expires_at=None,
        lease_until=NOW + timedelta(seconds=50),
        updated_at=NOW + timedelta(seconds=50),
    )
    rows.append(source_row(state, "disarm", previous=rows[-1].event_sha256))
    return tuple(rows), state


def replay(rows, state, *, head=None):
    return c.replay_control_journal_lineage_v1(
        rows,
        scope=SCOPE,
        target_state=state,
        expected_head_sha256=rows[-1].event_sha256 if head is None else head,
    )


def rehash(rows, mutate):
    """Re-sign all following source hashes; semantic falsifications still fail."""
    changed = []
    for index, row in enumerate(rows):
        value = json.loads(row.event_json)
        mutate(index, value)
        value["previous_sha256"] = changed[-1].event_sha256 if changed else None
        raw = c.canonical(value)
        changed.append(
            replace(
                row,
                previous_sha256=value["previous_sha256"],
                command_id=value["command_id"],
                action=value["action"],
                event_sha256=c.digest(raw),
                state_sha256=c.digest(c.canonical(value["state"])),
                event_json=raw,
            )
        )
    return tuple(changed)


def test_complete_prefix_replays_actions_owner_epochs_and_expired_safety_tightening_deterministically():
    rows, state = chain()
    result = replay(rows, state)
    assert result.receipt_json == replay(rows, state).receipt_json
    assert result.source_rows == rows and result.state == state
    assert result.state.owner_epoch == 2 and result.state.emergency_stop is True
    assert result.execution_authority is False
    value = json.loads(result.receipt_json)
    assert value["first_revision"] == 1 and value["through_revision"] == len(rows)
    assert value["native_source_owned"] is value["execution_authority"] is False
    assert value["event_sha256_chain"] == [row.event_sha256 for row in rows]
    assert rows[0].occurred_at < state.updated_at


def test_unowned_stopped_genesis_then_real_acquisition_preserves_original_stop():
    state = c.stopped_genesis(SCOPE, NOW)
    first = source_row(state, "estop")
    acquired = c.acquire(
        state,
        scope=SCOPE,
        owner_sha256=OWNER,
        pins=PINS,
        now=NOW + timedelta(seconds=1),
    )
    second = source_row(acquired, "acquire", previous=first.event_sha256)
    assert replay((first, second), acquired).state.emergency_stop is True
    assert acquired.owner_epoch == 1


@pytest.mark.parametrize(
    "defect", ["first", "middle", "tail", "suffix", "duplicate", "reverse", "list"]
)
def test_missing_duplicate_reordered_or_nonexact_population_never_becomes_complete(
    defect,
):
    rows, state = chain()
    if defect == "first":
        selected = rows[1:]
    elif defect == "middle":
        selected = rows[:3] + rows[4:]
    elif defect == "tail":
        selected = rows[:-1]
    elif defect == "suffix":
        selected = rows[-2:]
    elif defect == "duplicate":
        selected = rows[:3] + (rows[2],) + rows[4:]
    elif defect == "reverse":
        selected = tuple(reversed(rows))
    else:
        selected = list(rows)
    with pytest.raises(c.DemoControlError):
        replay(selected, state)


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", True),
        ("scope", c.ControlScope("demo", "987654321")),
        ("event_sha256", "f" * 64),
        ("state_sha256", "e" * 64),
        ("previous_sha256", None),
        ("occurred_at", NOW - timedelta(seconds=1)),
        ("event_json", "{}"),
    ],
)
def test_source_columns_are_bound_to_each_exact_event_and_state(field, value):
    rows, state = chain()
    changed = rows[:2] + (replace(rows[2], **{field: value}),) + rows[3:]
    with pytest.raises(c.DemoControlError):
        replay(changed, state)


@pytest.mark.parametrize(
    "defect",
    [
        "owner_epoch_bool",
        "stop_int",
        "unknown_action",
        "lease_extension",
        "pin_change",
        "created_at",
        "duplicate_command",
    ],
)
def test_rehashed_whole_prefix_cannot_hide_type_or_transition_changes(defect):
    rows, state = chain()

    def mutate(index, value):
        if index != 1:
            return
        if defect == "owner_epoch_bool":
            value["state"]["owner_epoch"] = True
        elif defect == "stop_int":
            value["state"]["emergency_stop"] = 0
        elif defect == "unknown_action":
            value["action"] = "clear_stop"
        elif defect == "lease_extension":
            value["state"]["lease_until"] = (NOW + timedelta(seconds=40)).isoformat()
        elif defect == "pin_change":
            value["state"]["config_sha256"] = "d" * 64
        elif defect == "created_at":
            value["state"]["created_at"] = (NOW - timedelta(seconds=1)).isoformat()
        else:
            value["command_id"] = f"{1:064x}"

    changed = rehash(rows, mutate)
    with pytest.raises(c.DemoControlError):
        replay(changed, state)


def test_rehashed_premature_takeover_is_not_a_genuine_new_owner_epoch():
    first_state = c.acquire(None, scope=SCOPE, owner_sha256=OWNER, pins=PINS, now=NOW)
    first = source_row(first_state, "acquire")
    forged = replace(
        first_state,
        revision=2,
        owner_epoch=2,
        owner_sha256="9" * 64,
        updated_at=NOW + timedelta(seconds=1),
        lease_until=NOW + timedelta(seconds=31),
    )
    second = source_row(forged, "acquire", previous=first.event_sha256)
    with pytest.raises(c.DemoControlError, match="owner_conflict"):
        replay((first, second), forged)


def test_target_state_and_head_are_comparisons_not_caller_permission():
    rows, state = chain()
    with pytest.raises(c.DemoControlError, match="target_head"):
        replay(rows, state, head="d" * 64)
    with pytest.raises(c.DemoControlError, match="target_head"):
        replay(rows, replace(state, emergency_stop=False))


@pytest.mark.parametrize("defect", ["row_count", "total_bytes", "event_bytes"])
def test_fixed_resource_bounds_fail_closed_instead_of_returning_a_prefix(
    monkeypatch, defect
):
    rows, state = chain()
    if defect == "row_count":
        rows = (rows[0],) * (c.MAX_CONTROL_LINEAGE_ROWS + 1)
        state = replace(state, revision=len(rows))
    elif defect == "total_bytes":
        monkeypatch.setattr(c, "MAX_CONTROL_LINEAGE_BYTES", 1)
    else:
        rows = (
            replace(rows[0], event_json=" " * (c.MAX_CONTROL_EVENT_BYTES + 1)),
        ) + rows[1:]
    with pytest.raises(c.DemoControlError):
        replay(rows, state)


def test_foreign_scope_equality_callback_is_rejected_without_invocation():
    class Foreign:
        def __eq__(self, _other):
            raise AssertionError("foreign callback must not run")

    rows, state = chain()
    rows = (replace(rows[0], scope=Foreign()),) + rows[1:]
    with pytest.raises(c.DemoControlError, match="row_invalid"):
        replay(rows, state)


@pytest.mark.asyncio
async def test_native_lineage_rejects_non_native_factory_without_clock_or_SQL():
    def forbidden():
        raise AssertionError("caller clock is not lineage provenance")

    repository = DemoControlRepository(object(), clock=forbidden)
    with pytest.raises(c.DemoControlError, match="native_factory_required"):
        await repository.read_lineage(SCOPE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect", ["head_race", "same_transaction", "clock_regression", "factory_changed"]
)
async def test_synthetic_readback_races_deny_without_retry_or_control_side_effects(
    monkeypatch, defect
):
    """Fixed fake I/O facts, real native unconnected factory type; not PG proof."""
    engine = create_async_engine(
        "postgresql+asyncpg://synthetic@localhost/synthetic", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    repository = DemoControlRepository(factory, clock=lambda: NOW)
    rows, state = chain()
    lineage = replay(rows, state)
    calls = []
    durable = tuple(rows)

    async def capture(scope, through_revision):
        assert scope == SCOPE
        calls.append(through_revision)
        current = state
        event = lineage.head_sha256
        if len(calls) == 2 and defect == "head_race":
            current = replace(state, revision=state.revision + 1)
            event = "f" * 64
        at = state.updated_at + timedelta(seconds=1 if len(calls) == 1 else 2)
        if len(calls) == 2 and defect == "clock_regression":
            at = state.updated_at
        transaction = "1" if len(calls) == 1 or defect == "same_transaction" else "2"
        if defect == "factory_changed":
            factory.kw["autoflush"] = True
        return lineage, current, event, (1234, transaction, at), lineage

    monkeypatch.setattr(repository, "_read_native_lineage_transaction", capture)
    try:
        with pytest.raises(
            c.DemoControlError, match="readback_conflict|factory_changed"
        ):
            await repository.read_lineage(SCOPE)
        assert len(calls) == (1 if defect == "factory_changed" else 2)
        assert rows == durable
    finally:
        await engine.dispose()
