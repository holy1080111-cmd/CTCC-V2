"""Append-only V7 current-account observation with independent B1 readback.

This is a diagnostic current-source index, separate from the B3 execution-
history index. It never issues an account revision, portfolio snapshot, flat-
start permission or execution authority. The clock is an injected diagnostic
dependency, not a native host-time attestation.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app.database.models.account_current_observation import (
    DemoAccountCurrentObservation as Current,
)
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_verifier as source
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

MAX_SEQUENCE = 9223372036854775807
POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.demo_current_observation.v1",
        "capture_plan": "ctcc.demo_current_account_plan.v7",
        "current_source_policy_sha256": source.V7_POLICY_SHA256,
        "source": "original_B1_terminal_with_eight_algo_EOF_page_chains",
        "storage": "append_only_account_locked_separate_session_readback",
        "clock": "injected_diagnostic_not_native_attestation",
        "db_timestamp": "trigger_overwritten_native_monotone_to_B1_and_prior",
        "historical_index": "separate_B3_history_only",
        "account_complete": False,
        "flat_start_permission": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class CurrentObservationError(ValueError):
    """Fixed private-data-safe failure code only."""


def _deny(code):
    raise CurrentObservationError(code)


def _keys(scope):
    checked_bootstrap(scope, LedgerScope)
    return {
        "environment": scope.environment,
        "account_id": scope.account_id,
        "settlement_currency": scope.settlement_currency,
    }


def _derive(chain, reference, scope, *, sequence, previous_sha256, validated_at):
    """Recompute stored bytes from the exact original terminal and V7 policy."""
    _keys(scope)
    pins = observed.reference_document(reference)
    if (
        observed.source_reference(chain) != reference
        or type(sequence) is not int
        or not 1 <= sequence <= MAX_SEQUENCE
        or (sequence == 1) != (previous_sha256 is None)
    ):
        _deny("current_observation_source_or_sequence_invalid")
    if previous_sha256 is not None:
        journal._sha(previous_sha256)
    at = capture._utc(validated_at)
    verified = source.verify_current_account_sources(
        chain,
        reference=reference,
        scope=scope,
        validated_at=at,
        expected_policy_sha256=source.V7_POLICY_SHA256,
    )
    receipt = verified.receipt_json
    value = json.loads(receipt)
    coverage = value.get("algo_type_coverage")
    if (
        journal.canonical(value) != receipt
        or len(receipt) > 8388608
        or value.get("schema_version") != "ctcc.current_account_source_observation.v6"
        or value.get("policy_sha256") != source.V7_POLICY_SHA256
        or value.get("effective_policy_sha256") != source.V7_POLICY_SHA256
        or value.get("source_reference") != pins
        or value.get("scope_sha256")
        != journal.digest(
            journal.canonical(
                [scope.environment, scope.account_id, scope.settlement_currency]
            )
        )
        or type(coverage) is not dict
        or coverage.get("all_eight_terminal_chains_replayed") is not True
        or coverage.get("order_types") != list(capture.CURRENT_ALGO_ORDER_TYPES_V7)
        or len(coverage.get("terminal_receipt_sha256s", {})) != 8
        or any(
            value.get(name) is not False
            for name in (
                "account_complete",
                "source_authenticity_verified",
                "flat_start_permission",
                "execution_authority",
            )
        )
        or value.get("admission") != "DENY"
    ):
        _deny("current_observation_v7_source_invalid")
    document = journal.canonical(
        {
            "schema_version": "ctcc.demo_current_observation.v1",
            "policy_sha256": POLICY_SHA256,
            "current_source_policy_sha256": source.V7_POLICY_SHA256,
            "scope_sha256": value["scope_sha256"],
            "sequence": sequence,
            "previous_sha256": previous_sha256,
            "source_reference": pins,
            "receipt_sha256": verified.receipt_sha256,
            "validated_at": at.isoformat(),
            "account_complete": False,
            "source_authenticity_verified": False,
            "flat_start_permission": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    if len(document) > 262144:
        _deny("current_observation_document_limit")
    return document, receipt


@dataclass(frozen=True, slots=True, repr=False)
class CurrentObservationReadback:
    sequence: int
    event_sha256: str
    document_json: bytes
    receipt_json: bytes

    @property
    def account_complete(self):
        return False

    @property
    def execution_authority(self):
        return False

    @property
    def flat_start_permission(self):
        return False

    @property
    def source_authenticity_verified(self):
        return False

    @property
    def admission(self):
        return "DENY"


class AccountCurrentObservationRepository:
    def __init__(self, session_factory, *, clock):
        self.session_factory = session_factory
        self.clock = clock
        self.journal = AccountCaptureJournalRepository(session_factory, clock=clock)

    async def read_one(self, scope, sequence):
        """A new SQL session rereads the saved row and original raw B1 chain."""
        try:
            _keys(scope)
            if type(sequence) is not int or not 1 <= sequence <= MAX_SEQUENCE:
                _deny("current_observation_sequence_invalid")
            async with self.session_factory() as session, session.begin():
                await self.journal._lock(session, scope)
                row = await session.get(Current, (*_keys(scope).values(), sequence))
                if row is None:
                    _deny("current_observation_missing")
                previous = (
                    None
                    if sequence == 1
                    else await session.get(
                        Current, (*_keys(scope).values(), sequence - 1)
                    )
                )
                if sequence > 1 and previous is None:
                    _deny("current_observation_predecessor_missing")
                prior_sha = None if previous is None else previous.event_sha256
                if (
                    previous is not None
                    and journal.digest(previous.document_json.encode("utf-8"))
                    != prior_sha
                ):
                    _deny("current_observation_predecessor_changed")
                chain = await self.journal._read_chain_locked(
                    session, scope, row.capture_id
                )
                row_capture_id = row.capture_id
                row_source_sequence = row.source_sequence
                row_previous_sha256 = row.previous_sha256
                row_event_sha256 = row.event_sha256
                row_recorded_at = row.db_recorded_at
                document = row.document_json.encode("utf-8")
                receipt = row.receipt_json.encode("utf-8")
            saved = json.loads(document)
            if (
                row_previous_sha256 != prior_sha
                or row_event_sha256 != journal.digest(document)
                or row_source_sequence != len(chain)
                or capture._utc(row_recorded_at)
                < capture._utc(chain[-1].db_recorded_at)
                or saved.get("source_reference", {}).get("capture_id") != row_capture_id
                or saved.get("sequence") != sequence
            ):
                _deny("current_observation_persisted_binding_invalid")
            reference = observed.CaptureReference(**saved["source_reference"])
            at = capture._utc(datetime.fromisoformat(saved["validated_at"]))
            if at.isoformat() != saved["validated_at"]:
                _deny("current_observation_validated_time_invalid")
            replay_document, replay_receipt = _derive(
                chain,
                reference,
                scope,
                sequence=sequence,
                previous_sha256=prior_sha,
                validated_at=at,
            )
            if document != replay_document or receipt != replay_receipt:
                _deny("current_observation_replay_mismatch")
            return CurrentObservationReadback(
                sequence, row_event_sha256, document, receipt
            )
        except Exception:  # noqa: BLE001 -- no private SQL/raw contents in errors
            raise CurrentObservationError("current_observation_read_failed") from None

    async def append(self, scope, reference, *, expected_sequence):
        """Commit once, then separately reread; uncertain commits never retry."""
        try:
            _keys(scope)
            observed.reference_document(reference)
            if (
                type(expected_sequence) is not int
                or not 0 <= expected_sequence < MAX_SEQUENCE
            ):
                _deny("current_observation_sequence_invalid")
            async with self.session_factory() as session, session.begin():
                await self.journal._lock(session, scope)
                existing = await session.scalar(
                    select(Current).where(Current.capture_id == reference.capture_id)
                )
                if existing is not None:
                    saved = json.loads(existing.document_json)
                    if (
                        (
                            existing.environment,
                            existing.account_id,
                            existing.settlement_currency,
                        )
                        != tuple(_keys(scope).values())
                        or existing.sequence != expected_sequence + 1
                        or saved.get("source_reference")
                        != observed.reference_document(reference)
                    ):
                        _deny("current_observation_idempotency_conflict")
                    number = existing.sequence
                else:
                    previous = await session.scalar(
                        select(Current)
                        .filter_by(**_keys(scope))
                        .order_by(Current.sequence.desc())
                        .limit(1)
                    )
                    if (
                        0 if previous is None else previous.sequence
                    ) != expected_sequence:
                        _deny("current_observation_revision_conflict")
                    prior_sha = None if previous is None else previous.event_sha256
                    chain = await self.journal._read_chain_locked(
                        session, scope, reference.capture_id
                    )
                    at = capture._utc(self.clock())
                    document, receipt = _derive(
                        chain,
                        reference,
                        scope,
                        sequence=expected_sequence + 1,
                        previous_sha256=prior_sha,
                        validated_at=at,
                    )
                    number = expected_sequence + 1
                    session.add(
                        Current(
                            **_keys(scope),
                            sequence=number,
                            capture_id=reference.capture_id,
                            source_sequence=len(chain),
                            previous_sha256=prior_sha,
                            event_sha256=journal.digest(document),
                            document_json=document.decode("utf-8"),
                            receipt_json=receipt.decode("utf-8"),
                        )
                    )
            # A commit exception exits above; this independent session is the
            # required readback and may fail without deleting accepted evidence.
            return await self.read_one(scope, number)
        except Exception:  # noqa: BLE001 -- no private SQL/raw contents in errors
            raise CurrentObservationError("current_observation_append_failed") from None
