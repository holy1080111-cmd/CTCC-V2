"""Require an interactive owner acknowledgement of one exact DB0056 grant plan."""

from __future__ import annotations

import os
import re
import sys

_SHA256 = re.compile(r"[a-f0-9]{64}", re.ASCII)
_COMMAND = "CTCC_DB0056_GRANT_42"
_REFUSAL = "native_owner_0056_grant_confirmation_refused"


class OwnerGrantConfirmationRefusal(ValueError):
    """The local process did not receive the exact interactive acknowledgement."""


def confirm_owner_grant_0056_v1(plan_file_sha256: str) -> None:
    """Check operator intent at the terminal, without asserting reviewer identity."""
    if type(plan_file_sha256) is not str or not _SHA256.fullmatch(plan_file_sha256):
        raise OwnerGrantConfirmationRefusal(_REFUSAL)

    try:
        interactive = os.isatty(sys.stdin.fileno()) and os.isatty(sys.stdout.fileno())
    except (AttributeError, OSError, ValueError):
        raise OwnerGrantConfirmationRefusal(_REFUSAL) from None
    if not interactive:
        raise OwnerGrantConfirmationRefusal(_REFUSAL)

    expected = f"{_COMMAND} {plan_file_sha256}"
    try:
        response = input(f"To authorize this exact DB0056 grant, type {expected}\n> ")
    except (EOFError, KeyboardInterrupt, OSError, ValueError):
        raise OwnerGrantConfirmationRefusal(_REFUSAL) from None
    if response != expected:
        raise OwnerGrantConfirmationRefusal(_REFUSAL)
