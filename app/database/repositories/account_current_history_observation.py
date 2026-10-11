"""DENY-only binding of separately replayed B3 and DB0038 observations.

The two indexes have different source semantics. This read-only diagnostic
checks their exact heads and original B1 references under one account lock;
it does not turn either index into a portfolio risk or execution revision.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app.database.models.account_current_observation import (
    DemoAccountCurrentObservation as Current,
)
from app.database.models.account_observation_index import (
    DemoAccountObservationBatch as History,
)
from app.database.repositories import account_current_observation as current_index
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.account_current_observation import (
    AccountCurrentObservationRepository,
)
from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_history_join as source_join
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

MAX_SEQUENCE = 9223372036854775807
POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.demo_current_history_observation_join.v1",
        "history_index_policy_sha256": observed.POLICY_SHA256,
        "current_index_policy_sha256": current_index.POLICY_SHA256,
        "source_join_policy_sha256": source_join.V7_ORDERED_POLICY_SHA256,
        "readback": "independent_selected_head_replay_then_same_exact_uid_locked_head_binding",
        "entire_history_index_replayed": False,
        "current_freshness_at_locked_readback": "unproven",
        "history_index_commit_before_current_request": "unproven",
        "account_complete": False,
        "flat_start_permission": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class CurrentHistoryObservationError(ValueError):
    """Fixed failure code; private source and SQL details are suppressed."""


@dataclass(frozen=True, slots=True, repr=False)
class CurrentHistoryObservationReadback:
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def account_complete(self):
        return False

    @property
    def flat_start_permission(self):
        return False

    @property
    def execution_authority(self):
        return False

    @property
    def admission(self):
        return "DENY"


def _keys(scope):
    checked_bootstrap(scope, LedgerScope)
    return {
        "environment": scope.environment,
        "account_id": scope.account_id,
        "settlement_currency": scope.settlement_currency,
    }


def _checked_replay(readback, *, sequence, expected_sha256):
    if (
        len(readback) != 1
        or readback[0].sequence != sequence
        or readback[0].event_sha256 != expected_sha256
    ):
        raise CurrentHistoryObservationError("observation_head_replay_mismatch")
    return readback[0]


class AccountCurrentHistoryObservationRepository:
    def __init__(self, session_factory, *, clock):
        self.session_factory = session_factory
        self.journal = AccountCaptureJournalRepository(session_factory, clock=clock)
        self.history = AccountObservationIndexRepository(session_factory, clock=clock)
        self.current = AccountCurrentObservationRepository(session_factory, clock=clock)

    async def read_joined(
        self,
        scope,
        *,
        history_sequence,
        history_sha256,
        current_sequence,
        current_sha256,
    ):
        """Pin both independently replayed index heads in one new UID lock.

        The current source is replayed at its original saved validation time,
        which is not evidence that it remains fresh at this readback time.
        """
        try:
            return await self._read_joined(
                scope,
                history_sequence=history_sequence,
                history_sha256=history_sha256,
                current_sequence=current_sequence,
                current_sha256=current_sha256,
            )
        except Exception:  # noqa: BLE001 -- never leak private account/SQL data
            raise CurrentHistoryObservationError(
                "current_history_observation_join_failed"
            ) from None

    async def _read_joined(
        self,
        scope,
        *,
        history_sequence,
        history_sha256,
        current_sequence,
        current_sha256,
    ):
        keys = _keys(scope)
        if (
            type(history_sequence) is not int
            or type(current_sequence) is not int
            or not 1 <= history_sequence <= MAX_SEQUENCE
            or not 1 <= current_sequence <= MAX_SEQUENCE
        ):
            raise CurrentHistoryObservationError("observation_sequence_invalid")
        journal._sha(history_sha256)
        journal._sha(current_sha256)
        history_readback = _checked_replay(
            await self.history.read(scope, after=history_sequence - 1, limit=1),
            sequence=history_sequence,
            expected_sha256=history_sha256,
        )
        current_readback = await self.current.read_one(scope, current_sequence)
        if current_readback.event_sha256 != current_sha256:
            raise CurrentHistoryObservationError("observation_current_hash_mismatch")
        history_document = json.loads(history_readback.document_json)
        current_document = json.loads(current_readback.document_json)
        history_reference = observed.CaptureReference(**history_document["reference"])
        current_reference = observed.CaptureReference(
            **current_document["source_reference"]
        )
        validated_at = capture._utc(
            datetime.fromisoformat(current_document["validated_at"])
        )
        if (
            history_document.get("policy_sha256") != observed.POLICY_SHA256
            or current_document.get("schema_version")
            != "ctcc.demo_current_observation.v1"
            or current_document.get("policy_sha256") != current_index.POLICY_SHA256
            or history_document.get("account_complete") is not False
            or history_document.get("execution_authority") is not False
            or current_document.get("account_complete") is not False
            or current_document.get("flat_start_permission") is not False
            or current_document.get("execution_authority") is not False
            or history_document.get("admission") != "DENY"
            or current_document.get("admission") != "DENY"
        ):
            raise CurrentHistoryObservationError("observation_policy_or_authority")
        async with self.session_factory() as session, session.begin():
            account = await self.journal._lock(session, scope)
            history_row = await session.scalar(
                select(History)
                .filter_by(**keys)
                .order_by(History.sequence.desc())
                .limit(1)
            )
            current_row = await session.scalar(
                select(Current)
                .filter_by(**keys)
                .order_by(Current.sequence.desc())
                .limit(1)
            )
            if (
                history_row is None
                or current_row is None
                or history_row.sequence != history_sequence
                or current_row.sequence != current_sequence
                or history_row.event_sha256 != history_sha256
                or current_row.event_sha256 != current_sha256
                or history_row.document_json.encode("utf-8")
                != history_readback.document_json
                or history_row.receipt_json.encode("utf-8")
                != history_readback.replay.receipt_json
                or current_row.document_json.encode("utf-8")
                != current_readback.document_json
                or current_row.receipt_json.encode("utf-8")
                != current_readback.receipt_json
                or history_row.capture_id != history_reference.capture_id
                or current_row.capture_id != current_reference.capture_id
            ):
                raise CurrentHistoryObservationError("observation_locked_head_changed")
            history_chain = await self.journal._read_chain_locked(
                session, scope, history_reference.capture_id
            )
            current_chain = await self.journal._read_chain_locked(
                session, scope, current_reference.capture_id
            )
            if (
                history_row.source_sequence != len(history_chain)
                or current_row.source_sequence != len(current_chain)
                or capture._utc(history_row.db_recorded_at)
                < capture._utc(history_chain[-1].db_recorded_at)
                or capture._utc(current_row.db_recorded_at)
                < capture._utc(current_chain[-1].db_recorded_at)
                or observed.source_reference(history_chain) != history_reference
                or observed.source_reference(current_chain) != current_reference
            ):
                raise CurrentHistoryObservationError("observation_original_binding")
            joined = source_join.join_recorded_account_sources(
                history_chain=history_chain,
                history_reference=history_reference,
                current_chain=current_chain,
                current_reference=current_reference,
                scope=scope,
                validated_at=validated_at,
                expected_policy_sha256=source_join.V7_ORDERED_POLICY_SHA256,
            )
            join_value = json.loads(joined.receipt_json)
            checkpoint = await self.journal.ledger._bootstrap_checkpoint(
                session, scope, account
            )
            if (
                checkpoint.state_sha256
                != join_value["recorded_local_checkpoint_sha256"]
            ):
                raise CurrentHistoryObservationError(
                    "observation_local_revision_changed"
                )
            history_observed = json.loads(history_readback.replay.receipt_json)
            history_blockers = history_observed["blocking_reasons"]
            if (
                type(history_blockers) is not list
                or any(type(item) is not str for item in history_blockers)
                or history_observed.get("account_complete") is not False
                or history_observed.get("execution_authority") is not False
                or history_observed.get("admission") != "DENY"
            ):
                raise CurrentHistoryObservationError(
                    "observation_history_receipt_invalid"
                )
            blockers = sorted(
                set(join_value["blocking_reasons"])
                | set(history_blockers)
                | {
                    "current_freshness_at_locked_readback_unproven",
                    "history_index_commit_before_current_request_unproven",
                    "exchange_atomic_revision_unproven",
                    "history_index_not_portfolio_risk_snapshot",
                    "reservation_intent_protection_not_joined",
                }
            )
            document = {
                "schema_version": "ctcc.demo_current_history_observation_join.v1",
                "policy_sha256": POLICY_SHA256,
                "scope_sha256": join_value["scope_sha256"],
                "session_binding_sha256": join_value["session_binding_sha256"],
                "history_sequence": history_sequence,
                "history_index_sha256": history_sha256,
                "history_index_receipt_sha256": (
                    history_readback.replay.receipt_sha256
                ),
                "history_source_reference": join_value["history_source_reference"],
                "history_index_policy_sha256": observed.POLICY_SHA256,
                "current_sequence": current_sequence,
                "current_index_sha256": current_sha256,
                "current_index_receipt_sha256": journal.digest(
                    current_readback.receipt_json
                ),
                "current_source_reference": join_value["current_source_reference"],
                "current_index_policy_sha256": current_document["policy_sha256"],
                "original_source_join_policy_sha256": (
                    source_join.V7_ORDERED_POLICY_SHA256
                ),
                "original_source_join_receipt_sha256": joined.receipt_sha256,
                "recorded_local_checkpoint_sha256": (
                    join_value["recorded_local_checkpoint_sha256"]
                ),
                "db_local_state_sha256": checkpoint.state_sha256,
                "db_account_revision": checkpoint.state.account_revision,
                "db_ledger_revision": checkpoint.state.ledger_revision,
                "history_selected_head_replayed": True,
                "current_selected_head_replayed": True,
                "entire_history_index_replayed": False,
                "same_account_locked_head_binding": True,
                "history_index_commit_before_current_request_verified": False,
                "current_freshness_at_locked_readback_verified": False,
                "exchange_atomic_revision_verified": False,
                "history_tail_closed": False,
                "funding_accrual_complete": False,
                "portfolio_risk_snapshot": None,
                "blocking_reasons": blockers,
                "account_complete": False,
                "source_authenticity_verified": False,
                "flat_start_permission": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        return CurrentHistoryObservationReadback(journal.canonical(document))
