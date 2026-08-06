class DomainError(ValueError):
    """Base exception for deterministic domain-rule failures."""


class InvalidStateTransition(DomainError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"invalid lifecycle transition: {current} -> {target}")
        self.current = current
        self.target = target
