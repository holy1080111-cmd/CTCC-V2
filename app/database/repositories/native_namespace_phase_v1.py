"""Source-owned native phase observations; neither history nor G6 authority.

Both reads retain the existing same-UID Control/ledger locks, original B5
all-currency checks, and the actual source-owned coverage repository. No saved
row, caller receipt, latest-row selector or supplied pass flag enters this path.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text

from app.database.repositories import controlled_writer_coverage as coverage_rows
from app.database.repositories.demo_control import DemoControlRepository
from app.database.repositories.prospective_event_cutover_v2 import _STABLE_SCAN_FIELDS
from app.database.repositories.qualification_ledger import (
    _ConsumedEventJournalInspection,
)
from app.domain import prospective_event_cutover_catalog_v2 as catalog
from app.domain import prospective_event_cutover_v2 as records
from app.domain.source_primitives import utc_from_ns
from app.trade_qualification import controlled_writer_coverage_v1 as coverage


@dataclass(frozen=True, slots=True, repr=False)
class _NativePhaseReadback:
    coverage: coverage_rows.CoverageObservationV1
    lineage: object
    scan: _ConsumedEventJournalInspection
    transaction_id: str
    observed_at: datetime


class NativeNamespacePhaseRepositoryV1:
    def __init__(self, factory):
        self._factory = factory
        self._attempted = False

    async def _read_once(self, binding, historical, *, key):
        state, _ = coverage._namespace_phase_state_v1(binding, key=key)
        if state["factory"] is not self._factory:
            records.deny("namespace_phase_native_factory_changed")
        active = state["active"]
        async with self._factory() as session, session.begin():
            held, _, audit, snapshot = await coverage._checked_await(
                binding,
                state["repository"]._native_locked(
                    session,
                    binding,
                    coverage._HOOK_KEY,
                    (active.journal_sequence, active.journal_sha256),
                ),
            )
            observed = coverage_rows._observation(held["control"].state.scope, snapshot)
            if (
                observed.records_json != active.records_json
                or observed.audit_json != audit.document_json.decode("ascii")
                or not audit.known_flat
            ):
                records.deny("namespace_phase_active_or_all_currency_audit_changed")
            # A genuine catalog seal remains mandatory. None/UNSEALED is not
            # replaced by a caller digest or a synthetic test sentinel.
            actual_catalog = await coverage._checked_await(
                binding, session.scalar(text(catalog.CATALOG_SQL))
            )
            catalog.verify_catalog(actual_catalog)
            catalog.verify_runtime_role(actual_catalog)
            original = historical["record"]
            old = records.decode(original.record_json)
            cutover = await coverage._checked_await(
                binding,
                session.scalar(
                    text(
                        "SELECT public.controlled_writer_coverage_v2_cutover_read(:environment,:uid,:epoch,:expected)"
                    ),
                    {
                        "environment": "demo",
                        "uid": old["account_id"],
                        "epoch": old["epoch_id"],
                        "expected": original.record_sha256,
                    },
                ),
            )
            if (
                type(cutover) is not dict
                or set(cutover)
                != {"record_json", "record_sha256", "recorded_at", "coverage_start_at"}
                or cutover["record_json"] != original.record_json.decode("ascii")
                or cutover["record_sha256"] != original.record_sha256
                or datetime.fromisoformat(cutover["recorded_at"])
                != original.recorded_at
                or datetime.fromisoformat(cutover["coverage_start_at"])
                != original.coverage_start_at
            ):
                records.deny("namespace_phase_historical_cutover_changed")
            control = held["control"]
            lineage = await coverage._checked_await(
                binding,
                DemoControlRepository._read_lineage_prefix(
                    session, control.state, control.event_sha256
                ),
            )
            selected = await coverage._checked_await(
                binding, held["ledger"]._locked(session, held["ledger_scope"])
            )
            scan = await coverage._checked_await(
                binding,
                held["ledger"]._inspect_prospective_event_journal_locked(
                    session, held["ledger_scope"], selected
                ),
            )
            if type(scan) is not _ConsumedEventJournalInspection:
                records.deny("namespace_phase_original_event_scan_required")
            transaction, at = (
                await coverage._checked_await(
                    binding,
                    session.execute(
                        text(
                            "SELECT pg_catalog.txid_current()::text,pg_catalog.clock_timestamp()"
                        )
                    ),
                )
            ).one()
            state, stamp = coverage._namespace_phase_state_v1(binding, key=key)
            if (
                type(transaction) is not str
                or not transaction.isdigit()
                or type(at) is not datetime
                or at.tzinfo is not UTC
                or not control.state.updated_at <= at <= utc_from_ns(stamp["utc_ns"])
                or scan.received_at > at
            ):
                records.deny("namespace_phase_database_clock_or_transaction")
        # Session-close callbacks and original source deadlines remain fenced.
        coverage._namespace_phase_state_v1(binding, key=key)
        return _NativePhaseReadback(observed, lineage, scan, transaction, at)

    async def read_native(self, binding, historical, *, key):
        if self._attempted:
            records.deny("namespace_phase_native_attempt_burned")
        self._attempted = True
        try:
            first = await self._read_once(binding, historical, key=key)
            second = await self._read_once(binding, historical, key=key)
            if (
                first.transaction_id == second.transaction_id
                or second.observed_at < first.observed_at
                or first.coverage.records_json != second.coverage.records_json
                or first.lineage.receipt_json != second.lineage.receipt_json
                or any(
                    json.loads(first.scan.document_json)[name]
                    != json.loads(second.scan.document_json)[name]
                    for name in _STABLE_SCAN_FIELDS
                )
                or first.scan.consumed_event_keys != second.scan.consumed_event_keys
            ):
                records.deny("namespace_phase_independent_readback_changed")
            coverage._namespace_phase_state_v1(binding, key=key)
            return second
        except asyncio.CancelledError:
            raise
        except records.ProspectiveCutoverError:
            raise
        except Exception:  # noqa: BLE001 -- no raw SQL/account/source exceptions escape.
            records.deny("namespace_phase_native_read_unavailable")
