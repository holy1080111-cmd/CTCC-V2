"""Bounded local event observations; no source, candidate or order issuer."""

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.trade_qualification.models import require_aware
from app.trade_qualification.reservations import (
    Digest,
    LedgerModel,
    LedgerScope,
    QualificationLedgerError,
    ReservationReceipt,
    Revision,
    reservation_id,
)

VERSION = "ctcc-ledger-event-observation-v1"
MAX_OBSERVATION_BYTES = 65536


class LedgerEventObservation(LedgerModel):
    """An exact key at one locked read, including a terminal tombstone.

    Absence means only that this query found no row at the measured read. This
    new type is not registered with any existing ledger/qualification issuer.
    The matching receipt carries the actual storage scope's revisions; the
    outer pair belongs to the requested scope, which may use another currency.
    """

    contract_version: Literal["ctcc-ledger-event-observation-v1"]
    scope: LedgerScope
    original_event_key: Digest
    account_revision: Revision
    ledger_revision: Revision
    matched: ReservationReceipt | None
    request_started_at: datetime
    observed_at: datetime
    received_at: datetime
    monotonic_started_ns: Annotated[int, Field(ge=0, lt=2**63)]
    monotonic_received_ns: Annotated[int, Field(ge=0, lt=2**63)]
    admission: Literal["DENY"] = "DENY"
    order_retry_authority: Literal[False] = False

    @field_validator("order_retry_authority", mode="before")
    @classmethod
    def retry_stays_false(cls, value):
        if value is not False:
            raise ValueError("event_observation_cannot_grant_authority")
        return value

    @field_validator("request_started_at", "observed_at", "received_at")
    @classmethod
    def utc(cls, value):
        if type(value) is not datetime:
            raise ValueError("exact_ledger_clock_required")
        return require_aware(value)

    @model_validator(mode="after")
    def scope_and_bounds(self):
        if (
            self.ledger_revision < self.account_revision
            or (self.account_revision == 0 and self.ledger_revision != 0)
            or not self.request_started_at <= self.observed_at <= self.received_at
            or self.monotonic_started_ns > self.monotonic_received_ns
        ):
            raise ValueError("event_observation_revision_or_clock_invalid")
        match = self.matched
        if match is not None and (
            match.scope.environment != self.scope.environment
            or match.scope.account_id != self.scope.account_id
            or match.original_event_key != self.original_event_key
            or match.reservation_id
            != reservation_id(match.scope, self.original_event_key)
            or match.ledger_revision < match.account_revision
            or match.updated_at > self.observed_at
            or (
                match.scope == self.scope
                and (match.account_revision, match.ledger_revision)
                != (self.account_revision, self.ledger_revision)
            )
        ):
            raise ValueError("event_observation_match_binding_invalid")
        return self

    @property
    def canonical_json(self):
        # Revalidate a detached copy; a mutated/model_construct DTO is never a
        # shortcut around the diagnostic schema or the constant denial fields.
        if type(self) is not LedgerEventObservation:
            raise QualificationLedgerError("exact_event_observation_required")
        try:
            checked = LedgerEventObservation.model_validate(
                self.model_dump(mode="python", round_trip=True), strict=True
            )
            raw = json.dumps(
                checked.model_dump(mode="json", round_trip=True),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            if len(raw.encode("ascii")) > MAX_OBSERVATION_BYTES:
                raise ValueError
            return raw
        except (ValueError, TypeError, RecursionError):
            raise QualificationLedgerError("event_observation_invalid") from None

    @property
    def sha256(self):
        return hashlib.sha256(self.canonical_json.encode("ascii")).hexdigest()
