from app.domain.enums import LifecycleState
from app.domain.errors import InvalidStateTransition


_ALLOWED_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.CANDIDATE: frozenset({
        LifecycleState.RISK_APPROVED,
        LifecycleState.REJECTED,
        LifecycleState.CANCELLED,
        LifecycleState.FAILED,
    }),
    LifecycleState.RISK_APPROVED: frozenset({
        LifecycleState.SUBMITTED,
        LifecycleState.CANCELLED,
        LifecycleState.FAILED,
    }),
    LifecycleState.SUBMITTED: frozenset({
        LifecycleState.ACCEPTED,
        LifecycleState.PARTIAL_FILLED,
        LifecycleState.FILLED,
        LifecycleState.REJECTED,
        LifecycleState.CANCELLED,
        LifecycleState.FAILED,
    }),
    LifecycleState.ACCEPTED: frozenset({
        LifecycleState.PARTIAL_FILLED,
        LifecycleState.FILLED,
        LifecycleState.CANCELLED,
        LifecycleState.FAILED,
    }),
    LifecycleState.PARTIAL_FILLED: frozenset({
        LifecycleState.PARTIAL_FILLED,
        LifecycleState.FILLED,
        LifecycleState.CLOSING,
        LifecycleState.FAILED,
    }),
    LifecycleState.FILLED: frozenset({
        LifecycleState.PROTECTED,
        LifecycleState.CLOSING,
        LifecycleState.FAILED,
    }),
    LifecycleState.PROTECTED: frozenset({
        LifecycleState.CLOSING,
        LifecycleState.FAILED,
    }),
    LifecycleState.CLOSING: frozenset({
        LifecycleState.CLOSED,
        LifecycleState.FAILED,
    }),
    LifecycleState.CLOSED: frozenset({LifecycleState.ARCHIVED}),
    LifecycleState.ARCHIVED: frozenset(),
    LifecycleState.REJECTED: frozenset({LifecycleState.ARCHIVED}),
    LifecycleState.CANCELLED: frozenset({LifecycleState.ARCHIVED}),
    LifecycleState.FAILED: frozenset({LifecycleState.CLOSING, LifecycleState.ARCHIVED}),
}


def allowed_transitions(current: LifecycleState) -> frozenset[LifecycleState]:
    return _ALLOWED_TRANSITIONS[current]


def ensure_transition(current: LifecycleState, target: LifecycleState) -> None:
    if target not in allowed_transitions(current):
        raise InvalidStateTransition(current.value, target.value)


def transition_map() -> dict[str, list[str]]:
    return {
        state.value: sorted(target.value for target in targets)
        for state, targets in _ALLOWED_TRANSITIONS.items()
    }
