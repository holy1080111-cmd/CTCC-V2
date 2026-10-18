"""Record a DENY-only, source-derived witness for an existing Demo revision.

The caller supplies only source locators and an expected ledger revision. Both
observation indexes independently replay their original B1 sources before a
UID-locked transaction binds their saved heads to the *current* claims hash.
The returned witness is read again in a different SQL session. No method here
issues an account revision, portfolio snapshot, reservation or order permit.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from sqlalchemy import select

from app.database.models.account_current_observation import (
    DemoAccountCurrentObservation as Current,
)
from app.database.models.account_observation_index_typed import (
    DemoAccountObservationTypedBatch as Typed,
)
from app.database.models.account_revision_source_witness import (
    DemoAccountRevisionSourceWitness as Witness,
)
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.account_current_observation import (
    AccountCurrentObservationRepository,
)
from app.database.repositories.account_observation_index_typed import (
    AccountObservationTypedIndexRepository,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_history_join as joined
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_revision_recorded_claim as recorded_claim
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

_SCHEMA = "ctcc.demo_account_revision_source_witness.v1"
_BLOCKERS = (
    "account_revision_is_caller_claim_not_collector_issued",
    "complete_portfolio_snapshot_unavailable",
    "funding_accrual_and_complete_history_unproven",
    "native_clock_and_credential_session_unverified",
    "registered_region_unverified",
    "risk_reservation_and_execution_recheck_unperformed",
)


class AccountRevisionSourceError(ValueError):
    """Stable denial without account, source or SQL details."""


def _deny(code: str) -> None:
    raise AccountRevisionSourceError(code)


def _keys(scope: LedgerScope) -> dict[str, str]:
    checked_bootstrap(scope, LedgerScope)
    return {
        "environment": scope.environment,
        "account_id": scope.account_id,
        "settlement_currency": scope.settlement_currency,
    }


def _sequence(value: int) -> int:
    if type(value) is not int or not 1 <= value < 2**63:
        _deny("account_revision_source_sequence_invalid")
    return value


def _claims_match_scope(claims, scope) -> bool:
    return (
        claims.scope == scope
        and claims.account.account_id == scope.account_id
        and claims.account.settlement_currency == scope.settlement_currency
        and claims.authority.environment == scope.environment
        and all(
            stamp.account_id == scope.account_id
            for stamp in (
                claims.account.balance_stamp,
                claims.account.positions_stamp,
                claims.account.history_stamp,
                claims.account.reservations_stamp,
                claims.authority.stamp,
            )
        )
    )


@dataclass(frozen=True, slots=True, repr=False)
class AccountRevisionSourceWitnessReadback:
    witness_json: bytes
    witness_sha256: str
    db_recorded_at: object

    @property
    def account_complete(self) -> bool:
        return False

    @property
    def source_authenticity_verified(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def admission(self) -> str:
        return "DENY"


class AccountRevisionSourceWitnessRepository:
    def __init__(self, session_factory, *, clock):
        self.session_factory = session_factory
        self.ledger = QualificationLedgerRepository(session_factory, clock=clock)
        self.journal = AccountCaptureJournalRepository(session_factory, clock=clock)
        self.typed = AccountObservationTypedIndexRepository(
            session_factory, clock=clock
        )
        self.current = AccountCurrentObservationRepository(session_factory, clock=clock)

    async def _replay_indexes(self, scope, typed_sequence, current_sequence):
        # These existing readers replay saved B3/DB0038 bytes from their
        # original raw B1 chains. They own separate sessions and must run
        # before the final UID lock to avoid self-deadlock.
        typed = await self.typed.read(scope, after=typed_sequence - 1, limit=1)
        current = await self.current.read_one(scope, current_sequence)
        if (
            len(typed) != 1
            or typed[0].sequence != typed_sequence
            or typed[0].account_complete is not False
            or typed[0].source_authenticity_verified is not False
            or typed[0].execution_authority is not False
            or current.sequence != current_sequence
            or current.account_complete is not False
            or current.source_authenticity_verified is not False
            or current.execution_authority is not False
        ):
            _deny("account_revision_source_original_replay_missing")
        return typed[0], current

    async def _derive_locked(
        self,
        session,
        scope,
        account_revision,
        typed_sequence,
        current_sequence,
        *,
        require_latest,
        claims_snapshot=None,
    ):
        ledger_row = await self.ledger._locked(session, scope)
        source_claims = ledger_row if claims_snapshot is None else claims_snapshot
        if (
            source_claims.account_revision != account_revision
            or source_claims.claims_json is None
            or source_claims.claims_sha256 is None
            or journal.digest(source_claims.claims_json.encode("utf-8"))
            != source_claims.claims_sha256
        ):
            _deny("account_revision_source_claims_changed")
        if claims_snapshot is not None and any(
            getattr(source_claims, key) != value for key, value in _keys(scope).items()
        ):
            _deny("account_revision_source_claims_scope_mismatch")
        # Decode the exact claims contract, without treating caller-set
        # completeness stamps as source evidence. For historical readback,
        # use the immutable bytes copied under the original UID lock.
        claims = self.ledger._claims(source_claims)
        if not _claims_match_scope(claims, scope):
            _deny("account_revision_source_nested_scope_mismatch")
        typed_row = await session.get(Typed, (*_keys(scope).values(), typed_sequence))
        current_row = await session.get(
            Current, (*_keys(scope).values(), current_sequence)
        )
        if typed_row is None or current_row is None:
            _deny("account_revision_source_observation_missing")
        if require_latest:
            newest_typed = await session.scalar(
                select(Typed.sequence)
                .filter_by(**_keys(scope))
                .order_by(Typed.sequence.desc())
                .limit(1)
            )
            newest_current = await session.scalar(
                select(Current.sequence)
                .filter_by(**_keys(scope))
                .order_by(Current.sequence.desc())
                .limit(1)
            )
            if newest_typed != typed_sequence or newest_current != current_sequence:
                _deny("account_revision_source_head_changed")
        history_chain = await self.journal._read_chain_locked(
            session, scope, typed_row.capture_id
        )
        current_chain = await self.journal._read_chain_locked(
            session, scope, current_row.capture_id
        )
        history_ref = observed.source_reference(history_chain)
        current_ref = observed.source_reference(current_chain)
        history_packet = joined._packet(history_chain, history_ref)
        current_packet = joined._packet(current_chain, current_ref)
        if (
            type(history_packet.plan) is not capture.AllProductDemoAccountCapturePlan
            or type(current_packet.plan) is not capture.CurrentDemoAccountCapturePlanV7
            or history_ref.capture_id == current_ref.capture_id
            or any(
                getattr(history_packet.plan, name) != getattr(current_packet.plan, name)
                for name in (
                    "environment",
                    "expected_uid",
                    "expected_main_uid",
                    "session_binding_id",
                    "settlement_currency",
                    "registration_region",
                    "origin",
                    "registration_evidence_sha256",
                )
            )
            or history_packet.plan.expected_uid != scope.account_id
            or history_packet.plan.settlement_currency != scope.settlement_currency
            or journal.checked_event(history_chain[-1].event)["kind"] != "terminal"
            or journal.checked_event(current_chain[-1].event)["kind"] != "terminal"
            or journal.checked_event(history_chain[-1].event)["outcome"]
            != "complete_recorded"
            or journal.checked_event(current_chain[-1].event)["outcome"]
            != "complete_recorded"
            or history_chain[-1].db_recorded_at >= current_chain[0].db_recorded_at
        ):
            _deny("account_revision_source_chain_or_plan_mismatch")
        typed_doc = self.typed._checked_document(typed_row, scope)
        current_doc = json.loads(current_row.document_json)
        if (
            typed_row.source_sequence != len(history_chain)
            or typed_row.event_sha256
            != journal.digest(typed_row.document_json.encode("utf-8"))
            or typed_doc["reference"] != observed.reference_document(history_ref)
            or current_row.source_sequence != len(current_chain)
            or current_row.event_sha256
            != journal.digest(current_row.document_json.encode("utf-8"))
            or current_doc.get("source_reference")
            != observed.reference_document(current_ref)
            or current_doc.get("admission") != "DENY"
            or current_doc.get("account_complete") is not False
            or typed_doc.get("admission") != "DENY"
            or typed_doc.get("account_complete") is not False
        ):
            _deny("account_revision_source_persisted_binding_mismatch")
        document = journal.canonical(
            {
                "schema_version": _SCHEMA,
                "scope": [
                    scope.environment,
                    scope.account_id,
                    scope.settlement_currency,
                ],
                "account_revision": account_revision,
                "ledger_revision": source_claims.ledger_revision,
                "claims_sha256": source_claims.claims_sha256,
                "history": {
                    "capture_id": history_ref.capture_id,
                    "terminal_sequence": len(history_chain),
                    "head_sha256": history_ref.head_sha256,
                    "typed_sequence": typed_row.sequence,
                    "typed_head_sha256": typed_row.event_sha256,
                },
                "current": {
                    "capture_id": current_ref.capture_id,
                    "terminal_sequence": len(current_chain),
                    "head_sha256": current_ref.head_sha256,
                    "index_sequence": current_row.sequence,
                    "index_sha256": current_row.event_sha256,
                },
                "blocking_reasons": list(_BLOCKERS),
                "account_complete": False,
                "source_authenticity_verified": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
        if len(document) > 262144:
            _deny("account_revision_source_document_bound")
        return document, source_claims.claims_json

    async def record_denied_source_witness(
        self,
        scope,
        *,
        account_revision,
        typed_sequence,
        current_sequence,
    ) -> AccountRevisionSourceWitnessReadback:
        """Commit one blocked source binding, then replay it in another session.

        The method intentionally does not take claims, region proof, booleans,
        packet JSON or an authority flag from the caller. A commit/readback
        uncertainty leaves the row in place and returns no usable result.
        """
        try:
            _keys(scope)
            _sequence(account_revision)
            _sequence(typed_sequence)
            _sequence(current_sequence)
            typed, current = await self._replay_indexes(
                scope, typed_sequence, current_sequence
            )
            async with self.session_factory() as session, session.begin():
                document, claims_json = await self._derive_locked(
                    session,
                    scope,
                    account_revision,
                    typed_sequence,
                    current_sequence,
                    require_latest=True,
                )
                value = json.loads(document)
                if (
                    typed.event_sha256 != value["history"]["typed_head_sha256"]
                    or current.event_sha256 != value["current"]["index_sha256"]
                ):
                    _deny("account_revision_source_replayed_head_changed")
                session.add(
                    Witness(
                        witness_sha256=journal.digest(document),
                        **_keys(scope),
                        account_revision=account_revision,
                        ledger_revision=value["ledger_revision"],
                        claims_sha256=value["claims_sha256"],
                        claims_json=claims_json,
                        history_capture_id=value["history"]["capture_id"],
                        history_terminal_sequence=value["history"]["terminal_sequence"],
                        history_head_sha256=value["history"]["head_sha256"],
                        typed_sequence=typed_sequence,
                        typed_head_sha256=value["history"]["typed_head_sha256"],
                        current_capture_id=value["current"]["capture_id"],
                        current_terminal_sequence=value["current"]["terminal_sequence"],
                        current_head_sha256=value["current"]["head_sha256"],
                        current_sequence=current_sequence,
                        current_index_sha256=value["current"]["index_sha256"],
                        witness_json=document.decode("utf-8"),
                        account_complete=False,
                        source_authenticity_verified=False,
                        execution_authority=False,
                        admission="DENY",
                    )
                )
            return await self.read_one(scope, journal.digest(document))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- never disclose private source or SQL text
            raise AccountRevisionSourceError(
                "account_revision_source_record_failed"
            ) from None

    async def read_one(
        self, scope, witness_sha256
    ) -> AccountRevisionSourceWitnessReadback:
        """Separate-session readback of source, claims and saved witness bytes."""
        try:
            _keys(scope)
            journal._sha(witness_sha256)
            async with self.session_factory() as session, session.begin():
                row = await session.get(Witness, witness_sha256)
                if row is None or any(
                    getattr(row, key) != value for key, value in _keys(scope).items()
                ):
                    _deny("account_revision_source_witness_missing")
                account_revision = row.account_revision
                typed_sequence = row.typed_sequence
                current_sequence = row.current_sequence
                saved = row.witness_json.encode("utf-8")
                saved_claims = row.claims_json
                recorded_at = row.db_recorded_at
            typed, current = await self._replay_indexes(
                scope, typed_sequence, current_sequence
            )
            async with self.session_factory() as session, session.begin():
                row = await session.get(Witness, witness_sha256)
                if row is None:
                    _deny("account_revision_source_witness_missing")
                expected, claims_json = await self._derive_locked(
                    session,
                    scope,
                    account_revision,
                    typed_sequence,
                    current_sequence,
                    require_latest=False,
                    claims_snapshot=row,
                )
                if (
                    row.witness_json.encode("utf-8") != saved
                    or claims_json != saved_claims
                    or saved != expected
                    or journal.digest(saved) != witness_sha256
                    or typed.event_sha256
                    != json.loads(saved)["history"]["typed_head_sha256"]
                    or current.event_sha256
                    != json.loads(saved)["current"]["index_sha256"]
                    or row.account_complete is not False
                    or row.source_authenticity_verified is not False
                    or row.execution_authority is not False
                    or row.admission != "DENY"
                ):
                    _deny("account_revision_source_witness_readback_changed")
            return AccountRevisionSourceWitnessReadback(
                saved, witness_sha256, recorded_at
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- no private source or SQL exception text
            raise AccountRevisionSourceError(
                "account_revision_source_readback_failed"
            ) from None

    async def _recorded_claim_once(self, scope, witness_sha256):
        """Recompute a factual current claim under one independent UID lock."""
        async with self.session_factory() as session, session.begin():
            await self.ledger._locked(session, scope)
            row = await session.get(Witness, witness_sha256)
            if (
                row is None
                or any(
                    getattr(row, key) != value for key, value in _keys(scope).items()
                )
                or journal.digest(row.witness_json.encode("utf-8")) != witness_sha256
            ):
                _deny("account_revision_source_claim_witness_missing")
            chain = await self.journal._read_chain_locked(
                session, scope, row.current_capture_id
            )
            reference = observed.source_reference(chain)
            value = json.loads(row.witness_json)
            if (
                reference.capture_id != row.current_capture_id
                or reference.head_sha256 != row.current_head_sha256
                or len(chain) != row.current_terminal_sequence
                or value["current"]["capture_id"] != reference.capture_id
                or value["current"]["head_sha256"] != reference.head_sha256
                or value["claims_sha256"] != row.claims_sha256
                or value["account_revision"] != row.account_revision
            ):
                _deny("account_revision_source_claim_binding_changed")
            claims = self.ledger._claims(row)
            return recorded_claim.derive_recorded_current_claim(
                chain,
                reference=reference,
                scope=scope,
                witness_sha256=witness_sha256,
                account_revision=row.account_revision,
                claims_sha256=row.claims_sha256,
                claims=claims,
                validated_at=chain[-1].db_recorded_at,
            )

    async def read_denied_recorded_source_claim(
        self, scope, witness_sha256
    ) -> recorded_claim.RecordedAccountClaim:
        """Replay an immutable source projection twice; issue no revision.

        The initial and final DB0043 readbacks validate the original witness.
        Two additional independent sessions recompute the same source-derived
        receipt. If any session is uncertain or disagrees, no claim is returned.
        """
        try:
            before = await self.read_one(scope, witness_sha256)
            first = await self._recorded_claim_once(scope, witness_sha256)
            second = await self._recorded_claim_once(scope, witness_sha256)
            after = await self.read_one(scope, witness_sha256)
            if first != second or before != after:
                _deny("account_revision_source_claim_readback_changed")
            return first
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- no private source or SQL exception text
            raise AccountRevisionSourceError(
                "account_revision_source_claim_readback_failed"
            ) from None
