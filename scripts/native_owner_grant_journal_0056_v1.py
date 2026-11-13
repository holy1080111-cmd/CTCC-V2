"""One-way owner-host attempt journal for the reviewed DB0056 role grant."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, overload

from app.domain.source_primitives import canonical, sha
from app.trade_evidence.native_private_root_v2 import require_private_evidence_base_v2
from app.trade_qualification.account_native_proof_storage import (
    _root_context,
    _root_identity,
)
from scripts.evidence_json import read_evidence_json
from scripts.native_owner_target_plan_0056_v1 import _require_private_file

_SCHEMA = "ctcc.native_owner_0056.grant_attempt_journal.v1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_TERMINALS = frozenset(
    {"REFUSED", "COMMIT_UNKNOWN", "POSTCOMMIT_INCOMPLETE", "OBSERVED_GRANTED"}
)
_Terminal = Literal[
    "REFUSED", "COMMIT_UNKNOWN", "POSTCOMMIT_INCOMPLETE", "OBSERVED_GRANTED"
]
_Reconciliation = Literal["EXACT_GRANTED_ACL_OBSERVED", "EXACT_PREGRANT_OBSERVED"]
_RECONCILIATIONS = frozenset({"EXACT_GRANTED_ACL_OBSERVED", "EXACT_PREGRANT_OBSERVED"})
_MAX_RECORD = 4096


class OwnerGrantJournal0056Error(ValueError):
    """Fixed denial, including an unusable or ambiguous local journal."""


def _need(condition: bool) -> None:
    if condition is not True:
        raise OwnerGrantJournal0056Error(
            "db0056_owner_grant_attempt_journal_refused"
        ) from None


@dataclass(frozen=True, slots=True)
class _AttemptPins:
    target_sha256: str
    plan_sha256: str
    source_archive_sha256: str

    def __post_init__(self) -> None:
        _need(
            all(
                type(value) is str and _HEX.fullmatch(value) is not None
                for value in (
                    self.target_sha256,
                    self.plan_sha256,
                    self.source_archive_sha256,
                )
            )
        )

    @property
    def names(self) -> tuple[str, str, str]:
        base = f"ctcc-db0056-owner-{self.target_sha256}"
        return (
            f"{base}.started.json",
            f"{base}.terminal.json",
            f"{base}.reconciliation.json",
        )

    @property
    def payload(self) -> bytes:
        return canonical(
            {
                "schema": _SCHEMA,
                "status": "STARTED",
                "target_sha256": self.target_sha256,
                "plan_sha256": self.plan_sha256,
                "source_archive_sha256": self.source_archive_sha256,
            }
        )


def _result_payload(started_sha256: str, outcome: _Terminal | _Reconciliation) -> bytes:
    return canonical(
        {
            "schema": _SCHEMA,
            "status": outcome,
            "started_sha256": started_sha256,
        }
    )


def _read_record(directory, name: str) -> bytes:
    _require_private_file(directory.path / name)
    return directory.read(name, _MAX_RECORD)


def _inventory(directory, pins: _AttemptPins) -> set[str]:
    names = set(directory.names())
    _need(
        not any(
            name.startswith(f".{final}.") and name.endswith(".partial")
            for name in names
            for final in pins.names
        )
    )
    return names.intersection(pins.names)


def _read_result(directory, names, path, started_sha256, allowed):
    if path not in names:
        return None, None
    raw = _read_record(directory, path)
    document = read_evidence_json(raw)
    _need(
        type(document) is dict
        and type(document.get("status")) is str
        and document["status"] in allowed
        and raw == _result_payload(started_sha256, document["status"])
    )
    return document["status"], sha(raw)


def _read_journal(directory, pins, names):
    started, terminal, reconcile = pins.names
    _need(started in names and _read_record(directory, started) == pins.payload)
    started_sha256 = sha(pins.payload)
    result, terminal_sha256 = _read_result(
        directory, names, terminal, started_sha256, _TERMINALS
    )
    reconciliation, reconciliation_sha256 = _read_result(
        directory, names, reconcile, started_sha256, _RECONCILIATIONS
    )
    return (
        result or "STARTED",
        started_sha256,
        terminal_sha256,
        reconciliation,
        reconciliation_sha256,
    )


@dataclass(frozen=True, slots=True)
class OwnerGrantAttemptJournal0056V1:
    """Exact readback for one attempt. A pre-existing marker permits reads only."""

    created: bool
    status: Literal["STARTED", _Terminal]
    started_sha256: str
    terminal_sha256: str | None
    reconciliation_status: _Reconciliation | None
    reconciliation_sha256: str | None
    _directory: Path = field(repr=False)
    _identity: tuple[int, int] = field(repr=False)
    _pins: _AttemptPins = field(repr=False)

    def record_terminal(self, outcome: _Terminal) -> str:
        """Publish one terminal result. The started marker remains in place."""
        _need(
            self.created is True
            and self.status == "STARTED"
            and type(outcome) is str
            and outcome in _TERMINALS
        )
        return self._record_result(outcome, reconciliation=False)

    def record_reconciliation(self, outcome: _Reconciliation) -> str:
        """Retain an observed database outcome separately from the attempt."""
        _need(
            self.created is False
            and type(outcome) is str
            and outcome in _RECONCILIATIONS
        )
        return self._record_result(outcome, reconciliation=True)

    def _record_result(
        self, outcome: _Terminal | _Reconciliation, *, reconciliation: bool
    ) -> str:
        try:
            root = require_private_evidence_base_v2(str(self._directory))
            with _root_context(root) as directory:
                _need(_root_identity(root) == self._identity)
                names = _inventory(directory, self._pins)
                current = _read_journal(directory, self._pins, names)
                _need(current[1] == self.started_sha256)
                _need(
                    (outcome, current[3])
                    != ("OBSERVED_GRANTED", "EXACT_PREGRANT_OBSERVED")
                    and (current[0], outcome)
                    != ("OBSERVED_GRANTED", "EXACT_PREGRANT_OBSERVED")
                )
                if not reconciliation:
                    _need(current[:3] == ("STARTED", self.started_sha256, None))
                elif current[3] is not None:
                    _need(current[3] == outcome)
                    return current[4]
                index = 2 if reconciliation else 1
                raw = _result_payload(self.started_sha256, outcome)
                directory.publish(self._pins.names[index], raw)
                seal = sha(raw)
                reopened = _read_journal(
                    directory, self._pins, _inventory(directory, self._pins)
                )
                _need(
                    _root_identity(root) == self._identity
                    and reopened
                    == (
                        (current[0], self.started_sha256, current[2], outcome, seal)
                        if reconciliation
                        else (outcome, self.started_sha256, seal, *current[3:])
                    )
                )
            return seal
        except OwnerGrantJournal0056Error:
            raise
        except Exception:  # noqa: BLE001 -- do not expose local journal details
            _need(False)


@overload
def _journal(
    directory: Path, pins: _AttemptPins, *, allow_start: Literal[True]
) -> OwnerGrantAttemptJournal0056V1: ...


@overload
def _journal(
    directory: Path, pins: _AttemptPins, *, allow_start: Literal[False]
) -> OwnerGrantAttemptJournal0056V1 | None: ...


def _journal(
    directory: Path, pins: _AttemptPins, *, allow_start: bool
) -> OwnerGrantAttemptJournal0056V1 | None:
    try:
        _need(type(directory) is type(Path()))
        root = require_private_evidence_base_v2(str(directory))
        identity = _root_identity(root)
        with _root_context(root) as held:
            names = _inventory(held, pins)
            created = allow_start and not names
            if created:
                held.publish(pins.names[0], pins.payload)
                names = _inventory(held, pins)
            if not names:
                _need(not created and _root_identity(root) == identity)
                return None
            status, start, terminal, reconciliation, reconciliation_sha = _read_journal(
                held, pins, names
            )
            _need(
                _root_identity(root) == identity
                and (not created or status == "STARTED")
            )
        return OwnerGrantAttemptJournal0056V1(
            created,
            status,
            start,
            terminal,
            reconciliation,
            reconciliation_sha,
            root,
            identity,
            pins,
        )
    except OwnerGrantJournal0056Error:
        raise
    except Exception:  # noqa: BLE001 -- do not expose local journal details
        _need(False)


def load_or_begin(
    directory: Path,
    *,
    target_sha256: str,
    plan_sha256: str,
    source_archive_sha256: str,
) -> OwnerGrantAttemptJournal0056V1:
    """Publish a no-clobber start and verify its exact on-filesystem bytes."""
    return _journal(
        directory,
        _AttemptPins(target_sha256, plan_sha256, source_archive_sha256),
        allow_start=True,
    )


def load_existing(
    directory: Path,
    *,
    target_sha256: str,
    plan_sha256: str,
    source_archive_sha256: str,
) -> OwnerGrantAttemptJournal0056V1 | None:
    """Inspect existing exact files without creating or changing a record."""
    return _journal(
        directory,
        _AttemptPins(target_sha256, plan_sha256, source_archive_sha256),
        allow_start=False,
    )
