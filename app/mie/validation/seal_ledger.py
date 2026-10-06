"""Local no-clobber publication and single-attempt Gate 3 evaluation ledger.

The ledger records computational evidence only. Its local clock and file are
not an independent timestamp authority or proof that an evaluator did not read
the holdout through some other path. No record grants predictive or trading
authority. An interrupted evaluation remains consumed until a new seal is made.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.mie.validation.artifact import (
    SHA256_RE,
    ArtifactVerificationError,
    freeze_prospective_holdout_receipt,
    freeze_prospective_preregistration,
    verify_prospective_holdout_receipt,
    verify_prospective_preregistration,
)
from app.mie.validation.contracts import Gate3Claim, require_utc
from app.mie.validation.prospective import (
    Gate3ProspectiveHoldoutReceipt,
    Gate3ProspectivePreregistration,
)


class SealLedgerError(ValueError):
    """The offline seal or evaluation cannot be trusted or published."""


@dataclass(frozen=True, slots=True)
class PublishedSeal:
    sha256: str
    published_at: datetime
    current_claim: Gate3Claim = field(default=Gate3Claim.COMPUTATIONAL, init=False)
    predictive_oos_eligible: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class PublishedReceipt:
    sha256: str
    seal_sha256: str
    published_at: datetime
    current_claim: Gate3Claim = field(default=Gate3Claim.COMPUTATIONAL, init=False)
    predictive_oos_eligible: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class FormalEvaluationReservation:
    reservation_id: str
    seal_sha256: str
    receipt_sha256: str
    started_at: datetime
    current_claim: Gate3Claim = field(default=Gate3Claim.COMPUTATIONAL, init=False)
    predictive_oos_eligible: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


_SEALS_SQL = """CREATE TABLE seals (
    seal_sha256 TEXT PRIMARY KEY,
    preregistration_id TEXT NOT NULL UNIQUE,
    holdout_id TEXT NOT NULL UNIQUE,
    window_key TEXT NOT NULL UNIQUE,
    payload BLOB NOT NULL,
    published_at TEXT NOT NULL
)"""
_RECEIPTS_SQL = """CREATE TABLE receipts (
    seal_sha256 TEXT PRIMARY KEY REFERENCES seals(seal_sha256),
    receipt_sha256 TEXT NOT NULL UNIQUE,
    payload BLOB NOT NULL,
    published_at TEXT NOT NULL
)"""
_EVALUATIONS_SQL = """CREATE TABLE formal_evaluations (
    seal_sha256 TEXT PRIMARY KEY REFERENCES seals(seal_sha256),
    receipt_sha256 TEXT NOT NULL REFERENCES receipts(receipt_sha256),
    reservation_id TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL
)"""
_SCHEMA = {
    "seals": _SEALS_SQL,
    "receipts": _RECEIPTS_SQL,
    "formal_evaluations": _EVALUATIONS_SQL,
}
_RESERVATION_ID = re.compile(r"[0-9a-f]{32}\Z")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _sha256(value: str) -> str:
    if type(value) is not str or not SHA256_RE.fullmatch(value):
        raise SealLedgerError("expected SHA256 pin is invalid")
    return value


def _parse_time(value: str) -> datetime:
    try:
        return require_utc(datetime.fromisoformat(value), "ledger timestamp")
    except (TypeError, ValueError) as exc:
        raise SealLedgerError("ledger timestamp is invalid") from exc


def _window_key(seal: Gate3ProspectivePreregistration) -> str:
    holdout = seal.prospective_holdout
    coordinates = {
        "source": holdout.source,
        "instrument_ids": holdout.instrument_ids,
        "start_at": holdout.start_at.isoformat(),
        "end_at": holdout.end_at.isoformat(),
    }
    return hashlib.sha256(
        json.dumps(coordinates, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Gate3SealLedger:
    """Dedicated local SQLite store; every write is unique and read back anew.

    The database is an offline control, not a remotely attested access service.
    Callers must keep its whole file and independent hash pins under controlled
    custody. A reservation is the first evaluator access through this interface.
    """

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise SealLedgerError("ledger path must be an absolute Path")
        if not path.parent.is_dir() or path.is_symlink():
            raise SealLedgerError("ledger parent missing or path is a symlink")
        if path.exists() and not path.is_file():
            raise SealLedgerError("ledger path is not a regular file")
        self.path = path
        self._initialized = False
        try:
            with closing(self._connect(readonly=False)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                objects = dict(
                    db.execute(
                        "SELECT name, sql FROM sqlite_master "
                        "WHERE name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                )
                if version == 0 and not objects:
                    for sql in _SCHEMA.values():
                        db.execute(sql)
                    db.execute("PRAGMA user_version=1")
                elif version != 1 or objects != _SCHEMA:
                    raise SealLedgerError("ledger schema drift or unknown objects")
                if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise SealLedgerError("ledger integrity check failed")
                if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise SealLedgerError("ledger foreign key check failed")
        except sqlite3.Error as exc:
            raise SealLedgerError("ledger initialization failed") from exc
        self._initialized = True

    @staticmethod
    def _schema_matches(db: sqlite3.Connection) -> bool:
        return (
            db.execute("PRAGMA user_version").fetchone() == (1,)
            and dict(
                db.execute(
                    "SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                ).fetchall()
            )
            == _SCHEMA
        )

    def _connect(self, *, readonly: bool) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise SealLedgerError("ledger path became a symlink")
        if readonly:
            db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5)
        else:
            db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA foreign_keys=ON")
            if not readonly:
                db.execute("PRAGMA journal_mode=DELETE")
                db.execute("PRAGMA synchronous=FULL")
            if not readonly and db.execute("PRAGMA synchronous").fetchone() != (2,):
                raise SealLedgerError("ledger durable sync is unavailable")
            if self._initialized and not self._schema_matches(db):
                raise SealLedgerError("ledger schema drift or unknown objects")
        except (sqlite3.Error, SealLedgerError):
            db.close()
            raise
        return db

    def _seal_row(
        self, db: sqlite3.Connection, expected_sha256: str
    ) -> tuple[Gate3ProspectivePreregistration, datetime]:
        row = db.execute(
            "SELECT payload, published_at, preregistration_id, holdout_id, window_key "
            "FROM seals WHERE seal_sha256=?",
            (_sha256(expected_sha256),),
        ).fetchone()
        if row is None:
            raise SealLedgerError("prospective seal is not published")
        try:
            seal = verify_prospective_preregistration(
                bytes(row[0]), expected_sha256=expected_sha256
            )
        except ArtifactVerificationError as exc:
            raise SealLedgerError("published seal failed readback") from exc
        if (
            row[2] != seal.preregistration_id
            or row[3] != seal.prospective_holdout.holdout_id
            or row[4] != _window_key(seal)
        ):
            raise SealLedgerError("published seal identity mismatch")
        published_at = _parse_time(row[1])
        if not seal.created_at <= published_at < seal.prospective_holdout.start_at:
            raise SealLedgerError("published seal missed its future window")
        return seal, published_at

    def publish_seal(
        self, preregistration: Gate3ProspectivePreregistration
    ) -> PublishedSeal:
        """Publish once before the future window; a duplicate never overwrites."""

        if type(preregistration) is not Gate3ProspectivePreregistration:
            raise SealLedgerError("exact prospective preregistration required")
        try:
            frozen = freeze_prospective_preregistration(preregistration)
        except ArtifactVerificationError as exc:
            raise SealLedgerError("prospective seal validation failed") from exc
        now = _utc_now()
        if (
            not preregistration.created_at
            <= now
            < preregistration.prospective_holdout.start_at
        ):
            raise SealLedgerError(
                "prospective seal cannot publish outside future window"
            )
        try:
            with closing(self._connect(readonly=False)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO seals VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        frozen.sha256,
                        frozen.contract.preregistration_id,
                        frozen.contract.prospective_holdout.holdout_id,
                        _window_key(frozen.contract),
                        frozen.payload,
                        now.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise SealLedgerError("prospective seal already published") from exc
        except sqlite3.Error as exc:
            raise SealLedgerError("prospective seal publication uncertain") from exc
        seal, published_at = self.read_seal(frozen.sha256)
        if seal.canonical_json_bytes() != frozen.payload or published_at != now:
            raise SealLedgerError("prospective seal independent readback differs")
        return PublishedSeal(frozen.sha256, published_at)

    def read_seal(
        self, expected_sha256: str
    ) -> tuple[Gate3ProspectivePreregistration, datetime]:
        """Read from a new connection using an independently retained pin."""

        try:
            with closing(self._connect(readonly=True)) as db:
                return self._seal_row(db, expected_sha256)
        except sqlite3.Error as exc:
            raise SealLedgerError("prospective seal readback failed") from exc

    def _receipt_row(
        self, db: sqlite3.Connection, seal_sha256: str, receipt_sha256: str
    ) -> tuple[Gate3ProspectiveHoldoutReceipt, datetime]:
        seal, _ = self._seal_row(db, seal_sha256)
        row = db.execute(
            "SELECT payload, published_at FROM receipts "
            "WHERE seal_sha256=? AND receipt_sha256=?",
            (_sha256(seal_sha256), _sha256(receipt_sha256)),
        ).fetchone()
        if row is None:
            raise SealLedgerError("prospective receipt is not published")
        try:
            receipt = verify_prospective_holdout_receipt(
                bytes(row[0]), expected_sha256=receipt_sha256
            )
        except ArtifactVerificationError as exc:
            raise SealLedgerError("published receipt failed readback") from exc
        if (
            receipt.preregistration_sha256 != seal_sha256
            or receipt.preregistration.canonical_json_bytes()
            != seal.canonical_json_bytes()
        ):
            raise SealLedgerError("published receipt belongs to another seal")
        published_at = _parse_time(row[1])
        if published_at < receipt.recorded_at:
            raise SealLedgerError("published receipt predates its recorded time")
        return receipt, published_at

    def publish_receipt(
        self,
        receipt: Gate3ProspectiveHoldoutReceipt,
        *,
        expected_seal_sha256: str,
    ) -> PublishedReceipt:
        """Pin one post-acquisition receipt; its caller flags grant no authority."""

        if type(receipt) is not Gate3ProspectiveHoldoutReceipt:
            raise SealLedgerError("exact prospective receipt required")
        try:
            frozen = freeze_prospective_holdout_receipt(receipt)
        except ArtifactVerificationError as exc:
            raise SealLedgerError("prospective receipt validation failed") from exc
        now = _utc_now()
        if receipt.recorded_at > now:
            raise SealLedgerError("prospective receipt is dated in the future")
        try:
            with closing(self._connect(readonly=False)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                seal, _ = self._seal_row(db, expected_seal_sha256)
                if (
                    receipt.preregistration_sha256 != expected_seal_sha256
                    or receipt.preregistration.canonical_json_bytes()
                    != seal.canonical_json_bytes()
                ):
                    raise SealLedgerError("receipt does not match published seal")
                db.execute(
                    "INSERT INTO receipts VALUES (?, ?, ?, ?)",
                    (
                        expected_seal_sha256,
                        frozen.sha256,
                        frozen.payload,
                        now.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise SealLedgerError("prospective receipt already published") from exc
        except sqlite3.Error as exc:
            raise SealLedgerError("prospective receipt publication uncertain") from exc
        stored, published_at = self.read_receipt(expected_seal_sha256, frozen.sha256)
        if stored.canonical_json_bytes() != frozen.payload or published_at != now:
            raise SealLedgerError("prospective receipt independent readback differs")
        return PublishedReceipt(frozen.sha256, expected_seal_sha256, published_at)

    def read_receipt(
        self, expected_seal_sha256: str, expected_receipt_sha256: str
    ) -> tuple[Gate3ProspectiveHoldoutReceipt, datetime]:
        try:
            with closing(self._connect(readonly=True)) as db:
                return self._receipt_row(
                    db, expected_seal_sha256, expected_receipt_sha256
                )
        except sqlite3.Error as exc:
            raise SealLedgerError("prospective receipt readback failed") from exc

    def reserve_formal_evaluation(
        self, *, expected_seal_sha256: str, expected_receipt_sha256: str
    ) -> FormalEvaluationReservation:
        """Consume the seal once before evaluator access; crashes do not reset it."""

        now = _utc_now()
        reservation_id = uuid.uuid4().hex
        try:
            with closing(self._connect(readonly=False)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                receipt, published_at = self._receipt_row(
                    db, expected_seal_sha256, expected_receipt_sha256
                )
                if (
                    now < published_at
                    or now
                    < receipt.preregistration.prospective_holdout.first_permitted_access_at
                ):
                    raise SealLedgerError("formal evaluation precedes permitted access")
                db.execute(
                    "INSERT INTO formal_evaluations VALUES (?, ?, ?, ?)",
                    (
                        expected_seal_sha256,
                        expected_receipt_sha256,
                        reservation_id,
                        now.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise SealLedgerError(
                "formal evaluation already consumed for seal"
            ) from exc
        except sqlite3.Error as exc:
            raise SealLedgerError("formal evaluation reservation uncertain") from exc
        observed = self.read_evaluation(expected_seal_sha256)
        if observed.reservation_id != reservation_id or observed.started_at != now:
            raise SealLedgerError("formal evaluation independent readback differs")
        return observed

    def read_evaluation(self, expected_seal_sha256: str) -> FormalEvaluationReservation:
        try:
            with closing(self._connect(readonly=True)) as db:
                row = db.execute(
                    "SELECT receipt_sha256, reservation_id, started_at "
                    "FROM formal_evaluations WHERE seal_sha256=?",
                    (_sha256(expected_seal_sha256),),
                ).fetchone()
                if row is None:
                    raise SealLedgerError("formal evaluation was not reserved")
                _, receipt_published_at = self._receipt_row(
                    db, expected_seal_sha256, row[0]
                )
                started_at = _parse_time(row[2])
                if started_at < receipt_published_at:
                    raise SealLedgerError(
                        "formal evaluation predates receipt publication"
                    )
                if type(row[1]) is not str or not _RESERVATION_ID.fullmatch(row[1]):
                    raise SealLedgerError("formal evaluation reservation ID invalid")
                return FormalEvaluationReservation(
                    row[1], expected_seal_sha256, row[0], started_at
                )
        except sqlite3.Error as exc:
            raise SealLedgerError("formal evaluation readback failed") from exc
