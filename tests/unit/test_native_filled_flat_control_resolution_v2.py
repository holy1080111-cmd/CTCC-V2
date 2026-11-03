"""Negative Control DATA contracts; no Source, DB or Root capability is minted."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.trade_qualification import demo_control as control
from app.trade_qualification import native_controlled_account_source_v6 as source


def _original():
    now = datetime(2026, 11, 3, tzinfo=UTC)
    scope = control.ControlScope("demo", "550099")
    pins = control.ControlPins("a" * 64, "b" * 64, "c" * 64)
    return control.acquire(None, scope=scope, owner_sha256="d" * 64, pins=pins, now=now)


def _kind(state, clock, *, owner_sha256="d" * 64, owner_epoch=1):
    return source._filled_cleanup_control_kind_v12(
        state, owner_sha256, owner_epoch, clock
    )


def _metadata():
    return (
        "runtime_writer",
        "runtime_writer",
        "none",
        1001,
        False,
        False,
        False,
        1234,
        "9001",
        datetime(2026, 11, 3, tzinfo=UTC),
    )


def test_exact_own_unexpired_and_equal_expiry_are_distinct_data_only():
    state = _original()
    before = tuple(source._FILLED_FLAT_CLEANUP_RECORDS_V6)
    assert (
        _kind(state, state.lease_until - timedelta(microseconds=1))
        == "release_required"
    )
    assert _kind(state, state.lease_until) == "own_expired"
    assert _kind(state, state.lease_until + timedelta(seconds=20)) == "own_expired"
    assert tuple(source._FILLED_FLAT_CLEANUP_RECORDS_V6) == before


def test_expired_arm_fields_are_not_cleared_or_relabelled_as_a_disarm():
    original = _original()
    armed = control.advance(
        original,
        owner_sha256=original.owner_sha256,
        owner_epoch=original.owner_epoch,
        expected_revision=original.revision,
        action="arm_requested",
        command_id="1" * 64,
        now=original.updated_at + timedelta(seconds=1),
        arm_expires_at=original.updated_at + timedelta(seconds=10),
    )
    raw = control.canonical(control.document(armed))
    assert armed.arm_request_id is not None
    assert _kind(armed, armed.lease_until) == "own_expired"
    assert control.canonical(control.document(armed)) == raw
    assert armed.arm_request_id is not None


def test_legitimate_newer_armed_owner_is_only_observed_and_never_mutated():
    original = _original()
    newer = control.acquire(
        original,
        scope=original.scope,
        owner_sha256="e" * 64,
        pins=original.pins,
        now=original.lease_until,
    )
    armed = control.advance(
        newer,
        owner_sha256=newer.owner_sha256,
        owner_epoch=newer.owner_epoch,
        expected_revision=newer.revision,
        action="arm_requested",
        command_id="2" * 64,
        now=newer.updated_at + timedelta(seconds=1),
        arm_expires_at=newer.updated_at + timedelta(seconds=10),
    )
    raw = control.canonical(control.document(armed))
    assert _kind(armed, armed.updated_at) == "owner_superseded"
    assert control.canonical(control.document(armed)) == raw
    assert armed.arm_intent_current(armed.updated_at) is True


@pytest.mark.parametrize(
    "fault",
    (
        "different_same_epoch",
        "same_hash_new_epoch",
        "hash_bool",
        "hash_upper",
        "epoch_bool",
        "epoch_zero",
        "naive_clock",
        "regressed_db_clock",
        "missing_state",
    ),
)
def test_ownership_ambiguity_never_becomes_expired_or_superseded(fault):
    state = _original()
    clock, sha, epoch = state.updated_at, state.owner_sha256, state.owner_epoch
    if fault == "different_same_epoch":
        state = replace(state, owner_sha256="e" * 64)
    elif fault == "same_hash_new_epoch":
        state = replace(state, revision=2, owner_epoch=2)
    elif fault == "hash_bool":
        sha = True
    elif fault == "hash_upper":
        sha = "D" * 64
    elif fault == "epoch_bool":
        epoch = True
    elif fault == "epoch_zero":
        epoch = 0
    elif fault == "naive_clock":
        clock = clock.replace(tzinfo=None)
    elif fault == "regressed_db_clock":
        clock -= timedelta(microseconds=1)
    else:
        state = None
    with pytest.raises(ValueError):
        source._filled_cleanup_control_kind_v12(state, sha, epoch, clock)


def test_existing_control_cas_disarm_then_release_shortens_without_resetting_stop():
    # The actual unchanged domain core used by owner._perform/repository.change.
    original = replace(_original(), emergency_stop=True)
    disarmed = control.advance(
        original,
        owner_sha256=original.owner_sha256,
        owner_epoch=original.owner_epoch,
        expected_revision=original.revision,
        action="disarm",
        command_id="3" * 64,
        now=original.updated_at + timedelta(seconds=1),
    )
    released = control.advance(
        disarmed,
        owner_sha256=disarmed.owner_sha256,
        owner_epoch=disarmed.owner_epoch,
        expected_revision=disarmed.revision,
        action="release",
        command_id="4" * 64,
        now=disarmed.updated_at + timedelta(seconds=1),
    )
    assert released.revision == original.revision + 2
    assert disarmed.lease_until == original.lease_until
    assert released.lease_until == released.updated_at < original.lease_until
    assert released.arm_request_id is None and released.arm_expires_at is None
    assert released.emergency_stop is True and released.pins == original.pins
    assert _kind(released, released.updated_at) == "own_expired"


@pytest.mark.parametrize("fault", ("owner", "epoch", "revision", "expired"))
def test_existing_release_cas_rejects_replacement_or_expiry_before_mutation(fault):
    state = _original()
    sha, epoch, revision = state.owner_sha256, state.owner_epoch, state.revision
    now = state.updated_at + timedelta(seconds=1)
    if fault == "owner":
        sha = "e" * 64
    elif fault == "epoch":
        epoch += 1
    elif fault == "revision":
        revision += 1
    else:
        now = state.lease_until
    raw = control.canonical(control.document(state))
    with pytest.raises(control.DemoControlError):
        control.advance(
            state,
            owner_sha256=sha,
            owner_epoch=epoch,
            expected_revision=revision,
            action="release",
            command_id="5" * 64,
            now=now,
        )
    assert control.canonical(control.document(state)) == raw


@pytest.mark.parametrize(
    "index,value",
    (
        (0, True),
        (1, "other_writer"),
        (2, "other_role"),
        (3, True),
        (3, 0),
        (4, True),
        (5, True),
        (6, True),
        (7, True),
        (7, 0),
        (8, "0"),
        (8, "1e3"),
        (8, "9" * 21),
        (8, "９"),
        (9, datetime(2026, 11, 3)),  # noqa: DTZ001 -- Verify rejection of a naive timestamp.
    ),
)
def test_retained_db_metadata_replay_rejects_type_role_clock_and_xid_faults(
    index, value
):
    valid = _metadata()
    assert source._filled_cleanup_control_metadata_v12(valid) is valid
    changed = list(valid)
    changed[index] = value
    with pytest.raises(source.ControlledAccountSourceError, match="metadata_changed"):
        source._filled_cleanup_control_metadata_v12(tuple(changed))


def test_foreign_metadata_equality_callback_is_rejected_before_invocation():
    calls = []

    class Foreign:
        def __eq__(self, _other):
            calls.append("foreign")
            raise AssertionError("unexpected callback")

    changed = list(_metadata())
    changed[1] = Foreign()
    with pytest.raises(source.ControlledAccountSourceError):
        source._filled_cleanup_control_metadata_v12(tuple(changed))
    assert calls == []


@pytest.mark.parametrize("resolution", (None, (), (True,) * 6, ("own_expired",) * 6))
def test_labels_flags_and_incomplete_tuples_never_prove_control_resolution(resolution):
    before = tuple(source._FILLED_FLAT_CLEANUP_RETIRED_V6)
    with pytest.raises(source.ControlledAccountSourceError):
        source._filled_cleanup_control_resolution_v12(resolution, ())
    assert tuple(source._FILLED_FLAT_CLEANUP_RETIRED_V6) == before
