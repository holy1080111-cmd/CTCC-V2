"""Replay the complete accepted B3 index prefix before a fresh account GET.

This is a source-owned, read-only prefix witness, not exchange-history finality
or an account/risk authority. B3's own read path replays original B1 sources.
"""

import json
import re
from dataclasses import dataclass

from sqlalchemy import select

from app.database.models.account_current_observation import (
    DemoAccountCurrentObservation as Current,
)
from app.database.models.account_observation_index import (
    DemoAccountObservationBatch as History,
)
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.account_current_observation import (
    AccountCurrentObservationRepository,
)
from app.database.repositories.account_observation_index import (
    PAGE_SIZE,
    AccountObservationIndexRepository,
    ObservationBatchReadback,
)
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.account_accepted_b3_prefix.v1",
        "source_policy_sha256": observed.POLICY_SHA256,
        "population": "all_committed_B3_sequences_1_through_latest_head",
        "source": "each_original_B1_chain_replayed_by_B3_read",
        "head_and_local_state": "same_exact_UID_lock_before_and_after_replay",
        "current_predecessor": "replayed_DB0038_head_or_genesis",
        "exchange_history_finality": False,
        "source_authenticity_verified": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class AccountHistoryPrefixError(ValueError):
    """Fixed rejection code with no account, SQL or source values."""


@dataclass(frozen=True, slots=True, repr=False)
class HistoryPrefixReadback:
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def account_complete(self):
        return False

    @property
    def execution_authority(self):
        return False

    @property
    def admission(self):
        return "DENY"


def _deny(code):
    raise AccountHistoryPrefixError(code)


def _sha(value):
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        _deny("history_prefix_hash_invalid")
    return value


def _keys(scope):
    return {
        "environment": scope.environment,
        "account_id": scope.account_id,
        "settlement_currency": scope.settlement_currency,
    }


class AccountHistoryPrefixRepository:
    """Derive a fixed B3 head from PostgreSQL; accept no caller head or proof."""

    def __init__(self, session_factory, *, clock):
        self.session_factory = session_factory
        self.journal = AccountCaptureJournalRepository(session_factory, clock=clock)
        self.history = AccountObservationIndexRepository(session_factory, clock=clock)
        self.current = AccountCurrentObservationRepository(session_factory, clock=clock)

    async def _heads(self, scope):
        async with self.session_factory() as session, session.begin():
            account = await self.journal._lock(session, scope)
            history = await session.scalar(
                select(History)
                .filter_by(**_keys(scope))
                .order_by(History.sequence.desc())
                .limit(1)
            )
            current = await session.scalar(
                select(Current)
                .filter_by(**_keys(scope))
                .order_by(Current.sequence.desc())
                .limit(1)
            )
            checkpoint = await self.journal.ledger._bootstrap_checkpoint(
                session, scope, account
            )
            if history is None:
                _deny("history_prefix_head_missing")
            return (
                history.sequence,
                _sha(history.event_sha256),
                0 if current is None else current.sequence,
                None if current is None else _sha(current.event_sha256),
                _sha(checkpoint.state_sha256),
            )

    async def read_full_prefix(self, scope):
        try:
            return await self._read_full_prefix(scope)
        except Exception:  # noqa: BLE001 -- never include private SQL/source data
            raise AccountHistoryPrefixError("history_prefix_read_failed") from None

    async def _read_full_prefix(self, scope):
        checked_bootstrap(scope, LedgerScope)
        head = await self._heads(scope)
        through, head_sha, current_seq, current_sha, local_sha = head
        if type(through) is not int or through < 1:
            _deny("history_prefix_head_invalid")
        if current_seq:
            previous = await self.current.read_one(scope, current_seq)
            if previous.event_sha256 != current_sha:
                _deny("history_prefix_current_predecessor_changed")
        scope_sha = journal.digest(journal.canonical(list(_keys(scope).values())))
        membership = journal.digest(
            journal.canonical([POLICY_SHA256, scope_sha, "accepted_B3_prefix"])
        )
        prior_event = prior_anchor = None
        prior_counts = (0, 0, 0)
        last_reference = None
        last_anchor = None
        after = 0
        while after < through:
            count = min(PAGE_SIZE, through - after)
            page = await self.history.read(scope, after=after, limit=count)
            if type(page) is not tuple or len(page) != count:
                _deny("history_prefix_page_missing")
            for number, item in enumerate(page, after + 1):
                if (
                    type(item) is not ObservationBatchReadback
                    or type(item.sequence) is not int
                    or item.sequence != number
                ):
                    _deny("history_prefix_sequence_missing")
                raw = item.document_json
                if type(raw) is not bytes:
                    _deny("history_prefix_document_invalid")
                document = json.loads(raw)
                if journal.canonical(document) != raw:
                    _deny("history_prefix_document_invalid")
                anchor = json.loads(document["anchor_json"])
                counts = tuple(
                    anchor.get(name)
                    for name in (
                        "source_facts_through_sequence",
                        "coverage_through_sequence",
                        "unresolved_findings_through_sequence",
                    )
                )
                if (
                    document.get("schema_version")
                    != "ctcc.account_observation_batch.v1"
                    or document.get("scope_sha256") != scope_sha
                    or document.get("sequence") != number
                    or document.get("previous_sha256") != prior_event
                    or document.get("policy_sha256") != observed.POLICY_SHA256
                    or document.get("account_complete") is not False
                    or document.get("execution_authority") is not False
                    or document.get("admission") != "DENY"
                    or journal.digest(raw) != _sha(item.event_sha256)
                    or document.get("receipt_sha256") != item.replay.receipt_sha256
                    or anchor.get("through_sequence") != number
                    or anchor.get("previous_sequence") != number - 1
                    or anchor.get("previous_event_sha256") != prior_event
                    or anchor.get("previous_anchor_sha256") != prior_anchor
                    or anchor.get("policy_sha256") != observed.POLICY_SHA256
                    or anchor.get("scope_sha256") != scope_sha
                    or anchor.get("current_source_membership_sha256")
                    != document.get("source_membership_sha256")
                    or anchor.get("current_coverage_membership_sha256")
                    != document.get("coverage_membership_sha256")
                    or any(
                        type(value) is not int or value < previous
                        for value, previous in zip(counts, prior_counts, strict=True)
                    )
                    or journal.canonical(anchor) != document["anchor_json"].encode()
                    or journal.digest(document["anchor_json"].encode())
                    != _sha(document.get("anchor_sha256"))
                ):
                    _deny("history_prefix_source_or_chain_invalid")
                membership = journal.digest(
                    journal.canonical(
                        [
                            membership,
                            number,
                            item.event_sha256,
                            document["anchor_sha256"],
                            item.replay.receipt_sha256,
                        ]
                    )
                )
                prior_event = item.event_sha256
                prior_anchor = document["anchor_sha256"]
                prior_counts = counts
                last_reference = document.get("reference")
                last_anchor = anchor
            after += count
        if prior_event != head_sha or head != await self._heads(scope):
            _deny("history_prefix_head_or_local_state_changed")
        if type(last_reference) is not dict:
            _deny("history_prefix_source_reference_missing")
        observed.CaptureReference(**last_reference)
        result = {
            "schema_version": "ctcc.account_accepted_b3_prefix.v1",
            "policy_sha256": POLICY_SHA256,
            "scope_sha256": scope_sha,
            "through_sequence": through,
            "head_sha256": head_sha,
            "head_anchor_sha256": prior_anchor,
            "ordered_membership_sha256": membership,
            "last_source_reference": last_reference,
            "current_predecessor_sequence": current_seq,
            "current_predecessor_sha256": current_sha,
            "local_checkpoint_sha256": local_sha,
            "unresolved_findings_through_sequence": last_anchor.get(
                "unresolved_findings_through_sequence"
            ),
            "entire_accepted_b3_index_prefix_replayed": True,
            "exchange_history_finality_verified": False,
            "source_authenticity_verified": False,
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
        return HistoryPrefixReadback(journal.canonical(result))
