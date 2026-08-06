import pytest

from app.domain.enums import LifecycleState
from app.domain.errors import InvalidStateTransition
from app.domain.state_machine import ensure_transition


def test_happy_path_transitions() -> None:
    path = [
        LifecycleState.CANDIDATE,
        LifecycleState.RISK_APPROVED,
        LifecycleState.SUBMITTED,
        LifecycleState.ACCEPTED,
        LifecycleState.PARTIAL_FILLED,
        LifecycleState.FILLED,
        LifecycleState.PROTECTED,
        LifecycleState.CLOSING,
        LifecycleState.CLOSED,
        LifecycleState.ARCHIVED,
    ]
    for current, target in zip(path, path[1:]):
        ensure_transition(current, target)


def test_cannot_skip_risk_approval() -> None:
    with pytest.raises(InvalidStateTransition):
        ensure_transition(LifecycleState.CANDIDATE, LifecycleState.SUBMITTED)


def test_cannot_reopen_archived_trade() -> None:
    with pytest.raises(InvalidStateTransition):
        ensure_transition(LifecycleState.ARCHIVED, LifecycleState.CANDIDATE)
