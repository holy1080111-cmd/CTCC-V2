"""Transient recorded EOF-to-DB-clock delta for the V7 current B1 chain.

This companion does not amend the v1 observation receipt or grant account,
flat-start, portfolio, or execution authority. Source/host clock authenticity
remains unproven even when recorded body-EOF times appear within 30 seconds of
the native PostgreSQL clock.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

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
from app.database.repositories.account_current_history_observation import (
    POLICY_SHA256 as PRIOR_POLICY_SHA256,
)
from app.database.repositories.account_current_history_observation import (
    AccountCurrentHistoryObservationRepository,
)
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_verifier as source
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

MAX_AGE = timedelta(seconds=30)
POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.demo_current_history_observation_join_recency.v2",
        "prior_join_policy_sha256": PRIOR_POLICY_SHA256,
        "source_policy_sha256": source.V7_POLICY_SHA256,
        "clock": "postgresql_clock_timestamp_after_same_uid_locked_original_V7_B1_reread",
        "freshness_basis": "oldest_replay_verified_B1_body_complete_EOF",
        "maximum_age_seconds": 30,
        "proof_scope": "recorded_B1_EOF_to_native_DB_clock_delta_only_not_actual_freshness",
        "account_complete": False,
        "flat_start_permission": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class CurrentHistoryRecencyError(ValueError):
    """Fixed safe failure code; no private data or SQL in exceptions."""


@dataclass(frozen=True, slots=True, repr=False)
class CurrentHistoryRecencyReadback:
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


def _deny(code):
    raise CurrentHistoryRecencyError(code)


def _utc_datetime(value):
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _deny("recency_timestamp_invalid")
    return value.astimezone(UTC)


def _page_timestamp(value):
    if type(value) is not str:
        _deny("recency_page_timestamp_missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _deny("recency_page_timestamp_invalid")
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.isoformat() != value
    ):
        _deny("recency_page_timestamp_invalid")
    return parsed.astimezone(UTC)


def evaluate_db_clock_page_recency(pages, db_clock_at):
    """Evaluate only original page times against a caller-owned DB sample.

    Production obtains both arguments from an original V7 B1 replay and a
    native clock_timestamp() inside the same exact-UID transaction. This pure
    helper cannot mint a source or execution permit.
    """
    at = _utc_datetime(db_clock_at)
    if type(pages) is not list or not pages:
        _deny("recency_pages_missing")
    earliest_eof = None
    previous_close = None
    for page in pages:
        if type(page) is not dict:
            _deny("recency_page_invalid")
        started = _page_timestamp(page.get("request_started_at"))
        headers = _page_timestamp(page.get("headers_received_at"))
        eof = _page_timestamp(page.get("body_exhausted_at"))
        closed = _page_timestamp(page.get("body_completed_at"))
        if (
            not started <= headers <= eof <= closed <= at
            or started == closed
            or (previous_close is not None and started < previous_close)
        ):
            _deny("recency_page_order_or_future_invalid")
        earliest_eof = eof if earliest_eof is None else min(earliest_eof, eof)
        previous_close = closed
    age = at - earliest_eof
    return {
        "db_clock_at": at.isoformat(),
        "earliest_replayed_body_exhausted_at": earliest_eof.isoformat(),
        "age_microseconds": age // timedelta(microseconds=1),
        "maximum_age_seconds": 30,
        "recorded_eof_db_clock_delta_within_30_seconds": age <= MAX_AGE,
    }


def _native_db_clock_statement():
    """Resolve the system function independent of a caller's search_path."""
    return select(func.pg_catalog.clock_timestamp())


class AccountCurrentHistoryRecencyRepository:
    def __init__(self, session_factory, *, clock):
        self.session_factory = session_factory
        self.prior = AccountCurrentHistoryObservationRepository(
            session_factory, clock=clock
        )
        self.journal = AccountCaptureJournalRepository(session_factory, clock=clock)

    async def read_joined(self, scope, **pins):
        try:
            return await self._read_joined(scope, **pins)
        except Exception:  # noqa: BLE001 -- suppress private source and SQL details
            raise CurrentHistoryRecencyError(
                "current_history_recency_read_failed"
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
        checked_bootstrap(scope, LedgerScope)
        prior = await self.prior.read_joined(
            scope,
            history_sequence=history_sequence,
            history_sha256=history_sha256,
            current_sequence=current_sequence,
            current_sha256=current_sha256,
        )
        base = json.loads(prior.receipt_json)
        if (
            journal.canonical(base) != prior.receipt_json
            or base.get("schema_version")
            != "ctcc.demo_current_history_observation_join.v1"
            or base.get("policy_sha256") != PRIOR_POLICY_SHA256
            or base.get("same_account_locked_head_binding") is not True
            or base.get("account_complete") is not False
            or base.get("source_authenticity_verified") is not False
            or base.get("flat_start_permission") is not False
            or base.get("execution_authority") is not False
            or base.get("admission") != "DENY"
        ):
            _deny("recency_prior_receipt_invalid")
        keys = {
            "environment": scope.environment,
            "account_id": scope.account_id,
            "settlement_currency": scope.settlement_currency,
        }
        reference = observed.CaptureReference(**base["current_source_reference"])
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
                or history_row.event_sha256 != history_sha256
                or current_row.sequence != current_sequence
                or current_row.event_sha256 != current_sha256
                or history_row.capture_id
                != base["history_source_reference"]["capture_id"]
                or current_row.capture_id != reference.capture_id
                or journal.digest(history_row.receipt_json.encode("utf-8"))
                != base["history_index_receipt_sha256"]
                or journal.digest(current_row.receipt_json.encode("utf-8"))
                != base["current_index_receipt_sha256"]
            ):
                _deny("recency_locked_head_changed")
            chain = await self.journal._read_chain_locked(
                session, scope, reference.capture_id
            )
            if (
                current_row.source_sequence != len(chain)
                or observed.source_reference(chain) != reference
                or _utc_datetime(current_row.db_recorded_at)
                < _utc_datetime(chain[-1].db_recorded_at)
            ):
                _deny("recency_original_current_chain_changed")
            checkpoint = await self.journal.ledger._bootstrap_checkpoint(
                session, scope, account
            )
            if (
                checkpoint.state_sha256 != base["db_local_state_sha256"]
                or checkpoint.state.account_revision != base["db_account_revision"]
                or checkpoint.state.ledger_revision != base["db_ledger_revision"]
            ):
                _deny("recency_local_revision_changed")
            current_document = json.loads(current_row.document_json)
            if (
                journal.canonical(current_document)
                != current_row.document_json.encode("utf-8")
                or current_document.get("source_reference")
                != base["current_source_reference"]
                or current_document.get("policy_sha256") != current_index.POLICY_SHA256
            ):
                _deny("recency_current_document_changed")
            validated_at = _page_timestamp(current_document.get("validated_at"))
            replay = source.verify_current_account_sources(
                chain,
                reference=reference,
                scope=scope,
                validated_at=validated_at,
                expected_policy_sha256=source.V7_POLICY_SHA256,
            )
            if replay.receipt_json != current_row.receipt_json.encode("utf-8"):
                _deny("recency_current_replay_changed")
            replayed = json.loads(replay.receipt_json)
            if (
                replayed.get("policy_sha256") != source.V7_POLICY_SHA256
                or replayed.get("source_reference") != base["current_source_reference"]
                or replayed.get("account_complete") is not False
                or replayed.get("source_authenticity_verified") is not False
                or replayed.get("flat_start_permission") is not False
                or replayed.get("execution_authority") is not False
                or replayed.get("admission") != "DENY"
            ):
                _deny("recency_current_policy_or_authority_invalid")
            db_clock_at = await session.scalar(_native_db_clock_statement())
            at = _utc_datetime(db_clock_at)
            if at < _utc_datetime(chain[-1].db_recorded_at) or at < _utc_datetime(
                current_row.db_recorded_at
            ):
                _deny("recency_db_clock_before_committed_source")
            recency = evaluate_db_clock_page_recency(replayed.get("current_pages"), at)
            interval = replayed.get("observation_interval")
            if (
                type(interval) is not dict
                or interval.get("freshness_basis")
                != "replay_verified_B1_body_complete_EOF"
                or interval.get("earliest_body_exhausted_at")
                != recency["earliest_replayed_body_exhausted_at"]
            ):
                _deny("recency_replayed_eof_mismatch")
            blockers = set(base["blocking_reasons"])
            if not recency["recorded_eof_db_clock_delta_within_30_seconds"]:
                blockers.add("recorded_eof_db_clock_delta_over_30_seconds")
            document = {
                "schema_version": "ctcc.demo_current_history_observation_join_recency.v2",
                "policy_sha256": POLICY_SHA256,
                "prior_receipt_sha256": prior.receipt_sha256,
                "scope_sha256": base["scope_sha256"],
                "session_binding_sha256": base["session_binding_sha256"],
                "history_index_sha256": history_sha256,
                "current_index_sha256": current_sha256,
                "current_source_reference": base["current_source_reference"],
                "current_source_receipt_sha256": replay.receipt_sha256,
                "db_local_state_sha256": checkpoint.state_sha256,
                "same_account_locked_head_binding": True,
                "recorded_cross_clock_delta": recency,
                "recorded_cross_clock_delta_only": True,
                "blocking_reasons": sorted(blockers),
                "source_authenticity_verified": False,
                "history_tail_closed": False,
                "portfolio_risk_snapshot": None,
                "account_complete": False,
                "flat_start_permission": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        return CurrentHistoryRecencyReadback(journal.canonical(document))
